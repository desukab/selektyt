"""The audit's own checks, pinned (criterion P1-P4 and the caption checks).

``tools/editorial_audit.py`` is the benchmark the release validation phase asked
for: it scores the concrete properties that decide whether a shipped clip is
publishable, and its exit code composes with CI. Six of its seven checks had no
test at all — only ``check_silence_share`` was pinned — so any of them could
have silently inverted and shipped a wrong PASS verdict. These tests are the
fix for that, and they pin the semantics rather than the implementation, so a
check that drifts from what it claims to measure has to delete a stated
property.

The one case worth reading before changing ``check_cuts_respect_sentences``
is ``test_tightening_a_pause_between_two_kept_words_is_not_a_split``. On
emwcon-panel-2018 clip 01 the audit used to report a cut splitting ' was,'/
' at' even though the cut lands 0.18 s past the end of ' was,' and 1.50 s
before ' at' begins, and no word was lost: the two kept intervals drop only
the 1.44 s gap between two transcript-adjacent words of one utterance, and
the utterance arrives on screen complete. That was a false positive, and its
cause was one level up: the sentence structure came from splitting the
candidate's ``text`` on punctuation, and the transcript segment it was cut
from carries no terminal punctuation, so two utterances six seconds apart
were read as one sentence. The check now takes the transcript's own
utterance spans and fires on the loss of a word, which is what
``test_dropping_a_word_from_the_middle_of_a_sentence_is_flagged`` pins.
``check_cuts_land_between_words`` stays silent on the same boundary because
67.09 s is inside neither word — the two checks are testing different things,
and both are right.
``test_a_clean_cut_between_two_unpunctuated_utterances`` is the regression
test for the source of the sentence structure: it is the pentagon clip 02
shape, and without the transcript spans it fails.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from tools.editorial_audit import (  # noqa: E402
    _load,
    _terminal_punct,
    TOLERANCE_S,
    check_caption_text_is_transcript_text,
    check_cues_fit_the_render,
    check_cuts_land_between_words,
    check_cuts_respect_sentences,
    check_edges_respect_sentences,
    check_last_word_is_not_shaved,
    check_no_duplicate_sentences,
)


def _cand(text: str, words: list[tuple[float, float, str]]) -> dict:
    """A candidate whose word spans and text are exactly as given."""
    return {
        "text": text,
        "start": words[0][0],
        "end": words[-1][1],
        "words": [{"text": t, "start": s, "end": e} for s, e, t in words],
    }


# --- mid-word cuts ---------------------------------------------------------


def test_a_cut_inside_a_word_is_flagged():
    words = [(0.0, 0.5, "hello"), (0.5, 1.0, "world")]
    ok, bad = check_cuts_land_between_words(_cand("hello world", words), [(0.0, 0.75)])
    assert not ok
    assert any("inside word" in b for b in bad)


def test_a_cut_exactly_at_a_word_edge_is_not_flagged():
    """``st < t < en`` is strict, so a boundary on a word edge is clean."""
    words = [(0.0, 0.5, "hello"), (0.5, 1.0, "world")]
    ok, bad = check_cuts_land_between_words(_cand("hello world", words), [(0.0, 0.5)])
    assert ok and not bad


def test_a_cut_between_distant_words_is_not_inside_either():
    """The gap case: 0.18 s past one word's end and 1.50 s before the next
    starts is not inside a word, so this check must stay quiet."""
    words = [(0.0, 0.42, "was"), (2.10, 2.10, "at"), (2.15, 2.55, "first")]
    ok, bad = check_cuts_land_between_words(_cand("was at first", words), [(0.0, 0.6)])
    assert ok and not bad


def test_no_words_is_not_a_failure():
    ok, bad = check_cuts_land_between_words({"text": "", "words": []}, [(0.0, 1.0)])
    assert ok and not bad


# --- mid-sentence cuts -----------------------------------------------------


def test_a_cut_between_sentences_is_not_flagged():
    words = [(0.0, 0.5, "hello"), (0.6, 1.1, "world"),
             (1.4, 1.9, "goodbye"), (2.0, 2.5, "now")]
    ok, bad = check_cuts_respect_sentences(
        _cand("Hello world. Goodbye now.", words), [(0.0, 1.15)])
    assert ok, f"cut at a sentence edge reported as splitting: {bad}"


def test_a_cut_inside_a_sentence_is_flagged():
    words = [(0.0, 0.5, "hello"), (0.6, 1.1, "cruel"), (1.2, 1.7, "world")]
    ok, bad = check_cuts_respect_sentences(
        _cand("hello cruel world", words), [(0.0, 0.85)])
    assert not ok
    assert any("splits" in b for b in bad)


def test_a_clip_ending_mid_sentence_is_flagged():
    """The clip ends inside a sentence, so the sentence loses its end.

    The boundary is 0.18 s past the end of ' was' and 1.50 s before ' at'
    begins — composed from the real emwcon clip 01 boundary. The words after
    the clip are dropped, so the sentence loses them and the viewer hears a
    thought that never finished.
    """
    words = [(0.0, 0.42, "was"), (2.10, 2.10, "at"), (2.15, 2.55, "first")]
    ok, bad = check_cuts_respect_sentences(
        _cand("was at first", words), [(0.0, 0.60)])
    assert not ok, "a clip ending inside a sentence was not reported"
    assert any("'was'" in b and "'at'" in b for b in bad)


def test_tightening_a_pause_between_two_kept_words_is_not_a_split():
    """A boundary that shortens only the gap between two kept words is clean.

    Both words are kept and no word between them exists, so the sentence is
    intact on screen and the clip goes on. This is the case the check used to
    flag: all four mid-sentence findings on the committed corpus were it
    (pentagon clip 04, emwcon clip 01, ooi clip 01, royal-society clip 01,
    each removing 0.5-1.4 s of gap between transcript-adjacent words). Firing
    here would make the audit reject every clip that removes a pause.
    """
    words = [(0.0, 0.42, "was"), (2.10, 2.40, "at"), (2.45, 2.85, "first")]
    ok, bad = check_cuts_respect_sentences(
        _cand("was at first", words), [(0.0, 0.60), (2.00, 2.90)])
    assert ok, f"removing a gap between two kept words was reported: {bad}"


def test_dropping_a_word_from_the_middle_of_a_sentence_is_flagged():
    """The case the criterion exists for: a hole in the sentence itself.

    ' at' is dropped by the gap between the two kept intervals, so the clip
    reads ' was first' and the sentence lost a word it should have kept.
    """
    words = [(0.0, 0.42, "was"), (0.60, 1.00, "at"), (1.10, 1.50, "first")]
    ok, bad = check_cuts_respect_sentences(
        _cand("was at first", words), [(0.0, 0.55), (1.05, 1.60)])
    assert not ok, "a word dropped from inside a sentence was not reported"
    assert any("'was'" in b and "'at'" in b for b in bad) or \
        any("'at'" in b and "'first'" in b for b in bad), bad


def test_the_message_does_not_leak_an_fstring_prefix():
    """The start-boundary message used to print 'was'/f'at' — a literal ``f``
    from a misquoted f-string field. The end-boundary message never had it, so
    a finding from each boundary is needed to cover both formats.

    The interval cuts a sentence at both ends — ' at' is dropped before it and
    ' now' after it — so a start finding and an end finding both come out.
    """
    words = [(0.0, 0.42, "was"), (0.60, 1.00, "at"),
             (1.10, 1.50, "first"), (1.60, 2.00, "now")]
    cand = _cand("was at first now", words)
    _ok, bad = check_cuts_respect_sentences(cand, [(1.05, 1.55)])
    starts = [b for b in bad if b.startswith("start")]
    ends = [b for b in bad if b.startswith("end")]
    assert starts and ends, "need a finding from each boundary to cover both formats"
    for line in starts + ends:
        assert "/f'" not in line, f"literal f-string prefix leaked: {line!r}"


def test_a_clean_cut_between_two_unpunctuated_utterances():
    """The regression test for the pentagon-esper-briefing clip 02 shape.

    The transcript's segments are 'We stay in continuous contact with them' and
    'What was used to destroy the compound', neither punctuated, and the
    candidate's text joins them with a space. The interval ends 0.18 s after
    ' them' and the next starts 0.06 s before ' What', so the cut is between
    two utterances and no word is lost. This used to be reported as a split,
    and five of the seven committed projects failed the audit on findings of
    exactly this shape.
    """
    words = [(0.0, 0.30, "We"), (0.30, 0.50, "stay"), (0.50, 0.80, "contact"),
             (0.80, 1.00, "with"), (1.00, 1.20, "them"),
             (6.00, 6.30, "What"), (6.30, 6.60, "was"), (6.60, 7.00, "used")]
    cand = _cand("contact with them What was used", words)
    intervals = [(0.0, 1.38), (5.94, 7.00)]
    utterances = [(0.0, 1.20), (6.00, 7.00)]

    ok, bad = check_cuts_respect_sentences(cand, intervals, utterances)
    assert ok, f"a clean cut between two utterances was reported: {bad}"


def test_a_whole_utterance_dropped_between_two_kept_ones_is_not_a_split():
    """Where the sentence structure is read from is what decides this one.

    Dropping an utterance wholesale between two kept ones is an edit, not a
    hole: nothing in a kept sentence is lost. The candidate's text has no
    punctuation between the three utterances, so splitting that text reads
    them as one long sentence and the dropped middle one looks like a hole in
    it. The transcript's own utterance spans are the structure that says
    otherwise — which is why ``audit`` passes them and does not rely on text.
    """
    words = [(0.0, 0.30, "We"), (0.30, 0.60, "stay"), (0.60, 0.90, "in"),
             (0.90, 1.20, "contact"),
             (2.00, 2.30, "What"), (2.30, 2.60, "was"),
             (4.00, 4.30, "The"), (4.30, 4.60, "rest")]
    cand = _cand("We stay in contact What was The rest", words)
    intervals = [(0.0, 1.38), (3.94, 4.70)]
    utterances = [(0.0, 1.20), (2.00, 2.60), (4.00, 4.60)]

    ok, bad = check_cuts_respect_sentences(cand, intervals, utterances)
    assert ok, f"a dropped utterance was reported as a hole in a kept one: {bad}"

    # The text-split fallback still merges the three utterances and so still
    # fires here. That is the imprecision the utterances exist to remove, and
    # asserting it keeps the fallback honest rather than quietly dead.
    fallback_ok, _ = check_cuts_respect_sentences(cand, intervals)
    assert not fallback_ok, "the fallback no longer merges unpunctuated utterances"


def test_one_word_cannot_be_mid_sentence():
    ok, bad = check_cuts_respect_sentences(
        _cand("hello", [(0.0, 0.5, "hello")]), [(0.0, 0.5)])
    assert ok and not bad


# --- edges that drop speech ------------------------------------------------


def _seg(start: float, words: list[tuple[float, float, str]]) -> tuple[
        tuple[float, float], list[tuple[float, float, str]]]:
    """An utterance span and its own word list, as the audit builds them.

    ``check_edges_respect_sentences`` reads the transcript's words, not the
    candidate's: a clip that begins partway into an utterance keeps only its
    own half of that speech, so the words it dropped live in the transcript and
    nowhere in the candidate. This helper keeps the two lists it joins parallel.
    """
    return (start, words[-1][1]), words


def test_a_clip_opening_mid_sentence_is_flagged():
    """The pentagon-esper-briefing clip 04 shape, distilled.

    The utterance is 'You mentioned the oil fields and securing the oil fields in
    the east of Syria.' and the clip opens on ' Syria.', dropping the 12 words
    before it. Nothing in the candidate says so: its word list starts at
    ' Syria.' and looks complete.
    """
    utterance = _seg(0.0, [
        (0.00, 0.20, "You"), (0.20, 0.40, "mentioned"),
        (0.40, 0.60, "the"), (0.60, 0.80, "oil"),
        (2.20, 2.40, "east"), (2.40, 2.60, "of"),
        (2.60, 2.80, "Syria."),
    ])
    cand = _cand("Syria.", [(2.60, 2.80, "Syria.")])

    ok, _review, bad = check_edges_respect_sentences(
        cand, [utterance[0]], [utterance[1]])
    assert not ok, "a clip opening mid-sentence was not reported"
    assert any("opens mid-sentence" in b and "Syria." in b for b in bad), bad


def test_a_clip_opening_on_a_completed_sentence_is_clean():
    """The four other clips that open mid-utterance, and why they are not defects.

    Dropping the tail of a finished sentence is an edit, not a hole: the speech
    that remains starts a new one. The discriminator is the terminal punctuation
    of what was dropped, not the utterance boundary the clip opened inside.
    """
    utterance = _seg(0.0, [
        (0.00, 0.20, "it"), (0.20, 0.40, "rained."),
        (0.40, 0.60, "Then"), (0.60, 0.80, "we"),
        (0.80, 1.00, "left."),
    ])
    cand = _cand("Then we left.", [(0.40, 0.60, "Then"),
                                   (0.60, 0.80, "we"), (0.80, 1.00, "left.")])

    ok, review, bad = check_edges_respect_sentences(
        cand, [utterance[0]], [utterance[1]])
    assert ok and not review, f"opening after a sentence end was reported: {bad}"


def test_a_clip_opening_its_own_utterance_is_clean():
    """Dropping nothing means the boundary is the utterance's own start."""
    utterance = _seg(0.0, [(0.00, 0.20, "hello"), (0.20, 0.40, "world.")])
    cand = _cand("hello world.", [(0.00, 0.20, "hello"), (0.20, 0.40, "world.")])

    ok, review, bad = check_edges_respect_sentences(
        cand, [utterance[0]], [utterance[1]])
    assert ok and not review, f"an utterance's own opening was reported: {bad}"


