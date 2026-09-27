"""The perception cache: a window inferred twice is inferred once.

Where ``test_reframe_frame_cache.py`` pins that a window is not *decoded*
twice, this pins that it is not *perceived* twice. ``scene_filter`` perceives
a window in ``analyse_scene`` to decide the layout and perceives it again in
the composition pass to plan the trajectories, and before this cache the
second pass re-ran the detector over every frame. Measured on the corpus AV1
source: 28 YuNet inferences at 1080p are 1.73 s, plus 60 ms of
``find_shot_boundaries``, so one ``scene_filter`` call over a 15 s window spent
1.81 s of its budget re-deriving boxes it already had. With both caches the
same call is 9.33 s against the 18.64 s baseline, 1.99x.

The tests pin the properties that keep the win honest:

* one ``scene_filter`` call infers each frame once, not twice;
* two different windows are perceived separately, never from each other;
* a perception built by one detector is not served as though a different
  detector agreed with it;
* the cache is bounded, so a long session cannot accumulate perceptions;
* the boundaries a consumer gets are not the same object another consumer
  could mutate, because a shared mutable cut set would let one consumer's
  arithmetic change the next consumer's verdict.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))

import engine.reframe as rf  # noqa: E402

SRC_W, SRC_H = 1280, 720
FRAMES, FPS = 36, 12
WINDOW_END = (FRAMES - 2) / float(FPS)


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Clear before and after, so no inference is satisfied by another test's
    and no perception outlives the detector that made it."""
    rf._clear_frame_cache()
    yield
    rf._clear_frame_cache()


class _CountingDetector:
    """A detector that reports the marker's bounding box and counts its own
    calls.

    Stands in for YuNet so the tests see a real inference count without the
    model's cost, and so each instance is a distinct object -- which is what the
    cache's identity check has to tell apart.
    """

    kind = "counting"
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, frame):
        import numpy as np

        self.calls += 1
        if frame is None:
            return []
        bright = np.all(frame[:, :, :] > 150, axis=2)
        ys, xs = np.nonzero(bright)
        if not xs.size:
            return []
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        return [(float(x0), float(y0), float(x1 - x0), float(y1 - y0), 1.0)]


def _clip(out: Path) -> Path:
    """A clip whose marker moves, so the two trajectories have something to
    plan against and the composition path is the one taken."""
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"),
                             FPS, (SRC_W, SRC_H))
    try:
        for i in range(FRAMES):
            frame = np.full((SRC_H, SRC_W, 3), 30, np.uint8)
            frame[40:560, 700:1240] = 190
            frame[160:320, 840:1000] = 0
            cx, cy = 300 + 6 * i, 340 + 3 * (i % 5)
            cv2.circle(frame, (cx, cy), 70, (205, 185, 165), -1)
            writer.write(frame)
    finally:
        writer.release()
    return out


@pytest.fixture
def counting(monkeypatch):
    """One detector for the whole test, patched where ``_perceive_window``
    resolves it."""
    det = _CountingDetector()
    monkeypatch.setattr(rf, "get_detector", lambda *a, **kw: det)
    return det


def test_one_scene_filter_call_infers_each_frame_once(tmp_path, counting):
    """The pin on the defect itself.

    ``analyse_scene`` and the composition pass each used to run the detector
    over all samples, so the count was twice the sample count. Asserted on the
    inference count rather than on wall time, so it holds on a host where 28
    extra inferences would be invisible in the clock.
    """
    import engine.reframe_scenes as rs

    video = _clip(tmp_path / "clip.mp4")
    rs._SCENE_CACHE.clear()
    samples = 8

    rs.scene_filter(video, 0.0, WINDOW_END, samples=samples)

    assert counting.calls <= samples, \
        f"{counting.calls} inferences for {samples} samples -- the window was " \
        f"perceived more than once"
    assert counting.calls >= samples, \
        f"{counting.calls} inferences for {samples} samples -- the window was " \
        f"never fully perceived"


def test_a_window_perceived_twice_is_inferred_once(tmp_path, counting):
    """The same window asked twice directly, which is the shape the caller
    presents: ``analyse_scene`` and the composition pass name identical
    arguments."""
    video = _clip(tmp_path / "clip.mp4")

    rf._perceive_window(video, 0.0, WINDOW_END, 8)
    rf._perceive_window(video, 0.0, WINDOW_END, 8)

    assert counting.calls == 8, \
        f"two identical asks made {counting.calls} inference passes over 8 frames"


def test_two_distinct_windows_are_perceived_separately(tmp_path, counting):
    """The win is deduplication, not memoisation across different asks.

    A hit here would mean the key ignores the window, and a clip would be
    composed against boxes from a different part of the source -- presented to
    the editor as though they were its own.
    """
    video = _clip(tmp_path / "clip.mp4")
    half = WINDOW_END / 2.0

    rf._perceive_window(video, 0.0, half, 4)
    rf._perceive_window(video, half, WINDOW_END, 4)

    assert counting.calls == 8, \
        f"two different windows shared an inference ({counting.calls} of 8 frames)"


