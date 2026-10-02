"""Failure-first tests for ``engine.eval``.

Every case here is a way a metric can report a better number than the
underlying reality, not a way it can report a good one: an empty population that
would read as a perfect score, a multi-span answer that a flattened comparison
would certify as exact, a recall built from the same weighting it is scored
with, a retrieval that predicts the union of everything, a reference grid that
is not there, and a ground truth that names no moments.

The pattern throughout: assert on the population and the denominator, not only
on the rate, because a percentage with no denominator is the thing this module
exists to stop being called a measurement.
"""
import math

import pytest

from engine.memory.intervals import MERGE_EPS, Interval, IntervalSet
from engine.eval import (
    BENCHMARKS,
    DOCUMENTED_PERCEPTION_SIGNALS,
    DEFAULT_K,
    MAX_DIFFERENCES,
    RELEASE_BLOCKING_SEVERITY,
    BenchmarkStatus,
    ConformanceReport,
    CoverageReport,
    Fraction,
    Mean,
    MiouReport,
    OverlapReport,
    Referenced,
    Retrievable,
    ReferenceIntegrity,
    RetrievalReport,
    RoundTripReport,
    SeverityCase,
    SeverityReport,
    Signal,
    SignalRow,
    SpanDegeneracyReport,
    benchmark,
    declined_benchmarks,
    ingest_coverage,
    known_item_retrieval,
    rarest_terms,
    rank_candidates,
    reference_integrity,
    retrievable_from_text,
    round_trip_identity,
    severity_pass_rate,
    span_degeneracy,
    temporal_iou,
    temporal_miou,
    temporal_overlap,
    word_boundary_conformance,
    word_edges,
)


# ── Fraction and Mean: the denominator is part of the number ────────────────

def test_fraction_value_is_none_without_a_denominator():
    """The defect this guards: ``0 / 0`` reads as ``0.0`` or ``1.0`` depending
    on which half of the division a caller reaches, and a metric over an empty
    population would print a clean score for a measurement that never ran."""
    f = Fraction(0, 0)
    assert f.value is None
    assert not f.is_defined
    assert not f.is_perfect


def test_fraction_value_and_perfect():
    assert Fraction(4, 5).value == pytest.approx(0.8)
    assert Fraction(5, 5).is_perfect
    assert not Fraction(4, 5).is_perfect
    # A full numerator over a zero denominator is not perfect, it is invalid.
    assert not Fraction(1, 0).is_perfect


def test_fraction_zero_over_population_is_a_real_zero():
    """Distinct from undefined: nothing passed out of something measured."""
    f = Fraction(0, 10)
    assert f.is_defined
    assert f.value == 0.0
    assert not f.is_perfect


def test_fraction_to_dict_carries_the_denominator():
    d = Fraction(3, 7).to_dict()
    assert d["numerator"] == 3
    assert d["denominator"] == 7
    assert d["value"] == pytest.approx(3 / 7)


def test_mean_value_is_none_without_a_population():
    assert Mean(0.0, 0).value is None


def test_mean_carries_its_population():
    m = Mean(4.0, 2)
    assert m.value == pytest.approx(2.0)
    assert m.population == 2


# ── temporal IoU: the union-of-everything game and the flattening trap ───────

def test_iou_exact_multispan_match_is_one():
    ref = IntervalSet.of((0.0, 10.0), (20.0, 30.0))
    assert temporal_iou(ref, ref) == pytest.approx(1.0)


def test_iou_is_not_computed_on_a_flattened_bounding_box():
    """The standing requirement: a multi-span answer is not flattened to one
    span before scoring. A prediction that bridges the gap between the two
    reference spans has the right bounding box and the wrong answer — it asserts
    ten extra seconds of coverage the reference does not contain — and must
    score below 1.0. A comparison done on ``(start, end)`` pairs would return
    exactly 1.0 here and certify it."""
    ref = IntervalSet.of((0.0, 10.0), (20.0, 30.0))
    bridged = IntervalSet.spanning(0.0, 30.0)
    assert temporal_iou(bridged, ref) == pytest.approx(20.0 / 30.0)


def test_iou_disjoint_prediction_is_zero():
    ref = IntervalSet.of((0.0, 10.0))
    assert temporal_iou(IntervalSet.of((100.0, 110.0)), ref) == pytest.approx(0.0)


def test_iou_predicting_the_whole_source_scores_low_not_high():
    """The gaming test. A retrieval that returns the union of everything
    intersects every reference span; the metric must not reward breadth. The
    union denominator dominates, exactly as the design document says B2's
    0.045–0.109 does against second-scale targets."""
    ref = IntervalSet.spanning(100.0, 104.0)
    everything = IntervalSet.spanning(0.0, 3600.0)
    got = temporal_iou(everything, ref)
    assert got < 0.01


def test_iou_superset_prediction_is_not_perfect():
    ref = IntervalSet.of((10.0, 20.0))
    superset = IntervalSet.of((0.0, 30.0))
    got = temporal_iou(superset, ref)
    assert 0.0 < got < 1.0


def test_iou_both_empty_is_undefined_not_zero():
    """Two empty sets are not "no overlap"; they are no measurement."""
    assert temporal_iou(IntervalSet.empty(), IntervalSet.empty()) is None


def test_iou_one_empty_side_is_a_real_zero():
    assert temporal_iou(IntervalSet.empty(), IntervalSet.of((0.0, 1.0))) == 0.0