def test_a_clip_closing_mid_sentence_is_flagged():
    """The royal-society-cloke clip 01 shape, distilled.

    The transcript is '...a day in advance. Somebody says, you know, actually
    tomorrow' and the clip ends on 'Somebody'. The sentence it started goes out
    under the cut, and again nothing in the candidate says so: 'Somebody' is its
    last kept word and looks like an end.
    """
    utterance = _seg(5.0, [
        (5.00, 5.20, "day"), (5.20, 5.40, "in"),
        (5.40, 5.60, "advance."), (5.60, 5.80, "Somebody"),
        (5.80, 6.00, "says,"), (6.00, 6.20, "you"),
        (6.20, 6.40, "know,"), (6.40, 6.60, "actually"),
        (6.60, 6.80, "tomorrow"),
    ])
    cand = _cand("Somebody", [(5.60, 5.80, "Somebody")])

    ok, _review, bad = check_edges_respect_sentences(
        cand, [utterance[0]], [utterance[1]])
    assert not ok, "a clip closing mid-sentence was not reported"
    assert any("closes mid-sentence" in b and "Somebody" in b for b in bad), bad


def test_the_close_edge_uses_the_utterance_the_clip_ends_in():
    """Why the probes that found the open defect missed the close one.

    The real clip opens in segment 178.7 s and closes in segment 232.9 s, so the
    words that follow its last kept word are in a *different* utterance from the
    one it opened in. Keying the close edge on the first utterance puts them out
    of reach and the defect reads as clean. This is the shape that hides it.
    """
    a = _seg(0.0, [
        (0.00, 0.20, "it"), (0.20, 0.40, "rained."),
        (0.40, 0.60, "then"), (0.60, 0.80, "we"), (0.80, 1.00, "left."),
    ])
    b = _seg(5.0, [
        (5.00, 5.20, "day"), (5.20, 5.40, "in"),
        (5.40, 5.60, "advance."), (5.60, 5.80, "Somebody"),
        (5.80, 6.00, "says,"), (6.00, 6.20, "you"),
        (6.20, 6.40, "know,"), (6.40, 6.60, "actually"),
        (6.60, 6.80, "tomorrow"),
    ])
    # Opens in the first utterance, closes in the second.
    cand = _cand("then Somebody", [(0.40, 0.60, "then"), (5.60, 5.80, "Somebody")])

    ok, _review, bad = check_edges_respect_sentences(
        cand, [a[0], b[0]], [a[1], b[1]])
    assert not ok, "the close edge read the utterance the clip opened in, not ended in"
    assert any("closes mid-sentence" in b for b in bad), bad


