"""FFmpeg execution wrapper with actionable error diagnostics.

Captures stderr tail, identifies the failing stage, and produces a
reproducible error message showing source/target paths and the command.
"""
from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path


class FfmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe invocation fails with diagnostics."""

    def __init__(
        self,
        stage: str,
        command: list[str],
        stderr_tail: str,
        source: str | None = None,
        target: str | None = None,
    ):
        self.stage = stage
        self.command = command
        self.stderr_tail = stderr_tail
        self.source = source
        self.target = target
        lines = [
            f"ffmpeg failed during '{stage}'",
        ]
        if source:
            lines.append(f"  source: {source}")
        if target:
            lines.append(f"  target: {target}")
        lines.append(f"  command: {' '.join(command)}")
        tail = stderr_tail.strip().splitlines()
        if tail:
            lines.append("  stderr tail:")
            lines += [f"    {l}" for l in tail[-15:]]
        super().__init__("\n".join(lines))


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required and must be on PATH")


def require_ffprobe() -> None:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe is required and must be on PATH")


def run_capture(
    command: list[str],
    *,
    stage: str,
    source: str | None = None,
    target: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run an ffmpeg command, capturing output and raising FfmpegError on failure."""
    require_ffmpeg()
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise FfmpegError(
            stage=stage,
            command=command,
            stderr_tail=proc.stderr,
            source=source,
            target=target,
        )
    return proc


def probe_duration(media: Path) -> float:
    """Return media duration in seconds via ffprobe."""
    require_ffprobe()
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise FfmpegError(
            stage="probe-duration",
            command=["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
            stderr_tail=proc.stderr,
            source=str(media),
        )
    return float(proc.stdout.strip())


def probe_fps(media: Path) -> float:
    """Return the media's frame rate as a plain float.

    ``r_frame_rate`` is a rational ("30000/1001") and is the rate frames are
    actually delivered at, which is what an EDL or FCPXML sequence is stamped
    with. Unlike a duration probe, the rate is metadata for the interchange
    files rather than an input to the render, so any failure to obtain it
    falls back to 30.0 instead of aborting a run that would otherwise have
    succeeded. The caller records what it actually used.
    """
    try:
        require_ffprobe()
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        raw = proc.stdout.strip() if proc.returncode == 0 else ""
        num, _, den = raw.partition("/")
        fps = float(num) / (float(den) if den else 1.0)
        if fps > 0.0 and math.isfinite(fps):
            return fps
    except (FfmpegError, ValueError, ZeroDivisionError):
        pass
    return 30.0