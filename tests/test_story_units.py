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


def test_split_sentences_keeps_titles_with_their_names():
    """A title's period is not a sentence boundary -- the failure that put it
    here was real, and measured.

    ``Thank you, Mr. Secretary.`` used to split into ``Thank you, Mr.`` and
    ``Secretary.``; the second piece was timed into the middle of the word
    ``Mr.`` (359.203 against a word spanning 359.14-359.50), the clip start
    snapped back to that word's start, and the clip opened on ``Mr.`` having
    dropped ``Thank you,`` with no sentence break between them. That was an
    audit FAIL on a real source, not a hypothetical.
    """
    assert split_sentences("Thank you, Mr. Secretary.") == ["Thank you, Mr. Secretary."]
    assert split_sentences("Thank you so much, Dr. List.") == ["Thank you so much, Dr. List."]
    # The title can end the text on its own, and then its period is terminal.
    assert split_sentences("Over to you, Dr.") == ["Over to you, Dr."]
    # A boundary after a title's name is still a boundary.
    assert split_sentences("Dr. List. Thank you.") == ["Dr. List.", "Thank you."]


def test_split_sentences_keeps_numbers_and_domains_intact():
    """Structural non-boundaries that need no list to recognise."""
    assert split_sentences("0.03% of the population.") == ["0.03% of the population."]
    assert split_sentences("Go to mediawiki.org and look.") == ["Go to mediawiki.org and look."]
    assert split_sentences("The U.S. space agency. It launched.") == [
        "The U.S. space agency.", "It launched."]


def test_split_sentences_keeps_a_lettered_list_item_short():
    """``a. The first step`` is a list marker, not a one-letter sentence."""
    assert split_sentences("a. The first step is to read.") == ["a. The first step is to read."]


def test_coverage_pairs_a_split_decimal_with_the_sentence_token():
    """``"40.3"`` in the sentence is ``"40"`` and ``".3"`` in the word stream.

    faster-whisper emits the decimal as two words. The coverage test compares
    token bodies, and ``"40.3"`` reduces to the one body ``"403"``, which no
    word ever produces -- so a sentence the words plainly cover fell back to
    proportional timing, and on the committed Royal Society transcript that
    refusal cost the reel's opening 12 s of hook. Digits pair digit by digit;
    letters still decide identity.
    """
    from engine.story_units import _piece_tokens
    assert _piece_tokens("40.3 degrees") == {"4", "0", "3", "degrees"}
    assert _piece_tokens("40") | _piece_tokens(".3") >= _piece_tokens("40.3")
    # A different number is still a different number.
    assert not (_piece_tokens("34 degrees") <= _piece_tokens("40.3 Celsius"))


def test_a_split_decimal_sentence_anchors_to_its_words():
    """The Royal Society segment that used to fall back now anchors."""
    from engine.story_units import _segment_sentences
    seg = _wseg(0.3, 7.44, "Now last year the UK recorded its highest ever "
                            "temperature of 40.3 degrees Celsius and on the news recently", [
        _word(" Now", 0.30, 0.80), _word(" last", 0.80, 1.12),
        _word(" year", 1.12, 1.40), _word(" the", 1.40, 1.58),
        _word(" UK", 1.58, 1.84), _word(" recorded", 1.84, 2.34),
        _word(" its", 2.34, 2.58), _word(" highest", 2.58, 3.14),
        _word(" ever", 3.14, 3.42), _word(" temperature", 3.42, 3.86),
        _word(" of", 3.86, 4.18), _word(" 40", 4.18, 4.50),
        _word(".3", 4.50, 5.02), _word(" degrees", 5.02, 5.28),
        _word(" Celsius", 5.28, 5.70), _word(" and", 5.70, 6.64),
        _word(" on", 6.64, 6.74), _word(" the", 6.74, 6.82),
        _word(" news", 6.82, 6.98), _word(" recently", 6.98, 7.44),
    ])
    sents = _segment_sentences(seg)
    assert len(sents) == 1
    assert sents[0].word_anchored is True
    assert sents[0].start == 0.30
    assert sents[0].end == 7.44


def test_split_sentences_errs_towards_not_splitting():
    """The rule cannot see ``I saw Dr. He left.`` as two sentences.

    That is the accepted cost, and it is measured against the alternative: the
    failure this fixes is a clip opening mid-word, and this one is a sentence
    that runs one name long. Asserting it keeps the choice where a reader can
    find it, rather than hiding it as an accident of the regex.
    """
    assert split_sentences("I saw Dr. He left.") == ["I saw Dr. He left."]


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


def test_coherence_single_boundary_is_neutral_not_zero():
    # One boundary cannot show a pattern: a real splice and a coherent but
    # word-diverse pair are lexically identical at two sentences. Zero
    # overlap must not be scored as zero coherence.
    from engine.story_units import coherence_score
    diverse = ("World Atnet's coverage of the satellite internet race examines "
               "one example in detail. The origins of GPS began in the early 1970s.")
    splice = ("The origins of GPS began in the early 1970s. Inland marine "
              "operations have historically struggled with limited connectivity.")
    assert coherence_score(diverse) == 0.5
    assert coherence_score(splice) == 0.5
    assert coherence_score("The metric moved overnight. The metric surprised everyone.") == 1.0


