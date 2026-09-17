"""Story-unit segmentation, boilerplate handling and coherence scoring.

These cover the editorial-quality layer that decides whether a candidate is
one story or a splice: story units, boilerplate detection, the seam-aware
coherence score, and the candidate-level effects (no boilerplate in clip
text, penalty for spanning unrelated stories).
"""
from models.core import Segment, Word

from engine.editorial import make_thoughts, rank
from engine.story_units import (
    boilerplate_fraction,
    coherence_score,
    segment_story_units,
    split_sentences,
)


def _seg(start, end, text):
    return Segment(start, end, text, [])


def test_split_sentences_keeps_punctuation():
    assert split_sentences("First one. Second two!") == ["First one.", "Second two!"]
    assert split_sentences("No punctuation here") == ["No punctuation here"]
    assert split_sentences("Trailing period.") == ["Trailing period."]


def test_split_sentences_is_multilingual():
    assert split_sentences("తెలుగు వాక్యం। మరొకటి।") == ["తెలుగు వాక్యం।", "మరొకటి।"]


def test_boilerplate_detects_byline_and_issue():
    frac, kinds = boilerplate_fraction(
        "The system shipped on time. "
        "This article appears in the December 2024 print issue. "
        "Willie Jones is an associate editor at IEEE Spectrum."
    )
    assert frac > 0.5
    assert "meta" in kinds


def test_boilerplate_leaves_real_dates_alone():
    # A historical fact is content, not a dateline.
    frac, kinds = boilerplate_fraction(
        "In March 1990 the agency launched the first satellite of the "
        "modern constellation, and coverage expanded across the continent."
    )
    assert frac < 0.2


def test_boilerplate_section_marker_is_structural_not_content():
    # "Part 2" alone is a boundary, and costs little: it is the source's own
    # structure, not an apology by the editor.
    frac, kinds = boilerplate_fraction("Part 2")
    assert frac <= 0.25
    assert "section" in kinds


def test_section_marker_is_peeled_off_a_content_sentence():
    units = segment_story_units([
        _seg(0, 10, "Differential GPS reached full operational capability in 1994, Part 1."),
    ])
    assert len(units) == 2
    assert units[0].boilerplate is False
    assert "Part 1." not in units[0].text
    assert units[1].boilerplate is True


def test_byline_is_reunited_across_a_sentence_break():
    # The ASR garbles the role word, so the byline lands in two pieces; the
    # affiliation must still pull the first half in as metadata.
    units = segment_story_units([
        _seg(0, 5, "The rollout completed ahead of schedule."),
        _seg(5, 8, "Willie Jones is an associated data"),
        _seg(8, 10, "at ID spectrum."),
    ])
    byline = [u for u in units if "Willie Jones" in u.text]
    assert byline and byline[0].boilerplate is True


def test_topic_shift_splits_a_unit():
    units = segment_story_units([
        _seg(0, 6, "The satellite network reached global coverage in 1995."),
        _seg(6, 12, "The satellite network reached global coverage in 1995."),
        _seg(12, 18, "The satellite network reached global coverage in 1995."),
        _seg(18, 24, "Baking sourdough requires patience and a warm kitchen."),
        _seg(24, 30, "Baking sourdough requires patience and a warm kitchen."),
        _seg(30, 36, "Baking sourdough requires patience and a warm kitchen."),
    ])
    assert len(units) == 2
    assert "satellite" in units[0].text
    assert "sourdough" in units[1].text


def test_story_unit_spans_are_disjoint_and_ordered():
    units = segment_story_units([
        _seg(0, 6, "The satellite network reached global coverage in 1995."),
        _seg(6, 12, "The satellite network reached global coverage in 1995."),
        _seg(12, 18, "Baking sourdough requires patience and a warm kitchen."),
        _seg(18, 24, "Baking sourdough requires patience and a warm kitchen."),
    ])
    for a, b in zip(units, units[1:]):
        assert a.end <= b.start
    assert units[0].start == 0.0


def test_coherence_is_high_for_one_story():
    text = (
        "The satellite network reached global coverage in 1995. "
        "The satellite network then supported civilian aviation. "
        "Civilian aviation became the network's largest user."
    )
    assert coherence_score(text) > 0.3


def test_coherence_penalises_a_splice():
    # Each half is internally consistent; the seam between them is the defect.
    text = (
        "The satellite network reached global coverage in 1995. "
        "The satellite network then supported civilian aviation. "
        "Baking sourdough requires patience and a warm kitchen. "
        "Baking sourdough also needs a reliable starter culture."
    )
    assert coherence_score(text) < coherence_score(
        "The satellite network reached global coverage in 1995. "
        "The satellite network then supported civilian aviation. "
        "Civilian aviation became the network's largest user."
    )


