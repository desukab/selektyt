"""Tests for ``engine.memory.store`` — the Level 0..5 memory for one source.

Every test here is written against a way the store could be silently wrong
rather than against a spec sheet; the failure modes it names are recorded in
``docs/memory-failure-modes.md`` and referenced by number where they are
pinned. The store is the layer a persisted evidence file is read back through,
so its tests are mostly about *not* repairing what should be reported and not
claiming coverage that was never recorded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.memory.evidence import Evidence, EvidenceSet, Modality, Source
from engine.memory.intervals import Interval, IntervalSet
from engine.memory.store import (
    SCHEMA_VERSION,
    Finding,
    IncompatibleSchema,
    Level,
    LevelRecord,
    SourceMeta,
    StaleMemory,
    VideoMemory,
)


# ── fixtures ─────────────────────────────────────────────────────────────

def _meta(**over) -> SourceMeta:
    kw = dict(path="talk.mp4", duration=100.0, fingerprint="abc123")
    kw.update(over)
    return SourceMeta(**kw)


def _ev(claim: str = "The second speaker is on screen",
        spans=IntervalSet.of((12.0, 31.0)),
        modality: Modality = Modality.VISUAL,
        source: Source = Source.of("engine.reframe", "track_subject_center", samples=28),
        confidence: float = 1.0,
        rationale: str = "face track stable across the window",
        grounded: bool = True,
        id: str = "") -> Evidence:
    return Evidence(
        claim=claim,
        intervals=spans,
        modality=modality,
        source=source,
        confidence=confidence,
        rationale=rationale,
        grounded=grounded,
        id=id,
    )


def _memory(**over) -> VideoMemory:
    meta = over.pop("meta", None) or _meta()
    return VideoMemory(meta=meta, **over)


# ── level 0: the source rung is complete by construction ─────────────────

def test_empty_memory_still_covers_level_0():
    m = VideoMemory.empty(_meta())
    assert m.items == ()
    assert len(m.levels) == 1
    assert m.levels[0].level is Level.SOURCE
    assert list(m.levels[0].coverage) == [Interval(0.0, 100.0)]


def test_empty_memory_reports_no_findings():
    assert VideoMemory.empty(_meta()).validate() == []


# ── level resolution ─────────────────────────────────────────────────────

def test_level_of_reads_exact_method_before_module():
    # ``engine.reframe`` spans rungs, so the method decides.
    inspect = _ev(source=Source.of("engine.reframe", "track_subject_center"))
    assert VideoMemory.empty(_meta()).level_of(inspect) is Level.INSPECT


def test_level_of_falls_back_to_module():
    coarse = _ev(source=Source.of("engine.vad", "segment_with_meta"))
    assert VideoMemory.empty(_meta()).level_of(coarse) is Level.COARSE


def test_level_of_defaults_to_ground_for_unknown_instruments():
    """The load-bearing case. An instrument the table does not know is recorded
    as the *hardest* looking, so coverage is never over-claimed and no
    escalation is skipped on an unrecognised source."""
    unknown = _ev(source=Source.of("engine.experimental", "new_detector"))
    assert VideoMemory.empty(_meta()).level_of(unknown) is Level.GROUND


def test_levels_are_ordered_by_ordinal():
    """The ladder is a total order, which is what ``escalate_needed`` needs to
    mean anything."""
    assert Level.SOURCE < Level.COARSE < Level.SCENES < Level.CANDIDATES \
        < Level.INSPECT < Level.GROUND


# ── record: ids are assigned by the store, once ──────────────────────────

def test_record_assigns_deterministic_ids():
    m = VideoMemory.empty(_meta()).record(_ev(), _ev())
    assert [i.id for i in m.items] == ["e0001", "e0002"]


def test_record_does_not_mutate_the_original():
    m0 = VideoMemory.empty(_meta())
    m1 = m0.record(_ev())
    assert len(m0) == 0
    assert len(m1) == 1


def test_record_no_items_returns_self():
    m = VideoMemory.empty(_meta())
    assert m.record() is m


def test_record_respects_a_caller_supplied_id():
    m = VideoMemory.empty(_meta()).record(_ev(id="face-2"))
    assert m.items[0].id == "face-2"


def test_record_batches_in_one_copy():
    """Folding ``items + (new,)`` per item is quadratic; a batch of thousands
    must be one copy. The observable contract is that all items land and ids
    stay ascending."""
    batch = [_ev(claim=f"claim {n}") for n in range(500)]
    m = VideoMemory.empty(_meta()).record(*batch)
    assert len(m) == 500
    assert m.items[0].id == "e0001"
    assert m.items[-1].id == "e0500"


def test_record_ids_do_not_collide_with_supplied_ids():
    """A generated id starts past the highest numeric id already taken, so the
    two id spellings interleave instead of fighting over the same numbers."""
    m = VideoMemory.empty(_meta()).record(
        _ev(id="e0003"), _ev(), _ev(id="e0001"), _ev(),
    )
    ids = [i.id for i in m.items]
    assert ids == ["e0003", "e0004", "e0001", "e0005"]
    assert len(set(ids)) == 4


def test_record_keeps_a_duplicate_supplied_id_and_validate_reports_it():
    """A duplicate id is not silently renamed — the caller's name is theirs —
    and not silently dropped, because that would discard evidence over a naming
    mistake. It is kept, and ``validate`` reports it (failure mode #20); a
    query for the id returns the first item, which is the documented
    resolution."""
    m = VideoMemory.empty(_meta()).record(_ev(id="dup"), _ev(id="dup"))
    assert len(m) == 2
    findings = m.validate()
    dup = [f for f in findings if f.item_id == "dup"]
    assert len(dup) == 1
    assert dup[0].severity == "error"
    assert m.evidence().with_id("dup") is m.items[0]


def test_record_across_calls_keeps_ids_ascending():
    m = VideoMemory.empty(_meta()).record(_ev()).record(_ev()).record(_ev())
    assert [i.id for i in m.items] == ["e0001", "e0002", "e0003"]


# ── query: coverage is derived, not trusted from a stored summary ────────

def test_coverage_reads_the_items_not_the_levels():
    """``levels`` is a summary written by whoever last saved. A caller who has
    recorded since must see the new coverage, so it is derived."""
    m = VideoMemory.empty(_meta()).record(
        _ev(spans=IntervalSet.of((10.0, 20.0)),
            source=Source.of("engine.reframe", "track_subject_center")),
    )
    assert list(m.coverage(Level.INSPECT)) == [Interval(10.0, 20.0)]
    assert m.coverage(Level.SCENES).is_empty


def test_at_level_and_at_or_above_split_the_ladder():
    scenes = _ev(source=Source.of("engine.reframe_scenes", "analyse_scene"))
    inspect = _ev(source=Source.of("engine.reframe", "track_subject_center"))
    m = VideoMemory.empty(_meta()).record(scenes, inspect)
    assert len(m.at_level(Level.SCENES)) == 1
    assert len(m.at_level(Level.INSPECT)) == 1
    assert len(m.at_or_above(Level.INSPECT)) == 1
    assert len(m.at_or_above(Level.SCENES)) == 2
    assert len(m.at_or_above(Level.GROUND)) == 0


def test_escalate_needed_is_the_complement_of_at_or_above():
    """The reason the ladder exists: this is the work order a planner reads."""
    m = VideoMemory.empty(_meta()).record(
        _ev(spans=IntervalSet.of((10.0, 20.0)),
            source=Source.of("engine.reframe", "track_subject_center")),
    )
    gaps = m.escalate_needed(Level.INSPECT)
    assert list(gaps) == [Interval(0.0, 10.0), Interval(20.0, 100.0)]


def test_escalate_needed_is_the_whole_source_when_nothing_is_recorded():
    m = VideoMemory.empty(_meta())
    assert list(m.escalate_needed(Level.COARSE)) == [Interval(0.0, 100.0)]


def test_escalate_needed_is_empty_when_a_harder_rung_covers_everything():
    m = VideoMemory.empty(_meta()).record(
        _ev(spans=IntervalSet.of((0.0, 100.0)),
            source=Source.of("engine.memory.ground", "verify_span")),
    )
    assert m.escalate_needed(Level.COARSE).is_empty


def test_evidence_wraps_items_for_retrieval():
    m = VideoMemory.empty(_meta()).record(_ev(modality=Modality.AUDIO))
    s = m.evidence()
    assert isinstance(s, EvidenceSet)
    assert len(s) == 1
    assert len(s.for_modality(Modality.AUDIO)) == 1


# ── with_levels: a rung record is not derivable from the evidence ────────

def test_with_levels_records_a_rung():
    m = VideoMemory.empty(_meta()).with_levels(
        LevelRecord(Level.SCENES, IntervalSet.of((0.0, 40.0)), items=9),
    )
    assert len(m.levels) == 2
    assert m.levels[1].level is Level.SCENES
    assert m.levels[1].items == 9


def test_with_levels_unions_a_repeated_rung():
    """A second sweep at the same rung extends coverage; it does not replace
    the record, and it does not append a second record for the same level —
    the ladder has one entry per rung or the planner cannot read it."""
    m = VideoMemory.empty(_meta()).with_levels(
        LevelRecord(Level.SCENES, IntervalSet.of((0.0, 40.0)), items=9),
    ).with_levels(
        LevelRecord(Level.SCENES, IntervalSet.of((30.0, 70.0)), items=4),
    )
    by_level = {r.level: r for r in m.levels}
    assert list(by_level[Level.SCENES].coverage) == [Interval(0.0, 70.0)]
    assert by_level[Level.SCENES].items == 13
    assert sum(1 for r in m.levels if r.level is Level.SCENES) == 1


def test_with_levels_keeps_levels_sorted():
    m = VideoMemory.empty(_meta()).with_levels(
        LevelRecord(Level.GROUND, IntervalSet.of((0.0, 1.0))),
    ).with_levels(
        LevelRecord(Level.COARSE, IntervalSet.of((0.0, 1.0))),
    )
    assert [r.level for r in m.levels] == [
        Level.SOURCE, Level.COARSE, Level.GROUND,
    ]


def test_with_levels_no_records_returns_self():
    m = VideoMemory.empty(_meta())
    assert m.with_levels() is m


def test_an_empty_sweep_is_still_coverage():
    """The case ``record`` cannot express: a level-2 sweep that found nothing
    still examined the span, and that is a fact the planner needs — otherwise
    the span is re-examined at level 2 forever."""
    m = VideoMemory.empty(_meta()).with_levels(
        LevelRecord(Level.SCENES, IntervalSet.of((0.0, 40.0)), items=0,
                    notes="swept; no shot boundaries above threshold"),
    )
    assert m.at_level(Level.SCENES).is_empty
    assert list(m.levels[1].coverage) == [Interval(0.0, 40.0)]


# ── validate: report, never repair (failure modes #17, #18, #20) ────────

def test_validate_reports_zero_cardinality_without_rationale():
    """#17: no supporting spans, grounded, full confidence and no rationale is
    indistinguishable from a detection that returned nothing."""
    m = _memory(meta=_meta()).record(
        _ev(spans=IntervalSet.empty(), rationale="", confidence=1.0, grounded=True),
    )
    findings = m.validate()
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "failure mode #17" in findings[0].message


def test_validate_accepts_an_absence_claim_that_says_what_it_looked_for():
    """A rationale turns the #17 shape into a legitimate absence claim —
    "swept 0..100s for a slide; none detected" is a conclusion an editor can
    check, and the store has no basis to dispute it."""
    m = _memory(meta=_meta()).record(
        _ev(spans=IntervalSet.empty(),
            rationale="swept 0..100s for a slide; none detected"),
    )
    assert m.validate() == []


def test_validate_warns_on_unexplained_zero_cardinality_at_low_confidence():
    """The soft form of #17: no spans and no rationale, but not presented as a
    grounded certainty. Still worth an editor's attention, not an error."""
    m = _memory(meta=_meta()).record(
        _ev(spans=IntervalSet.empty(), rationale="",
            confidence=0.4, grounded=False),
    )
    findings = m.validate()
    assert len(findings) == 1
    assert findings[0].severity == "warning"


def test_validate_silent_on_normal_evidence():
    m = _memory(meta=_meta()).record(_ev())
    assert m.validate() == []


def test_validate_reports_span_past_source_end():
    """#18: bounds are reported, not clamped. A coarse instrument may legitimately
    report past the end; an editor trusting the timestamp is the harm."""
    m = _memory(meta=_meta(duration=100.0)).record(
        _ev(spans=IntervalSet.of((90.0, 110.0))),
    )
    findings = m.validate()
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "failure mode #18" in findings[0].message


def test_validate_reports_span_before_zero():
    m = _memory(meta=_meta()).record(
        _ev(spans=IntervalSet.of((-5.0, 10.0))),
    )
    assert "failure mode #18" in m.validate()[0].message


def test_validate_bounds_tolerate_floating_point_dust():
    """Sub-microsecond overshoot is representation noise, not an out-of-bounds
    span; reporting it would drown the real findings in dust."""
    m = _memory(meta=_meta(duration=100.0)).record(
        _ev(spans=IntervalSet.of((0.0, 100.0 + 1e-9))),
    )
    assert m.validate() == []


def test_validate_reports_each_finding_by_id():
    m = _memory(meta=_meta()).record(
        _ev(id="a", spans=IntervalSet.empty(), rationale="", confidence=1.0),
        _ev(id="b", spans=IntervalSet.of((0.0, 400.0))),
    )
    ids = {f.item_id for f in m.validate()}
    assert ids == {"a", "b"}


def test_finding_round_trip():
    f = Finding("error", "e0007", "message")
    d = f.to_dict()
    assert d == {"severity": "error", "item_id": "e0007", "message": "message"}


# ── serialisation: deterministic, and stale records refuse to load ───────

def test_save_and_load_round_trip(tmp_path: Path):
    m = VideoMemory.empty(_meta()).record(
        _ev(id="e0001"),
        _ev(claim="Speech region", spans=IntervalSet.of((0.0, 5.0)),
            modality=Modality.AUDIO, source=Source.of("engine.vad", "segment_with_meta")),
    ).with_levels(LevelRecord(Level.SCENES, IntervalSet.of((0.0, 40.0)), items=3))
    p = m.save(tmp_path / "nested" / "memory.json")
    assert p.exists()
    back = VideoMemory.load(p)
    assert back.meta == _meta()
    assert len(back) == 2
    assert [i.id for i in back.items] == ["e0001", "e0002"]
    assert back.at_level(Level.COARSE).items[0].claim == "Speech region"
    assert [r.level for r in back.levels] == [Level.SOURCE, Level.SCENES]


def test_save_is_deterministic(tmp_path: Path):
    """``sort_keys=True``, no wall clock, no absolute paths in the record: the
    same memory is the same bytes, which is what makes a committed memory file
    an auditable artifact."""
    m = VideoMemory.empty(_meta()).record(_ev())
    a = m.save(tmp_path / "a.json")
    b = VideoMemory.empty(_meta()).record(_ev()).save(tmp_path / "b.json")
    assert a.read_bytes() == b.read_bytes()


def test_save_is_atomic_via_a_tmp_file(tmp_path: Path):
    """The write goes to a ``.tmp`` sibling and is renamed into place, so a
    crash mid-write cannot leave a truncated memory that loads as an empty
    one."""
    m = VideoMemory.empty(_meta()).record(_ev())
    p = tmp_path / "memory.json"
    m.save(p)
    assert not (tmp_path / "memory.json.tmp").exists()
    assert json.loads(p.read_text())["schema"] == SCHEMA_VERSION


def test_load_rejects_a_future_schema(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"schema": "9.9", "meta": _meta().to_dict()}))
    with pytest.raises(IncompatibleSchema) as exc:
        VideoMemory.load(p)
    assert "9.9" in str(exc.value)


