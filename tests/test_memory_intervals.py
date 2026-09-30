"""Failure-first tests for ``engine.memory.intervals``.

Every case here is a way this module can be wrong rather than a way it can be
right: poison values that corrupt a sort, inversions that arrive from a real
decoder, float noise at the 1e-14 level that has already moved a real cut point
in this codebase (``engine.edit_plan._word_aligned``), and the thirteen Allen
relations where a mis-dispatch is silent because every branch returns a string.

The normalisation invariant is tested by construction, not by cooperation: the
inputs are deliberately the shape a caller would hand over wrong, and the
assertions are on the invariants (sorted, disjoint, merged) rather than on the
specific list the implementation happens to build.
"""
import math

import pytest

from engine.memory.intervals import (
    ALLEN_RELATIONS,
    MERGE_EPS,
    Interval,
    IntervalSet,
    allen,
    converse,
)


# ── Interval construction: poison and inversion ─────────────────────────────

def test_interval_nan_is_clamped_not_propagated():
    """NaN comparisons are all False, so a NaN reaching a sort silently
    reorders results. It must be clamped at the door."""
    iv = Interval(float("nan"), 5.0)
    assert iv.start == 0.0
    assert iv.end == 5.0


def test_interval_inf_is_clamped():
    iv = Interval(1.0, float("inf"))
    assert math.isfinite(iv.start)
    assert math.isfinite(iv.end)


def test_interval_negative_inf_start():
    iv = Interval(float("-inf"), 2.0)
    assert iv.start == 0.0
    assert iv.end == 2.0


def test_interval_inverted_is_repaired():
    """A decoder handing back end < start is a real failure mode and must not
    abort an hour-scale run; the span is the same, the endpoints were swapped."""
    iv = Interval(5.0, 1.0)
    assert iv.start == 1.0
    assert iv.end == 5.0


def test_interval_both_nan_yields_instant_at_origin():
    iv = Interval(float("nan"), float("nan"))
    assert iv.start == 0.0
    assert iv.end == 0.0
    assert iv.is_instant


def test_interval_is_hashable_and_orderable():
    """Frozen + ordered so spans work as dict keys; the grounding layer depends
    on it."""
    a, b = Interval(1.0, 2.0), Interval(3.0, 4.0)
    assert a < b
    assert {a: "x", b: "y"}[a] == "x"


def test_instant_is_below_merge_eps():
    """An epsilon-width span is an instant, not a duration."""
    assert Interval(1.0, 1.0).is_instant
    assert Interval(1.0, 1.0 + MERGE_EPS / 2.0).is_instant
    assert not Interval(1.0, 1.0 + MERGE_EPS * 10.0).is_instant


# ── Interval containment and overlap ────────────────────────────────────────

def test_contains_is_closed():
    iv = Interval(1.0, 3.0)
    assert iv.contains(1.0)
    assert iv.contains(3.0)
    assert iv.contains(2.0)
    assert not iv.contains(0.999)
    assert not iv.contains(3.001)


def test_contains_tolerates_documented_float_noise():
    """The 2.8e-14 straddle from engine.edit_plan must not push a bound
    outside its own interval."""
    iv = Interval(364.84, 370.0)
    assert iv.contains(364.84000000000003)


def test_overlaps_shared_boundary_is_true():
    """Closed intervals: touching is overlapping, because "speech ends exactly
    when the face appears" is one event."""
    assert Interval(1.0, 2.0).overlaps(Interval(2.0, 3.0))


def test_overlaps_disjoint_is_false():
    assert not Interval(1.0, 2.0).overlaps(Interval(2.0 + MERGE_EPS * 2, 3.0))


def test_intersect_of_touching_is_instant():
    hit = Interval(1.0, 2.0).intersect(Interval(2.0, 3.0))
    assert hit is not None
    assert hit.is_instant


def test_intersect_disjoint_is_none():
    assert Interval(1.0, 2.0).intersect(Interval(5.0, 6.0)) is None


def test_union_of_distant_contains_the_gap():
    """Documented behaviour, not a bug: the caller asked for one span covering
    both, and that span includes the silence between them."""
    u = Interval(1.0, 2.0).union(Interval(5.0, 6.0))
    assert (u.start, u.end) == (1.0, 6.0)


# ── Interval expand / shift ─────────────────────────────────────────────────