def test_iou_identical_instants_is_undefined():
    """Two identical zero-width spans are arguably IoU 1.0 and arguably 0/0.
    Picking either silently would certify a beat-to-beat comparison this
    duration-based measure cannot make."""
    point = IntervalSet.of(Interval(5.0, 5.0))
    assert temporal_iou(point, point) is None


def test_iou_instant_against_a_span_is_a_real_zero():
    point = IntervalSet.of(Interval(5.0, 5.0))
    span = IntervalSet.of((0.0, 10.0))
    assert temporal_iou(point, span) == 0.0


def test_iou_is_symmetric():
    a = IntervalSet.of((0.0, 10.0), (20.0, 30.0))
    b = IntervalSet.of((5.0, 25.0))
    assert temporal_iou(a, b) == pytest.approx(temporal_iou(b, a))


def test_overlap_report_names_the_dominating_denominator():
    """A small IoU is only attributable when both durations are visible: 0.04
    from a 4-second target against a 100-second prediction and 0.04 from two
    4-second spans that barely touch are different claims."""
    ref = IntervalSet.spanning(0.0, 4.0)
    wide = IntervalSet.spanning(0.0, 100.0)
    report = temporal_overlap(wide, ref)
    assert report.iou == pytest.approx(0.04)
    assert report.union == pytest.approx(100.0)
    assert report.intersection == pytest.approx(4.0)
    assert report.predicted_duration == pytest.approx(100.0)
    assert report.reference_duration == pytest.approx(4.0)
    assert report.predicted_cardinality == 1
    assert report.reference_cardinality == 1


def test_overlap_report_on_undefined_input_reports_none():
    report = temporal_overlap(IntervalSet.empty(), IntervalSet.empty())
    assert report.iou is None
    assert report.intersection == 0.0


# ── mIoU: population is the reference, not the prediction ───────────────────

def test_miou_finding_one_of_two_reference_spans_is_a_half():
    ref = IntervalSet.of((0.0, 10.0), (20.0, 30.0))
    pred = IntervalSet.spanning(0.0, 10.0)
    got = temporal_miou(pred, ref)
    assert got.value == pytest.approx(0.5)
    assert got.population == 2
    assert got.per_span == pytest.approx([1.0, 0.0])


def test_miou_exact_multispan_is_one():
    ref = IntervalSet.of((0.0, 10.0), (20.0, 30.0))
    assert temporal_miou(ref, ref).value == pytest.approx(1.0)


def test_miou_loses_points_for_bridging_a_gap():
    """Scoring a span against the *whole* predicted set would return 0.5 for
    this exact-shape match; the local view is what keeps a correct multi-span
    answer correct while still charging for the ten seconds of bridged silence."""
    ref = IntervalSet.of((0.0, 10.0), (20.0, 30.0))
    bridged = IntervalSet.spanning(0.0, 30.0)
    got = temporal_miou(bridged, ref)
    assert got.value < 1.0
    assert got.per_span == pytest.approx([1 / 3, 1 / 3])


def test_miou_extra_disjoint_spans_are_free_and_that_is_reported():
    """The failure mode this metric has by construction: mIoU is recall-oriented,
    so predicted spans overlapping no reference span cost nothing here. It is
    the reason B2 reports ``temporal_iou`` alongside it — breadth is penalised
    there, not here."""
    ref = IntervalSet.of((0.0, 10.0))
    pred = IntervalSet.of((0.0, 10.0), (100.0, 200.0), (300.0, 400.0))
    got = temporal_miou(pred, ref)
    assert got.value == pytest.approx(1.0)
    assert got.population == 1
    # The whole-set view does charge for the same prediction.
    assert temporal_iou(pred, ref) < 1.0


def test_miou_local_breadth_still_costs():
    """A prediction that overshoots *inside* a reference span is penalised even
    under the local view — the union reaches past the span it is meant to cover."""
    ref = IntervalSet.spanning(0.0, 10.0)
    pred = IntervalSet.spanning(0.0, 40.0)
    assert temporal_miou(pred, ref).value == pytest.approx(0.25)


def test_miou_empty_reference_is_undefined():
    """mIoU against a ground truth naming no moments is unmeasured, not zero —
    a system with unpopulated ground truth must not print a clean-looking 0.000
    and must not print 1.000 either."""
    got = temporal_miou(IntervalSet.spanning(0.0, 10.0), IntervalSet.empty())
    assert got.value is None
    assert got.population == 0
    assert got.worst_span is None


def test_miou_report_carries_the_span_distribution():
    """The mean hides the shape: a 0.5 from "one perfect, one missed" and a 0.5
    from "two partial" are different failures, and per_span is what tells them
    apart."""
    ref = IntervalSet.of((0.0, 10.0), (20.0, 30.0))
    pred = IntervalSet.spanning(5.0, 25.0)
    got = temporal_miou(pred, ref)
    assert got.per_span == pytest.approx([0.2, 0.2])
    assert got.worst_span == pytest.approx(0.2)
    assert got.reference.value == pytest.approx(1.0)


def test_miou_reports_unscored_instant_reference_spans():
    """A zero-width reference span has no duration to score; it is reported as
    undefined rather than read as a zero that would look like localisation
    failure."""
    ref = IntervalSet.of(Interval(5.0, 5.0))
    pred = IntervalSet.spanning(0.0, 10.0)
    got = temporal_miou(pred, ref)
    assert got.undefined == 1
    assert got.population == 1
    assert got.value is None


# ── query construction and ranking: circularity and determinism ─────────────

