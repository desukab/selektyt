"""The story graph — narrative structure as evidence-backed nodes.

Why this module exists
----------------------

``engine.corpus`` finds a story's pieces — where a subject recurs, where a
question is asked and answered, where the dramatic intensity peaks — but it
cannot say "*this* moment is the setup", and the records it writes in place of
that judgement are lossy in three ways the audit in ``docs/story-graph-design.md``
measures:

- ``DramaticBeat.kind`` realises five of the eight kinds the mission names, and
  the one its own comment documents as ``"setup"`` is assigned nowhere, so the
  highest-priority role in the only join between the graph and an edit
  (``engine.story_path``) is unreachable through it;
- every temporal record in that layer is one span, so a subject discussed three
  times across an hour reports a bounding span that silently contains the two
  gaps between the discussions, and ``StoryThread.arc`` collapses
  ``tension → tension → resolution`` and ``tension → resolution`` into one
  indistinguishable label;
- the kinds it cannot support — conflict above all — are simply absent, so a
  consumer cannot tell "the analyser found no conflict" from "the analyser did
  not look for one".

This module is the projection that closes those three. ``build_story_graph``
reads an ``EditorialCorpus`` and returns ``StoryNode`` objects — one per (kind,
moment) — each carrying the spans where the kind holds as an ``IntervalSet``
(variable cardinality by construction, never a single span), the ids of the
evidence that backs it, the verbatim cues the corpus quotes, the basis on which
the kind was established, and the signals the node degrades without.

Why a node subclasses ``Evidence`` rather than being a parallel record: a story
node *is* a claim bound to where it holds and what supports it, which is what
``engine.memory.evidence`` defines, and the mission's constraint is that story
nodes are evidence rather than a second type system that would have to be kept
consistent with the first. Subclassing means a node takes part in the evidence
algebra directly — it goes into an ``EvidenceSet``, and its links are checked by
``engine.memory.graph.EvidenceGraph`` with no checker of its own, so the
geometry of a setup→payoff edge is verified by the same witness reading as a
face track's. The fields the base does not carry (``kind``, ``basis``,
``span_resolution``, the degradation contract) are what make it a *story* node;
everything else is inherited unchanged, including the rule that a confidence is
recorded as the source instrument measured it and never adjusted here.

What it is not
--------------

It is not a new analysis. Apart from one computation — folding a recurring
subject's member spans into an interval set, which the corpus does not do and
which is what stops a three-run topic reporting one bounding span — every node
field is a mechanical read of a record the corpus already holds. The layer adds
no perception: no stance model for conflict, no embeddings for ``supports`` or
``explains``, no forward projection for ``foreshadows``. A node with
``basis == UNSUPPORTED`` and ``unsupported_reason`` populated is a *correct*
output of this layer, not a degraded one; it is how a graph says what it cannot
see without pretending to have seen it.

It is not an edit. No node carries a priority, a budget, a keep/drop verdict or
an ordering other than source time — those live in ``engine.story_path`` and
``engine.editorial``, which weigh evidence against a story's needs. Removing
this layer must not change a shipped frame.

It is not persisted. The corpus is the canonical, schema-versioned record; the
graph is re-derived from it by a pure function and holds no transcript text and
no copy of any corpus record — only indices, spans and the bounded verbatim
cues the corpus already quotes.

Purity
------

Deterministic and side-effect free: an identical corpus and identical
assertions yield identical nodes, and there is no wall clock, no network, no
inference and no file access. The corpus handed in is never mutated, so the
same graph yields every presentation of it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Sequence

from engine.corpus import EditorialCorpus
from engine.lexical import fingerprint
from engine.memory.evidence import Evidence, EvidenceLink, EvidenceSet, Modality, Source
from engine.memory.graph import EvidenceGraph, GraphIntegrityError
from engine.memory.intervals import Interval, IntervalSet, allen, converse as converse_name


# The instruments a node can be traced to. ``Source.of`` digests the parameters
# that shape the output, so a re-run with different thresholds does not inherit
# the previous run's evidence.
_SRC_LINK = Source.of("engine.threads", "build_threads", stage="links")
_SRC_BEAT = Source.of("engine.threads", "build_beats")
_SRC_TOPIC = Source.of("engine.topics", "cluster_topics")
_SRC_THREAD = Source.of("engine.threads", "build_threads", stage="chains")
_SRC_REFUSAL = Source.of(
    "engine.story_graph", "build_story_graph", stage="unsupported_kind"
)

#: What a node needs in order to be the kind it claims, and what the corpus
#: calls the same signal, so a consumer reads the degradation from the source's
#: own vocabulary rather than a new one.
_SIGNAL_CHECKS: dict[str, str] = {
    "word_timestamps": "has_word_timestamps",
    "speaker_labels": "has_speaker_labels",
    # "stance_model" has no corpus field and never will: it names a capability
    # the pipeline does not have, so it is reported as missing on every build.
}

_CONFLICT_REASON = (
    "conflict needs a stance model to see opposition that is not yet on the "
    "page; DramaticBeat.polarity is lexical valence, not opposition, and the "
    "parties are unattributed when the transcript carries no speaker labels"
)


class NodeKind(str, Enum):
    """The eight narrative kinds the mission names.

    ``str`` so a node serialises without a custom encoder, following
    ``engine.memory.evidence.Modality``. Five of these are realised by the
    corpus's own beat or link records; ``setup`` and ``payoff`` are the sides of
    an edge, which the corpus holds on ``SetupPayoffLink`` and never as a
    property of a moment; ``conflict`` is not realisable on lexical evidence at
    all and is always emitted as ``UNSUPPORTED``.
    """

    SETUP = "setup"
    CONTEXT = "context"
    DEVELOPMENT = "development"
    CONFLICT = "conflict"
    TURN = "turn"
    PEAK = "peak"
    PAYOFF = "payoff"
    RESOLUTION = "resolution"


#: The two kinds the corpus's beat ladder realises directly, mapped onto the
#: mission's eight. ``continuation``, ``tension`` and ``revelation`` are
#: deliberately absent: they are not node kinds, and relabelling one of them as
#: a ``development`` would be exactly the inference this layer refuses to make.
_BEAT_KINDS: dict[str, NodeKind] = {"turn": NodeKind.TURN,
                                    "resolution": NodeKind.RESOLUTION}


class NodeBasis(str, Enum):
    """How a node's kind was established.

    This is the distinction the audit demanded and the corpus already makes in
    its own export (``engine.story_view`` separates kinds it infers from roles
    it derives and kinds it refuses). A node that hid the difference would
    relabel a position as an observation.
    """

    QUOTED = "quoted"            # a record's own field says this
    POSITIONAL = "positional"    # an edge side or a chain membership
    DERIVED = "derived"          # a new computation over existing records
    UNSUPPORTED = "unsupported"  # asserted absent, with the reason kept


@dataclass(frozen=True)
class NodeLink(EvidenceLink):
    """One typed edge between story nodes.

    ``relation`` is an Allen name — the *temporal shape* of how two nodes fit,
    computed from their spans rather than assumed — and ``evidence_id`` names the
    record backing the *link*, which is a different question from the record
    backing either node.

    ``source`` exists because ``EvidenceLink`` holds only one end of its edge and
    so cannot reverse it: ``EvidenceGraph`` derives a converse from the holder at
    index time, but the graph's own integrity check compares two *declared*
    directions, so the reverse edge still has to be built and stored on the node
    it belongs to. Carrying the source lets ``converse()`` build it instead of a
    call site writing the second direction by hand and spelling it wrong. The
    evidence graph ignores the field — it reads ``target`` and ``relation`` — so
    nothing downstream changes.

    ``converse_recorded`` is the audit flag: the reverse edge exists in the graph
    and was derived with ``converse()`` rather than authored.

    Subclassing ``EvidenceLink`` means the relation is validated on construction
    by the base class and read by ``EvidenceGraph`` without that module knowing a
    story node exists — a typo in a relation is a loud error here, not an
    unmatchable string later.
    """

    source: str
    evidence_id: str = ""
    converse_recorded: bool = False

    def converse(self) -> NodeLink:
        """The reverse edge, derived rather than hand-written."""
        return NodeLink(
            source=self.target, target=self.source,
            relation=converse_name(self.relation),
            evidence_id=self.evidence_id,
            converse_recorded=self.converse_recorded,
        )


@dataclass(frozen=True, kw_only=True)
class StoryNode(Evidence):
    """One evidence-backed story node: a kind, the spans where it holds, and the
    evidence that backs it — never a kind alone.

    ``intervals`` is the answer to "where in the source does this beat hold" and
    its cardinality is data. A unit-derived node has cardinality 1 because the
    corpus spine is one span per unit; the variable-cardinality kinds are
    ``context`` (a subject discussed in several separated stretches) and
    ``development`` (a chain whose members are not contiguous in time), and
    those are the two shapes the corpus's single-span records could not express.

    ``span_resolution`` names the reproducible query that produced
    ``intervals``, so a consumer can re-derive the spans from the corpus alone
    and check them against the node. ``cues`` are the verbatim phrases the
    source record already quotes, bounded as that record bounds them; a node
    never holds text, so the corpus's one copy per unit cannot drift.

    ``kw_only`` because ``Evidence``'s trailing defaults would otherwise force a
    default onto ``kind`` and ``basis``, and a node without a kind is not a node.
    """

    kind: NodeKind
    basis: NodeBasis
    unit_indices: tuple[int, ...]      # spine members, chronological
    span_resolution: str               # the reproducible query behind intervals
    evidence_ids: tuple[str, ...]      # ids in the evidence set
    derives_from: tuple[str, ...]      # e.g. "engine.threads.DramaticBeat#kind"
    cues: tuple[str, ...]              # verbatim, capped as the source caps them

    # ── the honest absences ──
    unsupported_reason: str = ""        # non-empty iff basis == UNSUPPORTED
    requires: tuple[str, ...] = ()      # signals this node degrades without
    degraded_by: tuple[str, ...] = ()   # which of requires the input lacked
    timing_precision: str = ""          # word_aligned | character_proportional

    def __post_init__(self) -> None:
        super().__post_init__()
        # ``grounded`` is a function of ``basis`` on a node, not an independent
        # claim: a kind a record states (QUOTED) or a position a record names
        # (POSITIONAL) is an observation, and a reconstruction (DERIVED) or an
        # assertion (UNSUPPORTED) is not. Deriving it here is what stops a node
        # from presenting an inference as an observation — nothing else in the
        # stack is positioned to catch the two disagreeing.
        object.__setattr__(
            self, "grounded",
            self.basis not in (NodeBasis.DERIVED, NodeBasis.UNSUPPORTED),
        )

    @property
    def node_id(self) -> str:
        """``Evidence.id`` under the name the graph's own vocabulary uses."""
        return self.id


