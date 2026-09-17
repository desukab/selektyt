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
from engine.fcpxml import FcpxmlParams, Track
from engine.jumpcut import CutList


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
    """Optional source-media metadata. Populated when the track is backed by a
    real file so it can be exported as FCPXML (see ``to_fcpxml_params``).
    ``timecode_offset`` is the project-time second this media begins at; the
    FCPXML emitter maps it straight onto the asset-clip offset."""
    has_video: bool = True
    has_audio: bool = True
    source_path: str | None = None
    start_timecode: str | None = None
    media_duration: float | None = None

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

    @property
    def media_extent(self) -> float:
        """Duration of this track's own media, from explicit metadata when
        present, otherwise the furthest point its intervals read from."""
        if self.media_duration is not None:
            return self.media_duration
        if not self.intervals:
            return 0.0
        return round(max(i.source_end for i in self.intervals), 3)

    def to_fcpxml_track(self) -> Track:
        """This track as an FCPXML source asset. ``timecode_offset`` (where the
        media begins in project time) maps onto the export ``offset``."""
        return Track(
            name=self.name,
            duration=self.media_extent,
            has_video=self.has_video,
            has_audio=self.has_audio,
            start_timecode=self.start_timecode,
            offset=self.timecode_offset,
            path=self.source_path,
        )


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

    def to_fcpxml_params(self, fps: float, title: str = "SelekyT Edit") -> FcpxmlParams:
        """Export params for the FCPXML emitter.

        The first track is the reference: it defines project time, so its
        offset must be zero. The rest ride along as linked tracks, cut in
        lockstep with the reference's keeps. Lane numbers are derived from
        order + has_video by the emitter, matching AutoCut's lane fan-out, so
        this model's own ``lane`` field is advisory only.

        Raises ValueError when there is no reference track.
        """
        if not self.tracks:
            raise ValueError("no tracks: nothing to export")
        reference = self.tracks[0]
        if reference.timecode_offset != 0.0:
            raise ValueError(
                f"the reference track '{reference.name}' must sit at project "
                "time 0; its offset defines the timeline, not a placement"
            )
        return FcpxmlParams(
            fps=fps,
            title=title,
            reference=reference.to_fcpxml_track(),
            linked=[t.to_fcpxml_track() for t in self.tracks[1:]],
        )


def multitrack_from_cutlist(
    cutlist: CutList,
    tracks: list[TimelineTrack],
    source_duration: float | None = None,
) -> MultiTrackProject:
    """Build a multi-track project where every linked track is cut in
    lockstep with the reference's keeps.

    The reference intervals come from ``cutlist``; each linked track gets the
    same source intervals (its own media is read at the same project times)
    unless it already carries intervals.
    """
    if not tracks:
        raise ValueError("at least one track is required")
    project = MultiTrackProject(
        tracks=list(tracks), source_duration=source_duration or cutlist.original_duration
    )
    reference = project.tracks[0]
    if not reference.intervals:
        reference.intervals = TimelineTrack.from_cutlist(cutlist).intervals
    return project


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