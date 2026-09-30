from __future__ import annotations

import re
from collections import Counter
from models.core import Candidate, Segment, Word

# A bound within this of a word's end is on the boundary, not inside the word.
# Sentence bounds are estimated to character span while word timing is
# measured, so the two routinely disagree in the last ulp (see
# ``_word_aligned`` for the shipped case it caused). Kept as a twin of
# ``editorial._BOUND_EPS`` rather than imported, because ``editorial`` imports
# this module.
_BOUND_EPS = 1e-6


_FILLER_TEXTS = frozenset({
    "um", "uh", "erm", "hmm", "hm", "you know", "i mean",
    "अर्थात", "मतलब", "यानी", "అంటే", "ఉమ్", "హ్మ్",
    "அதாவது", "ஹ்ம்", "ಕಂಬ", "এর",
})


def _is_filler(text: str) -> bool:
    t = " ".join(text.lower().split()).strip(" ,.!?;:-")
    return t in _FILLER_TEXTS


def _word_aligned(t: float, words: list[Word]) -> float:
    """Move ``t`` back to a word boundary when it falls inside a word.

    A boundary inside a word cuts the speaker mid-word on one side of the cut:
    the deliverable opens or closes on a partial syllable. Selection bounds are
    chosen for content, not for phonetics, so a boundary that lands inside a
    word is moved back to that word's start. For a clip start that keeps the
    whole opening word; for a clip end it drops the partial word, which belongs
    to whatever the speaker said next rather than to this story.

    Measured on a real selection, a candidate ending 235.237 s clipped 143 ms
    off a word spanning 235.220-235.380 s, and the shipped render cut the
    speaker mid-word. This is what prevents that.

    A bound sitting *on* a word's end is not inside it, but the comparison
    below says it is when the two disagree in the last ulp: sentence bounds are
    estimated proportionally to character span while word timing is measured,
    so on the committed pentagon source a sentence starting at 364.84 met a
    word ending at 364.84000000000003. Snapping there pulled the bound 340 ms
    backward and made the clip open on the previous sentence's last word. A
    bound within ``_BOUND_EPS`` of a word's end is a boundary and is left
    alone; the two functions must agree, because ``editorial._words_in``
    decides whether a word is in the clip at all and this decides where its
    bound then sits.
    """
    for w in words:
        if w.start < t < w.end:
            if w.end - t < _BOUND_EPS:
                continue
            return w.start
    return t


def join_words(words: list[Word]) -> str:
    """Rebuild readable text from whisper word tokens.

    faster-whisper emits ``' has'`` rather than ``'has'``, and the leading
    space is load-bearing: a hyphen token is ``'-lived'``, which has to glue
    onto the word before it (``'short' + '-lived'`` -> ``'short-lived'``), and
    a number splits as ``'1' + ',024'``. Concatenating raw tokens is therefore
    right for every token after the first, and wrong for exactly one — the
    first token of a segment carries no leading space, so when such a token is
    pulled in behind a word the two glue together (``'Syria.' + 'has'``). A
    separator is inserted only where a word actually starts: a token beginning
    with a letter or digit, following one that does not end in a space.
    Punctuation-initial tokens still glue.

    This is the *only* correct way to turn word tokens back into text, so every
    emitter that builds a caption, a content pack or a sub-span from words must
    go through here. The naive ``' '.join(w.text.strip() for w in words)``
    re-corrupts exactly what ``align_span`` repaired: it strips the leading
    space that carries the gluing, so the shipped captions read
    ``"This is about the 1 ,024"`` while the transcript they caption reads
    ``"1,024"``. A caption track that disagrees with its own transcript is not
    a cosmetic defect — it is the deliverable being wrong.

    ``words`` may be any sequence of objects carrying a ``text`` attribute:
    ``Word`` and ``timeline_captions.TimelineWord`` both meet that, and both
    are joined by the same rule so a per-candidate cue and a timeline cue cut
    from the same speech render identical text.
    """
    parts: list[str] = []
    for w in words:
        if parts and not parts[-1][-1:].isspace() and re.match(r"\w", w.text):
            parts.append(" ")
        parts.append(w.text)
    return " ".join("".join(parts).split())


