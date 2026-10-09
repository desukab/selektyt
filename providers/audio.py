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

from providers.ffmpeg import run_capture, FfmpegError, ffmpeg_bin, ffprobe_bin


def _duration(path: Path) -> float:
    return float(
        subprocess.check_output(
            [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
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
        ffmpeg_bin(), "-y", "-i", str(media), "-vn",
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


class AudioReadError(RuntimeError):
    """A WAV that exists but that the stdlib reader cannot parse.

    The stdlib ``wave`` module does not raise ``OSError`` for most of what a
    damaged extraction produces: a headerless or truncated file raises
    ``wave.Error`` or ``EOFError``, and a file whose data chunk is not a whole
    number of samples raises ``ValueError`` from ``frombuffer``. None of those
    is an ``OSError``, so a caller that catches ``OSError`` to degrade
    gracefully still dies. This type lets a caller distinguish "unreadable"
    from "absent", which the no-speech diagnosis needs: a file that cannot be
    read is not evidence of silence.
    """


def _read_mono_pcm(wav: Path) -> tuple[np.ndarray, int]:
    """Mono float32 PCM and the source's sample rate, or an error.

    Raises :class:`AudioReadError` for anything the stdlib reader rejects, so
    every reader of an extraction reports the same thing for the same damage
    instead of a caller having to know three exception types. ``AudioReadError``
    is the one type this module's callers have to catch; the stdlib's own
    ``wave.Error``, ``EOFError`` and ``ValueError`` stop here.
    """
    import wave

    try:
        with wave.open(str(wav), "rb") as fh:
            channels = fh.getnchannels()
            rate = fh.getframerate()
            raw = fh.readframes(fh.getnframes())
    except (OSError, ValueError, EOFError, wave.Error) as exc:
        raise AudioReadError(f"cannot read {wav.name} as a WAV: "
                             f"{type(exc).__name__}: {exc}") from exc
    import numpy as np

    try:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            # A truncated multichannel file leaves a partial final frame, which
            # reshape rejects the same way frombuffer rejects an odd byte count.
            data = data[: (len(data) // channels) * channels].reshape(
                -1, channels).mean(axis=1)
    except ValueError as exc:
        raise AudioReadError(f"{wav.name} cannot be turned into samples: {exc}") from exc
    return data, rate


def peak_dbfs(wav: Path) -> float | None:
    """Loudest sample of the mono PCM extraction, in dBFS; ``None`` if absent.

    The distinction the no-speech diagnosis has to make and previously did
    not: a source whose audio track is digital silence has nothing to
    recover, while a source with real level and no transcript is a decode or
    container problem. A corpus source at 95.5% speech energy was reported to
    a user as "no audible spoken content" because that test did not exist.

    An unreadable extraction raises :class:`AudioReadError` rather than
    returning ``None``: ``None`` means the file is not there, and a file that
    is there but cannot be parsed is a different diagnosis — it is not
    evidence that the source is silent. Conflating them would send a user
    looking for a missing audio track in a file that has one.
    """
    import math

    import numpy as np

    if not wav.is_file():
        return None
    data, _rate = _read_mono_pcm(wav)
    if not data.size:
        return None
    peak = float(np.max(np.abs(data)))
    if peak <= 0.0:
        return -math.inf
    return 20.0 * math.log10(peak)