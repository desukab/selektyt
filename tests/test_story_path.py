"""Tests for ``engine.story_path`` — where the story graph meets an edit.

``docs/corpus-model.md`` used to say "nothing consumes this layer yet", and the
gap was structural: the corpus holds a story graph, the pipeline's selection
layer holds ``Candidate``\\ s, and nothing between the two knew about each
other. ``story_path`` is the seam. These tests pin its editorial contract —
what ships at a budget, what gets refused, and what the path is obliged to
report — and they pin it on corpora the real analyser produced rather than on
hand-written graphs, because the point is that a graph nobody built is useless.

The fixtures are transcripts, not corpora: each test builds the corpus the way
the pipeline does and then asserts on the graph the analyser actually found.
The measured values they depend on were recorded from the analyser, not
assumed; the comments say which ones those are.
"""

from __future__ import annotations

import pytest

from engine.corpus import build_corpus
from engine.story_path import DroppedMoment, StoryPath, story_path
from models.core import Segment, Word


def _seg(a: float, b: float, text: str) -> Segment:
    """A timed segment like a transcript carries one, word for word."""
    words = text.split()
    span = (b - a) / max(1, len(words))
    return Segment(
        a, b, text,
        [Word(w, a + i * span, a + (i + 1) * span, 0.9)
         for i, w in enumerate(words)],
    )


# One story across three distant moments: a question, its answer, and the
# consequence of the answer. The analyser finds thread 0 = units [0, 2, 3]
# with a question_answer link (0 → 2) and a causal link (2 → 3), and reads the
# arc as 'tension → resolution'. Units 1 and 4 belong to other, single-unit
# stories — they are the interference that makes the three linked moments
# *distant* rather than adjacent.
def _story_segs() -> list[Segment]:
    return [
        _seg(0.0, 8.0, "Why did the satellite clock fail? The launch was nominal."),
        _seg(12.0, 20.0, "The harbour was rebuilt. The contractors finished early."),
        _seg(24.0, 32.0, "The satellite clock failed to synchronise. "
                         "The battery was under-spec."),
        _seg(36.0, 44.0, "The lighthouse needs paint. Its lamp is original."),
        _seg(48.0, 56.0, "So the battery was replaced. "
                         "The vendor paid the replacement cost."),
    ]


def _story_corpus() -> object:
    return build_corpus(_story_segs(), duration=65.0)


# The same story, with its opening moment replaced by one the editorial gate
# refuses: "Which is why the satellite clock failed." opens mid-conversation.
# Used to pin that a moment the gate turns away is dropped and reported, never
# shipped, and that a payoff left without its setup says *which* of the two
# causes that happened.
def _guard_segs() -> list[Segment]:
    segs = _story_segs()
    segs[0] = _seg(0.0, 8.0, "Which is why the satellite clock failed. "
                             "The launch was nominal.")
    return segs


# Two stories sharing one moment. The analyser finds thread 0 = [0, 4] (the
# satellite clock) and thread 2 = [2, 4] (the lighthouse), each with its own
# question_answer link into the shared payoff: unit 4 answers both questions.
# ``unit_threads[4] == [0, 2]`` is the whole point of the fixture.
def _shared_segs() -> list[Segment]:
    return [
        _seg(0.0, 8.0, "Why did the satellite clock fail? The launch was nominal."),
        _seg(12.0, 20.0, "The harbour was rebuilt. The contractors finished early."),
        _seg(24.0, 32.0, "Why does the lighthouse need painting? "
                         "The salt corrodes metal."),
        _seg(36.0, 44.0, "The estuary was dredged. The shipping channel widened."),
        _seg(48.0, 58.0, "The satellite clock failed to synchronise. "
                         "The lighthouse needed painting and the salt corroded "
                         "the metal."),
        _seg(62.0, 70.0, "The vendor paid the replacement cost."),
    ]


# ── the shape a path must have ──────────────────────────────────────────────


def test_a_path_is_a_sequence_of_candidates():
    """The design called for a list; ``StoryPath`` is one, plus its report."""
    path = story_path(_story_corpus(), _story_segs(),
                      thread_id=0, target_seconds=400.0)
    assert isinstance(path, StoryPath)
    assert len(path) == 3
    assert path[0] is path.candidates[0]
    assert list(path) == path.candidates
    assert all(c.start < c.end for c in path)
    # A shipped path carries the measured scores every other candidate carries;
    # it is not a second-class citizen of the pipeline.
    assert all(c.total_score > 0.0 for c in path)
    assert all(c.hook for c in path)


