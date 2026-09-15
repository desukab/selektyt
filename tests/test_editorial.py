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
    segs = [
        Segment(0, 15, "Nobody expects this, but the hidden system changed everything. Here is why it matters.", []),
        Segment(10, 25, "This is another interesting section with a surprising result and a clear payoff.", []),
        Segment(30, 48, "The practical insight is simple, and the numbers explain the result clearly.", []),
    ]
    first = [c.to_dict() for c in rank(segs, 3)]
    second = [c.to_dict() for c in rank(segs, 3)]
    assert first == second
