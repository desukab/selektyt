"""The sidecar resolver and its JSON-RPC client, against the real binary where
one is built.

``providers/sidecar.py`` is the seam between the Python intelligence core and
the compiled Rust mechanical layer, so these tests pin two things separately:
that the binary is *found* in the right order, and that the wire protocol is
*spoken* correctly. The protocol half is skipped on a host without a build —
it names the build command in its skip reason rather than failing opaquely —
while the resolution half runs everywhere, because getting the order wrong is
what makes an installed wheel find nothing or a developer's checkout find the
wrong binary.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from providers import sidecar
from providers.sidecar import SidecarError, ping, run_op, sidecar_available, sidecar_path

_REPO = Path(__file__).resolve().parent.parent
_DEV_BINARY = _REPO / "rust" / "target" / "release" / "selekyt-mech"

skip_no_mech = pytest.mark.skipif(
    not sidecar_available(),
    reason=(
        "selekyt-mech not built and not on PATH "
        "(run `cargo build --release` in rust/, or point SELEKYT_MECH at a build)"
    ),
)


# ── resolution order ──

def test_sidecar_path_honours_the_env_override(monkeypatch, tmp_path):
    """``SELEKYT_MECH`` is the operator escape hatch — how a CI job or a
    packaging check aims at one specific build — so a file it names must win
    outright, ahead of the bundled copy and the dev-tree build."""
    fake = tmp_path / "fake-mech"
    fake.write_bytes(b"#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("SELEKYT_MECH", str(fake))
    assert sidecar_path() == fake


def test_sidecar_path_falls_through_when_env_names_nothing(monkeypatch):
    """A stale or mistyped override must degrade to discovery, not disable the
    sidecar: the value points at no file, so resolution continues past it and
    never reports the missing path as if it were the answer."""
    bogus = Path("/nonexistent/selekyt-mech")
    monkeypatch.setenv("SELEKYT_MECH", str(bogus))
    resolved = sidecar_path()
    assert resolved != bogus, "a broken override was returned instead of fallen through"
    # Whatever it fell back to is still a real file (the bundle, a build, or
    # PATH) — never a promise it did not verify.
    assert resolved is None or resolved.is_file()


# ── protocol, against the real binary ──

@skip_no_mech
def test_ping_answers_the_real_binary():
    """``ping`` is the liveness probe the shell uses before it commits to a
    real op, so it must reach a running binary and parse its reply: a packaging
    or PATH problem has to surface here rather than midway through a render."""
    data = ping()
    assert data["pong"] is True
    assert data["crate"] == "selekyt-core"
    assert data["vad_backend"], "backend must be reported"
    assert data["vad_model"], "model must be reported"


@skip_no_mech
def test_run_op_returns_the_envelopes_data():
    """The client unwraps ``data`` for a caller, so a successful op yields the
    payload directly rather than the envelope around it."""
    data = run_op("detect_rate", {"fps": 29.97, "drop_frame": True})
    assert data["nominal_fps"] == 30
    assert data["is_drop_frame"] is True


@skip_no_mech
def test_run_op_surfaces_a_json_rpc_error_for_an_unknown_op():
    """An unknown op is a protocol-level result, not a crash: the binary must
    stay alive, answer with an ``ok: false`` envelope, and have that error
    surfaced as an op failure rather than as the sidecar being missing."""
    with pytest.raises(SidecarError) as excinfo:
        run_op("definitely_not_an_op")
    message = str(excinfo.value)
    assert "failed" in message, "an op error must be reported as an op failure"
    assert "unknown op" in message
    assert "not available" not in message


# ── availability vs op failure ──

def test_run_op_reports_a_missing_binary_as_unavailable(monkeypatch):
    """No candidate binary is an installation problem, not a result: the error
    says the sidecar is not available and never claims an op ran and failed."""
    monkeypatch.setattr(sidecar, "sidecar_path", lambda: None)
    with pytest.raises(SidecarError) as excinfo:
        run_op("ping")
    assert "not available" in str(excinfo.value)
    assert "failed" not in str(excinfo.value)


def test_run_op_reports_a_path_that_cannot_be_spawned_as_unavailable(monkeypatch):
    """Resolution returned a path that is not executable — the file vanished
    between resolving and spawning, or the bundle is incomplete. That is the
    same class of failure as finding nothing, and is reported the same way."""
    monkeypatch.setattr(sidecar, "sidecar_path", lambda: Path("/nonexistent/selekyt-mech"))
    with pytest.raises(SidecarError) as excinfo:
        run_op("ping")
    assert "not available" in str(excinfo.value)


@skip_no_mech
def test_the_bundled_copy_is_preferred_over_the_dev_tree_build(monkeypatch):
    """Both candidates exist in this tree — the staged wheel copy under
    ``engine/bin/`` and the release build under ``rust/target/`` — and the
    wheel copy is what an installed copy of SeleKyT actually runs, so it wins.
    The dev-tree build is the fallback for a checkout that has not staged the
    bundle, not an equal."""
    monkeypatch.delenv("SELEKYT_MECH", raising=False)
    resolved = sidecar_path()
    assert resolved is not None
    assert resolved.name == "selekyt-mech"
    assert resolved.parent.parent.name == "engine", (
        f"expected the wheel-vendored copy under engine/bin, got {resolved}")


@skip_no_mech
def test_the_dev_tree_build_is_used_when_the_bundle_is_absent(monkeypatch, tmp_path):
    """Remove the wheel copy and the resolver falls through to the release
    build in the Rust workspace — the path the parity test and the Tauri shell
    also use. This is the case a developer hits before staging assets."""
    monkeypatch.delenv("SELEKYT_MECH", raising=False)
    monkeypatch.setattr(sidecar, "_bundled_binary", lambda: None)
    assert sidecar_path() == _DEV_BINARY
