"""The ffmpeg resolver: why an install without a system ffmpeg still works.

``providers.ffmpeg.resolve`` is the one place SeleKyT decides which ffmpeg to
run, and every module that shells out reaches it. These tests pin the order and
pin what happens at each boundary, because the order is load-bearing:

- ``SELEKYT_FFMPEG`` first, because a staged desktop bundle names its own ffmpeg
  and an operator who set the variable means it;
- ``shutil.which`` next, because a system ffmpeg is the normal case and the one
  the corpus was measured against;
- the ``static-ffmpeg`` wheel last, which is what makes ``pip install
  selekyt[media]`` work on a machine with no ffmpeg at all.

Reordering the first two would silently ignore a bundle's own ffmpeg; dropping
the third would restore the "must be on PATH" failure mode that the extra
exists to remove.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from providers.ffmpeg import resolve, ffmpeg_bin, ffprobe_bin


@pytest.fixture
def no_static_ffmpeg(monkeypatch):
    """Make the wheel fallback report nothing, so a test that wants to exercise
    the system-PATH branch is not satisfied by an installed static-ffmpeg."""
    monkeypatch.setattr("providers.ffmpeg._static_ffmpeg_bin", lambda _: None)


def test_an_override_wins_over_a_system_binary(monkeypatch):
    """``SELEKYT_FFMPEG`` is consulted first, so a bundle that ships its own
    ffmpeg actually uses it rather than whichever the PATH happens to offer."""
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setenv("SELEKYT_FFMPEG", "/bundle/ffmpeg")
    assert resolve("ffmpeg") == "/bundle/ffmpeg"


def test_a_system_binary_is_used_when_no_override_is_set(monkeypatch, no_static_ffmpeg):
    monkeypatch.setattr("providers.ffmpeg.shutil.which",
                        lambda n: f"/usr/bin/{n}")
    assert resolve("ffmpeg") == "/usr/bin/ffmpeg"
    assert resolve("ffprobe") == "/usr/bin/ffprobe"


def test_the_static_ffmpeg_wheel_is_the_last_resort(monkeypatch, tmp_path):
    """With no override and no system binary, the wheel's own ffmpeg answers.
    The path returned must be a real file the caller can exec, not a directory
    the zip has not unpacked into yet."""
    fake = tmp_path / "linux" / "ffmpeg"
    fake.parent.mkdir(parents=True)
    fake.write_bytes(b"")
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: None)
    monkeypatch.setattr("providers.ffmpeg.Path", lambda *a: fake.parent)
    monkeypatch.setattr("providers.ffmpeg._static_ffmpeg_bin",
                        lambda n: str(fake) if n == "ffmpeg" else None)
    assert resolve("ffmpeg") == str(fake)


def test_a_missing_binary_raises_with_an_install_hint(monkeypatch):
    """The terminal error names the extra that fixes it. A bare 'not found'
    sends a user hunting for a package they were never told about."""
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: None)
    monkeypatch.setattr("providers.ffmpeg._static_ffmpeg_bin", lambda _: None)
    with pytest.raises(RuntimeError) as exc:
        resolve("ffmpeg")
    assert "static-ffmpeg" in str(exc.value)


def test_the_override_is_read_per_binary(monkeypatch, no_static_ffmpeg):
    """``SELEKYT_FFMPEG`` and ``SELEKYT_FFPROBE`` are separate keys, so a bundle
    that ships only one of them still resolves the other from the system."""
    monkeypatch.setattr("providers.ffmpeg.shutil.which",
                        lambda n: f"/usr/bin/{n}")
    monkeypatch.setenv("SELEKYT_FFPROBE", "/bundle/ffprobe")
    assert resolve("ffprobe") == "/bundle/ffprobe"
    assert resolve("ffmpeg") == "/usr/bin/ffmpeg"


def test_the_public_helpers_resolve_their_own_binary(monkeypatch, no_static_ffmpeg):
    """``ffmpeg_bin`` / ``ffprobe_bin`` are the names the call sites use; they
    must never be swapped with each other."""
    seen = []

    def fake_which(name):
        seen.append(name)
        return f"/usr/bin/{name}"

    monkeypatch.setattr("providers.ffmpeg.shutil.which", fake_which)
    assert ffmpeg_bin() == "/usr/bin/ffmpeg"
    assert ffprobe_bin() == "/usr/bin/ffprobe"
    assert seen == ["ffmpeg", "ffprobe"]


def test_the_wheel_fallback_is_skipped_when_the_package_is_absent(monkeypatch):
    """A host without ``static-ffmpeg`` installed must fall through cleanly
    rather than raising an ImportError from inside a binary lookup."""
    import builtins

    real_import = builtins.__import__

    def _block(name, *a, **k):
        if name == "static_ffmpeg":
            raise ImportError("no static_ffmpeg")
        return real_import(name, *a, **k)

    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: None)
    monkeypatch.setattr(builtins, "__import__", _block)
    with pytest.raises(RuntimeError, match="static-ffmpeg"):
        resolve("ffmpeg")


# ── the VAD chain order, which the resolver must not reorder ──────────────
#
# ``get_vad_backend`` prefers silero, then ffmpeg, then energy, then whisper.
# Making ffmpeg *reachable* on a host that has it bundled is the point of the
# resolver; promoting it above silero would be a regression against the
# measured table in docs/AI_EXECUTION_STATE.md. The two tests below pin both
# halves of that distinction.

def test_the_ffmpeg_vad_backend_reports_available_through_the_resolver(monkeypatch):
    """An install whose only ffmpeg is the bundled wheel still gets the ffmpeg
    VAD backend at its measured place in the chain."""
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: None)
    monkeypatch.setattr("providers.ffmpeg._static_ffmpeg_bin",
                        lambda n: "/wheel/ffmpeg" if n == "ffmpeg" else None)
    from engine.vad import FFmpegVADBackend
    assert FFmpegVADBackend().is_available() is True


def test_silero_still_wins_when_ffmpeg_is_bundled(monkeypatch):
    """The resolver makes the ffmpeg rung reachable; it must not promote it.
    Silero stays preferred wherever its model and onnxruntime are present."""
    monkeypatch.setattr("providers.ffmpeg.shutil.which", lambda _: None)
    monkeypatch.setattr("providers.ffmpeg._static_ffmpeg_bin",
                        lambda n: "/wheel/ffmpeg" if n == "ffmpeg" else None)
    from engine.vad import get_vad_backend, SileroVADBackend
    if not SileroVADBackend().is_available():
        pytest.skip("silero needs onnxruntime and its model, absent here")
    assert get_vad_backend().name == "silero"
