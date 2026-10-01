"""The assembly stage — one ordered edit from a selection of clips.

``engine.selection.select`` chooses a *set*. Nothing orders it: the candidates
leave in greedy pick order and the render loop numbers the deliverables from
that order, so ``01.mp4`` is "the clip the policy scored highest" and not "the
opening" (recon G1). This module turns the set into a *program*: one ordered,
single-track ``Timeline`` on one source, plus the ``EditDecisionGraph`` that
records why each placement is where it is and what the edit dropped (G3).

It invents no signal. The orderings it can express are both read from records
the corpus already computed or the candidates already carry:

* ``order="source"`` — chronological, the neutral baseline and the honest
  default when the corpus is absent, thin, or has no thread that binds two
  selections together.
* ``order="arc"`` — thread-contiguous. Selections bound to one story thread
  become adjacent and keep their internal chronology, because a thread split
  across a reel makes each of its clips unresolvable out of context; the
  thread groups are then ordered by the strongest opening among them, so the
  reel leads with its best opener. When no thread binds anything this
  degenerates to source order, and it does so visibly rather than silently.

The removed spans within a clip are the pauses ``engine.edit_plan.
retention_intervals`` shortened — that function's own contract is to shorten
rather than delete, so the kind is ``remove_silence`` and the reason names the
instrument and its parameters rather than claiming a silence was detected by a
measurement this module never made. Candidates the selection deferred are
recorded as ``not_selected`` when the caller passes them, which is the gap G3
named: a span the edit dropped, with the reason it was dropped.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from engine.edit_decision import (
    Confidence,
    EditDecision,
    EditDecisionGraph,
    EditNote,
    EditorialAction,
    EvidenceRef,
    Marker,
    RemovedRange,
)
from engine.edit_plan import retention_intervals
from engine.selection import Scored, _covers
from engine.timeline import Clip, SourceMedia, Timeline

# ── configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AssemblyConfig:
    """The editorial choices the assembly is bound to, named so they can be
    audited rather than inferred from the output."""

    order: str = "arc"             # "source" | "arc"
    # A thread binds a group when at least this many selections come from it.
    # Two is the meaning of a group: one clip from a thread is a clip with a
    # subject, two clips from a thread are a story told in two parts, and
    # those are the ones an editor keeps adjacent.
    min_group_size: int = 2


# ── the assembly's own record of one placement ───────────────────────────────


@dataclass(frozen=True)
class Placement:
    """A candidate as the assembly sees it: sequenced, and carrying the thread
    it belongs to and the role the corpus gave it.

    ``thread_id`` is the story the clip comes from — a property of the corpus,
    read whether or not the reel groups the clip with a sibling, so an
    ungrouped clip still carries its story's confidence. ``grouped`` is the
    reel's decision: whether another selection came from the same thread, and
    therefore whether this clip opens a chapter the editor can jump to.
    ``role`` is empty when nothing was measured — unmeasured is not the same as
    ``continuation``."""

    candidate: object
    sequence: int              # 0-based position in the assembled order
    thread_id: int | None      # the corpus thread this clip belongs to, or None
    thread_title: str = ""
    role: str = ""             # DramaticBeat.kind ladder, or "" if unmeasured
    grouped: bool = False      # another selection shares this thread


# ── sequencing ───────────────────────────────────────────────────────────────


def _covered_units(corpus, c) -> list[int]:
    """The corpus units this candidate's span carries.

    Reuses ``engine.selection._covers`` rather than reimplementing the overlap
    test, so "a clip carries a unit" means the same thing in the policy that
    chose the clip and in the assembly that sequences it. Empty — not an
    error — when there is no corpus, which is how the assembly degrades to
    source order and unroled, unmeasured decisions on a transcript too thin to
    analyse.
    """
    if corpus is None:
        return []
    return [i for i in range(len(corpus.units)) if _covers(corpus, i, c)]


def _dominant_role(corpus, unit_indices: list[int]) -> tuple[str, float | None]:
    """The strongest beat kind among these units, with the thread confidence
    that measured it.

    ``CorpusUnit`` carries ``beat_id`` directly, so the beat is read rather
    than re-derived from the text. Intensity is the corpus's own saturation
    scale and is never normalised here. Returns ``("", None)`` when the corpus
    has no beats for these units — a clip the corpus did not characterise is
    left unroled rather than assigned ``continuation`` by default.
    """
    if corpus is None or not unit_indices:
        return "", None
    by_kind: dict[str, float] = {}
    confidence: dict[str, float] = {}
    for i in unit_indices:
        if not 0 <= i < len(corpus.units):
            continue
        u = corpus.units[i]
        if u.beat_id is None or not 0 <= u.beat_id < len(corpus.beats):
            continue
        beat = corpus.beats[u.beat_id]
        by_kind[beat.kind] = max(by_kind.get(beat.kind, 0.0), beat.intensity)
        # The thread's own confidence in the story this beat serves, so a role
        # from a weakly-evidenced thread is reported as weakly evidenced.
        if u.thread_id is not None and 0 <= u.thread_id < len(corpus.threads):
            confidence[beat.kind] = corpus.threads[u.thread_id].confidence
    if not by_kind:
        return "", None
    kind = max(by_kind, key=lambda k: (by_kind[k], k))
    return kind, confidence.get(kind)


def _thread_of(corpus, c) -> int | None:
    """The thread this candidate belongs to, or None.

    Chosen by the thread covering the most of the candidate's corpus units, at
    any count: the binding question is not "how much of the thread does this
    clip carry" but "does this clip share a thread with another selection",
    which is decided in ``_sequence`` once every candidate has been assigned.
    A candidate covering one unit of a three-unit thread belongs to that
    thread; whether that makes it part of a group depends on whether another
    selection does too.
    """
    if corpus is None or not corpus.threads:
        return None
    units = _covered_units(corpus, c)
    if not units:
        return None
    counts: dict[int, int] = {}
    for i in units:
        if not 0 <= i < len(corpus.units):
            continue
        u = corpus.units[i]
        if u.thread_id is not None:
            counts[u.thread_id] = counts.get(u.thread_id, 0) + 1
    # Fall back to the thread's own unit membership: a candidate may cover a
    # unit the thread claims without that unit carrying a thread_id of its own.
    for t in corpus.threads:
        counts[t.thread_id] = max(counts.get(t.thread_id, 0),
                                  len(set(t.unit_indices) & set(units)))
    if not counts:
        return None
    return max(counts, key=lambda t: (counts[t], -t))


def _sequence(cands: list, corpus, config: AssemblyConfig) -> list[Placement]:
    """Order the selection into a program.

    ``order="source"`` is chronological. ``order="arc"`` groups the candidates
    a thread *shares* — two or more selections from one story thread become
    adjacent and keep their internal chronology, because a thread split across
    a reel makes each of its clips unresolvable out of context — and orders the
    groups by the strongest opening among them, so the reel leads with the clip
    that opens best. A thread only one selection comes from binds nothing, so
    that selection stays a singleton; when no thread is shared, arc order is
    source order, and it degenerates visibly rather than silently.

    Group ties fall back to source order, which is what makes the ordering
    stable across runs.
    """
    tid_by_id = {id(c): _thread_of(corpus, c)
                 for c in cands} if corpus is not None else {}
    # A thread binds a group only when more than one selection comes from it.
    # This is the rule that makes the grouping editorial rather than
    # statistical: one clip's thread membership is a label on that clip, and
    # only a second clip from the same thread makes a pair the reel should not
    # separate.
    counts: dict[int | None, int] = {}
    for tid in tid_by_id.values():
        counts[tid] = counts.get(tid, 0) + 1
    shared = {tid for tid in counts
              if tid is not None and counts[tid] >= config.min_group_size}

    if config.order == "arc" and corpus is not None and corpus.threads:
        groups: dict = {}
        for pos, c in enumerate(cands):
            tid = tid_by_id[id(c)]
            if tid in shared:
                key: object = tid
            else:
                # An ungrouped candidate is its own group, keyed uniquely, so
                # the reel orders singletons by their opening strength too —
                # a solo clip that opens best should still lead the reel, not
                # sit behind another solo clip merely because it starts later
                # in the source.
                key = ("solo", pos)
            groups.setdefault(key, []).append((pos, c))
        # Within a group, source order. Group ties fall back to source order,
        # which is what makes the ordering stable across runs.
        ordered: list[tuple[int, object]] = []
        for k in sorted(
            groups,
            key=lambda k: (
                -max(_opening(c) for _p, c in groups[k]),
                min(c.start for _p, c in groups[k]),
            ),
        ):
            ordered.extend(sorted(groups[k], key=lambda pc: pc[1].start))
    else:
        ordered = sorted(enumerate(cands), key=lambda pc: pc[1].start)

    placements: list[Placement] = []
    for sequence, (_pos, c) in enumerate(ordered):
        tid = tid_by_id.get(id(c))
        title = ""
        if tid is not None and 0 <= tid < len(corpus.threads):
            title = corpus.threads[tid].title
        role, _conf = _dominant_role(corpus, _covered_units(corpus, c))
        placements.append(Placement(
            candidate=c, sequence=sequence,
            thread_id=tid, thread_title=title, role=role,
            grouped=tid in shared,
        ))
    return placements


def _opening(c) -> float:
    """The clip's measured opening strength, or the neutral reading.

    ``Candidate.opening_score`` defaults to 1.0 when the scorer recorded
    nothing; that is unmeasured rather than strong, so it does not win an
    ordering it did not earn, and ties fall through to source order.
    """
    return max(0.0, min(1.0, float(getattr(c, "opening_score", 1.0))))


# ── identity and evidence ────────────────────────────────────────────────────


def _decision_id(c, title: str) -> str:
    """A content-addressed decision id.

    Position-independent on purpose: an editor who reorders the reel must not
    orphan a decision, and a decision whose id followed its slot could not be
    tracked across the reorder. ``hash()`` is salted per process for strings,
    so this hashes through ``sha1`` to stay stable across runs — the id is
    what a later override names in ``replaces``.
    """
    key = f"{title}|{round(float(c.start), 3)}|{round(float(c.end), 3)}|{c.hook}"
    return "ed-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _evidence_for(c, scored: dict) -> tuple[EvidenceRef, ...]:
    """The policy's own terms as evidence, in the policy's own words.

    ``engine.selection.PolicyNote`` already records the term, the delta and the
    editor-readable reason a clip was moved relative to the set. That is the
    reasoning behind the placement, copied here so a consumer can state it
    without importing the policy — the same copy ``EvidenceRef.claim`` exists
    to make. The deltas are adjustments, not confidences, so they are carried
    as prose in the claim and never dressed as a measured trust value.
    """
    notes = scored.get(id(c))
    if not notes:
        return ()
    out: list[EvidenceRef] = []
    for note in notes:
        out.append(EvidenceRef(
            source=f"engine.selection.score_in_context#{note.term}",
            claim=note.reason,
            confidence=None,
        ))
    return tuple(out)


# ── removed ranges ───────────────────────────────────────────────────────────


def _pause_removals(c, decision_id: str) -> list[RemovedRange]:
    """The pauses ``retention_intervals`` shortened, as removed ranges.

    The gaps between consecutive retained intervals are the spans the edit
    dropped inside the clip. They are ``remove_silence`` in kind because that
    is what a shortened pause is, and the reason names the instrument and its
    parameters rather than implying a silence measurement was made here.
    """
    intervals = retention_intervals(c)
    if len(intervals) < 2:
        return []
    out: list[RemovedRange] = []
    for (_s, prev_end), (start, _e) in zip(intervals, intervals[1:]):
        if start > prev_end + 1e-6:
            out.append(RemovedRange(
                start=prev_end, end=start, kind="remove_silence",
                reason=(f"pause shortened by engine.edit_plan.retention_intervals "
                        f"(min_pause=0.70, keeps a 0.18s breath)"),
                decision_id=decision_id,
            ))
    return out


# ── the assembly ─────────────────────────────────────────────────────────────


@dataclass
class Assembly:
    """The result of assembling a selection: one timeline and the decisions
    behind it. ``skipped`` names candidates with no renderable span — the
    assembly drops them and says so rather than emitting a clip with no
    source."""

    timeline: Timeline
    graph: EditDecisionGraph
    placements: tuple[Placement, ...] = ()
    skipped: tuple[str, ...] = ()
    report: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.timeline.duration

    def as_dict(self) -> dict:
        g = self.graph.as_dict()
        g["placements"] = [
            {
                "sequence": p.sequence,
                "thread_id": p.thread_id,
                "thread_title": p.thread_title,
                "grouped": p.grouped,
                "role": p.role,
                "decision": p.candidate.hook or "",
                "start": round(float(p.candidate.start), 3),
                "end": round(float(p.candidate.end), 3),
            }
            for p in self.placements
        ]
        g["skipped"] = list(self.skipped)
        return g


def assemble(selected: list,
             *,
             media: Path | str | None = None,
             fps: float = 30.0,
             source_duration: float | None = None,
             title: str = "SeleKyT Edit",
             corpus=None,
             scored: list[Scored] | None = None,
             deferred: list | None = None,
             config: AssemblyConfig | None = None,
             ) -> Assembly:
    """Turn a selection into one ordered edit.

    ``selected`` is the candidate list, in whatever order the caller holds it.
    ``scored`` is the optional list of ``Scored`` records the selection policy
    returned, whose notes become the evidence on each decision. ``deferred`` is
    the optional list of candidates that passed the gate but lost to the budget
    or a policy term; passing them records them as ``not_selected`` removed
    ranges rather than as clips the edit silently never had.
    """
    config = config or AssemblyConfig()
    scored_by_id: dict[int, tuple] = {}
    if scored:
        for s in scored:
            scored_by_id[id(s.candidate)] = s.notes

    placements = _sequence(selected, corpus, config)
    source = SourceMedia(path=str(media) if media is not None else "",
                         fps=fps, duration=source_duration,
                         start_timecode=None)

    clips: list[Clip] = []
    decisions: list[EditDecision] = []
    removed: list[RemovedRange] = []
    markers: list[Marker] = []
    notes: list[EditNote] = []
    skipped: list[str] = []

    # Source order is the baseline against which PROMOTE/DEMOTE are measured,
    # so it is computed once from the same candidates rather than assumed.
    source_rank = {id(c): i for i, c in enumerate(sorted(selected, key=lambda c: c.start))}

    cursor = 0.0
    seen_thread_markers: set[int | None] = set()
    for p in placements:
        c = p.candidate
        intervals = retention_intervals(c)
        if not intervals:
            skipped.append(c.hook or c.text[:60])
            continue

        did = _decision_id(c, title)
        # The anchor is the timeline position this candidate's first clip lands
        # on, captured before the cursor moves past it. Decisions attach to the
        # candidate's opening placement, and every retained range of the same
        # candidate is covered by that one decision.
        anchor = round(cursor, 6)
        # The clips of one candidate share its decision: they are the retained
        # ranges of one editorial judgement, and the graph records the
        # judgement once rather than per fragment.
        for start, end in intervals:
            dur = end - start
            clips.append(Clip(source=source, source_in=start, source_out=end,
                              timeline_in=cursor, timeline_out=cursor + dur,
                              track="V1",
                              reason=c.reason or c.story_type))
            cursor += dur

        action = _action_for(p.sequence, source_rank[id(c)])
        _role, role_confidence = _dominant_role(corpus, _covered_units(corpus, c))
        confidence = (
            Confidence(role_confidence,
                       basis=f"corpus thread {p.thread_title!r} confidence")
            if role_confidence is not None and p.thread_id is not None
            else Confidence.unmeasured_of("no corpus thread characterised this clip")
        )
        decisions.append(EditDecision(
            id=did,
            anchor_track="V1",
            anchor_timeline_in=anchor,
            action=action,
            role=p.role,
            reason=_reason_for(p, action, c),
            confidence=confidence,
            evidence=_evidence_for(c, scored_by_id),
            provenance=("engine.selection.select → engine.assembly.assemble "
                        f"(order={config.order})"),
        ))
        removed.extend(_pause_removals(c, did))

        # A chapter mark only where there is a chapter: a thread two or more
        # selections share. A clip alone in its thread has a story, but not a
        # chapter the editor can jump to, so it takes no marker.
        if p.grouped and p.thread_id not in seen_thread_markers:
            seen_thread_markers.add(p.thread_id)
            markers.append(Marker(
                time=anchor,
                label=f"thread: {p.thread_title}" if p.thread_title
                      else f"thread {p.thread_id}",
                kind="chapter",
            ))

    for d in deferred or []:
        removed.append(RemovedRange(
            start=float(d.start), end=float(d.end), kind="not_selected",
            reason=(d.reason or
                    "passed the editorial gate but lost to the budget "
                    "or a selection-policy term"),
            decision_id="",
        ))

    if not clips:
        raise RuntimeError(
            "none of the selected candidates have renderable intervals, so "
            "the edit has no timeline"
        )

    timeline = Timeline(clips=clips, title=title, fps=fps)
    graph = _bind_anchors(
        EditDecisionGraph(
            decisions=tuple(decisions),
            removed=tuple(removed),
            markers=tuple(markers),
            notes=tuple(notes),
            title=title,
            fps=fps,
        ),
        timeline,
    )

    # A candidate with no renderable span has no placement in the edit; the
    # report names it so an editor can see a selection that produced nothing.
    kept = tuple(p for p in placements
                 if (p.candidate.hook or p.candidate.text[:60]) not in set(skipped))
    return Assembly(
        timeline=timeline, graph=graph, placements=kept,
        skipped=tuple(skipped),
        report={
            "order": config.order,
            "clips": len(clips),
            "decisions": len(decisions),
            "removed": len(removed),
            "markers": len(markers),
            "dangling": len(graph.dangling()),
        },
    )


def program_order_of(project: Path) -> list[tuple[float, float]] | None:
    """The source spans of the program's clips, in the order the reel plays them.

    Reads ``edit.json``, the canonical edit, so a tool that numbers artifacts by
    position (captions, timelines, the audit) derives that numbering from the
    edit rather than from the order ``selections.json`` happens to list clips in.
    Those two orders differ whenever the assembly reorders the reel — which is
    what an assembly is for — and a tool that zipped the policy's pick order
    against the program's numbering would then audit or rewrite the wrong clip.

    Returns ``None`` when the project has no canonical edit, which is the honest
    signal to fall back to the selection order rather than an empty list that
    would read as "the program holds nothing".
    """
    edit_path = Path(project) / "edit.json"
    if not edit_path.is_file():
        return None
    try:
        edit = json.loads(edit_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    placements = edit.get("placements")
    if not placements:
        return None
    ordered = sorted(placements, key=lambda p: p.get("sequence", 0))
    return [(float(p["start"]), float(p["end"])) for p in ordered]


def _action_for(sequence: int, source_rank: int) -> EditorialAction:
    """How the assembled position differs from source order.
    A clip placed earlier than its source position was promoted, one placed
    later demoted; equal is the neutral ``keep``. This is derived from the two
    orders rather than asserted, so the action is a fact about the assembly.
    """
    if sequence < source_rank:
        return EditorialAction.PROMOTE
    if sequence > source_rank:
        return EditorialAction.DEMOTE
    return EditorialAction.KEEP


def _reason_for(p: Placement, action: EditorialAction, c) -> str:
    """An editor-readable account of this placement, in the corpus's terms."""
    parts = []
    if action is EditorialAction.PROMOTE:
        parts.append("moved ahead of its source order")
    elif action is EditorialAction.DEMOTE:
        parts.append("held back from its source order")
    if p.grouped:
        parts.append(
            f"kept with thread {p.thread_title!r}" if p.thread_title
            else f"kept with thread {p.thread_id}")
    if p.role:
        parts.append(f"role {p.role}")
    base = "; ".join(parts)
    return (base + (f" — {c.reason}" if c.reason else "")) or "placed in source order"


def _bind_anchors(graph: EditDecisionGraph, timeline: Timeline) -> EditDecisionGraph:
    """Rebuild the graph with the timeline's real anchors, so ``dangling``
    means what it says: an anchor with no clip is a moved decision."""
    anchors = tuple((c.track, round(c.timeline_in, 6)) for c in timeline.clips)
    return EditDecisionGraph(
        decisions=graph.decisions, removed=graph.removed,
        markers=graph.markers, notes=graph.notes,
        title=graph.title, fps=graph.fps, anchors=anchors,
    )
