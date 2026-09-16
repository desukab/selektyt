from __future__ import annotations

import re
from collections import Counter
from models.core import Candidate


_FILLER_TEXTS = frozenset({
    "um", "uh", "erm", "hmm", "hm", "you know", "i mean",
    "अर्थात", "मतलब", "यानी", "అంటే", "ఉమ్", "హ్మ్",
    "அதாவது", "ஹ்ம்", "ಕಂಬ", "এর",
})


def _is_filler(text: str) -> bool:
    t = " ".join(text.lower().split()).strip(" ,.!?;:-")
    return t in _FILLER_TEXTS


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

    intervals: list[tuple[float, float]] = []
    cur_start = max(c.start, words[0].start - keep_edge)
    prev_end = words[0].end
    for w in words[1:]:
        gap = max(0.0, w.start - prev_end)
        if gap >= min_pause:
            cut_end = min(c.end, prev_end + keep_pause)
            if cut_end - cur_start >= 0.45:
                intervals.append((cur_start, cut_end))
            cur_start = max(c.start, w.start - keep_edge)
        prev_end = max(prev_end, w.end)

    end = min(c.end, prev_end + keep_edge)
    if end - cur_start >= 0.45:
        intervals.append((cur_start, end))
    if not intervals:
        return [(c.start, c.end)]

    kept = sum(b - a for a, b in intervals)
    original = max(0.01, c.end - c.start)
    # If the edit would remove too much material, prefer the intact source story.
    if len(intervals) > 3 or kept / original < 0.72:
        return [(c.start, c.end)]
    return intervals


def filler_words(c: Candidate) -> list[str]:
    """Return conservatively recognized filler tokens for reporting/UI."""
    return [w.text for w in (c.words or []) if _is_filler(w.text)]


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
) -> dict:
    """Project-level pacing and candidate quality report.

    Includes: duration stats, distribution buckets, silence%, filler%,
    avg_words_per_second, context_failures, duplicate_overlap_rate,
    hook_latency stats, payoff_presence_rate.
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
        "avg_duration": avg_duration,
        "duration_distribution": buckets,
        "silence_pct": silence_pct,
        "filler_pct": filler_pct,
        "avg_words_per_second": avg_wps,
        "avg_hook_latency": avg_hook_latency,
        "payoff_presence_rate": payoff_rate,
        "duplicate_overlap_rate": overlap_rate,
    }