def test_moments_ship_in_source_order_not_role_order():
    """Selection ranks by narrative role, but the edit still plays in time."""
    path = story_path(_story_corpus(), _story_segs(),
                      thread_id=0, target_seconds=40.0)
    assert path.unit_indices == [0, 2, 3]
    assert [c.start for c in path] == sorted(c.start for c in path)
    assert path.roles == {0: "tension", 2: "tension", 3: "resolution"}


def test_thread_id_and_budget_are_carried_on_the_path():
    c = _story_corpus()
    path = story_path(c, _story_segs(), thread_id=0, target_seconds=37.5)
    assert path.thread_id == 0
    assert path.target_seconds == 37.5
    assert path.candidates  # a budget this tight still ships something


# ── nesting: the 60-second cut is inside the 3-minute cut ──────────────────


def test_a_larger_budget_takes_every_moment_a_smaller_one_took():
    """Greedy selection over a fixed key makes every smaller path a subset."""
    c = _story_corpus()
    segs = _story_segs()
    for tight in (1.0, 8.0, 20.0, 30.0):
        for loose in (40.0, 100.0, 400.0):
            small = story_path(c, segs, thread_id=0, target_seconds=tight)
            large = story_path(c, segs, thread_id=0, target_seconds=loose)
            assert set(small.unit_indices) <= set(large.unit_indices), \
                f"{tight}s path {small.unit_indices} is not a subset of " \
                f"{loose}s path {large.unit_indices}"


def test_the_graph_is_not_changed_by_reading_a_path():
    """Two presentations of one story are two views; the graph is one object."""
    c = _story_corpus()
    before = c.to_dict()
    story_path(c, _story_segs(), thread_id=0, target_seconds=20.0)
    story_path(c, _story_segs(), thread_id=0, target_seconds=400.0)
    assert c.to_dict() == before
    assert before["threads"][0]["unit_indices"] == [0, 2, 3]


def test_a_story_with_its_setup_cut_says_so():
    """The 20s path keeps the resolution and drops the setup that caused it.

    A resolution without its premise is the defect the gate catches for clips
    and the graph can see for stories — the link names the setup, so the note
    can say what the viewer is missing and which unit held it.
    """
    path = story_path(_story_corpus(), _story_segs(),
                      thread_id=0, target_seconds=20.0)
    assert path.unit_indices == [0, 3]
    assert any("was cut from the 20s budget" in n for n in path.notes)
    assert any("causal setup at unit 2" in n for n in path.notes)
    assert path.dropped == []  # nobody refused it; the budget did


def test_a_budget_that_cannot_hold_a_moment_ships_it_whole():
    """Moments are never trimmed to fit — a truncated moment is a different one."""
    path = story_path(_story_corpus(), _story_segs(),
                      thread_id=0, target_seconds=20.0)
    assert any("overruns its budget" in n for n in path.notes)
    # Nothing is clipped to satisfy the budget: the two shipped moments keep
    # the spans the graph found, which overrun 20s between them.
    total = sum(c.end - c.start for c in path)
    assert total > 20.0


def test_a_tight_budget_still_ships_the_storys_peak():
    """The peak is force-included: a story without its strongest beat is not it."""
    c = _story_corpus()
    assert c.beats[0].is_corpus_peak or c.beats[0].is_thread_peak
    path = story_path(c, _story_segs(), thread_id=0, target_seconds=1.0)
    assert 0 in path.unit_indices


def test_a_budget_report_says_how_many_moments_did_not_fit():
    path = story_path(_story_corpus(), _story_segs(),
                      thread_id=0, target_seconds=8.0)
    assert path.unit_indices == [0]
    assert any("2 of the story's 3 moment(s)" in n for n in path.notes)


# ── the gate: a moment that cannot stand alone is refused, never shipped ────


def test_a_moment_that_opens_mid_conversation_is_dropped():
    """Required test #15: the gate applies to story nodes as it does to clips."""
    c = build_corpus(_guard_segs(), duration=65.0)
    assert [u for u in c.threads[0].unit_indices] == [0, 2, 3]
    path = story_path(c, _guard_segs(), thread_id=0, target_seconds=400.0)
    assert path.unit_indices == [2, 3]
    assert len(path.dropped) == 1
    dropped = path.dropped[0]
    assert isinstance(dropped, DroppedMoment)
    assert dropped.unit_index == 0
    assert dropped.role == "tension"
    assert dropped.start == 0.0
    assert "satellite clock" in dropped.text
    assert "mid-conversation" in dropped.reason
    assert dropped.reason.startswith("starts")  # the guard's wording, verbatim
    assert 0 not in path.roles


