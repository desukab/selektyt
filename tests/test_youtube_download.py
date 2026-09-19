"""The URL download path: wire purity and file discovery.

Two real defects surfaced when a URL source was first run through the web
shell, and both are subtle enough to regress silently, so they are pinned here
against a stubbed yt-dlp rather than the network:

1. yt-dlp's progress output landed on fd 1 and corrupted the sidecar's
   one-JSON-object-per-line protocol (the caller saw "sidecar sent a non-JSON
   line"). The sidecar swaps ``sys.stdout``, but an inherited child writes to
   the *real* fd 1 and bypasses that swap.
2. yt-dlp wrote ``.mpg`` for a source whose best muxed format was already
   MPEG-PS, and a fixed ``{mp4, mkv, webm, mov}`` allowlist then rejected a
   download that had succeeded.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import providers.youtube as youtube_mod
from providers.youtube import download


def _stub_ytdlp(monkeypatch, captures: dict, *, stdout_progress: bool, ext: str):
    """Replace yt-dlp with a script that emits progress and writes a dummy file.

    Two yt-dlp invocations happen: a metadata lookup (``--dump-single-json``,
    which captures stdout) and the download itself (which must not write to fd 1).
    The stub distinguishes them so both behave like the real tool.
    """
    def fake_run(cmd, *args, **kw):
        is_metadata = "--dump-single-json" in cmd
        captures["stdout"] = kw.get("stdout", "inherit")
        if is_metadata:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"id": "x"}), stderr="")
        # Emulate the real child: write progress to fd 1 unless the caller
        # redirected it away.
        if stdout_progress and kw.get("stdout") is None:
            sys.stdout.write("[archive.org] progress: 50%\n")
            sys.stdout.flush()
        target = next((a for a in cmd if "%(ext)s" in str(a)), None)
        if target is not None:
            Path(str(target).replace("%(ext)s", ext)).write_bytes(b"0" * 1024)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def _no_cookie_args(monkeypatch):
    monkeypatch.setattr(youtube_mod, "_cookie_args", lambda c: [])
    monkeypatch.setattr(youtube_mod, "_runtime_args", lambda: [])
    monkeypatch.setattr(youtube_mod, "require", lambda name: None)


def test_download_accepts_a_muxed_mpg_source(tmp_path, monkeypatch):
    """``--merge-output-format mp4`` only applies when yt-dlp merges. A source
    whose best muxed format is already MPEG-PS is written as ``.mpg``, and the
    download must still be discovered."""
    caps: dict = {}
    _stub_ytdlp(monkeypatch, caps, stdout_progress=False, ext="mpg")
    _no_cookie_args(monkeypatch)
    monkeypatch.setattr(sys, "stdout", sys.stdout)  # keep the real stream

    media, info = download("https://archive.org/details/x", tmp_path / "src")
    assert media.suffix == ".mpg"
    assert media.is_file()


def test_download_progress_never_reaches_stdout(tmp_path, monkeypatch):
    """The child's progress must be redirected off fd 1, or the sidecar's wire
    is corrupted. A plain ``subprocess.run`` inherits fd 1; the fix points the
    child at fd 2."""
    caps: dict = {}
    _stub_ytdlp(monkeypatch, caps, stdout_progress=True, ext="mp4")
    _no_cookie_args(monkeypatch)

    import io
    real = sys.stdout
    wire = io.StringIO()
    sys.stdout = wire
    try:
        download("https://archive.org/details/x", tmp_path / "src")
    finally:
        sys.stdout = real

    # Anything the child wrote must not be on the stream the sidecar serves.
    assert "progress" not in wire.getvalue(), (
        "yt-dlp progress reached stdout and would corrupt the JSON-RPC wire"
    )
    assert caps["stdout"] == 2, "the child must be pointed at fd 2, not inherited"


def test_download_reports_bot_detection_without_credentials(tmp_path, monkeypatch):
    """The sign-in error must surface as actionable text, not a raw traceback,
    and must not echo credentials."""
    def fake_run(cmd, *args, **kw):
        raise subprocess.CalledProcessError(1, cmd, stderr="Sign in to confirm you're not a bot")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _no_cookie_args(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        download("https://youtube.com/watch?v=x", tmp_path / "src")
    assert "automated traffic" in str(exc.value)
    assert "cookies" in str(exc.value).lower()


def _recording_stub(monkeypatch, results: list, *, metadata_json: dict | None = None):
    """Run each canned result in order and record the client each attempt used."""
    calls: list[str] = []

    def fake_run(cmd, *args, **kw):
        for a, b in zip(cmd, cmd[1:]):
            if a == "--extractor-args" and str(b).startswith("youtube:player_client="):
                calls.append(str(b).split("player_client=")[1])
        is_metadata = "--dump-single-json" in cmd
        outcome = results.pop(0) if results else ("ok", "")
        if outcome[0] == "bot":
            raise subprocess.CalledProcessError(1, cmd, stderr="Sign in to confirm you're not a bot")
        if outcome[0] == "err":
            raise subprocess.CalledProcessError(1, cmd, stderr=outcome[1])
        if is_metadata:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(metadata_json or {"id": "v"}), stderr="")
        target = next((a for a in cmd if "%(ext)s" in str(a)), None)
        if target is not None:
            Path(str(target).replace("%(ext)s", "mp4")).write_bytes(b"0" * 1024)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _no_cookie_args(monkeypatch)
    return calls


def test_download_retries_the_next_player_client_when_one_is_blocked(tmp_path, monkeypatch):
    """The sign-in wall is per-video, not per-client: a blocked client must not
    end the download when another client is still untried."""
    calls = _recording_stub(monkeypatch, [("bot",), ("bot",), ("ok", ""), ("ok", "")])

    media, _info = download("https://youtube.com/watch?v=v", tmp_path / "src")

    assert media.suffix == ".mp4"
    # The lookup had to fall through to the third default client, and the
    # download then started from that client rather than redoing the two
    # blocked attempts.
    assert calls == ["android", "tv_embedded", "web_safari", "web_safari"]


def test_download_does_not_retry_a_real_download_failure(tmp_path, monkeypatch):
    """An error that is not the sign-in wall must be raised immediately, without
    burning the remaining clients."""
    calls = _recording_stub(monkeypatch, [("err", "HTTP Error 403: Forbidden")])

    with pytest.raises(RuntimeError) as exc:
        download("https://youtube.com/watch?v=v", tmp_path / "src")
    assert "403" in str(exc.value)
    assert calls == ["android"]


def test_player_client_list_is_configurable(tmp_path, monkeypatch):
    """A network where the default list is entirely refused can override it."""
    monkeypatch.setenv("SELEKYT_YOUTUBE_PLAYER_CLIENT", "ios,web")
    calls = _recording_stub(monkeypatch, [("ok", ""), ("ok", "")])

    download("https://youtube.com/watch?v=v", tmp_path / "src")

    assert calls == ["ios", "ios"]
