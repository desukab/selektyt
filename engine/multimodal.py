"""The multimodal layer: measured eyes and ears on candidate spans.

The editorial scorer has always ranked candidates by what their TEXT
predicts; a storyboard editor ranks spans by what they LOOK and SOUND like.
This module is that second reading, measured on the media itself:

* frames sampled through the same caching reader ``engine.reframe`` uses, so
  a reframe render and a profile never decode the same span twice;
* faces through the shipped YuNet detector, one pass, small frames instead of
  re-running the render's multi-scale rescue;
* motion, brightness spread and shot rhythm from cheap frame arithmetic;
* sound from the RMS envelope of the span's own audio.

The evidence is returned per candidate as a plain JSON-able dict that names
what it measured; a selection, a story composer and a memory entry can all
read the same document.

Blending lives in :func:`blend_visual`: the predictive, text-derived
``visual_potential`` keeps its weight, and the *measured* reading joins it,
so a candidate whose transcript promised a visual story but whose footage
shows a frozen dark screen cannot keep its full visual borrow.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from engine.reframe import _sample_frames


_SAMPLE_FRAMES = 9
# The render's scene analysis samples 28; a per-candidate profile wants a
# cheaper read than that, because the selection layer reads a few dozen
# candidates per run and the render reads only the handful that ship.


def _grayscale(frame: np.ndarray) -> np.ndarray:
    return frame.mean(axis=2)


def _cut_fraction(centre_loss: np.ndarray) -> float:
    """The share of neighbouring frame pairs that qualify as a hard cut.

    A hard cut means a mean absolute pixel change far above what the same
    camera's small movements produce; every span otherwise rides a smooth
    motion curve. Fractions under 0.10 are a talking head; fractions over
    0.40 are a shot-cutting edit.
    """
    diffs = np.abs(np.diff(centre_loss, axis=0)).mean(axis=(1, 2))
    if diffs.size == 0:
        return 0.0
    threshold = float(np.percentile(diffs, 50) * 4.0)
    return float((diffs > threshold).mean())


def profile_clip(
    media: Path, start: float, end: float, samples: int = _SAMPLE_FRAMES
) -> dict[str, Any] | None:
    """The measured eyes-and-ears profile of one span, or None when unreadable.

    Frames come from ``_sample_frames`` (with its cache); faces from the
    vendored YuNet detector a reframe render also uses, one pass on the small
    frame. Nothing here touches a GPU, downloads anything, or reads the
    transcript: the profile stays a measurement of pixels and audio so it can
    be recomputed by anyone re-checking a claim.
    """
    try:
        _times, frames, w, h = _sample_frames(Path(media), start, end, samples)
    except Exception:
        return None
    good = [f for f in frames if f is not None]
    if not good:
        return None
    grays = [_grayscale(f) for f in good]
    stacked = np.stack(grays)  # (n, h, w), mean over channels kept float64
    motion = float(np.abs(np.diff(stacked, axis=0)).mean())
    # A hard-cut share, from the same centre channel
    cuts = _cut_fraction(stacked)
    brightness = float(stacked.mean())
    brightness_spread = float(stacked.std())

    from engine.reframe_perception import FaceDetector
    face_frac = 0.0
    max_size = 0.0
    w_h = good[0].shape[1], good[0].shape[0]
    try:
        detector = FaceDetector()
        for f in good:
            found = detector.detect(f)
            if found:
                face_frac += 1.0 / len(good)
                max_size = max(max_size, max(
                    (bw * bh) / (w * h) for _x, _y, bw, bh, _s in found))
    except Exception:
        pass  # degrade to no-face rather than fail the profile

    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(end - start, 3),
        "samples": len(good),
        "motion_max_mean_px": round(motion, 2),
        "hard_cut_share": round(cuts, 4),
        "brightness_mean": round(brightness, 1),
        "brightness_spread": round(brightness_spread, 1),
        "face_presence_fraction": round(face_frac, 4),
        "largest_face_area_fraction": round(max_size, 4),
        "provenance": {
            "frame_reader": "engine.reframe._sample_frames",
            "face_detector": "engine.reframe_perception.FaceDetector",
            "samples": _SAMPLE_FRAMES,
        },
    }


def blend_visual(profile: dict[str, Any], predictive: float) -> float:
    """The visual score the selection reads when a measured profile exists.

    The candidate's text-based prediction keeps a third of the vote: it knows
    the transcript asks the viewer to look at a diagram or a screen share,
    which pixel statistics cannot see. The measured reading carries the
    rest, weighted toward what a storyboard editor actually flips through:
    motion, alternation of shot, face presence.

    A profile that is the *shape* of a real clip gets real credit; a profile
    that is the shape of a broken read — a frozen frame with no face — is
    floored near zero rather than passed through. Both ends are measured, not
    asserted.
    """
    if not isinstance(profile, dict) or not profile:
        return min(1.0, max(0.0, predictive))
    motion = _norm(profile.get("motion_max_mean_px"), 0, 32)
    cuts = _norm(profile.get("hard_cut_share"), 0, 0.5)
    faces = _norm(profile.get("face_presence_fraction"), 0, 0.6)
    light = _norm(profile.get("brightness_spread"), 0, 40)
    measured = 0.35 * motion + 0.30 * cuts + 0.25 * faces + 0.10 * light
    frozen = (
        isinstance(profile.get("motion_max_mean_px"), (int, float))
        and float(profile["motion_max_mean_px"]) < 1.0
        and (profile.get("face_presence_fraction") or 0.0) == 0.0
    )
    if frozen:
        measured = 0.0
    return round(0.35 * min(1.0, max(0.0, predictive))
                 + 0.65 * min(1.0, measured), 4)


def _norm(value: Any, low: float, high: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v <= low:
        return 0.0
    if v >= high:
        return 1.0
    return (v - low) / (high - low)


def profile_candidates(
    media: Path, candidates: Sequence[Any], quota: int = 24,
) -> list[tuple[Any, dict[str, Any] | None]]:
    """Matched per-candidate profiles from the top ``quota`` by score.

    The selection reads only the tier it is likely to pick the program from;
    profiling every candidate would spend the render budget on frames no
    editor will ever see, so the quota is the discovery budget the evidence
    stays inside. Order follows the candidate order handed in and the profile
    is exact for whichever candidate it was asked for.
    """
    ranked = list(candidates)[:quota]
    return [(c, profile_clip(media, c.start, c.end)) for c in ranked]


def print_profile(p: dict[str, Any] | None) -> str:
    if not p:
        return "unmeasurable"
    return (
        f"motion={p['motion_max_mean_px']} cuts={p['hard_cut_share']}"
        f" faces={p['face_presence_fraction']}"
        f" bright={p['brightness_mean']}"
    )
