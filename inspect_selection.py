"""Inspect what the changed selection does on a pool shaped like the benchmark's.

The benchmark's ``truncated_by_limit = 6`` against ``selected_count = 6`` means
twelve candidates passed the gate for six slots, so the policy had six real
alternatives to choose among. This builds a pool of that shape from the fixture
corpus -- one candidate per complete story arc, plus a dense run of
independently strong but isolated moments in one window -- and prints both the
old head-of-list selection and the new policy's, with the term behind every move.
"""
from __future__ import annotations

from engine.corpus import build_corpus
from engine.selection import select
from tests.story_fixtures import varied_transcript

LIMIT = 6

segs = varied_transcript(96)
corpus = build_corpus(segs, duration=max(s.end for s in segs), language="en")

print("source: %.0fs  corpus: %d units / %d threads / %d links"
      % (max(s.end for s in segs), len(corpus.units), len(corpus.threads), len(corpus.links)))

# One candidate per thread: the span that carries its setup through its payoff.
# ``rank`` skips any candidate within 4s of one already chosen, so the pool the
# policy receives never contains two near-coincident candidates; the arcs are
# filtered the same way so the pool respects that invariant.
CLUSTER = 900.0
arcs = []
for t in sorted(corpus.threads, key=lambda x: -(x.end - x.start)):
    us = sorted(t.unit_indices)
    a, b = corpus.units[us[0]].start, corpus.units[us[-1]].end
    if b - a < 8.0:
        continue
    if b > CLUSTER - 120.0:
        continue                      # keep the arcs clear of the cluster below
    if any(not (b <= p - 60.0 or a >= p + 60.0) for p, _q, _t in arcs):
        continue                      # one arc per local scene, as rank would
    arcs.append((a, b, "arc of %r" % t.arc))

# The clustered region: five punchy isolated moments inside ~120s, each one
# scored a little higher than the arcs, so a sort-and-take takes all of them.
punches = [
    "The reactor scrammed without warning and nobody in the room could say why.",
    "Every gauge on the back wall swung hard to the right at the same moment.",
    "The coolant pump cavitated and the whole primary loop shuddered sideways.",
    "A warning light nobody had ever seen lit up above the night supervisor.",
    "The duty engineer was called twice at three am and did not answer either.",
]
cluster = [(CLUSTER + i * 22.0, CLUSTER + i * 22.0 + 18.0, p) for i, p in enumerate(punches)]


def mk(a, b, text, total, opening=1.0):
    from models.core import Candidate
    return Candidate(
        a, b, text, "story", text[:44], text,
        editorial_score=total, virality_score=total, context_score=total,
        confidence=0.9, reason="opening=%.2f" % opening,
        visual_potential=total, words=None, coherence_score=1.0,
        completeness=1.0, opening_score=opening,
    )


pool = ([mk(a, b, text, 0.58) for a, b, text in arcs]
        + [mk(a, b, text, 0.62, opening=0.85) for a, b, text in cluster])
print("pool: %d candidates (%d arcs, %d clustered punchlines)"
      % (len(pool), len(arcs), len(cluster)))
print()

old = sorted(pool, key=lambda c: c.total_score, reverse=True)[:LIMIT]
print("=== OLD: sort by score, take %d ===" % LIMIT)
for c in old:
    print("  %7.1f-%-7.1f  %.3f" % (c.start, c.end, c.total_score))
old_starts = sorted(round(c.start, 1) for c in old)
print("  starts:", old_starts)
in_cluster = sum(1 for s in old_starts if CLUSTER - 5 <= s <= CLUSTER + 120)
print("  -> %d of %d from the one %.0fs window" % (in_cluster, LIMIT, 120.0))
print()

report = select(pool, limit=LIMIT, corpus=corpus)
print("=== NEW: the policy's %d ===" % LIMIT)
for s in report.selected:
    c = s.candidate
    adj = "; ".join("%s %+.3f" % (n.term, n.delta) for n in s.notes) or "no term fired"
    print("  %7.1f-%-7.1f  base %.3f -> %.3f  %s" % (c.start, c.end, s.base, s.score, adj))
new_starts = sorted(round(c.start, 1) for c in report.candidates)
print("  starts:", new_starts)
in_cluster = sum(1 for s in new_starts if CLUSTER - 5 <= s <= CLUSTER + 120)
print("  -> %d of %d from the one %.0fs window" % (in_cluster, LIMIT, 120.0))
print()
print("deferred to the bench: %d" % len(report.deferred))
for s in report.deferred:
    c = s.candidate
    adj = "; ".join("%s %+.3f" % (n.term, n.delta) for n in s.notes) or "-"
    print("  %7.1f-%-7.1f  base %.3f -> %.3f  %s" % (c.start, c.end, s.base, s.score, adj))
