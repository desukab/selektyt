"""Audio extraction provider — normalized 16 kHz mono PCM with cache fingerprinting.

The expensive extraction is done once and cached. Fingerprints allow later
stages to reuse the normalized audio for VAD analysis without re-reading
the source video.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from providers.ffmpeg import run_capture, FfmpegError


def _duration(path: Path) -> float:
    return float(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            text=True,
            encoding="utf-8",
        ).strip()
    )


def extract_pcm_wav(media: Path, out: Path, sample_rate: int = 16000) -> Path:
    """Extract single-channel PCM WAV at the given sample rate.

    Raises:
        FfmpegError: if ffmpeg fails, with actionable diagnostics.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".wav.tmp")
    cmd = [
        "ffmpeg", "-y", "-i", str(media), "-vn",
        "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", "-f", "wav", str(tmp),
    ]
    run_capture(cmd, stage="audio-extract", source=str(media), target=str(out))
    tmp.replace(out)
    return out


def source_fingerprint(media: Path, source: str) -> str:
    """Fingerprint a media file independent of path (size + mtime + source)."""
    st = media.stat()
    raw = f"{source}|{media.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha256(raw.encode()).hexdigest()


def audio_fingerprint(wav: Path) -> str:
    """Fingerprint the normalized audio content (size + mtime)."""
    if not wav.exists():
        return ""
    st = wav.stat()
    raw = f"{wav.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha256(raw.encode()).hexdigest()


def peak_dbfs(wav: Path) -> float | None:
    """Loudest sample of the mono PCM extraction, in dBFS; ``None`` if absent.

    The distinction the no-speech diagnosis has to make and previously did
    not: a source whose audio track is digital silence has nothing to
    recover, while a source with real level and no transcript is a decode or
    container problem. A corpus source at 95.5% speech energy was reported to
    a user as "no audible spoken content" because that test did not exist.
    """
    import math
    import wave

    if not wav.is_file():
        return None
    with wave.open(str(wav), "rb") as fh:
        channels = fh.getnchannels()
        raw = fh.readframes(fh.getnframes())
    if not raw:
        return None
    import numpy as np

    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak <= 0.0:
        return -math.inf
    return 20.0 * math.log10(peak)