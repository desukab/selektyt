"""The scene-aware reframe: what the clip is made of, and how it is composed.

``engine.reframe_scenes`` is the two-region composition the single-crop reframe
cannot serve. Three things are pinned here, in the order the module builds them:

1. the *decision* -- given a speaker and a content region, does one legal crop
   hold both, or is the layout one the crop has to concede? These are pure, so
   the mandated regressions (a slide appearing, a speaker leaving, a slide too
   large to compose beside) are numbers rather than a guess at what a detector
   would see;
2. the *plan* -- per-sample classes become held, shot-aligned, contiguous
   segments, because the composed graph trims each segment to its own window and
   a gap between two would drop video while the audio played on;
3. the *layout* -- a SPLIT really does render two bands that each carry their
   own region of the source, at the delivered frame's pixel aspect, with the
   composition measured in pixels rather than read off the filtergraph text.
"""
from __future__ import annotations

import re
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
    """Render a composed graph. A labelled multi-chain description needs
    ``-filter_complex`` and an explicit ``[v]`` map; a plain ``-vf`` rejects it
    (measured: "Error initializing a simple filtergraph")."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), "-t", f"{duration}",
         "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-an", str(out)],
        check=True, capture_output=True)


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


def _band_chain(graph: str, label: str) -> tuple[int, int, int, int]:
    """One band's declared source window and delivered size, read out of the
    graph: ``(window_w, window_h, delivered_w, delivered_h)``.

    The squeeze assertion compares the marker's delivered size against the
    isotropic scaling the graph itself declares, so it follows the layout
    instead of pinning one set of numbers to it. The band split is read the
    same way rather than assumed, so the measurement cannot be silently
    inverted against a changed layout.
    """
    for chain in graph.split(";"):
        if chain.startswith(f"[{label}]"):
            m = re.search(r"crop=(\d+):(\d+):x='[^']*':y='[^']*',"
                          r"scale=(\d+):(\d+)", chain)
            assert m, f"the {label} chain must declare its window; got {chain!r}"
            return tuple(int(g) for g in m.groups())
    raise AssertionError(f"no {label} band in the graph: {graph}")


def test_a_split_renders_two_bands_that_each_hold_their_region(composed, tmp_path):
    """The centrepiece, measured in pixels: the speaker is in one band, the
    slide is in the other, and neither content is squeezed.

    Geometry alone cannot say this -- the crop and scale can be exactly right on
    paper and still carry the wrong region, or carry both, which is the failure
    the two-window layout exists to repair. So the frame is rendered and read
    back, and each band is asked what it actually shows.
    """
    import numpy as np

    import engine.reframe_scenes as rs

    graph = rs.scene_filter(composed, 0.0, 2.0, samples=12)
    assert rs.is_composed(graph), (
        f"a SPLIT scene must build a composed graph, not a plain -vf chain: "
        f"{graph}")
    assert "vstack=inputs=2" in graph

    out = tmp_path / "split.mp4"
    _render(composed, graph, out, 2.0)
    frames, w, h = _gray_frames(out)

    # The delivered frame is the 9:16 frame the source's square pixels imply.
    assert w == 406, f"the crop width is {CROP_W}; got {w}"
    assert abs(w / h - 9 / 16) < 0.005, f"the frame must display as 9:16; got {w}x{h}"

    # The band split the graph declared, read out of it rather than assumed.
    # The speaker band is vstacked first, so it is the frame's top rows.
    _, _, sp_w, band_h = _band_chain(graph, "a")
    win_w, win_h, sl_w, sl_h = _band_chain(graph, "b")
    assert sp_w == w and sl_w == w, "both bands must deliver the full frame width"
    assert band_h + sl_h == h, (
        f"the bands declare {band_h}+{sl_h} rows but the frame is {h} rows")
    speaker, slide = frames[len(frames) // 2][:band_h], frames[len(frames) // 2][band_h:]
    assert band_h > 0 and h - band_h > 0, "both bands must be non-empty"

    # The speaker band: the marker is bright, and the slide block -- which lives
    # at source columns the speaker window does not contain -- cannot be in it.
    speaker_bright = speaker > 150
    assert speaker_bright.sum() > 0, "the speaker marker must be in its band"
    ys, xs = np.nonzero(speaker_bright)
    assert xs.mean() / w < 0.6, (
        f"the marker sits at source x~300 of a 0..674 window, so it lands left "
        f"of centre; got {xs.mean() / w:.2f}")
    assert speaker_bright.mean() < 0.15, (
        "the speaker band must not be carrying the slide block as well")

    # The slide band: mostly the block, with the marker's black square inside it.
    slide_bright = slide > 150
    assert slide_bright.mean() > 0.5, (
        f"the slide band must hold the slide block; got {slide_bright.mean():.0%}")
    cols = np.nonzero(slide_bright.any(axis=0))[0]
    rows = np.nonzero(slide_bright.any(axis=1))[0]
    block = slide[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
    hole = block < 100
    assert hole.any(), "the 160x160 marker must survive into the slide band"
    hcols = np.nonzero(hole.any(axis=0))[0]
    hrows = np.nonzero(hole.any(axis=1))[0]
    mw = hcols.max() - hcols.min() + 1
    mh = hrows.max() - hrows.min() + 1

    # A shape of known source size, scaled by the factors the graph declares.
    # Squeeze is when x and y disagree, so both are checked against the
    # isotropic expectation and against each other.
    delivered_w, delivered_h = sl_w, sl_h
    expect_w = 160.0 * delivered_w / win_w
    expect_h = 160.0 * delivered_h / win_h
    assert abs(mw / expect_w - 1) < 0.08, (
        f"the marker is {mw}px wide, {mw / expect_w:.2f}x the isotropic scale; "
        f"a window of {win_w}px onto {delivered_w}px cannot disagree with itself")
    assert abs(mh / expect_h - 1) < 0.08, (
        f"the marker is {mh}px tall, {mh / expect_h:.2f}x the isotropic scale")
    assert abs(mw / mh - 1) < 0.06, (
        f"a 160x160 source square arrives as {mw}x{mh}; the band's pixel aspect "
        f"and its window's must match or the content is distorted")


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

    # The first frame is still the first layout: two bands, the lower one the
    # slide block. The split is read out of the graph for the same reason the
    # single-scene test reads it.
    _, _, _, band_h = _band_chain(graph, "a")
    first = frames[0]
    assert (first[band_h:] > 150).mean() > 0.5, (
        "the clip opens on the lecture; the slide band must carry the slide block")
    assert (first[:band_h] > 150).any(), (
        "and its speaker band must carry the speaker marker")

    # The last frame has settled on the second layout: one band, no slide block
    # anywhere in it, and the marker still in frame. A slide band is mostly
    # bright; a talking head is mostly background with a marker in it.
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

    # And the layout genuinely changed: the two ends differ by more than the
    # marker moving, because the first holds a bright band the second does not.
    assert first[band_h:].mean() - last[band_h:].mean() > 50, (
        "the last frame is not the first layout; the dissolve changed something")
