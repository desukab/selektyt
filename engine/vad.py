"""Pluggable VAD backends with score caching and post-hoc segmentation.

VAD is an independent audio-truth signal. Scoring is decoupled from
segmentation: raw per-frame probabilities are computed once and cached;
threshold/hysteresis/padding/min-speech/min-silence are applied later
without re-running the model.

Backend hierarchy:
  ├── SileroVADBackend        (preferred, when installable)
  ├── FFmpegVADBackend        (always available fallback)
  └── WhisperVADBackend       (last-resort, uses transcript word timestamps)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from providers.audio import extract_pcm_wav
from providers.ffmpeg import run_capture

log = logging.getLogger(__name__)

FRAME_MS = 30  # Silero V5 uses 512-sample frames at 16kHz (32ms); use 30ms to match WakeWord-style cadence


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


class VADBackend:
    name = "base"

    def is_available(self) -> bool:
        return True

    def analyze(self, audio: Path) -> VADResult:
        raise NotImplementedError


# ── Energy-based VAD (pure numpy, deterministic, no deps) ──

class EnergyVAD(VADBackend):
    name = "energy"

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = FRAME_MS,
        base_threshold: float = 0.02,
        speech_ceiling: float = 0.08,
        zcr_min: float = 0.02,
        zcr_max: float = 0.45,
    ):
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.base_threshold = base_threshold
        self.speech_ceiling = speech_ceiling
        self.zcr_min = zcr_min
        self.zcr_max = zcr_max

    def _read_mono(self, audio: Path) -> np.ndarray:
        wav = Path(str(audio))
        if wav.suffix.lower() != ".wav":
            probe = wav.parent / f"{wav.stem}_probe.wav"
            extract_pcm_wav(audio, probe, self.sample_rate)
            wav = probe
        import wave
        with wave.open(str(wav), "rb") as f:
            if f.getnchannels() != 1 or f.getframerate() != self.sample_rate:
                raise ValueError(
                    f"expected mono {self.sample_rate}Hz WAV, got "
                    f"{f.getnchannels()}ch {f.getframerate()}Hz"
                )
            raw = f.readframes(f.getnframes())
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return data

    def analyze(self, audio: Path) -> VADResult:
        data = self._read_mono(audio)
        frame = int(self.sample_rate * self.frame_ms / 1000)
        n_frames = max(1, len(data) // frame)
        if n_frames * frame > len(data):
            data = np.pad(data, (0, n_frames * frame - len(data)))
        frames = data[: n_frames * frame].reshape(n_frames, frame)
        rms = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1) + 1e-8)
        signs = np.signbit(frames)
        zcr_full = np.abs(np.diff(signs.astype(np.int8), axis=1)).sum(axis=1)
        zcr = zcr_full / max(1, frame - 1)
        scores = np.clip((rms - self.base_threshold) / (self.speech_ceiling - self.base_threshold), 0.0, 1.0)
        low_zcr = zcr < self.zcr_min
        high_zcr = zcr > self.zcr_max
        scores[low_zcr | high_zcr] *= 0.3
        return VADResult(
            scores=scores.tolist(),
            sample_rate=self.sample_rate,
            backend=self.name,
            frame_ms=self.frame_ms,
            duration=len(data) / self.sample_rate,
        )


# ── FFmpeg silencedetect fallback ──

class FFmpegVADBackend(VADBackend):
    name = "ffmpeg"

    def __init__(self, noise_db: float = -35.0, sample_rate: int = 16000, frame_ms: int = FRAME_MS):
        self.noise_db = noise_db
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms

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
        # silencedetect prints silence_start, then silence_end, per silence run
        events = []
        for line in proc.stderr.splitlines():
            line = line.strip()
            if "silence_start:" in line:
                events.append(("start", float(line.split("silence_start:")[1].strip())))
            elif "silence_end:" in line:
                end_part = line.split("silence_end:")[1].strip()
                end = float(end_part.split("|")[0].split()[0])
                events.append(("end", end))
        sil_ranges: list[tuple[float, float]] = []
        pending: float | None = None
        for kind, ts in events:
            if kind == "start":
                pending = ts
            elif kind == "end" and pending is not None:
                sil_ranges.append((pending, ts))
                pending = None
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
        )


# ── Whisper timestamps last-resort ──

class WhisperVADBackend(VADBackend):
    name = "whisper"

    def __init__(self, words: list, sample_rate: int = 16000, frame_ms: int = FRAME_MS, duration: float = 0.0):
        self.words = words
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.duration = duration

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
        )


class SileroVADBackend(VADBackend):
    """Silero V5 via onnxruntime — preferred when the package is installable."""

    name = "silero"

    def __init__(self, model_path: str | None = None):
        self.model_path = model_path

    def is_available(self) -> bool:
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            return False
        return self.model_path is not None

    def analyze(self, audio: Path) -> VADResult:
        raise NotImplementedError(
            "SileroVADBackend requires onnxruntime + model file. Not installed on "
            "this environment; falling back to energy/ffmpeg/whisper backends."
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
            instance: VADBackend = backend(words or [], duration=duration)
        else:
            instance = backend()
        if not instance.is_available():
            raise RuntimeError(f"VAD backend '{force}' unavailable")
        return instance

    if SileroVADBackend().is_available():
        log.info("selecting Silero VAD backend")
        return SileroVADBackend()
    if EnergyVAD().is_available():
        log.info("selecting energy VAD backend (device default — Silero not installable)")
        return EnergyVAD()
    if FFmpegVADBackend().is_available():
        log.info("selecting FFmpeg silencedetect VAD backend")
        return FFmpegVADBackend()
    log.info("selecting Whisper-timestamp VAD backend (last resort)")
    return WhisperVADBackend(words or [], duration=duration)


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
) -> list[SpeechRegion]:
    """Convert per-frame speech probabilities into speech regions.

    Uses hysteresis: speech starts when a frame exceeds `threshold`, but only
    ends when it drops below `release` (default threshold - 0.15, i.e. the
    Silero-style hysteresis gap). Short speech runs below `min_speech_ms` are
    dropped; short silences below `min_silence_ms` are merged into speech.
    Pure function — given the same cached scores + params, output is identical.
    """
    arr = np.asarray(scores, dtype=np.float32)
    frame_dur = frame_ms / 1000.0
    if release is None:
        release = threshold - 0.15

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
    result = [SpeechRegion(s, e) for s, e in merged if (e - s) >= min_speech_ms / 1000.0]
    return result


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
    )