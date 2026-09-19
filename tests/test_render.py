"""Render path — real ffmpeg, no mocking.

render_clip is the product output step (MP4 + optional burned captions, with
concat for multi-interval candidates). It needs a real encoder, so these skip
cleanly when ffmpeg is absent but otherwise run the genuine subprocess.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.captions import ass_for_candidate
from engine.render import render_clip, require_ffmpeg
from models.core import Candidate, Word

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not available")


def _video(out: Path, frames: int = 30, size: int = 320, fps: int = 15) -> Path:
    import cv2
    import numpy as np

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out), fourcc, fps, (size, size))
    try:
        for i in range(frames):
            frame = np.zeros((size, size, 3), dtype=np.uint8)
            cv2.putText(
                frame, f"frame {i}", (20, size // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2,
            )
            writer.write(frame)
    finally:
        writer.release()
    return out


def _candidate_with_words(start: float, end: float) -> Candidate:
    words = [
        Word("hello", start + 0.0, start + 0.4),
        Word("world", start + 0.4, start + 0.8),
        Word("today", start + 0.8, start + 1.2),
    ]
    return Candidate(
        start, end, "hello world today", "story", "hello", "world today",
        editorial_score=0.8, virality_score=0.7, context_score=0.9,
        confidence=0.9, reason="t", visual_potential=0.8, words=words,
    )


def test_require_ffmpeg_raises_when_absent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg is required"):
        require_ffmpeg()


def test_render_horizontal_clip(tmp_path):
    source = _video(tmp_path / "source.mp4")
    out = render_clip(source, _candidate_with_words(0.0, 1.3), tmp_path / "fb" / "01.mp4")
    assert out.is_file()
    assert out.stat().st_size > 0
    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,sample_aspect_ratio",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    w, h, sar = probe.stdout.strip().split(",")
    w, h = int(w), int(h)
    # The landscape deliverable's frame must display at 16:9. A square-pixel
    # source gets pillarboxed rather than stretched or cropped.
    sar_w, sar_h = (int(p) for p in sar.split(":"))
    assert (w * sar_w) / (h * sar_h) == pytest.approx(16 / 9, abs=0.02)
    # F12: a sub-1080p source must not be *upscaled*. The frame is wider than
    # the source now — pillarbox bars, not a rescale — so the invariant is that
    # the frame is never taller than the source: the picture fits inside the
    # frame, so its height cannot exceed the source's own 320 lines.
    assert h <= 320, f"the source picture was upscaled to a {h}px-tall frame"
    # no leftover part directories on the single-interval path
    assert not (tmp_path / "fb" / ".01_parts").exists()


def test_render_downscales_larger_sources(tmp_path):
    """A source wider than the delivery size is scaled down to 1920."""
    import subprocess

    wide = tmp_path / "wide.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         "color=c=blue:s=2560x1440:d=1.5", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(wide)],
        check=True, capture_output=True,
    )
    out = render_clip(wide, _candidate_with_words(0.0, 1.2), tmp_path / "fb" / "01.mp4")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    w, h = (int(x) for x in probe.stdout.strip().split(","))
    assert w == 1920 and h == 1080, f"expected downscale to 1920x1080, got {w}x{h}"


def test_render_vertical_clip_is_9_16(tmp_path):
    source = _video(tmp_path / "source.mp4")
    out = render_clip(
        source, _candidate_with_words(0.0, 1.3),
        tmp_path / "sh" / "01.mp4", vertical=True,
    )
    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    w, h = (int(x) for x in probe.stdout.strip().split(","))
    assert h / w == pytest.approx(16 / 9, abs=0.02)


def _anamorphic_video(out: Path, size: str = "720x486", sar: str = "8/9") -> Path:
    """720x486 pixels at SAR 8:9, i.e. DAR 320:243 — real NTSC DV shape.

    Written with ffmpeg, not cv2: OpenCV cannot record a sample aspect ratio,
    and the whole point is that the pixels are not square. The ratio is spelled
    with a slash because ffmpeg's setsar misreads the colon form of a
    sub-unitary ratio and writes 8:1 instead.
    """
    import subprocess

    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=size={size}:rate=12:duration=2",
         "-vf", f"setsar={sar}", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "28", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_render_vertical_clip_is_9_16_in_display_space_for_anamorphic(tmp_path):
    """The real defect, end to end: an anamorphic source cropped at
    ``ih*9/16`` *pixels* came out 272x486 SAR 8:9, which displays at
    1088:2187 — nearer 1:2 than 9:16. The pixel count is only the right ratio
    when the pixels are square; the crop width has to be scaled by the sample
    aspect ratio so the *display* ratio lands on 9:16."""
    import subprocess

    source = _anamorphic_video(tmp_path / "ntsc.mp4")
    out = render_clip(
        source, _candidate_with_words(0.0, 1.3),
        tmp_path / "sh" / "01.mp4", vertical=True,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries",
         "stream=width,height,sample_aspect_ratio,display_aspect_ratio",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    w, h, sar, _dar = probe.stdout.strip().split(",")
    sar_w, sar_h = (int(p) for p in sar.split(":"))
    # The pixel dimensions are NOT 9:16 and must not be asserted to be: the
    # frame stays anamorphic, so the ratio that has to be right is the one a
    # player displays. 272x486 SAR 8:9 scores 0.497 here; 308x486 scores 0.563.
    dar = int(w) * sar_w / (int(h) * sar_h)
    assert dar == pytest.approx(9 / 16, abs=0.005), (
        f"vertical output displays at {dar:.4f}, not 9:16 "
        f"({w}x{h} SAR {sar})"
    )


def test_render_with_burned_captions(tmp_path):
    source = _video(tmp_path / "source.mp4")
    c = _candidate_with_words(0.0, 1.3)
    ass = ass_for_candidate(c, tmp_path / "captions" / "01.ass")
    out = render_clip(source, c, tmp_path / "fb" / "01.mp4", ass=ass)
    assert out.is_file()
    assert out.stat().st_size > 0


def test_render_rejects_empty_intervals(tmp_path):
    """An empty-text candidate has no renderable intervals (edit_plan returns
    [] only when text is blank or the span is non-positive); render must
    refuse rather than emit an empty file."""
    source = _video(tmp_path / "source.mp4")
    c = Candidate(0.0, 1.0, "", "story", "h", "p", 0.8, 0.7, 0.9, 0.9, "t")
    with pytest.raises(RuntimeError, match="no renderable intervals"):
        render_clip(source, c, tmp_path / "fb" / "01.mp4")


def _probe_frames(path: Path) -> int:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=nb_frames",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip())


def test_render_multi_part_is_frame_accurate(tmp_path):
    """A multi-interval clip must render exactly the frames its exported
    timeline is stamped with.

    Passing a bare ``-t`` to ffmpeg emits every frame at or below the requested
    time, so a non-whole-frame interval rounds *up*. Each concatenated part
    does that independently, and the overshoot grew with the cut count while
    the EDL stayed on the frame grid — 2.63 frames on a two-part real-world
    clip, which tools/nle_check.py flagged as a render/timeline disagreement.
    The two candidates below ask for 5.3 and 7.3 frames at 15 fps, so the
    rounding is exercised exactly; the expected counts are what
    engine.timecode.format_smpte resolves the same intervals to, which is the
    invariant that makes an EDL describe the file that was actually shipped.
    """
    source = _video(tmp_path / "source.mp4", frames=120, fps=15)
    # 5.3 and 7.3 frames at 15 fps -> neither interval is a whole frame count,
    # which is the case the bare ``-t`` path rounded up: ffmpeg emits every
    # frame whose timestamp is at or below the request (6 and 8 frames here),
    # one more than the grid the timecode is stamped with (5 and 7). The rate
    # passed here is the same one the timeline is stamped with, mirroring how
    # the pipeline hands probe_fps(media) to both the render and the export.
    cases = [(1.0, 1.0 + 5.3 / 15.0, 5), (6.0, 6.0 + 7.3 / 15.0, 7)]
    for i, (start, end, expected) in enumerate(cases, start=1):
        out = tmp_path / "fb" / f"{i:02d}.mp4"
        render_clip(source, _candidate_with_words(start, end), out, fps=15)
        got = _probe_frames(out)
        assert got == expected, (
            f"clip {i} rendered {got} frames but its timeline is stamped with "
            f"{expected}: an NLE relinking this cut would not get the file that "
            f"was shipped"
        )