@dataclass(frozen=True)
class SetupPayoffAssertion:
    """A setup→payoff relation the corpus did not infer, supplied by a caller.

    ``engine.threads`` caps inference at 6 units / 150 s, which is the right
    bound for *guessing* and the wrong one for *holding*: a setup paid off three
    hours later is a real structure an editor can see and the lexical evidence
    cannot. An assertion carries it as a node with ``basis == UNSUPPORTED``, so
    the graph represents the relation rather than being unable to, and never
    presents it as something the analyser found. ``rationale`` is the caller's
    own sentence and is never generated here — an assertion without one is a
    claim with nothing to check, and is rejected rather than carried.
    """

    setup_unit: int
    payoff_unit: int
    rationale: str


@dataclass(frozen=True)
class StoryGraph:
    """The story graph: evidence-backed nodes over one source.

    A projection, not a store: it is re-derived from the corpus by a pure
    function and holds no transcript text. ``unsupported_kinds`` names every one
    of the eight kinds this build could not support, with the reason — absence
    reported, not papered over.
    """

    nodes: tuple[StoryNode, ...]
    evidence: EvidenceSet
    unsupported_kinds: tuple[tuple[NodeKind, str], ...]

    def node(self, node_id: str) -> StoryNode | None:
        """The node named by ``node_id``, or ``None`` — reported, not raised."""
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def by_kind(self, kind: NodeKind) -> tuple[StoryNode, ...]:
        """Every node of one kind, in the graph's deterministic order."""
        return tuple(n for n in self.nodes if n.kind is kind)

    def checked(self, *, strict: bool = True) -> EvidenceGraph:
        """An ``EvidenceGraph`` over these nodes, checking their own links.

        Every ``NodeLink`` this layer builds is an Allen relation computed from
        the two nodes' actual spans, so the geometry check the evidence stack
        already implements either confirms the edge or names what is wrong with
        it. Building the index here rather than in this module is the point: a
        story edge is verified by the same code as every other edge, and a
        hand-written converse or an impossible ordering is caught instead of
        silently shipped.

        The index is always built non-strict and the report is then escalated by
        hand, because ``EvidenceGraph``'s own ``strict`` treats a
        ``bounds_mismatch`` as a failure and this layer produces one on every
        multi-member chain. A development node's link to a member names the
        relation one of *its* spans bears to the member's span — ``equals`` for
        a member that is a stretch of the chain on its own — and that is the
        relation an editor acts on. The two items' *bounding boxes* necessarily
        disagree: a chain's extent contains its member's, so the bounds reading
        says ``contains``. ``engine.memory.graph`` classifies that disagreement
        as information the edge is still traversable and still true, so it is
        surfaced in ``report()`` and never escalated here. What ``strict``
        raises on is the class a graph cannot carry at all: a dangling or
        self-directed edge, two declared relations disagreeing about one pair, a
        contradiction no member pair realises, a cycle whose ordering is a
        temporal impossibility, a duplicated id, or a relation that is not an
        Allen name.
        """
        graph = EvidenceGraph.build(self.nodes, strict=False)
        if strict:
            report = graph.report()
            if _integrity_failure(report):
                raise GraphIntegrityError(_describe_integrity(report))
        return graph


