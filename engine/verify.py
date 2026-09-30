"""Verify an edit plan against the truth, diagnose the fault, repair, re-verify.

Why this module exists
----------------------

Every artifact SeleKyT ships is a projection of one plan: ``engine.edit_plan.
retention_intervals`` decides which source seconds are kept, and the render,
the EDL, the FCPXML and the captions are all functions of that list
(``docs/verify-repair-design.md`` §2.1 — "no exporter holds private editorial
state worth patching"). Verification can therefore be phrased as *does every
artifact and every boundary agree with the plan and with the evidence behind
it*, and repair can be phrased as *re-emit from the plan*, never as a patch to
an emitted file.

The checks this loop runs are the ones the out-of-band auditors already run —
``tools/editorial_audit.py``'s editorial predicates and ``tools/nle_check.py``'s
interchange predicates — but they are re-expressed here over typed, in-memory
inputs rather than over JSON read from a project directory. The reason is the
purity contract below: those tools read files and shell out, and
``tools/nle_check`` mutates ``sys.path`` at import time, so a module that
imported them would not be a function of its arguments. The *predicates* are the
same, including the tolerances, which are pinned by name in this module and
cited to the file that measured them rather than re-spelled as bare numbers.

What it is not
--------------

It is not an executor. ``REEMIT`` (re-render, re-export, re-caption) and
``REMUX`` (stream-copy container repair) touch files and are recorded here as
``RepairRecord``\\ s for the caller to perform; the loop re-verifies what it can
see from the facts the caller passed and escalates what it cannot. It is not an
editorial decision-maker: per ``engine.memory.evidence``'s own rule that
evidence is the *input* to a judgement, the loop only snaps a boundary onto an
edge the transcript already defines, trims spans the truth does not cover,
re-derives an artifact from an unchanged plan, or escalates. It never invents a
span, never inflates or smooths a confidence, never re-scores a candidate, and
never edits emitted interchange text — the exporter is a pure function of the
plan, and patching its output would break that.

Termination
-----------

The loop cannot hang. Five guards, from ``docs/verify-repair-design.md`` §5:

- one plan-level repair per pass, then the whole suite re-runs. Two repairs
  computed against the same plan can move the same boundary in opposite
  directions — a mid-word cut wants it at the word's start, a mid-sentence cut
  wants it at the utterance's end — and applying both in one pass would undo the
  first with the second;
- a repair that does not move the plan escalates rather than retries: a finding
  whose span does not reach the interval it names (itself the symptom of a
  transcript/plan desync), and a snap whose target lies past the end of the
  source or off the ground truth, which would be clamped onto the grid and moved
  back on the next pass — oscillation, not progress;
- every plan-level repair that does move the plan strictly decreases the
  ``measure`` tuple ``(kept_duration, boundary_distance_to_nearest_word_edge,
  span_count)``; a snap that increases kept time is permitted only onto time the
  truth covers, so it cannot gain at both components at once;
- a ``REEMIT``/``REMUX`` that does not clear its fault is not retried — the
  fault converts to ``UNDECIDABLE`` and escalates, so an emitter whose output is
  not a function of its input surfaces immediately instead of looping;
- ``MAX_PASSES = len(words) + 3``, and hitting the cap is reported as a failure
  of the loop rather than a silent stop.

Purity
------

Deterministic and side-effect free: no wall clock, no network, no filesystem,
no ``sys.path``. Identical input yields byte-identical findings, repair records
and final plan. Probed facts (a container's duration, a decoded frame count,
whether an asset path resolves) arrive as values on ``RenderArtifact`` and
``InterchangeArtifact`` — the caller performs the probe, the loop performs the
reasoning. A fact the caller did not supply is reported as ``UNDECIDABLE``, not
as a pass: a clip this loop could not measure is not a clip it has verified.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from enum import Enum

from engine.cut_algebra import DEGENERATE_SECONDS, merge_intervals
from engine.edit_plan import _BOUND_EPS, join_words
from engine.memory.evidence import EvidenceSet, Modality
from engine.memory.intervals import Interval, IntervalSet
from models.core import Segment, Word

# ── tolerances: the contract, named once and cited, never re-spelled ──────────
# A cue ending inside this of the render is on the render's end, not past it
# (``tools/editorial_audit.py:56`` ``TOLERANCE_S``).
CAPTION_TOLERANCE_S = 0.05
# An interchange interval's endpoints may sit this many frames off the plan: the
# export snaps each interval to the frame grid independently
# (``tools/nle_check.py:87``, worst deviation measured over the corpus: 1.00).
INTERCHANGE_FRAME_TOLERANCE = 1.5
# The exported timeline runs a fraction of a frame behind the container
# (``tools/nle_check.py:98``; largest measured gap 0.69 frames at 30 fps).
RENDER_FRAME_TOLERANCE = 2.0
# ffmpeg's own container clocking cannot hit exact equality, so the container
# duration is checked to 1% of the plan (``engine/qa.py:245``; worst measured
# drift 0.025 s in 17.08 s, so this is ~7x the observed noise).
CONTAINER_DURATION_TOLERANCE = 0.01
# A duplicate sentence is only a duplicate if it is long enough to be a
# sentence and not a stock phrase (``tools/editorial_audit.py:638``).
DUPLICATE_MIN_CHARS = 24
# The sentence terminators the transcript punctuation check covers: Latin,
# Devanagari, Armenian, CJK — the corpus already carries Hungarian and Telugu,
# so an English-only split would read every Telugu utterance as one sentence.
_SENTENCE_END = re.compile(r"[.!?։।。？][\"')\]]*\s*$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?։।。？])\s+")
# Content words a caption cue must justify: a 3-letter stopword list would let a
# cue ship "the and of" as text. Mirrors ``tools/editorial_audit.py:410``.
_STOPWORDS = frozenset(
    {"the", "a", "an", "of", "and", "to", "in", "is", "it", "that", "this"}
)


class Fault(str, Enum):
    """What a failed check is. Each maps to one check, one diagnosis and a set
    of permitted repairs. ``UNDECIDABLE`` is the escape hatch that keeps the
    loop terminating — it is a terminal state, never repaired."""

    MID_WORD_CUT = "mid_word_cut"            # boundary inside a spoken word
    MID_SENTENCE_CUT = "mid_sentence_cut"    # boundary splits an utterance
    SHAVED_TAIL_WORD = "shaved_tail_word"    # the render truncates a word's tail
    UNSUPPORTED_SPAN = "unsupported_span"    # kept time with no backing truth
    LOW_CONFIDENCE_ONLY = "low_confidence_only"  # backed only below the floor
    DUPLICATE_SENTENCE = "duplicate_sentence"    # same sentence in two clips
    CAPTION_TEXT_DRIFT = "caption_text_drift"    # cue text absent from the plan
    CAPTION_SPAN_DRIFT = "caption_span_drift"    # cue ends past the render
    INTERCHANGE_DRIFT = "interchange_drift"  # EDL/FCPXML interval off the plan
    RECORD_OVERLAP = "record_overlap"        # non-monotonic record per channel
    RENDER_DRIFT = "render_drift"            # MP4 duration off the plan
    RENDER_UNDECODABLE = "render_undecodable"  # declared stream decodes to nil
    MEDIA_UNRESOLVABLE = "media_unresolvable"  # FROM CLIP / asset src not found
    UNDECIDABLE = "undecidable"              # the check cannot decide — REVIEW


class Repair(str, Enum):
    """The closed set of permitted repairs. This closure is the whole safety
    argument: it is what makes the loop terminate and what makes it auditable.
    Anything not in this set — patching emitted text, re-encoding to hit a
    duration, re-scoring a candidate — is forbidden by
    ``docs/verify-repair-design.md`` §4.5."""

    SNAP = "snap"        # move a boundary onto a word/utterance edge
    TRIM = "trim"        # remove spans the truth does not cover
    REEMIT = "reemit"    # regenerate an artifact from the unchanged plan
    REMUX = "remux"      # stream-copy container repair
    ESCALATE = "escalate"  # record and stop; a human looks at it


class Verdict(str, Enum):
    """The auditor's three states (``tools/editorial_audit.py:838``). ``REVIEW``
    still fails the exit code: a clip the loop could not measure is not a clip
    it has passed."""

    CLEAN = "clean"
    REVIEW = "review"
    FAIL = "fail"


@dataclass(frozen=True)
class Finding:
    """One failed check, located on the plan.

    ``interval_index`` is the plan interval the finding is about (``-1`` when it
    is about an artifact rather than a span), and ``edge`` is which end of that
    interval (``"whole"`` for a span-wide finding). Together they are the repair
    target, and they are the key the loop uses to recognise a fault that
    survived its own repair.
    """

    fault: Fault
    verdict: Verdict
    stage: str                    # the check that produced this
    interval_index: int
    edge: str
    span: Interval                # the offending region, in source seconds
    detail: str
    instrument: str               # the component that decided


@dataclass(frozen=True)
class RepairRecord:
    """One repair, for ``repairs.jsonl`` beside ``edit_plan.json``.

    Carries ``(fault, stage, before, after, instrument)`` as the design's §4.6
    requires: a repair that is not recorded is, by this project's own standard
    (``selekyt/pipeline.py:714``, "a repaired container, not the file they
    handed in"), a silent rewrite of the editor's decision. ``changed_plan`` is
    False for ``REEMIT``/``REMUX``, which the caller performs; ``before`` and
    ``after`` are the interval the repair touched, with ``after=None`` meaning
    the interval did not survive it.
    """

    fault: Fault
    stage: str
    repair: Repair
    before: tuple[float, float] | None
    after: tuple[float, float] | None
    instrument: str
    note: str
    changed_plan: bool


@dataclass(frozen=True)
class Diagnosis:
    """What a finding *is*, which channel failed, and what may be done."""

    fault: Fault
    modality: Modality            # which channel the failure is on
    permitted: tuple[Repair, ...]
    rationale: str


@dataclass(frozen=True)
class Cue:
    """One caption cue, in output (render) time."""

    text: str
    start: float
    end: float


@dataclass(frozen=True)
class SiblingClip:
    """Another clip shipped from the same source, for duplicate detection.

    ``rank`` is the candidate's own score (``editorial_score`` / ``virality_score``
    on ``Candidate``); a duplicate is trimmed out of the lower-ranked clip and
    escalated out of the higher-ranked one, and the ranking is never recomputed
    here — re-scoring to make a check pass is forbidden.
    """

    text: str
    rank: float


@dataclass(frozen=True)
class InterchangeArtifact:
    """An EDL or FCPXML as facts, not as text. The loop never parses emitted
    text: it compares the intervals and record spans a reader — pycmx or the
    vendored OTIO adapter, readers SeleKyT did not write — reported against the
    plan."""

    kind: str                                     # "edl" | "fcpxml"
    intervals: tuple[tuple[float, float], ...] = ()
    record_spans: tuple[tuple[float, float], ...] = ()
    media_src: str = ""                           # asset src / FROM CLIP
    media_resolvable: bool = True                 # the caller resolved it


@dataclass(frozen=True)
class RenderArtifact:
    """The rendered MP4 as facts. ``decoded_frames == -1`` means the caller did
    not decode a sample, which is reported as undecidable rather than clean."""

    duration: float | None = None                 # None = unprobeable
    decoded_frames: int = -1
    declared_audio_present: bool = True


@dataclass(frozen=True)
class VerifyContext:
    """Everything verification needs, as values. The ground-truth ladder
    (§4.2) is read off this context: evidence is used when present (G3),
    transcript word coverage otherwise (G2), and an absent rung is reported as
    degraded, never as a pass."""

    source_duration: float
    fps: float
    words: tuple[Word, ...] = ()
    utterances: tuple[Segment, ...] = ()
    evidence: EvidenceSet | None = None
    confidence_floor: float = 0.0
    cues: tuple[Cue, ...] = ()
    interchange: InterchangeArtifact | None = None
    render: RenderArtifact | None = None
    siblings: tuple[SiblingClip, ...] = ()
    rank: float = 0.0


@dataclass(frozen=True)
class VerifyReport:
    """The result of one pass of the check suite."""

    findings: tuple[Finding, ...]

    @property
    def verdict(self) -> Verdict:
        return worst_verdict(self.findings)


@dataclass(frozen=True)
class LoopResult:
    """The result of the whole loop: the final plan, the audit trail, and the
    state it stopped in.

    ``caller_actions`` are the ``REEMIT``/``REMUX`` records the caller must
    perform and then re-run; ``escalated`` are the findings a human must look
    at, including every fault that survived its own repair. ``capped`` means the
    loop hit ``MAX_PASSES`` — the loop's own failure, reported as one.
    """

    plan: tuple[tuple[float, float], ...]
    findings: tuple[Finding, ...]
    repairs: tuple[RepairRecord, ...]
    escalated: tuple[Finding, ...]
    caller_actions: tuple[RepairRecord, ...]
    passes: int
    capped: bool
    verdict: Verdict


# ── helpers over the context ──────────────────────────────────────────────────


def _words(ctx: VerifyContext) -> tuple[Word, ...]:
    """Words in transcript time order. Sorting is the caller's job in some
    paths and not others, so the loop sorts once and every check sees the same
    order — a check that depended on the transcript's declaration order would
    not be a function of the plan."""
    return tuple(sorted(ctx.words, key=lambda w: (w.start, w.end)))


def _terminal_punct(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.strip()))


def _sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _utterance_of(t: float, utterances: tuple[Segment, ...]) -> int:
    if not utterances:
        return -1
    starts = [u.start for u in utterances]
    i = bisect.bisect_right(starts, t) - 1
    return i if 0 <= i < len(utterances) else -1


def _utterance_words(seg: Segment, words: tuple[Word, ...]) -> list[Word]:
    """The words of one utterance, by time. Prefers the segment's own word
    list; falls back to the transcript's words inside the segment's span."""
    if seg.words:
        return sorted(seg.words, key=lambda w: (w.start, w.end))
    return [w for w in words if seg.start <= w.start <= seg.end]


def _kept(word: Word, plan: list[tuple[float, float]]) -> bool:
    return any(a <= word.start and word.end <= b for a, b in plan)


def _adjacent(starts: list[float], t: float) -> tuple[int, int]:
    """Indices of the word at-or-before and at-or-after ``t``, clamped."""
    lo = max(0, min(bisect.bisect_right(starts, t) - 1, len(starts) - 1))
    hi = max(0, min(bisect.bisect_left(starts, t), len(starts) - 1))
    return lo, hi


def _support(ctx: VerifyContext) -> tuple[IntervalSet, str]:
    """The ground truth for span coverage, and which rung of the ladder it is.

    Evidence is the designed-in ground truth and is unpopulated today (§3.4),
    so an absent store degrades to transcript word coverage and says so in the
    rung name — the finding's ``instrument`` carries it, so a reader of the
    repair record sees which truth was used.
    """
    if ctx.evidence is not None and not ctx.evidence.is_empty:
        return ctx.evidence.intervals, "G3 evidence"
    words = _words(ctx)
    if not words:
        return IntervalSet.empty(), "G0 no truth available"
    return (
        IntervalSet.of(*((w.start, w.end) for w in words)),
        "G2 word coverage (degraded: no evidence store)",
    )


def _snap_truth(ctx: VerifyContext) -> tuple[IntervalSet, str]:
    """The truth a snap may move a boundary onto.

    ``_support`` is the rung the *checks* read, which is the whole evidence
    store. A snap is a repair against that truth, but the confidence floor is
    part of what counts as backing (an item below the floor is reported, not
    relied on), so the repair is held to the stronger of the two when the floor
    excludes anything. Without this, ``MID_SENTENCE_CUT`` would snap a boundary
    onto time ``LOW_CONFIDENCE_ONLY`` must then trim straight back — two
    repairs undoing each other, which §5 I1 forbids a repair to be able to do.
    """
    if (ctx.evidence is not None and not ctx.evidence.is_empty
            and ctx.confidence_floor > 0.0):
        above = ctx.evidence.above_confidence(ctx.confidence_floor).intervals
        if not above.is_empty:
            return above, "G3 evidence at or above the confidence floor"
    return _support(ctx)


def _plan_words(plan: list[tuple[float, float]],
                ctx: VerifyContext) -> list[Word]:
    return [w for w in _words(ctx) if _kept(w, plan)]


def _plan_text(plan: list[tuple[float, float]], ctx: VerifyContext) -> str:
    """The text the plan keeps, rebuilt the only correct way: ``join_words`` on
    the word tokens inside the intervals. A caption or content pack that
    disagrees with this is what ``CAPTION_TEXT_DRIFT`` reports."""
    return join_words(_plan_words(plan, ctx))


def _sentence_spans(plan: list[tuple[float, float]],
                    ctx: VerifyContext) -> list[tuple[str, Interval]]:
    """The sentences the plan ships, each with its time span. Built by walking
    the kept words and cutting after one that closes a sentence, so the span
    and the text come from the same tokens and cannot desync."""
    out: list[tuple[str, Interval]] = []
    group: list[Word] = []
    for w in _plan_words(plan, ctx):
        group.append(w)
        if _terminal_punct(w.text):
            out.append((join_words(group), Interval(group[0].start, w.end)))
            group = []
    if group:
        out.append((join_words(group), Interval(group[0].start, group[-1].end)))
    return out


def _normalise_plan(intervals: list[tuple[float, float]],
                    source_duration: float) -> list[tuple[float, float]]:
    """Clamp into ``[0, source_duration]``, drop degenerate spans, sort and
    merge. ``engine.cut_algebra.normalize_keeps`` applies the same rules to the
    same kind of input, so a repaired plan is a plan the exporters recognise."""
    if not intervals:
        return []
    top = max(source_duration, 0.0)
    clamped = [(max(0.0, min(a, top)), max(0.0, min(b, top)))
               for a, b in intervals]
    kept = [(a, b) for a, b in clamped if b - a > DEGENERATE_SECONDS]
    return merge_intervals(kept, gap=0.0)


# Repairs that change the plan, as opposed to regenerating an artifact from it.
_PLAN_REPAIRS = frozenset({Repair.SNAP, Repair.TRIM})

# Where two faults at the same boundary disagree about which way to move it,
# the one that resolves the other's case goes first. ``SHAVED_TAIL_WORD`` and
# ``MID_WORD_CUT`` both fire on an interval end inside a word: the mid-word
# repair drops the fragment, the tail repair completes it. Only one of those is
# available at a given boundary — ``check_shaved_tail_word`` stays quiet unless
# completing the word also completes the utterance — so this precedence picks
# the tail repair where it applies and leaves the mid-word repair to drop the
# fragment everywhere else.
_BOUNDARY_PRECEDENCE = {
    Fault.SHAVED_TAIL_WORD: 0,
    Fault.MID_WORD_CUT: 1,
    Fault.MID_SENTENCE_CUT: 2,
}


def _repair_key(f: Finding, ctx: VerifyContext) -> tuple:
    """The order faults are repaired in within one pass.

    Artifact faults come first. The export is specified as a pure function of
    the plan, so a disagreement between them is the emitter's until a re-emit
    proves otherwise; repairing the plan to match a divergent artifact is the
    repair backwards, and an artifact repair cannot change the plan, so putting
    it first costs a pass and never undoes anything.

    Within a boundary, the precedence table resolves the faults that disagree
    about a direction. Position breaks the rest, so two runs over the same input
    still repair in the same order.
    """
    d = diagnose(f, ctx)
    artifact = 0 if d.permitted and d.permitted[0] not in _PLAN_REPAIRS else 1
    return (artifact, f.interval_index,
            0 if f.edge == "start" else 1,
            _BOUNDARY_PRECEDENCE.get(f.fault, 9), f.fault.value, f.stage)


def _key(f: Finding) -> tuple:
    """The identity of a fault across passes: the same check, the same interval,
    the same edge and the same span. Two findings that share a key are the same
    problem, so a repair that did not change the verdict is visible as a
    recurrence rather than as a new complaint.

    The fault itself is *not* in the key: an ``UNDECIDABLE`` conversion replaces
    the finding it was raised from, and keying on the fault would keep the
    original ``FAIL`` beside the escalation that superseded it — a reader would
    see both the verdict and its retraction, and the loop's exit verdict would
    still fail.
    """
    return (
        f.stage, f.interval_index, f.edge,
        round(f.span.start, 6), round(f.span.end, 6),
    )


def worst_verdict(findings) -> Verdict:
    """The worst of a set of findings. ``FAIL`` beats ``REVIEW`` beats
    ``CLEAN``, and an empty set is clean — not unmeasured."""
    if any(f.verdict == Verdict.FAIL for f in findings):
        return Verdict.FAIL
    if any(f.verdict == Verdict.REVIEW for f in findings):
        return Verdict.REVIEW
    return Verdict.CLEAN


# ── VERIFY: one check per fault ───────────────────────────────────────────────


def check_mid_word_cuts(plan: list[tuple[float, float]],
                        ctx: VerifyContext) -> tuple[Finding, ...]:
    """No interval boundary may fall inside a spoken word.

    A boundary within ``_BOUND_EPS`` of a word's end is on the boundary, not
    inside it: the two disagree in the last ulp because sentence bounds are
    estimated to character span while word timing is measured, and the shipped
    pentagon source met ``364.84`` with a word ending at ``364.84000000000003``
    (``engine.edit_plan._word_aligned``). The check and the repair share that
    tolerance, so a bound this check leaves alone is one the repair will not
    move — and one this check flags is one the repair will always move.
    """
    words = _words(ctx)
    if not words or not plan:
        return ()
    out: list[Finding] = []
    for index, (a, b) in enumerate(plan):
        for w in words:
            for t, edge in ((a, "start"), (b, "end")):
                if not (w.start < t < w.end) or w.end - t < _BOUND_EPS:
                    continue
                out.append(Finding(
                    fault=Fault.MID_WORD_CUT, verdict=Verdict.FAIL,
                    stage="check_mid_word_cuts", interval_index=index, edge=edge,
                    span=Interval(w.start, w.end),
                    detail=(f"{edge} {t:.3f}s inside word {w.text!r} "
                            f"({w.start:.3f}-{w.end:.3f}s)"),
                    instrument="engine.edit_plan._word_aligned",
                ))
    return tuple(out)


def check_mid_sentence_cuts(plan: list[tuple[float, float]],
                            ctx: VerifyContext) -> tuple[Finding, ...]:
    """No cut may take a word out of the middle of a sentence, and no edge may
    open or close partway through one.

    Two predicates, from ``tools/editorial_audit.py``: ``check_cuts_respect_
    sentences`` asks whether a boundary *loses a word the clip kept* (a
    boundary that only shortens a pause between two kept words is not a split —
    every mid-sentence finding the audit produced on the corpus was that case),
    and ``check_edges_respect_sentences`` looks outward at the words the clip
    dropped, which is what catches the clip that opens on "Syria." after
    dropping the sentence it finished.

    The inward predicate keys on which side of the boundary the clip keeps: a
    close is only a split when the clip keeps the word before the edge and drops
    the one after it, and an open only when it keeps the word after the edge and
    drops the one before it. A boundary that ends a clip a few milliseconds into
    the *next* utterance — having kept none of it — is not a split of that
    utterance, it is an overrun, and ``MID_WORD_CUT`` is the fault that covers
    it. Naming it here made the loop prefer the mid-sentence repair, which snaps
    the boundary forward to the utterance's end and absorbed a whole sentence
    the editor never selected.

    A word only counts as dropped when the truth reaches it. A clip trimmed to
    the edge of the evidence has not cut a sentence in half — there is no speech
    there to complete it — and counting those words would make this check and
    ``UNSUPPORTED_SPAN``'s trim undo each other (§5, I1: a repair may not create
    a fault).

    The sentence structure is the transcript's own utterance spans, not the
    plan's text. Without them the question has no answer and the finding is
    ``UNDECIDABLE`` rather than a pass. With them, an utterance that carries no
    terminal punctuation at all — 34-47% of English segments on this corpus —
    makes *every* edge in it consistent with mid-sentence, and that too is
    ``UNDECIDABLE``: 34% is a property of the ASR output, not of the clips, and
    a guess at a sentence end is an editorial decision this loop does not make.
    """
    words = _words(ctx)
    if not plan or len(words) < 2:
        return ()
    if not ctx.utterances:
        return (Finding(
            fault=Fault.UNDECIDABLE, verdict=Verdict.REVIEW,
            stage="check_mid_sentence_cuts", interval_index=-1, edge="whole",
            span=Interval(0.0, 0.0),
            detail=("no transcript utterance spans; the sentence structure is "
                    "the transcript's own segments and cannot be derived from "
                    "the plan's text without inheriting its joins"),
            instrument="transcript utterance spans (G2)",
        ),)
    starts = [w.start for w in words]
    sent_of = [_utterance_of(w.start, ctx.utterances) for w in words]
    # Words the truth reaches. A clip that ends at the edge of the available
    # evidence has not cut a sentence in half — there is no speech there to
    # complete it, so the boundary is the end of the material, not a split.
    # Without this, trimming a span down to the evidence (``UNSUPPORTED_SPAN``'s
    # repair) would manufacture a mid-sentence finding at the trim line and the
    # two repairs would undo each other.
    support, _rung = _support(ctx)
    out: list[Finding] = []

    def fail(index: int, edge: str, seg: Segment, detail: str) -> None:
        out.append(Finding(
            fault=Fault.MID_SENTENCE_CUT, verdict=Verdict.FAIL,
            stage="check_mid_sentence_cuts", interval_index=index, edge=edge,
            span=Interval(seg.start, seg.end), detail=detail,
            instrument="transcript utterance spans (G2)",
        ))

    def lost(word: Word) -> bool:
        """A word the clip dropped that the truth says is really there."""
        return not _kept(word, plan) and support.covers(Interval(word.start,
                                                                word.end))

    for index, (a, b) in enumerate(plan):
        for t, edge in ((a, "start"), (b, "end")):
            lo, hi = _adjacent(starts, t)
            # ``lo == hi`` is a boundary exactly on a word edge — the outward
            # part below is what decides that case, because it sees the words
            # the clip dropped rather than the two it kept.
            if lo == hi or sent_of[lo] < 0 or sent_of[lo] != sent_of[hi]:
                continue
            seg = ctx.utterances[sent_of[lo]]
            uw = _utterance_words(seg, words)
            if edge == "start":
                # The clip opens at ``t``. The question is what it dropped on
                # the far side, so the word it opens *on* has to be one it keeps
                # — otherwise the boundary is the clip's own end in another
                # utterance, and the word this names is not the clip's first.
                if not _kept(words[hi], plan):
                    continue
                dropped = [w for w in uw
                           if w.end <= t + _BOUND_EPS and lost(w)]
                if not dropped:
                    continue
                fail(index, edge, seg,
                     f"{edge} {t:.3f}s opens mid-sentence: the clip's first "
                     f"word {words[hi].text!r} follows {dropped[0].text!r}, "
                     f"dropped with no sentence break inside utterance "
                     f"{seg.start:.3f}-{seg.end:.3f}s")
            else:
                # The clip closes at ``t``, keeping the word before the edge.
                if not _kept(words[lo], plan):
                    continue
                dropped = [w for w in uw
                           if w.start >= t - _BOUND_EPS and lost(w)]
                if not dropped:
                    continue
                fail(index, edge, seg,
                     f"{edge} {t:.3f}s closes mid-sentence: the clip's last "
                     f"word {words[lo].text!r} is followed by {dropped[0].text!r}, "
                     f"dropped with no sentence break inside utterance "
                     f"{seg.start:.3f}-{seg.end:.3f}s")

    kept_words = [w for w in words if _kept(w, plan)]
    if not kept_words:
        return tuple(out)
    undecidable: list[Finding] = []

    def undecidable_edge(index: int, edge: str, why: str) -> None:
        undecidable.append(Finding(
            fault=Fault.UNDECIDABLE, verdict=Verdict.REVIEW,
            stage="check_mid_sentence_cuts", interval_index=index, edge=edge,
            span=Interval(0.0, 0.0), detail=why,
            instrument="transcript utterance spans (G2)",
        ))

    first = kept_words[0]
    i = _utterance_of(first.start, ctx.utterances)
    if i >= 0:
        seg = ctx.utterances[i]
        uw = _utterance_words(seg, words)
        if not any(_terminal_punct(w.text) for w in uw):
            undecidable_edge(_covering(plan, first.start), "start",
                             f"the utterance at {seg.start:.3f}s carries no "
                             f"terminal punctuation, so no edge inside it is "
                             f"distinguishable from a clean one")
        else:
            # Only speech the truth reaches can be what the clip dropped; a
            # clip opening at the edge of the evidence opens the material, not
            # a sentence halfway through.
            dropped = " ".join(w.text for w in uw
                               if w.end <= first.start + 0.05 and lost(w))
            if dropped.strip() and not _terminal_punct(dropped):
                fail(_covering(plan, first.start), "start", seg,
                     f"opens mid-sentence: the clip's first word "
                     f"{first.text!r} follows {dropped[-72:]!r} with no "
                     f"sentence break between them")

    last = kept_words[-1]
    j = _utterance_of(last.start, ctx.utterances)
    if j >= 0:
        seg = ctx.utterances[j]
        uw = _utterance_words(seg, words)
        # Words the clip dropped after its last, and only ones the truth
        # reaches: a clip that runs to the end of the evidence has nothing
        # after it to complete the sentence with.
        after = [w for w in uw
                 if w.start >= last.end - 0.05 and lost(w)]
        if after:
            if not any(_terminal_punct(w.text) for w in uw):
                undecidable_edge(_covering(plan, last.start), "end",
                                 f"the utterance at {seg.start:.3f}s carries "
                                 f"no terminal punctuation, so the clip's close "
                                 f"cannot be classified")
            elif not _terminal_punct(last.text):
                post = " ".join(w.text for w in after).strip()
                fail(_covering(plan, last.start), "end", seg,
                     f"closes mid-sentence: the clip's last word {last.text!r} "
                     f"is followed by {post[:72]!r} with no sentence break")
    return tuple(out + undecidable)


def _covering(plan: list[tuple[float, float]], t: float) -> int:
    """The plan interval containing ``t``, or ``-1``. A word the plan does not
    cover is a transcript/plan desync, and ``-1`` reaches the loop's no-op
    guard rather than an index error."""
    for index, (a, b) in enumerate(plan):
        if a <= t <= b:
            return index
    return -1


def check_shaved_tail_word(plan: list[tuple[float, float]],
                           ctx: VerifyContext) -> tuple[Finding, ...]:
    """The last kept word must fit inside the interval with its phoneme tail.

    Word *duration* alone is not the signal — unstressed function words in real
    speech genuinely run 90-150 ms, so thresholding on duration flags natural
    speech (``tools/editorial_audit.py:397``). What matters is that the
    interval's end is at or past the word's end, which is the actual cut. The
    fault name says "shaved tail word" and the design's fault table describes it
    as "last kept word < 200ms"; the check the table points at measures the cut,
    not the duration, and the cut is what is implemented here.
    """
    words = _words(ctx)
    if not words or not plan:
        return ()
    last_end = plan[-1][1]
    kept = [w for w in words if w.start < last_end]
    if not kept:
        return ()
    w = max(kept, key=lambda w: w.start)
    if w.end <= last_end + _BOUND_EPS:
        return ()
    if ctx.utterances:
        # A partial word at the clip's end is extended only when completing it
        # also completes the utterance — the closing word is the one the
        # sentence exists to deliver, and a render that truncates it cuts the
        # clip's payoff mid-phoneme. A partial word with speech still after it
        # is dropped instead: the utterance continues past the clip, so the
        # fragment belongs to what the speaker said next, and snapping the
        # boundary forward to *this* word's end would ship a clip that closes
        # mid-sentence (see ``MID_SENTENCE_CUT``) while the word it extended is
        # not the sentence's end. ``edit_plan._word_aligned`` makes the same
        # choice on the producer side, so the loop's plan and the plan that
        # shipped agree.
        i = _utterance_of(w.start, ctx.utterances)
        if i >= 0:
            uw = _utterance_words(ctx.utterances[i], words)
            if uw and uw[-1].end > w.end + _BOUND_EPS:
                return ()
    return (Finding(
            fault=Fault.SHAVED_TAIL_WORD, verdict=Verdict.FAIL,
            stage="check_shaved_tail_word",
            interval_index=len(plan) - 1, edge="end",
            span=Interval(last_end, w.end),
            detail=(f"final word {w.text!r} ends {w.end:.3f}s but the "
                    f"interval ends {last_end:.3f}s"),
            instrument="tools.editorial_audit.check_last_word_is_not_shaved",
        ),)
    return ()


def check_unsupported_spans(plan: list[tuple[float, float]],
                            ctx: VerifyContext) -> tuple[Finding, ...]:
    """Every kept span must sit inside the truth.

    ``IntervalSet.covers`` is the question the verify stage exists to ask
    (``engine.memory.intervals.py:380``), and ``gaps`` inverts it to name the
    precise unsupported sub-span — not "this span is thin" but "these seconds
    have no backing", which is what a trim removes.
    """
    support, rung = _support(ctx)
    out: list[Finding] = []
    for index, iv in enumerate(plan):
        span = Interval(*iv)
        if support.covers(span):
            continue
        for gap in support.gaps(span):
            out.append(Finding(
                fault=Fault.UNSUPPORTED_SPAN, verdict=Verdict.FAIL,
                stage="check_unsupported_spans", interval_index=index,
                edge="whole", span=gap,
                detail=(f"{gap.start:.3f}-{gap.end:.3f}s ({gap.duration:.3f}s) "
                        f"of interval {index} has no backing; truth: {rung}"),
                instrument=f"IntervalSet.covers / gaps ({rung})",
            ))
    return tuple(out)


def check_low_confidence_only(plan: list[tuple[float, float]],
                              ctx: VerifyContext) -> tuple[Finding, ...]:
    """Support may exist only below the confidence floor.

    Confidence is never inflated (``engine.memory.evidence.py:38-45``): the
    repair trims to the above-floor support and reports what it discarded, per
    the module's own rule that filters report rather than repair. Without an
    evidence store the rung does not exist and the check reports nothing — the
    G2 degradation is ``check_unsupported_spans``'s, and a low-confidence
    finding with no confidence to read would be a fabrication.
    """
    if ctx.evidence is None or ctx.evidence.is_empty or ctx.confidence_floor <= 0.0:
        return ()
    above = ctx.evidence.above_confidence(ctx.confidence_floor).intervals
    full = ctx.evidence.intervals
    out: list[Finding] = []
    for index, iv in enumerate(plan):
        span = Interval(*iv)
        if not full.covers(span) or above.covers(span):
            continue
        dropped = list(ctx.evidence.in_window(span.start, span.end)
                       .below_confidence(ctx.confidence_floor))
        names = ", ".join(d.claim for d in dropped[:3]) or "no items"
        for gap in above.gaps(span):
            out.append(Finding(
                fault=Fault.LOW_CONFIDENCE_ONLY, verdict=Verdict.FAIL,
                stage="check_low_confidence_only", interval_index=index,
                edge="whole", span=gap,
                detail=(f"{gap.start:.3f}-{gap.end:.3f}s is backed only below "
                        f"the floor {ctx.confidence_floor}; discarded: {names}"),
                instrument="EvidenceSet.above_confidence / below_confidence",
            ))
    return tuple(out)


def check_duplicate_sentence(plan: list[tuple[float, float]],
                             ctx: VerifyContext) -> tuple[Finding, ...]:
    """The same sentence must not ship in two clips (``tools/editorial_audit.
    py:630``). A duplicate is trimmed out of the lower-ranked clip and escalated
    out of the higher-ranked one; the ranking is the candidate's own and is
    never recomputed here."""
    if not ctx.siblings or not plan:
        return ()
    their = set()
    for sib in ctx.siblings:
        for s in _sentences(sib.text):
            key = " ".join(s.lower().split())
            if len(key) >= DUPLICATE_MIN_CHARS:
                their.add(key)
    if not their:
        return ()
    out: list[Finding] = []
    for sentence, span in _sentence_spans(plan, ctx):
        key = " ".join(sentence.lower().split())
        if len(key) < DUPLICATE_MIN_CHARS or key not in their:
            continue
        index = _covering(plan, span.start)
        lower = ctx.rank <= max((s.rank for s in ctx.siblings), default=0.0)
        if not lower:
            # The duplicate is the other clip's to give up: this one outranks
            # it, and trimming the better clip to make a check pass is the
            # forbidden move. No interval is named, so the repair has no target
            # and the loop escalates.
            out.append(Finding(
                fault=Fault.DUPLICATE_SENTENCE, verdict=Verdict.FAIL,
                stage="check_duplicate_sentence", interval_index=-1,
                edge="whole", span=span,
                detail=(f"{sentence[:60]!r} also ships in a lower-ranked clip; "
                        f"this clip outranks it, so the duplicate is removed "
                        f"there, not here"),
                instrument="tools.editorial_audit.check_no_duplicate_sentences",
            ))
            continue
        if index < 0:
            continue
        out.append(Finding(
            fault=Fault.DUPLICATE_SENTENCE, verdict=Verdict.FAIL,
            stage="check_duplicate_sentence", interval_index=index, edge="whole",
            span=span,
            detail=(f"{sentence[:60]!r} also ships in a higher-ranked clip; "
                    f"trimming it out of this one"),
            instrument="tools.editorial_audit.check_no_duplicate_sentences",
        ))
    return tuple(out)


def check_caption_text(plan: list[tuple[float, float]],
                       ctx: VerifyContext) -> tuple[Finding, ...]:
    """Every cue's content words must appear in the plan's own text. Span is in
    output time: a cue is a thing the render carries, and locating it on the
    source is the re-emit's job, not this check's."""
    if not ctx.cues:
        return ()
    text = _plan_text(plan, ctx).lower()
    if not text:
        return ()
    out: list[Finding] = []
    for cue in ctx.cues:
        for w in cue.text.split():
            wl = w.strip(",.!?;:-\"'").lower()
            if len(wl) > 2 and wl not in _STOPWORDS and wl not in text:
                out.append(Finding(
                    fault=Fault.CAPTION_TEXT_DRIFT, verdict=Verdict.FAIL,
                    stage="check_caption_text", interval_index=-1, edge="whole",
                    span=Interval(cue.start, cue.end),
                    detail=(f"cue {cue.text[:40]!r} has {wl!r} absent from "
                            f"the plan's transcript"),
                    instrument=("tools/editorial_audit."
                                "check_caption_text_is_transcript_text"),
                ))
                break
    return tuple(out)


def check_caption_spans(plan: list[tuple[float, float]],
                        ctx: VerifyContext) -> tuple[Finding, ...]:
    """A cue must not end past the rendered duration. The render's duration is
    a probe, so an unprobed render is ``UNDECIDABLE`` rather than clean."""
    if not ctx.cues:
        return ()
    if ctx.render is None or ctx.render.duration is None:
        return (Finding(
            fault=Fault.UNDECIDABLE, verdict=Verdict.REVIEW,
            stage="check_caption_spans", interval_index=-1, edge="whole",
            span=Interval(0.0, 0.0),
            detail=("no rendered duration was supplied, so whether the cues "
                    "fit the render cannot be decided"),
            instrument="tools/editorial_audit.check_cues_fit_the_render",
        ),)
    out: list[Finding] = []
    for cue in ctx.cues:
        if cue.end > ctx.render.duration + CAPTION_TOLERANCE_S:
            out.append(Finding(
                fault=Fault.CAPTION_SPAN_DRIFT, verdict=Verdict.FAIL,
                stage="check_caption_spans", interval_index=-1, edge="whole",
                span=Interval(cue.start, cue.end),
                detail=(f"cue ends {cue.end:.3f}s past the "
                        f"{ctx.render.duration:.3f}s render"),
                instrument="tools.editorial_audit.check_cues_fit_the_render",
            ))
    return tuple(out)


def check_interchange_drift(plan: list[tuple[float, float]],
                            ctx: VerifyContext) -> tuple[Finding, ...]:
    """An EDL/FCPXML interval must agree with the plan, one at a time and in
    order. Comparing only the first and last interval passes an export that
    dropped or reordered a middle one — every multi-interval cut in the corpus
    has its extra interval in the middle, where a totals comparison is blind.
    """
    art = ctx.interchange
    if art is None:
        return ()
    if ctx.fps <= 0.0:
        return (Finding(
            fault=Fault.UNDECIDABLE, verdict=Verdict.REVIEW,
            stage="check_interchange_drift", interval_index=-1, edge="whole",
            span=Interval(0.0, 0.0),
            detail="source frame rate unknown; a frame tolerance cannot be cast",
            instrument="tools.nle_check._interval_findings",
        ),)
    frame = 1.0 / ctx.fps
    if len(art.intervals) != len(plan):
        return (Finding(
            fault=Fault.INTERCHANGE_DRIFT, verdict=Verdict.FAIL,
            stage="check_interchange_drift", interval_index=-1, edge="whole",
            span=Interval(0.0, 0.0),
            detail=(f"{len(art.intervals)} {art.kind} interval(s) describe a "
                    f"cut the plan records as {len(plan)} interval(s)"),
            instrument="tools.nle_check._interval_findings",
        ),)
    worst = 0.0
    where = -1
    for index, (got, want) in enumerate(zip(art.intervals, plan)):
        for g, w in zip(got, want):
            d = abs(g - w) / frame
            if d > worst:
                worst, where = d, index
    if worst > INTERCHANGE_FRAME_TOLERANCE:
        return (Finding(
            fault=Fault.INTERCHANGE_DRIFT, verdict=Verdict.FAIL,
            stage="check_interchange_drift", interval_index=where, edge="whole",
            span=Interval(*art.intervals[where]),
            detail=(f"interval {where} of the {art.kind} is {worst:.2f} "
                    f"frames off the plan (tolerance "
                    f"{INTERCHANGE_FRAME_TOLERANCE:.1f})"),
            instrument="tools.nle_check._interval_findings",
        ),)
    return ()


def check_record_overlap(plan: list[tuple[float, float]],
                         ctx: VerifyContext) -> tuple[Finding, ...]:
    """The record grid must advance monotonically per channel. The cursor
    accumulates over kept durations only (``engine/timeline_build.py:56-71``),
    so a correct plan cannot produce an overlap — an overlapping export means
    the emitter's cursor and the plan's diverged, and the repair is a re-emit.
    """
    art = ctx.interchange
    if art is None or len(art.record_spans) < 2:
        return ()
    out: list[Finding] = []
    for prev, cur in zip(art.record_spans, art.record_spans[1:]):
        if cur[0] < prev[1] - _BOUND_EPS:
            out.append(Finding(
                fault=Fault.RECORD_OVERLAP, verdict=Verdict.FAIL,
                stage="check_record_overlap", interval_index=-1, edge="whole",
                span=Interval(prev[0], max(prev[1], cur[1])),
                detail=(f"record span {cur[0]:.3f}-{cur[1]:.3f}s overlaps "
                        f"{prev[0]:.3f}-{prev[1]:.3f}s; the record grid must "
                        f"advance per channel"),
                instrument="engine.nle_export.validate_edl record cursor",
            ))
    return tuple(out)


def check_render_drift(plan: list[tuple[float, float]],
                       ctx: VerifyContext) -> tuple[Finding, ...]:
    """The container duration must agree with the plan to 1%. This is the only
    check that sees a wrong *cut* rather than a broken file: a cleanly encoded
    video of the wrong program passes every other check (``engine/qa.py:327``).
    """
    if ctx.render is None or ctx.render.duration is None:
        return ()
    expected = sum(b - a for a, b in plan)
    if expected <= 0.0:
        return ()
    drift = ctx.render.duration - expected
    if abs(drift) / expected > CONTAINER_DURATION_TOLERANCE:
        return (Finding(
            fault=Fault.RENDER_DRIFT, verdict=Verdict.FAIL,
            stage="check_render_drift", interval_index=-1, edge="whole",
            span=Interval(0.0, ctx.render.duration),
            detail=(f"output is {ctx.render.duration:.3f}s, the plan expected "
                    f"{expected:.3f}s (drift {drift:+.3f}s, "
                    f"{abs(drift) / expected:.1%}); an interval was probably "
                    f"dropped or duplicated"),
            instrument="engine.qa.validate_output",
        ),)
    return ()


def check_render_decodable(plan: list[tuple[float, float]],
                           ctx: VerifyContext) -> tuple[Finding, ...]:
    """A stream that declares itself must decode to something. Zero decoded
    frames on a stream that claims to exist is a broken render
    (``engine/qa.py:296``). A decode sample the caller did not run is
    ``UNDECIDABLE``: a check that passes while its validator is absent is how a
    self-consistent-but-wrong emitter ships unchallenged (``tools/nle_check.py``
    on its own missing readers)."""
    if ctx.render is None:
        return ()
    r = ctx.render
    if r.decoded_frames < 0:
        return (Finding(
            fault=Fault.UNDECIDABLE, verdict=Verdict.REVIEW,
            stage="check_render_decodable", interval_index=-1, edge="whole",
            span=Interval(0.0, 0.0),
            detail="no decode sample was supplied; the render is unmeasured",
            instrument="engine.qa.decode_sample",
        ),)
    if r.declared_audio_present and r.decoded_frames == 0:
        return (Finding(
            fault=Fault.RENDER_UNDECODABLE, verdict=Verdict.FAIL,
            stage="check_render_decodable", interval_index=-1, edge="whole",
            span=Interval(0.0, 0.0),
            detail=("a declared stream decoded to zero frames; a remux of a "
                    "silent file is a silent file, so a remux that yields "
                    "nothing escalates"),
            instrument="engine.qa.decode_sample",
        ),)
    return ()


def check_media_resolvable(plan: list[tuple[float, float]],
                           ctx: VerifyContext) -> tuple[Finding, ...]:
    """An asset's ``src`` / ``FROM CLIP`` must resolve, or every clip comes up
    offline in an NLE and the editor faces a relink dialog for a file that
    never moved. Resolution is a filesystem question, so the caller answers it
    and this check reasons about the answer; a genuinely absent file is
    ``UNDECIDABLE`` — the audit already treats deliberately-deleted media as
    not-run rather than as a defect (``tools/nle_check.py:806``)."""
    art = ctx.interchange
    if art is None:
        return ()
    if art.media_src and not art.media_resolvable:
        return (Finding(
            fault=Fault.MEDIA_UNRESOLVABLE, verdict=Verdict.FAIL,
            stage="check_media_resolvable", interval_index=-1, edge="whole",
            span=Interval(0.0, 0.0),
            detail=(f"asset src {art.media_src!r} does not resolve; re-resolve "
                    f"off the artifact's own FROM CLIP and re-emit"),
            instrument="tools.nle_check._referenced_media_present",
        ),)
    return ()


_CHECKS = (
    check_mid_word_cuts,
    check_mid_sentence_cuts,
    check_shaved_tail_word,
    check_unsupported_spans,
    check_low_confidence_only,
    check_duplicate_sentence,
    check_caption_text,
    check_caption_spans,
    check_interchange_drift,
    check_record_overlap,
    check_render_drift,
    check_render_decodable,
    check_media_resolvable,
)


def verify(plan, ctx: VerifyContext) -> VerifyReport:
    """Run every check. The suite is fixed and is re-run whole after every
    repair — never a subset, because a subset is how a repair that swapped one
    defect for another ships.

    Findings are ordered by where they land on the plan, so two runs over the
    same input produce byte-identical reports.
    """
    intervals = list(plan)
    findings: list[Finding] = []
    for check in _CHECKS:
        findings.extend(check(intervals, ctx))
    findings.sort(key=lambda f: (
        f.interval_index, 0 if f.edge == "start" else 1, f.fault.value, f.stage,
    ))
    return VerifyReport(findings=tuple(findings))


# ── DIAGNOSE ──────────────────────────────────────────────────────────────────


_DIAGNOSES: dict[Fault, tuple[Modality, tuple[Repair, ...], str]] = {
    Fault.MID_WORD_CUT: (
        Modality.TEXT, (Repair.SNAP,),
        "the boundary lies inside a word span; snap it to that word's edge",
    ),
    Fault.MID_SENTENCE_CUT: (
        Modality.TEXT, (Repair.SNAP,),
        "the boundary splits an utterance; snap it to the utterance edge, and "
        "an utterance with no terminal punctuation is undecidable, never a guess",
    ),
    Fault.SHAVED_TAIL_WORD: (
        Modality.TEXT, (Repair.SNAP,),
        "the interval ends before the word does; extend it to the word's end, "
        "and escalate if that end lies past the source or off the truth",
    ),
    Fault.UNSUPPORTED_SPAN: (
        Modality.AUDIO, (Repair.TRIM, Repair.SNAP),
        "kept time has no backing truth; trim the uncovered span, or snap to "
        "the nearest support; an empty result escalates",
    ),
    Fault.LOW_CONFIDENCE_ONLY: (
        Modality.AUDIO, (Repair.TRIM, Repair.ESCALATE),
        "support exists only below the floor; trim to the above-floor support "
        "and report what was discarded — never inflate the confidence",
    ),
    Fault.DUPLICATE_SENTENCE: (
        Modality.EDITORIAL, (Repair.TRIM, Repair.ESCALATE),
        "the same sentence ships twice; trim the lower-ranked clip's interval, "
        "escalate the higher-ranked one, and never re-score either",
    ),
    Fault.CAPTION_TEXT_DRIFT: (
        Modality.TEXT, (Repair.REEMIT,),
        "the cue's text is not the plan's text; re-emit the captions from "
        "engine.edit_plan.join_words — never patch a cue string",
    ),
    Fault.CAPTION_SPAN_DRIFT: (
        Modality.TEXT, (Repair.REEMIT,),
        "the cue ends past the render; re-emit with per-part ASS slicing at the "
        "plan's own offsets; font coverage is a refusal, not a repair",
    ),
    Fault.INTERCHANGE_DRIFT: (
        Modality.EDITORIAL, (Repair.REEMIT,),
        "the EDL/FCPXML disagrees with the plan; re-emit from the plan via "
        "timeline_build then nle_export — never edit emitted text",
    ),
    Fault.RECORD_OVERLAP: (
        Modality.EDITORIAL, (Repair.REEMIT,),
        "the record grid does not advance; the cursor accumulates over kept "
        "durations only, so a correct plan cannot overlap — re-emit",
    ),
    Fault.RENDER_DRIFT: (
        Modality.VISUAL, (Repair.REEMIT,),
        "the MP4 disagrees with the plan; re-render from the plan, never patch "
        "the file and never re-encode to hit a duration",
    ),
    Fault.RENDER_UNDECODABLE: (
        Modality.VISUAL, (Repair.REMUX,),
        "a declared stream decodes to nothing; remux as a stream copy, and "
        "escalate if that yields nothing",
    ),
    Fault.MEDIA_UNRESOLVABLE: (
        Modality.EDITORIAL, (Repair.REEMIT,),
        "the asset path does not resolve; re-resolve off the artifact's own "
        "FROM CLIP and re-emit, or escalate if the file is genuinely absent",
    ),
    Fault.UNDECIDABLE: (
        Modality.EDITORIAL, (Repair.ESCALATE,),
        "the question has no answer on this input; no repair is permitted",
    ),
}


def diagnose(finding: Finding, ctx: VerifyContext) -> Diagnosis:
    """Map a finding to its fault class, the channel that failed, and the
    repairs permitted for it. The permitted set is closed: a repair outside it
    is forbidden by design, and the loop will not attempt it.

    ``modality`` is the channel the failure is *on*. For an unsupported span it
    is the channel of the evidence that did not cover it, read off the store,
    because "no speech here" and "no face here" are different defects even
    though the fault class is the same.
    """
    modality, permitted, rationale = _DIAGNOSES[finding.fault]
    if finding.fault in (Fault.UNSUPPORTED_SPAN, Fault.LOW_CONFIDENCE_ONLY):
        if ctx.evidence and not ctx.evidence.is_empty:
            in_window = list(ctx.evidence.in_window(finding.span.start,
                                                    finding.span.end))
            if in_window:
                modality = in_window[0].modality
    return Diagnosis(fault=finding.fault, modality=modality,
                     permitted=permitted, rationale=rationale)


# ── REPAIR: a value transformation plus a record ──────────────────────────────


def _replace(plan: list[tuple[float, float]], index: int,
             spans: list[tuple[float, float]],
             source_duration: float) -> list[tuple[float, float]]:
    """The plan with interval ``index`` replaced by ``spans``, order intact.
    Normalisation re-sorts and merges, so the caller gets back a plan, not an
    edit — and a degenerate result is dropped here rather than shipped as a
    span the exporters would reject."""
    out = list(plan[:index]) + list(spans) + list(plan[index + 1:])
    return _normalise_plan(out, source_duration)


def apply_repair(plan: list[tuple[float, float]], finding: Finding,
                 ctx: VerifyContext
                 ) -> tuple[list[tuple[float, float]], RepairRecord]:
    """Apply the first permitted repair for ``finding`` and return the new plan
    with a record of what changed. The plan is a value: nothing here mutates
    the caller's list, and the record's ``before``/``after`` are the interval
    the repair touched.

    ``REEMIT`` and ``REMUX`` cannot change the plan — they are files the caller
    writes — so they return the plan unchanged with ``changed_plan=False``, and
    the loop's re-verify decides whether they cleared the fault.
    """
    d = diagnose(finding, ctx)
    repair = d.permitted[0]
    before = tuple(plan[finding.interval_index]) \
        if 0 <= finding.interval_index < len(plan) else None

    if repair == Repair.ESCALATE or finding.fault == Fault.UNDECIDABLE:
        return list(plan), RepairRecord(
            fault=finding.fault, stage=finding.stage, repair=Repair.ESCALATE,
            before=before, after=before, instrument=d.rationale,
            note="no repair is permitted; escalated to human review",
            changed_plan=False,
        )

    if repair == Repair.REEMIT:
        instrument = {
            Fault.CAPTION_TEXT_DRIFT: "engine.edit_plan.join_words",
            Fault.CAPTION_SPAN_DRIFT: "engine/render.py per-part ASS slicing",
            Fault.INTERCHANGE_DRIFT:
                "engine.timeline_build.timeline_for_candidate + nle_export.export",
            Fault.RECORD_OVERLAP:
                "engine.timeline_build.timeline_for_candidate + nle_export.export",
            Fault.RENDER_DRIFT: "engine.render.render_clip",
            Fault.MEDIA_UNRESOLVABLE:
                "tools/nle_check FROM CLIP re-resolution + nle_export.export",
        }.get(finding.fault, "re-emit from the plan")
        return list(plan), RepairRecord(
            fault=finding.fault, stage=finding.stage, repair=Repair.REEMIT,
            before=before, after=before, instrument=instrument,
            note=("regenerate the artifact from the unchanged plan; the plan "
                  "is not touched, and a re-verify that still finds this fault "
                  "converts it to UNDECIDABLE"),
            changed_plan=False,
        )

    if repair == Repair.REMUX:
        return list(plan), RepairRecord(
            fault=finding.fault, stage=finding.stage, repair=Repair.REMUX,
            before=before, after=before,
            instrument="providers.ffmpeg.remux (stream copy, never a re-encode)",
            note=("a remux of a silent file is a silent file; if a stream copy "
                  "still decodes to nothing, escalate"),
            changed_plan=False,
        )

    if not (0 <= finding.interval_index < len(plan)):
        # A finding whose interval the plan does not hold is itself the defect:
        # a transcript/plan desync. Repairing it would invent a span, so the
        # loop escalates on the no-op rather than touching the plan.
        return list(plan), RepairRecord(
            fault=finding.fault, stage=finding.stage, repair=repair,
            before=None, after=None, instrument=d.rationale,
            note=(f"finding names interval {finding.interval_index}, which the "
                  f"plan does not hold; the repair is a no-op"),
            changed_plan=False,
        )

    index = finding.interval_index
    a, b = plan[index]
    ctx_duration = ctx.source_duration

    if repair == Repair.SNAP:
        if finding.fault == Fault.MID_WORD_CUT:
            # ``_word_aligned`` moves a bound inside a word to that word's start,
            # and this check fires exactly when it would (the same ``_BOUND_EPS``
            # test gates both), so the word's start is the snap target on both
            # edges. Read off the finding rather than off the plan, so the repair
            # is bound to the boundary that was measured, not to whatever the
            # interval happens to hold by the time it runs.
            target = finding.span.start
        elif finding.fault == Fault.MID_SENTENCE_CUT:
            i = _utterance_of(finding.span.start, ctx.utterances)
            if i < 0:
                return list(plan), RepairRecord(
                    fault=finding.fault, stage=finding.stage, repair=repair,
                    before=before, after=before, instrument=d.rationale,
                    note="the utterance the finding named is not in the context",
                    changed_plan=False,
                )
            seg = ctx.utterances[i]
            target = seg.start if finding.edge == "start" else seg.end
        else:  # Fault.SHAVED_TAIL_WORD: extend to the word's end
            target = finding.span.end

        # A boundary is clamped into the source before it is tested against it.
        # An unclamped target is what the *speech* asks for; the source is what
        # the render can hold, and a word that runs past the end of the file is
        # cut by the file, not by this repair. Clamping says so honestly: the
        # interval ends where the media does, which is on the grid the source
        # defines. The next pass measures that interval and either clears it or
        # escalates on a no-op, so the loop still terminates.
        target = max(0.0, min(target, ctx_duration))

        # An *utterance* edge the source cannot hold is different, and it is the
        # one case that escalates rather than clamping. Extending a clip to a
        # sentence boundary the file does not reach would manufacture speech the
        # media does not contain, and clamping an utterance edge lands the
        # boundary wherever the file happens to end — off the sentence grid,
        # where the next pass sees a sentence the clip still has not closed and
        # pulls at it again, an oscillation rather than progress. The editor
        # decides whether the source is too short for the speech in it.
        if finding.fault == Fault.MID_SENTENCE_CUT and target > ctx_duration + _BOUND_EPS:
            return list(plan), RepairRecord(
                fault=finding.fault, stage=finding.stage, repair=Repair.ESCALATE,
                before=before, after=before, instrument="engine.edit_plan.align_span",
                note=(f"the sentence boundary {target:.3f}s lies past the end of "
                      f"the source ({ctx_duration:.3f}s); the clip cannot be "
                      f"extended to a complete thought within it and is escalated"),
                changed_plan=False,
            )

        # A snap the truth cannot back is not a repair either: extending a clip
        # into time the evidence does not reach would manufacture the support it
        # is being repaired against, and the unsupported-span check would have to
        # trim it straight back. The whole *resulting* interval has to be backed,
        # not just the point snapped to — a target inside the support can still
        # drag the interval's other end across a gap, which is the same undo a
        # point check would let through. See UNSUPPORTED_SPAN for the same
        # reasoning in the other direction.
        truth, rung = _snap_truth(ctx)
        new_iv = (target, b) if finding.edge == "start" else (a, target)
        if target < -_BOUND_EPS or not truth.covers(Interval(*new_iv)):
            return list(plan), RepairRecord(
                fault=finding.fault, stage=finding.stage, repair=Repair.ESCALATE,
                before=before, after=before, instrument="engine.edit_plan.align_span",
                note=(f"snapping the {finding.edge} of interval {index} to "
                      f"{target:.3f}s would leave {new_iv} outside the ground "
                      f"truth ({rung}); the boundary is escalated rather than "
                      f"extended onto time that is not backed"),
                changed_plan=False,
            )
        after = new_iv if new_iv[1] - new_iv[0] > DEGENERATE_SECONDS else None
        return _replace(plan, index, [new_iv] if after else [],
                        ctx_duration), RepairRecord(
            fault=finding.fault, stage=finding.stage, repair=Repair.SNAP,
            before=before, after=after, instrument="engine.edit_plan.align_span",
            note=(f"snapped the {finding.edge} of interval {index} from "
                  f"{before} to {after}"),
            changed_plan=after != before,
        )

    # Repair.TRIM: remove the finding's span from the interval. ``IntervalSet``
    # and ``cut_algebra.subtract_intervals`` are documented as mutually
    # consistent ("a cut list and an evidence set never disagree about whether
    # a span is in or out"), and the re-verify catches any disagreement at
    # ``MERGE_EPS`` scale.
    base = IntervalSet.of((a, b))
    if finding.fault == Fault.LOW_CONFIDENCE_ONLY:
        above = ctx.evidence.above_confidence(ctx.confidence_floor).intervals
        result = base.intersect(above)
    else:
        result = base.subtract(IntervalSet.of(finding.span))
    spans = [(iv.start, iv.end) for iv in result]
    replaced = _replace(plan, index, spans, ctx_duration)
    if not spans and len(plan) > 1 and finding.fault != Fault.LOW_CONFIDENCE_ONLY:
        # The finding's span *is* the interval, and other intervals survive, so
        # the trim drops it. That is a repair, not an escalation: for the faults
        # that name content the clip must not ship, the whole interval being the
        # fault is the whole interval being wrong. A duplicate sentence is the
        # case in point — the check has already decided which clip gives it up,
        # from a ranking the editor set, so dropping the lower-ranked clip's copy
        # executes that decision rather than overriding one. (LOW_CONFIDENCE_ONLY
        # is excluded below: confidence is a property of the media, not an
        # editorial judgment, and the memory layer's own rule is that filters
        # report rather than delete.)
        return replaced, RepairRecord(
            fault=finding.fault, stage=finding.stage, repair=Repair.TRIM,
            before=before, after=None, instrument="IntervalSet.subtract",
            note=(f"the finding's span is the whole of interval {index}, so the "
                  f"trim drops it; {len(plan) - 1} interval(s) survive"),
            changed_plan=True,
        )
    if not spans:
        # The trim would delete the interval rather than shorten it, and either
        # it is the only interval or the fault is one the memory layer reports
        # rather than repairs. Removing footage is an editor's decision, not a
        # check's: a span whose only backing is below the confidence floor is
        # *reported*, and a single interval whose whole extent is the fault is
        # the clip disappearing. Either way the fault is escalated so the editor
        # sees it rather than finding the clip gone.
        return list(plan), RepairRecord(
            fault=finding.fault, stage=finding.stage, repair=Repair.ESCALATE,
            before=before, after=before, instrument="IntervalSet.subtract",
            note=(f"trimming would remove interval {index} outright rather than "
                  f"shorten it; deleting footage is an editor's decision, so the "
                  f"interval is kept and the fault is escalated"),
            changed_plan=False,
        )
    if replaced == plan:
        # The finding's span never reached the interval it named — a
        # transcript/plan desync, whose symptom this is. Retrying it is a fixed
        # point, so the no-op is reported as one and the fault escalates
        # (§5, I4).
        return list(plan), RepairRecord(
            fault=finding.fault, stage=finding.stage, repair=repair,
            before=before, after=before, instrument="IntervalSet.subtract",
            note=(f"the finding's span {finding.span.start:.3f}-"
                  f"{finding.span.end:.3f}s does not reach interval {index}; "
                  f"the repair is a no-op"),
            changed_plan=False,
        )
    if not replaced:
        # The trim would leave the plan shipping nothing. An empty plan is not a
        # repair, it is the clip disappearing, and that is an editor's decision
        # — so the interval is kept and the fault is escalated (§5, I4).
        return list(plan), RepairRecord(
            fault=finding.fault, stage=finding.stage, repair=Repair.ESCALATE,
            before=before, after=before, instrument="IntervalSet.subtract",
            note=("trimming the unsupported span would empty the plan; the "
                  "interval is kept and the fault is escalated"),
            changed_plan=False,
        )
    after = spans[0] if len(spans) == 1 else None
    return replaced, RepairRecord(
        fault=finding.fault, stage=finding.stage, repair=Repair.TRIM,
        before=before, after=after, instrument="IntervalSet.subtract",
        note=(f"trimmed {finding.span.start:.3f}-{finding.span.end:.3f}s out of "
              f"interval {index}: {len(spans)} span(s) survive"),
        changed_plan=replaced != plan,
    )


# ── VERIFY AGAIN: the loop ────────────────────────────────────────────────────


def measure(plan: list[tuple[float, float]],
            ctx: VerifyContext) -> tuple[float, float, int]:
    """The lexicographic measure a plan-level repair strictly decreases
    (§5, I1): total kept time, the total distance of every boundary from the
    nearest word edge, and the span count. ``TRIM`` reduces the first and third
    and cannot introduce a mid-word cut — removing time cannot split a word.
    ``SNAP`` drives the second component to zero, and may raise the first by
    extending a clip onto time the truth covers; that gain is bounded by the
    coverage check that gates the snap, so the pair cannot rise together, and
    the third component only falls as overlaps merge.
    """
    words = _words(ctx)
    kept = sum(b - a for a, b in plan)
    edges = [t for a, b in plan for t in (a, b)]
    if words:
        boundary = 0.0
        for t in edges:
            boundary += min(min(abs(t - w.start), abs(t - w.end))
                            for w in words)
    else:
        boundary = 0.0
    return kept, boundary, len(plan)


def run_loop(plan, ctx: VerifyContext, *,
             max_passes: int | None = None) -> LoopResult:
    """VERIFY → DIAGNOSE → REPAIR → VERIFY AGAIN, with a record of every repair.

    The plan-level repairs (``SNAP``, ``TRIM``) happen here; the artifact
    repairs (``REEMIT``, ``REMUX``) are recorded for the caller to perform and
    the loop re-verifies against the facts it was given. A recorded artifact
    repair whose fault recurs is converted to ``UNDECIDABLE`` and escalated —
    that is the guard against an emitter whose output is not a function of its
    input, and it is why no repair may patch emitted text.
    """
    words = _words(ctx)
    cap = max_passes if max_passes is not None else len(words) + 3
    current = _normalise_plan(list(plan), ctx.source_duration)
    repairs: list[RepairRecord] = []
    actions: list[RepairRecord] = []
    escalated: list[Finding] = []
    converted: list[Finding] = []
    reemit_keys: set[tuple] = set()
    passes = 0
    capped = False
    last: VerifyReport = verify(current, ctx)

    while True:
        last = verify(current, ctx)
        passes += 1
        actionable = [f for f in last.findings if f.verdict != Verdict.CLEAN]
        if not actionable:
            break
        if passes > cap:
            capped = True
            break

        # I2 first: a fault a re-derivation was supposed to clear, still there.
        # The artifact facts did not change inside one call, so a recurring
        # reemit fault is the emitter's, and the question becomes undecidable
        # here rather than retried forever.
        still: list[Finding] = []
        reemit_failed = False
        for f in actionable:
            if _key(f) in reemit_keys:
                reemit_keys.discard(_key(f))
                reemit_failed = True
                escalated.append(Finding(
                    fault=Fault.UNDECIDABLE, verdict=Verdict.REVIEW,
                    stage=f.stage, interval_index=f.interval_index, edge=f.edge,
                    span=f.span,
                    detail=(f"re-derivation did not clear this fault: "
                            f"{f.detail}"),
                    instrument=f.instrument,
                ))
                converted.append(escalated[-1])
                repairs.append(RepairRecord(
                    fault=f.fault, stage=f.stage, repair=Repair.ESCALATE,
                    before=None, after=None, instrument=f.instrument,
                    note=("the recorded re-emit did not change the verdict; "
                          "the fault is undecidable and escalated (I2)"),
                    changed_plan=False,
                ))
            else:
                still.append(f)
        if not still or reemit_failed:
            # An emitter whose output is not a function of its input ends the
            # loop, not just this pass: any plan repair made under a divergent
            # artifact would be rebuilt against an export this loop cannot
            # verify, and the editor would be handed a plan justified by a
            # document that disagrees with it. The caller performs the recorded
            # action and re-enters with the artifact facts it measured.
            break

        # One repair per pass. Every finding here was measured against the same
        # plan, and two repairs can pull an interval in opposite directions — a
        # mid-word cut wants the boundary back to the word's start, a mid-sentence
        # cut wants it forward to the utterance's end — so applying both in one
        # pass silently undoes the first with the second and the pass exits having
        # fixed nothing. The first actionable finding is repaired and the whole
        # suite is re-run, which is also what the measure's decrease is argued
        # against (§5, I1: each pass pins at least one boundary).
        f = min(still, key=lambda finding: _repair_key(finding, ctx))
        if f.fault == Fault.UNDECIDABLE:
            escalated.append(f)
            repairs.append(RepairRecord(
                fault=f.fault, stage=f.stage, repair=Repair.ESCALATE,
                before=None, after=None, instrument=f.instrument,
                note=f.detail, changed_plan=False,
            ))
            break
        d = diagnose(f, ctx)
        if Repair.ESCALATE == d.permitted[0]:
            escalated.append(f)
            repairs.append(RepairRecord(
                fault=f.fault, stage=f.stage, repair=Repair.ESCALATE,
                before=None, after=None, instrument=d.rationale,
                note="the permitted set for this fault is escalate-only",
                changed_plan=False,
            ))
            break
        new_plan, record = apply_repair(current, f, ctx)
        repairs.append(record)
        if record.repair in (Repair.REEMIT, Repair.REMUX):
            actions.append(record)
            reemit_keys.add(_key(f))
            continue
        if new_plan == current:
            # A plan-level repair that moved nothing: the finding's span did not
            # reach the interval it named, or the snap target lay outside the
            # source. Retrying would loop forever, so the fault escalates instead
            # (§5, I4) and the record's note says which.
            escalated.append(Finding(
                fault=Fault.UNDECIDABLE, verdict=Verdict.REVIEW,
                stage=f.stage, interval_index=f.interval_index, edge=f.edge,
                span=f.span,
                detail=(f"the repair did not change the plan: {f.detail}"),
                instrument=f.instrument,
            ))
            converted.append(escalated[-1])
            break
        current = new_plan

    superseded = {_key(c) for c in converted}
    findings = tuple(
        f for f in last.findings if _key(f) not in superseded
    ) + tuple(converted)
    verdict = Verdict.FAIL if capped else worst_verdict(findings)
    return LoopResult(
        plan=tuple(current), findings=findings, repairs=tuple(repairs),
        escalated=tuple(escalated), caller_actions=tuple(actions),
        passes=passes, capped=capped, verdict=verdict,
    )
