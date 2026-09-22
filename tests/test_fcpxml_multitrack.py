"""Multi-track FCPXML export tests.

Mirrors the behaviors pinned in `rust/core/src/export_fcpxml.rs` tests so the
Python port is independently pinned: lane fan-out, coverage trimming, record
cursor, per-track timecode, drop-frame stamping, escaping, and the refusal to
render a timeline with nothing kept. Byte-identical agreement with the Rust
sidecar is asserted in `tests/test_sidecar_parity.py`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from engine import fcpxml, timecode
from engine.jumpcut import CutList, CutSegment, SegmentType, cutlist_from_wire


def _cl(intervals, duration: float) -> CutList:
    return cutlist_from_wire(intervals, duration)


def _keep(start, end):
    return {"start": start, "end": end, "kind": "keep"}


def _remove(start, end):
    return {"start": start, "end": end, "kind": "remove"}


def _track(name, duration, **kw):
    return fcpxml.Track(name=name, duration=duration, **kw)


def _params(reference, linked=None, *, fps=25.0, title="demo"):
    return fcpxml.FcpxmlParams(fps=fps, title=title, reference=reference, linked=linked or [])


def _connected(xml: str) -> list[str]:
    """Every `<asset-clip>` that carries a `lane` (the connected clips)."""
    return [
        line.strip()
        for line in xml.splitlines()
        if line.strip().startswith("<asset-clip") and "lane=" in line
    ]


def _two_keeps() -> CutList:
    # [0,2) keep, [2,4) remove, [4,6) keep, inside a 10s source.
    return _cl(
        [_keep(0.0, 2.0), _remove(2.0, 4.0), _keep(4.0, 6.0), _remove(6.0, 10.0)],
        10.0,
    )


def _reference(duration: float, **kw) -> fcpxml.Track:
    return _track("reference", duration, **kw)


def _linked(name: str, duration: float, **kw) -> fcpxml.Track:
    return _track(name, duration, **kw)


# ── single track: the behavior that predates linked tracks ──

def test_renders_two_keeps_at_2997_drop_frame():
    cutlist = _cl([_keep(0.0, 1.0), _remove(1.0, 2.0), _keep(2.0, 3.0)], 3.0)
    xml = fcpxml.render(
        cutlist,
        _params(
            _reference(3.0, start_timecode="01:00:00;00"),
            fps=29.97,
        ),
    )
    assert 'tcFormat="DF"' in xml
    assert xml.count("<asset-clip") == 2
    assert 'frameDuration="1001/30000s"' in xml
    # 01:00:00;00 at 29.97 DF is 107892 continuous frames (108000 display
    # minus 108 dropped), which as a rational is 107892 * 1001 / 30000.
    assert 'start="107999892/30000s"' in xml


def test_ndf_when_no_drop_frame():
    xml = fcpxml.render(_cl([_keep(0.0, 1.0)], 1.0), _params(_reference(1.0)))
    assert 'tcFormat="NDF"' in xml
    assert 'frameDuration="1/25s"' in xml


def test_xml_escapes_names():
    xml = fcpxml.render(
        _cl([_keep(0.0, 1.0)], 1.0),
        fcpxml.FcpxmlParams(fps=25.0, title="a & b <c>", reference=_reference(1.0)),
    )
    assert "a &amp; b &lt;c&gt;" in xml


def test_marks_audio_absent_when_source_has_no_audio():
    xml = fcpxml.render(
        _cl([_keep(0.0, 1.0)], 1.0),
        _params(_reference(1.0, has_audio=False)),
    )
    assert 'hasAudio="0"' in xml
    assert "audioSources=" not in xml


def test_marks_video_absent_for_an_audio_only_track():
    xml = fcpxml.render(
        _cl([_keep(0.0, 1.0)], 1.0),
        _params(_reference(1.0, has_video=False)),
    )
    assert 'hasVideo="0"' in xml
    assert "videoSources=" not in xml


def test_refuses_to_render_a_timeline_with_nothing_kept():
    with pytest.raises(ValueError, match="nothing to keep"):
        fcpxml.render(_cl([_remove(0.0, 3.0)], 3.0), _params(_reference(3.0)))


def test_asset_src_is_a_file_uri_when_a_path_is_given(tmp_path: Path):
    media = tmp_path / "cam a.mp4"
    media.write_bytes(b"")
    xml = fcpxml.render(
        _cl([_keep(0.0, 1.0)], 1.0),
        _params(_reference(1.0, path=str(media))),
    )
    # Spaces are percent-encoded; an absolute path becomes a file:// URI.
    assert f'src="file://{media.parent}/cam%20a.mp4"' in xml


def test_asset_src_passthrough_for_url_paths():
    xml = fcpxml.render(
        _cl([_keep(0.0, 1.0)], 1.0),
        _params(_reference(1.0, path="https://example.com/v.mp4")),
    )
    assert 'src="https://example.com/v.mp4"' in xml


def test_an_unresolvable_asset_path_fails_instead_of_shipping_a_relative_uri():
    """``engine/nle_export._to_file_uri`` raises on a path it cannot make
    absolute, and this emitter has to agree: a relative-looking ``src`` imports
    with every clip offline because it resolves against the timeline file's own
    directory, not the media's. A live run hit that — the timeline directory
    moved, resolve() raised, and the fallback wrote a URI that *looked*
    complete. TECHNICAL_AUDIT row #9 once recorded the two emitters as having
    divergent policy here (raise vs silent fallback); this pins the alignment,
    so a silent fallback reintroduced on either side fails this test."""
    from engine.nle_export import _to_file_uri as timeline_uri

    def raises(uri):
        with pytest.raises(RuntimeError, match="cannot turn the source path"):
            uri("a\0b.mov")

    raises(fcpxml._to_file_uri)
    raises(timeline_uri)


# ── linked tracks ──

def test_a_linked_video_track_becomes_a_connected_clip_above_the_primary():
    xml = fcpxml.render(_two_keeps(), _params(_reference(10.0), [_linked("cam-b", 10.0)]))
    # One asset per track, and the linked one referenced from every keep.
    assert xml.count("<asset id=") == 2
    clips = _connected(xml)
    assert len(clips) == 2
    assert all('lane="1"' in c for c in clips)
    assert all('ref="r2"' in c for c in clips)


def test_an_audio_only_track_sits_below_the_primary():
    xml = fcpxml.render(
        _two_keeps(),
        _params(_reference(10.0), [_linked("lav", 10.0, has_video=False)]),
    )
    clips = _connected(xml)
    assert len(clips) == 2
    assert all('lane="-1"' in c for c in clips)


def test_lanes_stack_outward_in_list_order():
    # Video lanes climb 1, 2 above the primary storyline; audio lanes descend
    # -1, -2 below it. That is how an NLE lays out V2/V3 and A2/A3, so list
    # order has to survive into the lane numbers.
    xml = fcpxml.render(
        _two_keeps(),
        _params(_reference(10.0), [
            _linked("cam-b", 10.0),
            _linked("lav", 10.0, has_video=False),
            _linked("cam-c", 10.0),
            _linked("room", 10.0, has_video=False),
        ]),
    )
    first_keep = _connected(xml)[:4]

    def lane_of(ref: str) -> str:
        line = next(c for c in first_keep if f'ref="{ref}"' in c)
        return line.split('lane="')[1].split('"')[0]

    assert lane_of("r2") == "1"
    assert lane_of("r3") == "-1"
    assert lane_of("r4") == "2"
    assert lane_of("r5") == "-2"


def test_a_track_that_does_not_reach_a_keep_is_omitted_from_it():
    # The b-roll stops at 3s but the second keep starts at 4s. Emitting a clip
    # there would point the NLE past the end of the media.
    xml = fcpxml.render(_two_keeps(), _params(_reference(10.0), [_linked("cam-b", 3.0)]))
    assert len(_connected(xml)) == 1


def test_a_track_that_only_partly_covers_a_keep_is_trimmed_to_what_it_has():
    # Media runs out 1s into the second keep, so that clip is 1s, not 2s.
    xml = fcpxml.render(_two_keeps(), _params(_reference(10.0), [_linked("cam-b", 5.0)]))
    clips = _connected(xml)
    assert len(clips) == 2
    assert 'duration="50/25s"' in clips[0]
    assert 'duration="25/25s"' in clips[1]


def test_a_track_starting_later_reads_from_earlier_in_its_own_media():
    # Recorder rolled 3s after the camera. At project time 4s the recorder is
    # only 1s into its own file.
    xml = fcpxml.render(
        _two_keeps(),
        _params(_reference(10.0), [_linked("lav", 10.0, offset=3.0)]),
    )
    clips = _connected(xml)
    # Nothing to contribute to the keep at [0,2): it had not started.
    assert len(clips) == 1
    assert 'start="25/25s"' in clips[0]
    assert 'offset="50/25s"' in clips[0]


def test_a_linked_tracks_own_timecode_shifts_where_it_reads_from():
    xml = fcpxml.render(
        _two_keeps(),
        _params(_reference(10.0), [_linked("cam-b", 10.0, start_timecode="00:00:10:00")]),
    )
    assert 'start="250/25s"' in _connected(xml)[0]


def test_connected_clips_ride_the_record_cursor_with_the_primary():
    # The second keep lands 2s into the finished timeline, so both the primary
    # clip and everything hanging off it are offset by 2s.
    xml = fcpxml.render(_two_keeps(), _params(_reference(10.0), [_linked("cam-b", 10.0)]))
    clips = _connected(xml)
    assert 'offset="0s"' in clips[0]
    assert 'offset="50/25s"' in clips[1]


def test_export_fcpxml_single_track_backward_compat(tmp_path: Path):
    out = fcpxml.export_fcpxml(_two_keeps(), tmp_path / "out.fcpxml", source_path="/tmp/x.mp4")
    xml = out.read_text()
    assert out.exists()
    assert '<fcpxml version="1.11">' in xml
    assert 'ref="r1"' in xml
    assert len(_connected(xml)) == 0  # no linked tracks -> no connected clips


def test_export_fcpxml_accepts_linked_tracks(tmp_path: Path):
    out = fcpxml.export_fcpxml(
        _two_keeps(),
        tmp_path / "multi.fcpxml",
        source_path="/tmp/cam.mp4",
        linked=[_linked("lav", 10.0, has_video=False)],
    )
    xml = out.read_text()
    assert len(_connected(xml)) == 2
    assert 'lane="-1"' in xml


def test_kept_intervals_skips_removes():
    cl = _two_keeps()
    assert fcpxml.kept_intervals(cl) == [(0.0, 2.0), (4.0, 6.0)]
