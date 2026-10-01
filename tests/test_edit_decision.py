"""Tests for the canonical edit decision graph.

These pin the three rules in ``engine/edit_decision.py``'s docstring:
unmeasured is not confident, no second vocabulary, and every decision is
reversible. They deliberately do *not* test timing — that is
``tests/test_timeline.py``'s job, and the two are checked together upstream.
"""

import pytest

from engine.edit_decision import (
    Confidence,
    EditDecision,
    EditDecisionGraph,
    EditNote,
    EditorialAction,
    EvidenceRef,
    Marker,
    RemovedRange,
    STORY_ROLES,
)
from engine.timeline import Clip, SourceMedia, Timeline


# --------------------------------------------------------------- fixtures

def _clip(track: str, tin: float, tout: float, source: SourceMedia) -> Clip:
    return Clip(source=source, source_in=0.0, source_out=tout - tin,
                timeline_in=tin, timeline_out=tout, track=track)


@pytest.fixture
def source() -> SourceMedia:
    return SourceMedia(path="src.mp4", fps=30.0, duration=100.0,
                       start_timecode=None)


@pytest.fixture
def timeline(source: SourceMedia) -> Timeline:
    return Timeline(clips=[
        _clip("V1", 0.0, 10.0, source),
        _clip("V1", 10.0, 20.0, source),
    ], title="T", fps=30.0)


# ------------------------------------------------------------- confidence

class TestConfidence:
    def test_unmeasured_is_not_a_number(self):
        c = Confidence.unmeasured_of()
        assert c.value is None
        assert c.unmeasured is True
        assert c.as_dict() == {"value": None, "basis": "unmeasured"}

    def test_measured_value_clamps_to_unit_interval(self):
        assert Confidence(1.5, basis="b").value == 1.0
        assert Confidence(-0.25, basis="b").value == 0.0

    def test_measured_needs_a_basis(self):
        with pytest.raises(ValueError, match="basis"):
            Confidence(0.8)
        with pytest.raises(ValueError, match="basis"):
            Confidence(0.8, basis="unmeasured")
        with pytest.raises(ValueError, match="basis"):
            Confidence(0.8, basis="")

    def test_nan_is_refused_not_read_as_maximum(self):
        # min(1.0, nan) == 1.0, which would dress a NaN up as full trust.
        with pytest.raises(ValueError, match="NaN"):
            Confidence(float("nan"), basis="b")

    def test_basis_carries_the_instrument(self):
        c = Confidence(0.62, basis="jumpcut.pause_density over 30s")
        assert c.unmeasured is False
        assert c.as_dict() == {"value": 0.62, "basis": "jumpcut.pause_density over 30s"}

    def test_round_trip_dict_keeps_four_places(self):
        c = Confidence(0.123456, basis="b")
        assert c.as_dict()["value"] == 0.1235


# -------------------------------------------------------------- vocabulary

