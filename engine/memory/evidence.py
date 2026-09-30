"""Evidence: a claim bound to the spans, modality and provenance that support it.

Why this module exists
----------------------

``engine.corpus`` already attaches ``reason`` and ``evidence`` to its editorial
artifacts, and that provenance is the reason a reader of a SeleKyT report can
check a judgement rather than trust it. But those fields are attached to
*rankings* — a candidate's score and why it scored. They do not survive the run,
they are not queryable by time or modality, and a conclusion that is not about a
candidate (the second speaker is on screen from 12:40 to 31:15; the topic that
opens the video is never returned to; the audio drops out for nine seconds at
the chapter break) has nowhere to live at all.

``Evidence`` is that place. It is a claim plus:

- ``intervals`` — an ``IntervalSet``, so the answer to "where" is
  variable-cardinality by construction, not a single span that silently became
  one;
- ``modality`` — which channel the evidence came from, because "the transcript
  says" and "the frame shows" are different qualities of support and an editor
  weighs them differently;
- ``source`` — the module, method and parameter digest that produced it, so a
  conclusion is traceable to the instrument and a replaced instrument
  invalidates the conclusion rather than being absorbed by it;
- ``confidence`` and ``rationale`` — the number and the sentence that makes the
  number checkable;
- ``links`` — typed edges to other pieces of evidence, which make the evidence
  graph a structure this module already carries rather than a separate thing to
  be invented later;

What an ``Evidence`` is *not*: it is not a judgement. "Clip this span" is an
editorial decision that weighs evidence against a story's needs; ``Evidence``
is the input to that weighing. Keeping the two apart is what lets the same
evidence feed a short-form cut, a chapter index and a fact-check without each
one re-deriving it.

Purity
------

Values are pure and deterministic: identical input yields identical evidence,
no wall clock, no network, no inference. Confidence is a number produced by the
source instrument and recorded, never adjusted here — an ``EvidenceSet`` filter
that drops low-confidence items reports what it dropped rather than quietly
smoothing the distribution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from engine.memory.intervals import (
    ALLEN_RELATIONS,
    Interval,
    IntervalSet,
    converse as converse_name,
)
from engine.lexical import fingerprint

SCHEMA_VERSION = "1.1"


class Modality(str, Enum):
    """The channel a piece of evidence was perceived through.

    ``str`` so the value serialises to JSON as a plain name and survives a
    round trip without a custom encoder. Order matters only for reporting; it
    is not a quality ranking — see the note below.
    """

    TEXT = "text"          # transcript, ASR words, captions, OCR
    AUDIO = "audio"        # speech regions, pauses, silence, music
    VISUAL = "visual"      # faces, shot classes, scene changes, on-screen text
    EDITORIAL = "editorial"  # story structure: threads, topics, beats, arcs

    # Note: the order above is the order the pipeline *produces* these channels,
    # not a ranking of their trustworthiness. TEXT evidence from a transcript
    # with 91% WER is weaker than VISUAL evidence from a reliable detector; the
    # confidence field carries that, and a consumer who ranks modalities a
    # priori will rank wrong on the corpus's own Telugu sources.


@dataclass(frozen=True)
class Source:
    """The instrument that produced a piece of evidence.

    ``params_digest`` is a short digest of the parameters that matter to the
    output — model name, sample count, thresholds. It exists so that a
    re-run with a different sample count does not silently inherit the previous
    run's evidence: a memory built at 28 samples per window is not the same
    memory as one built at 56, and the store checks this rather than trusting
    filenames.
    """

    module: str            # e.g. "engine.reframe"
    method: str            # e.g. "track_subject_center"
    params_digest: str = ""

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "method": self.method,
            "params_digest": self.params_digest,
        }

    @staticmethod
    def from_dict(d: dict) -> Source:
        return Source(
            module=d.get("module", ""),
            method=d.get("method", ""),
            params_digest=d.get("params_digest", ""),
        )

    @staticmethod
    def of(module: str, method: str, **params: object) -> Source:
        """Build with a digest of the parameters that influenced the output.

        Only stable, primitive params should be passed; a path or a timestamp in
        here would make the digest useless, since it would change between
        identical runs.
        """
        digest = ""
        if params:
            # Reuses the project's own normalising fingerprint so the digest is
            # stable across key order and float formatting.
            digest = fingerprint(
                "|".join(f"{k}={params[k]}" for k in sorted(params))
            )[:12]
        return Source(module=module, method=method, params_digest=digest)


@dataclass(frozen=True)
class EvidenceLink:
    """One labelled edge from an item to another, named by id.

    A record per edge, rather than a tuple of target ids carrying one shared
    relation label, because a link is a pair — *which* item, and *what*
    relation — and a single label over many targets cannot express both.
    ``link("B", "before").link("C", "after")`` kept both targets but recorded
    only ``after``, silently relabelling the first edge; an editor tracing that
    chain would read the wrong relation for half of it.
    """

    target: str
    relation: str

    def __post_init__(self) -> None:
        if self.relation not in ALLEN_RELATIONS:
            raise ValueError(
                f"evidence relation {self.relation!r} is not an Allen relation; "
                f"see engine.memory.intervals.ALLEN_RELATIONS"
            )

    def converse(self) -> EvidenceLink:
        """The reverse edge. ``engine.memory.intervals.converse`` supplies the
        name, so the graph never carries two spellings of one relation."""
        return EvidenceLink(self.target, converse_name(self.relation))

    def to_dict(self) -> dict:
        return {"target": self.target, "relation": self.relation}

    @staticmethod
    def from_dict(d: dict) -> EvidenceLink:
        return EvidenceLink(target=d.get("target", ""), relation=d.get("relation", ""))


@dataclass(frozen=True)
class Evidence:
    """One claim and the support for it.

    ``intervals`` is the answer to "where". Its cardinality is data: a claim
    supported at four spans is a different claim from one supported at one, and
    a consumer that assumes a single span will take the first and be wrong.
    """

    claim: str               # what is asserted, in editor-readable prose
    intervals: IntervalSet   # where in the source the support is
    modality: Modality       # which channel produced it
    source: Source           # which instrument, and with what parameters
    confidence: float = 1.0  # 0..1, as measured by the source
    rationale: str = ""      # why the evidence supports the claim
    grounded: bool = True    # False = inferred, not directly observed
    artifacts: tuple[str, ...] = ()  # files an editor can open to check
    links: tuple[EvidenceLink, ...] = ()  # typed edges into the evidence graph
    id: str = ""             # stable id; empty until a store assigns one

    def __post_init__(self) -> None:
        # Non-finite confidence is handled before the range check, not by it —
        # the same rule ``intervals._finite`` states, for the same reason:
        # ``min(1.0, nan)`` compares ``nan < 1.0``, which is False, and returns
        # 1.0, so a range clamp alone would record a NaN confidence as *maximum*
        # trust. Infinity is a measurement failure too, not superlative
        # confidence. Both arrive from a sensor that divided by zero or a record
        # that corrupted on disk, and the honest recording of a failed
        # measurement is zero confidence, not certainty.
        if not math.isfinite(self.confidence):
            object.__setattr__(self, "confidence", 0.0)
        elif self.confidence < 0.0 or self.confidence > 1.0:
            object.__setattr__(self, "confidence", max(0.0, min(1.0, self.confidence)))

    @property
    def cardinality(self) -> int:
        """How many distinct spans support this claim."""
        return self.intervals.cardinality

    @property
    def duration(self) -> float:
        """Total supported time across all spans."""
        return self.intervals.duration

    def with_id(self, id: str) -> Evidence:
        """Return a copy carrying a stable id. The store calls this when it
        assigns ids, so an evidence object stays immutable in flight."""
        return Evidence(
            claim=self.claim,
            intervals=self.intervals,
            modality=self.modality,
            source=self.source,
            confidence=self.confidence,
            rationale=self.rationale,
            grounded=self.grounded,
            artifacts=self.artifacts,
            links=self.links,
            id=id,
        )

    def link(self, other_id: str, relation: str) -> Evidence:
        """Return a copy that asserts a typed relation to ``other_id``.

        The relation is validated at construction of the ``EvidenceLink`` — so a
        typo is a loud error rather than an unmatchable string in the evidence
        graph — and every edge keeps its own label. The converse is available
        via ``EvidenceLink.converse``; call it when building the reverse edge,
        so the graph never carries two spellings of one relation.
        """
        return Evidence(
            claim=self.claim,
            intervals=self.intervals,
            modality=self.modality,
            source=self.source,
            confidence=self.confidence,
            rationale=self.rationale,
            grounded=self.grounded,
            artifacts=self.artifacts,
            links=(*self.links, EvidenceLink(other_id, relation)),
            id=self.id,
        )

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "id": self.id,
            "claim": self.claim,
            "intervals": self.intervals.to_dict(),
            "modality": self.modality.value,
            "source": self.source.to_dict(),
            "confidence": self.confidence,
            "rationale": self.rationale,
            "grounded": self.grounded,
            "artifacts": list(self.artifacts),
            "links": [l.to_dict() for l in self.links],
        }

    @staticmethod
    def from_dict(d: dict) -> Evidence:
        links: tuple[EvidenceLink, ...] = ()
        raw_links = d.get("links")
        if raw_links:
            links = tuple(EvidenceLink.from_dict(i) for i in raw_links)
        else:
            # Schema 1.0 carried a tuple of target ids under one shared relation
            # label. Migrated one edge per target; a record with targets and no
            # label cannot be recovered as typed edges and is dropped rather
            # than guessed.
            legacy_targets = d.get("relates_to") or ()
            legacy_relation = d.get("relation") or ""
            if legacy_targets and legacy_relation:
                links = tuple(
                    EvidenceLink(t, legacy_relation) for t in legacy_targets
                )
        return Evidence(
            claim=d["claim"],
            intervals=IntervalSet.from_dict(d.get("intervals") or {}),
            modality=Modality(d.get("modality") or "text"),
            source=Source.from_dict(d.get("source") or {}),
            confidence=d.get("confidence", 1.0),
            rationale=d.get("rationale") or "",
            grounded=d.get("grounded", True),
            artifacts=tuple(d.get("artifacts") or ()),
            links=links,
            id=d.get("id") or "",
        )


@dataclass
class EvidenceSet:
    """A variable-cardinality set of evidence — the answer object for a
    multi-channel query.

    Where ``IntervalSet`` answers "where", this answers "what supports the
    conclusion, on which channels, and how strongly". A retrieval query returns
    one of these; the reasoning layer reads it, asks for targeted grounding
    where it is thin, and only then concludes.

    Filters report rather than repair: ``above_confidence`` returns the subset
    above the floor, and a caller who wants to know what they discarded inspects
    the difference. Nothing here inflates, smooths or imputes a confidence.
    """

    items: list[Evidence] = field(default_factory=list)

    # ── construction ───────────────────────────────────────────────────────

    @classmethod
    def empty(cls) -> EvidenceSet:
        return cls(items=[])

    @classmethod
    def of(cls, *items: Evidence) -> EvidenceSet:
        return cls(items=list(items))

    @property
    def cardinality(self) -> int:
        """Total spans across all items — the cost of acting on this answer."""
        return sum(i.cardinality for i in self.items)

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def intervals(self) -> IntervalSet:
        """The union of every supporting span. This is the answer to "where in
        the source does *any* of this evidence fall", and it is what a grounding
        layer intersects against a proposed clip."""
        # Flat then normalise once. Folding union across the items re-normalises
        # a growing set per item — quadratic in the span count, and measured at
        # ~2700x a single pass on a 4000-item set for an identical result.
        return IntervalSet([s for i in self.items for s in i.intervals])

    # ── query ──────────────────────────────────────────────────────────────

    def for_modality(self, modality: Modality) -> EvidenceSet:
        # ``==``, not ``is``: ``Modality`` mixes in ``str`` so a modality read
        # back from a persisted record is a plain string until it is re-wrapped,
        # and ``is`` compares the two as distinct objects and silently returns
        # an empty set — every modality filter would then report "no evidence"
        # for a whole channel.
        return EvidenceSet([i for i in self.items if i.modality == modality])

    def in_window(self, start: float, end: float) -> EvidenceSet:
        """Items with any support inside ``[start, end]``. The spans themselves
        are not clipped here — a consumer needs to know the evidence extends
        past the window, which a clipped interval would hide. ``restrict`` is
        the clipping counterpart."""
        w = Interval(start, end)
        return EvidenceSet([i for i in self.items if i.intervals.overlaps(w)])

    def above_confidence(self, floor: float) -> EvidenceSet:
        return EvidenceSet([i for i in self.items if i.confidence >= floor])

    def below_confidence(self, ceiling: float) -> EvidenceSet:
        """The complement of ``above_confidence``, exposed because "what did I
        discard" is a question the verify stage asks and a single filter cannot
        answer both ways."""
        return EvidenceSet([i for i in self.items if i.confidence < ceiling])

    def grounded_only(self) -> EvidenceSet:
        return EvidenceSet([i for i in self.items if i.grounded])

    def about(self, needle: str) -> EvidenceSet:
        """Items whose claim or rationale mentions ``needle`` (case-folded).
        Text-only and deliberately crude: the semantic retrieval layer builds on
        the interval algebra, not on this, and a keyword match that is honest
        about being a keyword match is worth more here than a fuzzy matcher that
        implies understanding it does not have."""
        n = needle.casefold()
        return EvidenceSet([
            i for i in self.items
            if n in i.claim.casefold() or n in i.rationale.casefold()
        ])

    def with_id(self, id: str) -> Evidence | None:
        for i in self.items:
            if i.id == id:
                return i
        return None

    # ── combination ────────────────────────────────────────────────────────

    def union(self, other: EvidenceSet) -> EvidenceSet:
        return EvidenceSet([*self.items, *other.items])

    def restrict(self, spans: IntervalSet) -> EvidenceSet:
        """Clip every item's intervals to ``spans``. Used when a reasoning pass
        has selected a region and wants only the evidence that survives the
        selection — the clip, not the source, is now the world."""
        return EvidenceSet([
            Evidence(
                claim=i.claim,
                intervals=i.intervals.intersect(spans),
                modality=i.modality,
                source=i.source,
                confidence=i.confidence,
                rationale=i.rationale,
                grounded=i.grounded,
                artifacts=i.artifacts,
                links=i.links,
                id=i.id,
            )
            for i in self.items
            if i.intervals.intersect(spans).cardinality > 0
        ])

    # ── serialisation ───────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "count": len(self.items),
            "cardinality": self.cardinality,
            "items": [i.to_dict() for i in self.items],
        }

    @staticmethod
    def from_dict(d: dict) -> EvidenceSet:
        return EvidenceSet([Evidence.from_dict(i) for i in d.get("items", [])])

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)