# ── identifiers and spans ──────────────────────────────────────────────────


def _node_id(kind: NodeKind, basis: NodeBasis, unit_indices: tuple[int, ...]) -> str:
    """A stable id from the kind, the basis and the spine members.

    Two builds over the same corpus produce the same ids, and two different
    nodes cannot collide: the kind and the basis separate a unit that is a
    setup because an edge names it from the same unit asserted as one, and the
    member list separates two subjects or two chains.
    """
    payload = f"{kind.value}|{basis.value}|" + ",".join(map(str, unit_indices))
    return f"{kind.value}:{fingerprint(payload)}"


def _sort_key(node: StoryNode) -> tuple:
    """Source order, then kind name, then id — deterministic across rebuilds.

    A node with no spans sorts last rather than first, so an unsupported node
    never displaces a beat a consumer is looking for.
    """
    bounds = node.intervals.bounds
    start = bounds.start if bounds is not None else float("inf")
    return (start, node.kind.value, node.id)


def _spans_for(spans: list[Interval], indices) -> IntervalSet:
    """The spans of the given units, normalised.

    Members are merged when they touch, so the result's cardinality is the
    number of *time-separated* stretches rather than the count of units — a
    topic whose members are adjacent in time is one stretch, and a chain whose
    members are scattered across the source is several. That is the
    variable-cardinality answer the corpus's index lists and bounding spans
    could not give.
    """
    return IntervalSet([spans[i] for i in indices if 0 <= i < len(spans)])


