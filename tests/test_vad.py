"""VAD backend tests — unit tests use synthetic signals (deterministic,
no media files); integration tests use the local WAV corpus when present."""
import wave
import numpy as np
import pytest

from engine.vad import (
    EnergyVAD, FFmpegVADBackend, WhisperVADBackend, SileroVADBackend,
    get_vad_backend, segment_from_scores, segment_with_meta, silence_from_speech,
    save_scores, load_scores, SpeechRegion, SilenceRegion, VADResult,
)


def _write_tone_wav(tmp_path, freq=440, duration=1.0, amplitude=0.5, sr=16000):
    path = tmp_path / "tone.wav"
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    sig = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((sig * 32767).astype(np.int16).tobytes())
    return path


def _write_speech_silence_wav(tmp_path, sr=16000):
    """0-2s silence, 2-5s tone (speech), 5-7s silence, 7-9s tone."""
    path = tmp_path / "mix.wav"
    dur = 9.0
    n = int(sr * dur)
    sig = np.zeros(n, dtype=np.float32)
    t2 = np.linspace(0, 3, 3 * sr, endpoint=False)
    speech1 = 0.4 * np.sin(2 * np.pi * 220 * t2)
    t4 = np.linspace(0, 2, 2 * sr, endpoint=False)
    speech2 = 0.4 * np.sin(2 * np.pi * 180 * t4)
    sig[2 * sr:5 * sr] = speech1[:3 * sr]
    sig[7 * sr:9 * sr] = speech2[:2 * sr]
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
        f.writeframes((sig * 32767).astype(np.int16).tobytes())
    return path


# ── segmentation (pure functions) ──

def test_segment_identifies_speech():
    scores = [0.0] * 10 + [0.9] * 20 + [0.0] * 10 + [0.8] * 15
    regions = segment_from_scores(scores, frame_ms=30, threshold=0.5,
                                  min_speech_ms=100, min_silence_ms=50)
    assert len(regions) == 2
    assert abs(regions[0].start - 10 * 0.03) < 0.05
    assert abs(regions[1].start - 40 * 0.03) < 0.05


def test_segment_hysteresis_keeps_mid_speech_dips():
    scores = [0.0] * 5 + [0.9] * 10 + [0.45] * 5 + [0.9] * 10
    strict = segment_from_scores(scores, frame_ms=30, threshold=0.5,
                                 release=0.35, min_speech_ms=100, min_silence_ms=50)
    assert len(strict) == 1, "hysteresis release=0.35 keeps dip at 0.45"


def test_segment_release_stricter_splits():
    scores = [0.0] * 5 + [0.9] * 10 + [0.2] * 5 + [0.9] * 10
    regions = segment_from_scores(scores, frame_ms=30, threshold=0.5,
                                  release=0.35, min_speech_ms=50, min_silence_ms=20)
    assert len(regions) == 2


def test_segment_min_speech_drops_short():
    scores = [0.9] * 2  # 60ms < 150ms
    regions = segment_from_scores(scores, frame_ms=30, min_speech_ms=150)
    assert regions == []


def test_segment_min_speech_keeps_longer():
    scores = [0.9] * 6  # 180ms >= 150ms
    regions = segment_from_scores(scores, frame_ms=30, min_speech_ms=150)
    assert len(regions) == 1


def test_segment_min_silence_merges_close():
    scores = [0.9] * 10 + [0.0] * 2 + [0.9] * 10  # 60ms gap
    regions = segment_from_scores(scores, frame_ms=30, min_silence_ms=100)
    assert len(regions) == 1


def test_segment_min_silence_splits_large_gap():
    scores = [0.9] * 10 + [0.0] * 8 + [0.9] * 10  # 240ms gap
    regions = segment_from_scores(scores, frame_ms=30, min_silence_ms=100)
    assert len(regions) == 2


