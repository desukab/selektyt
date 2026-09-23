"""Transcript coverage: does the transcript describe the audio it claims to?

The corpus shipped one clip from ``telugu-tutorial`` as an OK deliverable whose
transcript covers **4.5%** of a 339.6 s file that is **73%** speech-energy
audio. It passed every audit the pipeline runs, because every one of them
compares the transcript against itself, against the selection, or against the
render — never against the audio. The transcript was well-formed, non-empty,
timestamped and in the language that was asked for, so there was nothing for a
mechanical check to object to. A user would have shipped wrong captions and
read a success message.

This module is the missing comparison. It is deliberately a *completeness*
check, not a correctness one: it cannot tell whether the Telugu words are the
right words — only a speaker of the language can — but it can tell that the
transcript claims a small fraction of audio that demonstrably carries speech.

Measured across all 11 corpus sources (``tools/coverage_measure.py``,
``corpus/wer/runs/coverage-2026-09-19.json``), the ratio of transcript coverage
to speech-energy fraction separates the one failure cleanly:

    telugu-tutorial   vad 72.6%  asr  4.5%  ratio 0.062   <- the failure
    royal-society-... vad 71.7%  asr 93.3%  ratio 1.302   <- lowest good source

Nothing lies between them; the margin is 21x. A cut point anywhere in that gap
works, and ``COLLAPSE_RATIO`` is placed conservatively inside it with 4x of
room below and 5x above. With one failure and nine good sources this is not a
calibrated threshold and is not presented as one — it is a cut point inside a
measured empty interval.

Two properties the measurement forced on the design:

* **It is one-directional.** The ratio is only meaningful when the transcript
  covers *less* than the speech energy. ``hadhazy-protest`` returns a VAD
  fraction of 0% against an ASR coverage of 87% — the energy VAD's absolute
  threshold is blind to a mean of −43.3 dBFS, so the VAD is the broken
  instrument there, not the transcript. A symmetric check would have failed a
  source whose transcript is fine. ``pentagon-esper-briefing`` is the same
  shape at 0.4% VAD against 82% ASR.
* **It degrades instead of passing.** Below ``MIN_VAD_FRACTION`` the VAD found
  too little speech to be a denominator, so the ratio is meaningless. The
  verdict becomes ``not_computable`` and is recorded as such — a check that
  reports *pass* when it could not measure anything is the failure mode this
  module exists to remove.

What this still cannot see: an energy VAD cannot tell speech from music, so a
source that is mostly score with a little dialogue has a high speech-energy
fraction and a legitimately low transcript coverage and would be flagged. The
error cost is asymmetric and intentionally so — a false positive is a loud
message asking the user to verify, while a false negative ships wrong captions
silently. The pipeline raises rather than warns for the same reason the
empty-transcript guard does.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from pathlib import Path

from engine.vad import EnergyVAD, segment_with_meta
from providers.audio import extract_pcm_wav, _read_mono_pcm, AudioReadError

FRAME_MS = 32  # EnergyVAD's fixed window; the score grid the segments read.

# A VAD fraction below this leaves the ratio without a trustworthy denominator.
# Measured: the two sources in this regime (hadhazy-protest 0.0%,
# pentagon-esper-briefing 0.4%) both have good transcripts and broken VAD
# reads, so declining to compute is the correct answer, not a pass.
MIN_VAD_FRACTION = 0.10

# Transcript coverage below this multiple of the speech-energy fraction is a
# collapse. Sits inside the measured empty interval [0.062, 1.302].
COLLAPSE_RATIO = 0.25


@dataclass
class CoverageReport:
    duration_s: float
    vad_speech_s: float
    vad_speech_frac: float
    asr_speech_s: float
    asr_speech_frac: float
    verdict: str  # ok | coverage_collapse | not_computable | not_measured
    detail: str
    coverage_ratio: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _dbfs(amplitude: float) -> float:
    if amplitude <= 0.0:
        return -math.inf
    return 20.0 * math.log10(amplitude)


def _read_mono(path: Path, sample_rate: int = 16000) -> tuple[list[float], int]:
    """Mono float PCM on the VAD's own grid.

    Reuses ``EnergyVAD``'s conforming reader indirectly by reading the same way
    it does, so the frames measured here are the frames the VAD scores rather
    than a second, slightly different extraction of the same source.

    The WAV itself is read through :func:`providers.audio._read_mono_pcm`, the
    single place that turns stdlib reader failures into ``AudioReadError``.
    Coverage is an audit, not a decode: an extraction that cannot be parsed has
    to become ``not_measured`` here, not a crash, because crashing the run would
    replace an honest "I could not check this" with a stack trace the user
    cannot act on.
    """
    wav = path
    if wav.suffix.lower() != ".wav":
        probe = wav.parent / f"{wav.stem}_probe.wav"
        extract_pcm_wav(wav, probe, sample_rate)
        wav = probe
    data, rate = _read_mono_pcm(wav)
    if rate != sample_rate:
        raise ValueError(f"{path} is {rate} Hz, expected {sample_rate}; the "
                         "fractions would be computed on the wrong grid")
    return data.tolist(), rate


def _rms_frames(data: list[float], sample_rate: int) -> list[float]:
    import numpy as np

    frame = int(sample_rate * FRAME_MS / 1000)
    n_frames = max(1, len(data) // frame)
    arr = np.asarray(data[: n_frames * frame], dtype=np.float32).reshape(n_frames, frame)
    return np.sqrt(np.mean(arr ** 2, axis=1) + 1e-8).tolist()


def _union_span(spans: list[tuple[float, float]]) -> float:
    """Unioned, not summed: adjacent segments overlap at word boundaries, so
    summing would count the same audio twice and overstate the coverage."""
    total = 0.0
    last = -math.inf
    for start, end in sorted(spans):
        if end <= start:
            continue
        total += max(0.0, end - max(start, last))
        last = max(last, end)
    return total


def measure(audio: Path, segments: list[dict]) -> dict:
    """The raw numbers, with no verdict attached.

    ``segments`` are ``transcript.json`` records carrying ``start``/``end``.
    Kept free of any pass/fail judgement so ``tools/coverage_measure.py`` can
    report the distribution across a corpus without inheriting a threshold.
    """
    data, rate = _read_mono(audio)
    duration = len(data) / rate
    rms = _rms_frames(data, rate)
    peak = max(rms) if rms else 0.0
    mean = sum(rms) / len(rms) if rms else 0.0

    vad = EnergyVAD().analyze(audio)
    _regions, meta = segment_with_meta(vad.scores, sample_rate=rate, frame_ms=FRAME_MS,
                                       threshold=0.5, min_speech_ms=150,
                                       min_silence_ms=100)
    vad_seconds = meta["speech_seconds"]
    asr_seconds = _union_span([(s["start"], s["end"]) for s in segments])

    vad_frac = vad_seconds / duration if duration > 0 else 0.0
    asr_frac = asr_seconds / duration if duration > 0 else 0.0
    return {
        "duration_s": round(duration, 3),
        "peak_dbfs": round(_dbfs(peak), 2),
        "mean_rms_dbfs": round(_dbfs(mean), 2),
        "vad_backend": vad.backend,
        "vad_speech_s": round(vad_seconds, 3),
        "vad_speech_frac": round(vad_frac, 4),
        "asr_speech_s": round(asr_seconds, 3),
        "asr_speech_frac": round(asr_frac, 4),
        "coverage_ratio": round(asr_frac / vad_frac, 4) if vad_frac > 0 else None,
    }


def check(audio: Path, segments: list[dict]) -> CoverageReport:
    """Compare what the transcript claims against what the audio contains.

    Raises nothing; the caller decides whether a ``coverage_collapse`` verdict
    aborts the run. The pipeline aborts, matching the empty-transcript guard,
    because a silent wrong deliverable is worse than a loud objection.
    """
    try:
        m = measure(audio, segments)
    except FileNotFoundError:
        return CoverageReport(0.0, 0.0, 0.0, 0.0, 0.0, "not_measured",
                              "the extracted audio is missing, so coverage "
                              "could not be measured")
    except (OSError, ValueError, AudioReadError) as exc:
        return CoverageReport(0.0, 0.0, 0.0, 0.0, 0.0, "not_measured",
                              f"coverage could not be measured: {type(exc).__name__}: {exc}")

    ratio = m["coverage_ratio"]
    vad_frac = m["vad_speech_frac"]
    asr_frac = m["asr_speech_frac"]

    # An empty transcript is the empty-guard's case, not this one; reporting a
    # collapse here would duplicate that message with worse numbers.
    if asr_frac <= 0.0:
        verdict, detail = "ok", "no transcript spans to compare"
    elif ratio is None or vad_frac < MIN_VAD_FRACTION:
        # Not a pass. Recording it as one is exactly the green-without-measuring
        # failure the rest of the audit is about; the verdict says why.
        verdict = "not_computable"
        detail = (f"the energy VAD found {vad_frac * 100:.1f}% speech, below the "
                  f"{MIN_VAD_FRACTION * 100:.0f}% floor, so the ratio has no "
                  "trustworthy denominator — the VAD is likely blind to a quiet "
                  "source, not the transcript empty")
    elif ratio < COLLAPSE_RATIO:
        verdict = "coverage_collapse"
        detail = (f"the transcript covers {asr_frac * 100:.1f}% of a "
                  f"{m['duration_s']:.0f}s source that is {vad_frac * 100:.1f}% "
                  f"speech-energy audio (ratio {ratio:.3f}, below "
                  f"{COLLAPSE_RATIO}). The transcript is well-formed, which is why "
                  "nothing else flagged it — verify it against the audio before "
                  "shipping anything cut from this run")
    else:
        verdict = "ok"
        detail = (f"transcript covers {asr_frac * 100:.1f}% against "
                  f"{vad_frac * 100:.1f}% speech energy (ratio {ratio:.3f})")

    return CoverageReport(m["duration_s"], m["vad_speech_s"], vad_frac,
                          m["asr_speech_s"], asr_frac, verdict, detail,
                          coverage_ratio=ratio)
