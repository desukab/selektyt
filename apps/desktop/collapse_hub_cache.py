"""Collapse a staged huggingface_hub cache to one real file per snapshot entry.

Run by apps/desktop/stage-windows.sh after the weights are staged, so that the
installer ships exactly one copy of each model file. The cache layout keeps each
downloaded file at the end of a chain

    snapshots/<commit>/<file>  ->  blobs/<etag>  ->  <cache>/blobs/<2hex>/<xet>

where only the last element holds bytes and the rest are symlinks. That is the
layout on a POSIX runner. The chain is walked with a symlink-aware copy fallback
(`_create_symlink` in huggingface_hub/file_download.py) which, on a host that
cannot create symlinks, materialises every hop as a byte copy — so the number of
copies depends on the runner's privileges and on the installed hub version, and
has historically been more than one on windows-latest.

The prune is therefore driven by content, not by layout assumptions:

1. every snapshot entry is materialised as a regular file at its own path;
2. the sha256 of each surviving snapshot file is the "live" set;
3. any blob under the repo's own ``blobs/`` or the cache-wide ``blobs/`` store
   whose content is in the live set is deleted, because nothing resolves through
   it any more.

huggingface_hub resolves a file to ``snapshots/<commit>/<file>`` and returns it
as soon as that path exists — file_download.py returns early with "Pointer
already exists -> ... return without attempting to write to the cache" on both
the online and the offline path. The blob paths are only consulted when the
pointer is missing, so deleting a blob whose bytes survive at the pointer cannot
break resolution. Offline snapshot validation (`_raise_if_incomplete_snapshot`)
checks the same snapshot paths against the cached ``trees/<commit>.json``
listing, which is why ``refs/`` and ``trees/`` are left untouched.

Nothing here is Windows-specific, and the step is idempotent: a cache that
already holds one real file per snapshot entry reports that it changed nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

# A blob heavier than this that survives the prune is a hard error rather than a
# warning: it means the staged cache does not match the layout this script
# assumes, and shipping it would silently inflate the installer. Metadata-sized
# leftovers are only warned about, since they cost nothing.
HARD_ERROR_BLOB_BYTES = 1 << 20

_CHUNK = 1 << 20


def sha256(path: Path, expected_size: int | None = None) -> str | None:
    """Hash ``path``, or return None if its size rules out a match.

    Size is checked first because it is free and because a size difference alone
    proves the contents differ, which avoids hashing hundreds of megabytes only
    to discover the file was never a candidate.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if expected_size is not None and size != expected_size:
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def snapshot_paths(repo_dir: Path) -> list[Path]:
    """Every regular entry under each of the repo's snapshot revisions."""
    found: list[Path] = []
    snapshots = repo_dir / "snapshots"
    if not snapshots.is_dir():
        return found
    for snapshot in sorted(snapshots.iterdir()):
        if snapshot.is_dir():
            found.extend(sorted(p for p in snapshot.rglob("*") if p.is_file()))
    return found


def materialise(path: Path) -> str | None:
    """Turn a snapshot entry into a regular file at its own path.

    Returns None when it was already a regular file. Otherwise the chain is
    resolved to the bytes and linked in place — a hardlink, so the prune that
    follows costs no extra disk — falling back to a copy across volumes or on a
    filesystem that refuses hardlinks.
    """
    if not path.is_symlink():
        return None
    target = Path(os.path.realpath(path))
    if not target.is_file():
        raise FileNotFoundError(
            f"{path} points at {target}, which does not exist — the cache is half-built and "
            "collapsing it would lose data"
        )
    tmp = path.with_name(f".{path.name}.collapse")
    try:
        os.link(target, tmp)
    except OSError:
        shutil.copyfile(target, tmp)
    os.replace(tmp, path)
    return str(target)


def live_sizes(repo_dir: Path) -> dict[int, set[Path]]:
    """Size -> snapshot files of that size, for the cheap pre-filter."""
    by_size: dict[int, set[Path]] = {}
    for path in snapshot_paths(repo_dir):
        by_size.setdefault(path.stat().st_size, set()).add(path)
    return by_size


