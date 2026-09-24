"""The staged model cache must ship one real copy of each file, or the installer
pays for the same weights two or three times.

huggingface_hub keeps each downloaded file at the end of a chain

    snapshots/<commit>/<file>  ->  blobs/<etag>  ->  <cache>/blobs/<2hex>/<xet>

where only the last element holds bytes on a host that can create symlinks. On
one that cannot, each hop can be materialised as a byte copy instead, and the
installer then carries the same ``model.bin`` at two or three paths. At the
recorded worst that was 1383 MiB of a 2057.5 MiB payload — 67% of the package
was one file, counted more than once.

``collapse_hub_cache.py`` is the step that removes the redundancy. It is pure
stdlib, so the behaviour below is exercised on every host without a download and
without a Windows runner. Four things, each worth failing on its own:

1. the step is wired into staging and runs on every invocation. A cache staged
   before the step existed is exactly the cache that needs collapsing, so gating
   the collapse behind the same "already staged" check that guards the download
   would skip it forever and ship the redundant copies in perpetuity.

2. both layouts collapse to the same thing: one real file at each snapshot path,
   no blob directories, and ``refs/`` / ``trees/`` intact. Those last two are what
   offline resolution reads, so a step that tidied them would trade installer
   size for a cache the app can no longer use offline.

3. the result is genuinely resolvable by huggingface_hub, not merely
   well-shaped. This builds a cache in the format the installed hub actually
   reads and asks the library to resolve it offline. A fixture that imitated the
   format would only ever test the imitation.

4. the step fails the build on a large blob it cannot account for. A blob that is
   not a copy of any snapshot file means the staged layout is not the one this
   step assumes, and shipping it would silently inflate the installer — the same
   defect, undetected. Metadata-sized leftovers cost nothing and are warned
   about, because failing the build over a stray lock file would be worse.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

DESKTOP = pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop"
COLLAPSE = DESKTOP / "collapse_hub_cache.py"
STAGE_SCRIPT = DESKTOP / "stage-windows.sh"

# A staged cache is a single repo by construction, which is what licenses
# touching the cache-wide shared blob store at all.
REPO_DIR = "models--Systran--faster-whisper-small"
COMMIT = "d90ca5fe260221311c53c58e660288d3deb8d356"

# Distinct contents of distinct sizes, so a size or hash collision between them
# cannot make a layout look collapsed when it is not. model.bin sits over the
# 1 MiB hard-error threshold so the failure paths are exercised by real weight.
FILES = {
    "config.json": b'{"model_type":"whisper"}\n',
    "model.bin": bytes((i * 7 + 3) & 0xFF for i in range(1 << 21)),
    "vocabulary.txt": b"alpha\nbravo\ncharlie\n",
}


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _build_cache(root, *, symlinks: bool) -> None:
    """Stage a cache in one of the two layouts hub can leave behind.

    ``symlinks`` is the POSIX form: the bytes live once, in the shared store,
    with the two hops above them as links. Without it every hop is a byte
    copy, which is the shape the collapse exists to undo. Both carry the same
    ``refs/`` and ``trees/`` bookkeeping so the resolution check sees a cache
    that could have been downloaded rather than assembled by hand.
    """
    cache = root / "hub"
    repo = cache / REPO_DIR

    def link_at(path, target_from_cache: str) -> None:
        """``path`` as a symlink to ``target_from_cache``, a cache-root-relative path.

        Relative, like hub writes them, so the staged tree is movable as a whole
        and the fixture does not depend on ``tmp_path`` being stable.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        depth = len(path.parent.relative_to(cache).parts)
        path.symlink_to("/".join([".."] * depth + [target_from_cache]))

    for name, payload in FILES.items():
        etag = _sha256(payload)  # the LFS etag is the content sha256
        # The shared store is content-addressed too, under a separate Xet
        # namespace. It is opaque to this step, but its key point matters here:
        # distinct contents land at distinct paths. One synthesised name for
        # every file would make three different files share a payload, and the
        # fixture would stage a cache that hub could never have downloaded.
        xet = "95" + _sha256(payload)[2:64]
        shared = f"blobs/{xet[:2]}/{xet}"
        (cache / shared).parent.mkdir(parents=True, exist_ok=True)
        if not (cache / shared).is_file():
            (cache / shared).write_bytes(payload)
            # The shared store's own manifest of who points at the payload.
            (cache / shared).with_suffix(".refs").write_text(
                json.dumps([REPO_DIR]), encoding="utf-8"
            )

        blob = repo / "blobs" / etag  # hop 2: repo-scoped, named by etag
        entry = repo / "snapshots" / COMMIT / name  # hop 1: what the app resolves
        if symlinks:
            link_at(blob, shared)
            link_at(entry, f"{REPO_DIR}/blobs/{etag}")
        else:
            for path in (blob, entry):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)

    refs = repo / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    # _snapshot_download reads this with f.read() and no strip, so a trailing
    # newline is not merely cosmetic — it makes the revision unresolvable.
    (refs / "main").write_text(COMMIT, encoding="utf-8")

    # Written through the installed hub's own schema, so the fixture tracks a
    # format change rather than pinning the version this host happens to have.
    tree = repo / "trees"
    tree.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub._tree_cache import TREE_CACHE_FORMAT_VERSION, TreeCacheEntry

        files = {
            name: TreeCacheEntry(
                size=len(payload), blob_id=_sha256(payload), lfs_sha256=_sha256(payload),
                lfs_size=len(payload), xet_hash="95" + "a" * 62,
            ).to_json()
            for name, payload in FILES.items()
        }
        payload = json.dumps(
            {"format_version": TREE_CACHE_FORMAT_VERSION, "files": files}
        )
    except ImportError:
        pytest.skip("huggingface_hub is not installed; the resolution check cannot run")
    (tree / f"{COMMIT}.json").write_text(payload, encoding="utf-8")


