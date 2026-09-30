"""Failure-first tests for ``engine.memory.graph``.

The interval algebra breaks by being numerically wrong; evidence breaks by being
unverifiable. The graph is the layer that makes an edge *checkable*, so the
hazards here are the ones that would make a conclusion look grounded when it is
not:

- a converse spelled two ways, so an editor reads the wrong relation half the
  time;
- an edge whose declared relation the two items' spans do not realise, so a
  trace shows a temporal claim that cannot hold;
- a cycle over ordering relations, which no assignment of times satisfies;
- a chain that names the item it departed from rather than the observation it
  arrived at, so the support is absent from its own trace;
- a rebuild after ``restrict`` that silently keeps edges pointing at spans that
  no longer exist.

Every case below is one of those, and the assertions pin behaviour the
fact-checking layer will rely on.
"""
import pytest

from engine.memory.evidence import Evidence, EvidenceLink, EvidenceSet, Modality, Source
from engine.memory.graph import (
    Edge,
    EvidenceGraph,
    GeometryConflict,
    GraphIntegrityError,
    IntegrityReport,
    Neighbour,
    TraceResult,
)
from engine.memory.intervals import MERGE_EPS, Interval, IntervalSet, converse

ALL_THIRTEEN = [
    "before", "meets", "overlaps", "finished_by", "contains", "starts",
    "equals", "started_by", "finishes", "during", "overlapped_by", "met_by",
    "after",
]

# Spans placing ``a`` in each of the thirteen relations to ``b``, where
# ``a = (10, 20)``. Kept as a table rather than derived, because the test's job
# is to pin the relations the algebra produces, not to re-derive them.
_FOR_RELATION = {
    "before": (30.0, 40.0),
    "meets": (20.0, 30.0),
    "overlaps": (15.0, 25.0),
    "finished_by": (12.0, 20.0),
    "contains": (12.0, 18.0),
    "starts": (10.0, 25.0),
    "equals": (10.0, 20.0),
    "started_by": (10.0, 15.0),
    "finishes": (8.0, 20.0),
    "during": (8.0, 22.0),
    "overlapped_by": (8.0, 15.0),
    "met_by": (5.0, 10.0),
    "after": (0.0, 5.0),
}


def _ev(id: str, spans, **over) -> Evidence:
    base = dict(
        claim=f"evidence {id}",
        intervals=IntervalSet.of(*spans) if not isinstance(spans, IntervalSet) else spans,
        modality=Modality.VISUAL,
        source=Source.of("engine.reframe", "track_subject_center", stride=28),
        grounded=True,
    )
    base.update(over)
    return Evidence(**base, id=id)


def _pair(relation: str, a_id="a", b_id="b") -> tuple[Evidence, Evidence]:
    """Two items whose single spans realise ``relation``, ``a`` to ``b``."""
    a = _ev(a_id, [(10.0, 20.0)])
    b = _ev(b_id, [_FOR_RELATION[relation]])
    return a.link(b_id, relation), b


def _graph(*items: Evidence, strict: bool = True) -> EvidenceGraph:
    return EvidenceGraph.build(EvidenceSet.of(*items), strict=strict)


# ── converse coverage: all thirteen relations, both directions ─────────────


@pytest.mark.parametrize("relation", ALL_THIRTEEN)
def test_converse_served_in_both_directions(relation):
    """``neighbours`` reads an edge from either side and names the relation the
    side implies. A wrong converse table would make every reverse query report
    the opposite of the truth — an editor following a ``before`` edge backward
    would be told ``after``."""
    a, b = _pair(relation)
    g = _graph(a, b)

    forward = g.neighbours(a.id)
    assert [(n.item.id, n.relation, n.converse, n.declared) for n in forward] == [
        (b.id, relation, converse(relation), True)
    ]

    reverse = g.neighbours(b.id)
    assert [(n.item.id, n.relation, n.converse, n.declared) for n in reverse] == [
        (a.id, converse(relation), relation, False)
    ]
    assert g.report().ok()


