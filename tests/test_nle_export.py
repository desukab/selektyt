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
    independent_parse_edl,
    independent_parse_fcpxml,
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


# ── the independent reader: a second parser on the same text ─────────────

@pytest.mark.parametrize("tl_name", ["two_clip", "gapped", "multitrack", "telugu"])
def test_the_exported_edl_is_accepted_by_an_independent_reader(tl_name, tmp_path):
    """``validate_edl`` and ``render_edl`` are one grammar: whatever the emitter
    writes, the validator is built to accept, so an EDL that passes it has been
    checked by nobody but its author. ``pycmx`` is an independent CMX 3600
    reader, so a real export must parse under it too.

    This is the half of ``tools/nle_check.py``'s audit that needs no media, and
    running it on every export is what closes that tool being "wired into
    nothing" for the EDL format.
    """
    pytest.importorskip("pycmx", reason="the nle extra supplies the independent reader")
    tl = {
        "two_clip": two_clip_timeline,
        "gapped": gapped_timeline,
        "multitrack": multitrack_timeline,
        "telugu": telugu_timeline,
    }[tl_name]()
    text = render_edl(tl)
    ran, problems = independent_parse_edl(text)
    assert ran, "pycmx importorskip above means the reader must be present"
    assert problems == [], f"pycmx rejected an EDL our own validator passed: {problems}"


def test_an_edl_our_validator_accepts_but_a_reader_rejects_is_caught(tmp_path, monkeypatch):
    """The independent parse has to be able to fire, and it has to be able to
    fire *when our own validator finds nothing* — that is the entire reason it
    exists. So this takes a real, valid EDL (``validate_edl`` returns an empty
    list for it) and injects a reader that rejects the file at the read, which
    is where a genuinely malformed EDL would fail.

    Without this the previous test is one-sided: it can only prove agreement,
    and an ``independent_parse_edl`` that always returned ``(True, [])`` would
    pass it as easily as a real one."""
    text = render_edl(two_clip_timeline())
    assert validate_edl(text) == [], \
        "the fixture must be an EDL our own validator accepts, or this " \
        "test is not demonstrating what its name claims"
    monkeypatch.setattr(
        "engine.nle_export.io.StringIO",
        lambda s: _ExplodingReader(s),
    )
    ran, problems = independent_parse_edl(text)
    assert ran and problems, \
        "a reader that rejects the text must report a problem, not pass"


class _ExplodingReader:
    """Stand in for a file object a real reader chokes on: ``parse_cmx3600``
    takes a file-like object, so the failure is injected at the read, which is
    where a genuinely malformed EDL would fail."""

    def __init__(self, text):
        self._text = text

    def readlines(self):
        raise ValueError("malformed CMX 3600 input")


