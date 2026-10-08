"""Editable interchange export for an edited timeline.

Two self-contained writers, both driven by ``engine.timeline.Timeline``:

* **EDL (CMX 3600)** — the ASCII online-editing list. One event per clip,
  V and A channel blocks sharing an event number, ``C`` (cut) transitions,
  an ``FCM`` frame-mode header and ``M2`` / ``* FROM CLIP:`` annotations that
  keep the reel name pointing back at the real file.
* **FCPXML 1.11** — a *new*, self-contained emitter that describes the
  finished edit directly: assets for each source media, asset-clips placed on
  the primary storyline with explicit gaps, and clips from other tracks
  attached as connected clips on the lane above (video) or below (audio).
  It deliberately does **not** reuse ``engine/fcpxml.py``'s cutlist-driven
  emitter: that one speaks AutoCut's keeps/removes vocabulary, and this one
  speaks an editor's clips. They share ``engine/timecode`` for every frame
  and rational.

Every emitted timecode is produced by ``engine.timeline.format_smpte`` /
``engine.timecode.seconds_to_rational``, so the frame-rate math has one
source of truth.

Validation is the point of the module: ``validate_edl`` and
``validate_fcpxml`` are structural checks that run on the emitted *text*,
not on the in-memory model, and are therefore able to catch a corrupted
export. They are exercised against deliberately broken documents in
``tests/test_nle_export.py``.

Honest compatibility note — this module produces **structurally valid** EDL
and FCPXML documents. NLE import (Premiere Pro, DaVinci Resolve, Final Cut
Pro, Avid Media Composer) is **not verified on this host**: no NLE is
installed here and none was run. Do not read the tests as a compatibility
claim; they are format-validity claims.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePath

from engine import timecode
from engine.timeline import Clip, SourceMedia, Timeline, Track, format_smpte

FCPXML_VERSION = "1.11"

RATIONAL_RE = re.compile(r"^(?P<num>\d+)(?:/(?P<den>\d+))?s$")
SMPTE_RE = re.compile(r"^\d+:\d{2}:\d{2}[;:]\d{2}$")
TAG_NAME_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_.-]*)")


# ------------------------------------------------------------------ helpers


def _primary_clips(timeline: Timeline) -> list[Clip]:
    return timeline.clips_on(timeline.primary_track.name)


def _rate_for(timeline: Timeline) -> timecode.TimecodeRate:
    """Sequence rate. Drop-frame is stamped when the primary track's media
    carries a drop-frame timecode, matching ``engine/fcpxml``'s convention."""
    drop = any(";" in (c.source.start_timecode or "") for c in _primary_clips(timeline))
    return timecode.detect_rate(timeline.fps, drop)


def _lane_of(track: Track, timeline: Timeline) -> int:
    """Lane for a non-primary track: video lanes climb above the primary
    storyline, audio lanes descend below it, mirroring AutoCut's fan-out."""
    primary = timeline.primary_track.name
    if track.kind == "video":
        order = [t for t in timeline.tracks if t.kind == "video" and t.name != primary]
        return order.index(track) + 1
    order = [t for t in timeline.tracks if t.kind == "audio"]
    return -(order.index(track) + 1)


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _to_file_uri(path: str) -> str:
    """An absolute ``file:///`` URI, or a failure naming the path.

    An NLE relinks a clip by this URI, and a *relative* one resolves against
    the timeline file's own directory — which is almost never where the media
    is — so the sequence imports with every clip offline and the user is left
    facing a relink dialog for a file that never moved. A CLI invocation with
    a relative source path produced exactly this on a real run, because
    ``PurePath`` does not touch the filesystem and a relative path stayed
    relative. Resolving against the working directory is the fix, and a path
    that cannot be resolved must fail the export rather than ship offline
    media under a URI that looks complete.
    """
    if path.startswith(("file://", "http://", "https://")):
        return path
    try:
        resolved = Path(path).resolve()
        abs_path = str(resolved)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "cannot turn the source path into an absolute file URI, so the "
            f"export would reference media an NLE cannot relink: {path!r}"
        ) from exc
    # A Windows path carries a drive letter ("C:\\...") or a UNC root
    # ("\\\\server\\share\\..."). The generic encoder below would emit
    # "file://C:%5Cdir%5Cclip.mp4": the drive letter becomes the URI host and
    # every separator is percent-encoded, so an NLE resolves each clip against
    # a nonexistent server and the whole sequence comes up offline. pathlib's
    # as_uri() places the drive after a third slash with an empty host
    # ("file:///C:/dir/clip.mp4") and renders a UNC root as
    # "file://server/share/clip.mp4", which are the forms NLEs relink.
    if PurePath(abs_path).drive:
        return resolved.as_uri()
    encoded = ["file://"]
    for byte in abs_path.encode("utf-8", errors="surrogateescape"):
        if byte in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~/:":
            encoded.append(chr(byte))
        else:
            encoded.append(f"%{byte:02X}")
    return "".join(encoded)


