"""Test auto-fallback logic for whisper model sizing."""
import io
import sys
import types
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch


def _write_minimal_wav(path: Path) -> None:
    frames = b"\x00\x00" * int(16000 * 0.6)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(frames)


def _install_fake_faster_whisper(model_names):
    mod = types.ModuleType("faster_whisper")

    class Factory:
        def __init__(self, name, **_kw):
            model_names.append(name)
            self.transcribe = self._transcribe

        def _transcribe(self, *args, **kwargs):
            seg = MagicMock()
            seg.start = 0.0
            seg.end = 2.0
            seg.text = "test"
            seg.words = []
            info = MagicMock()
            info.language = "en"
            return ([seg], info)

    mod.WhisperModel = Factory
    sys.modules["faster_whisper"] = mod


def _run_transcribe(tmp_path: Path, duration: float, chunk_minutes: int):
    model_names = []
    _install_fake_faster_whisper(model_names)
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()

    def fake_ffmpeg(cmd, *_a, **_kw):
        target = Path(cmd[-1])
        src = next(Path(a) for a in cmd if str(a).endswith("audio.wav"))
        target.write_bytes(src.read_bytes())

    try:
        with patch("providers.whisper._duration", return_value=duration):
            with patch("providers.whisper._extract_audio") as mock_extract:
                mock_extract.side_effect = lambda media, out: _write_minimal_wav(out)
                with patch("providers.whisper.subprocess.run", side_effect=fake_ffmpeg):
                    with patch("providers.whisper._require"):
                        from providers.whisper import transcribe

                        audio = tmp_path / "fake.wav"
                        audio.write_bytes(b"x")
                        transcribe(audio, model_name="small", chunk_minutes=chunk_minutes)
    finally:
        sys.stdout = old_stdout
        sys.modules.pop("faster_whisper", None)
    return model_names


def test_no_fallback_when_fast(tmp_path):
    """With no slow chunks, the model is never re-created."""
    names = _run_transcribe(tmp_path, duration=180.0, chunk_minutes=1)
    assert names == ["small"], f"Expected only initial model, got: {names}"


def test_no_fallback_on_first_chunk_alone(tmp_path):
    """A slow second chunk must not trigger fallback if threshold not met, and
    the first chunk alone must never decide."""
    names = _run_transcribe(tmp_path, duration=180.0, chunk_minutes=1)
    assert len(names) == 1, f"First chunk must never trigger fallback: {names}"


def test_fallback_after_consecutive_slow_chunks(tmp_path):
    """After N slow non-first chunks, model should fall back to base."""
    from providers.whisper import transcribe  # ensure import path exists
    model_names = []

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    env = dict(__import__("os").environ)
    __import__("os").environ["SELEKYT_FALLBACK_CONSECUTIVE"] = "1"
    __import__("os").environ["SELEKYT_FALLBACK_RT"] = "-1.0"

    def fake_ffmpeg(cmd, *_a, **_kw):
        target = Path(cmd[-1])
        src = next(Path(a) for a in cmd if str(a).endswith("audio.wav"))
        target.write_bytes(src.read_bytes())

    try:
        _install_fake_faster_whisper(model_names)
        with patch("providers.whisper._duration", return_value=180.0):
            with patch("providers.whisper._extract_audio") as mock_extract:
                mock_extract.side_effect = lambda media, out: _write_minimal_wav(out)
                with patch("providers.whisper.subprocess.run", side_effect=fake_ffmpeg):
                    with patch("providers.whisper._require"):
                        audio = tmp_path / "fake.wav"
                        audio.write_bytes(b"x")
                        transcribe(audio, model_name="small", chunk_minutes=1)
    finally:
        sys.stdout = old_stdout
        import os

        for key in ("SELEKYT_FALLBACK_CONSECUTIVE", "SELEKYT_FALLBACK_RT"):
            os.environ.pop(key, None)
        sys.modules.pop("faster_whisper", None)
    assert "base" in model_names, f"Expected fallback to base, got: {model_names}"