def _timing_for(timing: list[str], indices) -> str:
    """The precision of the spans, propagated from the spine.

    ``word_aligned`` only when every member is; one estimated sentence in a
    subject's three runs makes the whole node's spans estimated, because a
    consumer quoting the node is quoting a span of measured or estimated
    precision and must know which.
    """
    if not indices:
        return ""
    values = {timing[i] for i in indices if 0 <= i < len(timing)}
    return "word_aligned" if values == {"word_aligned"} else "character_proportional"


def _missing_signals(corpus: EditorialCorpus, requires: tuple[str, ...]) -> tuple[str, ...]:
    """Which of ``requires`` this corpus does not carry.

    A capability the pipeline simply does not have is missing on every build,
    which is the honest reading: the node degrades without it, and saying so is
    what ``degraded_by`` is for.
    """
    out = []
    for name in requires:
        attr = _SIGNAL_CHECKS.get(name)
        if attr is None or not getattr(corpus, attr, False):
            out.append(name)
    return tuple(out)


# ── the projection ─────────────────────────────────────────────────────────


def build_story_graph(
    corpus: EditorialCorpus,
    *,
    assertions: Sequence[SetupPayoffAssertion] = (),
) -> StoryGraph:
    """Project an ``EditorialCorpus`` onto evidence-backed story nodes.

    Args:
        corpus: the corpus to project. Never mutated.
        assertions: setup→payoff relations the corpus did not infer — typically
            far outside its inference window — carried as ``UNSUPPORTED`` nodes
            so the graph can hold a structure the lexical evidence cannot reach.
            An assertion whose unit is out of range, or that carries no
            rationale, is a caller error and raises ``ValueError``.

    Returns:
        A ``StoryGraph``. A corpus with no links, no topics and no threads
        yields the single unsupported-conflict node plus an entry in
        ``unsupported_kinds`` for every other kind — an empty analysis is
        reported, never padded with invented beats.

    Deterministic and pure; see the module docstring.
    """
    spans = [Interval(u.start, u.end) for u in corpus.units]
    timing = [u.timing_source for u in corpus.units]

    evidence: list[Evidence] = []
    nodes: dict[str, StoryNode] = {}
    order: list[str] = []
    pending: dict[str, list[NodeLink]] = {}

    def remember(node: StoryNode) -> None:
        nodes[node.id] = node
        order.append(node.id)

    # ── setup and payoff: the edge sides the corpus never names as a kind ──
    side_links: dict[tuple[NodeKind, int], list[int]] = {}
    for i, lk in enumerate(corpus.links):
        if not (0 <= lk.setup_unit < len(spans) and 0 <= lk.payoff_unit < len(spans)):
            # A link whose unit does not resolve cannot ground a node. The
            # corpus's own reader rejects such a record, so this guard is
            # defence in depth rather than a normal path.
            continue
        evidence.append(Evidence(
            id=f"link:{i}",
            claim=(f"Unit {lk.setup_unit} sets up unit {lk.payoff_unit} "
                   f"({lk.kind})."),
            intervals=IntervalSet.of(
                (lk.setup_start, lk.setup_end), (lk.payoff_start, lk.payoff_end)),
            modality=Modality.EDITORIAL,
            source=_SRC_LINK,
            confidence=lk.confidence,
            rationale=lk.reason,
            grounded=True,
        ))
        side_links.setdefault((NodeKind.SETUP, lk.setup_unit), []).append(i)
        side_links.setdefault((NodeKind.PAYOFF, lk.payoff_unit), []).append(i)

    for (kind, unit), link_ids in sorted(side_links.items()):
        remember(_edge_side_node(kind, unit, link_ids, corpus, spans, timing))

    # ── turn, resolution and peak: kinds a beat record states directly ──
    for i, beat in enumerate(corpus.beats):
        if not (0 <= beat.unit_index < len(spans)):
            continue
        kinds: list[NodeKind] = []
        if beat.kind in _BEAT_KINDS:
            kinds.append(_BEAT_KINDS[beat.kind])
        if beat.is_thread_peak or beat.is_corpus_peak:
            kinds.append(NodeKind.PEAK)
        if not kinds:
            continue
        evidence.append(Evidence(
            id=f"beat:{i}",
            claim=f"Unit {beat.unit_index} is a {beat.kind} beat in its story.",
            intervals=IntervalSet.of((beat.start, beat.end)),
            modality=Modality.EDITORIAL,
            source=_SRC_BEAT,
            confidence=beat.confidence,
            rationale=beat.reason,
            grounded=True,
        ))
        for kind in kinds:
            if kind is NodeKind.PEAK:
                flags = (["is_thread_peak"] if beat.is_thread_peak else []) + (
                    ["is_corpus_peak"] if beat.is_corpus_peak else [])
                scope = " and of the corpus" if beat.is_corpus_peak else ""
                claim = (f"Unit {beat.unit_index} is the dramatic peak of its "
                         f"thread{scope}.")
                derives = tuple(
                    f"engine.threads.DramaticBeat[{i}]#{f}" for f in flags)
                # ``_raw_intensity`` adds up to 0.30 from rush and emphatic
                # pause only when rhythm is present, so a prosody-free corpus
                # measures every beat against a compressed scale. The number is
                # not adjusted to compensate; the degradation is reported.
                requires = ("word_timestamps",)
            else:
                claim = f"Unit {beat.unit_index} is a {kind.value} in its story."
                derives = (f"engine.threads.DramaticBeat[{i}]#kind",)
                requires = ()
            remember(StoryNode(
                claim=claim,
                intervals=IntervalSet.of(spans[beat.unit_index]),
                modality=Modality.EDITORIAL,
                source=_SRC_BEAT,
                confidence=beat.confidence,
                rationale=beat.reason,
                id=_node_id(kind, NodeBasis.QUOTED, (beat.unit_index,)),
                links=(),
                kind=kind,
                basis=NodeBasis.QUOTED,
                unit_indices=(beat.unit_index,),
                span_resolution=f"units[{beat.unit_index}].span",
                evidence_ids=(f"beat:{i}",),
                derives_from=derives,
                cues=tuple(beat.cues),
                requires=requires,
                degraded_by=_missing_signals(corpus, requires),
                timing_precision=timing[beat.unit_index],
            ))

    # ── context: a subject's spans, reconstructed ──
    for i, topic in enumerate(corpus.topics):
        members = tuple(
            u for u in topic.unit_indices if 0 <= u < len(spans))
        if not members:
            continue
        intervals = _spans_for(spans, members)
        evidence.append(Evidence(
            id=f"topic:{i}",
            claim=(f"The subject {topic.label!r} is discussed in "
                   f"{len(members)} passage(s)."),
            intervals=intervals,
            modality=Modality.EDITORIAL,
            source=_SRC_TOPIC,
            confidence=topic.confidence,
            rationale=topic.reason,
            grounded=True,
        ))
        remember(StoryNode(
            claim=(f"The subject {topic.label!r} is discussed across "
                   f"{intervals.cardinality} separated stretch(es) of this "
                   f"source."),
            intervals=intervals,
            modality=Modality.EDITORIAL,
            source=_SRC_TOPIC,
            confidence=topic.confidence,
            rationale=topic.reason,
            id=_node_id(NodeKind.CONTEXT, NodeBasis.DERIVED, members),
            links=(),
            kind=NodeKind.CONTEXT,
            basis=NodeBasis.DERIVED,
            unit_indices=members,
            span_resolution=(f"topics[{i}].unit_indices["
                             + ",".join(map(str, members)) + "].spans"),
            evidence_ids=(f"topic:{i}",),
            derives_from=(f"engine.topics.Topic[{i}]#unit_indices",),
            cues=tuple(topic.evidence),
            timing_precision=_timing_for(timing, members),
        ))

    # ── development: a chain's membership, as the spans it occupies ──
    for i, thread in enumerate(corpus.threads):
        chain = tuple(
            dict.fromkeys(u for u in thread.unit_indices if 0 <= u < len(spans)))
        if len(chain) < 2:
            # A one-member chain is a moment, not a development; the kind is
            # reported unsupported on a corpus where no story has two members.
            continue
        intervals = _spans_for(spans, chain)
        evidence.append(Evidence(
            id=f"thread:{i}",
            claim=(f"A story runs through {len(chain)} passage(s) in "
                   f"{intervals.cardinality} separated stretch(es)."),
            intervals=intervals,
            modality=Modality.EDITORIAL,
            source=_SRC_THREAD,
            confidence=thread.confidence,
            rationale=thread.reason,
            grounded=True,
        ))
        remember(StoryNode(
            claim=(f"A story develops across {len(chain)} passage(s) in "
                   f"{intervals.cardinality} separated stretch(es) of this "
                   f"source."),
            intervals=intervals,
            modality=Modality.EDITORIAL,
            source=_SRC_THREAD,
            confidence=thread.confidence,
            rationale=thread.reason,
            id=_node_id(NodeKind.DEVELOPMENT, NodeBasis.POSITIONAL, chain),
            links=(),
            kind=NodeKind.DEVELOPMENT,
            basis=NodeBasis.POSITIONAL,
            unit_indices=chain,
            span_resolution=(f"threads[{i}].unit_indices["
                             + ",".join(map(str, chain)) + "].spans"),
            evidence_ids=(f"thread:{i}",),
            derives_from=(f"engine.threads.StoryThread[{i}]#unit_indices",),
            cues=tuple(thread.evidence),
            timing_precision=_timing_for(timing, chain),
        ))

    # ── conflict: the kind this layer must refuse to infer ──
    remember(StoryNode(
        claim="Conflict is not observable in this source.",
        intervals=IntervalSet.empty(),
        modality=Modality.EDITORIAL,
        source=_SRC_REFUSAL,
        confidence=0.0,
        rationale=_CONFLICT_REASON,
        id=_node_id(NodeKind.CONFLICT, NodeBasis.UNSUPPORTED, ()),
        links=(),
        kind=NodeKind.CONFLICT,
        basis=NodeBasis.UNSUPPORTED,
        unit_indices=(),
        span_resolution="none — the kind is unsupported and claims no spans",
        evidence_ids=(),
        derives_from=("engine.story_view#unsupported_relationships",),
        cues=(),
        unsupported_reason=_CONFLICT_REASON,
        requires=("stance_model", "speaker_labels"),
        degraded_by=_missing_signals(corpus, ("stance_model", "speaker_labels")),
        timing_precision="",
    ))

    # ── asserted relations, held rather than inferred ──
    for k, assertion in enumerate(assertions):
        for unit in (assertion.setup_unit, assertion.payoff_unit):
            if not (0 <= unit < len(spans)):
                raise ValueError(
                    f"assertion names unit {unit}, but the corpus has "
                    f"{len(spans)} unit(s)"
                )
        if not assertion.rationale.strip():
            raise ValueError(
                "an asserted setup→payoff relation must carry a rationale"
            )
        eid = f"assertion:{k}"
        relation = allen(spans[assertion.setup_unit], spans[assertion.payoff_unit])
        evidence.append(Evidence(
            id=eid,
            claim=(f"Unit {assertion.setup_unit} sets up unit "
                   f"{assertion.payoff_unit} (asserted, unsupported)."),
            intervals=IntervalSet.of(
                spans[assertion.setup_unit], spans[assertion.payoff_unit]),
            modality=Modality.EDITORIAL,
            source=Source.of(
                "engine.story_graph", "build_story_graph", stage="assertion"),
            confidence=0.0,
            rationale=assertion.rationale,
            grounded=False,
        ))
        for kind, unit in ((NodeKind.SETUP, assertion.setup_unit),
                           (NodeKind.PAYOFF, assertion.payoff_unit)):
            positional = _node_id(kind, NodeBasis.POSITIONAL, (unit,))
            asserted = _node_id(kind, NodeBasis.UNSUPPORTED, (unit,))
            if positional in nodes or asserted in nodes:
                # The corpus already named this unit on this side, or an earlier
                # assertion did: the new relation becomes an edge on the
                # existing node rather than a second node with a weaker basis.
                continue
            remember(StoryNode(
                claim=(f"Unit {unit} is the {kind.value} side of an asserted "
                       f"relation."),
                intervals=IntervalSet.of(spans[unit]),
                modality=Modality.EDITORIAL,
                source=_SRC_REFUSAL,
                confidence=0.0,
                rationale=assertion.rationale,
                id=asserted,
                links=(),
                kind=kind,
                basis=NodeBasis.UNSUPPORTED,
                unit_indices=(unit,),
                span_resolution=f"units[{unit}].span",
                evidence_ids=(eid,),
                derives_from=(
                    "engine.story_graph.build_story_graph#assertions",),
                cues=(),
                unsupported_reason=(
                    f"no SetupPayoffLink in the corpus names unit {unit} as the "
                    f"{kind.value} side, so the relation is asserted rather "
                    f"than inferred"),
                timing_precision=timing[unit],
            ))

    # ── links: Allen relations computed from the two nodes' actual spans ──
    for i, lk in enumerate(corpus.links):
        if not (0 <= lk.setup_unit < len(spans) and 0 <= lk.payoff_unit < len(spans)):
            continue
        source_id = _node_id(NodeKind.SETUP, NodeBasis.POSITIONAL, (lk.setup_unit,))
        target_id = _node_id(NodeKind.PAYOFF, NodeBasis.POSITIONAL, (lk.payoff_unit,))
        if source_id not in nodes or target_id not in nodes:
            continue
        forward = NodeLink(
            source=source_id, target=target_id,
            relation=allen(spans[lk.setup_unit], spans[lk.payoff_unit]),
            evidence_id=f"link:{i}", converse_recorded=True)
        pending.setdefault(source_id, []).append(forward)
        pending.setdefault(target_id, []).append(forward.converse())

    for i, thread in enumerate(corpus.threads):
        chain = tuple(
            dict.fromkeys(u for u in thread.unit_indices if 0 <= u < len(spans)))
        if len(chain) < 2:
            continue
        dev_id = _node_id(NodeKind.DEVELOPMENT, NodeBasis.POSITIONAL, chain)
        if dev_id not in nodes:
            continue
        dev = nodes[dev_id].intervals
        for unit in chain:
            hit = dev.intervals_in(spans[unit])
            if not hit:
                continue
            member = _unit_member_id(nodes, unit)
            if member is None:
                # A chain member the corpus gave no beat to has no node to link;
                # its span still counts toward the development's intervals, so
                # the chain's extent is reported even where its interior is not.
                continue
            forward = NodeLink(
                source=dev_id, target=member,
                relation=hit[0].relation(spans[unit]),
                evidence_id=f"thread:{i}", converse_recorded=True)
            pending.setdefault(dev_id, []).append(forward)
            pending.setdefault(member, []).append(forward.converse())

    for k, assertion in enumerate(assertions):
        relation = allen(spans[assertion.setup_unit], spans[assertion.payoff_unit])
        source_id = _node_id(NodeKind.SETUP, NodeBasis.POSITIONAL,
                             (assertion.setup_unit,))
        if source_id not in nodes:
            source_id = _node_id(NodeKind.SETUP, NodeBasis.UNSUPPORTED,
                                 (assertion.setup_unit,))
        target_id = _node_id(NodeKind.PAYOFF, NodeBasis.POSITIONAL,
                             (assertion.payoff_unit,))
        if target_id not in nodes:
            target_id = _node_id(NodeKind.PAYOFF, NodeBasis.UNSUPPORTED,
                                 (assertion.payoff_unit,))
        if source_id not in nodes or target_id not in nodes:
            continue
        forward = NodeLink(
            source=source_id, target=target_id, relation=relation,
            evidence_id=f"assertion:{k}", converse_recorded=True)
        pending.setdefault(source_id, []).append(forward)
        pending.setdefault(target_id, []).append(forward.converse())

    for node_id in order:
        links = tuple(pending.get(node_id, ()))
        if links:
            nodes[node_id] = replace(nodes[node_id], links=links)

    built = sorted(nodes.values(), key=_sort_key)
    present = {n.kind for n in built}
    unsupported = []
    for kind in NodeKind:
        # ``conflict`` has a node and is still listed: the kind is unsupported
        # on every corpus, and a consumer asking "what could this graph not
        # support?" must not have to reconcile the two records.
        if kind not in present or kind is NodeKind.CONFLICT:
            unsupported.append((kind, _kind_reason(corpus, kind)))

    return StoryGraph(
        nodes=tuple(built),
        evidence=EvidenceSet(items=evidence),
        unsupported_kinds=tuple(unsupported),
    )


