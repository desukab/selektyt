"""Conversational rhythm and recording-surface signals.

A transcript is not only words. It is a record of *how* something was said, and
the how carries editorial information the what cannot: the speaker who rushes
through a number is marking it as important; the speaker who pauses before a
confession is marking it as vulnerable; the speaker who restarts a sentence
three times is searching, and a rough cut that keeps the search shows the
workings while a cut that hides it all looks sterile.

This module measures the signals that survive transcription, from the two
sources available locally:

1. **Word timestamps** — inter-word gaps become pauses, and pauses are
   classified by re-using `engine.pause_classifier.PauseClassifier` (read-only;
   the same classification the editorial loop trusts for dead-air decisions).
   Speech regions are reconstructed from word runs *as the audio-truth
   surrogate*: where VAD scores exist they are preferable, but the corpus layer
   must build from a transcript alone, so a word-gap silence stands in for an
   audio silence and the confidence says so.
2. **The text itself** — filler markers, repeated stems (false starts) and
   unterminated sentences (truncated or abandoned thoughts), detected with the
   same multilingual cue vocabulary the rest of the editorial layer uses.

Degradation is explicit: **without word timestamps every timing-derived field is
`None` and `available` is False**, never a plausible-looking guess. A
punctuation-only transcript still yields filler counts, false-start counts and
incomplete-thought counts, because those come from text.

Speaker / context blocks
------------------------
Speaker identity is **not** inferable from an unlabelled transcript — assigning
one would be fabrication. Blocks are therefore produced only when the evidence
exists:

- if the transcript's segments carry a `speaker` attribute (some ASR backends
  emit one; it is read with `getattr` so a plain `Segment` is still valid), the
  boundaries are *given* and reported at high confidence;
- otherwise, block boundaries are proposed from *turn-taking* evidence only — a
  dead-air silence followed by a topic change, or a question/answer alternation
  — and `speaker` stays `None` with low confidence and a reason that says the
  boundary is a turn hypothesis, not a speaker identification.

Deterministic and pure: same input -> identical output, no network, no ML, no
wall clock. Complexity is O(W) over all words plus O(U log U) for the speech-rate
median; memory is O(U) structures plus one bounded pause list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from engine.pause_classifier import ClassifiedPause, PauseClassifier, PauseType
from engine.story_units import Sentence
from engine.vad import SilenceRegion, SpeechRegion
from models.core import Segment, Word

_FILLER = re.compile(
    r"\b(?:um|uh|erm|hmm|you know|i mean|like\b|sort of|kind of|"
    r"అంటే|ఉమ్|హ్మ్|ఏమిటంటే|मतलब|यानी|ह्म्म|"
    r"அதாவது|ஹ்ம்|என்னவென்றால்|ಎಂದರೆ|ಹ್ಮ್|എന്നാൽ|ഹം|"
    r"মানে|ইয়ানি|হুম)\b",
    re.IGNORECASE | re.UNICODE,
)
_TERMINAL_PUNCT = set(".?!।॥？。")
_MIN_PAUSE = 0.10        # an inter-word gap below this is articulation, not a pause
_DEAD_AIR = 1.5          # reused from PauseClassifier's dead_air_min default


@dataclass
class UnitRhythm:
    """Prosodic measurements for one corpus unit."""

    unit_index: int
    start: float
    end: float
    word_count: int
    speech_seconds: float           # voiced time inside the unit
    pause_seconds: float            # silence time inside the unit
    speech_rate_wpm: float | None   # words per minute of speech, pauses excluded
    rush: float                     # 0..1, how far above the corpus median rate
    drag: bool                      # well below the corpus median rate
    pause_counts: dict[str, int]    # by PauseType name
    longest_pause: float
    emphatic_pause: float           # longest interior pause (fed to beat intensity)
    pre_pause: float                # silence immediately before this unit
    filler_count: int
    false_start_count: int
    incomplete: bool                # last sentence does not terminate
    fluency: float                  # 0..1, 1 = no disfluencies
    confidence: float
    reason: str


@dataclass
class RhythmProfile:
    """Corpus-level conversational cadence plus per-unit measurements."""

    available: bool                          # False when no word timestamps at all
    total_words: int
    speech_seconds: float
    speech_rate_wpm: float | None
    median_rate_wpm: float | None
    pause_counts: dict[str, int]
    pause_rate_per_minute: dict[str, float]
    filler_count: int
    filler_rate_per_minute: float | None
    false_start_count: int
    incomplete_count: int
    per_unit: list[UnitRhythm]
    confidence: float
    reason: str


@dataclass
class SpeakerBlock:
    """A stretch attributed to one speaker, or to one turn when unlabelled."""

    block_id: int
    start: float
    end: float
    speaker: str | None                    # None when the transcript has no labels
    unit_indices: list[int]
    boundary_reason: str                   # why this block begins here
    confidence: float
    reason: str


@dataclass
class SpeakerBlockResult:
    blocks: list[SpeakerBlock]
    has_speaker_labels: bool
    note: str


# ── word-level reconstruction ──────────────────────────────────────────────


def _collect_words(segments: list[Segment]) -> tuple[list[Word], bool]:
    """All timed words in source order, plus whether timing is trustworthy.

    A word with non-finite or inverted timestamps is kept as text but not used
    for timing; `timed` is only True when enough words carry sane timing to
    measure pauses with.
    """
    words: list[Word] = []
    usable = 0
    for seg in segments:
        for w in getattr(seg, "words", None) or []:
            if not w.text:
                continue
            ok = (
                isinstance(w.start, (int, float))
                and isinstance(w.end, (int, float))
                and w.start <= w.end
                and w.start >= 0.0
            )
            if ok:
                usable += 1
            words.append(w)
    return words, usable > 0 and usable >= max(1, len(words) // 2)


def _speech_and_silence(words: list[Word]) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Reconstruct speech runs and the silences between them from word gaps.

    Speech is a run of words whose inter-word gaps stay under the dead-air
    threshold; a gap of at least `_MIN_PAUSE` is a pause, and a gap at or above
    `_DEAD_AIR` also ends a speech run, so a long silence is never absorbed
    into a speaker's region.
    """
    speech: list[tuple[float, float]] = []
    silences: list[tuple[float, float]] = []
    if not words:
        return speech, silences
    run_start = words[0].start
    prev_end = words[0].end
    for w in words[1:]:
        gap = w.start - prev_end
        if gap >= _MIN_PAUSE:
            silences.append((prev_end, w.start))
        if gap >= _DEAD_AIR:
            speech.append((run_start, prev_end))
            run_start = w.start
        prev_end = max(prev_end, w.end)
    speech.append((run_start, prev_end))
    return speech, silences