def test_a_dropped_moment_reports_the_guards_own_words():
    """The note is never a sentence this module composed about the moment."""
    path = story_path(build_corpus(_guard_segs(), duration=65.0),
                      _guard_segs(), thread_id=0, target_seconds=400.0)
    assert path.dropped[0].reason in path.notes[0] or \
        any(path.dropped[0].reason in n for n in path.notes)


def test_a_payoff_whose_setup_was_refused_says_the_gate_refused_it():
    """Required test #16: the two causes of a missing setup are not conflated.

    "The budget cut it" is wrong when what happened is the gate refused it, and
    an editor who reads the first will fix the wrong problem.
    """
    path = story_path(build_corpus(_guard_segs(), duration=65.0),
                      _guard_segs(), thread_id=0, target_seconds=400.0)
    setup_notes = [n for n in path.notes
                   if "setup at unit 0" in n and "payoff at unit 2" in n]
    assert setup_notes, path.notes
    assert "refused by the editorial gate" in setup_notes[0]
    assert "was cut from" not in setup_notes[0]


def test_a_story_whose_moments_all_fail_the_gate_says_the_path_is_empty_by_judgement():
    c = build_corpus(_guard_segs(), duration=65.0)
    path = story_path(c, _guard_segs(), thread_id=0, target_seconds=8.0)
    assert path.unit_indices == []
    assert len(path.dropped) == 1
    assert any("empty by judgement" in n for n in path.notes)
    assert all(u.index != 0 for u in path.candidates)


def test_the_gate_is_the_same_one_independent_clips_answer_to():
    """A story path cannot smuggle a moment through that ``rank`` would drop."""
    from engine.editorial import rank

    refused = rank(_guard_segs(), limit=8)
    assert all(c.start > 1.0 for c in refused), \
        "the independent path must also reject the mid-conversation opening"
    path = story_path(build_corpus(_guard_segs(), duration=65.0),
                      _guard_segs(), thread_id=0, target_seconds=400.0)
    assert all(c.start > 1.0 for c in path), \
        "the story path must not ship what the independent path refused"


# ── degradation: no word timing, but the story is still the story ───────────


def test_no_transcript_still_yields_a_path_and_says_the_timing_is_unmeasured():
    """An imported memory read back with no transcript degrades, not vanishes."""
    c = _story_corpus()
    path = story_path(c, [], thread_id=0, target_seconds=40.0)
    assert path.unit_indices == [0, 2, 3]
    assert all(not cand.words for cand in path.candidates)
    assert any("no source transcript" in n for n in path.notes)
    assert any("unmeasured" in n for n in path.notes)
    # The spans survive: they are what the story actually is.
    assert [round(cand.start, 1) for cand in path.candidates] == [0.0, 24.0, 36.0]


def test_unresolved_segment_references_are_reported_not_faked():
    """A transcript that disagrees with the corpus yields a note, not silence."""
    from models.core import Segment as Seg

    c = _story_corpus()
    # A transcript short enough that some corpus segment references miss it.
    path = story_path(c, [Seg(0.0, 8.0, "one passage", [])],
                      thread_id=0, target_seconds=40.0)
    assert any("did not resolve against the transcript" in n for n in path.notes)
    assert path.unit_indices  # the path is still produced


# ── one moment in several stories ───────────────────────────────────────────


def test_a_moment_can_belong_to_two_stories():
    """Required test #5: membership is a list, never forced to exactly one."""
    c = build_corpus(_shared_segs(), duration=75.0)
    assert c.unit_threads[4] == [0, 2]
    assert c.threads[0].unit_indices == [0, 4]
    assert c.threads[2].unit_indices == [2, 4]


def test_a_shared_moment_is_reached_by_two_different_setups():
    """Each story brings its own typed edge into the shared payoff."""
    c = build_corpus(_shared_segs(), duration=75.0)
    kinds = {lk.kind for t in (c.threads[0], c.threads[2]) for lk in t.links}
    assert kinds == {"question_answer"}
    assert c.threads[0].links[0].setup_unit == 0
    assert c.threads[2].links[0].setup_unit == 2
    # Both edges pay off at the same unit — that is the sharing.
    assert c.threads[0].links[0].payoff_unit == 4
    assert c.threads[2].links[0].payoff_unit == 4


