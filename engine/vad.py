"""Pluggable VAD backends with score caching and post-hoc segmentation.

VAD is an independent audio-truth signal. Scoring is decoupled from
segmentation: raw per-frame probabilities are computed once and cached;
threshold/hysteresis/padding/min-speech/min-silence are applied later
without re-running the model.

Backend hierarchy as actually selected (see ``get_vad_backend``):
  ├── SileroVADBackend        (the intended preferred backend; **not
  |                           implemented**, so this leg never fires and the
  |                           chain always starts at energy)
  ├── EnergyVAD               (the effective default on a normal host)
  ├── FFmpegVADBackend        (always available fallback)
  └── WhisperVADBackend       (last-resort, uses transcript word timestamps)

``SileroVADBackend`` is kept in the registry so that asking for it by name
reports "not implemented" rather than "unknown backend".
"""
from __future__ import annotations

import json
import shutil
import subprocess
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from providers.audio import extract_pcm_wav, AudioReadError
from providers.ffmpeg import run_capture

log = logging.getLogger(__name__)

FRAME_MS = 32  # Silero V5 fixed window: 512 samples at 16kHz = 32ms (matches rust core)


@dataclass
class SpeechRegion:
    start: float
    end: float
    score: float = 1.0


@dataclass
class SilenceRegion:
    start: float
    end: float
    duration: float


@dataclass
class VADResult:
    scores: list[float]
    sample_rate: int
    backend: str
    frame_ms: int
    duration: float
    vad_model: str | None = None
    hysteresis: dict | None = None
    fallback_reason: str | None = None


class VADBackend:
    name = "base"

    def is_available(self) -> bool:
        return True

    def analyze(self, audio: Path) -> VADResult:
        raise NotImplementedError


def _conforming_wav(wav: Path, sample_rate: int) -> Path:
    """Return a mono ``sample_rate`` Hz 16-bit PCM WAV for ``wav``.

    Converts in place (to a sibling file) only when the source is not already
    what the scorer needs. Non-PCM containers are handled by ffmpeg, which
    downmixes and resamples; multichannel PCM is averaged to mono rather than
    dropping channels, so a stereo recording does not lose a speaker on one
    side of the mix.
    """
    import wave

    try:
        with wave.open(str(wav), "rb") as f:
            channels = f.getnchannels()
            rate = f.getframerate()
            width = f.getsampwidth()
    except (OSError, ValueError):
        # Not a WAV the stdlib can open at all — let ffmpeg decode it.
        channels, rate, width = 0, 0, 0

    if channels == 1 and rate == sample_rate and width == 2:
        return wav
    converted = wav.parent / f"{wav.stem}_{sample_rate}hz_mono.wav"
    if not converted.is_file():
        extract_pcm_wav(wav, converted, sample_rate)
    return converted


# ── Energy-based VAD (pure numpy, deterministic, no deps) ──