def test_segment_threshold_controls_sensitivity():
    scores = [0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4]
    assert segment_from_scores(scores, frame_ms=30, threshold=0.5, min_speech_ms=100) == []
    assert len(segment_from_scores(scores, frame_ms=30, threshold=0.3, min_speech_ms=100)) == 1


def test_silence_from_speech_complement():
    speech = [SpeechRegion(2.0, 5.0)]
    sil = silence_from_speech(speech, 9.0)
    assert len(sil) == 2
    assert sil[0].start == 0.0 and abs(sil[0].end - 2.0) < 0.01
    assert abs(sil[1].start - 5.0) < 0.01 and abs(sil[1].end - 9.0) < 0.01


def test_silence_from_speech_no_speech():
    sil = silence_from_speech([], 5.0)
    assert sil == [SilenceRegion(0.0, 5.0, 5.0)]


# ── Energy VAD ──

def test_energy_vad_detects_tone(tmp_path):
    path = _write_tone_wav(tmp_path)
    backend = EnergyVAD()
    result = backend.analyze(path)
    assert result.backend == "energy"
    assert len(result.scores) > 0
    assert any(s > 0.5 for s in result.scores)


def test_energy_vad_silence_then_speech_regions(tmp_path):
    path = _write_speech_silence_wav(tmp_path)
    backend = EnergyVAD()
    result = backend.analyze(path)
    regions = segment_from_scores(result.scores, result.sample_rate, result.frame_ms,
                                  threshold=0.5, min_speech_ms=200, min_silence_ms=100)
    assert len(regions) == 2
    assert regions[0].start > 1.0  # skip leading silence
    assert abs(regions[-1].end - 9.0) < 1.0


def test_cached_scores_resegment_with_different_params(tmp_path):
    """The core caching architecture: one scoring pass, many segmentations."""
    path = _write_speech_silence_wav(tmp_path)
    backend = EnergyVAD()
    result = backend.analyze(path)

    # Two different parameter sets over the SAME cached scores
    loose = segment_from_scores(result.scores, result.sample_rate, result.frame_ms,
                                threshold=0.3, min_speech_ms=100, min_silence_ms=50)
    strict = segment_from_scores(result.scores, result.sample_rate, result.frame_ms,
                                 threshold=0.8, min_speech_ms=500, min_silence_ms=400)

    # Loose params should find at least as much speech as strict; and one
    # parameter set must be sufficient — never re-run the backend.
    assert len(loose) >= len(strict)
    total_loose = sum(r.end - r.start for r in loose)
    total_strict = sum(r.end - r.start for r in strict)
    assert total_loose >= total_strict


# ── FFmpeg backend ──

def test_ffmpeg_backend_available():
    backend = FFmpegVADBackend()
    assert backend.is_available()  # ffmpeg present on this device
    assert backend.name == "ffmpeg"


def test_ffmpeg_backend_analyze_silence_tone(tmp_path):
    _write_speech_silence_wav(tmp_path)
    path = next(tmp_path.glob("mix.wav"))
    backend = FFmpegVADBackend()
    result = backend.analyze(path)
    assert result.backend == "ffmpeg"
    assert len(result.scores) > 0
    assert result.duration > 0
    regions = segment_from_scores(result.scores, result.sample_rate, result.frame_ms,
                                  threshold=0.5, min_speech_ms=200, min_silence_ms=100)
    assert len(regions) == 2


# ── Whisper backend ──

def test_whisper_backend_uses_word_timestamps(tmp_path):
    words = [type("W", (), {"start": 1.0, "end": 2.0})(),
             type("W", (), {"start": 4.0, "end": 5.0})()]
    backend = WhisperVADBackend(words, duration=6.0)
    result = backend.analyze(tmp_path)
    assert result.backend == "whisper"
    regions = segment_from_scores(result.scores, result.sample_rate, result.frame_ms,
                                  threshold=0.5, min_speech_ms=100, min_silence_ms=100)
    assert len(regions) == 2
    assert abs(regions[0].start - 1.0) < 0.1
    assert abs(regions[1].start - 4.0) < 0.1


