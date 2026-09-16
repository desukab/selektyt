from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from models.core import Candidate, Word


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


def ass_for_candidate(c: Candidate, out: Path, words_per_line: int = 6) -> Path:
    """Write minimal, word-timed ASS captions relative to the candidate start."""
    out.parent.mkdir(parents=True, exist_ok=True)
    words = sorted(c.words or [], key=lambda w: (w.start, w.end))
    if not words:
        # Fallback for candidates produced without word timestamps.
        raw = c.text.split()
        duration = max(0.1, c.end - c.start)
        step = duration / max(1, (len(raw) + words_per_line - 1) // words_per_line)
        words = [Word(" ".join(raw[i:i + words_per_line]), c.start + (i // words_per_line) * step, min(c.end, c.start + (i // words_per_line + 1) * step)) for i in range(0, len(raw), words_per_line)]

    groups = _group_words(words, max_words=words_per_line)
    lines: list[str] = []
    for group in groups:
        st = max(0.0, group[0].start - c.start)
        en = min(max(0.1, c.end - c.start), group[-1].end - c.start + 0.10)
        text = " ".join(w.text.strip() for w in group if w.text.strip()).replace("{", "(").replace("}", ")")
        if text:
            lines.append(f"Dialogue: 0,{_ts(st)},{_ts(max(st + 0.08, en))},Default,,0,0,0,,{text}")

    font = os.getenv("SELEKYT_FONT", "Noto Sans")
    content = f"""[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Default,{font},54,&H00FFFFFF,&H00FFFFFF,&H99000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,70,70,170,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n""" + "\n".join(lines) + "\n"
    out.write_text(content, encoding="utf-8")
    return out