def test_an_utterance_with_no_sentence_marks_is_review_not_fail():
    """34-47% of the English segments on this corpus carry no terminal
    punctuation at all, so on such a segment every edge is consistent with
    mid-sentence and none is distinguishable from a clean one.

    That is unmeasured, not failed: the finding goes to review so the exit code
    still fails on it, and no unearned PASS is recorded. Guessing would either
    flag every clip cut from an unpunctuated segment or excuse every one.
    """
    utterance = _seg(0.0, [(0.00, 0.20, "we"), (0.20, 0.40, "stay"),
                           (0.40, 0.60, "in"), (0.60, 0.80, "contact")])
    cand = _cand("in contact", [(0.40, 0.60, "in"), (0.60, 0.80, "contact")])

    ok, review, bad = check_edges_respect_sentences(
        cand, [utterance[0]], [utterance[1]])
    assert ok, "an undecidable edge was reported as a hard failure"
    assert review, "an undecidable edge did not go to human review"
    assert any("undecidable" in b for b in bad), bad


def test_a_hard_failure_and_an_undecidable_edge_are_reported_separately():
    """One real defect and one unmeasurable edge on the same clip.

    The review flag must not swallow the hard failure, and the hard failure must
    not retire the edge: they are different kinds of statement about the clip.
    """
    a = _seg(0.0, [(0.00, 0.20, "we"), (0.20, 0.40, "stay")])          # unpunctuated
    b = _seg(5.0, [(5.00, 5.20, "day"), (5.20, 5.40, "in"),
                   (5.40, 5.60, "advance."), (5.60, 5.80, "Somebody"),
                   (5.80, 6.00, "says,")])                             # punctuated
    cand = _cand("stay Somebody", [(0.20, 0.40, "stay"), (5.60, 5.80, "Somebody")])

    ok, review, bad = check_edges_respect_sentences(
        cand, [a[0], b[0]], [a[1], b[1]])
    assert not ok, "the mid-sentence close was not reported"
    assert not review, "a clip with a hard failure was also marked for review"
    assert any("closes mid-sentence" in d for d in bad), bad


def test_both_edges_undecidable_are_counted():
    utterance = _seg(0.0, [(0.00, 0.20, "we"), (0.20, 0.40, "stay"),
                           (0.40, 0.60, "in"), (0.60, 0.80, "contact")])
    # Drops words at both ends, on an utterance that cannot decide either.
    cand = _cand("stay in", [(0.20, 0.40, "stay"), (0.40, 0.60, "in")])

    ok, _review, bad = check_edges_respect_sentences(
        cand, [utterance[0]], [utterance[1]])
    assert ok
    assert any(b.startswith("2 edges undecidable") for b in bad), bad


def test_no_utterances_is_not_a_failure():
    """A project whose transcript has no segments cannot be judged, and saying
    so would be a finding about the transcript rather than about the clips."""
    ok, review, bad = check_edges_respect_sentences(
        _cand("hello", [(0.0, 0.5, "hello")]), [], [])
    assert ok and not review and not bad


