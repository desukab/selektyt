"""SeleKyT evaluation framework — measuring whether the intelligence layer works.

Why this module exists
----------------------

The repository already audits the *deterministic editorial pipeline* — cut
placement, caption fidelity, render geometry, NLE interchange — and that audit
is a closed question here (``tools/editorial_audit.py``, ``tools/nle_check.py``,
documented in ``docs/EVALUATION.md`` and ``docs/CORPUS_RESULTS.md``). It does
not and cannot answer the question this module is for: *is a retrieved moment
the right moment?* Its checks compare a clip against the transcript the clip was
cut from, so a transcript that is phonetic noise produces a clean audit —
``docs/CORPUS_RESULTS.md`` records exactly that case on the corpus's own Telugu
source. Self-consistency is not truth, and the layer that decides *what to
consider* produces no clips for the audit to catch when it is wrong.

This module implements the benchmarks defined in ``docs/selekyt-eval-framework.md``
(B1–B9 as code; B6 and B7 already exist as committed tests and are *cited*, not
reinvented; B10–B12 are declined and recorded here as refusals so a caller
cannot accidentally compute a number for them). Each metric is built to be
scored on the corpus's own long-form sources rather than a borrowed benchmark,
and each carries, in its docstring, the three things the project's standing rule
demands: **what it measures, what it does not measure, and its known
limitation.**

The governing rule, quoted: *"Do not manufacture metrics."* Where a field of a
benchmark cannot be filled, the design document says so and names the blocker;
the same discipline is enforced in code. A metric with no population returns a
fraction whose value is ``None`` rather than ``0.0`` or ``1.0``, because a
percentage with no denominator is not a measurement, and a degenerate input that
silently reads as a perfect score is the failure mode this framework exists to
prevent.

What it is not
--------------

It is not a benchmark runner and it is not a report generator. Nothing here
reads a file, decodes media, starts a clock or touches the network: the module
takes already-loaded data and returns values, so a caller composes it with
whatever owns the artefacts. It is not a quality claim either. Every metric here
is a mechanical self-consistency measurement, and the design document's §7 is
the honest summary of that: no human has judged any corpus clip, there is no
editorial ground truth beyond the edit plan and the word-timestamped transcript,
and B2's recall of 1.000 is achieved with a query built from the same idf
weighting the retrieval scores with. A number from this module is evidence that
the system behaves as designed, never evidence that it edits well.

Purity
------

Deterministic and side-effect free: identical input yields byte-identical
output, across interpreters. No wall clock, no network, no filesystem, no
inference. Every set iteration that could affect output order is sorted, and
every tie-break is explicit, because a benchmark whose results reorder between
runs is measuring the hash seed. Every reported rate is a ``Fraction`` carrying
its own denominator, and every mean carries its population, so a reader can
always see the size of the thing being averaged.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from engine.lexical import idf_weights, weighted_containment
from engine.memory.intervals import MERGE_EPS, Interval, IntervalSet

SCHEMA_VERSION = "1.0"

#: The operating point the retrieval research settled on and this framework did
#: not reinvent (``docs/selekyt-eval-framework.md`` §6): retrieval width buys
#: recall and costs localisation precision, so K is a per-target-width choice
#: and 3 is the documented default.
DEFAULT_K = 3

#: The six perception signals the inventory documents as reaching, or not
#: reaching, memory (``docs/memory-perception-inventory.md``, scored as B9).
#: Named here so the denominator of ingest coverage is the inventory's own list
#: and not whatever a caller happened to pass.
DOCUMENTED_PERCEPTION_SIGNALS: tuple[str, ...] = (
    "face_subject_tracks",        # engine.reframe._perceive_window
    "shot_scene_classification",  # engine.reframe_scenes.analyse_scene
    "speech_regions",             # engine.vad chain
    "pause_structure",            # engine.pause_classifier + engine.audio_fusion
    "story_structure",            # engine.corpus.build_corpus
    "pipeline_persistence",       # selekyt/pipeline.py persistence list
)

#: The severity that gates a release, per B4: S1 *"can put a wrong clip in front
#: of an editor or make the verify stage lie about backing"*.
RELEASE_BLOCKING_SEVERITY = "S1"

#: The score at which a different passage counts as restating the query's
#: subject. ``weighted_containment`` is capped at 1.0, and 1.0 is reached
#: exactly when the smaller term bag is fully shared — so this is the
#: leave-one-out variant's definition of a hit, and the reason it measures
#: restatement frequency rather than retrieval quality.
RESTATEMENT_THRESHOLD = 1.0

#: Cap on the difference list a round-trip report carries, so a structurally
#: different file produces a report rather than a memory blow-up.
MAX_DIFFERENCES = 32


# ── value objects: a rate is a number plus its denominator ───────────────────


@dataclass(frozen=True)
class Fraction:
    """A share that cannot be read without seeing what it was a share of.

    ``value`` is ``None`` when the denominator is zero. That is the whole point:
    ``0 / 0`` in a metric reads as either ``0.0`` or ``1.0`` depending on which
    half of the division a caller reaches first, and an empty population scored
    either way is a fabricated number. A caller who receives ``None`` has been
    told the measurement did not run, which is the honest answer.
    """

    numerator: int = 0
    denominator: int = 0

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    @property
    def is_defined(self) -> bool:
        return self.denominator > 0

    @property
    def is_perfect(self) -> bool:
        """True only when something was measured and all of it passed. An empty
        population is not a perfect score; it is no score."""
        return self.denominator > 0 and self.numerator == self.denominator

    def to_dict(self) -> dict:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
        }


@dataclass(frozen=True)
class Mean:
    """An average carrying the population it was taken over.

    A mean of 0.040 over 83 queries and a mean of 0.040 over 2 queries are not
    the same claim, and the ``population`` field is what keeps them apart. As
    with ``Fraction``, a zero population yields ``None`` rather than a number.
    """

    total: float = 0.0
    population: int = 0

    @property
    def value(self) -> float | None:
        return self.total / self.population if self.population else None

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "population": self.population,
            "value": self.value,
        }


def _mean(values: Iterable[float]) -> Mean:
    total = 0.0
    count = 0
    for v in values:
        total += v
        count += 1
    return Mean(total, count)


# ── temporal grounding quality (B2's localisation half, and every B3-adjacent
#    question about where an answer fell) ──────────────────────────────────────


@dataclass(frozen=True)
class OverlapReport:
    """Intersection-over-union between two interval sets, with both durations.

    ``intersection`` and ``union`` are the denominator made explicit: B2's
    measured mIoU of 0.045–0.109 is small because the union of three
    minute-scale candidates dwarfs a second-scale target, and a reader who sees
    only the ratio cannot tell a precise answer from a wide one
    (``docs/selekyt-eval-framework.md`` §6, on why that number is not comparable
    to the paper's 0.354).
    """

    intersection: float
    union: float
    iou: float | None
    predicted_cardinality: int
    reference_cardinality: int
    predicted_duration: float
    reference_duration: float

    def to_dict(self) -> dict:
        return {
            "intersection": self.intersection,
            "union": self.union,
            "iou": self.iou,
            "predicted_cardinality": self.predicted_cardinality,
            "reference_cardinality": self.reference_cardinality,
            "predicted_duration": self.predicted_duration,
            "reference_duration": self.reference_duration,
        }


def temporal_iou(predicted: IntervalSet, reference: IntervalSet) -> float | None:
    """Duration IoU of two interval sets, or ``None`` when it is undefined.

    Computed on ``IntervalSet``, not on a flattened ``(start, end)`` pair, so a
    multi-span answer keeps its gaps: a prediction that fills in the silence
    between two reference spans scores below 1.0 because the union grows, which
    a bounding-box comparison would hide.

    ``None`` — not ``0.0``, not ``1.0`` — when the union has no duration. That
    happens when both operands are empty, or when both are instants at the same
    point. Two identical zero-width spans are arguably IoU 1.0 and arguably
    0/0, and picking either silently would certify a beat-to-beat comparison
    this duration-based measure cannot actually make. Callers comparing cut
    points need a different metric (see ``span_degeneracy`` and
    ``word_boundary_conformance``); this one reports that it did not measure.

    Measures: how much of the predicted time coincides with the reference, scaled
    by how much extra time the prediction asserts.
    Does not measure: whether the predicted spans are the *right* spans. A
    prediction that covers the whole source intersects every reference and still
    scores low, which is the metric resisting the union-of-everything game, but
    a prediction that covers the right region for the wrong reason scores well.
    Known limitation: duration-weighted, so it is blind to *where inside* a span
    the overlap falls, and it cannot see ordering, cardinality mismatch or any
    property other than covered time.
    """
    intersection = predicted.intersect(reference).duration
    union = predicted.duration + reference.duration - intersection
    if union <= 0.0:
        return None
    return intersection / union


def temporal_overlap(
    predicted: IntervalSet, reference: IntervalSet
) -> OverlapReport:
    """``temporal_iou`` with both durations and both cardinalities attached.

    Use this rather than ``temporal_iou`` whenever the number is going into a
    report: the durations are what make a small IoU attributable to a wide
    prediction rather than to a wrong one.
    """
    intersection = predicted.intersect(reference).duration
    union = predicted.duration + reference.duration - intersection
    return OverlapReport(
        intersection=intersection,
        union=union,
        iou=(None if union <= 0.0 else intersection / union),
        predicted_cardinality=predicted.cardinality,
        reference_cardinality=reference.cardinality,
        predicted_duration=predicted.duration,
        reference_duration=reference.duration,
    )


@dataclass(frozen=True)
class MiouReport:
    """Mean IoU over the *reference* spans against one predicted set.

    The population is the reference cardinality, never the predicted one,
    because mIoU answers "of the moments that were actually there, how many did
    the prediction find" — a prediction adding spans the reference does not
    contain does not enlarge the denominator.

    ``undefined`` counts reference spans too narrow to score under a duration
    measure (an instant against anything): they are reported separately rather
    than read as zeros, because a zero would look like localisation failure and
    hide that the comparison was not made.
    """

    value: float | None
    population: int
    per_span: tuple[float, ...]
    worst_span: float | None
    undefined: int
    reference: Fraction

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "population": self.population,
            "per_span": list(self.per_span),
            "worst_span": self.worst_span,
            "undefined": self.undefined,
            "reference": self.reference.to_dict(),
        }


def temporal_miou(
    predicted: IntervalSet, reference: IntervalSet
) -> MiouReport:
    """Mean over the reference spans of each span's IoU against the prediction.

    Each reference span is scored against the union of the predicted spans that
    overlap it — the local view, not the global one. Scoring a span against the
    *whole* predicted set would make an exact multi-span match score 0.5 rather
    than 1.0, because the other reference members inflate the union. The
    multi-span structure is preserved throughout and never flattened to a
    bounding box, so a prediction that bridges two reference spans across their
    gap pays for the gap in every span it bridges.

    Measures: per-moment localisation quality, averaging over the moments the
    reference says exist.
    Does not measure: precision. A predicted span that overlaps no reference
    span at all costs this metric nothing — mIoU is recall-oriented by
    construction, so a retrieval that scatter-guns candidates everywhere still
    scores well on the spans it happens to cover. ``temporal_iou`` is the
    whole-set view that penalises breadth, and B2 reports both for exactly this
    reason.
    Known limitation: an empty reference yields ``value`` of ``None``, because
    mIoU against a ground truth that names no moments is not 0.0 — it is an
    unmeasured property, and reporting a number would let a system with no
    populated ground truth print a clean score. The same holds per span: a
    reference span with no duration is counted in ``undefined`` and excluded
    from the mean, because a moment cannot be localised, only hit or missed,
    and mIoU is not the metric that says which.
    """
    per_span: list[float] = []
    defined: list[float] = []
    undefined = 0
    found = 0
    for span in reference:
        if span.duration <= MERGE_EPS:
            # A reference span with no duration names a moment, not a region:
            # there is nothing to localise, so no IoU is defined for it. Scoring
            # it 0.0 would read a localisation failure on a ground truth that
            # carries no extent, and scoring it by the prediction's overlap would
            # read a success on a moment the prediction may not contain at all.
            undefined += 1
            per_span.append(0.0)
            continue
        local = predicted.intervals_in(span)
        intersection = predicted.intersect(
            IntervalSet.spanning(span.start, span.end)
        ).duration
        local_union = sum(member.duration for member in local)
        union = span.duration + local_union - intersection
        if union <= 0.0:
            undefined += 1
            per_span.append(0.0)
            continue
        value = intersection / union
        per_span.append(value)
        defined.append(value)
        if value > 0.0:
            found += 1
    population = reference.cardinality
    mean = _mean(defined)
    return MiouReport(
        value=mean.value if population > 0 else None,
        population=population,
        per_span=tuple(per_span),
        worst_span=min(per_span) if per_span else None,
        undefined=undefined,
        reference=Fraction(found, population),
    )


# ── B2: known-item lexical retrieval ─────────────────────────────────────────


@dataclass(frozen=True)
class Retrievable:
    """One thing a retrieval query can be built from and scored against.

    ``id`` is a string so a tie-break on id is deterministic across callers;
    a corpus unit maps here with ``retrievable_from_text`` and its index as id.
    """

    id: str
    intervals: IntervalSet
    tokens: frozenset[str]


def retrievable_from_text(
    id: str, start: float, end: float, text: str
) -> Retrievable:
    """Build a ``Retrievable`` from a span and the text over it.

    Tokens come from ``engine.lexical.content_tokens``, the one notion of
    "content word" the corpus layer and the coherence scorer share, so a query
    built here is scored against the same terms an indexed population was built
    from. This module otherwise stays independent of the corpus layer's own
    types, so the benchmark does not inherit a dependency on the thing it
    scores.
    """
    from engine.lexical import content_tokens

    return Retrievable(
        id=id,
        intervals=IntervalSet.spanning(start, end),
        tokens=frozenset(content_tokens(text)),
    )


def rarest_terms(
    bag: frozenset[str] | set[str],
    idf: Mapping[str, float],
    fraction: float = 0.5,
) -> frozenset[str]:
    """The idf-weighted rarest share of a term bag — B2's query construction.

    Rounds *up*, so a five-term bag keeps three and a one-term bag keeps its one
    term. Ties in idf break on the token itself, which makes the result
    independent of set iteration order.

    This function is the circularity B2 documents and does not hide: the query
    is built from the same weighting the retrieval scores with, so recall
    measured on a self-constructed query is evidence about the index's
    machinery, not about its judgement. ``fraction`` of 1.0 gives the full-bag
    variant, which is the leave-one-out spelling that measures restatement
    frequency instead.
    """
    if not 0.0 < fraction <= 1.0:
        raise ValueError(
            f"query term fraction must be in (0.0, 1.0], got {fraction!r}"
        )
    if not bag:
        return frozenset()
    ranked = sorted(bag, key=lambda t: (-idf.get(t, 1.0), t))
    keep = max(1, math.ceil(len(bag) * fraction))
    return frozenset(ranked[:keep])


def rank_candidates(
    query: frozenset[str] | set[str],
    candidates: Sequence[Retrievable],
    idf: Mapping[str, float],
) -> list[tuple[float, Retrievable]]:
    """Score and rank candidates by ``engine.lexical.weighted_containment``.

    Ties break on candidate id, so an all-zero score field — an empty query, or
    a population sharing no terms with it — still yields a stable order rather
    than one that moves with the hash seed.
    """
    scored = [
        (weighted_containment(set(query), set(c.tokens), idf), c)
        for c in candidates
    ]
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return scored


@dataclass(frozen=True)
class RetrievalReport:
    """B2's results, with the circularity visible in the fields.

    ``self_excluded`` records which of the two documented variants ran, because
    a recall of 1.000 in the self-included variant and 0.000 in the
    leave-one-out one are opposite readings of the same machinery, and a report
    that printed one number without saying which would be unusable.
    """

    queries: int
    recall_at_k: Fraction
    mean_rank: Mean
    mean_union_iou: Mean
    mean_miou: Mean
    io_undefined: int
    self_excluded: bool
    k: int

    def to_dict(self) -> dict:
        return {
            "queries": self.queries,
            "recall_at_k": self.recall_at_k.to_dict(),
            "mean_rank": self.mean_rank.to_dict(),
            "mean_union_iou": self.mean_union_iou.to_dict(),
            "mean_miou": self.mean_miou.to_dict(),
            "io_undefined": self.io_undefined,
            "self_excluded": self.self_excluded,
            "k": self.k,
        }


def known_item_retrieval(
    items: Sequence[Retrievable],
    k: int = DEFAULT_K,
    exclude_self: bool = False,
    query_fraction: float = 0.5,
) -> RetrievalReport:
    """Leave-one-out or self-included known-item retrieval over a population.

    Each item in turn supplies a query built by ``rarest_terms``; the candidate
    pool is every item, minus the query item itself when ``exclude_self`` is
    set. Reports recall@K, the mean rank of the query item when it was found,
    and two localisation measures of the union of the top-K spans against the
    query item's own span: set IoU and mIoU.

    Measures: whether the lexical machinery locates a passage from its own rare
    terms, and where the resulting union of candidates falls relative to the
    passage.
    Does not measure: retrieval quality. The query is constructed from the same
    idf the scoring uses, so recall@K with ``exclude_self=False`` is circular by
    construction and a 1.000 is the expected score of a *deterministic* index
    over a population with no restatements — not evidence that retrieval works.
    Set ``exclude_self=True`` for the variant that can fail, which measures how
    often another passage restates this one. Neither variant can see whether a
    retrieved span is semantically right, only whether the terms line up;
    semantic retrieval is unmeasured by design (``docs/selekyt-eval-framework.md``
    §5).
    Known limitation: an item with no content tokens cannot form a query and is
    counted as a miss against the full population, so the denominator stays the
    census size. ``io_undefined`` counts queries that contributed nothing to the
    IoU means — no content terms to form a query, or spans with no duration to
    score — so ``queries`` reconciles as the IoU mean's population plus
    ``io_undefined``, and a reader can see the means were not taken over a
    quietly smaller set.
    """
    if k < 1:
        raise ValueError(f"K must be at least 1, got {k!r}")

    catalog = list(items)
    df: Counter = Counter()
    for item in catalog:
        df.update(item.tokens)
    idf = idf_weights(df, len(catalog))

    hits = 0
    ranks: list[float] = []
    union_ious: list[float] = []
    mious: list[float] = []
    undefined = 0

    for query_item in catalog:
        pool = [c for c in catalog if c.id != query_item.id] if exclude_self \
            else list(catalog)
        query_bag = rarest_terms(query_item.tokens, idf, query_fraction)
        ranked = rank_candidates(query_bag, pool, idf)
        top = ranked[:k]
        if not query_bag:
            # An item with no content terms cannot form a query. It is a miss
            # against the full census rather than a free hit from the id
            # tie-break, which would place the item itself in the top-K at
            # score 0.0 and report a retrieval that never happened. Nothing is
            # scoreable for it, so it is counted as unscored rather than read
            # into the IoU means as either a zero or a perfect self-overlap.
            undefined += 1
            continue
        if exclude_self:
            # The leave-one-out variant: a hit is a *different* passage fully
            # carrying the query's subject, which is what makes its 0/83 on this
            # corpus a reading about restatement frequency rather than a metric
            # that cannot see a match.
            hit = any(score >= RESTATEMENT_THRESHOLD for score, _ in top)
            if hit:
                hits += 1
                for rank, (score, _) in enumerate(ranked, start=1):
                    if score >= RESTATEMENT_THRESHOLD:
                        ranks.append(float(rank))
                        break
        else:
            hit = any(c.id == query_item.id for _, c in top)
            if hit:
                hits += 1
                for rank, (_, candidate) in enumerate(ranked, start=1):
                    if candidate.id == query_item.id:
                        ranks.append(float(rank))
                        break
        predicted = IntervalSet(
            [span for _, candidate in top for span in candidate.intervals]
        )
        report = temporal_overlap(predicted, query_item.intervals)
        if report.iou is None:
            # The union had no duration to score: instants against instants, or
            # an empty query span. Read as unmeasured rather than as a zero that
            # would drag the mean down and look like localisation failure.
            undefined += 1
            continue
        union_ious.append(report.iou)
        miou = temporal_miou(predicted, query_item.intervals).value
        if miou is None:
            undefined += 1
        else:
            mious.append(miou)

    return RetrievalReport(
        queries=len(catalog),
        recall_at_k=Fraction(hits, len(catalog)),
        mean_rank=_mean(ranks),
        mean_union_iou=_mean(union_ious),
        mean_miou=_mean(mious),
        io_undefined=undefined,
        self_excluded=exclude_self,
        k=k,
    )


# ── B3 / B5: grounding conformance to the word grid ──────────────────────────


@dataclass(frozen=True)
class ConformanceReport:
    """B3: how close interval bounds land to transcript word edges.

    ``reference_edges`` is reported alongside ``population`` because a
    conformance share measured against a transcript with two word timestamps and
    one measured against 4725 are not the same claim.
    """

    population: int
    within_tolerance: Fraction
    max_deviation: float | None
    mean_deviation: Mean
    tolerance: float
    reference_edges: int

    def to_dict(self) -> dict:
        return {
            "population": self.population,
            "within_tolerance": self.within_tolerance.to_dict(),
            "max_deviation": self.max_deviation,
            "mean_deviation": self.mean_deviation.to_dict(),
            "tolerance": self.tolerance,
            "reference_edges": self.reference_edges,
        }


def word_edges(words: Iterable[tuple[float, float]]) -> list[float]:
    """The sorted grid of transcript word starts and ends — B3's reference.

    Both edges of every word, because a bound may legitimately sit on either:
    a clip that opens at a word's start and one that closes at a word's end are
    both word-anchored. Duplicates are left in place; they cannot change the
    nearest-edge distance, and removing them would cost a sort the caller did
    not ask for.
    """
    edges: list[float] = []
    for start, end in words:
        edges.append(start)
        edges.append(end)
    return sorted(edges)


def _nearest_distance(t: float, edges: list[float]) -> float:
    i = bisect.bisect_left(edges, t)
    best = math.inf
    if i < len(edges):
        best = min(best, abs(edges[i] - t))
    if i > 0:
        best = min(best, t - edges[i - 1])
    return best


def word_boundary_conformance(
    bounds: Iterable[float],
    reference_edges: Iterable[float],
    tolerance: float = MERGE_EPS,
) -> ConformanceReport:
    """Share of interval bounds that coincide with a transcript word edge.

    For each bound, the distance to the nearest word start or end; the report
    carries the share within ``tolerance`` (``MERGE_EPS`` by default, the same
    tolerance the interval algebra merges on), the mean and the farthest.

    On the corpus this separates two timing provenances: bounds from
    ``word_aligned`` units sit on a word edge to within a microsecond, while
    ``character_proportional`` bounds can be tenths of a second off, and the
    share is what tells a consumer which population they are looking at
    (``docs/selekyt-eval-framework.md`` B3, where one corpus measures 92.7% and
    the other 51.6%).

    Measures: whether the memory and story layers' interval bounds are
    word-anchored.
    Does not measure: correctness. A span can land on the *wrong* word exactly
    and score a perfect 1.000, and the metric is blind to every channel the
    transcript does not carry — speaker identity, on-screen text, slide content
    — so "conforms to words" is not "conforms to the video".
    Known limitation: with no reference grid at all, the share is 0.0 over the
    bound population (nothing is near an edge that does not exist) but the
    deviation statistics are ``None``, because a distance from a grid that is
    not there is not a measurement. With no bounds at all, the share is
    undefined rather than vacuously perfect.
    """
    edges = sorted(reference_edges)
    bound_list = list(bounds)
    population = len(bound_list)
    if population == 0 or not edges:
        return ConformanceReport(
            population=population,
            within_tolerance=Fraction(0, population),
            max_deviation=None,
            mean_deviation=Mean(0.0, 0),
            tolerance=tolerance,
            reference_edges=len(edges),
        )
    deviations = [_nearest_distance(b, edges) for b in bound_list]
    within = sum(1 for d in deviations if d <= tolerance)
    return ConformanceReport(
        population=population,
        within_tolerance=Fraction(within, population),
        max_deviation=max(deviations),
        mean_deviation=_mean(deviations),
        tolerance=tolerance,
        reference_edges=len(edges),
    )


@dataclass(frozen=True)
class SpanDegeneracyReport:
    """B5(b): how many zero-width spans the layers emit.

    Reported separately from conformance because it is a different question —
    not "is the bound on a word" but "is the span a span". The structural
    baseline is zero, since units are built from transcript segments.
    """

    population: int
    degenerate: int
    fraction: Fraction
    threshold: float

    def to_dict(self) -> dict:
        return {
            "population": self.population,
            "degenerate": self.degenerate,
            "fraction": self.fraction.to_dict(),
            "threshold": self.threshold,
        }


def span_degeneracy(
    spans: Iterable[Interval], threshold: float = MERGE_EPS
) -> SpanDegeneracyReport:
    """Count of spans no wider than ``threshold``.

    Uses ``MERGE_EPS`` rather than ``engine.cut_algebra.DEGENERATE_SECONDS``
    (0.01 s) because the question here is what the *memory* algebra will treat
    as an instant — the two constants are three different notions of "zero
    width" living in this codebase, and a consumer that assumed one was the
    other would be wrong on the majority of spans (``docs/memory-failure-modes.md``
    failure mode #16).

    Measures: how often a span carries no duration.
    Does not measure: whether a zero-width span is *correct*. A cut point is a
    legitimate instant; this counts them, it does not judge them. The B5 claim
    is only that a *unit* of zero width is a defect, and that judgement is the
    caller's, not this function's.
    Known limitation: the count alone cannot say which caller handed the span to
    ``covers()`` — the precondition B5 is actually about — so a nonzero count
    here means the hazard is present, not that it has fired.
    """
    population = 0
    degenerate = 0
    for span in spans:
        population += 1
        if span.duration <= threshold:
            degenerate += 1
    return SpanDegeneracyReport(
        population=population,
        degenerate=degenerate,
        fraction=Fraction(degenerate, population),
        threshold=threshold,
    )


# ── B1: round-trip determinism ───────────────────────────────────────────────


@dataclass(frozen=True)
class RoundTripReport:
    """B1: field-level identity between a value and its serialised return trip.

    ``differences`` is bounded by ``MAX_DIFFERENCES`` and sorted, so a
    structurally different input still yields a stable, readable report.
    """

    leaves: int
    identical: Fraction
    differences: tuple[str, ...]
    difference_count: int
    differences_truncated: bool

    @property
    def is_exact(self) -> bool:
        """True only when leaves were compared and every one matched. An empty
        comparison verified nothing, so it is not exact — it is absent."""
        return self.leaves > 0 and self.difference_count == 0

    def to_dict(self) -> dict:
        return {
            "leaves": self.leaves,
            "identical": self.identical.to_dict(),
            "differences": list(self.differences),
            "difference_count": self.difference_count,
            "differences_truncated": self.differences_truncated,
        }


def _round_trip_walk(
    a: Any, b: Any, path: str, leaves: list[int], diffs: list[str]
) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            sub = f"{path}.{key}" if path else str(key)
            if key not in a or key not in b:
                leaves.append(1)
                diffs.append(sub)
            else:
                _round_trip_walk(a[key], b[key], sub, leaves, diffs)
    elif isinstance(a, list) and isinstance(b, list):
        for index in range(max(len(a), len(b))):
            sub = f"{path}[{index}]"
            if index >= len(a) or index >= len(b):
                leaves.append(1)
                diffs.append(sub)
            else:
                _round_trip_walk(a[index], b[index], sub, leaves, diffs)
    elif isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        # A scalar where a container was expected, or the reverse: the shapes
        # disagree at this path, which is one difference, not a walk into a
        # value that has no structure to walk.
        leaves.append(1)
        diffs.append(path)
    else:
        leaves.append(1)
        if a != b:
            diffs.append(path)


def round_trip_identity(before: Any, after: Any) -> RoundTripReport:
    """Leaf-level identity of two JSON-shaped values, with the paths that differ.

    Dicts compare over the union of their keys and lists over the longer length,
    so a field present on only one side is a named difference rather than a
    silent drop. Scalars compare by ``==``, which is exact — the round trip B1
    measures is a dataclass through ``to_dict`` and back, and that is exact, so
    the ``364.84`` / ``364.84000000000003`` straddle shows up here as a
    difference at a named path rather than being absorbed.

    Measures: that serialisation is lossless and the schema round-trips.
    Does not measure: that the value is *correct*. A wrong-but-stable corpus
    passes this benchmark perfectly, which is why B1's limitation in the design
    document is stated as "round-trip identity proves serialisation is
    lossless; it does not prove the corpus is correct, only that it is stable".
    Known limitation: a shape mismatch at a path counts as one leaf, which
    understates the population when a container on one side held many. The
    difference is still named; only the share's denominator is approximate in
    that case.
    """
    leaves: list[int] = []
    diffs: list[str] = []
    _round_trip_walk(before, after, "", leaves, diffs)
    population = sum(leaves)
    diffs.sort()
    count = len(diffs)
    shown = diffs[:MAX_DIFFERENCES]
    return RoundTripReport(
        leaves=population,
        identical=Fraction(population - count, population),
        differences=tuple(shown),
        difference_count=count,
        differences_truncated=count > len(shown),
    )


# ── B4: adversarial regression, scored by severity ───────────────────────────


@dataclass(frozen=True)
class SeverityCase:
    """One adversarial input and whether the current code behaves as documented.

    ``severity`` is a free label rather than an enum so the census can carry
    S1/S2/S3 and a verified-clean row alike; the release gate is applied to
    whichever severities the caller names as blocking.
    """

    id: str
    severity: str
    passed: bool


@dataclass(frozen=True)
class SeverityReport:
    """B4: pass rate per severity, with the release gate stated as a field.

    The gate is not a threshold the caller tunes after seeing results: it is
    "no blocking-severity case may fail", recorded in ``release_blocked``.
    """

    population: int
    overall: Fraction
    by_severity: tuple[tuple[str, Fraction], ...]
    failed: tuple[str, ...]
    release_blocked: bool
    blocking_severity: str

    def to_dict(self) -> dict:
        return {
            "population": self.population,
            "overall": self.overall.to_dict(),
            "by_severity": [(s, f.to_dict()) for s, f in self.by_severity],
            "failed": list(self.failed),
            "release_blocked": self.release_blocked,
            "blocking_severity": self.blocking_severity,
        }


def severity_pass_rate(
    cases: Iterable[SeverityCase],
    blocking_severity: str = RELEASE_BLOCKING_SEVERITY,
) -> SeverityReport:
    """Per-severity pass rate over adversarial cases, with the release gate.

    B4's population is the 20 constructed inputs in
    ``docs/memory-failure-modes.md`` (4 × S1, 10 × S2, 5 × S3, 1 verified-clean);
    the gate is that the S1 subset must be 1.000, because an S1 failure *"can
    put a wrong clip in front of an editor or make the verify stage lie about
    backing"*, while S3 is a design gap tracked rather than gated.

    Measures: that the interval algebra behaves as the failure-mode document
    records on adversarial input.
    Does not measure: that real outputs ever trigger the precondition. Defects
    #3, #5 and #6 all *depend on a sub-``MERGE_EPS`` geometry occurring to
    bite*, and whether that is exotic or common is a different benchmark — B5,
    which measures it on real bounds. A perfect pass rate here is compatible
    with the defects being live on the corpus, and on this corpus they are.
    Known limitation: an empty census yields an overall rate of ``None`` and
    does not block release, because a gate that ran on nothing did not run. A
    population with no blocking-severity cases at all reports that severity's
    rate as ``None`` for the same reason — read the population, not the boolean.
    """
    tallies: dict[str, list[int]] = {}
    failed: list[str] = []
    total_pass = 0
    total = 0
    blocked = False
    for case in cases:
        total += 1
        total_pass += 1 if case.passed else 0
        if not case.passed:
            failed.append(case.id)
            if case.severity == blocking_severity:
                blocked = True
        tallies.setdefault(case.severity, [0, 0])
        tallies[case.severity][0] += 1 if case.passed else 0
        tallies[case.severity][1] += 1
    by_severity = tuple(
        (name, Fraction(tallies[name][0], tallies[name][1]))
        for name in sorted(tallies)
    )
    # The gate's own severity is carried even when no case of it exists, so a
    # census that ran without any S1 input reports the gate as unrun rather
    # than omitting the only row a release decision reads.
    if blocking_severity not in tallies:
        by_severity = tuple(
            sorted(by_severity + ((blocking_severity, Fraction(0, 0)),))
        )
    failed.sort()
    return SeverityReport(
        population=total,
        overall=Fraction(total_pass, total),
        by_severity=by_severity,
        failed=tuple(failed),
        release_blocked=blocked,
        blocking_severity=blocking_severity,
    )


# ── B8: memory reference integrity ───────────────────────────────────────────


@dataclass(frozen=True)
class Referenced:
    """One exported record, the ids it points at, and the joins it claims.

    ``joins`` are ``(table_name, target_id)`` pairs against the caller's index
    tables — the thread/topic/beat memberships B8 checks bidirectionally.
    """

    id: str
    intervals: IntervalSet
    references: tuple[str, ...] = ()
    joins: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ReferenceIntegrity:
    """B8: that exported references resolve, spans stay in range, joins agree.

    Distinct from ``engine.memory.graph``'s ``IntegrityReport``, which checks
    Allen-relation edges against the geometry of the evidence they connect.
    This one checks *exported artefacts* against the index tables and the source
    duration, because a shared memory that dangles a reference is worse than no
    memory.
    """

    population: int
    references: Fraction
    in_range: Fraction
    joins: Fraction
    dangling: tuple[str, ...]
    out_of_range: tuple[str, ...]
    inconsistent: tuple[str, ...]
    source_duration: float | None

    @property
    def is_clean(self) -> bool:
        """True when every check that could run did run and all passed. A
        population of zero is not clean, and a missing ``source_duration``
        leaves the range check unrun rather than passed."""
        if self.population == 0:
            return False
        if not (self.references.is_perfect and self.joins.is_perfect):
            return False
        return self.in_range.is_perfect

    def to_dict(self) -> dict:
        return {
            "population": self.population,
            "references": self.references.to_dict(),
            "in_range": self.in_range.to_dict(),
            "joins": self.joins.to_dict(),
            "dangling": list(self.dangling),
            "out_of_range": list(self.out_of_range),
            "inconsistent": list(self.inconsistent),
            "source_duration": self.source_duration,
        }


def reference_integrity(
    items: Iterable[Referenced],
    source_duration: float | None = None,
    index_tables: Mapping[str, Mapping[str, set[str]]] | None = None,
) -> ReferenceIntegrity:
    """Resolve every reference, range-check every span, cross-check every join.

    ``index_tables`` maps a table name to a target id to the set of record ids
    that table says belong to it; a join is consistent only if the target exists
    *and* the table names the record back. A one-directional index is the
    failure this catches, and it is the one a writer that appended membership
    only on one side would produce.

    Measures: structural integrity of an exported memory — no dangling
    reference, no span outside the source, no join the index does not echo.
    Does not measure: truth. A reference can resolve to the *wrong* record and
    pass every check here, and a span can sit inside the source duration and
    still be a claim the source does not support. This catches corruption, not
    hallucination; the latter needs populated evidence or a human (B11).
    Known limitation: passing ``source_duration=None`` leaves the range check
    unmeasured — ``in_range`` is ``None`` rather than 1.000 — because a range
    check against an unknown duration is a check that did not run, and reading
    it as passed would let an exporter that dropped the field ship unverified
    spans.
    """
    records = list(items)
    known = {r.id for r in records}
    tables = index_tables or {}

    reference_total = 0
    reference_ok = 0
    dangling: list[str] = []
    range_total = 0
    range_ok = 0
    out_of_range: list[str] = []
    join_total = 0
    join_ok = 0
    inconsistent: list[str] = []

    for record in records:
        for target in record.references:
            reference_total += 1
            if target in known:
                reference_ok += 1
            else:
                dangling.append(f"{record.id}->{target}")
        if source_duration is not None:
            for span in record.intervals:
                range_total += 1
                if span.start < -MERGE_EPS or span.end > source_duration + MERGE_EPS:
                    out_of_range.append(
                        f"{record.id}:{span.start:g}-{span.end:g}"
                    )
                else:
                    range_ok += 1
        for table_name, target in record.joins:
            join_total += 1
            members = tables.get(table_name, {}).get(target)
            if members is not None and record.id in members:
                join_ok += 1
            else:
                inconsistent.append(f"{record.id}@{table_name}:{target}")

    dangling.sort()
    out_of_range.sort()
    inconsistent.sort()
    return ReferenceIntegrity(
        population=len(records),
        references=Fraction(reference_ok, reference_total),
        in_range=Fraction(range_ok, range_total)
        if source_duration is not None else Fraction(0, 0),
        joins=Fraction(join_ok, join_total),
        dangling=tuple(dangling),
        out_of_range=tuple(out_of_range),
        inconsistent=tuple(inconsistent),
        source_duration=source_duration,
    )


# ── B9: perception -> memory ingest coverage ─────────────────────────────────


@dataclass(frozen=True)
class Signal:
    """One perception signal and whether it reached memory, with its size.

    ``time_base`` is carried because presence is not fidelity: a signal that
    reaches disk in the wrong units or on the wrong time base passes a presence
    check and is still unusable, and recording the time base is what lets a
    reviewer see that without a second benchmark.
    """

    name: str
    present: bool
    record_count: int = 0
    time_base: str = ""


@dataclass(frozen=True)
class SignalRow:
    name: str
    present: bool
    record_count: int
    time_base: str
    in_input: bool


@dataclass(frozen=True)
class CoverageReport:
    """B9: which of the documented perception signals reached memory.

    The denominator is the inventory's list of six, so a caller who ingests
    three signals scores 3/6 and cannot report 3/3 by forgetting the other half.
    """

    population: int
    present: Fraction
    per_signal: tuple[SignalRow, ...]
    absent: tuple[str, ...]
    unexpected: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "population": self.population,
            "present": self.present.to_dict(),
            "per_signal": [row.__dict__ for row in self.per_signal],
            "absent": list(self.absent),
            "unexpected": list(self.unexpected),
        }


def ingest_coverage(
    signals: Iterable[Signal],
    expected: Sequence[str] = DOCUMENTED_PERCEPTION_SIGNALS,
) -> CoverageReport:
    """Share of the documented perception signals present in memory.

    A signal counts as present only when it carried records. A signal that
    reached the store with zero rows has a schema and no data, and scoring it
    present would let an adapter that opened a table and wrote nothing report
    the same coverage as one that ingested the channel — the record count is
    reported alongside so a reader can see which case it was.

    Measures: that the six perception channels reach memory at all.
    Does not measure: fidelity. A signal in the wrong units, on the wrong time
    base, or with its confidence stripped passes this benchmark.
    Known limitation: the inventory's headline is that two of the six reach
    disk, but one of the two — the coverage entry — reaches it as aggregates
    only, with no regions behind it, and this metric counts a signal only when
    it carried records. The measured floor under this metric is therefore one of
    six, not two, and that is the number to beat; the gap between the two is
    itself the measurement, and it is what the record-count column exists to
    expose. The population of six is asserted against the inventory's design
    rather than against any external standard, and a signal the inventory does
    not name lands in ``unexpected`` rather than inflating that denominator,
    because an undocumented channel reaching disk is a schema change the
    inventory should record before the benchmark counts it.
    """
    by_name: dict[str, Signal] = {}
    for signal in signals:
        by_name[signal.name] = signal
    expected_list = list(expected)

    rows: list[SignalRow] = []
    present = 0
    for name in sorted(expected_list):
        signal = by_name.get(name)
        if signal is None:
            rows.append(SignalRow(name, False, 0, "", in_input=False))
            continue
        effective = signal.present and signal.record_count > 0
        present += 1 if effective else 0
        rows.append(
            SignalRow(
                name,
                effective,
                signal.record_count,
                signal.time_base,
                in_input=True,
            )
        )
    unexpected = sorted(set(by_name) - set(expected_list))
    return CoverageReport(
        population=len(expected_list),
        present=Fraction(present, len(expected_list)),
        per_signal=tuple(rows),
        absent=tuple(row.name for row in rows if not row.present),
        unexpected=tuple(unexpected),
    )


# ── the benchmark index, including the three the framework declines ──────────


@dataclass(frozen=True)
class BenchmarkStatus:
    """One benchmark's state, stated as data.

    ``state`` is one of:

    - ``"implementable"`` — this module scores it; ``metrics`` names the
      functions.
    - ``"exists_elsewhere"`` — already a committed test, cited rather than
      reinvented (B6, B7).
    - ``"declined"`` — the framework refuses to state it. ``blocker`` names what
      stops it and ``computable_when`` what would unblock it, because a refused
      benchmark that does not say what would make it computable is a gap
      disguised as a decision.
    """

    benchmark_id: str
    name: str
    state: str
    metrics: tuple[str, ...] = ()
    citation: str = ""
    blocker: str = ""
    computable_when: str = ""


BENCHMARKS: tuple[BenchmarkStatus, ...] = (
    BenchmarkStatus(
        "B1", "Memory round-trip determinism", "implementable",
        metrics=("round_trip_identity",),
        citation="docs/selekyt-eval-framework.md B1",
    ),
    BenchmarkStatus(
        "B2", "Known-item lexical retrieval", "implementable",
        metrics=("known_item_retrieval", "temporal_iou", "temporal_miou"),
        citation="docs/selekyt-eval-framework.md B2",
    ),
    BenchmarkStatus(
        "B3", "Grounding word-boundary conformance", "implementable",
        metrics=("word_boundary_conformance", "word_edges"),
        citation="docs/selekyt-eval-framework.md B3",
    ),
    BenchmarkStatus(
        "B4", "Interval-algebra adversarial regression", "implementable",
        metrics=("severity_pass_rate",),
        citation="docs/memory-failure-modes.md",
    ),
    BenchmarkStatus(
        "B5", "Real-output precondition rate", "implementable",
        metrics=("word_boundary_conformance", "span_degeneracy"),
        citation="docs/selekyt-eval-framework.md B5",
    ),
    BenchmarkStatus(
        "B6", "Story-graph structural bounds at scale", "exists_elsewhere",
        citation="tests/test_story_bench.py; docs/story-memory-design.md:370-411",
    ),
    BenchmarkStatus(
        "B7", "Story -> edit-plan behaviour coverage", "exists_elsewhere",
        citation="tests/test_story_memory.py; docs/story-memory-design.md:337-361",
    ),
    BenchmarkStatus(
        "B8", "Memory reference integrity", "implementable",
        metrics=("reference_integrity",),
        citation="docs/selekyt-eval-framework.md B8",
    ),
    BenchmarkStatus(
        "B9", "Perception -> memory ingest coverage", "implementable",
        metrics=("ingest_coverage", "DOCUMENTED_PERCEPTION_SIGNALS"),
        citation="docs/memory-perception-inventory.md",
    ),
    BenchmarkStatus(
        "B10", "Retrieval at hour scale", "declined",
        blocker=(
            "No corpus source reaches one hour (longest 55.5 min) and only "
            "26.4 min of media survive on disk; no ground truth names which "
            "moments are right, and no index module exists."
        ),
        computable_when=(
            "One rights-clear 60-180 min source with media committed, roughly "
            "1 GB of free disk, ~20 human-labelled editorial moments, and an "
            "index module. The disk is feasible today; the labels and the index "
            "are the real blockers."
        ),
    ),
    BenchmarkStatus(
        "B11", "Editorial-reasoning quality vs human judgement", "declined",
        blocker=(
            "No human has judged any corpus clip; the designed protocol is "
            "DESIGNED, NOT RUN, with founder approval for external contact not "
            "given, and mechanical and editorial scores can never be combined."
        ),
        computable_when=(
            "Founder approval for external contact, then a participant. The "
            "instrument exists and is executable the moment one does; until "
            "then every editorial quality claim is UNCHECKED, not low."
        ),
    ),
    BenchmarkStatus(
        "B12", "Non-roman-script retrieval and grounding", "declined",
        blocker=(
            "Zero usable non-roman-script units with word timestamps exist in a "
            "shipping run: telugu-launch shipped 0 clips and telugu-tutorial no "
            "longer ships; te measures 91.54% WER on SLR66 and hi/hu have no "
            "measured error floor at all."
        ),
        computable_when=(
            "One non-roman-script source whose transcript clears the 0.75 "
            "confidence floor with per-word timestamps, which needs a better "
            "model than small or the language IR that canonicalises script "
            "before indexing."
        ),
    ),
)


def benchmark(benchmark_id: str) -> BenchmarkStatus | None:
    """The status of one benchmark, or ``None`` for an id the framework does not
    carry — a caller asking for a benchmark that is not in the index gets a
    loud ``None`` rather than a freshly invented metric."""
    for status in BENCHMARKS:
        if status.benchmark_id == benchmark_id:
            return status
    return None


def declined_benchmarks() -> tuple[BenchmarkStatus, ...]:
    """Every benchmark the framework refuses to state, with its blocker.

    Exposed as a query because the governing rule is that a framework full of
    unmeasurable metrics is worse than one that names its gaps, and the refusals
    are the gaps, stated.
    """
    return tuple(s for s in BENCHMARKS if s.state == "declined")
