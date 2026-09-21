"""End-to-end pipeline orchestration on synthetic media.

Patches only the two network boundaries (download + transcribe) and runs the
real run() body: transcript cache, thoughts/rank/context-gate, captions,
content packs, real ffmpeg renders, QA report, and the resume/cache branches.
This is the coverage backbone for selekyt/pipeline.py.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from models.core import Segment, Word

_REPO = Path(__file__).resolve().parent.parent
_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not available")


def _synthetic_video(out: Path, frames: int = 180, size: int = 320, fps: int = 10) -> Path:
    """16:9 mp4 with a silent audio track: the QA gate requires landscape
    video plus an audio stream on every output."""
    import cv2
    import numpy as np
    import subprocess

    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.parent / f"{out.stem}_silent.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(raw), fourcc, fps, (size, size))
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
    # pad to 16:9 and add a continuous tone so the silent-tail QA check
    # passes (a fully silent track reads as one 16s "silent tail")
    tone = out.parent / f"{out.stem}_tone.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=20", str(tone),
        ],
        check=True, capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(raw), "-i", str(tone),
            "-filter:v", "pad=iw*16/9:ih:(ow-iw)/2:0:black",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-shortest", "-movflags", "+faststart",
            str(out),
        ],
        check=True, capture_output=True,
    )
    return out


def _transcript() -> list[Segment]:
    """Word-timed transcript forming two 8s thoughts with sentence boundaries.

    make_thoughts needs >= 6s spans ending in terminal punctuation, and the
    context guard rejects clips that start with a connective or anaphora, so
    the first thought starts with a subject and each sentence ends cleanly.
    """

    def w(text: str, start: float, end: float) -> Word:
        return Word(text, start, end)

    seg1 = Segment(
        0.0, 8.0,
        "We shipped the feature on Tuesday morning. The metric moved "
        "forty percent overnight. The team celebrated the launch.",
        [
            w("We", 0.0, 0.2), w("shipped", 0.2, 0.5), w("the", 0.5, 0.7),
            w("feature", 0.7, 1.1), w("on", 1.1, 1.3), w("Tuesday", 1.3, 1.7),
            w("morning", 1.7, 2.1), w("The", 2.1, 2.3), w("metric", 2.3, 2.7),
            w("moved", 2.7, 3.1), w("forty", 3.1, 3.5), w("percent", 3.5, 3.9),
            w("overnight", 3.9, 4.4), w("The", 4.4, 4.6), w("team", 4.6, 5.0),
            w("celebrated", 5.0, 5.5), w("the", 5.5, 5.7), w("launch", 5.7, 8.0),
        ],
    )
    seg2 = Segment(
        8.0, 16.0,
        "The rollout completed ahead of schedule. Customers reported "
        "faster load times across the board. This outcome validated "
        "the quarter of hard work.",
        [
            w("The", 8.0, 8.2), w("rollout", 8.2, 8.6), w("completed", 8.6, 9.1),
            w("ahead", 9.1, 9.4), w("of", 9.4, 9.6), w("schedule", 9.6, 10.1),
            w("Customers", 10.1, 10.6), w("reported", 10.6, 11.1),
            w("faster", 11.1, 11.5), w("load", 11.5, 11.8), w("times", 11.8, 12.1),
            w("across", 12.1, 12.5), w("the", 12.5, 12.7), w("board", 12.7, 13.1),
            w("This", 13.1, 13.4), w("outcome", 13.4, 13.9),
            w("validated", 13.9, 14.4), w("the", 14.4, 14.6),
            w("quarter", 14.6, 15.0), w("of", 15.0, 15.2), w("hard", 15.2, 15.5),
            w("work", 15.5, 16.0),
        ],
    )
    return [seg1, seg2]


@pytest.fixture
def media(tmp_path: Path) -> Path:
    return _synthetic_video(tmp_path / "source" / "clip.mp4")


@pytest.fixture
def patched_pipeline(monkeypatch, media):
    import selekyt.pipeline as p

    monkeypatch.setattr(
        p, "download", lambda source, dest, cookies=None: (media, {"title": "synthetic clip"}),
    )
    monkeypatch.setattr(
        p, "transcribe_auto",
        lambda media, **kw: (
            _transcript(),
            "en",
            {"engine": "test", "model": "test", "word_timestamps": "test",
             "fallback_reason": ""},
        ),
    )
    return p


def test_run_produces_the_full_project_tree(patched_pipeline, tmp_path, media):
    project = patched_pipeline.run(
        "https://example.com/v", tmp_path, model="small", limit=2,
    )
    assert project == tmp_path / "project"
    for name in (
        "pipeline_state.json", "transcript.json", "selections.json",
        "editorial_report.json", "editorial_corpus.json",
        "pacing_report.json", "edit_plan.json",
        "content_pack.json", "report.json",
    ):
        assert (project / name).is_file(), f"missing {name}"
    for sub in ("captions", "facebook", "shorts", "content_pack"):
        assert (project / sub).is_dir(), f"missing {sub}/"

    report = json.loads((project / "report.json").read_text())
    assert report["source"] == "https://example.com/v"
    assert report["title"] == "synthetic clip"
    assert report["language"] == "en"
    assert report["selected_count"] >= 1
    assert report["audio"] == {"enabled": False}, "audio stage is explicitly off"
    assert "fabricated" in report["policy"]

    # The corpus layer used to be reachable only by importing engine.corpus
    # directly; the report must now name it, and the path it names must exist.
    corpus = json.loads((project / "editorial_corpus.json").read_text())
    assert corpus["build"]["schema"]
    assert report["editorial_corpus"] == str(project / "editorial_corpus.json")

    # captions for every selected clip, both formats
    packs = json.loads((project / "content_pack.json").read_text())
    assert len(packs) == report["selected_count"]
    for pack in packs:
        assert pack["source_based"] is True
        assert pack["titles"], "every clip ships title variants"
    idx = "01"
    assert (project / "captions" / f"{idx}.ass").is_file()
    assert (project / "captions" / f"{idx}.srt").is_file()
    assert (project / "facebook" / f"{idx}.mp4").stat().st_size > 0
    assert (project / "shorts" / f"{idx}.mp4").stat().st_size > 0


def test_audio_stage_reports_provenance_when_enabled(patched_pipeline, tmp_path, media):
    project = patched_pipeline.run(
        "https://example.com/v", tmp_path, model="small", limit=1,
        audio_analysis=True, vad_backend="energy",
    )
    report = json.loads((project / "report.json").read_text())
    assert report["audio"]["vad_backend"] == "energy"
    assert report["audio"]["vad_model"] == "energy-rms"
    assert report["audio"]["frame_ms"] == 32
    assert (project / "audio").is_dir()


def test_resume_reuses_download_transcript_and_renders(patched_pipeline, tmp_path, media, capsys):
    args = ("https://example.com/v", tmp_path, "small", None, 2)
    patched_pipeline.run(*args)
    capsys.readouterr()  # discard first-run output
    project = patched_pipeline.run(*args)
    second = capsys.readouterr().out

    # all three caches hit on the second run
    assert "reusing downloaded source" in second, "download cache must hit on resume"
    assert "using unchanged transcript" in second, "transcript cache must hit on resume"
    assert "reusing rendered clip" in second, "render cache must hit on resume"
    assert (project / "report.json").is_file()


def test_local_source_path_is_used_directly(patched_pipeline, tmp_path, media):
    project = patched_pipeline.run(str(media), tmp_path, model="small", limit=1)
    state = json.loads((project / "pipeline_state.json").read_text())
    assert "media_path" not in state, "local files are not cached as downloads"
    assert (project / "report.json").is_file()


def test_missing_local_source_raises(monkeypatch, tmp_path):
    import selekyt.pipeline as p
    with pytest.raises(RuntimeError, match="Local video not found"):
        p.run(str(tmp_path / "nope.mp4"), tmp_path, model="small")


def test_transcript_cache_is_invalidated_on_model_change(patched_pipeline, tmp_path, media, capsys):
    patched_pipeline.run("https://example.com/v", tmp_path, model="small", limit=1)
    capsys.readouterr()
    patched_pipeline.run("https://example.com/v", tmp_path, model="medium", limit=1)
    out = capsys.readouterr().out
    assert "[transcribe]" in out, "a model change must bust the transcript cache"
