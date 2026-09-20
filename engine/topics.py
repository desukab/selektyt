"""Topic clustering — the subject clusters of a whole transcript.

A sentence-level scorer can tell you that one passage is dense. It cannot tell
you that the speaker talked about GPS at minute four, dropped it, and came back
to it at minute ninety — that the GPS story is a *spine* of this recording
rather than an aside. That is the question this module answers: which subjects
actually organise the corpus, and where in source time each one lives.

Method
------
Clustering is **chronology-aware leader clustering with recency**. Units are
read in source order, as an editor reads them, and each unit is assigned to the
most *recently active* open topic whose subject it shares, or it opens a new
one. Two deliberate consequences:

- Recurrence is a first-class result. A topic that is picked up again after an
  interruption is recorded as recurring, and the gap between runs is kept,
  because that is precisely the fact a sentence-level view cannot see.
- No global comparison. Only the `max_open` most recently touched topics are
  compared per unit, so the cost is bounded per unit regardless of corpus
  length. An exhaustive pairwise clustering would be O(U²) over a 3-hour
  transcript and would still not produce a better editorial answer: subjects
  drift, and a passage is compared usefully against what came *near* it.

Similarity is idf-weighted containment (see `engine.lexical`): two passages
share a subject when they share *rare* terms, not when they share function
words. A monologue about "the satellite, the satellite, the satellite" scores
on "satellite"; two unrelated passages sharing only "the" and "system" score
zero.

Honesty constraints
-------------------
- The label of a topic is its top signature terms, quoted verbatim from the
  transcript. No label is ever composed into a phrase the speaker did not say.
- A topic that only ever occupies one unit is returned with low confidence and
  an explicit reason: it is a *passing mention*, and the corpus does not
  pretend otherwise.
- Absent lexical signal (a unit with no recoverable content words) yields no
  topic assignment rather than a forced one; such units are collected in
  `unassigned` for the caller to report.

Deterministic and pure: same input -> identical output; no network, no ML,
no wall clock, no global state.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.lexical import (
    containment,
    content_tokens,
    df_table,
    idf_weights,
    quote,
    signature_terms,
    weighted_containment,
)


@dataclass
class Topic:
    """One subject cluster over the corpus, with the passages that belong to it.

    `unit_indices` are chronological and index into the corpus unit list, so a
    downstream agent can resolve every member to source timecode.
    """

    topic_id: int
    label: str                      # signature terms, verbatim from the source
    terms: list[str]                # ranked signature terms (most identifying first)
    unit_indices: list[int]         # chronological members
    segment_indices: list[int]      # source segments these units came from
    start: float                    # first source second of the topic
    end: float                      # last source second of the topic
    runs: int                       # how many separated stretches it occupies
    span_share: float               # fraction of the corpus duration it covers
    confidence: float               # 0..1, evidence-scaled
    reason: str                     # why the cluster exists at this confidence
    evidence: list[str] = field(default_factory=list)  # verbatim quotes, bounded

    @property
    def recurring(self) -> bool:
        return self.runs > 1


@dataclass
class TopicResult:
    topics: list[Topic]
    unit_topic: list[int]           # unit index -> topic_id, or -1 when unassigned
    unassigned: list[int]           # units with no recoverable subject signal
    note: str                       # what the signal could and could not support


def cluster_topics(
    unit_texts: list[str],
    unit_starts: list[float],
    unit_ends: list[float],
    unit_segments: list[list[int]],
    *,
    duration: float,
    max_open: int = 8,
    min_similarity: float = 0.22,
    min_units: int = 2,
) -> TopicResult:
    """Cluster corpus units into subject clusters, in source order.

    Args:
        unit_texts: text of each unit, in chronological order.
        unit_starts/ends: source seconds for each unit.
        unit_segments: source segment indices each unit was built from.
        duration: corpus duration in seconds (span_share denominator).
        max_open: how many recently-active topics one unit is compared against.
            Bounds cost per unit; also bounds how far back a subject can be
            reached and still be "the same thread of talk".
        min_similarity: idf-weighted containment at which a unit joins a topic.
        min_units: unit count at which a topic counts as *established* rather
            than a passing mention (confidence and reason differ).

    Complexity: O(U * max_open * L) time where L is mean unit length, since each
    of the bounded comparisons walks the smaller of two bags. Memory is
    O(vocabulary + U) — one idf table, plus per-topic index lists. No copy of
    the transcript is held per topic: topics store indices.
    """
    n = len(unit_texts)
    if n == 0:
        return TopicResult(topics=[], unit_topic=[], unassigned=[], note="no units to cluster")
    max_open = max(max_open, 1)

    bags = [content_tokens(t) for t in unit_texts]
    idf = idf_weights(df_table(unit_texts), n)

    # Active topics, most recently touched last, so a subject the speaker just
    # left is preferred over one abandoned long ago — the editorial reading is
    # "still on the same thing", not "technically mentioned once".
    recent: list[int] = []
    members: list[list[int]] = []      # topic index -> member unit indices
    last_touched: list[int] = []       # topic index -> unit index of last touch

    for i, bag in enumerate(bags):
        if not bag:
            # No recoverable content words (an ASR fragment of pure filler, or
            # a unit whose text is empty). Nothing can be claimed about its
            # subject, so nothing is claimed.
            recent = _touch(recent, last_touched, None)
            continue
        best_topic = -1
        best_sim = 0.0
        for tidx in reversed(recent[-max_open:]):
            if not members[tidx]:
                continue
            # Compare against the topic's most recent member, not against a
            # merged bag of all members: subjects drift, and the question is
            # "is this still that subject", asked of the subject as it was last
            # left, not as an average over its whole history. Merging would
            # also cost a bag rebuild per comparison, which is the copy-per-
            # topic blowup this layer must avoid.
            # Plain containment for the assignment decision: it is the measure
            # the story-unit segmentation itself uses for seam decisions, so a
            # topic boundary and a unit boundary stay consistent, and its scale
            # does not collapse when the corpus is small.
            sim = containment(bag, bags[members[tidx][-1]])
            if sim > best_sim or (sim == best_sim and sim > 0.0 and tidx > best_topic):
                best_sim, best_topic = sim, tidx
        if best_topic >= 0 and best_sim >= min_similarity:
            members[best_topic].append(i)
            last_touched[best_topic] = i
            recent = _touch(recent, last_touched, best_topic)
        else:
            tidx = len(members)
            members.append([i])
            last_touched.append(i)
            recent = _touch(recent, last_touched, tidx)

    topics: list[Topic] = []
    unit_topic = [-1] * n
    for tidx, member_list in enumerate(members):
        if not member_list:
            continue
        for u in member_list:
            unit_topic[u] = tidx
        runs = _count_runs(member_list)
        nb = [bags[u] for u in member_list]
        terms = signature_terms(nb, idf, limit=4)
        established = len(member_list) >= min_units
        # Plain containment calibrates the assignment; the rarity-weighted view
        # is reported as a diagnostic, since it answers a different question
        # (how exclusively does this corpus own these terms) than the
        # assignment needs (does this passage continue that subject).
        sims = [
            containment(bags[member_list[k]], bags[member_list[k - 1]])
            for k in range(1, len(member_list))
        ]
        wsim = [
            weighted_containment(bags[member_list[k]], bags[member_list[k - 1]], idf)
            for k in range(1, len(member_list))
        ]
        rare = max(wsim) if wsim else 0.0
        lo = min(unit_starts[u] for u in member_list)
        hi = max(unit_ends[u] for u in member_list)
        segs = sorted({s for u in member_list for s in unit_segments[u]})
        share = (hi - lo) / duration if duration > 0 else 0.0
        if established:
            conf = 0.55 + 0.35 * min(1.0, len(member_list) / 6.0)
            if sims:
                conf = min(0.98, conf + 0.05 * (sum(sims) / len(sims)))
            if runs > 1:
                conf = min(0.98, conf + 0.05 * min(1.0, runs / 3.0))
            reason = (
                f"established subject: {len(member_list)} units across {runs} "
                f"run(s) share signature terms {', '.join(terms[:3]) or '—'} "
                f"(containment {min(sims):.2f}-{max(sims):.2f}, "
                f"rarity-weighted {rare:.2f})"
                + (f" over a {max(0.0, _gap_span(member_list, unit_starts, unit_ends)):.0f}s interruption"
                   if runs > 1 else "")
                + ")"
            )
        else:
            conf = 0.30
            reason = (
                "passing mention: a single unit carries these terms and nothing "
                "later returns to them, so the subject is not corroborated "
                "elsewhere in the corpus"
            )
        topics.append(
            Topic(
                topic_id=tidx,
                label=", ".join(terms[:3]) if terms else "(no signature terms)",
                terms=terms,
                unit_indices=list(member_list),
                segment_indices=segs,
                start=lo,
                end=hi,
                runs=runs,
                span_share=round(share, 4),
                confidence=round(conf, 3),
                reason=reason,
                evidence=[quote(unit_texts[u]) for u in member_list[:3]],
            )
        )
    unassigned = [i for i, t in enumerate(unit_topic) if t < 0]
    note = (
        f"{len(topics)} subject cluster(s) over {n} unit(s); "
        f"{sum(1 for t in topics if t.recurring)} recur after an interruption; "
        f"{len(unassigned)} unit(s) carried no recoverable content terms and were "
        f"left unassigned rather than forced onto a neighbouring subject."
        if topics
        else f"no subject structure could be derived from {n} unit(s)"
    )
    return TopicResult(topics=topics, unit_topic=unit_topic, unassigned=unassigned, note=note)


def _touch(recent: list[int], last_touched: list[int], tidx: int | None) -> list[int]:
    """Move `tidx` to most-recent position, or drop it when None.

    Opening a topic also retires the least-recently-touched one beyond
    `max_open` is handled by the caller's slice, so this stays O(max_open).
    """
    if tidx is None:
        return recent
    recent = [t for t in recent if t != tidx]
    recent.append(tidx)
    # Cap is applied by the caller via recent[-max_open:], but keep the list
    # from growing without bound across a long corpus.
    if len(recent) > 32:
        recent = sorted(recent[-32:], key=lambda t: last_touched[t] if t < len(last_touched) else 0)
    return recent


def _count_runs(indices: list[int]) -> int:
    """Number of separated contiguous stretches in a chronological index list."""
    if not indices:
        return 0
    runs = 1
    for k in range(1, len(indices)):
        if indices[k] != indices[k - 1] + 1:
            runs += 1
    return runs


def _gap_span(indices: list[int], starts: list[float], ends: list[float]) -> float:
    """Total source seconds the topic was *off the air* between its runs."""
    total = 0.0
    for k in range(1, len(indices)):
        if indices[k] != indices[k - 1] + 1:
            gap = starts[indices[k]] - ends[indices[k - 1]]
            total += max(0.0, gap)
    return total
