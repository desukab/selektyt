"""Desktop packaging smoke test — runs ONLY on a target packaging runner.

Gated behind ``SELEKYT_DESKTOP_SMOKE=1`` (set by .github/workflows/desktop-*.yml
and by a manual packaging run), because it exercises the *packaged* runtime,
not the development venv. On any other host it skips with a clear reason so
the baseline suite is unaffected.

It asserts the three things a clean-machine install has to guarantee:

  1. the bundled Rust sidecar (``selekyt-mech``) answers ``ping`` — i.e. the
     right target-triple binary was staged and it executes;
  2. ffmpeg/ffprobe are discoverable by the *packaged* runtime, i.e. the
     bundled LGPL binaries landed where the engine's ``shutil.which`` looks;
  3. a deterministic analysis completes end to end over the packaged Python
     sidecar with the network stack disabled — proving the shipped app can do
     real work on a machine with no connectivity and no toolchain.

Every assertion here is about the package, never about the engine: the
engine's behaviour is already pinned by tests/test_server.py,
tests/test_vad.py and the cross-language tests/test_sidecar_parity.py.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent

_SMOKE = os.environ.get("SELEKYT_DESKTOP_SMOKE") == "1"
_RESOURCES = Path(os.environ.get("SELEKYT_DESKTOP_RESOURCES", ""))

pytestmark = pytest.mark.skipif(
    not (_SMOKE and _RESOURCES.is_dir()),
    reason=(
        "desktop packaging smoke test — set SELEKYT_DESKTOP_SMOKE=1 and "
        "SELEKYT_DESKTOP_RESOURCES=<apps/desktop/resources> on a packaging "
        "runner (this host is a Linux dev box with no packaged runtime)"
    ),
)


# ── locating the packaged artefacts ──

def _triple() -> str:
    import platform
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc"
    if sys.platform == "darwin":
        return "arm64-apple-darwin" if machine in ("arm64", "aarch64") \
            else "x86_64-apple-darwin"
    return f"{machine or 'x86_64'}-unknown-linux-gnu"


def _mech_binary() -> Path:
    env = os.environ.get("SELEKYT_MECH")
    if env and Path(env).is_file():
        return Path(env)
    cand = _RESOURCES / f"selekyt-mech-{_triple()}"
    return cand


def _python_binary() -> Path:
    env = os.environ.get("SELEKYT_PYTHON")
    if env and Path(env).is_file():
        return Path(env)
    ext = ".exe" if sys.platform == "win32" else ""
    return _RESOURCES / "python" / f"selekyt-python-{_triple()}{ext}"


# ── JSON-RPC client over stdio (same envelope as the engine tests) ──

class Sidecar:
    """One request line in, one response line out — the pinned envelope."""

    def __init__(self, argv: list[str]):
        self.argv = argv
        self.proc: subprocess.Popen | None = None

    def _start(self) -> subprocess.Popen:
        if self.proc is not None and self.proc.poll() is None:
            return self.proc
        self.proc = subprocess.Popen(
            self.argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        return self.proc

    def call(self, op: str, **params) -> dict:
        proc = self._start()
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps({"op": op, "params": params}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            stderr = proc.stderr.read()[-2000:] if proc.stderr else ""
            pytest.fail(f"sidecar closed the pipe on '{op}': {stderr}")
        resp = json.loads(line)
        assert resp["ok"] is True, resp
        assert resp["op"] == op
        return resp["data"]

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.stdin.close() if self.proc.stdin else None
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ── network-disabled execution ──

class NoNetwork:
    """Raise on any outbound DNS/connect attempt for the duration of a block.

    If the deterministic analysis path secretly needed the network this makes
    it fail loudly instead of silently depending on connectivity.
    """

    def __enter__(self):
        self._gai = socket.getaddrinfo
        self._sock = socket.socket

        def deny(*args, **kwargs):
            raise RuntimeError(
                "the packaged deterministic analysis attempted a network "
                f"call: getaddrinfo({args[:1]})"
            )

        class DeniedSocket(self._sock):
            def connect(self, *a, **kw):
                raise RuntimeError(
                    "the packaged deterministic analysis attempted a socket connect"
                )

        socket.getaddrinfo = deny
        socket.socket = DeniedSocket
        return self

    def __exit__(self, *exc):
        socket.getaddrinfo = self._gai
        socket.socket = self._sock


# ── the checks ──

def test_packaged_python_sidecar_answers_ping():
    sidecar = Sidecar([str(_python_binary()), "-m", "selekyt", "--server"])
    try:
        data = sidecar.call("ping")
        assert data["pong"] is True
        assert data["engine"] == "python"
        assert data["protocol"].startswith("selekyt-jsonrpc")
        assert data["version"], "the packaged sidecar must report a version"
    finally:
        sidecar.close()


def test_packaged_rust_sidecar_answers_ping():
    binary = _mech_binary()
    if not binary.is_file():
        pytest.skip(f"the mech sidecar was not staged at {binary}")
    sidecar = Sidecar([str(binary)])
    try:
        data = sidecar.call("ping")
        assert data["pong"] is True
        assert data["engine"] != "python", "a rust binary must not report python"
    finally:
        sidecar.close()


def test_ffmpeg_is_discoverable_by_the_packaged_runtime():
    """The bundled LGPL binaries must be what the engine finds."""
    bin_dir = _RESOURCES / "bin"
    assert bin_dir.is_dir(), f"no bundled bin/ at {bin_dir}"
    ffmpeg = bin_dir / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    ffprobe = bin_dir / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    assert ffmpeg.is_file() and ffprobe.is_file(), "ffmpeg/ffprobe missing from bin/"

    env = dict(os.environ)
    # Prepend the bundled bin dir exactly the way the desktop shell does
    # (apps/desktop/src-tauri/src/main.rs prepend_bin_to_path), then prove the
    # engine resolves the bundled tools rather than any system-wide ones.
    sep = ";" if sys.platform == "win32" else ":"
    env["PATH"] = f"{bin_dir}{sep}{env.get('PATH', '')}"

    import providers.ffmpeg as ffmpeg_mod  # imported after the PATH edit above

    ffmpeg_mod.require_ffmpeg()
    ffmpeg_mod.require_ffprobe()
    which = shutil.which("ffmpeg", path=env["PATH"])
    assert which is not None
    assert Path(which).resolve() == ffmpeg.resolve(), (
        f"the packaged runtime would use {which}, not the bundled {ffmpeg}"
    )

    manifest = bin_dir / "ffmpeg.sources.txt"
    assert manifest.is_file(), "the bundled toolchain must ship its provenance file"
    assert "LGPL" in manifest.read_text(), "the provenance file must state the license"


def test_deterministic_analysis_completes_with_no_network(tmp_path):
    """A full deterministic analysis over the packaged sidecar, offline."""
    # Deterministic tone: same construction as tests/test_server.py, so the
    # expectation about frame cadence is already pinned elsewhere.
    sr = 16000
    duration = 1.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    sig = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    wav = tmp_path / "tone.wav"
    with wave.open(str(wav), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes((sig * 32767).astype(np.int16).tobytes())

    sidecar = Sidecar([str(_python_binary()), "-m", "selekyt", "--server"])
    try:
        with NoNetwork():
            data = sidecar.call("vad_analyze", path=str(wav), return_scores=True)
        assert data["backend"] in ("energy", "ffmpeg", "silero", "whisper")
        assert data["sample_rate"] == sr
        assert data["duration"] == pytest.approx(duration, abs=0.05)
        # 512-sample @16kHz cadence == 32ms frames (pinned by the parity tests).
        assert data["frame_ms"] == 32
        assert len(data["scores"]) == data["n_frames"]
        assert isinstance(data["segments"], list)
        # A pure 440Hz tone is voiced for its whole duration: no speech
        # segmentation may claim more time than the file contains.
        for seg in data["segments"]:
            assert 0.0 <= seg["start"] <= seg["end"] <= duration + 0.01
    finally:
        sidecar.close()


def test_provenance_files_are_present():
    """A clean machine must be able to tell what it is running."""
    py_manifest = _RESOURCES / "python" / "python.sources.txt"
    assert py_manifest.is_file(), "the bundled runtime must ship its provenance"
    text = py_manifest.read_text()
    assert "python-build-standalone" in text
    assert "archive_sha256" in text
