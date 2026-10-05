"""The corpus editorial representation.

This module looks at a whole transcript as **one body of material**. Everything
else in the editorial layer scores passages; this layer answers the questions an
experienced editor asks *before* scoring anything, because they change what a
good score even means:

- What are the subjects of this recording, and which ones come back? A subject
  that recurs is a spine; a subject mentioned once is an aside, and no
  per-passage score can tell them apart.
- Where are the stories set up and where do they pay off? A clip that contains
  a payoff without its setup lands flat.
- Where does the recording repeat itself, and which of the repetitions is the
  one worth keeping? An editor who cuts all repetition produces a faster video
  and a worse one.
- What is the speaker's rhythm — where do they rush, hesitate, restart? That is
  where the edit's music lives.
- And above every one of these: **what is the evidence**, and where in the
  source can a human editor go to check it?

The contract
------------
``build_corpus`` returns an ``EditorialCorpus``: a pure value holding the
transcript's editorial structure. Every derived artifact carries

- ``start`` / ``end`` — source seconds, so any decision can be jumped to in the
  source media;
- ``confidence`` — 0..1, scaled by how explicit the evidence was;
- ``reason`` — a sentence naming the evidence that produced the judgement;
- ``evidence`` — verbatim quotations from the transcript, bounded in count and
  length.

Signals the transcript does not carry are reported as ``None`` or an empty list
— never as a fabricated value. Concretely:

- No word timestamps ⇒ ``rhythm.available is False`` and every timing-derived
  field is ``None`` (filler, false-start and truncation counts, which come from
  text, are still reported).
- No speaker labels (the common case: ``models.core.Segment`` has no speaker
  field) ⇒ every ``SpeakerBlock.speaker`` is ``None`` and the blocks are
  documented as *turn* hypotheses.
- No visual or deictic cues ⇒ ``broll`` is an empty list. No shot is invented.
- No boilerplate / no punctuation ⇒ units are still built; boilerplate flags are
  simply false and sentence splitting falls back to whole-segment units.

Determinism, purity, and cost
-----------------------------
``build_corpus`` is deterministic and pure: identical input yields byte-identical
output, there is no global state, no network access, no ML inference and no
reading of the wall clock. The only input-derived field is ``build.fingerprint``,
a digest of the normalised input recorded so a report can state *which* input a
corpus came from.

Cost over U units, S sentences and W words:

- unit building: O(S) (plus O(W) when sentences are word-aligned);
- topics: O(U * K) with K the bounded number of open topics;
- threads and beats: O(U * W_window) with a bounded forward window;
- redundancy: O(U + P), P the blocked candidate-pair count, bounded per unit;
- rhythm: O(W) plus one O(U log U) sort for the median speech rate.

Memory is O(U + W): structures hold indices into the unit list and one bounded
evidence quotation per artifact. **No topic, thread or group holds a copy of the
transcript** — they hold indices, and text is consulted by index. The per-unit
``text`` field is the one place a full text copy exists, deliberately: reports
need it, and there is exactly one copy in the whole corpus.

Stability
---------
The constructor signature and the field set of every dataclass here are the
contract agents B (selection), C (long-form) and D (short-form) build against.
Backwards-incompatible changes require a version bump in ``BuildInfo.schema``.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from engine.context_guard import detect_languages
from engine.lexical import fingerprint
from engine.redundancy import RedundancyGroup, group_redundancy
from engine.rhythm import (
    RhythmProfile,
    SpeakerBlock,
    analyze_rhythm,
    derive_speaker_blocks,
)
from engine.story_units import Sentence, StoryUnit, _all_sentences, segment_story_units
from engine.threads import (
    BrollMarker,
    DramaticBeat,
    SetupPayoffLink,
    StoryThread,
    ThreadResult,
    UnitRhythmRef,
    build_threads,
)
from engine.topics import Topic, TopicResult, cluster_topics
from models.core import Segment

SCHEMA_VERSION = "1.1"

_PUNCT = "।॥？！.,;:—-–\"'()[]{}"
_TERMINAL_PUNCT = set(".?!।॥？。")


# ── data contract ──────────────────────────────────────────────────────────


@dataclass
class CorpusUnit:
    """The corpus's atomic editorial element: one coherent passage.

    Every downstream artifact (topic, thread, beat, redundancy group, block)
    refers to units by index, so this list is the spine of the whole
    representation. ``text`` is the only full text copy kept per unit.
    """

    index: int
    start: float
    end: float
    segment_indices: list[int]
    text: str
    sentence_count: int
    word_count: int
    coherence: float
    boilerplate: bool
    timing_source: str           # "word_aligned" | "character_proportional"
    restatement_count: int = 0   # sentence pairs inside this unit that repeat
    # cross-links, filled once every analysis has run
    topic_id: int = -1
    thread_id: int | None = None
    beat_id: int | None = None
    redundancy_group_id: int = -1
    speaker_block_id: int = -1


@dataclass
class BuildInfo:
    """Provenance of the build itself — no wall clock, no host identity."""

    schema: str = SCHEMA_VERSION
    fingerprint: str = ""        # digest of the normalised input
    unit_count: int = 0
    segment_count: int = 0
    duration_source: str = ""    # "given" | "inferred_from_segments" | "unknown"
    note: str = ""


@dataclass
class EditorialCorpus:
    """The corpus-level editorial representation of one transcript.

    Field groups, all optional-safe (degenerate input yields a valid corpus with
    empty lists and ``None`` signals):

    - ``units`` — the spine; every other artifact indexes into it.
    - ``topics`` — subject clusters with their member units.
    - ``threads`` / ``beats`` / ``links`` — narrative chains, dramatic beats and
      the setup->payoff relations joining them.
    - ``redundancy_groups`` — passages that say the same thing, with the
      recommended survivor.
    - ``speaker_blocks`` — speaker or turn boundaries.
    - ``broll`` — visual opportunities the transcript itself licenses.
    - ``rhythm`` — conversational cadence, per unit and corpus-wide.
    - ``notes`` — editorial caveats a human editor should read first.
    """

    # identity
    source: str | None
    language: str
    detected_languages: list[str]
    duration: float
    build: BuildInfo
    # signal availability
    has_word_timestamps: bool
    has_speaker_labels: bool
    # the spine
    units: list[CorpusUnit]
    # analyses
    topics: list[Topic]
    threads: list[StoryThread]
    beats: list[DramaticBeat]
    links: list[SetupPayoffLink]
    redundancy_groups: list[RedundancyGroup]
    speaker_blocks: list[SpeakerBlock]
    broll: list[BrollMarker]
    rhythm: RhythmProfile
    # editorial read
    arc_summary: str
    # every thread each unit belongs to, indexed by unit position; a unit may
    # be one story's payoff and another's setup, so membership is a list
    unit_threads: list[list[int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """JSON-serialisable view of the whole corpus.

        Every field is a primitive, list or dict, so the result round-trips
        through ``json.dumps`` without custom encoders. ``units`` are excluded
        from embedded copies: artifacts already carry the indices needed to
        resolve them.
        """
        d = asdict(self)
        # `rhythm.per_unit` is already a flat list of measurements; keep the
        # structure as-is — downstream agents need per-unit cadence by index.
        return d


# ── input normalisation ─────────────────────────────────────────────────────


def _finite(x: float, default: float = 0.0) -> float:
    """Clamp non-finite values to a default; never let NaN/inf reach the model."""
    try:
        if math.isnan(x) or math.isinf(x):
            return default
    except TypeError:
        return default
    return float(x)


def _normalise(segments: list[Segment], duration: float) -> tuple[list[Segment], float, str, list[str]]:
    """Defensive copies with clamped times, sorted by start.

    The input is never mutated. Degenerate members are repaired, not dropped:
    an empty-text segment still occupies source time and can bound a pause.
    """
    notes: list[str] = []
    out: list[Segment] = []
    for seg in segments:
        start = max(0.0, _finite(seg.start))
        end = _finite(seg.end)
        if end < start:
            notes.append(
                f"segment with end {seg.end:g} before start {seg.start:g} was "
                f"repaired by swapping its bounds"
            )
            start, end = end, start
        text = (seg.text or "").strip()
        words = [
            w for w in (seg.words or [])
            if w.text and _finite(w.start, -1.0) >= 0.0
            and _finite(w.end, -1.0) >= _finite(w.start, -1.0)
        ]
        copy = Segment(start=start, end=end, text=text, words=words)
        # Some ASR backends attach a speaker label that models.core.Segment does
        # not declare; carry it through so the block layer can use it. Read
        # defensively so a plain Segment never breaks this path.
        speaker = getattr(seg, "speaker", None)
        if speaker:
            copy.speaker = str(speaker).strip()  # type: ignore[attr-defined]
        out.append(copy)
    out.sort(key=lambda s: s.start)
    reordered = any(out[i].start > out[i + 1].start for i in range(len(out) - 1))
    # (sort is stable; the check above is only for the note)
    if reordered:
        notes.append("segments were not in source order and were sorted by start")

    dur = _finite(duration)
    max_end = max((s.end for s in out), default=0.0)
    if dur <= 0.0:
        dur = max_end
        source = "inferred_from_segments" if dur > 0.0 else "unknown"
    else:
        source = "given"
        if max_end > dur + 1e-6:
            notes.append(
                f"transcript extends to {max_end:g}s, beyond the stated "
                f"duration {duration:g}s; times are reported as transcribed "
                f"and the discrepancy is noted rather than silently clamped"
            )
    return out, dur, source, notes


def _segment_word_count(segments: list[Segment]) -> int:
    return sum(len(s.words) for s in segments)


# ── unit construction ──────────────────────────────────────────────────────


def _norm_text(t: str) -> str:
    return t.translate(str.maketrans("", "", _PUNCT)).strip()


def _align_sentence_times(segments: list[Segment]) -> dict[int, list[tuple[float, float]]]:
    """Word-anchored start/end for each sentence of each segment.

    The anchoring itself lives in ``story_units._segment_sentences``, which runs
    it at the source so the candidate scan inherits real bounds rather than this
    layer repairing them after the fact. What remains here is the report the
    corpus's own ``timing_source`` field needs: which segments were anchored and
    which fell back to character-proportional timing.

    Deterministic and deliberately conservative: when the count does not line up
    (punctuation-heavy ASR, empty words), the sentence keeps its proportional
    timing and ``timing_source`` reports the fallback.
    """
    by_segment: dict[int, list[tuple[float, float]]] = {}
    for si, seg in enumerate(segments):
        if not seg.words:
            continue
        sents = _all_sentences([seg])
        if not sents:
            continue
        # A sentence carrying ``word_anchored=False`` means the segment fell
        # back to proportional timing, so no entry is reported for it. This is
        # the same per-segment all-or-nothing rule the anchoring itself applies.
        if not all(sent.word_anchored for sent, _ in sents):
            continue
        by_segment[si] = [(sent.start, sent.end) for sent, _ in sents]
    return by_segment


def _first_index_of_segment(seq: list[tuple[Sentence, int]], seg_no: int) -> int:
    """Index just past the last sentence of segment `seg_no` in `seq`."""
    out = len(seq)
    for i, (_, si) in enumerate(seq):
        if si == seg_no:
            out = i + 1
    return out


def _build_units(
    segments: list[Segment], *, has_words: bool,
) -> tuple[list[CorpusUnit], list[list[Sentence]], list[float], list[float], list[list[int]], list[str]]:
    """Story units with word-anchored sentence timing where available.

    Returns the parallel arrays every downstream analysis consumes, so no
    analysis has to re-derive them (and none of them re-copies the text).
    """
    units = segment_story_units(segments)
    notes: list[str] = []
    if not units and segments:
        # Every passage was empty or unparseable as a story; fall back to
        # one unit per segment so the corpus still has a spine.
        notes.append(
            "no story units could be segmented (empty or punctuation-free "
            "text); falling back to one unit per segment"
        )
        units = [
            StoryUnit(
                start=seg.start, end=seg.end, segment_indices=[i],
                sentences=[Sentence(seg.text or "", seg.start, seg.end,
                                    word_anchored=False)]
                if seg.text else [],
            )
            for i, seg in enumerate(segments)
        ]

    aligned = _align_sentence_times(segments) if has_words else {}

    # Re-anchor sentence timing: the global source sentence sequence is exactly
    # the concatenation of per-segment sentences, so a single cursor over it
    # resolves every unit sentence in order.
    source_seq = _all_sentences(segments)
    aligned_seq: list[tuple[float, float]] = []
    for si, seg in enumerate(segments):
        times = aligned.get(si)
        if times is None:
            for _ in _all_sentences([seg]):
                aligned_seq.append(None)  # type: ignore[arg-type]
        else:
            aligned_seq.extend(times)
    cursor = 0
    corpus_units: list[CorpusUnit] = []
    all_sentences: list[list[Sentence]] = []
    starts: list[float] = []
    ends: list[float] = []
    seg_lists: list[list[int]] = []
    fallback_used = False
    for ui, u in enumerate(units):
        sents_out: list[Sentence] = []
        # Where this unit's sentences may be found in the global stream: from
        # the current cursor up to the end of the unit's *last* segment. A
        # sentence that repeats across two stories ("The clock failed." in the
        # GPS story and again in the lighthouse story) can then only match
        # inside the segment it belongs to, never an identical sentence in a
        # different part of the recording — so a reported timecode is always the
        # timecode of the right occurrence.
        seg_idx = set(u.segment_indices)
        seg_limit = max(seg_idx) + 1 if seg_idx else 0
        limit = min(len(source_seq), _first_index_of_segment(source_seq, seg_limit))
        for s in u.sentences:
            t = None
            while cursor < limit:
                src_sent, src_seg = source_seq[cursor]
                if src_seg not in seg_idx:
                    cursor += 1
                    continue
                if src_sent.text == s.text:
                    if cursor < len(aligned_seq) and aligned_seq[cursor] is not None:
                        t = aligned_seq[cursor]  # type: ignore[assignment]
                    cursor += 1
                    break
                # A mismatch inside the unit's own segments: marker-peeling or
                # boilerplate removal changed the text, so no timecode here can
                # be trusted for this sentence. Stop and keep the proportional
                # timing rather than skipping past sentences of this unit that
                # might still match.
                break
            if t is not None:
                # Unrounded when anchored: these are word timestamps, and
                # rounding them breaks ``sentence.start == word.start`` for
                # ``edit_plan`` (see ``story_units._segment_sentences``).
                s = Sentence(s.text, t[0], t[1], s.boilerplate,
                             word_anchored=s.word_anchored)
            else:
                fallback_used = True
            sents_out.append(s)
        # Advance past the remainder of this unit's segments so the next unit's
        # search starts at its own sentences.
        while cursor < len(source_seq) and source_seq[cursor][1] in seg_idx:
            cursor += 1
        # Advance past the remainder of this unit's segments so the next unit's
        # search starts at its own sentences.
        while cursor < len(source_seq) and source_seq[cursor][1] in seg_idx:
            cursor += 1
        text = " ".join(s.text for s in sents_out).strip()
        corpus_units.append(CorpusUnit(
            index=ui,
            start=u.start,
            end=u.end,
            segment_indices=list(u.segment_indices),
            text=text,
            sentence_count=len(sents_out),
            word_count=len(text.split()),
            coherence=u.coherence,
            boilerplate=u.boilerplate,
            timing_source="word_aligned" if has_words and not fallback_used
            else "character_proportional",
        ))
        all_sentences.append(sents_out)
        starts.append(u.start)
        ends.append(u.end)
        seg_lists.append(list(u.segment_indices))
    if fallback_used and has_words:
        notes.append(
            "some sentence timecodes could not be word-anchored (ASR text and "
            "word stream disagree); those sentences keep character-proportional "
            "timing"
        )
    return corpus_units, all_sentences, starts, ends, seg_lists, notes


# ── the constructor ────────────────────────────────────────────────────────


def build_corpus(
    segments: list[Segment],
    *,
    duration: float,
    language: str = "unknown",
    source: str | None = None,
) -> EditorialCorpus:
    """Build the corpus-level editorial representation of a transcript.

    Args:
        segments: ASR segments in any order; the input is never mutated.
        duration: total source duration in seconds. When non-positive, it is
            inferred from the last segment end and the choice is recorded in
            ``build.duration_source``.
        language: the source language as reported by the caller ("unknown" when
            not known). Script-range detection is additionally run on a bounded
            sample and reported in ``detected_languages``; the two are kept
            separate because a reported language is metadata and a detected one
            is evidence.
        source: an optional origin label (a path or URL) carried through to the
            report; never dereferenced.

    Returns:
        An ``EditorialCorpus``. Degenerate input (empty list, one segment, no
        word timestamps, no text) yields a valid corpus with empty analyses and
        notes explaining exactly what could not be inferred.

    Deterministic and pure; see the module docstring for the cost model.
    """
    norm_segments, dur, dur_source, notes = _normalise(segments, duration)
    has_words = _segment_word_count(norm_segments) > 0

    corpus_units, unit_sentences, starts, ends, seg_lists, unit_notes = _build_units(
        norm_segments, has_words=has_words,
    )
    notes.extend(unit_notes)

    unit_texts = [u.text for u in corpus_units]
    coherence = [u.coherence for u in corpus_units]
    n = len(corpus_units)

    # Language detection over a bounded sample — never the whole transcript.
    sample = " ".join(unit_texts[:64])[:4000]
    detected = detect_languages(sample) if sample.strip() else []

    topics: TopicResult = cluster_topics(
        unit_texts, starts, ends, seg_lists, duration=dur,
    )
    rhythm = analyze_rhythm(
        norm_segments, unit_sentences, starts, ends, duration=dur,
    )
    rhythm_refs = [
        UnitRhythmRef(unit_index=u.unit_index, rush=u.rush,
                      pre_pause=u.pre_pause, emphatic_pause=u.emphatic_pause)
        for u in rhythm.per_unit
    ]
    threads: ThreadResult = build_threads(
        unit_texts, unit_sentences, starts, ends, duration=dur,
        topic_of_unit=topics.unit_topic, rhythm=rhythm_refs,
    )
    redundancy = group_redundancy(unit_texts, starts, ends, coherence)
    blocks = derive_speaker_blocks(
        norm_segments, starts, ends, topics.unit_topic, rhythm,
    )

    # Cross-link the spine so any artifact resolves to any other by index.
    for u, tid in zip(corpus_units, threads.unit_thread):
        u.thread_id = tid
    for u, bid in zip(corpus_units, threads.unit_beat):
        u.beat_id = bid
    for u, gid in zip(corpus_units, redundancy.unit_group):
        u.redundancy_group_id = gid
    for u, count in redundancy.internal_restatement.items():
        if 0 <= u < n:
            corpus_units[u].restatement_count = count
    for u, tid in zip(corpus_units, topics.unit_topic):
        u.topic_id = tid
    for blk in blocks.blocks:
        for u in blk.unit_indices:
            if 0 <= u < n:
                corpus_units[u].speaker_block_id = blk.block_id

    if not has_words:
        notes.append(
            "no usable word timestamps: pause classification, speech rate and "
            "pause-derived B-roll markers are unavailable rather than estimated"
        )
    if not blocks.has_speaker_labels:
        notes.append(
            "no speaker labels in the transcript: speaker identity is not "
            "guessed; block boundaries are turn hypotheses from pause and "
            "subject-change evidence"
        )
    for u in topics.unassigned:
        notes.append(
            f"unit {u} carried no recoverable content terms and was left "
            f"unassigned rather than forced onto a neighbouring subject"
        )
    orphan = [u.index for u in corpus_units if u.thread_id is None]
    if orphan:
        notes.append(
            f"{len(orphan)} unit(s) have no inferable narrative relation "
            f"(e.g. {orphan[:5]}); reported as standalone passages"
        )

    fp = fingerprint(
        f"{language}|{dur_source}|"
        + "|".join(f"{s.start:g},{s.end:g},{s.text}" for s in norm_segments)
    )

    corpus = EditorialCorpus(
        source=source,
        language=language,
        detected_languages=detected,
        duration=dur,
        build=BuildInfo(
            schema=SCHEMA_VERSION,
            fingerprint=fp,
            unit_count=n,
            segment_count=len(norm_segments),
            duration_source=dur_source,
            note=f"built from {len(norm_segments)} segment(s) into {n} unit(s)",
        ),
        has_word_timestamps=has_words,
        has_speaker_labels=blocks.has_speaker_labels,
        units=corpus_units,
        topics=topics.topics,
        threads=threads.threads,
        beats=threads.beats,
        links=threads.links,
        redundancy_groups=redundancy.groups,
        speaker_blocks=blocks.blocks,
        broll=threads.broll,
        rhythm=rhythm,
        arc_summary=_arc_summary(corpus_units, threads, topics, redundancy, rhythm, dur, detected),
        unit_threads=threads.unit_threads,
        notes=notes,
    )
    return corpus


# ── editorial read ─────────────────────────────────────────────────────────


def _arc_summary(
    units: list[CorpusUnit],
    threads: ThreadResult,
    topics: TopicResult,
    redundancy,
    rhythm: RhythmProfile,
    duration: float,
    detected: list[str],
) -> str:
    """The editor's first-read paragraph, assembled only from computed facts.

    Every clause is traceable to a field; nothing here is a stylistic guess.
    """
    if not units:
        return ("Empty corpus: no segments were supplied, so no editorial "
                "structure exists. This is a valid result, not an error.")
    parts: list[str] = []
    mins = duration / 60.0 if duration > 0 else 0.0
    parts.append(
        f"{len(units)} coherent passage(s) over {mins:.1f} min "
        f"({len(topics.topics)} subject cluster(s), {len(threads.threads)} "
        f"narrative chain(s))"
    )
    recurring = [t for t in topics.topics if t.recurring]
    if recurring:
        spine = ", ".join(t.label for t in sorted(recurring, key=lambda t: -len(t.unit_indices))[:3])
        parts.append(
            f"recurring subject(s) — the spine(s) of this recording: {spine}"
        )
    else:
        parts.append(
            "no subject recurs after an interruption; this recording moves "
            "from topic to topic rather than developing one"
        )
    linked = [t for t in threads.threads if t.links]
    if linked:
        kinds = {}
        for lk in threads.links:
            kinds[lk.kind] = kinds.get(lk.kind, 0) + 1
        shape = ", ".join(f"{k}×{v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
        parts.append(f"{len(linked)} chain(s) with setup-to-payoff links ({shape})")
    peak = max(threads.beats, key=lambda b: b.intensity, default=None)
    if peak is not None and duration > 0:
        pos = 100.0 * peak.start / duration
        parts.append(
            f"highest dramatic intensity at {pos:.0f}% of runtime "
            f"({peak.kind}, intensity {peak.intensity:.2f})"
        )
    if redundancy.groups:
        cuttable = sum(len(g.members) - 1 for g in redundancy.groups)
        parts.append(
            f"{len(redundancy.groups)} redundancy group(s) with {cuttable} "
            f"passage(s) that could be removed without losing a point"
        )
    if rhythm.available:
        # ``available`` means word objects exist, not that any voiced time could
        # be measured — a transcript whose every word carries zero duration has
        # a rate of None, and that must read as unmeasured rather than crash.
        if rhythm.speech_rate_wpm is None:
            parts.append(
                f"word timing is present but no measurable voiced time, so the "
                f"speech rate is unmeasured ({rhythm.filler_count} filler "
                f"marker(s), {rhythm.false_start_count} false start(s), "
                f"{rhythm.incomplete_count} truncated passage(s))"
            )
        else:
            parts.append(
                f"speech rate {rhythm.speech_rate_wpm:.0f} wpm over voiced time "
                f"({rhythm.filler_count} filler marker(s), "
                f"{rhythm.false_start_count} false start(s), "
                f"{rhythm.incomplete_count} truncated passage(s))"
            )
    if detected:
        parts.append(f"scripts detected: {', '.join(detected)}")
    return ". ".join(parts) + "."