def test_load_refuses_a_stale_fingerprint(tmp_path: Path):
    """The timestamps in a memory describe a specific file. A different
    fingerprint means a different cut, and that is the caller's decision to
    make, not the store's to absorb."""
    p = tmp_path / "m.json"
    VideoMemory.empty(_meta(fingerprint="aaaa1111")).record(_ev()).save(p)
    with pytest.raises(StaleMemory) as exc:
        VideoMemory.load(p, fingerprint="bbbb2222")
    assert "aaaa1111"[:4] in str(exc.value)


def test_load_accepts_a_matching_fingerprint(tmp_path: Path):
    p = tmp_path / "m.json"
    VideoMemory.empty(_meta(fingerprint="aaaa1111")).record(_ev()).save(p)
    assert len(VideoMemory.load(p, fingerprint="aaaa1111")) == 1


def test_load_skips_the_fingerprint_check_when_not_given(tmp_path: Path):
    """A caller reading a memory for inspection — not for evidence about a live
    file — must not be forced to re-hash the source to open it."""
    p = tmp_path / "m.json"
    VideoMemory.empty(_meta(fingerprint="aaaa1111")).record(_ev()).save(p)
    assert len(VideoMemory.load(p)) == 1


def test_load_missing_file_raises_not_empty(tmp_path: Path):
    """An empty memory asserts level-0 coverage of a source; returning one for a
    path that does not exist would have the store claiming something about a
    file it never opened."""
    with pytest.raises(FileNotFoundError):
        VideoMemory.load(tmp_path / "nope.json")


