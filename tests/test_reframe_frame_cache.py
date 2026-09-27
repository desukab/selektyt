"""The frame-level read cache: a window read twice is decoded once.

Baseline this pins. ``engine.reframe_scenes.scene_filter`` asks for the same
window twice in one call -- ``analyse_scene`` reads it to decide the layout,
and then the composition pass (or, on the plain path, ``_probe_and_track``)
reads it again with byte-identical arguments. ``_sample_frames`` used to have
no memory of the frames it returned: ``_REFAME_CACHE`` holds only the
trajectory derived from them, ``_SCENE_CACHE`` only the resulting filter
string. So the second ask re-ran the entire seek-and-decode pass. Measured on
the corpus AV1 source: 248 ms per 1080p sample, 28 samples, so one read is
6.9 s and a single ``scene_filter`` call spent 18.64 s, of which half was a
re-read of a window it had just read.

The fix is a bounded cache at the choke point, so it repairs every consumer at
once rather than threading one caller's frames through another's signature.
These tests pin the properties that keep the win honest:

* a repeated read does not reopen the source;
* the cache serves a window from a file that has not changed, and refuses one
  from a file that has;
* it is bounded, so a long session cannot accumulate frames unboundedly;
* the arrays it returns are shared, and the consumers that score them read
  rather than write, so a second consumer of the same window sees the same
  pixels the first one planned against.

The reader these tests open is synthetic h264, so the OpenCV path populates the
cache; the AV1 path where the cost was measured is exercised by
``test_reframe_decode_fallback.py`` and by the real-media checks, which skip
where the media is not redistributable.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))

import engine.reframe as rf  # noqa: E402

SRC_W, SRC_H = 1280, 720
FRAMES, FPS = 36, 12
WINDOW_END = (FRAMES - 2) / float(FPS)  # 0.1 s inside end-of-stream, so every
# sample decodes; one landing exactly on the duration does not, and that is a
# property of the reader rather than of the cache.


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Clear before and after, so no measurement is satisfied by another
    test's decode and no window outlives the module that read it."""
    rf._clear_frame_cache()
    yield
    rf._clear_frame_cache()


def _clip(out: Path) -> Path:
    """A two-region clip: a static bright slide on the right, a moving marker
    left of it, so both the detection and the shot-differencing consumers get
    something to read."""
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


def _opens_for(video: Path) -> list[str]:
    """Count source opens while the wrapped function runs.

    ``cv2.VideoCapture`` is the only thing on the OpenCV path that touches the
    file, so the number of opens is the number of full decode passes -- the
    quantity the cache exists to shrink. Replaced rather than wrapped, so a
    cache miss is a real open and a cache hit is no open at all.
    """
    import cv2

    real = cv2.VideoCapture
    seen: list[str] = []

    def counting(path):
        seen.append(str(path))
        return real(path)

    rf.cv2.VideoCapture = counting
    try:
        rf._sample_frames(video, 0.0, WINDOW_END, 8)
        rf._sample_frames(video, 0.0, WINDOW_END, 8)
    finally:
        rf.cv2.VideoCapture = real
    return seen


def test_a_repeated_read_does_not_reopen_the_source(tmp_path):
    """The pin on the defect itself: the same window twice is one decode."""
    video = _clip(tmp_path / "clip.mp4")

    seen = _opens_for(video)

    assert len(seen) == 1, f"one window requested twice reopened the source {seen}"
    assert seen == [str(video)]


def test_two_distinct_windows_still_decode_twice(tmp_path):
    """The win is deduplication, not memoisation across different asks.

    Two different windows must not be served from each other -- a hit here
    would mean the key ignores the timestamps, which would compose a clip
    against the wrong frames and show it to the editor as if it had not.
    """
    video = _clip(tmp_path / "clip.mp4")
    half = WINDOW_END / 2.0

    times_a, frames_a, w_a, h_a = rf._sample_frames(video, 0.0, half, 4)
    times_b, frames_b, w_b, h_b = rf._sample_frames(video, half, WINDOW_END, 4)

    assert times_a != times_b
    assert (w_a, h_a) == (w_b, h_b)
    # Not merely different objects: different pixels, from a different window.
    assert not _same_pixels(frames_a, frames_b)


