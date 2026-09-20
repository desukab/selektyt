#!/usr/bin/env bash
# apps/desktop/stage-windows.sh
#
# Stages everything `npx tauri build --bundles nsis,msi` needs to produce a
# real Windows x64 installer, into the exact places
# apps/desktop/src-tauri/tauri.conf.json declares:
#
#   bundle.externalBin  binaries/selekyt-mech       -> binaries/selekyt-mech-x86_64-pc-windows-msvc.exe
#   bundle.externalBin  binaries/selekyt-python     -> binaries/selekyt-python-x86_64-pc-windows-msvc.exe
#   bundle.resources    resources/python            -> python/   python-build-standalone 3.12 + selekyt
#   bundle.resources    resources/bin               -> bin/      ffmpeg.exe, ffprobe.exe
#   bundle.resources    resources/models            -> models/   faster-whisper weights (HF cache layout)
#   bundle.resources    resources/share             -> share/    kept present; the mapping must resolve
#
# The two `externalBin` entries land next to `SelekyT.exe` at the install root
# while the four resource trees land in subdirectories of it, so the python
# sidecar is a small launcher (built here) that forwards argv to
# `python/python.exe`: a python-build-standalone interpreter resolves its
# standard library relative to its own executable, so the tree has to ship
# intact and cannot be copied to the install root on its own.
#
# Run from bash on windows-latest (what .github/workflows/build-windows.yml
# does) or locally in Git Bash to preview what a build would bundle. It is
# idempotent — nothing already proven staged is re-downloaded — and exits non
# zero on any missing piece.
#
# Environment overrides:
#   PBS_TAG    pin a python-build-standalone release tag (default: resolve "latest")
#   GH_TOKEN   lifts the GitHub API rate limit while resolving that tag
#   SELEKYT_SKIP_MODEL=1   skip the ~500 MB whisper download (build smoke runs)

set -euo pipefail

# ── configuration ────────────────────────────────────────────────────────────
TRIPLE="x86_64-pc-windows-msvc"
PY_MINOR="3.12"
WHISPER_REPO="Systran/faster-whisper-small"
WHISPER_CACHE_DIR="models--${WHISPER_REPO//\//--}"
FFMPEG_URL="https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
PBS_TAG="${PBS_TAG:-}"
GH_TOKEN="${GH_TOKEN:-}"
SKIP_MODEL="${SELEKYT_SKIP_MODEL:-0}"

# ── paths ────────────────────────────────────────────────────────────────────
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"              # apps/desktop
SRC_TAURI="$HERE/src-tauri"
RES="$SRC_TAURI/resources"
BIN="$SRC_TAURI/binaries"
REPO="$(cd "$HERE/../.." && pwd)"                                 # selekyt root
WORK="$SRC_TAURI/.stage-downloads"

# Paths handed to a Windows-native program (python.exe, cargo) must be Windows
# paths: bash on the runner is MSYS, so $PWD is /d/a/selektyt/selektyt, which a
# Windows binary reads as a path relative to the current drive.
WIN() { cygpath -w "$1" 2>/dev/null || printf '%s\n' "$1"; }