def test_expand_grows_about_midpoint():
    assert Interval(1.0, 3.0).expand(1.0) == Interval(0.0, 4.0)


def test_expand_negative_to_instant_does_not_invert():
    """Shrinking past zero width yields the midpoint, never an inverted span."""
    out = Interval(1.0, 3.0).expand(-100.0)
    assert out.is_instant
    assert out.start == pytest.approx(2.0)


def test_expand_clamps_to_bounds():
    out = Interval(1.0, 3.0).expand(10.0, bounds=Interval(0.0, 4.0))
    assert (out.start, out.end) == (0.0, 4.0)


def test_expand_wholly_outside_bounds_is_none():
    """The defect this pins: clamping the two endpoints independently
    manufactured support. ``Interval(200, 210)`` against bounds ``(0, 100)``
    clamped to ``(100, 200)`` and came back as a hundred seconds of coverage the
    source never had. A span lying entirely outside the world has no expansion
    inside it, and ``None`` is how that is reported."""
    assert Interval(200.0, 210.0).expand(0.0, bounds=Interval(0.0, 100.0)) is None
    assert Interval(200.0, 210.0).expand(50.0, bounds=Interval(0.0, 100.0)) is None


def test_expand_partly_outside_bounds_intersects():
    assert Interval(95.0, 105.0).expand(0.0, bounds=Interval(0.0, 100.0)) == Interval(95.0, 100.0)


def test_expand_zero_is_identity():
    assert Interval(1.0, 3.0).expand(0.0) == Interval(1.0, 3.0)


def test_shift_translates():
    assert Interval(1.0, 3.0).shift(2.5) == Interval(3.5, 5.5)


# ── Allen relations: all thirteen, plus the boundary traps ──────────────────

def test_allen_equals():
    assert Interval(1.0, 3.0).relation(Interval(1.0, 3.0)) == "equals"


def test_allen_before_after():
    assert Interval(1.0, 2.0).relation(Interval(3.0, 4.0)) == "before"
    assert Interval(3.0, 4.0).relation(Interval(1.0, 2.0)) == "after"


def test_allen_meets_met_by():
    """A shared boundary is meets/met_by, not a degenerate overlap."""
    assert Interval(1.0, 2.0).relation(Interval(2.0, 3.0)) == "meets"
    assert Interval(2.0, 3.0).relation(Interval(1.0, 2.0)) == "met_by"


def test_allen_overlaps_overlapped_by():
    assert Interval(1.0, 3.0).relation(Interval(2.0, 4.0)) == "overlaps"
    assert Interval(2.0, 4.0).relation(Interval(1.0, 3.0)) == "overlapped_by"


def test_allen_starts_started_by():
    assert Interval(1.0, 2.0).relation(Interval(1.0, 3.0)) == "starts"
    assert Interval(1.0, 3.0).relation(Interval(1.0, 2.0)) == "started_by"


def test_allen_finishes_finished_by():
    assert Interval(2.0, 3.0).relation(Interval(1.0, 3.0)) == "finishes"
    assert Interval(1.0, 3.0).relation(Interval(2.0, 3.0)) == "finished_by"


def test_allen_during_contains():
    assert Interval(2.0, 3.0).relation(Interval(1.0, 4.0)) == "during"
    assert Interval(1.0, 4.0).relation(Interval(2.0, 3.0)) == "contains"


def test_allen_covers_exactly_thirteen_relations():
    """The dispatch must be total and unique: a mis-routed pair would show up
    as a missing or duplicated name before it shows up as a wrong answer."""
    big = Interval(0.0, 100.0)
    probe = Interval(20.0, 40.0)
    seen = set()
    # Sample pairs chosen to walk every branch.
    pairs = [
        (Interval(0, 10), Interval(20, 40)),      # before
        (Interval(0, 20), Interval(20, 40)),      # meets
        (Interval(0, 30), Interval(20, 40)),      # overlaps
        (Interval(20, 30), Interval(20, 40)),     # starts
        (Interval(30, 35), Interval(20, 40)),     # during
        (Interval(30, 40), Interval(20, 40)),     # finishes
        (Interval(20, 40), Interval(20, 40)),     # equals
    ]
    for a, b in pairs:
        seen.add(a.relation(b))
        seen.add(b.relation(a))
    for inner in (probe, Interval(20, 30), Interval(10, 40)):
        seen.add(inner.relation(big))
        seen.add(big.relation(inner))
    assert seen == set(ALLEN_RELATIONS), seen