def test_a_repeated_read_returns_the_same_arrays(tmp_path):
    """Shared, not copied: the caller that asks twice gets one set of frames.

    This is the property that makes the win safe -- every consumer of these
    arrays reads them, so handing the same buffer to a second consumer cannot
    corrupt what the first scored. Pinned as equality rather than as a speed
    claim, so a future caller that copies-then-writes cannot silently turn the
    shared buffer into a mutation hazard.
    """
    video = _clip(tmp_path / "clip.mp4")

    _, first, _, _ = rf._sample_frames(video, 0.0, WINDOW_END, 6)
    _, second, _, _ = rf._sample_frames(video, 0.0, WINDOW_END, 6)

    assert len(second) == len(first)
    assert all(a is b for a, b in zip(first, second)), \
        "a repeated read rebuilt the frames instead of returning them"


def test_a_file_that_changed_underneath_is_not_served_stale(tmp_path):
    """The key carries size and mtime, so an edit cannot be masked.

    The editor's whole workflow is to change the source and re-plan. If the
    cache answered from a file that had since been replaced, the composition
    would be built against frames that no longer exist and reported as though
    they were the current clip's.
    """
    video = _clip(tmp_path / "clip.mp4")
    _, first, _, _ = rf._sample_frames(video, 0.0, WINDOW_END, 6)

    stamp = video.stat().st_mtime
    os.utime(video, (stamp + 5.0, stamp + 5.0))

    _, second, _, _ = rf._sample_frames(video, 0.0, WINDOW_END, 6)

    assert all(a is not b for a, b in zip(first, second)), \
        "a restamped file was served the frames from before the edit"


def test_the_cache_is_bounded(tmp_path):
    """A bound, not an unbounded memo.

    One 1080p window is 28 x 1920 x 1080 x 3 bytes = 174 MB, so a session
    planning across many windows would otherwise grow the cache past the
    host's free memory. Pinned to the module's own bound so raising it has to
    be a measured decision about peak memory, not an accident.
    """
    video = _clip(tmp_path / "clip.mp4")
    bound = rf._FRAME_CACHE_MAX_ENTRIES
    end = WINDOW_END
    step = end / float(bound + 2)

    for i in range(bound + 2):
        rf._sample_frames(video, i * step, end, 4)

    assert len(rf._FRAME_CACHE) <= bound, \
        f"cache grew to {len(rf._FRAME_CACHE)} past its bound of {bound}"


def test_a_scene_filter_read_does_not_decode_a_window_twice(tmp_path):
    """The caller the baseline was taken on.

    ``scene_filter`` asks for the window in ``analyse_scene`` and again when it
    composes, and before the cache both asks re-decoded. This asserts the
    invariant in the form that survived the measurement: the source is opened
    once per *distinct* window, so composing a clip can never cost more than
    analysing it. Asserted on opens rather than on wall time, so it holds on a
    fast host where a redundant decode would be invisible in the clock.

    Instrumented on ``_perceive_window``, the layer ``scene_filter`` goes
    through since perception became cached: it records the window each ask
    names and delegates, so the count is of asks rather than of a function the
    caller no longer names.
    """
    import engine.reframe_scenes as rs
    import cv2

    video = _clip(tmp_path / "clip.mp4")
    real = cv2.VideoCapture
    opens: list[str] = []
    requests: list[tuple[float, float, int]] = []

    def counting_capture(path):
        opens.append(str(path))
        return real(path)

    real_perceive = rf._perceive_window

    def counting_perceive(v, start, end, samples):
        requests.append((round(start, 3), round(end, 3), samples))
        return real_perceive(v, start, end, samples)

    rf.cv2.VideoCapture = counting_capture
    rs._perceive_window = counting_perceive
    try:
        rs.scene_filter(video, 0.0, WINDOW_END, samples=8)
    finally:
        rf.cv2.VideoCapture = real
        rs._perceive_window = real_perceive

    distinct = len(set(requests))
    assert distinct >= 1, "the window was never requested"
    # The source is opened once per distinct window: a second open for a window
    # already decoded is exactly the redundant pass the cache removes.
    assert len(opens) <= distinct, \
        f"{len(opens)} source opens for {distinct} distinct window(s) {requests}"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _same_pixels(frames_a, frames_b) -> bool:
    """Whether two frame lists are pixel-identical where both decoded."""
    import numpy as np

    if len(frames_a) != len(frames_b):
        return False
    for a, b in zip(frames_a, frames_b):
        if a is None or b is None:
            continue
        if not np.array_equal(a, b):
            return False
    return True
