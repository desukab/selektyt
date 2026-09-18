"""Regression tests for transcription checkpoint integrity and ASR provenance.

Covers what the audit flagged as the two ways a long transcription run can
silently lose work or lie about itself:

F1 — a chunk whose decode FAILED must never be checkpointed as though it were
     a successfully transcribed (possibly silent) chunk. A failed chunk has to
     be retried on resume; genuine silence has to stay distinguishable from
     failure, so a quiet passage is still cached and not re-decoded.

F3 — pipeline_state.json must be written atomically and must be recoverable
     when it is truncated or corrupt, without throwing away the download cache.

F5 — provenance must name the model that actually ran, not the one that was
     requested, and must say why it changed.
"""
from __future__ import annotations

import json
import sys
import types
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.core import Segment, Word
from providers.atomic import read_json, write_json_atomic
from providers import transcribe as transcribe_auto


# ── shared fixtures ──

def _write_wav(path: Path, seconds: float = 0.6, rate: int = 16000) -> Path:
    frames = b"\x00\x00" * int(rate * seconds)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(frames)
    return path


def _install_fake_faster_whisper(model_names: list[str]) -> None:
    """Inject a fake faster_whisper module capturing every model constructed."""
    mod = types.ModuleType("faster_whisper")

    def _whisper_model(name, *args, **kwargs):
        model_names.append(name)
        return MagicMock()

    mod.WhisperModel = _whisper_model
    sys.modules["faster_whisper"] = mod


