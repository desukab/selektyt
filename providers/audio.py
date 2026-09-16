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


def pcm_s16_file(media: Path, out: Path, sample_rate: int = 16000) -> Path:
    """Extract raw signed-16-bit mono PCM (for direct numpy consumption)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".pcm.tmp")
    cmd = [
        "ffmpeg", "-y", "-i", str(media), "-vn",
        "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", "-f", "s16le", str(tmp),
    ]
    run_capture(cmd, stage="audio-extract-pcm", source=str(media), target=str(out))
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