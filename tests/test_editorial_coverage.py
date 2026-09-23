"""make_thoughts must search the whole source, not just its prefix.

The candidate scan used to return once it had emitted a fixed number of
candidates. Candidates are built in start order, so on a long source that cut
the tail outright: 36% of one committed transcript and 30% of another
generated zero candidates, and no story there could ever be selected. At the
product's 1-3 hour target the cut landed near the 18 minute mark of a 3 hour
source -- most of the video was never searched.

These pin the two properties the replacement has to hold at once: the tail is
reachable on a real-length source, and a pathological sub-segment transcript
still cannot blow the scan up.
"""

from __future__ import annotations

from engine.editorial import make_thoughts
from models.core import Segment


def _varied(n: int, dur: float) -> list[Segment]:
    """A plausible-density transcript: distinct vocabulary per segment, so the
    lexical scorers see real text rather than a repeated string. ``dur`` is
    what makes a case pathological, not the vocabulary."""
    out = []
    for i in range(n):
        text = (f"This is segment number {i} and it says something quite "
                f"specific about topic {i % 17} that a transcript might hold.")
        out.append(Segment(i * dur, i * dur + dur, text, []))
    return out


def test_long_source_produces_candidates_near_its_end():
    """The last minutes of a long source must still yield candidates.

    The source is longer than the old fixed cap could cover and emits more
    candidates than that cap allowed, so a prefix-truncating scan would have
    stopped well before the end.
    """
    segs = _varied(700, 8.0)          # ~93 minutes
    end = segs[-1].end

    thoughts = make_thoughts(segs, min_sec=6.0)

    assert len(thoughts) > 4000, (
        "this source must exceed the old cap for the test to mean anything")
    # A candidate whose window lies in the final 10% of the source.
    tail = [t for t in thoughts if t.start >= 0.9 * end]
    assert tail, (
        f"no candidate in the final 10% of a {end / 60:.0f} minute source; "
        f"the last candidate ends at "
        f"{max((t.end for t in thoughts), default=0.0) / 60:.1f} min")


def test_every_start_is_searched_at_normal_density():
    """A source under the budget is scanned exactly: no starting position is
    skipped. That is what makes the change additive on real media rather than a
    re-roll of every selection the corpus has already committed."""
    segs = _varied(200, 8.0)
    starts = {round(s.start, 3) for s in segs}
    found = {round(t.start, 3) for t in make_thoughts(segs, min_sec=6.0)}
    assert found == starts


def test_sub_second_transcript_stays_bounded():
    """A transcript with a segment every second — denser than any human
    utterance, and within what ASR produces on fast or noisy audio — has
    hundreds of thousands of overlapping windows. The budget strides across
    starting positions, so the work stays bounded while every part of the
    source is still sampled: bounding the cost by *not* searching the tail
    would just reintroduce the defect under a different name.

    One segment per second is the slowest density that still trips the budget,
    which is why this test uses it: it exercises the stride at the smallest
    scale that does, and stays under ten seconds.
    """
    dur = 1.0
    segs = _varied(int(13 * 60 / dur), dur)
    end = segs[-1].end

    thoughts = make_thoughts(segs, min_sec=6.0)

    assert len(thoughts) <= 60_000, (
        f"scan ran away to {len(thoughts)} candidates")
    assert any(t.start >= 0.9 * end for t in thoughts), (
        "bounding the cost must not come at the price of the tail")
