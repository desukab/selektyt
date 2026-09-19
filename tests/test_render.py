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
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    w, h = (int(x) for x in probe.stdout.strip().split(","))
    # F12: a sub-1080p source must not be upscaled. The source is 320x320, so
    # the render keeps 320 wide — enlarging it would add no detail, cost encode
    # time and soften the image. A larger source is still scaled down to 1920.
    assert w == 320, f"expected source-width passthrough, got {w}"
    assert h / w == pytest.approx(1.0, abs=0.02)
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
