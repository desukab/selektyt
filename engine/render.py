from __future__ import annotations
import math
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from models.core import Candidate
from engine.reframe import landscape_filter, vertical_crop_filter
from engine.edit_plan import retention_intervals, join_words
from engine.captions import ass_for_candidate


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required and must be on PATH")


# ── script coverage: the burn must not draw placeholder boxes ─────────────
#
# libass resolves the ASS's declared font through fontconfig and falls back to
# the ``.notdef`` glyph for every codepoint no installed font carries. That
# fallback still puts ink in the frame — a row of identical hollow rectangles,
# one per codepoint, combining marks included — so every measurement that asks
# "is there caption ink?" passes on a deliverable nobody can read. This was not
# hypothetical: the shipped Telugu corpus render was produced on a host with no
# Telugu font, and its cue band is 13 even-pitched boxes (verified against a
# burn of the same text with a deliberately-missing font, byte-identical).
#
# The only fix is at the source: refuse to burn text the host cannot draw.
_COVERED: set[int] | None = None
_TAGS = re.compile(r"\{[^}]*\}")
_SCRIPT_WORD = re.compile(r"^([A-Z][A-Za-z'-]+) ")


def _covered_codepoints() -> set[int] | None:
    """Every codepoint any installed font can draw, or ``None`` if unknown.

    fontconfig is the same resolver libass uses, so its answer is the right
    one. Without it there is no answer to give and the caller must not guess:
    ``None`` means "unchecked", not "covered". The scan is a single subprocess
    over the installed set and is memoised for the process.
    """
    global _COVERED
    if _COVERED is not None:
        return _COVERED
    fc_list = shutil.which("fc-list")
    if fc_list is None:
        return None
    try:
        proc = subprocess.run(
            [fc_list, "--format", "%{charset}\n"], capture_output=True,
            text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    covered: set[int] = set()
    for line in proc.stdout.splitlines():
        for item in line.split():
            # Each item is a hex codepoint or a closed hex range ("0c00-0c7f").
            lo, _, hi = item.partition("-")
            try:
                a, b = int(lo, 16), int(hi or lo, 16)
            except ValueError:
                continue
            covered.update(range(a, b + 1))
    _COVERED = covered
    return covered


def _script_name(cp: int) -> str:
    """A readable name for the block a codepoint belongs to.

    ``unicodedata`` gives "TELUGU LETTER NA", and the leading word is the
    script — enough to name the missing font family in the error without a
    table that would have to track every block.
    """
    try:
        name = unicodedata.name(chr(cp))
    except ValueError:
        return f"U+{cp:04X}"
    m = _SCRIPT_WORD.match(name)
    return f"{m.group(1)} (U+{cp:04X})" if m else f"U+{cp:04X}"


def uncovered_script(text: str) -> list[str]:
    """Scripts in ``text`` that no installed font can render.

    Empty means the burn will draw real glyphs (or that fontconfig is absent
    and the question cannot be answered, which the caller reports as
    "unchecked" rather than as a pass).
    """
    covered = _covered_codepoints()
    if covered is None:
        return []
    missing = sorted({cp for cp in (ord(c) for c in text) if cp not in covered})
    return [_script_name(cp) for cp in missing]


def _check_caption_coverage(ass: Path) -> None:
    """Refuse to burn captions the host's fonts cannot draw.

    Called for every burn, so it has to stay cheap: one fontconfig scan per
    process and a set lookup per codepoint.
    """
    covered = _covered_codepoints()
    if covered is None:
        # No fontconfig (a Windows ffmpeg build resolves through DirectWrite
        # instead). The burn may still be tofu, but this code cannot tell, and
        # failing here would break every render on a platform it cannot
        # diagnose. The question stays open rather than being answered wrong.
        return
    text = ""
    for line in ass.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        text += _TAGS.sub("", line.split(",", 9)[-1]).replace("\\N", " ")
    missing = sorted({cp for cp in (ord(c) for c in text) if cp not in covered})
    if not missing:
        return
    by_script: dict[str, int] = {}
    for cp in missing:
        by_script[_script_name(cp)] = by_script.get(_script_name(cp), 0) + 1
    detail = "; ".join(f"{name}: {n} codepoint(s)" for name, n in
                       sorted(by_script.items(), key=lambda kv: -kv[1])[:4])
    raise RuntimeError(
        f"the captions in {ass.name} contain script no installed font can "
        f"render — libass would draw placeholder boxes into the deliverable "
        f"instead of text, and the result looks like a successful burn while "
        f"being unreadable ({detail}). Install a font covering the script "
        f"(for example the 'fonts-noto-core' package, which provides Noto "
        f"Sans Telugu and the other Indic families) and re-render. "
        f"Refusing to ship captions nobody can read.")


def _frame_count(start: float, end: float, fps: float) -> tuple[int, float]:
    """Resolve a [start, end) interval to a whole frame count on ``fps``.

    The exported timeline stamps every cut on this same grid, so the render has
    to produce exactly that many frames or the deliverable and the interchange
    file disagree. Asking ffmpeg for ``-t 8.367`` does not do this: it emits
    every frame whose timestamp falls at or below the requested time, so an
    interval that is not a whole number of frames is rounded *up* to the next
    frame. One concatenated part does this once; a clip of N parts does it N
    times, and the overshoot grows with the cut count while the EDL stays on
    the grid. Measured on a two-part clip, 2.63 frames long. Deriving the count
    explicitly instead keeps a 15-part supercut frame-accurate against its own
    EDL, and the returned duration is what ``-t`` is set to so the two bounds
    cannot argue about which one wins.
    """
    n = int(round((end - start) * fps))
    # A degenerate interval still has to render something, and a negative one
    # would ask for a negative frame count.
    return max(1, n), max(1, n) / fps


def _has_audio(path: Path) -> bool:
    """Whether a file carries an audio stream at all.

    Used to decide whether a part that came out video-only is a defect or the
    faithful rendering of a video-only source. The distinction is what keeps
    the retry below from looping on a source that never had audio.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of",
             "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True, encoding="utf-8",
        )
        return bool(out.stdout.strip())
    except (subprocess.CalledProcessError, OSError):
        return False


def _render_one(source: Path, start: float, end: float, out: Path, ass: Path | None, vertical: bool, fps: float) -> None:
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
    frames, span = _frame_count(start, end, fps)
    if ass:
        safe = ass.as_posix().replace("'", "\\'")
        # A Windows drive letter ("C:/...") puts a bare ":" in the filtergraph,
        # which ffmpeg parses as the option separator and then fails with
        # "Invalid argument". Escaping it as "\:" is the documented way to pass
        # a literal colon inside a filter value.
        safe = safe.replace(":", "\\:")
        vf.append(f"subtitles='{safe}'")
    common = ["-frames:v", str(frames), "-t", f"{span:.6f}",
              "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]

    # Input seeking (``-ss`` before ``-i``) is the fast path: ffmpeg seeks
    # straight to the keyframe instead of decoding up to it. On some containers
    # that seek loses the audio stream outright — the demuxer discards every
    # audio packet whose timestamp precedes the seek target and then has
    # nothing left to emit, so the part is written video-only and no error is
    # reported. Reproduced on an AV1/Opus WebM where seeking past ~15 s dropped
    # the audio on 7 of 8 sampled positions while the container provably holds
    # audio throughout. An output-side deliverable with no sound is not a
    # warning-grade problem, so fall back to output seeking, which decodes and
    # discards and therefore cannot drop a stream.
    subprocess.run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source), *common],
                   check=True)
    if not _has_audio(out) and _has_audio(source):
        subprocess.run(["ffmpeg", "-y", "-i", str(source), "-ss", f"{start:.3f}", *common],
                       check=True)


def _subcandidate(c: Candidate, start: float, end: float) -> Candidate:
    words = [w for w in (c.words or []) if w.end >= start and w.start <= end]
    return Candidate(start, end, join_words(words), c.story_type, c.hook, c.payoff, c.editorial_score, c.virality_score, c.context_score, c.confidence, c.reason, c.visual_potential, words)


def render_clip(source: Path, candidate: Candidate, out: Path, ass: Path | None = None, vertical: bool = False, fps: float = 30.0) -> Path:
    require_ffmpeg(); out.parent.mkdir(parents=True, exist_ok=True)
    intervals = retention_intervals(candidate)
    if not intervals: raise RuntimeError("candidate has no renderable intervals")
    if ass is not None:
        _check_caption_coverage(ass)
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
        _render_one(source, start, end, part, part_ass, vertical, fps)
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
