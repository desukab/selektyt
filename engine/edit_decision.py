"""The canonical edit decision graph.

``engine/timeline.py`` models *geometry*: where a span of source media sits on
the edited timeline. It deliberately carries nothing else — its own docstring
says a clip is a pure placement, and that restraint is what makes it safe to
relink in an NLE.

The canonical product needs a second thing, and it is not geometry. Every clip
in an edit is the answer to a question — *why is this here, and what would
change that?* An editor who disagrees needs to know what the decision rested on
before they can override it, and a derivative (a caption, a reframe, a social
clip) that is derived from a decision it cannot see is a derivative that will
silently disagree with the edit it serves.

This module is that layer. It does not replace or subclass ``Timeline``; it
sits above it, annotating each placement with the decision that produced it and
recording what the edit *removed* as carefully as what it kept. The graph is
the source of truth; the timeline is its geometry.

Three rules keep it honest, and each is a defect the reconnaissance found in an
existing layer:

* **Unmeasured is not confident.** ``engine.memory.evidence.Evidence`` defaults
  ``confidence`` to ``1.0`` with no sentinel for a missing measurement, which
  is how an unscored span can read as maximally trusted. ``Confidence`` here
  carries ``None`` for unmeasured and a mandatory ``basis`` sentence for
  anything it does report. The precedent is ``engine.story_view._confidence_of``,
  which already returns ``(float | None, basis)`` for exactly this reason.
* **No second vocabulary.** The story-role names are ``DramaticBeat.kind``'s
  own ladder, and a removed range's ``kind`` is ``jumpcut.SegmentType``'s own.
  An earlier SeleKyT change duplicated a token definition and silently dropped
  the stopword filter; a second taxonomy of the same concepts would be that
  defect class in its worst form.
* **Every decision is reversible in principle.** A decision carries the id of
  the one it superseded, and the graph keeps the superseded ones rather than
  overwriting them. An edit history that only records the current state cannot
  answer "what did the editor override?", which is the first question a
  review workflow asks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------- vocabulary

# The story-role ladder is ``DramaticBeat.kind``'s own (engine/threads.py), not
# a second taxonomy of the same concept. See the module docstring.
STORY_ROLES: tuple[str, ...] = (
    "setup", "tension", "turn", "revelation", "resolution", "continuation",
)

# Editorial actions, in the order an editor would challenge them. ``keep`` is
# the neutral default — a clip the policy placed and nothing further acted on.
class EditorialAction(str, Enum):
    KEEP = "keep"            # placed on the sequence as selected
    PROMOTE = "promote"      # moved earlier than its source order
    DEMOTE = "demote"        # moved later than its source order
    TRIM = "trim"            # bounds narrowed (silence, filler, drift)
    SPLIT = "split"          # one source span became two placements
    REORDER = "reorder"      # sequenced against a principle, not source order
    CUT = "cut"              # removed from the edit
    RESTORE = "restore"      # returned after a cut, by an editor or a repair


# ---------------------------------------------------------------- confidence

@dataclass(frozen=True)
class Confidence:
    """A measured trust value, or the honest statement that none was taken.

    ``value`` is ``None`` when nothing was measured, and that state is not
    equivalent to any number — a derivative that reads an unmeasured
    confidence as ``1.0`` will treat an unexamined clip as a maximally trusted
    one. ``basis`` is mandatory for any measured value, so a figure in a
    report always names the instrument that produced it.
    """

    value: float | None
    basis: str = "unmeasured"

    def __post_init__(self) -> None:
        if self.value is None:
            return
        if self.value != self.value:  # NaN: min(1.0, nan) == 1.0 reads a NaN
            raise ValueError(            # as maximum trust; refuse it outright
                "confidence is NaN — use None for an unmeasured value"
            )
        v = max(0.0, min(1.0, self.value))
        if v != self.value:
            object.__setattr__(self, "value", v)
        if self.basis in ("", "unmeasured"):
            raise ValueError(
                "a measured confidence needs a basis naming its instrument"
            )

    @property
    def unmeasured(self) -> bool:
        return self.value is None

    @classmethod
    def unmeasured_of(cls, basis: str = "unmeasured") -> "Confidence":
        return cls(None, basis=basis)

    def as_dict(self) -> dict:
        return {
            "value": None if self.value is None else round(self.value, 4),
            "basis": self.basis,
        }


# ------------------------------------------------------------------ evidence

@dataclass(frozen=True)
class EvidenceRef:
    """A pointer at the evidence behind a decision.

    ``source`` is the instrument that produced it — a module and method, in the
    ``engine.memory.evidence.Source`` convention — and ``claim`` is the
    editor-readable assertion, copied here so a consumer can say what the
    evidence asserted without loading ``memory.json``. ``id`` is the evidence
    store's own id when the decision was built from a stored item, else "".
    """

    source: str
    claim: str
    id: str = ""
    confidence: Confidence | None = None

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "claim": self.claim,
            "id": self.id,
            "confidence": self.confidence.as_dict() if self.confidence else None,
        }


# ------------------------------------------------------------- removed span

@dataclass(frozen=True)
class RemovedRange:
    """A span the edit dropped, with the reason it was dropped.

    ``kind`` is ``jumpcut.SegmentType``'s vocabulary (``remove_silence``,
    ``remove_filler``, ``remove_hallucination``) for intra-clip removals, plus
    ``not_selected`` and ``gate_rejected`` for spans the assembly never
    placed. Times are source seconds, so an editor can jump to what was cut and
    verify the judgement.
    """

    start: float
    end: float
    kind: str
    reason: str
    decision_id: str = ""

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(
                f"a removed range must be non-empty, got {self.start}..{self.end}"
            )
        if self.kind not in _REMOVAL_KINDS:
            raise ValueError(
                f"unknown removal kind {self.kind!r}; "
                f"expected one of {sorted(_REMOVAL_KINDS)}"
            )

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "kind": self.kind,
            "reason": self.reason,
            "decision_id": self.decision_id,
        }


_REMOVAL_KINDS: frozenset[str] = frozenset({
    "remove_silence", "remove_filler", "remove_hallucination",
    "not_selected", "gate_rejected",
})


# --------------------------------------------------------------- a decision

@dataclass(frozen=True)
class EditDecision:
    """Why one clip is where it is.

    The anchor is the clip's identity on the timeline — its ``track`` plus its
    ``timeline_in``, which the model guarantees is unique among placements on
    one lane — rather than an index, because an assembly that reorders clips
    must not orphan the decisions attached to them.

    ``replaces`` holds the id of the decision this one superseded. The graph
    keeps superseded decisions, so an override is an addition to the history
    rather than a silent overwrite; ``prior`` is the convenience accessor that
    walks one step back.
    """

    id: str
    anchor_track: str
    anchor_timeline_in: float
    action: EditorialAction = EditorialAction.KEEP
    role: str = ""
    reason: str = ""
    confidence: Confidence = field(default_factory=Confidence.unmeasured_of)
    evidence: tuple[EvidenceRef, ...] = ()
    provenance: str = ""
    replaces: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("a decision needs a stable id")
        if self.role and self.role not in STORY_ROLES:
            raise ValueError(
                f"unknown story role {self.role!r}; expected one of "
                f"{STORY_ROLES} (the DramaticBeat ladder) or empty"
            )

    @property
    def anchor(self) -> tuple[str, float]:
        return (self.anchor_track, self.anchor_timeline_in)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "anchor": {"track": self.anchor_track,
                       "timeline_in": round(self.anchor_timeline_in, 3)},
            "action": self.action.value,
            "role": self.role,
            "reason": self.reason,
            "confidence": self.confidence.as_dict(),
            "evidence": [e.as_dict() for e in self.evidence],
            "provenance": self.provenance,
            "replaces": self.replaces,
        }


# -------------------------------------------------------------------- markers

@dataclass(frozen=True)
class Marker:
    """A point of editorial interest that is not a cut: a chapter, a beat, a
    place the editor should look. ``time`` is a *timeline* second."""

    time: float
    label: str
    kind: str = "note"

    def as_dict(self) -> dict:
        return {"time": round(self.time, 3), "label": self.label, "kind": self.kind}


@dataclass(frozen=True)
class EditNote:
    """Free-form editorial commentary, kept separate from ``reason``: a
    decision's reason is the machine's account, a note is a person's."""

    text: str
    at: float = 0.0
    author: str = "selekyt"

    def as_dict(self) -> dict:
        return {
            "text": self.text, "at": round(self.at, 3), "author": self.author,
        }


