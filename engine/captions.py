from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from models.core import Candidate, Word
from engine.caption_director import (
    MAX_LINE_WIDTH, MAX_WORDS, PLAYRES_X, PLAYRES_Y, MARGIN_H,
    build_blocks, style_name, CaptionBlock, caption_width, max_line_width_for,
)
from engine.edit_plan import retention_intervals, join_words


def caption_presets() -> dict:
    """The visual presets selectable by ``SELEKYT_CAPTION_STYLE``.

    One source of truth. The CLI's ``--caption-style`` and the sidecar's
    ``caption_style`` param both need the valid names — the sidecar to refuse a
    typo that would otherwise silently render as ``minimal`` — and a second list
    of names would drift from the one actually used to write the ASS file.
    """
    return {
        "minimal": dict(fontsize=54, primary="&H00FFFFFF", outline="&H99000000", back="&H00000000", bold=0, outline_w=2, shadow=1, margin_v=170),
        "punch": dict(fontsize=58, primary="&H00FFFFFF", outline="&HCC000000", back="&H00000000", bold=1, outline_w=3, shadow=1, margin_v=180),
        "spotlight": dict(fontsize=56, primary="&H00FFFFFF", outline="&H99000000", back="&H00000000", bold=1, outline_w=2, shadow=2, margin_v=170),
        "noir": dict(fontsize=52, primary="&H00EAEAEA", outline="&HCC111111", back="&H00000000", bold=0, outline_w=2, shadow=2, margin_v=165),
    }


# The canvas the burned-in captions are laid out on, and the safe area inside
# it. These live with the width policy in ``engine.caption_director`` because
# ``caption_width``'s column only means something against them: a line of the
# budget's width has to fit between the two margins at the preset's font size,
# which is a relation the places that copy a width would otherwise each assume
# and none of them test. ``tests/test_caption_safe_area.py`` burns the widest
# line the policy admits and measures where its pixels land.
__all__ = ["caption_presets", "preset_for", "caption_geometry", "PLAYRES_X",
           "PLAYRES_Y", "MARGIN_H"]


def caption_geometry() -> dict:
    """The layout the burned-in ASS declares.

    One source of truth for the same reason as the presets: the timeline's ASS
    writer carries its own copy of these numbers in ``CaptionConfig``, and a
    change here that did not reach it would lay the same cue out on two
    different canvases.
    """
    return {"playres_x": PLAYRES_X, "playres_y": PLAYRES_Y, "margin_h": MARGIN_H}


def preset_for(style: str | None = None) -> dict:
    """Resolve a style name to its preset, falling back to ``minimal``.

    An unknown name is not an error here: the CLI accepts arbitrary strings and
    a script that asks for a style a newer build removed still gets captions
    rather than a failed run. The sidecar rejects unknown names instead, because
    a typo in a dropdown is otherwise invisible.
    """
    presets = caption_presets()
    return presets.get(str(style or style_name()).strip().lower(), presets["minimal"])


