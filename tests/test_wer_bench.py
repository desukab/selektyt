"""Tests for the WER/CER arithmetic behind the transcription accuracy number.

`tools/wer_bench.py` produces the one number in this repository that a reader
is most likely to quote: transcription accuracy. The edit-distance code under
it had no tests at all, so a bug there would have misreported the headline
figure while every other test in the suite stayed green. These pin the
arithmetic itself — they do not re-run the model, which is what
`tools/wer_bench.py bench` is for.
"""
import json
from pathlib import Path

import pytest

from tools.wer_bench import _edit_distance, _normalize, _rates


# ── normalization ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("HE HOPED THERE WOULD BE STEW", "he hoped there would be stew"),
    # Punctuation becomes whitespace, so it cannot masquerade as a word error.
    ("Well, if I don't know — who he was.", "well if i don't know who he was"),
    # Apostrophes and hyphens *inside* a word are part of the word, so
    # "don't" is one token, not "don" + "t".
    ("don't well-known", "don't well-known"),
    # Multiple separators collapse; leading/trailing space drops.
    ("  a   b  ", "a b"),
    # Typographic apostrophes are treated the same as ASCII ones, because the
    # reference transcripts use ASCII and a hypothesis can use either.
    ("don’t", "don’t"),
])
def test_normalization_flattens_case_and_punctuation(raw, expected):
    assert _normalize(raw) == expected


def test_apostrophe_and_hyphen_are_word_characters_not_separators():
    # If they were separators, "don't" would score as two words against a
    # one-word reference and inflate WER on the most common English contraction.
    assert _normalize("don't").split() == ["don't"]
    assert _normalize("well-known").split() == ["well-known"]


# ── edit distance ─────────────────────────────────────────────────────────────

def test_edit_distance_of_identical_sequences_is_zero():
    assert _edit_distance(list("abc"), list("abc"))[-1][-1] == 0


def test_edit_distance_counts_each_operation_once():
    # One substitution, one insertion, one deletion — hand-computed.
    assert _edit_distance(list("kitten"), list("sitting"))[-1][-1] == 3


def test_edit_distance_of_an_empty_reference_counts_every_insertion():
    assert _edit_distance([], list("abcd"))[-1][-1] == 4


def test_edit_distance_matrix_has_the_expected_dimensions():
    # The caller indexes [len(reference)][len(hypothesis)], so the shape is a
    # contract, not an implementation detail.
    matrix = _edit_distance(list("ab"), list("abc"))
    assert len(matrix) == len("ab") + 1
    assert all(len(r) == len("abc") + 1 for r in matrix)
    # The corner the caller reads: bottom-right cell is the edit count.
    assert matrix[-1][-1] == 1


# ── rates ─────────────────────────────────────────────────────────────────────

def test_a_perfect_transcript_scores_zero():
    rates = _rates("He hoped there would be stew.",
                   "he hoped there would be stew")
    assert rates["wer"] == 0.0
    assert rates["cer"] == 0.0


def test_a_single_substitution_is_one_error_out_of_n_reference_words():
    # 4 reference words, 1 wrong → 25%.
    rates = _rates("the quick brown fox", "the quick green fox")
    assert rates["wer"] == pytest.approx(0.25)
    assert rates["wer_err"] == 1
    assert rates["wer_ref_len"] == 4


def test_an_insertion_is_charged_and_the_reference_length_is_unchanged():
    # The hypothesis has an extra word; the denominator is still the 4
    # reference words.
    rates = _rates("the quick brown fox", "the quick brown red fox")
    assert rates["wer"] == pytest.approx(0.25)
    assert rates["wer_err"] == 1
    assert rates["wer_ref_len"] == 4


def test_a_deletion_is_charged_like_a_substitution_at_one_word():
    rates = _rates("the quick brown fox", "the brown fox")
    assert rates["wer"] == pytest.approx(0.25)
    assert rates["wer_err"] == 1


def test_a_silence_against_a_real_reference_is_a_total_failure():
    # This is what makes a speech-free decode show up as a bad number instead
    # of a crash: 100%, never a division by zero.
    rates = _rates("the quick brown fox", "")
    assert rates["wer"] == 1.0
    assert rates["cer"] == 1.0


def test_an_empty_reference_with_a_real_hypothesis_is_also_a_total_failure():
    rates = _rates("", "the quick brown fox")
    assert rates["wer"] == 1.0


def test_an_empty_reference_and_an_empty_hypothesis_is_a_pass_not_a_crash():
    rates = _rates("", "")
    assert rates["wer"] == 0.0
    assert rates["cer"] == 0.0


def test_a_hypothesis_can_exceed_one_hundred_percent_error():
    # A reference of one word against a hypothesis of five scores 5.0. This is
    # correct behaviour, and the value is clamped *only* for the empty-reference
    # case, so a summary row must never read above 100% from that path alone.
    rates = _rates("word", "one two three four five")
    assert rates["wer"] == 5.0