def align_span(start: float, end: float, words: list[Word], text: str) -> tuple[float, float, str, list[Word]]:
    """Make a span's bounds, text and word list describe the same clip.

    Returns ``(start, end, text, words)``, all four agreeing. A candidate's
    bounds are estimated proportionally to character span (see
    ``story_units._segment_sentences``), not read off word timing, so a bound
    can land inside a word. The word list, by contrast, is real timing, and
    ``retention_intervals`` snaps to it. A candidate built on an estimated
    bound therefore ships a clip whose opening or closing word is absent from
    its own ``text`` — the content pack's hook then describes a clip that is
    not the rendered one, and ``editorial_audit``'s caption-vs-text check
    fires on a real desync rather than a false positive.

    Snapping both bounds here and rebuilding the text from the words kept
    inside them makes the candidate identical to the interval that ships:
    ``retention_intervals`` then sees already-aligned bounds and its snap is a
    no-op rather than a repair. The word list is returned for the same reason:
    a caller that keeps the list it was handed while adopting the snapped
    bounds ships a clip whose words disagree with its own text, and every
    emitter downstream of the candidate burns that disagreement.

    Measured on the committed corpus, 3 of 27 candidates with word timing had
    a text that did not agree with the words the shipped captions are built
    from: one opened on a word its text omitted, one closed inside a word its
    text included, and one carried a period merged into a word that the words
    hold as its own token. ``align_span`` corrects those three and leaves the
    other twenty-four byte-identical.
    """
    if not words or end <= start:
        return start, end, text, []
    a = _word_aligned(start, words)
    b = _word_aligned(end, words)
    if b <= a:
        return start, end, text, []
    kept = [w for w in words if w.start >= a - _BOUND_EPS and w.end <= b + _BOUND_EPS]
    if not kept:
        return start, end, text, []
    # ``join_words`` is the only correct way to rebuild text from whisper
    # tokens: the naive ``" ".join(w.text.strip() ...)`` strips the leading
    # space that carries the gluing and re-corrupts what the snap just fixed.
    # See its docstring for why the separator is inserted only where a word
    # actually starts.
    rebuilt = join_words(kept)
    if not rebuilt:
        return start, end, text, []
    # Rebuilding is only warranted when the kept words are not the words the
    # text already carries. On the committed corpus that is three candidates
    # in 27 — two that closed inside a word the text kept, and one that opened
    # on a word the text omitted. A fourth differed only in where a lone
    # punctuation token picked up its space, which is a gluing artefact rather
    # than a content change, so its text is left as the segment produced it.
    # The bounds are left untouched for the same reason, and the returned word
    # list is the words inside *those* bounds rather than the snapped ones.
    key = lambda s: [t.strip(",.!?;:\"'") for t in s.split()]
    if key(rebuilt) == key(text):
        return start, end, text, \
            [w for w in words if w.start >= start - _BOUND_EPS
             and w.end <= end + _BOUND_EPS]
    return a, b, rebuilt, kept