def _ts(v: float) -> str:
    v = max(0.0, v)
    h = int(v // 3600)
    m = int(v % 3600 // 60)
    s = int(v % 60)
    cs = min(99, int(round((v - int(v)) * 100)))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _width(text: str) -> int:
    # One policy for every caption writer: see caption_director.caption_width.
    return caption_width(text)


def _group_words(words: list[Word], max_words: int = MAX_WORDS,
                 max_width: int | None = None) -> list[list[Word]]:
    # Derived rather than defaulted, for the same reason ``build_blocks``
    # derives it: the budget is a width relative to the preset's font size.
    if max_width is None:
        geo = caption_geometry()
        max_width = max_line_width_for(preset_for(), geo["playres_x"], geo["margin_h"])
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for word in words:
        trial = cur + [word]
        if cur and (len(trial) > max_words or _width(" ".join(w.text for w in trial)) > max_width):
            groups.append(cur)
            cur = [word]
        else:
            cur = trial
    if cur:
        groups.append(cur)
    return groups


def _escape(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\\", "")


# The override and karaoke blocks this module writes. ``engine.render`` carries
# its own copy of the same pattern: it only needs the stripped text, and
# importing the writer from the render path would pull the whole module in.
_TAGS = re.compile(r"\{[^}]*\}")


def _karaoke_text(words: list[Word], emphasis: set[int]) -> str:
    """Render word timings with ASS karaoke tags and subtle emphasis."""
    out: list[str] = []
    prev_raw = ""
    for idx, word in enumerate(words):
        duration_cs = max(1, int(round((word.end - word.start) * 100)))
        # The spacing decision is made on the raw token, where whisper's
        # leading space still carries the gluing ('1' + ',024' -> '1,024').
        # Only the visible text is escaped; stripping before deciding would
        # destroy that space and re-split the number.
        raw = word.text
        if not raw.strip():
            continue
        if out and not prev_raw[-1:].isspace() and re.match(r"\w", raw):
            out.append(" ")
        text = _escape(raw.strip())
        if idx in emphasis:
            text = r"{\c&H00D7FF&\b1}" + text + r"{\c&HFFFFFF&\b0}"
        out.append(rf"{{\k{duration_cs}}}{text}")
        prev_raw = raw
    return "".join(out)


def _fallback_words(c: Candidate, words_per_line: int) -> list[Word]:
    """Synthesize evenly-spaced words from the plain text when no word
    timestamps are available, so captions still exist."""
    raw = c.text.split()
    duration = max(0.1, c.end - c.start)
    step = duration / max(1, (len(raw) + words_per_line - 1) // words_per_line)
    return [
        Word(
            " ".join(raw[i : i + words_per_line]),
            c.start + (i // words_per_line) * step,
            min(c.end, c.start + (i // words_per_line + 1) * step),
        )
        for i in range(0, len(raw), words_per_line)
    ]


def _blocks_for_candidate(c: Candidate, words_per_line: int) -> list[CaptionBlock]:
    """Cue grouping shared by the ASS and SRT writers so both track identically."""
    words = sorted(c.words or [], key=lambda w: (w.start, w.end))
    if not words:
        words = _fallback_words(c, words_per_line)
    blocks = build_blocks(c, max_words=words_per_line)
    if not blocks:
        groups = _group_words(words, max_words=words_per_line)
        blocks = [CaptionBlock(group, set()) for group in groups]
    return blocks


def _edited_duration(c: Candidate) -> float:
    """Seconds of media the rendered clip actually contains.

    ``retention_intervals`` shortens long pauses rather than deleting them, so
    this is shorter than ``c.end - c.start`` whenever the candidate has a cut.
    """
    return sum(b - a for a, b in retention_intervals(c))


def _source_to_edited(c: Candidate, source_t: float) -> float:
    """Map a source second onto the candidate's edited timeline.

    The rendered file is the concatenation of the retention intervals: the
    pauses between them are removed from the picture and the soundtrack, so a
    caption timed by a flat ``source_t - c.start`` offset drifts by every gap
    that was cut. On the real 8.9-minute source, clip 1 removed 1.04s in two
    gaps and every cue after the first one ran up to 0.48s early, with the last
    cue landing past the end of the rendered file.

    Summing the kept duration up to the interval that contains the instant
    puts the cue where the word is actually heard. Instants in a removed gap
    clamp to the end of the preceding interval — they have no position of
    their own, and rounding them up would push the cue into the next word.
    """
    out = 0.0
    for a, b in retention_intervals(c):
        if source_t <= a:
            return out
        if source_t <= b:
            return out + (source_t - a)
        out += b - a
    return out


def _cue_bounds(c: Candidate, block: CaptionBlock) -> tuple[float, float]:
    st = max(0.0, _source_to_edited(c, block.start))
    en = _source_to_edited(c, block.end)
    total = max(0.1, _edited_duration(c))
    en = min(total, en + 0.12)
    return st, max(st + 0.08, en)


def _cue_sequence(c: Candidate, blocks: list[CaptionBlock]) -> list[tuple[float, float]]:
    """Non-overlapping cue bounds for a candidate's blocks.

    ``_cue_bounds`` holds each cue ~0.12s past its last spoken word so the
    caption does not vanish mid-sentence at a block boundary. That tail would
    run into the next cue, so every end is clamped to the following cue's
    start. SRT and ASS players both render overlapping cues badly (flicker,
    stacked lines), and a hard gap of one frame keeps the two tracks clean.
    """
    bounds = [_cue_bounds(c, b) for b in blocks]
    for i in range(len(bounds) - 1):
        st, en = bounds[i]
        next_start = bounds[i + 1][0]
        # Hold the tail only while nobody else needs the screen.
        bounds[i] = (st, max(st + 0.08, min(en, next_start - 0.033)))
    return bounds


def _srt_ts(v: float) -> str:
    v = max(0.0, v)
    h = int(v // 3600)
    m = int(v % 3600 // 60)
    s = int(v % 60)
    ms = min(999, int(round((v - int(v)) * 1000)))
    return f"{h}:{m:02d}:{s:02d},{ms:03d}"


def srt_for_candidate(c: Candidate, out: Path, words_per_line: int = 6) -> Path:
    """Write a plain SubRip (SRT) track for a candidate.

    Platform-neutral counterpart to ``ass_for_candidate``: identical cue
    grouping and timing, no styling. Editors and players that cannot consume
    ASS (or its karaoke tags) get the same captions.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    cues: list[str] = []
    blocks = _blocks_for_candidate(c, words_per_line)
    # Cue numbers are sequential over the cues actually written. Enumerating
    # the blocks and skipping an empty one would leave a gap in the numbering
    # (cue 4 jumping to cue 6), which players tolerate but which is still an
    # invalid SubRip index and breaks anything that renumbers or diffs cues.
    for i, (st, en) in enumerate(_cue_sequence(c, blocks)):
        block = blocks[i]
        text = join_words([w for w in block.words if w.text.strip()])
        if not text:
            continue
        cues.append(f"{len(cues) + 1}\n{_srt_ts(st)} --> {_srt_ts(en)}\n{text}")
    out.write_text(("\n\n".join(cues) + "\n") if cues else "", encoding="utf-8")
    return out


def ass_content(c: Candidate, words_per_line: int = 6) -> str:
    """The ASS document for a candidate, as a string.

    Separated from :func:`ass_for_candidate` so a caller can hold what it
    *would* write beside what is already on disk, without touching the file.
    That comparison is how the shipped document is protected from being
    clobbered — see :func:`ship_captions`.
    """
    p = preset_for()
    # Declared by name, not embedded: libass resolves it through fontconfig, so
    # the font that actually renders depends on the machine. "Noto Sans" is not
    # installed on this build host — fontconfig substitutes DejaVu Sans, which
    # is wider, so a safe-area bound measured here is conservative rather than
    # optimistic. tests/test_caption_safe_area.py records what resolved.
    font = os.getenv("SELEKYT_FONT", "Noto Sans")
    lines: list[str] = []
    blocks = _blocks_for_candidate(c, words_per_line)
    for (st, en), block in zip(_cue_sequence(c, blocks), blocks):
        text = _karaoke_text(block.words, block.emphasis)
        if text:
            lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Default,,0,0,0,,{text}")

    geo = caption_geometry()
    return f"""[Script Info]\nScriptType: v4.00+\nPlayResX: {geo['playres_x']}\nPlayResY: {geo['playres_y']}\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Default,{font},{p['fontsize']},{p['primary']},&H00FFFFFF,{p['outline']},{p['back']},{p['bold']},0,0,0,100,100,0,0,1,{p['outline_w']},{p['shadow']},2,{geo['margin_h']},{geo['margin_h']},{p['margin_v']},1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n""" + "\n".join(lines) + "\n"


def ass_for_candidate(c: Candidate, out: Path, words_per_line: int = 6) -> Path:
    """Write Replix-inspired, word-timed ASS captions locally.

    Features: multilingual-safe grouping, active-word karaoke timing, keyword
    emphasis, and compact visual presets selected by SELEKYT_CAPTION_STYLE.

    This always (re)writes the generated document. Use :func:`ship_captions`
    from the pipeline instead when the file may already have been hand-edited.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ass_content(c, words_per_line), encoding="utf-8")
    return out


def srt_from_ass(text: str) -> str:
    """Project an ASS document onto a plain SubRip track.

    The ASS is the master caption document — it is what the render burns in —
    so the SRT shipped beside it must be *derived* from it once the ASS has been
    edited. Regenerating the SRT from the candidate instead would describe the
    cues the editor started from, and the package would carry two caption
    documents that do not agree.

    Only the dialect this module emits is supported: one ``Dialogue`` line per
    cue, fields in the documented order. That is the whole point of owning the
    format — the projection stays a few lines rather than a general ASS parser.
    """
    cues: list[str] = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) < 10:
            continue
        body = _TAGS.sub("", fields[9])
        body = body.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ").strip()
        if not body:
            continue
        start, end = _ass_to_srt_ts(fields[1]), _ass_to_srt_ts(fields[2])
        cues.append(f"{len(cues) + 1}\n{start} --> {end}\n{body}")
    return ("\n\n".join(cues) + "\n") if cues else ""


def _ass_to_srt_ts(field: str) -> str:
    """``H:MM:SS.cc`` (ASS) to ``H:MM:SS,mmm`` (SubRip)."""
    h, m, rest = field.strip().split(":", 2)
    s, _, cs = rest.partition(".")
    return f"{int(h)}:{int(m):02d}:{int(s):02d},{min(999, int(cs.ljust(3, '0')[:3])):03d}"


_ASS_TS = re.compile(r"\A(\d+):(\d+):(\d+)\.(\d+)\Z")


def _ass_to_sec(field: str) -> float:
    """``H:MM:SS.cc`` to seconds — the inverse of :func:`_ts`."""
    m = _ASS_TS.match(field.strip())
    if m is None:
        raise ValueError(f"not an ASS timestamp: {field!r}")
    h, mi, s, cs = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(cs) / 100.0


def slice_ass(text: str, start: float, end: float) -> str:
    """Keep only the cues inside ``[start, end]`` and shift them to start at 0.

    A candidate keeping more than one interval renders as a concatenation of
    parts, and each part is burned with its own subtitle stream at part-local
    time. Regenerating the per-part ASS from the sub-candidate — the old
    behaviour in ``engine/render.py`` — replaced the document the editor may
    have retimed with one built from the clip's words, so the shipped MP4
    disagreed with the file beside it. Slicing the shipped document instead
    keeps the editor's cues, text and tags and only retargets the times.

    The window is on the timeline the document itself keeps, which for a shipped
    ASS is the *edited* one: :func:`ass_content` emits cues through
    ``_source_to_edited``, with every removed gap already closed, so part `i`
    of the render begins at the sum of the durations before it — not at the
    interval's source time.

    A cue straddling a window edge is clipped to it, so one an editor let run
    across a removed gap appears in both parts that touch it, for as long as
    each part holds the media. A cue lying wholly outside the window is dropped,
    as it must be: the part does not contain the speech it describes.
    """
    out: list[str] = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            out.append(line)
            continue
        fields = line.split(",", 9)
        if len(fields) < 10:
            out.append(line)
            continue
        a, b = _ass_to_sec(fields[1]), _ass_to_sec(fields[2])
        if b <= start or a >= end or b <= a:
            continue
        a, b = max(a, start), min(b, end)
        out.append(f"{fields[0]},{_ts(a - start)},{_ts(b - start)},"
                   + ",".join(fields[3:]))
    return "\n".join(out) + "\n"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ship_captions(c: Candidate, ass_path: Path, srt_path: Path,
                  prev_digest: str | None = None) -> tuple[Path, str]:
    """Write the caption documents for a clip, keeping a hand edit.

    Returns the shipped ASS path and the digest of what is now on it. The
    render cache keys on that digest, because the ASS is burned into the MP4 —
    without it a moved cue re-renders nothing and the deliverable keeps the old
    burn while the document beside it says the new one.

    ``prev_digest`` is what this build left on the file last run, and it is what
    separates an editor's change from our own: a file that differs from the
    fresh generation *and* from that digest was rewritten by someone else, so
    it is kept verbatim and its SRT is projected from it. A file that differs
    from the fresh generation but still matches the digest means the *clip*
    changed, so the document is regenerated. No digest at all is a state older
    than this guard; the difference cannot be attributed, so the document is
    regenerated rather than risk shipping captions that no longer match the
    clip's words.
    """
    fresh = ass_content(c)
    if ass_path.is_file():
        on_disk = ass_path.read_text(encoding="utf-8")
        if on_disk != fresh and prev_digest and _digest(on_disk) != prev_digest:
            srt_path.parent.mkdir(parents=True, exist_ok=True)
            srt_path.write_text(srt_from_ass(on_disk), encoding="utf-8")
            return ass_path, _digest(on_disk)
    ass_for_candidate(c, ass_path)
    srt_for_candidate(c, srt_path)
    return ass_path, _digest(fresh)
