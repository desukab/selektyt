"""VAD backend tests — unit tests use synthetic signals (deterministic,
no media files); integration tests use the local WAV corpus when present."""
import wave
from pathlib import Path

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


# ── Silero (implemented; available only where the model file resolves) ──

def test_silero_is_unavailable_without_a_model_file():
    """``is_available`` is not "can onnxruntime be imported": weights are
    needed too, and a path that does not resolve must report unavailable even
    on a host that has onnxruntime."""
    assert SileroVADBackend(model_path="/nonexistent/silero_v6.onnx").is_available() is False


def test_silero_is_unavailable_when_only_onnxruntime_is_present(monkeypatch):
    """The mirror case: onnxruntime importable, no weights. The old stub inverted
    this — it reported unavailable on every host because the inference path did
    not exist, which was correct for the wrong reason."""
    import sys, types

    fake = types.ModuleType("onnxruntime")
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    assert SileroVADBackend(model_path="/nonexistent/silero_v6.onnx").is_available() is False


def test_silero_is_available_where_the_vendored_model_ships():
    """The model is vendored under ``engine/models`` so a checkout is runnable
    without a download step. This is the assertion that keeps it that way: if
    the file stops shipping, this test fails rather than the first real run."""
    here = Path(__file__).resolve().parent.parent / "engine" / "models" / SileroVADBackend._MODEL_NAME
    if not here.is_file():
        pytest.skip(f"{here.name} absent from this checkout")
    assert SileroVADBackend().is_available() is True
    assert here in SileroVADBackend()._candidate_paths()


def test_silero_user_copy_takes_precedence_over_the_vendored_one(tmp_path, monkeypatch):
    """An operator who drops a model into ``~/.selektyt/models`` has chosen it
    deliberately, and that choice must outrank the shipped file — the same
    resolution order the YuNet face model uses."""
    user = (tmp_path / "selektyt-home" / ".selektyt" / "models"
            / SileroVADBackend._MODEL_NAME)
    user.parent.mkdir(parents=True)
    user.write_bytes(b"not really an onnx model")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "selektyt-home"))
    paths = SileroVADBackend()._candidate_paths()
    assert paths[0] == user


def test_forcing_silero_without_the_model_names_the_real_condition(monkeypatch):
    """The backend is implemented, so "not implemented" would now be a lie.
    What can still fail is the model file or onnxruntime, and the message has to
    say that and name the backends that do work — the same contract the stub's
    message held, applied to the real condition."""
    monkeypatch.setattr(SileroVADBackend, "_resolve_model", lambda self: None)
    with pytest.raises(RuntimeError) as excinfo:
        get_vad_backend(force="silero")
    message = str(excinfo.value)
    assert "not implemented" not in message
    assert "silero_vad_v6.onnx" in message
    assert "energy" in message


# ── backend selection and persistence ──

def test_get_vad_backend_prefers_silero_where_the_model_resolves():
    """Silero is the preferred backend: it scores learned speech probability and
    is level-invariant, where the energy scorer compares RMS against fixed
    absolute thresholds and is blind to a quiet source (see
    ``tests/test_vad_silero.py``). Where the model resolves it is selected;
    where it does not the chain still lands on a backend that works and says
    why it fell back."""
    if SileroVADBackend().is_available():
        assert get_vad_backend().name == "silero"
    else:
        backend = get_vad_backend()
        assert backend.name in ("energy", "ffmpeg", "whisper")
        assert backend.is_available()
        assert backend.fallback_reason is not None
        assert "silero" in backend.fallback_reason


def test_ffmpeg_outranks_energy_in_the_fallback_chain(monkeypatch):
    """The order is measured, not assumed. On all three corpus sources ffmpeg
    beats energy on word recall and on words clipped, and energy fails outright
    on two — its fixed absolute thresholds score a quiet source entirely silent
    (0/331 words on hadhazy-protest, 12/2727 on pentagon-esper-briefing). Energy
    still outranks whisper because it needs nothing but numpy, so a host without
    ffmpeg keeps a working backend; it simply is not the default where ffmpeg
    exists. See ``docs/AI_EXECUTION_STATE.md`` for the table.

    This pins the order so it cannot revert silently: with both available and
    silero out of the picture, ffmpeg is selected and says why."""
    monkeypatch.setattr(SileroVADBackend, "is_available", lambda self: False)
    if not (FFmpegVADBackend().is_available() and EnergyVAD().is_available()):
        pytest.skip("needs ffmpeg and numpy both present to compare the two")

    backend = get_vad_backend()
    assert backend.name == "ffmpeg"
    assert backend.fallback_reason is not None
    assert "silero" in backend.fallback_reason
    assert "energy" not in backend.fallback_reason, \
        "ffmpeg was selected, so the reason must not blame energy"


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
    # Where the vendored model and onnxruntime are present the chain prefers
    # silero and there is no fallback to explain; where either is absent the
    # selected backend must say which preferred backend it is standing in for.
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