def test_converse_is_derived_not_stored():
    """The graph carries one spelling of a relation. A second declared edge for
    the converse is not needed and not duplicated: ``edges()`` holds one edge per
    declared link, and the reverse direction is served from it."""
    a, b = _pair("before")
    g = _graph(a, b)

    assert len(g.edges()) == 1
    edge = g.edges()[0]
    assert edge.converse == "after"
    assert edge.declared is True


def test_edge_geometry_is_the_fine_class(relation="during"):
    """``geometry`` is what an editor is shown: the coarse class the spans
    actually realise, alongside the relation the edge claims."""
    a, b = _pair("during")
    g = _graph(a, b)
    assert g.edges()[0].geometry == "inside"


# ── declared edges: one label per target (the D1 regression) ────────────────


def test_two_links_keep_two_labels():
    """Schema 1.0 stored a tuple of target ids under one shared relation and
    kept only the last label. Two links with two relations must both survive,
    each with its own label, in declaration order."""
    obs = _ev("obs", [(0.0, 60.0)])
    other = _ev("other", [(40.0, 80.0)])
    mid = _ev("mid", [(10.0, 20.0)], grounded=False).link("obs", "during")
    mid = mid.link("other", "before")
    g = _graph(obs, other, mid)

    edges = {e.target: e.relation for e in g.edges() if e.source == "mid"}
    assert edges == {"obs": "during", "other": "before"}

    names = [(n.item.id, n.relation) for n in g.neighbours("mid")]
    assert ("obs", "during") in names
    assert ("other", "before") in names


def test_second_relation_to_same_target_is_reported_not_silently_dropped():
    """Two edges to one item with different relations cannot both hold. The
    first declared one is kept, the rest are reported, so an adapter that wrote
    a contradiction finds out which edge it kept."""
    obs = _ev("obs", [(0.0, 60.0)])
    bad = _ev("bad", [(10.0, 20.0)], grounded=False)
    bad = bad.link("obs", "during").link("obs", "after")
    g = _graph(obs, bad, strict=False)

    assert g.report().unsupported == (("bad", "obs", "after",
                                      "a second relation to the same item"),)
    kept = [e.relation for e in g.edges() if e.source == "bad"]
    assert kept == ["during"]


def test_non_allen_relation_cannot_enter_the_graph():
    """The ``unsupported`` category "not an Allen relation" is unreachable from
    the public API, because ``EvidenceLink`` rejects a bad name at construction
    rather than letting an unmatchable string reach the index. Defence in depth:
    a semantic label ("same_speaker") in an edge would make a temporal check
    read as a semantic claim."""
    with pytest.raises(ValueError, match="not an Allen relation"):
        EvidenceLink(target="obs", relation="same_speaker")


# ── mutual declaration: collapse or exclude ─────────────────────────────────


def test_agreeing_pair_collapses_to_one_neighbour():
    """Both directions declared, and the second is the converse of the first:
    one edge with ``declared=True`` from either side, not two entries for the
    same neighbour."""
    a, b = _pair("before")
    b = b.link("a", "after")
    g = _graph(a, b)

    assert g.report().ok()
    assert [n.declared for n in g.neighbours("a")] == [True]
    assert [n.declared for n in g.neighbours("b")] == [True]
    assert [(n.item.id, n.relation) for n in g.neighbours("a")] == [("b", "before")]
    assert [(n.item.id, n.relation) for n in g.neighbours("b")] == [("a", "after")]


def test_disagreeing_pair_is_excluded_both_ways():
    """Both directions declared, neither the converse of the other: neither
    spelling can be trusted over the other, so both are reported and neither is
    traversable. A graph that kept one would be keeping an arbitrary answer."""
    a = _ev("a", [(10.0, 20.0)], grounded=False)
    b = _ev("b", [_FOR_RELATION["before"]], grounded=False)
    a = a.link("b", "before")
    b = b.link("a", "before")
    g = _graph(a, b, strict=False)

    assert g.report().converse_conflicts == (("a", "b", "before", "before"),)
    assert g.neighbours("a") == ()
    assert g.neighbours("b") == ()
    # Both directions excluded, so ``a`` has no traversable hop and the walk has
    # nowhere to go — the disagreement is visible in the trace, not papered over
    # by silently keeping one of the two spellings.
    assert [c.terminated for c in g.trace("a").chains] == ["unresolved"]


