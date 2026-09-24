"""Reframe regressions: the editorial behaviour, not the coordinate plausibility.

The contract these tests enforce is the one the product is judged on: the
vertical output keeps the active subject in frame, it switches subjects
deliberately rather than oscillating, it does not read a hard cut as a subject
walking across the room, and it degrades to a safe composition when perception
is unsure of itself.

Two families, deliberately separate:

* **Synthetic detection streams** drive ``engine.reframe.plan_crop`` directly.
  They are labelled synthetic in every name because they are *not* evidence that
  the detector works -- they are evidence about what the layers behind the
  detector do with a given detection stream. ``reframe_perception`` has its own
  tests against real footage. The reason for the split is that the previous
  generation of these behaviours could not be tested this way at all, so a
  broken election hid behind a working Haar cascade, and neither was verified.

* **Rendered output** checks score the frames the pipeline actually ships, with
  an independent detector, after a real ffmpeg encode. These are the tests that
  would have caught the shipped failure: a face in the empty middle of a
  two-shot while the crop sat on nobody. They skip when the corpus media is not
  present, because the corpus is local-only.
"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest

from engine.reframe import (
    _reframe_track,
    _x_expression,
    plan_crop,
    track_subject_center,
    vertical_crop_width,
    vertical_reframe_filter,
)
from engine.reframe_perception import FaceDetector

REPO = Path(__file__).resolve().parent.parent

# The corpus media is local-only and large; the rendered tests skip rather than
# fabricate footage, and say so in the report rather than passing vacuously.
PENTAGON = REPO / "corpus" / "media" / "pentagon-esper-briefing.mp4"
SELECTIONS = REPO / "corpus" / "pentagon-esper-briefing_run" / "project" / "selections.json"

_HAVE_CORPUS = PENTAGON.is_file() and SELECTIONS.is_file()

# A face comfortably inside the crop needs this much clearance on either side,
# in units of its own width. Matches tools/reframe_benchmark.py so a test
# failure and a benchmark report use one definition of "well framed".
MARGIN_FRACTION_OK = 0.35

# ── shared geometry ────────────────────────────────────────────────────────

SRC_W, SRC_H = 1280, 720           # 16:9, the common case
CROP_W = vertical_crop_width(SRC_H, 1, 1)      # 406
STEP_S = 0.5                       # the planner's sample cadence


def _times(duration_s: float, step: float = STEP_S) -> list[float]:
    return [round(i * step, 3) for i in range(int(duration_s / step) + 1)]


def _face(cx_frac: float, w_frac: float = 0.10, score: float = 0.95,
          src_w: int = SRC_W, src_h: int = SRC_H) -> tuple[float, float, float, float, float]:
    """One detection box in source pixels.

    ``y`` is the top of the box, chosen to sit a realistic head's worth below
    the frame top. Nothing here depends on it -- a 9:16 crop of a 16:9 source
    has no vertical freedom, so y cannot affect the outcome -- but a box parked
    at y=0 would be a strange face.
    """
    w = max(8, int(w_frac * src_w))
    h = int(w * 1.2)
    x = max(0.0, min(src_w - w, cx_frac * src_w - w / 2.0))
    y = int(src_h * 0.18)
    return (x, y, float(w), float(h), score)


def _crop_window(center_frac: float, src_w: int = SRC_W, crop_w: int = CROP_W
                 ) -> tuple[float, float]:
    max_x = max(0.0, float(src_w - crop_w))
    x0 = float(np.clip(src_w * center_frac - crop_w / 2.0, 0.0, max_x))
    return x0, x0 + float(crop_w)


def _centers(pts: list[tuple[float, float]]) -> list[float]:
    return [c for _, c in pts]


def _interpolate(pts: list[tuple[float, float]], t: float) -> float:
    """The value the ffmpeg expression yields at ``t``: piecewise linear,
    clamped at both ends, exactly like ``_x_expression`` does."""
    if not pts:
        return 0.5
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for i in range(1, len(pts)):
        if pts[i][0] >= t:
            t0, c0 = pts[i - 1]
            t1, c1 = pts[i]
            return float(np.clip(c0 + (c1 - c0) * (t - t0) / max(1e-6, t1 - t0), 0.0, 1.0))
    return pts[-1][1]


def _travel(pts: list[tuple[float, float]]) -> float:
    """Total path in fractions of source width, not displacement: a camera that
    went somewhere and came back moved twice."""
    c = _centers(pts)
    return float(sum(abs(b - a) for a, b in zip(c, c[1:])))


def _face_is_well_framed(face, center_frac) -> bool:
    x0, x1 = _crop_window(center_frac)
    fcx = face[0] + face[2] / 2.0
    if not (x0 <= fcx <= x1):
        return False
    return min(fcx - x0, x1 - fcx) >= MARGIN_FRACTION_OK * face[2]


# ── 1. one moving speaker ─────────────────────────────────────────────────


def test_moving_single_speaker_is_followed_and_kept_framed():
    """The baseline case: a speaker crosses the frame and the crop stays with
    them without clipping the face."""
    times = _times(30.0)
    path = np.linspace(0.20, 0.70, len(times))
    dets = [[_face(float(p))] for p in path]

    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)

    assert len(pts) >= 4, "a moving subject must produce a time-varying crop"
    assert _interpolate(pts, times[-1]) > 0.60, (
        "the crop must end where the speaker ended"
    )
    assert _interpolate(pts, 0.0) < 0.40
    for t, face in zip(times, dets):
        c = _interpolate(pts, t)
        assert _face_is_well_framed(face[0], c), (
            f"t={t:.1f}s: speaker at {face[0] + face[2] / 2:.0f}px lost or clipped "
            f"by crop at centre {c:.3f}"
        )
    # The crop must not roam: following a steady walk is one deliberate move.
    assert _travel(pts) < 2.5 * abs(0.70 - 0.20), (
        f"crop travel {_travel(pts):.3f}xw far exceeds the subject's own "
        f"0.50xw traverse"
    )


# ── 2/3. two speakers: deliberate switch, no oscillation ──────────────────


def test_two_speakers_switch_once_and_deliberately():
    """A speaks alone, then both overlap, then B alone. The crop must move to B
    once and stay: A->B->A->A->B from detection noise is the named failure."""
    times = _times(30.0)
    dets, boundaries = [], set()
    for i, t in enumerate(times):
        row = []
        if t < 18.0:
            row.append(_face(0.25))
        if 12.0 <= t <= 24.0:
            row.append(_face(0.72))
        dets.append(row)

    pts = plan_crop(times, dets, boundaries, SRC_W, SRC_H, CROP_W)
    c = _centers(pts)

    early = _interpolate(pts, 6.0)
    assert abs(early - 0.25) < 0.10, f"while only A speaks the crop holds A, got {early:.3f}"
    late = _interpolate(pts, 28.0)
    assert abs(late - 0.72) < 0.10, f"once only B speaks the crop holds B, got {late:.3f}"

    # Exactly one crossing of the midpoint between them: oscillation would show
    # up as three or more.
    mid = 0.485
    crossings = sum(1 for a, b in zip(c, c[1:]) if (a - mid) * (b - mid) < 0)
    assert crossings == 1, f"a handover is one deliberate move, saw {crossings}"


def test_a_passing_second_person_does_not_steal_the_crop():
    """B appears for a few seconds in a two-shot and leaves again. The crop
    holds the established speaker: a transient second detection is not a
    speaker change."""
    times = _times(30.0)
    dets = []
    for t in times:
        row = [_face(0.30)]
        if 10.0 <= t <= 14.0:
            row.append(_face(0.70, score=0.93))
        dets.append(row)

    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)

    for t in times:
        c = _interpolate(pts, t)
        assert c < 0.45, (
            f"t={t:.1f}s: the crop drifted toward a transient passer-by "
            f"(centre {c:.3f})"
        )


def test_low_confidence_rival_does_not_displace_a_confident_speaker():
    """The same layout, but the second face is detected at 0.6 rather than 0.93
    -- an uncertain sighting must not outweigh a certain one."""
    times = _times(30.0)
    dets = []
    for t in times:
        row = [_face(0.30)]
        if t >= 10.0:
            row.append(_face(0.70, score=0.60))
        dets.append(row)

    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)
    for t in times:
        assert _interpolate(pts, t) < 0.45, (
            f"t={t:.1f}s: an uncertain rival displaced a confident speaker")


def test_single_frame_false_detections_are_ignored():
    """Isolated high-score detections appearing one frame at a time at random
    positions. A single sighting carries almost no presence, so it must not
    move the crop at all."""
    rng = np.random.default_rng(20260923)
    times = _times(30.0)
    dets = []
    for t in times:
        row = [_face(0.32)]
        if t > 2.0 and rng.random() < 0.35:
            # A loud but unsupported detection, somewhere far away.
            row.append(_face(float(rng.uniform(0.7, 0.95)), score=0.99))
        dets.append(row)

    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)

    for t in times:
        c = _interpolate(pts, t)
        assert abs(c - 0.32) < 0.10, (
            f"t={t:.1f}s: isolated false detections moved the crop to {c:.3f}"
        )


# ── 4. shot cuts reset the tracker, never read as motion ──────────────────


def test_a_hard_cut_is_not_read_as_subject_motion():
    """Before the cut the speaker is at 0.15 in a close-up; after it they are at
    0.65. The two are close enough in pixels that a distance matcher *would*
    chain them into one moving track, which is exactly the misreading the brief
    names. The boundary marker must prevent it, and the crop must re-establish
    on the new subject instead of drifting after a phantom.
    """
    times = _times(30.0)
    cut_at = times.index(min(times, key=lambda t: abs(t - 10.0)))
    dets = []
    for i, t in enumerate(times):
        if i <= cut_at:
            dets.append([_face(0.15, w_frac=0.24)])
        else:
            dets.append([_face(0.65, w_frac=0.24)])

    pts = plan_crop(times, dets, {cut_at}, SRC_W, SRC_H, CROP_W)

    # The crop has re-established on the new subject by 2s after the cut.
    settled = _interpolate(pts, times[cut_at] + 2.0)
    assert settled > 0.50, (
        f"after a cut the crop must snap to the new subject, was at "
        f"{settled:.3f} after 2s"
    )
    late = _interpolate(pts, 28.0)
    assert abs(late - 0.65) < 0.10, f"the new subject is held, got {late:.3f}"


def test_without_the_boundary_marker_a_cut_looks_like_motion():
    """The negative control for the above: the same detections with no boundary
    get chained into one subject's walk. This is the regression's signature --
    if the perception layer stops emitting boundaries, this test fails and says
    why rather than the failure surfacing as a twitchy Short.
    """
    from engine.reframe import _MAX_JUMP_AFTER_CUT_WIDTHS, _MAX_JUMP_WIDTHS, _Track, _update_tracks

    wide = _face(0.15, w_frac=0.24)
    far = _face(0.65, w_frac=0.24)
    gap_px = abs((far[0] + far[2] / 2) - (wide[0] + wide[2] / 2))

    same_shot: list[_Track] = []
    _update_tracks(same_shot, [wide], 0.0, after_cut=False)
    _update_tracks(same_shot, [far], 0.5, after_cut=False)
    assert len(same_shot) == 1, (
        "the close-up and the post-cut subject are "
        f"{gap_px:.0f}px apart with a {wide[2]:.0f}px face, i.e. within "
        f"{_MAX_JUMP_WIDTHS} face widths, so a matcher without the cut marker "
        "must chain them -- if it does not, the boundary feature is inert"
    )

    across_cut: list[_Track] = []
    _update_tracks(across_cut, [wide], 0.0, after_cut=False)
    _update_tracks(across_cut, [far], 0.5, after_cut=True)
    assert len(across_cut) == 2, (
        "the same two detections across a cut are "
        f"{gap_px / wide[2]:.1f} face widths apart, above the "
        f"{_MAX_JUMP_AFTER_CUT_WIDTHS}-width post-cut allowance, so the cut "
        "must start a fresh track rather than inherit the old subject's position"
    )


# ── 5/6/8. losing the subject: hold, do not guess ──────────────────────────


def test_temporary_detection_loss_holds_the_last_known_composition():
    """The detector loses the speaker for a few seconds (a turn to profile, a
    hand in front of the lens) and finds them slightly moved. The crop must not
    snap to centre or to a rival while it cannot see."""
    times = _times(30.0)
    dets = []
    for t in times:
        if 10.0 <= t <= 14.0:
            dets.append([])                      # the blind window
        else:
            dets.append([_face(0.30 if t < 10.0 else 0.34)])
    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)

    held = _interpolate(pts, 12.0)
    assert abs(held - 0.30) < 0.08, (
        f"while the subject is out of view the crop holds where it last saw "
        f"them, was at {held:.3f}"
    )
    # And the recovery is not a jump: still near the speaker, not a snap to 0.5.
    recovered = _interpolate(pts, 16.0)
    assert abs(recovered - 0.34) < 0.08, f"recovery tracks the subject, got {recovered:.3f}"
    assert _travel(pts) < 0.25, f"the blind window cost {_travel(pts):.3f}xw of roaming"


def test_occlusion_by_a_larger_subject_does_not_switch():
    """A's face is briefly covered by a bigger box at a different x. The cover
    is not a new speaker, and once it clears A is still there."""
    times = _times(30.0)
    dets = []
    for t in times:
        row = [_face(0.30)]
        if 12.0 <= t <= 15.0:
            # Larger, higher score, and overlapping: an occluding body.
            row.append(_face(0.44, w_frac=0.20, score=0.97))
        dets.append(row)
    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)

    before, after = _interpolate(pts, 9.0), _interpolate(pts, 20.0)
    assert abs(before - 0.30) < 0.08 and abs(after - 0.30) < 0.08, (
        f"an occluding body moved the crop off the speaker "
        f"({before:.3f} -> {after:.3f})"
    )


def test_total_confidence_loss_freezes_the_crop():
    """Every detection goes from 0.95 to 0.2. With nothing trustworthy the crop
    must not chase the noise; it holds the last composition it was sure of,
    which is the 'safe wider composition' the brief asks for."""
    times = _times(30.0)
    dets = []
    for i, t in enumerate(times):
        score = 0.95 if t < 10.0 else 0.20
        dets.append([_face(0.30, score=score)])
    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)

    sure = _interpolate(pts, 8.0)
    unsure = _interpolate(pts, 28.0)
    assert abs(sure - unsure) < 0.10, (
        f"when confidence collapses the crop holds instead of roaming "
        f"({sure:.3f} -> {unsure:.3f})"
    )


def test_a_faceless_interval_produces_no_keypoints():
    """No detections at all: the planner has no subject to follow, and returns
    nothing so the caller's fallback path takes over rather than rendering a
    crop keyed to a centre the planner invented."""
    times = _times(20.0)
    pts = plan_crop(times, [[] for _ in times], set(), SRC_W, SRC_H, CROP_W)
    assert pts == [], f"no detections must mean no keypoints, got {pts}"


def test_track_centre_interpolates_between_sightings():
    """Covered directly because a wrong tuple shape in ``_Track.center`` is
    invisible to the file-level tests: those run the planner only on footage
    with no faces, so the interpolation path was never executed when it was
    broken. A sighting-span interpolation must return the midpoint between two
    detections, and None outside the span so the election layer can tell a
    subject is not there."""
    from engine.reframe import _Track

    tr = _Track([(0.0, 100.0, 50.0, 60.0, 72.0, 0.95)])
    assert tr.center(0.0) == (130.0, 86.0), "a single sighting is its own centre"
    assert tr.center(-1.0) is None and tr.center(1.0) is None, (
        "outside the span there is nothing to interpolate"
    )

    tr = _Track([(0.0, 100.0, 0.0, 60.0, 72.0, 0.95),
                 (1.0, 220.0, 0.0, 60.0, 72.0, 0.93)])
    assert tr.center(0.0)[0] == 130.0
    assert abs(tr.center(0.5)[0] - 190.0) < 1e-6, "linear midpoint between sightings"
    assert tr.center(2.0) is None, (
        "outside the span there is nothing to interpolate; _update_tracks "
        "compensates by falling back to the last sighting's own time"
    )


# ── 7/10. geometry: boundary safety, portrait, wide and close shots ───────


def test_the_crop_expression_lands_on_the_subject_not_offset_by_a_crop_width():
    """The keypoint centre is a fraction of the *source width*, so the crop
    window's left edge is ``src_w * c - crop_w / 2``. An earlier version wrote
    ``(src_w - crop_w) * c - crop_w / 2``, which differs by ``crop_w * c`` --
    a whole 406 px window for a speaker standing at c = 1.0. Every consumer
    that scores the trajectory used the correct mapping, so the source-space
    metrics reported a centred subject while the shipped render landed on
    empty frame and lost him for the entire clip. This is the unit test that
    pins the mapping the delivered ffmpeg expression actually uses."""
    src_w, crop_w = SRC_W, CROP_W

    def _window_left(expr: str, t: float) -> float:
        # the expression's own evaluation rule: if(lt(t,T),A+(B-A)*(t-T0)/(T1-T0),else)
        import re
        best = None
        stack = [(expr, 0.0)]
        while stack:
            cur, depth = stack.pop()
            m = re.match(r"if\(lt\(t,([\d.]+)\),(-?[\d.]+)\+\((-?[\d.]+)-(-?[\d.]+)\)"
                         r"\*\(t-([\d.]+)\)/([\d.]+),(.*)\)$", cur.strip())
            if not m:
                continue
            t1, x0, x1, _x1b, t0, span, rest = m.groups()
            if t < float(t1):
                left = float(x0) + (float(x1) - float(x0)) * (t - float(t0)) / float(span)
                best = left if best is None else best
            else:
                stack.append((rest, depth + 1))
        return best if best is not None else float(expr)

    # a stationary subject at three quarters of the width
    expr = _x_expression(src_w, crop_w, [(0.0, 0.75), (10.0, 0.75)])
    assert _window_left(expr, 0.0) == pytest.approx(src_w * 0.75 - crop_w / 2, abs=0.5)

    # the case the defect destroyed: a confident speaker on the far side
    far = 0.7145
    expr = _x_expression(src_w, crop_w, [(0.0, far)])
    left = float(expr)
    assert left == pytest.approx(src_w * far - crop_w / 2, abs=0.5)
    assert src_w * far - crop_w / 2 <= left, "no clamp needed this far inside"
    # and the window genuinely covers him, which is the point
    assert left < src_w * far < left + crop_w

    # a subject past the legal range clamps rather than leaving the source
    expr = _x_expression(src_w, crop_w, [(0.0, 1.0)])
    assert float(expr) == pytest.approx(src_w - crop_w, abs=0.5)
    expr = _x_expression(src_w, crop_w, [(0.0, 0.0)])
    assert float(expr) == pytest.approx(0.0, abs=0.5)

    # no track at all: middle of the available range, not zero
    mid = float(_x_expression(src_w, crop_w, []))
    assert mid == pytest.approx((src_w - crop_w) / 2, abs=0.5)
    assert 0 < mid < src_w - crop_w




@pytest.mark.parametrize("cx", [0.02, 0.08, 0.20, 0.50, 0.80, 0.94, 0.99])
def test_crop_boundary_safety_across_the_frame(cx):
    """For a subject held anywhere across the width, the crop must keep the
    face's centre inside and keep most of the face on screen. A subject at the
    far edge of a wide shot is the case the margin terms exist for."""
    times = _times(10.0)
    dets = [[_face(cx)] for _ in times]
    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)

    for t in times:
        x0, x1 = _crop_window(_interpolate(pts, t))
        face = dets[times.index(t)][0]
        fcx = face[0] + face[2] / 2.0
        assert x0 <= fcx <= x1, (
            f"cx={cx} t={t:.1f}s: face centre {fcx:.0f}px outside crop "
            f"[{x0:.0f},{x1:.0f}]"
        )
        kept = min(face[0] + face[2], x1) - max(face[0], x0)
        assert kept >= 0.6 * face[2], (
            f"cx={cx} t={t:.1f}s: only {kept / face[2]:.0%} of the face is "
            f"inside the crop; a face should not be bisected"
        )


def test_wide_shot_subject_is_still_centred():
    """A small face in a wide frame: the crop must still find and hold it rather
    than defaulting to the middle of a mostly-empty shot."""
    times = _times(20.0)
    dets = [[_face(0.28, w_frac=0.045)] for _ in times]
    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)

    c = _interpolate(pts, 10.0)
    assert abs(c - 0.28) < 0.06, f"a wide shot's subject must still be held, got {c:.3f}"
    assert _face_is_well_framed(dets[10][0], c)


def test_extreme_close_up_is_not_bisected():
    """A face wider than the crop itself: there is no good answer, but the least
    bad one keeps the face's centre on screen and does not saw through it."""
    times = _times(20.0)
    dets = [[_face(0.50, w_frac=0.45)] for _ in times]
    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)

    x0, x1 = _crop_window(_interpolate(pts, 10.0))
    face = dets[10][0]
    fcx = face[0] + face[2] / 2.0
    assert x0 <= fcx <= x1, "the face's centre must stay on screen"
    # And the crop does not park the face half-off the frame out of indecision.
    assert min(fcx - x0, x1 - fcx) >= 0.15 * face[2], (
        "a close-up face should not be pushed against the frame edge"
    )