def test_cer_uses_characters_so_a_single_letter_error_is_a_fraction_of_a_word():
    # "stew" → "steu": one character error out of 28 reference characters, but
    # one word error out of 6. CER is the finer instrument, which is why both
    # are reported rather than WER alone.
    rates = _rates("he hoped there would be stew", "he hoped there would be steu")
    assert rates["wer"] == pytest.approx(1 / 6)
    assert rates["cer"] == pytest.approx(1 / 28)


# ── corpus-level aggregation, which is what the reported figure actually is ──

def _corpus_level(references, hypotheses):
    """Recompute `cmd_bench`'s aggregation over paired clips.

    The reported WER is summed edit operations over summed reference length,
    never an average of per-clip rates: averaging would weight a 3-second clip
    as heavily as a 30-second one. This test exists so that aggregation choice
    cannot be silently changed to the wrong one.
    """
    rows = [_rates(r, h) for r, h in zip(references, hypotheses)]
    wer = sum(r["wer_err"] for r in rows) / sum(r["wer_ref_len"] for r in rows)
    cer = sum(r["cer_err"] for r in rows) / sum(r["cer_ref_len"] for r in rows)
    return wer, cer


def test_corpus_level_wer_is_not_the_mean_of_per_clip_wer():
    # Two clips: a 10-word clip with one substitution (10%) and a 2-word clip
    # that is perfect (0%).
    wer, _ = _corpus_level(
        ["a b c d e f g h i j", "one word"],
        ["x b c d e f g h i j", "one word"],
    )
    # Summed operations: 1 error over 12 reference words.
    assert wer == pytest.approx(1 / 12)
    # Mean-of-rates would have been (0.10 + 0.00) / 2 = 0.05, which rounds to a
    # different reported figure; the two are not interchangeable.


def test_corpus_level_weighting_is_why_the_sum_is_used_not_the_mean():
    # Same one error, but spread over a longer or a shorter reference. The
    # summed rule says the longer clip is the better transcript; averaging
    # per-clip rates would say they are identical.
    short, _ = _corpus_level(["a b c"], ["x b c"])
    long, _ = _corpus_level(["a b c d e f g h i j"], ["x b c d e f g h i j"])
    assert short == pytest.approx(1 / 3)
    assert long == pytest.approx(1 / 10)
    assert short > long


# ── the committed run records are self-consistent ─────────────────────────────

RUNS = Path(__file__).resolve().parent.parent / "corpus" / "wer" / "runs"


@pytest.mark.parametrize("name, expected_wer", [
    # Three independent runs of the same model on the same manifest agree to
    # four decimal places. If any of these moves, the accuracy figure quoted in
    # the docs is stale and must be re-checked, not re-stated.
    ("c9-small", 0.0338),
    ("redteam-small-verify", 0.0338),
    ("verify-current-small", 0.0338),
    # A wider beam is *worse* on this sample, which is counterintuitive enough
    # that it is pinned rather than left as a claim in prose.
    ("c9-small-beam5", 0.0405),
    ("c9-medium", 0.0299),
    ("c9-large-v3", 0.0338),
])
def test_the_committed_wer_figures_are_what_the_records_say(name, expected_wer):
    path = RUNS / f"{name}.json"
    if not path.is_file():
        pytest.skip(f"{name} not present (corpus/wer/runs is regenerated)")
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["wer"] == expected_wer
    if "wer_err_total" not in rec:
        # Written before the summary recorded its own numerator and
        # denominator. The pinned rate above is still asserted; the
        # arithmetic check applies to records that can carry it.
        pytest.skip(f"{name} predates wer_err_total in the run record")
    # The reported rate is a ratio of two integers that the record also
    # carries; a summary computed by a different rule than _rates would break
    # this and every doc quoting the number with it. The stored rate is
    # rounded to four decimals, so the tolerance is the rounding interval.
    assert rec["wer"] == pytest.approx(rec["wer_err_total"] / rec["wer_ref_words"],
                                       abs=5e-5)
    assert rec["cer"] == pytest.approx(rec["cer_err_total"] / rec["cer_ref_chars"],
                                       abs=5e-5)


def test_every_committed_bench_run_records_the_production_decode_parameters():
    # A benchmark that silently used different settings from the pipeline
    # measures nothing the pipeline can act on. The run record is the evidence
    # that it did not.
    if not RUNS.is_dir():
        pytest.skip("corpus/wer/runs not present")
    scored = [json.loads(p.read_text(encoding="utf-8"))
              for p in sorted(RUNS.glob("*.json")) if "wer" in json.loads(
                  p.read_text(encoding="utf-8"))]
    assert scored, "no scored runs found"
    for rec in scored:
        params = rec.get("decode_params", {})
        assert params.get("compute_type") == "int8", rec["tag"]
        assert params.get("vad_filter") is True, rec["tag"]
        assert params.get("condition_on_previous_text") is False, rec["tag"]