def test_misaligned_utterance_words_is_not_a_failure():
    """The two lists are parallel by index; a caller that breaks that pairing
    must not turn a bookkeeping error into a shipped-clip verdict."""
    ok, review, bad = check_edges_respect_sentences(
        _cand("hello", [(0.0, 0.5, "hello")]), [(0.0, 1.0)], [])
    assert ok and not review and not bad


def test_a_clip_with_no_words_is_not_a_failure():
    ok, review, bad = check_edges_respect_sentences(
        {"text": "", "words": []}, [(0.0, 1.0)], [[(0.0, 0.5, "hello")]])
    assert ok and not review and not bad


def test_a_word_outside_every_utterance_is_skipped_not_reported():
    """A clip in the gaps between transcript segments has no utterance to judge
    it against — the same unmeasured case, reached from a different direction."""
    utterance = _seg(100.0, [(100.00, 100.20, "far"), (100.20, 100.40, "away.")])
    cand = _cand("hello world", [(0.0, 0.5, "hello"), (0.5, 1.0, "world.")])

    ok, review, bad = check_edges_respect_sentences(
        cand, [utterance[0]], [utterance[1]])
    assert ok and not review and not bad, (
        "a clip outside every utterance was reported: " + str(bad))


def test_sentence_terminators_are_recognised_outside_english():
    """The corpus already carries Hungarian, Hindi and Telugu. A check that only
    matched '.' would be unable to decide any edge of a Devanagari segment that
    ends its sentences with the danda, and would send every clip from one to
    review."""
    assert _terminal_punct("rained.")
    assert _terminal_punct("rained?")
    assert _terminal_punct("बारिश।")
    assert _terminal_punct("雨。")
    assert _terminal_punct('quoted."')
    assert not _terminal_punct("rained")
    assert not _terminal_punct("rained,")
    assert not _terminal_punct("")


def test_a_devanagari_clip_opening_after_a_danda_is_clean():
    """The same discriminator, on a script whose terminator is not the period.

    Without the danda in the recognised set this clip is indistinguishable from
    one that opens mid-sentence; with it, the dropped text closes a sentence.
    """
    utterance = _seg(0.0, [
        (0.00, 0.20, "बारिश"), (0.20, 0.40, "हुई।"),
        (0.40, 0.60, "फिर"), (0.60, 0.80, "हम"),
        (0.80, 1.00, "गए।"),
    ])
    cand = _cand("फिर हम गए।", [(0.40, 0.60, "फिर"),
                                 (0.60, 0.80, "हम"), (0.80, 1.00, "गए।")])

    ok, review, bad = check_edges_respect_sentences(
        cand, [utterance[0]], [utterance[1]])
    assert ok and not review, (
        f"a clip opening after a Devanagari sentence end was reported: {bad}")


# --- final word ------------------------------------------------------------


def test_an_interval_ending_mid_word_is_flagged():
    words = [(0.0, 0.5, "hello"), (0.6, 1.2, "world")]
    ok, bad = check_last_word_is_not_shaved(_cand("hello world", words), [(0.0, 1.0)])
    assert not ok
    assert any("final word" in b for b in bad)


def test_a_short_word_fully_inside_the_interval_passes():
    """Unstressed function words genuinely run 90-150 ms, so this check
    deliberately does not threshold on word *duration* — only on whether the
    interval's end is past the word's end. A 120 ms 'to' fully contained is a
    pass."""
    words = [(0.0, 0.12, "to"), (0.2, 0.9, "go")]
    ok, bad = check_last_word_is_not_shaved(_cand("to go", words), [(0.0, 1.0)])
    assert ok and not bad


def test_an_interval_containing_no_words_is_not_a_failure():
    ok, bad = check_last_word_is_not_shaved(
        _cand("hello", [(5.0, 5.5, "hello")]), [(0.0, 1.0)])
    assert ok and not bad


# --- captions --------------------------------------------------------------


def test_a_cue_word_absent_from_the_transcript_is_flagged():
    ok, bad = check_caption_text_is_transcript_text(
        _cand("the quick brown fox", [(0.0, 0.3, "fox")]),
        [{"text": "lazy dog runs", "start": 0.0, "end": 1.0}])
    assert not ok
    assert any("'lazy'" in b for b in bad)


def test_stopwords_are_not_flagged():
    """Common function words are dropped, because they match trivially and
    would flood the findings with noise."""
    ok, bad = check_caption_text_is_transcript_text(
        _cand("the quick brown fox", [(0.0, 0.3, "fox")]),
        [{"text": "the a an of and to in is it that this", "start": 0.0, "end": 1.0}])
    assert ok and not bad


def test_no_cues_is_not_a_failure():
    ok, bad = check_caption_text_is_transcript_text(
        _cand("hello world", [(0.0, 0.5, "hello")]), [])
    assert ok and not bad


def test_a_cue_ending_past_the_render_is_flagged():
    ok, bad = check_cues_fit_the_render(
        [{"start": 0.0, "end": 5.0, "text": "x"}], 4.0)
    assert not ok


def test_a_cue_within_tolerance_of_the_render_passes():
    """ffprobe rounds to the same order as the tolerance, so a cue ending just
    short of the render is a rounding artefact, not an overrun."""
    ok, bad = check_cues_fit_the_render(
        [{"start": 0.0, "end": 4.0 + TOLERANCE_S / 2, "text": "x"}], 4.0)
    assert ok and not bad


def test_no_cues_or_no_render_is_not_a_failure():
    assert check_cues_fit_the_render([], 4.0) == (True, [])
    assert check_cues_fit_the_render([{"start": 0.0, "end": 9.0, "text": "x"}], 0.0) == (True, [])


# --- duplication -----------------------------------------------------------


def test_the_same_long_sentence_in_two_clips_is_flagged():
    sentence = "we were handing out wikis to everyone who walked past the table"
    cands = [{"text": sentence}, {"text": "something else entirely here ok"},
             {"text": sentence}]
    ok, bad = check_no_duplicate_sentences(cands)
    assert not ok
    assert any("clips 1 and 3" in b for b in bad)


def test_short_sentences_are_below_the_bar():
    """Under 24 characters is ignored, so repeated fragments like 'oh yes'
    cannot manufacture a duplication finding."""
    ok, bad = check_no_duplicate_sentences(
        [{"text": "oh yes right"}, {"text": "oh yes right"}])
    assert ok and not bad