# ── Silero (never available here) ──

def test_silero_not_available_unless_onnxruntime():
    backend = SileroVADBackend()
    # on this environment onnxruntime is not installed → unavailable
    assert backend.is_available() is False


def test_silero_reports_unavailable_even_when_onnxruntime_is_present(monkeypatch):
    """The old ``is_available`` asked only whether onnxruntime was importable
    and whether a model path was supplied — both of which a host can satisfy —
    while ``analyze`` raised ``NotImplementedError`` regardless. So the backend
    could report available and then fail at the call. The stub has to say no
    whatever the environment has, because the inference path does not exist."""
    import sys, types

    fake = types.ModuleType("onnxruntime")
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    assert SileroVADBackend(model_path="/nonexistent/silero_v5.onnx").is_available() is False


def test_forcing_the_silero_backend_says_it_is_not_implemented():
    """A user who passes ``--vad-backend silero`` (it is in the CLI help and
    the runbook) is not served by "backend 'silero' unavailable" — that sends
    them to install onnxruntime, which cannot help, since the backend is a
    placeholder. The message has to name the real condition and the backends
    that do work."""
    with pytest.raises(RuntimeError) as excinfo:
        get_vad_backend(force="silero")
    message = str(excinfo.value)
    assert "not implemented" in message
    assert "energy" in message


# ── backend selection and persistence ──

def test_get_vad_backend_default_is_energy_or_ffmpeg():
    backend = get_vad_backend()
    assert backend.name in ("energy", "ffmpeg")
    assert backend.is_available()


def test_get_vad_backend_force_energy():
    backend = get_vad_backend(force="energy")
    assert backend.name == "energy"


def test_get_vad_backend_force_unknown():
    with pytest.raises(ValueError):
        get_vad_backend(force="bogus")


def test_save_load_scores_roundtrip(tmp_path):
    result = VADResult(scores=[0.1, 0.2, 0.9, 0.8], sample_rate=16000,
                       backend="energy", frame_ms=30, duration=0.5)
    path = tmp_path / "analysis" / "vad_raw.json"
    save_scores(result, path)
    loaded = load_scores(path)
    assert loaded is not None
    assert loaded.scores == [0.1, 0.2, 0.9, 0.8]
    assert loaded.backend == "energy"
    assert loaded.duration == 0.5


def test_load_scores_missing(tmp_path):
    assert load_scores(tmp_path / "nope.json") is None


