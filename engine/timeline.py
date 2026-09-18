"""In-memory edited-timeline model.

An honest, dependency-free representation of a *finished* edit: an ordered
list of clips, each one a mapping from a span of source media onto a span of
the edited timeline, with a track assignment. This is the single structure
that caption derivation (``engine/timeline_captions``) and NLE interchange
export (``engine/nle_export``) both consume, so captions and export can never
disagree about what the edit is.

Design rules that keep it trustworthy:

* A clip is a pure placement. It carries source media, source in/out,
  timeline in/out and a track. Nothing else. Effects, transitions and
  re-timing are out of scope — a clip's source duration must equal its
  timeline duration, which is exactly the invariant an NLE needs to relink
  it, and exactly what ``Timeline.validate`` checks.
* Traceability is a first-class field, not an afterthought: every clip can
  answer "which source timecode does my in-point correspond to?" through
  ``Clip.source_timecode_in/out``, and the timeline can map either direction
  (``source_to_timeline`` / ``timeline_to_source``).
* All frame-rate and SMPTE handling goes through ``engine.timecode`` — the
  rate comes from ``timecode.detect_rate`` and frame snapping from
  ``timecode.seconds_to_rational``. ``engine.timecode`` only parses; the one
  thing it cannot do is *format* seconds back into a SMPTE string, which EDL
  export needs. ``format_smpte`` below supplies that missing half and stays
  correct by treating ``timecode.parse_smpte`` as the authority: the formatted
  string is snapped back through the parser and corrected until it
  round-trips exactly (see ``tests/test_timeline.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import PurePath

from engine import timecode


@dataclass(frozen=True)
class SourceMedia:
    """One piece of source media that clips read from.

    ``start_timecode`` is the SMPTE timecode the file's own timeline starts
    at (its embedded TC), when known. It shifts where inside the file a given
    source second reads from, independently of any placement — the same
    convention ``engine/fcpxml`` uses for relink-safe assets.
    """

    path: str
    fps: float = 30.0
    duration: float | None = None
    start_timecode: str | None = None
    has_video: bool = True
    has_audio: bool = True

    @property
    def name(self) -> str:
        try:
            return PurePath(self.path).name or self.path
        except ValueError:  # pragma: no cover - exotic paths
            return self.path

    @property
    def key(self) -> str:
        """Identity used to share one asset between clips. The resolved path
        when it resolves, otherwise the raw string."""
        try:
            return str(PurePath(self.path))
        except ValueError:  # pragma: no cover
            return self.path


@dataclass(frozen=True)
class Track:
    """A timeline lane. ``kind`` is ``"video"`` or ``"audio"``; the name is the
    editor-facing label (``V1``, ``A1``, ``music`` …)."""

    name: str
    kind: str = "video"

    def __post_init__(self) -> None:
        if self.kind not in ("video", "audio"):
            raise ValueError(f"track kind must be 'video' or 'audio', got {self.kind!r}")


@dataclass(frozen=True)
class Clip:
    """A placement of a span of source media onto the edited timeline.

    ``source_in``/``source_out`` are seconds into the source file (additive
    with the media's ``start_timecode`` when computing SMPTE).
    ``timeline_in``/``timeline_out`` are seconds on the edited timeline.
    """

    source: SourceMedia
    source_in: float
    source_out: float
    timeline_in: float
    timeline_out: float
    track: str = "V1"
    name: str = ""
    """Editorial provenance: why this clip is here. Kept free-form so the
    report and the timeline can agree."""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.source_out <= self.source_in:
            raise ValueError(
                f"clip {self.name or self.source.name}: source_out "
                f"({self.source_out}) must exceed source_in ({self.source_in})"
            )
        if self.timeline_out <= self.timeline_in:
            raise ValueError(
                f"clip {self.name or self.source.name}: timeline_out "
                f"({self.timeline_out}) must exceed timeline_in ({self.timeline_in})"
            )
        if self.timeline_in < 0.0:
            raise ValueError(
                f"clip {self.name or self.source.name}: timeline_in must be >= 0"
            )
        if not self.name:
            object.__setattr__(self, "name", self.source.name)

    @property
    def duration(self) -> float:
        return self.timeline_out - self.timeline_in

    @property
    def source_duration(self) -> float:
        return self.source_out - self.source_in

    @property
    def rate(self) -> timecode.TimecodeRate:
        return timecode.detect_rate(self.source.fps, _is_df(self.source.start_timecode))

    def source_timecode_in(self) -> str:
        """SMPTE timecode of this clip's in-point in the source file.

        Built from the frame-snapped in-point plus the frame-snapped span (see
        ``source_timecode_out``) so the pair always brackets a whole number of
        frames — snapping each end independently from raw seconds can straddle
        a frame and report a length the export does not have.
        """
        in_frames, _span = self._source_frames()
        return format_smpte(in_frames * self.rate.frame_seconds(), self.rate)

    def source_timecode_out(self) -> str:
        """SMPTE timecode of this clip's out-point: the in-point's frame plus
        the clip's duration snapped to whole frames."""
        in_frames, span_frames = self._source_frames()
        return format_smpte((in_frames + span_frames) * self.rate.frame_seconds(), self.rate)

    def _source_frames(self) -> tuple[int, int]:
        frame = self.rate.frame_seconds()
        zero = _media_zero(self.source)
        in_frames = int(round((zero + self.source_in) / frame))
        span_frames = int(round(self.source_duration / frame))
        return in_frames, max(span_frames, 1)

    def trace(self) -> dict:
        """Everything needed to point back at the source from a cue or event."""
        return {
            "clip": self.name,
            "source_path": self.source.path,
            "source_in": round(self.source_in, 3),
            "source_out": round(self.source_out, 3),
            "source_timecode_in": self.source_timecode_in(),
            "source_timecode_out": self.source_timecode_out(),
            "timeline_in": round(self.timeline_in, 3),
            "timeline_out": round(self.timeline_out, 3),
            "track": self.track,
            "reason": self.reason,
        }


@dataclass
class RenderManifest:
    """What was (or will be) rendered for this timeline. Optional: a timeline
    that has not been rendered carries an empty manifest and still exports."""

    output_path: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration_seconds: float = 0.0
    captions_srt: str = ""
    captions_ass: str = ""
    captions_json: str = ""

    def is_populated(self) -> bool:
        return bool(self.output_path)

    def to_dict(self) -> dict:
        return {
            "output_path": self.output_path,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "duration_seconds": round(self.duration_seconds, 3),
            "captions": {
                "srt": self.captions_srt,
                "ass": self.captions_ass,
                "json": self.captions_json,
            },
        }


@dataclass
class Timeline:
    """An ordered edit. Clips are held in timeline order on their own tracks;
    ``clips`` is kept sorted by ``(timeline_in, track)`` on construction."""

    clips: list[Clip] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    title: str = "SelekyT Edit"
    fps: float = 30.0
    """Frame rate of the *edited timeline* (sequence), which need not match a
    clip's source rate; NLE export stamps the sequence with this one."""
    manifest: RenderManifest = field(default_factory=RenderManifest)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.clips = sorted(self.clips, key=lambda c: (c.timeline_in, c.track))
        declared = {t.name for t in self.tracks}
        for clip in self.clips:
            if clip.track not in declared:
                self.tracks = [
                    *self.tracks,
                    Track(clip.track, _default_kind(clip.track)),
                ]
                declared.add(clip.track)
        if not math.isfinite(self.fps) or self.fps <= 0.0:
            raise ValueError(f"timeline fps must be positive and finite, got {self.fps}")

    # ------------------------------------------------------------------ shape

    @property
    def duration(self) -> float:
        """Length of the edited timeline. Zero for an empty one."""
        if not self.clips:
            return 0.0
        return max(c.timeline_out for c in self.clips)

    @property
    def track_names(self) -> list[str]:
        return [t.name for t in self.tracks]

    @property
    def primary_track(self) -> Track:
        """The video track that owns the sequence. The first declared video
        track, else the first track, and the empty timeline is refused — an
        NLE export of nothing is a no-op that should be caught explicitly."""
        for t in self.tracks:
            if t.kind == "video":
                return t
        if self.tracks:
            return self.tracks[0]
        raise ValueError("timeline has no tracks: nothing to export")

    def clips_on(self, track: str) -> list[Clip]:
        return [c for c in self.clips if c.track == track]

    def source_media(self) -> list[SourceMedia]:
        """Distinct source media, in first-use order."""
        seen: dict[str, SourceMedia] = {}
        for c in self.clips:
            seen.setdefault(c.source.key, c.source)
        return list(seen.values())

    # ------------------------------------------------------- traceability maps

    def source_to_timeline(self, source_key: str, source_t: float) -> float | None:
        """Map a source second to its edited-timeline second.

        Returns None when the instant is not inside any clip of that media —
        cut-away instants genuinely have no timeline position, and inventing
        one would be exactly the silent fabrication the caption path must
        avoid."""
        for clip in self.clips:
            if clip.source.key != source_key:
                continue
            if clip.source_in <= source_t <= clip.source_out:
                return clip.timeline_in + (source_t - clip.source_in)
        return None

    def timeline_to_source(self, timeline_t: float) -> tuple[Clip, float] | None:
        """Inverse map: the clip under the playhead and the source second it
        reads. Picks the primary-track clip when several overlap."""
        order = [self.primary_track.name] + [
            t for t in self.track_names if t != self.primary_track.name
        ]
        by_track = {t: self.clips_on(t) for t in order}
        for track in order:
            for clip in by_track[track]:
                if clip.timeline_in <= timeline_t <= clip.timeline_out:
                    return clip, clip.source_in + (timeline_t - clip.timeline_in)
        return None

    # ------------------------------------------------------------- validation

    def validate(self, strict_timing: bool = False) -> list[str]:
        """Structural sanity. Returns a list of problems (empty = valid).

        ``strict_timing`` makes the source/timeline duration parity a
        frame-exact demand instead of the default half-frame tolerance; a
        re-timed clip is not representable by this model on purpose.
        """
        problems: list[str] = []
        if not self.clips:
            return problems

        frame = 1.0 / max(self.fps, 1.0)
        tolerance = 0.0 if strict_timing else frame * 0.5

        for clip in self.clips:
            drift = abs(clip.source_duration - clip.duration)
            if drift > tolerance + 1e-9:
                in_frames = drift / frame if frame > 0 else drift
                problems.append(
                    f"clip {clip.name}: source duration {clip.source_duration:.3f}s "
                    f"!= timeline duration {clip.duration:.3f}s "
                    f"(drift {in_frames:.2f} frames; re-timing is unsupported)"
                )
            media = clip.source
            if media.duration is not None and clip.source_out > media.duration + 1e-6:
                problems.append(
                    f"clip {clip.name}: source_out {clip.source_out:.3f}s "
                    f"exceeds media duration {media.duration:.3f}s"
                )
            if clip.source_in < 0.0:
                problems.append(f"clip {clip.name}: source_in is negative")

        # Per-track overlap: two clips on one lane may not share timeline time.
        for track in self.track_names:
            row = self.clips_on(track)
            for a, b in zip(row, row[1:]):
                if b.timeline_in < a.timeline_out - 1e-6:
                    problems.append(
                        f"track {track}: clips '{a.name}' "
                        f"({a.timeline_in:.3f}-{a.timeline_out:.3f}) and "
                        f"'{b.name}' ({b.timeline_in:.3f}-{b.timeline_out:.3f}) overlap"
                    )

        return problems

    # ----------------------------------------------------------------- export

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "fps": self.fps,
            "duration_seconds": round(self.duration, 3),
            "tracks": [{"name": t.name, "kind": t.kind} for t in self.tracks],
            "clips": [c.trace() for c in self.clips],
            "manifest": self.manifest.to_dict() if self.manifest.is_populated() else None,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------- SMPTE write


def _is_df(start_timecode: str | None) -> bool:
    return bool(start_timecode and ";" in start_timecode)


def _media_zero(media: SourceMedia) -> float:
    """Seconds into the file's own numbering where timecode 00:00:00:00 sits,
    i.e. the parsed embedded TC, so a clip at source_in reads at TC0+source_in."""
    return timecode.parse_smpte(media.start_timecode, media.fps)


def _default_kind(track: str) -> str:
    name = track.strip().upper()
    if name.startswith("A"):
        return "audio"
    return "video"


def _decompose(total_frames: int, rate: timecode.TimecodeRate) -> str:
    n = rate.nominal_fps
    f = total_frames % n
    s = (total_frames // n) % 60
    m = (total_frames // (n * 60)) % 60
    h = total_frames // (n * 3600)
    sep = ";" if rate.is_drop_frame else ":"
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{f:02d}"


def format_smpte(seconds: float, rate: timecode.TimecodeRate) -> str:
    """Render a (media-relative) second count as SMPTE timecode.

    The inverse of ``engine.timecode.parse_smpte``. Non-drop is a direct
    decomposition. Drop-frame is estimated from real time and then snapped:
    the candidate is re-parsed with ``parse_smpte`` and the residual frame
    error is added back, which converges in one or two passes because the
    drop count is a function of the displayed hour and minute alone. The
    result therefore round-trips through the parser exactly, and that is
    asserted over a dense frame range in ``tests/test_timeline.py``.
    """
    if seconds <= 0.0:
        return _decompose(0, rate)
    if not rate.is_drop_frame:
        return _decompose(int(round(seconds / rate.frame_seconds())), rate)

    nominal = rate.nominal_fps
    if nominal not in (30, 60):  # parser honors DF only for 30/60
        return _decompose(int(round(seconds / rate.frame_seconds())), rate)

    dpm = 2 if nominal == 30 else 4
    fps = nominal * 1000.0 / 1001.0
    total = int(round(seconds / rate.frame_seconds()))

    # Real-time estimate of the displayed hour/minute, hence the drop count.
    t = total * rate.frame_seconds()
    hh = int(t // 3600)
    mm = int((t % 3600) // 60)
    minutes = hh * 60 + mm
    display = total + dpm * (minutes - minutes // 10)

    # Snap: correct any residual against the parser, which is authoritative.
    for _ in range(4):
        tc = _decompose(display, rate)
        got = timecode.parse_smpte(tc, fps)
        err = total - int(round(got / rate.frame_seconds()))
        if err == 0:
            return tc
        display += err
    return tc  # best effort; tests pin the round trip


__all__ = [
    "Clip",
    "RenderManifest",
    "SourceMedia",
    "Timeline",
    "Track",
    "format_smpte",
]