def test_a_shared_moment_is_read_in_each_story_that_holds_it():
    """The role is read from *this* story's beats, not the beat's own thread.

    Beat 4's own ``thread_id`` is 2, not 0 — the shared payoff closes thread 2
    and thread 0 claims it too. Reading the role from the beat would make unit
    4 a stranger in thread 0; reading it from ``thread.beat_indices`` keeps it a
    participant, which is what the shared-membership requirement needs.
    """
    c = build_corpus(_shared_segs(), duration=75.0)
    assert c.beats[4].thread_id == 2
    assert c.units[4].thread_id == 0
    for tid in (0, 2):
        path = story_path(c, _shared_segs(), thread_id=tid,
                          target_seconds=300.0)
        assert 4 in path.unit_indices, f"unit 4 must ship in thread {tid}"
        assert path.roles[4] == "tension"
        assert 4 in path.verdicts


def test_each_story_sees_only_its_own_moments():
    c = build_corpus(_shared_segs(), duration=75.0)
    p0 = story_path(c, _shared_segs(), thread_id=0, target_seconds=300.0)
    p2 = story_path(c, _shared_segs(), thread_id=2, target_seconds=300.0)
    assert p0.unit_indices == [0, 4]
    assert p2.unit_indices == [2, 4]
    assert set(p0.unit_indices) & set(p2.unit_indices) == {4}
    # The shared unit keeps the same span whichever story ships it.
    s0 = next(cand for cand in p0 if round(cand.start, 1) == 48.0)
    s2 = next(cand for cand in p2 if round(cand.start, 1) == 48.0)
    assert s0.start == s2.start and s0.end == s2.end


# ── caller errors are not editorial outcomes ────────────────────────────────


def test_an_unknown_thread_is_a_caller_error():
    c = _story_corpus()
    n = len(c.threads)
    with pytest.raises(ValueError, match="not one of"):
        story_path(c, _story_segs(), thread_id=n, target_seconds=60.0)
    with pytest.raises(ValueError, match="not one of"):
        story_path(c, _story_segs(), thread_id=-1, target_seconds=60.0)


@pytest.mark.parametrize("budget", [0.0, -5.0, float("nan"), float("inf")])
def test_an_unusable_budget_is_a_caller_error(budget: float):
    c = _story_corpus()
    with pytest.raises(ValueError, match="target_seconds"):
        story_path(c, _story_segs(), thread_id=0, target_seconds=budget)


def test_a_story_of_one_moment_is_still_a_story():
    """Required test #14's shape: a story with no links does not collapse.

    Thread 1 in the fixture holds one unit and no edges — a passing remark the
    analyser could not relate to anything. It must still be walkable, and its
    lone moment must still be gated and shipped like any other.
    """
    c = _story_corpus()
    assert c.threads[1].unit_indices == [1]
    assert not c.threads[1].links
    path = story_path(c, _story_segs(), thread_id=1, target_seconds=60.0)
    assert path.unit_indices == [1]
    assert path.roles == {1: "continuation"}
    assert path.dropped == []


def test_the_storys_arc_is_available_to_the_caller():
    """The presentation carries the story's own shape, not just its clips."""
    c = _story_corpus()
    assert "→" in c.threads[0].arc
    path = story_path(c, _story_segs(), thread_id=0, target_seconds=40.0)
    assert path.roles[0] == "tension"
    assert path.roles[3] == "resolution"


def test_paths_do_not_overlap_moments_the_story_does_not_hold():
    """No path ships a unit outside its own thread."""
    c = _story_corpus()
    thread_units = set(c.threads[0].unit_indices)
    for budget in (8.0, 20.0, 40.0, 400.0):
        path = story_path(c, _story_segs(), thread_id=0,
                          target_seconds=budget)
        assert set(path.unit_indices) <= thread_units


def test_a_path_at_two_budgets_is_consistent_within_a_unit():
    """The same unit keeps the same span at every budget that admits it."""
    c = _story_corpus()
    seen: dict[int, tuple[float, float]] = {}
    for budget in (8.0, 20.0, 30.0, 40.0, 400.0):
        path = story_path(c, _story_segs(), thread_id=0,
                          target_seconds=budget)
        for idx, cand in zip(path.unit_indices, path.candidates):
            span = (round(cand.start, 3), round(cand.end, 3))
            if idx in seen:
                assert seen[idx] == span, f"unit {idx} moved with the budget"
            seen[idx] = span


