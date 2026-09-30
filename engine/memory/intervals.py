"""Time as a first-class value: intervals, interval sets, Allen relations.

Why this module exists
----------------------

Every temporal question an editor asks about an hour-scale source — "where do
they talk about the budget", "where is the second speaker on screen", "where is
there no speech" — has an answer that is a *set* of spans, not one span. A
single ``(start, end)`` pair cannot express "four times across an hour", and
code that forces it to will silently return the first or the longest span and
call it the answer. The retrieval and grounding layers cannot be built on a
type that cannot hold their result.

The merge rule and the closed-interval convention are not new here:
``engine.cut_algebra.merge_intervals`` and ``resolve_for_export`` already use
them, proven on the corpus's real cut lists. This module makes the same rule a
first-class type with set algebra, so the grounding layer can intersect
"speech" with "face present" and subtract "already used" without every caller
re-implementing a two-pointer walk. Where the rule differs from
``cut_algebra`` it is documented at the point of difference.

Semantics
---------

Intervals are **closed** ``[start, end]`` and in source seconds. Time is
continuous, so a boundary belongs to both intervals it separates is wrong for
cuts but right for evidence: "speech ends exactly when the face appears" is one
event, not two, and an editor who clicks to that point should land on it. Zero-width
intervals are legal and represent an instantaneous event (a cut point, a single
frame, a beat); the Allen relations degrade to point comparisons for them.

An ``IntervalSet`` is **normalised at construction**: sorted, disjoint, and with
gaps larger than ``MERGE_EPS`` between members. The invariant is enforced by the
constructor, never trusted from the caller, because every set-algorithm below is
only correct on a normalised operand and a caller-built sorted-disjoint list is
exactly the thing that drifts. Degenerate input yields a valid empty set, never
``None`` and never an exception: an empty answer is data, it says "nowhere in
this source", and it is reported rather than fabricated (see ``engine.corpus``,
which states the same principle for its own absent signals).

Float discipline
----------------

Second-scale arithmetic on float64 produces noise at the 1e-14 level, which the
corpus has already had to absorb once: ``engine.edit_plan._word_aligned`` treats
a bound within ``1e-6`` of a word's end as on the boundary because a
``364.84`` sentence bound met a word ending at ``364.84000000000003`` and a
``2.8e-14`` straddle moved a real cut point. ``MERGE_EPS`` sits above that noise
and below any gap an editor would ever care about, and every comparison here
goes through it. Non-finite values (NaN, inf) are clamped at construction — a
NaN interval is a propagating poison that makes sorting undefined and silently
reorders results, so it becomes a zero-width interval at 0.0 rather than
reaching any of the algorithms below.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

SCHEMA_VERSION = "1.0"

#: Above the 2.8e-14 s float straddle documented in ``engine.edit_plan``, below
#: any gap an editor would consider meaningful. Two intervals separated by less
#: than this are one interval.
MERGE_EPS = 1e-6

#: The thirteen Allen interval relations and their inverses. A relation name is
#: data, not a comment: it is what an ``Evidence`` link carries when it asserts
#: that one piece of evidence *precedes*, *contains* or *overlaps* another, so a
#: reader of the evidence graph can ask "what is *during* this span" and get a
#: typed answer rather than re-deriving it from coordinates.
ALLEN_RELATIONS: tuple[str, ...] = (
    "before", "meets", "overlaps", "starts", "during", "finishes", "equals",
    "finished_by", "contains", "started_by", "overlapped_by", "met_by", "after",
)

_CONVERSE: dict[str, str] = {
    "before": "after", "after": "before",
    "meets": "met_by", "met_by": "meets",
    "overlaps": "overlapped_by", "overlapped_by": "overlaps",
    "starts": "started_by", "started_by": "starts",
    "during": "contains", "contains": "during",
    "finishes": "finished_by", "finished_by": "finishes",
    "equals": "equals",
}


def converse(relation_name: str) -> str:
    """The Allen relation seen from the other side: ``converse("before")`` is
    ``"after"``. Raises ``ValueError`` for a name this module does not emit, so
    a typo in an evidence link fails loudly rather than becoming a silent
    unmatchable string."""
    try:
        return _CONVERSE[relation_name]
    except KeyError:
        raise ValueError(f"not an Allen relation: {relation_name!r}") from None


def _finite(value: float, default: float = 0.0) -> float:
    """Non-finite -> default. NaN must never reach a sort or a comparison here:
    every NaN comparison is False, so a NaN interval silently corrupts the
    normalisation invariant instead of raising."""
    return value if math.isfinite(value) else default


@dataclass(frozen=True, order=True)
class Interval:
    """A closed ``[start, end]`` span in source seconds.

    Frozen and ordered so intervals are hashable, sortable and usable as dict
    keys — the grounding layer builds lookup tables keyed on spans.
    """

    start: float
    end: float

    def __post_init__(self) -> None:
        # Clamp poison before it reaches any comparison, then repair inversion.
        # An inverted interval is a caller error, not a crash: the spans arriving
        # here come from ASR word timings, detector boxes and user selections,
        # and a decoder that hands back end < start is a real failure mode that
        # should not abort an hour-scale run.
        start = _finite(self.start)
        end = _finite(self.end)
        if end < start:
            start, end = end, start
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def is_instant(self) -> bool:
        """A zero-width span: a cut point, a single frame, a beat. Legal, and
        common — half the interesting moments in an edit are instants."""
        return self.duration <= MERGE_EPS

    def contains(self, t: float) -> bool:
        """Is ``t`` inside this span (closed)?"""
        return self.start - MERGE_EPS <= t <= self.end + MERGE_EPS

    def overlaps(self, other: Interval) -> bool:
        """Do the two spans share any point, including a shared boundary?"""
        return self.start <= other.end + MERGE_EPS and other.start <= self.end + MERGE_EPS

    def intersect(self, other: Interval) -> Interval | None:
        """The shared span, or ``None`` if they are disjoint."""
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        return Interval(start, end) if start <= end + MERGE_EPS else None

    def union(self, other: Interval) -> Interval:
        """The span covering both. Meaningful only when they overlap or meet;
        a caller taking the union of distant spans gets the span that contains
        the gap between them, which is what they asked for."""
        return Interval(min(self.start, other.start), max(self.end, other.end))

    def expand(self, pad: float, bounds: Interval | None = None) -> Interval | None:
        """Grow (positive ``pad``) or shrink (negative) about the midpoint.

        Shrinking past zero width yields the midpoint as an instant rather than
        an inverted interval. ``bounds`` intersects the result, because an expanded
        span that runs past the end of the source is not a claim about the
        source — and a span lying wholly outside the bounds has no expansion
        inside the world, so it is ``None``. Clamping the two endpoints
        independently instead manufactures support: ``Interval(200, 210)``
        against bounds ``(0, 100)`` clamped to ``(100, 200)`` and came back as a
        hundred seconds of coverage the source never had.
        """
        if pad == 0.0:
            out = self
        else:
            mid = (self.start + self.end) / 2.0
            half = max(self.duration / 2.0 + pad, 0.0)
            out = Interval(mid - half, mid + half)
        return out if bounds is None else out.intersect(bounds)

    def shift(self, dt: float) -> Interval:
        """Translate in time. Used by the export layer to map a source span onto
        output time; not clamped, because the caller knows the target grid."""
        return Interval(self.start + dt, self.end + dt)

    def relation(self, other: Interval) -> str:
        """The Allen relation of ``self`` to ``other``.

        Touching boundaries are ``meets`` / ``met_by`` rather than a degenerate
        ``overlaps`` with zero width, because "the speech ends exactly when the
        slide appears" is the relation an editor acts on and a zero-width
        overlap would hide it inside a comparison that reads as "partial".

        Boundary tests use the same ``MERGE_EPS`` tolerance as ``_normalise``,
        which merges members separated by no more than it. Exact ``==`` here
        would disagree with that merger: ``Interval(1, 2)`` against
        ``Interval(2 + 1e-9, 3)`` reported ``before`` while ``overlaps`` said
        they shared a point and ``IntervalSet`` merged them into one span —
        three layers of the system with three different answers about whether a
        cut point existed.
        """
        same_start = abs(self.start - other.start) <= MERGE_EPS
        same_end = abs(self.end - other.end) <= MERGE_EPS
        if same_start and same_end:
            return "equals"
        if self.end <= other.start + MERGE_EPS:
            return "meets" if other.start <= self.end + MERGE_EPS else "before"
        if other.end <= self.start + MERGE_EPS:
            return "met_by" if self.start <= other.end + MERGE_EPS else "after"
        if self.start >= other.start - MERGE_EPS and self.end <= other.end + MERGE_EPS:
            if same_start:
                return "starts"
            if same_end:
                return "finishes"
            return "during"
        if self.start <= other.start + MERGE_EPS and self.end >= other.end - MERGE_EPS:
            if same_start:
                return "started_by"
            if same_end:
                return "finished_by"
            return "contains"
        if self.start < other.start:
            return "overlaps"
        return "overlapped_by"

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end}

    @staticmethod
    def from_dict(d: dict) -> Interval:
        return Interval(d["start"], d["end"])


def allen(a: Interval, b: Interval) -> str:
    """Alias for ``a.relation(b)``; exported so evidence links read as
    ``allen(e1_span, e2_span)`` where the relation is the point of the line."""
    return a.relation(b)


@dataclass
class IntervalSet:
    """A variable-cardinality set of intervals.

    This is the answer type for every temporal question in the system. "Where
    in this 90-minute source is the budget discussed" is an ``IntervalSet`` with
    four members and a cardinality of four; the caller who asked for a single
    span gets the type telling them the answer is not singular.

    Normalised at construction: sorted by start, disjoint, separated by gaps
    above ``MERGE_EPS``. Every operation below assumes that invariant and
    preserves it.
    """

    intervals: list[Interval] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.intervals = _normalise(self.intervals)

    # ── construction ───────────────────────────────────────────────────────

    @classmethod
    def empty(cls) -> IntervalSet:
        """The valid empty answer: "nowhere in this source"."""
        return cls(intervals=[])

    @classmethod
    def of(cls, *spans: Interval | tuple[float, float]) -> IntervalSet:
        """Build from raw spans or ``(start, end)`` pairs, unsorted, possibly
        overlapping — the constructor normalises."""
        return cls(intervals=[
            s if isinstance(s, Interval) else Interval(s[0], s[1]) for s in spans
        ])

    @classmethod
    def from_pairs(cls, pairs: list[tuple[float, float]]) -> IntervalSet:
        return cls(intervals=[Interval(a, b) for a, b in pairs])

    @classmethod
    def spanning(cls, start: float, end: float) -> IntervalSet:
        """The single-span set covering ``[start, end]`` — the shape of a whole
        source, a whole chapter or a whole clip."""
        return cls(intervals=[Interval(start, end)])

    @property
    def cardinality(self) -> int:
        """The number of distinct spans. This is the field that makes the type
        variable-cardinality, and it is the number an evaluation harness
        compares against a ground-truth span count."""
        return len(self.intervals)

    @property
    def duration(self) -> float:
        """Total covered time, excluding the gaps. Distinct from
        ``end - start`` of the bounding box, which counts the gaps in."""
        return sum(i.duration for i in self.intervals)

    @property
    def is_empty(self) -> bool:
        return not self.intervals

    @property
    def bounds(self) -> Interval | None:
        """The bounding box, including every gap inside it. ``None`` for an
        empty set — reported, not fabricated as a zero span."""
        if not self.intervals:
            return None
        return Interval(self.intervals[0].start, self.intervals[-1].end)

    # ── query ──────────────────────────────────────────────────────────────

    def at(self, t: float) -> Interval | None:
        """The span containing time ``t``, or ``None``. O(log N): the set is
        sorted, so this is a bisect on starts, not a scan.

        The bisect keys on ``start`` alone, deliberately. A query instant
        ``Interval(t, t)`` ordered by the full ``(start, end)`` tuple sorts
        *before* a member that starts at ``t`` (its ``end`` is greater), and the
        member is skipped — so ``at(5.0)`` would miss ``[5.0, 8.0]`` and report
        no evidence at the boundary it is standing on.
        """
        if not self.intervals:
            return None
        i = bisect.bisect_right(self.intervals, t, key=lambda iv: iv.start) - 1
        if i < 0:
            return None
        cand = self.intervals[i]
        return cand if cand.contains(t) else None

    def contains_time(self, t: float) -> bool:
        return self.at(t) is not None

    def nearest(self, t: float) -> Interval | None:
        """The closest span to ``t`` by edge distance. This is the grounding
        operation: given a time a reasoner *believes* something happened, find
        the span where the evidence actually is, even if the belief was off."""
        if not self.intervals:
            return None
        i = bisect.bisect_right(self.intervals, t, key=lambda iv: iv.start) - 1
        if i < 0:
            return self.intervals[0]
        if i >= len(self.intervals) - 1:
            return self.intervals[-1]
        lo, hi = self.intervals[i], self.intervals[i + 1]
        if lo.contains(t):
            return lo
        if hi.contains(t):
            return hi
        return lo if (t - lo.end) <= (hi.start - t) else hi

    def overlaps(self, span: Interval | IntervalSet) -> bool:
        """Does any member share points with ``span``?

        ``span`` may be a single interval or another set. For two sets the test
        is one merge pass over both, O(N + M) rather than a pairwise comparison,
        because both operands are already normalised and sorted — the joins in
        the memory stack are set against set, which is why the wider spelling
        exists.
        """
        if isinstance(span, IntervalSet):
            return _sorted_overlap(self.intervals, span.intervals)
        return any(i.overlaps(span) for i in self.intervals_in(span))

    def intervals_in(self, span: Interval) -> list[Interval]:
        """Members that overlap ``span``, in order. Uses the sorted invariant to
        bound the scan rather than walking the whole set."""
        if not self.intervals:
            return []
        # Keyed on start for the same reason as ``at``: a query instant at the
        # member's own left edge must not sort ahead of it.
        i = bisect.bisect_right(self.intervals, span.start, key=lambda iv: iv.start) - 1
        out: list[Interval] = []
        for iv in self.intervals[max(i, 0):]:
            if iv.start > span.end + MERGE_EPS:
                break
            if iv.overlaps(span):
                out.append(iv)
        return out

    def covers(self, span: Interval) -> bool:
        """Is ``span`` entirely inside the set? The verify stage asks this: "is
        the whole proposed clip backed by evidence, or does part of it sit in a
        gap I have no support for?" """
        if not self.intervals:
            return False
        sliced = self.slice(span.start, span.end)
        if not sliced.intervals:
            # The empty slice is the interesting case: an instant probe has
            # duration 0, and a width-only check comparing it against an empty
            # slice's width of 0 is satisfied by ``0 <= 0 + MERGE_EPS``. That
            # read a point in a gap as fully supported, so the emptiness has to
            # be tested on its own.
            return False
        first, last = sliced.intervals[0], sliced.intervals[-1]
        if first.start > span.start + MERGE_EPS or last.end < span.end - MERGE_EPS:
            # The endpoints: if no member reaches ``span.start`` or ``span.end``,
            # part of the span is unsupported whatever the widths add up to.
            return False
        return span.duration <= sliced.duration + MERGE_EPS

    def slice(self, start: float, end: float) -> IntervalSet:
        """The set clamped to ``[start, end]`` — a window query. Members
        straddling a boundary are clipped to it, not dropped."""
        return self.intersect(IntervalSet.spanning(start, end))

    def gaps(self, bounds: Interval) -> IntervalSet:
        """The complement within ``bounds``: where nothing in this set happens.
        Inverting the question is how dead air, unsupported spans and unsearched
        regions are found, and it has to be a first-class operation because a
        gap is where the interesting failure usually is."""
        return self.complement(bounds)

    # ── set algebra ─────────────────────────────────────────────────────────

    def union(self, other: IntervalSet) -> IntervalSet:
        """Spans in either set. O(N + M): one merge walk over two sorted
        sequences, not a pairwise comparison."""
        out: list[Interval] = []
        i = j = 0
        a, b = self.intervals, other.intervals
        while i < len(a) and j < len(b):
            if a[i] <= b[j]:
                out.append(a[i]); i += 1
            else:
                out.append(b[j]); j += 1
        out.extend(a[i:]); out.extend(b[j:])
        return IntervalSet(out)

    def intersect(self, other: IntervalSet) -> IntervalSet:
        """Spans in both sets — the multi-channel AND: speech *and* face present,
        topic mentioned *and* on screen. O(N + M)."""
        out: list[Interval] = []
        i = j = 0
        a, b = self.intervals, other.intervals
        while i < len(a) and j < len(b):
            hit = a[i].intersect(b[j])
            if hit is not None:
                out.append(hit)
            if a[i].end <= b[j].end:
                i += 1
            else:
                j += 1
        return IntervalSet(out)

    def subtract(self, other: IntervalSet) -> IntervalSet:
        """Spans in this set but not the other — "talks about the budget but is
        not already used in the cut". O(N + M).

        Differs from ``engine.cut_algebra.subtract_intervals`` only in operating
        on typed intervals; the clip rule is the same, and the two are kept
        consistent deliberately so a cut list and an evidence set never disagree
        about whether a span is in or out.

        Linear in the interval counts: both operands are sorted and disjoint, so
        the outer pointer over ``other`` only advances and the inner walk breaks
        at the first interval starting past the current span.
        """
        out: list[Interval] = []
        j = 0
        for a in self.intervals:
            # Advance past other-intervals that cannot touch `a`; they are sorted
            # and disjoint, so this pointer never moves backwards.
            while j < len(other.intervals) and other.intervals[j].end < a.start - MERGE_EPS:
                j += 1
            cur = a
            k = j
            while k < len(other.intervals) and other.intervals[k].start <= cur.end + MERGE_EPS:
                cut = other.intervals[k]
                if cut.start > cur.start + MERGE_EPS:
                    out.append(Interval(cur.start, cut.start))
                cur = Interval(cut.end, cur.end) if cut.end < cur.end else None
                if cur is None:
                    break
                k += 1
            if cur is not None:
                out.append(cur)
        return IntervalSet(out)

    def complement(self, bounds: Interval) -> IntervalSet:
        """Everything in ``bounds`` not in this set."""
        return IntervalSet.spanning(bounds.start, bounds.end).subtract(self)

    def expand(self, pad: float, bounds: Interval | None = None) -> IntervalSet:
        """Grow or shrink every member. Expansion can make neighbours overlap,
        so the result is re-normalised — the constructor invariant, not a
        caller's discipline. Members lying wholly outside ``bounds`` contribute
        nothing, since ``Interval.expand`` reports their absence as ``None``
        rather than inventing a clamped span for them."""
        grown = (m.expand(pad, bounds) for m in self.intervals)
        return IntervalSet([g for g in grown if g is not None])

    # ── serialisation ───────────────────────────────────────────────────────

    def to_list(self) -> list[dict]:
        return [i.to_dict() for i in self.intervals]

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "cardinality": self.cardinality,
            "duration": self.duration,
            "intervals": self.to_list(),
        }

    @staticmethod
    def from_dict(d: dict) -> IntervalSet:
        return IntervalSet([Interval.from_dict(i) for i in d.get("intervals", [])])

    def __iter__(self):
        return iter(self.intervals)

    def __len__(self) -> int:
        return len(self.intervals)


