"""Audio analysis stage: provenance recording + cache/resume parity.

Pins the pluggable-VAD contract from the mandate: the summary must name which
backend/model actually ran (vad_model, fallback_reason, hysteresis), and the
score cache must be keyed by backend so switching backends never serves another
backend's cached scores. Resume must reuse PCM + scores without re-running the
model.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from engine.audio_analysis import run_audio_analysis

_SR = 16000


def _tone_wav(path: Path, seconds: float = 2.0, amp: float = 0.4) -> Path:
    """Deterministic 16kHz mono WAV: 0.5s tone bursts separated by silence."""
    n = int(seconds * _SR)
    t = np.arange(n, dtype=np.float32) / _SR
    burst = (amp * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    gate = (np.floor(t * 2) % 2 == 0).astype(np.float32)  # 0.5s on / 0.5s off
    samples = burst * gate
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(_SR)
        f.writeframes((samples * 32767.0).astype(np.int16).tobytes())
    return path


@pytest.fixture
def media(tmp_path: Path) -> Path:
    return _tone_wav(tmp_path / "media" / "clip.wav")


def test_summary_records_backend_and_model_provenance(tmp_path, media):
    summary = run_audio_analysis(media, tmp_path / "proj", source="clip.wav")
    assert summary["vad_backend"] == "energy"
    assert summary["vad_model"] == "energy-rms"
    assert summary["frame_ms"] == 32  # 512 samples @16kHz
    assert summary["n_frames"] > 0
    assert summary["duration"] > 0


def test_summary_records_hysteresis_that_actually_ran(tmp_path, media):
    summary = run_audio_analysis(
        media, tmp_path / "proj", source="clip.wav",
        threshold=0.6, min_speech_ms=200, min_silence_ms=120,
    )
    h = summary["hysteresis"]
    assert h["threshold"] == 0.6
    assert h["release"] == 0.45  # max(0.6 - 0.15, 0.05)
    assert h["min_speech_ms"] == 200
    assert h["min_silence_ms"] == 120


def test_export_fcpxml_produces_a_readable_file(tmp_path, media):
    """The `export_fcpxml` flag must actually write FCPXML. Regression: the
    bool parameter once shadowed the imported writer, so the flag silently
    raised 'bool' object is not callable whenever it was True."""
    summary = run_audio_analysis(
        media, tmp_path / "proj", source="clip.wav", export_fcpxml=True,
    )
    fcpxml = Path(summary["fcpxml"])
    assert fcpxml.is_file(), "fcpxml path must exist when export_fcpxml is set"
    text = fcpxml.read_text(encoding="utf-8")
    assert "<fcpxml" in text
    assert Path(summary["cutlist"]).is_file()


def test_export_fcpxml_off_by_default(tmp_path, media):
    summary = run_audio_analysis(media, tmp_path / "proj", source="clip.wav")
    assert summary["fcpxml"] is None


def test_release_floor_clamps_for_low_threshold(tmp_path, media):
    summary = run_audio_analysis(
        media, tmp_path / "proj", source="clip.wav", threshold=0.1,
    )
    assert summary["hysteresis"]["release"] == 0.05  # max(0.1 - 0.15, 0.05)


def test_resume_reuses_pcm_and_scores(tmp_path, media):
    first = run_audio_analysis(media, tmp_path / "proj", source="clip.wav")
    assert first["pcm_cached"] is False
    assert first["scores_cached"] is False

    second = run_audio_analysis(media, tmp_path / "proj", source="clip.wav")
    assert second["pcm_cached"] is True
    assert second["scores_cached"] is True
    # Cached scores reproduce identical provenance + frame grid.
    assert second["vad_backend"] == first["vad_backend"]
    assert second["vad_model"] == first["vad_model"]
    assert second["n_frames"] == first["n_frames"]


def test_force_bypasses_the_score_cache(tmp_path, media):
    run_audio_analysis(media, tmp_path / "proj", source="clip.wav")
    again = run_audio_analysis(media, tmp_path / "proj", source="clip.wav", force=True)
    assert again["scores_cached"] is False


def test_score_cache_key_is_isolated_per_backend(tmp_path, media):
    """Switching backends must never read another backend's cached scores."""
    energy = run_audio_analysis(
        media, tmp_path / "proj", source="clip.wav", vad_backend="energy",
    )
    assert energy["vad_backend"] == "energy"
    score_dir = tmp_path / "proj" / "audio"
    assert any(p.name.endswith(".energy.vad_raw.json") for p in score_dir.iterdir())

    words = [type("W", (), {"text": "hi", "start": 0.0, "end": 1.0})()]
    whisper = run_audio_analysis(
        media, tmp_path / "proj", source="clip.wav",
        vad_backend="whisper", words=words,
    )
    assert whisper["vad_backend"] == "whisper"
    assert whisper["vad_model"] == "whisper-wordtimestamps"
    # Distinct cache files coexist; the whisper run did not reuse energy scores.
    names = {p.name for p in score_dir.iterdir() if p.name.endswith(".vad_raw.json")}
    assert any(n.endswith(".energy.vad_raw.json") for n in names)
    assert any(n.endswith(".whisper.vad_raw.json") for n in names)
    assert whisper["scores_cached"] is False


def test_reproducible_segmentation_across_runs(tmp_path, media):
    """Threshold changes re-segment cached scores; the model never re-runs."""
    a = run_audio_analysis(
        media, tmp_path / "proj", source="clip.wav", threshold=0.5,
    )
    b = run_audio_analysis(
        media, tmp_path / "proj", source="clip.wav", threshold=0.9,
    )
    assert b["scores_cached"] is True, "re-tuning must not re-run the VAD model"
    assert a["speech_regions"] >= 0 and b["speech_regions"] >= 0