@pytest.fixture(scope="module")
def stage_text() -> str:
    assert STAGE_SCRIPT.is_file(), f"the staging script is missing: {STAGE_SCRIPT}"
    return STAGE_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def collapse_mod():
    """Loaded by path, so the module under test is the committed file."""
    assert COLLAPSE.is_file(), f"the collapse step is missing: {COLLAPSE}"
    spec = importlib.util.spec_from_file_location("selekyt_collapse", COLLAPSE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real_files(directory) -> list[pathlib.Path]:
    """Every regular file under ``directory``, symlinks excluded.

    A link in the chain holds no bytes of its own, so counting only regular
    files separates "this path points into the chain" from "this path is a copy
    of the payload".
    """
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.rglob("*") if p.is_file() and not p.is_symlink()
    )


def _payloads(directory) -> list[pathlib.Path]:
    """Real payload files under ``directory``, excluding the shared store's own
    ``<hash>.refs`` bookkeeping, which carries a repo list rather than bytes."""
    return [p for p in _real_files(directory) if not p.name.endswith(".refs")]


def test_the_staging_script_runs_the_collapse_on_every_invocation(stage_text: str) -> None:
    """The reuse guard has to key on the *snapshot*, and the collapse has to run
    outside it. A cache staged before this step existed has a repo directory and
    is still uncollapsed — guarding the collapse the same way the download is
    guarded would mean it never runs on the caches that need it."""
    assert "collapse_hub_cache.py" in stage_text, (
        "the staging script does not invoke the collapse step, so the staged "
        "cache keeps whatever layout hub happened to leave"
    )
    # The path is written relative to $SRC_TAURI, so it resolves the same way
    # the runner resolves it. A typo here is invisible until the Windows runner
    # is minutes into a 1 GB download.
    assert '$SRC_TAURI/../collapse_hub_cache.py' in stage_text, (
        "the collapse is invoked from a path this test does not model"
    )
    assert ((DESKTOP / "src-tauri" / ".." / "collapse_hub_cache.py")
            .resolve() == COLLAPSE), "the stage path does not resolve to the step"

    # Locate the reuse branch and confirm the collapse is not inside it.
    start = stage_text.index("stage_model()")
    body = stage_text[start:stage_text.index("\n}", start)]
    reuse = body.index("WHISPER_CACHE_DIR")
    assert "snapshots" in body[reuse:reuse + 60], (
        "the reuse guard keys on the repo directory rather than its snapshot; a "
        "tree staged before the collapse existed would be reused as-is and the "
        "redundant copies would ship forever"
    )
    collapse_at = body.index("collapse_hub_cache.py")
    # The `fi` at the reuse guard's own indentation closes the guarded block.
    # Indentation-relative rather than a bare "\nfi", so the check survives the
    # script being reindented; the point is the nesting, not the whitespace.
    guard_line = body[:body.index("\n", reuse)].rpartition("\n")[2]
    close = body.index("\n" + guard_line[: len(guard_line) - len(guard_line.lstrip())] + "fi", reuse)
    assert collapse_at > close, (
        "the collapse is inside the reuse branch, so an already-staged tree is "
        "skipped without ever being collapsed"
    )
    # The collapse command's own failure has to fail the build rather than pack
    # the redundant copies anyway. It runs to the blank line that ends it.
    command = body[collapse_at:body.index("\n\n", collapse_at)]
    assert "|| die" in command, (
        "a collapse that fails must fail the build rather than pack the "
        "redundant copies anyway"
    )