# ── node construction helpers ──────────────────────────────────────────────


def _edge_side_node(
    kind: NodeKind,
    unit: int,
    link_ids: list[int],
    corpus: EditorialCorpus,
    spans: list[Interval],
    timing: list[str],
) -> StoryNode:
    """One setup or payoff node, from every link that names this unit on that side.

    ``DramaticBeat.kind`` documents ``"setup"`` and assigns it nowhere, so a
    setup can only be the *position* a record names. The evidence for the node
    is the edge's own, and where several edges name the same unit the node
    carries the strongest one's measurement and rationale and cites them all —
    the number is selected, never recomputed, and a weak second edge cannot
    drag down a strong first one nor inflate it.
    """
    side = "setup" if kind is NodeKind.SETUP else "payoff"
    field = "setup_unit" if kind is NodeKind.SETUP else "payoff_unit"
    best = max(link_ids, key=lambda i: corpus.links[i].confidence)
    link = corpus.links[best]
    return StoryNode(
        claim=(f"Unit {unit} is the {side} side of {len(link_ids)} "
               f"setup→payoff relation(s)."),
        intervals=IntervalSet.of(spans[unit]),
        modality=Modality.EDITORIAL,
        source=_SRC_LINK,
        confidence=link.confidence,
        rationale=link.reason,
        id=_node_id(kind, NodeBasis.POSITIONAL, (unit,)),
        links=(),
        kind=kind,
        basis=NodeBasis.POSITIONAL,
        unit_indices=(unit,),
        span_resolution=f"units[{unit}].span",
        evidence_ids=tuple(f"link:{i}" for i in link_ids),
        derives_from=tuple(
            f"engine.threads.SetupPayoffLink[{i}]#{field}" for i in link_ids),
        cues=tuple(link.evidence),
        timing_precision=timing[unit],
    )


