"""Present a corpus's stories as a memory a person and a machine can both read.

This is the layer the directive calls STORY MEMORY: not a graph database, not a
UI, but a document. ``EditorialCorpus`` is the analysis and ``story_path`` is
the join to an edit; this module is the *report* — the answer to "what stories
are in this source, which moments belong to each, how do they connect, and why
is each moment here".

Three artifacts sit beside one another, and only the first is canonical:

- ``editorial_corpus.json`` — the graph. Written by
  ``EditorialCorpus.to_dict``, read back by ``engine.corpus_io.read_corpus``.
  This is the memory; everything else is a view of it.
- ``story_view.json`` — the machine view, one object per moment, answering the
  eight questions the directive asks of every node. Built by ``story_view``.
- ``story.md`` — the human view, which presents stories rather than a graph
  ("the graph is underneath; the user sees the story"). Built by
  ``render_story_md``.

Both views are pure functions of the corpus (and, optionally, of the transcript
the corpus was built from, needed for word timing alone). Neither re-runs the
analyser and neither mutates the graph, so a memory exported today and a memory
exported from the same file in a year present the same stories — which is what
makes the export honest rather than merely readable.

What the views deliberately do *not* do:

- They do not invent relationships. The five kinds the analyser infers from
  lexical and structural evidence are what the export carries; kinds no
  available signal can support are listed by name under
  ``unsupported_relationships`` with the reason, because an absence that is
  explained is honest and an absence that is hidden looks like an oversight
  (see ``_RELATIONSHIPS`` and the design note in ``docs/story-memory-design.md``
  §4).
- They do not invent confidence. Every number in the view is either measured by
  a layer below this one or replaced by an explicit ``confidence_basis`` saying
  why no number exists. The transcription provider's constant ``1.0``
  (``providers/sherpa.py``) is a known fabrication and is never quoted here as a
  measurement.
- They do not replace the edit plan, the timeline, or the NLE exports. A story
  becomes an edit through ``engine.story_path.story_path``; the view only says
  how, and the graph is unchanged by either.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.context_guard import validate
from engine.corpus import EditorialCorpus
from engine.editorial import score_candidate
from engine.lexical import quote
from engine.story_path import role_in_thread, unit_segments
from models.core import Segment, Word

VIEW_SCHEMA = "1.0"

# Two timecodes closer than this are the same moment. Mirrors the reader's
# tolerance: a view that disagreed with its corpus by a rounding residue would
# be reported as broken, and it is not.
_TOL = 1e-3

# The relationship taxonomy, as the export declares it rather than as the
# directive's wishlist implies it. Five kinds are inferred by ``engine.threads``
# from evidence a transcript actually supplies; two are roles read off an edge's
# position rather than kinds; three are facts the corpus already records under
# another analysis; the rest are refused, because no available signal
# distinguishes them from one another and inventing the distinction would be the
# fabricated precision the directive forbids.
_LINK_KINDS = (
    "question_answer",
    "problem_solution",
    "causal",
    "callback",
    "contrast_turn",
)
_ROLES_FROM_POSITION = ("sets_up", "pays_off")

_COVERED_ELSEWHERE = (
    {
        "kind": "repeats",
        "where": "corpus.redundancy_groups",
        "reason": "two passages the redundancy layer found near-identical; the "
                  "group names a representative, which is the one an edit keeps",
    },
    {
        "kind": "expands",
        "where": "corpus.redundancy_groups",
        "reason": "a later passage with materially more substance is reported "
                  "there with the relation 'elaboration'",
    },
    {
        "kind": "develops",
        "where": "this view, as an untyped chain co-membership",
        "reason": "co-membership inside one story's chain, plus any redundancy "
                  "group both moments belong to; labelled as chain membership, "
                  "never as an inferred edge",
    },
)

_UNSUPPORTED = (
    {
        "kind": "supports",
        "reason": "agreement between two claims needs a stance or entailment "
                  "model; lexical overlap cannot separate support from mere "
                  "repetition",
    },
    {
        "kind": "explains",
        "reason": "would need an explicit explanatory connective plus "
                  "co-reference, and the transcript supplies neither reliably",
    },
    {
        "kind": "provides_context",
        "reason": "topic co-membership alone is exactly the hallucination the "
                  "directive forbids, and no separate signal for this kind "
                  "exists, so it is never emitted",
    },
    {
        "kind": "foreshadows",
        "reason": "needs a later-realised anticipation, and there is no "
                  "forward-projection model to see it",
    },
    {
        "kind": "creates_conflict",
        "reason": "spoken contrast is already covered by 'contrast_turn'; this "
                  "kind would need a stance model to see conflict that is not "
                  "yet on the page",
    },
    {
        "kind": "escalates",
        "reason": "requires comparing degree across moments, and nothing "
                  "compares intensity at the link level",
    },
    {
        "kind": "qualifies",
        "reason": "requires detecting a hedge or partial negation that reaches "
                  "back to an earlier unit",
    },
    {
        "kind": "depends_on",
        "reason": "the direction of a causal edge is already carried by its "
                  "setup->payoff position; a second name for the same fact "
                  "would be ambiguous",
    },
)

_RELATIONSHIPS = {
    "kinds_inferred_from_evidence": list(_LINK_KINDS),
    "roles_derived_from_edge_position": list(_ROLES_FROM_POSITION),
    "recorded_under_another_analysis": list(_COVERED_ELSEWHERE),
    "unsupported": list(_UNSUPPORTED),
}

_UNATTRIBUTED = "unattributed"


@dataclass(frozen=True)
class StoryMemoryFiles:
    """The three files of an exported memory, by the path each was written to."""

    corpus: Path
    view: Path
    story_md: Path


# ── small honest formatters ─────────────────────────────────────────────────


def hms(seconds: float) -> str:
    """A source timestamp in ``MM:SS`` (or ``H:MM:SS`` past an hour).

    The corpus holds seconds and no frame rate, so this is deliberately not
    SMPTE: printing frames would imply a precision the memory does not have,
    and a source timestamp is what the chain of custody back to the video needs.
    """
    t = max(0.0, float(seconds))
    whole = int(t + 0.5)  # the nearest second, so 20.91 reads as 00:21
    h, whole = divmod(whole, 3600)
    m, s = divmod(whole, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _confidence_of(words: list[Word]) -> tuple[float | None, str]:
    """The moment's speech confidence, and the basis the number stands on.

    ``None`` is a value here, not a failure: a moment with no word timing has a
    known span and unknown speech, and the basis says so. A transcript whose
    every word reports exactly 1.0 is the provider's default rather than a
    measurement — see ``providers/sherpa.py``, whose word confidence is a
    constant — so the basis says the number is unmeasured rather than letting a
    perfect 1.0 pass for certainty.
    """
    if not words:
        return None, (
            "no word timing is recorded for this moment, so its speech "
            "confidence is unmeasured — the span is known, the words are not"
        )
    confs = [w.confidence for w in words]
    if all(c >= 1.0 for c in confs):
        return 1.0, (
            f"every one of {len(confs)} word(s) carries the default confidence "
            f"1.0, which a transcription provider returns when it computed no "
            f"per-token probabilities; treat this as unmeasured, not as "
            f"certainty"
        )
    return (
        round(sum(confs) / len(confs), 3),
        f"mean per-token transcription probability over {len(confs)} word(s)",
    )


# ── the view ────────────────────────────────────────────────────────────────


def _stories_holding(corpus: EditorialCorpus, a: int, b: int) -> list[int]:
    """Every story whose membership includes both ends of an edge.

    A shared payoff can close one story and open another, so an edge may belong
    to more than one; listing all of them is what keeps the view's graph honest
    for a moment that lives in several stories at once.
    """
    return [
        t.thread_id for t in corpus.threads
        if a in t.unit_indices and b in t.unit_indices
    ]


def _moment_view(
    corpus: EditorialCorpus,
    unit_index: int,
    segments: dict[int, Segment],
    broll_by_unit: dict[int, list[Any]],
    block_by_unit: dict[int, Any],
    thread_of: dict[int, Any],
) -> dict[str, Any]:
    u = corpus.units[unit_index]
    seg = segments[unit_index]
    cand = score_candidate(seg)
    ok, verdict = validate(cand)
    conf, basis = _confidence_of(seg.words or [])

    edges: list[dict[str, Any]] = []
    establishes: list[int] = []
    depends_on: list[int] = []
    for lk in corpus.links:
        if lk.setup_unit == unit_index:
            other, direction = lk.payoff_unit, "sets_up"
        elif lk.payoff_unit == unit_index:
            other, direction = lk.setup_unit, "pays_off"
        else:
            continue
        if direction == "sets_up":
            establishes.append(other)
        else:
            depends_on.append(other)
        edges.append({
            "unit": other,
            "timecode": hms(corpus.units[other].start),
            "kind": lk.kind,
            "direction": direction,
            "story_ids": _stories_holding(corpus, lk.setup_unit, lk.payoff_unit),
            "strength": lk.strength,
            "confidence": lk.confidence,
            "reason": lk.reason,
            "evidence": list(lk.evidence),
        })

    # Two moments in one story are members of one chain, which the graph already
    # asserts whether or not a typed edge joins them directly. Reporting that
    # separately is what keeps "these connect" (a typed edge that rests on
    # evidence) distinct from "these belong to one story" (chain co-membership):
    # the latter is structural and carries no inferred relationship, and the
    # reason says so so it cannot be read as one.
    chain_mates: list[dict[str, Any]] = []
    linked = {(min(lk.setup_unit, lk.payoff_unit), max(lk.setup_unit, lk.payoff_unit))
              for lk in corpus.links}
    for tid in corpus.unit_threads[unit_index]:
        members = thread_of[tid].unit_indices  # ascending: links run forward
        pos = members.index(unit_index)
        for other_pos, other in enumerate(members):
            if other == unit_index:
                continue
            pair = (min(unit_index, other), max(unit_index, other))
            if pair in linked:
                continue  # a typed edge already covers this pair
            shared = [g.group_id for g in corpus.redundancy_groups
                      if unit_index in g.members and other in g.members]
            gap = abs(other_pos - pos)
            if gap == 1:
                relation = ("adjacent in this chain with no typed edge between "
                            "them — succession, not an inferred relationship")
            else:
                through = members[min(pos, other_pos) + 1:max(pos, other_pos)]
                named = (" through unit " + str(through[0])) if len(through) == 1 \
                    else (f" through {len(through)} intervening moment(s)")
                relation = (f"{gap} moment(s) apart in this chain with no typed "
                            f"edge between them — connected by the chain"
                            f"{named}, not by an inferred relationship")
            chain_mates.append({
                "unit": other,
                "timecode": hms(corpus.units[other].start),
                "story_id": tid,
                "adjacent": gap == 1,
                "reason":
                    f"both in story {tid}'s chain; {relation}"
                    + (f"; both belong to redundancy group {shared[0]}"
                       if shared else ""),
            })

    stories: list[dict[str, Any]] = []
    for tid in corpus.unit_threads[unit_index]:
        thread = thread_of[tid]
        role = role_in_thread(corpus, thread, u)
        beat = corpus.beats[u.beat_id] \
            if u.beat_id is not None and u.beat_id in set(thread.beat_indices) \
            else None
        why: list[str] = []
        if beat is not None:
            rank = (" — the corpus's strongest beat" if beat.is_corpus_peak
                    else " — this story's strongest beat" if beat.is_thread_peak
                    else "")
            why.append(f"{role} beat (intensity {beat.intensity:.2f}{rank}): "
                       f"{beat.reason}")
        own = [e for e in edges if tid in e["story_ids"]]
        out_e = [e for e in own if e["direction"] == "sets_up"]
        in_e = [e for e in own if e["direction"] == "pays_off"]
        if out_e:
            why.append(f"sets up {len(out_e)} later moment(s) of this memory")
        if in_e:
            why.append(f"pays off {len(in_e)} earlier moment(s)")
        if not why:
            why.append("a member of this story's chain that none of its typed "
                       "links touches directly")
        stories.append({"story_id": tid, "role": role, "why": why})

    block = block_by_unit.get(unit_index)
    if block is not None and block.speaker:
        speaker, speaker_basis = block.speaker, "speaker label from the source"
    elif corpus.has_speaker_labels:
        speaker = _UNATTRIBUTED
        speaker_basis = "the source carries speaker labels but none resolves to this moment"
    else:
        speaker = _UNATTRIBUTED
        speaker_basis = "this source has no speaker labels, so every moment is unattributed"

    topic = corpus.topics[u.topic_id] if u.topic_id >= 0 else None
    redundancy = None
    if u.redundancy_group_id >= 0:
        g = corpus.redundancy_groups[u.redundancy_group_id]
        redundancy = {
            "group_id": g.group_id,
            "relation": g.relation,
            "representative": g.representative,
            "is_the_representative": g.representative == unit_index,
            "reason": g.reason,
        }

    return {
        "unit": unit_index,
        "start": round(u.start, 3),
        "end": round(u.end, 3),
        "timecode": hms(u.start),
        "text": u.text,
        "story_ids": list(corpus.unit_threads[unit_index]),
        "stories": stories,
        "speaker": speaker,
        "speaker_basis": speaker_basis,
        "visual": [
            {"kind": m.kind, "cue": m.cue, "confidence": m.confidence,
             "reason": m.reason}
            for m in broll_by_unit.get(unit_index, [])
        ],
        "edges": edges,
        "establishes": sorted(set(establishes)),
        "depends_on": sorted(set(depends_on)),
        "chain_mates": chain_mates,
        "topic": {"topic_id": topic.topic_id, "label": topic.label}
            if topic is not None else None,
        "redundancy": redundancy,
        "gate": {
            "ok": ok,
            "verdict": ("ok — clears the editorial gate" if ok
                        else f"refused — {verdict}"),
            # The span's clip confidence is only a measurement when the moment
            # has words to measure; a wordless span scores 1.0 by default, which
            # is the same fabrication the moment-level basis disavows above.
            "clip_confidence": round(cand.confidence, 3) if seg.words else None,
        },
        "confidence": conf,
        "confidence_basis": basis,
    }


def _story_view(corpus: EditorialCorpus, thread: Any) -> dict[str, Any]:
    members = set(thread.unit_indices)
    beats = [corpus.beats[bi] for bi in thread.beat_indices]
    peak = max(beats, key=lambda b: (b.intensity, -b.unit_index)) if beats else None
    return {
        "story_id": thread.thread_id,
        "title": thread.title,
        "arc": thread.arc,
        "confidence": thread.confidence,
        "reason": thread.reason,
        "evidence": list(thread.evidence),
        "start": round(thread.start, 3),
        "end": round(thread.end, 3),
        "timecode": f"{hms(thread.start)}–{hms(thread.end)}",
        "unit_indices": list(thread.unit_indices),
        "roles": {u: role_in_thread(corpus, thread, corpus.units[u])
                  for u in thread.unit_indices},
        "links": [
            {
                "setup_unit": lk.setup_unit,
                "payoff_unit": lk.payoff_unit,
                "setup_timecode": hms(lk.setup_start),
                "payoff_timecode": hms(lk.payoff_start),
                "kind": lk.kind,
                "strength": lk.strength,
                "confidence": lk.confidence,
                "reason": lk.reason,
                "evidence": list(lk.evidence),
            }
            for lk in thread.links
        ],
        "peak": {
            "unit": peak.unit_index,
            "kind": peak.kind,
            "intensity": peak.intensity,
            "is_corpus_peak": peak.is_corpus_peak,
            "is_thread_peak": peak.is_thread_peak,
        } if peak is not None else None,
        # A story is a set of moments plus the edges between them, not an
        # ordering: the graph holds it, and a presentation of it is chosen
        # separately by ``story_path``. Reporting the moments in source order
        # here is a reading convenience, not a claim about the edit.
        "moments_in_source_order": sorted(members),
    }


def story_view(
    corpus: EditorialCorpus,
    transcript: list[Segment] | None = None,
) -> dict[str, Any]:
    """Build the machine view of every story and every moment in a corpus.

    Args:
        corpus: the story graph. Read only — the same object yields this view,
            every other view of it, and every edit, unchanged.
        transcript: the source segments the corpus was built from. Used for word
            timing alone: a moment's speech confidence and the editorial gate's
            verdict both read the words. May be ``None`` or empty, in which case
            the view is still built and the moments carry an explicit
            ``confidence_basis`` saying the timing is unavailable.

    Returns:
        The view as a plain dict, JSON-serialisable and deterministic: the same
        corpus and transcript always yield byte-identical output, which is what
        makes an exported memory re-importable (see ``verify_story_view``).
    """
    segments, notes = unit_segments(
        corpus, list(transcript) if transcript else [])
    broll_by_unit: dict[int, list[Any]] = {}
    for m in corpus.broll:
        broll_by_unit.setdefault(m.unit_index, []).append(m)
    block_by_unit: dict[int, Any] = {}
    for blk in corpus.speaker_blocks:
        for u in blk.unit_indices:
            block_by_unit[u] = blk
    thread_of = {t.thread_id: t for t in corpus.threads}

    moments = [
        _moment_view(corpus, u.index, segments, broll_by_unit, block_by_unit,
                     thread_of)
        for u in corpus.units
    ]
    unattributed = sum(1 for m in moments if m["speaker"] == _UNATTRIBUTED)
    if not corpus.has_speaker_labels and moments and unattributed == len(moments):
        notes.append(
            "the source carries no speaker labels, so every moment is reported "
            "as unattributed rather than guessed"
        )

    return {
        "schema": VIEW_SCHEMA,
        "kind": "selektyt.story_view",
        "generated_by": "engine.story_view — a pure derivation of the corpus; "
                        "the analyser is never re-run to produce it",
        "provenance": {
            "source": corpus.source,
            "duration": corpus.duration,
            "duration_read": hms(corpus.duration),
            "languages": list(corpus.detected_languages),
            "corpus_fingerprint": corpus.build.fingerprint,
            "corpus_schema": corpus.build.schema,
            "unit_count": len(corpus.units),
            "has_word_timestamps": corpus.has_word_timestamps,
            "has_speaker_labels": corpus.has_speaker_labels,
        },
        "timing_note": (
            "every timestamp is seconds from the source's own zero; the corpus "
            "holds no frame rate, so these are source timestamps, not SMPTE"
        ),
        "stories": [_story_view(corpus, t) for t in corpus.threads],
        "moments": moments,
        "relationships": _RELATIONSHIPS,
        "how_a_story_becomes_an_edit": {
            "graph_unchanged": True,
            "selection": (
                "engine.story_path.story_path(corpus, transcript, thread_id=<id>, "
                "target_seconds=<budget>)"
            ),
            "nested_budgets": (
                "moments are chosen by (role, source order) over a fixed key, so "
                "every smaller budget's moments are a subset of every larger "
                "one's — the 60-second cut is inside the 3-minute cut"
            ),
            "timeline": (
                "each returned Candidate flows through engine.edit_plan, "
                "engine.timeline_build and engine.nle_export unchanged"
            ),
        },
        "notes": notes,
    }


# ── the human document ──────────────────────────────────────────────────────


def _role_label(role: str) -> str:
    return role.upper()


def render_story_md(
    corpus: EditorialCorpus,
    transcript: list[Segment] | None = None,
) -> str:
    """Render the human view of a corpus's stories.

    Presents stories, not a graph: each story lists its moments by the role they
    play in it, the typed edges that connect them with the evidence each edge
    rests on, and any moment the editorial gate refused. Moments no story claims
    and relationships this memory declines to claim are stated plainly at the
    end, because a document that quietly omits them looks complete.
    """
    view = story_view(corpus, transcript)
    provenance = view["provenance"]
    out: list[str] = []
    out.append("# SeleKyT story memory")
    out.append("")
    source = provenance["source"]
    out.append(
        f"source: {source if source else '(no source path recorded)'}"
        f"  ·  {provenance['duration_read']}  ·  "
        f"{', '.join(provenance['languages']) or 'language not detected'}"
    )
    out.append(
        f"corpus fingerprint {provenance['corpus_fingerprint']}  ·  "
        f"{provenance['unit_count']} moment(s)  ·  "
        f"{len(view['stories'])} story(ies)"
    )
    out.append(view["timing_note"] + ".")
    out.append("")

    for story in view["stories"]:
        out.append(
            f"## STORY {story['story_id'] + 1:02d} — {story['title'] or 'untitled'}"
        )
        out.append(
            f"{story['arc'] or 'no arc inferred'}  ·  confidence "
            f"{story['confidence']:.2f}  ·  {len(story['unit_indices'])} "
            f"moment(s)  ·  {story['timecode']}"
        )
        out.append(f"_{story['reason']}_")
        out.append("")
        for u in story["moments_in_source_order"]:
            moment = view["moments"][u]
            # The role belongs to *this* story: a moment shared by two can
            # play a different part in each, and reading the first entry would
            # describe it by the wrong story.
            own = next((s for s in moment["stories"]
                        if s["story_id"] == story["story_id"]), None)
            role = own["role"] if own is not None else "continuation"
            out.append(
                f"  {_role_label(role):<12} {moment['timecode']}  "
                f"\"{quote(moment['text'])}\""
            )
        out.append("")

        if story["links"]:
            out.append("  WHY THESE CONNECT")
            for lk in story["links"]:
                out.append(
                    f"    {lk['setup_timecode']} → {lk['payoff_timecode']}   "
                    f"{lk['kind']}   {lk['confidence']:.2f}"
                )
                out.append(f"      {lk['reason']}")
                for ev in lk["evidence"]:
                    out.append(f"      \"{quote(ev)}\"")
            out.append("")

        refused = [view["moments"][u] for u in story["moments_in_source_order"]
                   if not view["moments"][u]["gate"]["ok"]]
        if refused:
            out.append("  REFUSED BY THE EDITORIAL GATE")
            out.append("  (the moment is held by the story but cannot ship alone)")
            for m in refused:
                out.append(f"    {m['timecode']}  \"{quote(m['text'])}\"")
                out.append(f"      {m['gate']['verdict']}")
            out.append("")

        # Each co-membership is held by both of its ends, so dedupe by pair;
        # the pair is reported from its earlier end.
        seen_pairs: set[tuple[int, int]] = set()
        co_members: list[tuple[int, int]] = []
        for m in view["moments"]:
            for c in m["chain_mates"]:
                if c["story_id"] != story["story_id"]:
                    continue
                pair = (min(m["unit"], c["unit"]), max(m["unit"], c["unit"]))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    co_members.append(pair)
        if co_members:
            out.append("  ALSO IN THIS STORY (chain co-members, not inference)")
            for a, b in sorted(co_members):
                out.append(
                    f"    {hms(corpus.units[a].start)} → "
                    f"{hms(corpus.units[b].start)}  both in this story's chain "
                    f"with no typed edge between them — they are connected by "
                    f"the chain, not by an inferred relationship")
            out.append("")

    stray = [m for m in view["moments"] if not m["story_ids"]]
    if stray:
        out.append("## MOMENTS NO STORY CLAIMS")
        for m in stray:
            out.append(f"  {m['timecode']}  \"{quote(m['text'])}\"")
            why = m["topic"]["label"] if m["topic"] else "no subject cluster"
            out.append(f"    {why} — the analyser found no narrative relation "
                       f"to another moment")
        out.append("")

    out.append("## RELATIONSHIPS THIS MEMORY DOES NOT CLAIM")
    for entry in view["relationships"]["unsupported"]:
        out.append(f"  {entry['kind']:<18} {entry['reason']}")
    out.append("")

    out.append("## HOW A STORY BECOMES AN EDIT")
    how = view["how_a_story_becomes_an_edit"]
    out.append(f"  {how['selection']}")
    out.append(f"  {how['nested_budgets']}.")
    out.append(f"  {how['timeline']}.")
    out.append("  The graph is unchanged by any of it — an edit is one "
               "presentation of a story, not a version of it.")
    out.append("")
    return "\n".join(out)


# ── writing and reading the bundle ──────────────────────────────────────────


def write_story_memory(
    corpus: EditorialCorpus,
    out_dir: Path,
    transcript: list[Segment] | None = None,
) -> StoryMemoryFiles:
    """Write the memory bundle: the graph, the view, and the human document.

    The corpus JSON is the canonical artifact and is written from
    ``to_dict()`` exactly as the pipeline writes it, so a memory bundle and a
    working corpus file are never two different things. The two views are
    derived from it in the same call and cannot drift.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = out_dir / "editorial_corpus.json"
    view_path = out_dir / "story_view.json"
    md_path = out_dir / "story.md"

    corpus_path.write_text(
        json.dumps(corpus.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    view_path.write_text(
        json.dumps(story_view(corpus, transcript), indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8")
    md_path.write_text(render_story_md(corpus, transcript), encoding="utf-8")
    return StoryMemoryFiles(corpus=corpus_path, view=view_path, story_md=md_path)


def _load_view(src: Path | str | dict[str, Any]) -> tuple[dict[str, Any], str]:
    where: str
    d: Any
    if isinstance(src, dict):
        return src, "<dict>"
    if isinstance(src, Path):
        where = str(src)
        d = json.loads(src.read_text(encoding="utf-8"))
    else:
        candidate = Path(src)
        try:
            path = candidate if candidate.is_file() else None
        except OSError:
            path = None
        if path is None:
            where, text = "<json string>", src
        else:
            where = str(path)
            text = path.read_text(encoding="utf-8")
        d = json.loads(text)
    if not isinstance(d, dict):
        raise ValueError(f"{where}: a story view is a JSON object, got "
                         f"{type(d).__name__}")
    return d, where


def read_story_view(src: Path | str | dict[str, Any]) -> dict[str, Any]:
    """Read an exported view back, refusing anything that is not one.

    The view is a report, not an authority: nothing downstream is built from it.
    Refusal is therefore a ``ValueError`` for the caller to surface, not a
    silent degradation that might be mistaken for an empty memory.
    """
    d, where = _load_view(src)
    if d.get("schema") != VIEW_SCHEMA:
        raise ValueError(
            f"{where}: declares view schema {d.get('schema')!r}; this SeleKyT "
            f"reads {VIEW_SCHEMA} and will not guess at a structure it does not "
            f"know")
    if d.get("kind") != "selektyt.story_view":
        raise ValueError(
            f"{where}: the 'kind' field is {d.get('kind')!r}, not "
            f"'selektyt.story_view'")
    return d


def verify_story_view(
    view: dict[str, Any], corpus: EditorialCorpus,
) -> list[str]:
    """Cross-check an imported view against the corpus it was read alongside.

    Returns a list of disagreements, empty when the two agree. The round trip is
    the point of the memory layer: an exported view read into a clean SeleKyT
    process must describe the same moments the imported corpus holds, or the
    export was not the memory of this source. Timestamps are compared to a
    tolerance, because a float that crossed JSON is the same cut even when its
    decimal expansion is not.

    A non-empty result means the view and the corpus disagree; it never means
    the corpus is wrong, since the corpus is the memory and the view is a
    report of it.
    """
    problems: list[str] = []
    moments: list[dict[str, Any]] = view.get("moments", [])
    if len(moments) != len(corpus.units):
        problems.append(
            f"the view reports {len(moments)} moment(s) for a corpus holding "
            f"{len(corpus.units)}")
        return problems
    for m, u in zip(moments, corpus.units):
        if m.get("unit") != u.index:
            problems.append(f"moment {m.get('unit')!r} is not unit {u.index}")
        if abs(m.get("start", 0.0) - u.start) > _TOL or \
                abs(m.get("end", 0.0) - u.end) > _TOL:
            problems.append(
                f"unit {u.index}: the view spans {m.get('start')}–{m.get('end')}, "
                f"the corpus spans {u.start}–{u.end}")
        if list(m.get("story_ids", [])) != list(corpus.unit_threads[u.index]):
            problems.append(
                f"unit {u.index}: the view's stories {m.get('story_ids')} are not "
                f"the corpus's {corpus.unit_threads[u.index]}")

    view_stories: list[dict[str, Any]] = view.get("stories", [])
    if len(view_stories) != len(corpus.threads):
        problems.append(
            f"the view reports {len(view_stories)} story(ies) for a corpus "
            f"holding {len(corpus.threads)}")
        return problems
    for vs, thread in zip(view_stories, corpus.threads):
        if vs.get("story_id") != thread.thread_id:
            problems.append(f"a story reports id {vs.get('story_id')}, not "
                            f"{thread.thread_id}")
        if list(vs.get("unit_indices", [])) != list(thread.unit_indices):
            problems.append(
                f"story {thread.thread_id}: the view holds "
                f"{vs.get('unit_indices')}, the corpus holds "
                f"{thread.unit_indices}")
        if len(vs.get("links", [])) != len(thread.links):
            problems.append(
                f"story {thread.thread_id}: the view reports "
                f"{len(vs.get('links', []))} link(s), the corpus holds "
                f"{len(thread.links)}")
    return problems


__all__ = [
    "StoryMemoryFiles",
    "hms",
    "read_story_view",
    "render_story_md",
    "story_view",
    "verify_story_view",
    "write_story_memory",
]
