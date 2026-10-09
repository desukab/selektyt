"""FFmpeg error handling tests (mock-based, no real ffmpeg required).

The real-container tests at the end of this file are the exception, and exist
because a mock cannot catch a container ffprobe reads differently: MPEG-PS is
the case that motivated them.
"""
import shutil
import subprocess
from pathlib import Path
import pytest
from providers.ffmpeg import (
    FfmpegError,
    _ensure_bundled_ffmpeg,
    probe_duration,
    run_capture,
)


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
    # ``resolve`` walks three steps: the SELEKYT_FFMPEG override (cleared by the
    # conftest env isolation), ``shutil.which``, then the static-ffmpeg wheel.
    # A test of the terminal error must close both real lookups, otherwise it
    # passes on a machine where static-ffmpeg is installed and fails on one
    # where it is not — the opposite of what a "missing ffmpeg" test is for.
    # Provisioning is neutralised for the same reason: patching ``shutil.which``
    # rewrites the attribute on the shared ``shutil`` module, so
    # ``_ensure_bundled_ffmpeg`` also sees ffmpeg as absent and tries to
    # provision the wheel, whose own outcome depends on whether the platform zip
    # has already been fetched. Letting it run would make this test's message
    # depend on download state rather than on ``resolve``.
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: None)
    monkeypatch.setattr("providers.ffmpeg._static_ffmpeg_bin", lambda _: None)
    monkeypatch.setattr("providers.ffmpeg._ensure_bundled_ffmpeg", lambda: None)
    with pytest.raises(RuntimeError, match="ffmpeg was not found"):
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
    # Both real lookups closed, for the same reason as the ffmpeg case above,
    # and provisioning neutralised with them.
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: None)
    monkeypatch.setattr("providers.ffmpeg._static_ffmpeg_bin", lambda _: None)
    monkeypatch.setattr("providers.ffmpeg._ensure_bundled_ffmpeg", lambda: None)
    with pytest.raises(RuntimeError, match="ffprobe was not found"):
        probe_duration(Path("x.mp4"))


def test_provisioning_chatter_goes_to_stderr_not_stdout(monkeypatch, capsys):
    """``_ensure_bundled_ffmpeg`` runs at import time, so anything it lets
    through to stdout lands in ``selekyt --version`` and in every JSON the CLI
    emits on a host's first run — this was read back as ``'Downloading h'``
    where a subprocess expected a JSON document. ``static_ffmpeg`` writes its
    download and extract progress to *stdout*, so the call has to redirect it
    to stderr, where a user running interactively still sees it.

    ``static_ffmpeg`` is stubbed rather than imported because the source matrix
    in CI runs without the heavyweight wheels, and the contract under test is
    the redirect, not the library: whatever ``add_paths`` prints must not reach
    stdout, whatever it is."""
    import sys as _sys
    import types as _types

    import providers.ffmpeg as ffmpeg_mod

    class _ChattyStaticFFmpeg:
        @staticmethod
        def add_paths(*args, **kwargs):
            # What static_ffmpeg does on a first-run download: progress on
            # stdout, which is exactly the stream a consumer parses.
            _sys.stdout.write("Downloading https://github.com/zackees/ffmpeg_bins/\n")
            _sys.stdout.flush()

    monkeypatch.setattr(ffmpeg_mod, "shutil", _types.SimpleNamespace(which=lambda _: None))
    monkeypatch.setitem(_sys.modules, "static_ffmpeg", _ChattyStaticFFmpeg)

    ffmpeg_mod._ensure_bundled_ffmpeg()

    captured = capsys.readouterr()
    assert captured.out == "", (
        "bundled-ffmpeg provisioning wrote to stdout, which corrupts any "
        "consumer parsing this process's output; progress belongs on stderr"
    )
    assert "Downloading" in captured.err


# The tests above are mock-based, which cannot catch a container the real
# ffprobe reads differently. MPEG-PS is the case that motivated this: yt-dlp
# writes a ``.mpg`` for a source whose best muxed format is already a program
# stream (pinned in test_youtube_download.py), and the download discovery
# hands that file to probes that had only ever been pointed at MP4/MKV. These
# exercise the real binary against the real container.
_HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
_need_ffmpeg = pytest.mark.skipif(
    not _HAVE_FFMPEG, reason="ffmpeg and ffprobe are required for container tests")


@_need_ffmpeg
def test_the_probes_read_a_real_mpeg_ps_mpg(tmp_path):
    """A ``.mpg`` is MPEG-PS, not a variant of MP4: it has no moov atom, its
    timestamps start at a non-zero SCR offset, and its duration comes from the
    system clock rather than a header field. Every probe the pipeline runs on
    a downloaded source must still read it, because the download path can
    produce nothing else for such a source."""
    from providers.ffmpeg import probe_duration, probe_fps, probe_sample_aspect

    mpg = tmp_path / "probe.mpg"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-c:v", "mpeg2video", "-b:v", "200k",
         "-c:a", "mp2", "-b:a", "64k",
         str(mpg)],
        check=True,
    )
    assert mpg.stat().st_size > 1000, "the fixture encode produced nothing"

    # 4 s of content; MPEG-PS duration is derived from the SCR, so drift of a
    # few hundredths is the container, not a probe bug. The bound is
    # deliberately loose: this asserts the probe *reads* the container, and
    # does not reach for a precision the container cannot provide.
    assert 3.5 < probe_duration(mpg) < 4.5
    assert probe_fps(mpg) == 25.0
    assert probe_sample_aspect(mpg) == (1, 1)


@_need_ffmpeg
def test_a_mpeg_ps_mpg_renders_a_window_without_losing_audio(tmp_path):
    """The render seeks with ``-ss`` before ``-i`` on the fast path, and that
    seek has been measured to drop the audio stream outright on some
    containers. MPEG-PS is a likely candidate -- its seeking is timestamp-based
    over a stream with no index -- so this pins that a window cut from a real
    ``.mpg`` still arrives with audio, which is what makes the deliverable
    usable rather than a silent video."""
    from engine.render import _has_audio, _render_one

    mpg = tmp_path / "probe.mpg"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-c:v", "mpeg2video", "-b:v", "200k",
         "-c:a", "mp2", "-b:a", "64k",
         str(mpg)],
        check=True,
    )
    assert _has_audio(mpg), "the fixture must actually carry audio to test this"

    out = tmp_path / "landscape.mp4"
    _render_one(mpg, 1.0, 3.0, out, ass=None, vertical=False, fps=25.0)

    assert out.stat().st_size > 1000
    assert _has_audio(out), (
        "a window cut from an MPEG-PS .mpg lost its audio; the fast-path seek "
        "is dropping the stream on this container"
    )
