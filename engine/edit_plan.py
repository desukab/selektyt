from __future__ import annotations
from models.core import Candidate


def retention_intervals(c: Candidate, min_pause: float = 0.55, keep_edge: float = 0.06) -> list[tuple[float, float]]:
    """Conservatively remove long speech gaps while preserving natural rhythm."""
    if not c.text.strip():
        return []
    words = c.words or []
    if not words:
        return [(c.start + keep_edge, max(c.start + keep_edge, c.end - keep_edge))]
    intervals: list[tuple[float, float]] = []
    cur_start = max(c.start, words[0].start - keep_edge)
    prev_end = words[0].end
    for w in words[1:]:
        gap = w.start - prev_end
        if gap >= min_pause:
            end = min(c.end, prev_end + keep_edge)
            if end - cur_start >= 0.35:
                intervals.append((cur_start, end))
            cur_start = max(c.start, w.start - keep_edge)
        prev_end = max(prev_end, w.end)
    end = min(c.end, prev_end + keep_edge)
    if end - cur_start >= 0.35:
        intervals.append((cur_start, end))
    return intervals or [(c.start, c.end)]