def test_allen_is_symmetric_through_converse():
    """If relation() and converse() disagree, the evidence graph would carry
    two spellings of one edge."""
    a, b = Interval(1.0, 3.0), Interval(2.0, 4.0)
    assert a.relation(b) == converse(b.relation(a))


def test_allen_agrees_with_merges_across_layers():
    """The defect this pins: ``relation`` used exact ``==`` while ``_normalise``
    merges members separated by no more than ``MERGE_EPS``, so three layers of
    the system gave three different answers about whether a cut point existed.
    ``Interval(1, 2)`` against ``Interval(2 + 1e-9, 3)`` reported ``before``,
    ``overlaps`` said they shared a point, and ``IntervalSet`` merged them into
    one span. All three must now agree: the boundary is a touch."""
    a = Interval(1.0, 2.0)
    b = Interval(2.0 + 1e-9, 3.0)
    assert a.relation(b) == "meets"
    assert a.overlaps(b)
    assert IntervalSet.of((1.0, 2.0), (2.0 + 1e-9, 3.0)).cardinality == 1


def test_allen_near_equals_is_equals():
    """Two intervals differing only within ``MERGE_EPS`` are the same span; a
    ``before`` here would make a chain through an evidence graph loop forever
    chasing a gap that does not exist."""
    assert Interval(1.0, 3.0).relation(Interval(1.0 + 1e-9, 3.0 - 1e-9)) == "equals"


@pytest.mark.parametrize("name", ALLEN_RELATIONS)
def test_converse_round_trip_returns_original(name):
    assert converse(converse(name)) == name


def test_converse_unknown_raises():
    with pytest.raises(ValueError):
        converse("preceded_by")


def test_allen_alias_matches_method():
    a, b = Interval(1.0, 3.0), Interval(2.0, 4.0)
    assert allen(a, b) == a.relation(b)


def test_allen_instants_degrade_to_point_comparisons():
    """A zero-width span is legal and common; its relations must still be
    correct, not an untested branch."""
    p = Interval(2.0, 2.0)
    assert p.relation(Interval(1.0, 3.0)) == "during"
    assert Interval(1.0, 3.0).relation(p) == "contains"
    assert p.relation(Interval(0.0, 1.0)) == "after"
    assert p.relation(Interval(3.0, 4.0)) == "before"
    assert p.relation(p) == "equals"


# ── IntervalSet normalisation: the invariant, enforced not trusted ──────────

def test_set_sorts_unsorted_input():
    s = IntervalSet.of((5.0, 6.0), (1.0, 2.0), (3.0, 4.0))
    assert [i.start for i in s] == [1.0, 3.0, 5.0]


def test_set_merges_overlapping():
    s = IntervalSet.of((1.0, 3.0), (2.0, 5.0))
    assert s.cardinality == 1
    assert s.bounds == Interval(1.0, 5.0)


def test_set_merges_nested():
    s = IntervalSet.of((1.0, 10.0), (2.0, 3.0))
    assert s.cardinality == 1


def test_set_merges_near_touch_below_merge_eps():
    """The float-noise case. Two spans separated by 1e-14 are one span; at
    hour scale a pairwise comparison that missed this would be a wait."""
    s = IntervalSet.of((1.0, 2.0), (2.0 + 1e-14, 3.0))
    assert s.cardinality == 1
    assert s.bounds == Interval(1.0, 3.0)


def test_set_keeps_gap_above_merge_eps():
    s = IntervalSet.of((1.0, 2.0), (2.0 + MERGE_EPS * 2, 3.0))
    assert s.cardinality == 2


def test_set_merges_duplicates():
    s = IntervalSet.of((1.0, 2.0), (1.0, 2.0))
    assert s.cardinality == 1


def test_set_empty_is_valid_data():
    """An empty answer means "nowhere in this source". It is never None and
    never an exception — see the module docstring."""
    s = IntervalSet.empty()
    assert s.is_empty
    assert s.cardinality == 0
    assert s.duration == 0.0
    assert s.bounds is None
    assert s.at(5.0) is None
    assert s.nearest(5.0) is None
    assert not s.covers(Interval(0.0, 1.0))
    assert list(s) == []


def test_set_accepts_interval_objects_and_pairs():
    s = IntervalSet.of(Interval(1.0, 2.0), (3.0, 4.0))
    assert s.cardinality == 2


