"""SMPTE timecode parsing and FCPXML rational rendering.

Ported from `rust/core/src/timecode.rs` (itself adapted from AutoCut's
src-tauri/src/timecode.rs, reused with the author's permission — see
docs/autocut-provenance.md). Kept behavior-identical so the Python and Rust
sides of the hybrid produce the same rationals for the same input.

DaVinci Resolve and Premiere bind FCPXML clips to the source media via the
embedded source timecode AND the file path; if the asset says it starts at 0s
but the MP4 carries `15:33:27;24`, the relink dialog fires.

Drop-frame is honored for NTSC 30 (29.97) and 60 (59.94): drop 2 or 4 frames
at every minute boundary except every tenth minute.

`seconds_to_rational` keeps the canonical denominator (e.g. 60000 for 59.94,
24000 for 23.976) un-reduced; NLEs key on these well-known values.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# NTSC rates: (nominal fps, frame_duration numerator, frame_duration denominator)
NTSC_RATES: tuple[tuple[int, int, int], ...] = (
    (24, 1001, 24000),  # 23.976
    (30, 1001, 30000),  # 29.97
    (60, 1001, 60000),  # 59.94
)

DEFAULT_FPS = 30.0


@dataclass(frozen=True)
class TimecodeRate:
    nominal_fps: int
    is_drop_frame: bool
    frame_duration_num: int
    frame_duration_den: int

    def frame_seconds(self) -> float:
        return self.frame_duration_num / self.frame_duration_den


def detect_rate(fps: float, drop_frame: bool = False) -> TimecodeRate:
    """Resolve a reported fps to a canonical FCPXML frame rate.

    Zero, negative, NaN and infinity are all turned away here: the rational
    fallback below computes ``round(120000 / fps)``, which for fps = 0 is
    infinite, and an infinite/oversized numerator makes one "frame" last
    effectively forever. Everything downstream then rounds to zero frames and
    the FCPXML exports an empty timeline.
    """
    if not math.isfinite(fps) or fps <= 0.0:
        fps = DEFAULT_FPS
    nominal = int(round(fps))
    if nominal > 0:
        for n, num, den in NTSC_RATES:
            if n == nominal:
                ntsc = n * 1000.0 / 1001.0
                if abs(fps - ntsc) < 0.01:
                    return TimecodeRate(n, drop_frame, num, den)
    if nominal > 0 and abs(fps - nominal) < 0.001:
        return TimecodeRate(nominal, False, 1, nominal)
    # Fallback: approximate with a 120000-denominator rational. Rare path.
    den = 120000
    num = int(round(den / fps))
    return TimecodeRate(max(nominal, 1), drop_frame, num, den)


def parse_smpte(tc: str | None, fps: float) -> float:
    """Parse ``HH:MM:SS:FF`` (non-drop) or ``HH:MM:SS;FF`` (drop) into
    source-relative seconds.

    Returns 0.0 for None/empty/unparseable input. Drop-frame is only honored
    for NTSC 30/60.
    """
    if tc is None:
        return 0.0
    tc = tc.strip()
    if not tc:
        return 0.0

    # Manual parse: HH:MM:SS<sep>FF where sep is ':' or ';'. Mirrors the Rust
    # byte-machine so the two agree on every malformed input, not just the
    # well-formed ones.
    raw = tc.encode("utf-8", errors="ignore")
    parts = [0, 0, 0, 0]
    sep_drop = False
    field = 0
    acc = 0
    have_digit = False
    for i, b in enumerate(raw):
        if 0x30 <= b <= 0x39:  # digit
            acc = acc * 10 + (b - 0x30)
            have_digit = True
        elif b in (0x3A, 0x3B):  # ':' or ';'
            if not have_digit or field >= 3:
                return 0.0
            parts[field] = acc
            if field == 2 and b == 0x3B:
                sep_drop = True
            field += 1
            acc = 0
            have_digit = False
        else:
            return 0.0
        if i + 1 == len(raw):
            if not have_digit or field != 3:
                return 0.0
            parts[field] = acc

    h, m, s, f = parts
    rate = detect_rate(fps, sep_drop)
    nominal = rate.nominal_fps
    if nominal == 0:
        return 0.0
    display_frames = h * 3600 * nominal + m * 60 * nominal + s * nominal + f
    if rate.is_drop_frame and nominal in (30, 60):
        drops_per_min = 2 if nominal == 30 else 4
        total_minutes = h * 60 + m
        dropped = drops_per_min * (total_minutes - total_minutes // 10)
        display_frames = max(display_frames - dropped, 0)
    return display_frames * rate.frame_seconds()


def seconds_to_rational(seconds: float, rate: TimecodeRate) -> str:
    """Render ``seconds`` snapped to the nearest frame as a FCPXML rational
    string, preserving the canonical denominator unreduced."""
    if seconds <= 0.0:
        return "0s"
    frames = int(round(seconds / rate.frame_seconds()))
    return f"{frames * rate.frame_duration_num}/{rate.frame_duration_den}s"


def track_offset_from_timecode(
    reference_timecode: str | None, track_timecode: str | None, fps: float
) -> float:
    """Where a linked track belongs on the timeline.

    When both the reference and the track carry embedded timecode, the same
    wall-clock instant is the one where their timecodes agree, so the track's
    media begins at project time ``trackTC - referenceTC``. Without timecode on
    both sides there is nothing to derive it from, so it starts at zero for
    the user to correct. Mirrors AutoCut's ``open_track`` command.
    """
    if reference_timecode is None or track_timecode is None:
        return 0.0
    ref = parse_smpte(reference_timecode, fps)
    track = parse_smpte(track_timecode, fps)
    return track - ref