def test_mutual_equals_is_not_a_conflict():
    """``equals`` is its own converse, so a mutual declaration agrees rather than
    conflicting — and it imposes no temporal order, so it forms no cycle."""
    a, b = _pair("equals")
    b = b.link("a", "equals")
    g = _graph(a, b)

    assert g.report().ok()
    assert g.report().cycles == ()
    assert [n.relation for n in g.neighbours("a")] == ["equals"]


# ── integrity: what a bad build reports ─────────────────────────────────────


def test_dangling_edge_reported_and_raised():
    """An edge to an id the index does not hold is a typo, a dropped item or a
    cross-memory reference — the graph reports it rather than guessing, and
    ``strict`` turns it into a failure naming the edge."""
    lone = _ev("mid", [(0.0, 10.0)], grounded=False).link("ghost", "before")
    with pytest.raises(GraphIntegrityError, match=r"1 dangling edge\(s\)"):
        EvidenceGraph.build(EvidenceSet.of(lone))


def test_dangling_edge_trace_terminates_dangling():
    g = _graph(_ev("mid", [(0.0, 10.0)], grounded=False).link("ghost", "before"),
               strict=False)
    result = g.trace("mid")
    assert len(result.chains) == 1
    chain = result.chains[0]
    assert chain.terminated == "dangling"
    # No step records the hop: a step names the item it arrives at, and there is
    # no such item. The missing id is in the report.
    assert chain.steps == ()
    assert chain.depth == 1
    assert result.report.dangling == (("mid", "ghost", "before"),)


def test_self_link_reported():
    """An item citing itself cannot support itself; reported and excluded rather
    than forming a one-item cycle."""
    solo = _ev("solo", [(0.0, 10.0)], grounded=False).link("solo", "during")
    g = _graph(solo, strict=False)
    assert g.report().self_links == (("solo", "during"),)
    assert g.neighbours("solo") == ()


def test_duplicate_ids_reported_and_second_items_edges_not_indexed():
    """Two items sharing an id make every edge to that id ambiguous. The first
    given to ``build`` wins, which is the resolution ``EvidenceSet.with_id``
    uses; the second item's links are not indexed, because indexing both would
    attribute one item's edges to the other."""
    first = _ev("dup", [(0.0, 10.0)])
    second = _ev("dup", [(20.0, 30.0)], grounded=False).link("dup", "before")
    g = _graph(first, second, strict=False)

    assert g.report().duplicates == {"dup": 2}
    assert g.edges() == ()


def test_error_message_names_every_category():
    """One failed build is enough to see the whole shape of the problem: an
    adapter that wrote one bad edge usually wrote several."""
    a = _ev("a", [(0.0, 10.0)], grounded=False).link("ghost", "before")
    b = _ev("b", [(0.0, 10.0)], grounded=False).link("b", "during")
    with pytest.raises(GraphIntegrityError) as excinfo:
        EvidenceGraph.build(EvidenceSet.of(a, b))
    message = str(excinfo.value)
    assert "dangling edge" in message
    assert "self-link" in message


# ── geometry: declared relation against the spans ───────────────────────────


def test_before_declared_on_overlapping_spans_is_a_contradiction():
    """The check an editor needs. "This topic is discussed before this scene"
    asserted over spans that overlap cannot hold, and the edge is excluded from
    traversal rather than served with a label the data contradicts."""
    a = _ev("a", [(0.0, 20.0)], grounded=False).link("b", "before")
    b = _ev("b", [(10.0, 30.0)])
    g = _graph(a, b, strict=False)

    conflicts = g.report().geometry_conflicts
    assert len(conflicts) == 1
    assert conflicts[0].kind == "contradiction"
    assert (conflicts[0].declared, conflicts[0].computed) == ("before", "partial")
    assert g.neighbours("a") == ()


def test_multi_span_witness_reading_supported():
    """The witness reading, not the universal one. ``a`` overlaps ``b``'s first
    span but sits inside its second: ``during`` holds at the resolution an editor
    acts at, so the edge survives — a universal reading would have declared it
    contradictory and buried the real findings."""
    a = _ev("a", [(150.0, 160.0), (200.0, 210.0)], grounded=False).link("b", "during")
    b = _ev("b", [(150.0, 220.0)])
    g = _graph(a, b)

    assert g.report().geometry_conflicts == ()
    edge = g.edges()[0]
    assert edge.geometry == "inside"


