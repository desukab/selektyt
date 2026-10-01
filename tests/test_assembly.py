"""Tests for the assembly stage.

These build small corpora and candidate lists by hand rather than running a
transcription, because the sequencing is the logic under test and it must be
exercised against a corpus whose threads and beats are known. The fixtures
deliberately include the degenerate cases the module documents: a corpus with
no threads, a candidate bound to no thread, and a clip with no renderable span.
"""

from __future__ import annotations

import json

import pytest

from engine.assembly import AssemblyConfig, assemble, program_order_of
from engine.edit_decision import EditorialAction
from engine.edit_plan import retention_intervals as _retention
from engine.selection import PolicyNote, Scored


# ── minimal stand-ins for the corpus and candidate layers ────────────────────


class _U:
    """A corpus unit carrying only what the assembly reads."""

    def __init__(self, index, start, end, thread_id=None, beat_id=None):
        self.index = index
        self.start = start
        self.end = end
        self.thread_id = thread_id
        self.beat_id = beat_id


class _Beat:
    def __init__(self, kind, intensity=0.5):
        self.kind = kind
        self.intensity = intensity


class _Thread:
    def __init__(self, thread_id, title, unit_indices, confidence=0.7):
        self.thread_id = thread_id
        self.title = title
        self.unit_indices = unit_indices
        self.confidence = confidence


class _Corpus:
    def __init__(self, units, beats, threads):
        self.units = units
        self.beats = beats
        self.threads = threads


class _Word:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class _Cand:
    """A candidate with just enough for the assembly: a span, words to align
    on, and the scores the ordering and the report read."""

    def __init__(self, start, end, *, hook="", text="", words=None,
                 opening_score=1.0, reason="", story_type="story"):
        self.start = start
        self.end = end
        self.hook = hook
        self.text = text or hook
        self.words = words or _synth_words(start, end)
        self.opening_score = opening_score
        self.reason = reason
        self.story_type = story_type

    # ``total_score`` is read only to break ties in the policy; give it a
    # stable value so the assembly's ordering is fully determined here.
    total_score = 0.5


def _synth_words(start, end, n=6):
    """Evenly spaced words so ``retention_intervals`` has something to align."""
    span = max(0.0, end - start)
    step = span / n
    return [_Word(start + i * step, start + (i + 1) * step * 0.9)
            for i in range(n)]


# ── fixtures


@pytest.fixture
def two_threads() -> _Corpus:
    """Thread 0 covers units 0-2 (an early story), thread 1 covers units 3-5
    (a later one), so an arc ordering has something to bind."""
    units = [
        _U(0, 0.0, 20.0, thread_id=0, beat_id=0),
        _U(1, 20.0, 40.0, thread_id=0, beat_id=0),
        _U(2, 40.0, 60.0, thread_id=0, beat_id=1),
        _U(3, 200.0, 220.0, thread_id=1, beat_id=2),
        _U(4, 220.0, 240.0, thread_id=1, beat_id=2),
        _U(5, 240.0, 260.0, thread_id=1, beat_id=3),
    ]
    beats = [_Beat("setup", 0.4), _Beat("tension", 0.8),
             _Beat("turn", 0.6), _Beat("resolution", 0.5)]
    threads = [_Thread(0, "the incident", [0, 1, 2], 0.72),
               _Thread(1, "the aftermath", [3, 4, 5], 0.55)]
    return _Corpus(units, beats, threads)


# ─────────────────────────────────────────────────────── sequencing: source

