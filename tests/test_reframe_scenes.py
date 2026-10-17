"""The scene-aware reframe: classifications and coherent crop rendering.

``engine.reframe_scenes`` classifies speakers and other static content while
preserving one source crop per instant. Three things are pinned here:

1. the *decision* -- given a speaker and a content region, does one legal crop
   hold both, or is the layout one the crop has to concede? These are pure, so
   the mandated regressions (a slide appearing, a speaker leaving, a slide too
   large to compose beside) are numbers rather than a guess at what a detector
   would see;
2. the *plan* -- per-sample classes become held, shot-aligned, contiguous
   segments, because the filtergraph trims each segment to its own window and a
   gap between two would drop video while the audio played on;
3. the *layout* -- even a SPLIT classification renders one tracked speaker crop,
   never stacked source regions.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SRC_W, SRC_H = 1280, 720
# A 16:9 source's vertical crop is 406 px, and the lecture fixtures below are
# built so that width is what separates the two regions.
CROP_W = 406


# ── the decision: which class, and can one crop hold both ── #


def test_a_slide_and_speaker_that_no_crop_can_hold_is_a_split():
    """The case §4A exists for, as a verdict rather than as a layout choice.

    A speaker at x=212..352 and a slide at x=672..1024 are ~420 px
    centre-to-centre in a 406 px window, so no legal crop keeps the speaker and
    half the slide. ``classify_scene`` answers SPLIT for exactly that reason --
    and the reason is checked here by enumerating every legal crop left-edge,
    not by trusting the arithmetic of the interval arithmetic.
    """
    from engine.reframe_scenes import (
        FaceBox,
        SceneClass,
        SlideRect,
        both_fit_in_crop,
        classify_scene,
        joint_crop_x,
    )

    face = FaceBox(212.0, 240.0, 140.0, 180.0)
    slide = SlideRect(672.0, 32.0, 352.0, 544.0)

    assert both_fit_in_crop(face, slide, CROP_W, SRC_W) is False
    assert joint_crop_x(face, slide, CROP_W, SRC_W) is None
    assert classify_scene(face, slide, CROP_W, SRC_W, SRC_H) is SceneClass.SPLIT

    # The self-evidencing half: no crop left-edge holds the speaker *and* half
    # the slide. ``joint_crop_x`` is derived from the two windows below, so this
    # is the independent check on the derivation, and a change that widened the
    # crop to make them fit would break here before it broke the 9:16 contract.
    holds_face, keeps_slide = 0, 0
    for x in range(SRC_W - CROP_W + 1):
        face_in = x <= 282.0 <= x + CROP_W
        overlap = (min(x + CROP_W, 1024.0) - max(x, 672.0))
        slide_in = overlap >= 0.5 * slide.w
        if face_in:
            holds_face += 1
        if face_in and slide_in:
            keeps_slide += 1
    assert holds_face > 0, "some legal crop must be able to hold the speaker"
    assert keeps_slide == 0, (
        f"{keeps_slide} of the {holds_face} crops that hold the speaker also "
        f"keep half the slide; if that is not zero, the two regions are no "
        f"longer mutually exclusive inside a {CROP_W} px window and SPLIT is "
        f"the wrong answer for this layout"
    )


def test_a_slide_the_crop_can_also_hold_is_a_speaker_scene():
    """The converse, and the reason SPLIT is not the default: when one window
    does hold both, the speaker keeps the full frame and the slide rides along.

    A speaker at x=212..352 and a graphic at x=400..500 are close enough that a
    crop centred between them carries both, so the election is SPEAKER and the
    ``joint`` layout is available rather than a second band.
    """
    from engine.reframe_scenes import (
        FaceBox,
        SceneClass,
        SlideRect,
        both_fit_in_crop,
        classify_scene,
        joint_crop_x,
    )

    face = FaceBox(212.0, 240.0, 140.0, 180.0)
    slide = SlideRect(400.0, 200.0, 100.0, 300.0)
    assert both_fit_in_crop(face, slide, CROP_W, SRC_W) is True
    joint = joint_crop_x(face, slide, CROP_W, SRC_W)
    assert joint is not None
    assert classify_scene(face, slide, CROP_W, SRC_W, SRC_H) is SceneClass.SPEAKER
    # The returned edge is the one that holds both, by the contract above.
    assert joint <= 282.0 <= joint + CROP_W, "the speaker must still be in frame"
    assert (min(joint + CROP_W, 500.0) - max(joint, 400.0)) >= 0.5 * slide.w, (
        "the slide must retain at least half its width in that same window"
    )


def test_a_slide_filling_the_frame_still_has_to_answer_for_the_speaker():
    """A full-frame slide with a speaker present is not a SLIDE scene.

    The tempting rule -- a slide covering the frame *is* the frame, so compose
    nothing -- was shipped once and measured on a real corpus cut, where the
    shot widened to a speaker whose face was 52 px wide beside a static region
    covering 98% of the frame. The rule called it SLIDE, the layout cropped to
    the slide's centre, and the speaker left the deliverable on 6 of 26 samples
    while the pre-composition reframe kept it on 25 of 26. So the speaker is
    consulted first and the geometry decides:

    * a speaker *inside* the slide's own crop window is a SPEAKER scene, one
      crop, clamped to hold both;
    * a speaker that window cannot reach is a SPLIT scene, and the two-region
      layout is exactly what exists to hold that pair;
    * a full-frame slide with nobody in front of it is still a SLIDE scene, and
      nothing is composed for it.
    """
    from engine.reframe_scenes import FaceBox, SceneClass, SlideRect, classify_scene

    full_frame = SlideRect(0.0, 0.0, float(SRC_W), float(SRC_H))
    assert full_frame.area >= 0.80 * SRC_W * SRC_H

    # A speaker off at the left edge: a 406 px crop cannot hold both it and half
    # of a 1280 px slide, so the pair is a SPLIT, never a SLIDE.
    aside = FaceBox(212.0, 240.0, 140.0, 180.0)
    assert classify_scene(aside, full_frame, CROP_W, SRC_W, SRC_H) is SceneClass.SPLIT

    # A speaker the slide's own window reaches: one crop holds both.
    centred = FaceBox(560.0, 240.0, 140.0, 180.0)
    assert classify_scene(centred, full_frame, CROP_W, SRC_W, SRC_H) is SceneClass.SPEAKER

    # And the case the rule existed for, which still has to be honoured: nobody
    # in front of the slide, so there is nothing to compose with.
    assert classify_scene(None, full_frame, CROP_W, SRC_W, SRC_H) is SceneClass.SLIDE


def test_a_slide_filling_the_frame_with_no_speaker_is_a_slide():
    """The full-frame slide is the content when there is no speaker to lose."""
    from engine.reframe_scenes import SceneClass, SlideRect, classify_scene

    full_frame = SlideRect(0.0, 0.0, float(SRC_W), float(SRC_H))
    assert classify_scene(None, full_frame, CROP_W, SRC_W, SRC_H) is SceneClass.SLIDE
    # A slide merely large, not dominant, is the same answer without a speaker.
    big = SlideRect(0.0, 0.0, 0.79 * SRC_W, float(SRC_H))
    assert classify_scene(None, big, CROP_W, SRC_W, SRC_H) is SceneClass.SLIDE


def test_a_speaker_alone_and_a_slide_alone():
    """The single-region classes need no geometry to pick."""
    from engine.reframe_scenes import FaceBox, SceneClass, SlideRect, classify_scene

    face = FaceBox(212.0, 240.0, 140.0, 180.0)
    slide = SlideRect(672.0, 32.0, 352.0, 544.0)
    assert classify_scene(face, None, CROP_W, SRC_W, SRC_H) is SceneClass.SPEAKER
    assert classify_scene(None, slide, CROP_W, SRC_W, SRC_H) is SceneClass.SLIDE
    assert classify_scene(None, None, CROP_W, SRC_W, SRC_H) is SceneClass.SPEAKER


def test_the_speakers_own_torso_is_not_a_slide():
    """A detected region hanging under and overlapping the speaker's face,
    barely larger than the face itself, is the speaker's body -- not content.

    Measured on a real NASA interview where the dark polo shirt under the
    subject's chin was detected as a 472x352 "slide" at (180, 352) while the
    face box measured 224x321 at (543, 156): slide top 352 > face midline 317,
    horizontal overlap 542..652, and the region's area is 2.31x the face box's.
    The SPLIT layout that classification chose rendered the speaker at 1.55x
    zoom in a top band and the subject's own shoulders as a "content" band
    below -- a talking head that visibly loses its face for the length of the
    SPLIT. The torso rejection collapses it back to a plain tracked speaker
    crop.

    The synthetic fixtures above stay correct: their slides begin at or above
    the face's midline or cover so much area that no ratio cut them off, so
    this check is only reached by the anatomy case.
    """
    from engine.reframe_scenes import FaceBox, SceneClass, SlideRect, classify_scene

    face = FaceBox(542.6, 156.3, 223.9, 321.4)
    torso = SlideRect(180.0, 352.0, 472.0, 352.0)
    assert classify_scene(face, torso, CROP_W, SRC_W, SRC_H) is SceneClass.SPEAKER

    # The same geometry moved UP beside the face is a backdrop, not anatomy:
    # it no longer begins below the face's midline, so the strict SPLIT answer
    # (one crop cannot hold both) still stands.
    backdrop = SlideRect(180.0, 80.0, 472.0, 352.0)
    assert classify_scene(face, backdrop, CROP_W, SRC_W, SRC_H) is SceneClass.SPLIT


def test_a_trunk_sized_slide_beside_the_face_is_still_a_slide():
    """The torso check is spatial, not a blanket small-region dismissal.

    A laptop screen the speaker stands beside, beginning above the face's
    midline and not overlapping the face box, must keep its geometry answer
    regardless of its size -- only the below-and-overlapping pattern is
    anatomy.
    """
    from engine.reframe_scenes import FaceBox, SceneClass, SlideRect, classify_scene

    face = FaceBox(542.6, 156.3, 223.9, 321.4)
    screen = SlideRect(430.0, 150.0, 120.0, 220.0)
    assert classify_scene(face, screen, CROP_W, SRC_W, SRC_H) is SceneClass.SPEAKER


# ── the plan: classes over time become held, contiguous segments ── #


def _even_times(n: int, step: float = 0.1) -> list[float]:
    return [round(i * step, 6) for i in range(n)]


def _contiguous(segments: list) -> None:
    """Assert the contract the render depends on: no hole, no overlap, monotonic
    cuts, and the span covered is the one the caller asked about."""
    assert segments, "the plan must not be empty for a non-empty sample set"
    for prev, cur in zip(segments, segments[1:]):
        assert prev.end == cur.start, (
            f"segments must hand off at one time; {prev.end} != {cur.start}")
        assert prev.end < cur.end, "a zero-or-negative-length segment is fatal"
    from engine.reframe_scenes import _MIN_HOLD_S
    for seg in segments:
        assert seg.end - seg.start >= _MIN_HOLD_S - 1e-9, (
            f"no segment may be shorter than the {_MIN_HOLD_S:.1f}s hold, which "
            f"is what stops an oscillation becoming two layout changes")


def test_plan_scenes_absorbs_a_short_oscillation():
    """A slide that appears for 0.4s inside a speaker segment is evidence the
    slide is there, not enough to re-lay the clip for: the run is absorbed into
    its longer neighbour, and the deliverable keeps one layout."""
    from engine.reframe_scenes import SceneClass, plan_scenes

    times = _even_times(41)
    classes = ([SceneClass.SPEAKER] * 15 + [SceneClass.SLIDE] * 5
               + [SceneClass.SPEAKER] * 21)
    assert times[19] - times[15] < 1.0, "the fixture must be a short blip"

    segments = plan_scenes(times, classes)
    _contiguous(segments)
    assert len(segments) == 1, (
        f"a {times[19] - times[15]:.1f}s blip must be absorbed; got "
        f"{[(s.start, s.end, s.cls.value) for s in segments]}")
    assert segments[0].cls is SceneClass.SPEAKER
    assert (segments[0].start, segments[0].end) == (times[0], times[-1])


def test_plan_scenes_holds_two_real_scenes_and_cuts_between_samples():
    """Two runs that both outlast the minimum hold survive as two segments, and
    the handoff is the *midpoint between the two samples* that disagree -- the
    honest place, since neither sample is the switch."""
    from engine.reframe_scenes import SceneClass, plan_scenes

    times = _even_times(82)
    classes = [SceneClass.SPEAKER] * 41 + [SceneClass.SLIDE] * 41
    segments = plan_scenes(times, classes)
    _contiguous(segments)
    assert len(segments) == 2
    assert [s.cls for s in segments] == [SceneClass.SPEAKER, SceneClass.SLIDE]
    # times[40] = 4.0 is the last SPEAKER sample, times[41] = 4.1 the first SLIDE
    assert (segments[0].end, segments[1].start) == (0.5 * (4.0 + 4.1),) * 2


def test_plan_scenes_snaps_a_class_change_onto_a_shot_boundary():
    """A layout change within 0.75s of a hard cut moves to the cut: the change
    reads as an edit there, and as a mistake a frame later."""
    from engine.reframe_scenes import _SNAP_TO_SHOT_S, SceneClass, plan_scenes

    times = _even_times(82)
    classes = [SceneClass.SPEAKER] * 41 + [SceneClass.SLIDE] * 41
    # A shot boundary on the first SLIDE sample.
    segments = plan_scenes(times, classes, boundaries={41})
    assert len(segments) == 2
    assert segments[0].end == times[41], (
        "the cut should move to the boundary, not stay at the midpoint")
    assert abs(times[41] - 0.5 * (4.0 + 4.1)) <= _SNAP_TO_SHOT_S

    # A boundary out of reach leaves the midpoint alone.
    far = plan_scenes(times, classes, boundaries={50})
    assert far[0].end == 0.5 * (4.0 + 4.1)


def test_one_class_for_the_whole_interval_covers_the_span():
    """A single-class clip is reported over the whole span it was asked about,
    not as a degenerate one-sample span, so a caller that lays out from segment
    bounds covers the clip."""
    from engine.reframe_scenes import SceneClass, plan_scenes

    times = _even_times(41)
    segments = plan_scenes(times, [SceneClass.SLIDE] * 41)
    assert len(segments) == 1
    assert (segments[0].start, segments[0].end) == (times[0], times[-1])

    assert plan_scenes([], []) == []


def test_an_absorbed_run_leaves_the_segment_its_slide():
    """A segment whose first samples were absorbed from another class still
    carries the slide its own class needs.

    ``plan_scenes`` absorbs a run shorter than ``_MIN_HOLD_S`` by rewriting its
    class to its longer neighbour's. The segment that results can therefore
    *start* on samples the classifier never saw as the segment's class, and
    those samples' shots can hold no slide at all. The carry used to take the
    slide from the first sample at or after the segment's start regardless, so
    a segment the layout dereferences a slide for -- a ``SPLIT`` it composes two
    bands from -- arrived with ``slide=None`` and the render died on its own
    assertion. Measured, on the corpus: a ``--force`` re-run of
    royal-society-cloke, whose fresh transcript reached that path, failed in
    ``_layout_chains`` at ``assert seg.slide is not None``.

    The fixture below is that shape, built small. A 0.4s speaker opening is
    absorbed into a 2.9s SPLIT run, so the SPLIT segment starts on a
    slide-less shot while the samples that earned it the SPLIT class sit later.
    """
    from engine.reframe_scenes import (
        SceneClass,
        SlideRect,
        _carry_slides,
        _layout_chains,
        plan_scenes,
    )

    times = _even_times(60)          # 0.0 .. 5.9 at 0.1s
    # shots: 0 for 0..4, 1 for 5..34, 2 for 35..59
    shot_of = [0] * 5 + [1] * 30 + [2] * 25
    # Only the middle shot holds a slide; the speaker shots hold none.
    slide = SlideRect(672.0, 40.0, 352.0, 640.0)
    per_shot = {0: None, 1: slide, 2: None}
    classes = ([SceneClass.SPEAKER] * 5 + [SceneClass.SPLIT] * 30
               + [SceneClass.SPEAKER] * 25)

    # The fixture has to actually be the dangerous shape: the opening run is
    # shorter than the minimum hold, so it is absorbed, and its shot has no
    # slide to give.
    assert times[4] - times[0] < 2.0
    assert times[34] - times[5] >= 2.0
    assert times[59] - times[35] >= 2.0
    assert per_shot[shot_of[0]] is None

    segments = plan_scenes(times, classes)
    assert len(segments) == 2
    assert segments[0].cls is SceneClass.SPLIT, (
        "the short speaker opening must be absorbed into the SPLIT run, not "
        "survive as its own segment")
    # This is the absorption doing the absorbing: the segment starts on a
    # sample the classifier called a SPEAKER.
    assert classes[0] is SceneClass.SPEAKER

    out = _carry_slides(segments, times, classes, shot_of, per_shot, [], CROP_W,
                        SRC_W)
    assert out[0].slide is slide, (
        "the SPLIT segment must carry the slide of a sample its class came "
        "from, not the empty hand of its first sample")
    assert out[0].start == times[0]

    # And the layout it feeds renders instead of asserting. This is the
    # failure the corpus run hit, at the point it hit it.
    chains = _layout_chains(out[0], SRC_W, SRC_H, 1, 1, CROP_W, 720, 1280,
                            [], "", None)
    assert len(chains) == 1 and "crop=" in chains[0], (
        "a SPLIT classification still renders one tracked crop")
    assert "vstack" not in "".join(chains)


def test_a_slide_held_by_a_later_shot_is_carried_to_a_slide_segment():
    """The same lookup serves ``SLIDE``, which dereferences ``seg.slide`` for its
    crop and would raise ``AttributeError`` rather than assert. A slide segment
    starting on an absorbed slide-less sample has to pick the slide up too."""
    from engine.reframe_scenes import (
        SceneClass,
        SlideRect,
        _carry_slides,
        plan_scenes,
    )

    times = _even_times(60)
    shot_of = [0] * 5 + [1] * 30 + [2] * 25
    slide = SlideRect(672.0, 40.0, 352.0, 640.0)
    per_shot = {0: None, 1: slide, 2: None}
    # A SPEAKER opening absorbed into a SLIDE run (no face in shot 1).
    classes = ([SceneClass.SPEAKER] * 5 + [SceneClass.SLIDE] * 30
               + [SceneClass.SPEAKER] * 25)

    segments = plan_scenes(times, classes)
    assert segments[0].cls is SceneClass.SLIDE
    out = _carry_slides(segments, times, classes, shot_of, per_shot, [], CROP_W,
                        SRC_W)
    assert out[0].slide is slide


def test_transitions_are_reported_between_classes():
    """The render turns these into dissolves rather than cuts, and an empty list
    is the common single-class clip."""
    from engine.reframe_scenes import (
        SceneClass,
        SceneSegment,
        SlideRect,
        transition_points,
    )

    single = [SceneSegment(0.0, 4.0, SceneClass.SPEAKER)]
    assert transition_points(single) == []
    mixed = [SceneSegment(0.0, 4.0, SceneClass.SPEAKER, None, False),
             SceneSegment(4.0, 8.0, SceneClass.SPLIT,
                          SlideRect(672.0, 32.0, 352.0, 544.0), False),
             SceneSegment(8.0, 12.0, SceneClass.SLIDE,
                          SlideRect(0.0, 0.0, 1280.0, 720.0), False)]
    points = transition_points(mixed)
    assert [(t, a.value, b.value) for t, a, b in points] == [
        (4.0, "speaker", "split"), (8.0, "split", "slide")]


# ── the layout: a lecture clip that no crop can serve, composed and rendered ── #


def _lecture_video(out: Path) -> Path:
    """A lecture layout the 406 px crop cannot serve, built to be measured.

    The speaker is a bright marker on the left that moves slowly -- moving so
    the cell analysis does not mistake it for static content and report it as
    the slide -- and the slide is a bright static block on the right holding a
    black square. The square is the instrument: a shape of known source size
    whose delivered size measures the scale, and whose delivered *shape*
    measures the squeeze.
    """
    import cv2
    import numpy as np

    frames, fps = 24, 12
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (SRC_W, SRC_H))
    try:
        for i in range(frames):
            frame = np.full((SRC_H, SRC_W, 3), 30, np.uint8)
            # The slide, with a 160x160 marker inside it.
            frame[40:560, 700:1240] = 190
            frame[160:320, 840:1000] = 0
            # The speaker, orbiting slowly inside a known box (below).
            cx, cy = 300 + 20.0 * (i % 7) / 7, 340 + 12.0 * (i % 5) / 5
            cv2.circle(frame, (int(cx), int(cy)), 70, (205, 185, 165), -1)
            writer.write(frame)
    finally:
        writer.release()
    return out


def _changing_video(out: Path) -> Path:
    """A clip whose layout changes midway: a lecture, then a talking head.

    The slide is drawn only for the first half and vanishes on a hard cut, which
    is the case the composition cascade exists for -- the graph has to hand the
    frame from one chain to the other on its own clock. Eight seconds so each
    scene outlives the 2 s minimum hold, and the marker crawls only 3 px a frame
    so it stays inside the 674 px window a SPLIT speaker band can reach over the
    whole clip; a marker that outran its window would lose the speaker half of
    the second layout for a reason the code under test is not responsible for.
    """
    import cv2
    import numpy as np

    frames, fps = 96, 12
    half = frames // 2
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (SRC_W, SRC_H))
    try:
        for i in range(frames):
            frame = np.full((SRC_H, SRC_W, 3), 30, np.uint8)
            if i < half:
                frame[40:560, 700:1240] = 190
                frame[160:320, 840:1000] = 0
            cv2.circle(frame, (300 + 3 * i, 340), 70, (205, 185, 165), -1)
            writer.write(frame)
    finally:
        writer.release()
    return out


class _MarkerDetector:
    """A face detector for the fixture: reports the speaker marker's box.

    The slide detection in ``analyse_scene`` is real -- cell staticness and
    edges -- but a cascade has nothing to find in a drawn marker, so the
    *speaker* half of the scene is supplied here. What is under test is the
    decision and the composition, not the detector, whose own tests run against
    real footage; supplying the box is the same separation ``plan_crop``'s
    tests already use.
    """

    kind = "fixture"
    available = True

    def detect(self, frame):
        import numpy as np
        marker = np.all(frame[:, :, :] > 150, axis=2)
        # The slide block is also bright; the marker is the one on the left.
        marker[:, 600:] = False
        ys, xs = np.nonzero(marker)
        if not xs.size:
            return []
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        return [(float(x0), float(y0), float(x1 - x0), float(y1 - y0), 1.0)]


@pytest.fixture
def composed(tmp_path, monkeypatch):
    """The lecture clip analysed as a SPLIT scene, with the speaker supplied.

    Clears the scene cache on the way in and out: it is process-global, and a
    key that ignored its ``tail`` would otherwise hand a later call this
    fixture's graph.
    """
    import engine.reframe as rf
    import engine.reframe_scenes as rs

    # The detector is patched on ``reframe`` rather than on ``reframe_scenes``:
    # perception moved to ``_perceive_window`` when it became a cached layer, so
    # this is the name the inference resolves. One instance, not a fresh one per
    # ask, because the perception cache keys on detector identity -- a new
    # marker every call would be correct but would make every ask a miss.
    _marker = _MarkerDetector()
    monkeypatch.setattr(rf, "get_detector", lambda *a, **kw: _marker)
    monkeypatch.setattr(rs, "_SCENE_CACHE", {})
    rf._clear_frame_cache()
    return _lecture_video(tmp_path / "lecture.mp4")


@pytest.fixture
def changing(tmp_path, monkeypatch):
    """The clip whose layout changes midway, with the speaker supplied.

    Shares the ``composed`` fixture's cache handling for the same reason.
    """
    import engine.reframe as rf
    import engine.reframe_scenes as rs

    _marker = _MarkerDetector()
    monkeypatch.setattr(rf, "get_detector", lambda *a, **kw: _marker)
    monkeypatch.setattr(rs, "_SCENE_CACHE", {})
    rf._clear_frame_cache()
    return _changing_video(tmp_path / "changing.mp4")


def test_a_split_scene_is_detected_and_classified(composed):
    """The perception and the decision, end to end on the fixture: the slide is
    found, the speaker is found, and the two are too far apart for one crop."""
    import engine.reframe_scenes as rs
    from engine.reframe import vertical_crop_width
    from engine.reframe_scenes import SceneClass

    segments, slides, w, h = rs.analyse_scene(composed, 0.0, 2.0, samples=12)
    assert (w, h) == (SRC_W, SRC_H)
    assert slides, "the slide block must be detected as a content region"
    crop_w = vertical_crop_width(h, 1, 1)
    slide = max(slides, key=lambda s: s.area)
    assert slide.x + slide.w > 600, "the detected slide must be the right block"
    assert all(seg.cls is SceneClass.SPLIT for seg in segments), (
        f"a speaker left of a slide {crop_w}px apart must be a SPLIT scene; "
        f"got {[(s.start, s.end, s.cls.value) for s in segments]}")


def test_the_scene_cache_key_includes_the_tail(composed):
    """The ``tail`` is part of the returned graph, not a caller-side decoration:
    it is folded into the composition's last chain. Two calls that differ only
    in the tail get different graphs -- and before the key carried it, the
    second call received the first's, which silently dropped the subtitles from
    a captioned render whose uncaptioned render had already populated the cache.
    """
    import engine.reframe_scenes as rs

    tail = ("subtitles='/nowhere/captions.ass'",)
    plain = rs.scene_filter(composed, 0.0, 2.0, samples=12)
    captioned = rs.scene_filter(composed, 0.0, 2.0, samples=12, tail=tail)

    # The fixture is a SPLIT scene, so both graphs are composed multi-chain
    # layouts rather than a single -vf chain. That is exactly why the tail has
    # to ride *inside* the composition's output pad: a caller that appended it
    # after the fact would be putting captions after a ``[v]`` label.
    assert rs.is_composed(plain) and rs.is_composed(captioned)
    assert plain != captioned, (
        "the tail is part of the graph; a cache that ignored it would hand the "
        "second caller the first caller's filter")
    assert tail[0] in captioned
    assert "subtitles" not in plain

    # And the hit path is honest: the same question twice gets the same answer.
    again = rs.scene_filter(composed, 0.0, 2.0, samples=12, tail=tail)
    assert again == captioned


def _render(source: Path, graph: str, out: Path, duration: float) -> None:
    """Render either the normal single-crop filter or a multi-scene graph."""
    cmd = ["ffmpeg", "-y", "-i", str(source), "-t", f"{duration}"]
    if ";" in graph:
        cmd += ["-filter_complex", graph, "-map", "[v]"]
    else:
        cmd += ["-vf", graph]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)


def _gray_frames(path: Path) -> tuple[object, int, int]:
    import numpy as np
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,sample_aspect_ratio,display_aspect_ratio",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip().split(",")
    w, h = int(probe[0]), int(probe[1])
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
         "-pix_fmt", "gray", "-"], capture_output=True).stdout
    n = len(raw) // (w * h)
    return np.frombuffer(raw, np.uint8).reshape(n, h, w), w, h


def test_a_split_classification_keeps_both_regions_in_one_vertical_frame(composed, tmp_path):
    """A speaker and a slide no single crop can hold are stacked, not chosen between.

    The fixture classifies as SPLIT, and the deliverable keeps both halves: the
    speaker band above, the slide band below, on the 1080x1920 caption canvas
    the burned ASS is authored for. The old behaviour cropped to the speaker and
    silently deleted the slide's content; the two-band composition is the fix,
    so this test pins that neither region is lost and that the two bands really
    carry different source material rather than one duplicated branch.
    """
    import numpy as np

    import engine.reframe_scenes as rs

    graph = rs.scene_filter(composed, 0.0, 2.0, samples=12)
    # A SPLIT scene is a multi-chain graph even on its own, so it needs
    # -filter_complex plus -map [v]; ``is_composed`` is the caller's signal.
    assert rs.is_composed(graph), (
        f"a SPLIT scene composes its bands rather than cropping to one: {graph}")
    assert "vstack=inputs=2" in graph, "the two regions must be stacked"

    out = tmp_path / "split.mp4"
    _render(composed, graph, out, 2.0)
    frames, w, h = _gray_frames(out)

    # The delivered frame is the caption canvas the source's square pixels are
    # scaled into: 1080x1920, the size the burned ASS is authored for.
    assert (w, h) == (1080, 1920), (
        f"the composition must deliver on the caption canvas; got {w}x{h}")
    assert abs(w / h - 9 / 16) < 0.005, f"the frame must display as 9:16; got {w}x{h}"

    frame = frames[len(frames) // 2]
    # The band split the layout uses: the speaker takes the larger upper band.
    split_y = int(h * 0.58)
    top, bottom = frame[:split_y], frame[split_y:]
    assert (top > 150).any(), "the speaker marker must survive in the top band"
    assert (bottom > 150).any(), "the slide block must survive in the bottom band"
    # A graph that duplicated one source branch into both pads would stack the
    # same picture twice; the two bands come from disjoint source regions, so
    # their colours differ by far more than codec noise.
    band_gap = float(np.mean(np.abs(
        top.mean(axis=0).mean(axis=0).astype(float)
        - bottom.mean(axis=0).mean(axis=0).astype(float)
    )))
    assert band_gap > 2.0, (
        f"the two split bands appear to contain the same visual source "
        f"(gap {band_gap:.2f}); one branch was duplicated")


# ── the composition across a mid-clip change of layout ── #

# The fixture's own clock; the interval the render is asked about.
CHG_FPS, CHG_SPAN = 12, 8.0


def test_a_mid_clip_class_change_composes_and_keeps_the_interval(changing, tmp_path):
    """Two layouts in one clip, rendered as a dissolve between two chains.

    This is the path ``_graph`` documents hardest and the single-scene tests do
    not reach: the perception has to see the class change, the plan has to hold
    two scenes, and the graph has to rebase each chain onto its own clock and
    crossfade between them. Three things are measured, because the failure mode
    of each is silent:

    * the deliverable keeps the whole interval. The last sample of the analysis
      grid lands on the interval's end, seeks past the final frame and decodes
      as nothing, so the plan used to stop a sample short and the composed
      graph -- which trims each chain to its segment -- rendered 94 frames of an
      8 s clip while its audio played all 96. The interval is what the caller
      asked about, not what the grid managed to decode;
    * the dissolve lands between two different chains, so the frame really does
      change layout rather than crossfading a chain with itself;
    * the rebased clocks agree with the wall clock: the first frame is still the
      first layout and the last frame has settled on the second. A wrong PTS
      rebase shows up as the picture lagging the transition.
    """
    import numpy as np

    import engine.reframe_scenes as rs
    from engine.reframe_scenes import SceneClass

    segments, slides, w, h = rs.analyse_scene(
        changing, 0.0, CHG_SPAN, samples=CHG_FPS * 4)
    assert (w, h) == (SRC_W, SRC_H)
    classes = {seg.cls for seg in segments}
    assert SceneClass.SPLIT in classes and SceneClass.SPEAKER in classes, (
        f"the clip changes layout, so the plan must hold both classes; got "
        f"{[(s.start, s.end, s.cls.value) for s in segments]}")
    assert len(segments) > 1, "a change of layout must not be absorbed into one scene"

    graph = rs.scene_filter(changing, 0.0, CHG_SPAN, samples=CHG_FPS * 4)
    assert rs.is_composed(graph), (
        f"two layouts must build a composed graph, not a plain -vf chain: {graph}")
    assert "xfade=" in graph, "the segment boundary must be a dissolve between chains"

    out = tmp_path / "changed.mp4"
    _render(changing, graph, out, CHG_SPAN)
    frames, w, h = _gray_frames(out)

    # The interval, frame for frame. Not the sum of the trims -- the xfade reads
    # each chain on its own clock and the total has to come back to the duration
    # the EDL stamps and the audio fills.
    assert len(frames) == round(CHG_FPS * CHG_SPAN), (
        f"the deliverable must keep the whole {CHG_SPAN}s interval; got "
        f"{len(frames)} frames of {CHG_FPS * CHG_SPAN:g}")

    # Each half carries the layout its scene called for: the opening SPLIT
    # stacks its two bands, and the closing lone speaker stays a single crop. A
    # graph that stacked the speaker-only half would be composing a band no
    # scene asked for, and one that cropped the SPLIT half would be back to
    # discarding the slide.
    assert graph.count("vstack=inputs=2") == 1, (
        "only the SPLIT half stacks its bands; the speaker-only half must be "
        f"one crop: {graph}")
    assert graph.count(f"crop={CROP_W}:{SRC_H}:") == 1, (
        "only the speaker-only half is a plain tracked crop: {graph}")
    first = frames[0]
    assert (first > 150).any(), "the clip must open with the speaker in frame"

    # The last frame has settled on the second layout: one band, no slide block
    # with the marker still in frame.
    last = frames[-1]
    assert (last > 150).mean() < 0.15, (
        f"the slide is gone by the end of the clip; got "
        f"{(last > 150).mean():.0%} bright pixels, which is a band, not a head")
    bright = last > 150
    assert bright.any(), "the speaker marker must survive into the second layout"
    xs = np.nonzero(bright)[1]
    assert 0.2 < xs.mean() / w < 0.9, (
        f"the marker is inside the tracked window; got x fraction "
        f"{xs.mean() / w:.2f}, which is off-frame or in the slide's columns")


def test_panel_detection_marks_a_spread_multi_person_shot():
    """A shot holding three or more faces spread over most of the frame width
    is a seated panel, and the scene planner marks it for the card layout.

    Measured defect, on the NASA Crew-8 media Q&A: five panelists at 22-31 px
    faces in a 640x360 source, spanning ~72% of the frame, whose backdrop
    detection composed a full-bleed layout where no panelist was readable. The
    spread rule is what says "this shot seats people side by side": a single
    presenter in front of a real presentation (or two seated close together)
    never fires it, so the split layout keeps its content half there.
    """
    import engine.reframe_scenes as rs

    det = lambda x: [(x, 96.0, 24.0, 30.0, 0.93)]  # noqa: E731
    spread = [
        det(64.0), det(196.0), det(311.0), det(424.0), det(530.0),
    ]
    assert rs._panel_shot(spread, 640.0, 360.0), \
        "five faces across 72% of the width is a panel"

    clustered = [
        [(196.0, 96.0, 24.0, 30.0, 0.93)],
        [(205.0, 98.0, 24.0, 30.0, 0.93)] * 3,
    ]
    assert not rs._panel_shot(clustered, 1280.0, 720.0), \
        "faces within ~8% of the source width are one person"

    two_close = [
        [(540.0, 96.0, 24.0, 30.0, 0.93)] * 2,
        [(640.0, 98.0, 24.0, 30.0, 0.93)],
    ]
    assert not rs._panel_shot(two_close, 1280.0, 720.0), \
        "two close presenters keep the split layout's content half"


def test_a_panel_segment_renders_the_group_card_not_a_crop():
    """The panel layout is a 16:9 card of the whole scene floating on a
    blurred fill: full width, centred vertically, no speaker crop that has to
    choose which seated panelist stays readable."""
    from engine.reframe_scenes import SceneClass, _layout_chains

    from engine.reframe_scenes import SceneSegment

    s = SceneSegment(0.0, 2.0, SceneClass.SPEAKER, None, False, None, True)
    chains = _layout_chains(s, 1280, 720, 1, 1, CROP_W, 720, 1280,
                            [], "", None)
    graph = "".join(chains)
    # One layout, expressed as the labelled card-over-fill subgraph.
    assert "overlay=" in graph and "gblur" in graph, \
        "the panel card composes a blurred fill under a centred card"
    # The card keeps the source's aspect: 720-wide card of a 720p source in a
    # 1280-tall canvas.
    assert "scale=720:406" in graph, graph
    assert "overlay=0:438" in graph, graph
