"""Resolution and JSON-RPC client for the compiled Rust `selekyt-mech` sidecar.

The mechanical layer (cut algebra, timecode, FCPXML emission, the ffprobe/ffmpeg
wrappers) ships as a Rust binary spoken to over stdio, so the Python
intelligence core never pays for an FFI boundary. Locating that binary is
deliberately layered rather than hard-coded: an operator override wins, then the
copy the wheel vendors next to the engine, then a dev-tree build, then PATH. The
order matters in both directions — a wheel install has no `rust/` tree and must
find its bundled copy, while a developer running from the tree has no bundled
copy and must find `target/release`, and neither should ever silently fall back
to an unrelated `selekyt-mech` that happens to be on PATH when a better one was
already built.

The wire protocol mirrors `selekyt/server.py` (PROTOCOL_RUST_COMPAT there):
one JSON object per line, request `{"op", "params"}`, response
`{"ok": true, "op", "data"}` or `{"ok": false, "op", "error"}`. The binary is a
daemon that reads until EOF, but every call here is one request then stdin
closed: a long-lived client is a property of the caller (the Tauri shell), and a
one-shot round trip keeps the contract stateless, testable, and unable to leak
a process across a failure.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# The vendored copy an installed wheel ships, resolved against this module so an
# editable checkout (where `providers/` sits at the tree root) and a wheel
# install (where it sits under the package) both land on the same place.
_BUNDLED_NAME = "selekyt-mech"


class SidecarError(RuntimeError):
    """The sidecar could not be reached, or answered with a failure.

    The two are reported distinctly because they mean different things to an
    operator: "not available" is an installation problem — no binary, or one
    that cannot be executed — while "op failed" is a real result from a working
    binary, and may be a legitimate answer rather than a bug (``probe`` on a
    file ffprobe cannot read, for instance). Both are raised through one type so
    a caller that only cares that it failed can catch it, and a caller that
    wants to distinguish can inspect the message.
    """


def _dev_tree_binary() -> Path | None:
    """The release build in the source tree this module was imported from.

    Walks upward looking for the workspace markers (``pyproject.toml`` and
    ``rust/Cargo.toml``) rather than assuming a fixed depth, because the depth
    differs between a checkout and an editable install. The markers are the
    guard that keeps this from firing from a wheel install: a wheel has the
    package but never the Rust workspace, so an installed copy falls through to
    its bundled binary instead of hunting for a build it does not have.
    """
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        if (cand / "pyproject.toml").is_file() and (cand / "rust" / "Cargo.toml").is_file():
            binary = cand / "rust" / "target" / "release" / _BUNDLED_NAME
            return binary if binary.is_file() else None
    return None


def _bundled_binary() -> Path | None:
    """The copy the wheel vendors beside the engine, or ``None``.

    Resolved relative to this module so an editable checkout (``providers/`` at
    the tree root) and a wheel install (``providers/`` under the package) both
    land on the same place.
    """
    bundled = Path(__file__).resolve().parent.parent / "engine" / "bin" / _BUNDLED_NAME
    return bundled if bundled.is_file() else None


def sidecar_path() -> Path | None:
    """Resolve the sidecar binary, or ``None`` when no candidate exists.

    Order, and why each step is where it is:

    1. ``SELEKYT_MECH`` when it names an existing file — an explicit operator
       override, which is how the parity test is aimed at a specific build. A
       value that does not point at a file is ignored rather than fatal, so a
       stale setting degrades to discovery instead of disabling the sidecar.
    2. The wheel-bundled copy at ``engine/bin/selekyt-mech`` (see
       ``_bundled_binary``). This is what an installed wheel sees and the
       primary path in production.
    3. The dev-tree release build (see ``_dev_tree_binary``), which is what a
       developer running from the checkout sees.
    4. ``shutil.which`` — last, so a stray binary on PATH never shadows a build
       or bundle that was found first.
    """
    env = os.environ.get("SELEKYT_MECH")
    if env and Path(env).is_file():
        return Path(env)

    bundled = _bundled_binary()
    if bundled is not None:
        return bundled

    dev = _dev_tree_binary()
    if dev is not None:
        return dev

    found = shutil.which(_BUNDLED_NAME)
    return Path(found) if found else None


def sidecar_available() -> bool:
    """Whether ``sidecar_path()`` resolved to something. Callers that prefer to
    branch over skip a call use this; callers that prefer an exception let
    ``run_op`` report the same condition through ``SidecarError``."""
    return sidecar_path() is not None


def _round_trip(op: str, params: dict[str, Any] | None, timeout: float) -> dict:
    """Spawn the binary, send one request, read one response, and tear it down.

    Returns the envelope's ``data`` object on success. Every failure path raises
    ``SidecarError``, and the message says which of the two classes it is:
    unreachable binaries, failed spawns, nonzero exits and timeouts are
    availability problems, while an ``ok: false`` envelope is an op failure.

    stdin is closed after the request so the daemon's read loop hits EOF and
    exits on its own; the pipe and the process are torn down in a ``finally``
    regardless, because a hung or crashed sidecar must never be left as an
    orphan holding a media file open.
    """
    binary = sidecar_path()
    if binary is None:
        raise SidecarError(
            f"selekyt-mech is not available: no binary found (set SELEKYT_MECH, "
            f"install a wheel that bundles engine/bin/{_BUNDLED_NAME}, or run "
            "`cargo build --release` in rust/)"
        )

    try:
        proc = subprocess.Popen(
            [str(binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
    except OSError as exc:
        raise SidecarError(
            f"selekyt-mech is not available at {binary}: {exc}"
        ) from exc

    try:
        assert proc.stdin is not None and proc.stdout is not None
        request = json.dumps({"op": op, "params": params or {}}) + "\n"

        try:
            stdout, _ = proc.communicate(input=request, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise SidecarError(
                f"op '{op}' failed: selekyt-mech did not answer within {timeout}s"
            ) from None

        if proc.returncode != 0:
            raise SidecarError(
                f"op '{op}' failed: selekyt-mech exited with code {proc.returncode}"
            ) from None

        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        if not lines:
            raise SidecarError(
                f"op '{op}' failed: selekyt-mech closed the pipe without a response"
            ) from None
        try:
            resp = json.loads(lines[0])
        except ValueError as exc:
            raise SidecarError(
                f"op '{op}' failed: unparsable response {lines[0]!r}"
            ) from exc

        if not resp.get("ok"):
            raise SidecarError(
                f"op '{op}' failed: {resp.get('error')}"
            ) from None
        return resp["data"]
    finally:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def ping(timeout: float = 10.0) -> dict:
    """Round-trip a ``ping`` and return its result (``pong``, ``vad_backend``,
    ``vad_model``). The cheapest possible liveness check — no params, no media —
    so a packaging or PATH problem surfaces here rather than midway through a
    real op."""
    return _round_trip("ping", None, timeout)


def run_op(op: str, params: dict[str, Any] | None = None, timeout: float = 60.0) -> dict:
    """Send one op and return its ``data`` object.

    One request/response per call (see the module docstring for why the daemon
    is treated as one-shot here). Raises ``SidecarError`` when the binary is
    missing, cannot start, exits nonzero, times out, or answers with an
    ``ok: false`` envelope; the message distinguishes the binary being
    unavailable from the op itself having failed."""
    return _round_trip(op, params, timeout)