class TestVocabulary:
    def test_story_roles_are_the_dramatic_beat_ladder(self):
        # ``DramaticBeat.kind`` is a plain str whose ladder exists only as a
        # comment, so this parses the comment. If the ladder moves, this fails
        # — which is the point: one taxonomy of story function, not two.
        import inspect

        from engine import threads
        src = inspect.getsource(threads.DramaticBeat)
        (line,) = (l for l in src.splitlines() if "kind:" in l)
        documented = tuple(w for w in
                           line.split("#", 1)[1].replace("|", " ").split()
                           if w.isalpha())
        assert documented == STORY_ROLES, (
            f"STORY_ROLES drifted from DramaticBeat's documented ladder: "
            f"{STORY_ROLES} vs {documented}"
        )

    def test_every_kind_the_builder_can_emit_is_a_role(self):
        # The beat builder assigns from a fixed set in _unit_beats; this pins
        # that set rather than trusting the docstring to stay in step.
        import inspect

        from engine import threads
        src = inspect.getsource(threads)
        emitted = set()
        for line in src.splitlines():
            if "kind = " in line:
                emitted.add(line.split('"')[1])
        assert emitted, "no beat kinds found — the builder changed shape"
        assert emitted <= set(STORY_ROLES), (
            f"the beat builder emits a role the EDG does not name: "
            f"{emitted - set(STORY_ROLES)}"
        )

    def test_setup_is_documented_but_never_assigned(self):
        # Recon finding: "setup" is in DramaticBeat's ladder and nowhere in the
        # builder. A decision with role "setup" is therefore not derivable from
        # a corpus today — it is the shape an editor's own beat taxonomy would
        # take, and the graph must accept it rather than reject it as unknown.
        d = EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0,
                         role="setup")
        assert d.role == "setup"

    def test_removal_kinds_are_jumpcuts_own(self):
        from engine.jumpcut import SegmentType
        ours = {"remove_silence", "remove_filler", "remove_hallucination"}
        theirs = {s.value for s in SegmentType
                  if s.value.startswith("remove_")}
        assert ours <= theirs, (
            "RemovedRange accepts a kind jumpcut does not name — that is a "
            "second vocabulary, and the recon calls it the defect to avoid"
        )

    def test_removal_kinds_add_only_assembly_level_ones(self):
        # The two extra kinds describe spans the assembly never placed, which
        # jumpcut has no concept of.
        assert RemovedRange(0.0, 1.0, "not_selected", "r").kind == "not_selected"
        assert RemovedRange(0.0, 1.0, "gate_rejected", "r").kind == "gate_rejected"


# ---------------------------------------------------------- removed ranges

class TestRemovedRange:
    def test_empty_range_is_refused(self):
        with pytest.raises(ValueError, match="non-empty"):
            RemovedRange(5.0, 5.0, "remove_silence", "r")
        with pytest.raises(ValueError, match="non-empty"):
            RemovedRange(5.0, 4.0, "remove_silence", "r")

    def test_unknown_kind_is_refused(self):
        with pytest.raises(ValueError, match="unknown removal kind"):
            RemovedRange(0.0, 1.0, "remove_boring", "r")

    def test_duration_is_reported_rounded(self):
        r = RemovedRange(1.0, 3.5, "remove_filler", "r")
        assert r.duration == 2.5
        assert r.as_dict()["duration"] == 2.5

    def test_decision_id_links_back_to_the_thing_that_cut(self):
        r = RemovedRange(0.0, 1.0, "remove_silence", "long pause", "d03")
        assert r.decision_id == "d03"
        assert r.as_dict()["decision_id"] == "d03"


# ------------------------------------------------------------- a decision

class TestEditDecision:
    def test_anchor_is_track_plus_sequence_position(self, source):
        d = EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=10.0)
        assert d.anchor == ("V1", 10.0)

    def test_id_is_required(self):
        with pytest.raises(ValueError, match="stable id"):
            EditDecision(id="", anchor_track="V1", anchor_timeline_in=0.0)

    def test_unknown_role_is_refused(self):
        with pytest.raises(ValueError, match="story role"):
            EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0,
                         role="macguffin")

    def test_empty_role_is_allowed(self):
        d = EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0)
        assert d.role == ""

    def test_action_defaults_to_keep(self):
        d = EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0)
        assert d.action is EditorialAction.KEEP

    def test_confidence_defaults_to_unmeasured(self):
        d = EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0)
        assert d.confidence.unmeasured is True

    def test_evidence_serialises_with_its_own_confidence(self):
        d = EditDecision(
            id="d1", anchor_track="V1", anchor_timeline_in=0.0,
            evidence=(EvidenceRef(source="jumpcut.pause_density",
                                  claim="pauses cover 31% of the span",
                                  id="e0007",
                                  confidence=Confidence(0.8, basis="word-aligned")),),
        )
        ev = d.as_dict()["evidence"][0]
        assert ev["id"] == "e0007"
        assert ev["confidence"] == {"value": 0.8, "basis": "word-aligned"}

    def test_provenance_names_where_the_decision_came_from(self):
        d = EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0,
                         provenance="engine.selection.select round 2")
        assert d.as_dict()["provenance"] == "engine.selection.select round 2"