def test_the_role_rank_prefers_the_two_ends_of_a_story():
    """Under a tight budget the setup and the resolution go before the middle."""
    from engine.story_path import _ROLE_RANK

    assert _ROLE_RANK["setup"] < _ROLE_RANK["tension"]
    assert _ROLE_RANK["resolution"] < _ROLE_RANK["tension"]
    # A tight budget keeps the two ends; a loose one keeps the development too.
    c = _story_corpus()
    tight = story_path(c, _story_segs(), thread_id=0, target_seconds=20.0)
    loose = story_path(c, _story_segs(), thread_id=0, target_seconds=40.0)
    assert 3 in tight.unit_indices and 3 in loose.unit_indices
    assert 2 not in tight.unit_indices and 2 in loose.unit_indices


# A story that recalls its own opening. The same sentence is spoken twice, far
# apart, and the analyser's redundancy layer groups the two units while its link
# layer joins them with a callback — so thread 0 holds *both* members of
# redundancy group 0. The shape is fiddly by necessity: a unit is sentences, not
# segments, so the repeated sentence has to open a segment that follows an
# unrelated one, or the splitter merges it into a neighbour and the group never
# forms. Measured: units [0..4], group 0 = members [0, 3] restatement with
# representative 0, link (0 → 3, callback, 0.45), thread 0 = [0, 3] at
# confidence 0.68, and every unit clears the coherence gate at 0.50.
_RESTATED = (
    "The satellite antenna failed on launch, and the agency wrote a report."
)


def _restatement_segs() -> list[Segment]:
    return [
        _seg(0.0, 14.0, "The studio microphone arrived this week, and the "
                        "engineer tested it all afternoon."),
        _seg(30.0, 44.0, _RESTATED + " The funding review approved the "
                                     "replacement budget."),
        _seg(60.0, 74.0, "A weather forecast note was published later that "
                        "afternoon."),
        _seg(90.0, 104.0, "The office coffee machine broke again, and nobody "
                         "fixed it."),
        _seg(120.0, 134.0, "The intern organised the archive shelves on a "
                          "quiet Friday."),
        _seg(150.0, 164.0, _RESTATED + " The review board met in the morning."),
        _seg(180.0, 194.0, "A delivery van parked outside the building all "
                          "morning."),
        _seg(210.0, 224.0, "The auditor asked for the telemetry logs by the "
                          "weekend."),
    ]


def test_the_corpus_that_feeds_this_story_is_not_hand_written():
    """The restatement fixture is only meaningful if the analyser found it."""
    c = build_corpus(_restatement_segs(), duration=240.0)
    assert len(c.redundancy_groups) == 1
    group = c.redundancy_groups[0]
    assert group.members == [0, 3]
    assert group.relation == "restatement"
    assert (0, 3, "callback") in [
        (l.setup_unit, l.payoff_unit, l.kind) for l in c.links]
    assert c.threads[0].unit_indices == [0, 3]


def test_a_path_that_repeats_a_passage_says_it_repeats():
    """Required test #14: redundant moments are reported, not silently shipped.

    The link that put both units in one thread is a callback, and recalling an
    earlier point is a real narrative move, so the path ships both and names the
    collision instead of quietly overriding the corpus's redundancy judgement.
    """
    c = build_corpus(_restatement_segs(), duration=240.0)
    path = story_path(c, _restatement_segs(), thread_id=0, target_seconds=400.0)
    assert path.unit_indices == [0, 3]
    restatements = [n for n in path.notes if "repeats a passage" in n]
    assert len(restatements) == 1, path.notes
    only = restatements[0]
    assert "units 0, 3" in only
    assert "redundancy group 0" in only
    assert "restatement" in only
    assert "keeping unit 0 as the representative passage" in only


def test_a_path_that_ships_one_member_of_a_group_says_nothing():
    """The note is about an actual collision, not about the group existing."""
    c = build_corpus(_restatement_segs(), duration=240.0)
    path = story_path(c, _restatement_segs(), thread_id=0, target_seconds=20.0)
    assert path.unit_indices == [0]
    assert not any("repeats a passage" in n for n in path.notes)


def test_every_shipped_moment_carries_a_verdict():
    """``verdicts`` covers the kept as well as the dropped — nothing is silent."""
    c = _story_corpus()
    path = story_path(c, _story_segs(), thread_id=0, target_seconds=400.0)
    assert set(path.verdicts) == {0, 2, 3}
    assert all(path.verdicts.values())
    guard = story_path(build_corpus(_guard_segs(), duration=65.0),
                       _guard_segs(), thread_id=0, target_seconds=400.0)
    assert set(guard.verdicts) == {0, 2, 3}
    assert "mid-conversation" in guard.verdicts[0]