def test_portrait_source_has_no_horizontal_freedom():
    """A portrait source is already narrower than 9:16, so the crop is the whole
    frame and the planner must say so: a trajectory that 'tracks' a subject
    across a portrait frame is describing a crop the render cannot perform."""
    pw, ph = 720, 1280
    crop = vertical_crop_width(ph, 1, 1)
    assert crop == pw, "a 9:16 crop of a 720x1280 portrait frame is the frame itself"

    times = _times(20.0)
    # A subject 'moving' -- in a portrait source this cannot be followed.
    dets = [[_face(0.30 + 0.02 * i, src_w=pw, src_h=ph)] for i, _ in enumerate(times)]
    pts = plan_crop(times, dets, set(), pw, ph, crop)

    assert all(abs(c - 0.5) < 1e-6 for _, c in pts), (
        "with crop width == source width the only valid position is centred, "
        f"got {pts[:4]}"
    )


# ── 9/11. motion quality: smoothing, jitter, caption geometry ──────────────


def test_crop_smoothing_absorbs_subject_jitter():
    """The subject fidgets +/-2% of the width several times a second. A camera
    operator does not replicate that, so the crop's travel must be a fraction of
    the subject's."""
    times = _times(20.0)
    wiggle = [0.40 + 0.02 * math.sin(2 * math.pi * 1.5 * t) for t in times]
    dets = [[_face(p)] for p in wiggle]
    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)

    subject_travel = sum(abs(b - a) for a, b in zip(wiggle, wiggle[1:]))
    crop_travel = _travel(pts)
    assert crop_travel < 0.35 * subject_travel, (
        f"crop travelled {crop_travel:.3f}xw following {subject_travel:.3f}xw "
        f"of fidgeting"
    )
    # And it stayed on the subject while doing so.
    for t, w in zip(times, wiggle):
        assert abs(_interpolate(pts, t) - w) < 0.05, "smoothing lost the subject"