def test_a_missing_reader_is_reported_as_not_run_not_as_a_defect(monkeypatch):
    """``pycmx`` is an optional extra. A base install must still be able to
    export, so absence is ``(False, [])`` — not a problem. The runtime pipeline
    turns that into an advisory line; ``tools/nle_check.py`` turns it into a
    FAIL, and that difference is deliberate and documented at the function."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *a, **k):
        if name == "pycmx" or name.startswith("pycmx."):
            raise ImportError("simulated: the nle extra is not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", refuse)
    ran, problems = independent_parse_edl("TITLE: x\nFCM: NON-DROP FRAME\n")
    assert ran is False, "the reader's absence is not a defect in this EDL"
    assert problems == []


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


# ── the independent reader: a second parser on the same text ─────────────

@pytest.mark.parametrize("tl_name", ["two_clip", "gapped", "multitrack", "telugu"])
def test_the_exported_fcpxml_is_read_by_an_independent_reader(tl_name):
    """``validate_fcpxml`` and ``render_fcpxml`` are one grammar: whatever the
    emitter writes, the validator is built to accept, so an FCPXML that passes
    it has been checked by nobody but its author. The vendored OpenTimelineIO
    adapter is an independent reader, so a real export must parse under it too.

    Acceptance here is deliberately more than "it parsed". The adapter resolves
    an asset's media through ``<media-rep kind="original-media">``, which is
    where the emitter writes ``src``; the OpenTimelineIO project's own copy of
    the same adapter reads ``src`` off the ``<asset>`` element and returns every
    clip as a ``MissingReference``. That parse succeeds and drops every media
    reference, so it would pass a bare "it parsed" gate while landing every cut
    offline in an NLE. Each clip must therefore come back as an
    ``ExternalReference`` with a target URL.
    """
    otio = pytest.importorskip(
        "opentimelineio",
        reason="the nle extra supplies the independent FCPXML reader",
    )
    tl = {
        "two_clip": two_clip_timeline,
        "gapped": gapped_timeline,
        "multitrack": multitrack_timeline,
        "telugu": telugu_timeline,
    }[tl_name]()
    text = render_fcpxml(tl)
    ran, problems = independent_parse_fcpxml(text)
    assert ran, "importorskip above means the reader must be present"
    assert problems == [], (
        f"the FCPXML reader rejected a document our own validator passed: "
        f"{problems}"
    )
    # The property the problems list exists to guard, asserted directly so a
    # function that always returned an empty list cannot pass this test.
    from third_party.otio_fcpxml_adapter.fcpxml import FcpxXml

    clips = list(FcpxXml(text).to_otio().find_children(otio.schema.Clip))
    assert clips, "the reader parsed the document but returned no clips"
    assert all(
        isinstance(c.media_reference, otio.schema.ExternalReference)
        and c.media_reference.target_url
        for c in clips
    )


def test_a_media_rep_blind_reader_is_caught_by_the_check(monkeypatch):
    """The failure mode this check exists for is not a malformed document but a
    *successful* parse that resolves no media — which is what the
    OpenTimelineIO project's own copy of this adapter produces on SelekyT's
    output, because it reads ``src`` off the ``<asset>`` element and SelekyT
    writes it on the ``<media-rep>``. This makes that reader's behaviour
    explicit on a document our own validator passes, so the gate is known to
    fire on the real false positive rather than only on synthetic breakage."""
    pytest.importorskip("opentimelineio", reason="the reader under test is not installed")
    text = render_fcpxml(two_clip_timeline())
    assert validate_fcpxml(text) == [], (
        "the fixture must be a document our own validator accepts, or this "
        "test is not demonstrating what its name claims"
    )

    # Strip every media-rep, leaving the asset elements and the spine intact:
    # well-formed, schema-shaped, and pointing at no media at all.
    import xml.etree.ElementTree as ET

    root = ET.fromstring(text)
    stripped = sum(1 for _ in root.iter("media-rep"))
    assert stripped, "the emitter must write media-rep for this test to mean anything"
    for rep in list(root.iter("media-rep")):
        for parent in root.iter():
            if rep in list(parent):
                parent.remove(rep)
                break
    blind = ET.tostring(root, encoding="unicode")

    ran, problems = independent_parse_fcpxml(blind)
    assert ran, "the reader must still be present"
    assert problems, (
        "a document whose clips resolve to no media must be reported as a "
        f"problem, not passed: {problems!r}"
    )


def test_a_fcpxml_our_validator_accepts_but_a_reader_rejects_is_caught(monkeypatch):
    """The independent parse has to be able to fire when our own validator
    finds nothing — that is the entire reason it exists. A document that is
    well-formed XML and passes the vocabulary list is still rejected by a real
    reader if the FCPXML structure underneath is wrong, so this breaks the
    structure in a way ``validate_fcpxml`` cannot see."""
    pytest.importorskip("opentimelineio", reason="the reader under test is not installed")
    text = render_fcpxml(two_clip_timeline())
    assert validate_fcpxml(text) == [], (
        "the fixture must be a document our own validator accepts, or this "
        "test is not demonstrating what its name claims"
    )
    monkeypatch.setattr(
        "third_party.otio_fcpxml_adapter.fcpxml.FcpxXml",
        _ExplodingFcpxmlReader,
    )
    ran, problems = independent_parse_fcpxml(text)
    assert ran and problems, \
        "a reader that rejects the document must report a problem, not pass"


class _ExplodingFcpxmlReader:
    """Stand in for a reader that rejects the document at the read, which is
    where a genuinely malformed FCPXML would fail."""

    def __init__(self, text):
        pass

    def to_otio(self):
        raise ValueError("malformed FCPXML input")


def test_a_missing_fcpxml_reader_is_reported_as_not_run_not_as_a_defect(monkeypatch):
    """``opentimelineio`` is an optional extra, and the vendored reader imports
    it. A base install must still be able to export, so absence is
    ``(False, [])`` — not a problem. The runtime pipeline turns that into an
    advisory line; ``tools/nle_check.py`` turns it into a FAIL, and that
    difference is deliberate and documented at the function."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *a, **k):
        if name == "opentimelineio" or name.startswith("opentimelineio.") \
                or name == "third_party.otio_fcpxml_adapter.fcpxml":
            raise ImportError("simulated: the nle extra is not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", refuse)
    ran, problems = independent_parse_fcpxml("<fcpxml></fcpxml>")
    assert ran is False, "the reader's absence is not a defect in this FCPXML"
    assert problems == []


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


