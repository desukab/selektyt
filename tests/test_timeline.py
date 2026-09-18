"""The edited-timeline model: shape, validation, traceability and SMPTE write.

``engine/timecode`` is read-only here — it parses and renders rationals but
cannot *format* seconds into a timecode string, which EDL export needs.
``engine.timeline.format_smpte`` supplies that half, and its correctness is
pinned by round-tripping through the parser over a dense frame range.
"""

from __future__ import annotations

import pytest

from engine import timecode
from engine.timeline import (
    Clip,
    RenderManifest,
    SourceMedia,
    Timeline,
    Track,
    format_smpte,
)

# --------------------------------------------------------------- SMPTE write


@pytest.mark.parametrize("fps,drop", [
    (30.0, False), (29.97, True), (29.97, False), (24.0, False),
    (23.976, False), (25.0, False), (60.0, True), (59.94, True),
])
def test_format_smpte_round_trips_through_the_parser(fps, drop):
    """Every whole frame formats to a timecode that parses back to exactly
    that frame — the guarantee the EDL writer leans on."""
    rate = timecode.detect_rate(fps, drop)
    frame = rate.frame_seconds()
    mismatches = 0
    for n in range(0, 259_200, 13):  # ~2.4 hours, strided
        seconds = n * frame
        tc = format_smpte(seconds, rate)
        back = timecode.parse_smpte(tc, fps)
        if abs(back - seconds) > 1e-9:
            mismatches += 1
    assert mismatches == 0


@pytest.mark.parametrize("tc,fps", [
    ("01:00:00;00", 29.97), ("00:00:10;00", 29.97), ("01:23:45;12", 29.97),
    ("00:00:00:00", 30.0), ("00:00:10:00", 30.0), ("01:59:59:29", 30.0),
    ("00:10:00:00", 25.0), ("01:00:00:00", 23.976),
])
def test_format_smpte_is_the_identity_on_parsed_timecodes(tc, fps):
    """format(parse(tc)) == tc for well-formed timecodes at their own rate."""
    rate = timecode.detect_rate(fps, ";" in tc)
    seconds = timecode.parse_smpte(tc, fps)
    assert format_smpte(seconds, rate) == tc


def test_format_smpte_snaps_to_the_nearest_frame():
    rate = timecode.detect_rate(30.0)
    assert format_smpte(0.0, rate) == "00:00:00:00"
    assert format_smpte(-1.0, rate) == "00:00:00:00"
    # 0.0166s is under half a frame at 30fps -> frame 0; 0.02s -> frame 1.
    assert format_smpte(0.0166, rate) == "00:00:00:00"
    assert format_smpte(0.02, rate) == "00:00:00:01"


def test_format_smpte_honors_drop_frame_separators():
    rate = timecode.detect_rate(29.97, True)
    assert ";" in format_smpte(3600.0, rate)
    ndf = timecode.detect_rate(29.97, False)
    assert ":" in format_smpte(3600.0, ndf)


# ------------------------------------------------------------------- clips


def test_clip_requires_positive_durations():
    media = SourceMedia(path="a.mp4", fps=30.0)
    with pytest.raises(ValueError, match="source_out"):
        Clip(media, 5.0, 5.0, 0.0, 1.0)
    with pytest.raises(ValueError, match="timeline_out"):
        Clip(media, 0.0, 1.0, 5.0, 5.0)
    with pytest.raises(ValueError, match="timeline_in"):
        Clip(media, 0.0, 1.0, -0.1, 1.0)


def test_clip_name_defaults_to_the_source_file_name():
    media = SourceMedia(path="/clips/take-one.mov", fps=30.0)
    clip = Clip(media, 0.0, 1.0, 0.0, 1.0)
    assert clip.name == "take-one.mov"


def test_clip_source_timecode_accounts_for_embedded_tc():
    """A clip reading source 10-20s of media whose TC starts at 01:00:00;00
    reports 1:00:10;00 .. 1:00:19;29 — exactly the EDL source pair."""
    media = SourceMedia(path="a.mp4", fps=29.97, start_timecode="01:00:00;00")
    clip = Clip(media, 10.0, 20.0, 0.0, 10.0)
    assert clip.source_timecode_in() == "01:00:10;00"
    assert clip.source_timecode_out() == "01:00:20;00"
    # ... and those timecodes parse back onto the clip's source span.
    assert timecode.parse_smpte(clip.source_timecode_out(), 29.97) > timecode.parse_smpte(
        clip.source_timecode_in(), 29.97
    )


def test_clip_rejects_a_bad_track_kind():
    with pytest.raises(ValueError, match="track kind"):
        Track("V1", "music")


# ------------------------------------------------------------- timeline shape


def test_empty_timeline_has_no_duration_and_refuses_a_primary_track():
    tl = Timeline()
    assert tl.duration == 0.0
    assert tl.validate() == []
    with pytest.raises(ValueError, match="no tracks"):
        _ = tl.primary_track


def test_clips_are_sorted_and_tracks_are_inferred():
    media = SourceMedia(path="a.mp4", fps=30.0)
    b = Clip(media, 40.0, 45.0, 10.0, 15.0, "V1")
    a = Clip(media, 10.0, 20.0, 0.0, 10.0, "V1")
    tl = Timeline(clips=[b, a])
    assert [c.timeline_in for c in tl.clips] == [0.0, 10.0]
    assert "V1" in tl.track_names
    assert tl.primary_track.name == "V1"
    assert tl.duration == 15.0