def test_set_absorbs_poison_without_raising():
    """A NaN from a detector must not abort the run; it becomes a clamped
    member and the set stays normalised."""
    s = IntervalSet.of((1.0, 2.0), (float("nan"), 4.0), (0.5, 0.8))
    assert s.cardinality >= 1
    for iv in s:
        assert math.isfinite(iv.start)
        assert math.isfinite(iv.end)


def test_set_from_pairs():
    s = IntervalSet.from_pairs([(3.0, 4.0), (1.0, 2.0)])
    assert [i.start for i in s] == [1.0, 3.0]


def test_set_spanning_single_span():
    s = IntervalSet.spanning(0.0, 3600.0)
    assert s.cardinality == 1
    assert s.duration == pytest.approx(3600.0)


def test_set_duration_excludes_gaps():
    s = IntervalSet.of((0.0, 10.0), (20.0, 30.0))
    assert s.duration == pytest.approx(20.0)
    assert s.bounds == Interval(0.0, 30.0)


def test_set_bounds_none_when_empty():
    """Reported as None, not fabricated as a zero span — a zero span would read
    as "evidence at the start of the video"."""
    assert IntervalSet.empty().bounds is None


# ── IntervalSet query: at / nearest / covers ────────────────────────────────

def test_at_finds_containing_span():
    s = IntervalSet.of((1.0, 2.0), (5.0, 8.0))
    assert s.at(1.5) == Interval(1.0, 2.0)
    assert s.at(7.0) == Interval(5.0, 8.0)


def test_at_finds_a_members_own_left_edge():
    """Regression: a query instant ordered by the full (start, end) tuple sorts
    before a member that starts at the same time, so the bisect skipped it and
    at(5.0) reported no evidence at the start of the [5.0, 8.0] span it was
    standing on. A boundary is where an editor clicks."""
    s = IntervalSet.of((1.0, 2.0), (5.0, 8.0))
    assert s.at(5.0) == Interval(5.0, 8.0)
    assert s.contains_time(5.0)
    assert s.at(1.0) == Interval(1.0, 2.0)


def test_at_finds_right_edge():
    s = IntervalSet.of((1.0, 2.0), (5.0, 8.0))
    assert s.at(2.0) == Interval(1.0, 2.0)
    assert s.at(8.0) == Interval(5.0, 8.0)


def test_at_finds_instant_member():
    """A zero-width member is legal; a bisect that needs a width to land on it
    would lose every beat and cut point in the source."""
    s = IntervalSet.of((1.0, 2.0), Interval(4.0, 4.0), (5.0, 8.0))
    assert s.at(4.0) == Interval(4.0, 4.0)
    assert s.contains_time(4.0)


def test_at_single_member_at_origin():
    s = IntervalSet.of((0.0, 3.0))
    assert s.at(0.0) == Interval(0.0, 3.0)


def test_at_returns_none_in_gap():
    s = IntervalSet.of((1.0, 2.0), (5.0, 8.0))
    assert s.at(3.0) is None


def test_at_returns_none_before_first():
    s = IntervalSet.of((5.0, 8.0))
    assert s.at(1.0) is None


def test_at_returns_none_on_empty():
    assert IntervalSet.empty().at(1.0) is None


def test_at_is_log_not_scan():
    """at() must stay sublinear; a regression to a scan is a performance bug
    at hour scale with per-word intervals. Guarded by structure: the bisect
    result is asserted through behaviour on a large set."""
    s = IntervalSet.from_pairs([(float(i), float(i) + 0.5) for i in range(0, 200000, 2)])
    assert s.at(100000.0) is not None
    assert s.cardinality == 100000


def test_nearest_snaps_forward_into_gap():
    """The grounding operation: a reasoner's belief is off, nearest finds the
    real span."""
    s = IntervalSet.of((1.0, 2.0), (5.0, 8.0))
    assert s.nearest(3.0) == Interval(1.0, 2.0)
    assert s.nearest(4.0) == Interval(5.0, 8.0)


def test_nearest_before_first_returns_first():
    s = IntervalSet.of((5.0, 8.0))
    assert s.nearest(1.0) == Interval(5.0, 8.0)


def test_nearest_after_last_returns_last():
    s = IntervalSet.of((1.0, 2.0))
    assert s.nearest(9.0) == Interval(1.0, 2.0)