def _unit_member_id(nodes: dict[str, StoryNode], unit: int) -> str | None:
    """The id of the node covering ``unit`` for a development edge, if any.

    A unit can carry several nodes — it may be one story's payoff and another's
    setup at once — so the first in the graph's order is returned; the
    development links to the beats the corpus found in the chain, and the
    relation is computed from the spans either way.
    """
    for node in nodes.values():
        if node.unit_indices == (unit,):
            return node.id
    return None


def _kind_reason(corpus: EditorialCorpus, kind: NodeKind) -> str:
    """Why a kind could not be supported on this corpus.

    ``conflict`` is unsupported on every corpus — the stance model the kind
    needs is not a signal an input can carry — while the other seven are
    unsupported *here*, because this corpus holds no record that states them.
    """
    if kind is NodeKind.CONFLICT:
        return _CONFLICT_REASON
    if kind is NodeKind.SETUP:
        return ("no setup→payoff link exists in this corpus, so no unit holds "
                "the setup side of an edge")
    if kind is NodeKind.PAYOFF:
        return ("no setup→payoff link exists in this corpus, so no unit holds "
                "the payoff side of an edge")
    if kind is NodeKind.CONTEXT:
        return "no subject cluster exists over this corpus"
    if kind is NodeKind.DEVELOPMENT:
        return "no story thread has more than one member"
    if kind is NodeKind.TURN:
        return "no passage is led by a contrast cue"
    if kind is NodeKind.PEAK:
        return "no beat is flagged as a peak"
    return "no passage carries a solution or result cue"


