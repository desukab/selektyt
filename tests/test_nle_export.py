"""Editable interchange export — and the structural validation that proves the
format is really checked, not just emitted.

Every "good" export below is fed through the validator and must come back
clean; every "broken" export is a document that has been deliberately
corrupted in a way an NLE would choke on, and the validator must say so.
That is the evidence the formats are validated rather than trusted.
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from engine import timecode
from engine.nle_export import (
    export,
    export_edl,
    render_edl,
    render_fcpxml,
    validate_edl,
    validate_fcpxml,
)
from tests.timeline_fixtures import (
    gapped_timeline,
    multitrack_timeline,
    telugu_timeline,
    two_clip_timeline,
)

SMPTE = r"\d{2}:\d{2}:\d{2}[;:]\d{2}"


# --------------------------------------------------------------------- EDL


@pytest.mark.parametrize("tl_name", ["two_clip", "gapped", "multitrack", "telugu"])
def test_edl_is_structurally_valid_and_round_trips_its_own_validator(tl_name, tmp_path):
    tl = {
        "two_clip": two_clip_timeline,
        "gapped": gapped_timeline,
        "multitrack": multitrack_timeline,
        "telugu": telugu_timeline,
    }[tl_name]()
    path = export_edl(tl, tmp_path / "edit.edl")
    text = path.read_text(encoding="ascii")
    assert validate_edl(text) == [], validate_edl(text)


def test_edl_header_and_shape():
    text = render_edl(two_clip_timeline())
    lines = text.splitlines()
    assert lines[0].startswith("TITLE:")
    assert lines[0].endswith("Two Clip Edit")
    fcm = next(ln for ln in lines if ln.startswith("FCM:"))
    assert fcm == "FCM: DROP FRAME"  # the fixture media carries ';' timecode
    events = [ln for ln in lines if re.match(r"^\d{3}\s", ln)]
    assert len(events) == 4  # V + A for each of two clips
    for ln in events:
        assert re.fullmatch(
            rf"\d{{3}}\s+\S+\s+[VA]\s+C\s+{SMPTE}\s+{SMPTE}\s+{SMPTE}\s+{SMPTE}\s*", ln
        )


def test_edl_channel_blocks_share_an_event_number():
    text = render_edl(two_clip_timeline())
    lines = [ln for ln in text.splitlines() if re.match(r"^\d{3}\s", ln)]
    numbers = [ln.split()[0] for ln in lines]
    # A linked AV clip is a V block plus an A block under one event number.
    assert numbers == ["001", "001", "002", "002"]
    assert "M2" in text and "* FROM CLIP:" in text


def test_edl_assigns_one_channel_code_per_lane():
    text = render_edl(multitrack_timeline())
    codes = [ln.split()[2] for ln in text.splitlines() if re.match(r"^\d{3}\s", ln)]
    # Primary AV clip -> V and A; the B-roll lane -> V2 only; the recorder
    # lane -> A1. No two lanes may collapse onto the same channel code.
    assert "V2" in codes and "A1" in codes
    assert codes.count("V") == codes.count("A") == 2


def test_edl_is_ascii_even_with_a_unicode_title_and_clip_names(tmp_path):
    """Telugu fixture: the EDL body must stay ASCII so the default encoder
    never fails, and the substitution must be announced, not silent."""
    path = export_edl(telugu_timeline(), tmp_path / "telugu_edit.edl")
    text = path.read_text(encoding="ascii")
    assert text.isascii()
    assert "is not ASCII" in text
    assert validate_edl(text) == []


def test_edl_source_pair_matches_the_clips_own_timecode():
    """What the EDL declares as source in/out is exactly what the timeline
    model reports for the same clip, so an editor can walk an event back to
    footage."""
    tl = two_clip_timeline()
    lines = render_edl(tl).splitlines()
    events = [ln for ln in lines if re.match(r"^\d{3}\s", ln)]
    first = events[0].split()
    assert first[4] == tl.clips[0].source_timecode_in()
    assert first[5] == tl.clips[0].source_timecode_out()


def test_edl_non_drop_when_the_media_is_non_drop():
    tl = telugu_timeline()  # 25fps, ':' timecode
    text = render_edl(tl)
    assert "FCM: NON-DROP FRAME" in text
    assert validate_edl(text) == []


def test_edl_reel_names_are_ascii_and_unique():
    text = render_edl(multitrack_timeline())
    reels = {ln.split()[1] for ln in text.splitlines() if re.match(r"^\d{3}\s", ln)}
    assert reels, "reels must be named"
    # 8 characters is the CMX 3600 standard, not an arbitrary tidy limit.
    # Premiere's 32-character reel names are a non-default extension that does
    # not round-trip cleanly, and Resolve's conform "requires reel names and
    # accurate timecode" — a longer name is not a superset here, it is a
    # different format. See docs/NLE_INTERCHANGE.md.
    assert all(r.isascii() and len(r) <= 8 for r in reels)


def test_edl_refuses_an_unvalidated_timeline():
    from engine.timeline import Clip, SourceMedia, Timeline

    media = SourceMedia(path="a.mp4", fps=30.0, duration=10.0)
    tl = Timeline(clips=[Clip(media, 0.0, 5.0, 0.0, 3.0)])  # re-timed
    with pytest.raises(ValueError, match="fails validation"):
        render_edl(tl)
    with pytest.raises(ValueError, match="nothing to export"):
        render_edl(Timeline())


# ------------------------------------------------------- EDL negative tests


def test_validator_catches_a_retimed_event():
    good = render_edl(two_clip_timeline())
    # Shorten the record out of event 2: the record span no longer matches the
    # source span, which is exactly what a bad conform looks like.
    needle = "00:00:15;00"
    assert needle in good, "the corruption must target a real token"
    bad = good.replace(needle, "00:00:12;00")
    problems = validate_edl(bad)
    assert problems, "a re-timed event must fail validation"
    assert any("frames" in p for p in problems)


def test_validator_catches_a_missing_fcm_header():
    good = render_edl(two_clip_timeline())
    bad = "\n".join(ln for ln in good.splitlines() if not ln.startswith("FCM:"))
    assert "missing FCM" in " ".join(validate_edl(bad))


def test_validator_catches_a_missing_from_clip_annotation():
    good = render_edl(two_clip_timeline())
    bad = "\n".join(ln for ln in good.splitlines() if "FROM CLIP" not in ln)
    problems = validate_edl(bad)
    assert any("FROM CLIP" in p for p in problems)


def test_validator_catches_out_of_order_event_numbers():
    good = render_edl(two_clip_timeline())
    bad = good.replace("\n001  ", "\n003  ")
    assert bad != good
    assert "ascending" in " ".join(validate_edl(bad))


def test_validator_catches_a_frame_mode_lie():
    good = render_edl(two_clip_timeline())
    bad = good.replace("FCM: DROP FRAME", "FCM: NON-DROP FRAME")
    assert any("FCM header declares" in p for p in validate_edl(bad))


def test_validator_catches_a_malformed_event_line():
    good = render_edl(two_clip_timeline())
    bad = good.replace("001  INTERVIE V", "001  INTERVIE V X")
    assert any(p for p in validate_edl(bad))


def test_validator_catches_a_malformed_timecode():
    good = render_edl(two_clip_timeline())
    bad = good.replace("00:00:00;00 00:00:10;00", "00:00:0X;00 00:00:10;00", 1)
    assert bad != good
    # The corrupted line no longer parses as an event, so it must be reported
    # rather than dropped on the floor.
    assert any("does not parse as a CMX 3600 event" in p for p in validate_edl(bad))


def test_validator_rejects_garbage():
    assert validate_edl("") != []
    assert validate_edl("not an edl at all") != []


# ------------------------------------------------------------------ FCPXML


@pytest.mark.parametrize("tl_name", ["two_clip", "gapped", "multitrack", "telugu"])
def test_fcpxml_is_well_formed_and_valid(tl_name):
    tl = {
        "two_clip": two_clip_timeline,
        "gapped": gapped_timeline,
        "multitrack": multitrack_timeline,
        "telugu": telugu_timeline,
    }[tl_name]()
    xml = render_fcpxml(tl)
    ET.fromstring(xml)  # raises if not well formed
    assert validate_fcpxml(xml) == [], validate_fcpxml(xml)
    # And it agrees with the model that produced it.
    assert validate_fcpxml(xml, tl) == [], validate_fcpxml(xml, tl)


def test_fcpxml_declares_one_asset_per_source_media():
    xml = render_fcpxml(multitrack_timeline())
    root = ET.fromstring(xml)
    assets = root.findall("./resources/asset")
    assert len(assets) == 3
    assert {a.get("name") for a in assets} == {
        "interview.mp4", "broll.mov", "recorder.wav"
    }
    fmt = root.find("./resources/format")
    assert fmt.get("frameDuration") == "1001/30000s"  # 29.97 sequence


def test_fcpxml_primary_clips_match_the_timeline():
    tl = two_clip_timeline()
    root = ET.fromstring(render_fcpxml(tl))
    primary = [c for c in root.findall(".//asset-clip") if c.get("lane") in (None, "0", "")] \
        or [c for c in root.findall("./library/event/project/sequence/spine/asset-clip")]
    assert len(primary) == 2
    # Each value is the timeline's seconds snapped to the sequence grid, so
    # agreement is to the frame, not to the microsecond.
    slack = 1.5 * timecode.detect_rate(29.97).frame_seconds()
    for clip, elem in zip(tl.clips, primary):
        assert abs(_rational_sec(elem.get("offset")) - clip.timeline_in) < slack
        assert abs(_rational_sec(elem.get("duration")) - clip.duration) < slack
        want_start = timecode.parse_smpte(clip.source.start_timecode, clip.source.fps) + clip.source_in
        assert abs(_rational_sec(elem.get("start")) - want_start) < slack


def _rational_sec(text):
    m = re.match(r"^(\d+)(?:/(\d+))?s$", text)
    return int(m.group(1)) / (int(m.group(2)) if m.group(2) else 1)


def test_fcpxml_fills_primary_holes_with_gaps():
    xml = render_fcpxml(gapped_timeline())
    root = ET.fromstring(xml)
    spine = root.find("./library/event/project/sequence/spine")
    tags = [c.tag for c in spine]
    # The 4-9s hole must be an explicit gap between the two clips.
    assert tags == ["asset-clip", "gap", "asset-clip"]
    gaps = spine.findall("gap")
    slack = 1.5 * timecode.detect_rate(29.97).frame_seconds()
    assert abs(_rational_sec(gaps[0].get("duration")) - 5.0) < slack
    assert abs(_rational_sec(gaps[0].get("offset")) - 4.0) < slack


def test_fcpxml_puts_connected_clips_on_lanes():
    root = ET.fromstring(render_fcpxml(multitrack_timeline()))
    connected = [c for c in root.findall(".//asset-clip") if c.get("lane") not in (None, "0", "")]
    assert len(connected) == 2  # V2 b-roll and A1 sound
    lanes = {c.get("ref"): int(c.get("lane")) for c in connected}
    root_assets = {a.get("id"): a.get("name") for a in root.findall("./resources/asset")}
    by_name = {name: lane for ref, lane in lanes.items() for name in [root_assets[ref]]}
    assert by_name["broll.mov"] == 1   # video lanes climb
    assert by_name["recorder.wav"] == -1  # audio lanes descend


def test_fcpxml_connected_clips_are_nested_under_a_covering_element():
    root = ET.fromstring(render_fcpxml(multitrack_timeline()))
    spine = root.find("./library/event/project/sequence/spine")
    # Every connected clip has a parent that is a spine element covering it.
    for parent in list(spine):
        for child in parent:
            assert child.tag == "asset-clip"
            parent_offset = _rational_sec(parent.get("offset"))
            parent_end = parent_offset + _rational_sec(parent.get("duration"))
            child_offset = _rational_sec(child.get("offset"))
            assert parent_offset - 1e-6 <= child_offset < parent_end


def test_fcpxml_refuses_an_unvalidated_timeline():
    from engine.timeline import Clip, SourceMedia, Timeline

    media = SourceMedia(path="a.mp4", fps=30.0, duration=10.0)
    tl = Timeline(clips=[Clip(media, 0.0, 5.0, 0.0, 3.0)])
    with pytest.raises(ValueError, match="fails validation"):
        render_fcpxml(tl)
    with pytest.raises(ValueError, match="nothing to export"):
        render_fcpxml(Timeline())


# --------------------------------------------------- FCPXML negative tests


def test_validator_catches_malformed_xml():
    problems = validate_fcpxml('<fcpxml version="1.11"><spine></fcpxml>')
    assert any("not well-formed" in p for p in problems)


def test_validator_catches_a_wrong_root_element():
    problems = validate_fcpxml('<?xml version="1.0"?><timeline><sequence/></timeline>')
    assert any("root element" in p for p in problems)


def test_validator_catches_a_dangling_asset_reference():
    xml = render_fcpxml(two_clip_timeline())
    bad = xml.replace('ref="r1"', 'ref="rX"')
    assert any("unknown asset" in p for p in validate_fcpxml(bad))


def test_validator_catches_a_non_frame_rational():
    xml = render_fcpxml(two_clip_timeline())
    # 1001/60000s is half a frame at 29.97 — not a value this emitter makes.
    bad = xml.replace('duration="300300/30000s"', 'duration="1001/60000s"')
    assert any(p for p in validate_fcpxml(bad))


def test_validator_catches_a_clip_reading_past_the_asset_end():
    from engine.timeline import Clip, SourceMedia, Timeline, Track

    media = SourceMedia(path="a.mp4", fps=30.0, duration=5.0)
    tl = Timeline(clips=[Clip(media, 0.0, 4.0, 0.0, 4.0)], tracks=[Track("V1", "video")])
    xml = render_fcpxml(tl)
    # Inflate the clip duration past the asset's 5s extent.
    bad = xml.replace('duration="120/30s"', 'duration="300/30s"')
    assert any("reads past the end" in p for p in validate_fcpxml(bad))


def test_validator_catches_a_broken_storyline():
    xml = render_fcpxml(two_clip_timeline())
    # Move the second clip so the primary spine no longer tiles.
    bad = xml.replace('offset="300300/30000s" start="109200091/30000s"',
                      'offset="900900/30000s" start="109200091/30000s"')
    assert any("continuity" in p for p in validate_fcpxml(bad))


def test_validator_catches_a_clip_that_disagrees_with_the_timeline():
    xml = render_fcpxml(two_clip_timeline())
    bad = xml.replace('duration="300300/30000s"', 'duration="600600/30000s"', 1)
    problems = validate_fcpxml(bad, two_clip_timeline())
    assert any("duration is" in p for p in problems)


def test_validator_catches_a_connected_clip_without_a_lane():
    xml = render_fcpxml(multitrack_timeline())
    bad = re.sub(r'(<asset-clip ref="r\d") lane="-?\d+"', r"\1", xml, count=1)
    assert bad != xml
    assert any("non-zero lane" in p for p in validate_fcpxml(bad))


def test_validator_catches_a_missing_spine():
    xml = render_fcpxml(two_clip_timeline())
    bad = xml.replace("<spine>", "<spool>").replace("</spine>", "</spool>")
    assert any("no <spine>" in p for p in validate_fcpxml(bad))


# --------------------------------------------------------------- packaging


def test_export_writes_both_formats_and_rejects_unknown(tmp_path):
    tl = two_clip_timeline()
    paths = export(tl, tmp_path / "out", formats=("edl", "fcpxml"))
    assert set(paths) == {"edl", "fcpxml"}
    assert all(p.is_file() for p in paths.values())
    assert validate_edl(paths["edl"].read_text(encoding="ascii")) == []
    assert validate_fcpxml(paths["fcpxml"].read_text(encoding="utf-8"), tl) == []
    with pytest.raises(ValueError, match="unsupported export format"):
        export(tl, tmp_path / "out", formats=("xml",))


def test_export_is_deterministic(tmp_path):
    tl = two_clip_timeline()
    a = render_edl(tl)
    b = render_edl(tl)
    assert a == b
    assert render_fcpxml(tl) == render_fcpxml(tl)


def test_a_relative_source_path_still_emits_an_absolute_uri(tmp_path, monkeypatch):
    """The defect this pins, found by auditing a real run: the timeline's media
    path was relative, the exporter wrapped it in ``file://`` without ever
    touching the filesystem, and every clip came up offline in the NLE because
    a relative URI resolves against the timeline file's own directory. A CLI
    invocation with a relative source path reaches exactly this state."""
    from engine.nle_export import _to_file_uri

    media = tmp_path / "source" / "a movie.mov"
    media.parent.mkdir()
    media.write_bytes(b"0")
    monkeypatch.chdir(tmp_path)

    uri = _to_file_uri("source/a movie.mov")
    assert uri.startswith("file:///"), f"the URI must be absolute, got {uri!r}"
    # The media is found at that URI, not re-read relative to the timeline.
    resolved = Path(urllib.parse.unquote(uri[len("file://"):]))
    assert resolved == media.resolve()


def test_an_unresolvable_source_path_fails_the_export(tmp_path, monkeypatch):
    """A path that cannot be made absolute must fail loudly rather than ship an
    offline timeline under a URI that looks complete."""
    from engine.nle_export import _to_file_uri

    def unresolvable(self, strict=False):
        raise OSError("working directory has been removed")

    monkeypatch.setattr(Path, "resolve", unresolvable)
    with pytest.raises(RuntimeError, match="absolute file URI"):
        _to_file_uri("a/b.mov")


def test_export_carries_source_traceability_for_every_event():
    tl = multitrack_timeline()
    text = render_edl(tl)
    for clip in tl.clips:
        assert f"* FROM CLIP: {clip.source.path}" in text
        assert clip.name in text