def test_bounds_mismatch_is_informational_not_exclusion():
    """``a``'s extent straddles ``b``'s first span and is inside its second. The
    witness reading supports ``during``; the at-a-glance reading of the two
    bounding boxes says ``overlaps``. The divergence is reported — an editor who
    skims extents would be misled — but the edge is kept, because it is not
    wrong."""
    a = _ev("a", [(0.0, 100.0), (200.0, 210.0)], grounded=False).link("b", "during")
    b = _ev("b", [(150.0, 220.0)])
    g = _graph(a, b, strict=False)

    conflicts = g.report().geometry_conflicts
    assert len(conflicts) == 1
    assert conflicts[0].kind == "bounds_mismatch"
    assert g.neighbours("a") != ()


def test_sub_eps_overlap_reads_as_meets():
    """``Interval.relation`` and ``IntervalSet``'s merger share a tolerance, so
    a sub-eps overlap is ``meets`` here and merged there. Without that agreement
    the geometry check would turn a touching boundary into a ``partial``
    contradiction and the system would have three answers about whether a cut
    point existed."""
    gap = MERGE_EPS / 1000.0
    a = _ev("a", [(0.0, 10.0)], grounded=False).link("b", "meets")
    b = _ev("b", [(10.0 - gap, 20.0)])
    g = _graph(a, b)

    assert Interval(0.0, 10.0).relation(Interval(10.0 - gap, 20.0)) == "meets"
    assert g.report().geometry_conflicts == ()


def test_empty_spans_make_no_geometry_claim():
    """An item with no spans has no extent to compare; the witness check returns
    the declared class and the bounds check declines. An empty item is a
    recording failure elsewhere in the stack, not a geometric contradiction
    here."""
    a = _ev("a", [], grounded=False).link("b", "before")
    b = _ev("b", [(10.0, 20.0)])
    g = _graph(a, b, strict=False)

    bounds_conflicts = [c for c in g.report().geometry_conflicts
                        if c.kind == "bounds_mismatch"]
    assert bounds_conflicts == []


# ── cycles ───────────────────────────────────────────────────────────────────


def test_cycle_of_before_is_reported():
    """A before B before C before A is a temporal impossibility, not a modelling
    choice: each edge forbids the next. The cycle is reported, and so is the
    geometry contradiction one of those edges necessarily carries."""
    a = _ev("a", [(0.0, 10.0)], grounded=False).link("b", "before")
    b = _ev("b", [(20.0, 30.0)], grounded=False).link("c", "before")
    c = _ev("c", [(40.0, 50.0)], grounded=False).link("a", "before")
    g = _graph(a, b, c, strict=False)

    assert len(g.report().cycles) == 1
    names = g.report().cycles[0]
    assert names[0] == names[-1]
    assert set(names) == {"a", "b", "c"}
    assert any(gc.declared == "before" and gc.kind == "contradiction"
               for gc in g.report().geometry_conflicts)


# ── trace ─────────────────────────────────────────────────────────────────────


def _chain_of(n: int) -> list[Evidence]:
    """``n`` inferred items, each citing the next, resting on one observation.

    The last inferred item cites ``obs`` — the chain must arrive somewhere, and a
    citation of the next index instead would dangle and make this a different
    test."""
    items = []
    for i in range(n - 1):
        items.append(_ev(f"inf-{i}", [(0.0, 10.0)], grounded=False)
                     .link(f"inf-{i + 1}", "during"))
    items.append(_ev(f"inf-{n - 1}", [(0.0, 10.0)], grounded=False)
                 .link("obs", "during"))
    items.append(_ev("obs", [(0.0, 10.0)]))
    return items


