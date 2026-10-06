"""Compose a set of source moments into a coherent editorial story.

This layer is deliberately above per-clip scoring.  Candidates are evidence
from the source; a story is a relationship between those candidates.  The
composer keeps several plausible editorial constructions alive, scores the
whole construction, and returns the strongest one under the caller's clip
budget.

It does not invent text or synthesize events.  It only reuses corpus threads,
beats, setup/payoff links, and candidate measurements already produced by
SeleKyT.
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
    "turn": 0.13,
    "revelation": 0.16,
    "resolution": 0.20,
    "continuation": 0.02,
}


def _thread_ids(c: Candidate, corpus) -> set[int]:
    if corpus is None:
        return set()
    out: set[int] = set()
    for i, u in enumerate(corpus.units):
        if _covers(corpus, i, c):
            if u.thread_id is not None:
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
    """Number of measured setup/payoff links closed by the pair."""
    if corpus is None:
        return 0
    tids = _thread_ids(a, corpus) & _thread_ids(b, corpus)
    total = 0
    for tid in tids:
        if not 0 <= tid < len(corpus.threads):
            continue
        thread = corpus.threads[tid]
        for link in thread.links:
            if (_covers(corpus, link.setup_unit, a) and
                    _covers(corpus, link.payoff_unit, b)):
                total += 1
            elif (_covers(corpus, link.setup_unit, b) and
                  _covers(corpus, link.payoff_unit, a)):
                total += 1
    return total


def _pair_score(a: Candidate, b: Candidate, corpus, style: str) -> float:
    score = 0.0

    # Keep measured story relationships together.
    if _thread_ids(a, corpus) & _thread_ids(b, corpus):
        score += 0.10
    score += 0.18 * min(1.0, _links_closed(a, b, corpus))

    # A later moment that resolves an earlier one is a strong narrative move.
    if b.start >= a.end:
        if "setup" in _roles(a, corpus):
            score += 0.05
        if _roles(b, corpus) & {"turn", "revelation", "resolution"}:
            score += 0.08

    # Avoid stitching two near-identical moments together.
    if a.text and b.text:
        from engine.editorial import _similarity
        if _similarity(a.text, b.text) >= 0.78:
            score -= 0.18

    # Different construction styles are intentionally explicit.  They are not
    # claims about what a human editor must prefer; they are alternative search
    # paths over the same measured material.
    if style == "chronological" and b.start >= a.start:
        score += 0.035
    elif style == "cold_open":
        if len(a.text.split()) >= 12:
            score += 0.025
    elif style == "mystery":
        if "setup" in _roles(b, corpus) or "tension" in _roles(b, corpus):
            score += 0.025
    elif style == "payoff_first":
        if _roles(a, corpus) & {"revelation", "resolution"}:
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
    """Keep a diverse, bounded search frontier rather than factorial enumeration."""
    if len(candidates) <= width:
        return list(candidates)

    ranked = sorted(candidates, key=lambda c: c.total_score, reverse=True)
    chosen: list[Candidate] = []
    seen_threads: set[int] = set()

    # Preserve the strongest general candidates first.
    chosen.extend(ranked[: min(24, width)])

    # Then deliberately admit strong material from distinct story threads.
    for c in ranked[24:]:
        tids = _thread_ids(c, corpus)
        if tids - seen_threads:
            chosen.append(c)
            seen_threads.update(tids)
        if len(chosen) >= width:
            break

    # Finally fill the frontier by score.
    if len(chosen) < width:
        chosen_ids = {id(c) for c in chosen}
        chosen.extend(c for c in ranked if id(c) not in chosen_ids)
    return chosen[:width]


def _beam(candidates: list[Candidate], corpus, limit: int, style: str,
          beam_width: int = 12) -> StoryPlan:
    if not candidates or limit <= 0:
        return StoryPlan((), 0.0, style)

    states: list[tuple[list[Candidate], float]] = [
        ([c], _node_score(c, corpus, style)) for c in candidates
    ]
    states.sort(key=lambda x: x[1], reverse=True)
    states = states[:beam_width]

    for _ in range(1, limit):
        expanded: list[tuple[list[Candidate], float]] = []
        for seq, score in states:
            used = {id(c) for c in seq}
            for c in candidates:
                if id(c) in used:
                    continue
                delta = _node_score(c, corpus, style)
                delta += _pair_score(seq[-1], c, corpus, style)

                # Penalise returning to the same story after unrelated material.
                tids = _thread_ids(c, corpus)
                previous_tids = set().union(*(_thread_ids(x, corpus) for x in seq))
                if tids and previous_tids and not (tids & previous_tids):
                    delta -= 0.025

                expanded.append((seq + [c], score + delta))

        if not expanded:
            break
        expanded.sort(key=lambda x: x[1], reverse=True)
        # De-duplicate equivalent source spans in the beam.
        seen: set[tuple[tuple[float, float], ...]] = set()
        next_states: list[tuple[list[Candidate], float]] = []
        for seq, score in expanded:
            key = tuple((round(c.start, 3), round(c.end, 3)) for c in seq)
            if key in seen:
                continue
            seen.add(key)
            next_states.append((seq, score))
            if len(next_states) >= beam_width:
                break
        states = next_states

    best_seq, best_score = max(states, key=lambda x: x[1])
    return StoryPlan(tuple(best_seq), best_score, style)


def compose_story(
    candidates: Sequence[Candidate],
    *,
    corpus=None,
    limit: int = 6,
    beam_width: int = 12,
) -> StoryPlan:
    """Find the best whole-edit story from a bounded editorial search.

    The search deliberately evaluates several constructions instead of
    pretending there is one universal definition of a good edit.
    """
    pool = _candidate_pool(candidates, corpus)
    if not pool:
        return StoryPlan((), 0.0, "none")

    styles = ("chronological", "cold_open", "mystery", "payoff_first")
    plans = [
        _beam(pool, corpus, limit, style, beam_width=beam_width)
        for style in styles
    ]
    best = max(plans, key=lambda p: p.score)

    reasons = [
        f"searched {len(pool)} candidate moments with beam width {beam_width}",
        f"compared {len(plans)} editorial constructions",
        "whole-edit score rewards measured story roles and setup/payoff continuity",
        "candidate count is a budget; story discovery remains independent of --limit",
    ]
    return StoryPlan(best.candidates, best.score, best.style, tuple(reasons))


__all__ = ["StoryPlan", "compose_story"]
