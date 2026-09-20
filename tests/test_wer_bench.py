"""Tests for the WER/CER arithmetic behind the transcription accuracy number.

`tools/wer_bench.py` produces the one number in this repository that a reader
is most likely to quote: transcription accuracy. The edit-distance code under
it had no tests at all, so a bug there would have misreported the headline
figure while every other test in the suite stayed green. These pin the
arithmetic itself — they do not re-run the model, which is what
`tools/wer_bench.py bench` is for.
"""
import io
import re
import json
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

from tools import wer_bench
from tools.wer_bench import (
    _edit_distance,
    _normalize,
    _prepare_tar,
    _prepare_zip,
    _rates,
)


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
    # The non-English measurement (P9). Pinned for the same reason, but note
    # what it pins: this is a *failure*, not a number to quote as progress. WER
    # above 1.0 means the hypothesis carries more error words than the
    # reference has words. If this moves, P9's row moves with it.
    ("telugu-small", 1.5108),
    # The same Telugu manifest on a medium model: WER 151.08% -> 91.54%, CER
    # 127.40% -> 69.24%, and clips inside the 25% bar 0 -> 1 of 50. Capacity is a
    # real lever and it does not reach the bar. Pinned so a doc can quote the
    # improvement without quoting the distance still left.
    ("telugu-medium", 0.9154),
    # The first C10 language measured on its own criterion corpus. Polish is one
    # of the six P9 names, and the same small model that scores 3.38% English
    # scores 11% here with diacritics intact. This is the number that localises
    # the Telugu failure to Indic rather than to "everything outside English".
    ("polish-small", 0.1101),
    # The second C10 language, and the one that shows Polish was not a fluke of
    # one easy audiobook. Italian on MLS test (40 clips, 9.80 min, 1258
    # reference words) scores 20.11% — worse than Polish, but inside the bar,
    # and on Dante, whose archaic Italian is a harder condition than Polish's
    # modern prose. P9 needs 3 of 6, so this is the result that makes the
    # criterion reachable rather than merely not-yet-contradicted.
    ("italian-small", 0.2011),
    # The third C10 language, and the one that satisfies P9: the criterion asks
    # for <=25% on at least 3 of the 6, and Polish (11.01%), Italian (20.11%)
    # and this are all inside the bar. Spanish on MLS test is 40 clips /
    # 10.08 min / 1503 reference words, 129 word errors, 38 of 40 clips under
    # the bar. It is the *best* of the three, which is worth noting rather than
    # burying: the non-English results are not a graded descent from English's
    # 3.38% but a spread, and the spread is what makes "one easy audiobook" the
    # wrong reading of any single one of them.
    ("spanish-small", 0.0858),
    # The fourth C10 language, and the one that moves P9 from "met on the
    # minimum" to "met with one to spare". German on MLS test is 40 clips /
    # 10.04 min / 1388 reference words, WER 10.66%, CER 3.20%, 36 of 40 clips
    # inside the 25% bar. Its worst clip is 42.9% — the same tail Polish and
    # Italian show — so it is a corpus-level pass, not a uniformly easy one.
    # Four of the six C10 names are now measured and inside the bar; French and
    # Dutch remain unmeasured, and Telugu remains the counterexample outside C10.
    ("german-small", 0.1066),
    # The fifth C10 language, and the one that leaves one rather than two
    # unmeasured. French on MLS test is 33 clips / 7.95 min / 1163 reference
    # words, WER 13.59%, CER 5.30% — the highest CER of the five — with 29 of
    # 33 clips inside the 25% bar and a worst clip of 44.4%. It is the
    # second-hardest of the five after Italian's Dante. Dutch is now the only
    # C10 name without a measurement.
    ("french-small", 0.1359),
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


# ── the non-English measurement, and what it actually says ───────────────────

# P9 asked for ≤25% WER on at least 3 of 6 languages. Telugu is one language,
# measured, and it is not close. These hold the measurement to its record so a
# doc can soften the finding without re-running the bench.

TELUGU = RUNS / "telugu-small.json"


def test_telugu_is_a_measured_failure_not_an_unmeasured_risk():
    """P9's row used to say 'never measured, and suggests it would fail'.
    It is measured now: this asserts the measured number and the direction."""
    if not TELUGU.is_file():
        pytest.skip("telugu-small run not present")
    rec = json.loads(TELUGU.read_text(encoding="utf-8"))
    assert rec["wer"] > 1.0, "Telugu WER is no longer above 1.0 — update P9's row"
    assert rec["clip_count"] >= 50
    # The record names the corpus it was scored on, so an English reader cannot
    # mistake this for an English figure.
    assert "Telugu" in rec["corpus"], rec["corpus"]


def test_no_telugu_clip_meets_the_p9_bar():
    """Not one clip reaches 25%, so this is a whole-corpus failure rather than
    a few hard utterances dragging an otherwise-fine average down."""
    if not TELUGU.is_file():
        pytest.skip("telugu-small run not present")
    rec = json.loads(TELUGU.read_text(encoding="utf-8"))
    per_clip = rec.get("per_clip") or []
    if len(per_clip) < 50:
        pytest.skip("run record has no per-clip detail")
    passing = [c["utterance"] for c in per_clip if c["wer"] <= 0.25]
    assert not passing, (
        f"{len(passing)} of {len(per_clip)} Telugu clips now meet the 25% bar: "
        f"{passing[:5]}. P9's row must be re-written."
    )