def test_load_scores_corrupt(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_scores(p) is None


# ── AutoCut parity (reference values computed from rust/core/src/vad.rs) ──

def test_parity_reference_boundaries_32ms():
    """AutoCut-exact run boundaries at the 32ms Silero cadence (2-chunk gap merges)."""
    scores = [0.9] * 10 + [0.0] * 2 + [0.9] * 10
    regions = segment_from_scores(scores, frame_ms=32, threshold=0.5,
                                  min_silence_ms=100, min_speech_ms=150)
    assert len(regions) == 1
    assert regions[0].start == 0.0
    assert abs(regions[0].end - 22 * 0.032) < 1e-9


def test_parity_reference_gap_stays_split():
    """4-chunk gap (128ms) strictly exceeds min_silence 100ms → two regions."""
    scores = [0.9] * 10 + [0.0] * 4 + [0.9] * 10
    regions = segment_from_scores(scores, frame_ms=32, threshold=0.5,
                                  min_silence_ms=100, min_speech_ms=150)
    assert len(regions) == 2
    assert regions[0].start == 0.0
    assert abs(regions[0].end - 10 * 0.032) < 1e-9
    assert abs(regions[1].start - 14 * 0.032) < 1e-9
    assert abs(regions[1].end - 24 * 0.032) < 1e-9


def test_parity_release_floor_clamps_low_threshold():
    """release = max(threshold-0.15, 0.05): a dip at 0.04 splits, 0.06 survives."""
    dip_below = [0.2] * 6 + [0.04] * 4 + [0.2] * 6
    dip_above = [0.2] * 6 + [0.06] * 4 + [0.2] * 6
    kw = dict(frame_ms=32, threshold=0.1, min_speech_ms=10, min_silence_ms=0)
    assert len(segment_from_scores(dip_below, **kw)) == 2
    assert len(segment_from_scores(dip_above, **kw)) == 1


def test_segment_with_meta_reports_hysteresis():
    scores = [0.9] * 10 + [0.0] * 2 + [0.9] * 10
    regions, meta = segment_with_meta(scores, frame_ms=32, threshold=0.5,
                                      min_speech_ms=100, min_silence_ms=100)
    assert len(regions) == 1
    assert meta["hysteresis"]["threshold"] == 0.5
    assert meta["hysteresis"]["release"] == 0.35
    assert meta["speech_regions"] == 1
    assert meta["speech_seconds"] > 0
    assert meta["time_offset"] == 0.0


def test_parity_time_offset_shifts_boundaries_not_durations():
    """`time_offset` is AutoCut's DetectParams::source_offset: detection on a
    linked track that rolled after the reference still yields project-time
    boundaries. Durations are untouched — only placement moves."""
    scores = [0.9] * 10 + [0.0] * 4 + [0.9] * 10
    base = segment_from_scores(scores, frame_ms=32, threshold=0.5,
                               min_silence_ms=100, min_speech_ms=150)
    shifted = segment_from_scores(scores, frame_ms=32, threshold=0.5,
                                  min_silence_ms=100, min_speech_ms=150,
                                  time_offset=120.5)
    assert len(shifted) == len(base) == 2
    for b, s in zip(base, shifted):
        assert s.start == pytest.approx(b.start + 120.5)
        assert s.end == pytest.approx(b.end + 120.5)
        assert (s.end - s.start) == pytest.approx(b.end - b.start), "offset must not rescale"


def test_time_offset_zero_is_the_default_and_a_no_op():
    scores = [0.9] * 6 + [0.0] * 3 + [0.9] * 6
    kw = dict(frame_ms=32, threshold=0.5, min_silence_ms=100, min_speech_ms=0)
    assert segment_from_scores(scores, **kw) == segment_from_scores(scores, time_offset=0.0, **kw)


def test_segment_with_meta_records_the_offset():
    scores = [0.9] * 8
    _, meta = segment_with_meta(scores, frame_ms=32, time_offset=7.0, min_speech_ms=0)
    assert meta["time_offset"] == 7.0
    # speech_seconds is a duration, so the offset must not inflate it.
    assert abs(meta["speech_seconds"] - 8 * 0.032) < 1e-9


# ── backend provenance (which model actually ran) ──

def test_energy_backend_reports_model_and_fallback_reason(tmp_path):
    path = _write_tone_wav(tmp_path)
    backend = EnergyVAD(fallback_reason="silero unavailable")
    result = backend.analyze(path)
    assert result.vad_model == "energy-rms"
    assert result.fallback_reason == "silero unavailable"


def test_default_backend_reports_fallback_reason():
    backend = get_vad_backend()
    # This device never has silero; the selected backend must say so.
    if backend.name != "silero":
        assert backend.fallback_reason is not None
        assert "silero" in backend.fallback_reason


def test_save_load_preserves_provenance(tmp_path):
    result = VADResult(scores=[0.1, 0.9], sample_rate=16000, backend="energy",
                       frame_ms=32, duration=0.1,
                       vad_model="energy-rms", hysteresis={"release": 0.35},
                       fallback_reason="silero unavailable on this device")
    path = tmp_path / "vad_raw.json"
    save_scores(result, path)
    loaded = load_scores(path)
    assert loaded.vad_model == "energy-rms"
    assert loaded.hysteresis == {"release": 0.35}
    assert loaded.fallback_reason == "silero unavailable on this device"