def test_rarest_terms_rounds_up_and_is_deterministic():
    """Five terms at fraction one half keep three, and ties in idf break on the
    token, so the result never depends on set iteration order."""
    idf = {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0, "e": 1.0}
    bag = {"a", "b", "c", "d", "e"}
    got = rarest_terms(bag, idf, 0.5)
    assert len(got) == 3
    assert rarest_terms(bag, idf, 0.5) == rarest_terms(bag, idf, 0.5)


def test_rarest_terms_orders_by_idf_descending():
    idf = {"common": 0.1, "rare": 5.0, "mid": 1.0}
    got = rarest_terms({"common", "rare", "mid"}, idf, 2 / 3)
    assert got == {"rare", "mid"}


def test_rarest_terms_single_token_bag_keeps_its_token():
    """A one-term query is the common case for a short unit and must not become
    an empty query, which would score as a miss for a reason that is the
    metric's, not the population's."""
    assert rarest_terms({"gps"}, {"gps": 3.0}) == {"gps"}


def test_rarest_terms_empty_bag_is_empty():
    assert rarest_terms(set(), {"a": 1.0}) == frozenset()


def test_rarest_terms_full_fraction_is_the_leave_one_out_query():
    assert len(rarest_terms({"a", "b"}, {"a": 1.0, "b": 2.0}, 1.0)) == 2


def test_rarest_terms_rejects_a_fraction_outside_the_unit_interval():
    with pytest.raises(ValueError):
        rarest_terms({"a"}, {"a": 1.0}, 0.0)
    with pytest.raises(ValueError):
        rarest_terms({"a"}, {"a": 1.0}, 1.5)


def test_rank_candidates_ties_break_on_id_not_hash_order():
    """An all-zero score field — an empty query, or a population sharing no
    terms — must still yield a stable order, or the benchmark is measuring the
    hash seed."""
    a = Retrievable("a", IntervalSet.spanning(0.0, 1.0), frozenset())
    b = Retrievable("b", IntervalSet.spanning(2.0, 3.0), frozenset())
    c = Retrievable("c", IntervalSet.spanning(4.0, 5.0), frozenset())
    ranked = rank_candidates(frozenset(), [c, a, b], {})
    assert [cand.id for _, cand in ranked] == ["a", "b", "c"]


def test_rank_candidates_scores_by_weighted_containment():
    idf = {"budget": 5.0, "the": 0.1}
    q = frozenset({"budget"})
    a = Retrievable("a", IntervalSet.spanning(0.0, 1.0), frozenset({"budget", "the"}))
    b = Retrievable("b", IntervalSet.spanning(2.0, 3.0), frozenset({"the"}))
    ranked = rank_candidates(q, [b, a], idf)
    assert ranked[0][1].id == "a"
    assert ranked[0][0] == pytest.approx(1.0)
    assert ranked[1][0] == pytest.approx(0.0)


def test_retrievable_from_text_builds_tokens_and_span():
    got = retrievable_from_text("1", 10.0, 20.0, "The budget for the satellite")
    assert got.id == "1"
    assert got.intervals == IntervalSet.spanning(10.0, 20.0)
    assert "budget" in got.tokens
    assert "the" not in got.tokens


def _unit(uid: str, start: float, end: float, text: str) -> Retrievable:
    return retrievable_from_text(uid, start, end, text)


# ── known-item retrieval: the circular variant and the one that can fail ─────

def _distinct_population() -> list[Retrievable]:
    """Eight units whose rarest terms are unique to each unit — a population
    with no restatements, which is what the two available corpora measure as."""
    return [
        _unit("1", 0.0, 10.0, "satellite orbit telemetry budget"),
        _unit("2", 20.0, 30.0, "submarine canyon pressure gauge"),
        _unit("3", 40.0, 50.0, "glacier melt runoff salinity"),
        _unit("4", 60.0, 70.0, "vaccine cold chain logistics"),
        _unit("5", 80.0, 90.0, "telescope mirror alignment"),
        _unit("6", 100.0, 110.0, "river delta sediment core"),
        _unit("7", 120.0, 130.0, "battery cathode chemistry"),
        _unit("8", 140.0, 150.0, "trade tariff negotiation"),
    ]


def test_retrieval_self_included_is_circularly_perfect():
    """The headline number of B2: with the query item in its own candidate pool
    and the query built from the same idf the scoring uses, recall is 1.000 by
    construction. This is the expected score of a deterministic index, not
    evidence that retrieval works — the report carries ``self_excluded`` so the
    two variants are never confused."""
    report = known_item_retrieval(_distinct_population(), k=DEFAULT_K)
    assert report.recall_at_k.value == 1.0
    assert report.self_excluded is False
    assert report.queries == 8


def test_retrieval_mean_rank_when_found_is_near_one():
    report = known_item_retrieval(_distinct_population(), k=3)
    assert report.mean_rank.value == pytest.approx(1.0)
    assert report.mean_rank.population == 8


def test_retrieval_leave_one_out_on_a_population_with_no_restatements_is_zero():
    """The honest signal. With the query item out of the pool, a hit means a
    *different* passage fully carrying the query's subject; none does, so
    recall is 0/8. That is a property of the corpus — no restatements to
    confuse the index — not a metric that cannot see a match."""
    report = known_item_retrieval(
        _distinct_population(), k=DEFAULT_K, exclude_self=True, query_fraction=1.0
    )
    assert report.recall_at_k.value == 0.0
    assert report.self_excluded is True
    assert report.queries == 8