def test_fcpxml_preserves_native_source_timebase_when_sequence_rate_differs():
    """FCPXML source positions must stay on the media's native frame grid.

    A 29.97 source in a 30fps sequence must not have its source timecode
    re-encoded at 30fps: doing so accumulates roughly 1.2s of drift by the
    20-minute mark even though the EDL remains correct.
    """
    from engine.timeline import Clip, SourceMedia, Timeline

    media = SourceMedia(
        path="source.mp4",
        fps=30000 / 1001,
        duration=2000.0,
        start_timecode="00:00:00:00",
    )
    clip = Clip(media, 19 * 60 + 49 + 19 / (30000 / 1001),
                19 * 60 + 50 + 19 / (30000 / 1001), 0.0, 1.0)
    tl = Timeline(clips=[clip], fps=30.0)
    xml = render_fcpxml(tl)
    root = ET.fromstring(xml)
    elem = root.find(".//spine/asset-clip")
    assert elem is not None
    got = _rational_sec(elem.get("start"))
    want = clip.source_in
    assert got == pytest.approx(want, abs=1.0 / (30000 / 1001))
    assert validate_fcpxml(xml, tl) == []


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


def test_fcpxml_keeps_frame_contiguous_program_clips_contiguous():
    """Independent frame rounding must not create a hole inside a touching edit.

    A 2.10-second clip at 25 fps rounds to 2.08 seconds. After prior clips
    have accumulated a half-frame discrepancy, independently rounding the
    following clip's raw offset can place it one frame after that end. The
    source timeline has no gap, so the emitted spine must not invent one.
    """
    from engine.timeline import Clip, SourceMedia, Timeline

    media = SourceMedia("source.mp4", fps=25.0, duration=100.0)
    clips = [
        Clip(media, 0.0, 39.92, 0.0, 39.92),
        Clip(media, 40.0, 49.4, 39.92, 49.32),
        Clip(media, 50.0, 52.1, 49.32, 51.42),
        Clip(media, 60.0, 61.0, 51.42, 52.42),
    ]
    xml = render_fcpxml(Timeline(clips=clips, fps=25.0))
    spine = ET.fromstring(xml).find("./library/event/project/sequence/spine")
    placed = [c for c in spine if c.tag == "asset-clip"]
    assert len(placed) == len(clips)
    for previous, current in zip(placed, placed[1:]):
        previous_end = (
            _rational_sec(previous.get("offset"))
            + _rational_sec(previous.get("duration"))
        )
        assert _rational_sec(current.get("offset")) == pytest.approx(previous_end)


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


def test_an_edl_never_ships_a_relative_from_clip(tmp_path, monkeypatch):
    """Regression: the EDL wrote ``clip.source.path`` verbatim, so a CLI
    invocation given a relative source emitted a FROM CLIP that only resolves
    from the directory the export was run in. An NLE resolves it against the
    timeline's own directory, so the same file imported with every clip
    offline. ``render_fcpxml`` already absolutised; ``render_edl`` did not.

    Measured on a real corpus project: the EDL read
    ``corpus/media/royal-society-cloke.webm`` from ``project/timeline/01.edl``.
    """
    from engine.timeline import Clip, SourceMedia, Timeline

    real = tmp_path / "media" / "talk.mp4"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"stub")
    # The CLI is handed a source relative to its working directory, as
    # tools/corpus_run.py hands it one.
    monkeypatch.chdir(tmp_path)
    media = SourceMedia(path="media/talk.mp4", fps=30.0, duration=10.0)
    tl = Timeline(clips=[Clip(media, 0.0, 5.0, 0.0, 5.0, name="clip one")])
    text = render_edl(tl)

    m = re.search(r"^\s*\*\s*FROM CLIP:\s*(.+)$", text, re.M)
    assert m, "the EDL must annotate every event with its source"
    emitted = Path(m.group(1))
    assert emitted.is_absolute(), f"FROM CLIP must be absolute, got {emitted!r}"
    assert emitted == real.resolve()


def test_an_edl_that_cannot_resolve_its_source_fails_the_export(tmp_path, monkeypatch):
    """A path that cannot be made absolute must fail loudly rather than ship an
    offline timeline, exactly as the FCPXML path already does."""
    from engine.timeline import Clip, SourceMedia, Timeline
    from engine.nle_export import _from_clip_path

    def unresolvable(self, strict=False):
        raise OSError("working directory has been removed")

    monkeypatch.setattr(Path, "resolve", unresolvable)
    with pytest.raises(RuntimeError, match="cannot make the source path absolute"):
        _from_clip_path("a/b.mov")
    # And the export itself, not just the helper.
    media = SourceMedia(path="a/b.mov", fps=30.0, duration=10.0)
    tl = Timeline(clips=[Clip(media, 0.0, 5.0, 0.0, 5.0, name="clip one")])
    with pytest.raises(RuntimeError, match="cannot make the source path absolute"):
        render_edl(tl)
