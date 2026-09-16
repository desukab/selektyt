"""FFmpeg error handling tests (mock-based, no real ffmpeg required)."""
import subprocess
from pathlib import Path
import pytest
from providers.ffmpeg import FfmpegError, run_capture, probe_duration


class _FakeErrorProc:
    returncode = 1
    stderr = "Unknown encoder 'nonsense'\nmore detail\n"
    stdout = ""


def _fake_fail(*args, **kwargs):
    return _FakeErrorProc()


def test_ffmpeg_error_message_fields():
    err = FfmpegError(
        stage="render",
        command=["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"],
        stderr_tail="Error: bad thing",
        source="in.mp4",
        target="out.mp4",
    )
    assert err.stage == "render"
    assert err.source == "in.mp4"
    assert err.target == "out.mp4"
    assert "in.mp4" in str(err)
    assert "out.mp4" in str(err)
    assert "ffmpeg" in str(err)
    msg = str(err)
    assert "bad thing" in msg


def test_ffmpeg_error_shows_stderr_tail(monkeypatch):
    tail = "\n".join(f"line {i}" for i in range(20))
    err = FfmpegError(stage="probe", command=["x"], stderr_tail=tail)
    msg = str(err)
    assert "line 19" in msg  # last line of tail
    assert "line 0" not in msg  # first line truncated (only last 15)


def test_run_capture_raises_on_failure(monkeypatch):
    monkeypatch.setattr("providers.ffmpeg.subprocess.run", _fake_fail)
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: "/bin/ffmpeg")
    with pytest.raises(FfmpegError) as exc_info:
        run_capture(
            ["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"],
            stage="render",
            source="in.mp4",
            target="out.mp4",
        )
    assert exc_info.value.stage == "render"
    assert "Unknown encoder" in str(exc_info.value)


class _FakeOkProc:
    returncode = 0
    stderr = ""
    stdout = "42.5"


def test_run_capture_success(monkeypatch):
    monkeypatch.setattr("providers.ffmpeg.subprocess.run", lambda *a, **k: _FakeOkProc())
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: "/bin/ffmpeg")
    proc = run_capture(["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"], stage="render")
    assert proc.returncode == 0


def test_run_capture_missing_ffmpeg(monkeypatch):
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="ffmpeg is required"):
        run_capture(["ffmpeg"], stage="render")


def test_probe_duration_success(monkeypatch):
    monkeypatch.setattr("providers.ffmpeg.subprocess.run", lambda *a, **k: _FakeOkProc())
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: "/bin/ffprobe")
    assert probe_duration(Path("x.mp4")) == 42.5


class _FakeEmptyProc:
    returncode = 0
    stderr = "warning"
    stdout = ""


def test_probe_duration_empty_stdout_raises(monkeypatch):
    monkeypatch.setattr("providers.ffmpeg.subprocess.run", lambda *a, **k: _FakeEmptyProc())
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: "/bin/ffprobe")
    with pytest.raises(FfmpegError) as exc_info:
        probe_duration(Path("x.mp4"))
    assert exc_info.value.stage == "probe-duration"


def test_probe_duration_missing_ffprobe(monkeypatch):
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="ffprobe is required"):
        probe_duration(Path("x.mp4"))