def test_nearest_returns_none_on_empty():
    assert IntervalSet.empty().nearest(1.0) is None


def test_contains_time_uses_at():
    s = IntervalSet.of((1.0, 2.0))
    assert s.contains_time(1.5)
    assert not s.contains_time(3.0)


def test_overlaps_and_intervals_in_window():
    s = IntervalSet.of((1.0, 2.0), (3.0, 4.0), (5.0, 8.0))
    assert s.overlaps(Interval(3.5, 3.6))
    assert not s.overlaps(Interval(2.1, 2.9))
    got = s.intervals_in(Interval(2.0, 5.0))
    assert Interval(3.0, 4.0) in got
    # A member straddling the window edge is included, not dropped.
    assert Interval(5.0, 8.0) in got


def test_overlaps_accepts_a_set_and_finds_a_hit_in_a_later_member():
    """The evidence joins are set against set — a b-roll marker spans two regions
    and a scene item sits inside the second one. A pairwise comparison against
    only the first member would miss it, and a caller forced to unwrap the set
    would decide which member to test and guess wrong."""
    markers = IntervalSet.of((10.0, 20.0), (600.0, 615.0))
    scene = IntervalSet.of((605.0, 610.0))
    assert markers.overlaps(scene)
    assert scene.overlaps(markers)


def test_overlaps_of_two_sets_can_be_disjoint_in_every_member():
    """Symmetry cuts both ways: three markers against three scenes, none of them
    touching, must read as no relation — a join layer that fell through to
    "overlaps something" would link every item in the source."""
    a = IntervalSet.of((0.0, 1.0), (10.0, 11.0), (20.0, 21.0))
    b = IntervalSet.of((5.0, 6.0), (15.0, 16.0), (25.0, 26.0))
    assert not a.overlaps(b)
    assert not b.overlaps(a)


def test_overlaps_of_two_sets_handles_an_empty_operand():
    """An empty evidence set is the common case — a source with no b-roll cues,
    or a channel that produced nothing. It must not become a wild card that
    overlaps everything."""
    full = IntervalSet.of((0.0, 10.0))
    empty = IntervalSet.empty()
    assert not full.overlaps(empty)
    assert not empty.overlaps(full)
    assert not empty.overlaps(empty)


def test_overlaps_of_two_sets_detects_a_shared_boundary():
    """A cut point is zero-width and a face sample is an instant; the join that
    matters is often the two touching at exactly one time. Closed intervals, so
    a shared point is an overlap."""
    a = IntervalSet.of((0.0, 10.0))
    b = IntervalSet.of((10.0, 20.0))
    assert a.overlaps(b)


def test_overlaps_of_two_sets_is_linear_not_pairwise():
    """Two members each, but a naive nested scan over hour-scale sets is the
    difference between a millisecond and a wait. The observable contract is only
    the answer, so this is a guard on the complexity the merge pass buys: it must
    stay correct as the counts grow, and it must not visit pairs it can skip."""
    a = IntervalSet.of(*((n * 1000.0, n * 1000.0 + 1.0) for n in range(2000)))
    b = IntervalSet.of(*((n * 1000.0 + 0.5, n * 1000.0 + 1.5) for n in range(2000)))
    assert a.overlaps(b)
    far = IntervalSet.of(*((n * 1000.0 + 500.0, n * 1000.0 + 501.0)
                           for n in range(2000)))
    assert not a.overlaps(far)


def test_covers_whole_span():
    s = IntervalSet.of((0.0, 10.0))
    assert s.covers(Interval(2.0, 8.0))
    assert not s.covers(Interval(2.0, 12.0))


def test_covers_across_merged_members():
    """Two members meeting within the query span still cover it; this is the
    verify stage's "is every second of this clip backed by evidence"."""
    s = IntervalSet.of((0.0, 5.0), (5.0, 10.0))
    assert s.covers(Interval(0.0, 10.0))


def test_covers_false_on_empty_set():
    assert not IntervalSet.empty().covers(Interval(0.0, 1.0))


def test_covers_point_on_closed_edge_is_true():
    """Intervals are closed: an instant probe exactly on a member's end is
    covered. Getting this wrong in either direction is a false negative or a
    false positive on a cut point."""
    s = IntervalSet.of((1.0, 2.0))
    assert s.covers(Interval(2.0, 2.0))
    assert not s.covers(Interval(2.5, 2.5))


