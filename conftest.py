"""Suite-wide fixtures for the media-dependent validation tests.

Several tests validate the audio stack against real recordings. Real speech is
the point of them and none of it is redistributable with the repo, so each one
names exactly what un-skips it in its own skip reason rather than hiding behind
a bare "not present" — see tests/test_proxy_validation.py for the measurements
that stay opt-in, and tests/test_sidecar_parity.py for the one that does not.

That one is different. ``test_parity_on_real_media`` cross-checks the Rust VAD
sidecar against the Python engine, and the contract it asserts — identical
score cadence over the same file, and identical segmentation of identical
scores — is fully exercised by *any* 16 kHz mono WAV, real or synthetic. Real
speech is still the harder case, so ``SELEKYT_PARITY_MEDIA`` is honoured when it
points at a real recording; when it does not, ``parity_media`` generates a
deterministic synthetic one once per session so the contract is checked on every
CI run instead of silently skipping on every machine that lacks a corpus.
"""
from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np
import pytest

_SR = 16000

# EnergyVAD scores a frame by clipping its RMS into a [0.02, 0.08) window and
# multiplies any frame whose zero-crossing rate falls outside [0.02, 0.45] by
# 0.3. Broadband noise fails the ZCR band (it sits near 0.5) and a clean tone
# never goes quiet, so neither would give the segmentation anything to find. A
# harmonic stack under a syllable-rate envelope, alternated with real silence,
# lands inside both windows and produces genuine on/off transitions — the
# segmentation has regions to agree about, so the parity assertion is not
# vacuous.
_F0 = 190.0
_HARMONICS = ((1, 0.55), (2, 0.30), (3, 0.18), (4, 0.10))
_SYLLABLE_HZ = 4.0
_PHRASES = 4
_PHRASE_S = 1.2
_GAP_S = 0.8
_SEED = 1234


@pytest.fixture(autouse=True)
def _isolate_selekyt_env():
    """Restore every ``SELEKYT_*`` environment variable after each test.

    Production writes into the process environment and reads it back later —
    ``selekyt.cli.main`` exports the chosen caption style this way because the
    render cache key reads it from there, and the sidecar's
    ``set_caption_style`` does the same and restores the previous value itself.
    A test that drives ``main()`` in-process therefore mutates the environment
    of every test after it, and monkeypatch only restores a key it was told
    about. Two tests were not told.

    That leaked ``SELEKYT_CAPTION_STYLE`` and it silently broke a third test:
    run after the flag-forwarding one, ``test_valid_cached_render_is_reused``
    found a cache written under ``minimal`` with the environment still saying
    ``punch``, concluded the render was stale and re-encoded it, and failed on
    an assertion about exactly that. The full suite stayed green because its
    ordering never put the two tests together — the failure only appeared when
    the module was run on its own. Owning the namespace here is what makes the
    suite order-independent for this whole class, not just for the one test.
    """
    before = {k: v for k, v in os.environ.items() if k.startswith("SELEKYT_")}
    yield
    for k in list(os.environ):
        if k.startswith("SELEKYT_") and k not in before:
            del os.environ[k]
    for k, v in before.items():
        if os.environ.get(k) != v:
            os.environ[k] = v


def _synthetic_speech_pcm(seconds: float, seed: int = _SEED) -> np.ndarray:
    """Deterministic speech-like 16 kHz mono samples."""
    rng = np.random.default_rng(seed)
    n = int(seconds * _SR)
    t = np.arange(n) / _SR
    voiced = np.zeros(n, dtype=np.float64)
    for mult, amp in _HARMONICS:
        voiced += amp * np.sin(2 * np.pi * _F0 * mult * t)
    # A syllable-rate envelope only where a phrase is speaking; silence
    # between phrases is real digital silence, not low-level noise.
    envelope = np.zeros(n, dtype=np.float64)
    for k in range(_PHRASES):
        start = _GAP_S / 2 + k * (_PHRASE_S + _GAP_S)
        m = (t >= start) & (t < start + _PHRASE_S)
        if not m.any():
            continue
        envelope[m] = 0.5 + 0.5 * np.sin(2 * np.pi * _SYLLABLE_HZ * (t[m] - start))
    signal = voiced * envelope
    # A whisper of dither keeps consecutive frames from being bit-identical,
    # the way real microphone noise would, without approaching the RMS window.
    signal = signal + rng.standard_normal(n) * 1e-4
    return np.clip(signal, -1.0, 1.0)


def _write_wav(path: Path, pcm: np.ndarray, sample_rate: int = _SR) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes((np.asarray(pcm * 32767.0, dtype="<i2")).tobytes())
    return path


@pytest.fixture(scope="session")
def parity_media(tmp_path_factory) -> Path:
    """A 16 kHz mono WAV to cross-check the Rust and Python VAD paths.

    ``SELEKYT_PARITY_MEDIA`` wins when it names an existing file — point it at
    a real recording to exercise the harder, real-speech case. Otherwise a
    deterministic synthetic recording is generated once per session.
    """
    env = os.environ.get("SELEKYT_PARITY_MEDIA")
    if env and Path(env).is_file():
        return Path(env)
    directory = tmp_path_factory.mktemp("parity-media")
    seconds = _GAP_S / 2 + _PHRASES * (_PHRASE_S + _GAP_S)
    return _write_wav(directory / "synthetic-speech.wav",
                      _synthetic_speech_pcm(seconds))
