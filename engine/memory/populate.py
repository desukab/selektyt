"""Populate a ``VideoMemory`` from the artifacts a pipeline run already holds.

The case for this module is the case for the whole package: by the time
``selekyt.pipeline.run`` has selected clips, it has already paid for a VAD pass
over the whole audio and for a full corpus analysis, and it throws both away.
The speech regions are computed inside ``engine.coverage.measure`` and bound to
a local named ``_regions``; the topics, beats, setup-payoff links and b-roll
markers are fields of a ``CorpusReport`` that gets written to JSON and never
read again. Nothing below re-decodes, re-listens or re-clusters anything.

What this module is *not* is a wiring. It does not import ``engine.coverage``,
``engine.corpus`` or ``selekyt.pipeline``: those modules would then depend on
the memory package, and a memory failure would become a coverage-gate failure.
Instead it takes the records those modules already produce — duck-typed on
``.start``/``.end`` and the corpus report's own field names — and the caller
hands them in. The intercept points are two lines in ``selekyt/pipeline.py``.

Fault isolation
---------------

Every adapter runs behind a boundary, because the failure modes are real and
they are not symmetric. A VAD that raises on a truncated file must not also
destroy the topic evidence that a completely separate instrument produced; a
corpus report whose ``beats`` are malformed must not take the speech regions
with it. A stage that fails is recorded with its error and contributes no items,
and the summary names it — a memory that silently omitted a modality is worse
than one that reports it omitted one, because the first is indistinguishable
from a source with no speech in it.

Silence
-------

The complement of the speech regions is recorded as evidence in its own right
rather than left for a consumer to derive. The derivation is trivial —
``IntervalSet.complement`` — but "the audio drops out from 12:03 to 12:11" is a
*claim an editor queries*, and a query that must first reconstruct its own
answer from a different modality is a query the memory cannot answer. Recording
it also makes the level-1 coverage span the whole source, which is what
``escalate_needed`` is supposed to mean: examined everywhere at the coarsest
rung, not "examined wherever speech happened to be".
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .evidence import Evidence
from .ingest import (
    broll_markers,
    beats as beats_from,
    setup_payoff_links,
    silence_regions,
    speech_regions as speech_regions_from,
    topics as topics_from,
)
from .intervals import Interval, IntervalSet
from .store import Finding, Level, LevelRecord, SourceMeta, VideoMemory


@dataclass(frozen=True)
class _Span:
    """A silence span, in the shape ``silence_regions`` consumes.

    ``SpeechRegion`` has no ``duration`` and silence is not a speech region, so
    this is the smallest record that satisfies the adapter's contract without
    the adapter being widened to take a second type.
    """

    start: float
    end: float
    duration: float


@dataclass(frozen=True)
class StageResult:
    """What one adapter contributed, or why it contributed nothing.

    ``items`` is a tuple rather than a list so the summary is hashable enough to
    hold in a frozen record, and because a stage that produced items is a fact
    about the run, not a buffer to append to.

    An empty stage and a failed stage are reported separately and never
    confused: a source with no b-roll markers and a b-roll adapter that raised
    both leave the memory without b-roll, but only one of them is a defect, and
    the level records and the findings below are read on the assumption that
    "absent" means "looked and found none".
    """

    name: str
    items: tuple[Evidence, ...] = ()
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)


@dataclass(frozen=True)
class PopulateSummary:
    """The build's own audit trail, for the run report and for the tests."""

    stages: tuple[StageResult, ...]
    findings: tuple[Finding, ...] = ()
    carried: int = 0

    @property
    def items(self) -> int:
        return sum(len(s.items) for s in self.stages)

    @property
    def errors(self) -> tuple[str, ...]:
        """One human-readable line per failed stage."""
        return tuple(f"{s.name}: {s.error}" for s in self.stages if s.failed)

    def as_dict(self) -> dict:
        return {
            "items": self.items,
            "errors": list(self.errors),
            # ``Finding.to_dict`` is used rather than reassembling the fields
            # here, because the shape a finding serialises to is the store's
            # contract and this summary must not drift from it.
            "findings": [f.to_dict() for f in self.findings],
            "carried": self.carried,
        }


def _run(name: str, fn) -> StageResult:
    """One adapter behind a fault boundary.

    ``Exception`` is caught broadly and deliberately: an adapter here calls into
    VAD and corpus code the memory package did not write and cannot vet, and the
    contract is "record the failure and keep the other stages", not "diagnose
    it". The error names the exception type so the caller is not left with a
    bare message.
    """
    try:
        items = fn()
    except Exception as exc:  # noqa: BLE001 - reported per stage, see above
        return StageResult(name, error=f"{type(exc).__name__}: {exc}")
    return StageResult(name, items=tuple(items))


def _silences_between(speech, duration: float) -> list[_Span]:
    """The gaps between speech regions, as silence records.

    ``duration`` is required: without an upper bound the complement after the
    last speech region is unbounded, and a silence span running to infinity is
    precisely the out-of-bounds span ``VideoMemory.validate`` exists to catch.
    A source of unknown length has no complement to compute, and returning
    nothing is the honest answer rather than a synthetic whole-source silence.
    """
    if duration <= 0.0:
        return []
    spans = IntervalSet.of(
        *[(float(r.start), float(r.end)) for r in speech if r.end > r.start]
    )
    out: list[_Span] = []
    for gap in spans.complement(Interval(0.0, duration)):
        if gap.duration <= 0.0:
            continue
        out.append(_Span(gap.start, gap.end, gap.duration))
    return out


