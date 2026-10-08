#!/usr/bin/env python3
"""Prepare all non-Python assets for a production SeleKyT wheel.

This script is intentionally release-only: CI/build tooling invokes it before
building the wheel. End users never run it and never need Rust, Cargo, or a
model download tool.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHISPER_DIR = ROOT / "selekyt" / "assets" / "whisper" / "tiny"
BIN_DIR = ROOT / "selekyt" / "assets" / "bin" / "linux-x86_64"
MODEL_REPO = "Systran/faster-whisper-tiny"
MODEL_REVISION = "d90ca5fe260221311c53c58e660288d3deb8d356"


def prepare_whisper() -> None:
    from huggingface_hub import snapshot_download

    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        MODEL_REPO,
        revision=MODEL_REVISION,
        local_dir=str(WHISPER_DIR),
        allow_patterns=[
            "config.json",
            "model.bin",
            "tokenizer.json",
            "vocabulary.*",
        ],
    )
    required = ["config.json", "model.bin", "tokenizer.json"]
    missing = [name for name in required if not (WHISPER_DIR / name).is_file()]
    if missing:
        raise RuntimeError(f"bundled Whisper model is incomplete: {missing}")
    # The model repository is MIT licensed. Keep the upstream notice beside the
    # weights so the wheel is redistributable with its provenance intact.
    license_path = WHISPER_DIR / "LICENSE"
    if not license_path.exists():
        license_path.write_text(
            "The bundled faster-whisper-tiny model is distributed under the "
            "MIT License by SYSTRAN. Source: https://huggingface.co/"
            "Systran/faster-whisper-tiny\n",
            encoding="utf-8",
        )


def prepare_mech() -> None:
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeError("the production PyPI wheel currently targets Linux x86_64")
    cargo = shutil.which("cargo")
    if cargo is None:
        raise RuntimeError("cargo is required only on the release builder")
    subprocess.run(
        [cargo, "build", "--release", "--locked",
         "--target", "x86_64-unknown-linux-musl"],
        cwd=ROOT,
        check=True,
    )
    binary = ROOT / "target" / "x86_64-unknown-linux-musl" / "release" / "selekyt-mech"
    if not binary.is_file():
        raise RuntimeError(f"Rust release binary was not produced: {binary}")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    dest = BIN_DIR / "selekyt-mech"
    shutil.copy2(binary, dest)
    dest.chmod(0o755)
    # The binary must be self-contained; a dynamically linked libc would make
    # the platform wheel falsely portable.
    subprocess.run(["ldd", str(dest)], check=False)
    probe = subprocess.run(
        [str(dest)],
        input='{"op":"ping"}\n',
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    if '"pong":true' not in probe.stdout.replace(" ", ""):
        raise RuntimeError(f"Rust mechanical runtime ping failed: {probe.stdout!r}")


def main() -> None:
    prepare_whisper()
    prepare_mech()
    print("production release assets prepared")


if __name__ == "__main__":
    main()