def _from_clip_path(path: str) -> str:
    """An absolute path for ``* FROM CLIP:``, or a failure naming the path.

    The same problem ``_to_file_uri`` guards against, on the EDL side: an NLE
    resolves a relative FROM CLIP against the timeline file's *own directory*,
    which is almost never where the media is, so the sequence imports with
    every clip offline and the editor faces a relink dialog for a file that
    never moved. ``_to_file_uri`` turns the source into an absolute URI and
    refuses to ship one it cannot resolve; an EDL has no URI, only a path, so
    the equivalent guarantee is an absolute path. A CLI invocation given a
    relative source produced exactly this on a real corpus run — the EDL read
    ``corpus/media/x.webm`` while sitting in ``project/timeline/`` — and the
    audit passed it, because the audit resolved the same relative string
    against its own working directory instead of the timeline's.
    """
    if path.startswith(("file://", "http://", "https://")):
        return path
    try:
        return str(Path(path).resolve())
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "cannot make the source path absolute, so the EDL would reference "
            f"media an NLE cannot relink: {path!r}"
        ) from exc


def _reel_name(path: str, taken: set[str]) -> str:
    """Classic CMX reel id: uppercase, no spaces, 8 characters. Uniqueness is
    enforced so two sources cannot share a reel and silently relink to each
    other inside an NLE."""
    try:
        stem = PurePath(path).stem or "SOURCE"
    except ValueError:  # pragma: no cover
        stem = "SOURCE"
    base = re.sub(r"[^A-Za-z0-9_]", "_", stem).upper()[:8] or "SOURCE"
    name, n = base, 1
    while name in taken:
        suffix = str(n)
        name = base[: 8 - len(suffix)] + suffix
        n += 1
    taken.add(name)
    return name


def _media_duration(timeline: Timeline, media: SourceMedia) -> float:
    """What to declare as an asset's duration: the media's own duration when
    known, otherwise the furthest point any clip reads from it. Never zero, so
    an asset cannot be declared shorter than the clips citing it."""
    if media.duration is not None and media.duration > 0.0:
        return media.duration
    used = [c.source_out for c in timeline.clips if c.source.key == media.key]
    return max(used) if used else 0.0


# --------------------------------------------------------------------- EDL


@dataclass(frozen=True)
class _EdlEvent:
    number: int
    reel: str
    channel: str
    src_in: str
    src_out: str
    rec_in: str
    rec_out: str
    clip_name: str
    source_path: str
    non_ascii_name: bool = False


def _channel_code(clip: Clip, timeline: Timeline) -> str:
    """CMX 3600 channel code for a clip's track: ``V`` for the primary video
    lane, ``V2``/``V3`` for lanes above it, ``A`` for the first audio lane and
    ``A2``… below it. Two clips on the *same* channel at the same record time
    is a genuine EDL error, so lanes must not collapse onto one code."""
    primary = timeline.primary_track.name
    if clip.track == primary:
        return "V" if clip.source.has_video else "A"
    track = next((t for t in timeline.tracks if t.name == clip.track), None)
    if track is None or track.kind == "video":
        order = [t for t in timeline.tracks if t.kind == "video" and t.name != primary]
        return f"V{order.index(track) + 2}" if track is not None else "V2"
    order = [t for t in timeline.tracks if t.kind == "audio"]
    return f"A{order.index(track) + 1}" if track else "A"


def _channel_priority(clip: Clip, timeline: Timeline) -> int:
    """Event order within one record instant: primary video, then upper video
    lanes, then audio lanes — so the event list reads like an editor's
    channel block rather than being ordered by track name."""
    primary = timeline.primary_track.name
    if clip.track == primary:
        return 0
    track = next((t for t in timeline.tracks if t.name == clip.track), None)
    if track is None:
        return 3
    return 1 if track.kind == "video" else 2


def _ascii_name(name: str, fallback: str) -> tuple[str, bool]:
    """An ASCII-safe value for the ``M2`` annotation: EDL is an ASCII format,
    so a Telugu or Devanagari clip name is replaced by the reel name and the
    caller notes the substitution. Returns (value, was_substituted)."""
    if name and name.isascii():
        return name, False
    return fallback, True


def _edl_events(timeline: Timeline) -> list[_EdlEvent]:
    rec_rate = _rate_for(timeline)
    reels: dict[str, str] = {}
    taken: set[str] = set()
    events: list[_EdlEvent] = []
    # Events are numbered in record order, not declaration order.
    ordered = sorted(
        timeline.clips, key=lambda c: (c.timeline_in, _channel_priority(c, timeline))
    )
    # Record positions must advance monotonically within a channel. Rounding
    # each event's timeline_in to the record grid independently lets the
    # accumulated sum drift by a frame in either direction, and an event can
    # then start one frame *before* its predecessor ends — an overlap the
    # validator rejects and an NLE would render as a spurious crossfade. So
    # each event's in-point is the greater of its own snapped timeline_in and
    # the previous event's out-point on that channel, which keeps the list
    # gap-free without re-timing anything: only the boundary frame moves.
    rec_end: dict[str, int] = {}
    raw_end: dict[str, float] = {}
    for n, clip in enumerate(ordered, 1):
        reel = reels.setdefault(clip.source.key, _reel_name(clip.source.path, taken))
        # Snap source and record onto their own frame grids *once*, then build
        # the record span out of the source span, so the EDL is exactly
        # self-consistent: an event's record duration is its source duration
        # by construction, which is what makes the list conformable. The
        # clip's own timecode pair uses the same snapping.
        rate = clip.rate
        _, span_frames = clip._source_frames()
        span_seconds = span_frames * rate.frame_seconds()
        rec_span_frames = int(round(span_seconds / rec_rate.frame_seconds()))
        channels = _channel_codes(clip, timeline)
        # Video and audio of the same cut share one record in-point.
        own_in = int(round(clip.timeline_in / rec_rate.frame_seconds()))
        previous_end = max((rec_end.get(ch, 0) for ch in channels), default=0)
        # Mirror FCPXML's serialization cursor: when the in-memory timeline
        # says this clip starts where the previous clip on the channel ended,
        # use the already-serialized record position. Independently rounding
        # the raw float timeline_in can otherwise add a one-frame gap to the
        # EDL while FCPXML correctly keeps the spine contiguous.
        contiguous = all(
            ch not in raw_end
            or abs(clip.timeline_in - raw_end[ch]) <= rec_rate.frame_seconds() * 0.5
            for ch in channels
        )
        rec_in_frames = previous_end if contiguous and previous_end else max(own_in, previous_end)
        src_in = clip.source_timecode_in()
        src_out = clip.source_timecode_out()
        rec_in = format_smpte(rec_in_frames * rec_rate.frame_seconds(), rec_rate)
        rec_out = format_smpte((rec_in_frames + rec_span_frames) * rec_rate.frame_seconds(), rec_rate)
        for ch in channels:
            rec_end[ch] = rec_in_frames + rec_span_frames
            raw_end[ch] = clip.timeline_out
        name, substituted = _ascii_name(clip.name, reel)
        for ch in channels:
            events.append(
                _EdlEvent(
                    number=n,
                    reel=reel,
                    channel=ch,
                    src_in=src_in,
                    src_out=src_out,
                    rec_in=rec_in,
                    rec_out=rec_out,
                    clip_name=name,
                    source_path=clip.source.path,
                    non_ascii_name=substituted,
                )
            )
    return events


