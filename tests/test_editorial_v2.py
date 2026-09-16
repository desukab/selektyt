"""Extended editorial engine tests: human-editor scoring, anti-repetition,
semantic-boundary generation, dead-air detection, code-switching support."""
import re
from models.core import Segment, Word, Candidate
from engine.editorial import (
    make_thoughts, rank, score_candidate, _similarity, _hook_similarity,
    _standalone_score, _repetition_score, _dead_air_fraction,
)


def _segs(texts, starts, ends):
    return [Segment(s, e, t, []) for t, s, e in zip(texts, starts, ends)]


def _w(start, end, text):
    return Word(text, start, end, 1.0)


def test_weak_hook_low_score():
    seg = Segment(0, 12, "So basically, I mean, you know, it is what it is, right.", [])
    c = score_candidate(seg)
    assert c.editorial_score < 0.45


def test_missing_payoff_low_score():
    seg = Segment(0, 18, "The whole system is designed around one principle and it goes like this", [])
    c = score_candidate(seg)
    assert "payoff=" in c.reason
    payoff_val = float(c.reason.split("payoff=")[1].split(";")[0])
    assert payoff_val < 0.5


def test_duplicate_ideas_filtered_by_rank():
    segs = [
        Segment(0, 20, "Nobody knows this, but the system changed everything. The hidden reason is clear and obvious.", []),
        Segment(20, 40, "Nobody knows this, but the system changed completely. The hidden reason is obvious and clear.", []),
        Segment(45, 65, "The surprising result shows a clear pattern, and the numbers explain why it happened.", []),
    ]
    ranked = rank(segs, limit=3)
    texts = [c.text for c in ranked]
    assert not any(_similarity(a, b) >= 0.78 for a, b in zip(texts, texts[1:])), \
        "rank() should filter near-duplicate candidates"


def test_overlapping_candidates_filtered_by_rank():
    segs = [
        Segment(0, 18, "First, the surprising change happened in the system overnight and nobody noticed.", []),
        Segment(3, 22, "Then, the surprising change happened in the system overnight and nobody noticed at all.", []),
        Segment(30, 50, "Finally, the result was clear and the numbers explained the pattern perfectly.", []),
    ]
    ranked = rank(segs, limit=3)
    for c in ranked:
        for d in ranked:
            if c is not d:
                assert c.end <= d.start - 4 or c.start >= d.end + 4, \
                    "rank() should not produce overlapping clips"


def test_filler_heavy_clip_low_editorial():
    seg = Segment(0, 20, "Um, you know, I mean, so basically, um, uh, you know, the thing is, right, um, so.", [])
    c = score_candidate(seg)
    assert c.editorial_score < 0.35


def test_dead_air_detection():
    words = [_w(0.0, 1.0, "hello"), _w(5.0, 6.0, "world")]
    dur = 6.0
    frac = _dead_air_fraction(words, dur)
    assert frac > 0.5
    words_fast = [_w(0.0, 0.5, "hi"), _w(0.6, 1.0, "there")]
    frac_fast = _dead_air_fraction(words_fast, 1.0)
    assert frac_fast < 0.01


def test_code_switched_candidate_scores():
    seg = Segment(0, 15, "So ఇది actually, అయితే, the problem is very important.", [])
    c = score_candidate(seg)
    assert c.editorial_score > 0.0


def test_standalone_score():
    assert _standalone_score("but actually this is wrong") == 0.0
    assert _standalone_score("the surprising result shows everything") == 1.0
    assert _standalone_score("so they went ahead") == 0.0
    assert _standalone_score("") == 0.0


def test_repetition_score():
    assert _repetition_score([]) == 0.0
    assert _repetition_score(["a", "b", "c", "d"]) == 0.0
    assert _repetition_score(["a", "b", "a", "b", "a", "b"]) > 0.3


def test_hook_similarity_filters_different_openings():
    a = "Why does nobody talk about the hidden system?"
    b = "How the system actually changed everything overnight."
    c = "Why does nobody mention the secret system at all?"
    assert _hook_similarity(a, b) < 0.3
    assert _hook_similarity(a, c) > 0.6


def test_semantic_boundary_generation():
    segs = [
        Segment(0, 2, "First sentence ends here.", []),
        Segment(2, 5, "But this continues without a break.", []),
        Segment(5, 10, "Another sentence ends here.", []),
        Segment(10, 15, "And a final complete sentence.", []),
        Segment(15, 18, "Yet more without ending.", []),
        Segment(18, 24, "Final ending.", []),
    ]
    thoughts = make_thoughts(segs, min_sec=5)
    starts = [t.start for t in thoughts]
    assert 0 in starts, "should start at first segment"
    assert 10 in starts, "should start at sentence boundary after segment 2"


def test_context_pass_absolute_with_context():
    from engine.context_guard import validate
    c = Candidate(
        start=0, end=15,
        text="Nobody ever knew this secret, the system changed overnight in a surprising way for everyone watching the video today right now here.",
        story_type="insight", hook="Nobody ever knew this secret",
        payoff="watching the video today right now here.",
        editorial_score=0.5, virality_score=0.5, context_score=0.7,
        confidence=0.9, reason="test", visual_potential=0.5,
        words=[], llm_metadata=None,
    )
    ok, _ = validate(c)
    assert ok