log()  { printf '[stage] %s\n' "$*"; }
die()  { printf '[stage] FATAL: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "required tool '$1' is not on PATH"; }

download() {  # download <destination> <url>
  local out="$1" url="$2"
  [[ -s "$out" ]] && { log "reusing $(basename "$out")"; return 0; }
  log "downloading $url"
  curl -fSL --retry 3 --retry-delay 5 -o "$out" "$url" \
    || die "download failed: $url"
}

# ── preflight ────────────────────────────────────────────────────────────────
need cargo
need curl
HOST_PY="$(command -v python3 || command -v python || true)"
[[ -n "$HOST_PY" ]] || die "no python3/python on PATH (used to unpack archives and query the GitHub API)"
need cygpath
# Invoked by bare name from here on: MSYS rewrites the posix path command -v
# returns inconsistently, and a bare name resolves through PATH on Windows.
HOST_PY="${HOST_PY##*/}"
log "repo:            $REPO"
log "target triple:   $TRIPLE"
log "host python:     $HOST_PY"
log "cargo:           $(command -v cargo)"

mkdir -p "$WORK" "$BIN" "$RES/python" "$RES/bin" "$RES/models" "$RES/share"

# ── 1. the rust mechanical core ──────────────────────────────────────────────
stage_mech() {
  log "building selekyt-mech (cargo build --release, rust workspace)"
  ( cd "$REPO/rust" && cargo build --release --locked ) \
    || die "cargo build --release failed in $REPO/rust"
  local built="$REPO/rust/target/release/selekyt-mech.exe"
  [[ -s "$built" ]] || die "cargo produced no $built"
  cp -f "$built" "$BIN/selekyt-mech-$TRIPLE.exe"
  log "selekyt-mech staged ($(du -h "$BIN/selekyt-mech-$TRIPLE.exe" | cut -f1))"
}

# ── 2. the bundled interpreter ───────────────────────────────────────────────
resolve_python_asset() {  # echoes the asset name for the resolved release tag
  local tag="$1"
  "$HOST_PY" - "$tag" "$PY_MINOR" "$GH_TOKEN" <<'PY'
import json
import re
import sys
import urllib.request

tag, minor, token = sys.argv[1], sys.argv[2], sys.argv[3]
url = f"https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{tag}"
request = urllib.request.Request(url)
request.add_header("User-Agent", "selektyt-stage-windows")
request.add_header("Accept", "application/vnd.github+json")
if token:
    request.add_header("Authorization", f"Bearer {token}")
with urllib.request.urlopen(request, timeout=60) as response:
    release = json.load(response)
# Prefer the PDB-stripped build: the debug symbols are useless in a shipped
# app and it is roughly half the size. Both variants carry the complete
# standard library, which the asserts after extraction check for.
pattern = re.compile(
    r"^cpython-" + minor + r"\.\d+\+" + re.escape(tag)
    + r"-x86_64-pc-windows-msvc-install_only(?:_stripped)?\.tar\.gz$"
)
assets = [a["name"] for a in release.get("assets", []) if pattern.match(a["name"])]
if not assets:
    sys.exit(f"release {tag} has no cpython {minor} x86_64-pc-windows-msvc install_only asset")
stripped = [name for name in assets if name.endswith("_stripped.tar.gz")]
print((stripped or assets)[0])
PY
}

stage_python() {
  local tag asset tarball tmp tree
  if [[ -n "$PBS_TAG" ]]; then
    tag="$PBS_TAG"
  else
    tag="$(curl -fsSL -o /dev/null -w '%{url_effective}' \
             https://github.com/astral-sh/python-build-standalone/releases/latest \
           | sed -E 's#.*/tag/##')"
    [[ -n "$tag" ]] || die "could not resolve the latest python-build-standalone release"
  fi
  log "python-build-standalone release: $tag"
  asset="$(resolve_python_asset "$tag")" \
    || die "could not pick a python-build-standalone asset for $tag"
  tarball="$WORK/$asset"
  download "$tarball" \
    "https://github.com/astral-sh/python-build-standalone/releases/download/$tag/$asset"

  tmp="$WORK/python-extract"
  rm -rf "$tmp"
  mkdir -p "$tmp"
  "$HOST_PY" - "$(WIN "$tarball")" "$(WIN "$tmp")" <<'PY'
import sys
import tarfile

# python-build-standalone archives are tar.gz even on Windows, and their top
# level is a single `python/` directory.
archive, destination = sys.argv[1], sys.argv[2]
kwargs = {"filter": "fully_trusted"} if hasattr(tarfile, "data_filter") else {}
with tarfile.open(archive) as tar:
    tar.extractall(destination, **kwargs)
PY

  tree="$tmp/python"
  [[ -s "$tree/python.exe" ]] || die "$asset extracted no python.exe"
  [[ -s "$tree/Lib/os.py" ]] || die "$asset has no Lib/os.py — the stripped build dropped the stdlib"
  [[ -d "$tree/Lib/site-packages" ]] || die "$asset has no Lib/site-packages"

  rm -rf "$RES/python"
  mkdir -p "$RES/python"
  cp -a "$tree/." "$RES/python/"
  rm -rf "$tmp" "$tarball"
  log "interpreter staged ($(du -s "$RES/python" | cut -f1) KiB)"

  local py="$RES/python/python.exe"
  local py_win
  py_win="$(WIN "$py")"
  "$py_win" --version \
    || die "the staged interpreter will not start: $py"
  "$py_win" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else sys.version)" \
    || die "the staged interpreter is not cpython 3.12"

  log "installing selekyt and its runtime deps into the staged interpreter"
  ( cd "$REPO" && PIP_DISABLE_PIP_VERSION_CHECK=1 "$py_win" -m pip install \
      --no-warn-script-location ".[transcription,llm]" ) \
    || die "pip install failed for the staged interpreter"
  "$py_win" -m pip check \
    || die "the staged interpreter has inconsistent dependencies (pip check)"

  # Point huggingface_hub at the installer's own `models/` directory so the
  # first transcription loads the staged weights and never needs the network.
  # This is best-effort on purpose: an exception in sitecustomize is fatal at
  # interpreter startup, which would break the app for a reason nobody could
  # see in the webview.
  log "writing the interpreter bootstrap: $RES/python/Lib/site-packages/sitecustomize.py"
  cat > "$RES/python/Lib/site-packages/sitecustomize.py" <<'PY'
"""SelekyT bootstrap for the bundled python-build-standalone interpreter.

Points huggingface_hub at the `models/` directory the installer drops beside
this tree, so a first run transcription uses the staged faster-whisper weights
with no network. Best-effort: an exception here is fatal at startup.
"""
import os
import sys
from pathlib import Path

try:
    here = Path(sys.executable).resolve().parent
    for root in (here.parent, here.parent.parent):
        cache = root / "models" / "hub"
        if cache.is_dir():
            os.environ.setdefault("HF_HOME", str(root / "models"))
            os.environ.setdefault("HF_HUB_CACHE", str(cache))
            break
except Exception:
    pass
PY
}