def test_retrieval_leave_one_out_detects_an_actual_restatement():
    """The metric must be able to fail *and* to succeed: when unit 9 restates
    unit 1's terms, the leave-one-out variant finds the restatement, proving the
    zero above was about the population and not about the metric.

    Restatement is symmetric here — unit 9's query finds unit 1, and unit 1's
    query finds unit 9 — so the count is two, not one. That symmetry is load
    bearing: it is what makes the leave-one-out number a reading about how often
    the corpus restates itself, rather than about which unit happened to be
    written first.
    """
    population = _distinct_population()
    population.append(
        _unit("9", 300.0, 310.0, "the satellite orbit telemetry budget again")
    )
    report = known_item_retrieval(
        population, k=DEFAULT_K, exclude_self=True, query_fraction=1.0
    )
    assert report.recall_at_k.numerator == 2
    assert report.recall_at_k.denominator == 9
    assert report.mean_rank.value == pytest.approx(1.0)
    assert report.io_undefined == 0


def test_retrieval_leave_one_out_ignores_a_partial_overlap():
    """Sharing some terms is not a restatement: a passage that mentions the
    satellite in another context scores below the threshold and is not a hit,
    which is what keeps the leave-one-out number from inflating on topical
    overlap alone."""
    population = _distinct_population()
    population.append(_unit("9", 300.0, 310.0, "the satellite launch was scrubbed"))
    report = known_item_retrieval(
        population, k=DEFAULT_K, exclude_self=True, query_fraction=1.0
    )
    assert report.recall_at_k.value == 0.0


def test_retrieval_recall_is_a_fraction_of_the_full_census():
    """An item with no content tokens cannot form a query; it is counted as a
    miss, so the denominator stays the census size rather than quietly
    shrinking to the items the metric found convenient to score — and it is not
    a free hit from the id tie-break either."""
    population = _distinct_population()
    population.append(Retrievable("10", IntervalSet.spanning(200.0, 210.0), frozenset()))
    report = known_item_retrieval(population, k=DEFAULT_K)
    assert report.queries == 9
    assert report.recall_at_k.denominator == 9
    assert report.recall_at_k.value == pytest.approx(8 / 9)
    assert report.io_undefined == 1


def test_retrieval_wide_k_drops_localisation_to_the_denominator():
    """The K trade-off the research settles and this framework does not
    reinvent: retrieval width buys recall and costs localisation. A K large
    enough to return every candidate makes recall perfect and the union IoU
    collapse, because the union denominator comes to dominate.

    mIoU does not collapse, and that is the point of carrying both: mIoU scores
    each reference span against only the predicted spans that overlap it, so a
    candidate returned on the far side of the source costs a span it does not
    touch nothing. Union IoU is the whole-set view that penalises breadth; mIoU
    is the local view that does not. Reporting one and not the other would let a
    scatter-gun retrieval print whichever number flatters it.
    """
    population = _distinct_population()
    report = known_item_retrieval(population, k=len(population))
    assert report.recall_at_k.value == 1.0
    assert report.mean_union_iou.value < 0.2
    assert report.mean_miou.value == 1.0
    # The two means must reconcile to the same scoreable population.
    assert report.mean_union_iou.population == report.mean_miou.population


def test_retrieval_reports_undefined_io_separately():
    """Queries with nothing to score — no content terms, or spans with no
    duration — are counted rather than silently read as zero, and the means are
    not taken over the query count they were excluded from."""
    population = [
        Retrievable("1", IntervalSet.of(Interval(5.0, 5.0)), frozenset({"a"})),
        Retrievable("2", IntervalSet.of(Interval(6.0, 6.0)), frozenset({"a"})),
    ]
    report = known_item_retrieval(population, k=1)
    assert report.io_undefined == 2
    assert report.mean_union_iou.population == 0
    assert report.queries == 2


def test_retrieval_empty_population_is_undefined_not_perfect():
    report = known_item_retrieval([], k=DEFAULT_K)
    assert report.queries == 0
    assert report.recall_at_k.value is None
    assert report.mean_rank.value is None


def test_retrieval_rejects_zero_k():
    with pytest.raises(ValueError):
        known_item_retrieval(_distinct_population(), k=0)


def test_retrieval_k_larger_than_the_pool_is_scored_not_raised():
    report = known_item_retrieval(_distinct_population()[:2], k=50)
    assert report.k == 50
    assert report.recall_at_k.value == 1.0


def test_retrieval_is_byte_identical_across_calls():
    a = known_item_retrieval(_distinct_population(), k=3)
    b = known_item_retrieval(_distinct_population(), k=3)
    assert a.to_dict() == b.to_dict()


# ── word-boundary conformance: a missing grid is not a pass ──────────────────

def test_word_edges_sorts_starts_and_ends():
    got = word_edges([(20.0, 21.0), (5.0, 8.0)])
    assert got == [5.0, 8.0, 20.0, 21.0]


def test_conformance_all_bounds_on_edges_is_perfect():
    edges = word_edges([(0.0, 1.0), (1.0, 2.0)])
    report = word_boundary_conformance([0.0, 1.0, 2.0], edges)
    assert report.within_tolerance.is_perfect
    assert report.max_deviation == pytest.approx(0.0)
    assert report.population == 3
    assert report.reference_edges == 4


def test_conformance_counts_a_bound_on_either_word_edge():
    """A clip opening at a word's start and one closing at a word's end are both
    word-anchored; only accepting starts would report the latter as drifting."""
    edges = word_edges([(10.0, 12.0)])
    report = word_boundary_conformance([10.0, 12.0], edges)
    assert report.within_tolerance.is_perfect