def test_a_symlink_chain_collapses_to_one_real_file_per_entry(collapse_mod, tmp_path) -> None:
    """The POSIX layout: one copy exists and the two hops above it are links.
    Collapsing promotes the bytes to the snapshot path and removes the links."""
    cache = tmp_path / "hub"
    repo = cache / REPO_DIR
    _build_cache(tmp_path, symlinks=True)
    # The only real bytes are the three payloads hub downloaded, sitting in the
    # shared store; both hops above them are links, so nothing under the repo
    # holds bytes of its own.
    assert len(_payloads(cache / "blobs")) == len(FILES), (
        "the shared store did not stage one payload per file"
    )
    assert not _real_files(repo / "blobs") + _real_files(repo / "snapshots"), (
        "the symlink layout staged real bytes under the repo, so the fixture "
        "built the wrong layout"
    )

    report = collapse_mod.collapse(cache, REPO_DIR)

    assert report["entries"] == len(FILES)
    assert len(report["materialised"]) == len(FILES), (
        "the snapshot entries were already real files in a symlink layout, which "
        "means the fixture built the wrong layout"
    )
    # The payloads are gone from the store and the bytes now live exactly once,
    # at each snapshot path.
    assert not (cache / "blobs").is_dir(), (
        "the shared blob store survived the collapse; nothing resolves through "
        "it, so it is dead installer payload"
    )
    assert not (repo / "blobs").is_dir(), (
        "the repo's own blob directory survived the collapse"
    )
    assert len(_real_files(repo / "snapshots")) == len(FILES)
    for name in FILES:
        path = repo / "snapshots" / COMMIT / name
        assert not path.is_symlink(), f"{name} is still a symlink after collapsing"
        assert path.read_bytes() == FILES[name], (
            f"{name} did not survive the collapse with its contents intact"
        )
    # Resolution bookkeeping: offline resolution reads both of these.
    assert (repo / "refs" / "main").read_text() == COMMIT
    assert (repo / "trees" / f"{COMMIT}.json").is_file()


def test_a_byte_copy_layout_collapses_to_the_same_tree(collapse_mod, tmp_path) -> None:
    """The layout a host without the symlink privilege leaves behind: every hop
    is a byte copy, so the same file exists at three paths. Both layouts have to
    arrive at the same single-copy tree, or the fix only holds on one runner."""
    cache = tmp_path / "hub"
    repo = cache / REPO_DIR
    _build_cache(tmp_path, symlinks=False)
    # Three payload-bearing paths per file — the store, the repo blob, and the
    # snapshot entry — is the defect this step exists to undo.
    for directory in (cache / "blobs", repo / "blobs", repo / "snapshots"):
        assert len(_payloads(directory)) == len(FILES), (
            f"{directory} did not stage one byte copy per file, so the fixture "
            "is not exercising the redundant layout"
        )

    collapse_mod.collapse(cache, REPO_DIR)

    assert not (cache / "blobs").is_dir(), "the shared blob store survived the collapse"
    assert not (repo / "blobs").is_dir(), "the repo's blob directory survived the collapse"
    assert len(_real_files(repo / "snapshots")) == len(FILES), (
        "a three-copy layout left something other than one real file per entry"
    )
    for name in FILES:
        path = repo / "snapshots" / COMMIT / name
        assert path.read_bytes() == FILES[name]