# --------------------------------------------------------------------- the EDG

@dataclass
class EditDecisionGraph:
    """The canonical edit: a timeline plus the decisions that made it.

    The graph does not own a ``Timeline`` by composition; it holds the same
    clip list and indexes the decisions by anchor, so a consumer that already
    has a ``Timeline`` can derive the geometry it already knows how to read and
    ask the graph only for the *why*. Construction is cheap and validates
    nothing about timing — that remains ``Timeline.validate``'s job, and the
    two are meant to be checked together.

    Decisions whose anchor no longer matches a clip are *dangling*, reported
    rather than dropped: an assembly that moves a clip without moving its
    decision has lost the reasoning, and that is a bug to surface, not
    evidence to delete.
    """

    decisions: tuple[EditDecision, ...] = ()
    removed: tuple[RemovedRange, ...] = ()
    markers: tuple[Marker, ...] = ()
    notes: tuple[EditNote, ...] = ()
    title: str = "SeleKyT Edit"
    fps: float = 30.0
    # The clip anchors the decisions may attach to, as (track, timeline_in)
    # pairs. Set by ``from_timeline``; empty when the graph is built by hand,
    # in which case every decision is dangling and the report says so.
    anchors: tuple[tuple[str, float], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        ids = {d.id for d in self.decisions}
        for d in self.decisions:
            if d.replaces is not None and d.replaces not in ids:
                raise ValueError(
                    f"decision {d.id} replaces {d.replaces!r}, which is not in "
                    f"this graph — a history that names a decision it does not "
                    f"hold cannot be walked backwards"
                )
        # A decision is superseded by at most one successor. A second
        # successor would fork the history into two edits at once.
        successors: dict[str, str] = {}
        for d in self.decisions:
            if d.replaces is None:
                continue
            if d.replaces in successors:
                raise ValueError(
                    f"decision {d.replaces} is superseded twice, by "
                    f"{successors[d.replaces]!r} and by {d.id!r} — a decision "
                    f"is the predecessor of one edit, not two"
                )
            successors[d.replaces] = d.id
        # ``replaces`` must target something in the graph, so a cycle has no
        # live head and the walk below would not terminate; bound it and name
        # it, since a cyclic history cannot answer "what did I override?".
        by_id = {d.id: d for d in self.decisions}
        for d in self.decisions:
            seen: set[str] = {d.id}
            cur: EditDecision | None = d
            while cur is not None and cur.replaces is not None:
                if cur.replaces in seen:
                    raise ValueError(
                        f"decision {d.id}: the history through "
                        f"{cur.replaces!r} is a cycle, so it has no origin"
                    )
                seen.add(cur.replaces)
                cur = by_id.get(cur.replaces)
        # One *live* decision per anchor — not one decision per anchor. A
        # decision that moves a clip supersedes the one at the old position and
        # anchors on the new one; that cross-anchor replace is how relocation
        # is recorded, and forbidding it would make an edit unmovable.
        by_anchor: dict[tuple[str, float], list[EditDecision]] = {}
        for d in self.decisions:
            by_anchor.setdefault(d.anchor, []).append(d)
        for anchor, group in by_anchor.items():
            live = [d for d in group if d.id not in successors]
            if len(live) > 1:
                raise ValueError(
                    f"anchor {anchor} holds {len(live)} live decisions "
                    f"({', '.join(d.id for d in live)}); an anchor is one "
                    f"current judgement, and the rest must be under it in "
                    f"history"
                )

    # ------------------------------------------------------------------ shape

    @property
    def decision_by_anchor(self) -> dict[tuple[str, float], EditDecision]:
        """The live decision for each anchor. Superseded ones are in
        ``history``, not here — an anchor holds one current judgement."""
        return {d.anchor: d for d in self.decisions
                if not self._is_superseded(d.id)}

    def _is_superseded(self, decision_id: str) -> bool:
        return any(d.replaces == decision_id for d in self.decisions)

    def live(self) -> tuple[EditDecision, ...]:
        """Decisions nothing has superseded: the current state of the edit."""
        return tuple(d for d in self.decisions
                     if not self._is_superseded(d.id))

    def dangling(self) -> tuple[EditDecision, ...]:
        """Live decisions whose anchor is not a clip on the timeline.

        Empty is the healthy state. Non-empty means an assembly moved a clip
        without moving the reasoning attached to it. Only live decisions are
        checked — a superseded decision whose clip moved is already recorded
        in ``history``, and reporting it twice would bury the real signal.
        """
        if not self.anchors:
            return self.live()
        have = set(self.anchors)
        return tuple(d for d in self.live() if d.anchor not in have)

    def history(self, decision_id: str) -> tuple[EditDecision, ...]:
        """The chain a decision supersedes, oldest first, including itself.

        A cycle is impossible by construction — ``replaces`` must name an id
        present in the graph, and a id cannot precede itself — but the walk is
        bounded anyway so a malformed graph built by hand cannot hang here.
        """
        by_id = {d.id: d for d in self.decisions}
        chain: list[EditDecision] = []
        cur: EditDecision | None = by_id.get(decision_id)
        while cur is not None and len(chain) <= len(self.decisions):
            chain.append(cur)
            cur = by_id.get(cur.replaces) if cur.replaces else None
        return tuple(reversed(chain))

    # ------------------------------------------------------------- conversion

    @classmethod
    def from_timeline(cls, timeline, **kw) -> "EditDecisionGraph":
        """Build a graph whose anchors are a ``Timeline``'s placements.

        Decisions are empty; this is the entry point for an assembly that will
        fill them in, and it sets ``anchors`` so ``dangling`` means what it
        says.
        """
        anchors = tuple(
            (c.track, round(c.timeline_in, 6)) for c in timeline.clips
        )
        return cls(anchors=anchors, title=timeline.title, fps=timeline.fps, **kw)

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "fps": self.fps,
            "decisions": [d.as_dict() for d in self.decisions],
            "removed": [r.as_dict() for r in self.removed],
            "markers": [m.as_dict() for m in self.markers],
            "notes": [n.as_dict() for n in self.notes],
            "anchors": [
                {"track": t, "timeline_in": round(s, 3)}
                for t, s in self.anchors
            ],
            "dangling": len(self.dangling()),
        }