def test_crop_acceleration_is_bounded():
    """Second-difference of the crop position: a step change in velocity is what
    reads as a twitch, so it must stay small relative to the crop."""
    times = _times(20.0)
    dets = [[_face(0.20 + 0.03 * t)] for t in times]          # a steady walk
    pts = plan_crop(times, dets, set(), SRC_W, SRC_H, CROP_W)
    xs = [c * SRC_W for _, c in pts]
    accel = [abs(xs[i + 1] - 2 * xs[i] + xs[i - 1]) for i in range(1, len(xs) - 1)]
    assert max(accel) < 0.06 * CROP_W, (
        f"crop acceleration reached {max(accel):.0f}px/sample "
        f"({max(accel) / CROP_W:.0%} of the crop)"
    )


def test_vertical_crop_cannot_move_a_face_out_of_the_caption_band(tmp_path):
    """The honest geometry of the deliverable, stated as a test so it is not
    'fixed' by a mechanism the render cannot have.

    A 9:16 crop of a landscape source is always full-height (``y=0``, crop height
    == source height), so the crop has *no* vertical degree of freedom at all.
    Moving x therefore cannot lift a face out of the bottom caption band -- and
    equally cannot be asked to. What it does control is horizontal lead room,
    which is what the planner's margin terms are actually for.

    This means the crop genuinely cannot avoid a caption overlap by moving
    vertically, and no test should claim it can. If a future change gives the
    crop vertical freedom (a scale-then-pad approach, or a taller crop with
    headroom), this assertion fails and the caption interaction has to be
    handled for real rather than by this paragraph.
    """
    from engine import reframe as reframe_mod

    for w, h in ((1920, 1080), (1280, 720), (720, 486), (960, 720)):
        # Built with ffmpeg rather than cv2 because the four geometries are the
        # point, and cv2 cannot write anything but its own frame size.
        video = tmp_path / f"geo_{w}x{h}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", f"testsrc=size={w}x{h}:rate=12:duration=2",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
             "-pix_fmt", "yuv420p", str(video)],
            check=True, capture_output=True,
        )
        sar_w, sar_h = probe_sar(video)
        crop_w = min(vertical_crop_width(h, sar_w, sar_h), w)
        filt = reframe_mod.vertical_crop_filter(video, 0.0, 1.5)
        head = filt.split(":")
        # crop=<w>:<h>:x='...':y=0 -- the height field is the source's own
        # height, i.e. the whole frame.
        assert head[1] == f"{h}", (
            f"{w}x{h}: a 9:16 crop of a landscape source must be full-height, "
            f"got {filt!r}"
        )
        assert head[-1] == "y=0", "there is no vertical freedom to spend"
        # ...so the only place the planner can put the subject is sideways, and
        # the crop is never asked for a window wider than the frame.
        assert crop_w <= w


