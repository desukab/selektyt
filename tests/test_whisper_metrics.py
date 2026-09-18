"""Test that transcribe reports timing metrics."""
import io
import subprocess
import sys
import types
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch


def _fake_faster_whisper(mock_model):
    """Inject a fake faster_whisper module so imports succeed (not installed here)."""
    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = lambda *a, **kw: mock_model
    sys.modules["faster_whisper"] = mod


def _write_minimal_wav(path: Path) -> None:
    """Write a tiny valid 16 kHz mono PCM WAV (larger than the 10 KB guard)."""
    frames = b"\x00\x00" * int(16000 * 0.6)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(frames)


def _run_transcribe_with_mocks(tmp_path: Path, duration: float, chunk_minutes: float):
    """Run transcribe() with mocked faster-whisper, capturing stdout."""
    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.start = 0.0
    mock_segment.end = 2.0
    mock_segment.text = "hello world"
    mock_segment.words = []
    mock_info = MagicMock()
    mock_info.language = "en"
    mock_model.transcribe.return_value = ([mock_segment], mock_info)

    _fake_faster_whisper(mock_model)
    old_stdout = sys.stdout
    buffer = io.StringIO()
    sys.stdout = buffer


    def fake_ffmpeg(cmd, *_a, **_kw):
        """Stub ffmpeg chunk extraction by copying a valid wav to the target."""
        if cmd[0] != "ffmpeg":
            return
        target = Path(cmd[-1])
        src = next(Path(a) for a in cmd if str(a).endswith("audio.wav"))
        Path(src).parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(src).read_bytes())

    try:
        with patch("providers.whisper._duration", return_value=duration):
            with patch("providers.whisper._extract_audio") as mock_extract:
                mock_extract.side_effect = lambda media, out: _write_minimal_wav(out)
                with patch("providers.whisper.subprocess.run", side_effect=fake_ffmpeg):
                    with patch("providers.whisper._require"):
                        from providers.whisper import transcribe

                        audio = tmp_path / "fake.wav"
                        audio.write_bytes(b"x")
                        segs, lang, provenance = transcribe(
                            audio, chunk_minutes=chunk_minutes, cpu_threads=2
                        )
    finally:
        sys.stdout = old_stdout
        sys.modules.pop("faster_whisper", None)
    return segs, lang, buffer.getvalue()


def test_transcribe_reports_chunk_timing(tmp_path):
    """Verify chunk timing metrics are printed."""
    segs, lang, output = _run_transcribe_with_mocks(tmp_path, duration=120.0, chunk_minutes=60)
    assert "wall" in output, f"Missing wall-clock time in: {output}"
    assert "RT" in output, f"Missing realtime factor in: {output}"
    assert "threads" in output, f"Missing cpu threads in: {output}"


def test_transcribe_reports_eta_after_three_chunks(tmp_path):
    """Verify empirical ETA appears once enough chunks have completed."""
    segs, lang, output = _run_transcribe_with_mocks(tmp_path, duration=180.0, chunk_minutes=1)
    assert "ETA" in output, f"Missing ETA in: {output}"


def test_transcribe_warns_on_stalled_chunk(tmp_path):
    """Verify a warning appears when a chunk exceeds the timeout."""

    def fake_ffmpeg(cmd, *_a, **_kw):
        target = Path(cmd[-1])
        src = next(Path(a) for a in cmd if str(a).endswith("audio.wav"))
        target.write_bytes(src.read_bytes())

    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.start = 0.0
    mock_segment.end = 2.0
    mock_segment.text = "hello world"
    mock_segment.words = []
    mock_info = MagicMock()
    mock_info.language = "en"
    mock_model.transcribe.return_value = ([mock_segment], mock_info)
    _fake_faster_whisper(mock_model)

    old_stdout = sys.stdout
    buffer = io.StringIO()
    sys.stdout = buffer
    try:
        with patch("providers.whisper._duration", return_value=120.0):
            with patch("providers.whisper._extract_audio") as mock_extract:
                mock_extract.side_effect = lambda media, out: _write_minimal_wav(out)
                with patch("providers.whisper.subprocess.run", side_effect=fake_ffmpeg):
                    with patch("providers.whisper._require"):
                        with patch("providers.whisper.time.time", side_effect=[0.0, 1300.0]):
                            with patch.dict("os.environ", {"SELEKYT_CHUNK_TIMEOUT_MIN": "20"}):
                                from providers.whisper import transcribe

                                audio = tmp_path / "fake.wav"
                                audio.write_bytes(b"x")
                                transcribe(audio, chunk_minutes=60, cpu_threads=2)
    finally:
        sys.stdout = old_stdout
        sys.modules.pop("faster_whisper", None)
    output = buffer.getvalue()
    assert "WARNING" in output, f"Missing stall warning in: {output}"