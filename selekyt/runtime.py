"""Runtime assets shipped with the production PyPI distribution."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path


def bundled_whisper_model() -> Path | None:
    """Return the bundled Whisper model directory, if present."""
    path = Path(__file__).resolve().parent / "assets" / "whisper" / "tiny"
    return path if (path / "model.bin").is_file() else None


def bundled_mech_path() -> Path | None:
    """Return the platform-specific Rust mechanical-core sidecar."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux" and machine in {"x86_64", "amd64"}:
        path = Path(__file__).resolve().parent / "assets" / "bin" / "linux-x86_64" / "selekyt-mech"
    elif system == "darwin" and machine in {"x86_64", "amd64"}:
        path = Path(__file__).resolve().parent / "assets" / "bin" / "macos-x86_64" / "selekyt-mech"
    elif system == "darwin" and machine in {"arm64", "aarch64"}:
        path = Path(__file__).resolve().parent / "assets" / "bin" / "macos-arm64" / "selekyt-mech"
    elif system == "windows" and machine in {"amd64", "x86_64"}:
        path = Path(__file__).resolve().parent / "assets" / "bin" / "windows-x86_64" / "selekyt-mech.exe"
    else:
        return None
    return path if path.is_file() and os.access(path, os.X_OK) else None


def ping_mech(timeout: float = 5.0) -> dict:
    """Run the bundled Rust sidecar's real JSON-RPC ping."""
    binary = bundled_mech_path()
    if binary is None:
        raise RuntimeError(
            "the SeleKyT Rust mechanical runtime is not bundled for this platform"
        )
    proc = subprocess.run(
        [str(binary)],
        input=json.dumps({"op": "ping"}) + "\n",
        text=True,
        capture_output=True,
        timeout=timeout,
        check=True,
    )
    line = next((line for line in proc.stdout.splitlines() if line.strip()), "")
    response = json.loads(line)
    if response.get("ok") is not True or not response.get("data", {}).get("pong"):
        raise RuntimeError(f"Rust mechanical runtime ping failed: {response!r}")
    return response["data"]