def test_two_different_sentences_are_not_duplicates():
    ok, bad = check_no_duplicate_sentences(
        [{"text": "we were handing out wikis to everyone who walked past the table"},
         {"text": "later that evening the table was empty and we went home"}])
    assert ok and not bad


# --- the audit over a whole project ----------------------------------------


def test_a_project_with_nothing_to_audit_fails_with_a_reason(tmp_path):
    """Four of the eleven committed sources have no selections.json — the VAD
    gate suppressed their transcripts — and ``audit`` used to raise KeyError
    on them. A sweep across the corpus has to keep going past the first
    incomplete project, so this is a reported failure, not an exception."""
    from tools.editorial_audit import audit

    ok, rep = audit(tmp_path)
    assert not ok
    findings = rep["findings"]
    assert findings, "the missing artifact must be a finding, not an empty report"
    assert any("no selections.json" in detail for _n, _o, detail in findings)


def test_the_finding_names_the_stage_that_stopped_the_run(tmp_path, ):
    """'No selections' is the symptom, not the cause.

    All four committed sources that land here have a transcript.json with zero
    segments — transcription came back empty, so selection never ran. Telling
    an editor only that selections.json is missing sends them to the wrong
    stage: the fix for an empty transcript (the VAD gate ate the audio) is not
    the fix for a selection stage that rejected everything it saw.
    """
    from tools.editorial_audit import audit

    (tmp_path / "transcript.json").write_text(
        '{"language": "te", "asr": {"engine": "faster-whisper"}, "segments": []}',
        encoding="utf-8",
    )
    ok, rep = audit(tmp_path)
    assert not ok
    detail = rep["findings"][0][2]
    assert "transcription returned no segments" in detail
    assert "te" in detail, "the language the model decoded as belongs in the reason"


def test_a_transcript_that_returned_segments_points_at_selection_not_transcription(tmp_path):
    """The other shape: transcription worked, selection produced nothing. The
    finding must distinguish it, because this one is an editor's tuning problem
    and the empty-transcript one is a transcription bug."""
    from tools.editorial_audit import audit

    (tmp_path / "transcript.json").write_text(
        '{"language": "en", "segments": [{"text": "hello", "start": 0.0, "end": 1.0}]}',
        encoding="utf-8",
    )
    ok, rep = audit(tmp_path)
    assert not ok
    assert "transcription returned 1 segments" in rep["findings"][0][2]


def test_a_project_with_no_clips_reports_why_not_just_that_it_failed(tmp_path):
    """Selections exist but are empty: a run that rejected everything. The
    rejection reasons are the useful part, so they travel with the finding."""
    from tools.editorial_audit import audit

    (tmp_path / "selections.json").write_text("[]", encoding="utf-8")
    (tmp_path / "report.json").write_text(
        '{"rejection_reasons": {"no hook": 3, "too short": 2}}', encoding="utf-8"
    )
    ok, rep = audit(tmp_path)
    assert not ok
    assert any("no clips selected" in d for _n, _o, d in rep["findings"])
    assert any("no hook" in d for _n, _o, d in rep["findings"])


def test_the_whole_corpus_is_auditable_without_keyerror():
    """Every committed source, including the four with no transcript. This is
    the sweep the audit exists to support, and it is what used to stop at
    alice-1903 with a traceback."""
    from tools.editorial_audit import audit

    runs = sorted((TOOLS.parent / "corpus").glob("*_run/project"))
    assert runs, "no corpus runs found; the test below would pass vacuously"
    unreachable = []
    for project in runs:
        try:
            audit(project)
        except Exception as exc:
            unreachable.append(f"{project.parent.name}: {type(exc).__name__}: {exc}")
    assert not unreachable, (
        "the audit raised on a committed project instead of reporting it: "
        + "; ".join(unreachable)
    )


def test_the_audit_verdict_on_the_committed_corpus_is_the_recorded_baseline():
    """The audit's verdict over the eleven committed sources, pinned.

    This tool's purpose is to fail CI on a clip that is not publishable, so a
    corpus leg cannot assert that everything passes. Five sources were
    suppressed by the VAD gate before selection ran, and those five are the
    *only* hard failures left: no shipped clip in the corpus is mechanically
    defective any more. Eleven clips sit on transcripts too unpunctuated for
    the edge check to decide, which the audit reports as awaiting review.

    The verdict has three states, not two, so the pin records both: which clips
    *fail* (a defect in the shipped artifact) and which clips are *under review*
    (unmeasured, and still a non-zero exit code). Both sets are matched exactly,
    because a check that silently inverts reads as a clean clip becoming a
    failure or the reverse, and either is a change a reader of the baseline has
    to be told about. A detail that gains a figure is still the same finding, so
    reasons match by prefix.

    The last clip-level failure was pentagon-esper-briefing clip 04, which
    opened on 'Mr.' inside the sentence 'Thank you, Mr. Secretary.' because the
    sentence splitter broke that sentence on the period in the honorific and
    the candidate then opened on the fragment 'Secretary.'. The splitter no
    longer breaks there, the source was re-run on real media, and the clip is
    clean -- the third failure this instrument found and the code fixed,
    after 'Syria.' and 'Somebody'. The review set is a property of the
    transcripts, not of the clips: 34-47% of the English segments carry no
    terminal punctuation, and on such a segment every edge is consistent with
    mid-sentence.
    """
    from tools.editorial_audit import audit

    runs = sorted((TOOLS.parent / "corpus").glob("*_run/project"))
    assert runs, "no corpus runs found; the test below would pass vacuously"

    # The recorded verdict. ``fail`` is (finding name, reason prefix) for every
    # hard failure; ``review`` is the set of clips the audit could not decide and
    # sent to a human instead. Both are exhaustive: a clip may appear in either
    # but a change to either set fails this test.
    EXPECTED = {
        "alice-1903_run": {
            "fail": [("project", "no selections.json")],
        },
        "emwcon-panel-2018_run": {
            "review": {"clip 02", "clip 03", "clip 04"},
        },
        "hadhazy-protest_run": {
            "review": {"clip 01", "clip 02"},
        },
        "hindi-baiga-interview_run": {
            "fail": [("project", "no selections.json")],
        },
        "nasa360-ep5_run": {
            "review": {"clip 03", "clip 05"},
        },
        "ooi-vertical_run": {
            "review": {"clip 02"},
        },
        "pentagon-esper-briefing_run": {
            # clip 03's edge became decidable under the word-anchored timer, and
            # clip 05 is a different clip entirely (the ranking moved it to
            # 910.0-928.18) whose opening utterance — "At the end of the day, my
            # expectation" — carries no terminal punctuation at all, so the
            # transcript cannot say where its sentence ends there.
            "review": {"clip 05"},
        },
        "roman-portrait-trailer_run": {
            "fail": [("project", "no selections.json")],
        },
        "royal-society-cloke_run": {
            # clip 01 is the reel's new opening (0.3-39.08, the 40.3-degrees
            # passage) and its opening utterance is likewise unpunctuated; the
            # edge is undecidable, not wrong. clip 04 was already review.
            "review": {"clip 01", "clip 04"},
        },
        "telugu-launch_run": {
            "fail": [("project", "no selections.json")],
        },
        "telugu-tutorial_run": {
            "review": {"clip 01"},
        },
    }

    seen = {}
    for project in runs:
        _ok, rep = audit(project)
        review = set(rep.get("review") or [])
        fail = [(name, detail) for name, cok, detail in rep["findings"]
                if not cok and name not in review]
        seen[project.parent.name] = {"fail": fail, "review": review}

    unexpected = []
    for name, got in sorted(seen.items()):
        want = EXPECTED.get(name)
        if want is None:
            unexpected.append(f"{name}: no recorded verdict for this project")
            continue
        for finding, reason in want.get("fail", []):
            if not any(f == finding and d.startswith(reason) for f, d in got["fail"]):
                unexpected.append(
                    f"{name}: expected {finding} to fail with {reason!r}, "
                    f"got {got['fail']}")
        if len(want.get("fail", [])) != len(got["fail"]):
            unexpected.append(
                f"{name}: expected {len(want.get('fail', []))} hard failure(s), "
                f"got {len(got['fail'])}: {got['fail']}")
        if got["review"] != want.get("review", set()):
            unexpected.append(
                f"{name}: review set changed; expected "
                f"{sorted(want.get('review', set()))}, got {sorted(got['review'])}")
    for name in EXPECTED:
        if name not in seen:
            unexpected.append(f"{name}: recorded but not present in the corpus")
    assert not unexpected, (
        "the audit's verdict on the committed corpus changed; update the table "
        "and say why:\n" + "\n".join(unexpected))