class TestSourceOrder:
    def test_chronological_by_default(self, two_threads):
        # Candidates given out of source order come back in source order.
        c1 = _Cand(210.0, 240.0, hook="aftermath")
        c0 = _Cand(10.0, 50.0, hook="incident")
        a = assemble([c1, c0], corpus=two_threads,
                     config=AssemblyConfig(order="source"))
        assert [p.candidate.hook for p in a.placements] == ["incident", "aftermath"]

    def test_source_order_ignores_threads(self, two_threads):
        c0 = _Cand(10.0, 50.0, hook="incident")
        c1 = _Cand(210.0, 240.0, hook="aftermath")
        a = assemble([c0, c1], corpus=two_threads,
                     config=AssemblyConfig(order="source"))
        # Chronological. Each clip still names the thread it comes from, but
        # nothing is grouped because no thread holds two selections.
        assert [p.candidate.hook for p in a.placements] == ["incident", "aftermath"]
        assert [p.thread_id for p in a.placements] == [0, 1]
        assert not any(p.grouped for p in a.placements)

    def test_actions_are_all_keep_in_source_order(self, two_threads):
        c0 = _Cand(10.0, 50.0, hook="incident")
        c1 = _Cand(210.0, 240.0, hook="aftermath")
        a = assemble([c0, c1], corpus=two_threads,
                     config=AssemblyConfig(order="source"))
        assert [d.action for d in a.graph.decisions] == [EditorialAction.KEEP] * 2


# ───────────────────────────────────────────────────────── sequencing: arc

class TestArcOrder:
    def test_thread_clips_become_adjacent(self, two_threads):
        # Interleaved by thread: the two incident clips sit next to each other
        # even though an aftermath clip starts between them in source time.
        inc_a = _Cand(5.0, 35.0, hook="incident a")
        aft = _Cand(205.0, 235.0, hook="aftermath")
        inc_b = _Cand(45.0, 58.0, hook="incident b")
        a = assemble([inc_a, aft, inc_b], corpus=two_threads,
                     config=AssemblyConfig(order="arc"))
        hooks = [p.candidate.hook for p in a.placements]
        assert hooks.index("incident a") + 1 == hooks.index("incident b")

    def test_thread_internal_order_is_chronological(self, two_threads):
        inc_b = _Cand(45.0, 58.0, hook="incident b")
        inc_a = _Cand(5.0, 35.0, hook="incident a")
        a = assemble([inc_b, inc_a], corpus=two_threads,
                     config=AssemblyConfig(order="arc"))
        assert [p.candidate.hook for p in a.placements] == ["incident a", "incident b"]

    def test_groups_lead_with_the_strongest_opening(self, two_threads):
        # The aftermath opens better than the incident, so its group leads
        # the reel despite starting 200 s later in the source. Both groups need
        # two clips each, or no thread is shared and nothing groups.
        inc_a = _Cand(5.0, 35.0, hook="incident a", opening_score=0.20)
        inc_b = _Cand(45.0, 58.0, hook="incident b", opening_score=0.20)
        aft_a = _Cand(205.0, 235.0, hook="aftermath a", opening_score=0.90)
        aft_b = _Cand(245.0, 258.0, hook="aftermath b", opening_score=0.90)
        a = assemble([inc_a, inc_b, aft_a, aft_b], corpus=two_threads,
                     config=AssemblyConfig(order="arc"))
        assert [p.candidate.hook for p in a.placements] == [
            "aftermath a", "aftermath b", "incident a", "incident b"]

    def test_a_move_is_recorded_as_promote_and_demote(self, two_threads):
        inc_a = _Cand(5.0, 35.0, hook="incident a", opening_score=0.20)
        inc_b = _Cand(45.0, 58.0, hook="incident b", opening_score=0.20)
        aft_a = _Cand(205.0, 235.0, hook="aftermath a", opening_score=0.90)
        aft_b = _Cand(245.0, 258.0, hook="aftermath b", opening_score=0.90)
        a = assemble([inc_a, inc_b, aft_a, aft_b], corpus=two_threads,
                     config=AssemblyConfig(order="arc"))
        prom = [d for d in a.graph.decisions if d.action is EditorialAction.PROMOTE]
        dem = [d for d in a.graph.decisions if d.action is EditorialAction.DEMOTE]
        # The aftermath pair jumps the incident pair, so both aftermath clips
        # promote and both incident clips demote.
        assert len(prom) == 2 and len(dem) == 2
        assert max(d.anchor_timeline_in for d in prom) < \
            min(d.anchor_timeline_in for d in dem)

    def test_arc_degrades_to_source_without_threads(self):
        corpus = _Corpus(units=[_U(0, 0.0, 20.0)], beats=[], threads=[])
        c1 = _Cand(100.0, 130.0, hook="late")
        c0 = _Cand(0.0, 20.0, hook="early")
        a = assemble([c1, c0], corpus=corpus,
                     config=AssemblyConfig(order="arc"))
        assert [p.candidate.hook for p in a.placements] == ["early", "late"]
        assert all(p.thread_id is None for p in a.placements)

    def test_a_clip_bound_to_no_thread_is_ungrouped(self, two_threads):
        # A clip in a span no thread claims stays a singleton, placed by its
        # own opening among the groups.
        stray = _Cand(400.0, 430.0, hook="stray", opening_score=0.95)
        inc = _Cand(5.0, 55.0, hook="incident", opening_score=0.10)
        a = assemble([stray, inc], corpus=two_threads,
                     config=AssemblyConfig(order="arc"))
        assert a.placements[0].candidate.hook == "stray"
        assert not a.placements[0].grouped
        # and it takes no chapter marker
        assert a.graph.markers == ()

    def test_a_thread_only_one_selection_shares_binds_nothing(self, two_threads):
        # One clip from a thread is a labelled clip, not a group: it stays a
        # singleton and takes no chapter marker.
        lone = _Cand(5.0, 35.0, hook="the incident alone", opening_score=0.9)
        a = assemble([lone], corpus=two_threads,
                     config=AssemblyConfig(order="arc"))
        # It still names its thread — that is corpus membership, not grouping.
        assert a.placements[0].thread_id == 0
        assert not a.placements[0].grouped
        assert a.graph.markers == ()

    def test_min_group_size_raises_the_binding_bar(self, two_threads):
        # Two clips from one thread bind at the default; raising the bar past
        # the group's size leaves them as singletons again.
        inc_a = _Cand(5.0, 35.0, hook="incident a", opening_score=0.5)
        inc_b = _Cand(45.0, 58.0, hook="incident b", opening_score=0.4)
        grouped = assemble([inc_a, inc_b], corpus=two_threads,
                           config=AssemblyConfig(order="arc", min_group_size=2))
        loose = assemble([inc_a, inc_b], corpus=two_threads,
                         config=AssemblyConfig(order="arc", min_group_size=3))
        assert grouped.placements[0].thread_id == 0
        assert all(p.grouped for p in grouped.placements)
        assert not any(p.grouped for p in loose.placements)
        # membership is reported either way; grouping is what the bar moves
        assert loose.placements[0].thread_id == 0


