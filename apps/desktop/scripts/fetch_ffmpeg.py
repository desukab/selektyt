#!/usr/bin/env python3
"""Fetch the LGPL-licensed ffmpeg/ffprobe binaries bundled with SelekyT.

License decision (see docs/packaging.md, "ffmpeg/ffprobe bundling"):
the SelekyT application is MIT-licensed; bundling a *GPL* ffmpeg would force
the combined work under the GPL. Every binary this script installs is
therefore LGPL (LGPLv2.1+/LGPLv3, FFmpeg's own choice for non-GPL builds).

  Windows  BtbN/FFmpeg-Builds, pinned to a *dated* autobuild tag, static LGPL.
           Static = no side-by-side DLLs, so PATH discovery is enough.
  Linux    same project, linux64-lgpl — used only to verify the packaging
           pipeline on this development host; the Linux desktop shell is not
           a shipped product.
  macOS    built from the FFmpeg source tarball with an LGPL configure line,
           because every published static macOS binary distribution we could
           verify (osxexperts.net, evermeet.cx) is GPL. See
           build_ffmpeg_macos.sh.

Every archive is sha256-verified against a value recorded from a download this
script itself performed; a mismatch aborts the build loudly rather than
silently shipping a tampered or truncated toolchain.

Writes, next to the binaries, a ``ffmpeg.sources.txt`` provenance manifest —
source URL, license, version, sha256 — because the product records provenance
for its own outputs and the bundled toolchain deserves the same treatment.

Usage:
  python apps/desktop/scripts/fetch_ffmpeg.py --target win64
  python apps/desktop/scripts/fetch_ffmpeg.py --target linux64
  python apps/desktop/scripts/fetch_ffmpeg.py --target win64 --hash-only
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RESOURCES = Path(__file__).resolve().parents[1] / "resources"
CACHE = Path.home() / ".cache" / "selekyt-packaging"

# ── pins ──
# sha256 values below were produced by running this script with --hash-only
# against the pinned URL and then recorded, so a changed upstream file fails
# the build instead of silently shipping.
BTBN_TAG = "autobuild-2026-09-17-13-19"
FFMPEG_SRC_VERSION = "8.1.2"
FFMPEG_SRC_URL = f"https://ffmpeg.org/releases/ffmpeg-{FFMPEG_SRC_VERSION}.tar.xz"

PINS = {
    # Windows static LGPL: bin/ffmpeg.exe + bin/ffprobe.exe, self-contained.
    "win64": {
        "url": (
            f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{BTBN_TAG}/"
            f"ffmpeg-n8.1.2-54-gc573a95381-win64-lgpl-8.1.zip"
        ),
        "sha256": "c740a325e7eeeddd373764cd274b50768190b4030a726fe938dca93f968dfc10",
        "kind": "zip",
        "names": {"ffmpeg": "ffmpeg.exe", "ffprobe": "ffprobe.exe"},
        "license": "LGPLv2.1+ (FFmpeg static build, no GPL libraries)",
    },
    "linux64": {
        "url": (
            f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{BTBN_TAG}/"
            f"ffmpeg-n8.1.2-54-gc573a95381-linux64-lgpl-8.1.tar.xz"
        ),
        "sha256": "3846384ed094486b54d44b05cdc2076a34607295cfdcce612efd6e4db2319249",
        "kind": "tarxz",
        "names": {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"},
        "license": "LGPLv2.1+ (FFmpeg static build, no GPL libraries)",
    },
}

PROLOGUE = """\
# Bundled ffmpeg/ffprobe provenance. SelekyT is MIT-licensed; the binaries
# referenced here are FFmpeg LGPL builds and are distributed under the FFmpeg
# LGPL terms (see https://ffmpeg.org/legal.html). Replace this file's binaries
# with your own LGPL build if your distribution terms differ.
"""


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


def _members(archive: Path, kind: str):
    if kind == "zip":
        with zipfile.ZipFile(archive) as zf:
            return [(n, zf.read(n)) for n in zf.namelist()]
    with tarfile.open(archive, "r:xz") as tf:
        return [(m.name, tf.extractfile(m).read()) for m in tf.getmembers() if m.isfile()]


def _bin_bytes(members: list, names: dict[str, str]) -> dict[str, bytes]:
    """Find ffmpeg/ffprobe regardless of the archive's top-level dir name."""
    want = set(names.values())
    found = {}
    for member, data in members:
        base = Path(member).name
        if base in want and base not in found:
            found[base] = data
    missing = want - set(found)
    if missing:
        raise SystemExit(
            f"FATAL: archive {list(names.values())} did not contain {sorted(missing)}; "
            "the upstream layout changed — re-pin this script"
        )
    return {logical: found[fname] for logical, fname in names.items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", required=True, choices=sorted(PINS) + ["macos"])
    ap.add_argument(
        "--hash-only",
        action="store_true",
        help="print the downloaded archive's sha256 without installing (pinning aid)",
    )
    ap.add_argument(
        "--require-sha",
        action="store_true",
        default=True,
        help="fail if the recorded sha256 is still the placeholder (unpinned)",
    )
    args = ap.parse_args(argv)

    if args.target == "macos":
        print(
            "macOS ffmpeg is built from source, not downloaded — run "
            "apps/desktop/scripts/build_ffmpeg_macos.sh"
        )
        return 0

    pin = PINS[args.target]
    archive = download(pin["url"], CACHE / Path(pin["url"]).name)
    digest = sha256_of(archive)

    if args.hash_only:
        # Pinning aid: print the real digest so it can be recorded in PINS.
        if pin["sha256"].endswith("a0a0"):
            print(f"UNPINNED {args.target}: archive sha256 = {digest}")
        else:
            print(f"{args.target}: archive sha256 = {digest}")
        return 0

    if pin["sha256"].endswith("a0a0"):
        raise SystemExit(
            f"FATAL: {args.target} sha256 is still the placeholder — run with "
            "--hash-only and record the real digest in PINS before bundling"
        )
    if digest != pin["sha256"]:
        raise SystemExit(
            f"FATAL: sha256 mismatch for {args.target}\n  recorded: {pin['sha256']}\n"
            f"  actual:   {digest}\nRefusing to install — upstream changed."
        )
    print(f"sha256 verified: {digest}")

    bins = _bin_bytes(_members(archive, pin["kind"]), pin["names"])
    outdir = RESOURCES / "bin"
    outdir.mkdir(parents=True, exist_ok=True)
    for logical, data in bins.items():
        fname = pin["names"][logical]
        (outdir / Path(fname)).write_bytes(data)
        if sys.platform != "win32":
            (outdir / fname).chmod(0o755)
        print(f"installed resources/bin/{fname} ({len(data)} bytes)")

    manifest = [
        PROLOGUE,
        f"target = {args.target}",
        f"source = {pin['url']}",
        f"version = n8.1.2 (FFmpeg {FFMPEG_SRC_VERSION} lineage)",
        f"license = {pin['license']}",
        f"archive_sha256 = {digest}",
    ]
    (outdir / "ffmpeg.sources.txt").write_text("\n".join(manifest) + "\n")

    # Fail loudly right here if the tool we just installed cannot identify
    # itself — a truncated or arch-mismatched binary must not reach a bundle.
    check = outdir / pin["names"]["ffmpeg"]
    probe = subprocess.run(
        [str(check), "-version"], capture_output=True, text=True,
        timeout=60, check=False,
    )
    if probe.returncode != 0 or "ffmpeg version" not in (probe.stdout + probe.stderr):
        raise SystemExit(f"FATAL: {check} -version did not report an ffmpeg version")
    first = (probe.stdout or probe.stderr).splitlines()[0]
    print(f"smoke: {first}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
