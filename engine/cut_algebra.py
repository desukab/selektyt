"""Deterministic cut/interval algebra — single source of truth for all edit boundaries.

Every function is pure, deterministic, and operates on sorted float intervals.
No external dependencies. Used by: jumpcut, FCPXML, renderer, multi-track, QA.
"""
from __future__ import annotations

from collections import namedtuple

# Canonical typed interval: ("keep"|"remove", start, end). This is the shape
# the Rust core's EditCut produces and every exporter consumes.
CutSpan = namedtuple("CutSpan", ["kind", "start", "end"])


def cutlist_from_intervals(
    intervals: list[tuple[float, float]],
    source_duration: float,
    pad: float = 0.3,
) -> list[CutSpan]:
    """AutoCut-exact VAD→CutList inversion (parity with `CutList::from_speech_segments`).

    Pipeline, in the same order as AutoCut:
      1. pad every keep by `pad` on both sides
      2. clamp into [0, source_duration]
      3. drop degenerate intervals (end <= start)
      4. sort by start
      5. merge when the next start `<=` the previous end (touching intervals
         merge too)
      6. tile the gaps as Remove — the result covers [0, source_duration].

    Pure and deterministic; the renderer/export path uses this shape directly.
    """
    keeps = sorted(
        (
            (max(0.0, s - pad), min(source_duration, e + pad))
            for s, e in intervals
            if min(source_duration, e + pad) > max(0.0, s - pad)
        ),
        key=lambda iv: iv[0],
    )

    merged: list[tuple[float, float]] = []
    for s, e in keeps:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    spans: list[CutSpan] = []
    cursor = 0.0
    for s, e in merged:
        if s > cursor:
            spans.append(CutSpan("remove", cursor, s))
        spans.append(CutSpan("keep", s, e))
        cursor = e
    if cursor < source_duration:
        spans.append(CutSpan("remove", cursor, source_duration))
    return spans


def resolve_for_export(spans: list[CutSpan], coalesce: float = 1e-6) -> list[CutSpan]:
    """Coalesce adjacent same-kind spans closer than `coalesce` (AutoCut
    `resolveForExport`). Disabled-keep handling is a frontend concept; the
    Python editor represents those as `remove` spans already."""
    if not spans:
        return []
    out: list[CutSpan] = [spans[0]]
    for span in spans[1:]:
        last = out[-1]
        if (
            last.kind == span.kind
            and abs(last.end - span.start) < coalesce
        ):
            out[-1] = CutSpan(last.kind, last.start, max(last.end, span.end))
        else:
            out.append(span)
    return out


