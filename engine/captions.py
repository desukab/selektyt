from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from models.core import Candidate, Word
from engine.caption_director import build_blocks, style_name, CaptionBlock


def _ts(v: float) -> str:
    v = max(0.0, v)
    h = int(v // 3600)
    m = int(v % 3600 // 60)
    s = int(v % 60)
    cs = min(99, int(round((v - int(v)) * 100)))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WFA" else 1 for ch in text)


def _group_words(words: list[Word], max_words: int = 6, max_width: int = 34) -> list[list[Word]]:
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


def _karaoke_text(words: list[Word], emphasis: set[int]) -> str:
    """Render word timings with ASS karaoke tags and subtle emphasis."""
    pieces: list[str] = []
    for idx, word in enumerate(words):
        duration_cs = max(1, int(round((word.end - word.start) * 100)))
        text = _escape(word.text.strip())
        if not text:
            continue
        if idx in emphasis:
            text = r"{\c&H00D7FF&\b1}" + text + r"{\c&HFFFFFF&\b0}"
        pieces.append(rf"{{\k{duration_cs}}}{text}")
    return " ".join(pieces)


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


def _cue_bounds(c: Candidate, block: CaptionBlock) -> tuple[float, float]:
    st = max(0.0, block.start - c.start)
    en = min(max(0.1, c.end - c.start), block.end - c.start + 0.12)
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
    for i, (st, en) in enumerate(_cue_sequence(c, blocks), 1):
        block = blocks[i - 1]
        text = " ".join(w.text.strip() for w in block.words if w.text.strip())
        if not text:
            continue
        cues.append(f"{i}\n{_srt_ts(st)} --> {_srt_ts(en)}\n{text}")
    out.write_text(("\n\n".join(cues) + "\n") if cues else "", encoding="utf-8")
    return out


def ass_for_candidate(c: Candidate, out: Path, words_per_line: int = 6) -> Path:
    """Write Replix-inspired, word-timed ASS captions locally.

    Features: multilingual-safe grouping, active-word karaoke timing, keyword
    emphasis, and compact visual presets selected by SELEKYT_CAPTION_STYLE.
    """
    out.parent.mkdir(parents=True, exist_ok=True)

    style = style_name()
    presets = {
        "minimal": dict(fontsize=54, primary="&H00FFFFFF", outline="&H99000000", back="&H00000000", bold=0, outline_w=2, shadow=1, margin_v=170),
        "punch": dict(fontsize=58, primary="&H00FFFFFF", outline="&HCC000000", back="&H00000000", bold=1, outline_w=3, shadow=1, margin_v=180),
        "spotlight": dict(fontsize=56, primary="&H00FFFFFF", outline="&H99000000", back="&H00000000", bold=1, outline_w=2, shadow=2, margin_v=170),
        "noir": dict(fontsize=52, primary="&H00EAEAEA", outline="&HCC111111", back="&H00000000", bold=0, outline_w=2, shadow=2, margin_v=165),
    }
    p = presets.get(style, presets["minimal"])
    font = os.getenv("SELEKYT_FONT", "Noto Sans")

    lines: list[str] = []
    blocks = _blocks_for_candidate(c, words_per_line)
    for (st, en), block in zip(_cue_sequence(c, blocks), blocks):
        text = _karaoke_text(block.words, block.emphasis)
        if text:
            lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Default,,0,0,0,,{text}")

    content = f"""[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Default,{font},{p['fontsize']},{p['primary']},&H00FFFFFF,{p['outline']},{p['back']},{p['bold']},0,0,0,100,100,0,0,1,{p['outline_w']},{p['shadow']},2,70,70,{p['margin_v']},1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n""" + "\n".join(lines) + "\n"
    out.write_text(content, encoding="utf-8")
    return out