def retention_intervals(c: Candidate, min_pause: float = 0.70, keep_pause: float = 0.18, keep_edge: float = 0.06) -> list[tuple[float, float]]:
    """Build conservative source intervals using actual word timestamps.

    Cuts occur only between words. Long pauses are shortened rather than deleted,
    preserving a small breath so the result does not become an aggressive jump-cut reel.
    """
    if not c.text.strip() or c.end <= c.start:
        return []
    words = sorted(c.words or [], key=lambda w: (w.start, w.end))
    if len(words) < 2:
        return [(c.start, c.end)]

    # Both ends of the selection are snapped to word boundaries before the
    # intervals are built, so every boundary the loop emits is word-aligned.
    # No clamp here: a start that snaps back moves *earlier* by design, to keep
    # the opening word whole, so a max() against c.start would undo the snap.
    sel_start = _word_aligned(c.start, words)
    sel_end = _word_aligned(c.end, words)
    if sel_end <= sel_start:
        return [(c.start, c.end)]

    intervals: list[tuple[float, float]] = []
    cur_start = max(sel_start, words[0].start - keep_edge)
    prev_end = words[0].end
    for w in words[1:]:
        gap = max(0.0, w.start - prev_end)
        if gap >= min_pause:
            cut_end = min(sel_end, prev_end + keep_pause)
            if cut_end - cur_start >= 0.45:
                intervals.append((cur_start, cut_end))
            cur_start = max(sel_start, w.start - keep_edge)
        prev_end = max(prev_end, w.end)

    end = min(sel_end, prev_end + keep_edge)
    if end - cur_start >= 0.45:
        intervals.append((cur_start, end))
    if not intervals:
        return [(sel_start, sel_end)]

    kept = sum(b - a for a, b in intervals)
    original = max(0.01, sel_end - sel_start)
    # If the edit would remove too much material, prefer the intact source story.
    if len(intervals) > 3 or kept / original < 0.72:
        return [(sel_start, sel_end)]
    return intervals


def pacing_report(c: Candidate, truth: dict | None = None) -> dict:
    """Per-candidate pacing diagnostics.

    Returns a dict with: words_per_second, total_words, long_pauses,
    silence_fraction, filler_pct, natural_rhythm, edit_ratio,
    hook_latency, payoff_presence, duration, and when VAD `truth` is
    supplied, VAD-enhanced fields: vad_speech_ratio, vad_dead_air_ratio.
    """
    duration = max(0.01, c.end - c.start)
    words = c.words or []
    total_words = len(re.findall(r"\w+", c.text, re.UNICODE))
    words_per_second = round(total_words / duration, 2)

    # Long pauses (>= 0.7s)
    long_pauses = 0
    silence = 0.0
    sorted_words = sorted(words, key=lambda w: w.start) if words else []
    for i in range(1, len(sorted_words)):
        gap = sorted_words[i].start - sorted_words[i - 1].end
        if gap >= 0.7:
            long_pauses += 1
            silence += gap
    silence_fraction = round(min(1.0, silence / max(0.01, duration)), 3)

    # Filler percentage
    filler_count = sum(1 for w in words if _is_filler(w.text))
    filler_pct = round(filler_count / max(1, total_words) * 100, 1)

    # Natural rhythm: words_per_second in [1.8, 3.2] is typical speech
    natural_rhythm = 1.8 <= words_per_second <= 3.2

    # Edit ratio: if we already computed retention intervals
    from engine.edit_plan import retention_intervals as _ri
    try:
        kept = sum(b - a for a, b in _ri(c))
    except Exception:
        kept = duration
    edit_ratio = round(min(1.0, kept / max(0.01, duration)), 3)

    # Hook latency: time from clip start to first word matching hook pattern
    hook_latency = None
    if words:
        for w in words:
            if re.search(r"\b(?:why|how|what|the|first|problem|secret|actually|ఎందుకు|ఎలా|क्यों|कैसे|ஏன்|எப்படி|ಕೇಂದು|কেন)\b", w.text, re.I):
                hook_latency = round(max(0.0, w.start - c.start), 2)
                break

    # Payoff presence: clip ends with terminal punctuation and has substance
    payoff_presence = bool(re.search(r"[.!?।॥？！]$", c.text.strip())) and total_words >= 15

    result: dict = {
        "words_per_second": words_per_second,
        "total_words": total_words,
        "long_pauses": long_pauses,
        "silence_fraction": silence_fraction,
        "filler_pct": filler_pct,
        "natural_rhythm": natural_rhythm,
        "edit_ratio": edit_ratio,
        "hook_latency": hook_latency,
        "payoff_presence": payoff_presence,
        "duration": round(duration, 2),
    }

    if truth:
        result.update(_vad_pacing_fields(truth))
    return result


