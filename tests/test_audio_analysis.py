"""Audio analysis stage: provenance recording + cache/resume parity.

Pins the pluggable-VAD contract from the mandate: the summary must name which
backend/model actually ran (vad_model, fallback_reason, hysteresis), and the
score cache must be keyed by backend so switching backends never serves another
backend's cached scores. Resume must reuse PCM + scores without re-running the
model.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from engine.audio_analysis import run_audio_analysis

_SR = 16000

# The fixture is a *voiced excitation*, not a tone, and the distinction
# matters. The chain prefers a neural speech model, and a speech
# model is entitled to score a pure tone as non-speech: the previous fixture was
# a 220 Hz sine gated on and off, and silero scores it at a maximum of 0.503 —
# which sounds close to the default 0.5 threshold but yields **zero speech
# regions at every threshold**, so the cutlist was empty and any test that
# needed a selection died with "nothing to keep: the cutlist removes the whole
# timeline". The energy and ffmpeg backends scored that same tone 1.0
# throughout, so the fixture passed for as long as one of them was preferred.
# The properties this suite pins (provenance, cache keying, resume, threshold
# reach) are about the plumbing, not about one backend's notion of speech, so
# the fixture has to look like speech to every backend. A 120 Hz fundamental
# with eight harmonics and a 4 Hz syllable envelope at depth 0.8 sits in the
# plateau where all three segment both bursts; the design space and its
# boundaries are recorded in `docs/AI_EXECUTION_STATE.md`.
_F0_HZ = 120.0
_HARMONICS = 8
_SYLLABLE_HZ = 4.0
_SYLLABLE_DEPTH = 0.8


def _speech_wav(path: Path, seconds: float = 2.0, amp: float = 0.9) -> Path:
    """Deterministic 16kHz mono WAV: 0.5s voiced bursts separated by silence."""
    n = int(seconds * _SR)
    t = np.arange(n, dtype=np.float32) / _SR
    v = np.zeros(n, dtype=np.float32)
    for k in range(1, _HARMONICS + 1):
        v += np.sin(2 * np.pi * k * _F0_HZ * t)
    v /= _HARMONICS
    v *= 1.0 - _SYLLABLE_DEPTH + _SYLLABLE_DEPTH * np.sin(
        2 * np.pi * _SYLLABLE_HZ * t
    )
    gate = (np.floor(t * 2) % 2 == 0).astype(np.float32)  # 0.5s on / 0.5s off
    samples = v * gate
    # Peak-normalise: the harmonic stack's peak is not predictable from the
    # harmonic count, and the energy backend compares RMS against fixed absolute
    # thresholds, so an unnormalised level leaves whether this fixture yields a
    # selection to chance on the one host whose default backend is level-blind.
    peak = float(np.max(np.abs(samples)))
    if peak > 0:
        samples = samples * (amp / peak)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(_SR)
        f.writeframes((samples * 32767.0).astype(np.int16).tobytes())
    return path


@pytest.fixture
def media(tmp_path: Path) -> Path:
    return _speech_wav(tmp_path / "media" / "clip.wav")


# backend name -> the model string that backend reports. Which one is
# auto-selected depends on what this host has installed (silero where the
# vendored model and onnxruntime are present, ffmpeg or energy otherwise), so
# asserting a specific one pins the machine rather than the contract.
_BACKEND_MODELS = {
    "energy": "energy-rms",
    "ffmpeg": "silencedetect",
    "silero": "silero-v6",
    "whisper": "whisper-wordtimestamps",
}


def test_summary_records_backend_and_model_provenance(tmp_path, media):
    summary = run_audio_analysis(media, tmp_path / "proj", source="clip.wav")
    assert summary["vad_backend"] in _BACKEND_MODELS, summary["vad_backend"]
    assert summary["vad_model"] == _BACKEND_MODELS[summary["vad_backend"]]
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
    """Threshold changes re-segment cached scores; the model never re-runs.

    The two thresholds are derived from the scores the first run cached rather
    than hard-coded, and the reason is a measurement, not a style choice. This
    suite's fixture is voiced excitation, so every backend's score series is
    bimodal — frames are either near zero or near the peak — and *any* two
    thresholds inside that gap give byte-identical regions. On the old tone
    fixture the pair 0.3/0.5 appeared to straddle the boundary only because one
    backend's peak happened to sit at 0.503, i.e. the test was measuring a
    knife-edge on one host's preferred backend, and it went from passing to
    segfaulting the whole suite the day the chain preferred a different one.

    Deriving instead: `lo` is below the peak so speech survives, `hi` is above
    the peak so none does. That is a real difference on every backend by
    construction, and it fails loudly rather than vacuously on a fixture no
    backend hears — the guard below is the assertion that the fixture is still
    speech, which is the part that actually broke last time. `hi` is allowed
    above 1.0 deliberately: scores are probabilities in [0, 1], so a threshold
    above the peak is the only value guaranteed to exclude a frame sitting
    *at* the peak, and the saturating backends put their peak at exactly 1.0 —
    capping `hi` at 1.0 would make it inclusive (`s >= threshold`) and give the
    same regions as `lo` on a host whose backend saturates.
    """
    warm = run_audio_analysis(
        media, tmp_path / "proj", source="clip.wav", threshold=0.5,
    )
    scores = json.loads(Path(warm["vad_score_file"]).read_text(encoding="utf-8"))
    peak = max(scores["scores"])
    assert peak > 0.2, f"the fixture scored no speech above 0.2 (peak {peak})"
    lo = peak / 2.0
    hi = peak + 0.1

    a = run_audio_analysis(
        media, tmp_path / "proj", source="clip.wav", threshold=lo,
    )
    a_regions = json.loads(Path(a["vad_regions"]).read_text(encoding="utf-8"))
    b = run_audio_analysis(
        media, tmp_path / "proj", source="clip.wav", threshold=hi,
    )
    b_regions = json.loads(Path(b["vad_regions"]).read_text(encoding="utf-8"))
    same = run_audio_analysis(
        media, tmp_path / "proj", source="clip.wav", threshold=lo,
    )
    same_regions = json.loads(Path(same["vad_regions"]).read_text(encoding="utf-8"))
    assert warm["scores_cached"] is False, "the first run must score the audio"
    assert b["scores_cached"] is True, "re-tuning must not re-run the VAD model"
    assert same["scores_cached"] is True
    assert Path(b["vad_score_file"]) == Path(a["vad_score_file"]) == \
        Path(warm["vad_score_file"]), \
        "a threshold change reused a different score file than the first run"
    # The threshold genuinely reaches the segmenter: the same cached scores,
    # sliced at a different point, give a different cut.
    assert b_regions["speech"] != a_regions["speech"], \
        f"thresholds {lo:.3f} and {hi:.3f} both produce {a_regions['speech']}"
    # Reproducible means a re-run at the same threshold lands on the same
    # regions, not merely on a non-negative count — which an empty result also
    # satisfies. The cached scores make this deterministic, so any difference
    # here is state leaking between runs.
    assert same_regions["speech"] == a_regions["speech"], "segments are not reproducible"
    assert same["speech_regions"] == a["speech_regions"]
    assert same["speech_ratio"] == a["speech_ratio"]
    # Monotone over the same scores: the frames above `hi` are a subset of those
    # above `lo`, so the stricter tuning cannot retain more speech than the
    # laxer one. This holds even where the two thresholds agree, so it is the
    # part of the assertion that survives a fixture whose scores saturate.
    assert b_regions["config"]["threshold"] == pytest.approx(hi)
    b_speech = sum(r[1] - r[0] for r in b_regions["speech"])
    a_speech = sum(r[1] - r[0] for r in a_regions["speech"])
    assert b_speech <= a_speech, "a stricter threshold retained more speech than a laxer one"
