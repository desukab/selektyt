"""Walk a story thread into a sequence of clips.

This is the only place the story graph and an edit meet. ``EditorialCorpus``
holds the graph — threads, links, beats — and the pipeline's selection layer
holds ``Candidate``\\ s; nothing between the two knew about each other, which is
why the corpus layer was a finished analysis that no editor could act on (see
the note at ``selekyt/pipeline.py`` that still says so). ``story_path`` takes a
story and a duration budget and returns ``Candidate``\\ s on the existing type,
which then flow through ``retention_intervals``, ``nle_export`` and
``render_clip`` unchanged. The graph is never replaced and never rewritten by
this call: it is read, and a presentation of it is produced.

What the function is *not*:

- It is not a reranking of ``engine.editorial.rank``. ``rank`` selects the
  strongest independent moments across a whole source and applies diversity
  rules so a single-topic video does not yield eight clips about the same
  thing. ``story_path`` selects *inside one story*, by narrative role, so that
  what ships is a story rather than a set of good clips. A good clip is not
  automatically a good story node.
- It is not a synthesizer of moments. Every unit it considers already exists
  on the corpus spine; the only judgements it makes are which ones this budget
  can hold and which ones can stand alone.
- It does not invent confidence. The word probabilities come from the
  transcript the caller passes in, exactly as they do for every other
  candidate; a refusal reason is the guard's measured vocabulary, not a
  generated sentence.

The returned ``StoryPath`` is a ``Sequence`` of the candidates it kept, so it
can be handed to anything expecting a list of candidates, and it also carries
the moments it had to drop and why — the guard's verdict is reported, never
swallowed, because a moment that cannot stand alone is an editorial fact the
person cutting the story needs to see (see ``DroppedMoment``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from engine.context_guard import validate
from engine.corpus import CorpusUnit, EditorialCorpus
from engine.editorial import score_candidate
from engine.threads import StoryThread
from models.core import Candidate, Segment, Word

# Selection priority by narrative role. A story under a tight budget keeps its
# two ends — the setup that stakes it and the resolution that lands it — and
# sacrifices its complications first, which is why a 60-second cut of a story
# is its opening and its payoff while a 3-minute cut carries the development
# and the conflict between them. The order is not "most exciting first": a
# conflict with its resolution cut is a worse story than a resolution whose
# conflict is only implied.
_ROLE_RANK = {
    "setup": 0,
    "resolution": 1,
    "revelation": 2,
    "tension": 3,
    "turn": 4,
    "continuation": 5,
}
_NO_BEAT = 5  # a unit with no dramatic function in this story is connective


@dataclass(frozen=True)
class DroppedMoment:
    """A moment the story wanted but the editorial gate refused.

    ``reason`` is the guard's own wording — "opens or closes inside a sentence
    (completeness 0.62)", "starts mid-conversation (so) — depends on prior
    context" — never a sentence this module composed. Reporting it rather than
    silently omitting the moment is what keeps a path honest: an editor who
    sees a story missing its conflict can see why.
    """

    unit_index: int
    role: str
    start: float
    end: float
    text: str
    reason: str


@dataclass(frozen=True)
class StoryPath(Sequence):
    """One presentation of one story at one budget.

    A ``Sequence`` of the ``Candidate``\\ s it kept, so ``len(path)``,
    ``path[0]``, ``for c in path`` and ``list(path)`` all behave as they would
    for the list the design specified, and the extra fields carry what a list
    cannot: the moments refused by the gate, and the notes about the budget.
    The graph is unchanged by reading any of them.
    """

    thread_id: int
    target_seconds: float
    candidates: list[Candidate]
    unit_indices: list[int]
    roles: dict[int, str]
    verdicts: dict[int, str]
    dropped: list[DroppedMoment] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.candidates)

    def __getitem__(self, index):
        return self.candidates[index]


# ── reconstructing a unit's words ──────────────────────────────────────────


def _clip_words(words: list[Word], start: float, end: float) -> list[Word]:
    """The words that actually fall inside a unit's span, in order.

    A unit's bounds come from its sentences, which are character-proportional
    estimates unless the transcript carries timing; clipping the real word
    stream to the span is what gives ``score_candidate`` the same measured
    input every other candidate gets.
    """
    kept = [w for w in words if w.end > start and w.start < end]
    kept.sort(key=lambda w: (w.start, w.end))
    return kept


def unit_segments(
    corpus: EditorialCorpus, transcript: list[Segment],
) -> tuple[dict[int, Segment], list[str]]:
    """Rebuild a ``Segment`` per corpus unit, with words where the source has them.

    ``segment_indices`` is the unit's own record of where in the source its
    passage came from, so the words are looked up rather than guessed. A
    transcript that does not line up with the corpus — an imported memory read
    back against a different or stale source — yields wordless segments and a
    note, never fabricated timing: the moments keep their text and their
    spans, which are what the story actually is.
    """
    notes: list[str] = []
    if not transcript:
        notes.append(
            "no source transcript was given alongside the corpus, so the path's "
            "word timing is unavailable and its clip confidence is unmeasured "
            "rather than low — it carries the default a wordless span gets"
        )
        return (
            {u.index: Segment(u.start, u.end, u.text, [], u.coherence)
             for u in corpus.units},
            notes,
        )

    per_unit: dict[int, Segment] = {}
    unresolved = 0
    for u in corpus.units:
        words: list[Word] = []
        for si in u.segment_indices:
            if 0 <= si < len(transcript):
                words.extend(transcript[si].words or [])
            else:
                unresolved += 1
        per_unit[u.index] = Segment(
            u.start, u.end, u.text,
            _clip_words(words, u.start, u.end) if words else [],
            u.coherence,
        )
    if unresolved:
        notes.append(
            f"{unresolved} unit segment reference(s) did not resolve against the "
            f"transcript supplied (the corpus and the source may differ); those "
            f"moments keep their text and spans but carry no word timing"
        )
    return per_unit, notes


# ── the walk ───────────────────────────────────────────────────────────────


def role_in_thread(
    corpus: EditorialCorpus, thread: StoryThread, unit: CorpusUnit,
) -> str:
    """The unit's dramatic function *in this story*.

    A beat is this story's beat when the thread lists it among its own; the
    beat's own ``thread_id`` is the unit's primary thread, which is not
    necessarily this one — a shared payoff closes one story and opens another,
    and its beat can only be claimed once. Roles are therefore read from the
    thread's ``beat_indices``, not from the beat.
    """
    if unit.beat_id is not None and unit.beat_id in set(thread.beat_indices):
        return corpus.beats[unit.beat_id].kind
    return "continuation"


# ``role_in_thread`` is the seam the presentation layer shares with the walk:
# ``engine.story_view`` reads the same role for the same moment in the same
# story, so a moment's role in an exported memory cannot drift from its role in
# a shipped edit. Kept here because the role is only meaningful relative to a
# story, which is this module's subject.
_role_of = role_in_thread


def story_path(
    corpus: EditorialCorpus,
    transcript: list[Segment],
    *,
    thread_id: int,
    target_seconds: float,
) -> StoryPath:
    """Select the moments of one story that fit a duration budget.

    Args:
        corpus: the story graph. Only read — it is never modified, so the same
            graph yields every presentation of it.
        transcript: the source segments the corpus was built from, needed for
            the word timing a candidate's confidence is measured from. May be
            empty, in which case the path is still produced and its confidence
            reads as unknown rather than invented.
        thread_id: which story to walk.
        target_seconds: the budget. A budget, not a target: a story longer than
            its budget ships the moments that fit, in story order, with a note
            saying what was cut. Moments are never shrunk to fit — a truncated
            moment is a different moment, and the graph found this one.

    Returns:
        A ``StoryPath`` — a sequence of the candidates kept, in source order,
        with the gate's verdict on each and the ones it refused.

    Raises:
        ValueError: ``thread_id`` names a story the corpus does not hold, or the
            budget is not positive. Both are caller errors, not editorial
            outcomes, so they are not reported as an empty path: an empty path
            is a legitimate answer for a story with no moments that clear the
            gate, and it must not be indistinguishable from a bad call.
    """
    if not 0 <= thread_id < len(corpus.threads):
        raise ValueError(
            f"thread_id {thread_id} is not one of {len(corpus.threads)} "
            f"story(ies) in this corpus")
    if not (math.isfinite(target_seconds) and target_seconds > 0.0):
        # ``inf`` is a real float that compares positive and equals itself, so
        # a plain positivity check lets it through; a caller passing it gets a
        # path of every moment in the story, which is not what "a budget" means.
        raise ValueError(
            f"target_seconds must be positive and finite, got {target_seconds!r}")

    thread = corpus.threads[thread_id]
    segments, notes = unit_segments(corpus, transcript)
    members = set(thread.unit_indices)

    # The thread's peak is not negotiable: a story with its strongest beat cut
    # out is not that story. It is force-included even when the budget would
    # rather have it gone, which is how a tight budget still ships the moment
    # the story exists to make.
    peak_unit: int | None = None
    if thread.beat_indices:
        peak = max(
            (corpus.beats[bi] for bi in thread.beat_indices),
            key=lambda b: (b.intensity, -b.unit_index),
        )
        peak_unit = peak.unit_index
    forced = {peak_unit} if peak_unit is not None else set()

    # Role order, then source order within a role. Both keys are stable, so a
    # larger budget takes every moment a smaller one took and then some — the
    # 60-second path is a subset of the 3-minute path, and the graph did not
    # change between the two calls. That nesting is what the round trip and
    # requirement #9 depend on.
    units = [
        (u, role_in_thread(corpus, thread, u))
        for u in corpus.units if u.index in members
    ]
    ordered = sorted(
        units,
        key=lambda pair: (_ROLE_RANK.get(pair[1], _NO_BEAT), pair[0].index),
    )
    role_by_unit = {u.index: role for u, role in ordered}

    chosen: list[int] = []
    total = 0.0
    over_budget = False
    for u, _role in ordered:
        span = segments[u.index].duration
        if u.index in forced and u.index not in chosen:
            chosen.append(u.index)
            total += span
            if total > target_seconds:
                over_budget = True
            continue
        if total + span <= target_seconds:
            chosen.append(u.index)
            total += span
    if not chosen and ordered:
        # Not even the highest-priority moment fits. Shipping it whole and
        # saying so is honest; shipping a fraction of it, or shipping nothing,
        # is not.
        u, _role = ordered[0]
        chosen.append(u.index)
        total = segments[u.index].duration
        over_budget = True
    if over_budget:
        notes.append(
            f"the story's highest-priority moment(s) alone run {total:.1f}s, "
            f"longer than the {target_seconds:.0f}s budget; they are shipped "
            f"whole rather than truncated, so this path overruns its budget"
        )
    if len(chosen) < len(ordered):
        notes.append(
            f"{len(ordered) - len(chosen)} of the story's {len(ordered)} "
            f"moment(s) did not fit the {target_seconds:.0f}s budget and were "
            f"left out of this presentation; the story still holds them"
        )

    # A moment the gate refuses is dropped and reported, never shipped. The
    # guard's rules are the same ones every other candidate in the pipeline is
    # held to, so a story path cannot smuggle a moment through that the
    # independent-clip path would have rejected.
    kept: list[Candidate] = []
    unit_indices: list[int] = []
    roles: dict[int, str] = {}
    verdicts: dict[int, str] = {}
    dropped: list[DroppedMoment] = []

    for idx in sorted(chosen):
        u = corpus.units[idx]
        cand = score_candidate(segments[idx])
        ok, why = validate(cand)
        verdicts[idx] = why
        if not ok:
            dropped.append(DroppedMoment(
                unit_index=idx, role=role_by_unit.get(idx, "continuation"),
                start=u.start, end=u.end, text=u.text, reason=why,
            ))
            continue
        kept.append(cand)
        unit_indices.append(idx)
        roles[idx] = role_by_unit.get(idx, "continuation")

    if not kept and ordered:
        notes.append(
            "no moment of this story cleared the editorial gate at this budget; "
            "the path is empty by judgement, not by absence of story"
        )

    # A payoff whose setup is missing is a story that starts mid-thought, which
    # is the defect the gate catches for clips and the graph can see for stories:
    # the thread's own links name the setup. A setup can be absent for two
    # unrelated reasons, and conflating them would make an honest layer lie —
    # "the budget cut it" is wrong when what actually happened is that the gate
    # refused it, and an editor who reads the first will fix the wrong problem.
    present = set(unit_indices)
    refused = {d.unit_index: d.reason for d in dropped}
    for lk in thread.links:
        if lk.payoff_unit in present and lk.setup_unit not in present:
            when = f"({corpus.units[lk.setup_unit].start:.0f}s)"
            if lk.setup_unit in refused:
                notes.append(
                    f"the payoff at unit {lk.payoff_unit} "
                    f"({corpus.units[lk.payoff_unit].start:.0f}s) is included but "
                    f"its {lk.kind} setup at unit {lk.setup_unit} {when} was "
                    f"refused by the editorial gate — {refused[lk.setup_unit]}"
                )
            else:
                notes.append(
                    f"the payoff at unit {lk.payoff_unit} "
                    f"({corpus.units[lk.payoff_unit].start:.0f}s) is included but "
                    f"its {lk.kind} setup at unit {lk.setup_unit} {when} was cut "
                    f"from the {target_seconds:.0f}s budget — this path opens on "
                    f"a moment whose premise the viewer never gets"
                )

    # A path that ships two members of one redundancy group ships the same
    # passage twice under two timecodes. The corpus's own redundancy layer has
    # already judged this case — it named a representative passage — and the
    # path does not silently override that judgement. Dropping the restatement
    # would be wrong too: the link that put both units in this thread is a
    # callback, and recalling an earlier point is a real narrative move. So the
    # path reports the collision and leaves the call to the editor.
    shipped_groups: dict[int, list[int]] = {}
    for idx in unit_indices:
        gid = corpus.units[idx].redundancy_group_id
        if gid >= 0:
            shipped_groups.setdefault(gid, []).append(idx)
    for gid, members_ in sorted(shipped_groups.items()):
        if len(members_) < 2:
            continue
        group = corpus.redundancy_groups[gid]
        notes.append(
            f"this path ships units {', '.join(str(m) for m in members_)} "
            f"together, and the corpus marks them as {group.relation} of one "
            f"another in redundancy group {gid}, keeping unit "
            f"{group.representative} as the representative passage; the edit "
            f"therefore repeats a passage the story memory already records"
        )

    return StoryPath(
        thread_id=thread_id,
        target_seconds=target_seconds,
        candidates=kept,
        unit_indices=unit_indices,
        roles=roles,
        verdicts=verdicts,
        dropped=dropped,
        notes=notes,
    )


__all__ = [
    "DroppedMoment",
    "StoryPath",
    "role_in_thread",
    "story_path",
    "unit_segments",
]
