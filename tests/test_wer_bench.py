"""Tests for the WER/CER arithmetic behind the transcription accuracy number.

`tools/wer_bench.py` produces the one number in this repository that a reader
is most likely to quote: transcription accuracy. The edit-distance code under
it had no tests at all, so a bug there would have misreported the headline
figure while every other test in the suite stayed green. These pin the
arithmetic itself — they do not re-run the model, which is what
`tools/wer_bench.py bench` is for.
"""
import io
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
