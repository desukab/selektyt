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
    # horizontal render scales to 1920 wide and preserves the source aspect
    assert w == 1920
    assert h / w == pytest.approx(1.0, abs=0.02)
    # no leftover part directories on the single-interval path
    assert not (tmp_path / "fb" / ".01_parts").exists()


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