def test_a_crop_that_is_full_height_has_no_caption_freedom(tmp_path):
    """The consequence, asserted on the geometry rather than assumed: because
    ``y=0`` and the crop height equals the source height, the caption band of
    the deliverable is the caption band of the source translated unchanged.
    Whatever the planner does in x, a face in the source's caption band is in
    the output's caption band. Handling that needs a scale or a pad, not a
    smarter x."""
    import cv2
    import numpy as np

    from engine import reframe as reframe_mod

    # A source with a bright marker in its bottom fifth, where captions go.
    src_w, src_h, n = 640, 360, 18
    marker_y = int(src_h * 0.89)
    video = tmp_path / "caption_band_src.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(video), fourcc, 12.0, (src_w, src_h))
    try:
        for _ in range(n):
            frame = np.zeros((src_h, src_w, 3), dtype=np.uint8)
            frame[marker_y:marker_y + 12, 60:src_w - 60] = 255
            vw.write(frame)
    finally:
        vw.release()

    sar_w, sar_h = probe_sar(video)
    assert (sar_w, sar_h) == (1, 1)
    filt = reframe_mod.vertical_crop_filter(video, 0.0, 1.2)
    out = tmp_path / "caption_band.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-t", "1", "-vf", filt,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    cap = cv2.VideoCapture(str(out))
    ok, frame = cap.read()
    cap.release()
    assert ok, "could not read a frame back"
    out_h = frame.shape[0]
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    rows = np.where(grey.max(axis=1) > 16)[0]
    assert rows.min() / out_h > 0.80, (
        f"the marker started at {marker_y / src_h:.0%} of the source height and "
        f"the vertical crop put it at {rows.min() / out_h:.0%}: a full-height "
        f"crop has moved it vertically, which the geometry says it cannot"
    )


