from __future__ import annotations

from models.core import Candidate


def _is_filler(text: str) -> bool:
    t = " ".join(text.lower().split()).strip(" ,.!?;:-")
    return t in {"um", "uh", "erm", "hmm", "hm", "you know", "i mean", "अर्थात", "मतलब", "यानी", "అంటే", "ఉమ్", "హ్మ్", "அதாவது", "ஹ்ம்"}


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