def _channel_codes(clip: Clip, timeline: Timeline) -> list[str]:
    """Channel blocks for one clip.

    A clip on the primary track with both picture and sound is written as a V
    block plus an A block sharing the event number — the canonical CMX 3600
    way to describe one linked piece of media. A clip on a lane above or below
    the primary is written as a single block for its own lane, because CMX
    3600 has no way to attach the primary's audio channel to a different lane
    without two events colliding on that channel.
    """
    primary = timeline.primary_track.name
    if clip.track == primary and clip.source.has_video and clip.source.has_audio:
        return ["V", "A"]
    return [_channel_code(clip, timeline)]


def render_edl(timeline: Timeline) -> str:
    """Render the timeline as a CMX 3600 EDL string."""
    _require_exportable(timeline)
    events = _edl_events(timeline)
    rate = _rate_for(timeline)
    drop = rate.is_drop_frame and rate.nominal_fps in (30, 60)

    title, title_sub = _ascii_name(timeline.title, "SelekyT Edit")
    out: list[str] = [f"TITLE:   {title}"]
    if title_sub:
        out.append("* COMMENT: the edit's title is not ASCII; it is kept in the timeline sidecar")
    out += ["", f"FCM: {'DROP' if drop else 'NON-DROP'} FRAME", ""]
    last = 0
    for ev in events:
        first_block = ev.number != last
        if first_block:
            if last:
                out.append("")
            last = ev.number
        out.append(
            f"{ev.number:03d}  {ev.reel:<8} {ev.channel:<5} C        "
            f"{ev.src_in} {ev.src_out} {ev.rec_in} {ev.rec_out}"
        )
        if first_block:
            out.append(f"     M2   {ev.clip_name}")
            out.append(f"* FROM CLIP: {_from_clip_path(ev.source_path)}")
            if ev.non_ascii_name:
                out.append(
                    "* COMMENT: clip name is not ASCII; the reel name is used "
                    "above and the original name lives in the timeline sidecar"
                )
    out.append("")
    return "\n".join(out)