def _vad_pacing_fields(truth) -> dict:
    """VAD-enhanced pacing fields (speech ratio, dead-air ratio, spike count).

    Expects an AudioTruth-like object with vad_speech, vad_silence, and
    pauses (with a `pause_type` str-compareable to 'dead_air'). All values
    computed from VAD regions; Whisper words remain untouched.
    """
    duration = max(0.01, getattr(truth, "duration", 0.0))
    speech_secs = sum(s.end - s.start for s in truth.vad_speech)
    dead_secs = sum(
        p.duration for p in truth.pauses
        if getattr(p.pause_type, "value", p.pause_type) == "dead_air"
    ) if truth.pauses else 0.0
    return {
        "vad_speech_ratio": round(speech_secs / duration, 3),
        "vad_dead_air_ratio": round(dead_secs / duration, 3),
        "vad_speech_regions": len(truth.vad_speech),
    }


def aggregate_pacing_report(
    selected: list[Candidate],
    rejected: list[Candidate],
    context_guard_rejections: int,
    truncated_by_limit: int = 0,
) -> dict:
    """Project-level pacing and candidate quality report.

    Includes: duration stats, distribution buckets, silence%, filler%,
    avg_words_per_second, context_failures, duplicate_overlap_rate,
    hook_latency stats, payoff_presence_rate.

    ``context_guard_rejections`` and ``truncated_by_limit`` are reported
    separately because they mean different things to an editor: the first is
    an editorial judgment the editor can challenge, the second is only the
    ``--limit`` budget — a clip truncated by budget is not a clip the engine
    judged unusable. Callers that still pass a single combined count (legacy
    signature) get ``truncated_by_limit`` defaulting to 0, so nothing breaks.
    """
    all_candidates = selected + rejected
    if not all_candidates:
        return {"total_candidates": 0}

    durations = [c.end - c.start for c in all_candidates]
    reports = [pacing_report(c) for c in all_candidates]

    # Duration distribution
    buckets = {"<15s": 0, "15-30s": 0, "30-45s": 0, "45-55s": 0}
    for d in durations:
        if d < 15:
            buckets["<15s"] += 1
        elif d < 30:
            buckets["15-30s"] += 1
        elif d < 45:
            buckets["30-45s"] += 1
        else:
            buckets["45-55s"] += 1

    avg_duration = round(sum(durations) / len(durations), 2)
    silence_pct = round(sum(r["silence_fraction"] for r in reports) / len(reports) * 100, 1)
    filler_pct = round(sum(r["filler_pct"] for r in reports) / len(reports), 1)
    avg_wps = round(sum(r["words_per_second"] for r in reports) / len(reports), 2)

    hook_latencies = [r["hook_latency"] for r in reports if r["hook_latency"] is not None]
    avg_hook_latency = round(sum(hook_latencies) / len(hook_latencies), 2) if hook_latencies else None
    payoff_rate = round(sum(1 for r in reports if r["payoff_presence"]) / len(reports), 2)

    # Overlap / similarity rate among rejected candidates
    if len(rejected) > 1:
        from engine.editorial import _similarity
        dup_count = 0
        for i, a in enumerate(rejected):
            for b in rejected[i + 1:]:
                if _similarity(a.text, b.text) >= 0.78:
                    dup_count += 1
        overlap_rate = round(dup_count / max(1, len(rejected) * (len(rejected) - 1) / 2), 3)
    else:
        overlap_rate = 0.0

    return {
        "total_candidates": len(all_candidates),
        "selected_count": len(selected),
        "rejected_count": len(rejected),
        "context_guard_rejections": context_guard_rejections,
        # Candidates that passed the gate but lost to --limit. Not a rejection:
        # these are the editor's bench, available in the ranked set.
        "truncated_by_limit": truncated_by_limit,
        "avg_duration": avg_duration,
        "duration_distribution": buckets,
        "silence_pct": silence_pct,
        "filler_pct": filler_pct,
        "avg_words_per_second": avg_wps,
        "avg_hook_latency": avg_hook_latency,
        "payoff_presence_rate": payoff_rate,
        "duplicate_overlap_rate": overlap_rate,
    }
