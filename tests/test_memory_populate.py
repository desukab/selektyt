"""Tests for the pipeline-to-memory seam (engine.memory.populate).

These pin the properties the module exists for, and each one is written from a
failure mode first — the way an unpopulated or half-populated memory can lie:

1. it captures signals a run already computed, without re-listening;
2. one failing instrument cannot destroy another's evidence;
3. a source with unknown length does not get an unbounded silence span;
4. a coverage pass that never measured does not become an empty-looking memory;
5. the record round-trips, so a retrieval layer reads what the pipeline wrote.

The audio here is synthesised through ``tests.speech_fixtures`` for the same
reason the coverage tests use it: the speech/silence boundary has to be heard
by the real VAD chain rather than asserted, and a fixture that only pretends to
have speech would make the silence-complement assertions vacuous.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from engine.coverage import check as check_coverage
from engine.memory import Level, SourceMeta, StaleMemory, VideoMemory, populate
from engine.memory.evidence import Modality
from tests.speech_fixtures import LOUD_AMP, write_speech_wav


def _region(start: float, end: float, score: float = 0.9):
    return SimpleNamespace(start=start, end=end, score=score)


def _meta(duration: float = 100.0, **kw) -> SourceMeta:
    base = dict(path="source.mp4", duration=duration, fingerprint="media-digest")
    base.update(kw)
    return SourceMeta(**base)


def _corpus(*, topics=(), beats=(), links=(), broll=()):
    """A corpus report stand-in: the adapters are duck-typed on field names, so
    the shape is what matters and the real ``CorpusReport`` is exercised by the
    pipeline test below."""
    return SimpleNamespace(topics=list(topics), beats=list(beats),
                           links=list(links), broll=list(broll))


def _topic(start, end, label="budget"):
    return SimpleNamespace(start=start, end=end, label=label,
                           reason="the numbers", confidence=0.7)


def _beat(start, end, kind="turn"):
    return SimpleNamespace(start=start, end=end, kind=kind, confidence=0.6,
                           label="the turn", text="but then")


def _broll(start, end, cue="cutaway to the slides"):
    return SimpleNamespace(start=start, end=end, cue=cue, confidence=0.5)


def _link(setup, payoff):
    return SimpleNamespace(setup_start=setup[0], setup_end=setup[1],
                           payoff_start=payoff[0], payoff_end=payoff[1],
                           setup_text="a", payoff_text="b",
                           distance_s=payoff[0] - setup[0])


# ── 1. capture, no second listen ────────────────────────────────────────────

def test_regions_and_corpus_become_queryable_evidence():
    """Every artifact handed in lands as evidence with the right modality, and
    the silence complement is derived rather than re-measured."""
    mem, summary = populate.build(
        _meta(duration=12.0),
        speech_regions=(_region(0.0, 5.0), _region(7.0, 12.0)),
        vad_backend="silero",
        corpus=_corpus(topics=[_topic(2.0, 11.0)],
                       beats=[_beat(8.0, 9.0)],
                       links=[_link((2.0, 4.0), (8.0, 9.0))],
                       broll=[_broll(3.0, 5.0)]),
    )

    assert summary.errors == (), "a clean build reports no stage failures"
    assert summary.items == 7

    claims = {i.claim: i.modality for i in mem.items}
    assert claims["Speech is present"] == Modality.AUDIO
    assert claims["Silence: no speech detected"] == Modality.AUDIO
    assert claims["The topic “budget” is discussed"] == Modality.EDITORIAL

    # Silence fills the one gap the speech regions leave: the whole source is
    # covered at the coarsest rung, which is what makes escalate_needed mean
    # "examined everywhere at level 1" rather than "examined wherever speech
    # happened to be".
    silence = [i for i in mem.items if i.claim.startswith("Silence")]
    assert len(silence) == 1
    assert silence[0].intervals.intervals[0].start == 5.0
    assert silence[0].intervals.intervals[0].end == 7.0

    # Provenance reaches the item: a speech claim from the energy backend and one
    # from silero are not the same claim, and the rationale is what separates
    # them.
    speech = [i for i in mem.items if i.claim == "Speech is present"][0]
    assert "silero" in speech.rationale
    assert speech.source.module == "engine.vad"


def test_levels_are_derived_from_the_items_not_from_the_request():
    """A rung record appears for a level only if evidence or coverage exists at
    it — a level that was never reached must not be recorded as examined."""
    mem, _ = populate.build(
        _meta(duration=20.0),
        speech_regions=(_region(0.0, 5.0),),
        corpus=_corpus(topics=[_topic(1.0, 4.0)]),
    )
    by_level = {r.level: r for r in mem.levels}
    assert by_level[Level.COARSE].items == 2  # speech + the derived silence
    assert by_level[Level.CANDIDATES].items == 1
    # No instrument produced VISUAL evidence here, so no level-2 record claims a
    # scene examination that never happened.
    assert Level.SCENES not in by_level
    assert Level.GROUND not in by_level


# ── 2. one failing instrument cannot lose another's evidence ────────────────

def test_a_corpus_stage_failure_keeps_the_speech_evidence():
    """A malformed beat must not also destroy the topics and the regions. This
    is the failure mode that a single try/except around the whole corpus would
    create, and it is why each family is its own stage."""
    broken = _corpus(topics=[_topic(1.0, 4.0)],
                     beats=[SimpleNamespace(start="not-a-number", end=2.0)])
    mem, summary = populate.build(
        _meta(duration=10.0),
        speech_regions=(_region(0.0, 5.0),),
        corpus=broken,
    )

    assert any("beats" in e for e in summary.errors), \
        "the failing stage is named, not silent"
    assert all("topics" not in e for e in summary.errors), \
        "an unrelated stage is not implicated"
    claims = {i.claim for i in mem.items}
    assert "The topic “budget” is discussed" in claims, \
        "the topic stage's evidence survives the beat stage's failure"
    assert "Speech is present" in claims, \
        "the audio evidence survives a corpus failure"
    assert summary.findings == (), "a stage failure is not a validation finding"


def test_a_raising_speech_adapter_still_records_the_corpus():
    """The fault boundary runs both ways: a VAD-shaped failure must not reach
    into the editorial evidence either."""
    mem, summary = populate.build(
        _meta(duration=10.0),
        speech_regions=object(),  # not iterable: any adapter touching it raises
        corpus=_corpus(topics=[_topic(1.0, 4.0)]),
    )
    assert "speech_regions" in " ".join(summary.errors)
    assert [i.claim for i in mem.items] == ["The topic “budget” is discussed"]


def test_no_corpus_means_no_topic_stages_and_no_duplicated_diagnosis():
    """The pipeline already printed why its corpus build failed; the memory
    records the absence as a missing stage rather than inventing an error."""
    mem, summary = populate.build(
        _meta(duration=10.0),
        speech_regions=(_region(0.0, 5.0),),
        corpus=None,
    )
    names = {s.name for s in summary.stages}
    assert names == {"speech_regions", "silence_regions"}
    assert summary.items == 2


# ── 3. unknown length must not become an unbounded silence ───────────────────

def test_unknown_duration_produces_no_silence_span():
    """``complement`` on an unbounded range is an infinite span, which is the
    out-of-bounds failure ``VideoMemory.validate`` exists to catch. The honest
    answer for a source of unknown length is no silence evidence at all — not a
    span running to infinity. The regions that do not fit are reported rather
    than clamped, because a timestamp an editor cannot seek to is a lie."""
    mem, summary = populate.build(
        _meta(duration=0.0),
        speech_regions=(_region(0.0, 5.0),),
    )
    assert not any(i.claim.startswith("Silence") for i in mem.items), \
        "an unknown length must not turn into an unbounded silence span"
    assert summary.items == 1
    assert len(summary.findings) == 1, \
        "the region that does not fit a zero-length source is flagged"
    assert "outside the source" in summary.findings[0].message


def test_regions_outside_the_source_are_reported_not_silently_dropped():
    """A coarse instrument can report past the end; validate flags it rather
    than clamping."""
    mem, summary = populate.build(
        _meta(duration=8.0),
        speech_regions=(_region(0.0, 5.0), _region(6.0, 12.0)),
    )
    bounds = [f for f in summary.findings if "outside the source" in f.message]
    assert len(bounds) == 1, "only the out-of-bounds region is flagged"


# ── 4. the coverage gate's own record is the source of truth ────────────────

def test_check_carries_the_regions_measure_computed(tmp_path):
    """``check`` used to bind them to a throwaway name. If they are not carried
    out here, the pipeline would have to run the VAD a second time to populate
    memory — the whole cost argument for the capture."""
    audio = tmp_path / "audio.wav"
    write_speech_wav(audio, 20.0, amp=LOUD_AMP)
    report = check_coverage(audio, [{"start": 0.0, "end": 4.0}])

    assert report.speech_regions, "the regions must leave the gate"
    assert report.vad_backend, "provenance travels with them"
    first = report.speech_regions[0]
    assert 0.0 <= first.start < first.end

    # The gate's own report is a verdict for a UI, not a span index: thousands
    # of regions would swamp a record an editor opens to read one ratio.
    as_dict = report.as_dict()
    assert "speech_regions" not in as_dict
    assert as_dict["vad_backend"] == report.vad_backend


def test_a_gate_that_never_measured_reports_nothing_to_remember(tmp_path):
    """Missing audio means no duration and no regions. Returning an empty record
    would make an unmeasured source look like one measured and found empty, so
    the gate says it did not measure and the pipeline skips memory entirely."""
    report = check_coverage(tmp_path / "absent.wav", [{"start": 0.0, "end": 1.0}])
    assert report.verdict == "not_measured"
    assert tuple(report.speech_regions) == ()


# ── 5. what the pipeline wrote is what a retrieval layer reads ──────────────

def test_the_built_memory_round_trips(tmp_path):
    """The store's own load path must hand back the same evidence and levels:
    a retrieval layer reads ``memory.json``, so a write that does not survive a
    read is a capture that never happened."""
    mem, summary = populate.build(
        _meta(duration=12.0),
        speech_regions=(_region(0.0, 5.0), _region(7.0, 12.0)),
        vad_backend="silero",
        corpus=_corpus(topics=[_topic(2.0, 11.0)]),
    )
    assert summary.findings == ()
    path = tmp_path / "memory.json"
    mem.save(path)
    assert path.is_file()

    back = VideoMemory.load(path)
    assert back.meta.duration == 12.0
    assert back.meta.transcript_fingerprint == ""
    assert {i.claim for i in back.items} == {i.claim for i in mem.items}
    assert [r.level for r in back.levels] == [r.level for r in mem.levels]
    assert back.coverage(Level.COARSE).duration == 12.0


def test_summary_is_json_serialisable_for_the_run_report():
    """The pipeline writes this into ``report.json``, so a non-serialisable
    summary would break a run whose clips are already rendered."""
    import json as _json

    mem, summary = populate.build(
        _meta(duration=10.0),
        speech_regions=(_region(0.0, 5.0),),
        corpus=_corpus(beats=[_beat(1.0, 2.0)]),
    )
    as_dict = summary.as_dict()
    # findings carry severity, item and detail in one line, not a dataclass
    round_tripped = _json.loads(_json.dumps(as_dict))
    assert round_tripped["items"] == summary.items
    assert isinstance(round_tripped["errors"], list)
    assert isinstance(round_tripped["findings"], list)


def test_fingerprints_bind_the_memory_to_its_inputs(tmp_path):
    """A re-transcribe changes the words and not the pixels, so the media digest
    and the transcript digest are separate keys: a stale memory is detectable
    without discarding good visual evidence, and the load path raises rather
    than migrating timestamps that describe a different file."""
    mem, _ = populate.build(
        _meta(duration=10.0, transcript_fingerprint="run-abc"),
        speech_regions=(_region(0.0, 5.0),),
    )
    path = tmp_path / "memory.json"
    mem.save(path)

    back = VideoMemory.load(path)
    assert back.meta.fingerprint == "media-digest"
    assert back.meta.transcript_fingerprint == "run-abc", \
        "the transcript digest survives the round trip"

    # The media digest is the load path's staleness check: a caller that hands
    # in a different file must be refused rather than silently served spans
    # recorded from another source.
    with pytest.raises(StaleMemory):
        VideoMemory.load(path, fingerprint="a-different-file")


# ── 6. a second run accumulates the first run's understanding ──────────────

def test_a_previous_record_is_merged_not_overwritten():
    """When the caller hands in the record a prior run wrote, the new build
    keeps the prior evidence beside this run's: a topic the corpus failed to
    produce today stays queryable from the day it was measured."""
    prior, _ = populate.build(
        _meta(duration=12.0),
        speech_regions=(_region(0.0, 5.0), _region(7.0, 12.0)),
        vad_backend="silero",
        corpus=_corpus(topics=[_topic(2.0, 11.0)]),
    )

    # This run's corpus stage failed upstream (no corpus handed in): the build
    # cannot re-derive the topic evidence the prior run recorded.
    mem, summary = populate.build(
        _meta(duration=12.0),
        speech_regions=(_region(0.0, 5.0), _region(7.0, 12.0)),
        vad_backend="silero",
        previous=prior,
    )

    claims = {i.claim for i in mem.items}
    prior_topic_claims = {i.claim for i in prior.items
                          if i.modality is Modality.EDITORIAL}
    assert prior_topic_claims.issubset(claims), \
        "a corpus failure this run must not delete the topic evidence"
    assert summary.carried > 0, "the summary must say evidence was carried"


def test_rederived_identical_claims_are_carried_with_their_ids():
    """VAD and corpus are deterministic instruments: a second identical run
    re-derives the same claims. The claim is not duplicated -- and the item's
    id, which an editor may have referenced, is not recast."""
    kwargs = dict(
        meta=_meta(duration=12.0),
        speech_regions=(_region(0.0, 5.0), _region(7.0, 12.0)),
        vad_backend="silero",
        corpus=_corpus(topics=[_topic(2.0, 11.0)], beats=[_beat(2.0, 5.0)]),
    )
    prior, summary1 = populate.build(**kwargs)
    mem, summary2 = populate.build(**kwargs, previous=prior)

    assert len(mem.items) == len(prior.items), "an identical claim is not two claims"
    assert summary2.carried == len(mem.items)
    # byte-level: the same record content under stable ids round-trips equal
    assert [i.id for i in mem.items if i.id] == [i.id for i in prior.items if i.id]
    assert mem.to_dict() == prior.to_dict()


def test_carried_ids_never_collide_with_new_ones():
    """The store's generated-id counter starts past every id already present,
    carried or not: the merged record must not fabricate a duplicate id."""
    prior, _ = populate.build(
        _meta(duration=12.0),
        speech_regions=(_region(0.0, 5.0),),
        vad_backend="silero",
    )
    mem, _ = populate.build(
        _meta(duration=12.0),
        speech_regions=(_region(0.0, 5.0),),
        vad_backend="silero",
        corpus=_corpus(topics=[_topic(2.0, 11.0)]),
        previous=prior,
    )
    ids = [i.id for i in mem.items if i.id]
    assert len(ids) == len(set(ids)), "duplicate ids in a merged record"
    assert all(i.claim for i in mem.items)

    findings = mem.validate()
    assert not [f for f in findings if f.severity == "error"], findings


def test_a_different_instrument_is_a_new_not_identical_claim():
    """A coverage re-pass with a different VAD backend reports a different
    claim even over the same seconds: an energy reading and a Silero detection
    disagree about provenance, and memory keeps both rather than betting."""
    prior, _ = populate.build(
        _meta(duration=12.0),
        speech_regions=(_region(0.0, 5.0),),
        vad_backend="silero",
    )
    mem, _ = populate.build(
        _meta(duration=12.0),
        speech_regions=(_region(0.0, 5.0),),
        vad_backend="energy",
        previous=prior,
    )
    claims = [i for i in mem.items if "speech" in i.claim.lower()]
    assert len({i.source.params_digest or i.source.method for i in claims}) >= 2, (
        "the two backends must both survive as distinct observations")