class EnergyVAD(VADBackend):
    name = "energy"
    vad_model = "energy-rms"

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = FRAME_MS,
        base_threshold: float = 0.02,
        speech_ceiling: float = 0.08,
        zcr_min: float = 0.02,
        zcr_max: float = 0.45,
        fallback_reason: str | None = None,
    ):
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.base_threshold = base_threshold
        self.speech_ceiling = speech_ceiling
        self.zcr_min = zcr_min
        self.zcr_max = zcr_max
        self.fallback_reason = fallback_reason

    def _conforming_path(self, audio: Path) -> Path:
        """Resolve ``audio`` to a mono ``sample_rate`` Hz 16-bit PCM WAV.

        The pipeline always extracts 16 kHz mono itself, but a WAV handed in
        directly (the web shell, a user drop) is routinely stereo or 44.1/48
        kHz. Downmixing and resampling is correct; refusing used to fail the
        whole analysis on a perfectly good file.
        """
        wav = Path(str(audio))
        if wav.suffix.lower() != ".wav":
            probe = wav.parent / f"{wav.stem}_probe.wav"
            extract_pcm_wav(audio, probe, self.sample_rate)
            wav = probe
        return _conforming_wav(wav, self.sample_rate)

    def _score_frames(self, frames: np.ndarray) -> np.ndarray:
        """Score full frames: one value per row, the cadence contract below."""
        rms = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1) + 1e-8)
        signs = np.signbit(frames)
        zcr_full = np.abs(np.diff(signs.astype(np.int8), axis=1)).sum(axis=1)
        zcr = zcr_full / max(1, frames.shape[1] - 1)
        scores = np.clip((rms - self.base_threshold) / (self.speech_ceiling - self.base_threshold), 0.0, 1.0)
        scores[(zcr < self.zcr_min) | (zcr > self.zcr_max)] *= 0.3
        return scores

    def analyze(self, audio: Path) -> VADResult:
        wav = self._conforming_path(audio)
        frame = int(self.sample_rate * self.frame_ms / 1000)
        # Cadence contract (docs/autocut-parity.md): one score per 512-sample
        # (32ms) chunk, trailing samples short of a full chunk are dropped —
        # same grid as the Rust `score_energy` scorer, so `segment_from_scores`
        # reads either scorer's output identically. Only a sub-frame input
        # (<32ms) is padded up to a single frame so the score array is never
        # empty for downstream division.
        import wave

        # Scoring a window at a time rather than reading the file first: the
        # whole point of chunked transcription is that a long source never has
        # to be resident, and reading it end to end made the decoded audio the
        # biggest allocation in the process. Measured (forked children, peak
        # RSS) on the largest committed source, 55.6 min / 214 MB of float32:
        # the old whole-file read peaked at 533 MiB and this reader at 43 MiB.
        # The score array is one float per 32 ms, so it stays the only
        # full-length object; the audio in memory is bounded by one window plus
        # one frame of carry. ``_WindowedWav`` in providers/sherpa.py exists for
        # the same reason on the transcription path, and the two deliberately do
        # not share a class — this one scores while it reads and keeps the
        # padding rule here.
        window = max(frame * 4, self.sample_rate * 60)
        scores: list[float] = []
        carry = np.zeros(0, dtype=np.float32)
        total = 0
        try:
            with wave.open(str(wav), "rb") as f:
                total = f.getnframes()
                pos = 0
                while pos < total:
                    want = min(window, total - pos)
                    f.setpos(pos)
                    raw = f.readframes(want)
                    data = np.concatenate([carry, np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0])
                    usable = (len(data) // frame) * frame
                    if usable:
                        scores.extend(self._score_frames(data[:usable].reshape(-1, frame)).tolist())
                    carry = data[usable:]
                    pos += want
        except (OSError, ValueError, EOFError, wave.Error) as exc:
            # The same damage ``peak_dbfs`` survives: a truncated or headerless
            # extraction raises one of these, and none is an OSError, so a
            # caller that degrades on OSError would crash here instead. Routed
            # through AudioReadError so every reader of an extraction reports
            # the same thing for the same damage.
            raise AudioReadError(f"cannot read {wav.name} as a WAV: "
                                 f"{type(exc).__name__}: {exc}") from exc
        if total < frame:
            # Sub-frame input: padded to one score, as above.
            scores = self._score_frames(
                np.pad(carry, (0, frame - len(carry))).reshape(1, frame)
            ).tolist()
        return VADResult(
            scores=scores,
            sample_rate=self.sample_rate,
            backend=self.name,
            frame_ms=self.frame_ms,
            duration=total / self.sample_rate,
            vad_model=self.vad_model,
            fallback_reason=self.fallback_reason,
        )


# ── FFmpeg silencedetect fallback ──

class FFmpegVADBackend(VADBackend):
    name = "ffmpeg"
    vad_model = "silencedetect"

    def __init__(self, noise_db: float = -35.0, sample_rate: int = 16000, frame_ms: int = FRAME_MS,
                 fallback_reason: str | None = None):
        self.noise_db = noise_db
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.fallback_reason = fallback_reason

    def is_available(self) -> bool:
        return shutil.which("ffmpeg") is not None

    def analyze(self, audio: Path) -> VADResult:
        wav = Path(str(audio))
        if wav.suffix.lower() != ".wav":
            probe = wav.parent / f"{wav.stem}_probe.wav"
            extract_pcm_wav(audio, probe, self.sample_rate)
            wav = probe
        from providers.ffmpeg import probe_duration
        duration = probe_duration(wav)
        cmd = [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(wav),
            "-af", f"silencedetect=noise={self.noise_db}dB:d=0.2", "-f", "null", "-",
        ]
        proc = run_capture(cmd, stage="vad-ffmpeg", source=str(wav))
        # silencedetect prints silence_start, then silence_end, per silence run.
        # A run that is still open at EOF prints no silence_end, so it must be
        # closed here — otherwise a file whose tail is silent scores as speech
        # all the way to the end and the trailing dead air is never reported.
        # A start and its end can also land on one line for very short runs, so
        # both markers are parsed per line rather than elif-chained.
        starts: list[float] = []
        ends: list[float] = []
        for line in proc.stderr.splitlines():
            if "silence_start:" in line:
                starts.append(float(line.split("silence_start:")[1].strip()))
            if "silence_end:" in line:
                ends.append(float(line.split("silence_end:")[1].split("|")[0].split()[0]))
        sil_ranges: list[tuple[float, float]] = []
        pending = list(starts)
        for e in ends:
            if pending:
                sil_ranges.append((pending.pop(0), e))
        if pending:
            sil_ranges.append((pending[-1], duration))
        # Build per-frame scores: 1.0 in speech, 0.0 in silence (uniform schema).
        frame = self.frame_ms / 1000.0
        n = max(1, int(np.ceil(duration / frame)))
        scores = np.ones(n, dtype=np.float32)
        for s, e in sil_ranges:
            si = max(0, int(s / frame))
            ei = min(n, int(np.ceil(e / frame)) + 1)
            scores[si:ei] = 0.0
        return VADResult(
            scores=scores.tolist(),
            sample_rate=self.sample_rate,
            backend=self.name,
            frame_ms=self.frame_ms,
            duration=duration,
            vad_model=self.vad_model,
            fallback_reason=self.fallback_reason,
        )


# ── Whisper timestamps last-resort ──

class WhisperVADBackend(VADBackend):
    """Speech presence inferred from transcript word timings, not from audio.

    This is a last-resort backend, selected only when silero, energy and ffmpeg
    are all unavailable. ``analyze`` deliberately does not read its ``audio``
    argument: it marks a frame as speech iff a transcribed word covers it. It
    therefore cannot find speech the transcriber missed, and on a source with a
    music bed under no speech it reports the bed as silence. Both limits are
    why the backends above are preferred, and both are recorded in the audio
    summary via ``fallback_reason``.
    """
    name = "whisper"
    vad_model = "whisper-wordtimestamps"

    def __init__(self, words: list, sample_rate: int = 16000, frame_ms: int = FRAME_MS, duration: float = 0.0,
                 fallback_reason: str | None = None):
        self.words = words
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.duration = duration
        self.fallback_reason = fallback_reason

    def analyze(self, audio: Path) -> VADResult:
        frame = self.frame_ms / 1000.0
        total = self.duration or (max((w.end for w in self.words), default=0.0) + 0.5)
        n = max(1, int(total / frame))
        scores = np.zeros(n, dtype=np.float32)
        for w in self.words:
            s = max(0, int(w.start / frame))
            e = min(n, int(w.end / frame) + 1)
            scores[s:e] = 1.0
        return VADResult(
            scores=scores.tolist(),
            sample_rate=self.sample_rate,
            backend=self.name,
            frame_ms=self.frame_ms,
            duration=total,
            vad_model=self.vad_model,
            fallback_reason=self.fallback_reason,
        )


class SileroVADBackend(VADBackend):
    """Silero V5 via onnxruntime — the intended preferred backend, **not
    implemented**.

    The model is never loaded and ``analyze`` cannot run, so ``is_available``
    reports False whatever the environment has installed. It stays in the
    registry for a reason: a user who asks for it by name should hear "not
    implemented", not "unknown backend", and the selection chain needs
    something concrete to name when it explains why it fell back.

    Note the asymmetry with the other backends: those are unavailable because
    of this *host* (no ffmpeg, no transcript), which is fixable by installing
    something; this one is unavailable because the code does not exist. That
    is why its forced-selection error carries a different message.
    """

    name = "silero"
    vad_model = "silero-v5"

    def __init__(self, model_path: str | None = None):
        self.model_path = model_path

    def is_available(self) -> bool:
        # Deliberately not "can onnxruntime be imported": the inference path
        # does not exist, so reporting available here would hand a caller a
        # backend whose analyze() raises. When the backend is implemented this
        # becomes the real check against onnxruntime and the model file.
        return False

    def analyze(self, audio: Path) -> VADResult:
        raise NotImplementedError(
            "the silero VAD backend is not implemented: analyze() is a "
            "placeholder. The selection chain falls back to energy, ffmpeg or "
            "whisper automatically; pass one of those by name to choose."
        )


_BACKENDS: dict[str, type[VADBackend]] = {
    "silero": SileroVADBackend,
    "energy": EnergyVAD,
    "ffmpeg": FFmpegVADBackend,
    "whisper": WhisperVADBackend,
}


def get_vad_backend(force: str | None = None,
                    words: list | None = None,
                    duration: float = 0.0) -> VADBackend:
    """Instantiate the best available backend.

    Preference: silero → energy (best available on this device) → ffmpeg
    → whisper. `force` overrides to a specific named backend.
    """
    if force:
        if force not in _BACKENDS:
            raise ValueError(f"unknown VAD backend '{force}'; choose from {sorted(_BACKENDS)}")
        backend = _BACKENDS[force]
        if force == "whisper":
            instance: VADBackend = backend(words or [], duration=duration, fallback_reason="forced")
        elif force == "silero":
            instance = backend()
        else:
            instance = backend(fallback_reason="forced")
        if not instance.is_available():
            if force == "silero":
                # A different message from the other backends because it is a
                # different condition: they are missing on this host, this one
                # is missing from the codebase. Saying "unavailable" here would
                # send a user to install something that cannot help.
                raise RuntimeError(
                    "the silero VAD backend is not implemented; "
                    "use --vad-backend energy, ffmpeg or whisper"
                )
            raise RuntimeError(f"VAD backend '{force}' unavailable")
        return instance

    if SileroVADBackend().is_available():
        log.info("selecting Silero VAD backend")
        return SileroVADBackend()
    if EnergyVAD().is_available():
        reason = "silero unavailable: onnxruntime/model not present"
        log.info("selecting energy VAD backend (%s)", reason)
        return EnergyVAD(fallback_reason=reason)
    if FFmpegVADBackend().is_available():
        reason = "silero and energy unavailable"
        log.info("selecting FFmpeg silencedetect VAD backend (%s)", reason)
        return FFmpegVADBackend(fallback_reason=reason)
    reason = "silero, energy and ffmpeg unavailable"
    log.info("selecting Whisper-timestamp VAD backend (last resort: %s)", reason)
    return WhisperVADBackend(words or [], duration=duration, fallback_reason=reason)


# ── Segmentation (post-hoc, deterministic) ──

def segment_from_scores(
    scores: list[float] | np.ndarray,
    sample_rate: int = 16000,
    frame_ms: int = FRAME_MS,
    *,
    threshold: float = 0.5,
    release: float | None = None,
    min_speech_ms: int = 150,
    min_silence_ms: int = 100,
    time_offset: float = 0.0,
) -> list[SpeechRegion]:
    """Convert per-frame speech probabilities into speech regions.

    AutoCut-exact hysteresis segmentation (parity with `rust/core/src/vad.rs`):
    speech starts when a frame reaches `threshold` and continues while frames
    stay at or above `release = max(threshold - 0.15, 0.05)`; runs are grouped,
    gaps strictly shorter than `min_silence_ms` are merged, runs shorter than
    `min_speech_ms` are dropped. Pure function — given the same cached scores +
    params, output is identical, and it is cadence-independent: it operates on
    the score's own frame grid.

    `time_offset` is added to every region boundary, mirroring the Rust
    `time_offset` parameter: when detection scores a windowed slice of the
    audio (a linked track that rolled after the reference), the regions must
    still land in project time.
    """
    arr = np.asarray(scores, dtype=np.float32)
    frame_dur = frame_ms / 1000.0
    if release is None:
        release = max(threshold - 0.15, 0.05)

    active = False
    active_start = 0
    active_end = 0
    speech: list[tuple[float, float]] = []

    for i, s in enumerate(arr):
        if not active and s >= threshold:
            active = True
            active_start = i
            active_end = i
        elif active:
            if s >= release:
                active_end = i
            else:
                # potential end; look ahead for hysteresis
                speech.append((active_start, active_end))
                active = False

    if active:
        speech.append((active_start, active_end))

    # merge short silences
    merged: list[tuple[float, float]] = []
    for start, end in speech:
        s = start * frame_dur
        e = (end + 1) * frame_dur
        if not merged:
            merged.append((s, e))
            continue
        if s - merged[-1][1] < min_silence_ms / 1000.0:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # drop short speech
    result = [
        SpeechRegion(time_offset + s, time_offset + e)
        for s, e in merged
        if (e - s) >= min_speech_ms / 1000.0
    ]
    return result


def segment_with_meta(
    scores: list[float] | np.ndarray,
    sample_rate: int = 16000,
    frame_ms: int = FRAME_MS,
    **kwargs,
) -> tuple[list[SpeechRegion], dict]:
    """`segment_from_scores` plus the report record: which segmentation params
    (threshold/release/min-speech/min-silence) actually ran and what came out.
    The pipeline stamps this onto the analysis report for provenance."""
    threshold = kwargs.get("threshold", 0.5)
    release = kwargs.get("release")
    if release is None:
        release = max(threshold - 0.15, 0.05)
    min_speech_ms = kwargs.get("min_speech_ms", 150)
    min_silence_ms = kwargs.get("min_silence_ms", 100)
    time_offset = kwargs.get("time_offset", 0.0)
    regions = segment_from_scores(scores, sample_rate, frame_ms, **kwargs)
    meta = {
        "hysteresis": {
            "threshold": threshold,
            "release": round(release, 4),
            "min_speech_ms": min_speech_ms,
            "min_silence_ms": min_silence_ms,
        },
        "frame_ms": frame_ms,
        "n_frames": int(np.asarray(scores).size),
        "time_offset": time_offset,
        "speech_regions": len(regions),
        "speech_seconds": round(sum(r.end - r.start for r in regions), 3),
    }
    return regions, meta


def silence_from_speech(
    regions: list[SpeechRegion], duration: float
) -> list[SilenceRegion]:
    """Complement of speech regions within [0, duration]."""
    if not regions:
        return [SilenceRegion(0.0, duration, duration)]
    sil: list[SilenceRegion] = []
    if regions[0].start > 0:
        sil.append(SilenceRegion(0.0, regions[0].start, round(regions[0].start, 3)))
    for a, b in zip(regions, regions[1:]):
        if b.start - a.end > 0:
            sil.append(SilenceRegion(round(a.end, 3), round(b.start, 3), round(b.start - a.end, 3)))
    if regions[-1].end < duration:
        sil.append(SilenceRegion(round(regions[-1].end, 3), round(duration, 3), round(duration - regions[-1].end, 3)))
    return sil


# ── score persistence (cached analysis) ──

def save_scores(result: VADResult, path: Path) -> None:
    """Persist cached VAD scores. Segmentation params are applied later."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    payload = {
        "sample_rate": result.sample_rate,
        "backend": result.backend,
        "frame_ms": result.frame_ms,
        "duration": round(result.duration, 3),
        "n_frames": len(result.scores),
        "scores": result.scores,
    }
    if result.vad_model is not None:
        payload["vad_model"] = result.vad_model
    if result.hysteresis is not None:
        payload["hysteresis"] = result.hysteresis
    if result.fallback_reason is not None:
        payload["fallback_reason"] = result.fallback_reason
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def load_scores(path: Path) -> VADResult | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return VADResult(
        scores=payload.get("scores", []),
        sample_rate=payload.get("sample_rate", 16000),
        backend=payload.get("backend", "unknown"),
        frame_ms=payload.get("frame_ms", FRAME_MS),
        duration=payload.get("duration", 0.0),
        vad_model=payload.get("vad_model"),
        hysteresis=payload.get("hysteresis"),
        fallback_reason=payload.get("fallback_reason"),
    )