def test_trace_arrives_at_the_grounding_observation():
    """A step names the item it *arrives at*, so a chain read top-to-bottom is
    the sequence of things the conclusion rests on and the final step is the
    observation itself. Naming the item departed from would leave the support
    out of its own trace."""
    obs = _ev("obs", [(0.0, 60.0)])
    mid = _ev("mid", [(10.0, 20.0)], grounded=False).link("obs", "during")
    top = _ev("top", [(10.0, 20.0)], grounded=False).link("mid", "during")
    g = _graph(obs, mid, top)

    result = g.trace("top")
    assert len(result.chains) == 1
    chain = result.chains[0]
    assert chain.terminated == "grounded"
    assert [s.item.id for s in chain.steps] == ["mid", "obs"]
    assert chain.depth == 2


def test_trace_stops_at_first_grounded_item():
    """A chain is a path to *one* supporting observation, not a tour of the
    neighbourhood. An observation's own neighbours are not the conclusion's
    support, so the walk ends at the first ``grounded`` item it reaches."""
    root = _ev("root", [(0.0, 10.0)], grounded=False).link("obs", "during")
    obs = _ev("obs", [(0.0, 10.0)]).link("further", "before")
    further = _ev("further", [(20.0, 30.0)])
    g = _graph(root, obs, further)

    chains = g.trace("root").chains
    assert len(chains) == 1
    assert chains[0].terminated == "grounded"
    assert [s.item.id for s in chains[0].steps] == ["obs"]


def test_trace_reports_unresolved_for_a_conclusion_citing_nothing():
    """The query's most useful failure: a conclusion that cites nothing is a
    claim the system cannot defend, and the trace says so rather than
    fabricating a support for it."""
    g = _graph(_ev("lone", [(0.0, 10.0)], grounded=False))
    result = g.trace("lone")
    assert len(result.chains) == 1
    assert result.chains[0].terminated == "unresolved"
    assert result.chains[0].steps == ()


def test_trace_collects_provenance_across_chains():
    """The sources and modalities a fact-checker reads, gathered from every item
    a chain touches — including the root, which the steps do not name."""
    obs = _ev("obs", [(0.0, 60.0)],
              source=Source.of("engine.reframe", "track_faces", n=28))
    top = _ev("top", [(10.0, 20.0)], grounded=False,
              modality=Modality.EDITORIAL,
              source=Source.of("engine.corpus", "acts")).link("obs", "during")
    g = _graph(obs, top)

    result = g.trace("top")
    assert result.modalities == (Modality.EDITORIAL, Modality.VISUAL)
    assert {s.method for s in result.sources} == {"track_faces", "acts"}


def test_trace_cap_reports_unresolved_at_that_depth():
    """The depth cap is a bound, not a cliff: a chain that runs past it is
    reported with its depth and stopped, so a runaway citation chain is visible
    rather than silently truncated."""
    g = _graph(*_chain_of(12))
    result = g.trace("inf-0", max_depth=3)
    assert len(result.chains) == 1
    chain = result.chains[0]
    assert chain.terminated == "unresolved"
    assert chain.depth == 3


def test_trace_cap_on_chains_bounds_the_answer_size():
    g = _graph(*_chain_of(6))
    result = g.trace("inf-0", max_chains=1)
    assert len(result.chains) == 1


def test_trace_unknown_id_is_empty_not_an_error():
    """Asking about an id the index does not hold returns no chains rather than
    raising: a diagnostic pass over a suspect memory still answers queries about
    the parts that hold."""
    g = _graph(_ev("a", [(0.0, 10.0)]))
    result = g.trace("nope")
    assert isinstance(result, TraceResult)
    assert result.chains == ()


def test_trace_follows_declaration_order_across_branches():
    """An item citing several others yields one chain per citation, and the
    chains emerge in declaration order. Without the reversed push the stack's
    last-in-first-out would reverse them."""
    obs1 = _ev("obs1", [(0.0, 60.0)])
    obs2 = _ev("obs2", [(0.0, 60.0)])
    top = (_ev("top", [(10.0, 20.0)], grounded=False)
           .link("obs1", "during").link("obs2", "during"))
    g = _graph(obs1, obs2, top)

    result = g.trace("top")
    assert [(c.steps[0].item.id, c.terminated) for c in result.chains] == [
        ("obs1", "grounded"), ("obs2", "grounded"),
    ]