def test_covers_instant_in_a_gap_is_false():
    """The defect this pins: an instant probe has duration 0, and a width-only
    check comparing it against an empty slice's width of 0 is satisfied by
    ``0 <= 0 + MERGE_EPS``. A point in a gap read as fully supported, which is
    the worst failure a verify stage can make — it certifies a clip at a
    timestamp where nothing was observed."""
    s = IntervalSet.of((0.0, 1.0), (5.0, 10.0))
    assert not s.covers(Interval(3.0, 3.0))
    assert s.at(3.0) is None


def test_covers_span_in_a_gap_is_false():
    s = IntervalSet.of((0.0, 1.0), (5.0, 10.0))
    assert not s.covers(Interval(1.0, 5.0))


def test_slice_clips_straddling_members():
    s = IntervalSet.of((1.0, 5.0), (10.0, 20.0))
    got = s.slice(3.0, 12.0)
    assert list(got) == [Interval(3.0, 5.0), Interval(10.0, 12.0)]


def test_gaps_inverts_the_question():
    """Where nothing happens is where the interesting failure usually is."""
    s = IntervalSet.of((1.0, 2.0), (5.0, 8.0))
    gaps = s.gaps(Interval(0.0, 10.0))
    assert list(gaps) == [Interval(0.0, 1.0), Interval(2.0, 5.0), Interval(8.0, 10.0)]


def test_gaps_of_full_set_is_empty():
    s = IntervalSet.spanning(0.0, 10.0)
    assert s.gaps(Interval(0.0, 10.0)).is_empty


# ── IntervalSet algebra: union / intersect / subtract / complement ──────────

def test_union_merges_interleaved():
    a = IntervalSet.of((0.0, 2.0), (4.0, 6.0))
    b = IntervalSet.of((1.0, 5.0))
    assert list(a.union(b)) == [Interval(0.0, 6.0)]


def test_union_disjoint_keeps_all():
    a = IntervalSet.of((0.0, 1.0))
    b = IntervalSet.of((5.0, 6.0))
    assert a.union(b).cardinality == 2


def test_union_with_empty_is_identity():
    a = IntervalSet.of((0.0, 1.0))
    assert list(a.union(IntervalSet.empty())) == list(a)


def test_intersect_is_the_multichannel_and():
    """Speech AND face present — the operation the grounding layer exists for."""
    speech = IntervalSet.of((0.0, 10.0), (20.0, 30.0))
    faces = IntervalSet.of((5.0, 25.0))
    assert list(speech.intersect(faces)) == [Interval(5.0, 10.0), Interval(20.0, 25.0)]


def test_intersect_disjoint_is_empty():
    a = IntervalSet.of((0.0, 1.0))
    b = IntervalSet.of((5.0, 6.0))
    assert a.intersect(b).is_empty


def test_intersect_with_empty_is_empty():
    assert IntervalSet.of((0.0, 1.0)).intersect(IntervalSet.empty()).is_empty


def test_intersect_is_commutative():
    a = IntervalSet.of((0.0, 10.0), (20.0, 30.0))
    b = IntervalSet.of((5.0, 25.0))
    assert list(a.intersect(b)) == list(b.intersect(a))


def test_intersect_touching_yields_instants():
    """Closed-interval semantics: a shared boundary is a real (zero-width)
    intersection, not an empty one."""
    a = IntervalSet.of((0.0, 2.0))
    b = IntervalSet.of((2.0, 4.0))
    got = list(a.intersect(b))
    assert len(got) == 1
    assert got[0].is_instant


def test_subtract_removes_middle_leaving_two():
    a = IntervalSet.of((0.0, 10.0))
    b = IntervalSet.of((3.0, 6.0))
    assert list(a.subtract(b)) == [Interval(0.0, 3.0), Interval(6.0, 10.0)]


def test_subtract_left_edge_straddle():
    """The case this module has to get right that a naive pairwise walk gets
    wrong: the removed span shares the left boundary, so the surviving piece is
    the right remainder and nothing else."""
    a = IntervalSet.of((2.0, 5.0))
    b = IntervalSet.of((2.0, 3.0))
    assert list(a.subtract(b)) == [Interval(3.0, 5.0)]


def test_subtract_right_edge_straddle():
    a = IntervalSet.of((2.0, 5.0))
    b = IntervalSet.of((4.0, 5.0))
    assert list(a.subtract(b)) == [Interval(2.0, 4.0)]


