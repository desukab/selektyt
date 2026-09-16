from __future__ import annotations

import re
from dataclasses import dataclass
from collections import Counter
from models.core import Candidate, Word


@dataclass
class CaptionBlock:
    words: list[Word]
    emphasis: set[int]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words if w.text.strip())


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _width(text: str) -> int:
    # Approximate visual width; Indian scripts generally need more room than ASCII.
    return sum(2 if ord(ch) >= 0x0900 else 1 for ch in text)


def _tokens(words: list[Word]) -> list[str]:
    return [_clean(w.text).lower() for w in words]


def _emphasis(words: list[Word]) -> set[int]:
    """Pick a few meaningful words without requiring an LLM.

    Numbers, longer content words, English technical terms and words carrying
    strong punctuation are useful visual anchors. Repeated filler-like tokens
    are deliberately ignored.
    """
    stop = {
        "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for",
        "is", "are", "was", "were", "this", "that", "it", "i", "we", "you",
        "అది", "ఇది", "అంటే", "మరి", "కానీ", "మనం", "అతను", "ఆమె",
        "और", "यह", "वह", "है", "हैं", "के", "की", "का", "में",
    }
    scores: list[tuple[float, int]] = []
    for i, w in enumerate(words):
        t = _clean(w.text)
        low = t.lower()
        if not t or low in stop or len(low) <= 2:
            continue
        score = min(4.0, len(re.sub(r"[^\w\u0900-\u0dff]", "", t)) / 4)
        if re.search(r"\d", t):
            score += 2.0
        if re.search(r"[!?]", t):
            score += 1.5
        if t.isupper() and len(t) > 2:
            score += 1.0
        scores.append((score, i))
    scores.sort(reverse=True)
    # Never overwhelm the caption: roughly one emphasized word per 3 words.
    limit = max(1, min(2, len(words) // 3))
    return {i for _, i in scores[:limit]}


def build_blocks(candidate: Candidate, max_words: int = 6, max_width: int = 34, max_duration: float = 3.2) -> list[CaptionBlock]:
    words = sorted((candidate.words or []), key=lambda w: (w.start, w.end))
    words = [w for w in words if _clean(w.text)]
    if not words:
        return []

    blocks: list[CaptionBlock] = []
    cur: list[Word] = []
    for word in words:
        trial = cur + [word]
        text = " ".join(w.text.strip() for w in trial)
        duration = word.end - (cur[0].start if cur else word.start)
        boundary = bool(re.search(r"[.!?।॥]$", cur[-1].text.strip())) if cur else False
        if cur and (len(trial) > max_words or _width(text) > max_width or duration > max_duration or boundary):
            blocks.append(CaptionBlock(cur, _emphasis(cur)))
            cur = [word]
        else:
            cur = trial
    if cur:
        blocks.append(CaptionBlock(cur, _emphasis(cur)))
    return blocks


def style_name() -> str:
    import os
    return os.getenv("SELEKYT_CAPTION_STYLE", "minimal").strip().lower()


def caption_metadata(candidate: Candidate) -> dict:
    blocks = build_blocks(candidate)
    return {
        "style": style_name(),
        "blocks": [
            {
                "start": round(b.start, 3),
                "end": round(b.end, 3),
                "text": b.text,
                "emphasis": [b.words[i].text for i in sorted(b.emphasis)],
            }
            for b in blocks
        ],
    }