def test_every_word_of_the_committed_corpus_lands_in_exactly_one_utterance():
    """The word-to-sentence join is what the mid-sentence check rests on.

    The check attributes a word to the utterance containing its start, so a
    word the transcript does not cover is a word the check cannot see and a
    word straddling two is a word it may attribute either way. On the seven
    committed projects that produced a timeline every word lands inside exactly
    one segment, so the join is exact rather than approximate — and 155 of the
    1755 words start precisely at a segment boundary, which is the case an
    off-by-one in the join misattributes. This pins both.
    """
    from tools.editorial_audit import _word_sentence_map, _word_spans

    checked = words_at_boundary = 0
    for project in sorted((TOOLS.parent / "corpus").glob("*_run/project")):
        selections = _load(project / "selections.json")
        transcript = _load(project / "transcript.json")
        if selections is None or transcript is None:
            continue
        segments = transcript.get("segments") or []
        if not segments:
            continue
        utterances = sorted((float(s["start"]), float(s["end"])) for s in segments)
        starts = {u[0] for u in utterances}
        for cand in selections:
            spans = _word_spans(cand)
            if not spans:
                continue
            sent_of = _word_sentence_map(cand, utterances)
            assert len(sent_of) == len(spans), "the map must answer one index per word"
            for (st, en, _t), s in zip(spans, sent_of):
                checked += 1
                words_at_boundary += st in starts
                assert s != -1, f"{project.parent.name}: word {st:.3f}s is in no utterance"
                lo, hi = utterances[s]
                assert lo <= st and en <= hi + 0.05, (
                    f"{project.parent.name}: word {st:.3f}-{en:.3f}s is not inside "
                    f"utterance {lo}-{hi}s")
    assert checked > 1000, f"only {checked} words checked; the corpus glob is wrong"
    assert words_at_boundary > 100, (
        f"only {words_at_boundary} words start exactly at a segment boundary; "
        "the off-by-one case this guards is no longer represented")


def test_the_corpus_candidate_words_are_already_the_kept_set():
    """Why the utterance source changes no verdict the corpus can reach.

    A selection's ``words`` list is not the transcript's words for that span —
    it is the words the selection kept. Measured over all seven selected
    projects, 1754 of the 1755 words lie wholly inside a retention interval.
    So at an interval boundary the word on each side is kept, every word
    between them is kept, and the word-loss criterion cannot fire; the
    sentence source is never the thing that decides the outcome. That is a
    property of the data, not of the check: a candidate whose word list
    carried an excised word would still be flagged, and the unit tests build
    exactly those candidates.

    The one word outside every interval is royal-society-cloke clip 00's last
    word, which begins exactly at the final interval's end — the boundary
    excludes it whole rather than shaving it, so it is clean for a different
    reason: the adjacent-word lookup lands on it twice and the check continues.

    Pinned because it is the fact a reader needs in order to trust the corpus
    and e2e legs. If a change makes selections carry their excised words, this
    test fails and the utterance source becomes observable through ``audit`` —
    at which point the corpus baseline above is the leg that moves.
    """
    from engine.edit_plan import retention_intervals
    from models.core import Candidate, Word
    from tools.editorial_audit import _word_spans

    def to_cand(cand: dict) -> Candidate:
        return Candidate(
            start=cand["start"], end=cand["end"], text=cand.get("text", ""),
            story_type=cand.get("story_type", ""), hook=cand.get("hook", ""),
            payoff=cand.get("payoff", ""),
            editorial_score=cand.get("editorial_score", 0.0),
            virality_score=cand.get("virality_score", 0.0),
            context_score=cand.get("context_score", 0.0),
            confidence=cand.get("confidence", 0.0),
            reason=cand.get("reason", ""),
            visual_potential=cand.get("visual_potential", 0.0),
            words=[Word(**w) for w in cand.get("words", [])])

    total = outside = 0
    for project in sorted((TOOLS.parent / "corpus").glob("*_run/project")):
        selections = _load(project / "selections.json")
        if selections is None:
            continue
        for cand in selections:
            spans = _word_spans(cand)
            if not spans:
                continue
            intervals = retention_intervals(to_cand(cand))
            for st, en, _t in spans:
                total += 1
                outside += not any(a <= st and en <= b for a, b in intervals)
    assert total > 1000, f"only {total} words checked; the corpus glob is wrong"
    assert outside <= 1, (
        f"{outside} of {total} candidate words fall outside every retention "
        "interval; the corpus now carries excised words, so the utterance "
        "source is observable through audit() and this test's claim is stale")


