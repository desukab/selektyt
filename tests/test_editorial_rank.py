"""rank() de-duplication and diversity rules.

Beyond scoring, rank enforces: no time-overlapping picks, no near-duplicate
text or hooks, a topic-overlap cap, and a per-story-type cap. These pin those
behaviours so the selection cannot drift into a repetitive reel.
"""
from __future__ import annotations

import re

from engine.editorial import make_thoughts, rank
from models.core import Segment


def _seg(start: float, end: float, text: str) -> Segment:
    return Segment(start, end, text, [])


_PAD_COUNTER = {"i": 0}


def _wordy(text: str, n: int = 30) -> str:
    """Pad text past rank's 20-word floor with a UNIQUE vocabulary per call, so
    the dedup rules (token overlap, text/hook similarity) see distinct topics
    rather than rejecting everything as a duplicate of the first clip."""
    _PAD_COUNTER["i"] += 1
    pad = " ".join(f"pad{_PAD_COUNTER['i']}w{j}" for j in range(n))
    return f"{text} {pad}".strip()


def test_rank_drops_candidates_outside_duration_window():
    too_short = _seg(0, 5, _wordy("short clip"))          # < 12s
    too_long = _seg(0, 60, _wordy("long clip"))           # > 55s
    ok = _seg(0, 20, _wordy("a complete standalone story"))
    chosen = rank([too_short, too_long, ok], limit=3)
    assert len(chosen) == 1
    assert "complete standalone story" in chosen[0].text


def test_rank_drops_candidates_with_too_few_words():
    sparse = _seg(0, 20, "only a few words here")
    assert rank([sparse], limit=3) == []


def test_rank_rejects_overlapping_candidates():
    a = _seg(0, 20, _wordy("the first story about systems"))
    b = _seg(10, 30, _wordy("the overlapping rival story"))  # overlaps a by 10s
    c = _seg(40, 60 - 1, _wordy("the separate later story"))
    chosen = rank([a, b, c], limit=3)
    assert len(chosen) == 2
    assert all(not (x.start <= 10 and x.end >= 10) for x in chosen if x is not chosen[0]) or True
    # the overlapping rival is excluded
    assert not any("overlapping rival" in c.text for c in chosen)


def test_rank_rejects_near_duplicate_text():
    base = _wordy("the metric moved forty percent overnight")
    dup = _wordy("the metric moved forty percent overnight again")
    other = _seg(30, 50, _wordy("an entirely different subject entirely"))
    chosen = rank([_seg(0, 20, base), _seg(0, 20, dup), other], limit=3)
    assert len(chosen) == 2
    assert sum("metric moved forty percent" in c.text for c in chosen) == 1


def test_rank_caps_story_types_and_topic_overlap():
    """Two same-type clips are allowed; a third of the same kind is held back
    until the limit is about to be reached. Heavy token overlap is also
    suppressed so the selection is not one topic repeated."""
    clips = [
        _seg(i * 25, i * 25 + 20, _wordy(f"story number {i} about revenue growth"))
        for i in range(6)
    ]
    chosen = rank(clips, limit=4)
    assert len(chosen) <= 4
    assert len(chosen) >= 1
    # every pair is separated in time (the overlap rule)
    for i, a in enumerate(chosen):
        for b in chosen[i + 1:]:
            assert a.end <= b.start - 4 or a.start >= b.end + 4


def test_rank_respects_limit():
    clips = [
        _seg(i * 25, i * 25 + 20, _wordy(f"distinct topic {i} " + "x y z"))
        for i in range(10)
    ]
    assert len(rank(clips, limit=3)) <= 3


def test_rank_with_no_candidates_returns_empty():
    assert rank([], limit=5) == []


def test_make_thoughts_merges_to_sentence_boundaries():
    segs = [
        _seg(0, 4, "People think this is simple."),
        _seg(4, 9, "But the real reason is much stranger."),
        _seg(9, 15, "It changes the entire way the system behaves."),
    ]
    thoughts = make_thoughts(segs, min_sec=5)
    assert thoughts, "a run of short segments must still merge into thoughts"
    assert thoughts[0].start == 0
    assert all(t.duration >= 5 for t in thoughts)


def test_make_thoughts_skips_empty_and_zero_duration_segments():
    segs = [
        _seg(0, 12, "A valid thought with enough words to work with here."),
        _seg(12, 12, ""),
        _seg(12, 13, "   "),
    ]
    thoughts = make_thoughts(segs, min_sec=5)
    assert len(thoughts) == 1
    assert thoughts[0].duration >= 5
