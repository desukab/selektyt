#!/usr/bin/env python3
"""Assemble the bundled Python runtime for the SelekyT desktop app.

Decision (see docs/desktop-bundling.md): we ship a relocatable
``python-build-standalone`` interpreter plus a pip install of THIS repository
into that interpreter's site-packages, rather than a PyInstaller freeze.

  - the desktop sidecar runs ``python -m selekyt --server`` — literally the
    CLI's intelligence layer, so "the packaged app uses the same engine/API as
    the CLI" holds structurally, with zero duplicated business logic;
  - no hook/hidden-import maintenance for numpy, opencv-python-headless,
    ctranslate2 or sherpa-onnx, which is the recurring cost of a freeze;
  - the bundled tree stays inspectable and debuggable.

The interpreter binary is renamed to the Tauri sidecar name
``selekyt-python-<target-triple>[.exe]`` so the webview can execute exactly
that one program (see apps/desktop/src-tauri/capabilities/main.json), and it
keeps its ``Lib/``-or-``lib/`` tree as a sibling, which is what
python-build-standalone needs to locate its prefix.

Usage (from the repo root):

  python apps/desktop/scripts/build_python.py --triple x86_64-pc-windows-msvc
  python apps/desktop/scripts/build_python.py --triple aarch64-apple-darwin \\
      --hash-only

``--triple x86_64-unknown-linux-gnu`` exists to exercise this whole pipeline
on the Linux development host; it is never shipped.
"""
from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DESKTOP = Path(__file__).resolve().parents[1]
RESOURCES = DESKTOP / "resources"
CACHE = Path.home() / ".cache" / "selekyt-packaging"

PBS_TAG = "20260901"
PY_VERSION = "3.12.14"
# 3.12 (not 3.13/3.14) is the pinned interpreter: every binary dependency has
# published 3.12 wheels for all four targets, and pyproject requires >=3.10.
SIDECAR_NAME = "selekyt-python"

# Recorded with --hash-only from the pinned URLs below.
PINS = {
    "x86_64-pc-windows-msvc": {
        "asset": f"cpython-{PY_VERSION}+{PBS_TAG}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
        "sha256": "7c45c9622400d578709a9b2cddbe8124cc21d382409d9f13406d706d28e31b14",
        "exe": "python.exe",
    },
    "aarch64-apple-darwin": {
        "asset": f"cpython-{PY_VERSION}+{PBS_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz",
        "sha256": "81a359f1cfadd4da11766534c5913791cea55f26e1bb902cacd2a531bb1e4b2b",
        "exe": "bin/python3.12",
    },
    "x86_64-apple-darwin": {
        "asset": f"cpython-{PY_VERSION}+{PBS_TAG}-x86_64-apple-darwin-install_only_stripped.tar.gz",
        "sha256": "65b195c9cedc1fef6767f044f9822069adbd1bd9204d424ece4628776fdc04bb",
        "exe": "bin/python3.12",
    },
    # Development-host verification only; never bundled into a release.
    "x86_64-unknown-linux-gnu": {
        "asset": f"cpython-{PY_VERSION}+{PBS_TAG}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
        "sha256": "72748da13197c1fb161e3afeef20a6a385ff24f2165e6e2758e47008e7faba4c",
        "exe": "bin/python3.12",
    },
}