# ── 12. SAR: the crop is 9:16 in display space ─────────────────────────────


@pytest.mark.parametrize("h,sar_w,sar_h,expect", [
    (720, 1, 1, 406),      # plain 16:9 HD
    (486, 1, 1, 274),      # square-pixel NTSC frame size
    (486, 8, 9, 308),      # the same frame at SAR 8/9: wide pixels, wider crop
    (1080, 1, 1, 608),
])
def test_vertical_crop_width_is_display_space(h, sar_w, sar_h, expect):
    """An anamorphic source's pixels are not square, so the *pixel* width that
    displays as 9:16 is not the square-pixel answer. Getting this backwards crops
    a 4:3-ish window and calls it vertical."""
    got = vertical_crop_width(h, sar_w, sar_h)
    assert got == expect, f"height {h} SAR {sar_w}/{sar_h} -> {got}, expected {expect}"
    # The delivered display ratio really is 9:16, to rounding.
    dar = (got * sar_w) / (h * sar_h)
    assert abs(dar - 9 / 16) < 0.01, f"display ratio {dar:.4f} is not 9:16"
    assert got % 2 == 0, "even width, for the scaler and the encoder"


# ── 13. the trajectory the render actually uses ─────────────────────────────


def _moving_square_video(out: Path) -> Path:
    """A white block crossing a dark frame: a subject for the fallback path.

    Not a face, by construction -- YuNet will not see it, which is the point:
    this exercises the no-detections path where the heuristic must still track a
    moving subject. The face-driven path is covered by the rendered tests below.
    """
    import cv2

    w, h, n = 320, 320, 24
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(out), fourcc, 12.0, (w, h))
    for i in range(n):
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        x = int((0.15 + 0.60 * i / (n - 1)) * (w - 40))
        frame[100:180, x:x + 40] = 255
        vw.write(frame)
    vw.release()
    return out


