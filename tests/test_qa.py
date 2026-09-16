"""QA validation tests — silent tail, black frames, diversity, coverage, pacing."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from engine.qa import (
    probe,
    validate_output,
    validate_outputs,
    detect_silent_tail,
    black_frame_pct,
    visual_diversity_score,
    validate_outputs_expanded,
    source_coverage,
    write_report,
)

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")


def _lavfi_video(out: Path, color: str = "blue", duration: float = 3.0,
                 size: str = "640x360", tone: float | None = 440.0,
                 silent_tail: float = 0.0) -> None:
    """Render lavfi test video. tone=None → no audio; silent_tail>0 → silence at end."""
    out.parent.mkdir(parents=True, exist_ok=True)
    filters = ["-f", "lavfi", "-i", f"color=c={color}:s={size}:d={duration}"]
    if tone is not None:
        tone_dur = max(0.0, duration - silent_tail)
        filters += [
            "-f", "lavfi", "-i", f"sine=frequency={tone}:duration={tone_dur}",
        ]
        if silent_tail > 0:
            filters += [
                "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={silent_tail}",
                "-filter_complex", f"[1:a][2:a]concat=n=2:v=0:a=1[aout]",
                "-map", "0:v", "-map", "[aout]",
            ]
        else:
            filters += ["-map", "0:v", "-map", "1:a", "-shortest"]
    else:
        filters += ["-map", "0:v"]
    cmd = ["ffmpeg", "-y", *filters,
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
           "-c:a", "aac", "-b:a", "64k", "-t", str(duration), str(out)]
    subprocess.run(cmd, check=True, capture_output=True)


# ── validate_output / validate_outputs ──

def test_validate_good_output():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "good.mp4"
        _lavfi_video(mp4)
        ok, errors = validate_output(mp4, vertical=False)
        assert ok and errors == []


def test_validate_orientation():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "vert.mp4"
        _lavfi_video(mp4, size="360x640")
        ok, _ = validate_output(mp4, vertical=True)
        assert ok


def test_validate_outputs_gathers_missing():
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "nope.mp4"
        errors = validate_outputs([missing])
        assert errors != []


# ── silent tail ──

def test_detect_silent_tail_positive():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "tail.mp4"
        _lavfi_video(mp4, duration=6.0, silent_tail=2.0)
        has_tail, sec = detect_silent_tail(mp4, min_sec=1.0)
        assert has_tail
        assert sec > 0


def test_detect_silent_tail_negative():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "notail.mp4"
        _lavfi_video(mp4, duration=6.0)
        has_tail, sec = detect_silent_tail(mp4, min_sec=1.0)
        assert not has_tail
        assert sec == 0.0


def test_detect_silent_tail_short_video_noop():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "short.mp4"
        _lavfi_video(mp4, duration=1.0)
        has_tail, sec = detect_silent_tail(mp4)
        assert not has_tail and sec == 0.0


# ── black frame percentage ──

def test_black_frame_pct_black_video():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "black.mp4"
        _lavfi_video(mp4, color="black")
        pct = black_frame_pct(mp4)
        assert pct > 0.8


def test_black_frame_pct_bright_video():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "blue.mp4"
        _lavfi_video(mp4, color="blue")
        pct = black_frame_pct(mp4)
        assert pct < 0.2


def test_black_frame_pct_ranges():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "wht.mp4"
        _lavfi_video(mp4, color="white")
        pct = black_frame_pct(mp4)
        assert 0.0 <= pct <= 1.0


# ── visual diversity ──

def test_visual_diversity_identical_clips_low():
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td) / "a.mp4", Path(td) / "b.mp4"
        _lavfi_video(a, color="blue", duration=2.0)
        _lavfi_video(b, color="blue", duration=2.0)
        score = visual_diversity_score([a, b])
        assert score < 0.1


def test_visual_diversity_different_clips_higher():
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a.mp4"
        b = Path(td) / "b.mp4"
        _lavfi_video(a, color="blue", duration=2.0)
        _lavfi_video(b, color="red", duration=2.0)
        score = visual_diversity_score([a, b])
        assert score > 0.1


def test_visual_diversity_single_clip_defaults_1():
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a.mp4"
        _lavfi_video(a)
        score = visual_diversity_score([a])
        assert score == 1.0


# ── expanded validation ──

def test_validate_outputs_expanded_shape():
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "facebook").mkdir()
        (Path(td) / "shorts").mkdir()
        fb = Path(td) / "facebook" / "01.mp4"
        sh = Path(td) / "shorts" / "01.mp4"
        _lavfi_video(fb, duration=3.0, size="640x360")
        _lavfi_video(sh, duration=3.0, size="360x640")
        report = validate_outputs_expanded([fb, sh])
        assert report["ok"] is True
        assert report["errors"] == []
        assert report["diversity"] == 1.0  # facebook has only one clip
        for f in (fb, sh):
            fff = report["per_file"][str(f)]
            assert fff["black_frame_pct"] < 0.2
            assert fff["silent_tail"] is False
            assert fff["size"] > 0


# ── source coverage ──

def test_source_coverage_from_selections():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        out = td / "facebook" / "01.mp4"
        _lavfi_video(out, duration=3.0, size="640x360")
        (td / "selections.json").write_text(json.dumps([
            {"start": 0.0, "end": 4.0},
            {"start": 5.0, "end": 6.0},
            {"start": 5.5, "end": 6.5},  # overlaps previous
        ]))
        cov = source_coverage([out], td / "selections.json", source_duration=10.0)
        assert cov["coverage_pct"] == 55.0  # 0-4 + 5-6.5 = 5.5s / 10s
        assert cov["overlap_count"] == 1
        assert cov["total_covered_sec"] == 5.5


def test_source_coverage_no_file():
    with tempfile.TemporaryDirectory() as td:
        cov = source_coverage([], Path(td) / "missing.json")
        assert cov["coverage_pct"] == 0
        assert cov["overlap_count"] == 0


def test_source_coverage_falls_back_to_probe():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        out = td / "out.mp4"
        _lavfi_video(out, duration=2.0)
        (td / "selections.json").write_text(json.dumps([{"start": 0, "end": 1}]))
        cov = source_coverage([out], td / "selections.json")
        assert cov["coverage_pct"] > 0


# ── write_report end-to-end ──

def test_write_report_full():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "facebook").mkdir()
        (td / "shorts").mkdir()
        fb = td / "facebook" / "01.mp4"
        sh = td / "shorts" / "01.mp4"
        _lavfi_video(fb, duration=3.0, size="640x360")
        _lavfi_video(sh, duration=3.0, size="360x640")
        (td / "selections.json").write_text(json.dumps([{"start": 1, "end": 3}]))
        src = td / "src.mp4"
        _lavfi_video(src, duration=30.0, size="640x360")
        (td / "pacing_report.json").write_text(json.dumps({"mean_length_s": 2.0}))
        qa_path = write_report(td, [fb, sh], source_path=str(src))
        data = json.loads(qa_path.read_text())
        assert data["ok"] is True
        assert "source_coverage" in data
        assert data["source_coverage"]["coverage_pct"] > 0
        assert "pacing" in data
        assert data["pacing"] == {"mean_length_s": 2.0}
        assert "diversity" in data
        assert "per_file" in data


def test_write_report_raises_on_silent_tail():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "facebook").mkdir()
        (td / "shorts").mkdir()
        fb = td / "facebook" / "01.mp4"
        sh = td / "shorts" / "01.mp4"
        _lavfi_video(fb, duration=6.0, size="640x360", silent_tail=2.0)
        _lavfi_video(sh, duration=6.0, size="360x640", silent_tail=2.0)
        (td / "selections.json").write_text(json.dumps([{"start": 1, "end": 3}]))
        src = td / "src.mp4"
        _lavfi_video(src, duration=30.0)
        with pytest.raises(RuntimeError, match="silent tail"):
            write_report(td, [fb, sh], source_path=str(src))