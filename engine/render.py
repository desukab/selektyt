from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from models.core import Candidate
from engine.reframe import crop_x_expression


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required and must be on PATH")


def render_clip(source: Path, candidate: Candidate, out: Path, ass: Path | None = None, vertical: bool = False) -> Path:
    require_ffmpeg()
    out.parent.mkdir(parents=True, exist_ok=True)
    duration = candidate.end - candidate.start
    vf: list[str] = []
    if vertical:
        x = crop_x_expression(source, candidate.start, candidate.end)
        vf.append(f"crop=ih*9/16:ih:x='{x}':y=0")
        vf.append("scale=1080:1920:flags=lanczos")
    else:
        vf.append("scale=1920:-2:flags=lanczos")
    if ass:
        vf.append(f"subtitles='{ass.as_posix().replace(chr(39), chr(92)+chr(39))}'")
    cmd = ["ffmpeg", "-y", "-ss", f"{candidate.start:.3f}", "-i", str(source), "-t", f"{duration:.3f}", "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    return out
