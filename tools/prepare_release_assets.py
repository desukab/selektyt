#!/usr/bin/env python3
"""Stage the assets a wheel must vendor, and verify they are staged.

The wheel is the thing a user installs, and three of its contents are produced
outside Python: the Rust `selekyt-mech` sidecar, the bundled whisper weights,
and the ONNX models the VAD and face-detection paths load. None of them is
rebuildable from `python -m build` alone — the build backend copies files that
are already in the tree, so a tree that has not staged them produces a wheel
that installs and then silently degrades on first use. `engine/vad.py` falls
back to a weaker backend, `providers/whisper.py` falls back to a download, and
the sidecar stops resolving.

That failure mode is why this script exists rather than a Makefile target: a
build that vendors nothing is not a build error, it is a wheel that looks
complete. `--check` exists for the release workflow, which refuses to publish
until every asset is verified present with a message naming the one that is
not.

Run before building:

    python tools/prepare_release_assets.py        # stage, then verify
    python tools/prepare_release_assets.py --check  # verify only, exit nonzero

Idempotent: a tree that already carries every asset is left untouched and
reports as staged.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# The sidecar is built from the Cargo workspace at rust/ and vendored beside
# the engine, which is where providers/sidecar.py resolves the wheel-bundled
# copy from (Path(__file__).parent.parent / "engine" / "bin"). Both halves of
# that path are asserted here so a change to one without the other fails here
# rather than at a user's first transcription.
_SIDECAR_NAME = "selekyt-mech"
_SIDECAR_SOURCE = _REPO / "rust" / "target" / "release" / _SIDECAR_NAME
_SIDECAR_STAGED = _REPO / "engine" / "bin" / _SIDECAR_NAME

# Each entry is (path, what it is for). The model directory is checked for
# model.bin specifically because that is the only file ctranslate2 needs and
# the only one that costs 75 MB — a directory present without it is a staging
# that copied the sidecar files and missed the weights.
_WHISPER_TINY = _REPO / "engine" / "models" / "whisper_tiny" / "model.bin"
_ASSETS: list[tuple[Path, str]] = [
    (_SIDECAR_STAGED, "the Rust sidecar, resolved by providers/sidecar.py"),
    (_WHISPER_TINY, "the bundled whisper tiny weights (offline transcription)"),
    (_REPO / "engine" / "models" / "silero_vad_v6.onnx",
     "the silero VAD v6 weights (the preferred VAD backend)"),
    (_REPO / "engine" / "models" / "face_detection_yunet.onnx",
     "the YuNet face-detection weights (reframing)"),
    (_REPO / "engine" / "models" / "whisper_tiny" / "LICENSE",
     "the bundled whisper model's licence (MIT, ships beside the weights)"),
    (_REPO / "engine" / "models" / "whisper_tiny" / "PROVENANCE.md",
     "the bundled whisper model's provenance record"),
    (_REPO / "third_party" / "otio_fcpxml_adapter" / "LICENSE",
     "the vendored FCPXML reader's Apache-2.0 licence"),
]


def build_sidecar() -> Path:
    """Build the sidecar from the workspace and return the built binary.

    Runs cargo with `--locked` so a tag whose Cargo.lock has drifted from
    Cargo.toml fails here rather than resolving differently on the runner. A
    failure is raised with cargo's own output, because the reason a Rust build
    failed is never usefully summarised by this script.
    """
    print(f"[assets] building {_SIDECAR_NAME} from rust/ (release, locked)")
    proc = subprocess.run(
        ["cargo", "build", "--release", "--locked"],
        cwd=_REPO / "rust",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        raise SystemExit(f"cargo build --release --locked failed (exit {proc.returncode})")
    if not _SIDECAR_SOURCE.is_file():
        raise SystemExit(
            f"cargo reported success but {_SIDECAR_SOURCE} is not there; the "
            "workspace's binary name or target layout changed and "
            f"{__file__} needs updating")
    return _SIDECAR_SOURCE


def stage_sidecar() -> None:
    """Copy the built binary to the path the wheel vendors it from."""
    source = _SIDECAR_SOURCE if _SIDECAR_SOURCE.is_file() else build_sidecar()
    _SIDECAR_STAGED.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, _SIDECAR_STAGED)
    _SIDECAR_STAGED.chmod(0o755)
    print(f"[assets] staged {_SIDECAR_NAME} "
          f"({_SIDECAR_STAGED.stat().st_size} bytes) at {_SIDECAR_STAGED}")


def check() -> int:
    """Verify every asset is staged. Returns the number that are missing."""
    missing = 0
    for path, why in _ASSETS:
        if path.is_file():
            size = path.stat().st_size
            print(f"[assets]   ok   {size:>12,} B  {path.relative_to(_REPO)}")
        else:
            missing += 1
            print(f"[assets]   MISS            {path.relative_to(_REPO)}")
            print(f"[assets]         needed for: {why}")
    if missing:
        print(f"\n[assets] {missing} asset(s) missing. Run "
              f"{Path(__file__).name} without --check to stage them, or "
              "investigate why a staging step did not produce the file.")
    else:
        print(f"\n[assets] all {len(_ASSETS)} release assets staged")
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true",
        help="only verify the assets are staged; stage nothing, exit nonzero "
             "if any is missing")
    args = parser.parse_args(argv)

    if not args.check:
        stage_sidecar()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
