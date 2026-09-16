"""Subject-aware reframing: center tracking is temporally smooth.

A misdetection must not become a visible crop jump, since crop_x_expression
interpolates directly between sampled centers. Also asserts the fallback
(edge-density) tracker genuinely follows a moving subject on a synthesized
clip, so the 9:16 crop tracks motion instead of sitting at a fixed center.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.reframe import _smooth_centers, track_subject_center, crop_x_expression

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not available")


def test_smooth_damps_an_isolated_spike():
    centers = [(0.0, 0.2), (1.0, 0.25), (2.0, 0.9), (3.0, 0.3), (4.0, 0.35)]
    raw_peak = max(c for _, c in centers)
    smoothed = _smooth_centers(centers, window=5)
    assert len(smoothed) == len(centers)
    assert [t for t, _ in smoothed] == [t for t, _ in centers]
    vals = [c for _, c in smoothed]
    # an average cannot leave the input range...
    assert min(vals) >= min(c for _, c in centers) - 1e-9
    assert max(vals) <= raw_peak + 1e-9
    # ...and the spike must be pulled toward its neighbours
    assert max(vals) < raw_peak


def test_smooth_preserves_a_monotone_ramp():
    centers = [(float(i), i / 10.0) for i in range(10)]
    smoothed = _smooth_centers(centers, window=3)
    vals = [c for _, c in smoothed]
    assert all(b >= a - 1e-9 for a, b in zip(vals, vals[1:])), "a clean ramp stays monotone"


def test_smooth_short_runs_untouched():
    assert _smooth_centers([], 5) == []
    assert _smooth_centers([(0.0, 0.5)], 5) == [(0.0, 0.5)]
    assert _smooth_centers([(0.0, 0.5), (1.0, 0.6)], 5) == [(0.0, 0.5), (1.0, 0.6)]


def _moving_square_video(out: Path, frames: int = 24, size: int = 320) -> Path:
    """A bright square traveling left-to-right on a dark background: no face,
    so the fallback edge-density tracker must follow it."""
    import cv2
    import numpy as np

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out), fourcc, 12, (size, size))
    try:
        for i in range(frames):
            frame = np.zeros((size, size, 3), dtype=np.uint8)
            x = int(size * 0.15 + (size * 0.6) * i / max(1, frames - 1))
            y = size // 2
            cv2.rectangle(frame, (x - 30, y - 30), (x + 30, y + 30), (255, 255, 255), -1)
            writer.write(frame)
    finally:
        writer.release()
    return out


def test_tracker_follows_a_moving_subject(tmp_path):
    video = _moving_square_video(tmp_path / "square.mp4")
    centers = track_subject_center(video, start=0.0, end=1.8, samples=12)
    assert len(centers) >= 6, f"expected several samples, got {len(centers)}"
    first, last = centers[0][1], centers[-1][1]
    assert last - first > 0.1, f"subject moved right but tracking did not: {first:.3f} -> {last:.3f}"


def test_crop_expression_is_bounded_and_time_keyed(tmp_path):
    video = _moving_square_video(tmp_path / "square.mp4")
    expr = crop_x_expression(video, start=0.0, end=1.8)
    assert "if(lt(t," in expr, "the crop must be keyed to time"
    # The expression must survive ffmpeg's filtergraph parser exactly as the
    # render pipeline embeds it (single-quoted x value -- unquoted commas would
    # be read as filter separators and break the 9:16 render).
    import subprocess

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-vf",
         f"crop=ih*9/16:ih:x='{expr}':y=0", "-f", "null", "-"],
        check=True, capture_output=True,
    )
