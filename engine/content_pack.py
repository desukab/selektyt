from __future__ import annotations

import re
from models.core import Candidate


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[\w\u0900-\u0dff]+", text, flags=re.UNICODE) if len(w) > 2]


def _sentence(text: str) -> str:
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _densest_phrase(text: str) -> str:
    """Extractive title variant: the information-densest clause of the text.

    Every variant must trace to spoken source words (the 'no fabricated
    claims' policy), so instead of a boilerplate frame we pick the clause with
    the highest content-word density. Clauses need at least three words to be
    title candidates; ties go to the clause carrying more content words.
    """
    clauses = [c.strip() for c in re.split(r"[,;.]|\s–\s|\s-\s", text) if len(c.split()) >= 3]
    if not clauses:
        return ""

    def density(clause: str) -> tuple[float, int]:
        toks = _words(clause)
        return len(toks) / len(clause.split()), len(toks)

    best = max(clauses, key=density)
    return _sentence(best)[:90]


def _title_options(c: Candidate) -> list[str]:
    hook = _sentence(c.hook or c.text)
    payoff = _sentence(c.payoff or "")
    dense = _densest_phrase(c.text)
    options = [hook, payoff, dense]
    return list(dict.fromkeys(o for o in options if o))[:3]


def _hashtags(c: Candidate) -> list[str]:
    tags = {"#reels", "#shorts", "#india"}
    for token in _words(c.text):
        low = token.lower()
        if low in {"telugu", "india", "technology", "tech", "podcast", "business", "ai", "youtube", "cinema"}:
            tags.add("#" + low)
    if c.story_type:
        tags.add("#" + c.story_type)
    return sorted(tags)[:8]


def build_content_pack(c: Candidate) -> dict:
    """Generate deterministic post metadata from the selected spoken clip.

    This deliberately does not invent facts or add claims not present in the
    source. It is a local-first equivalent of a simple social content pack.
    """
    text = _sentence(c.text)
    return {
        "titles": _title_options(c),
        "instagram_caption": text,
        "hashtags": _hashtags(c),
        "story_type": c.story_type,
        "source_based": True,
        "policy": "Generated from selected source speech; no fabricated claims.",
    }