def test_hub_still_resolves_the_collapsed_cache_offline(collapse_mod, tmp_path) -> None:
    """The step's premise is that hub resolves a file as soon as its snapshot
    pointer exists and never consults a blob path otherwise. That is checked
    against the real library rather than assumed, because the whole fix is
    worthless if the collapsed cache makes the app download the model again."""
    _build_cache(tmp_path, symlinks=True)
    collapse_mod.collapse(tmp_path / "hub", REPO_DIR)

    env = {**os.environ, "HF_HOME": str(tmp_path), "HF_HUB_CACHE": str(tmp_path / "hub")}
    env.pop("HF_HUB_OFFLINE", None)
    proc = subprocess.run(
        [sys.executable, "-c", """
import os, sys
from huggingface_hub import snapshot_download
p = snapshot_download("Systran/faster-whisper-small", local_files_only=True,
                      allow_patterns=["config.json", "model.bin", "vocabulary.txt"])
print(p)
for name in sorted(os.listdir(p)):
    fp = os.path.join(p, name)
    print(name, os.path.getsize(fp), os.path.islink(fp))
"""],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    if proc.returncode != 0:
        pytest.fail(
            "huggingface_hub could not resolve the collapsed cache offline — the "
            "step removed something resolution needs:\n" + proc.stderr.strip()
        )
    lines = proc.stdout.strip().splitlines()
    assert lines[0].endswith(f"snapshots/{COMMIT}"), (
        f"resolved to {lines[0]!r} rather than the staged snapshot; the cache's "
        "revision bookkeeping is broken"
    )
    resolved = {}
    for line in lines[1:]:
        name, size, is_link = line.rsplit(" ", 2)
        resolved[name] = (int(size), is_link == "True")
    assert set(resolved) == set(FILES), (
        f"resolved {sorted(resolved)} rather than the staged files; an entry the "
        "tree lists is missing from the snapshot"
    )
    for name, payload in FILES.items():
        size, is_link = resolved[name]
        assert size == len(payload), f"{name} resolved at the wrong size: {size}"
        assert not is_link, (
            f"{name} resolved as a symlink; it points through a blob the collapse "
            "was supposed to remove"
        )


def test_the_collapse_is_idempotent(collapse_mod, tmp_path) -> None:
    """A cache that already holds one real file per entry must report that it
    changed nothing. The step runs on every staging invocation, so a second run
    that touched files would be churning the installer payload for no benefit."""
    cache = tmp_path / "hub"
    _build_cache(tmp_path, symlinks=True)
    first = collapse_mod.collapse(cache, REPO_DIR)
    assert first["removed"], "the fixture did not stage a redundant cache"

    second = collapse_mod.collapse(cache, REPO_DIR)
    assert not second["materialised"] and not second["removed"], (
        f"a second collapse materialised {len(second['materialised'])} and removed "
        f"{len(second['removed'])} entries; the step is not idempotent and would "
        "rewrite the payload on every build"
    )
    assert second["freed"] == 0


def test_an_unaccountable_blob_fails_the_build(collapse_mod, tmp_path) -> None:
    """A blob that is not a copy of any snapshot file means the staged layout is
    not the one this step assumes. Shipping it would silently inflate the
    installer — the original defect, undetected — so a heavy one must fail the
    build rather than pass with a warning."""
    cache = tmp_path / "hub"
    _build_cache(tmp_path, symlinks=True)
    # A weight-sized blob nothing points at and no snapshot file matches.
    stray = cache / REPO_DIR / "blobs" / ("0" * 64)
    stray.write_bytes(bytes((i * 11 + 5) & 0xFF for i in range(1 << 21)))

    proc = subprocess.run(
        [sys.executable, str(COLLAPSE), str(cache), REPO_DIR],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0, (
        "an unaccounted 2 MiB blob passed the build; the step would ship it "
        "inside the installer without notice"
    )
    assert "FATAL" in proc.stderr, (
        "the failure must be on stderr and say so, or it reads as a warning in "
        "a green build log"
    )
    # The cache is left for a human to inspect rather than deleted blind.
    assert stray.is_file(), "the unaccountable blob was deleted instead of reported"


def test_a_metadata_sized_leftover_is_warned_not_fatal(collapse_mod, tmp_path) -> None:
    """A stray lock or manifest costs nothing and carries no weights, so failing
    the build over one would make the step brittle for no installer gain."""
    cache = tmp_path / "hub"
    _build_cache(tmp_path, symlinks=True)
    stray = cache / REPO_DIR / "blobs" / ("1" * 64)
    stray.write_bytes(b"{}")

    proc = subprocess.run(
        [sys.executable, str(COLLAPSE), str(cache), REPO_DIR],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        "a 2-byte leftover failed the build; the threshold that separates a "
        "warning from a fatal error is too low"
    )
    assert "WARNING" in proc.stdout