def _stub_ffmpeg(tmp_path: Path):
    """Stand in for ffmpeg: write a valid WAV to whatever path it is given.

    The real code extracts audio.wav once, then slices it per chunk into
    chunk_NNNN.wav via a .wav.tmp that it ``replace()``s. The fake only has to
    leave a real WAV at each requested path so the pipeline's own atomic
    rename works.
    """
    audio_src = _write_wav(tmp_path / "audio.wav", seconds=0.6)

    def fake_run(cmd, *a, **kw):
        if cmd[0] != "ffmpeg":
            return MagicMock(returncode=0)
        target = Path(cmd[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        if "-i" in cmd and any(str(x).endswith("audio.wav") for x in cmd):
            target.write_bytes(audio_src.read_bytes())
        else:
            _write_wav(target, seconds=0.6)
        return MagicMock(returncode=0)

    return fake_run


def _fake_decode(model, wav, offset, language, beam_size):
    """Deterministic stand-in for a whisper decode: one segment per window."""
    return [Segment(offset, offset + 1.0, "hello world", [])], "en"


def _run_whisper_transcribe(tmp_path: Path, *, duration=120.0, chunk_minutes=1):
    """Run providers.whisper.transcribe against mocked ffmpeg + faster-whisper."""
    model_names: list[str] = []
    _install_fake_faster_whisper(model_names)
    stdout, sys.stdout = sys.stdout, __import__("io").StringIO()
    try:
        with patch("providers.whisper._duration", return_value=duration), \
             patch("providers.whisper._extract_audio") as mock_extract, \
             patch("providers.whisper.subprocess.run",
                   side_effect=_stub_ffmpeg(tmp_path)), \
             patch("providers.whisper._require"), \
             patch("providers.whisper._transcribe_chunk", side_effect=_fake_decode):
            mock_extract.side_effect = lambda media, out: _write_wav(out)
            from providers.whisper import transcribe
            segs, lang, prov = transcribe(
                tmp_path / "fake.wav", chunk_minutes=chunk_minutes, cpu_threads=2
            )
    finally:
        sys.stdout = stdout
        sys.modules.pop("faster_whisper", None)
    return segs, lang, prov, model_names


# ── F1: failed chunks are never checkpointed as success ──

def test_failed_chunk_is_not_checkpointed_as_ok(tmp_path):
    """A decode failure must be recorded as status=failed, never as ok+empty.

    The bug: an exception was caught, chunk_segments set to [], and the
    checkpoint written with that empty list under no status. On resume it was
    indistinguishable from a genuinely silent chunk, so the failure hardened
    into the transcript permanently.
    """
    checkpoint_dir = tmp_path / "transcription"
    chunks = checkpoint_dir / "chunks"
    chunks.mkdir(parents=True, exist_ok=True)
    _write_wav(checkpoint_dir / "audio.wav", seconds=0.6)

    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("decoder exploded")

    _install_fake_faster_whisper([])
    with patch("providers.whisper._duration", return_value=60.0), \
         patch("providers.whisper._extract_audio"), \
         patch("providers.whisper.subprocess.run",
               side_effect=_stub_ffmpeg(tmp_path)), \
         patch("providers.whisper._require"), \
         patch("providers.whisper._transcribe_chunk", side_effect=boom):
        from providers.whisper import transcribe
        with pytest.raises(RuntimeError, match="1 of 1 transcription chunks failed"):
            transcribe(tmp_path / "fake.wav", chunk_minutes=1)

    sys.modules.pop("faster_whisper", None)

    record = read_json(chunks / "chunk_0000.json")
    assert record is not None, "the failure must be recorded on disk"
    assert record["status"] == "failed"
    assert "decoder exploded" in record["error"]
    assert not record.get("segments"), \
        "no segments may be claimed for a failed decode"


def test_failed_chunk_retries_on_resume(tmp_path):
    """A failed chunk is re-decoded on the next run, not served as a result."""
    chunks = tmp_path / "transcription" / "chunks"
    chunks.mkdir(parents=True, exist_ok=True)

    attempts = {"n": 0}

    def flaky(model, wav, offset, language, beam_size):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient")
        return [Segment(offset, offset + 1.0, "recovered", [])], "en"

    _install_fake_faster_whisper([])
    with patch("providers.whisper._duration", return_value=60.0), \
         patch("providers.whisper._extract_audio"), \
         patch("providers.whisper.subprocess.run",
               side_effect=_stub_ffmpeg(tmp_path)), \
         patch("providers.whisper._require"), \
         patch("providers.whisper._transcribe_chunk", side_effect=flaky):
        from providers.whisper import transcribe
        with pytest.raises(RuntimeError):
            transcribe(tmp_path / "fake.wav", chunk_minutes=1)
        segs, _lang, _prov = transcribe(tmp_path / "fake.wav", chunk_minutes=1)

    sys.modules.pop("faster_whisper", None)
    assert attempts["n"] == 2, "the failed chunk must be retried exactly once more"
    assert any(s.text == "recovered" for s in segs)
    assert read_json(chunks / "chunk_0000.json")["status"] == "ok"


def test_silence_stays_cached_and_distinguishable_from_failure(tmp_path):
    """A successfully decoded silent chunk is cached as ok with empty segments.

    This is the other half of F1: the fix must not make every empty chunk look
    like a failure, or a genuinely quiet passage is re-decoded forever.
    """
    chunks = tmp_path / "transcription" / "chunks"
    chunks.mkdir(parents=True, exist_ok=True)

    decodes = {"n": 0}

    def silent(model, wav, offset, language, beam_size):
        decodes["n"] += 1
        return [], "en"

    _install_fake_faster_whisper([])
    with patch("providers.whisper._duration", return_value=60.0), \
         patch("providers.whisper._extract_audio"), \
         patch("providers.whisper.subprocess.run",
               side_effect=_stub_ffmpeg(tmp_path)), \
         patch("providers.whisper._require"), \
         patch("providers.whisper._transcribe_chunk", side_effect=silent):
        from providers.whisper import transcribe
        transcribe(tmp_path / "fake.wav", chunk_minutes=1)
        # Second run: the silent chunk must be served from cache.
        transcribe(tmp_path / "fake.wav", chunk_minutes=1)

    sys.modules.pop("faster_whisper", None)
    assert decodes["n"] == 1, "a cached silent chunk must not be re-decoded"
    record = read_json(chunks / "chunk_0000.json")
    assert record["status"] == "ok"
    assert record["segments"] == []


def test_truncated_checkpoint_is_re_decoded(tmp_path):
    """A checkpoint truncated mid-write must not stand in for a result."""
    chunks = tmp_path / "transcription" / "chunks"
    chunks.mkdir(parents=True, exist_ok=True)
    # Not valid JSON at all — what a kill mid-write used to leave behind.
    (chunks / "chunk_0000.json").write_text('{"index": 0, "start": 0.0, "end": 60.',
                                            encoding="utf-8")

    decodes = {"n": 0}

    def once(model, wav, offset, language, beam_size):
        decodes["n"] += 1
        return [Segment(offset, offset + 1.0, "redone", [])], "en"

    _install_fake_faster_whisper([])
    with patch("providers.whisper._duration", return_value=60.0), \
         patch("providers.whisper._extract_audio"), \
         patch("providers.whisper.subprocess.run",
               side_effect=_stub_ffmpeg(tmp_path)), \
         patch("providers.whisper._require"), \
         patch("providers.whisper._transcribe_chunk", side_effect=once):
        from providers.whisper import transcribe
        segs, _lang, _prov = transcribe(tmp_path / "fake.wav", chunk_minutes=1)

    sys.modules.pop("faster_whisper", None)
    assert decodes["n"] == 1
    assert any(s.text == "redone" for s in segs)


# ── F5: provenance names the model that actually ran ──

def test_provenance_reports_requested_and_actual_model(tmp_path):
    """When no downgrade happens, requested == actual and the reason is None."""
    _segs, _lang, prov, names = _run_whisper_transcribe(tmp_path)
    assert prov["requested_model"] == "small"
    assert prov["model"] == "small"
    assert prov["downgrade_reason"] is None
    assert names == ["small"], f"no fallback expected, got {names}"


def test_provenance_reports_downgrade_reason(tmp_path):
    """A slow-chunk downgrade must be visible in provenance, not silent."""
    import os

    env = dict(os.environ)
    os.environ["SELEKYT_FALLBACK_CONSECUTIVE"] = "1"
    os.environ["SELEKYT_FALLBACK_RT"] = "-1.0"  # every chunk is "slow"
    try:
        _segs, _lang, prov, names = _run_whisper_transcribe(
            tmp_path, duration=180.0, chunk_minutes=1
        )
    finally:
        os.environ.clear()
        os.environ.update(env)

    assert names[0] == "small"
    assert len(names) > 1, "expected at least one downgrade, got none"
    assert prov["model"] != "small", \
        f"provenance must name the model that actually ran, not the request: {prov}"
    assert prov["requested_model"] == "small"
    assert prov["downgrade_reason"] is not None
    assert "small" in prov["downgrade_reason"]
    assert prov["model"] in prov["downgrade_reason"]


def test_selector_provenance_is_rich(monkeypatch):
    """providers.transcribe surfaces requested/actual/downgrade to the caller."""
    fake_prov = {
        "requested_model": "small", "model": "base",
        "downgrade_reason": "model not installed",
        "chunking": "28s fixed whisper encoder window",
        "num_threads": 4,
    }
    monkeypatch.setattr(transcribe_auto, "_faster_whisper_available", lambda: False)

    import providers.sherpa as sherpa

    monkeypatch.setattr(sherpa, "is_available", lambda *a, **k: True)
    monkeypatch.setattr(
        sherpa, "transcribe",
        lambda *a, **k: ([Segment(0, 1, "x", [])], "en", fake_prov),
    )
    _segs, _lang, prov = transcribe_auto.transcribe(Path("media"), model_name="small")
    assert prov["requested_model"] == "small"
    assert prov["model"] == "base"
    assert prov["downgrade_reason"] == "model not installed"
    assert prov["fallback_reason"] is not None  # engine fallback is recorded too
    assert prov["word_timestamps"] == "character-proportional"


# ── F3: atomic, recoverable pipeline state ──

def test_write_json_atomic_leaves_no_partial_file(tmp_path):
    """A truncated write must not be visible to a reader."""
    target = tmp_path / "state.json"
    write_json_atomic(target, {"a": 1})
    assert read_json(target) == {"a": 1}
    # The temp file is gone; only the final payload is on disk.
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_read_json_rejects_empty_and_truncated(tmp_path):
    """An empty or truncated file must read as None, never as a valid record.

    A truncated ``""`` parses to an empty string, which is falsy but not an
    error; treating it as a record would reset state silently.
    """
    p = tmp_path / "empty.json"
    p.write_text("", encoding="utf-8")
    assert read_json(p) is None
    p.write_text('{"fingerprint": "abc", "media_path": "/tmp', encoding="utf-8")
    assert read_json(p) is None
    assert read_json(tmp_path / "absent.json") is None


def test_state_helpers_recover_from_corruption(tmp_path):
    """A corrupt pipeline_state.json recovers from its last good backup.

    The backup rotates the previous version, so it is one write behind the
    primary: a crash costs the last stage, not the run. What must survive is
    the expensive, non-reproducible part — the download cache.
    """
    from selekyt.pipeline import _load_state, _save_state, STATE_NAME

    state_path = tmp_path / STATE_NAME
    # Two saves: the first establishes the file, the second rotates the
    # previous good version into the backup. Recovery needs that backup, so a
    # crash after only the very first save has nothing to restore from —
    # which is safe, because an atomic first save is either whole or absent.
    _save_state(state_path, {"source": "url", "media_path": "/tmp/v.mp4",
                             "media_info": {"title": "t"}, "fingerprint": "fp"})
    _save_state(state_path, {"source": "url", "media_path": "/tmp/v.mp4",
                             "media_info": {"title": "t"}, "fingerprint": "fp",
                             "model": "small"})
    # Simulate a kill mid-write.
    state_path.write_text('{"source": "url", "media_path": "/tmp/v', encoding="utf-8")

    recovered = _load_state(state_path)
    assert recovered["media_path"] == "/tmp/v.mp4", \
        "the download cache must survive a corrupt state"
    assert recovered["media_info"] == {"title": "t"}
    assert recovered["fingerprint"] == "fp"
    assert state_path.is_file(), "the primary path must be restored for next time"
    assert (tmp_path / (STATE_NAME + ".corrupt.json")).is_file(), \
        "the corrupt file must be quarantined, not deleted"


def test_state_recovery_preserves_download_cache(tmp_path):
    """The point of recovery: a resumed run must not re-download."""
    from selekyt.pipeline import _load_state, _save_state, STATE_NAME

    media = tmp_path / "v.mp4"
    media.write_bytes(b"x" * 64)
    state_path = tmp_path / STATE_NAME
    for i in range(2):
        _save_state(state_path, {"source": "https://example.com/v",
                                 "media_path": str(media),
                                 "media_info": {"title": "t"},
                                 "stage": i})
    state_path.write_text('{"source": "https://example.com/v"', encoding="utf-8")
    recovered = _load_state(state_path)
    assert recovered["media_path"] == str(media)
    assert recovered["media_info"]["title"] == "t"
    assert recovered["source"] == "https://example.com/v"


def test_state_helpers_start_fresh_when_nothing_recovers(tmp_path):
    """No good state and no backup yields an empty dict, never a raise."""
    from selekyt.pipeline import _load_state, STATE_NAME

    state_path = tmp_path / STATE_NAME
    state_path.write_text('{"broken": ', encoding="utf-8")
    assert _load_state(state_path) == {}
    assert (tmp_path / (STATE_NAME + ".corrupt.json")).is_file()


def test_state_backup_only_rotates_from_valid_state(tmp_path):
    """The backup must always be something the recovery path can trust."""
    from selekyt.pipeline import _load_state, _save_state, STATE_NAME

    state_path = tmp_path / STATE_NAME
    _save_state(state_path, {"v": 1})
    # Corrupt the primary by hand; the save must still not promote garbage.
    state_path.write_text("not json", encoding="utf-8")
    _save_state(state_path, {"v": 2})
    assert _load_state(state_path) == {"v": 2}