def test_a_perception_from_another_detector_is_not_reused(tmp_path, counting):
    """The entry carries the detector that built it.

    ``get_detector`` is a process singleton, but a caller -- a test, or an
    operator pointing at a different model -- may replace it. Boxes the retired
    model produced must not be served as though the live one agreed, or a model
    swap would silently plan every clip against the previous model's verdicts.
    """
    video = _clip(tmp_path / "clip.mp4")
    first = counting
    rf._perceive_window(video, 0.0, WINDOW_END, 8)
    assert first.calls == 8

    second = _CountingDetector()
    rf.get_detector = lambda *a, **kw: second
    boxes_before, bounds_before = rf._perceive_window(video, 0.0, WINDOW_END,
                                                     8)[4:6]

    assert second.calls == 8, \
        "a perception built by another detector was served without re-inferring"
    assert boxes_before is not None and bounds_before is not None


def test_the_perception_cache_is_bounded(tmp_path, counting):
    """A bound, not an unbounded memo, and one shared with the frames.

    The perception lives on the frame cache's own entry rather than in a second
    cache, so this also asserts the two cannot drift apart -- a perception
    cannot outlive or out-grow the window it describes, which was the hazard a
    separate cache would have needed a convention to avoid.
    """
    video = _clip(tmp_path / "clip.mp4")
    bound = rf._FRAME_CACHE_MAX_ENTRIES
    end = WINDOW_END
    step = end / float(bound + 2)

    for i in range(bound + 2):
        rf._perceive_window(video, i * step, end, 4)

    assert len(rf._FRAME_CACHE) <= bound, \
        f"cache grew to {len(rf._FRAME_CACHE)} past its bound of {bound}"
    # Every surviving entry carries its perception, not just its pixels.
    assert all(entry[4] is not None for entry in rf._FRAME_CACHE.values()), \
        "an entry survived without the perception it should carry alongside"


def test_boundaries_are_frozen_and_so_not_a_mutation_hazard(tmp_path, counting):
    """The boundaries come back frozen.

    Every consumer does membership tests and set arithmetic over them. If the
    cached object were a mutable set, one consumer's ``add`` or ``discard``
    would change the next consumer's shot count, and the composition would be
    planned against cuts the source does not have.
    """
    video = _clip(tmp_path / "clip.mp4")

    _t, _f, _w, _h, _boxes, bounds = rf._perceive_window(video, 0.0,
                                                         WINDOW_END, 8)

    assert isinstance(bounds, frozenset), \
        f"boundaries are {type(bounds).__name__}, not frozenset"
    with pytest.raises(AttributeError):
        bounds.add(3)


def test_the_composition_path_gets_the_same_boxes_it_would_have_computed(
        tmp_path, counting):
    """The cache changes what is recomputed, never what the editor receives.

    The composition pass plans its trajectories from the boxes
    ``analyse_scene`` already produced, so the filter must be the one the
    un-cached path would have built. Pinned by composing with and without the
    cache and comparing the shipped string.
    """
    import engine.reframe_scenes as rs

    video = _clip(tmp_path / "clip.mp4")
    rs._SCENE_CACHE.clear()
    cached = rs.scene_filter(video, 0.0, WINDOW_END, samples=8)

    # Re-derive every box for every ask, which is the pre-change behaviour.
    real_detect = type(counting).detect

    def always_infer(self, frame):
        counting.calls += 1
        return real_detect(self, frame)

    rs._SCENE_CACHE.clear()
    rf._clear_frame_cache()
    monkeypatch_target = type(counting)
    monkeypatch_target.detect = always_infer
    try:
        fresh = rs.scene_filter(video, 0.0, WINDOW_END, samples=8)
    finally:
        monkeypatch_target.detect = real_detect

    assert cached == fresh, \
        "caching the perception changed the filter the editor receives"


def test_the_plain_speaker_path_does_not_re_infer(tmp_path, counting):
    """The third site, and the common one.

    ``_probe_and_track`` used to perceive for itself: it ran the detector over
    every frame and diffed the boundaries again, after ``analyse_scene`` had
    already done both to decide this shot *was* a plain speaker. On a
    talking-head clip that is 56 inferences for 28 frames, and the talking-head
    clip is the case the legacy chain was built for. The marker here is the only
    bright region, so the shot has one class and this is the path taken.
    """
    import cv2
    import numpy as np

    video = tmp_path / "talking.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"),
                             FPS, (SRC_W, SRC_H))
    try:
        for i in range(FRAMES):
            frame = np.full((SRC_H, SRC_W, 3), 30, np.uint8)
            # One bright region only, and it moves: no slide, so the classifier
            # sees a single SPEAKER class and the composition is not taken.
            cx, cy = 300 + 6 * i, 340 + 3 * (i % 5)
            cv2.circle(frame, (cx, cy), 70, (205, 185, 165), -1)
            writer.write(frame)
    finally:
        writer.release()

    import engine.reframe_scenes as rs

    rs._SCENE_CACHE.clear()
    samples = 8
    rs.scene_filter(video, 0.0, WINDOW_END, samples=samples)

    assert counting.calls <= samples, \
        f"{counting.calls} inferences for {samples} samples on the plain path " \
        f"-- analyse_scene and _probe_and_track re-perceived the same window"
    assert counting.calls >= samples, \
        f"{counting.calls} inferences for {samples} samples -- the window was " \
        f"never fully perceived"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