def export_edl(timeline: Timeline, output: str | PurePath, encoding: str = "ascii") -> Path:
    """Write a CMX 3600 EDL.

    ASCII by default: EDL is an ASCII interchange format, so a non-ASCII clip
    name is transliterated by the reel-name sanitizer while the real path is
    preserved verbatim in the ``* FROM CLIP:`` annotation (a path, hence
    ASCII-safe). Ask for ``encoding="utf-8"`` only when the receiving tool is
    known to accept it.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_edl(timeline), encoding=encoding)
    return output


# ------------------------------------------------------------------ FCPXML


def _asset_id_map(timeline: Timeline) -> dict[str, str]:
    return {m.key: f"r{i + 1}" for i, m in enumerate(timeline.source_media())}


def _asset_xml(
    asset_id: str, media: SourceMedia, duration: float, rate: timecode.TimecodeRate
) -> str:
    src = _to_file_uri(media.path)
    media_rate = timecode.detect_rate(media.fps, ";" in (media.start_timecode or ""))
    start = timecode.seconds_to_rational(
        timecode.parse_smpte(media.start_timecode, media.fps), media_rate
    )
    video = ' hasVideo="1" videoSources="1"' if media.has_video else ' hasVideo="0"'
    audio = (
        ' hasAudio="1" audioSources="1" audioChannels="2"'
        if media.has_audio
        else ' hasAudio="0"'
    )
    return (
        f'    <asset id="{asset_id}" name="{_xml_escape(media.name)}" '
        f'start="{start}" duration="{timecode.seconds_to_rational(duration, rate)}"'
        f"{video}{audio}>\n"
        f'      <media-rep kind="original-media" src="{_xml_escape(src)}"/>\n'
        f"    </asset>\n"
    )


def _clip_xml(
    clip: Clip,
    asset_id: str,
    lane: int | None,
    rate: timecode.TimecodeRate,
    tc_format: str,
    offset: float | None = None,
) -> str:
    if offset is None:
        offset = clip.timeline_in
    start = timecode.parse_smpte(clip.source.start_timecode, clip.source.fps) + clip.source_in
    source_rate = clip.rate
    lane_attr = f' lane="{lane}"' if lane is not None else ""
    return (
        f'<asset-clip ref="{asset_id}"{lane_attr} '
        f'offset="{timecode.seconds_to_rational(offset, rate)}" '
        f'start="{timecode.seconds_to_rational(start, source_rate)}" '
        f'duration="{timecode.seconds_to_rational(clip.duration, rate)}" '
        f'tcFormat="{tc_format}" />\n'
    )


def _gap_xml(offset: float, duration: float, rate: timecode.TimecodeRate) -> str:
    return (
        f'<gap offset="{timecode.seconds_to_rational(offset, rate)}" '
        f'duration="{timecode.seconds_to_rational(duration, rate)}" />\n'
    )


def _open_tag(xml: str) -> str:
    """``<gap ... />`` → ``<gap ...>`` for nesting connected clips inside."""
    s = xml.strip()
    if s.endswith("/>"):
        s = s[:-2].rstrip()
    return s + ">"


def _close_tag(xml: str) -> str:
    m = TAG_NAME_RE.match(xml.strip())
    return f"</{m.group(1)}>" if m else ""


def render_fcpxml(timeline: Timeline) -> str:
    """Render the timeline as a self-contained FCPXML 1.11 document.

    The primary track becomes the storyline: its clips are placed in order
    with explicit ``<gap>`` filler for every hole, so the spine is continuous
    (an NLE's timeline is; a CutList's keeps are not). Clips from other tracks
    become connected clips nested inside whichever storyline element covers
    their start, which is how FCPXML expresses an insert on a lane.
    """
    _require_exportable(timeline)

    rate = _rate_for(timeline)
    tc_format = "DF" if rate.is_drop_frame and rate.nominal_fps in (30, 60) else "NDF"
    frame_duration = f"{rate.frame_duration_num}/{rate.frame_duration_den}s"
    asset_ids = _asset_id_map(timeline)

    resources = "".join(
        _asset_xml(asset_ids[m.key], m, _media_duration(timeline, m), rate)
        for m in timeline.source_media()
    )

    primary = _primary_clips(timeline)
    primary_name = timeline.primary_track.name
    connected = [c for c in timeline.clips if c.track != primary_name]

    # Storyline elements with gaps filled in, in timeline order.
    elements: list[tuple[str, float, float]] = []  # (xml, offset, end)
    frame_seconds = rate.frame_seconds()
    cursor = 0.0
    raw_cursor = 0.0
    for clip in primary:
        # The offsets and durations are serialized separately as frame-grid
        # rationals. Accumulating unrounded floats here can put a later clip one
        # frame after the rounded end of its predecessor, creating a gap in the
        # FCPXML even though the in-memory timeline is contiguous. Advance the
        # serialization cursor by the same snapped durations the document will
        # carry, while retaining intentional frame-sized timeline gaps.
        # If the in-memory clips touch, their serialized starts must touch too:
        # a cumulative half-frame rounding difference can otherwise turn a
        # continuous edit into a one-frame FCPXML gap. Preserve an actual gap
        # only when the model itself has one.
        contiguous = clip.timeline_in <= raw_cursor + 1e-9
        offset = (
            cursor if contiguous
            else round(clip.timeline_in / frame_seconds) * frame_seconds
        )
        if offset > cursor + 1e-9:
            elements.append((_gap_xml(cursor, offset - cursor, rate), cursor, offset))
        offset = max(offset, cursor)
        duration = round(clip.duration / frame_seconds) * frame_seconds
        end = offset + duration
        elements.append(
            (_clip_xml(clip, asset_ids[clip.source.key], None, rate, tc_format,
                       offset=offset), offset, end)
        )
        cursor = end
        raw_cursor = clip.timeline_out
    if not elements:
        elements.append((_gap_xml(0.0, timeline.duration, rate), 0.0, timeline.duration))

    # Attach connected clips to the storyline element covering their start.
    nested: dict[int, list[str]] = {}
    for clip in connected:
        parent = next(
            (
                i
                for i, (_, offset, end) in enumerate(elements)
                if offset - 1e-9 <= clip.timeline_in < end + 1e-9
            ),
            None,
        )
        if parent is None:
            # Beyond the storyline's extent: extend it with a trailing gap.
            i = len(elements)
            elements.append((_gap_xml(cursor, clip.timeline_out - cursor, rate), cursor, clip.timeline_out))
            cursor = clip.timeline_out
            parent = i
        track = next(t for t in timeline.tracks if t.name == clip.track)
        rel = clip.timeline_in - elements[parent][1]
        nested.setdefault(parent, []).append(
            _clip_xml(clip, asset_ids[clip.source.key], _lane_of(track, timeline), rate,
                      tc_format, offset=rel)
        )

    spine = ""
    for i, (xml, _, _) in enumerate(elements):
        children = nested.get(i, [])
        if not children:
            spine += " " * 10 + xml
        else:
            spine += " " * 10 + _open_tag(xml) + "\n"
            spine += "".join(" " * 12 + c for c in children)
            spine += " " * 10 + _close_tag(xml) + "\n"

    title = _xml_escape(timeline.title)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        f'<fcpxml version="{FCPXML_VERSION}">\n'
        "  <resources>\n"
        f'    <format id="f1" name="FFVideoFormat" frameDuration="{frame_duration}" />\n'
        f"{resources}  </resources>\n"
        "  <library>\n"
        f'    <event name="{title}">\n'
        f'      <project name="{title}">\n'
        f'        <sequence format="f1" tcFormat="{tc_format}">\n'
        "          <spine>\n"
        f"{spine}          </spine>\n"
        "        </sequence>\n"
        "      </project>\n"
        "    </event>\n"
        "  </library>\n"
        "</fcpxml>\n"
    )


def export_fcpxml(timeline: Timeline, output: str | PurePath) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_fcpxml(timeline), encoding="utf-8")
    return output


def _require_exportable(timeline: Timeline) -> None:
    if not timeline.clips:
        raise ValueError("nothing to export: the timeline has no clips")
    problems = timeline.validate()
    if problems:
        raise ValueError(
            "refusing to export a timeline that fails validation: "
            + "; ".join(problems[:3])
        )


# ---------------------------------------------------------------- validators


def _parse_rational(text: str | None) -> tuple[int, int] | None:
    """Parse ``N/Ds`` or the bare ``Ns`` that ``seconds_to_rational`` emits for
    exactly zero."""
    if text is None:
        return None
    m = RATIONAL_RE.match(text.strip())
    if not m:
        return None
    return int(m.group("num")), int(m.group("den") or 1)


def _rational_seconds(text: str | None, rate: timecode.TimecodeRate) -> float | None:
    """Parse a rational and require it to land on a frame boundary of
    ``rate``; otherwise it is not a value this exporter would ever emit."""
    parsed = _parse_rational(text)
    if parsed is None:
        return None
    num, den = parsed
    if den == 0:
        return None
    frames = (num / den) / rate.frame_seconds()
    if abs(frames - round(frames)) > 1e-6:
        return None
    return num / den


def _rate_from_frame_duration(fd: str | None) -> timecode.TimecodeRate:
    parsed = _parse_rational(fd)
    if parsed is None or parsed[1] == 0:
        return timecode.detect_rate(30.0)
    return timecode.detect_rate(parsed[1] / parsed[0])


def validate_fcpxml(text: str, timeline: Timeline | None = None) -> list[str]:
    """Structural checks on an FCPXML document. Returns problems (empty = ok).

    Without a ``timeline`` this validates the document on its own merits:
    well-formedness, the expected element tree, resolvable asset references,
    required attributes, frame-consistent rationals, assets long enough for
    the clips citing them, and a continuous primary storyline. With a
    ``timeline`` it additionally cross-checks every placement against the
    model, so an export that silently disagrees with the edit is caught too.
    """
    problems: list[str] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [f"not well-formed XML: {exc}"]

    if root.tag != "fcpxml":
        return ["root element is not <fcpxml>"]
    if root.get("version") != FCPXML_VERSION:
        problems.append(
            f"fcpxml version is {root.get('version')!r}, expected {FCPXML_VERSION!r}"
        )

    seqs = root.findall(".//sequence")
    if len(seqs) != 1:
        return [f"expected exactly one <sequence>, found {len(seqs)}"]
    seq = seqs[0]

    formats = {f.get("id"): f for f in root.findall("./resources/format")}
    if not formats:
        return ["no <format> declared in <resources>"]
    fmt = formats.get(seq.get("format"))
    if fmt is None:
        return [f"sequence references unknown format {seq.get('format')!r}"]

    rate = _rate_from_frame_duration(fmt.get("frameDuration"))
    if _rational_seconds(fmt.get("frameDuration"), rate) is None:
        problems.append(
            f"format frameDuration {fmt.get('frameDuration')!r} is not a "
            "frame-exact rational"
        )

    assets = {a.get("id"): a for a in root.findall("./resources/asset")}
    if not assets:
        return ["no <asset> declared in <resources>"]

    spine = seq.find("spine")
    if spine is None:
        return ["sequence has no <spine>"]

    cursor = 0.0
    for child in list(spine):
        tag = child.tag
        if tag not in ("asset-clip", "gap"):
            problems.append(f"unexpected spine child <{tag}>")
            continue
        for attr in ("offset", "duration"):
            if child.get(attr) is None:
                problems.append(f"<{tag}> is missing required attribute {attr!r}")
        offset = _rational_seconds(child.get("offset"), rate)
        duration = _rational_seconds(child.get("duration"), rate)
        if offset is None or duration is None:
            problems.append(
                f"<{tag}> has non-rational or non-frame-exact offset/duration "
                f"({child.get('offset')!r} / {child.get('duration')!r})"
            )
            continue
        if child.get("lane") in (None, "", "0"):
            # Primary storyline elements must tile from zero without holes.
            if not _within_frames(offset, cursor, rate, slack=1.5):
                problems.append(
                    f"primary <{tag}> at offset {offset:.3f}s breaks storyline "
                    f"continuity (expected {cursor:.3f}s)"
                )
            cursor = offset + duration
        if tag == "asset-clip":
            ref = child.get("ref", "")
            asset = assets.get(ref)
            if asset is None:
                problems.append(f"asset-clip references unknown asset {ref!r}")
            for attr in ("start", "tcFormat"):
                if child.get(attr) is None:
                    problems.append(f"asset-clip is missing required attribute {attr!r}")
            if child.get("tcFormat") not in ("DF", "NDF"):
                problems.append(
                    f"asset-clip tcFormat is {child.get('tcFormat')!r}, expected DF/NDF"
                )
            start = _rational_seconds(child.get("start"), rate)
            if start is None:
                problems.append(
                    f"asset-clip start {child.get('start')!r} is not frame-exact"
                )
            if asset is not None and start is not None:
                asset_start = _rational_seconds(asset.get("start"), rate) or 0.0
                asset_dur = _rational_seconds(asset.get("duration"), rate) or 0.0
                if start + duration > asset_start + asset_dur + 1.5 * rate.frame_seconds():
                    problems.append(
                        f"asset-clip reads past the end of asset {ref} "
                        f"(start {start:.3f}s + {duration:.3f}s > asset extent "
                        f"{asset_start + asset_dur:.3f}s)"
                    )
        for sub in child:
            if sub.tag != "asset-clip":
                problems.append(f"unexpected nested element <{sub.tag}> in <{tag}>")
            elif sub.get("lane") in (None, "", "0"):
                problems.append("connected clip must declare a non-zero lane")

    if timeline is not None:
        problems.extend(_cross_check_fcpxml(root, timeline, rate))

    return problems


def _cross_check_fcpxml(
    root: ET.Element, timeline: Timeline, rate: timecode.TimecodeRate
) -> list[str]:
    problems: list[str] = []
    asset_ids = _asset_id_map(timeline)
    primary = _primary_clips(timeline)
    placed = [c for c in root.findall(".//asset-clip") if c.get("lane") in (None, "", "0")]
    if len(placed) != len(primary):
        problems.append(
            f"primary storyline carries {len(placed)} clips, the timeline has {len(primary)}"
        )
    for clip, elem in zip(primary, placed):
        if elem.get("ref") != asset_ids[clip.source.key]:
            problems.append(f"clip {clip.name!r} points at the wrong asset")
        want_start = timecode.parse_smpte(clip.source.start_timecode, clip.source.fps) + clip.source_in
        for attr, want in (("offset", clip.timeline_in), ("start", want_start), ("duration", clip.duration)):
            got = _rational_seconds(elem.get(attr), rate)
            if not _within_frames(got, want, rate):
                problems.append(
                    f"clip {clip.name!r} {attr} is {elem.get(attr)!r}, expected the "
                    f"timeline value {want:.3f}s"
                )

    # Connected clips: one per non-primary timeline clip, on the right lane.
    connected = [c for c in timeline.clips if c.track != timeline.primary_track.name]
    found = [c for c in root.findall(".//asset-clip") if c.get("lane") not in (None, "", "0")]
    if len(found) != len(connected):
        problems.append(
            f"connected clips: {len(found)} emitted, the timeline has {len(connected)}"
        )
    for clip, elem in zip(connected, found):
        want_lane = _lane_of(
            next(t for t in timeline.tracks if t.name == clip.track), timeline
        )
        if elem.get("lane") != str(want_lane):
            problems.append(
                f"clip {clip.name!r} sits on lane {elem.get('lane')}, expected {want_lane}"
            )
    return problems


def validate_edl(text: str, fps: float | None = None) -> list[str]:
    """Structural checks on a CMX 3600 EDL. Returns problems (empty = ok).

    Timecodes are parsed with ``engine.timecode.parse_smpte``. Source and
    record spans may legitimately carry different frame rates, so each is
    parsed with the rate its own separator implies (``;`` → 29.97 drop,
    ``:`` → 30 non-drop); what is checked is that the two *durations* agree,
    which is the invariant that makes an EDL an honest edit list.

    CMX 3600 carries no frame-rate field, so a ``:`` separator is read as 30
    fps — and this exporter writes whatever the timeline's rate actually is.
    For a 24 fps cut the guessed rate is wrong and the frame spans come out a
    few frames apart even though the list is correct, so a caller that knows
    the rate should pass ``fps`` and both spans are then measured on that
    grid. ``render_edl`` and this validator must agree on it; the rate is
    otherwise unknowable from the text.
    """
    problems: list[str] = []
    lines = text.splitlines()
    if not lines:
        return ["empty EDL"]
    if not lines[0].startswith("TITLE:"):
        problems.append("first line must be a TITLE: header")

    title_idx = next((i for i, ln in enumerate(lines) if ln.startswith("TITLE:")), -1)
    fcm_idx = next((i for i, ln in enumerate(lines) if ln.startswith("FCM:")), None)
    if fcm_idx is None:
        problems.append("missing FCM: frame-mode header")
    elif title_idx >= 0 and fcm_idx < title_idx:
        problems.append("FCM: header must follow the TITLE: header")

    event_re = re.compile(
        r"^(?P<num>\d{3,4})\s+(?P<reel>\S+)\s+(?P<chan>[VA]\d*)\s+(?P<trans>\S+)\s+"
        r"(?P<si>\d+:\d{2}:\d{2}[;:]\d{2})\s+(?P<so>\d+:\d{2}:\d{2}[;:]\d{2})\s+"
        r"(?P<ri>\d+:\d{2}:\d{2}[;:]\d{2})\s+(?P<ro>\d+:\d{2}:\d{2}[;:]\d{2})\s*$"
    )

    # Block the lines so an annotation can be attributed to its event.
    blocks: list[dict] = []
    for line_no, ln in enumerate(lines, 1):
        m = event_re.match(ln)
        if m:
            blocks.append({"event": m, "from_clip": False})
        elif blocks and ln.strip().startswith("* FROM CLIP:"):
            blocks[-1]["from_clip"] = True
        elif re.match(r"^\d{3,4}\s+\S+\s+[VA]", ln):
            # A line that is shaped like an event but cannot be parsed is a
            # corrupted event, not a comment, and must not be skipped quietly.
            problems.append(
                f"line {line_no}: does not parse as a CMX 3600 event: {ln.strip()[:70]!r}"
            )
    events = [b["event"] for b in blocks]
    if not events:
        problems.append("no valid event lines found")

    parsed: list[dict] = []
    for m in events:
        num = int(m.group("num"))
        if m.group("trans") != "C":
            problems.append(
                f"event {num}: transition {m.group('trans')!r} is not a cut (C); "
                "dissolves/wipes are unsupported by this exporter"
            )
        for field in ("si", "so", "ri", "ro"):
            if not SMPTE_RE.match(m.group(field)):
                problems.append(f"event {num}: malformed timecode {m.group(field)!r}")
        # When the caller supplies the rate the list was written on, it governs
        # the parse too, not just the frame conversion: reading a 24 fps cut as
        # 30 fps produces seconds that no longer land on the 24-frame grid, and
        # a correct list then reports a spurious retime.
        rate = fps if fps is not None else _sep_rate(m.group("si"))
        si, so = timecode.parse_smpte(m.group("si"), rate), timecode.parse_smpte(m.group("so"), rate)
        rrate = fps if fps is not None else _sep_rate(m.group("ri"))
        ri, ro = timecode.parse_smpte(m.group("ri"), rrate), timecode.parse_smpte(m.group("ro"), rrate)
        parsed.append(
            dict(
                num=num, reel=m.group("reel"), chan=m.group("chan"),
                s_sep=m.group("si"), r_sep=m.group("ri"),
                si=m.group("si"), so=m.group("so"), s_in=si, s_out=so, r_in=ri, r_out=ro,
            )
        )

    for e in parsed:
        src_dur, rec_dur = e["s_out"] - e["s_in"], e["r_out"] - e["r_in"]
        if src_dur <= 0:
            problems.append(f"event {e['num']}: source duration is not positive")
        if rec_dur <= 0:
            problems.append(f"event {e['num']}: record duration is not positive")
        # Durations are compared on the frame grid. Differencing drop-frame
        # timecodes at different origins is *not* a duration: two equal lengths
        # sitting at 01:00:00;00 and 00:00:00;00 differ by the drop count that
        # accumulated over the first hour, so second differences would lie. On
        # a common rate the frame counts must agree exactly; across rates the
        # tolerance is one frame at the coarser one.
        s_rate = fps if fps is not None else _sep_rate(e["s_sep"])
        r_rate = fps if fps is not None else _sep_rate(e["r_sep"])
        s_frames = _frame_count(e["s_out"], s_rate) - _frame_count(e["s_in"], s_rate)
        r_frames = _frame_count(e["r_out"], r_rate) - _frame_count(e["r_in"], r_rate)
        if s_rate == r_rate:
            if s_frames != r_frames:
                problems.append(
                    f"event {e['num']}: source span is {s_frames} frames but record "
                    f"span is {r_frames} frames (a re-timed event is not "
                    "representable)"
                )
        else:
            tolerance = max(
                timecode.detect_rate(s_rate).frame_seconds(),
                timecode.detect_rate(r_rate).frame_seconds(),
            )
            if abs(src_dur - rec_dur) > tolerance:
                problems.append(
                    f"event {e['num']}: source duration {src_dur:.3f}s differs from "
                    f"record duration {rec_dur:.3f}s by more than one frame"
                )

    # Record times must advance and never overlap within a channel.
    by_channel: dict[str, list[dict]] = {}
    for e in parsed:
        by_channel.setdefault(e["chan"], []).append(e)
    for chan, row in by_channel.items():
        row.sort(key=lambda e: e["r_in"])
        for a, b in zip(row, row[1:]):
            if b["r_in"] < a["r_out"] - 1e-3:
                problems.append(
                    f"channel {chan}: event {a['num']} record span overlaps event {b['num']}"
                )

    nums = [e["num"] for e in parsed]
    if nums != sorted(nums):
        problems.append("event numbers are not in ascending order")
    if nums and nums[0] != 1:
        problems.append(f"first event number is {nums[0]}, expected 1")

    annotated = {int(b["event"].group("num")) for b in blocks if b["from_clip"]}
    missing = sorted({int(m.group("num")) for m in events} - annotated)
    if missing:
        problems.append(
            f"events {missing} lack a '* FROM CLIP:' annotation; their reels "
            "would not trace back to a file"
        )

    if fcm_idx is not None and parsed:
        # Match the mode exactly: "NON-DROP FRAME" contains "DROP FRAME".
        mode = lines[fcm_idx].split(":", 1)[1].strip().upper()
        declared_drop = mode == "DROP FRAME"
        used_drop = any(";" in (e["si"] + e["so"]) for e in parsed)
        if declared_drop != used_drop:
            problems.append(
                f"FCM header declares {'drop' if declared_drop else 'non-drop'} frame "
                f"but events use {';' if used_drop else ':'} separators"
            )
    return problems


def independent_parse_edl(text: str) -> tuple[bool, list[str]]:
    """Parse an EDL with a third-party CMX 3600 reader, not our own.

    Returns (ran, problems). ``ran`` is False when the reader is absent —
    The reader is release-critical. A missing reader is therefore returned as
    a problem, never as a successful-but-unverified result. ``ran`` remains in
    the return shape for compatibility with callers, but is always True.

    ``validate_edl`` above and this function read the text with different
    parsers for the same reason: a single regex that both emits and checks a
    format is self-consistent by construction and cannot see its own mistakes.
    """
    try:
        from pycmx import parse_cmx3600
    except ImportError as exc:
        return True, [
            "required independent EDL reader pycmx is unavailable: "
            f"{type(exc).__name__}: {exc}"
        ]
    try:
        parsed = parse_cmx3600(io.StringIO(text))
        list(parsed.events)
    except Exception as exc:  # an independent reader rejecting it is the point
        return True, [f"pycmx rejected the EDL: {type(exc).__name__}: {exc}"]
    return True, []


def independent_parse_fcpxml(text: str) -> tuple[bool, list[str]]:
    """Parse an FCPXML with a third-party reader, not our own.

    The mirror of ``independent_parse_edl``. The reader is release-critical;
    absence is a problem, never a successful-but-unverified result.

    The reader is the OpenTimelineIO FCPXML adapter, vendored under
    ``third_party/otio_fcpxml_adapter/`` (Apache-2.0, see
    ``third_party/PROVENANCE.md``). It was chosen over the stale upstream
    adapter for one specific reason: it resolves an asset's media through
    ``<media-rep kind="original-media">`` rather than reading ``src`` off the
    ``<asset>`` element, and SelekyT writes ``src`` on the media-rep. A reader
    that only checks the attribute parses the whole spine and resolves no media
    at all, which is the worst possible outcome — a parse that looks successful
    and lands offline.

    That is why the checks below are what they are rather than a bare "it
    parsed". A document that reads back as clips with ``MissingReference``
    media has not been validated; it has been read by a library while the thing
    an editor actually needs — the file each cut points at — was dropped. So a
    spine with no clips is a problem, and so is any clip whose media reference
    is not an ``ExternalReference`` with a non-empty target.
    """
    try:
        import opentimelineio as otio  # type: ignore[import-not-found]
        from third_party.otio_fcpxml_adapter.fcpxml import FcpxXml
    except ImportError as exc:
        return True, [
            "required independent FCPXML reader is unavailable: "
            f"{type(exc).__name__}: {exc}"
        ]
    try:
        obj = FcpxXml(text).to_otio()
        clips = list(obj.find_children(otio.schema.Clip))
    except Exception as exc:  # an independent reader rejecting it is the point
        return True, [f"the FCPXML reader rejected the document: "
                      f"{type(exc).__name__}: {exc}"]
    problems: list[str] = []
    if not clips:
        problems.append("the reader parsed the document but found no clips, so "
                        "no cut in it was read back by an independent parser")
    unlinked = []
    for clip in clips:
        ref = clip.media_reference
        if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
            continue
        unlinked.append(repr(clip.name))
    if unlinked:
        problems.append(
            f"{len(unlinked)} of {len(clips)} clip(s) did not resolve to a "
            f"media reference with a target URL: {unlinked}. The reader parsed "
            "the spine but the media the cuts point at did not come back, "
            "which means the export lands offline in an NLE"
        )
    return True, problems


def _sep_rate(sep: str) -> float:
    """Rate implied by a timecode separator. ``;`` is read as 29.97 drop-frame;
    a 59.94 drop-frame EDL is not distinguished by this validator and is a
    documented limitation, since the EDL body carries no frame-rate field."""
    return 29.97 if sep == ";" else 30.0


def _frame_count(seconds: float, fps: float) -> int:
    """Whole frames at ``fps`` that a parsed timecode value represents."""
    return int(round(seconds / timecode.detect_rate(fps).frame_seconds()))


def _within_frames(
    got: float | None, want_seconds: float, rate: timecode.TimecodeRate, slack: float = 1.5
) -> bool:
    """Compare a parsed value to a timeline second count on the frame grid.

    Each value is snapped to the grid independently when emitted, so an
    accumulated sum can sit one frame off the snapped total; ``slack`` frames
    of tolerance covers exactly that and nothing more.
    """
    if got is None:
        return False
    return abs(got - want_seconds) <= slack * rate.frame_seconds()


def export(
    timeline: Timeline,
    output_dir: str | PurePath,
    formats: tuple[str, ...] = ("edl", "fcpxml"),
    stem: str | None = None,
) -> dict[str, Path]:
    """Export an edited timeline to editable interchange formats.

    Only formats this module can generate *and validate* are offered. Returns
    the written paths keyed by format name.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or timeline.title or "edit"
    out: dict[str, Path] = {}
    for fmt in formats:
        if fmt == "edl":
            out["edl"] = export_edl(timeline, output_dir / f"{stem}.edl")
        elif fmt == "fcpxml":
            out["fcpxml"] = export_fcpxml(timeline, output_dir / f"{stem}.fcpxml")
        else:
            raise ValueError(
                f"unsupported export format {fmt!r}; this build emits edl and fcpxml"
            )
    return out


__all__ = [
    "export",
    "export_edl",
    "export_fcpxml",
    "render_edl",
    "render_fcpxml",
    "validate_edl",
    "validate_fcpxml",
]
