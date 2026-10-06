"""Compose coherent editorial stories from source moments.

This layer sits above per-clip scoring. Candidates are evidence from the source;
the story is the relationship between those candidates. The composer keeps
multiple plausible editorial constructions alive, evaluates complete
constructions, and chooses a story length from a bounded budget rather than
equating the number of social cuts with the number of story moments.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from engine.selection import _covers
from models.core import Candidate


@dataclass(frozen=True)
class StoryPlan:
    candidates: tuple[Candidate, ...]
    score: float
    style: str
    reasons: tuple[str, ...] = ()


_ROLE_VALUE = {
    "setup": 0.16,
    "tension": 0.12,
    "turn": 0.15,
    "revelation": 0.18,
    "resolution": 0.22,
    "continuation": 0.02,
}
_STYLES = ("chronological", "cold_open", "mystery", "payoff_first")


def _thread_ids(c: Candidate, corpus) -> set[int]:
    if corpus is None:
        return set()
    out: set[int] = set()
    for i, u in enumerate(corpus.units):
        if _covers(corpus, i, c) and u.thread_id is not None:
            out.add(u.thread_id)
    return out


def _roles(c: Candidate, corpus) -> set[str]:
    if corpus is None:
        return set()
    out: set[str] = set()
    for i, u in enumerate(corpus.units):
        if not _covers(corpus, i, c) or u.beat_id is None:
            continue
        if 0 <= u.beat_id < len(corpus.beats):
            out.add(corpus.beats[u.beat_id].kind)
    return out


def _links_closed(a: Candidate, b: Candidate, corpus) -> int:
    if corpus is None:
        return 0
    tids = _thread_ids(a, corpus) & _thread_ids(b, corpus)
    total = 0
    for tid in tids:
        if not 0 <= tid < len(corpus.threads):
            continue
        for link in corpus.threads[tid].links:
            if (_covers(corpus, link.setup_unit, a) and
                    _covers(corpus, link.payoff_unit, b)):
                total += 1
            elif (_covers(corpus, link.setup_unit, b) and
                  _covers(corpus, link.payoff_unit, a)):
                total += 1
    return total


def _pair_score(a: Candidate, b: Candidate, corpus, style: str) -> float:
    score = 0.0
    if _thread_ids(a, corpus) & _thread_ids(b, corpus):
        score += 0.10
    score += 0.18 * min(1.0, _links_closed(a, b, corpus))

    if b.start >= a.end:
        if "setup" in _roles(a, corpus):
            score += 0.05
        if _roles(b, corpus) & {"turn", "revelation", "resolution"}:
            score += 0.08

    if a.text and b.text:
        from engine.editorial import _similarity
        if _similarity(a.text, b.text) >= 0.78:
            score -= 0.18

    if style == "chronological" and b.start >= a.start:
        score += 0.035
    elif style == "cold_open" and len(a.text.split()) >= 12:
        score += 0.025
    elif style == "mystery" and _roles(b, corpus) & {"setup", "tension"}:
        score += 0.025
    elif style == "payoff_first" and _roles(a, corpus) & {"revelation", "resolution"}:
        score += 0.04
    return score


def _node_score(c: Candidate, corpus, style: str) -> float:
    roles = _roles(c, corpus)
    score = float(c.total_score)
    score += sum(_ROLE_VALUE.get(r, 0.0) for r in roles)
    if style == "cold_open" and c.opening_score >= 0.45:
        score += 0.10
    if style == "mystery" and roles & {"tension", "setup"}:
        score += 0.06
    if style == "payoff_first" and roles & {"revelation", "resolution"}:
        score += 0.08
    return score


def _candidate_pool(candidates: Sequence[Candidate], corpus, width: int = 72) -> list[Candidate]:
    if len(candidates) <= width:
        return list(candidates)

    ranked = sorted(candidates, key=lambda c: c.total_score, reverse=True)
    chosen: list[Candidate] = []
    seen_threads: set[int] = set()
    chosen.extend(ranked[: min(24, width)])

    for c in ranked[24:]:
        tids = _thread_ids(c, corpus)
        if tids - seen_threads:
            chosen.append(c)
            seen_threads.update(tids)
        if len(chosen) >= width:
            break

    if len(chosen) < width:
        chosen_ids = {id(c) for c in chosen}
        chosen.extend(c for c in ranked if id(c) not in chosen_ids)
    return chosen[:width]


def _whole_score(seq: Sequence[Candidate], corpus, style: str,
                 target_seconds: float | None = None) -> float:
    if not seq:
        return -1.0

    score = sum(_node_score(c, corpus, style) for c in seq)
    score += sum(_pair_score(a, b, corpus, style)
                 for a, b in zip(seq, seq[1:]))

    roles = set().union(*(_roles(c, corpus) for c in seq)) if corpus else set()
    if "setup" in roles:
        score += 0.18
    if roles & {"turn", "revelation"}:
        score += 0.18
    if "resolution" in roles:
        score += 0.28

    closed = sum(_links_closed(a, b, corpus) for a, b in zip(seq, seq[1:]))
    score += min(0.36, 0.12 * closed)

    unique_threads: set[int] = set()
    for c in seq:
        unique_threads.update(_thread_ids(c, corpus))
    if len(unique_threads) > 1:
        score += min(0.12, 0.03 * (len(unique_threads) - 1))

    duration = sum(max(0.0, c.end - c.start) for c in seq)
    if target_seconds and target_seconds > 0:
        ratio = duration / target_seconds
        if 0.65 <= ratio <= 1.20:
            score += 0.20
        elif ratio < 0.35:
            score -= 0.12
        elif ratio > 1.75:
            score -= 0.08

    score -= max(0, len(seq) - 8) * 0.025
    return score


def _beam(candidates: list[Candidate], corpus, max_moments: int,
          style: str, beam_width: int, target_seconds: float | None) -> StoryPlan:
    if not candidates or max_moments <= 0:
        return StoryPlan((), 0.0, style)

    states: list[tuple[list[Candidate], float]] = [
        ([c], _whole_score([c], corpus, style, target_seconds))
        for c in candidates
    ]
    states.sort(key=lambda x: x[1], reverse=True)
    states = states[:beam_width]
    all_states = list(states)

    for _ in range(1, max_moments):
        expanded: list[tuple[list[Candidate], float]] = []
        for seq, _old_score in states:
            used = {id(c) for c in seq}
            previous_tids = set().union(*(_thread_ids(x, corpus) for x in seq))
            for c in candidates:
                if id(c) in used:
                    continue
                tids = _thread_ids(c, corpus)
                delta = _node_score(c, corpus, style) + _pair_score(
                    seq[-1], c, corpus, style
                )
                if tids and previous_tids and not (tids & previous_tids):
                    delta -= 0.025
                expanded.append((seq + [c], delta))

        if not expanded:
            break
        rescored = [
            (seq, _whole_score(seq, corpus, style, target_seconds))
            for seq, _delta in expanded
        ]
        rescored.sort(key=lambda x: x[1], reverse=True)

        seen: set[tuple[tuple[float, float], ...]] = set()
        next_states: list[tuple[list[Candidate], float]] = []
        for seq, score in rescored:
            key = tuple((round(c.start, 3), round(c.end, 3)) for c in seq)
            if key in seen:
                continue
            seen.add(key)
            next_states.append((seq, score))
            if len(next_states) >= beam_width:
                break
        states = next_states
        all_states.extend(states)

    best_seq, best_score = max(all_states, key=lambda x: x[1])
    return StoryPlan(tuple(best_seq), best_score, style)


def compose_story_variants(
    candidates: Sequence[Candidate],
    *,
    corpus=None,
    max_moments: int = 18,
    beam_width: int = 12,
    target_seconds: float | None = None,
) -> tuple[StoryPlan, ...]:
    pool = _candidate_pool(candidates, corpus)
    if not pool:
        return ()
    plans = tuple(
        _beam(pool, corpus, max_moments, style, beam_width, target_seconds)
        for style in _STYLES
    )
    return tuple(sorted(plans, key=lambda p: p.score, reverse=True))


def compose_story(
    candidates: Sequence[Candidate],
    *,
    corpus=None,
    limit: int = 6,
    beam_width: int = 12,
    max_moments: int | None = None,
    target_seconds: float | None = None,
) -> StoryPlan:
    """Choose the strongest whole story.

    limit remains accepted for compatibility, but story composition no longer
    treats the social-output budget as the story length. Callers should prefer
    max_moments; the fallback keeps older callers deterministic.
    """
    if max_moments is None:
        max_moments = max(6, limit)

    variants = compose_story_variants(
        candidates,
        corpus=corpus,
        max_moments=max_moments,
        beam_width=beam_width,
        target_seconds=target_seconds,
    )
    if not variants:
        return StoryPlan((), 0.0, "none")

    best = variants[0]
    reasons = (
        f"searched {min(len(candidates), 72)} candidate moments with beam width {beam_width}",
        f"compared {len(variants)} editorial constructions",
        "whole-story scoring rewards setup, development, revelation, resolution, and measured payoff closure",
        f"story length is adaptive up to {max_moments} moments; --limit is not used as the discovery budget",
    )
    return StoryPlan(best.candidates, best.score, best.style, reasons)


__all__ = ["StoryPlan", "compose_story", "compose_story_variants"]