# ── the graph's own integrity, escalated by hand ─────────────────────────────


def _integrity_failure(report) -> bool:
    """Does this report name something a graph cannot carry at all?

    The complement of ``IntegrityReport.ok`` in the one category that matters
    here: a ``bounds_mismatch`` is kept and traversable by the evidence graph's
    own rules, so it is not a failure even though ``ok`` counts it. Everything
    else ``ok`` rejects is a failure — a dangling edge, a self-link, two
    declared relations disagreeing about one pair, a contradiction no member
    pair realises, a temporally impossible cycle, a duplicated id, or a
    relation that is not an Allen name.
    """
    return bool(
        report.dangling
        or report.self_links
        or report.converse_conflicts
        or report.cycles
        or report.duplicates
        or report.unsupported
        or any(g.kind == "contradiction" for g in report.geometry_conflicts)
    )


def _describe_integrity(report) -> str:
    """Name every failing category, mirroring ``engine.memory.graph``'s own
    report so a failure here reads the same as one the store raised."""
    lines = ["story graph integrity check failed:"]
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
    contradictions = [g for g in report.geometry_conflicts if g.kind == "contradiction"]
    if contradictions:
        lines.append(f"  {len(contradictions)} contradiction(s): "
                     + ", ".join(f"{g.source}->{g.target}: declared {g.declared}, "
                                 f"found {g.computed}"
                                 for g in contradictions))
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
