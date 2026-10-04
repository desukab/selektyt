from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from engine.captions import ass_content
from engine.render import _subtitle_terms
from models.core import Candidate, Word


def test_default_caption_font_is_bundled_fredoka_and_passed_to_libass(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("SELEKYT_FONT", raising=False)
    candidate = Candidate(
        0.0, 1.0, "hello there", "story", "hook", "payoff",
        editorial_score=0.8, virality_score=0.8, context_score=0.8,
        confidence=0.9, reason="test", visual_potential=0.8,
        words=[Word("hello", 0.0, 0.45), Word(" there", 0.45, 1.0)],
    )
    ass = tmp_path / "captions.ass"

    ass.write_text(ass_content(candidate), encoding="utf-8")
    style = next(line for line in ass.read_text(encoding="utf-8").splitlines()
                 if line.startswith("Style: Default,"))
    assert style.startswith("Style: Default,Fredoka,")

    font_dir = Path(__file__).resolve().parents[1] / "engine" / "assets" / "fonts"
    font_files = (font_dir / "Fredoka-Regular.ttf", font_dir / "Fredoka-Bold.ttf")
    assert all(path.is_file() and path.stat().st_size > 40_000 for path in font_files)
    assert (font_dir.parent / "licenses" / "OFL-Fredoka.txt").is_file()

    terms = _subtitle_terms(ass)
    assert len(terms) == 1
    assert "subtitles=" in terms[0]
    assert "fontsdir=" in terms[0]
    assert font_dir.resolve().as_posix() in terms[0]


@pytest.mark.parametrize(
    ("style", "expected_font"),
    [("minimal", "Fredoka-Regular"), ("punch", "Fredoka-Bold")],
)
def test_ffmpeg_selects_the_bundled_fredoka_face(
    tmp_path: Path, monkeypatch, style: str, expected_font: str
) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required to verify subtitle font selection")
    monkeypatch.delenv("SELEKYT_FONT", raising=False)
    monkeypatch.setenv("SELEKYT_CAPTION_STYLE", style)
    candidate = Candidate(
        0.0, 1.0, "hello there", "story", "hook", "payoff",
        editorial_score=0.8, virality_score=0.8, context_score=0.8,
        confidence=0.9, reason="font selection test", visual_potential=0.8,
        words=[Word("hello", 0.0, 0.45), Word(" there", 0.45, 1.0)],
    )
    ass = tmp_path / "captions.ass"
    ass.write_text(ass_content(candidate), encoding="utf-8")
    terms = _subtitle_terms(ass)

    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "verbose", "-f", "lavfi",
         "-i", "color=c=black:s=640x360:r=10:d=1", "-vf", terms[0],
         "-frames:v", "1", "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    )
    assert "fontselect: (Fredoka," in result.stderr
    assert f"-> {expected_font}," in result.stderr