def test_coherence_detects_seam_with_two_boundaries():
    from engine.story_units import coherence_score
    # Two boundaries are the minimum from which a sharp seam is visible.
    one_story = ("The metric moved forty percent overnight. The metric "
                 "surprised the whole team. The team celebrated the launch.")
    spliced = ("The metric moved forty percent overnight. The team celebrated "
               "the launch. Inland marine operations have struggled with "
               "limited connectivity for decades.")
    assert coherence_score(one_story) > coherence_score(spliced)
    assert coherence_score(spliced) < 0.2


# ── word-anchored sentence timing ────────────────────────────────────────────


def _wseg(start, end, text, words):
    return Segment(start, end, text, words)


def _word(text, start, end):
    return Word(text, start, end)


def test_segment_sentences_anchors_bounds_to_words():
    """A sentence's bounds are the bounds of the words that compose it."""
    from engine.story_units import _segment_sentences
    seg = _wseg(0.0, 7.0, "We have main engine start. Four, three, two, one.", [
        _word("We", 0.00, 0.30), _word("have", 0.30, 0.60),
        _word("main", 0.60, 0.90), _word("engine", 0.90, 1.20),
        _word("start.", 1.20, 1.50),
    ])
    sents = _segment_sentences(seg)
    assert len(sents) == 2
    # The second sentence has no words of its own, so rather than manufacture a
    # bound for it the whole segment admits proportional timing. That is the
    # conservative direction: one admitted estimate beats one plausible-looking
    # exact timecode for a sentence whose words do not exist. The reel used to
    # open on ``"have main engine start."`` because the manufactured bound at
    # 3.792 s sat inside the word ``have``.
    assert all(not s.word_anchored for s in sents)
    assert sents[1].text == "Four, three, two, one."


def test_segment_sentences_refuses_a_length_only_match():
    """Words that reach the character count but are not the sentence do not
    anchor it.

    ``"Speed it up, guys."`` has one word in the stream, ``"Speed"``; the old
    character-count test consumed ``"Speed Why don't you just admit"``,
    reached the count, and anchored the sentence to speech that is not it.
    """
    from engine.story_units import _segment_sentences
    seg = _wseg(342.0, 349.0, "Speed it up, guys. Why don't you just admit it?", [
        _word("Speed", 342.78, 343.18),
        _word("Why", 346.94, 347.16), _word("don't", 347.16, 347.36),
        _word("you", 347.36, 347.56), _word("just", 347.56, 347.76),
        _word("admit", 347.76, 348.00),
    ])
    sents = _segment_sentences(seg)
    # The words cannot cover the first sentence, so the segment admits estimate.
    assert all(not s.word_anchored for s in sents)


def test_segment_sentences_anchors_when_punctuation_differs():
    """The text and the word stream disagree about punctuation and case by
    construction (``"hand?"`` vs ``"hand"``); coverage is judged on letters."""
    from engine.story_units import _segment_sentences
    seg = _wseg(0.0, 4.0, "Are you alright? Yes, I'm fine.", [
        _word("Are", 0.0, 0.3), _word("you", 0.3, 0.6),
        _word("alright?", 0.6, 1.2), _word("Yes,", 1.2, 1.6),
        _word("I'm", 1.6, 1.8), _word("fine.", 1.8, 2.2),
    ])
    sents = _segment_sentences(seg)
    assert [s.word_anchored for s in sents] == [True, True]
    assert sents[0].start == 0.0 and sents[0].end == 1.2
    assert sents[1].start == 1.2 and sents[1].end == 2.2


def test_segment_sentences_proportional_when_words_disagree():
    """Words and sentence text that cannot correspond leave the segment estimated."""
    from engine.story_units import _segment_sentences
    seg = _wseg(10.0, 14.0, "The weather is mild.", [
        _word("Completely", 10.0, 10.5), _word("different", 10.5, 11.0),
        _word("subject", 11.0, 11.5),
    ])
    assert all(not s.word_anchored for s in _segment_sentences(seg))


def test_anchored_sentence_bounds_are_not_rounded():
    """Rounding a word-derived bound breaks ``sentence.start == word.start``,
    and ``_BOUND_EPS`` is 1e-6, so the word that *is* the opening then falls
    outside the clip's own bound and is dropped."""
    from engine.story_units import _segment_sentences
    ya = 213.01999
    seg = _wseg(ya, 214.2, "Я люблю это!", [
        _word("Я", ya, ya + 0.48),
        _word("люблю", ya + 0.48, ya + 0.72),
        _word("это!", ya + 0.72, ya + 1.16),
    ])
    sents = _segment_sentences(seg)
    assert sents[0].word_anchored
    assert sents[0].start == ya