# ── 3. the faster-whisper weights ────────────────────────────────────────────
stage_model() {
  local cache="$RES/models/hub"
  if [[ -d "$cache/$WHISPER_CACHE_DIR" ]]; then
    log "reusing the staged $WHISPER_REPO snapshot"
    return 0
  fi
  mkdir -p "$cache"
  if [[ "$SKIP_MODEL" == "1" ]]; then
    printf '[stage] WARNING: skipping the whisper download (SELEKYT_SKIP_MODEL=1);\n[stage] WARNING: the installer will build but transcription will need the network.\n' >&2
    return 0
  fi
  log "staging $WHISPER_REPO into the huggingface cache layout"
  HF_HOME="$(WIN "$RES/models")" "$(WIN "$RES/python/python.exe")" - "$WHISPER_REPO" <<'PY' \
    || die "could not stage the faster-whisper model"
import sys
from huggingface_hub import snapshot_download

print(snapshot_download(sys.argv[1]))
PY
  local weights
  weights="$(find "$cache/$WHISPER_CACHE_DIR" -name model.bin -print -quit)"
  [[ -n "$weights" && -s "$weights" ]] \
    || die "the whisper snapshot under $cache contains no model.bin"
  log "whisper small staged ($(du -h "$weights" | cut -f1))"
}

# ── 4. ffmpeg / ffprobe ──────────────────────────────────────────────────────
stage_ffmpeg() {
  local zip="$WORK/ffmpeg-essentials.zip"
  download "$zip" "$FFMPEG_URL"
  "$HOST_PY" - "$(WIN "$zip")" "$(WIN "$RES/bin")" <<'PY' \
    || die "could not stage ffmpeg from $zip"
import os
import sys
import zipfile

zip_path, out_dir = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(zip_path) as archive:
    names = archive.namelist()
    for executable in ("ffmpeg.exe", "ffprobe.exe"):
        hits = [
            name for name in names
            if name.endswith("/bin/" + executable) and not name.startswith("__MACOSX")
        ]
        if not hits:
            sys.exit(f"{zip_path} has no {executable} (looked for */bin/{executable})")
        with archive.open(hits[0]) as source, \
                open(os.path.join(out_dir, executable), "wb") as destination:
            while chunk := source.read(1 << 20):
                destination.write(chunk)
PY
  [[ -s "$RES/bin/ffmpeg.exe" ]] || die "$RES/bin/ffmpeg.exe is missing"
  [[ -s "$RES/bin/ffprobe.exe" ]] || die "$RES/bin/ffprobe.exe is missing"
  log "ffmpeg staged ($(du -h "$RES/bin/ffmpeg.exe" | cut -f1) + $(du -h "$RES/bin/ffprobe.exe" | cut -f1))"
}