def _content_key(item: Evidence) -> str:
    """The claim's content identity, independent of its stored id.

    Ids are assigned by the store and carried forward, so two records of the
    same claim must be compared without them. Everything else the item
    serialises -- claim text, modality, instrument and parameter digest,
    intervals, confidence, rationale, artifacts, links -- IS the claim: two
    items with the same serialisation are the same observation, and one of
    them is redundant, no matter which run wrote it.
    """
    d = item.to_dict()
    d.pop("id", None)
    return json.dumps(d, sort_keys=True, ensure_ascii=False)


def build(
    meta: SourceMeta,
    *,
    speech_regions: tuple = (),
    vad_backend: str = "",
    corpus: object = None,
    language: str = "unknown",
    previous: VideoMemory | None = None,
) -> tuple[VideoMemory, PopulateSummary]:
    """Turn the artifacts a run already holds into a populated memory.

    ``speech_regions`` are the VAD regions ``engine.coverage`` computes — the
    records that carry ``.start``/``.end``/``.score``. Pass them even on a run
    whose coverage gate was bypassed, because a bypassed gate still listened to
    the audio and the regions are the whole point of the capture.

    ``vad_backend`` is the name the coverage gate recorded, forwarded so it lands
    in every speech item's provenance. Provenance is what makes a silence claim
    checkable: an energy-backend "speech" on a quiet source is a level reading,
    not a detection, and the rationale has to be able to say so.

    ``corpus`` is the ``CorpusReport``, or anything exposing its field names.
    ``None`` means the corpus stage failed upstream, which the pipeline already
    reports; recording it here as no stage at all keeps that fact in the
    pipeline's report without duplicating the upstream diagnosis here.

    ``previous`` is the record a prior run of the same source persisted (the
    caller has already checked it against the live media fingerprint), merged
    instead of overwritten. Understanding a video is cumulative: a stage that
    failed this run keeps the evidence the previous run produced, and claims
    this run re-derived identically are carried with their stable ids rather
    than re-cast under fresh ones, so an editor's references between runs don't
    rot. Claims under a *different* instrument or parameter digest are
    different observations, not duplicates, and are kept alongside.

    Returns the memory and its summary rather than raising: the caller writes
    both, and a memory that is partially populated is still the most useful
    object to hand a retrieval layer, because the modalities that did land are
    queryable immediately.
    """
    stages: list[StageResult] = []

    stages.append(_run("speech_regions", lambda: speech_regions_from(
        speech_regions, backend=vad_backend, duration=meta.duration,
    )))
    stages.append(_run("silence_regions", lambda: silence_regions(
        _silences_between(speech_regions, meta.duration),
    )))
    if corpus is not None:
        # Each corpus family is a separate stage for the same reason the whole
        # module isolates stages: the beats table is derived from the topic
        # table, and one malformed record in the former must not lose the
        # latter. A single "corpus" stage would make the whole report hostage
        # to the weakest adapter of the four.
        stages.append(_run("topics", lambda: topics_from(
            getattr(corpus, "topics", ()), language=language,
        )))
        stages.append(_run("beats", lambda: beats_from(
            getattr(corpus, "beats", ()), language=language,
        )))
        stages.append(_run("setup_payoff_links", lambda: setup_payoff_links(
            getattr(corpus, "links", ()), language=language,
        )))
        stages.append(_run("broll_markers", lambda: broll_markers(
            getattr(corpus, "broll", ()), language=language,
        )))

    items: list[Evidence] = []
    for stage in stages:
        items.extend(stage.items)

    carried = 0
    if previous is not None:
        # Merge, deduplicated on content: a prior item this run re-derived
        # identically keeps its settled id, and everything not re-derived --
        # claims from a stage that failed this run, or from an instrument or
        # parameter set this run did not use -- survives as evidence this run
        # did not contradict. The store's ``record`` keeps prior ids and
        # continues its generated counter past them, so the merged record's
        # id space stays consistent.
        known = {_content_key(i) for i in previous.items}
        fresh = []
        for item in items:
            key = _content_key(item)
            if key in known:
                carried += 1
                continue
            known.add(key)
            fresh.append(item)
        memory = VideoMemory.empty(meta).record(*previous.items, *fresh)
    else:
        memory = VideoMemory.empty(meta).record(*items)

    # The rung records are *derived from the items*, not asserted from what was
    # asked for: an empty sweep is coverage, but only if the sweep ran, and the
    # distinction between "swept and found nothing" and "never swept" is exactly
    # the failed/empty distinction the stages above already made. Levels with no
    # items and no coverage are omitted so a sparse source does not get a
    # ladder rung that claims an examination that never happened.
    records = []
    for level in (Level.COARSE, Level.CANDIDATES):
        covered = memory.coverage(level)
        count = len(memory.at_level(level))
        if count or covered.cardinality:
            records.append(LevelRecord(
                level=level, coverage=covered, items=count,
                notes=(
                    "populated from artifacts a pipeline run already held; "
                    "no second decode or listen"
                ),
            ))
    memory = memory.with_levels(*records)

    findings = memory.validate()

    return memory, PopulateSummary(tuple(stages), tuple(findings), carried)
