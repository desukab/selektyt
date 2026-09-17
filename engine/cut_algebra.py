"""Deterministic cut/interval algebra — single source of truth for all edit boundaries.

Every function is pure, deterministic, and operates on sorted float intervals.
No external dependencies. Used by: jumpcut, FCPXML, renderer, multi-track, QA.
"""
from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass

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


# ── Editorial transport algebra (AutoCut src/lib/cuts.ts) ──
#
# These are the shell-facing player helpers: they decide where playback jumps
# and how far a dragged keep edge may move. Ported behavior-identical from
# rust/core/src/editalgebra.rs (itself a port of cuts.ts) so the Python and
# Rust sidecars answer identically — asserted in tests/test_sidecar_parity.py.

DEGENERATE_SECONDS = 0.01
MIN_KEEP_SECONDS = 0.05
SKIP_SETTLED_SECONDS = 0.005
RESTART_WINDOW_SECONDS = 1.0
NEXT_EPSILON_SECONDS = 0.05


@dataclass(frozen=True)
class Keep:
    """A keep as normalized: absolute bounds plus the frontend-only disabled
    flag (the export collapses it into a remove via resolve_for_export)."""

    start: float
    end: float
    disabled: bool = False


@dataclass(frozen=True)
class EditCut:
    """A cutlist interval that may still carry the editorial disabled flag."""

    start: float
    end: float
    kind: str  # "keep" | "remove"
    disabled: bool = False


@dataclass
class EditCutList:
    """Canonical editorial cutlist: tiles the source, every interval is either
    a keep (possibly disabled) or a remove. This is the single representation
    the Python sidecar, the Rust core, and eventually the Tauri shell speak."""

    source_duration: float
    intervals: list[EditCut]


def normalize_keeps(
    keeps: list[tuple[float, float, bool] | Keep], duration: float
) -> list[Keep]:
    """Clip keeps to [0, duration], drop degenerate ones, sort by start, and
    fuse the ones that genuinely overlap.

    Touching is not overlapping: two keeps sharing a boundary stay two keeps.
    They render identically in the export either way, but fusing them
    renumbers every later keep, and callers (a timeline edge drag) hold an
    index across the whole gesture.
    """
    clipped: list[Keep] = []
    for k in keeps:
        if isinstance(k, Keep):
            start, end, disabled = k.start, k.end, k.disabled
        else:
            start, end, disabled = k[0], k[1], k[2]
        start = min(max(start, 0.0), duration)
        end = min(max(end, 0.0), duration)
        if end - start > DEGENERATE_SECONDS:
            clipped.append(Keep(start, end, bool(disabled)))
    clipped.sort(key=lambda k: k.start)

    merged: list[Keep] = []
    for k in clipped:
        if merged and k.start < merged[-1].end:
            last = merged[-1]
            merged[-1] = Keep(
                last.start,
                max(last.end, k.end),
                # When an active keep overlaps a disabled one, active content
                # wins — otherwise a tiny drag overlap can silently remove
                # good footage.
                last.disabled and k.disabled,
            )
        else:
            merged.append(k)
    return merged


def omitted_intervals(cutlist: EditCutList | dict) -> list[EditCut]:
    """Everything the export will drop: real removes plus disabled keeps.
    Sorted and non-overlapping, because the cutlist it comes from tiles the
    source in order."""
    intervals = (
        cutlist["intervals"]
        if isinstance(cutlist, dict)
        else cutlist.intervals
    )
    normalized = [
        c
        if isinstance(c, EditCut)
        else EditCut(
            start=float(c["start"]),
            end=float(c["end"]),
            kind=str(c.get("kind", "remove")),
            disabled=bool(c.get("disabled", False)),
        )
        for c in intervals
    ]
    return [
        c
        for c in normalized
        if c.kind == "remove" or (c.kind == "keep" and c.disabled)
    ]


def skip_target_for(
    omitted: list[tuple[float, float]], time: float, lookahead: float
) -> float | None:
    """Where playback should jump to from ``time``, or None to keep rolling.

    ``lookahead`` is how far ahead to pre-empt an upcoming omitted interval so
    the dead frame is never rendered. Pass 0 while paused: that leaves only the
    "the playhead is sitting inside a removed region" rule active, which is
    what a manual seek into a gap needs.

    Binary search rather than a scan: this runs once per decoded video frame,
    and an hour of detected speech is easily a couple thousand intervals.
    """
    # First interval that hasn't already ended. Anything earlier is behind the
    # playhead and can't be relevant.
    lo, hi = 0, len(omitted)
    while lo < hi:
        mid = (lo + hi) >> 1
        if omitted[mid][1] > time:
            hi = mid
        else:
            lo = mid + 1
    # Two candidates, not one: when the playhead is in the settled tail of an
    # interval that abuts the next one, the first candidate declines and the
    # second is the one inside the lookahead window.
    for c in omitted[lo : lo + 2]:
        if time >= c[0] and time < c[1] - SKIP_SETTLED_SECONDS:
            return c[1]
        if time < c[0] and c[0] - time < lookahead:
            return c[1]
    return None


def clamp_keep_edge(
    keeps: list[Keep],
    index: int,
    edge: str,
    proposed: float,
    duration: float,
) -> float:
    """Clamp a proposed new in/out point for ``keeps[index]`` so it stays
    between its neighbours, inside [0, duration], and leaves the keep at least
    MIN_KEEP_SECONDS long.

    Both edit surfaces route through this: a timeline edge drag and the numeric
    fields in a cuts panel. Without it, typing an overlapping value fuses two
    keeps and renumbers everything after them.
    """
    if not 0 <= index < len(keeps):
        return proposed
    own = keeps[index]
    if edge == "in":
        floor = keeps[index - 1].end if index > 0 else 0.0
        ceil = own.end - MIN_KEEP_SECONDS
    else:
        floor = own.start + MIN_KEEP_SECONDS
        ceil = keeps[index + 1].start if index < len(keeps) - 1 else duration
    return min(max(proposed, floor), ceil)


def _enabled(keeps: list[Keep]) -> list[Keep]:
    """Disabled keeps are excluded from the export, so the transport treats
    them as if they weren't there. Stopping on one would be navigating to
    footage the user has already said they don't want."""
    return [k for k in keeps if not k.disabled]


def previous_keep_target(keeps: list[Keep], time: float) -> float | None:
    """Where the "previous cut" button should seek to, or None if there is
    nothing enabled to seek to.

    Past RESTART_WINDOW_SECONDS into a keep, "previous" restarts it rather than
    stepping back a keep — standard media-player behaviour.
    """
    active = _enabled(keeps)
    if not active:
        return None
    for idx, k in enumerate(active):
        if k.start <= time < k.end:
            if time - k.start > RESTART_WINDOW_SECONDS:
                return k.start
            return active[idx - 1].start if idx > 0 else k.start
    previous = None
    for k in active:
        if k.end <= time:
            previous = k.start
        else:
            break
    return previous if previous is not None else active[0].start


def next_keep_target(keeps: list[Keep], time: float, duration: float) -> float | None:
    """Where the "next cut" button should seek to, or None if there is nothing
    enabled to seek to. Past the last keep this runs out to ``duration`` so the
    button still does something at the tail of the timeline.

    Ignores keeps starting within NEXT_EPSILON_SECONDS, so pressing it while
    sitting exactly on an in-point doesn't land on the same keep again.
    """
    active = _enabled(keeps)
    if not active:
        return None
    for k in active:
        if k.start > time + NEXT_EPSILON_SECONDS:
            return k.start
    for k in active:
        if k.start <= time < k.end:
            return k.end
    return duration