def test_audio_tracks_sink_below_video_for_the_primary_lookup():
    from tests.timeline_fixtures import multitrack_timeline

    tl = multitrack_timeline()
    assert tl.primary_track.name == "V1"
    assert len(tl.clips_on("V2")) == 1
    assert len(tl.clips_on("A1")) == 1
    assert len(tl.source_media()) == 3


def test_degenerate_fps_is_refused():
    media = SourceMedia(path="a.mp4", fps=30.0)
    clip = Clip(media, 0.0, 1.0, 0.0, 1.0)
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="fps"):
            Timeline(fps=bad, clips=[clip])


# ------------------------------------------------------ traceability mapping


def test_source_to_timeline_and_back_round_trip():
    from tests.timeline_fixtures import two_clip_timeline

    tl = two_clip_timeline()
    media = tl.clips[0].source
    # source 12s sits inside clip 1 (10-20 -> 0-10) at timeline 2s.
    assert tl.source_to_timeline(media.key, 12.0) == pytest.approx(2.0)
    # source 42s sits inside clip 2 (40-45 -> 10-15) at timeline 12s.
    assert tl.source_to_timeline(media.key, 42.0) == pytest.approx(12.0)
    # Cut-away instants have no honest position.
    assert tl.source_to_timeline(media.key, 30.0) is None
    assert tl.source_to_timeline("other-media", 1.0) is None

    clip, src = tl.timeline_to_source(12.0)
    assert clip.name == "point"
    assert src == pytest.approx(42.0)
    assert tl.timeline_to_source(99.0) is None


def test_traceability_carries_smpte_and_placement():
    from tests.timeline_fixtures import two_clip_timeline

    tl = two_clip_timeline()
    tr = tl.clips[1].trace()
    assert tr["source_path"] == "/fixtures/interview.mp4"
    assert tr["source_timecode_in"] == "01:00:39;29"
    assert tr["timeline_in"] == 10.0 and tr["timeline_out"] == 15.0
    assert tr["track"] == "V1"


def test_to_dict_traces_every_clip():
    from tests.timeline_fixtures import multitrack_timeline

    d = multitrack_timeline().to_dict()
    assert len(d["clips"]) == 4
    assert all(c["source_timecode_in"] for c in d["clips"])
    assert d["duration_seconds"] == 15.0


# ------------------------------------------------------------- validation


def test_validate_accepts_the_fixtures():
    from tests.timeline_fixtures import (
        english_timeline,
        gapped_timeline,
        multitrack_timeline,
        telugu_timeline,
        two_clip_timeline,
    )

    for tl in (
        english_timeline(), gapped_timeline(), multitrack_timeline(),
        telugu_timeline(), two_clip_timeline(),
    ):
        assert tl.validate() == [], tl.title


def test_validate_flags_overlapping_clips_on_one_track():
    media = SourceMedia(path="a.mp4", fps=30.0, duration=100.0)
    tl = Timeline(clips=[Clip(media, 0.0, 5.0, 0.0, 5.0), Clip(media, 10.0, 15.0, 3.0, 8.0)])
    problems = tl.validate()
    assert any("overlap" in p for p in problems)


def test_validate_flags_reading_past_the_end_of_the_media():
    media = SourceMedia(path="a.mp4", fps=30.0, duration=10.0)
    tl = Timeline(clips=[Clip(media, 0.0, 12.0, 0.0, 12.0)])
    problems = tl.validate()
    assert any("exceeds media duration" in p for p in problems)


def test_validate_flags_retiming_by_default_within_half_a_frame():
    media = SourceMedia(path="a.mp4", fps=30.0, duration=100.0)
    # source 5.0s, timeline 4.0s: 1s of drift = 30 frames, far over tolerance.
    tl = Timeline(clips=[Clip(media, 0.0, 5.0, 0.0, 4.0)])
    problems = tl.validate()
    assert any("re-timing is unsupported" in p for p in problems)


def test_strict_validation_rejects_a_sub_frame_drift():
    media = SourceMedia(path="a.mp4", fps=30.0, duration=100.0)
    # A 0.004s drift is 0.12 of a frame: tolerated by default, not strictly.
    clip = Clip(media, 0.0, 1.004, 0.0, 1.0)
    assert Timeline(clips=[clip]).validate() == []
    assert Timeline(clips=[clip]).validate(strict_timing=True) != []


def test_clips_on_different_tracks_may_overlap():
    from tests.timeline_fixtures import multitrack_timeline

    tl = multitrack_timeline()
    # V2 and A1 overlap V1 in timeline time by design; only lanes conflict.
    assert all("overlap" not in p for p in tl.validate())


def test_render_manifest_round_trips_to_dict():
    m = RenderManifest(
        output_path="/out/01.mp4", width=1080, height=1920, fps=29.97,
        duration_seconds=10.0, captions_srt="/out/01.srt",
    )
    d = m.to_dict()
    assert d["output_path"] == "/out/01.mp4"
    assert d["duration_seconds"] == 10.0
    assert d["captions"]["srt"] == "/out/01.srt"


def test_transcript_words_are_carried_through_the_model():
    """Sanity that a Word's timing survives placement unchanged."""
    from engine.timeline import Clip, Timeline, Track
    from tests.timeline_fixtures import TELUGU_WORDS, telugu_media

    media = telugu_media()
    tl = Timeline(clips=[Clip(media, 0.0, 6.0, 0.0, 6.0)], tracks=[Track("V1", "video")])
    placed_start = tl.source_to_timeline(media.key, TELUGU_WORDS[0].start)
    assert placed_start == pytest.approx(TELUGU_WORDS[0].start)
