"""SeleKyT video memory — a persistent, queryable, temporally grounded record.

The product question this package answers is not "what clips can I cut?" —
``engine.editorial`` and ``engine.corpus`` already answer that from the
transcript. It is the question an editor asks *before* they cut anything, and
asks differently for an hour-scale source than for a two-minute one:

- What actually happened in this video, and where?
- What evidence supports each conclusion, and where can a human go to check it?
- Where did the speakers say the thing the story turns on, and what was on
  screen and in the audio when they said it?

The difference from the transcript-only model is that a video is not its
transcript. The pipeline already perceives the other modalities — face tracks
and shot classes in ``engine.reframe`` / ``engine.reframe_scenes``, speech
regions in ``engine.coverage``, pause structure in ``engine.pause_classifier``,
story structure in ``engine.corpus`` — and then throws every one of them away
once a crop filter or a coverage ratio has been read off it. That perception is
expensive, it is multimodal, and discarding it is the reason the system cannot
answer "what was on screen while this was said" without re-decoding the video.

This package makes those signals into *memory*: normalised, versioned,
queryable by time and by modality, and carrying the provenance and rationale an
editor needs to trust a conclusion and dispute it.

Structure
---------

``intervals``   Time as a first-class value: ``Interval``, ``IntervalSet`` and
                the thirteen Allen relations. Every temporal answer in the
                system is a *set* of intervals of variable cardinality, never a
                single start/end — the shape the long-video grounding
                literature converges on, because "where is this discussed" is
                usually four spans across an hour, not one.

``evidence``    ``Evidence`` binds a claim to its intervals, its modality, the
                source that produced it, a confidence and a rationale.
                ``EvidenceSet`` is the variable-cardinality answer object, and
                the links between items are the seed of the evidence graph.

``graph``       ``EvidenceGraph`` — the traceability index over an
                ``EvidenceSet``. It checks the items' own declared edges against
                the geometry of the spans they connect and answers ``trace`` and
                ``supports``: the two queries that make a conclusion checkable
                and a replaced instrument actionable.

``store``       ``VideoMemory`` — the persistent, queryable world for one
                source. Level 0..5 hierarchy: escalate computation only when
                evidence requires it.

``ingest``      Adapters that capture signals the pipeline already computes into
                memory, so memory is populated by work that is already being
                done rather than by a second decode pass.

``populate``    The seam between a pipeline run and the memory above. It takes
                the coverage gate's speech regions and the corpus report and
                returns a populated memory plus a per-stage audit, isolating
                each adapter so one failing instrument cannot lose another's
                evidence.

Determinism, purity and cost
----------------------------

``intervals`` and ``evidence`` are pure value types: no global state, no
network, no wall clock, no inference, byte-identical for identical input.
``store`` and ``ingest`` do read and write files; both are explicit about that
in their docstrings, and every persisted record carries the schema version and
the fingerprint of the input it was built from, so a stale memory is detectable
rather than silently wrong.

Interval set algebra is O(N + M) in the number of intervals for union,
intersection and subtraction: both operands are already normalised and sorted,
so the work is one merge pass rather than a pairwise comparison. Naive pairwise
intersection is O(N*M) and at hour scale with per-word intervals that is the
difference between a millisecond and a wait.

Stability
---------

The field set of ``Interval``, ``IntervalSet``, ``Evidence`` and ``EvidenceSet``
is the contract the retrieval, grounding and evidence-graph layers build
against. Backwards-incompatible changes require a ``SCHEMA_VERSION`` bump in
the module that owns the shape.
"""
from engine.memory.evidence import (
    Evidence,
    EvidenceLink,
    EvidenceSet,
    Modality,
    Source,
)
from engine.memory.graph import (
    Edge,
    EvidenceGraph,
    GeometryConflict,
    GraphIntegrityError,
    IntegrityReport,
    Neighbour,
    TraceChain,
    TraceResult,
    TraceStep,
)
from engine.memory.intervals import Interval, IntervalSet, allen, converse
from engine.memory.store import (
    Finding,
    IncompatibleSchema,
    Level,
    LevelRecord,
    SourceMeta,
    StaleMemory,
    VideoMemory,
)
from engine.memory.populate import PopulateSummary, StageResult

__all__ = [
    "Edge",
    "Evidence",
    "EvidenceGraph",
    "EvidenceLink",
    "EvidenceSet",
    "Finding",
    "GeometryConflict",
    "GraphIntegrityError",
    "IncompatibleSchema",
    "IntegrityReport",
    "Interval",
    "IntervalSet",
    "Level",
    "LevelRecord",
    "Modality",
    "Neighbour",
    "PopulateSummary",
    "Source",
    "SourceMeta",
    "StaleMemory",
    "StageResult",
    "TraceChain",
    "TraceResult",
    "TraceStep",
    "VideoMemory",
    "allen",
    "converse",
]
