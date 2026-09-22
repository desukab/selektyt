"""Shared lexical signals for the corpus editorial layer.

The corpus layer reasons about *content* words, not about every token: whether
two passages are about the same thing depends on the terms that carry subject
matter ("satellite", "ఉపగ్రహం"), never on the connective tissue ("the", "and",
"మరి").

Tokenisation is deliberately shared with `engine.story_units` — the module that
decides where one story ends and the next begins. Topic clusters and redundancy
groups must see the same words the coherence scorer saw, or a corpus-level
judgement would drift away from the unit boundaries it is built on. That is why
this module re-uses `story_units._content_tokens` rather than defining its own
notion of "content word".

Everything here is pure and deterministic: identical input yields identical
output, there is no global state, and no function reads the clock or the
network. Complexity is stated per function; none of them holds a copy of the
whole transcript.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from engine.story_units import _content_tokens


def content_tokens(text: str) -> set[str]:
    """Content-word bag of a passage.

    Re-exports `engine.story_units._content_tokens` so the corpus layer and the
    coherence scorer agree on what a "word" is. For scripts that do not write
    spaces between words the result is character-level, which still compares
    correctly — the signal we use is always *relative* overlap.
    """
    return _content_tokens(text)


def df_table(unit_texts: list[str]) -> Counter:
    """Document frequency of every content token across the units.

    O(total words) time, O(vocabulary) memory — the only structure that scales
    with corpus size, and it is one counter rather than a copy per topic.
    """
    df: Counter = Counter()
    for text in unit_texts:
        df.update(content_tokens(text))
    return df


def idf_weights(df: Counter, n_docs: int) -> dict[str, float]:
    """Inverse document frequency, smoothed so a singleton never divides by ~0.

    Rare terms are what mark a shared subject, so idf is the weight that lets
    "gps" outweigh "system" when two passages are compared. Smoothing keeps the
    function defined for a corpus of one unit.
    """
    if n_docs <= 0:
        return {}
    return {t: math.log((n_docs + 1.0) / (c + 1.0)) for t, c in df.items()}


def containment(a: set[str], b: set[str]) -> float:
    """Plain overlap relative to the smaller bag, in [0, 1].

    This is the measure the story-unit segmentation already trusts for its own
    seam decisions (`story_units._containment`), so corpus-level judgements that
    must agree with unit boundaries use it too. `weighted_containment` below is
    the rarity-sensitive variant; the two are kept separate because idf adds
    real discrimination at corpus scale and meaningless compression in a
    three-unit toy transcript, and callers that need a stable calibration use
    this one.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def weighted_containment(a: set[str], b: set[str], idf: dict[str, float]) -> float:
    """idf-weighted overlap relative to the *smaller* bag, in [0, 1].

    Containment rather than Jaccard, because the relation that matters
    editorially is asymmetric in practice — a long passage can fully contain
    the subject of a short one without the short one containing its subject.
    Scoring against the smaller bag keeps that judgement fair in both
    directions. Zero when either side carries no content (a passage with no
    recoverable words cannot be shown to share a subject with anything).
    """
    if not a or not b:
        return 0.0
    shared = a & b
    if not shared:
        return 0.0
    wa = sum(idf.get(t, 1.0) for t in shared)
    wmin = min(sum(idf.get(t, 1.0) for t in a), sum(idf.get(t, 1.0) for t in b))
    if wmin <= 0.0:
        return 0.0
    return min(1.0, wa / wmin)


def signature_terms(bags: list[set[str]], idf: dict[str, float], limit: int = 3) -> list[str]:
    """The terms that best identify this group of passages.

    Ranked by idf (how exclusively the group owns the term) times repetition
    (how much of the group actually uses it). Returned verbatim from the
    transcript — this is a signature, not a summary, so no term is ever
    synthesised or re-inflected.
    """
    if not bags:
        return []
    df_local: Counter = Counter()
    for bag in bags:
        df_local.update(bag)
    scored = sorted(
        df_local,
        key=lambda t: (df_local[t] * idf.get(t, 1.0), df_local[t], t),
        reverse=True,
    )
    return scored[:limit]


def fingerprint(payload: str) -> str:
    """Short stable digest of an input payload, for provenance records.

    Used to record *which* input a corpus was built from without storing the
    input itself. SHA1 over the textual payload: it is a deduplication label,
    not a security boundary.
    """
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def quote(text: str, limit: int = 90) -> str:
    """A verbatim, length-bounded quotation for an evidence field.

    Evidence fields must quote the source rather than paraphrase it: the human
    editor challenging a judgement needs to see the words the judgement rests
    on, exactly as transcribed (ASR warts included).
    """
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