# ── windowed read (the reader scores a window at a time so a long source
#    never has to be resident) ──

def _write_signal_wav(path, sig, sr=16000):
    """Write a mono 16-bit PCM WAV from a float signal in [-1, 1]."""
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((np.clip(sig, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
    return path


def _whole_file_scores(vad, path):
    """The whole-file read the windowed reader replaced, reconstructed from the
    same per-frame math. One score per full frame, trailing samples dropped."""
    frame = vad.sample_rate * vad.frame_ms // 1000
    with wave.open(str(path), "rb") as f:
        data = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    n_frames = len(data) // frame
    return vad._score_frames(data[: n_frames * frame].reshape(-1, frame))


def test_windowed_read_scores_across_a_window_boundary_like_a_whole_file(tmp_path):
    """Windowing is a memory change only: every score must be the value the
    whole-file read would have produced. This is the contract the Rust parity
    scorer also relies on, since it reads the same array.

    The signal is seeded noise rather than a tone because a tone scores the same
    in every frame, so a dropped, duplicated or shifted frame at a boundary
    would not show up. Noise differs frame to frame, so any misframing moves a
    score. 2 windows plus a tail crosses the boundary twice.
    """
    vad = EnergyVAD()
    frame = vad.sample_rate * vad.frame_ms // 1000
    window = max(frame * 4, vad.sample_rate * 60)
    rng = np.random.default_rng(0)
    sig = (rng.random(2 * window + 100, dtype=np.float32) - 0.5) * 0.6
    path = _write_signal_wav(tmp_path / "noise.wav", sig)

    result = vad.analyze(path)
    want = _whole_file_scores(vad, path)

    assert len(result.scores) == len(want), \
        f"{len(result.scores)} scores against {len(want)} for a whole-file read"
    assert result.scores == want.tolist(), \
        "windowed and whole-file reads disagree on some frame's score"


def test_windowed_read_drops_a_tail_shorter_than_one_frame(tmp_path):
    """A tail too short for a whole frame is dropped, not rounded up to a score
    that would shift the cadence the segmentation grid assumes."""
    vad = EnergyVAD()
    frame = vad.sample_rate * vad.frame_ms // 1000
    window = max(frame * 4, vad.sample_rate * 60)
    sig = np.zeros(2 * window + 100, dtype=np.float32)
    path = _write_signal_wav(tmp_path / "tail.wav", sig)

    result = vad.analyze(path)
    assert len(result.scores) == 2 * window // frame
    assert result.duration == pytest.approx((2 * window + 100) / vad.sample_rate)


def test_audio_shorter_than_one_frame_still_yields_one_score(tmp_path):
    """Sub-frame input is padded to a single score so the array is never empty
    for the divisions downstream. The whole-file read this replaced crashed
    here — it reshaped an empty buffer — so this is a fix, not a convention."""
    vad = EnergyVAD()
    frame = vad.sample_rate * vad.frame_ms // 1000
    path = _write_signal_wav(tmp_path / "short.wav",
                             np.full(100, 0.5, dtype=np.float32))
    result = vad.analyze(path)
    assert len(result.scores) == 1
    assert result.scores == _whole_file_scores(
        vad, _write_signal_wav(tmp_path / "padded.wav",
                               np.pad(np.full(100, 0.5, dtype=np.float32),
                                      (0, frame - 100)))).tolist()


def test_audio_of_exactly_one_frame_is_one_score(tmp_path):
    vad = EnergyVAD()
    frame = vad.sample_rate * vad.frame_ms // 1000
    rng = np.random.default_rng(1)
    path = _write_signal_wav(tmp_path / "frame.wav",
                             (rng.random(frame, dtype=np.float32) - 0.5) * 0.6)
    assert len(EnergyVAD().analyze(path).scores) == 1


def test_the_carry_across_a_boundary_is_scored_when_a_window_is_not_whole_frames(tmp_path):
    """A window that is not a whole number of frames leaves a partial frame that
    must be carried into the next one, not dropped or double-read.

    At the default 16 kHz this cannot happen: 60 s is exactly 1875 frames, so
    the carry is always empty and the case is unobservable. A non-default
    sample_rate makes the window 2 646 000 samples at 44.1 kHz, which is 496
    samples past a whole frame — so this is the test that actually reaches the
    carry, and the reason the carry exists at all.
    """
    vad = EnergyVAD(sample_rate=44100)
    frame = vad.sample_rate * vad.frame_ms // 1000
    window = max(frame * 4, vad.sample_rate * 60)
    assert window % frame != 0, "this test needs a window that straddles a frame"
    rng = np.random.default_rng(2)
    sig = (rng.random(2 * window + frame // 2, dtype=np.float32) - 0.5) * 0.6
    path = _write_signal_wav(tmp_path / "straddle.wav", sig, sr=44100)

    result = vad.analyze(path)
    want = _whole_file_scores(vad, path)
    assert len(result.scores) == len(want)
    assert result.scores == want.tolist()