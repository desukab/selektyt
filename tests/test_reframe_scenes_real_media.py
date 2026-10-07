"""The scene-aware reframe on the one real corpus source that composes.

``test_reframe_scenes.py`` pins the module's geometry and its composition on
generated sources, which is where the decision logic can be asserted exactly.
It cannot catch a rule that is *geometrically wrong about real footage*, and one
was: ``classify_scene`` used to return ``SLIDE`` for any content region covering
80% of the frame before it looked at the face, so a wide shot holding a small
speaker beside a large static region had its speaker cropped out. On the real
Royal Society interview that cost the speaker on 6 of 26 samples where the
pre-composition reframe kept it on 25 of 26.

This module pins the fix on that same footage, so the rule cannot come back as a
"cleaner" special case. It is opt-in for the same reason the other real-media
checks are: the media is not redistributable with the repo, so it skips with a
stated reason where it is absent rather than passing vacuously.

What this does and does not prove, said plainly. The oracle scoring the rendered
frame is the same YuNet family the pipeline uses, so a shared detector blind
spot would hide from both. What it *does* pin is the layout decision -- which
source window the composition chose -- because a wrong choice puts a real,
detectable face outside the delivered frame regardless of who is looking.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.reframe_scenes import (  # noqa: E402
    SceneClass, analyse_scene, scene_filter,
)

REAL_MEDIA = ROOT / "corpus" / "media" / "royal-society-cloke.webm"

pytestmark = pytest.mark.skipif(
    not REAL_MEDIA.is_file(),
    reason=f"{REAL_MEDIA} is not redistributable with the repo; this check "
           "runs only where the real media is present",
)

# The wide shot of the Royal Society interview: the class change lands 19.8s
# into the cut at 12.30, so this window sits wholly inside it. The speaker's
# face is 52-66 px wide here against a static region covering 98% of the frame.
WIDE_START, WIDE_END = 34.0, 42.0

_DOMINANT_FRAC = 0.80


def _ffmpeg(args: list[str], out: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-hide_banner", "-nostats",
         *args, str(out)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return out


def _deliver(graph: str, out: Path, start: float = WIDE_START,
             end: float = WIDE_END) -> Path:
    """Render a graph the way ``engine.render`` does: a composed description
    needs ``-filter_complex`` with an explicit ``[v]`` map, a plain chain takes
    ``-vf``."""
    frames = int(30 * (end - start))
    common = ["-ss", f"{start:.3f}", "-i", str(REAL_MEDIA),
              "-frames:v", str(frames), "-c:v", "libx264", "-preset",
              "veryfast", "-crf", "20", "-an", "-pix_fmt", "yuv420p"]
    if ";" in graph:
        _ffmpeg([*common, "-filter_complex", graph, "-map", "[v]"], out)
    else:
        _ffmpeg([*common, "-vf", graph], out)
    return out


def _faces_per_frame(clip: Path, max_samples: int = 60) -> list[bool]:
    """Whether the oracle sees a face in each sampled delivered frame."""
    import cv2

    sys.path.insert(0, str(ROOT / "tools"))
    from reframe_benchmark import _Oracle, _yunet_path

    oracle = _Oracle(_yunet_path())
    cap = cv2.VideoCapture(str(clip))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, total // max_samples)
    seen: list[bool] = []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            seen.append(bool(oracle.detect(frame)))
        i += 1
    cap.release()
    return seen


def _wide_plan() -> tuple:
    """The plan for the wide shot, after asserting the shot still carries the
    condition the broken rule misread. Every other assertion here depends on
    this, so a change in the footage fails loudly instead of passing vacuously.
    """
    from engine.reframe import _sample_frames
    from engine.reframe_perception import get_detector

    segments, slides, w, h = analyse_scene(REAL_MEDIA, WIDE_START, WIDE_END,
                                           samples=24)
    assert segments, "the interval must plan into segments"
    dominant = [s for s in slides
                if s.area >= _DOMINANT_FRAC * float(w * h)]
    assert dominant, (
        "the wide shot must contain a content region covering most of the "
        "frame; without it this test is not exercising the rule it pins")

    _times, frames, _w, _h = _sample_frames(REAL_MEDIA, WIDE_START, WIDE_END,
                                            samples=24)
    detector = get_detector()
    n_with_face = sum(1 for f in frames
                      if f is not None and detector.detect(f))
    assert n_with_face >= 0.75 * len(frames), (
        f"the wide shot holds a speaker on {n_with_face} of {len(frames)} "
        f"samples; a classification that drops it is dropping a real subject")
    return segments, slides, w, h


def test_the_wide_shot_actually_has_the_condition_the_rule_broke_on():
    """The control against a vacuous pass, run on its own so it is visible in
    the report rather than folded into the regression."""
    _wide_plan()


def test_a_speaker_in_front_of_a_frame_filling_region_is_not_a_slide_scene():
    """The regression, on real footage. The broken rule read the region's size
    before the face, so this shot planned a crop centred on the slide and the
    speaker left the deliverable. The class must answer to the speaker first."""
    segments, _slides, _w, _h = _wide_plan()

    assert all(seg.cls is not SceneClass.SLIDE for seg in segments), (
        "a speaker is present in this shot, so classifying it SLIDE crops to "
        "the content region's centre and drops them; the class must be SPEAKER "
        f"or SPLIT. got {[(s.start, s.end, s.cls.value) for s in segments]}")


def test_the_delivered_frame_still_contains_the_speaker(tmp_path):
    """End to end: the deliverable for the wide shot keeps a detectable face in
    frame.

    The mechanism is deliberately not asserted. On this window the geometry
    finds one 608 px crop that holds both the speaker and half of the
    full-frame slide, so the plan is a single tracking crop rather than two
    bands -- and that is the right answer, since splitting here would shrink a
    52 px face to fit a band. What is pinned is the outcome the old rule
    broke: the speaker is in the delivered frame.
    """
    graph = scene_filter(REAL_MEDIA, WIDE_START, WIDE_END, samples=24)
    clip = _deliver(graph, tmp_path / "wide.mp4")

    seen = _faces_per_frame(clip)
    assert len(seen) > 4, "the deliverable decoded too few frames to score"
    present = sum(seen) / float(len(seen))
    assert present >= 0.9, (
        f"a speaker detectable in the source is present in only {present:.0%} "
        f"of the delivered frames; the composition chose a window that lost "
        f"them")


def test_false_background_slide_stays_one_coherent_vertical_crop(tmp_path):
    """The selected Cloke interval misreads the bookshelf/lamp as a slide.

    That detector verdict used to reach the per-clip renderer as SPLIT and
    become two source crops stacked with vstack. Pin the scene that triggered
    the shipped artifact: it must remain a single tracked crop through actual
    vertical rendering.

    The delivered size is the caption canvas (1080x1920) rather than the crop's
    own 608 px: ``scene_filter`` raises a low-resolution crop to the canvas the
    burned-in ASS is laid out on, because libass scales that layout onto the
    frame it is burned on and a 608x1080 frame would carry the cues at 56% of
    their authored size. The source-space crop is unchanged -- the tracking
    window is still the one the geometry found -- it is scaled up to deliver.
    """
    start, end = 0.3, 8.3
    segments, _slides, _w, _h = analyse_scene(
        REAL_MEDIA, start, end, samples=24)
    assert segments and any(seg.cls is SceneClass.SPLIT for seg in segments), (
        "the real interval no longer exercises the false slide classification")

    graph = scene_filter(REAL_MEDIA, start, end, samples=24)
    assert "vstack" not in graph and "split=2[a][b]" not in graph, (
        "a 9:16 instant must come from one coherent source crop")
    assert "crop=" in graph and "scale=" in graph

    clip = _deliver(graph, tmp_path / "single-crop.mp4", start, end)
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         str(clip)], check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "1080,1920", (
        "a composed vertical scene delivers on the caption canvas, not at the "
        "source crop's own resolution")
