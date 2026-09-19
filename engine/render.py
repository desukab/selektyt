from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from models.core import Candidate
from engine.reframe import landscape_filter, vertical_crop_filter
from engine.edit_plan import retention_intervals
from engine.captions import ass_for_candidate


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required and must be on PATH")


def _render_one(source: Path, start: float, end: float, out: Path, ass: Path | None, vertical: bool) -> None:
    vf: list[str] = []
    if vertical:
        # Cap at 1080 wide rather than always scaling to it: upscaling a
        # sub-1080p source adds no detail, costs encode time and softens the
        # image. The crop already produced an exact 9:16 frame in display
        # space, so scaling by width with an even height keeps the aspect
        # without enlarging.
        vf += [
            vertical_crop_filter(source, start, end),
            r"scale=min(1080\,iw):-2:flags=lanczos",
        ]
    else:
        # Landscape fits the whole source into an exact 16:9 frame, padding
        # rather than cropping: a 4:3 source becomes a 640x486 square-pixel
        # picture inside an 864x486 frame. Without this the deliverable
        # inherits the source's own ratio and a 4:3 NTSC tape ships labelled
        # 16:9, and nothing in the pipeline would ever notice.
        vf.append(landscape_filter(source))
    if ass:
        safe = ass.as_posix().replace("'", "\\'")
        vf.append(f"subtitles='{safe}'")
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{max(0.05,end-start):.3f}", "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)


def _subcandidate(c: Candidate, start: float, end: float) -> Candidate:
    words = [w for w in (c.words or []) if w.end >= start and w.start <= end]
    return Candidate(start, end, " ".join(w.text for w in words), c.story_type, c.hook, c.payoff, c.editorial_score, c.virality_score, c.context_score, c.confidence, c.reason, c.visual_potential, words)


def render_clip(source: Path, candidate: Candidate, out: Path, ass: Path | None = None, vertical: bool = False) -> Path:
    require_ffmpeg(); out.parent.mkdir(parents=True, exist_ok=True)
    intervals = retention_intervals(candidate)
    if not intervals: raise RuntimeError("candidate has no renderable intervals")
    tmp = out.parent / f".{out.stem}_parts"; tmp.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, (start, end) in enumerate(intervals):
        sub = _subcandidate(candidate, start, end)
        part_ass = None
        if ass:
            if len(intervals) == 1:
                part_ass = ass
            else:
                part_ass = ass_for_candidate(sub, tmp / f"{i:03d}.ass")
        part = tmp / f"{i:03d}.mp4"
        _render_one(source, start, end, part, part_ass, vertical)
        parts.append(part)
    if len(parts) == 1:
        shutil.move(str(parts[0]), str(out)); shutil.rmtree(tmp, ignore_errors=True); return out
    # The concat demuxer resolves relative entries against the concat file's
    # own directory, not the process CWD — CWD-relative paths get doubled and
    # the whole render fails. Entries must be absolute.
    concat = tmp / "concat.txt"
    concat.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in parts), encoding="utf-8"
    )
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return out
