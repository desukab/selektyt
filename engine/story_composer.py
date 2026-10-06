"""Whole-story editorial composition.

Per-clip scoring answers "is this moment good?". This module answers the
different question: "does this sequence make sense as one story?"

The composer keeps the search bounded, but the objective is global: narrative
roles, setup/payoff links, recurring subjects, topic/thread continuity,
redundancy, chronology, opening strength and ending resolution are evaluated
across the complete sequence. Four constructions are exposed because they are
genuinely different ways of telling the same evidence: chronological,
cold-open, mystery and payoff-first.

No prose is invented here. The source transcript/corpus remains the evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from engine.lexical import content_tokens
from engine.selection import _covers
from models.core import Candidate


@dataclass(frozen=True)
class StoryPlan:
    candidates: tuple[Candidate, ...]
    score: float
    style: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Features:
    units: frozenset[int]
    threads: frozenset[int]
    topics: frozenset[int]
    roles: frozenset[str]
    links: tuple[tuple[int, int, float, str], ...]
    tokens: frozenset[str]


_STYLES = ("chronological", "cold_open", "mystery", "payoff_first")

_ROLE_VALUE = {
    "setup": 0.14,
    "tension": 0.13,
    "turn": 0.18,
    "revelation": 0.22,
    "resolution": 0.25,
    "continuation": 0.01,
}

_STYLE_ROLE_WEIGHTS = {
    "chronological": (
        {"setup": 0.18, "tension": 0.12},
        {"turn": 0.12, "revelation": 0.16},
        {"resolution": 0.24, "revelation": 0.12},
    ),
    "cold_open": (
        {"revelation": 0.32, "resolution": 0.30, "turn": 0.24, "tension": 0.10},
        {"setup": 0.24, "tension": 0.18, "continuation": 0.04},
        {"turn": 0.16, "revelation": 0.22, "resolution": 0.30},
    ),
    "mystery": (
        {"revelation": 0.24, "turn": 0.20, "tension": 0.20},
        {"setup": 0.24, "tension": 0.20, "turn": 0.12},
        {"revelation": 0.20, "resolution": 0.32, "turn": 0.18},
    ),
    "payoff_first": (
        {"resolution": 0.34, "revelation": 0.30, "turn": 0.22},
        {"setup": 0.26, "tension": 0.20, "continuation": 0.04},
        {"turn": 0.18, "revelation": 0.22, "resolution": 0.30},
    ),
}


def _features(c: Candidate, corpus) -> _Features:
    if corpus is None:
        return _Features(frozenset(), frozenset(), frozenset(), frozenset(), (), frozenset(content_tokens(c.text)))

    units: set[int] = set()
    threads: set[int] = set()
    topics: set[int] = set()
    roles: set[str] = set()
    for i, u in enumerate(corpus.units):
        if not _covers(corpus, i, c):
            continue
        units.add(i)
        if u.thread_id is not None:
            threads.add(u.thread_id)
        if u.topic_id is not None and u.topic_id >= 0:
            topics.add(u.topic_id)
        if u.beat_id is not None and 0 <= u.beat_id < len(corpus.beats):
            roles.add(corpus.beats[u.beat_id].kind)

    links = tuple(
        (link.setup_unit, link.payoff_unit, link.confidence, link.kind)
        for link in corpus.links
        if link.setup_unit in units or link.payoff_unit in units
    )

    tokens = set(content_tokens(c.text))
    for ui in units:
        if 0 <= ui < len(corpus.units):
            tokens.update(content_tokens(corpus.units[ui].text))

    return _Features(
        frozenset(units), frozenset(threads), frozenset(topics),
        frozenset(roles), links, frozenset(tokens),
    )


def _index(candidates: Sequence[Candidate], corpus) -> dict[int, _Features]:
    return {id(c): _features(c, corpus) for c in candidates}


def _thread_quality(corpus, tid: int) -> float:
    if corpus is None or not 0 <= tid < len(corpus.threads):
        return 0.0
    t = corpus.threads[tid]
    return (
        0.20 * min(1.0, len(t.unit_indices) / 6.0)
        + 0.40 * min(1.0, len(t.links) / 4.0)
        + 0.40 * max(0.0, min(1.0, t.confidence))
    )


def _candidate_pool(
    candidates: Sequence[Candidate],
    corpus,
    width: int = 120,
    features: dict[int, _Features] | None = None,
) -> list[Candidate]:
    """Preserve story material before whole-story search.

    The previous pool took global winners and then deliberately added unrelated
    threads. On a feature-film transcript that selected isolated punchlines
    from dozens of local conversations. This pool preserves the strongest
    evidenced narrative threads, dramatic functions, source regions and global
    winners.
    """
    if len(candidates) <= width:
        return list(candidates)

    features = features or _index(candidates, corpus)
    ranked = sorted(candidates, key=lambda c: c.total_score, reverse=True)
    chosen: list[Candidate] = []
    chosen_ids: set[int] = set()

    def add(c: Candidate) -> None:
        if len(chosen) >= width or id(c) in chosen_ids:
            return
        chosen.append(c)
        chosen_ids.add(id(c))

    for c in ranked[:24]:
        add(c)

    tids: set[int] = set()
    for f in features.values():
        tids.update(f.threads)
    ordered_tids = sorted(tids, key=lambda tid: _thread_quality(corpus, tid), reverse=True)

    for tid in ordered_tids[:28]:
        members = [c for c in candidates if tid in features[id(c)].threads]
        members.sort(
            key=lambda c: (
                1 if features[id(c)].links else 0,
                c.total_score,
                c.opening_score,
            ),
            reverse=True,
        )
        for c in members[:3]:
            add(c)

    for role in ("setup", "tension", "turn", "revelation", "resolution"):
        members = [c for c in candidates if role in features[id(c)].roles]
        for c in sorted(members, key=lambda x: x.total_score, reverse=True)[:10]:
            add(c)

    duration = max((c.end for c in candidates), default=0.0)
    if duration:
        for bucket in range(12):
            lo = duration * bucket / 12
            hi = duration * (bucket + 1) / 12
            members = [c for c in candidates if lo <= c.start < hi]
            for c in sorted(members, key=lambda x: x.total_score, reverse=True)[:4]:
                add(c)

    for c in ranked:
        add(c)
        if len(chosen) >= width:
            break
    return chosen


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _links_between(a: _Features, b: _Features) -> float:
    best = 0.0
    for setup, payoff, confidence, _kind in a.links + b.links:
        if setup in a.units and payoff in b.units:
            best = max(best, confidence)
        if setup in b.units and payoff in a.units:
            best = max(best, confidence)
    return best


def _same_story_affinity(a: _Features, b: _Features) -> float:
    thread = 1.0 if a.threads & b.threads else 0.0
    topic = min(1.0, len(a.topics & b.topics) / 2.0)
    lexical = _jaccard(a.tokens, b.tokens)
    return max(thread, 0.75 * topic, lexical)


def _role_position_score(roles: frozenset[str], position: int, length: int, style: str) -> float:
    if not roles:
        return 0.0
    profiles = _STYLE_ROLE_WEIGHTS[style]
    desired = profiles[0] if position == 0 else profiles[2] if position == length - 1 else profiles[1]
    return max((weight for role, weight in desired.items() if role in roles), default=0.0)


def _pair_score(a: Candidate, b: Candidate, fa: _Features, fb: _Features, style: str) -> float:
    score = 0.0
    affinity = _same_story_affinity(fa, fb)
    if affinity >= 0.50:
        score += 0.16
    elif affinity >= 0.22:
        score += 0.08

    closure = _links_between(fa, fb)
    if closure:
        score += 0.24 * closure

    if b.start >= a.end:
        if "setup" in fa.roles:
            score += 0.05
        if fb.roles & {"turn", "revelation", "resolution"}:
            score += 0.08
    elif b.end > a.start:
        score -= 0.50

    if a.text and b.text:
        from engine.editorial import _similarity
        if _similarity(a.text, b.text) >= 0.78:
            score -= 0.24

    if style == "chronological" and b.start >= a.end:
        score += 0.07

    gap = max(0.0, b.start - a.end)
    if gap > 900 and closure == 0 and affinity < 0.22:
        score -= min(0.30, 0.06 + gap / 5000.0)
    return score


def _whole_score(
    seq: Sequence[Candidate],
    corpus,
    features: dict[int, _Features],
    style: str,
    target_seconds: float | None = None,
) -> float:
    if not seq:
        return -1.0

    fs = [features[id(c)] for c in seq]
    score = sum(
        c.total_score + sum(_ROLE_VALUE.get(r, 0.0) for r in f.roles)
        for c, f in zip(seq, fs)
    )

    score += 0.28 * max(0.0, min(1.0, seq[0].opening_score))
    if fs[0].roles & {"revelation", "resolution", "turn"}:
        score += 0.14
    if fs[-1].roles & {"resolution", "revelation"}:
        score += 0.34

    for i, (a, b) in enumerate(zip(seq, seq[1:]), 1):
        score += _pair_score(a, b, fs[i - 1], fs[i], style)
        score += _role_position_score(fs[i].roles, i, len(seq), style)

    closure_count = 0
    closure_strength = 0.0
    for i, fa in enumerate(fs):
        for j in range(i + 1, len(fs)):
            strength = _links_between(fa, fs[j])
            if strength:
                closure_count += 1
                closure_strength += strength
    score += min(0.72, 0.20 * closure_count + 0.18 * closure_strength)

    thread_counts: dict[int, int] = {}
    topic_counts: dict[int, int] = {}
    for f in fs:
        for tid in f.threads:
            thread_counts[tid] = thread_counts.get(tid, 0) + 1
        for topic in f.topics:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1

    if thread_counts:
        top = max(thread_counts.values())
        score += min(0.36, 0.08 * top)
        if len(thread_counts) > 5:
            score -= 0.05 * (len(thread_counts) - 5)
    if topic_counts:
        repeated_topics = sum(1 for n in topic_counts.values() if n >= 2)
        score += min(0.24, 0.06 * repeated_topics)

    if len(fs) >= 3:
        affinities = [_same_story_affinity(fs[i - 1], fs[i]) for i in range(1, len(fs))]
        score += 0.18 * sum(affinities) / len(affinities)

    for i, a in enumerate(seq):
        for b in seq[i + 1:]:
            if a.text and b.text:
                from engine.editorial import _similarity
                sim = _similarity(a.text, b.text)
                if sim >= 0.78:
                    score -= 0.20
                elif sim >= 0.55:
                    score -= 0.07

    duration = sum(max(0.0, c.end - c.start) for c in seq)
    if target_seconds and target_seconds > 0:
        ratio = duration / target_seconds
        if 0.70 <= ratio <= 1.15:
            score += 0.14
        elif ratio < 0.30:
            score -= 0.10
        elif ratio > 1.80:
            score -= 0.08

    score -= 0.14 * len(seq)
    score -= max(0, len(seq) - 8) * 0.035
    return score


def _eligible_next(seq: Sequence[Candidate], c: Candidate, style: str) -> bool:
    if id(c) in {id(x) for x in seq}:
        return False
    if any(not (c.end <= x.start + 1e-6 or c.start >= x.end - 1e-6) for x in seq):
        return False
    if not seq:
        return True
    last = seq[-1]
    return c.start >= last.end - 1e-6


def _start_candidates(pool: list[Candidate], features: dict[int, _Features], style: str) -> list[Candidate]:
    if style == "chronological":
        return sorted(pool, key=lambda c: (c.start, -c.total_score))[:48]

    def opener(c: Candidate) -> float:
        roles = features[id(c)].roles
        role = (
            0.40 if "resolution" in roles else
            0.34 if "revelation" in roles else
            0.25 if "turn" in roles else
            0.10 if "tension" in roles else 0.0
        )
        return role + 0.30 * c.opening_score + 0.20 * c.total_score

    return sorted(pool, key=lambda c: (opener(c), c.total_score), reverse=True)[:36]


def _beam(
    pool: list[Candidate],
    corpus,
    features: dict[int, _Features],
    max_moments: int,
    style: str,
    beam_width: int,
    target_seconds: float | None,
) -> StoryPlan:
    starts = _start_candidates(pool, features, style)
    states: list[tuple[list[Candidate], float]] = [
        ([c], _whole_score([c], corpus, features, style, target_seconds))
        for c in starts
    ]
    states.sort(key=lambda x: x[1], reverse=True)
    states = states[:beam_width]
    all_states = list(states)

    for _ in range(1, max_moments):
        expanded: list[list[Candidate]] = []
        for seq, _score in states:
            for c in pool:
                if _eligible_next(seq, c, style):
                    expanded.append(seq + [c])

        if not expanded:
            break

        rescored = [
            (seq, _whole_score(seq, corpus, features, style, target_seconds))
            for seq in expanded
        ]
        rescored.sort(key=lambda x: x[1], reverse=True)

        next_states: list[tuple[list[Candidate], float]] = []
        seen: set[tuple[tuple[float, float], ...]] = set()
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
    features = _index(candidates, corpus)
    pool = _candidate_pool(candidates, corpus, features=features)
    if not pool:
        return ()
    plans = tuple(
        _beam(pool, corpus, features, max_moments, style, beam_width, target_seconds)
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
        f"searched {min(len(candidates), 120)} narrative candidates with beam width {beam_width}",
        f"compared {len(variants)} evidence-preserving editorial constructions",
        "whole-story scoring evaluates opening, role progression, narrative continuity, setup/payoff closure, repetition, and ending",
        "non-chronological constructions use one deliberate opening jump, then return to source order rather than arbitrary clip permutation",
        f"story length is adaptive up to {max_moments} moments; --limit remains an output budget",
    )
    return StoryPlan(best.candidates, best.score, best.style, reasons)


__all__ = ["StoryPlan", "compose_story", "compose_story_variants"]