# ------------------------------------------------- the graph's invariants

class TestEditDecisionGraph:
    def _two_decisions(self) -> tuple[EditDecision, ...]:
        return (
            EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0,
                         role="setup", reason="opens the thread"),
            EditDecision(id="d2", anchor_track="V1", anchor_timeline_in=10.0,
                         role="turn", reason="the reversal"),
        )

    def test_two_live_decisions_on_one_placement_are_refused(self):
        with pytest.raises(ValueError, match="2 live decisions"):
            EditDecisionGraph(decisions=(
                EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0),
                EditDecision(id="d2", anchor_track="V1", anchor_timeline_in=0.0),
            ))

    def test_a_superseded_decision_may_share_the_anchor(self):
        # This is the mechanism reversibility is built on: the override sits on
        # the same placement as what it replaced.
        g = EditDecisionGraph(decisions=(
            EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0),
            EditDecision(id="d2", anchor_track="V1", anchor_timeline_in=0.0,
                         replaces="d1"),
        ))
        assert len(g.decisions) == 2
        assert {d.id for d in g.live()} == {"d2"}

    def test_two_chains_on_one_anchor_are_refused(self):
        with pytest.raises(ValueError, match="2 live decisions"):
            EditDecisionGraph(decisions=(
                EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0),
                EditDecision(id="d2", anchor_track="V1", anchor_timeline_in=0.0,
                             replaces="d1"),
                EditDecision(id="d3", anchor_track="V1", anchor_timeline_in=0.0),
            ))

    def test_replacing_a_decision_on_another_anchor_is_a_move(self):
        # A clip moved to a new position supersedes the decision at the old
        # one. Forbidding this would make an edit unmovable, so it is allowed
        # and both anchors keep their decisions.
        g = EditDecisionGraph(decisions=(
            EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0),
            EditDecision(id="d2", anchor_track="V1", anchor_timeline_in=10.0,
                         action=EditorialAction.PROMOTE, replaces="d1"),
        ))
        assert len(g.decisions) == 2
        assert len(g.live()) == 1
        assert g.decision_by_anchor[("V1", 10.0)].id == "d2"

    def test_replacing_an_absent_decision_is_refused(self):
        with pytest.raises(ValueError, match="cannot be walked backwards"):
            EditDecisionGraph(decisions=(
                EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0,
                             replaces="gone"),
            ))

    def test_from_timeline_takes_the_clips_anchors(self, timeline):
        g = EditDecisionGraph.from_timeline(timeline)
        assert g.anchors == (("V1", 0.0), ("V1", 10.0))
        assert g.title == "T"
        assert g.fps == 30.0
        assert g.decisions == ()

    def test_anchors_round_to_microseconds_so_floats_compare(self, source):
        # A repeating decimal in a timeline position must still match a
        # decision anchored on it, so the anchor is rounded rather than copied.
        t = Timeline(clips=[_clip("V1", 10.0 / 3.0, 20.0 / 3.0, source)])
        g = EditDecisionGraph.from_timeline(t)
        assert g.anchors[0][1] == round(10.0 / 3.0, 6)
        d = EditDecision(id="d1", anchor_track="V1",
                         anchor_timeline_in=round(10.0 / 3.0, 6))
        g2 = EditDecisionGraph(decisions=(d,), anchors=g.anchors)
        assert g2.decision_by_anchor[("V1", round(10.0 / 3.0, 6))].id == "d1"
        assert g2.dangling() == ()

    def test_dangling_reports_decisions_without_a_clip(self, timeline):
        g = EditDecisionGraph.from_timeline(timeline)
        anchored, lost = g.decisions + self._two_decisions(), ()
        g = EditDecisionGraph(decisions=anchored + lost,
                              anchors=g.anchors)
        # nothing dangling yet
        assert g.dangling() == ()
        g = EditDecisionGraph(decisions=anchored + (
            EditDecision(id="dz", anchor_track="V1", anchor_timeline_in=99.0),
        ), anchors=g.anchors)
        assert [d.id for d in g.dangling()] == ["dz"]

    def test_no_anchors_means_everything_dangles_and_it_is_said(self):
        g = EditDecisionGraph(decisions=self._two_decisions())
        assert len(g.dangling()) == 2
        assert g.as_dict()["dangling"] == 2

    def test_decision_by_anchor_lookup(self, timeline):
        g = EditDecisionGraph(decisions=self._two_decisions(),
                              anchors=EditDecisionGraph
                              .from_timeline(timeline).anchors)
        assert g.decision_by_anchor[("V1", 10.0)].id == "d2"

    def test_removed_ranges_are_kept_separately_from_clips(self, timeline):
        g = EditDecisionGraph(
            decisions=self._two_decisions(),
            removed=(RemovedRange(1.0, 2.0, "remove_silence", "long pause"),),
            anchors=EditDecisionGraph.from_timeline(timeline).anchors,
        )
        assert [r["kind"] for r in g.as_dict()["removed"]] == ["remove_silence"]

    def test_markers_and_notes_serialize(self):
        g = EditDecisionGraph(
            markers=(Marker(time=10.0, label="the turn", kind="beat"),),
            notes=(EditNote(text="check this with the editor", at=10.0),),
        )
        d = g.as_dict()
        assert d["markers"] == [{"time": 10.0, "label": "the turn",
                                 "kind": "beat"}]
        assert d["notes"][0]["author"] == "selekyt"