def test_conformance_off_grid_bounds_fail_and_the_farthest_is_named():
    edges = word_edges([(0.0, 1.0), (2.0, 3.0)])
    report = word_boundary_conformance([0.0, 2.5, 10.0], edges)
    assert report.within_tolerance.value == pytest.approx(1 / 3)
    assert report.max_deviation == pytest.approx(7.0)
    assert report.mean_deviation.value == pytest.approx((0.0 + 0.5 + 7.0) / 3)


def test_conformance_tolerates_documented_float_noise():
    """The 364.84 / 364.84000000000003 straddle from ``engine.edit_plan``: a
    bound within MERGE_EPS of an edge is on the edge, or the algebra's own merge
    rule and this metric would disagree about whether a cut point existed."""
    report = word_boundary_conformance([364.84000000000003], [364.84, 370.0])
    assert report.within_tolerance.is_perfect


def test_conformance_respects_the_tolerance_argument():
    edges = word_edges([(0.0, 1.0)])
    assert word_boundary_conformance([1.5], edges, tolerance=1.0).within_tolerance.is_perfect
    assert word_boundary_conformance([1.5], edges, tolerance=0.1).within_tolerance.value == 0.0


def test_conformance_no_bounds_is_undefined_not_vacuously_perfect():
    """An empty bound population verified nothing; reporting 1.000 would be a
    perfect score produced by having nothing to score."""
    report = word_boundary_conformance([], [0.0, 1.0])
    assert report.population == 0
    assert report.within_tolerance.value is None
    assert not report.within_tolerance.is_perfect
    assert report.max_deviation is None


def test_conformance_missing_reference_grid_reports_failure_and_no_statistic():
    """A transcript with no word timestamps is the G2-degraded case. The share
    is a real 0.0 — no bound is near an edge that does not exist — but the
    deviation statistics are None, because a distance from a grid that is not
    there is not a measurement."""
    report = word_boundary_conformance([0.0, 5.0], [])
    assert report.population == 2
    assert report.within_tolerance.value == 0.0
    assert report.max_deviation is None
    assert report.mean_deviation.value is None
    assert report.reference_edges == 0


def test_conformance_lands_on_the_wrong_word_and_still_passes():
    """The limitation, pinned rather than hidden: conformance is not
    correctness. A bound exactly on a word edge scores 1.000 whether that word
    is the right one or not, and this metric cannot tell them apart."""
    edges = word_edges([(0.0, 1.0), (2.0, 3.0)])
    report = word_boundary_conformance([3.0], edges)
    assert report.within_tolerance.is_perfect


# ── span degeneracy: B5's precondition count ─────────────────────────────────

def test_degeneracy_counts_zero_width_spans():
    report = span_degeneracy([Interval(0.0, 10.0), Interval(5.0, 5.0), Interval(6.0, 6.0)])
    assert report.population == 3
    assert report.degenerate == 2
    assert report.fraction.value == pytest.approx(2 / 3)


def test_degeneracy_none_is_zero_not_undefined():
    report = span_degeneracy([Interval(0.0, 10.0)])
    assert report.degenerate == 0
    assert report.fraction.value == 0.0


def test_degeneracy_all_degenerate_is_one():
    report = span_degeneracy([Interval(1.0, 1.0), Interval(2.0, 2.0)])
    assert report.fraction.is_perfect


def test_degeneracy_empty_is_undefined():
    assert span_degeneracy([]).fraction.value is None


def test_degeneracy_threshold_follows_the_memory_algebra_not_cut_algebra():
    """Two notions of "zero width" live in this codebase — MERGE_EPS and
    ``engine.cut_algebra.DEGENERATE_SECONDS`` (0.01). The precondition for the
    memory algebra's own failure modes is the memory algebra's constant."""
    sub_eps = Interval(1.0, 1.0 + MERGE_EPS / 2)
    report = span_degeneracy([sub_eps])
    assert report.degenerate == 1
    assert report.threshold == MERGE_EPS


# ── round-trip identity: a wrong-but-stable corpus still passes ──────────────

def test_round_trip_identical_nested_values_are_exact():
    before = {"a": 1, "b": [1, 2, {"c": "x"}], "d": None}
    report = round_trip_identity(before, before)
    assert report.is_exact
    assert report.leaves == 5
    assert report.differences == ()


def test_round_trip_one_differing_leaf_is_named_and_counted():
    before = {"a": 1, "b": {"c": 2}}
    after = {"a": 1, "b": {"c": 3}}
    report = round_trip_identity(before, after)
    assert not report.is_exact
    assert report.leaves == 2
    assert report.identical.value == pytest.approx(0.5)
    assert report.differences == ("b.c",)
    assert report.difference_count == 1


def test_round_trip_reports_float_noise_as_a_real_difference():
    """Exact ``==`` by design: the round trip B1 measures is exact, so the
    364.84 / 364.84000000000003 straddle is a difference at a named path, not
    something absorbed into an epsilon and silently reported as identical."""
    report = round_trip_identity({"t": 364.84}, {"t": 364.84000000000003})
    assert report.difference_count == 1
    assert report.differences == ("t",)


def test_round_trip_key_only_on_one_side_is_named():
    report = round_trip_identity({"a": 1}, {"a": 1, "b": 2})
    assert report.differences == ("b",)
    assert report.leaves == 2
    assert report.identical.value == pytest.approx(0.5)


def test_round_trip_list_length_difference_names_every_extra_index():
    report = round_trip_identity({"l": [1, 2]}, {"l": [1, 2, 3, 4]})
    assert report.differences == ("l[2]", "l[3]")
    assert report.leaves == 4


