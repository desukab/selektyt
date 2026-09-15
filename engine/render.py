from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from models.core import Candidate, Word
from engine.reframe import crop_x_expression
from engine.edit_plan import retention_intervals
from engine.captions import ass_for_candidate


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required and must be on PATH")


def _render_one(source: Path, start: float, end: float, out: Path, ass: Path | None, vertical: bool) -> None:
    vf: list[str] = []
    if vertical:
        x = crop_x_expression(source, start, end)
        vf += [f"crop=ih*9/16:ih:x='{x}':y=0", "scale=1080:1920:flags=lanczos"]
    else:
        vf.append("scale=1920:-2:flags=lanczos")
    if ass:
        vf.append(f"subtitles='{ass.as_posix().replace(chr(39), chr(92)+chr(39))}'")
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end-start:.3f}", "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)


def render_clip(source: Path, candidate: Candidate, out: Path, ass: Path | None = None, vertical: bool = False) -> Path:
    require_ffmpeg()
    out.parent.mkdir(parents=True, exist_ok=True)
    intervals = retention_intervals(candidate)
    if len(intervals) <= 1:
        _render_one(source, intervals[0][0], intervals[0][1], out, ass, vertical)
        return out

    tmp = out.parent / f".{out.stem}_parts"
    tmp.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, (start, end) in enumerate(intervals):
        words = [w for w in (candidate.words or []) if w.end >= start and w.start <= end]
        sub_candidate = Candidate(start, end, " ".join(w.text for w in words), candidate.story_type, candidate.hook, candidate.payoff, candidate.editorial_score, candidate.virality_score, candidate.context_score, candidate.confidence, candidate.reason, candidate.visual_potential, words)
        part_ass = ass_for_candidate(sub_candidate, tmp / f"{i:03d}.ass") if ass else None
        part = tmp / f"{i:03d}.mp4"
        _render_one(source, start, end, part, part_ass, vertical)
        parts.append(part)
    concat = tmp / "concat.txt"
    concat.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return out