# --- the corpus sweep and its comparison ---------------------------------


CORPUS = TOOLS.parent / "corpus"
BASELINE_SWEEP = CORPUS / "eval" / "audit-baseline-2026-10-05.json"


def test_the_corpus_sweep_counts_every_run_and_every_clip():
    """The sweep is what makes 'is this SeleKyT better than that one' answerable,
    so its arithmetic is pinned: it is the figure a comparison is built on.

    Eleven committed runs; six produced a timeline and five were stopped by the
    VAD gate before selection ran, which the sweep records as project-level
    failures rather than as clean runs they never had. Of the 27 clips those six
    shipped, 15 are clean and 12 sit on transcripts the edge check cannot
    decide. **No clip fails**: the five failures are all project-level, a source
    that never reached selection.

    The arc the recorded baselines hold: `audit-baseline-2026-09-27.json`
    counted 14 clean and 2 mid-sentence failures, both found by the edge check
    that looks at the speech a clip *drops*. The two sources holding those clips
    were re-run on real media and the comparison reads two improvements and no
    regressions -- royal-society-cloke clip 01 fail -> clean on the word-boundary
    fix, then pentagon-esper-briefing clip 04 fail -> clean once the sentence
    splitter stopped ending a sentence on the period in the honorific 'Mr.'.
    Between the two re-runs the clip was still a failure, but for a different
    reason: the clipped word 'Syria.' was gone and what the audit found instead
    was the sentence the splitter had broken. That intermediate reading --
    15 clean, 5 failed, this clip failing on 'Mr.' rather than on 'Syria.' --
    lives here rather than in a baseline file, because the artifacts it was
    swept from were overwritten by the second re-run and a baseline that
    describes no artifact is not evidence. That was a new finding the re-run
    surfaced, not a regression the fix introduced.

    `audit-baseline-2026-10-05.json` is the third re-run, under the
    word-anchored sentence timer and the coherence re-scoring on the shipped
    text. It reads 15 clean / 12 review against the previous 16 / 11, and the
    three verdicts that moved are all consequence rather than judgement:

    - **pentagon-esper-briefing clip 03: review -> clean.** The word-anchored
      timer gave its opening edge a sentence boundary the transcript actually
      marks, so what was undecidable became decidable and the clip is clean.
    - **pentagon-esper-briefing clip 05: clean -> review.** The clip itself
      changed (the ranking moved it to 910.0-928.18), and its *new* opening
      utterance is ``'At the end of the day, my expectation'`` -- 8 words, none
      of them terminal-punctuated. The transcript cannot say where its own
      sentence ends there, so the edge is undecidable, which is the honest
      report rather than a clean one the text does not support.
    - **royal-society-cloke clip 01: clean -> review.** Same shape: the reel's
      opening moved to the 0.3-39.08 clip whose opening utterance is the
      40.3-degrees passage, and that utterance also carries no terminal
      punctuation.

    Neither ``review`` verdict is a clip that became wrong. ``review`` means the
    transcript cannot decide, and on these two utterances it provably cannot --
    the word lists are committed above. The count moved against the fix on
    paper because two clips changed identity entirely and the ones that replaced
    them open on unpunctuated speech; stating that here is the point of keeping
    the number pinned.

    The pinned split between ``review`` and ``fail`` rests on an acoustic
    measurement of the shipped renders — a word-free window is REVIEW when the
    decode shows content and FAIL when it corroborates dead air, and FAIL is
    also what an unmeasurable window stays. ``.gitignore`` excludes the renders
    wholesale (they are regenerable and large), so a checkout that never ran the
    corpus reads one clip as FAIL that a checkout with the renders reads as
    REVIEW, and the pinned totals cannot be reproduced. That is a property of
    what is committed, not of the audit, so the guard names the renders rather
    than pinning a number the checkout cannot derive.
    """
    from tools.editorial_audit import sweep

    if not any(CORPUS.glob(f"*_run/project/{kind}/*.mp4")
               for kind in ("facebook", "shorts")):
        # ``measure_clip_render`` reads the facebook render and falls back to
        # shorts; without either it measures nothing, an undecidable clip reads
        # as FAIL, and the review/fail split this pins moves with it.
        pytest.skip(
            "the pinned totals need the shipped renders, which .gitignore "
            "excludes; run the corpus to produce them")
    record = sweep(CORPUS)
    assert record["totals"] == {"runs": 11, "clean": 15, "review": 12, "fail": 4}
    sources = {run["source"] for run in record["runs"]}
    assert len(sources) == 11, "a run was audited twice or missed"
    # A clean clip is counted but not recorded: the comparison treats an absent
    # entry as clean, so keeping one would make the same state read two ways.
    assert all(run["clips"] for run in record["runs"]), (
        "a run with no recorded clips should have been counted as all-clean, "
        "which only happens when the run produced no timeline at all")


def test_a_clean_clip_and_a_clean_run_read_the_same_way_to_a_comparison():
    """An absent entry and an explicit clean one must not differ, or a clip that
    became clean would be indistinguishable from one the sweep lost."""
    from tools.editorial_audit import compare_sweeps

    before = {"runs": [{"source": "a_run", "clips": [
        {"clip": "clip 01", "state": "fail", "reason": "mid-word cut"}]}]}
    after = {"runs": [{"source": "a_run", "clips": []}]}
    changes = compare_sweeps(before, after)
    assert len(changes) == 1
    assert changes[0]["kind"] == "improvement"
    assert changes[0]["before"] == "fail" and changes[0]["after"] == "clean"