def test_round_trip_key_order_is_irrelevant():
    a = {"x": 1, "y": 2}
    b = {"y": 2, "x": 1}
    assert round_trip_identity(a, b).is_exact


def test_round_trip_shape_mismatch_is_one_named_difference():
    report = round_trip_identity({"a": {"b": 1}}, {"a": 5})
    assert report.difference_count == 1
    assert report.differences == ("a",)


def test_round_trip_empty_input_is_not_exact():
    """An empty comparison verified nothing — the same rule as an empty bound
    population, and for the same reason."""
    report = round_trip_identity({}, {})
    assert report.leaves == 0
    assert report.identical.value is None
    assert not report.is_exact


def test_round_trip_differences_are_sorted_and_bounded():
    """A structurally different file must still produce a stable, readable
    report rather than a blow-up."""
    before = {str(i): i for i in range(MAX_DIFFERENCES * 3)}
    after = {str(i): i + 1 for i in range(MAX_DIFFERENCES * 3)}
    report = round_trip_identity(before, after)
    assert report.differences_truncated is True
    assert len(report.differences) == MAX_DIFFERENCES
    assert report.difference_count == MAX_DIFFERENCES * 3
    assert list(report.differences) == sorted(report.differences)


# ── severity pass rate: the release gate must be able to fire ───────────────

def _cases(failing: set[str]) -> list[SeverityCase]:
    return [
        SeverityCase("1", "S1", "1" not in failing),
        SeverityCase("2", "S1", "2" not in failing),
        SeverityCase("3", "S1", "3" not in failing),
        SeverityCase("4", "S1", "4" not in failing),
        SeverityCase("5", "S2", "5" not in failing),
        SeverityCase("6", "S3", "6" not in failing),
        SeverityCase("19", "verified-clean", True),
    ]


def test_severity_all_pass_is_not_blocked():
    report = severity_pass_rate(_cases(set()))
    assert report.overall.is_perfect
    assert report.release_blocked is False
    assert report.failed == ()


def test_severity_per_severity_rates_are_reported():
    report = severity_pass_rate(_cases(set()))
    rates = dict(report.by_severity)
    assert rates["S1"].is_perfect
    assert rates["S2"].is_perfect
    assert rates["verified-clean"].is_perfect
    assert report.population == 7


def test_severity_an_s1_failure_blocks_release_even_when_everything_else_passes():
    """The gate: S1 *"can put a wrong clip in front of an editor or make the
    verify stage lie about backing"*, so one failure blocks the release however
    many S2 and S3 cases pass."""
    report = severity_pass_rate(_cases({"2"}))
    assert report.release_blocked is True
    assert report.overall.value == pytest.approx(6 / 7)
    assert report.failed == ("2",)
    assert dict(report.by_severity)["S1"].value == pytest.approx(0.75)


def test_severity_an_s3_failure_does_not_block():
    report = severity_pass_rate(_cases({"6"}))
    assert report.release_blocked is False
    assert report.failed == ("6",)


def test_severity_no_blocking_cases_reports_the_gate_did_not_run():
    """A population with no S1 at all carries the blocking severity as an
    undefined rate — read the population, not the boolean, because a gate over
    nothing neither passed nor failed."""
    cases = [SeverityCase("5", "S2", True), SeverityCase("6", "S3", False)]
    report = severity_pass_rate(cases)
    assert report.release_blocked is False
    assert dict(report.by_severity)[RELEASE_BLOCKING_SEVERITY].value is None
    assert dict(report.by_severity)[RELEASE_BLOCKING_SEVERITY].denominator == 0
    assert report.overall.value == pytest.approx(0.5)


def test_severity_empty_census_is_not_a_pass():
    report = severity_pass_rate([])
    assert report.population == 0
    assert report.overall.value is None
    assert report.release_blocked is False
    assert report.blocking_severity == RELEASE_BLOCKING_SEVERITY


def test_severity_blocking_severity_is_stated_as_a_field():
    report = severity_pass_rate([SeverityCase("1", "S1", False)], blocking_severity="S2")
    assert report.blocking_severity == "S2"
    assert report.release_blocked is False


# ── reference integrity: corruption is caught, hallucination is not ─────────

def _records() -> tuple[list[Referenced], dict[str, dict[str, set[str]]]]:
    tables = {"threads": {"t1": {"m1", "m2"}, "t2": {"m1"}}}
    records = [
        Referenced(
            "m1", IntervalSet.of((0.0, 10.0)),
            references=("m2",), joins=(("threads", "t1"), ("threads", "t2")),
        ),
        Referenced(
            "m2", IntervalSet.of((10.0, 20.0)),
            references=("m1",), joins=(("threads", "t1"),),
        ),
    ]
    return records, tables


def test_reference_integrity_clean_input_passes_all_three():
    records, tables = _records()
    report = reference_integrity(records, source_duration=20.0, index_tables=tables)
    assert report.is_clean
    assert report.references.is_perfect
    assert report.in_range.is_perfect
    assert report.joins.is_perfect
    assert report.population == 2


def test_reference_integrity_dangling_reference_is_named():
    records, tables = _records()
    records.append(Referenced("m3", IntervalSet.of((0.0, 1.0)), references=("nope",)))
    report = reference_integrity(records, source_duration=20.0, index_tables=tables)
    assert not report.is_clean
    assert report.references.value == pytest.approx(2 / 3)
    assert report.dangling == ("m3->nope",)


def test_reference_integrity_span_past_the_source_is_named():
    records, tables = _records()
    report = reference_integrity(
        records, source_duration=15.0, index_tables=tables
    )
    assert not report.is_clean
    assert report.in_range.value == pytest.approx(0.5)
    assert report.out_of_range == ("m2:10-20",)


