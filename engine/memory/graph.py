"""The evidence graph — a traceability index over a memory's items.

Why this module exists
----------------------

``Evidence`` carries ``links``: typed edges, named by Allen relation, to other
pieces of evidence. Those edges are what make a conclusion *checkable* — an
editor who reads "the second speaker is on screen for the whole middle chapter"
can follow an edge to the face track that says so, and from there to the frame
samples the track was built on.

But ``links`` live on the items, and a link is only a name. Nothing in
``evidence.py`` answers "does the thing I am pointing at still exist", "does the
relation I asserted match the two items' actual spans", or "if I follow these
edges, do I arrive at something observed or do I walk in a circle". A reasoning
layer that trusted the labels would inherit every mistake an adapter made when
it wrote them, and an editor would be shown a chain that looks grounded and is
not.

``EvidenceGraph`` is the layer that checks them. It is a *read index* over an
``EvidenceSet``: it takes the items' own declared edges, derives the converse at
query time rather than storing a second spelling, verifies each edge against the
geometry of the two items it connects, and then answers the two questions the
evidence stack exists to serve:

- ``trace(conclusion)`` — walk from a conclusion back to the observations it
  rests on, and say where the walk stopped.
- ``supports(observation)`` — walk forward from an observation to every
  conclusion that cites it, which is what makes a replaced instrument
  actionable: the digest changes, the memory is rebuilt, and this query names
  what is now unsupported.

What it is not
--------------

It is not a store. It holds no file, writes nothing, and is re-derived from the
set on every load — only the ``IntegrityReport`` is worth persisting, and only
as a diagnostic. It is not a query language or a traversal engine: there is no
path matching, no rule application, no inference. It does not introduce a new
modality, confidence model, or weighting; and it carries no semantic edges —
"same speaker", "same topic", "elaborates" are not Allen relations and a graph
that mixed them in would make a temporal check read as a semantic claim. Adding
them is a separate layer's decision to make, with its own evidence contract.

Direction
---------

An edge points from the *derived* item to the item it was derived *from*: a
conclusion links to the observation it was concluded from, not the other way
round. This matches ``engine.corpus``, which attaches ``reason`` and ``evidence``
to a conclusion rather than to its inputs, and it is what makes ``trace`` a walk
outward from the conclusion to the evidence. An inferred item is marked
``grounded=False``; a directly observed one is ``grounded=True``, and that flag —
not a special edge type — is what terminates a trace.

Purity
------

Deterministic and side-effect free: identical input yields an identical index,
byte-identical ``IntegrityReport``, and identical traces. No wall clock, no
network, no inference, no file access. Iteration order is the items' own
declaration order, so a re-run in a fresh interpreter reproduces every result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.memory.evidence import Evidence, EvidenceLink, EvidenceSet, Modality, Source
from engine.memory.intervals import MERGE_EPS, IntervalSet, converse as converse_name

# Thirteen Allen relations coarsened into six classes a geometry check can
# actually witness. The coarse classes are what make the check possible at set
# scale: "is A before B" over two multi-span items has an O(1) answer, while
# "is A *strictly* before B in the universal reading does not even hold for a
# single pair of overlapping items and is the wrong question.
_FINE_CLASS: dict[str, str] = {
    "before": "precedes",
    "meets": "precedes",
    "after": "preceded_by",
    "met_by": "preceded_by",
    "overlaps": "partial",
    "overlapped_by": "partial_reverse",
    "starts": "inside",
    "during": "inside",
    "finishes": "inside",
    "equals": "inside",
    "started_by": "contains",
    "contains": "contains",
    "finished_by": "contains",
}


class GraphIntegrityError(ValueError):
    """Raised when ``strict=True`` and the declared edges do not hold.

    The message names every failing category, because an adapter that wrote one
    bad edge usually wrote several, and fixing them one build at a time is a
    slow way to find that out.
    """


@dataclass(frozen=True)
class Edge:
    """One declared edge, with the facts the graph derived about it.

    ``converse`` is the reverse relation, derived from ``relation`` rather than
    read off a second declared edge — the graph never carries two spellings of
    one relation. ``declared`` is True for the direction someone asserted; the
    reverse direction serves the same edge with ``declared=False`` unless the
    caller asserted it too. ``geometry`` is the fine class a member pair
    actually realises, which is what a trace shows an editor alongside the
    relation the edge claims.
    """

    source: str
    target: str
    relation: str
    converse: str
    declared: bool
    geometry: str


@dataclass(frozen=True)
class Neighbour:
    """An item adjacent to another, with the relation read from either side.

    ``relation`` is the relation *from the item being asked about*; ``converse``
    is the same edge read the other way. ``declared`` distinguishes an edge the
    caller asserted from its converse, which the graph serves because an
    overlap is symmetric even when only one side recorded it.
    """

    item: Evidence
    relation: str
    converse: str
    declared: bool


@dataclass(frozen=True)
class GeometryConflict:
    """A declared relation that the two items' spans do not bear out.

    ``kind`` is ``"contradiction"`` when no member pair of the two interval sets
    realises the declared relation's fine class — the edge is wrong, and it is
    excluded from traversal rather than offered to a trace as though it held. It
    is ``"bounds_mismatch"`` when a member pair does realise the class but the
    two items' bounding boxes suggest a different one: the edge is kept, because
    it is true at the resolution an editor acts on, and flagged because an
    editor reading the two items' overall extents would be misled. A mismatch is
    information; a contradiction is a failed check.
    """

    source: str
    target: str
    declared: str
    computed: str
    kind: str


@dataclass(frozen=True)
class IntegrityReport:
    """Everything the graph found wrong with the declared edges.

    Reported, never repaired: the caller decides whether a dangling edge is a
    typo, a dropped item or an intentional cross-reference. ``duplicates`` is the
    one field that is not a list of edges — an id shared by several items is a
    property of the set, not of any one link, and it is what makes an edge's
    target ambiguous.
    """

    dangling: tuple[tuple[str, str, str], ...] = ()
    self_links: tuple[tuple[str, str], ...] = ()
    converse_conflicts: tuple[tuple[str, str, str, str], ...] = ()
    geometry_conflicts: tuple[GeometryConflict, ...] = ()
    cycles: tuple[tuple[str, ...], ...] = ()
    duplicates: dict[str, int] = field(default_factory=dict)
    unsupported: tuple[tuple[str, str, str, str], ...] = ()


    def ok(self) -> bool:
        """True iff every category is empty. A caller who wants the report's
        detail without the failure reads ``ok()`` first."""
        return not (
            self.dangling
            or self.self_links
            or self.converse_conflicts
            or self.geometry_conflicts
            or self.cycles
            or self.duplicates
            or self.unsupported
        )


@dataclass(frozen=True)
class TraceStep:
    """One hop of a trace, with the edge as the graph sees it.

    ``item`` is the item the hop *arrives at*, so a chain read top-to-bottom is
    the sequence of things the conclusion rests on; the root is named by the
    chain itself and never repeated as a step.
    """

    item: Evidence
    relation: str
    converse: str
    geometry: str
    declared: bool


@dataclass(frozen=True)
class TraceChain:
    """A single path from the root outward.

    ``terminated`` is why the walk stopped:

    - ``"grounded"`` — reached an item whose ``grounded`` flag is set, i.e. a
      directly observed fact. This is the outcome a conclusion wants, and the
      final step is that observation.
    - ``"dangling"`` — an edge points at an id the graph does not hold. The
      chain is incomplete and no step is recorded for the hop, because there is
      no item to name; the missing id is in ``report().dangling``.
    - ``"cycle"`` — the walk revisited an item already on this chain. The final
      step names the revisited item, so the loop is visible in the chain.
      Defensive: a cycle over non-``equals`` relations is a temporal
      impossibility that ``build`` reports, so reaching one means the report
      was ignored.
    - ``"unresolved"`` — an inferred item with no outgoing declared edges. This
      is the query's most useful failure: a conclusion that cites nothing is a
      claim the system cannot defend, and the editor needs to see that it is
      unsupported rather than have the trace fabricate a support for it.
    """

    root: str
    steps: tuple[TraceStep, ...]
    depth: int
    terminated: str


@dataclass(frozen=True)
class TraceResult:
    """Every chain from a root, plus the provenance a fact-checker reads."""

    root: str
    chains: tuple[TraceChain, ...]
    sources: tuple[Source, ...]
    modalities: tuple[Modality, ...]
    report: IntegrityReport


class EvidenceGraph:
    """A read index over an ``EvidenceSet`` that checks the set's own edges.

    Build with ``EvidenceGraph.build(items, strict=True)``. ``strict`` turns each
    integrity failure into a ``GraphIntegrityError`` naming all of them; with
    ``strict=False`` the same failures are reported and the offending edges are
    excluded from traversal, so a diagnostic pass over a suspect memory still
    answers queries about the parts that hold.

    The index is a pure function of the set: after ``restrict`` or ``union``,
    rebuild rather than patch, because clipping an item's spans can invalidate
    the geometry of every edge touching it.
    """

    def __init__(
        self,
        items: dict[str, Evidence],
        out: dict[str, tuple[Edge, ...]],
        into: dict[str, tuple[str, ...]],
        declared_pairs: frozenset[tuple[str, str]],
        report: IntegrityReport,
        excluded: frozenset[tuple[str, str]],
        dangling: dict[str, tuple[tuple[str, str], ...]],
        order: tuple[str, ...],
    ) -> None:
        self._items = items
        self._out = out
        self._into = into
        self._declared_pairs = declared_pairs
        self._report = report
        self._excluded = excluded
        self._dangling = dangling
        self._order = order

    # ── construction ───────────────────────────────────────────────────────

    @classmethod
    def build(cls, items, *, strict: bool = True) -> EvidenceGraph:
        """Index a set and check every declared edge.

        ``items`` is an ``EvidenceSet`` or any iterable of ``Evidence``. An item
        whose id is empty gets a positional fallback, matching
        ``engine.memory.ingest.link_overlapping``'s naming, so a graph built
        before the store has assigned ids still resolves the edges the adapters
        wrote — ids are the store's responsibility, and the fallback is a
        convenience for the pre-store case, not a substitute for assigning them.
        """
        sequence = list(items.items if isinstance(items, EvidenceSet) else items)
        index: dict[str, Evidence] = {}
        counts: dict[str, int] = {}
        fallback = 0
        order: list[str] = []
        for item in sequence:
            key = item.id
            if not key:
                # A positional name, not a stable one: two different orderings
                # of the same items produce different keys. The store assigns
                # ids before anything is persisted, so this only has to resolve
                # within one build.
                key = f"item-{fallback}"
                fallback += 1
            counts[key] = counts.get(key, 0) + 1
            if key not in index:
                index[key] = item
                order.append(key)

        duplicates = {k: v for k, v in counts.items() if v > 1}

        # One edge per adjacent item, in declaration order. A second edge to the
        # same target with a different relation cannot both hold, so the first
        # declared one is carried and the rest are reported rather than
        # silently dropped.
        outgoing: dict[str, list[Edge]] = {k: [] for k in index}
        seen: dict[str, set[str]] = {k: set() for k in index}
        unsupported: list[tuple[str, str, str, str]] = []
        dangling: list[tuple[str, str, str]] = []
        self_links: list[tuple[str, str]] = []
        dangling_out: dict[str, list[tuple[str, str]]] = {k: [] for k in index}
        for key in order:
            for link in index[key].links:
                if link.relation not in _FINE_CLASS:
                    unsupported.append((key, link.target, link.relation,
                                        "not an Allen relation"))
                    continue
                if link.target == key:
                    self_links.append((key, link.relation))
                    continue
                if link.target not in index:
                    dangling.append((key, link.target, link.relation))
                    dangling_out[key].append((link.target, link.relation))
                    continue
                if link.target in seen[key]:
                    unsupported.append((key, link.target, link.relation,
                                        "a second relation to the same item"))
                    continue
                seen[key].add(link.target)
                outgoing[key].append(Edge(
                    source=key,
                    target=link.target,
                    relation=link.relation,
                    converse=converse_name(link.relation),
                    declared=True,
                    geometry=_witness_class(
                        index[key].intervals, index[link.target].intervals,
                        link.relation,
                    )[1],
                ))

        declared_pairs = frozenset(
            (e.source, e.target)
            for edges in outgoing.values()
            for e in edges
        )

        # Two declared edges between the same pair must be one relation and its
        # converse, or they disagree about how the two items relate. An agreeing
        # pair collapses to a single declared edge — the reverse direction is
        # served from the same edge with ``declared`` set, since the caller did
        # assert it — and a disagreeing pair is reported with both relations and
        # excluded from traversal either way, because neither spelling can be
        # trusted over the other.
        converse_conflicts: list[tuple[str, str, str, str]] = []
        for e in (
            edge for edges in outgoing.values() for edge in edges
        ):
            if (e.target, e.source) not in declared_pairs:
                continue
            if e.source >= e.target:
                # Each unordered pair is examined once.
                continue
            other = next(
                o for o in outgoing[e.target] if o.target == e.source
            )
            if other.relation != e.converse:
                converse_conflicts.append(
                    (e.source, e.target, e.relation, other.relation)
                )

        # Geometry: an edge is kept and flagged when a member pair realises the
        # declared class but the two items' extents disagree, and excluded when
        # no member pair does. ``supported`` is the compatibility rule read off
        # the class the edge already carries, so the geometry pass reports
        # without recomputing the witness.
        geometry_conflicts: list[GeometryConflict] = []
        for key in order:
            for e in outgoing[key]:
                if e.geometry != _FINE_CLASS[e.relation]:
                    geometry_conflicts.append(GeometryConflict(
                        source=key, target=e.target,
                        declared=e.relation, computed=e.geometry,
                        kind="contradiction",
                    ))
                    continue
                bounds = _bounds_class(
                    index[key].intervals, index[e.target].intervals,
                )
                if bounds is not None and bounds != _FINE_CLASS[e.relation]:
                    geometry_conflicts.append(GeometryConflict(
                        source=key, target=e.target,
                        declared=e.relation, computed=bounds,
                        kind="bounds_mismatch",
                    ))

        cycles = _find_cycles(index, outgoing)

        report = IntegrityReport(
            dangling=tuple(dangling),
            self_links=tuple(self_links),
            converse_conflicts=tuple(converse_conflicts),
            geometry_conflicts=tuple(geometry_conflicts),
            cycles=tuple(cycles),
            duplicates=dict(duplicates),
            unsupported=tuple(unsupported),
        )

        excluded = frozenset(
            (gc.source, gc.target) for gc in geometry_conflicts
            if gc.kind == "contradiction"
        ) | frozenset(
            (a, b) for a, b, _r, _r2 in converse_conflicts
        ) | frozenset(
            (b, a) for a, b, _r, _r2 in converse_conflicts
        ) | frozenset(
            pair for cycle in cycles for pair in zip(cycle, cycle[1:])
        )

        if strict and not report.ok():
            raise GraphIntegrityError(_describe(report))

        into: dict[str, list[str]] = {k: [] for k in index}
        for key in order:
            for e in outgoing[key]:
                into[e.target].append(key)

        return cls(
            items=index,
            out={k: tuple(v) for k, v in outgoing.items()},
            into={k: tuple(v) for k, v in into.items()},
            declared_pairs=declared_pairs,
            report=report,
            excluded=excluded,
            dangling={k: tuple(v) for k, v in dangling_out.items()},
            order=tuple(order),
        )

    # ── query ──────────────────────────────────────────────────────────────

    def items(self) -> tuple[Evidence, ...]:
        """The indexed items, in the order they were given to ``build``."""
        return tuple(self._items[k] for k in self._order)

    def item(self, id: str) -> Evidence | None:
        """The item named by ``id``, or ``None`` if the index does not hold it.

        Where several items share an id the first given to ``build`` wins, which
        is the same resolution ``EvidenceSet.with_id`` uses; ``report()`` names
        the ambiguity.
        """
        return self._items.get(id)

    def edges(self) -> tuple[Edge, ...]:
        """Every declared edge, in declaration order."""
        return tuple(e for k in self._order for e in self._out[k])

    def neighbours(self, id: str) -> tuple[Neighbour, ...]:
        """The items adjacent to ``id``, from ``id``'s point of view.

        Covers both directions: an edge the item declared, and an edge another
        item declared to it, which is served as its converse. Excluded edges —
        contradictions, disagreeing converses, cycle members — are absent here
        and absent from every traversal, so a caller never has to filter them.
        """
        if id not in self._items:
            return ()
        out: list[Neighbour] = []
        for e in self._out[id]:
            if (e.source, e.target) in self._excluded:
                continue
            out.append(Neighbour(
                item=self._items[e.target],
                relation=e.relation,
                converse=e.converse,
                declared=True,
            ))
        # The reverse direction: an edge *to* this item, read as its converse.
        # Skipped where this item declared an edge back, because the forward
        # loop above already served that pair with ``declared=True`` — the
        # agreeing-pair collapse, one edge with two declared directions rather
        # than two entries for the same neighbour.
        for source in self._into[id]:
            if (id, source) in self._declared_pairs:
                continue
            for e in self._out[source]:
                if e.target != id:
                    continue
                if (e.source, e.target) in self._excluded:
                    continue
                out.append(Neighbour(
                    item=self._items[source],
                    relation=e.converse,
                    converse=e.relation,
                    declared=(id, source) in self._declared_pairs,
                ))
        return tuple(_sorted_neighbours(out))

    def trace(self, id: str, *, max_depth: int = 8,
              max_chains: int = 16) -> TraceResult:
        """Walk declared edges outward from ``id`` toward what it rests on.

        One chain per maximal path, declared edges only: the converse of an edge
        is derived, not asserted, so walking it would be following a relation
        nobody claimed. The walk stops at the first ``grounded=True`` item it
        reaches — a chain is a path to *one* supporting observation, not a tour
        of the neighbourhood, and a chain that continued past a grounded item
        would present an observation's neighbours as though they were the
        conclusion's support.
        """
        root = self._items.get(id)
        if root is None:
            return TraceResult(
                root=id, chains=(), sources=(), modalities=(),
                report=self._report,
            )

        # The walk runs in graph-key space. An item whose id is empty is keyed
        # positionally, so ``Evidence.id`` is not a name the index answers to,
        # and using it here would collapse every unassigned item onto the same
        # key and report a cycle that the index does not have.
        root_key = id
        if not root.id:
            for key in self._order:
                if self._items[key] is root:
                    root_key = key
                    break

        chains: list[TraceChain] = []
        sources: dict[Source, None] = {}
        modalities: dict[Modality, None] = {}

        def record(item: Evidence) -> None:
            sources[item.source] = None
            modalities[item.modality] = None

        record(root)

        # Depth-first over the declared edges. A stack of continuations rather
        # than recursion keeps the ordering deterministic and the depth bounded
        # independently of the chain length.
        # A step names the item it arrives at, not the one it departed from: a
        # chain read top-to-bottom is the sequence of things the conclusion rests
        # on, and a chain that ended by naming the *previous* item would leave
        # the supporting observation itself out of its own trace.
        stack: list[tuple[str, Evidence, tuple[TraceStep, ...], frozenset[str]]]
        stack = [(root_key, root, (), frozenset({root_key}))]
        while stack and len(chains) < max_chains:
            key, current, steps, visited = stack.pop()
            if len(steps) >= max_depth:
                chains.append(TraceChain(
                    root=id, steps=steps, depth=len(steps),
                    terminated="unresolved",
                ))
                continue
            if current.grounded:
                chains.append(TraceChain(
                    root=id, steps=steps, depth=len(steps),
                    terminated="grounded",
                ))
                continue
            hops: list[tuple[TraceStep, str, Evidence]] = []
            for e in self._out.get(key, ()):
                if (e.source, e.target) in self._excluded:
                    continue
                # ``build`` never indexes an edge whose target it does not hold,
                # so this cannot be None; a dangling citation is recorded in
                # ``self._dangling`` instead and handled below.
                target = self._items[e.target]
                if e.target in visited:
                    # The step names the revisited item, so the loop is visible
                    # in the chain itself and not only in ``report().cycles``.
                    chains.append(TraceChain(
                        root=id,
                        steps=(*steps, TraceStep(
                            item=target, relation=e.relation,
                            converse=e.converse, geometry=e.geometry,
                            declared=True,
                        )),
                        depth=len(steps) + 1,
                        terminated="cycle",
                    ))
                    continue
                hops.append((TraceStep(
                    item=target, relation=e.relation, converse=e.converse,
                    geometry=e.geometry, declared=True,
                ), e.target, target))
            if not hops:
                # An inferred item citing nothing: the query's most useful
                # failure, surfaced rather than papered over. The distinction
                # from a dangling citation matters — "cites something missing"
                # is an adapter or a stale rebuild, "cites nothing" is a
                # conclusion the system never grounded at all, and the repair
                # for the two is different. ``build`` keeps the dangling names
                # out of ``_out`` (a step names an item it arrives at, and
                # there is none), so they are read from the report index here.
                if self._dangling.get(key):
                    chains.append(TraceChain(
                        root=id, steps=steps, depth=len(steps) + 1,
                        terminated="dangling",
                    ))
                else:
                    chains.append(TraceChain(
                        root=id, steps=steps, depth=len(steps),
                        terminated="unresolved",
                    ))
                continue
            # Reversed so the sorted-first hop is popped first: the stack is
            # last-in-first-out, and without this the chains of an item citing
            # several others would emerge in reverse declaration order.
            for step, target_key, target in reversed(_sorted_hops(hops)):
                record(target)
                stack.append((
                    target_key, target, (*steps, step),
                    visited | {target_key},
                ))

        return TraceResult(
            root=id, chains=tuple(chains),
            sources=tuple(sources), modalities=tuple(modalities),
            report=self._report,
        )

    def supports(self, id: str) -> tuple[Evidence, ...]:
        """The items that cite ``id`` — the fact-checker's query.

        Walks the declared edges forward, from an observation to the conclusions
        built on it. This is what makes a replaced instrument actionable: the
        new run's ``params_digest`` differs, the memory is rebuilt, and this
        names every conclusion that now rests on evidence the current pipeline
        did not produce.
        """
        if id not in self._items:
            return ()
        out: list[Evidence] = []
        seen: set[str] = set()
        stack = list(self._into[id])
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            item = self._items.get(key)
            if item is None:
                continue
            out.append(item)
            stack.extend(self._into.get(key, ()))
        return tuple(out)

    def report(self) -> IntegrityReport:
        """The integrity findings from ``build``. Stable across rebuilds."""
        return self._report


# ── geometry ───────────────────────────────────────────────────────────────


def _witness_class(a: IntervalSet, b: IntervalSet,
                   declared: str) -> tuple[bool, str]:
    """Does some member pair of ``a`` and ``b`` realise the declared relation?

    Returns ``(supported, computed)`` where ``computed`` is the fine class an
    editor is shown when it does not. The *witness* reading rather than the
    universal one: two items that each carry several spans are related at the
    resolution at which an editor acts — "this topic is discussed *after* this
    scene" holds if any topic span follows any scene span — and a universal
    reading would declare nearly every multi-span pair contradictory and bury
    the real findings.

    Precedence is decidable from the extremes alone, because both sets are sorted
    and disjoint: the earliest-ending member of ``a`` precedes the
    latest-starting member of ``b`` iff any member pair does. Only sets that
    interleave without such a pair fall back to a member scan, and in that case
    every member pair overlaps — so the scan is bounded by the intersecting
    spans, not by the full product of two long sets.
    """
    want = _FINE_CLASS[declared]
    if not a.intervals or not b.intervals:
        return False, want
    if want == "precedes":
        if a.intervals[0].end <= b.intervals[-1].start + MERGE_EPS:
            return True, "precedes"
    elif want == "preceded_by":
        if b.intervals[0].end <= a.intervals[-1].start + MERGE_EPS:
            return True, "preceded_by"
    first = _FINE_CLASS[a.intervals[0].relation(b.intervals[0])]
    if first == want:
        return True, first
    for x in a.intervals:
        for y in b.intervals:
            cls = _FINE_CLASS[x.relation(y)]
            if cls == want:
                return True, cls
    return False, first


def _bounds_class(a: IntervalSet, b: IntervalSet) -> str | None:
    """The fine class of the two items' bounding boxes.

    ``None`` for an empty operand, which has no extent to compare. This is the
    reading an editor takes in at a glance, and it diverges from the witness
    reading whenever a multi-span item's extents straddle another item — which
    is why the divergence is reported rather than used as the check.
    """
    ba, bb = a.bounds, b.bounds
    if ba is None or bb is None:
        return None
    return _FINE_CLASS[ba.relation(bb)]


# ── cycles ─────────────────────────────────────────────────────────────────


_ORDER: dict[str, int] = {"before": -1, "meets": -1, "after": 1, "met_by": 1}


def _compose(a: int | None, b: int | None) -> int | None:
    """Compose two orderings: the signature of A→B followed by B→C.

    ``None`` is the identity — no edges yet. Strict orderings chain only when
    they point the same way: A<B and B<C gives A<C. A<B and C<B says only that
    both precede B, so A against C is unconstrained, and an overlapping pair
    constrains nothing. Anything unconstrained becomes ``0``, which composes
    away every ordering after it.
    """
    if a is None:
        return b
    if b is None:
        return a
    if a == 0 or b == 0 or a != b:
        return 0
    return a


def _find_cycles(index: dict[str, Evidence],
                 outgoing: dict[str, list[Edge]]) -> list[tuple[str, ...]]:
    """Cycles whose composed ordering is a temporal impossibility.

    A cycle of ``before`` edges — A before B before C before A — cannot hold:
    the composed ordering is A<A, and no assignment of times satisfies it. But
    a cycle is not by itself the problem. The agreeing pair — A before B, and
    B declares ``after`` back — closes a cycle and states one fact twice; its
    composed ordering is A<B composed with A<B, which is consistent. Detecting
    the cycle and reporting it would flag the graph's own converse convention
    as a contradiction, and a caller who took the report seriously would
    discard every mutual declaration.

    So the check is on the composition, not the topology: a cycle is reported
    only when the orderings around it chain into a strict self-ordering. Cases
    the ordering signature cannot see — A during B and B during A — are
    impossible too, and they are caught where they belong, by the
    converse-conflict check, where the second declaration is compared against
    the first's converse. ``equals`` is skipped outright: it imposes no order
    and composes to nothing.
    """
    cycles: list[tuple[str, ...]] = []
    done: set[str] = set()

    def visit(start: str) -> None:
        # The signature carried is the composition *around the cycle*, so the
        # edges before the cycle's first node are excluded: composing from the
        # DFS root would let an edge outside the cycle neutralise one inside it
        # and miss an impossibility.
        stack: list[tuple[str, tuple[str, ...], tuple[int, ...]]]
        stack = [(start, (start,), ())]
        while stack:
            node, path, sigs = stack.pop()
            if node in done:
                continue
            done.add(node)
            for e in outgoing.get(node, ()):
                if e.relation == "equals":
                    continue
                if e.target not in index:
                    continue
                s = _ORDER.get(e.relation, 0)
                if e.target in path:
                    idx = path.index(e.target)
                    closed = None
                    for prev in (*sigs[idx:], s):
                        closed = _compose(closed, prev)
                    if closed in (-1, 1):
                        cycles.append((*path[idx:], e.target))
                    continue
                stack.append((e.target, (*path, e.target), (*sigs, s)))

    for key in index:
        visit(key)
    return cycles


# ── ordering and reporting ────────────────────────────────────────────────


def _sorted_neighbours(neighbours: list[Neighbour]) -> list[Neighbour]:
    """Declared edges before derived converses, then by id: deterministic across
    rebuilds, and the declared ones are the edges a caller should act on."""
    return sorted(
        neighbours, key=lambda n: (0 if n.declared else 1, n.item.id)
    )


def _sorted_hops(hops: list[tuple[TraceStep, str, Evidence]]
                 ) -> list[tuple[TraceStep, str, Evidence]]:
    return sorted(hops, key=lambda h: h[1])


def _describe(report: IntegrityReport) -> str:
    """One message naming every failing category, so a single failed build is
    enough to see the whole shape of the problem."""
    lines = ["evidence graph integrity check failed:"]
    if report.dangling:
        lines.append(f"  {len(report.dangling)} dangling edge(s): "
                     + ", ".join(f"{s}->{t}({r})" for s, t, r in report.dangling))
    if report.self_links:
        lines.append(f"  {len(report.self_links)} self-link(s): "
                     + ", ".join(f"{s}({r})" for s, r in report.self_links))
    if report.converse_conflicts:
        lines.append(f"  {len(report.converse_conflicts)} converse conflict(s): "
                     + ", ".join(f"{a}<->{b}: {rab} vs {rba}"
                                 for a, b, rab, rba in report.converse_conflicts))
    if report.geometry_conflicts:
        lines.append(f"  {len(report.geometry_conflicts)} geometry conflict(s): "
                     + ", ".join(f"{g.source}->{g.target}: declared {g.declared}, "
                                 f"found {g.computed} ({g.kind})"
                                 for g in report.geometry_conflicts))
    if report.cycles:
        lines.append(f"  {len(report.cycles)} cycle(s): "
                     + ", ".join("->".join(c) for c in report.cycles))
    if report.duplicates:
        lines.append(f"  {len(report.duplicates)} duplicate id(s): "
                     + ", ".join(f"{k}x{v}" for k, v in report.duplicates.items()))
    if report.unsupported:
        lines.append(f"  {len(report.unsupported)} unsupported edge(s): "
                     + ", ".join(f"{s}->{t}({r}): {why}"
                                 for s, t, r, why in report.unsupported))
    return "\n".join(lines)
