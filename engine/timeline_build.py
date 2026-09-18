"""Build an edited ``Timeline`` from a pipeline candidate.

``engine/render.render_clip`` renders a candidate by concatenating its
``retention_intervals`` in order — nothing else. This module is that same
operation expressed in the timeline model, so an exported EDL or FCPXML
describes the video that was actually written: the same intervals, the same
order, the same accumulated record cursor.

Keeping the two in one place would have been neater, but the render path is
ffmpeg filter chains and this is a data model; the invariant that relates them
is pinned in ``tests/test_timeline_build.py`` against the rendered file's own
reported duration rather than trusted.
"""
from __future__ import annotations

from pathlib import Path

from models.core import Candidate
from engine.edit_plan import retention_intervals
from engine.timeline import Clip, SourceMedia, Timeline


def timeline_for_candidate(
    media: Path,
    candidate: Candidate,
    fps: float = 30.0,
    title: str | None = None,
) -> Timeline:
    """One candidate's edited cut as an exportable timeline.

    The record cursor accumulates the *kept* durations only — the pauses the
    edit removes are not present on the edited timeline, which is what makes
    the exported duration match the rendered MP4.
    """
    intervals = retention_intervals(candidate)
    if not intervals:
        raise RuntimeError(
            "candidate has no renderable intervals, so it has no timeline"
        )
    source = SourceMedia(
        path=str(media),
        fps=fps,
        # The duration is the source's, not the clip's; an asset in an NLE is
        # the whole file, and the clips reference into it.
        start_timecode=None,
    )
    cursor = 0.0
    clips: list[Clip] = []
    for start, end in intervals:
        duration = end - start
        clips.append(
            Clip(
                source=source,
                source_in=start,
                source_out=end,
                timeline_in=cursor,
                timeline_out=cursor + duration,
                track="V1",
                reason=candidate.reason or candidate.story_type,
            )
        )
        cursor += duration
    return Timeline(
        clips=clips,
        title=title or (candidate.hook or candidate.story_type),
        fps=fps,
    )