def test_reference_integrity_negative_start_is_out_of_range():
    records = [Referenced("m1", IntervalSet.of((-5.0, 5.0)))]
    report = reference_integrity(records, source_duration=10.0)
    assert report.out_of_range == ("m1:-5-5",)


def test_reference_integrity_unidirectional_join_is_named():
    """The failure a writer that appended membership on only one side produces:
    the record claims the thread, the thread does not claim the record back."""
    records, _ = _records()
    # t1 dropped m2's membership; t2 is left intact so only one join breaks.
    tables = {"threads": {"t1": {"m1"}, "t2": {"m1"}}}
    report = reference_integrity(records, source_duration=20.0, index_tables=tables)
    assert not report.is_clean
    assert report.joins.value == pytest.approx(2 / 3)
    assert report.inconsistent == ("m2@threads:t1",)


def test_reference_integrity_join_to_an_unknown_target_is_inconsistent():
    records, tables = _records()
    report = reference_integrity(records, source_duration=20.0, index_tables={})
    assert report.joins.value == 0.0
    # Every declared join is checked in record order — m1's two, then m2's — so
    # an exporter that dropped the index tables fails on all of them, not only
    # on the first.
    assert report.inconsistent == (
        "m1@threads:t1", "m1@threads:t2", "m2@threads:t1",
    )


def test_reference_integrity_unknown_duration_leaves_the_range_check_unrun():
    """An exporter that dropped the source duration must not thereby pass the
    range check: unmeasured is reported as None, never as 1.000."""
    records, tables = _records()
    records.append(Referenced("m3", IntervalSet.of((900.0, 1000.0))))
    report = reference_integrity(records, index_tables=tables)
    assert report.in_range.value is None
    assert report.source_duration is None
    assert report.out_of_range == ()


def test_reference_integrity_empty_is_not_clean():
    report = reference_integrity([])
    assert report.population == 0
    assert report.references.value is None
    assert report.in_range.value is None
    assert report.joins.value is None
    assert not report.is_clean


def test_reference_integrity_a_span_on_the_closed_boundary_is_in_range():
    records = [Referenced("m1", IntervalSet.of((0.0, 10.0)))]
    report = reference_integrity(records, source_duration=10.0)
    assert report.in_range.is_perfect


# ── ingest coverage: presence is not fidelity, and the denominator is fixed ─

def _all_six(record_count: int = 5) -> list[Signal]:
    return [
        Signal(name, present=True, record_count=record_count, time_base="source_s")
        for name in DOCUMENTED_PERCEPTION_SIGNALS
    ]


def _row(report: CoverageReport, name: str) -> SignalRow:
    """Look a signal up by name: the census is alphabetical, so its position is
    not the position it holds in ``DOCUMENTED_PERCEPTION_SIGNALS``."""
    for row in report.per_signal:
        if row.name == name:
            return row
    raise AssertionError(f"no census row for {name!r}")


def test_ingest_coverage_all_six_is_perfect():
    report = ingest_coverage(_all_six())
    assert report.present.is_perfect
    assert report.population == 6
    assert report.absent == ()
    assert report.unexpected == ()


def test_ingest_coverage_measured_floor_is_one_of_six():
    """The inventory's own measured state is the floor this benchmark beats, and
    it is lower than the inventory's headline: two signals reach disk, but the
    coverage entry reaches it as aggregates only, with no regions behind it. A
    signal counts only when it carried records, so the floor under this metric
    is one of six — the number a reader should compare against, not the two the
    inventory's prose leads with."""
    report = ingest_coverage([Signal("story_structure", True, 83, "source_s")])
    assert report.present.value == pytest.approx(1 / 6)
    assert report.population == 6
    assert "story_structure" not in report.absent
    assert _row(report, "story_structure").record_count == 83
    assert _row(report, "face_subject_tracks").in_input is False
    # The aggregate-only coverage entry is a documented signal that reached disk
    # carrying no records: named absent, not quietly dropped from the census.
    report = ingest_coverage([Signal("speech_regions", True, 0, "source_s")])
    assert report.present.value == 0.0
    assert "speech_regions" in report.absent


def test_ingest_coverage_present_with_no_records_counts_absent():
    """A signal that reached the store with zero rows has a schema and no data.
    Scoring it present would let an adapter that opened a table and wrote
    nothing report the same coverage as one that ingested the channel."""
    report = ingest_coverage([Signal("story_structure", True, 0, "")])
    assert report.present.value == 0.0
    # Every documented signal is named in the census; the one that reached disk
    # empty is named absent alongside the five that never reached it, so a
    # reader can tell "no schema" from "schema, no rows".
    assert len(report.absent) == 6
    assert "story_structure" in report.absent
    assert _row(report, "story_structure").in_input is True
    assert _row(report, "story_structure").record_count == 0
    assert _row(report, "face_subject_tracks").in_input is False


def test_ingest_coverage_a_missing_documented_signal_is_named_absent():
    report = ingest_coverage(_all_six()[:3])
    assert report.present.value == pytest.approx(3 / 6)
    # The census is alphabetical, so the three documented signals not fed in are
    # named in order — absent is a census of the inventory, not of the input.
    assert report.absent == ("pause_structure", "pipeline_persistence", "story_structure")
    assert _row(report, "face_subject_tracks").present
    assert _row(report, "face_subject_tracks").in_input is True
    assert _row(report, "pause_structure").in_input is False


