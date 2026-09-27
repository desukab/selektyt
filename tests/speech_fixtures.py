"""The one synthetic signal every VAD backend in the chain can hear.

Several suites need a short media file that counts as speech to whichever
backend the chain selects on this host. A pure tone does not work, and the
reason is a property of the model rather than of any harness: silero was
trained on voices and is entitled to score a tone as non-speech — a 220 Hz
sine scores a maximum of 0.503, which yields **zero speech regions at every
threshold**, so any test needing a selection dies with "nothing to keep". The
energy and ffmpeg backends score that same tone 1.0 throughout, so a tone
fixture passed for as long as one of them was preferred and broke the day the
chain preferred a neural model.

The measured design space (``tools/`` probe, 2026-09-27):

    shape                              energy   ffmpeg   silero
    220 Hz tone                            100%     100%      0.0%   <- scored, never segmented
    continuous voiced stack                  8.8%    46.5%      0.2%
    + 4 Hz syllable envelope                 8.8%    46.5%      8.8%
    + 0.5 s on / 0.5 s off gating           49.6%    43.5%     52.5%   <- this shape

Only the gated shape is segmented as speech by all three at once, and the
gating is a real cue — bursty speech with pauses between syllables is closer to
what a VAD is for than a continuous drone, so this is a fixture rather than a
hack to defeat a test.

The ``amp`` dial is the stand-in for a quiet recording, and it separates the
backends cleanly:

    amp 0.9      mean -18 dBFS   energy 49.6%  ffmpeg 43.5%  silero 52.5%
    amp 0.004    mean -65 dBFS   energy  0.0%  ffmpeg  0.0%  silero 60.8%
    amp 0.0001   mean -97 dBFS   energy  0.0%  ffmpeg  0.0%  silero  0.0%

The middle row is the corpus's quiet-source shape: a level-blind backend finds
no speech against a good transcript, while a model that listens does. The last
row is blind to every backend the chain can select, which makes it the one
host-independent way to reach "the ratio has no denominator".

What this is not: real speech. A model's score on this signal says nothing
about its accuracy on a voice. Use it where the property under test is the
plumbing — provenance, cache keying, threshold reach, coverage arithmetic —
not where it is the model's judgment.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

_SR = 16000
_F0_HZ = 120.0
_HARMONICS = 8
_SYLLABLE_HZ = 4.0
_SYLLABLE_DEPTH = 0.8

# The numbers the table above was measured at, kept as defaults so a suite that
# only needs *a* speech file does not have to re-derive a working shape.
LOUD_AMP = 0.9          # heard by every backend in the chain
QUIET_AMP = 0.004       # heard only by a model that listens (silero)
INAUDIBLE_AMP = 0.0001  # heard by no backend


def speech_signal(seconds: float, amp: float = LOUD_AMP,
                  on: float = 0.5, off: float = 0.5,
                  rate: int = _SR) -> np.ndarray:
    """Voiced bursts separated by silence, peak-normalised to ``amp``.

    Peak-normalisation is not optional: the harmonic stack's peak is not
    predictable from the harmonic count, and the energy backend compares frame
    RMS against fixed absolute thresholds, so an unnormalised level leaves
    whether this yields any speech to chance on the one host whose preferred
    backend is level-blind.
    """
    n = int(seconds * rate)
    t = np.arange(n, dtype=np.float32) / rate
    v = np.zeros(n, dtype=np.float32)
    for k in range(1, _HARMONICS + 1):
        v += np.sin(2 * np.pi * k * _F0_HZ * t)
    v /= _HARMONICS
    v *= 1.0 - _SYLLABLE_DEPTH + _SYLLABLE_DEPTH * np.sin(
        2 * np.pi * _SYLLABLE_HZ * t
    )
    gate = ((t % (on + off)) < on).astype(np.float32)
    samples = v * gate
    peak = float(np.max(np.abs(samples)))
    if peak > 0:
        samples = samples * (amp / peak)
    return samples


def write_speech_wav(path: Path, seconds: float = 2.0, amp: float = LOUD_AMP,
                     rate: int = _SR) -> Path:
    """``speech_signal`` written as 16 kHz mono int16 PCM at ``path``."""
    samples = speech_signal(seconds, amp=amp, rate=rate)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes((samples * 32767.0).astype(np.int16).tobytes())
    return path
