"""The audit's own checks, pinned (criterion P1-P4 and the caption checks).

``tools/editorial_audit.py`` is the benchmark the release validation phase asked
for: it scores the concrete properties that decide whether a shipped clip is
publishable, and its exit code composes with CI. Six of its seven checks had no
test at all — only ``check_silence_share`` was pinned — so any of them could
have silently inverted and shipped a wrong PASS verdict. These tests are the
fix for that, and they pin the semantics rather than the implementation, so a
check that drifts from what it claims to measure has to delete a stated
property.

The one case worth reading before changing ``check_cuts_respect_sentences`` is
``test_a_cut_in_a_gap_between_same_sentence_words_still_splits``. On
emwcon-panel-2018 clip 01 the audit reports a cut splitting ' was,'/' at' even
though the cut lands 0.18 s past the end of ' was,' and 1.50 s before ' at'
begins. That is not a false positive: both words belong to one
under-punctuated transcript sentence, so a cut between them is mid-sentence by
the criterion the check exists to enforce. The gap is large, not the words'
relationship. ``check_cuts_land_between_words`` stays silent on the same
boundary because 67.09 s is inside neither word — the two checks are testing
different things, and both are right.
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from tools.editorial_audit import (  # noqa: E402
    TOLERANCE_S,
    check_caption_text_is_transcript_text,
    check_cues_fit_the_render,
    check_cuts_land_between_words,
    check_cuts_respect_sentences,
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


def test_a_cut_in_a_gap_between_same_sentence_words_still_splits():
    """The check is about sentence membership, not proximity.

    A boundary can be far from both surrounding words and still split a
    sentence: this candidate's three words are one sentence, so removing the
    middle one leaves the clip starting a thought it never began. Composed
    from the real emwcon clip 01 boundary, where ' was,' ends 0.18 s before
    the cut and ' at' starts 1.50 s after it.
    """
    words = [(0.0, 0.42, "was"), (2.10, 2.10, "at"), (2.15, 2.55, "first")]
    ok, bad = check_cuts_respect_sentences(
        _cand("was at first", words), [(0.0, 0.60)])
    assert not ok, "a cut between two words of one sentence was not reported"
    assert any("'was'" in b and "'at'" in b for b in bad)


def test_the_message_does_not_leak_an_fstring_prefix():
    """The start-boundary message used to print 'was'/f'at' — a literal ``f``
    from a misquoted f-string field. The end-boundary message never had it, so
    a finding from each boundary is needed to cover both formats."""
    words = [(0.0, 0.42, "was"), (2.10, 2.10, "at"), (2.15, 2.55, "first")]
    cand = _cand("was at first", words)
    # start 0.60 splits 'was'/'at'; end 0.60 splits them too, on the other line.
    _ok, bad = check_cuts_respect_sentences(cand, [(0.0, 0.60), (0.60, 2.60)])
    starts = [b for b in bad if b.startswith("start")]
    ends = [b for b in bad if b.startswith("end")]
    assert starts and ends, "need a finding from each boundary to cover both formats"
    for line in starts + ends:
        assert "/f'" not in line, f"literal f-string prefix leaked: {line!r}"


def test_one_word_cannot_be_mid_sentence():
    ok, bad = check_cuts_respect_sentences(
        _cand("hello", [(0.0, 0.5, "hello")]), [(0.0, 0.5)])
    assert ok and not bad


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