PBS_URL = (
    f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/"
)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        print(f"downloading {url}")
        tmp = dest.with_suffix(dest.suffix + ".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(dest)
    return dest


def host_triple() -> str:
    m = platform.machine().lower()
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc"
    if sys.platform == "darwin":
        return "arm64-apple-darwin" if m in ("arm64", "aarch64") else "x86_64-apple-darwin"
    return f"{m or 'x86_64'}-unknown-linux-gnu"


def _extract(archive: Path, dest: Path) -> None:
    """Extract, flattening the single top-level ``python/`` directory."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            parts = Path(member.name).parts
            if len(parts) < 2:
                continue  # the top-level "python/" entry itself
            target = dest.joinpath(*parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                if member.mode & 0o111:
                    target.chmod(0o755)


def _verify_pin(triple: str, archive: Path, hash_only: bool) -> str:
    digest = sha256_of(archive)
    recorded = PINS[triple]["sha256"]
    if set(recorded) == {"0"}:
        if hash_only:
            print(f"{triple}: archive sha256 = {digest}")
            return digest
        raise SystemExit(
            f"FATAL: {triple} is still unpinned. Run with --hash-only and record "
            "the digest in this script's PINS table before bundling."
        )
    if digest != recorded:
        raise SystemExit(
            f"FATAL: sha256 mismatch for {triple}\n  recorded: {recorded}\n"
            f"  actual:   {digest}\nRefusing to bundle — upstream changed."
        )
    print(f"sha256 verified: {digest}")
    return digest


def install_project(python: Path, extras: list[str]) -> None:
    """pip-install this repository into the bundled interpreter.

    A real (non-editable) install: the packaged sidecar imports
    ``selekyt.server`` from site-packages, byte-identical to the CLI's code at
    build time. There is no second copy of the engine in the app.
    """
    spec = f".[{','.join(extras)}]" if extras else "."
    cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check",
           "--no-input", spec]
    print("+", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(
            "FATAL: pip install of the project failed inside the bundled "
            f"interpreter:\n{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}"
        )
    print(proc.stdout[-2000:])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--triple", default=host_triple(), choices=sorted(PINS))
    ap.add_argument(
        "--extras",
        default="transcription,llm",
        help="pyproject extras installed into the bundled interpreter",
    )
    ap.add_argument(
        "--no-extras", action="store_true", help="install only the base dependencies"
    )
    ap.add_argument(
        "--hash-only", action="store_true",
        help="download the pinned tarball and print its sha256 (pinning aid)",
    )
    args = ap.parse_args(argv)

    pin = PINS[args.triple]
    url = PBS_URL + pin["asset"]
    archive = download(url, CACHE / pin["asset"])
    digest = _verify_pin(args.triple, archive, args.hash_only)
    if args.hash_only:
        return 0

    runtime = RESOURCES / "python"
    if runtime.exists():
        shutil.rmtree(runtime)
    _extract(archive, runtime)

    interpreter = runtime / pin["exe"]
    if not interpreter.is_file():
        raise SystemExit(
            f"FATAL: interpreter not at resources/python/{pin['exe']} after "
            "extraction — the standalone layout changed, re-pin this script"
        )

    # Probe the interpreter before installing anything: an unusable runtime
    # must fail here, not inside the app bundle.
    probe = subprocess.run([str(interpreter), "-c", "import sys; print(sys.version)"],
                           capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        raise SystemExit(
            f"FATAL: bundled interpreter {interpreter} will not run on this host:\n"
            f"{probe.stderr[-2000:]}"
        )
    print(f"bundled interpreter: {probe.stdout.strip()}")

    install_project(interpreter, [] if args.no_extras else args.extras.split(","))

    # Rename to the Tauri sidecar name. Kept inside resources/python/ so the
    # Lib/lib tree stays adjacent and the interpreter can still resolve its
    # prefix; externalBin in tauri.conf.json points here.
    ext = ".exe" if args.triple.endswith("windows-msvc") else ""
    sidecar = runtime / f"{SIDECAR_NAME}-{args.triple}{ext}"
    shutil.copy2(interpreter, sidecar)
    if sys.platform != "win32":
        sidecar.chmod(0o755)
    print(f"wrote {sidecar.relative_to(DESKTOP)}")

    (runtime / "python.sources.txt").write_text(
        f"# Bundled CPython runtime provenance\n"
        f"source = {url}\n"
        f"python = {PY_VERSION}\n"
        f"build = python-build-standalone tag {PBS_TAG}\n"
        f"archive_sha256 = {digest}\n"
        f"license = PSF-2.0 (CPython), MIT/PNG/etc for bundled wheels\n"
        f"sidecar = {sidecar.name}\n"
    )

    # The deterministic ops the smoke test exercises must be importable.
    check = subprocess.run(
        [str(sidecar), "-c",
         "import selekyt, engine.vad, providers.ffmpeg; print('imports ok')"],
        capture_output=True, text=True, check=False,
    )
    if check.returncode != 0:
        raise SystemExit(
            "FATAL: bundled interpreter cannot import selekyt/engine.vad/"
            f"providers.ffmpeg:\n{check.stderr[-3000:]}"
        )
    print(check.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
