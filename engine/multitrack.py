"""Multi-track timeline model.

Represents a final edit as one or more TimelineTracks, each with its own
timecode offset. Used for:
- Single-camera content (1 track)
- Multi-camera / B-roll by the same engine
- External assets aligned to the source timeline

All interval logic delegates to engine.cut_algebra (single source of truth).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from engine import cut_algebra


@dataclass
class TrackInterval:
    source_start: float
    source_end: float
    output_start: float
    output_end: float
    clip_name: str = ""
    source_duration: float = 0.0

    def __post_init__(self):
        self.source_duration = round(self.source_end - self.source_start, 3)


@dataclass
class TimelineTrack:
    name: str
    lane: int
    timecode_offset: float
    intervals: list[TrackInterval] = field(default_factory=list)

    def add_interval(self, source_start, source_end, output_start=None, output_end=None, clip_name=""):
        """Add an interval, auto-computing output positions when omitted."""
        dur = round(source_end - source_start, 3)
        if output_start is None:
            prev_end = self.intervals[-1].output_end if self.intervals else 0.0
            output_start = round(self.timecode_offset + prev_end, 3)
        if output_end is None:
            output_end = round(output_start + dur, 3)
        self.intervals.append(TrackInterval(
            source_start=source_start,
            source_end=source_end,
            output_start=output_start,
            output_end=output_end,
            clip_name=clip_name,
        ))

    @property
    def total_output_duration(self) -> float:
        if not self.intervals:
            return 0.0
        return round(self.intervals[-1].output_end, 3)

    @property
    def source_intervals(self) -> list[tuple[float, float]]:
        return [(i.source_start, i.source_end) for i in self.intervals]

    @classmethod
    def from_cutlist(cls, cutlist, name="main", lane=0, timecode_offset=0.0):
        """Create a track from a CutList's KEEP segments."""
        track = cls(name=name, lane=lane, timecode_offset=timecode_offset)
        for seg in cutlist.segments:
            if seg.segment_type.value == "keep":
                track.add_interval(seg.start, seg.end, clip_name=seg.reason)
        return track


@dataclass
class MultiTrackProject:
    tracks: list[TimelineTrack] = field(default_factory=list)
    source_duration: float = 0.0

    def add_track(self, track: TimelineTrack) -> None:
        self.tracks.append(track)

    @property
    def output_duration(self) -> float:
        if not self.tracks:
            return 0.0
        return max(t.total_output_duration for t in self.tracks)

    @property
    def lane_count(self) -> int:
        return len({t.lane for t in self.tracks})

    def validate(self) -> list[str]:
        """Semantic validation across all tracks.

        Checks:
        - No source interval exceeds the source duration
        - No overlapping lanes on the same timeline
        - Intervals within a track are sequential (no backtracking)
        """
        issues: list[str] = []

        for track in self.tracks:
            for interval in track.intervals:
                if interval.source_end > self.source_duration + 0.01:
                    issues.append(
                        f"{track.name}: interval {interval.source_start:.2f}–"
                        f"{interval.source_end:.2f} exceeds source duration "
                        f"{self.source_duration:.2f}"
                    )
                if interval.source_start < 0:
                    issues.append(
                        f"{track.name}: interval starts before 0 "
                        f"({interval.source_start:.2f})"
                    )

            for a, b in zip(track.intervals, track.intervals[1:]):
                if a.output_start > b.output_start:
                    issues.append(
                        f"{track.name}: intervals out of order "
                        f"({a.output_start:.2f} then {b.output_start:.2f})"
                    )

        lanes_seen: dict[int, str] = {}
        for track in self.tracks:
            if track.lane in lanes_seen:
                issues.append(f"lane {track.lane} shared by tracks "
                              f"'{lanes_seen[track.lane]}' and '{track.name}'")
            else:
                lanes_seen[track.lane] = track.name

        return issues


def output_time_for_source(track: TimelineTrack, source_t: float) -> float:
    """Map a source time → output time on this track (delegates to cut_algebra)."""
    return round(
        cut_algebra.source_to_output_time(source_t, track.source_intervals), 3
    )


def source_to_output_map(track: TimelineTrack) -> dict[float, float]:
    """Build {source_time: output_time} for all interval boundaries."""
    mapping: dict[float, float] = {}
    for start, end in track.source_intervals:
        mapping[round(start, 3)] = output_time_for_source(track, start)
        mapping[round(end, 3)] = output_time_for_source(track, end)
    return mapping