# ─────────────────────────────────────────────────────────────── roles

class TestRoles:
    def test_role_comes_from_the_corpus_beats(self, two_threads):
        # Unit 2 is a tension beat, so a clip carrying it is tension.
        c = _Cand(40.0, 58.0, hook="the turn")
        a = assemble([c], corpus=two_threads)
        # unit 2 is beat 1 = "tension"
        assert a.placements[0].role == "tension"

    def test_role_is_empty_when_the_corpus_has_no_beat(self):
        corpus = _Corpus(units=[_U(0, 0.0, 20.0, thread_id=None, beat_id=None)],
                         beats=[], threads=[])
        a = assemble([_Cand(0.0, 20.0, hook="x")], corpus=corpus)
        assert a.placements[0].role == ""

    def test_role_is_empty_without_a_corpus(self):
        a = assemble([_Cand(0.0, 20.0, hook="x")], corpus=None)
        assert a.placements[0].role == ""


# ─────────────────────────────────────────────────────────── confidence

class TestConfidence:
    def test_confidence_is_measured_when_a_thread_characterises_the_clip(
            self, two_threads):
        # A clip alone in its thread is not grouped, but the corpus still
        # characterises it, so the confidence is measured and names the thread.
        a = assemble([_Cand(5.0, 55.0, hook="incident")], corpus=two_threads)
        d = a.graph.decisions[0]
        assert not a.placements[0].grouped
        assert not d.confidence.unmeasured
        assert "the incident" in d.confidence.basis
        assert d.confidence.value == pytest.approx(0.72)

    def test_confidence_is_unmeasured_for_a_clip_no_thread_claims(self, two_threads):
        a = assemble([_Cand(400.0, 430.0, hook="stray")], corpus=two_threads)
        assert a.graph.decisions[0].confidence.unmeasured

    def test_confidence_is_unmeasured_without_a_corpus(self):
        a = assemble([_Cand(0.0, 20.0, hook="x")], corpus=None)
        assert a.graph.decisions[0].confidence.unmeasured

    def test_no_decision_carries_a_fabricated_number(self, two_threads):
        a = assemble([_Cand(5.0, 55.0, hook="incident"),
                      _Cand(400.0, 430.0, hook="stray")], corpus=two_threads)
        for d in a.graph.decisions:
            if d.confidence.value is not None:
                assert 0.0 <= d.confidence.value <= 1.0
                assert d.confidence.basis not in ("", "unmeasured")