def _sorted_overlap(a: list[Interval], b: list[Interval]) -> bool:
    """Do two sorted-disjoint sequences share any point? One merge pass, stops
    at the first hit — the boolean form of ``IntervalSet.intersect``, for the
    callers that only need to know *whether* two items relate."""
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i].overlaps(b[j]):
            return True
        # Whichever span ends first cannot meet anything later in the other
        # sequence, which is sorted — so only that pointer may advance.
        if a[i].end <= b[j].end:
            i += 1
        else:
            j += 1
    return False


def _normalise(spans: list[Interval]) -> list[Interval]:
    """Sort, drop empties, merge overlaps and near-touches.

    The one place the sorted-disjoint invariant is established, so it is the one
    place that has to be right: every algorithm above is only correct on its
    output. Mirrors ``engine.cut_algebra.merge_intervals`` exactly in rule
    (closed intervals, merge on touch) and differs only in the ``MERGE_EPS``
    tolerance, which cut_algebra applies through its ``gap`` / ``coalesce``
    arguments on a per-call basis instead of once here.
    """
    if not spans:
        return []
    kept = sorted(spans)
    out: list[Interval] = [kept[0]]
    for iv in kept[1:]:
        last = out[-1]
        if iv.start <= last.end + MERGE_EPS:
            out[-1] = Interval(last.start, max(last.end, iv.end))
        else:
            out.append(iv)
    return out
