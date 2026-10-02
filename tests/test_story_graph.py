"""Failure-first tests for ``engine.story_graph`` — the narrative node layer.

``engine.corpus`` finds a story's pieces but cannot say "*this* moment is the
setup": ``DramaticBeat.kind`` documents ``"setup"`` and assigns it nowhere, every
temporal record it writes is one bounding span, and the kinds it cannot support
are simply absent. ``engine.story_graph`` is the projection that closes those
three, and these tests are what pins the closure — every one feeds the layer
something that would expose a relapse: a corpus whose beats never name a setup,
a subject discussed in three separated stretches, a chain whose ``arc`` label
collapsed two different structures into one string, a negative-polarity passage
that must not become "conflict", an asserted relation three hours outside the
inference window, a record naming a unit the spine does not hold.

The hazards these tests pin, in the order an editor would meet them:

- the phantom ``"setup"`` beat value — a setup node must come from edge
  *position*, because trusting the comment finds zero setups on any corpus;
- the per-unit single-kind limit — a moment that is one story's payoff and
  another's setup must carry both kinds, since a graph that still forced one
  function per moment could not represent the junction the corpus's own
  ``unit_threads`` exists for;
- the bounding-span lie — a subject discussed three times must report three
  stretches and the interruptions between them, not the one span that contains
  the gaps;
- ``StoryThread.arc``'s adjacent-duplicate collapse — two chains of different
  length flatten to one label, so a node must be keyed by membership, not by the
  label;
- conflict relabelled from lexical valence — the kind must be refused, with the
  stance-model reason kept, on every corpus;
- prosody-free peaks — the number is the source's measurement, recorded and
  never rescored, and the degradation is named in the corpus's own vocabulary;
- the Allen edge — a typo must raise rather than become an unmatchable string,
  and the converse must be derived rather than hand-written, so the graph never
  carries two spellings of one relation;
- the integrity check — it must surface the geometry it cannot confirm without
  failing the graph on information, and escalate the lies it cannot carry;
- the distant setup→payoff — the corpus caps *inference* at 6 units / 150 s but
  a graph must still *hold* a three-hour relation, as unsupported, and must
  reject an assertion with no rationale or a unit outside the spine;
- the honest empty corpus — absence is reported for all eight kinds, never padded
  with invented beats;
- and the layer's purity: no text copied from the spine, no mutation of the
  corpus, byte-identical rebuilds, and spans that survive a round trip through
  the corpus's own reader.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from engine.corpus import (
    BuildInfo,
    CorpusUnit,
    EditorialCorpus,
    build_corpus,
)
from engine.corpus_io import read_corpus
from engine.memory.graph import GraphIntegrityError
from engine.memory.intervals import Interval, IntervalSet, converse
from engine.rhythm import RhythmProfile
from engine.story_graph import (
    NodeBasis,
    NodeKind,
    NodeLink,
    SetupPayoffAssertion,
    StoryGraph,
    build_story_graph,
)
from engine.threads import DramaticBeat, SetupPayoffLink, StoryThread
from engine.topics import Topic
from models.core import Segment, Word
from tests.story_fixtures import duration_for, transcript


# ── fixtures ────────────────────────────────────────────────────────────────


def _seg(a: float, b: float, text: str, words: bool = True) -> Segment:
    """A timed segment like a transcript carries one, word for word."""
    ws = None
    if words:
        parts = text.split()
        span = (b - a) / max(1, len(parts))
        ws = [Word(w, a + i * span, a + (i + 1) * span, 0.9)
              for i, w in enumerate(parts)]
    return Segment(a, b, text, ws)


def _real(n_units: int = 12) -> EditorialCorpus:
    """A corpus built the way the pipeline builds one — the analyser's own
    output, not a hand-written stand-in, so a test that finds a setup node is
    finding one on a real beat ladder."""
    return build_corpus(transcript(n_units), duration=duration_for(n_units))


def _unit(index: int, start: float, end: float, *, text: str = "a passage",
          timing: str = "word_aligned") -> CorpusUnit:
    return CorpusUnit(
        index=index, start=start, end=end, segment_indices=[index], text=text,
        sentence_count=1, word_count=len(text.split()), coherence=0.8,
        boilerplate=False, timing_source=timing,
    )


def _beat(unit: int, start: float, end: float, **over) -> DramaticBeat:
    base = dict(
        unit_index=unit, start=start, end=end, kind="continuation",
        polarity="neutral", intensity=0.3, percentile=0.5,
        is_thread_peak=False, is_corpus_peak=False, cues=[], thread_id=0,
        confidence=0.7, reason="a measured beat",
    )
    base.update(over)
    return DramaticBeat(**base)


def _topic(topic_id: int, members: list[int], units: list[CorpusUnit], **over):
    # Spans come from the members that resolve, as the real topic does; a member
    # the spine does not hold is exactly the broken record this suite feeds in.
    spans = [Interval(units[m].start, units[m].end)
             for m in members if 0 <= m < len(units)]
    base = dict(
        topic_id=topic_id, label="the subject", terms=["the", "subject"],
        unit_indices=list(members), segment_indices=[members[0]],
        start=min(s.start for s in spans), end=max(s.end for s in spans),
        runs=len(members), span_share=0.5, confidence=0.8,
        reason="a clustered subject", evidence=[],
    )
    base.update(over)
    return Topic(**base)


def _thread(thread_id: int, chain: list[int], units: list[CorpusUnit], **over):
    spans = [Interval(units[m].start, units[m].end)
             for m in chain if 0 <= m < len(units)]
    base = dict(
        thread_id=thread_id, title="the story", terms=["the", "story"],
        unit_indices=list(chain), links=[], beat_indices=[],
        start=min(s.start for s in spans), end=max(s.end for s in spans),
        arc="continuation", span_share=0.5, confidence=0.8,
        reason="a story chain", evidence=[],
    )
    base.update(over)
    return StoryThread(**base)


def _corpus(units: list[CorpusUnit], *, topics=(), threads=(), beats=(),
            links=(), has_word_timestamps: bool = True,
            has_speaker_labels: bool = False,
            duration: float | None = None) -> EditorialCorpus:
    """A corpus assembled by hand, so a test can hand the layer exactly the
    broken record the hazard needs: a link naming a unit the spine does not
    hold, a chain whose ``arc`` lies, a signal the transcript does not carry."""
    return EditorialCorpus(
        source=None, language="en", detected_languages=["en"],
        duration=duration if duration is not None else
        (units[-1].end if units else 0.0),
        build=BuildInfo(),
        has_word_timestamps=has_word_timestamps,
        has_speaker_labels=has_speaker_labels,
        units=list(units), topics=list(topics), threads=list(threads),
        beats=list(beats), links=list(links), redundancy_groups=[],
        speaker_blocks=[], broll=[],
        rhythm=RhythmProfile(
            available=has_word_timestamps, total_words=0, speech_seconds=0.0,
            speech_rate_wpm=None, median_rate_wpm=None, pause_counts={},
            pause_rate_per_minute={}, filler_count=0,
            filler_rate_per_minute=None, false_start_count=0,
            incomplete_count=0, per_unit=[], confidence=1.0,
            reason="synthetic",
        ),
        arc_summary="",
        unit_threads=[[] for _ in units],
        notes=[],
    )


def _link(setup: int, payoff: int, units: list[CorpusUnit], **over) -> SetupPayoffLink:
    # A unit the spine does not hold gets a placeholder timecode, as a corrupt
    # record would: the span does not resolve, and that is the point.
    def span(unit: int) -> tuple[float, float]:
        if 0 <= unit < len(units):
            return units[unit].start, units[unit].end
        return 0.0, 0.0

    s_start, s_end = span(setup)
    p_start, p_end = span(payoff)
    base = dict(
        setup_unit=setup, payoff_unit=payoff, kind="question_answer",
        strength=0.6,
        setup_start=s_start, setup_end=s_end,
        payoff_start=p_start, payoff_end=p_end,
        confidence=0.6, reason="setup asks, payoff answers",
        evidence=["the question", "the answer"],
    )
    base.update(over)
    return SetupPayoffLink(**base)


# ── the phantom "setup" beat value ──────────────────────────────────────────


def test_setup_node_comes_from_edge_position_never_from_a_beat_kind():
    """``DramaticBeat.kind`` documents ``"setup"`` at ``engine/threads.py:294``
    and the assignment ladder five lines later never produces it, so a graph
    that trusted the comment would find zero setup nodes on any corpus.

    The corpus's own beat ladder over this transcript emits ``continuation``,
    ``tension`` and ``resolution`` and nothing else, yet a setup *is* there —
    positionally, as the side of an edge. A setup node must therefore be built
    from the links that name the unit, cite every one of them, and claim the
    unit's span rather than a beat's, because the evidence for "this moment is a
    setup" lives on ``SetupPayoffLink`` and nowhere else.
    """
    corpus = _real(12)
    assert "setup" not in {b.kind for b in corpus.beats}, (
        "the corpus's beat ladder learned to assign the phantom 'setup' value, "
        "so a setup node can no longer be assumed positional")

    graph = build_story_graph(corpus)
    setups = graph.by_kind(NodeKind.SETUP)
    assert setups, "a corpus with setup→payoff links yielded no setup nodes"

    for node in setups:
        assert node.basis is NodeBasis.POSITIONAL
        assert len(node.unit_indices) == 1
        unit = node.unit_indices[0]
        # The node cites every edge naming this unit on this side, and nothing
        # else: a setup presented without its edges is a label with no support.
        naming = [i for i, lk in enumerate(corpus.links)
                  if lk.setup_unit == unit]
        assert naming, "a setup node appeared for a unit no link names"
        assert set(node.evidence_ids) == {f"link:{i}" for i in naming}
        assert node.intervals == IntervalSet.of(
            (corpus.units[unit].start, corpus.units[unit].end))
        assert node.span_resolution == f"units[{unit}].span"
        for eid in node.evidence_ids:
            assert graph.evidence.with_id(eid) is not None
        for src in node.derives_from:
            assert "SetupPayoffLink[" in src and src.endswith("#setup_unit")


def test_a_unit_can_be_two_kinds_at_once():
    """``build_beats`` emits exactly one beat per unit, so a passage that is one
    story's payoff and another's setup carries one kind and the corpus's own
    ``unit_threads`` comment names the case it cannot express.

    The node layer must not inherit that limit: the same unit must hold a payoff
    node and a setup node at once, distinct in kind and id, agreeing on the span
    and citing the different edges that made each. A graph that merged them would
    drop half of a junction an editor cuts around.
    """
    corpus = _real(12)
    graph = build_story_graph(corpus)

    setup_units = {n.unit_indices[0] for n in graph.by_kind(NodeKind.SETUP)}
    payoff_units = {n.unit_indices[0] for n in graph.by_kind(NodeKind.PAYOFF)}
    both = setup_units & payoff_units
    assert both, ("no unit is both a setup and a payoff on this corpus, so the "
                  "two-kinds-at-once case is not exercised")

    for unit in both:
        kinds = {n.kind for n in graph.nodes if n.unit_indices == (unit,)}
        assert NodeKind.SETUP in kinds and NodeKind.PAYOFF in kinds
        span = Interval(corpus.units[unit].start, corpus.units[unit].end)
        nodes = [n for n in graph.nodes if n.unit_indices == (unit,)
                 and n.kind in (NodeKind.SETUP, NodeKind.PAYOFF)]
        assert len({n.id for n in nodes}) == len(nodes), (
            "two kinds on one unit collapsed to one node")
        for n in nodes:
            assert n.intervals == IntervalSet.of(span)


# ── the bounding-span lie ───────────────────────────────────────────────────


def test_context_node_reports_the_stretches_not_the_bounding_span():
    """``Topic.start/end`` is one bounding span, so a subject discussed in three
    separated stretches reports a span that silently contains the two gaps — the
    interruption is invisible and a clipper quoting the topic cuts dead air.

    The node's ``intervals`` must be the stretches: cardinality equal to the run
    count, the gaps recoverable, and the duration excluding the interruptions.
    The bounding box the topic flattened must still be the box — that is not a
    different claim, it is the same claim with the holes put back.
    """
    units = [_unit(i, s, e) for i, (s, e) in enumerate(
        [(0.0, 10.0), (10.0, 20.0), (30.0, 40.0), (50.0, 60.0), (60.0, 70.0)])]
    # members [0, 1] touch at 10.0: adjacent in time, so one stretch — the
    # cardinality is the number of *time-separated* runs, not the unit count.
    touching = _topic(0, [0, 1], units, evidence=["one stretch"])
    # members [0, 2, 4] are separated by real interruptions.
    recurring = _topic(1, [0, 2, 4], units, evidence=["first", "middle", "last"])
    corpus = _corpus(units, topics=[touching, recurring])
    graph = build_story_graph(corpus)

    contexts = graph.by_kind(NodeKind.CONTEXT)
    assert {tuple(n.unit_indices) for n in contexts} == {(0, 1), (0, 2, 4)}

    merged = next(n for n in contexts if n.unit_indices == (0, 1))
    assert merged.intervals.cardinality == 1
    assert merged.intervals.duration == pytest.approx(20.0)

    node = next(n for n in contexts if n.unit_indices == (0, 2, 4))
    assert node.basis is NodeBasis.DERIVED
    assert node.intervals.cardinality == 3
    assert node.intervals.duration == pytest.approx(30.0)
    bounds = node.intervals.bounds
    assert bounds == Interval(0.0, 70.0)
    assert bounds.duration == pytest.approx(70.0)
    # The gaps the topic's bounding span hid, recovered as a first-class answer.
    gaps = node.intervals.gaps(bounds)
    assert gaps.cardinality == 2
    assert gaps.duration == pytest.approx(40.0)
    assert node.cues == ("first", "middle", "last")
    assert node.span_resolution.startswith("topics[1].unit_indices[0,2,4]")
    # The node's box is exactly the span the topic reported — same claim, holes
    # and all, so a consumer comparing the two sees no disagreement.
    assert Interval(touching.start, touching.end) != bounds
    assert Interval(recurring.start, recurring.end) == bounds


def test_arc_collapse_is_repaired_by_membership_keyed_nodes():
    """``StoryThread.arc`` joins beat kinds and drops an adjacent duplicate
    (``engine/threads.py:782``), so a chain ``tension → tension → resolution``
    and a chain ``tension → resolution`` produce the identical string — the one
    place the corpus held variable-cardinality information and flattened it.

    A node cannot be keyed by the label, or two different stories would be one
    node. It must be keyed by the chain's members, so the two structures the
    corpus made indistinguishable are two nodes with two memberships.
    """
    units = [_unit(i, i * 10.0, i * 10.0 + 8.0) for i in range(5)]
    thread_a = _thread(0, [0, 1, 2], units, arc="tension → resolution")
    thread_b = _thread(1, [3, 4], units, arc="tension → resolution")
    # The corpus's own records cannot tell these two stories apart:
    assert thread_a.arc == thread_b.arc

    corpus = _corpus(units, threads=[thread_a, thread_b])
    graph = build_story_graph(corpus)

    devs = graph.by_kind(NodeKind.DEVELOPMENT)
    assert len(devs) == 2, "two chains with one arc label collapsed to one node"
    by_chain = {tuple(n.unit_indices) for n in devs}
    assert by_chain == {(0, 1, 2), (3, 4)}
    for node in devs:
        assert node.basis is NodeBasis.POSITIONAL
        # A chain of three separated members occupies three stretches, a chain
        # of two occupies two — the cardinality the arc label threw away.
        assert node.intervals.cardinality == len(node.unit_indices)
        assert node.intervals.cardinality > 1
        assert node.span_resolution.startswith("threads[")
        assert "unit_indices" in node.span_resolution


def test_arc_collapse_nodes_keep_their_own_spans_and_citations():
    """The companion to the arc-collapse test: the two development nodes must
    each cite their own thread's evidence and carry their own span resolution,
    since a node that shared a citation would be indistinguishable from a
    duplicate of the other.
    """
    units = [_unit(i, i * 10.0, i * 10.0 + 8.0) for i in range(5)]
    thread_a = _thread(0, [0, 1, 2], units, arc="tension → resolution",
                       evidence=["the first thread"])
    thread_b = _thread(1, [3, 4], units, arc="tension → resolution",
                       evidence=["the second thread"])
    corpus = _corpus(units, threads=[thread_a, thread_b])
    graph = build_story_graph(corpus)

    devs = graph.by_kind(NodeKind.DEVELOPMENT)
    citations = {n.evidence_ids for n in devs}
    assert citations == {("thread:0",), ("thread:1",)}
    assert {n.cues for n in devs} == {("the first thread",), ("the second thread",)}
    resolutions = sorted(n.span_resolution for n in devs)
    assert resolutions == ["threads[0].unit_indices[0,1,2].spans",
                           "threads[1].unit_indices[3,4].spans"]


# ── conflict: the kind this layer must refuse ───────────────────────────────


def test_conflict_is_refused_not_relabeled_from_polarity():
    """``_polarity`` counts lexicon hits and returns ``positive`` / ``negative``
    / ``neutral`` — valence, not opposition. A graph that mapped a negative
    passage onto ``conflict`` would relabel "this is phrased downbeat" as "two
    parties disagree", and the precondition for the real thing, an attributed
    party, is unavailable without speaker labels.

    The kind must be asserted unsupported on every corpus, with the reason kept,
    and a consumer asking what the graph could not see must find it listed even
    though a node exists — the two records are the same fact twice, not a
    contradiction to reconcile.
    """
    units = [_unit(0, 0.0, 10.0, text="we oppose this plan entirely"),
             _unit(1, 20.0, 30.0, text="they resist the proposal too")]
    # A passage drenched in negative valence and flagged as the corpus peak, so
    # a sloppy mapping had every reason to emit a conflict node from it.
    beats = [
        _beat(0, 0.0, 10.0, kind="tension", polarity="negative",
              intensity=0.95, percentile=1.0, is_thread_peak=True,
              is_corpus_peak=True, cues=["oppose"],
              reason="negative polarity from lexicon hits"),
        _beat(1, 20.0, 30.0, kind="tension", polarity="negative",
              intensity=0.9, percentile=0.9, is_thread_peak=True,
              cues=["resist"], reason="negative polarity"),
    ]
    corpus = _corpus(units, beats=beats)
    graph = build_story_graph(corpus)

    conflicts = graph.by_kind(NodeKind.CONFLICT)
    assert len(conflicts) == 1
    node = conflicts[0]
    assert node.basis is NodeBasis.UNSUPPORTED
    assert not node.grounded
    assert node.intervals.is_empty
    assert node.intervals.bounds is None
    assert node.unit_indices == ()
    assert node.confidence == 0.0
    assert node.cues == ()
    assert "stance" in node.unsupported_reason
    assert "unattributed" in node.unsupported_reason

    # The refusal is listed alongside the kinds this corpus merely happens to
    # lack, so a consumer reads one list and sees the whole of the absence.
    listed = {k for k, _ in graph.unsupported_kinds}
    assert NodeKind.CONFLICT in listed
    for kind in NodeKind:
        if kind not in {n.kind for n in graph.nodes}:
            assert kind in listed

    # Nothing in the graph derived conflict from the valence on either beat.
    assert not any(n.kind is NodeKind.CONFLICT and n.basis is NodeBasis.DERIVED
                   for n in graph.nodes)
    assert not any("polarity" in n.claim and "conflict" in n.claim.lower()
                   for n in graph.nodes)


def test_development_is_chain_membership_never_an_inferred_edge():
    """``engine.story_view`` declares ``develops`` an untyped co-membership
    "never as an inferred edge"; a development node must be the same fact under
    the node taxonomy — a *position* in a chain — and must not synthesise a
    ``SetupPayoffLink``, cite one, or claim to have measured a relation.
    """
    corpus = _real(12)
    graph = build_story_graph(corpus)
    devs = graph.by_kind(NodeKind.DEVELOPMENT)
    assert devs

    for node in devs:
        assert node.basis is NodeBasis.POSITIONAL
        assert len(node.unit_indices) >= 2
        # Chain membership cites the thread, never a link: the corpus's own
        # export refuses to call this an edge, and so must the node.
        assert node.evidence_ids == tuple(
            eid for eid in node.evidence_ids if eid.startswith("thread:"))
        assert not any(eid.startswith("link:") for eid in node.evidence_ids)
        assert node.span_resolution.startswith("threads[")
        assert "unit_indices" in node.span_resolution
        # The chain's members are de-duplicated and in spine order.
        assert node.unit_indices == tuple(sorted(set(node.unit_indices)))

    # A corpus whose every story has one member holds no development at all, and
    # says so rather than emitting a single-member node.
    units = [_unit(i, i * 10.0, i * 10.0 + 8.0) for i in range(3)]
    lone = _corpus(units, threads=[_thread(0, [1], units)])
    lone_graph = build_story_graph(lone)
    assert not lone_graph.by_kind(NodeKind.DEVELOPMENT)
    reasons = {k.value: r for k, r in lone_graph.unsupported_kinds}
    assert "more than one member" in reasons["development"]


# ── degradation: the number is recorded, the absence is named ──────────────


def test_peak_records_the_prosody_degradation_and_never_rescores_it():
    """``_raw_intensity`` adds up to 0.30 from rush and emphatic pause only when
    rhythm is present (``engine/threads.py:471-475``), so on an untimed
    transcript every beat is measured against a compressed scale and a peak is
    *less discriminating*. The node must name that degradation, and must not
    compensate for it — the confidence is the source instrument's measurement,
    recorded verbatim, never adjusted here.
    """
    text = ("Why did the satellite clock fail? The launch was nominal. "
            "The battery was the suspected cause.")
    timed = build_corpus([_seg(0.0, 8.0, text)], duration=12.0)
    untimed = build_corpus([_seg(0.0, 8.0, text, words=False)], duration=12.0)
    assert timed.has_word_timestamps and not untimed.has_word_timestamps

    for corpus in (timed, untimed):
        graph = build_story_graph(corpus)
        peaks = graph.by_kind(NodeKind.PEAK)
        assert peaks, "the corpus flags a peak even without prosody"
        for node in peaks:
            # The claim matches the flags the beat actually carries, so a node
            # saying "and of the corpus" is backed by a beat that is one.
            (eid,) = node.evidence_ids
            assert eid.startswith("beat:")
            beat = corpus.beats[int(eid.split(":")[1])]
            assert beat.is_thread_peak or beat.is_corpus_peak
            assert ("and of the corpus" in node.claim) == beat.is_corpus_peak
            # Recorded, never rescored: the node's number *is* the beat's.
            assert node.confidence == beat.confidence
            assert node.requires == ("word_timestamps",)
            assert set(node.degraded_by) <= set(node.requires)
        # The precision is the spine's for the unit the node actually claims,
        # propagated rather than assumed: a consumer quoting the node is
        # quoting a span of measured or estimated precision and must know which.
        assert all(
            n.timing_precision == corpus.units[n.unit_indices[0]].timing_source
            for n in peaks)

    # The degradation is reported only where it holds.
    fresh = build_story_graph(timed)
    assert all(n.degraded_by == () for n in fresh.by_kind(NodeKind.PEAK))
    starved = build_story_graph(untimed)
    assert all(n.degraded_by == ("word_timestamps",)
               for n in starved.by_kind(NodeKind.PEAK))


def test_timing_precision_reports_the_worst_member_of_a_set():
    """``_timing_for`` reports ``word_aligned`` only when *every* member is
    word-aligned: one estimated sentence in a subject's three runs makes the
    whole node's spans estimated, because a consumer quoting the node is quoting
    a span of measured or estimated precision and must know which — an editor
    who cut to an estimated bound would be cutting to a guess and be told it was
    a measurement.
    """
    units = [_unit(0, 0.0, 10.0, timing="word_aligned"),
             _unit(1, 20.0, 30.0, timing="character_proportional"),
             _unit(2, 40.0, 50.0, timing="word_aligned"),
             _unit(3, 60.0, 70.0, timing="word_aligned")]
    clean = _topic(0, [0, 2, 3], units, evidence=["clean"])
    one_bad = _topic(1, [0, 1, 2], units, evidence=["one estimated member"])
    corpus = _corpus(units, topics=[clean, one_bad],
                     threads=[_thread(0, [0, 1, 3], units)])
    graph = build_story_graph(corpus)

    by_members = {tuple(n.unit_indices): n for n in graph.by_kind(NodeKind.CONTEXT)}
    assert by_members[(0, 2, 3)].timing_precision == "word_aligned"
    assert by_members[(0, 1, 2)].timing_precision == "character_proportional"
    (dev,) = graph.by_kind(NodeKind.DEVELOPMENT)
    assert dev.unit_indices == (0, 1, 3)
    assert dev.timing_precision == "character_proportional"
    # A unit-derived node carries its own precision, and a node claiming no
    # spans claims no precision either.
    for node in graph.nodes:
        if len(node.unit_indices) == 1:
            assert node.timing_precision == \
                corpus.units[node.unit_indices[0]].timing_source
    assert graph.by_kind(NodeKind.CONFLICT)[0].timing_precision == ""


def test_degradation_is_named_in_the_corpus_own_vocabulary():
    """``requires`` names a signal and ``degraded_by`` names which of them the
    input lacked, in the source's own vocabulary — ``has_word_timestamps``,
    ``has_speaker_labels`` — because a consumer reading the degradation off the
    corpus must not have to learn a second set of terms.

    ``stance_model`` names a capability the pipeline does not have and no corpus
    can carry, so it is missing on every build; that is the honest reading, not
    a special case to paper over.
    """
    units = [_unit(0, 0.0, 10.0), _unit(1, 20.0, 30.0)]

    neither = _corpus(units, has_word_timestamps=False, has_speaker_labels=False)
    g_none = build_story_graph(neither)
    conflict = g_none.by_kind(NodeKind.CONFLICT)[0]
    assert set(conflict.requires) == {"stance_model", "speaker_labels"}
    assert set(conflict.degraded_by) == {"stance_model", "speaker_labels"}
    assert all(n.degraded_by == ("word_timestamps",)
               for n in g_none.by_kind(NodeKind.PEAK))

    labelled = _corpus(units, has_word_timestamps=True, has_speaker_labels=True)
    g_some = build_story_graph(labelled)
    conflict = g_some.by_kind(NodeKind.CONFLICT)[0]
    # Speaker labels can be present; a stance model cannot.
    assert "speaker_labels" not in conflict.degraded_by
    assert "stance_model" in conflict.degraded_by
    assert all(n.degraded_by == () for n in g_some.by_kind(NodeKind.PEAK))


def test_speaker_labels_off_a_real_transcript_still_degrade_the_conflict_node():
    """A label attached by an ASR backend is carried through defensively
    (``engine/corpus.py:248-252``), so a real corpus *can* carry speaker labels
    even though ``models.core.Segment`` declares no field for them.

    With labels present only the stance model is missing; without them the
    conflict node reports both, because opposition needs at least two
    attributed parties and unlabelled blocks are turn hypotheses.
    """
    text = "We oppose the plan. They resist it too."
    segs = [_seg(0.0, 5.0, text), _seg(5.0, 10.0, text)]
    for seg in segs:
        seg.speaker = "A"  # type: ignore[attr-defined]
    labelled = build_corpus(segs, duration=12.0)
    assert labelled.has_speaker_labels

    graph = build_story_graph(labelled)
    conflict = graph.by_kind(NodeKind.CONFLICT)[0]
    assert conflict.degraded_by == ("stance_model",)
    assert "speaker_labels" in conflict.requires


# ── the Allen edge ──────────────────────────────────────────────────────────


def test_node_link_rejects_a_relation_the_algebra_does_not_name():
    """A typo in a ``NodeLink.relation`` must raise here, exactly as
    ``Evidence.link`` raises (``engine/memory/evidence.py:209-210``), rather
    than become an unmatchable string a consumer silently never joins on."""
    with pytest.raises(ValueError, match="not an Allen relation"):
        NodeLink(source="a", target="b", relation="befor")

    ok = NodeLink(source="a", target="b", relation="before")
    assert ok.relation == "before"
    assert ok.converse_recorded is False
    assert ok.evidence_id == ""


def test_converse_edges_are_derived_so_the_graph_carries_one_spelling():
    """The reverse of a ``before`` link must be ``after`` via ``converse()``
    (``engine/memory/intervals.py:90-98``); a graph carrying two spellings of
    one relation is the failure ``EvidenceLink`` was written to prevent, and a
    hand-written second direction is how it gets misspelled.

    Every edge this layer emits must therefore be built and its reverse derived,
    so the two directions can never disagree about the shape they describe.
    """
    corpus = _real(12)
    graph = build_story_graph(corpus)

    edges = [(n.id, l) for n in graph.nodes for l in n.links]
    assert edges, "the corpus produced no edges to check the converse on"
    assert all(l.converse_recorded for _, l in edges)

    by_pair: dict[tuple[str, str], str] = {}
    for holder, link in edges:
        assert link.source != link.target
        by_pair[(link.source, link.target)] = link.relation

    for (a, b), relation in by_pair.items():
        reverse = by_pair.get((b, a))
        assert reverse is not None, (
            f"the edge {a}→{b} ({relation}) has no stored reverse")
        assert reverse == converse(relation), (
            f"{a}→{b} declares {relation} while {b}→{a} declares {reverse}; "
            "the two spellings disagree")
        # The derived direction is the relation the spans actually bear, not an
        # assumed one — a setup at t=0 and a payoff at t=24 are ``before``.
        assert relation in {"before", "after", "meets", "met_by", "equals",
                            "contains", "during", "starts", "started_by",
                            "finishes", "finished_by", "overlaps",
                            "overlapped_by"}

    # A link declared before its time is caught by the same geometry check the
    # evidence stack runs on every other edge, not by a checker of this layer's.
    checked = graph.checked()
    setups = graph.by_kind(NodeKind.SETUP)
    payoffs = {n.id: n for n in graph.by_kind(NodeKind.PAYOFF)}
    for node in setups:
        for link in node.links:
            if link.target in payoffs:
                neighbour = next(
                    nb for nb in checked.neighbours(node.id)
                    if nb.item.id == link.target)
                assert neighbour.relation == link.relation
                assert neighbour.converse == converse(link.relation)


# ── the integrity check ─────────────────────────────────────────────────────


def test_checked_surfaces_the_geometry_without_failing_the_graph():
    """A development node's link to a member names the relation one of *its*
    spans bears to the member's — ``equals`` for a member that is a stretch of
    the chain alone — and the two items' bounding boxes necessarily disagree,
    because a chain's extent contains its member's.

    ``engine.memory.graph`` classifies that as a ``bounds_mismatch``: the edge is
    true at the resolution an editor acts on and is kept in traversal. Escalating
    it to a failure would make ``checked()`` unusable on every corpus with a
    multi-member chain, so it must be reported and never raised on.
    """
    corpus = _real(12)
    graph = build_story_graph(corpus)
    devs = graph.by_kind(NodeKind.DEVELOPMENT)
    assert devs and all(len(n.unit_indices) >= 2 for n in devs)

    # The default call must not raise on a corpus the layer itself built.
    checked = graph.checked()
    report = checked.report()
    assert not report.dangling
    assert not report.self_links
    assert not report.converse_conflicts
    assert not report.cycles
    assert not report.duplicates
    assert not report.unsupported
    assert not any(g.kind == "contradiction" for g in report.geometry_conflicts)
    # The disagreement is surfaced and named, not hidden.
    assert all(g.kind == "bounds_mismatch" for g in report.geometry_conflicts)
    assert report.geometry_conflicts, (
        "this corpus produced no bounds mismatch, so the informational "
        "category is not exercised")

    # ...and the flagged edges are still traversable, still carrying the relation
    # an editor acts on.
    dev = devs[0]
    neighbours = checked.neighbours(dev.id)
    assert neighbours
    assert all(nb.relation == "equals" for nb in neighbours)


def test_checked_escalates_a_lie_the_graph_cannot_carry():
    """``checked`` must raise on the classes a graph cannot hold at all: a
    relation no member pair realises, and an edge pointing at a node the graph
    does not hold. These are the hand-written converse and the impossible
    ordering its docstring says it catches — a check that reported them and
    carried on would offer a trace through an edge it knows is false.
    """
    corpus = _real(12)
    graph = build_story_graph(corpus)
    (first, second) = [n for n in graph.nodes
                       if n.kind is NodeKind.CONTEXT][:2]
    assert not first.intervals.overlaps(second.intervals), (
        "two context nodes overlap, so no contradictory relation can be built")

    def tamper(**over) -> StoryGraph:
        nodes = [replace(n, **over) if n.id == first.id else n
                 for n in graph.nodes]
        return StoryGraph(nodes=tuple(nodes), evidence=graph.evidence,
                          unsupported_kinds=graph.unsupported_kinds)

    # A relation no member pair bears: two separated spans declared ``equals``.
    lied = tamper(links=tuple(first.links) + (
        NodeLink(source=first.id, target=second.id, relation="equals",
                 converse_recorded=True),))
    with pytest.raises(GraphIntegrityError):
        lied.checked()
    permissive = lied.checked(strict=False)
    assert any(g.source == first.id and g.target == second.id
               and g.kind == "contradiction" and g.declared == "equals"
               for g in permissive.report().geometry_conflicts)

    # An edge to a node the graph does not hold.
    dangling = tamper(links=(NodeLink(source=first.id, target="no-such-node",
                                      relation="before", converse_recorded=True),))
    with pytest.raises(GraphIntegrityError) as excinfo:
        dangling.checked()
    message = str(excinfo.value)
    assert "no-such-node" in message


# ── the distant setup→payoff ────────────────────────────────────────────────


def test_distant_setup_payoff_outside_the_window_is_held_as_unsupported():
    """``build_threads`` caps reach at 6 units / 150 s for *inference*
    (``engine/threads.py:604-606``) — the right bound for guessing and the wrong
    one for *holding*: a setup paid off three hours later is a structure an
    editor sees and the lexical evidence cannot.

    An assertion must carry it as a node with ``basis == UNSUPPORTED`` rather
    than the graph being unable to hold it, never present it as something the
    analyser found, and still compute the temporal shape from the spans.
    """
    # Units three minutes apart: every possible edge is outside the 150 s
    # window, so the corpus infers nothing and the graph must still hold the
    # relation an editor asserts.
    segs = [_seg(i * 200.0, i * 200.0 + 8.0, t) for i, t in enumerate([
        "Why did the satellite clock fail? The launch was nominal.",
        "The harbour was rebuilt. The contractors finished early.",
        "The lighthouse needs paint. Its lamp is original.",
        "So the battery was replaced. The vendor paid the cost."])]
    corpus = build_corpus(segs, duration=900.0)
    assert not corpus.links, ("the corpus inferred a link inside the window, so "
                              "the relation is not outside the inference reach")

    rationale = ("The interviewer returns to the clock at the very end, three "
                 "hours after asking what went wrong.")
    graph = build_story_graph(
        corpus, assertions=[SetupPayoffAssertion(0, 3, rationale)])

    setup = next(n for n in graph.by_kind(NodeKind.SETUP)
                 if n.basis is NodeBasis.UNSUPPORTED)
    payoff = next(n for n in graph.by_kind(NodeKind.PAYOFF)
                  if n.basis is NodeBasis.UNSUPPORTED)
    for node in (setup, payoff):
        assert node.basis is NodeBasis.UNSUPPORTED
        assert not node.grounded
        assert node.confidence == 0.0
        assert node.unsupported_reason
        assert node.evidence_ids == ("assertion:0",)
        assert node.cues == ()
        assert node.span_resolution == f"units[{node.unit_indices[0]}].span"

    assert setup.unit_indices == (0,)
    assert payoff.unit_indices == (3,)
    assert setup.intervals == IntervalSet.of(
        (corpus.units[0].start, corpus.units[0].end))
    # The edge's shape is computed from the spans, not assumed from the kind.
    out = [l for l in setup.links if l.target == payoff.id]
    assert out and out[0].relation == "before"
    assert out[0].evidence_id == "assertion:0"
    back = [l for l in payoff.links if l.target == setup.id]
    assert back and back[0].relation == "after"

    # The caller's sentence is carried verbatim and never generated here.
    record = graph.evidence.with_id("assertion:0")
    assert record is not None
    assert record.rationale == rationale
    assert record.grounded is False
    assert record.confidence == 0.0


def test_assertions_without_a_rationale_or_outside_the_spine_are_rejected():
    """An assertion without a rationale is a claim with nothing to check, and one
    naming a unit the spine does not hold cannot resolve to a span at all.
    Both are caller errors and must be rejected rather than carried, because a
    graph that silently dropped them would report an editor's structure as
    absent."""
    corpus = _real(8)
    n = len(corpus.units)

    with pytest.raises(ValueError, match="assertion names unit"):
        build_story_graph(corpus, assertions=[SetupPayoffAssertion(0, n, "why")])
    with pytest.raises(ValueError, match="assertion names unit"):
        build_story_graph(corpus, assertions=[SetupPayoffAssertion(-1, 2, "why")])
    with pytest.raises(ValueError, match="rationale"):
        build_story_graph(corpus, assertions=[SetupPayoffAssertion(0, 2, "  ")])


def test_an_assertion_extends_a_node_the_corpus_already_named():
    """Where the corpus already names a unit on one side, the asserted relation
    must become an edge on the existing node rather than a second, weaker node
    for the same unit and side — two nodes for one moment is exactly the
    parallel-record drift the layer exists to remove, and the stronger basis
    must not be displaced by the asserted one.
    """
    corpus = _real(8)
    without = build_story_graph(corpus)
    with_a = build_story_graph(
        corpus, assertions=[SetupPayoffAssertion(0, 7, "the editor's arc")])

    # No unsupported duplicate appeared for a side the corpus already names.
    assert not [n for n in with_a.by_kind(NodeKind.SETUP)
                if n.basis is NodeBasis.UNSUPPORTED]
    assert not [n for n in with_a.by_kind(NodeKind.PAYOFF)
                if n.basis is NodeBasis.UNSUPPORTED]

    # The node set is unchanged: the assertion added an edge, not a node.
    assert [n.id for n in without.nodes] == [n.id for n in with_a.nodes]

    setup = next(n for n in with_a.nodes
                 if n.kind is NodeKind.SETUP and n.unit_indices == (0,))
    assert setup.basis is NodeBasis.POSITIONAL
    edges = [l for l in setup.links if l.evidence_id == "assertion:0"]
    assert edges, "the asserted relation added no edge to the existing node"
    assert edges[0].converse_recorded
    payoff = with_a.node(edges[0].target)
    assert payoff is not None and payoff.kind is NodeKind.PAYOFF
    assert edges[0].relation == "before"


# ── records the spine does not hold ─────────────────────────────────────────


def test_records_naming_units_the_spine_does_not_hold_are_dropped():
    """A link or beat naming a unit the corpus does not hold cannot ground a
    node: its span does not resolve. The corpus's own reader rejects such a
    record, so this guard is defence in depth — and defence in depth must drop
    the record cleanly rather than index out of range, while still reporting the
    members that *are* in range.

    A topic or thread with one bad member must not lose the good ones, because
    a chain's extent is reported even where its interior is broken.
    """
    units = [_unit(i, i * 10.0, i * 10.0 + 8.0) for i in range(3)]
    corpus = _corpus(
        units,
        topics=[_topic(0, [9, 1], units, evidence=["the surviving member"])],
        threads=[_thread(0, [9, 0, 1], units, evidence=["the chain"])],
        beats=[_beat(9, 0.0, 8.0, kind="turn", cues=["dropped"])],
        links=[_link(9, 1, units), _link(0, 1, units)],
    )
    graph = build_story_graph(corpus)

    # Nothing claims a span the spine cannot give it.
    assert all(n.unit_indices == tuple(u for u in n.unit_indices if u < 3)
               for n in graph.nodes)
    assert not any(9 in n.unit_indices for n in graph.nodes)

    # The topic kept its in-range member and lost the one that does not resolve.
    (context,) = graph.by_kind(NodeKind.CONTEXT)
    assert context.unit_indices == (1,)
    assert context.cues == ("the surviving member",)
    assert context.intervals == IntervalSet.of((10.0, 18.0))

    # The chain kept both real members, de-duplicated and in order.
    (dev,) = graph.by_kind(NodeKind.DEVELOPMENT)
    assert dev.unit_indices == (0, 1)

    # The beat that does not resolve produced no node; the link whose setup is
    # out of range produced no evidence and no setup node, while the sound one
    # still did. Both halves of a link must resolve or neither does.
    assert not any(n.claim.startswith("Unit 9") for n in graph.nodes)
    assert graph.evidence.with_id("beat:0") is None
    assert graph.evidence.with_id("link:0") is None
    assert graph.evidence.with_id("link:1") is not None
    assert len(graph.by_kind(NodeKind.SETUP)) == 1


# ── the honest empty corpus ─────────────────────────────────────────────────


def test_a_corpus_with_no_structure_is_reported_not_padded():
    """An empty analysis is a correct output of this layer, never padded with
    invented beats: a corpus with nothing must yield the single
    unsupported-conflict node plus an entry in ``unsupported_kinds`` for every
    one of the other seven, so a consumer reads the absence rather than a
    fabrication shaped like a story.
    """
    empty = build_corpus([], duration=1.0)
    assert not empty.units
    graph = build_story_graph(empty)

    assert len(graph.nodes) == 1
    (only,) = graph.nodes
    assert only.kind is NodeKind.CONFLICT
    assert only.basis is NodeBasis.UNSUPPORTED
    assert {k for k, _ in graph.unsupported_kinds} == set(NodeKind)

    # The same discipline on a corpus that has units but no stories: the beats
    # the corpus did find are reported, and nothing is invented for the kinds it
    # could not.
    units = [_unit(0, 0.0, 10.0, text="one passage with no story at all")]
    plain = _corpus(units, beats=[_beat(0, 0.0, 10.0, is_thread_peak=True)])
    plain_graph = build_story_graph(plain)
    kinds = {n.kind for n in plain_graph.nodes}
    assert kinds == {NodeKind.PEAK, NodeKind.CONFLICT}
    reasons = {k.value: r for k, r in plain_graph.unsupported_kinds}
    assert "no setup→payoff link exists" in reasons["setup"]
    assert "no subject cluster exists" in reasons["context"]
    assert "no story thread has more than one member" in reasons["development"]
    # ``conflict`` is listed even though a node exists: the kind is unsupported
    # on every corpus, and a consumer must not reconcile the two records.
    assert NodeKind.CONFLICT in reasons


def test_every_node_cites_evidence_the_graph_actually_holds():
    """A node's ``evidence_ids`` are its audit trail; an id that does not resolve
    to a record in the graph's own evidence set is a citation of nothing — the
    failure mode the memory stack exists to prevent, and one a consumer cannot
    detect from the node alone.
    """
    corpus = _real(12)
    graph = build_story_graph(corpus)
    held = {e.id for e in graph.evidence.items}
    assert held

    for node in graph.nodes:
        assert node.evidence_ids or node.basis is NodeBasis.UNSUPPORTED, (
            f"{node.kind.value} node cites no evidence yet claims support")
        for eid in node.evidence_ids:
            assert eid in held, (
                f"{node.kind.value} node cites {eid}, which the graph does not "
                "hold")


# ── quoting, never copying ─────────────────────────────────────────────────


def test_node_passes_the_records_quotes_through_verbatim():
    """``cues`` are the verbatim phrases the source record already quotes, bounded
    as that record bounds them; the node must pass them through unchanged —
    never re-truncate, extend or paraphrase — because a node that re-quoted would
    be a second quoting of the source that could disagree with the first.
    """
    corpus = _real(12)
    graph = build_story_graph(corpus)

    beats = {f"beat:{i}": b for i, b in enumerate(corpus.beats)}
    topics = {f"topic:{i}": t for i, t in enumerate(corpus.topics)}
    threads = {f"thread:{i}": t for i, t in enumerate(corpus.threads)}
    links = {f"link:{i}": lk for i, lk in enumerate(corpus.links)}

    for node in graph.nodes:
        if not node.evidence_ids:
            continue
        if node.kind in (NodeKind.SETUP, NodeKind.PAYOFF) and all(
                eid.startswith("link:") for eid in node.evidence_ids):
            # A side named by several edges quotes the strongest one's evidence
            # and cites them all — the number is selected, never recomputed.
            best = max(node.evidence_ids,
                       key=lambda eid: links[eid].confidence)
            assert node.cues == tuple(links[best].evidence)
            continue
        (eid,) = node.evidence_ids
        record = beats.get(eid) or topics.get(eid) or threads.get(eid)
        assert record is not None, f"{node.kind.value} cites an unknown {eid}"
        assert node.cues == tuple(record.cues if isinstance(record, DramaticBeat)
                                  else record.evidence)

        # Every cue is quoted from the transcript, not paraphrased: either it
        # appears as the words were transcribed, or it is a bounded prefix the
        # corpus's own quoter trimmed and marked with an ellipsis.
        texts = [u.text for u in corpus.units]
        for cue in node.cues:
            verbatim = any(cue in t for t in texts)
            truncated = cue.endswith("…") and any(
                t.startswith(cue[:-1]) for t in texts)
            assert verbatim or truncated, (
                f"cue {cue!r} is neither verbatim nor a bounded quotation")


def test_node_quotes_the_record_and_never_reads_the_spines_text_field():
    """The one full text copy per unit is ``CorpusUnit.text``; a node that read
    that field into a cue would duplicate the spine and could drift from it,
    because the graph is re-derived while the text field is the editor's to
    correct. A node must quote the *record's* evidence list instead — the
    quotations the analysis already made and bounded.

    The failure this pins is silent and plausible: the node's cue reads well, it
    just came from the wrong place, so a corrected transcript and a stale graph
    would disagree about what was said.
    """
    # The spine says one thing and the record quotes another: a node that read
    # ``unit.text`` would carry the spine's words, a node that quotes the record
    # carries the record's.
    units = [_unit(0, 0.0, 10.0, text="the corrected transcript text"),
             _unit(1, 20.0, 30.0, text="another corrected passage")]
    thread = _thread(0, [0, 1], units, evidence=["what the record quoted"])
    topic = _topic(0, [0, 1], units, evidence=["the topic's own quotation"])
    corpus = _corpus(units, topics=[topic], threads=[thread],
                     beats=[_beat(0, 0.0, 10.0, cues=["the beat's cue"])])
    graph = build_story_graph(corpus)

    for node in graph.nodes:
        assert not hasattr(node, "text")
        for unit in units:
            assert node.claim != unit.text
            assert node.rationale != unit.text
            for cue in node.cues:
                assert cue != unit.text, (
                    f"a {node.kind.value} node carries unit {unit.index}'s "
                    "text field, duplicating the spine's one copy")

    # What the nodes carry is exactly what their records quoted.
    (context,) = graph.by_kind(NodeKind.CONTEXT)
    assert context.cues == ("the topic's own quotation",)
    (dev,) = graph.by_kind(NodeKind.DEVELOPMENT)
    assert dev.cues == ("what the record quoted",)


# ── grounding: observation vs inference ─────────────────────────────────────


def test_grounding_is_a_function_of_the_basis_not_an_independent_claim():
    """A kind a record states (QUOTED) or a position a record names (POSITIONAL)
    is an observation; a reconstruction (DERIVED) or an assertion (UNSUPPORTED)
    is not. ``grounded`` is derived from ``basis`` so a node cannot present an
    inference as an observation — nothing else in the stack is positioned to
    catch the two disagreeing.
    """
    corpus = _real(12)
    graph = build_story_graph(corpus)
    assert graph.nodes

    for node in graph.nodes:
        if node.basis in (NodeBasis.QUOTED, NodeBasis.POSITIONAL):
            assert node.grounded
        else:
            assert not node.grounded
        if node.basis is NodeBasis.UNSUPPORTED:
            # A refusal keeps its reason; a reconstruction (DERIVED) is not a
            # refusal and its ``unsupported_reason`` is empty by design.
            assert node.unsupported_reason, (
                f"an unsupported {node.kind.value} node gives no reason")
        else:
            assert not node.unsupported_reason

    observed = {n.kind for n in graph.nodes if n.grounded}
    inferred = {n.kind for n in graph.nodes if not n.grounded}
    assert NodeKind.CONFLICT in inferred
    # A context node is a reconstruction over the topic's members — a *new*
    # computation, which is the whole reason the layer is worth building.
    assert NodeKind.CONTEXT in inferred
    assert NodeKind.PEAK in observed


# ── purity ──────────────────────────────────────────────────────────────────


def test_the_corpus_is_unchanged_and_two_builds_agree():
    """The layer is a projection, not a store: it is re-derived by a pure
    function, mutates nothing it is given, and holds no transcript text — so
    the same corpus yields every presentation of it, byte-identically.
    """
    corpus = _real(12)
    before = [(u.index, u.start, u.end, u.text, u.timing_source)
              for u in corpus.units]

    first = build_story_graph(corpus)
    second = build_story_graph(corpus)
    third = build_story_graph(copy.deepcopy(corpus))

    assert [(n.id, n.kind, n.intervals, n.links) for n in first.nodes] == \
           [(n.id, n.kind, n.intervals, n.links) for n in second.nodes]
    assert [n.id for n in first.nodes] == [n.id for n in third.nodes]
    assert all(a.intervals == b.intervals
               for a, b in zip(first.nodes, third.nodes))
    assert [(u.index, u.start, u.end, u.text, u.timing_source)
            for u in corpus.units] == before


def test_node_ids_are_keyed_by_membership_and_stay_apart():
    """Ids must be stable across rebuilds and must keep two different nodes apart:
    the kind and the basis separate a unit that is a setup because an edge names
    it from the same unit asserted as one, and the member list separates two
    subjects or two chains. An id that collided would silently merge two stories
    or drop one.
    """
    units = [_unit(i, i * 10.0, i * 10.0 + 8.0) for i in range(4)]
    threads = [_thread(0, [0, 1], units), _thread(1, [2, 3], units)]
    corpus = _corpus(units, threads=threads)

    first = build_story_graph(corpus)
    again = build_story_graph(corpus)
    assert [n.id for n in first.nodes] == [n.id for n in again.nodes]

    devs = first.by_kind(NodeKind.DEVELOPMENT)
    assert len({n.id for n in devs}) == len(devs) == 2
    chains = {tuple(n.unit_indices) for n in devs}
    assert chains == {(0, 1), (2, 3)}

    # Same unit, two bases: the positional node an edge names and the asserted
    # one must not share an id.
    linked = _corpus(units, links=[_link(0, 1, units)])
    graph = build_story_graph(
        linked, assertions=[SetupPayoffAssertion(0, 1, "an editor's arc")])
    assert not any(n.kind is NodeKind.SETUP
                   and n.basis is NodeBasis.UNSUPPORTED for n in graph.nodes)
    positional = next(n for n in graph.by_kind(NodeKind.SETUP))
    other = build_story_graph(
        _corpus(units, threads=threads),
        assertions=[SetupPayoffAssertion(0, 3, "an editor's arc")])
    asserted = next(n for n in other.by_kind(NodeKind.SETUP)
                    if n.basis is NodeBasis.UNSUPPORTED)
    assert positional.unit_indices == asserted.unit_indices == (0,)
    assert positional.id != asserted.id


def test_unsupported_nodes_sort_last_and_the_order_is_source_time():
    """A node with no spans sorts last rather than first, so an unsupported node
    never displaces a beat a consumer is looking for, and the order is source
    time — the layer carries no ordering of moments by anything but the source,
    which is the seam's decision to make, not the graph's.
    """
    corpus = _real(12)
    graph = build_story_graph(corpus)

    starts = [n.intervals.bounds.start if n.intervals.bounds is not None
              else float("inf") for n in graph.nodes]
    assert starts == sorted(starts)
    assert starts[-1] == float("inf")
    assert graph.nodes[-1].kind is NodeKind.CONFLICT
    assert graph.nodes[-1].intervals.is_empty


def test_node_and_by_kind_lookups_report_absence():
    """``node`` reports a miss rather than raising, because a consumer asking for
    a node that is not there is the common case over a graph of a subset of
    kinds; ``by_kind`` returns the nodes of one kind in the graph's own order, so
    a caller iterating kinds stays in lockstep with a caller iterating nodes."""
    corpus = _real(12)
    graph = build_story_graph(corpus)

    assert graph.node("not-a-node") is None
    for node in graph.nodes:
        assert graph.node(node.id) is node

    for kind in NodeKind:
        found = graph.by_kind(kind)
        assert tuple(found) == tuple(n for n in graph.nodes if n.kind is kind)
        assert all(n.kind is kind for n in found)
    # ``by_kind`` is a subsequence of the graph order, so the two views cannot
    # disagree about where a node sits.
    positions = {n.id: i for i, n in enumerate(graph.nodes)}
    for kind in NodeKind:
        idx = [positions[n.id] for n in graph.by_kind(kind)]
        assert idx == sorted(idx)


def test_round_trip_through_the_corpus_reader_preserves_the_nodes():
    """A corpus read back through ``engine.corpus_io.read_corpus`` must project
    to the same nodes as the freshly built one: a reader-versus-builder
    disagreement is a silent memory that disagrees with the video, and the
    schema's whole purpose is that the two are interchangeable.
    """
    corpus = _real(12)
    rebuilt = read_corpus(corpus.to_dict())
    assert rebuilt.units

    fresh = build_story_graph(corpus)
    round_tripped = build_story_graph(rebuilt)

    assert [n.id for n in fresh.nodes] == [n.id for n in round_tripped.nodes]
    assert [(n.kind, n.basis, n.intervals) for n in fresh.nodes] == \
           [(n.kind, n.basis, n.intervals) for n in round_tripped.nodes]
    assert [(n.id, n.links) for n in fresh.nodes] == \
           [(n.id, n.links) for n in round_tripped.nodes]
    assert fresh.unsupported_kinds == round_tripped.unsupported_kinds
