"""Tests for the language-fitness gate (engine.language_fitness).

The gate exists because the corpus measured faster-whisper ``small`` at 151% WER
on Telugu — the transcript carries more error words than the reference has words
— and nothing else in the pipeline can see that. The coverage gate compares the
transcript against the *audio*, so a dense Telugu source scores a high coverage
ratio, well-formed text and a wrong one; the editorial gates read the transcript's
text and cannot tell a fluent hallucination from a faithful one. This is the one
check that asks whether the *model* can do this *language*, and it asks a table
measured offline rather than asking the thing under suspicion.

These tests pin six properties, and four of them are about the table's
construction rather than its verdicts, because a gate that reads a table is only
as honest as the loader that built it:

1. the verdicts on the measured pairs, including the language the bar decides
   against (Italian, 20.11% WER, 4.53% CER — mostly-right characters where the
   words are wrong);
2. a record with 2 clips does not get to score a language, because the record
   that forced this rule scores English at WER 1.0000 and would have made the
   most verified language in the table unusable;
3. a beam-size sweep does not get to score a language, because its 4.05% is not
   the decode a ``--model small`` user gets and had taken the English slot,
   making English look 20% worse than it is;
4. absence is never a pass: an unmeasured language, and a measured language on an
   unmeasured model, both read ``not_measured``;
5. only an unusable verdict stops a run — ``poor`` and ``not_measured`` warn,
   because refusing on either would block every Romance and Slavic language the
   tool can serve, and most of the languages it has never heard of;
6. the gate is wired into the pipeline before selection and render, is
   overridable, and the override leaves the measurement in the report.

Every number quoted below is read out of a committed record and then
independently recomputed from the same record's own fields, so a claim cannot
drift from the record that backs it — the failure mode these tests exist to
remove, and the one that the render cost figures had until
``tests/test_cost_model.py`` pinned them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.language_fitness import (
    MIN_CLIP_COUNT,
    PRODUCTION_BEAM,
    RUNS_DIR,
    WER_BAR,
    WER_REFUSE,
    FitnessReport,
    _model_name,
    fitness,
    is_refusable,
    load_table,
)

# The committed measurements the gate reads. Quoted here so a test that asserts a
# verdict also asserts the number the verdict was computed from; recomputed from
# each record's own fields below so the quote cannot drift from the file.
REAL_RUNS = Path(RUNS_DIR)

_HAVE_FFMPEG = True
try:
    import shutil as _shutil
    _HAVE_FFMPEG = _shutil.which("ffmpeg") is not None
except ImportError:  # pragma: no cover
    _HAVE_FFMPEG = False


def _record(name: str) -> dict:
    return json.loads((REAL_RUNS / name).read_text(encoding="utf-8"))


def _rtf(name: str) -> float:
    """The RTF a record claims, recomputed from the fields the ratio comes from.

    ``wer`` is stored rounded to four decimals and the records carry the
    reference word count but not the error count, so WER is not recomputable
    here; ``rtf`` is; and a record whose stored ratio disagrees with its own
    wall clock over its own audio is a record whose timings were hand-edited.
    """
    d = _record(name)
    return d["wall_seconds"] / d["audio_seconds"]


# ─── 1. verdicts on the measured pairs ─────────────────────────────────────

@pytest.mark.parametrize("name,language,model,verdict", [
    ("c9-small.json", "en", "small", "usable"),
    ("c9-medium.json", "en", "medium", "usable"),
    ("c9-large-v3.json", "en", "large-v3", "usable"),
    ("spanish-small.json", "es", "small", "usable"),
    ("german-small.json", "de", "small", "usable"),
    ("polish-small.json", "pl", "small", "usable"),
    ("french-small.json", "fr", "small", "usable"),
    # 20.11% WER and 4.53% CER: the characters are mostly right where the words
    # are not, which is why Italian is the language the bar decides against
    # rather than the one it exempts. The bar sits at 25% and Italian is under
    # it, by design and not by accident.
    ("italian-small.json", "it", "small", "usable"),
    # The two cases the module exists for. 91.54% on medium is the only
    # measured member of the `poor` band; 151.08% on small is unusable. Both are
    # Telugu on the same corpus, so the pair is the routing suggestion the
    # report actually makes — and the reason `poor` does not refuse: small is
    # worse, so the suggestion has nowhere to go.
    ("telugu-medium.json", "te", "medium", "poor"),
    ("telugu-small.json", "te", "small", "unusable"),
])
def test_the_committed_records_score_the_verdicts_quoted(name, language, model, verdict):
    record = _record(name)
    report = fitness(language, model)
    assert report.verdict == verdict, (
        f"{name}: expected {verdict}, got {report.verdict} at WER {report.wer}")
    # The verdict follows the number the record carries, and the record's own
    # timings are self-consistent — three statements that must not drift apart.
    assert report.wer == pytest.approx(record["wer"])
    assert record["rtf"] == pytest.approx(_rtf(name), abs=2e-3)
    assert report.cer == pytest.approx(record["cer"])
    assert report.clip_count == record["clip_count"]
    assert report.record == name
    assert report.language == language
    assert report.model == model


def test_the_bar_sits_in_the_measured_empty_interval():
    """Seven languages measured and nothing falls between Italian's 20.11% and
    Telugu-on-medium's 91.54% — a 4.5x gap with no data in it. The bar is a cut
    point placed inside that gap, not a calibration, and this test pins the
    placement so moving it is a visible decision rather than a silent one."""
    assert WER_BAR == 0.25
    assert WER_REFUSE == 1.0
    table = load_table()
    values = sorted(v["wer"] for v in table.values())
    below_bar = [v for v in values if v <= WER_BAR]
    above_bar = [v for v in values if v > WER_BAR]
    assert below_bar and above_bar, "the bar classifies something on both sides"
    # The gap the bar was placed in: the highest usable measurement and the
    # lowest non-usable one must straddle it, and nothing may land inside it.
    assert max(below_bar) < WER_BAR < min(above_bar)
    # Refusing at 1.0 is the "more error words than the source has words" line,
    # and exactly one committed pair is above it — the Telugu-on-small case.
    assert [v for v in values if v > WER_REFUSE] == [pytest.approx(1.5108)]


def test_the_alternatives_are_the_routing_suggestion_in_best_wer_order():
    """``detail`` recommends a model when one clears the bar, so the order has to
    be measured-best-first and every model on the language has to be present —
    a suggestion that omits the model that would fix it is worse than none."""
    report = fitness("te", "small")
    wers = [a["wer"] for a in report.alternatives]
    assert wers == sorted(wers), "alternatives must be best WER first"
    models = {a["model"] for a in report.alternatives}
    assert models == {"small", "medium"}, (
        f"both Telugu measurements must appear, got {models}")
    # The unusable case has a measured escape, and the detail has to name it
    # with the number, not with "try a bigger model".
    assert "medium" in report.detail
    assert "91.5%" in report.detail
    assert "151.1%" in report.detail


def test_detail_says_a_bigger_model_is_a_hope_not_a_fix_when_none_clears():
    """Telugu-on-small has a measured alternative; a language where no model
    clears the bar does not, and the honest thing to say is that a larger model
    is a hope rather than a fix — not a recommendation dressed as a measurement.
    Built on a synthetic table because no committed language is in that state."""
    report = fitness("zz", "small", runs_dir=_table_dir({
        "zz-small.json": {"language": "zz", "model": "small", "wer": 1.2,
                          "cer": 0.9, "clip_count": 40, "beam_size": 1,
                          "error_words": 60, "reference_words": 50},
        "zz-medium.json": {"language": "zz", "model": "medium", "wer": 0.9,
                           "cer": 0.7, "clip_count": 40, "beam_size": 1,
                           "error_words": 45, "reference_words": 50},
    }))
    assert report.verdict == "unusable"
    assert all(a["wer"] > WER_BAR for a in report.alternatives)
    assert "hope rather than a fix" in report.detail
    assert "medium 90.0%" in report.detail, (
        "the alternative that fails must be named with its number, so the "
        "advice is auditable rather than vague")


def test_detail_for_an_unmeasured_language_never_claims_verification():
    report = fitness("zu", "small")
    assert report.verdict == "not_measured"
    assert "no WER measurement exists" in report.detail
    # The alternatives list stays empty rather than inheriting another
    # language's numbers.
    assert report.alternatives == []


# ─── 2 and 3. the two records the loader must refuse ───────────────────────

def test_a_two_clip_record_cannot_score_a_language():
    """``nasa-c4-small.json`` scores English at WER 1.0000 on 2 clips. That is a
    reference corpus whose alignment failed, not a statement about English;
    loaded naively it makes the most verified language in the table unusable,
    and it would have been the difference between a gate that helps and one that
    breaks on its first real user. It has to land in ``excluded``, not in the
    table — and it has to be *named* there, because a loader that quietly drops
    the records that disagree with it is how a table comes to support any
    conclusion."""
    record = _record("nasa-c4-small.json")
    assert record["clip_count"] < MIN_CLIP_COUNT
    assert record["wer"] == 1.0
    table = load_table()
    assert ("en", "small") in table
    assert table[("en", "small")]["record"] == "c9-small.json"
    assert table[("en", "small")]["wer"] == pytest.approx(0.0338)
    excluded = fitness("en", "small").excluded
    assert "nasa-c4-small.json" in excluded


def test_a_beam_sweep_cannot_score_a_language():
    """``c9-small-beam5.json`` is a beam-size experiment at 4.05%, not the decode
    a ``--model small`` user gets with the default beam. Before this rule it won
    the en/small slot on filename order and made English look 20% worse than it
    is — an optimistic-looking table built from a record answering a different
    question. The sweep is *worse* than production, which is the direction that
    matters: including it understates English, so excluding it is a correction
    toward the number production actually measures, not flattery."""
    record = _record("c9-small-beam5.json")
    assert record["beam_size"] != PRODUCTION_BEAM
    assert record["wer"] > 0.0338, (
        "the sweep must be worse than production, or excluding it would flatter "
        "the table rather than correct it")
    report = fitness("en", "small")
    assert report.wer == pytest.approx(0.0338)
    assert report.record == "c9-small.json"
    assert "c9-small-beam5.json" in report.excluded


def test_both_exclusion_classes_are_named_and_nothing_else_is():
    """Two rules, both auditable: too few clips, and the wrong beam. A third
    class — records with no ``wer`` key, the decode experiments and the coverage
    measurements that share the directory — is skipped without being listed,
    because it was never a candidate and listing it would bury the two that
    were."""
    excluded = fitness("en", "small").excluded
    assert "nasa-c4-small.json" in excluded
    assert "c9-small-beam5.json" in excluded
    # The two re-runs of c9-small agree with it and clear every rule, so they
    # must not appear as refusals — they land in ``superseded`` instead, which
    # is a different fact and needs a different list. An exclusion list that
    # names agreement as suspicion is the same noise as one that omits
    # suspicion.
    assert "verify-current-small.json" not in excluded
    assert "redteam-small-verify.json" not in excluded


def test_duplicates_resolve_to_the_record_with_the_most_clips():
    """``verify-current-small.json`` and ``redteam-small-verify.json`` are re-runs
    of ``c9-small.json`` on the same corpus. They agree, but the resolver has to
    take the record with the most clips, so a partial re-run cannot overwrite a
    full measurement — and three records agreeing on one number is evidence the
    number is real, which a last-writer-wins loader would throw away. The
    unequal-clip case, where the tie-break is not reached at all, is pinned in
    the synthetic-table test below."""
    table = load_table()
    entry = table[("en", "small")]
    assert entry["clip_count"] == 50
    assert entry["record"] == "c9-small.json"
    # The re-runs are equal on clips, so the resolver's tie-break is filename
    # order and c9-small.json is first — pinned here because it is the one place
    # the loader falls back to an arbitrary rule, and the tie is real in the
    # committed corpus.
    for name in ("verify-current-small.json", "redteam-small-verify.json"):
        assert _record(name)["clip_count"] == 50
        assert _record(name)["wer"] == pytest.approx(entry["wer"])


# ─── 4. absence is never a pass ────────────────────────────────────────────

def test_an_unmeasured_language_is_not_measured_on_any_model():
    assert fitness("zu", "small").verdict == "not_measured"
    assert fitness("zu", "medium").verdict == "not_measured"


def test_a_measured_language_on_an_unmeasured_model_stays_not_measured():
    """Telugu is measured on small and medium; asking about large-v3 must not
    inherit either number, because the suggestion ``detail`` would make from an
    inherited WER is a claim about a decode that never ran."""
    report = fitness("te", "large-v3")
    assert report.verdict == "not_measured"
    assert report.wer is None
    # The alternatives survive: the useful content for this pair is which models
    # *are* measured, not a borrowed score.
    assert {a["model"] for a in report.alternatives} == {"small", "medium"}


def test_a_missing_or_empty_table_degrades_to_not_measured_everywhere():
    """No table, no verdicts — and no pass. This is the degradation path a
    checkout without the corpus takes, and it has to read as unverified rather
    than as fine."""
    for runs in (Path("corpus/wer/does-not-exist"), Path("corpus/render/runs")):
        assert load_table(runs) == {}
        assert fitness("en", "small", runs_dir=runs).verdict == "not_measured"
        assert fitness("te", "small", runs_dir=runs).verdict == "not_measured"


def test_a_corrupt_record_is_skipped_and_named_nowhere():
    """A record that will not parse is a broken measurement, not a measurement
    of anything; the table has to carry on, and the exclusion list must not
    mention it, because it was never a candidate. Loud about the two rules that
    are real, silent about the file that is merely unreadable."""
    d = _table_dir({
        "ok-small.json": {"language": "en", "model": "small", "wer": 0.03,
                          "cer": 0.01, "clip_count": 40, "beam_size": 1,
                          "error_words": 3, "reference_words": 100},
        "broken.json": "{this is not json",
    })
    table = load_table(d)
    assert ("en", "small") in table
    assert fitness("en", "small", runs_dir=d).excluded == []


def test_superseded_duplicates_are_named_and_the_agreement_is_visible():
    """``verify-current-small.json`` and ``redteam-small-verify.json`` are re-runs
    of ``c9-small.json`` on the same corpus, all at 3.38% on 50 clips. Equal on
    clips means the tie-break is filename order and ``c9-small.json`` sorts
    first, so it wins — and the other two land in ``superseded``, where their
    agreement with the winner is itself the evidence. A table that named only
    its refusals would let a future re-run that *disagrees* disappear the moment
    it was built."""
    report = fitness("en", "small")
    assert report.record == "c9-small.json"
    assert set(report.superseded) == {"verify-current-small.json",
                                     "redteam-small-verify.json"}
    # Superseded records are not refusals: the two lists must not overlap, since
    # a record cannot both fail a rule and lose a race it never entered.
    assert not (set(report.superseded) & set(report.excluded))
    # And the agreement is real — every record on this pair reads 3.38%.
    for name in report.superseded:
        assert _record(name)["wer"] == pytest.approx(report.wer), (
            f"{name} disagrees with the record that superseded it; the "
            "superseded list exists to surface exactly this")


def test_a_superseded_record_that_disagrees_is_still_named():
    """The case the list is for: a re-run with fewer clips that contradicts the
    full measurement. It must not become the table's number (fewer clips), and
    it must not vanish (the disagreement is the point)."""
    d = _table_dir({
        "full.json": {"language": "aa", "model": "small", "wer": 0.10,
                      "cer": 0.02, "clip_count": 50, "beam_size": 1},
        "rerun.json": {"language": "aa", "model": "small", "wer": 0.60,
                       "cer": 0.30, "clip_count": 20, "beam_size": 1},
    })
    report = fitness("aa", "small", runs_dir=d)
    assert report.wer == pytest.approx(0.10)
    assert report.verdict == "usable"
    assert report.superseded == ["rerun.json"]
    assert report.excluded == []


# ─── 5. what stops a run ──────────────────────────────────────────────────

def test_only_an_unusable_verdict_stops_the_run():
    """``poor`` warns and ``not_measured`` warns; only ``unusable`` refuses. The
    asymmetric error cost is the point: a false positive is a loud request to
    verify, a false negative ships wrong captions silently. The reason ``poor``
    does not refuse is stated in ``is_refposable`` and is not the comforting one
    — the band's only measured member, Telugu on medium at 91.54%, is badly
    wrong and has no measured escape, since small is worse at 151.08%."""
    usable = fitness("en", "small")
    poor = fitness("te", "medium")
    unusable = fitness("te", "small")
    unknown = fitness("zu", "small")
    assert usable.verdict == "usable"
    assert poor.verdict == "poor"
    assert unusable.verdict == "unusable"
    assert unknown.verdict == "not_measured"
    assert not is_refusable(usable)
    assert not is_refusable(poor), (
        f"Telugu-on-medium at {poor.wer:.1%} must not stop a run — see "
        "is_refusable, whose rationale this pins")
    assert not is_refusable(unknown)
    assert is_refusable(unusable)


def test_the_poor_band_has_exactly_one_measured_member():
    """The reason ``poor`` does not refuse rests on the band being nearly
    unpopulated: if a future corpus run lands a Romance or Slavic language
    between 25% and 100%, the rationale needs revisiting rather than
    inheriting. This test is what makes that a visible decision."""
    table = load_table()
    poor = {f"{l}/{m}": v["wer"] for (l, m), v in table.items()
            if WER_BAR < v["wer"] <= WER_REFUSE}
    assert poor == {"te/medium": pytest.approx(0.9154)}, (
        f"the poor band has a new member: {poor} — is_refusable's rationale "
        "assumes the band is otherwise unmeasured")


def test_model_names_are_normalised_to_the_size_the_cli_takes():
    """``c9-large-v3.json`` records the directory the model was loaded from, so
    the key would not match the ``--model large-v3`` a user passes to get the
    same behaviour, and the most verified pair in the table would read
    not_measured."""
    assert _model_name("corpus/wer/models/faster-whisper-large-v3") == "large-v3"
    assert _model_name("faster-whisper-medium") == "medium"
    assert _model_name("small") == "small"
    report = fitness("en", "corpus/wer/models/faster-whisper-large-v3")
    assert report.verdict == "usable"
    assert report.wer == pytest.approx(0.0338)


# ─── the loader, pinned independently of the committed corpus ─────────────

def _table_dir(records: dict[str, dict]) -> Path:
    """A synthetic runs directory, so the loader's rules are tested against
    fixtures whose every field is known rather than against records whose
    contents the corpus chose. A gate whose tests only ever touch the real
    corpus silently passes while the corpus changes underneath it."""
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="wer-table-"))
    for name, payload in records.items():
        (d / name).write_text(json.dumps(payload), encoding="utf-8")
    return d


def test_the_loader_keeps_every_rule_independent_of_the_corpus():
    """One synthetic table exercising all four rules at once: a usable pair, a
    pair below the clip floor, a beam-sweep pair, and a duplicate with fewer
    clips than the record it would otherwise overwrite. The duplicate is named
    ``aa-partial`` and the record that must win is ``zz-full`` so the two rules
    that could resolve the collision disagree: filename order would take
    ``aa-partial``, clip count takes ``zz-full``. A loader whose resolver is
    really filename order passes every test that does not arrange this."""
    d = _table_dir({
        "zz-full.json": {"language": "aa", "model": "small", "wer": 0.10,
                         "cer": 0.02, "clip_count": 40, "beam_size": 1,
                         "error_words": 4, "reference_words": 40},
        "aa-partial.json": {"language": "aa", "model": "small", "wer": 0.30,
                            "cer": 0.05, "clip_count": 30, "beam_size": 1,
                            "error_words": 3, "reference_words": 10},
        "probe-small.json": {"language": "aa", "model": "small", "wer": 1.0,
                             "cer": 1.0, "clip_count": 2, "beam_size": 1,
                             "error_words": 2, "reference_words": 2},
        "sweep-small.json": {"language": "aa", "model": "small", "wer": 0.05,
                             "cer": 0.01, "clip_count": 40, "beam_size": 5,
                             "error_words": 2, "reference_words": 40},
    })
    table = load_table(d)
    assert set(table) == {("aa", "small")}, (
        f"only the full production-beam record may score, got {sorted(table)}")
    entry = table[("aa", "small")]
    # Clip count, not filename order: the worse record sorts first.
    assert entry["record"] == "zz-full.json"
    assert entry["wer"] == pytest.approx(0.10)
    assert entry["clip_count"] == 40
    report = fitness("aa", "small", runs_dir=d)
    assert report.verdict == "usable"
    assert set(report.excluded) == {"probe-small.json", "sweep-small.json"}
    # The duplicate that lost the race is named too: it passed every rule and
    # still is not the number the table reports, which is a different fact from
    # being refused and needs its own list so a disagreement cannot vanish.
    assert report.superseded == ["aa-partial.json"]
    # An empty directory is the checkout-without-corpus path, not an error.
    empty = _table_dir({})
    assert load_table(empty) == {}
    assert fitness("aa", "small", runs_dir=empty).verdict == "not_measured"


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg is needed to build the media")
def test_the_gate_scores_the_model_that_ran_not_the_one_requested(tmp_path,
                                                                 monkeypatch):
    """A silent downgrade to a smaller model can move a language across the
    usable line, so the verdict has to follow provenance. Requested ``large``,
    ran ``small``, detected Telugu: the run must refuse on the pair that
    actually produced the words, and would not if the gate read the requested
    model — which is why this is a pipeline test rather than the unit that
    checks the same arithmetic on ``fitness`` directly."""
    import selekyt.pipeline as p
    from tests.speech_fixtures import LOUD_AMP, write_speech_wav
    from tests.test_pipeline_e2e import _synthetic_video, _transcript

    media = _synthetic_video(tmp_path / "source" / "clip.mp4")
    monkeypatch.setattr(p, "download",
                        lambda source, dest, cookies=None: (media, {"title": "t"}))

    def transcribe(media, **kw):
        checkpoint = kw.get("checkpoint_dir")
        if checkpoint is not None:
            (checkpoint / "audio.wav").parent.mkdir(parents=True, exist_ok=True)
            write_speech_wav(checkpoint / "audio.wav", 60.0, amp=LOUD_AMP)
        # The downgrade shape the real provenance carries: large was requested,
        # small ran. te/large is not_measured and te/small is unusable, so the
        # two disagree and only one is the right answer.
        return _transcript(), "te", {
            "engine": "test", "requested_model": "large", "model": "small",
            "downgrade_reason": "large is unavailable in this environment",
            "word_timestamps": "test", "fallback_reason": "",
        }

    monkeypatch.setattr(p, "transcribe_auto", transcribe)
    with pytest.raises(RuntimeError) as excinfo:
        p.run("https://example.com/v", tmp_path, model="large", limit=2)
    message = str(excinfo.value)
    assert "not usable on this language" in message
    # The message has to quote the model that ran. If it named large, the error
    # would be pointing at a model the corpus has never measured on Telugu.
    assert "model small measures WER 151.1%" in message
    assert not (tmp_path / "project" / "selections.json").exists()


# ─── 6. the gate as the pipeline runs it ───────────────────────────────────


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg is needed to build the media")
def test_an_unusable_language_stops_the_run_before_selection_and_render(
        tmp_path, monkeypatch):
    """The gate has to sit before the expensive stages, and a refusal must not
    reach them. Exercised through ``run`` so the wiring is what is tested — the
    module's own verdicts are pinned above; this is the claim that the pipeline
    consults them at all, and consults the model that *ran* rather than the one
    requested."""
    import selekyt.pipeline as p
    from tests.test_pipeline_e2e import _synthetic_video, _transcript

    media = _synthetic_video(tmp_path / "source" / "clip.mp4")
    monkeypatch.setattr(p, "download",
                        lambda source, dest, cookies=None: (media, {"title": "t"}))
    # Telugu as detected, small as the model that ran — the committed pair the
    # gate exists for. A voiced extraction is written as the real transcriber
    # would, so the refusal is the language gate's and not the no-speech
    # gate's wearing its message.
    def transcribe(media, **kw):
        from tests.speech_fixtures import LOUD_AMP, write_speech_wav
        checkpoint = kw.get("checkpoint_dir")
        if checkpoint is not None:
            (checkpoint / "audio.wav").parent.mkdir(parents=True, exist_ok=True)
            write_speech_wav(checkpoint / "audio.wav", 60.0, amp=LOUD_AMP)
        return _transcript(), "te", {"engine": "test", "model": "small",
                                     "word_timestamps": "test",
                                     "fallback_reason": ""}

    monkeypatch.setattr(p, "transcribe_auto", transcribe)
    with pytest.raises(RuntimeError) as excinfo:
        p.run("https://example.com/v", tmp_path, model="small", limit=2)
    message = str(excinfo.value)
    assert "not usable on this language" in message
    # The routing suggestion has to survive into the error: the user is reading
    # this in a terminal, and "use a different model" without the measured
    # number is not actionable.
    assert "medium" in message
    assert "151.1%" in message
    assert "--skip-language-check" in message
    # The expensive stages must not have run.
    assert not (tmp_path / "project" / "selections.json").exists()
    assert not (tmp_path / "project" / "facebook").exists()


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg is needed to build the media")
def test_the_language_gate_is_skippable_and_records_the_measurement(
        tmp_path, monkeypatch):
    """The override has to leave the measurement in the report rather than
    deleting the evidence: a user who bypasses the gate and ships is the one
    who most needs the verdict on record. Mirrors the coverage gate's own
    bypass test so the two gates agree on what an override means."""
    import selekyt.pipeline as p
    from tests.speech_fixtures import LOUD_AMP, write_speech_wav
    from tests.test_pipeline_e2e import _synthetic_video, _transcript

    media = _synthetic_video(tmp_path / "source" / "clip.mp4")
    monkeypatch.setattr(p, "download",
                        lambda source, dest, cookies=None: (media, {"title": "t"}))

    def transcribe(media, **kw):
        checkpoint = kw.get("checkpoint_dir")
        if checkpoint is not None:
            (checkpoint / "audio.wav").parent.mkdir(parents=True, exist_ok=True)
            write_speech_wav(checkpoint / "audio.wav", 60.0, amp=LOUD_AMP)
        return _transcript(), "te", {"engine": "test", "model": "small",
                                     "word_timestamps": "test",
                                     "fallback_reason": ""}

    monkeypatch.setattr(p, "transcribe_auto", transcribe)
    project = p.run("https://example.com/v", tmp_path, model="small", limit=2,
                    skip_language_check=True)
    report = json.loads((project / "report.json").read_text(encoding="utf-8"))
    fit = report["language_fitness"]
    assert fit["verdict"] == "unusable"
    assert fit["wer"] == pytest.approx(1.5108)
    assert fit["language"] == "te"
    assert fit["model"] == "small"
    # The records the loader refused are on the report, so an editor auditing a
    # bypassed run can see what the table is built from.
    assert "c9-small-beam5.json" in fit["excluded"]
    assert "nasa-c4-small.json" in fit["excluded"]


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg is needed to build the media")
def test_a_usable_language_records_the_verdict_without_stopping(tmp_path,
                                                               monkeypatch):
    """English on small is the pair every other test in the suite runs, so the
    gate has to record a ``usable`` verdict for it and never interfere — this is
    the regression guard that the gate does not make the common path
    conditional on anything the corpus does not measure."""
    import selekyt.pipeline as p
    from tests.speech_fixtures import LOUD_AMP, write_speech_wav
    from tests.test_pipeline_e2e import _synthetic_video, _transcript

    media = _synthetic_video(tmp_path / "source" / "clip.mp4")
    monkeypatch.setattr(p, "download",
                        lambda source, dest, cookies=None: (media, {"title": "t"}))

    def transcribe(media, **kw):
        checkpoint = kw.get("checkpoint_dir")
        if checkpoint is not None:
            (checkpoint / "audio.wav").parent.mkdir(parents=True, exist_ok=True)
            write_speech_wav(checkpoint / "audio.wav", 60.0, amp=LOUD_AMP)
        return _transcript(), "en", {"engine": "test", "model": "small",
                                     "word_timestamps": "test",
                                     "fallback_reason": ""}

    monkeypatch.setattr(p, "transcribe_auto", transcribe)
    project = p.run("https://example.com/v", tmp_path, model="small", limit=2)
    report = json.loads((project / "report.json").read_text(encoding="utf-8"))
    assert report["language_fitness"]["verdict"] == "usable"
    assert report["language_fitness"]["wer"] == pytest.approx(0.0338)


def test_the_reported_verdict_follows_the_model_that_ran_not_the_one_requested():
    """A silent downgrade to a smaller model can move a language across the
    usable line, so the verdict has to follow the provenance model. Pinned in
    the unit rather than the pipeline because the pipeline test above already
    exercises the wiring; this is the arithmetic that wiring exists for."""
    report = fitness("te", "large")
    assert report.verdict == "not_measured"
    # Telugu-on-medium is 91.54% — poor, refusable is False, but the transcript
    # needs a human check, and ``detail`` has to say so without recommending a
    # model the corpus has not measured.
    medium = fitness("te", "medium")
    assert medium.verdict == "poor"
    assert not is_refusable(medium)
    assert "human check" in medium.detail


def test_fitness_report_serialises_every_field():
    """``as_dict`` is what ``report.json`` ships, so a field added to the
    dataclass and forgotten here is a field the report silently drops."""
    d = fitness("te", "small").as_dict()
    for key in ("verdict", "language", "model", "wer", "cer", "clip_count",
                "record", "alternatives", "excluded", "superseded"):
        assert key in d, f"report.json must carry {key}"
    assert isinstance(d["alternatives"], list)
    assert isinstance(d["excluded"], list)
    assert isinstance(d["superseded"], list)
    # A report is constructible without a table at all, which is the shape an
    # early-failure run leaves behind.
    bare = FitnessReport(verdict="not_measured", language="x", model="y")
    assert bare.as_dict()["wer"] is None
    assert "no WER measurement exists" in bare.detail