# ───────────────────────────────────────────────────────────── evidence

class TestEvidence:
    def test_policy_notes_become_evidence(self):
        c = _Cand(0.0, 20.0, hook="x")
        scored = [Scored(candidate=c, base=0.5, adjustments=[
            PolicyNote(term="scene", delta=-0.22, other="other clip",
                       reason="2 clips already chosen within 60s of this span"),
        ])]
        a = assemble([c], corpus=None, scored=scored)
        ev = a.graph.decisions[0].evidence
        assert len(ev) == 1
        assert ev[0].source == "engine.selection.score_in_context#scene"
        assert "within 60s" in ev[0].claim

    def test_no_scored_notes_means_no_evidence(self):
        a = assemble([_Cand(0.0, 20.0, hook="x")], corpus=None)
        assert a.graph.decisions[0].evidence == ()


# ─────────────────────────────────────────────────────── removed ranges

class TestRemovedRanges:
    def test_no_removed_ranges_when_the_span_is_intact(self):
        # Fewer than two intervals means no internal gap to record.
        a = assemble([_Cand(0.0, 20.0, hook="x")], corpus=None)
        assert a.graph.removed == ()
        assert a.report["removed"] == 0

    def test_a_pause_between_intervals_is_recorded(self):
        # Two word groups separated by a pause small enough that
        # retention_intervals will cut it: its contract refuses a cut that
        # would drop more than 28% of the span, so a huge gap yields the
        # intact span and no removal — a property worth keeping the test
        # honest about, not something to route around.
        words = [_Word(0.0, 1.0), _Word(1.0, 2.0), _Word(2.0, 3.0),
                 _Word(4.5, 5.5), _Word(5.5, 6.5), _Word(6.5, 7.5)]
        c = _Cand(0.0, 7.5, hook="gappy", words=words)
        assert len(_retention(c)) == 2, "fixture must actually produce a cut"
        a = assemble([c], corpus=None)
        assert len(a.graph.removed) == 1
        r = a.graph.removed[0]
        assert r.kind == "remove_silence"
        assert r.start == pytest.approx(3.0, abs=0.2)
        assert r.end == pytest.approx(4.5, abs=0.2)
        assert "retention_intervals" in r.reason
        assert r.decision_id == a.graph.decisions[0].id

    def test_a_pause_too_big_to_cut_is_left_alone(self):
        # The 72% retention floor: a span that is mostly pause is shipped
        # intact, and the assembly records no removal it did not make.
        words = [_Word(0.0, 1.0), _Word(1.0, 2.0), _Word(10.0, 11.0),
                 _Word(11.0, 12.0), _Word(12.0, 13.0), _Word(13.0, 14.0)]
        c = _Cand(0.0, 14.0, hook="mostly pause", words=words)
        assert len(_retention(c)) == 1
        a = assemble([c], corpus=None)
        assert a.graph.removed == ()

    def test_deferred_candidates_are_recorded_as_not_selected(self):
        d = _Cand(300.0, 330.0, hook="deferred",
                  reason="lost to the budget")
        a = assemble([_Cand(0.0, 20.0, hook="x")], corpus=None,
                     deferred=[d])
        r = a.graph.removed[0]
        assert r.kind == "not_selected"
        assert r.reason == "lost to the budget"

    def test_a_removed_range_links_to_its_decision(self):
        words = [_Word(0.0, 1.0), _Word(1.0, 2.0), _Word(2.0, 3.0),
                 _Word(4.5, 5.5), _Word(5.5, 6.5), _Word(6.5, 7.5)]
        a = assemble([_Cand(0.0, 7.5, hook="gappy", words=words)],
                     corpus=None)
        decision_ids = {d.id for d in a.graph.decisions}
        assert all(r.decision_id in decision_ids for r in a.graph.removed)