def test_no_face_video_still_tracks_and_moves_the_crop(tmp_path):
    """The fallback path must produce a time-keyed crop that follows a moving
    subject, not a static centre. This is the case the shipped bug looked like
    it handled: a detector available but seeing nothing."""
    video = _moving_square_video(tmp_path / "square_reframe.mp4")
    expr = None
    from engine import reframe as reframe_mod
    expr = reframe_mod.crop_x_expression(video, 0.0, 1.8)
    assert expr and "if(lt(t," in expr, (
        "a clip with no detectable faces must still get a time-keyed crop"
    )
    centers = track_subject_center(video, 0.0, 1.8, samples=12)
    assert len(centers) >= 6, f"expected several samples, got {len(centers)}"
    assert centers[-1][1] - centers[0][1] > 0.10, (
        f"the crop must follow the block, moved only "
        f"{centers[-1][1] - centers[0][1]:.3f}"
    )


# ── rendered output: the product, not the coordinates ──────────────────────
#
# These are the tests that would have caught the shipped failure. They render
# the vertical output with the pipeline's own filtergraph and score the
# delivered frames with an independent detector -- the oracle is deliberately
# not the planner's detector, so a blind spot shared with the planner is not a
# shared pass.

def crop_x_expression_points(video: Path, start: float, end: float
                             ) -> list[tuple[float, float]]:
    """The ``(t, centre_fraction)`` keypoints the render's crop expression is
    built from, taken straight off the planner so the score is of the actual
    trajectory and not a reconstruction of it."""
    _w, _h, pts = _reframe_track(video, start, end)
    return pts


def _selections() -> list[tuple[str, float, float]]:
    if not SELECTIONS.is_file():
        return []
    raw = json.loads(SELECTIONS.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("candidates", [])
    # The corpus ships four shorts in this order; short04 is the known failure.
    names = ["short01", "short02", "short03", "short04", "short05"]
    return [(names[i] if i < len(names) else f"cut{i}",
             float(c["start"]), float(c["end"])) for i, c in enumerate(raw)]


def _render_vertical(video: Path, start: float, end: float, filt: str, out: Path
                     ) -> None:
    """The pipeline's own encode settings, so what is measured is what ships."""
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round((end - start) * 30.0)))
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(video),
         "-frames:v", str(frames), "-t", f"{frames / 30.0:.6f}",
         "-vf", filt, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-an", "-movflags", "+faststart", str(out)],
        check=True, capture_output=True, encoding="utf-8")


