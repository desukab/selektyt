"""SMPTE timecode unit tests.

Mirrors the reference values pinned in `rust/core/src/timecode.rs` tests so
the Python port and the Rust original are independently pinned to the same
numbers, and the cross-language agreement is separately asserted in
`tests/test_sidecar_parity.py`.
"""
from __future__ import annotations

import math

import pytest

from engine.timecode import (
    DEFAULT_FPS,
    NTSC_RATES,
    TimecodeRate,
    detect_rate,
    parse_smpte,
    seconds_to_rational,
    track_offset_from_timecode,
)


def test_detect_rate_ntsc_2997():
    r = detect_rate(29.97, True)
    assert r.nominal_fps == 30
    assert r.is_drop_frame is True
    assert r.frame_duration_num == 1001
    assert r.frame_duration_den == 30000


def test_detect_rate_integer_25():
    r = detect_rate(25.0, False)
    assert r.nominal_fps == 25
    assert r.is_drop_frame is False
    assert r.frame_duration_den == 25


def test_detect_rate_5994():
    r = detect_rate(59.94, True)
    assert (r.nominal_fps, r.frame_duration_num, r.frame_duration_den) == (60, 1001, 60000)


def test_detect_rate_23976_keeps_canonical_24000():
    # NLEs key on the well-known 24000 denominator; it must stay un-reduced.
    r = detect_rate(23.976, False)
    assert (r.nominal_fps, r.frame_duration_num, r.frame_duration_den) == (24, 1001, 24000)


def test_parse_non_drop_25():
    # 00:00:01:00 at 25 fps -> exactly 1.0s
    assert parse_smpte("00:00:01:00", 25.0) == pytest.approx(1.0)


def test_parse_drop_frame_2997_one_hour():
    # 01:00:00;00 at 29.97 drop -> 3600.0s (the drop-frame design intent)
    s = parse_smpte("01:00:00;00", 30.0 * 1000.0 / 1001.0)
    assert s == pytest.approx(3600.0, abs=0.01)


def test_parse_drop_frame_drops_two_frames_in_the_first_minute():
    # 00:01:00;00 at 29.97: 1800 display frames minus 2 dropped = 1798
    # continuous frames * (1001/30000)s.
    s = parse_smpte("00:01:00;00", 30.0 * 1000.0 / 1001.0)
    expected = 1798 * (1001 / 30000)
    assert s == pytest.approx(expected, abs=1e-9)
    # A non-drop code at the same nominal rate does not drop them.
    ndf = parse_smpte("00:01:00:00", 30.0 * 1000.0 / 1001.0)
    assert ndf > s


@pytest.mark.parametrize("tc", [None, "", "   ", "garbage", "1:2", "00:00:01", "a0:00:00:00"])
def test_parse_empty_or_unparseable_returns_zero(tc):
    assert parse_smpte(tc, 30.0) == 0.0


def test_parse_ignores_unparseable_without_raising():
    # The byte-machine must reject stray characters rather than truncating.
    assert parse_smpte("00:00:01:00junk", 25.0) == 0.0
    # Trailing separator with no frame digits, and missing fields, are not TCs.
    assert parse_smpte("00:00:01;", 25.0) == 0.0
    assert parse_smpte("00:00:", 25.0) == 0.0
    assert parse_smpte("00:00:01:00:00", 25.0) == 0.0  # too many fields
    # A frame field of 0 IS well-formed (`HH:MM:SS;0` is frame 0, not malformed).
    assert parse_smpte("00:00:01;0", 25.0) == pytest.approx(1.0)


def test_seconds_to_rational_2997():
    r = detect_rate(29.97, True)
    # 1 second at 29.97 NTSC is 30 frames * 1001/30000 = 30030/30000 s.
    assert seconds_to_rational(1.0, r) == "30030/30000s"


def test_seconds_to_rational_zero_and_negative():
    r = detect_rate(30.0, False)
    assert seconds_to_rational(0.0, r) == "0s"
    assert seconds_to_rational(-5.0, r) == "0s"


def test_seconds_to_rational_snaps_to_nearest_frame():
    r = detect_rate(25.0, False)
    # 0.999s rounds up to frame 25 -> 25/25s, not 24/25s.
    assert seconds_to_rational(0.999, r) == "25/25s"
    # 1.0s at an integer rate is exactly the denominator, un-reduced.
    assert seconds_to_rational(1.0, r) == "25/25s"
    # 2.0s keeps the canonical denominator rather than reducing to 2/1.
    assert seconds_to_rational(2.0, r) == "50/25s"


@pytest.mark.parametrize("fps", [0.0, -12.0, float("nan"), float("inf")])
def test_detect_rate_refuses_to_build_a_degenerate_rate(fps):
    # A non-positive fps used to reach the 120000-denominator fallback and
    # compute round(120000 / 0.0), which is infinite. That made one "frame"
    # last effectively forever and the FCPXML exported an empty timeline. It
    # is now substituted with the 30fps fallback, same as the Rust core.
    r = detect_rate(fps, False)
    assert 0.0 < r.frame_seconds() <= 1.0, f"fps {fps} -> {r.frame_seconds()}s/frame"
    assert r.nominal_fps > 0, f"fps {fps} produced nominal 0"
    assert (r.frame_duration_num, r.frame_duration_den) == (1, 30)


def test_seconds_to_rational_still_measures_time_at_a_degenerate_fps():
    # Downstream of the bug above, one second rounded to zero frames and every
    # asset-clip carried a zero duration, so the NLE imported an empty
    # timeline. With the 30fps fallback one second is 30 frames — nonzero.
    r = detect_rate(0.0, False)
    rendered = seconds_to_rational(1.0, r)
    num, den = rendered.rstrip("s").split("/")
    assert int(num) > 0, f"one second rendered as {rendered}"
    assert int(den) == 30


def test_irrational_fps_uses_the_120000_denominator_fallback():
    # A positive, finite, non-integer fps that is neither NTSC nor within
    # 0.001 of its nominal lands here — the rare path, kept un-reduced.
    r = detect_rate(23.5, False)
    assert (r.nominal_fps, r.frame_duration_num, r.frame_duration_den) == (24, 5106, 120000)
    # One second still measures a real duration at that rate.
    rendered = seconds_to_rational(1.0, r)
    num, den = rendered.rstrip("s").split("/")
    assert int(num) > 0 and int(den) == 120000, rendered


def test_ntsc_rates_table_is_the_canonical_set():
    assert NTSC_RATES == ((24, 1001, 24000), (30, 1001, 30000), (60, 1001, 60000))


def test_frame_seconds_roundtrips_through_the_rate():
    r = detect_rate(29.97, True)
    assert r.frame_seconds() == pytest.approx(1001 / 30000)


def test_track_offset_from_timecode_is_the_tc_difference():
    fps = 30000 / 1001
    # The recorder started 10s of timecode after the camera, so its media
    # begins 10s into the project.
    off = track_offset_from_timecode("01:00:00;00", "01:00:10;00", fps)
    assert off == pytest.approx(parse_smpte("00:00:10;00", fps), abs=1e-6)


def test_track_offset_from_timecode_needs_both_timecodes():
    fps = 25.0
    assert track_offset_from_timecode(None, "00:00:10:00", fps) == 0.0
    assert track_offset_from_timecode("00:00:00:00", None, fps) == 0.0


def test_default_fps_is_the_probe_default():
    assert DEFAULT_FPS == 30.0