# ─────────────────────────────────────────────────────── the timeline itself

class TestTimeline:
    def test_clips_tile_the_spine_without_gaps_or_overlaps(self):
        a = assemble([_Cand(0.0, 20.0, hook="a"),
                      _Cand(100.0, 120.0, hook="b")], corpus=None)
        assert a.timeline.validate() == []
        prev_out = 0.0
        for clip in a.timeline.clips:
            assert clip.timeline_in == pytest.approx(prev_out, abs=1e-6)
            assert clip.timeline_out > clip.timeline_in
            prev_out = clip.timeline_out

    def test_one_source_and_one_track(self):
        a = assemble([_Cand(0.0, 20.0, hook="a")], corpus=None)
        assert {c.track for c in a.timeline.clips} == {"V1"}
        assert {id(c.source) for c in a.timeline.clips} == {id(a.timeline.clips[0].source)}

    def test_duration_is_the_sum_of_kept_spans(self):
        a = assemble([_Cand(0.0, 20.0, hook="a")], corpus=None)
        expected = sum(c.timeline_out - c.timeline_in for c in a.timeline.clips)
        assert a.duration == pytest.approx(expected)

    def test_every_decision_anchors_on_a_real_clip(self):
        a = assemble([_Cand(0.0, 20.0, hook="a"),
                      _Cand(100.0, 120.0, hook="b")], corpus=None)
        assert a.graph.dangling() == ()
        assert a.report["dangling"] == 0

    def test_chapter_markers_land_on_thread_boundaries(self, two_threads):
        inc_a = _Cand(5.0, 35.0, hook="incident a", opening_score=0.1)
        aft_a = _Cand(205.0, 235.0, hook="aftermath a", opening_score=0.9)
        a = assemble([inc_a, aft_a], corpus=two_threads,
                     config=AssemblyConfig(order="arc"))
        # Neither thread is shared, so there are no chapters to mark.
        assert a.graph.markers == ()
        assert not any(p.grouped for p in a.placements)

    def test_chapter_markers_land_on_grouped_threads(self, two_threads):
        inc_a = _Cand(5.0, 35.0, hook="incident a", opening_score=0.1)
        inc_b = _Cand(45.0, 58.0, hook="incident b", opening_score=0.1)
        aft_a = _Cand(205.0, 235.0, hook="aftermath a", opening_score=0.9)
        aft_b = _Cand(245.0, 258.0, hook="aftermath b", opening_score=0.9)
        a = assemble([inc_a, inc_b, aft_a, aft_b], corpus=two_threads,
                     config=AssemblyConfig(order="arc"))
        labels = sorted(m.label for m in a.graph.markers)
        assert labels == ["thread: the aftermath", "thread: the incident"]
        # a marker sits exactly where its thread's first grouped clip begins
        anchors = {round(d.anchor_timeline_in, 6) for d in a.graph.decisions}
        assert {round(m.time, 6) for m in a.graph.markers} < anchors

    def test_skipped_candidates_are_named_not_silently_dropped(self):
        # A candidate with no text has no retention intervals. It is dropped
        # from the edit and named in the report; the other clip still ships.
        empty = _Cand(100.0, 110.0, hook="the empty one", text="   ")
        good = _Cand(0.0, 20.0, hook="good")
        a = assemble([good, empty], corpus=None)
        assert a.skipped == ("the empty one",)
        assert len(a.placements) == 1
        assert [p.candidate.hook for p in a.placements] == ["good"]

    def test_an_edit_with_nothing_renderable_raises(self):
        c = _Cand(0.0, 10.0, hook="", text="   ")
        with pytest.raises(RuntimeError, match="renderable intervals"):
            assemble([c], corpus=None)