def test_coherence_credits_discourse_continuation():
    # No shared words, but "But" ties the sentences into one argument.
    text = "People assume the protocol is simple. But the real reason is far stranger."
    assert coherence_score(text) > 0.5


def test_candidate_never_contains_boilerplate():
    segs = [
        _seg(0, 12, "The satellite network reached global coverage in 1995. Coverage expanded."),
        _seg(12, 14, "Part 2"),
        _seg(14, 26, "Baking sourdough requires patience and a warm kitchen everywhere."),
    ]
    for c in rank(make_thoughts(segs), limit=4):
        assert "Part 2" not in c.text
        assert "This article appears" not in c.text


def test_candidate_crossing_a_topic_shift_scores_low():
    segs = [
        _seg(0, 12, "The satellite network reached global coverage in 1995. Coverage expanded."),
        _seg(12, 24, "The satellite network then supported civilian aviation worldwide."),
        _seg(24, 36, "Baking sourdough requires patience and a warm kitchen everywhere."),
        _seg(36, 48, "Baking sourdough also needs a reliable starter culture today."),
    ]
    ranked = rank(make_thoughts(segs), limit=8)
    spliced = [c for c in ranked if "sourdough" in c.text and "satellite" in c.text]
    clean = [c for c in ranked if c.coherence_score > 0.5]
    if spliced:
        assert all(c.coherence_score < 0.5 for c in spliced)
        assert all(c.coherence_score > 0.5 for c in clean)


def test_hook_and_payoff_skip_boilerplate():
    # A byline must never be offered as a clip's payoff.
    segs = [
        _seg(0, 12, "The satellite network reached global coverage in 1995. "
                    "Coverage expanded across the continent and civilian "
                    "aviation adopted the signal for navigation."),
        _seg(12, 16, "This article appears in the December 2024 print issue."),
    ]
    ranked = rank(make_thoughts(segs), limit=4)
    assert ranked, "fixture must yield at least one candidate"
    for c in ranked:
        assert "print issue" not in c.payoff
        assert "print issue" not in c.hook


def test_thoughts_carry_coherence_and_completeness():
    segs = [
        _seg(0, 12, "The satellite network reached global coverage in 1995. Coverage expanded."),
        _seg(12, 24, "The satellite network then supported civilian aviation worldwide."),
    ]
    thoughts = make_thoughts(segs)
    assert thoughts
    for s in thoughts:
        assert 0.0 <= s.coherence <= 1.0
        assert 0.0 <= s.completeness <= 1.0


def test_rank_reports_coherence_on_candidates():
    segs = [
        _seg(0, 14, "The satellite network reached global coverage in 1995. Coverage expanded."),
        _seg(14, 28, "The satellite network then supported civilian aviation worldwide."),
    ]
    for c in rank(segs, limit=3):
        assert 0.0 <= c.coherence_score <= 1.0
        assert "coherence=" in c.reason


def test_words_are_not_duplicated_across_unit_boundary():
    # A marker peel splits one segment's sentences across two units. Words
    # must still be collected once, or the caption track shows a ghost cue
    # for the repeated word.
    segs = [
        _seg(0, 12, "The network reached global coverage in 1995, Part 1. "
                    "Coverage expanded across the continent quickly."),
        _seg(12, 24, "The network then supported civilian aviation worldwide."),
    ]
    words: list[str] = []
    for s in make_thoughts(segs):
        words.extend(w.text for w in s.words or [])
    assert sorted(words) == sorted(set(words))


def test_no_ghost_cue_for_word_on_a_unit_boundary():
    # The concrete symptom of duplicated words: two caption cues with the
    # same text and the same start.
    import re
    import tempfile
    from pathlib import Path

    from engine.captions import srt_for_candidate

    segs = [
        _seg(0, 14, "The network reached global coverage in 1995, Part 1. "
                    "Coverage expanded across the continent quickly and "
                    "civilian aviation adopted the signal for navigation."),
        _seg(14, 28, "The network then supported civilian aviation worldwide "
                     "and the agency extended the operational contract."),
    ]
    ranked = rank(segs, limit=2)
    assert ranked, "fixture must yield at least one candidate"
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "c.srt"
        srt_for_candidate(ranked[0], out)
        body = out.read_text()
    starts = re.findall(r"(\d+:\d+:\d+[,.]\d+) -->", body)
    cues = re.findall(r"\d+:\d+:\d+[,.]\d+ --> \d+:\d+:\d+[,.]\d+\n(.*?)\n\n", body + "\n\n", re.S)
    assert starts, "the fixture produced no cues"
    assert len(starts) == len(set(starts)), "two cues share a start time"
    assert sorted(cues) == sorted(set(cues)), "a cue's text is duplicated"
