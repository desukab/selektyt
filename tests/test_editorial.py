from models.core import Segment
from engine.editorial import make_thoughts, rank


def test_thought_segmentation_preserves_complete_thoughts():
    segs = [
        Segment(0, 2, "People think this is simple.", []),
        Segment(2, 5, "But the real reason is much stranger.", []),
        Segment(5, 10, "It changes the entire way the system behaves.", []),
    ]
    thoughts = make_thoughts(segs, min_sec=5)
    assert thoughts
    assert "real reason" in thoughts[0].text


def test_rank_is_deterministic_and_non_overlapping():
    """Three claims that clear the filter (each 20+ words, 8-55 s, distinct
    content), so the ranking has something to choose from.

    This test used to be vacuous in a way its name hid: the segments were 14
    words each, below the 20-word floor, so ``rank`` returned an empty list and
    the assertion compared ``[] == []``. It would have passed if ranking had
    stopped returning anything at all. The non-empty check below is what makes
    the determinism assertion mean something, and the overlap check is the one
    the name has always promised and never made.
    """
    segs = [
        Segment(0, 20, "Nobody expects this, but the hidden system quietly changed everything about how the industry works, and here is precisely why that matters to you today.", []),
        Segment(25, 48, "This is another genuinely interesting section of the talk, containing a surprising result that most practitioners would never predict without running the numbers themselves.", []),
        Segment(55, 80, "The practical insight is remarkably simple in hindsight, and the figures on the slide explain the final outcome far more clearly than any lengthy narrative could.", []),
    ]
    first = [c.to_dict() for c in rank(segs, 3)]
    second = [c.to_dict() for c in rank(segs, 3)]
    assert first, "ranking returned nothing, so the determinism check below is vacuous"
    assert first == second

    spans = [(c["start"], c["end"]) for c in first]
    for (a, b), (c, d) in zip(spans, spans[1:]):
        assert b <= c, f"selected clips overlap in source time: {b} > {c}"