# ───────────────────────────────────────────────────── identity and stability

class TestIdentity:
    def test_decision_ids_are_content_addressed(self):
        a1 = assemble([_Cand(0.0, 20.0, hook="x")], corpus=None, title="T")
        a2 = assemble([_Cand(0.0, 20.0, hook="x")], corpus=None, title="T")
        assert a1.graph.decisions[0].id == a2.graph.decisions[0].id

    def test_ids_do_not_move_when_the_order_does(self, two_threads):
        inc = _Cand(5.0, 55.0, hook="incident", opening_score=0.1)
        aft = _Cand(205.0, 255.0, hook="aftermath", opening_score=0.9)
        src = assemble([inc, aft], corpus=two_threads,
                       config=AssemblyConfig(order="source"))
        arc = assemble([inc, aft], corpus=two_threads,
                       config=AssemblyConfig(order="arc"))
        assert {d.id for d in src.graph.decisions} == {
            d.id for d in arc.graph.decisions}

    def test_provenance_names_the_stage_and_the_ordering(self):
        a = assemble([_Cand(0.0, 20.0, hook="x")], corpus=None,
                     config=AssemblyConfig(order="source"))
        p = a.graph.decisions[0].provenance
        assert "engine.assembly.assemble" in p
        assert p.endswith("order=source)")

    def test_as_dict_reports_every_part(self, two_threads):
        a = assemble([_Cand(5.0, 55.0, hook="incident")], corpus=two_threads)
        d = a.as_dict()
        assert d["dangling"] == 0
        assert len(d["placements"]) == 1
        assert d["placements"][0]["thread_title"] == "the incident"
        assert d["placements"][0]["grouped"] is False
        assert "clips" in a.report


class TestProgramOrderOf:
    """``program_order_of`` is the join between the canonical edit and the tools
    that number artifacts by position.

    ``selections.json`` is the policy's pick order and the caption, timeline and
    audit artifacts are numbered by position in the reel, so a tool that reads
    them positionally would pair the wrong clip's transcript with the wrong
    clip's captions whenever the assembly reordered anything. This is what makes
    the numbering derive from the edit instead.
    """

    def test_the_order_is_the_reel_order_not_the_pick_order(self, tmp_path):
        a = assemble([_Cand(60.0, 90.0, hook="second", opening_score=0.9),
                      _Cand(0.0, 30.0, hook="first", opening_score=0.1)],
                     corpus=None, title="T",
                     config=AssemblyConfig(order="source"))
        (tmp_path / "edit.json").write_text(
            json.dumps(a.as_dict()), encoding="utf-8")
        # source order, and the pick order above is reversed relative to it
        assert program_order_of(tmp_path) == [(0.0, 30.0), (60.0, 90.0)]

    def test_no_edit_is_none_and_not_empty(self, tmp_path):
        # None is the signal to fall back to the selection order; an empty list
        # would read as "the program holds nothing" and drop every clip's
        # captions instead of reordering them.
        assert program_order_of(tmp_path) is None

    def test_an_unreadable_edit_is_none(self, tmp_path):
        (tmp_path / "edit.json").write_text("{not json", encoding="utf-8")
        assert program_order_of(tmp_path) is None

    def test_an_edit_with_no_placements_is_none(self, tmp_path):
        (tmp_path / "edit.json").write_text(
            json.dumps({"placements": []}), encoding="utf-8")
        assert program_order_of(tmp_path) is None