# ── 5. the python sidecar launcher ───────────────────────────────────────────
build_python_launcher() {
  local build="$SRC_TAURI/.launcher-build"
  rm -rf "$build"
  mkdir -p "$build/src"

  cat > "$build/Cargo.toml" <<'TOML'
[package]
name = "selekyt-python-launcher"
version = "0.3.0"
edition = "2021"
publish = false

[[bin]]
name = "selekyt-python-launcher"
path = "src/main.rs"

[profile.release]
panic = "abort"
opt-level = "z"
codegen-units = 1
TOML

  # The launcher is deliberately dependency-free std only: it is a process
  # forwarder, nothing else, and a dependency here would be a build failure in
  # a workflow that has no reason to compile one.
  cat > "$build/src/main.rs" <<'RUST'
//! selekyt-python sidecar launcher.
//!
//! `tauri.conf.json` declares `binaries/selekyt-python` under `externalBin`, so
//! the installer drops this file next to `SelekyT.exe` as
//! `selekyt-python-x86_64-pc-windows-msvc.exe`, and the shell in
//! `apps/desktop/src-tauri/src/main.rs` spawns exactly that name with
//! `-m selekyt --server`. A python-build-standalone interpreter resolves its
//! standard library relative to its own executable, so the tree ships intact
//! under `python/` (see `bundle.resources`) and this launcher forwards argv to
//! `python/python.exe` instead of being a copy of it: a copy sitting at the
//! install root would have no `Lib/` beside it and could not bootstrap.

use std::path::PathBuf;
use std::process::{Command, Stdio};

/// Where `python/python.exe` lives relative to this executable.
fn interpreter() -> Option<PathBuf> {
    let executable = std::env::current_exe().ok()?;
    let directory = executable.parent()?;
    // The installed layout: the sidecar and the `python/` resource tree are
    // both children of the install directory.
    let installed = directory.join("python").join("python.exe");
    if installed.is_file() {
        return Some(installed);
    }
    // The staged layout on a build host: `binaries/` beside `resources/python/`,
    // so the sidecar can be exercised before any installer exists.
    let staged = directory
        .join("..")
        .join("resources")
        .join("python")
        .join("python.exe");
    if staged.is_file() {
        return Some(staged);
    }
    None
}

fn main() {
    let interpreter = match interpreter() {
        Some(path) => path,
        None => {
            eprintln!(
                "selekyt-python: the bundled interpreter is missing — looked for \
                 python/python.exe beside {}",
                std::env::current_exe()
                    .map(|path| path.display().to_string())
                    .unwrap_or_else(|_| "this executable".into())
            );
            std::process::exit(127);
        }
    };

    let mut command = Command::new(&interpreter);
    command
        // The shell's own arguments (`-m selekyt --server`) pass straight
        // through, so the wire protocol is identical to the dev wrapper.
        .args(std::env::args_os().skip(1))
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    match command.status() {
        Ok(status) => std::process::exit(status.code().unwrap_or(1)),
        Err(error) => {
            eprintln!(
                "selekyt-python: cannot launch the bundled interpreter {}: {error}",
                interpreter.display()
            );
            std::process::exit(126);
        }
    }
}
RUST

  log "building the selekyt-python sidecar launcher"
  ( cd "$build" && cargo build --release ) \
    || die "the sidecar launcher failed to compile"
  cp -f "$build/target/release/selekyt-python-launcher.exe" \
        "$BIN/selekyt-python-$TRIPLE.exe"
  rm -rf "$build"
  log "selekyt-python launcher staged ($(du -h "$BIN/selekyt-python-$TRIPLE.exe" | cut -f1))"
}

# ── 6. exercise the real path before the long tauri build ────────────────────
verify() {
  log "verifying the staged sidecars"
  "$(WIN "$RES/python/python.exe")" - <<'PY' \
    || die "the staged interpreter cannot import selekyt and its dependencies"
import cv2
import faster_whisper
import requests
import selekyt.server

print(f"[stage] imports ok (faster-whisper {faster_whisper.__version__}, opencv {cv2.__version__})")
PY

  # Ping the sidecar exactly the way the shell does: one request line in, one
  # reply line out. This runs the launcher, the interpreter and selekyt's
  # server loop end to end, so a broken stage fails here instead of after the
  # installer build.
  # The host interpreter is invoked by bare name so Windows resolves it
  # through PATH: converting its posix path can only lose.
  "$HOST_PY" - "$(WIN "$BIN/selekyt-python-$TRIPLE.exe")" <<'PY' \
    || die "the staged selekyt-python sidecar did not answer a ping"
import json
import subprocess
import sys

executable = sys.argv[1]
process = subprocess.Popen(
    [executable, "-m", "selekyt", "--server"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
try:
    stdout, stderr = process.communicate(
        input='{"op": "ping", "params": {}}\n', timeout=120
    )
except subprocess.TimeoutExpired:
    process.kill()
    stdout, stderr = process.communicate()
    sys.exit(
        f"the sidecar did not answer a ping within 120s\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
lines = [line for line in stdout.strip().splitlines() if line.strip()]
try:
    reply = json.loads(lines[-1])
except (IndexError, ValueError) as error:
    sys.exit(
        f"the sidecar sent no JSON reply ({error})\n"
        f"stdout: {stdout}\nstderr: {stderr}"
    )
if not reply.get("ok"):
    sys.exit(f"the sidecar replied with an error: {reply}\nstderr: {stderr}")
print(f"[stage] python sidecar ping ok: {reply.get('data')}")
PY

  local mech="$BIN/selekyt-mech-$TRIPLE.exe"
  [[ -s "$mech" ]] || die "$mech is missing"
  log "python and mech sidecars both answer; staging complete"
}

# ── run ──────────────────────────────────────────────────────────────────────
stage_mech
stage_python
stage_model
stage_ffmpeg
build_python_launcher
verify

log "staged tree:"
log "  $(du -sh "$BIN" | cut -f1)   apps/desktop/src-tauri/binaries/"
for directory in python bin models share; do
  log "  $(du -sh "$RES/$directory" | cut -f1)   apps/desktop/src-tauri/resources/$directory/"
done

log "done. Next: npx tauri build --bundles nsis,msi  (from $HERE)"