def _probe_dims(video: Path) -> tuple[int, int, float]:
    """(width, height, fps) of the source, from one open."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    try:
        if not cap.isOpened():
            raise RuntimeError("VideoCapture refused the source")
        return (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
                float(cap.get(cv2.CAP_PROP_FPS) or 30.0))
    finally:
        cap.release()


def _decode_source(video: Path, start: float, end: float, n: int
                   ) -> list[tuple[float, np.ndarray]]:
    """``n`` evenly spaced source frames as ``(t_seconds_from_start, frame)``.

    Seeks by absolute timestamp and reports the timestamp ffmpeg actually
    landed on, which is what the trajectory has to be evaluated against --
    seeking is not exact, and a crop keyed to the requested time would be
    compared against the wrong frame.
    """
    import cv2

    out: list[tuple[float, np.ndarray]] = []
    cap = cv2.VideoCapture(str(video))
    try:
        if not cap.isOpened():
            return out
        for i in range(n):
            want = start + (end - start) * i / max(1, n - 1)
            cap.set(cv2.CAP_PROP_POS_MSEC, want * 1000.0)
            ok, frame = cap.read()
            got = float(cap.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
            if ok:
                out.append((max(0.0, got - start), frame))
    finally:
        cap.release()
    return out


def _score_source_space(video: Path, start: float, end: float,
                        centers: list[tuple[float, float]], crop_w: int,
                        oracle: FaceDetector, n: int = 90) -> dict:
    """The instrument that actually catches the shipped failure.

    Scored in **source pixel space**, not on the rendered deliverable, for two
    reasons that both made the previous version of this test vacuous:

    1. At a 406px-wide vertical deliverable a 100px source face is 32px on
       output -- under the detector's 48px floor, so the oracle saw *nothing*
       and the measurement reported a blank file as a pass waiting to happen.
    2. On a rendered frame, "a face was detected" is near-tautological: any box
       the oracle found is inside the frame by construction. The question is
       whether the *crop window* contains it, and that question is answerable
       only where the faces are full size.

    The oracle is a separate ``FaceDetector`` instance from the planner's, and
    runs over the un-cropped source, so a blind spot shared with the planner
    cannot become a shared pass.

    The face scored is the one the crop is actually framing -- the detected face
    nearest the window's centre -- rather than the highest-scoring detection.
    On a two-shot the planner must choose a speaker, and the detection it does
    *not* choose can still be the more confident one; counting that as a miss
    would penalise a correct editorial decision. What the product is judged on
    is that the window contains somebody, and that the somebody it contains is
    comfortably inside it rather than bisected by its edge.
    """
    w, _h, _fps = _probe_dims(video)
    frames = _decode_source(video, start, end, n)
    rows: list[dict] = []
    for t_rel, frame in frames:
        faces = oracle.detect(frame)
        center = _interpolate(centers, t_rel)
        x0 = center * w - crop_w / 2.0
        x1 = x0 + crop_w
        inside = [f for f in faces if x0 <= f[0] + f[2] / 2.0 <= x1]
        row = {"t": round(t_rel, 3), "n_faces": len(faces),
               "crop_x0": round(x0, 1), "crop_x1": round(x1, 1),
               "faces_kept": len(inside)}
        if inside:
            # The face the crop is framing: nearest centre to the window centre.
            cx = center * w
            fx, fy, fw, fh, _s = min(inside, key=lambda f: abs(f[0] + f[2] / 2 - cx))
            fcx = fx + fw / 2.0
            row.update({
                "face_cx": round(fcx, 1), "face_w": round(fw, 1),
                "framed_inside": True,
                "framed_margin_frac": round(min(fcx - x0, x1 - fcx) / max(1.0, fw), 3),
                "framed_clipped": bool(fx < x0 or fx + fw > x1),
            })
        else:
            row.update({"framed_inside": False, "framed_margin_frac": None,
                        "framed_clipped": None})
        rows.append(row)

    seen = [r for r in rows if r["n_faces"]]
    held = [r for r in seen if r["framed_inside"]]
    well = [r for r in held if r.get("framed_margin_frac", 0) >= MARGIN_FRACTION_OK]
    clipped = [r for r in held if r.get("framed_clipped")]
    m = len(seen)
    # Losing *every* face in a frame is the failure mode this exists for; a
    # two-shot losing the second face is a framing decision.
    lost = [r for r in seen if not r["framed_inside"]]
    return {
        "verdict": "measured",
        "n_sampled": len(rows),
        "face_frames": m,
        "framed_frac": (len(held) / m) if m else None,
        "framed_well_framed_frac": (len(well) / m) if m else None,
        "framed_clipped_frac": (len(clipped) / m) if m else None,
        "all_faces_lost": lost,
        "rows": rows,
    }


def _make_filter(video: Path, start: float, end: float) -> str:
    # The pipeline's own chain, verbatim — engine.render's vertical branch is
    # exactly this string. A hand-composed copy here is where the 1080x1918
    # defect hid, reproducing it in the one test that renders the deliverable.
    return vertical_reframe_filter(video, start, end)


def _width(video: Path) -> int:
    return _probe_dims(video)[0]


def _height(video: Path) -> int:
    return _probe_dims(video)[1]


def probe_sar(video: Path) -> tuple[int, int]:
    from providers.ffmpeg import probe_sample_aspect
    return probe_sample_aspect(video)


@pytest.mark.skipif(not _HAVE_CORPUS, reason="corpus media is local-only")
class TestRenderedOutputKeepsTheSpeaker:
    """The product is the delivered frame. These score the vertical output on
    real long-form footage, in source space, after a real ffmpeg encode.

    The corpus is local-only (it is not in the repository), so on a fresh
    checkout these skip -- and the skip is reported, not silently counted as a
    pass. Everything else in this file is synthetic and runs anywhere.
    """

    def test_the_known_failure_short_keeps_a_speaker(self, tmp_path):
        """short04 was the total failure: two speakers detected at 0.94
        confidence for the whole clip while the crop sat in the empty middle
        between them, so every sampled frame lost every face. That is the case
        the instrument has to catch, so it is asserted first and hardest."""
        name, start, end = _selections()[3]
        assert name == "short04", f"the corpus order changed; got {name}"

        oracle = FaceDetector()
        assert oracle.available, "the oracle has no detector; the score is meaningless"
        centers = crop_x_expression_points(PENTAGON, start, end)
        sar_w, sar_h = probe_sar(PENTAGON)
        h = _height(PENTAGON)
        crop_w = min(vertical_crop_width(h, sar_w, sar_h), _width(PENTAGON))
        score = _score_source_space(PENTAGON, start, end, centers, crop_w, oracle)

        assert score["verdict"] == "measured"
        assert score["face_frames"] > 30, (
            f"the oracle saw faces in only {score['face_frames']} samples of "
            f"{score['n_sampled']} -- the measurement is too thin to trust"
        )
        # The shipped defect was a crop window that contained nobody at all,
        # for the whole clip. Zero tolerance for it.
        assert not score["all_faces_lost"], (
            f"short04: {len(score['all_faces_lost'])} frames where the crop "
            f"window contained no face at all, at "
            f"t={[r['t'] for r in score['all_faces_lost']]}"
        )
        assert score["framed_frac"] >= 0.98, (
            f"short04: {score['framed_frac']:.1%} of frames with a visible face "
            f"had the crop framing one"
        )
        assert score["framed_well_framed_frac"] >= 0.70, (
            f"short04: only {score['framed_well_framed_frac']:.1%} of frames "
            f"framed a face with a face's-width of clearance"
        )
        assert score["framed_clipped_frac"] <= 0.10, (
            f"short04: {score['framed_clipped_frac']:.1%} of framed faces were "
            f"bisected by the crop edge"
        )

    def test_the_output_is_genuinely_vertical(self, tmp_path):
        """Geometry of the deliverable, measured from the encoded pixels: 9:16
        in display space, square pixels, no letterbox."""
        import cv2

        name, start, end = _selections()[0]
        clip = tmp_path / "short01_vertical.mp4"
        _render_vertical(PENTAGON, start, end,
                         _make_filter(PENTAGON, start, end), clip)
        cap = cv2.VideoCapture(str(clip))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        sar_num = int(cap.get(cv2.CAP_PROP_SAR_NUM) or 1)
        sar_den = int(cap.get(cv2.CAP_PROP_SAR_DEN) or 1)
        cap.release()
        dar = (w * sar_num) / (h * sar_den)
        assert abs(dar - 9 / 16) < 0.02, f"delivered DAR {dar:.4f} is not 9:16"

    @pytest.mark.parametrize("which", [0, 1, 2])
    def test_the_speaking_shorts_still_keep_a_speaker(self, tmp_path, which):
        """shorts 01-03 measured well before; they must not regress while the
        failure case was being fixed. Their known misses each landed on a
        whip-pan frame where the oracle itself is briefly confused, so the bar
        is 95% rather than 98%."""
        name, start, end = _selections()[which]
        sar_w, sar_h = probe_sar(PENTAGON)
        h = _height(PENTAGON)
        crop_w = min(vertical_crop_width(h, sar_w, sar_h), _width(PENTAGON))
        centers = crop_x_expression_points(PENTAGON, start, end)
        score = _score_source_space(PENTAGON, start, end, centers, crop_w,
                                    FaceDetector())
        assert score["verdict"] == "measured"
        assert score["face_frames"] > 30, "measurement too thin to trust"
        assert score["framed_frac"] >= 0.95, (
            f"{name}: {score['framed_frac']:.1%} of frames with a face had the "
            f"crop framing one; all-faces-lost at "
            f"t={[r['t'] for r in score['all_faces_lost']]}"
        )
        assert score["framed_clipped_frac"] <= 0.10, (
            f"{name}: {score['framed_clipped_frac']:.1%} of framed faces were "
            f"bisected by the crop edge"
        )

    def test_the_encoded_deliverable_contains_the_speaker(self, tmp_path):
        """Everything else in this file scores the trajectory in *source* space,
        reading the planner's keypoints and applying the crop window by hand.
        Source space cannot catch a crop expression that disagrees with every
        consumer about how a centre fraction maps to a window position: the
        source-space score stayed at 1.000 while the shipped short04 render
        contained no detectable face in any sampled frame.

        This test decodes the encoded deliverable itself and asks the oracle
        whether a face is in the picture. It is the check that would have rung
        the bell. It is bounded below by the oracle's 48 px detector floor -- a
        face rendered smaller than that is invisible to it, and this asserts
        only that *some* frame carries a face, not that every frame does.
        """
        import cv2

        name, start, end = _selections()[3]
        assert name == "short04", f"the corpus order changed; got {name}"
        oracle = FaceDetector()
        assert oracle.available, "the oracle has no detector; the score is meaningless"

        clip = tmp_path / "short04_vertical_deliverable.mp4"
        _render_vertical(PENTAGON, start, end,
                         _make_filter(PENTAGON, start, end), clip)

        cap = cv2.VideoCapture(str(clip))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        assert (w, h) == (406, 722), (
            f"encoded geometry {(w, h)} -- expected 406x722, the exact 9:16 "
            f"frame the reframe builds from this 720-high source"
        )
        assert total > 600, f"only {total} frames encoded; the clip is ~884"

        step = max(1, total // 40)
        hits, widths, sampled = 0, [], 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if cap.get(cv2.CAP_PROP_POS_FRAMES) % step < 1:
                sampled += 1
                faces = oracle.detect(frame)
                if faces:
                    hits += 1
                    widths.append(max(f[2] for f in faces))
        cap.release()
        assert sampled > 20, f"only {sampled} frames decoded; cannot conclude"
        assert hits > 0, (
            f"short04: the encoded vertical deliverable contained no detectable "
            f"face in any of {sampled} sampled frames -- the speaker is not in "
            f"the shipped output, whatever the source-space metrics say"
        )
        # A face the oracle *can* see should be comfortably large, not a sliver
        # hugging the edge of the frame.
        largest = max(widths)
        assert largest > 0.15 * w, (
            f"short04: the largest detected face in the deliverable is "
            f"{largest:.0f} of {w} px -- the crop is framing an edge, a "
            f"shoulder, or the wrong person"
        )
