"""FFmpeg execution wrapper with actionable error diagnostics.

Captures stderr tail, identifies the failing stage, and produces a
reproducible error message showing source/target paths and the command.
"""
from __future__ import annotations

import math
import os
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


def _static_ffmpeg_bin(name: str) -> str | None:
    """Locate a binary inside the ``static-ffmpeg`` wheel, or None.

    ``static-ffmpeg`` ships platform zips that it extracts into its own
    ``bin/<platform>/`` directory at first use. The extraction happens on
    ``add_paths()``, which is what puts the directory on ``PATH``; but a caller
    that resolves the path directly rather than inheriting a mutated ``PATH``
    still needs the file, and a zip that was never unpacked has no file to
    find. Returns None in both the "not installed" and "not yet extracted"
    cases, so the caller falls through to the next strategy instead of
    raising from inside a lookup.
    """
    try:
        import static_ffmpeg  # type: ignore[import-not-found]
    except ImportError:
        return None
    root = Path(static_ffmpeg.__file__).parent / "bin"
    if not root.is_dir():
        return None
    for platform_dir in sorted(root.iterdir()):
        found = platform_dir / name
        if found.is_file():
            return str(found)
    return None


def resolve(name: str) -> str:
    """Return the path to the ``ffmpeg``/``ffprobe`` binary to invoke.

    Order, and why that order:

    1. ``SELEKYT_FFMPEG`` / ``SELEKYT_FFPROBE``, when set — the staged desktop
       installer and the CI parity matrix both point at a specific binary, and
       an operator who set the variable did so because they want that one.
    2. ``shutil.which`` — a system ffmpeg the user installed themselves is the
       normal case and the one the corpus was measured against.
    3. the ``static-ffmpeg`` wheel, when it is installed and has unpacked.

    The third step is what makes ``pip install selekyt`` usable on a machine
    with no system ffmpeg. It is deliberately last: the Rust sidecar and the
    desktop bundle resolve the same way through ``SELEKYT_FFMPEG``, so an
    install that carries its own ffmpeg names it explicitly rather than
    relying on the fallback.

    This function never downloads. ``static-ffmpeg`` downloads on first
    ``add_paths()``; resolving from an unpacked directory keeps an import of
    this module from reaching the network.
    """
    override = os.environ.get(f"SELEKYT_{name.upper()}")
    if override:
        return override
    found = shutil.which(name)
    if found:
        return found
    bundled = _static_ffmpeg_bin(name)
    if bundled:
        return bundled
    raise RuntimeError(
        f"{name} was not found. Install ffmpeg, or install the "
        "'static-ffmpeg' package so SeleKyT can provide one"
    )


def ffmpeg_bin() -> str:
    return resolve("ffmpeg")


def ffprobe_bin() -> str:
    return resolve("ffprobe")


def require_ffmpeg() -> None:
    """Keep the old name working: raise if ffmpeg cannot be resolved."""
    ffmpeg_bin()


def require_ffprobe() -> None:
    """Keep the old name working: raise if ffprobe cannot be resolved."""
    ffprobe_bin()


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
        encoding="utf-8",
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
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise FfmpegError(
            stage="probe-duration",
            command=[ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
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
            [ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8",
        )
        raw = proc.stdout.strip() if proc.returncode == 0 else ""
        num, _, den = raw.partition("/")
        fps = float(num) / (float(den) if den else 1.0)
        if fps > 0.0 and math.isfinite(fps):
            return fps
    except (FfmpegError, ValueError, ZeroDivisionError):
        pass
    return 30.0


def probe_sample_aspect(media: Path) -> tuple[int, int]:
    """Return the video stream's sample (pixel) aspect ratio as ``(w, h)``.

    Render geometry depends on this, not merely on the pixel dimensions: a crop
    ``ih*9/16`` pixels wide is only 9:16 in *display* space when the pixels are
    square. An anamorphic source (NTSC DV is 720x486 at SAR 8:9, so a 272-pixel
    crop displays at 1088:2187, nearer 1:2 than 9:16) would otherwise be cut to
    a vertical frame with the wrong proportions, content undistorted but the
    ratio simply wrong.

    Like the rate probe this is geometry metadata rather than something a run
    depends on, so an unobtainable value falls back to square pixels rather
    than aborting a run that would otherwise have succeeded. ``0:1`` and
    ``N/A`` both mean "unspecified" and are treated as square.
    """
    try:
        require_ffprobe()
        proc = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=sample_aspect_ratio",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8",
        )
        raw = proc.stdout.strip() if proc.returncode == 0 else ""
        num, _, den = raw.partition(":")
        sar_w, sar_h = int(num), int(den)
        if sar_w > 0 and sar_h > 0:
            return sar_w, sar_h
    except (FfmpegError, ValueError):
        pass
    return 1, 1


def remux(src: Path, dest: Path, timeout: float | None = None) -> Path:
    """Stream-copy a source into a fresh container.

    This exists for sources whose container is structurally damaged —
    non-monotonic DTS, mis-marked keyframes — in a way that makes a decoder
    produce little or no audio even though the packets are there. Copying the
    streams into a clean container repairs that without touching a pixel or a
    sample, which is what makes it a safe first response: it cannot change the
    content, only the box it arrives in.

    Raises ``FfmpegError`` if ffmpeg itself rejects the copy, so the caller can
    fall through to the honest no-speech diagnosis rather than reporting a
    recovery that did not happen.
    """
    if dest.exists():
        dest.unlink()
    run_capture(
        [ffmpeg_bin(), "-y", "-loglevel", "error",
         "-fflags", "+genpts",   # regenerate timestamps; the source's are the
                                 # thing being repaired, so trusting them
                                 # would carry the damage into the copy
         "-i", str(src), "-c", "copy", "-map", "0", str(dest)],
        stage="remux", source=str(src), target=str(dest), timeout=timeout,
    )
    return dest