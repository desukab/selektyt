"""FCPXML export — Final Cut Pro XML format from CutList.

Multi-track emitter producing FCPXML 1.11 documents. Ported
behavior-identical from `rust/core/src/export_fcpxml.rs` (adapted from
AutoCut's src-tauri/src/export_fcpxml.rs, reused with the author's
permission — see docs/autocut-provenance.md) so a cutlist rendered on either
side of the hybrid produces the same XML.

Verification status: the EDL half of each pair is structurally validated by
`pycmx`, an independent CMX 3600 reader, and the FCPXML half by the
OpenTimelineIO FCPXML adapter, vendored under
`third_party/otio_fcpxml_adapter/` (Apache-2.0; see
`third_party/PROVENANCE.md`). Both halves are also checked against the
rendered media (see tools/nle_check.py, docs/NLE_COMPATIBILITY.md). The FCPXML
reader is chosen for one property: it resolves an asset's media through
`<media-rep kind="original-media">`, which is where this emitter writes `src`;
the OpenTimelineIO project's own copy of the same adapter reads `src` off the
`<asset>` element instead, parses the whole spine, and returns every clip
unlinked — a false positive in the exact shape a validator must not produce, so
the check is not "it parsed" but "every clip came back as an ExternalReference
with a target URL". No NLE has yet imported one of these documents in this
project's history — do not state that FCP, Resolve or Premiere accept these
files until that import has actually been observed. An independent reader is
not an NLE import and the two are not exchanged for each other.

The critical detail, carried over from AutoCut: DaVinci binds an asset to its
source media by THREE checks (path, source-media start TC, frame rate
format). If the MP4 carries an embedded TC like `15:33:27;24` but the FCPXML
asset starts at `0s`, the relink dialog fires even when the path is correct.

We:
  - Emit the asset's `start` attribute = embedded source TC seconds
  - Shift every asset-clip's `start` by the same offset
  - Stamp `tcFormat="DF"` for NTSC drop-frame, `"NDF"` otherwise

That logic is implemented and covered by the synthetic tests in
``tests/test_fcpxml_multitrack.py``, including the 29.97 drop-frame case and
its 108-frame drop count. What has never happened is that it ran against media
that would exercise it. All 27 committed timelines declare
`asset start="0s"` and `tcFormat="NDF"`, and the two sources whose media is on
this host carry no timecode track at all — neither the webm nor the MP4 has a
TMCD stream, and both report `start_time=0`. The committed transcripts carry no
`source.start_timecode`, so the `;`-in-timecode test that selects drop-frame
has never seen a semicolon on real media. The branch is verified against a
constructed input and unobserved against a real one, which is a narrower gap
than untested, but the observation still matters: an NLE reads ``tcFormat`` as a
statement about the source media's own TC, so the interesting failure — a
document stamping NDF onto DF media — is one this corpus has never been able to
produce.

The export is optional and behind the --export-fcpxml flag.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from engine import timecode
from engine.jumpcut import CutList, SegmentType


@dataclass
class Track:
    """One piece of source media on the timeline.

    The reference track defines project time: project second ``t`` is
    reference source second ``t``. Every other track states where its own
    media begins in that same project time via ``offset``, which is what lets
    one cutlist govern all of them.
    """

    name: str
    duration: float
    has_video: bool = True
    has_audio: bool = True
    """SMPTE timecode this media starts at, if it carries one. Shifts where
    inside the file a clip reads from, independently of ``offset``."""
    start_timecode: str | None = None
    """Project-time second at which this track's media begins. Zero for the
    reference by definition."""
    offset: float = 0.0
    path: str | None = None


@dataclass
class FcpxmlParams:
    fps: float
    title: str
    reference: Track
    """Extra tracks cut in lockstep with the reference. Video tracks stack
    upward from lane 1, audio-only tracks downward from lane -1, in the order
    given."""
    linked: list[Track] = field(default_factory=list)


def kept_intervals(cutlist: CutList) -> list[tuple[float, float]]:
    """The KEEP segments of a CutList, in order (what the Rust core calls
    ``kept_intervals``)."""
    return [
        (s.start, s.end)
        for s in cutlist.segments
        if s.segment_type == SegmentType.KEEP
    ]


def ensure_exportable(cutlist: CutList) -> None:
    """Structural, not a courtesy check: an FCPXML with an empty spine is
    well-formed XML that imports as an empty timeline, so there is no later
    point at which anything would notice."""
    if not kept_intervals(cutlist):
        raise ValueError("nothing to keep: the cutlist removes the whole timeline")


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _to_file_uri(path: str) -> str:
    if path.startswith(("file://", "http://", "https://")):
        return path
    # An NLE relinks a clip by this URI. A *relative* URI silently resolves
    # against the timeline file's own directory, which is almost never where
    # the media lives, so the sequence imports with every clip offline and the
    # user is left facing a relink dialog for a file that never moved. A real
    # run hit that: the timeline directory moved between the download and the
    # export, Path.resolve() raised, and the fallback wrote a relative URI that
    # looked complete. Fail the export with a named path instead.
    try:
        abs_path = str(Path(path).resolve())
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"cannot turn the source path into an absolute file URI, so the "
            f"export would reference media that an NLE cannot relink: {path!r}"
        ) from exc
    encoded = ["file://"]
    for byte in abs_path.encode("utf-8", errors="surrogateescape"):
        # Unreserved per RFC 3986, plus '/' and ':' which are safe in a path
        # and in a Windows drive letter.
        if byte in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~/:":
            encoded.append(chr(byte))
        else:
            encoded.append(f"%{byte:02X}")
    return "".join(encoded)


def _asset_element(track_id: int, track: Track, fps: float, rate: timecode.TimecodeRate) -> str:
    src = _to_file_uri(track.path) if track.path else "source.mp4"
    start = timecode.seconds_to_rational(
        timecode.parse_smpte(track.start_timecode, fps), rate
    )
    duration = timecode.seconds_to_rational(track.duration, rate)
    video_attrs = (
        ' hasVideo="1" videoSources="1"' if track.has_video else ' hasVideo="0"'
    )
    audio_attrs = (
        ' hasAudio="1" audioSources="1" audioChannels="2"'
        if track.has_audio
        else ' hasAudio="0"'
    )
    return (
        f'    <asset id="r{track_id}" name="{_xml_escape(track.name)}" '
        f'start="{start}" duration="{duration}"{video_attrs}{audio_attrs}>\n'
        f'      <media-rep kind="original-media" src="{_xml_escape(src)}"/>\n'
        f"    </asset>\n"
    )


@dataclass
class _ClipTimes:
    offset: float
    start: float
    duration: float


def _clip_attrs(
    track_id: int,
    lane: int | None,
    times: _ClipTimes,
    rate: timecode.TimecodeRate,
    tc_format: str,
) -> str:
    lane_attr = f' lane="{lane}"' if lane is not None else ""
    return (
        f'<asset-clip ref="r{track_id}"{lane_attr} '
        f'offset="{timecode.seconds_to_rational(times.offset, rate)}" '
        f'start="{timecode.seconds_to_rational(times.start, rate)}" '
        f'duration="{timecode.seconds_to_rational(times.duration, rate)}" '
        f'tcFormat="{tc_format}"'
    )


def render(cutlist: CutList, params: FcpxmlParams) -> str:
    """Render a CutList as FCPXML 1.11 with optional linked tracks.

    Output is byte-identical to ``rust/core/src/export_fcpxml.rs::render`` for
    equal inputs — asserted in ``tests/test_sidecar_parity.py``.
    """
    ensure_exportable(cutlist)

    drop_frame = bool(
        params.reference.start_timecode and ";" in params.reference.start_timecode
    )
    rate = timecode.detect_rate(params.fps, drop_frame)
    tc_format = (
        "DF"
        if rate.is_drop_frame and rate.nominal_fps in (30, 60)
        else "NDF"
    )
    safe_title = _xml_escape(params.title)
    frame_duration = f"{rate.frame_duration_num}/{rate.frame_duration_den}s"

    # r1 is the reference; linked tracks follow in list order. Lanes fan out
    # from the primary storyline: video upward, audio-only downward.
    resources = ""
    lanes: list[int] = []
    next_video_lane = 1
    next_audio_lane = -1
    all_tracks = [params.reference, *params.linked]
    for i, track in enumerate(all_tracks):
        resources += _asset_element(i + 1, track, params.fps, rate)
        if i > 0:
            if track.has_video:
                lanes.append(next_video_lane)
                next_video_lane += 1
            else:
                lanes.append(next_audio_lane)
                next_audio_lane -= 1

    reference_tc = timecode.parse_smpte(params.reference.start_timecode, params.fps)
    spine = ""
    # The record cursor runs in whole frames, not seconds. Emitting each clip's
    # offset and duration as a float and rounding them to the frame grid
    # independently lets drift open a one-frame gap between neighbours — small,
    # but enough for an NLE to insert a black frame at every seam. On the real
    # 304-clip cutlist this measured 71 such gaps. Counting frames makes each
    # clip begin exactly where the previous one ended.
    frame_secs = rate.frame_seconds()
    record_frames = 0
    for cut_start, cut_end in kept_intervals(cutlist):
        cut_duration = cut_end - cut_start
        dur_frames = max(1, int(round(cut_duration / frame_secs)))
        clip = _ClipTimes(
            offset=record_frames * frame_secs,
            start=reference_tc + cut_start,
            duration=dur_frames * frame_secs,
        )

        connected = ""
        for i, track in enumerate(params.linked):
            # Project time the track actually has media for, intersected with
            # this keep. A track that rolled late, stopped early, or is simply
            # shorter contributes nothing here rather than pointing the NLE
            # past the end of its file.
            visible_from = max(cut_start, track.offset)
            visible_to = min(cut_end, track.offset + track.duration)
            if visible_to - visible_from <= 0.0:
                continue
            track_tc = timecode.parse_smpte(track.start_timecode, params.fps)
            # Snapped to the same grid as the parent, and clamped to it: a
            # connected clip must not outrun the asset-clip it rides inside.
            rel_frames = int(round((visible_from - cut_start) / frame_secs))
            conn_frames = max(
                1,
                min(
                    dur_frames - rel_frames,
                    int(round((visible_to - visible_from) / frame_secs)),
                ),
            )
            times = _ClipTimes(
                offset=(record_frames + rel_frames) * frame_secs,
                start=track_tc + (visible_from - track.offset),
                duration=conn_frames * frame_secs,
            )
            connected += " " * 12 + _clip_attrs(
                i + 2, lanes[i], times, rate, tc_format
            ) + " />\n"

        if not connected:
            spine += " " * 8 + _clip_attrs(1, None, clip, rate, tc_format) + " />\n"
        else:
            spine += " " * 8 + _clip_attrs(1, None, clip, rate, tc_format) + ">\n"
            spine += connected
            spine += " " * 8 + "</asset-clip>\n"
        record_frames += dur_frames

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        '<fcpxml version="1.11">\n'
        "  <resources>\n"
        f'    <format id="f1" name="FFVideoFormat" frameDuration="{frame_duration}" />\n'
        f"{resources}  </resources>\n"
        "  <library>\n"
        f'    <event name="{safe_title}">\n'
        f'      <project name="{safe_title}">\n'
        f'        <sequence format="f1" tcFormat="{tc_format}">\n'
        "          <spine>\n"
        f"{spine}          </spine>\n"
        "        </sequence>\n"
        "      </project>\n"
        "    </event>\n"
        "  </library>\n"
        "</fcpxml>\n"
    )


def export_fcpxml(
    cutlist: CutList,
    output: str | Path,
    source_path: str = "",
    fps: float = 30.0,
    title: str = "SelekyT Edit",
    linked: list[Track] | None = None,
) -> Path:
    """Export a CutList as FCPXML 1.11.

    Backward-compatible single-track entry point: with no ``linked`` tracks it
    emits the reference media on the primary storyline. Pass ``linked`` (from
    ``engine.multitrack``) to cut extra cameras or an external recorder in
    lockstep with the reference.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    xml = render(
        cutlist,
        FcpxmlParams(
            fps=fps,
            title=title,
            reference=Track(
                name=Path(source_path).name or "source",
                duration=cutlist.original_duration,
                start_timecode=None,
                has_video=True,
                has_audio=True,
                offset=0.0,
                path=source_path or None,
            ),
            linked=linked or [],
        ),
    )
    output.write_text(xml, encoding="utf-8")
    return output


def cutlist_to_simple_xml(cutlist: CutList, output: str | Path) -> Path:
    """Export a simplified XML cutlist for tool-agnostic consumption."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    root = ET.Element("cutlist", {
        "original_duration": f"{cutlist.original_duration:.3f}",
        "keep_duration": f"{cutlist.total_keep_duration:.3f}",
        "remove_duration": f"{cutlist.total_remove_duration:.3f}",
        "compression_ratio": f"{cutlist.compression_ratio:.3f}",
        "segments_removed": str(cutlist.segments_removed),
        "words_removed": str(cutlist.words_removed),
    })

    for seg in cutlist.segments:
        ET.SubElement(root, "segment", {
            "start": f"{seg.start:.3f}",
            "end": f"{seg.end:.3f}",
            "duration": f"{seg.duration:.3f}",
            "type": seg.segment_type.value,
            "reason": seg.reason,
        })

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(output), encoding="unicode", xml_declaration=True)
    return output