def test_load_tolerates_meta_without_a_fingerprint(tmp_path: Path):
    """A fingerprint field that is absent is not the same as one that
    disagrees; absence is checked, not assumed."""
    d = VideoMemory.empty(_meta(fingerprint="")).to_dict()
    d["items"] = [_ev().to_dict()]
    (tmp_path / "m.json").write_text(json.dumps(d))
    assert len(VideoMemory.load(tmp_path / "m.json", fingerprint="anything")) == 1


# ── SourceMeta and LevelRecord round trips ───────────────────────────────

def test_source_meta_round_trip_keeps_every_field():
    meta = _meta(width=1920, height=1080, fps=29.97,
                 transcript_fingerprint="ttt9999")
    back = SourceMeta.from_dict(meta.to_dict())
    assert back == meta


def test_source_meta_from_dict_tolerates_missing_fields():
    back = SourceMeta.from_dict({"path": "x.mp4", "duration": 5.0})
    assert back.path == "x.mp4"
    assert back.duration == 5.0
    assert back.fingerprint == ""
    assert back.width == 0
    assert back.fps == 0.0


def test_source_meta_from_dict_tolerates_null_fields():
    back = SourceMeta.from_dict({
        "path": "x.mp4", "duration": 5.0, "width": None, "height": None,
        "fps": None, "fingerprint": None, "transcript_fingerprint": None,
    })
    assert back.width == 0
    assert back.fps == 0.0
    assert back.transcript_fingerprint == ""


def test_level_record_round_trip():
    r = LevelRecord(Level.INSPECT, IntervalSet.of((1.0, 2.0)), items=7, notes="n")
    back = LevelRecord.from_dict(r.to_dict())
    assert back.level is Level.INSPECT
    assert list(back.coverage) == [Interval(1.0, 2.0)]
    assert back.items == 7
    assert back.notes == "n"


def test_level_record_from_dict_defaults_to_source():
    back = LevelRecord.from_dict({"coverage": {"intervals": []}})
    assert back.level is Level.SOURCE
    assert back.items == 0


# ── iteration and length ─────────────────────────────────────────────────

def test_memory_iterates_its_items():
    m = VideoMemory.empty(_meta()).record(_ev(id="a"), _ev(id="b"))
    assert [i.id for i in m] == ["a", "b"]
    assert len(m) == 2


def test_memory_is_pure_across_queries():
    """Querying does not record; a store that accumulated items as a side
    effect of being read would diverge from what it persisted."""
    m = VideoMemory.empty(_meta()).record(_ev())
    m.at_level(Level.INSPECT)
    m.evidence()
    m.escalate_needed(Level.GROUND)
    assert len(m) == 1
    assert len(m.items) == 1