def merge_intervals(intervals: list[tuple[float, float]], gap: float = 0.0) -> list[tuple[float, float]]:
    """Merge overlapping or adjacent intervals.

    Args:
        intervals: List of (start, end) pairs. May be unsorted.
        gap: Maximum gap (seconds) between intervals to merge them.
    Returns:
        Sorted, merged, non-overlapping intervals.
    """
    if not intervals:
        return []
    pairs = sorted((min(a, b), max(a, b)) for a, b in intervals)
    merged: list[tuple[float, float]] = [pairs[0]]
    for start, end in pairs[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + gap:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(
    base: list[tuple[float, float]], remove: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Subtract remove intervals from base intervals.

    Args:
        base: Intervals to keep from.
        remove: Intervals to remove.
    Returns:
        Remaining intervals (may be empty).
    """
    if not base or not remove:
        return list(base)
    result = list(merge_intervals(base))
    for rm_start, rm_end in merge_intervals(remove):
        new_result: list[tuple[float, float]] = []
        for b_start, b_end in result:
            if rm_end <= b_start or rm_start >= b_end:
                new_result.append((b_start, b_end))
            else:
                if rm_start > b_start:
                    new_result.append((b_start, rm_start))
                if rm_end < b_end:
                    new_result.append((rm_end, b_end))
        result = new_result
    return result


def pad_intervals(
    intervals: list[tuple[float, float]],
    pad: float,
    bounds: tuple[float, float] | None = None,
) -> list[tuple[float, float]]:
    """Extend (positive pad) or shrink (negative pad) intervals.

    Args:
        intervals: Intervals to pad.
        pad: Seconds to add (positive) or subtract (negative) from each edge.
        bounds: Optional (min, max) to clamp results to.
    Returns:
        Padded intervals, clamped to bounds if provided.
    """
    lo = bounds[0] if bounds else float("-inf")
    hi = bounds[1] if bounds else float("inf")
    result = []
    for start, end in intervals:
        s = max(lo, min(hi, start - pad)) if pad > 0 else min(hi, max(lo, start - pad))
        e = min(hi, max(lo, end + pad)) if pad > 0 else max(lo, min(hi, end + pad))
        if s < e:
            result.append((s, e))
    return result


def trim_intervals(intervals: list[tuple[float, float]], trim: float) -> list[tuple[float, float]]:
    """Trim edges of intervals (positive = inward, negative = outward).

    Args:
        trim: Positive trims inward, negative extends outward.
    Returns:
        Trimmed intervals, dropping any that collapse.
    """
    result = []
    for start, end in intervals:
        s = start + trim
        e = end - trim
        if s < e:
            result.append((s, e))
    return result


def split_intervals(
    intervals: list[tuple[float, float]], points: list[float]
) -> list[tuple[float, float]]:
    """Split intervals at specified time points.

    Args:
        points: Time points to split at (must be within interval bounds).
    Returns:
        Split intervals.
    """
    if not points or not intervals:
        return list(intervals)
    sorted_points = sorted(set(points))
    result: list[tuple[float, float]] = []
    for start, end in intervals:
        current = start
        for pt in sorted_points:
            if current < pt < end:
                result.append((current, pt))
                current = pt
        if current < end:
            result.append((current, end))
    return result


def word_safe_filter(
    intervals: list[tuple[float, float]],
    words: list,
    margin: float = 0.02,
) -> list[tuple[float, float]]:
    """Clamp interval edges to nearest word boundaries.

    Args:
        words: List of word-like objects with .start and .end attributes,
               or tuples (start, end, text).
        margin: Maximum distance from interval edge to snap to word boundary.
    Returns:
        Intervals with edges snapped to word boundaries where possible.
    """
    if not words or not intervals:
        return list(intervals)

    def _wstart(w):
        return w[0] if isinstance(w, tuple) else w.start

    def _wend(w):
        return w[1] if isinstance(w, tuple) else w.end

    sorted_words = sorted(words, key=_wstart)
    word_starts = [_wstart(w) for w in sorted_words]
    word_ends = [_wend(w) for w in sorted_words]

    def _snap_start(t: float) -> float:
        """Snap a clip start backward to a nearby word start (within margin)."""
        for ws in word_starts:  # ascending
            if 0 <= t - ws <= margin:
                return ws
        return t

    def _snap_end(t: float) -> float:
        """Snap a clip end forward to a nearby word end (within margin)."""
        for we in sorted(word_ends, reverse=True):  # descending
            if 0 <= we - t <= margin:
                return we
        return t

    result = []
    for start, end in intervals:
        s = _snap_start(start)
        e = _snap_end(end)
        if s < e:
            result.append((s, e))
    return result


def is_adjacent(a: tuple[float, float], b: tuple[float, float], tolerance: float = 0.001) -> bool:
    """Check if two intervals are adjacent (end of one touches start of other)."""
    a_start, a_end = min(a), max(a)
    b_start, b_end = min(b), max(b)
    return abs(a_end - b_start) < tolerance or abs(b_end - a_start) < tolerance


def is_nested(inner: tuple[float, float], outer: tuple[float, float]) -> bool:
    """Check if inner interval is fully contained within outer."""
    i_start, i_end = inner
    o_start, o_end = outer
    return i_start >= o_start and i_end <= o_end


def source_to_output_time(source_t: float, kept: list[tuple[float, float]]) -> float:
    """Map a source time to output time given kept intervals.

    Output time = cumulative duration of kept intervals before source_t.
    """
    if not kept:
        return 0.0
    sorted_kept = sorted(kept)
    output = 0.0
    for start, end in sorted_kept:
        if source_t <= start:
            break
        if source_t >= end:
            output += end - start
        else:
            output += source_t - start
            break
    return output


def concat_kept_ranges(intervals: list[tuple[float, float]]) -> float:
    """Total output duration of the union of all intervals.

    Overlapping/nested intervals are merged first so durations are not
    double-counted.
    """
    if not intervals:
        return 0.0
    return sum(max(0.0, end - start) for start, end in merge_intervals(intervals))


def enforce_duration(
    intervals: list[tuple[float, float]],
    min_dur: float = 0.0,
    max_dur: float = 60.0,
) -> list[tuple[float, float]]:
    """Enforce minimum and maximum duration on intervals.

    Intervals shorter than min_dur are dropped.
    Intervals longer than max_dur are truncated from the right.
    """
    result = []
    for start, end in sorted(intervals):
        dur = end - start
        if dur < min_dur:
            continue
        if dur > max_dur:
            end = start + max_dur
        result.append((start, end))
    return result


def remove_overlaps(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove overlapping intervals, keeping the first (by start time)."""
    if not intervals:
        return []
    sorted_ints = sorted(intervals)
    result = [sorted_ints[0]]
    for start, end in sorted_ints[1:]:
        prev_start, prev_end = result[-1]
        if start >= prev_end:
            result.append((start, end))
        else:
            result[-1] = (prev_start, max(prev_end, end))
    return result


def tile_cutlist(keeps: list[tuple[float, float]], total_duration: float) -> list[tuple[float, float]]:
    """Build remove intervals from keep intervals and total duration.

    Returns intervals that are NOT in the keep list (the gaps to cut out).
    """
    if not keeps:
        return [(0.0, total_duration)]
    sorted_kept = sorted(keeps)
    removes: list[tuple[float, float]] = []
    if sorted_kept[0][0] > 0.0:
        removes.append((0.0, sorted_kept[0][0]))
    for i in range(len(sorted_kept) - 1):
        gap_start = sorted_kept[i][1]
        gap_end = sorted_kept[i + 1][0]
        if gap_end - gap_start > 0.0:
            removes.append((gap_start, gap_end))
    if sorted_kept[-1][1] < total_duration:
        removes.append((sorted_kept[-1][1], total_duration))
    return removes