def blob_paths(cache_dir: Path, repo_dir: Path) -> list[Path]:
    """Blob files that belong to this repo: its own ``blobs/`` and the cache store.

    The cache-wide ``<cache>/blobs/`` store is keyed by content hash and may be
    shared with other repos, so it is only offered when this repo is the only one
    staged — the staging script's situation by construction.
    """
    found: list[Path] = []
    repo_blobs = repo_dir / "blobs"
    if repo_blobs.is_dir():
        found.extend(p for p in repo_blobs.rglob("*") if p.is_file())

    shared = cache_dir / "blobs"
    if shared.is_dir():
        others = [
            p
            for p in cache_dir.iterdir()
            if p.is_dir() and p.name.startswith("models--") and p != repo_dir
        ]
        if not others:
            for p in shared.rglob("*"):
                # The store carries its own bookkeeping alongside the payloads.
                if p.is_file() and not p.name.endswith((".refs", ".lock")):
                    found.append(p)
    return found


def collapse(cache_dir: Path, repo_dir_name: str) -> dict[str, object]:
    """Collapse one staged repo and return a report for the build log."""
    repo_dir = cache_dir / repo_dir_name
    if not repo_dir.is_dir():
        raise FileNotFoundError(f"no staged repo at {repo_dir}")

    entries = snapshot_paths(repo_dir)
    if not entries:
        raise FileNotFoundError(f"no snapshot under {repo_dir}/snapshots")

    materialised: list[str] = []
    for path in entries:
        target = materialise(path)
        if target is not None:
            materialised.append(target)

    # Re-read after materialising: the entries are now regular files, and any of
    # them may have changed inode.
    entries = snapshot_paths(repo_dir)
    for path in entries:
        if path.is_symlink():
            raise RuntimeError(f"{path} is still a symlink after collapsing it")

    by_size = live_sizes(repo_dir)
    live_hashes: set[str] = set()
    for path in entries:
        digest = sha256(path)
        if digest is not None:
            live_hashes.add(digest)

    removed: list[str] = []
    freed = 0
    unmatched: list[tuple[Path, int]] = []
    for blob in blob_paths(cache_dir, repo_dir):
        size = blob.stat().st_size
        # Only a blob matching a live file's *size* is worth hashing; one that
        # does not is unmatched outright. A blob that shares a live file's size
        # but not its content is unmatched too — the size check is a pre-filter,
        # never a verdict, because a weight-sized blob that is not a copy of any
        # shipped file is exactly what the hard error below exists to catch.
        digest = sha256(blob, expected_size=size) if size in by_size else None
        if digest is None or digest not in live_hashes:
            unmatched.append((blob, size))
            continue
        removed.append(str(blob))
        freed += size
        blob.unlink()
        # A shared-store payload carries a <hash>.refs manifest naming the
        # repos that point at it. With the payload gone the manifest is dead;
        # deleting it is the safe direction, since hub treats a missing
        # manifest as "leak the blob" rather than "delete a live one".
        blob.with_suffix(blob.suffix + ".refs").unlink(missing_ok=True)

    # Drop the blob directories that have gone empty, so the installer tree
    # carries no dead paths. rmdir rather than rmtree: a directory that still
    # holds something is left alone instead of being swept.
    for blob in map(Path, removed):
        directory = blob.parent
        while directory != directory.parent:
            try:
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent

    weights = repo_dir / "snapshots"
    return {
        "entries": len(entries),
        "materialised": materialised,
        "removed": removed,
        "freed": freed,
        "unmatched": [(str(p), s) for p, s in unmatched],
        "weights_bytes": sum(p.stat().st_size for p in weights.rglob("*") if p.is_file()),
    }


def _human(count: int) -> str:
    size = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1 << 10 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1 << 10
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cache_dir", help="the huggingface cache root (the HF_HUB_CACHE dir)")
    parser.add_argument("repo_dir_name", help="the models--<org>--<name> directory inside it")
    args = parser.parse_args()

    report = collapse(Path(args.cache_dir), args.repo_dir_name)

    print(f"[collapse] {report['entries']} snapshot file(s), {_human(report['weights_bytes'])} of payload")
    for target in report["materialised"]:
        print(f"[collapse] materialised a symlink from {target}")
    for path in report["removed"]:
        print(f"[collapse] removed redundant blob {path}")
    print(f"[collapse] freed {_human(report['freed'])}")

    failures: list[str] = []
    for path, size in report["unmatched"]:
        message = f"blob {path} ({_human(size)}) is not a copy of any snapshot file"
        if size >= HARD_ERROR_BLOB_BYTES:
            failures.append(message + " — refusing to ship a cache that does not match the expected layout")
        else:
            print(f"[collapse] WARNING: {message}; leaving it in place")

    if report["materialised"] or report["removed"]:
        print("[collapse] cache collapsed to one real file per snapshot entry")
    else:
        print("[collapse] cache already held one real file per snapshot entry; nothing to do")

    for failure in failures:
        print(f"[collapse] FATAL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