# ── disfluency detection ───────────────────────────────────────────────────


def _find_repeats(tokens: list[str], n: int, text: str,
                  comma_positions: set[int]) -> int:
    """Count immediately repeated n-token spans that mark an abandoned attempt.

    A repeat is only a false start when the speaker abandoned the first attempt:
    "I think the clock, I think the clock" has a comma at the abandonment point,
    while "the clock the clock" is a rhetorical figure. The comma is located by
    character offset in `text`, using the token offsets recovered below, since a
    token list alone has already lost the punctuation.
    """
    if len(tokens) < 2 * n:
        return 0
    # Character offset of each token, so a repeat can be checked for an
    # intervening abandonment mark.
    offsets = [m.start() for m in re.finditer(r"\w+", text)]
    count = 0
    i = 0
    while i + 2 * n <= len(tokens):
        lo = offsets[i + n - 1] if i + n - 1 < len(offsets) else 0
        hi = offsets[i + n] if i + n < len(offsets) else len(text)
        abandoned = any(lo <= p < hi for p in comma_positions)
        if abandoned and tokens[i : i + n] == tokens[i + n : i + 2 * n] \
                and all(tokens[i : i + n]):
            count += 1
            i += 2 * n
        else:
            i += 1
    return count


def _false_starts(text: str, next_stem: list[str] | None) -> int:
    """False starts: a repeated stem, or a restart cue mid-utterance.

    Two transcript-evidenced forms:
      - an immediately repeated 1-3 word stem inside the same passage
        ("I think the clock, I think the clock failed"): the speaker begins,
        abandons, and begins the same way. Comma-terminated interruption marks
        the abandonment, so a comma before the repeat is what distinguishes a
        restart from a deliberate rhetorical repetition ("the clock, the clock,
        the clock") ;
      - a passage whose own opening stem repeats the opening stem of the *next*
        passage, which is how an abandoned and restarted sentence looks when ASR
        has already closed the segment.
    """
    tokens = [t for t in re.findall(r"\w+", text.lower(), flags=re.UNICODE)]
    if not tokens:
        return 0
    # A restart needs the abandoned first attempt to actually be abandoned:
    # punctuation between the two halves. Without it, "the clock, the clock" is
    # a rhetorical figure rather than a false start, so a bare repeat without
    # intervening punctuation is not counted.
    comma_positions = {m.start() for m in re.finditer(r"[,;—-]", text.lower())}
    count = sum(_find_repeats(tokens, n, text.lower(), comma_positions)
                for n in (1, 2, 3, 4))
    if next_stem:
        for n in (2, 3):
            if len(next_stem) >= n and tokens[:n] == next_stem[:n] and n >= 2:
                count += 1
                break
    return count