def test_subtract_exact_match_leaves_empty():
    a = IntervalSet.of((2.0, 5.0))
    b = IntervalSet.of((2.0, 5.0))
    assert a.subtract(b).is_empty


def test_subtract_multiple_cutters_across_members():
    """The pointer over `other` must only advance: a regression here walks
    O(N*M) and at hour scale that is the difference between a ms and a wait."""
    a = IntervalSet.of((0.0, 10.0), (20.0, 30.0), (40.0, 50.0))
    b = IntervalSet.of((1.0, 2.0), (4.0, 6.0), (22.0, 24.0), (45.0, 60.0))
    got = list(a.subtract(b))
    assert got == [
        Interval(0.0, 1.0), Interval(2.0, 4.0), Interval(6.0, 10.0),
        Interval(20.0, 22.0), Interval(24.0, 30.0), Interval(40.0, 45.0),
    ]


def test_subtract_disjoint_is_identity():
    a = IntervalSet.of((0.0, 1.0))
    b = IntervalSet.of((5.0, 6.0))
    assert list(a.subtract(b)) == [Interval(0.0, 1.0)]


def test_subtract_from_empty_is_empty():
    assert IntervalSet.empty().subtract(IntervalSet.of((0.0, 1.0))).is_empty


def test_subtract_is_not_destructive():
    """The operands are pure values; a caller's set must survive being used."""
    a = IntervalSet.of((0.0, 10.0))
    b = IntervalSet.of((3.0, 6.0))
    a.subtract(b)
    assert a.cardinality == 1
    assert a.bounds == Interval(0.0, 10.0)


def test_complement_within_bounds():
    s = IntervalSet.of((2.0, 4.0))
    assert list(s.complement(Interval(0.0, 10.0))) == [
        Interval(0.0, 2.0), Interval(4.0, 10.0),
    ]


def test_expand_set_renormalises_overlaps():
    """Expansion can make neighbours touch; the result must still satisfy the
    invariant rather than carrying two nearly-identical members."""
    s = IntervalSet.of((0.0, 1.0), (1.5, 2.5))
    got = s.expand(1.0)
    assert got.cardinality == 1
    assert got.bounds == Interval(-1.0, 3.5)


def test_expand_set_drops_members_outside_bounds():
    """The set counterpart of ``Interval.expand`` returning ``None``: a member
    lying wholly outside the world contributes nothing, rather than being
    clamped into a span the source never had."""
    s = IntervalSet.of((1.0, 2.0), (200.0, 210.0))
    got = s.expand(1.0, bounds=Interval(0.0, 100.0))
    assert list(got) == [Interval(0.0, 3.0)]


def test_algebra_round_trip_union_after_subtract():
    """subtract then union of the removed part reconstructs the original within
    merge tolerance — the property the cut list and the evidence set are kept
    consistent by."""
    a = IntervalSet.of((0.0, 10.0), (20.0, 30.0))
    b = IntervalSet.of((3.0, 6.0), (22.0, 25.0))
    rest = a.subtract(b)
    rebuilt = rest.union(b)
    assert rebuilt.cardinality == a.cardinality
    assert rebuilt.duration == pytest.approx(a.duration)


# ── serialisation round trip ────────────────────────────────────────────────

def test_interval_dict_round_trip():
    iv = Interval(1.5, 3.5)
    assert Interval.from_dict(iv.to_dict()) == iv


def test_set_dict_round_trip():
    s = IntervalSet.of((1.0, 2.0), (5.0, 8.0))
    r = IntervalSet.from_dict(s.to_dict())
    assert list(r) == list(s)
    assert r.to_dict()["cardinality"] == 2


def test_set_dict_round_trip_empty():
    r = IntervalSet.from_dict(IntervalSet.empty().to_dict())
    assert r.is_empty


def test_set_dict_carries_schema_and_measures():
    d = IntervalSet.of((1.0, 2.0)).to_dict()
    assert d["schema"] == "1.0"
    assert d["duration"] == pytest.approx(1.0)
    assert d["intervals"] == [{"start": 1.0, "end": 2.0}]


def test_set_len_matches_cardinality():
    s = IntervalSet.of((1.0, 2.0), (5.0, 8.0))
    assert len(s) == s.cardinality == 2
    assert len(list(iter(s))) == 2