def test_trace_names_the_revisited_item_on_a_cycle():
    """The loop is visible in the chain itself and not only in the report: the
    final step names the item the walk revisited."""
    a = _ev("a", [(0.0, 10.0)], grounded=False).link("b", "equals")
    b = _ev("b", [(0.0, 10.0)], grounded=False).link("a", "equals")
    g = _graph(a, b, strict=False)

    # ``equals`` is skipped by the cycle finder, so the report is clean; the
    # traversal still has to terminate rather than loop.
    assert g.report().cycles == ()
    result = g.trace("a")
    cycles = [c for c in result.chains if c.terminated == "cycle"]
    assert len(cycles) == 1
    assert cycles[0].steps[-1].item.id == "a"


def test_trace_works_before_ids_are_assigned():
    """Items with empty ids get positional keys, matching the adapters' naming.
    Keying the walk on ``Evidence.id`` here would collapse every unassigned item
    onto the same key and report a cycle the index does not have."""
    obs = _ev("", [(0.0, 60.0)])
    mid = _ev("", [(10.0, 20.0)], grounded=False).link("item-0", "during")
    g = EvidenceGraph.build(EvidenceSet.of(obs, mid))

    result = g.trace("item-1")
    assert len(result.chains) == 1
    assert result.chains[0].terminated == "grounded"


def test_trace_is_independent_of_id_sort_order():
    """Iteration is the items' declaration order, not sorted ids. Two items given
    out of alphabetical order are indexed that way, and a re-run in a fresh
    interpreter reproduces the same chains."""
    obs = _ev("z", [(0.0, 60.0)])
    top = _ev("a", [(10.0, 20.0)], grounded=False).link("z", "during")
    g = _graph(obs, top)

    assert [i.id for i in g.items()] == ["z", "a"]
    first = g.trace("a")
    rebuilt = _graph(obs, top).trace("a")
    assert first.chains == rebuilt.chains


# ── supports: forward from an observation ─────────────────────────────────────


def test_supports_walks_forward_transitively():
    """The fact-checker's query, and what makes a replaced instrument
    actionable: the digest changes, the memory is rebuilt, and this names every
    conclusion that now rests on evidence the current pipeline did not produce.
    A conclusion citing nothing is a claim... — here it is the observation that
    is cited, directly and at a remove."""
    obs = _ev("obs", [(0.0, 60.0)])
    mid = _ev("mid", [(10.0, 20.0)], grounded=False).link("obs", "during")
    top = _ev("top", [(10.0, 20.0)], grounded=False).link("mid", "during")
    g = _graph(obs, mid, top)

    supported = g.supports("obs")
    assert [i.id for i in supported] == ["mid", "top"]


def test_supports_unknown_id_is_empty():
    g = _graph(_ev("a", [(0.0, 10.0)]))
    assert g.supports("nope") == ()


# ── rebuild after restrict ────────────────────────────────────────────────────


def test_restrict_then_rebuild_reports_orphaned_edges():
    """``EvidenceSet.restrict`` clips spans and drops items whose support did not
    survive, without touching any link — the clip, not the source, is now the
    world. Rebuilding rather than patching is what surfaces the consequence: an
    edge to a dropped item is dangling, and an edge whose spans were clipped may
    no longer hold."""
    # Disjoint spans, so a window exists that covers ``mid`` without covering
    # ``obs``: a clip that kept a span of both would drop neither and test
    # nothing.
    obs = _ev("obs", [(12.0, 31.0)])
    mid = _ev("mid", [(35.0, 45.0)], grounded=False).link("obs", "after")
    whole = EvidenceSet.of(obs, mid)

    # ``obs`` has no support inside this window, so ``restrict`` drops it and
    # ``mid``'s edge now points at an item the index does not hold.
    clipped = whole.restrict(IntervalSet.of((34.0, 46.0)))
    assert clipped.with_id("obs") is None

    g = EvidenceGraph.build(clipped, strict=False)
    assert g.report().dangling == (("mid", "obs", "after"),)
    assert g.trace("mid").chains[0].terminated == "dangling"

    # The unclipped set builds clean, so the finding is the clip's, not the
    # fixtures'.
    assert _graph(obs, mid).report().ok()