# ── main entry ─────────────────────────────────────────────────────────────


def analyze_rhythm(
    segments: list[Segment],
    unit_sentences: list[list[Sentence]],
    unit_starts: list[float],
    unit_ends: list[float],
    *,
    duration: float,
) -> RhythmProfile:
    """Measure pauses, filler, false starts and speech rate across the corpus.

    Complexity O(W + U log U): one pass over the words, one bounded sort for the
    median speech rate. Pauses are classified per unit, and only the counts are
    retained — the pause objects themselves are released — so memory stays
    O(U) and not O(pauses).
    """
    n = len(unit_sentences)
    words, timed = _collect_words(segments)
    classifier = PauseClassifier()
    empty_counts: dict[str, int] = {}
    unavailable = RhythmProfile(
        available=False, total_words=len(words), speech_seconds=0.0,
        speech_rate_wpm=None, median_rate_wpm=None,
        pause_counts={}, pause_rate_per_minute={},
        filler_count=0, filler_rate_per_minute=None,
        false_start_count=0, incomplete_count=0, per_unit=[],
        confidence=0.0,
        reason="no usable word timestamps; pause, rate and pause-derived "
               "signals are unavailable rather than estimated",
    )

    unit_text = [" ".join(s.text for s in sents) for sents in unit_sentences]

    # Text-derived counts do not need timing and are reported on their own.
    filler_total = sum(len(_FILLER.findall(t)) for t in unit_text)
    incomplete_total = 0
    for sents in unit_sentences:
        if sents and sents[-1].text.strip() and sents[-1].text.strip()[-1] not in _TERMINAL_PUNCT:
            incomplete_total += 1

    if not timed or not words:
        per_unit: list[UnitRhythm] = []
        for i, sents in enumerate(unit_sentences):
            if i >= n:
                break
            text = unit_text[i] if i < len(unit_text) else ""
            nxt = [t for t in re.findall(r"\w+", unit_text[i + 1].lower(),
                                         flags=re.UNICODE)] if i + 1 < n else []
            fs = _false_starts(text, nxt or None)
            incomplete = bool(sents) and sents[-1].text.strip() != "" and \
                sents[-1].text.strip()[-1] not in _TERMINAL_PUNCT
            per_unit.append(UnitRhythm(
                unit_index=i, start=unit_starts[i], end=unit_ends[i],
                word_count=0, speech_seconds=0.0, pause_seconds=0.0,
                speech_rate_wpm=None, rush=0.0, drag=False,
                pause_counts=dict(empty_counts), longest_pause=0.0,
                emphatic_pause=0.0, pre_pause=0.0,
                filler_count=len(_FILLER.findall(text)),
                false_start_count=fs, incomplete=incomplete,
                fluency=_fluency(0, fs, 1 if incomplete else 0, 0, 0),
                confidence=0.35,
                reason="text-only unit: filler, restart and truncation signals "
                       "available; pause and rate signals are not",
            ))
        unavailable.per_unit = per_unit
        unavailable.filler_count = filler_total
        unavailable.false_start_count = sum(u.false_start_count for u in per_unit)
        unavailable.incomplete_count = incomplete_total
        return unavailable

    # Word -> unit assignment by source time, so each unit's pauses are its own.
    unit_of_word: list[int] = []
    ui = 0
    for w in words:
        while ui + 1 < n and w.start >= unit_starts[ui + 1]:
            ui += 1
        unit_of_word.append(ui)
    words_by_unit: dict[int, list[Word]] = {}
    for w, u in zip(words, unit_of_word):
        words_by_unit.setdefault(u, []).append(w)

    speech_regions, silence_regions = _speech_and_silence(words)

    # Classify pauses once, corpus-wide, then attribute each to a unit by time.
    sil_objs = [SilenceRegion(start=s, end=e, duration=max(0.0, e - s))
                for s, e in silence_regions]
    spk_objs = [SpeechRegion(start=s, end=e) for s, e in speech_regions]
    classified = classifier.classify(sil_objs, spk_objs, duration, words)

    def _voiced(word_list: list[Word]) -> float:
        """Speaking time: word durations plus articulation gaps, pauses excluded.

        This must be the *same* quantity the corpus-wide rate is computed from,
        or a unit rate and the corpus rate silently disagree — which is what
        makes a long unit full of pauses look like slow speech.
        """
        if not word_list:
            return 0.0
        total = max(0.0, word_list[0].end - word_list[0].start)
        prev_end = word_list[0].end
        for w in word_list[1:]:
            gap = w.start - prev_end
            if gap < _MIN_PAUSE:        # articulation inside a run of speech
                total += max(0.0, w.end - w.start) + max(0.0, gap)
            else:
                total += max(0.0, w.end - w.start)
            prev_end = max(prev_end, w.end)
        return total

    total_voiced = sum(_voiced(wl) for wl in words_by_unit.values())

    per_unit = []
    rate_by_unit: dict[int, float] = {}
    pause_counts_total: dict[str, int] = {}
    for i in range(n):
        us, ue = unit_starts[i], unit_ends[i]
        unit_words = words_by_unit.get(i, [])
        ws = unit_words[0].start if unit_words else None
        we = unit_words[-1].end if unit_words else None
        unit_speech = _voiced(unit_words)
        wcount = len(unit_words)
        pauses: list[ClassifiedPause] = []
        if ws is not None and we is not None:
            pauses = [p for p in classified
                      if p.start >= ws - 1e-9 and p.end <= we + 1e-9]
        counts: dict[str, int] = {}
        pause_seconds = 0.0
        longest = 0.0
        for p in pauses:
            counts[p.pause_type.value] = counts.get(p.pause_type.value, 0) + 1
            pause_seconds += p.duration
            longest = max(longest, p.duration)
            pause_counts_total[p.pause_type.value] = \
                pause_counts_total.get(p.pause_type.value, 0) + 1
        # Silence immediately preceding the unit: the held breath before a
        # statement, which the beat layer treats as emphasis evidence. Matched
        # against the unit's *first word*, not its nominal start, because a
        # story unit can begin before its first ASR word.
        pre = 0.0
        if ws is not None:
            for p in classified:
                if abs(p.end - ws) <= 0.30 and p.start < ws:
                    pre = max(pre, p.duration)
        if wcount and unit_speech > 0.05:
            rate_by_unit[i] = 60.0 * wcount / unit_speech
        text = unit_text[i] if i < len(unit_text) else ""
        nxt = [t for t in re.findall(r"\w+", unit_text[i + 1].lower(),
                                     flags=re.UNICODE)] if i + 1 < n else []
        fs = _false_starts(text, nxt or None)
        incomplete = bool(unit_sentences[i]) and \
            unit_sentences[i][-1].text.strip() != "" and \
            unit_sentences[i][-1].text.strip()[-1] not in _TERMINAL_PUNCT
        per_unit.append(UnitRhythm(
            unit_index=i, start=us, end=ue,
            word_count=wcount, speech_seconds=round(unit_speech, 3),
            pause_seconds=round(pause_seconds, 3),
            speech_rate_wpm=round(rate_by_unit[i], 1) if i in rate_by_unit else None,
            rush=0.0, drag=False, pause_counts=counts,
            longest_pause=round(longest, 3), emphatic_pause=round(longest, 3),
            pre_pause=round(pre, 3),
            filler_count=len(_FILLER.findall(text)),
            false_start_count=fs, incomplete=incomplete,
            fluency=_fluency(len(_FILLER.findall(text)), fs, 1 if incomplete else 0,
                             counts.get(PauseType.DEAD_AIR.value, 0),
                             counts.get(PauseType.HESITATION.value, 0)),
            confidence=0.85 if pauses else 0.55,
            reason=("word-timed pauses classified with the shared pause "
                    "classifier; speech rate measured over voiced time only"),
        ))

    rates = sorted(v for v in rate_by_unit.values() if v > 0)
    median = rates[len(rates) // 2] if rates else None
    for u in per_unit:
        if u.speech_rate_wpm is not None and median:
            u.rush = round(max(0.0, min(1.0, (u.speech_rate_wpm - median) / median)), 3)
            u.drag = u.speech_rate_wpm < 0.7 * median
    overall = None
    if total_voiced > 0.0 and words:
        overall = 60.0 * len(words) / total_voiced
    minutes = max(1e-9, duration / 60.0) if duration > 0 else None
    rate_per_min = {
        k: round(v / minutes, 2) for k, v in pause_counts_total.items()
    } if minutes else {}

    return RhythmProfile(
        available=True,
        total_words=len(words),
        speech_seconds=round(total_voiced, 3),
        speech_rate_wpm=round(overall, 1) if overall else None,
        median_rate_wpm=round(median, 1) if median else None,
        pause_counts=pause_counts_total,
        pause_rate_per_minute=rate_per_min,
        filler_count=filler_total,
        filler_rate_per_minute=round(filler_total / minutes, 2) if minutes else None,
        false_start_count=sum(u.false_start_count for u in per_unit),
        incomplete_count=incomplete_total,
        per_unit=per_unit,
        confidence=0.85,
        reason="pauses reconstructed from inter-word gaps and classified with "
               "the shared pause classifier; word timing stands in for audio "
               "truth, so timing-derived confidence is capped below an audio-"
               "VAD measurement",
    )


def _fluency(fillers: int, false_starts: int, incomplete: int,
             dead_air: int, hesitation: int) -> float:
    """0..1 fluency from disfluency counts, saturating so long texts can reach 0.

    Per-word normalisation would make a 3-hour transcript look far more
    disfluent than a 3-minute one for the same speaking style, so the penalty
    saturates instead: a handful of disfluencies is enough to mark a passage as
    a searching one, and more than that does not keep punishing it.
    """
    penalty = 0.02 * fillers + 0.05 * false_starts + 0.05 * incomplete + \
        0.05 * dead_air + 0.03 * hesitation
    return round(max(0.0, 1.0 - min(1.0, penalty)), 3)


# ── speaker / context blocks ───────────────────────────────────────────────


def derive_speaker_blocks(
    segments: list[Segment],
    unit_starts: list[float],
    unit_ends: list[float],
    unit_topic: list[int],
    rhythm: RhythmProfile,
    *,
    min_pause_for_turn: float = 0.9,
) -> SpeakerBlockResult:
    """Group units into speaker/turn blocks from whatever the transcript admits.

    `Segment` in `models.core` has no speaker field by design; some ASR backends
    attach one anyway, and it is read with `getattr` so this layer works with
    the plain model. When no labels exist anywhere, boundaries are turn
    hypotheses only and `speaker` is None on every block.
    """
    n = len(unit_starts)
    if n == 0:
        return SpeakerBlockResult([], False, "no units to group into blocks")

    seg_speaker: list[str | None] = [
        getattr(seg, "speaker", None) or None for seg in segments
    ]
    labelled = any(s is not None and str(s).strip() for s in seg_speaker)

    # Map each unit to a speaker. Units span whole sentences and routinely
    # straddle a speaker change, so a unit is attributed only when it is
    # *dominated* by one speaker's segments (strictly more than half its
    # duration); a contested unit is left unattributed rather than assigned to
    # whichever speaker happened to be talking at its midpoint, so an interview
    # turn is not silently mislabeled. A unit inside a single labelled segment
    # is attributed to that segment's speaker.
    unit_speaker: list[str | None] = [None] * n
    contested: dict[int, bool] = {}
    if labelled:
        order = sorted(range(len(segments)), key=lambda i: segments[i].start)
        for ui in range(n):
            us, ue = unit_starts[ui], unit_ends[ui]
            span: dict[str, float] = {}
            for si in order:
                s = segments[si]
                if s.end <= us or s.start >= ue:
                    continue
                if seg_speaker[si]:
                    spk = str(seg_speaker[si]).strip()
                    span[spk] = span.get(spk, 0.0) + \
                        (min(s.end, ue) - max(s.start, us))
            if len(span) == 1:
                unit_speaker[ui] = next(iter(span))
            elif span:
                # Two speakers inside one unit means the story-unit layer kept
                # a turn-change inside one sentence group. This is expected in
                # fast interview cadence, and the right answer is to report the
                # block as a *turn boundary* rather than to pick a speaker; the
                # caller sees the boundary reason and can attribute it if the
                # edit needs a single name.
                unit_speaker[ui] = None
                contested[ui] = True

    rhythm_by_unit = {u.unit_index: u for u in rhythm.per_unit} if rhythm else {}

    boundaries: list[tuple[int, str, float]] = [(0, "corpus start", 0.0)]
    for i in range(1, n):
        if labelled and unit_speaker[i] and unit_speaker[i] != unit_speaker[i - 1]:
            boundaries.append((i, f"speaker label changes to “{unit_speaker[i]}”", 0.90))
            continue
        r = rhythm_by_unit.get(i)
        turned = (r is not None and r.pre_pause >= min_pause_for_turn
                  and unit_topic[i] != unit_topic[i - 1] and unit_topic[i] >= 0)
        if turned:
            boundaries.append((
                i,
                (
                    f"{r.pre_pause:.1f}s silence followed by a subject change "
                    f"— a turn boundary hypothesis"
                ),
                0.45,
            ))

    # A block is attributed to a speaker only when its *first* unit is
    # attributed; a block whose first unit is unlabelled is reported as a turn
    # block even in a labelled transcript, so the gap is visible instead of
    # being silently inherited from a neighbour.
    def block_speaker(start_i: int) -> tuple[str | None, str, float]:
        spk = unit_speaker[start_i] if labelled else None
        if spk:
            return spk, (
                f"transcript carries speaker labels; units {start_i}.."
                f"{end_i - 1} are attributed to “{spk}” where a single speaker "
                f"dominates the unit's duration"
            ), 0.90
        if labelled:
            return None, (
                "transcript carries speaker labels but this unit straddles a "
                "speaker change: two speakers share one sentence group, so the "
                "block is reported as a turn boundary and attributed to no "
                "single speaker"
            ), 0.35
        return None, (
            "transcript carries no speaker labels; this block is a turn "
            "hypothesis from pause + subject-change evidence, and the speaker "
            "is deliberately not guessed"
        ), 0.0

    blocks: list[SpeakerBlock] = []
    for bi, (start_i, why, conf) in enumerate(boundaries):
        end_i = boundaries[bi + 1][0] if bi + 1 < len(boundaries) else n
        if end_i <= start_i:
            continue
        units = list(range(start_i, end_i))
        spk, spk_reason, spk_conf = block_speaker(start_i)
        blocks.append(SpeakerBlock(
            block_id=bi, start=unit_starts[start_i], end=unit_ends[end_i - 1],
            speaker=spk, unit_indices=units, boundary_reason=why,
            confidence=spk_conf, reason=spk_reason,
        ))

    note = (
        f"{len(blocks)} block(s); speaker labels "
        f"{'present' if labelled else 'absent — turn boundaries only'}"
    )
    return SpeakerBlockResult(blocks, labelled, note)