def test_a_sweep_diff_classifies_each_kind_of_change():
    """Five ways a corpus can move, and the direction each one reads as.

    ``regression`` is a clip that got worse, ``improvement`` one that got better,
    and ``changed`` one that stayed broken in a different way — a check that
    swapped one defect for another is not a wash, so it is reported rather than
    cancelled. ``added`` and ``removed`` catch a run the comparison would
    otherwise score as an improvement it did not earn.
    """
    from tools.editorial_audit import compare_sweeps

    before = {"runs": [
        {"source": "regression_run", "clips": [
            {"clip": "clip 01", "state": "clean", "reason": "clean"}]},
        {"source": "improvement_run", "clips": [
            {"clip": "clip 01", "state": "fail", "reason": "mid-word cut"}]},
        {"source": "changed_run", "clips": [
            {"clip": "clip 01", "state": "fail", "reason": "mid-word cut"}]},
        {"source": "gone_run", "clips": [
            {"clip": "clip 01", "state": "review", "reason": "undecidable"}]},
    ]}
    after = {"runs": [
        {"source": "regression_run", "clips": [
            {"clip": "clip 01", "state": "fail", "reason": "mid-sentence"}]},
        {"source": "improvement_run", "clips": []},
        {"source": "changed_run", "clips": [
            {"clip": "clip 01", "state": "fail", "reason": "silence"}]},
        {"source": "new_run", "clips": []},
    ]}
    changes = compare_sweeps(before, after)

    by_kind = {}
    for c in changes:
        by_kind.setdefault(c["kind"], []).append(c["source"])
    assert set(by_kind) == {"regression", "improvement", "changed",
                            "added", "removed"}, by_kind
    assert by_kind["regression"] == ["regression_run"]
    assert by_kind["improvement"] == ["improvement_run"]
    assert by_kind["changed"] == ["changed_run"]
    assert by_kind["removed"] == ["gone_run"]
    assert by_kind["added"] == ["new_run"]
    # Regressions sort first, so a reader who only sees the head of the list
    # sees the worst of it.
    assert changes[0]["kind"] == "regression"


def test_a_review_state_does_not_read_as_a_failure_or_a_pass():
    """The three states are ordered, and the order is what classifies a move.

    clean < review < fail: a clip that went from failing to undecidable is an
    improvement, because a person can still save it, and one that went from
    undecidable to failing is a regression. Folding review into either would
    hide exactly the move a sweep exists to catch.
    """
    from tools.editorial_audit import compare_sweeps

    to_review = compare_sweeps(
        {"runs": [{"source": "r", "clips": [
            {"clip": "clip 01", "state": "fail", "reason": "x"}]}]},
        {"runs": [{"source": "r", "clips": [
            {"clip": "clip 01", "state": "review", "reason": "x"}]}]})
    assert to_review[0]["kind"] == "improvement", (
        "failing to undecidable is an improvement, not a wash")

    from_review = compare_sweeps(
        {"runs": [{"source": "r", "clips": [
            {"clip": "clip 01", "state": "review", "reason": "x"}]}]},
        {"runs": [{"source": "r", "clips": [
            {"clip": "clip 01", "state": "fail", "reason": "x"}]}]})
    assert from_review[0]["kind"] == "regression", (
        "undecidable to failing is a regression")


def test_a_sweep_compared_against_itself_reports_nothing():
    """The null comparison: an unchanged corpus is not a wall of unchanged
    lines, and it exits zero so a CI leg can tell signal from noise."""
    from tools.editorial_audit import compare_sweeps, sweep

    record = sweep(CORPUS)
    assert compare_sweeps(record, record) == [], (
        "an unchanged corpus produced a diff")


def test_the_recorded_baseline_sweep_is_the_current_corpus():
    """``--compare`` is only as good as the baseline it reads, so the committed
    record is pinned against a fresh sweep of the same corpus.

    This is the drift guard. A change to SeleKyT that moves any clip changes the
    sweep, this test fails, and the baseline has to be re-recorded with
    ``--corpus --baseline`` — after the diff it represents has been read and
    explained, which is the point of recording it.
    """
    from tools.editorial_audit import compare_sweeps, sweep

    if not BASELINE_SWEEP.is_file():
        pytest.skip("the recorded baseline sweep is not in this checkout")
    if not any(CORPUS.glob(f"*_run/project/{kind}/*.mp4")
               for kind in ("facebook", "shorts")):
        # The recorded states include the acoustic decode of each shipped render,
        # which .gitignore excludes: without the renders the undecidable clips
        # read as failures and this reports a regression that is not one.
        pytest.skip(
            "the baseline comparison needs the shipped renders, which "
            ".gitignore excludes; run the corpus to produce them")
    recorded = json.loads(BASELINE_SWEEP.read_text(encoding="utf-8"))
    changes = compare_sweeps(recorded, sweep(CORPUS))
    assert changes == [], (
        "the committed corpus no longer matches the recorded baseline sweep; "
        "read the diff, then re-record with "
        "`python tools/editorial_audit.py corpus --corpus --baseline "
        f"{BASELINE_SWEEP}`:\n" + "\n".join(
            f"  {c['kind']} {c['source']} {c['clip']}: "
            f"{c['before']} -> {c['after']}" for c in changes))


# --- the caption parser the audit reads through ----------------------------


def test_the_parser_reads_a_real_committed_caption_file():
    """The audit's cue checks are only as good as its SRT parse, so the parser
    is pinned against a committed artifact rather than a fixture: 20 cues on
    nasa360-ep5 clip 01, with real timestamps."""
    from tools.editorial_audit import _parse_srt_seconds

    srt = TOOLS.parent / "corpus" / "nasa360-ep5_run" / "project" / "captions" / "01.srt"
    if not srt.is_file():
        pytest.skip("nasa360-ep5 captions not present in this checkout")
    cues = _parse_srt_seconds(srt)
    assert len(cues) >= 10, f"a real shipped caption file parsed to {len(cues)} cues"
    # A timestamp that survived the parse has to be a real number, not 0.0 by
    # default — a parse that silently zeroed every cue would pass a length check.
    assert all(c["end"] > c["start"] for c in cues), "a cue does not end after it starts"


@pytest.mark.parametrize("raw", [
    "",
    "1\nhello world\n",
    "1\n00:00:00,000 --> 00:00:01,000\n",
    "1\n00:00:00 --> 00:00:01,000\ntext\n",
    "1\n\n",
], ids=["empty", "no timestamp line", "no text line", "bad timestamp", "blank block"])
def test_a_malformed_caption_file_parses_to_zero_cues_not_an_exception(tmp_path, raw):
    """Every malformed shape degrades to zero cues.

    This is the load-bearing property of the whole caption check family:
    ``check_caption_text_is_transcript_text`` and ``check_cues_fit_the_render``
    both short-circuit on an empty cue list, so a parse that raised — or worse,
    one that invented cues from a malformed block — would turn a caption
    regression into either a crash or a silent PASS. Zero is the honest reading
    of a file with no usable cues in it.
    """
    from tools.editorial_audit import _parse_srt_seconds

    srt = tmp_path / "01.srt"
    srt.write_text(raw, encoding="utf-8")
    assert _parse_srt_seconds(srt) == []