# ------------------------------------------------------------- reversibility

class TestReversibility:
    def _chain(self) -> EditDecisionGraph:
        return EditDecisionGraph(decisions=(
            EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0,
                         role="setup", reason="policy placed it here"),
            EditDecision(id="d2", anchor_track="V1", anchor_timeline_in=0.0,
                         action=EditorialAction.PROMOTE,
                         role="tension", reason="editor moved it earlier",
                         replaces="d1"),
        ))

    def test_superseded_decisions_are_kept_not_overwritten(self):
        g = self._chain()
        assert len(g.decisions) == 2

    def test_history_walks_back_oldest_first(self):
        g = self._chain()
        assert [d.id for d in g.history("d2")] == ["d1", "d2"]

    def test_history_of_an_unsuperseded_decision_is_just_itself(self):
        g = self._chain()
        assert [d.id for d in g.history("d1")] == ["d1"]

    def test_history_of_an_unknown_id_is_empty(self):
        assert self._chain().history("nope") == ()

    def test_three_generations(self):
        g = EditDecisionGraph(decisions=(
            EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0),
            EditDecision(id="d2", anchor_track="V1", anchor_timeline_in=0.0,
                         replaces="d1"),
            EditDecision(id="d3", anchor_track="V1", anchor_timeline_in=0.0,
                         replaces="d2"),
        ))
        assert [d.id for d in g.history("d3")] == ["d1", "d2", "d3"]

    def test_a_cycle_cannot_be_constructed(self):
        with pytest.raises(ValueError, match="is a cycle"):
            EditDecisionGraph(decisions=(
                EditDecision(id="d1", anchor_track="V1",
                             anchor_timeline_in=0.0, replaces="d2"),
                EditDecision(id="d2", anchor_track="V1",
                             anchor_timeline_in=10.0, replaces="d1"),
            ))

    def test_a_decision_cannot_be_superseded_twice(self):
        with pytest.raises(ValueError, match="superseded twice"):
            EditDecisionGraph(decisions=(
                EditDecision(id="d1", anchor_track="V1", anchor_timeline_in=0.0),
                EditDecision(id="d2", anchor_track="V1", anchor_timeline_in=10.0,
                             replaces="d1"),
                EditDecision(id="d3", anchor_track="V1", anchor_timeline_in=20.0,
                             replaces="d1"),
            ))

    def test_override_chain_explains_what_the_editor_changed(self):
        g = self._chain()
        head = g.history("d2")
        assert head[0].action is EditorialAction.KEEP
        assert head[1].action is EditorialAction.PROMOTE
