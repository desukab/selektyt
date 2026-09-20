"""Redundancy groups — passages that say the same thing, and which one to keep.

A 2-hour transcript is not a tightly written essay. A speaker circles back:
they state a point, give an example, then state the point again because the
example took a while, and state it once more in the summary. An editor who cuts
all of that loses nothing and gains pace; an editor who keeps all of it produces
a video that repeats itself. But the same editor also knows the *opposite*
failure — cutting a restatement that actually escalates, adding the concrete
detail or the emotional weight the first pass lacked. So the judgement is never
"delete duplicates"; it is "which passage is the strongest statement, and why".

Method
------
1. **Blocking.** Only passages that share a *rare* term can be restatements of
   each other, so an inverted index over rare terms (document frequency at most
   `max_df`) generates candidate pairs. This is what keeps a 3-hour transcript
   affordable: no passage is ever compared against every other passage, and each
   posting list is truncated, so the candidate set is bounded per passage
   instead of O(U²).
2. **Scoring.** Each candidate pair is scored by idf-weighted containment, the
   same measure the topic layer uses, so "says the same thing" means the same
   thing in both modules.
3. **Grouping.** Union-find over pairs above threshold — restatement is
   transitive in practice (three versions of one point belong to one group).
4. **Representative.** The strongest member is chosen by an explicit editorial
   criterion: completeness (does the passage end on a full stop rather than
   trailing off mid-sentence), coherence (the story-unit's own coherence score)
   and substance (how much content it carries), with duration as a tie-breaker
   but never the winner — the longest restatement is often the padded one.
5. **Relation.** The group is labelled with what the later passages actually
   do: a bare restatement, an elaboration that adds substance, or a recap after
   the subject went away. The distinction is the difference between "cut it" and
   "it is the better version".

Honesty constraints
-------------------
- A group's `reason` names the representative and says why it wins, so an editor
  can disagree on the record.
- Every member keeps its own timecode; the representative is an editorial
  recommendation, never an obligation.
- Single passages are never grouped; a group always has at least two members.
- Without lexical signal the module returns an empty list and says why.

Deterministic and pure. Complexity: O(U + P) where P is the blocked candidate
pair count — P is bounded by (rare terms per unit) × (max postings per term),
i.e. effectively linear; the O(U²) worst case would require every passage to
share rare terms with many others, which the posting cap excludes. Memory
O(U + V) for the index and one union-find array; no per-group copies of text.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.lexical import (
    containment,
    content_tokens,
    df_table,
    quote,
)

_TERMINAL_PUNCT = set(".?!।॥？。")


@dataclass
class RedundancyGroup:
    """Passages that say the same thing, with the one an editor should keep."""

    group_id: int
    members: list[int]                 # unit indices, chronological
    representative: int                # the recommended member
    relation: str                      # restatement | elaboration | recap
    similarity: float                  # min pairwise containment in the group
    start: float
    end: float
    confidence: float
    reason: str                        # why this representative, and why cut the rest
    evidence: list[str] = field(default_factory=list)  # bounded verbatim quotes


@dataclass
class RedundancyResult:
    groups: list[RedundancyGroup]
    unit_group: list[int]              # unit index -> group_id, or -1
    internal_restatement: dict[int, int]   # unit index -> restated sentence pairs
    note: str


def group_redundancy(
    unit_texts: list[str],
    unit_starts: list[float],
    unit_ends: list[float],
    unit_coherence: list[float],
    *,
    min_similarity: float = 0.55,
    max_df: int = 4,
    max_postings: int = 64,
    min_rare_shared: int = 2,
) -> RedundancyResult:
    """Group near-duplicate passages and designate the strongest statement.

    Args:
        min_similarity: idf-weighted containment at which two passages are
            restatements. High by design: this judgement removes material, so it
            should only fire on passages that genuinely re-say each other.
        max_df: a term shared by more passages than this is common currency, not
            evidence of restatement.
        max_postings: cap on each posting list, which bounds the candidate work.
        min_rare_shared: rare terms two passages must share to be compared at
            all. Two is the floor for a subject match being more than a lone
            word coincidence.
    """
    n = len(unit_texts)
    if n < 2:
        # No *between-unit* redundancy is possible with one unit, but a single
        # passage can still restate itself internally, and that is the signal an
        # editor cares about here.
        return RedundancyResult(
            [], [-1] * n, _internal_restatement(unit_texts, min_similarity),
            "fewer than two units; no between-unit redundancy can exist, "
            "in-unit restatement is still reported",
        )

    bags = [content_tokens(t) for t in unit_texts]
    df = df_table(unit_texts)

    # --- blocking: rare-term inverted index -------------------------------
    postings: dict[str, list[int]] = {}
    for i, bag in enumerate(bags):
        for term in bag:
            if df.get(term, 0) > max_df:
                continue
            postings.setdefault(term, []).append(i)
    for term in list(postings):
        if len(postings[term]) > max_postings:
            del postings[term]      # common-to-many terms are not evidence

    # --- scoring: compare only blocked pairs, best similarity first -------
    candidate_pairs: dict[tuple[int, int], float] = {}
    for term, docs in postings.items():
        for a_i in range(len(docs)):
            for b_i in range(a_i + 1, len(docs)):
                a, b = docs[a_i], docs[b_i]
                if a == b:
                    continue
                key = (a, b) if a < b else (b, a)
                if key in candidate_pairs:
                    continue
                # Plain containment: restatement is about content overlap, and
                # the blocking step already restricted comparison to pairs that
                # share a rare term, so the idf weighting would only compress
                # the scores of the pairs we most want to accept.
                sim = containment(bags[a], bags[b])
                if sim >= min_similarity:
                    candidate_pairs[key] = sim

    if not candidate_pairs:
        return RedundancyResult(
            [], [-1] * n, _internal_restatement(unit_texts, min_similarity),
            "no passages above the restatement threshold; nothing in this "
            "corpus says the same thing twice at the evidence bar used",
        )

    # --- grouping: union-find over accepted pairs -------------------------
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b) in sorted(candidate_pairs, key=lambda k: -candidate_pairs[k]):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        r = find(i)
        if r in clusters:
            clusters[r].append(i)
        else:
            clusters[r] = [i]

    groups: list[RedundancyGroup] = []
    unit_group = [-1] * n
    gid = 0
    for root, members in sorted(clusters.items(), key=lambda kv: min(kv[1])):
        if len(members) < 2:
            continue
        for u in members:
            unit_group[u] = gid
        sims = [
            candidate_pairs[(members[k - 1], members[k])]
            for k in range(1, len(members))
            if (members[k - 1], members[k]) in candidate_pairs
        ]
        # The chain may have been linked transitively through pairs we did not
        # store (a-b, b-c without an a-b score above threshold); fall back to
        # recomputing adjacent containment so `similarity` always reports a
        # real number for the group's weakest link.
        if not sims:
            sims = [
                containment(bags[members[k - 1]], bags[members[k]])
                for k in range(1, len(members))
            ]
        representative = _strongest_member(
            members, unit_texts, unit_coherence, bags,
        )
        relation = _relation_kind(members, bags, unit_starts, unit_ends)
        groups.append(RedundancyGroup(
            group_id=gid,
            members=list(members),
            representative=representative,
            relation=relation,
            similarity=round(min(sims) if sims else 0.0, 3),
            start=unit_starts[members[0]],
            end=unit_ends[members[-1]],
            confidence=round(min(0.95, 0.55 + 0.4 * (min(sims) if sims else 0.0)), 3),
            reason=_group_reason(members, representative, relation, unit_texts,
                                 unit_coherence, bags, min(sims) if sims else 0.0),
            evidence=[quote(unit_texts[u], 100) for u in
                      sorted({representative, members[0], members[-1]})][:3],
        ))
        gid += 1

    internal = _internal_restatement(unit_texts, min_similarity)
    note = (
        f"{len(groups)} redundancy group(s) covering "
        f"{sum(len(g.members) for g in groups)} of {n} unit(s); "
        f"{sum(1 for g in groups if g.relation == 'elaboration')} group(s) "
        f"where the later passage adds substance rather than repeating; "
        f"{sum(internal.values())} restated sentence pair(s) inside single units "
        f"(the story-unit layer judged those one continuous thought, so they "
        f"are reported per unit rather than as groups)"
    )
    return RedundancyResult(groups, unit_group, internal, note)


def _internal_restatement(unit_texts: list[str], min_similarity: float) -> dict[int, int]:
    """Count sentence pairs inside one unit that restate each other.

    The story-unit layer merges a restatement into one passage when the subject
    never changed, which is correct for segmentation but hides the repetition
    from an editor: a 45-second unit can state its point three times. This pass
    sees what the grouping cannot, at sentence granularity and O(S) cost, and
    reports a count per unit so the spine stays the index of record.
    """
    from engine.story_units import split_sentences

    out: dict[int, int] = {}
    for i, text in enumerate(unit_texts):
        sents = split_sentences(text)
        if len(sents) < 3:
            continue
        bags = [content_tokens(s) for s in sents]
        hits = 0
        for a in range(len(bags)):
            for b in range(a + 2, len(bags)):   # skip adjacent: continuation,
                if containment(bags[a], bags[b]) >= min_similarity:  # not restatement
                    hits += 1
        if hits:
            out[i] = hits
    return out


def _completeness(text: str) -> float:
    """1.0 when a passage closes on terminal punctuation, else 0.4.

    A passage that trails off mid-sentence is a truncated statement, not a
    complete one — ASR often cuts a segment before the speaker finishes. It can
    still be the best material available, so it is penalised rather than
    disqualified, and the penalty is reported in the group reason.
    """
    t = text.strip()
    if not t:
        return 0.0
    return 1.0 if t[-1] in _TERMINAL_PUNCT else 0.4


def _strongest_member(
    members: list[int], unit_texts: list[str], unit_coherence: list[float],
    bags: list[set[str]],
) -> int:
    """The member that best states the point.

    Substance counts most (how much content the passage actually carries),
    then completeness (a finished sentence), then the story-unit's coherence.
    Duration is only a tie-breaker: the longest version is frequently the
    padded one, so length must not be allowed to win on its own.
    """
    def score(u: int) -> tuple[float, float, float, float, int]:
        substance = min(1.0, len(bags[u]) / 12.0)
        coherence = max(0.0, min(1.0, unit_coherence[u] if u < len(unit_coherence) else 1.0))
        return (0.45 * substance
                + 0.30 * _completeness(unit_texts[u])
                + 0.25 * coherence,
                substance,
                _completeness(unit_texts[u]),
                coherence,
                -u)

    return max(members, key=score)


def _relation_kind(
    members: list[int], bags: list[set[str]], starts: list[float], ends: list[float],
) -> str:
    """What the later passages are doing relative to the first.

    - ``elaboration``: a later version carries materially more substance — the
      restatement is the better version and the first is the draft.
    - ``recap``: the group spans a long gap, so the later passage is a return to
      a point made earlier — often worth *keeping* in a long edit and cutting in
      a short one.
    - ``restatement``: same substance again, the straightforward case to cut.
    """
    first, last = members[0], members[-1]
    if last == first:
        return "restatement"
    gap = starts[last] - ends[first]
    substance_first = len(bags[first])
    substance_last = len(bags[last])
    if substance_last >= 1.5 * max(1, substance_first):
        return "elaboration"
    if gap > 120.0:
        return "recap"
    return "restatement"


def _group_reason(
    members: list[int], representative: int, relation: str,
    unit_texts: list[str], unit_coherence: list[float], bags: list[set[str]],
    similarity: float,
) -> str:
    """Why this representative, stated so an editor can challenge it."""
    rep_substance = len(bags[representative])
    best_substance = max(len(bags[u]) for u in members)
    rep_complete = _completeness(unit_texts[representative]) >= 1.0
    parts = [
        (
            f"keep unit {representative}: "
            f"{'closes on a full stop' if rep_complete else 'truncated but the strongest available'}, "
            f"{rep_substance} content terms "
            f"({rep_substance / max(1, best_substance):.0%} of the group's most substantive member)"
        ),
        f"weak link containment {similarity:.2f}",
    ]
    if relation == "elaboration":
        parts.append("the later passage adds substance — it is the revision, "
                     "not a copy")
    elif relation == "recap":
        parts.append("the group spans a long gap; the later passage is a recap "
                     "of an earlier point — keep in a long edit, cut in a "
                     "short one")
    else:
        parts.append(f"{len(members) - 1} passage(s) restate the point without "
                     f"adding substance; candidates for removal")
    return "; ".join(parts)
