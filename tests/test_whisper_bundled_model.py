"""The bundled whisper model: why a clean install transcribes offline.

``providers.whisper.resolve_model_path`` decides what ``WhisperModel`` is
given. A size name ("small") makes faster-whisper download from Hugging Face;
a directory path is loaded from disk. The tiny model ships inside the wheel
under ``engine/models/whisper_tiny/``, and the difference between passing the
name and passing the path is exactly the difference between a first run that
works offline and one that dies at the first chunk with a connection error
after the audio has already been extracted.

The tiny model is the last entry in ``FALLBACK_CHAIN``, so a machine that has
already given up on small and base for speed always lands here. That stop must
not be a download.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from providers.whisper import resolve_model_path, BUNDLED_MODELS


def test_tiny_resolves_to_the_bundled_directory():
    """The bundled copy is preferred over a download for the one size we ship."""
    resolved = resolve_model_path("tiny")
    assert Path(resolved).is_dir(), f"resolved to a path that is not a directory: {resolved}"
    assert (Path(resolved) / "model.bin").is_file(), "no model.bin in the bundled directory"


def test_sizes_we_do_not_ship_still_download(monkeypatch):
    """``small`` and ``base`` are not bundled, so they pass through unchanged
    for faster-whisper to fetch. Bundling every size would be a 3 GB wheel for
    a preference most users never override."""
    for name in ("small", "base", "medium", "large-v3"):
        assert resolve_model_path(name) == name, (
            f"{name} is not bundled and must stay a size name")


def test_an_override_wins_for_any_size(monkeypatch):
    """``SELEKYT_WHISPER_MODEL`` points a staged install at its own weights,
    including for sizes the wheel does not carry."""
    monkeypatch.setenv("SELEKYT_WHISPER_MODEL", "/staged/whisper-small")
    assert resolve_model_path("tiny") == "/staged/whisper-small"
    assert resolve_model_path("small") == "/staged/whisper-small"


def test_an_empty_override_is_treated_as_unset(monkeypatch):
    """``SELEKYT_WHISPER_MODEL=""`` is not a usable path — handing it to
    ``WhisperModel`` would raise a directory error from ctranslate2 — so an
    empty value means "no override", the same way an unset one does. The
    distinction that matters is set-vs-unset, and an empty string cannot name a
    model."""
    monkeypatch.setenv("SELEKYT_WHISPER_MODEL", "")
    assert resolve_model_path("tiny") == str(
        Path(__file__).resolve().parent.parent / "engine" / "models" / "whisper_tiny")


def test_a_bundled_directory_must_actually_contain_model_bin(monkeypatch, tmp_path):
    """A directory that exists but has no weights must not be handed to
    ``WhisperModel`` — it would raise a confusing directory-read error from
    ctranslate2 instead of the clear 'download it' path."""
    empty = tmp_path / "whisper_tiny"
    empty.mkdir()
    monkeypatch.setattr("providers.whisper._ENGINE_ROOT", tmp_path)
    assert resolve_model_path("tiny") == "tiny"


def test_the_bundled_model_table_only_names_tiny(monkeypatch):
    """Only tiny is bundled, and the table is what the resolver keys off. A
    second entry here without the weights beside it would break that size for
    every user."""
    assert set(BUNDLED_MODELS) == {"tiny"}
