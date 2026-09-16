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


def _title_options(c: Candidate) -> list[str]:
    hook = _sentence(c.hook or c.text)
    payoff = _sentence(c.payoff or "")
    options = []
    if hook:
        options.append(hook[:90])
    if payoff and payoff.lower() != hook.lower():
        options.append(payoff[:90])
    if c.story_type == "revelation":
        options.append("The detail most people miss")
    elif c.story_type == "conflict":
        options.append("What actually happened")
    elif c.story_type == "question":
        options.append("The question behind the story")
    else:
        options.append("The part worth knowing")
    return list(dict.fromkeys(options))[:3]


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