def test_ingest_coverage_undocumented_signal_does_not_inflate_the_denominator():
    """A channel reaching memory that the inventory never named is a schema
    change, and the benchmark counts it as ``unexpected`` rather than letting it
    quietly enlarge the population."""
    signals = list(_all_six())
    signals.append(Signal("haptic_feedback", True, 4, "source_s"))
    report = ingest_coverage(signals)
    assert report.population == 6
    assert report.present.is_perfect
    assert report.unexpected == ("haptic_feedback",)


def test_ingest_coverage_per_signal_rows_carry_counts_and_time_bases():
    report = ingest_coverage([Signal("story_structure", True, 83, "source_s")])
    row = next(r for r in report.per_signal if r.name == "story_structure")
    assert row.record_count == 83
    assert row.time_base == "source_s"
    absent = next(r for r in report.per_signal if r.name != "story_structure")
    assert absent.present is False
    assert absent.in_input is False


def test_ingest_coverage_empty_expected_is_undefined():
    assert ingest_coverage([], expected=[]).present.value is None


# ── the benchmark index: the refusals are queryable, and none is dropped ─────

def test_benchmark_index_covers_b1_through_b12():
    ids = [s.benchmark_id for s in BENCHMARKS]
    assert ids == [f"B{i}" for i in range(1, 13)]


def test_benchmark_lookup_by_id():
    got = benchmark("B11")
    assert got is not None
    assert got.state == "declined"
    assert "human" in got.blocker


def test_benchmark_unknown_id_is_none():
    """Asking for a benchmark the framework does not carry returns a loud None
    rather than a freshly invented metric."""
    assert benchmark("B13") is None


def test_declined_benchmarks_are_exactly_the_three_named():
    declined = declined_benchmarks()
    assert [s.benchmark_id for s in declined] == ["B10", "B11", "B12"]
    for status in declined:
        assert status.blocker
        assert status.computable_when


def test_every_implementable_benchmark_names_its_metric_functions():
    for status in BENCHMARKS:
        if status.state == "implementable":
            assert status.metrics, status.benchmark_id


def test_no_benchmark_is_both_implementable_and_declined():
    states = [s.state for s in BENCHMARKS]
    assert all(s in ("implementable", "exists_elsewhere", "declined") for s in states)


def test_benchmark_status_is_hashable_and_comparable():
    status = benchmark("B1")
    assert {status: "x"}[status] == "x"


# ── serialisation: every report round-trips through plain JSON types ─────────

def _json_native(value) -> bool:
    """Every leaf a stock ``json.dumps`` can encode, or the offending type name.

    ``to_dict`` is the boundary between a report and anything that ships it, so
    a ``Fraction`` or a ``datetime`` slipping through it is a bug the consumer
    finds as a ``TypeError``, far from the code that produced it.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, dict):
        return all(_json_native(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_json_native(v) for v in value)
    raise AssertionError(f"non-JSON-native type in a report: {type(value).__name__}")


@pytest.mark.parametrize("factory", [
    lambda: temporal_overlap(IntervalSet.of((0.0, 1.0)), IntervalSet.spanning(0.0, 5.0)),
    lambda: temporal_miou(IntervalSet.of((0.0, 1.0)), IntervalSet.of((0.0, 2.0))),
    lambda: known_item_retrieval(_distinct_population()),
    lambda: word_boundary_conformance([0.0, 4.0], word_edges([(0.0, 1.0)])),
    lambda: span_degeneracy([Interval(0.0, 0.0)]),
    lambda: round_trip_identity({"a": 1}, {"a": 2}),
    lambda: severity_pass_rate(_cases({"2"})),
    lambda: reference_integrity(_records()[0], source_duration=20.0, index_tables=_records()[1]),
    lambda: ingest_coverage(_all_six()),
])
def test_reports_serialise_to_json_types(factory):
    import json

    payload = factory().to_dict()
    assert _json_native(payload)
    # Must serialise with the default encoder, and the canonical form must be
    # stable under a round trip. Equality against the pre-serialisation payload
    # is deliberately not the assertion: tuples legitimately become lists in
    # JSON, so the real check is that re-serialising the decoded payload
    # reproduces the same bytes — no information lost, none coerced on a second
    # pass by a value that only looked JSON-native the first time.
    once = json.dumps(payload, sort_keys=True)
    assert json.dumps(json.loads(once), sort_keys=True) == once


def test_import_is_clean_and_module_states_its_version():
    import engine.eval as mod

    assert mod.SCHEMA_VERSION == "1.0"
    assert mod.DEFAULT_K == 3
    assert isinstance(mod.DOCUMENTED_PERCEPTION_SIGNALS, tuple)
    assert len(mod.DOCUMENTED_PERCEPTION_SIGNALS) == 6


def test_module_has_no_side_effects_on_import():
    """Purity contract: importing twice must not accumulate state or touch the
    environment."""
    import importlib

    import engine.eval as mod
    reloaded = importlib.reload(mod)
    assert reloaded.SCHEMA_VERSION == mod.SCHEMA_VERSION
    assert len(reloaded.BENCHMARKS) == len(mod.BENCHMARKS)


def test_math_import_is_used_for_rounding_not_for_timing():
    """No wall clock, no network, no filesystem in the module — guarded by
    checking the module never touches the usual suspects."""
    import engine.eval as mod
    import inspect

    source = inspect.getsource(mod)
    for banned in ("time.time", "datetime.now", "open(", "requests", "urllib", "pathlib"):
        assert banned not in source, banned
    assert math.isfinite(mod.Fraction(1, 1).value)