def test_five_c10_languages_are_measured_and_all_pass():
    """P9 asks for <=25% WER on at least 3 of the 6 C10 languages, and five are
    now measured and inside the bar: Polish 11.01%, Italian 20.11%, Spanish
    8.58%, German 10.66%, French 13.59%. The criterion was met at three and is
    now met with two to spare — but read what "met" covers. Five of six says
    nothing about Dutch, the one language still unmeasured, and it says nothing
    about Indic (Telugu fails at 151.08%, outside C10 entirely). The honest
    sentence is "P9 holds on the five measured C10 languages", not
    "transcription is multilingual".

    This test exists so the count of passing languages cannot drift up in prose
    without a run record to back each increment. It was raised from two to three
    when spanish-small landed, from three to four when german-small landed, and
    from four to five when french-small landed; raising it again needs a sixth
    file.
    """
    polish = RUNS / "polish-small.json"
    italian = RUNS / "italian-small.json"
    spanish = RUNS / "spanish-small.json"
    german = RUNS / "german-small.json"
    french = RUNS / "french-small.json"
    if not (polish.is_file() and italian.is_file() and spanish.is_file()
            and german.is_file() and french.is_file()):
        pytest.skip("all five C10 runs must be present to compare")
    passing = []
    for path, name in ((polish, "Polish"), (italian, "Italian"),
                       (spanish, "Spanish"), (german, "German"),
                       (french, "French")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec["wer"] <= 0.25, \
            f"{name} moved above the 25% bar ({rec['wer']}) — P9's row and " \
            f"every doc quoting it must be re-written"
        passing.append(name)
    # What the docs may say is bounded by what has actually been run.
    assert len(passing) == 5


def test_a_larger_model_helps_telugu_but_does_not_reach_the_bar():
    """The medium model on the same Telugu manifest is substantially better than
    small — WER 151.08% to 91.54% — and 49 of 50 clips still miss the 25% bar.
    That is the honest shape of the finding: capacity is a real lever, and it is
    not sufficient. Without this the improvement reads as progress toward P9
    when the criterion is still failed by a factor of almost four."""
    small = RUNS / "telugu-small.json"
    medium = RUNS / "telugu-medium.json"
    if not (small.is_file() and medium.is_file()):
        pytest.skip("both Telugu runs must be present to compare")
    s = json.loads(small.read_text(encoding="utf-8"))
    m = json.loads(medium.read_text(encoding="utf-8"))
    assert m["wer"] < s["wer"], \
        "the medium model no longer beats small on Telugu — re-read both rows"
    assert m["wer"] > 0.25, "Telugu reached the 25% bar — P9's row must move"
    pc = m.get("per_clip") or []
    if len(pc) >= 50:
        missing = sum(1 for c in pc if c["wer"] > 0.25)
        assert missing >= len(pc) // 2, \
            "most Telugu clips now meet the bar — this is no longer a " \
            "whole-corpus failure, and the docs say it is"


def test_the_telugu_corpus_manifest_is_committed_alongside_its_score():
    """A WER number scored against a manifest nobody can read is not auditable,
    so the manifest and its provenance live in the repo rather than on this host."""
    corpus = Path(__file__).resolve().parent.parent / "corpus" / "wer"
    manifest = corpus / "telugu-slr66-female" / "manifest.jsonl"
    provenance = corpus / "telugu-slr66-female" / "provenance.json"
    if not manifest.is_file():
        pytest.skip("Telugu corpus not present")
    items = [json.loads(l) for l in manifest.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    assert len(items) >= 50
    # Every clip carries the reference the score was computed against, and a
    # missing reference would have been silently scored as a perfect match.
    assert all(i.get("reference") is not None for i in items), items[:2]
    prov = json.loads(provenance.read_text(encoding="utf-8"))
    assert prov["licence"], "the corpus's licence must travel with its transcripts"
    assert prov["archive_member_count"] > prov["clips"], (
        "the sample must be smaller than what it was drawn from, or the stride "
        "is meaningless")


def test_the_evidence_files_are_not_gitignored():
    """``.gitignore`` excludes the WER audio, which is right — it is hundreds of
    MB. It must not exclude the *evidence* alongside it: the run record a quoted
    number is scored from, and the manifest and provenance that make it
    auditable. A rule general enough to cover a second corpus silently matched
    ``corpus/wer/runs/*`` once, and the only symptom was that ``git add``
    refused the new record while the already-tracked ones stayed put — a failure
    that looks like success until a new corpus lands."""
    root = Path(__file__).resolve().parent.parent
    corpus = root / "corpus" / "wer"
    if not (corpus / "telugu-slr66-female" / "manifest.jsonl").is_file():
        pytest.skip("Telugu corpus not present")
    evidence = [
        corpus / "runs" / "telugu-small.json",
        corpus / "telugu-slr66-female" / "manifest.jsonl",
        corpus / "telugu-slr66-female" / "provenance.json",
        corpus / "librispeech-clean" / "manifest.jsonl",
    ]
    ignored = []
    for path in evidence:
        # --no-index would answer for a path git has never seen; the point is
        # what a checkout would actually track, so ask the working tree.
        proc = subprocess.run(["git", "-C", str(root), "check-ignore", "--quiet",
                               str(path.relative_to(root))])
        if proc.returncode == 0:
            ignored.append(str(path.relative_to(root)))
    assert not ignored, (
        "these evidence files are gitignored, so a number quoted from them "
        "cannot be audited from a checkout: " + ", ".join(ignored)
    )


def test_the_mls_layout_streams_and_matches_only_the_named_split(tmp_path):
    """MLS archives are 6-114 GB, so ``prepare`` reads them as a stream and
    writes only the sample. This builds the MLS shape at miniature scale: a
    nested audio tree, one ``transcripts.txt`` per split, and a *train* utterance
    that must not leak into a test sample."""
    root = tmp_path / "mls_polish"
    (root / "test" / "audio" / "11295" / "11059").mkdir(parents=True)
    (root / "train" / "audio" / "20000" / "20001").mkdir(parents=True)
    for i in range(5):
        (root / "test" / "audio" / "11295" / "11059").joinpath(
            f"11295_11059_{i:06d}.flac").write_bytes(b"not really audio")
    (root / "train" / "audio" / "20000" / "20001").joinpath(
        "20000_20001_000000.flac").write_bytes(b"train audio")
    (root / "test").joinpath("transcripts.txt").write_text(
        "".join(f"11295_11059_{i:06d}\tpo polsku {i}\n" for i in range(5)),
        encoding="utf-8")
    (root / "train").joinpath("transcripts.txt").write_text(
        "20000_20001_000000\tthis must not appear\n", encoding="utf-8")

    archive = tmp_path / "mls.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                tf.add(path, arcname=str(path.relative_to(tmp_path)))

    # The streamer opens a local path directly when it is given one, which is
    # what makes this testable without a 6 GB download.
    meta = {"url": str(archive), "format": "mls-stream"}
    manifest, total, stride = wer_bench._prepare_mls_stream(
        meta, "test", tmp_path / "sub", 2)

    assert total == 5, "the train utterance must not be counted in the test split"
    assert stride == 2
    assert len(manifest) == 2
    assert manifest[0]["reference"] == "po polsku 0"
    assert all("train" not in m["audio"] for m in manifest)
    assert all("this must not appear" not in m["reference"] for m in manifest)


def test_an_mls_corpus_reports_a_missing_split_rather_than_sampling_nothing(tmp_path):
    """An empty split is a wrong argument, not a zero-sample run."""
    root = tmp_path / "mls_polish"
    (root / "train" / "audio").mkdir(parents=True)
    (root / "train" / "audio").joinpath("a.flac").write_bytes(b"audio")
    (root / "train").joinpath("transcripts.txt").write_text("a\ttext\n",
                                                            encoding="utf-8")
    archive = tmp_path / "mls.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(root / "train" / "audio" / "a.flac",
               arcname="mls_polish/train/audio/a.flac")
        tf.add(root / "train" / "transcripts.txt",
               arcname="mls_polish/train/transcripts.txt")
    meta = {"url": str(archive), "format": "mls-stream"}
    assert wer_bench._prepare_mls_stream(meta, "test", tmp_path / "sub", 2) is None


def test_the_registry_declares_what_each_format_reads():
    # A corpus missing its url or its licence is a runtime failure in the middle
    # of a multi-GB stream, or a provenance file that cannot cite its own terms.
    for key, meta in wer_bench.CORPORA.items():
        assert set(("name", "format", "licence")) <= set(meta), key
        if meta["format"] == "mls-stream":
            assert "url" in meta and meta["url"].startswith("https://"), key


def test_every_registered_corpus_can_write_its_own_provenance(tmp_path, monkeypatch):
    # Provenance is written last in cmd_prepare, after the sample is already on
    # disk. A corpus whose shape the writer could not handle failed there and
    # left a manifest with no provenance — the directory looks prepared and the
    # number it produced is uncitable. This drove a real failure on mls-polish:
    # the writer read ["files"][0][0] and a streaming corpus has "url" instead,
    # so it raised KeyError twelve minutes into a 6 GB stream.
    #
    # The test builds a two-utterance MLS archive and runs cmd_prepare against
    # it through the registered-corpus path, so the provenance writer itself is
    # what fails on a shape mismatch rather than a restatement of its lookup.
    # The audio has to be genuinely decodable: cmd_prepare measures every clip's
    # duration through ffmpeg, so bytes that are not audio fail later than the
    # code path under test and obscure the result.
    root = tmp_path / "mls_polish"
    (root / "test" / "audio" / "1889" / "1447").mkdir(parents=True)
    payload = _silence_flac(1.0)
    for name in ("1889_1447_000010", "1889_1447_000020"):
        (root / "test" / "audio" / "1889" / "1447").joinpath(
            f"{name}.flac").write_bytes(payload)
    (root / "test").joinpath("transcripts.txt").write_text(
        "1889_1447_000010\tpo polsku 0\n1889_1447_000020\tpo polsku 1\n",
        encoding="utf-8")
    archive = tmp_path / "mls.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(root / "test", arcname="mls_polish/test")

    monkeypatch.setitem(wer_bench.CORPORA, "mls-polish", {
        "name": "Multilingual LibriSpeech Polish (test split)",
        "format": "mls-stream",
        "url": str(archive),
        "licence": "Multilingual LibriSpeech (Facebook AI Research), CC BY 4.0",
    })
    out = tmp_path / "out"
    out.mkdir()
    assert wer_bench.cmd_prepare([
        "--corpus", "mls-polish", "--split", "test",
        "--out", str(out), "--count", "2",
    ]) == 0
    prov = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    assert prov["source_url"] == str(archive), \
        "provenance must name the stream it actually read"
    assert prov["clips"] == 2
    assert "licence" in prov and "CC BY 4.0" in prov["licence"]
    # The manifest the run will be scored against is committed alongside it.
    assert len((out / "manifest.jsonl").read_text(
        encoding="utf-8").strip().splitlines()) == 2


def test_every_c10_language_is_registered_as_a_streaming_corpus():
    # P9 names six languages. A registry that covered five of them would make
    # the criterion look addressable when one of the six had no path to a score.
    c10 = {"german", "spanish", "french", "dutch", "italian", "polish"}
    registered = {k[len("mls-"):] for k in wer_bench.CORPORA
                  if wer_bench.CORPORA[k]["format"] == "mls-stream"}
    assert registered == c10, f"C10 is {sorted(c10)}, registry is {sorted(registered)}"


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


# ── manifest construction: the input behind a WER figure ──────────────────────

# `prepare` was refactored from one LibriSpeech-only path into a registry with
# two layouts, and the manifest it emits is the artifact every quoted number is
# scored against. These build both layouts from synthetic archives so the
# refactor is tested without a 346 MB or 505 MB download.

def _silence_flac(seconds: float, rate: int = 16000) -> bytes:
    """Silence as real FLAC, so the duration pass in cmd_prepare can decode it.

    The MLS members are .flac and cmd_prepare measures each clip through
    ffmpeg; placeholder bytes fail in the decoder rather than in the code under
    test, which reports a misleading error.
    """
    import wave

    raw = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        with wave.open(raw, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(rate)
            f.writeframes(b"\x00\x00" * int(seconds * rate))
        raw.close()
        out = subprocess.run(["ffmpeg", "-y", "-i", raw.name, "-c:a", "flac",
                              "-f", "flac", "pipe:1"],
                             check=True, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        return out.stdout
    finally:
        Path(raw.name).unlink(missing_ok=True)


def _indic_zip(directory: Path, n: int = 7) -> tuple[Path, Path]:
    """The OpenSLR Indic shape: flat .wav members plus a TSV of references."""
    archive = directory / "indic.zip"
    refs = "".join(f"te_{i:04d}\tనమస్కారం {i}\n" for i in range(n))
    with zipfile.ZipFile(archive, "w") as zf:
        for i in range(n):
            zf.writestr(f"te_{i:04d}.wav", b"not really audio")
    index = directory / "line_index.tsv"
    index.write_text(refs, encoding="utf-8")
    return archive, index


def test_the_zip_layout_pairs_each_wav_with_its_tsv_reference():
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        archive, index = _indic_zip(directory)
        manifest, members, stride = _prepare_zip(archive, index, directory, 3)
    # stride is members // count, so 7 members at count 3 strides by 2.
    assert (members, stride) == (7, 2)
    assert len(manifest) == 3
    # The reference is looked up by FileID, not by filename or position, and
    # non-ASCII script must survive the round trip into JSON.
    assert manifest[0]["utterance"] == "te_0000"
    assert manifest[0]["reference"] == "నమస్కారం 0"
    assert manifest[0]["audio"].endswith(".wav")


def test_the_zip_layout_refuses_to_run_without_the_index():
    """Without the TSV there is no reference at all, so the WER would be scored
    against nothing — a number that looks like a measurement and is not."""
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        archive, _index = _indic_zip(directory)
        assert _prepare_zip(archive, None, directory, 3) is None


def test_an_archive_with_no_utterances_reports_nothing():
    """An empty archive is a fetch failure, not a zero-WER run."""
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        archive = directory / "empty.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("readme.txt", "no audio here")
        index = directory / "line_index.tsv"
        index.write_text("", encoding="utf-8")
        assert _prepare_zip(archive, index, directory, 3) is None


def test_a_mistyped_index_path_reports_the_argument_not_a_traceback():
    """``--index`` pointing at nothing is a wrong argument; the tool says so
    instead of letting ``read_text`` raise, which reads as a bug in it."""
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        archive, _index = _indic_zip(directory)
        assert _prepare_zip(archive, directory / "not-there.tsv", directory, 3) is None


def _librispeech_tar(directory: Path, n: int = 7) -> Path:
    """The LibriSpeech shape: one ``.trans.txt`` per chapter holds a line per
    utterance, keyed by the utterance id."""
    archive = directory / "libri.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for i in range(n):
            name = f"6930-75918-{i:04d}"
            info = tarfile.TarInfo(f"{name}.flac")
            payload = b"not really audio"
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        transcript = "".join(f"6930-75918-{i:04d} he said thing {i}\n"
                             for i in range(n))
        info = tarfile.TarInfo("6930-75918.trans.txt")
        info.size = len(transcript)
        tf.addfile(info, io.BytesIO(transcript.encode("utf-8")))
    return archive


def test_the_tar_layout_reads_the_chapter_transcript():
    """LibriSpeech keeps one transcript file per chapter, so the reference is
    found by utterance id inside it — not by a sibling named for the clip."""
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        manifest, members, stride = _prepare_tar(_librispeech_tar(directory),
                                                directory, 3)
    assert (members, stride) == (7, 2)
    assert len(manifest) == 3
    # The line is "utt reference...", so the id prefix is stripped on the way in.
    assert manifest[0]["reference"] == "he said thing 0"
    assert manifest[0]["utterance"] == "6930-75918-0000"


# ── the streaming prepare does not read past what it needs ───────────────────


class _CountingTar:
    """Wraps a ``TarFile`` so a test can see how many members were consumed.

    The prepare's early break is only observable from outside by what it did
    *not* read: nothing it returns says whether the loop ran to the end of the
    archive, and the bug it fixes is precisely that it used to.
    """

    def __init__(self, tf, read: list[str] | None = None):
        self._tf = tf
        # A random-access TarFile is not its own iterator, and a streaming one
        # is; iter() covers both, so the wrapper works either way.
        self._it = iter(tf)
        self.read = read if read is not None else []

    def __iter__(self):
        return self

    def __next__(self):
        member = next(self._it)
        self.read.append(member.name)
        return member

    def extract(self, *args, **kwargs):
        return self._tf.extract(*args, **kwargs)

    def extractfile(self, *args, **kwargs):
        return self._tf.extractfile(*args, **kwargs)

    def __enter__(self):
        self._tf.__enter__()
        return self

    def __exit__(self, *exc):
        return self._tf.__exit__(*exc)


def _mls_archive_with_a_tail(directory: Path, n_audio: int = 6) -> Path:
    """An MLS-shaped archive whose final members are padding the prepare has no
    reason to read: the wanted sample and its transcript both appear first.

    Returns the archive. The last member is named so a test can assert it was
    never reached."""
    root = directory / "mls_polish"
    audio = root / "test" / "audio" / "11295" / "11059"
    audio.mkdir(parents=True)
    for i in range(n_audio):
        audio.joinpath(f"11295_11059_{i:06d}.flac").write_bytes(b"not really audio")
    root.joinpath("test", "transcripts.txt").write_text(
        "".join(f"11295_11059_{i:06d}\tpo polsku {i}\n" for i in range(n_audio)),
        encoding="utf-8")
    archive = directory / "mls.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                tf.add(path, arcname=str(path.relative_to(directory)))
        # Deliberately last, and deliberately large enough that reading it would
        # dominate the runtime of a real prepare on a real archive.
        payload = b"x" * (2 << 20)
        info = tarfile.TarInfo("mls_polish/PADDING_AFTER_COMPLETION.bin")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return archive


def test_pass_2_stops_once_the_sample_and_its_references_are_complete(
        tmp_path, monkeypatch):
    """The sample is strided across the whole split, so every wanted clip and
    the transcript land at scattered points in the archive. Once the last one
    and the transcript are in hand, the rest of a 115 GiB stream is read for
    nothing — hours of it, on the throttled connection the german prepare hit
    after it had already extracted all 40 clips."""
    archive = _mls_archive_with_a_tail(tmp_path)
    # One log per stream: pass 1 must read the whole archive to learn the total
    # the stride needs, so the padding is expected to appear there. The claim is
    # about pass 2 only.
    streams: list[list[str]] = []

    def counting(url):
        log: list[str] = []
        streams.append(log)
        return _CountingTar(tarfile.open(url, "r:gz"), log)
    monkeypatch.setattr(wer_bench, "_open_tar_stream", counting)

    meta = {"url": str(archive), "format": "mls-stream"}
    manifest, total, stride = wer_bench._prepare_mls_stream(
        meta, "test", tmp_path / "out", 3)

    assert len(manifest) == 3 and total == 6 and stride == 2
    assert len(streams) == 2, "listing and extraction are two separate streams"
    listing, extraction = streams
    assert "mls_polish/PADDING_AFTER_COMPLETION.bin" in listing, (
        "the listing reads the whole archive by design; a test that denied that "
        "would be asserting the total it needs is knowable some other way"
    )
    assert "mls_polish/PADDING_AFTER_COMPLETION.bin" not in extraction, (
        "the padding member is after everything the prepare needs; reaching it "
        "means the early break is gone"
    )


def test_the_early_break_does_not_drop_a_sample_member(tmp_path, monkeypatch):
    """The break has to be safe as well as fast: stopping early must not cost a
    clip. Every member the stride selected is checked for on disk, and the
    manifest has to carry a reference for each."""
    archive = _mls_archive_with_a_tail(tmp_path, n_audio=8)
    read: list[str] = []
    monkeypatch.setattr(wer_bench, "_open_tar_stream",
                        lambda url: _CountingTar(tarfile.open(url, "r:gz"), read))

    meta = {"url": str(archive), "format": "mls-stream"}
    manifest, total, _stride = wer_bench._prepare_mls_stream(
        meta, "test", tmp_path / "out", 4)

    assert total == 8
    assert len(manifest) == 4, "a break that costs a clip is not a safe break"
    assert all(m["reference"].startswith("po polsku") for m in manifest)
    assert all((tmp_path / "out" / m["audio"]).is_file() for m in manifest)


def test_a_missing_transcript_still_reads_to_the_end(tmp_path, monkeypatch):
    """The break requires the references as well as the audio, so an archive
    whose transcript genuinely lacks a wanted utterance degrades to reading the
    whole thing rather than emitting a manifest with a silent hole in it."""
    archive = _mls_archive_with_a_tail(tmp_path, n_audio=6)
    streams: list[list[str]] = []

    def counting(url):
        log: list[str] = []
        streams.append(log)
        return _CountingTar(tarfile.open(url, "r:gz"), log)
    monkeypatch.setattr(wer_bench, "_open_tar_stream", counting)
    # Break the reference for the last wanted utterance, which the stride
    # selects, without touching the archive on disk.
    meta = {"url": str(archive), "format": "mls-stream"}
    real_sorted = wer_bench._mls_members

    def broken_listing(url, split, cache_dir=None):
        audio_names, transcripts = real_sorted(url, split)
        return audio_names, []  # no transcript is ever readable

    monkeypatch.setattr(wer_bench, "_mls_members", broken_listing)
    manifest, _total, _stride = wer_bench._prepare_mls_stream(
        meta, "test", tmp_path / "out", 3)

    assert manifest == [], "an unreadable transcript must surface as zero refs"
    extraction = streams[-1]
    assert "mls_polish/PADDING_AFTER_COMPLETION.bin" in extraction, (
        "with the references unobtainable the loop must not stop early and "
        "must not fabricate a manifest"
    )


def test_the_member_index_is_cached_so_a_restart_skips_the_listing(
        tmp_path, monkeypatch):
    """Pass 1 reads the whole archive to learn the total the stride needs, so a
    prepare that dies in pass 2 would otherwise repay the expensive half. The
    listing for a fixed URL does not change between runs, so it is cached beside
    the output and reused."""
    archive = _mls_archive_with_a_tail(tmp_path, n_audio=6)
    meta = {"url": str(archive), "format": "mls-stream"}
    streams: list[list[str]] = []

    def counting(url):
        log: list[str] = []
        streams.append(log)
        return _CountingTar(tarfile.open(url, "r:gz"), log)

    monkeypatch.setattr(wer_bench, "_open_tar_stream", counting)
    out = tmp_path / "out"

    wer_bench._prepare_mls_stream(meta, "test", out, 3)
    after_first = len(streams)
    assert after_first == 2, "the first run lists and then extracts"

    wer_bench._prepare_mls_stream(meta, "test", out, 3)
    assert len(streams) == after_first + 1, (
        "the second run should open exactly one stream: the extraction. A second "
        "listing means the cache did not take effect and a restart still pays "
        "for the expensive half"
    )

    cache = out / ".mls_index.json"
    assert cache.is_file(), "the cache has to be written for a reuse to happen"
    listing = json.loads(cache.read_text(encoding="utf-8"))
    assert len(listing) == 2 and isinstance(listing[0], list)
    assert len(listing[0]) == 6, "the cached listing must hold the audio names"


def test_a_corrupt_index_cache_is_relisted_not_trusted(tmp_path):
    """A truncated cache would silently change the stride and therefore the
    sample, which is worse than paying for the listing again."""
    archive = _mls_archive_with_a_tail(tmp_path, n_audio=6)
    out = tmp_path / "out"
    out.mkdir()
    out.joinpath(".mls_index.json").write_text("[this is not a listing",
                                               encoding="utf-8")
    meta = {"url": str(archive), "format": "mls-stream"}
    manifest, total, _stride = wer_bench._prepare_mls_stream(
        meta, "test", out, 3)
    assert total == 6 and len(manifest) == 3, (
        "a corrupt cache must fall back to a real listing"
    )


def test_a_wrong_shaped_index_cache_is_relisted_not_trusted(tmp_path):
    """Unparseable text is caught by the exception handler; valid JSON with the
    wrong shape is the case the type check exists for, and a cache like this
    would silently change the stride if it were trusted."""
    archive = _mls_archive_with_a_tail(tmp_path, n_audio=6)
    out = tmp_path / "out"
    out.mkdir()
    out.joinpath(".mls_index.json").write_text(
        # Parses, and is even a two-element list, but the elements are not the
        # lists of member names the caller expects.
        '["mls_polish/test/audio/one.flac", 7]',
        encoding="utf-8")
    meta = {"url": str(archive), "format": "mls-stream"}
    manifest, total, _stride = wer_bench._prepare_mls_stream(
        meta, "test", out, 3)
    assert total == 6 and len(manifest) == 3, (
        "a wrong-shaped cache must fall back to a real listing, not be used"
    )


# ── the docs' summary claims are checked against the run records ─────────────

# Run records are named for the language in full — polish-small.json, not
# pl-small.json — which keeps the two lists below aligned by construction.
C10_LANGUAGES = ["german", "spanish", "french", "dutch", "italian", "polish"]
C10_NAMES = {"german": "German", "spanish": "Spanish", "french": "French",
             "dutch": "Dutch", "italian": "Italian", "polish": "Polish"}
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
DOCS = Path(__file__).resolve().parent.parent / "docs"


def _doc_text() -> str:
    """The two documents that state the P9 headline, concatenated.

    A claim can live in either: EVALUATION.md carries the criterion table, and
    TRANSCRIPTION_EVALUATION.md carries the narrative sections. A drift between
    them is the defect, so both are read by the same check.
    """
    parts = []
    for name in ("EVALUATION.md", "TRANSCRIPTION_EVALUATION.md"):
        path = DOCS / name
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _measured_c10_languages() -> list[str]:
    """The C10 languages with a committed run record."""
    return sorted(lang for lang in C10_LANGUAGES
                  if (RUNS / f"{lang}-small.json").is_file())


def test_the_docs_measured_c10_count_matches_the_run_records():
    """The third time this drifted in this workstream a new measurement landed
    and the summary paragraph still said 'two of the six'. The run records are
    the evidence and the docs are the claim; this makes the two impossible to
    disagree without a test failing.

    Reads every statement of the form 'N of the six/6 C10 languages' and
    requires each to equal the number of committed run records. A new phrasing
    that the pattern does not match fails the *other* test below rather than
    passing silently.
    """
    measured = _measured_c10_languages()
    if not measured:
        pytest.skip("no C10 run records in this checkout")
    text = _doc_text()
    stated = set()

    # Two phrasings carry the count. The explicit one names the set — "four of
    # the six" — and the elliptical one drops it because the sentence already
    # established which six it is about: "Four are measured and all four pass".
    # The second is how the drifting paragraphs have actually been written, so a
    # gate over the first alone would have stayed green while the count in the
    # next sentence said something else.
    patterns = (
        r"(?i)\b(one|two|three|four|five|six|\d)\s+of\s+the\s+(?:six|6)",
        # The lookbehind stops this reading the "six" in "of the six are
        # measured" as the count: that sentence has already named the count in
        # full, and "six" there is the size of the set, not how many were run.
        r"(?i)(?<!of the )\b(one|two|three|four|five|six)\s+(?:are|were|is)\s+"
        r"(?:all\s+)?measured",
        r"(?i)all\s+(one|two|three|four|five|six)\s+(?:pass|passed)",
    )
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            # A count claim is phrased with "measured" or "pass" nearby; the other
            # occurrences of the same shape are not claims at all. "Telugu is not
            # one of the six" is membership, and "scoring one of the six costs
            # ~15 MB" is a cost statement. Requiring one of those two words is what
            # keeps them out, and the window is wide enough to see the verb even
            # when the sentence runs on past the number.
            start = max(0, m.start() - 90)
            window = text[start:m.end() + 90]
            if not re.search(r"(?i)\b(measured|passes|pass)\b", window):
                continue
            # "unmeasured" contains "measured"; the boundary above handles that, and
            # this handles an explicit negation of the count itself.
            if re.search(r"(?i)\bnot\b\s+(\*\*)?one\s+of\s+the\s+(?:six|6)",
                         text[max(0, m.start() - 20):m.start()]):
                continue
            # The criterion's own threshold, not a result: "at least 3 of the 6",
            # "requires 3 of the 6", "asks for 3 of the 6". That number is the bar a
            # measurement has to clear, so it stays at 3 while the measured count
            # climbs — without this exclusion the gate could never pass once a
            # fourth language landed, which is the opposite of what it is for.
            #
            # The marker has to govern the number directly, with nothing between
            # them but emphasis: the pre-window is deliberately short, so a claim
            # elsewhere in the sentence is still read as a claim. "P9 requires 3 of
            # the 6. Four are measured and all four pass" is a threshold followed by
            # a separate statement of four, and only the second is a count.
            pre = text[max(0, m.start() - 40):m.start()]
            if re.search(r"(?i)(at\s+least|requires?|asks?\s+for)\s*(\*\*)?$",
                         pre):
                continue
            word = m.group(1).lower()
            # Not dict.get(word, int(word)): the default is evaluated eagerly, so
            # int("three") would raise even though "three" is in the map.
            count = _WORDS.get(word)
            stated.add(count if count is not None else int(word))
    assert stated, (
        "no 'N of the six C10' claim found; the phrasing has moved and the "
        "pattern needs updating so the claim stays checked"
    )
    assert stated == {len(measured)}, (
        f"the docs claim {sorted(stated)} of the six C10 languages are "
        f"measured, but {len(measured)} run records are committed: "
        f"{', '.join(C10_NAMES[l] for l in measured)}"
    )


def test_every_measured_c10_language_is_named_in_the_docs():
    """A run record that never reaches the docs is evidence nobody can see. Each
    measured language has to appear by name, or the measurement is effectively
    unpublished."""
    text = _doc_text()
    missing = [C10_NAMES[l] for l in _measured_c10_languages()
               if C10_NAMES[l] not in text]
    assert not missing, (
        f"run records exist for {', '.join(missing)} but the docs never name "
        f"them"
    )


def test_no_c10_language_is_claimed_measured_without_a_run_record():
    """The mirror failure: a doc that claims a language is measured when no
    record backs it. Read alongside the count above, this is what keeps a
    summary from drifting upwards as well as down."""
    text = _doc_text()
    for lang in C10_LANGUAGES:
        record = RUNS / f"{lang}-small.json"
        if record.is_file():
            continue
        name = C10_NAMES[lang]
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if name not in sentence:
                continue
            # A percentage in the same *sentence* is not enough on its own.
            # "Dutch" is an English word as well as a language name, and a
            # sentence about the Dutch cluster of C10 can legitimately carry a
            # percentage about something else in the same row. The claim that
            # needs a run record is "Dutch scores WER N%" — the figure has to
            # be attributed to the language, not merely share a sentence.
            attributed = re.search(
                rf"{name}\b[^.!?]{{0,60}}(\d+\.\d+)\s*%", sentence)
            if not attributed:
                continue
            pytest.fail(
                f"{name} is quoted with a measured figure "
                f"({attributed.group(1)}%) — "
                f"{sentence.strip()[:110]!r} — but {record.name} is not "
                f"committed. A measurement and its evidence have to arrive "
                f"together.")


# The per-language sections of TRANSCRIPTION_EVALUATION.md each close with a
# "what this measurement changes" paragraph that names the languages still
# unmeasured *at that point in the narrative*. Those paragraphs are historical,
# and they go stale the moment a later section measures one of the names they
# list — the failure is not hypothetical, it is how §7.3, §7.4 and §7.5 read
# before this gate existed, each still declaring German or French unmeasured
# after a later run had measured it.
#
# The gate only fires on the present tense ("remains unmeasured" / "remain
# unmeasured"): a paragraph written in the past tense ("were then unmeasured")
# is describing the state of the world at the time of that run, which is still
# true and is the correct repair.
#
# Whose names are checked is the subtle part. An earlier version matched any
# language name within 80 characters of the verb, which is correct on the
# sentences that drift but is a false positive on a sentence that is right:
# "Spanish, Polish, German, French and Italian all pass, and only Dutch
# remains unmeasured" declares only Dutch unmeasured, and the 80-character
# window fired on all five of the languages it names as passing. So instead the
# gate reads the verb's subject — the coordination of capitalized names that
# the verb agrees with — and checks exactly those. "Only Dutch" is not a name
# coordination, so nothing is checked and the sentence passes; "French and
# Dutch remain" is, and both are checked.
#
# The coordination is comma-joined as well as "and"-joined, so the separator
# admits both; the chain still cannot cross a lowercase word, which is what
# keeps "all pass, and only Dutch remains" from reading its subject as the
# five languages named earlier in the sentence. The one shape this does not
# reach is a name separated from the verb by an appositive ("French, being a
# clean-read audiobook language, remains unmeasured") — the subject scan finds
# no capitalized word adjacent to the verb and skips the sentence. That is a
# narrow enough gap that widening for it is not worth the false positives.
_SUBJECT = re.compile(
    r"(?P<names>(?:[A-Z][\w’'-]*(?:\s*(?:,|and|or)\s*)?)+)\s*$",
    re.UNICODE)


def _unmeasured_subjects(text):
    """Every language the docs presently call unmeasured, as the subject of the
    verb."""
    for verb in re.finditer(r"(?i)\bremain(?:s)?\s+unmeasured\b", text):
        before = text[:verb.start()]
        subject = _SUBJECT.search(before)
        if subject is None:
            continue
        for word in re.findall(r"[A-Z][\w’'-]*", subject.group("names")):
            yield word, subject.group("names") + verb.group(0)


@pytest.mark.parametrize("lang", sorted(C10_LANGUAGES))
def test_no_measured_language_is_declared_still_unmeasured(lang):
    """The other mirror: a language with a committed run record cannot also be
    described as unmeasured. Overclaiming is caught above; this catches the
    subtler drift, where a summary is correct and a per-language section a few
    pages earlier contradicts it."""
    record = RUNS / f"{lang}-small.json"
    if not record.is_file():
        pytest.skip(f"no run record for {lang}")
    name = C10_NAMES[lang]
    for subject, clause in _unmeasured_subjects(_doc_text()):
        if subject != name:
            continue
        pytest.fail(
            f"{name} is declared unmeasured — {clause.strip()[:110]!r} — but "
            f"{record.name} is committed. A per-language section's 'still "
            f"unmeasured' list is a snapshot; it has to move to the past tense "
            f"when a later run supersedes it.")
