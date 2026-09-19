"""Subject-aware reframing: center tracking is temporally smooth.

A misdetection must not become a visible crop jump, since crop_x_expression
interpolates directly between sampled centers. Also asserts the fallback
(edge-density) tracker genuinely follows a moving subject on a synthesized
clip, so the 9:16 crop tracks motion instead of sitting at a fixed center.
"""
from __future__ import annotations

import shutil
import subprocess
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


# ── F16: the source is decoded once, not once per question asked of it ──


def test_probe_opens_the_source_exactly_once(tmp_path, monkeypatch):
    """F16: dimensions and subject tracking used to be two separate
    ``VideoCapture`` opens, so the seek-heavy metadata pass ran twice per
    vertical clip. Now one open answers both."""
    import engine.reframe as reframe_mod

    video = _moving_square_video(tmp_path / "square.mp4")
    real_open = reframe_mod.cv2.VideoCapture

    opened = []

    def counting_open(path):
        opened.append(str(path))
        return real_open(path)

    monkeypatch.setattr(reframe_mod.cv2, "VideoCapture", counting_open)
    expr = crop_x_expression(video, start=0.0, end=1.8)
    assert expr, "the crop expression must still be produced"
    assert len(opened) == 1, (
        f"the source must be opened once, opened {len(opened)} times: {opened}"
    )
    assert opened[0] == str(video)


def test_face_cascade_is_loaded_once_per_process(tmp_path, monkeypatch):
    """F16 (IO half): the Haar XML is the same for every clip and every 9:16
    render, so re-reading it per call is duplicated work on a constrained host.
    """
    import engine.reframe as reframe_mod

    # Some opencv builds (this host's, among others) ship without the Haar
    # class at all, in which case the code takes the edge-density fallback and
    # never loads anything. Inject a stub so the memo is testable everywhere.
    constructed = []

    class _StubCascade:
        def __init__(self, path):
            constructed.append(str(path))

        def empty(self):
            return False

        def detectMultiScale(self, *a, **kw):
            return []

    monkeypatch.setattr(reframe_mod.cv2, "CascadeClassifier", _StubCascade,
                         raising=False)  # not every build ships the class
    # Drop the process-level memo so the count is this test's alone.
    monkeypatch.setattr(reframe_mod, "_CACHED_CASCADE", False)

    video = _moving_square_video(tmp_path / "square2.mp4")
    for _ in range(3):
        crop_x_expression(video, start=0.0, end=1.8)
    assert len(constructed) == 1, (
        f"the cascade must be loaded once and reused, loaded {len(constructed)}"
    )
    # the memo survives later calls in the same process
    crop_x_expression(video, start=0.0, end=1.8)
    assert len(constructed) == 1


def test_absent_cascade_data_falls_back_without_crashing(tmp_path, monkeypatch):
    """A headless build without cv2.data must still produce a crop expression
    rather than raising — the edge-density tracker takes over."""
    import engine.reframe as reframe_mod

    class _Boom:
        def __init__(self, *a, **kw):
            raise AttributeError("cv2.data is not available on this build")

    monkeypatch.setattr(reframe_mod, "_CACHED_CASCADE", False)
    monkeypatch.setattr(reframe_mod.cv2, "CascadeClassifier", _Boom,
                         raising=False)

    video = _moving_square_video(tmp_path / "square3.mp4")
    expr = crop_x_expression(video, start=0.0, end=1.8)
    assert expr, "an unusable cascade must not prevent reframing"
    assert reframe_mod._load_face_cascade() is None


# ── anamorphic sources: the 9:16 crop must be 9:16 in *display* space ──


def _anamorphic_video(out: Path, sar: str = "8/9", size: str = "720x486") -> Path:
    """A real NTSC-shaped source: 720x486 pixels at SAR 8:9, so DAR 320:243.

    Built with ffmpeg rather than cv2 because OpenCV cannot write a sample
    aspect ratio at all, and the whole point is that the pixels are not
    square. The ratio is spelled ``8/9`` rather than ``8:9``: ffmpeg's setsar
    parses the colon form of a sub-unitary ratio as something else entirely
    and writes SAR 8:1, which is a very different (and much more broken)
    source.
    """
    import subprocess

    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         f"-i", f"testsrc=size={size}:rate=12:duration=2",
         "-vf", f"setsar={sar}", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "28", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    return out


def _probe_geometry(path: Path) -> tuple[int, int, int, int]:
    """(width, height, sar_w, sar_h) of the first video stream."""
    import subprocess

    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,sample_aspect_ratio",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    w, h, sar = proc.stdout.split()
    sar_w, sar_h = (int(p) for p in sar.split(":"))
    return int(w), int(h), sar_w, sar_h


def test_crop_width_accounts_for_a_wide_sample_aspect_ratio():
    """The anamorphic answer must be *wider in pixels* than the square-pixel
    one: 486 pixels at SAR 8:9 displays 1088:2187 (about 1:2) when cropped at
    ``ih*9/16``, and 1232:2187 (about 9:16) when cropped at the SAR-aware
    width."""
    from engine.reframe import vertical_crop_width

    assert vertical_crop_width(486, 1, 1) == 274   # square pixels
    anamorphic = vertical_crop_width(486, 8, 9)
    assert anamorphic == 308, anamorphic
    # the resulting display ratio, not the pixel ratio, is what must be 9:16
    assert abs(anamorphic * 8 / (486 * 9) - 9 / 16) < 0.002
    # and the crop never comes out wider than the source it is cut from
    assert vertical_crop_width(486, 8, 9) < 720


def test_an_anamorphic_source_renders_to_a_genuinely_9_16_frame(tmp_path):
    """The defect this pins: a real 720x486 SAR 8:9 source passed through the
    vertical filter came out 272x486 SAR 8:9, which displays at 1088:2187 —
    nearer 1:2 than 9:16. Content was never distorted; the ratio was simply
    wrong, because a 9:16 *pixel* count is only 9:16 for square pixels."""
    from engine.reframe import vertical_crop_filter

    video = _anamorphic_video(tmp_path / "ntsc.mp4")
    filt = vertical_crop_filter(video, start=0.0, end=1.5)
    assert filt.startswith("crop=308:486:x='"), filt

    out = tmp_path / "vertical.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-t", "1",
         "-vf", f"{filt},scale=min(1080\\,iw):-2:flags=lanczos",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    w, h, sar_w, sar_h = _probe_geometry(out)
    dar = w * sar_w / (h * sar_h)
    assert abs(dar - 9 / 16) < 0.005, (
        f"the vertical output must display at 9:16, got {w}x{h} SAR "
        f"{sar_w}:{sar_h} -> DAR {dar:.4f}"
    )


def test_a_square_pixel_source_is_unchanged_by_the_aspect_correction(tmp_path):
    """The fix has to be a no-op for the common case: a square-pixel source
    must still render to 9:16 with its pixels undistorted."""
    from engine.reframe import vertical_crop_filter

    video = _anamorphic_video(tmp_path / "square_px.mp4", sar="1:1",
                              size="640x480")
    filt = vertical_crop_filter(video, start=0.0, end=1.5)
    # 480 * 9/16 = 270 -> rounded to even
    assert filt.startswith("crop=270:480:x='"), filt

    out = tmp_path / "vertical.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-t", "1", "-vf",
         f"{filt},scale=min(1080\\,iw):-2:flags=lanczos",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    w, h, sar_w, sar_h = _probe_geometry(out)
    assert abs(w * sar_w / (h * sar_h) - 9 / 16) < 0.005


def test_a_source_already_narrower_than_9_16_is_not_dropped(tmp_path):
    """A portrait source cannot be cropped to 9:16 at all; the width clamp
    passes it through at its own ratio instead of asking the crop filter for
    a window wider than the frame, which ffmpeg rejects outright."""
    from engine.reframe import vertical_crop_filter

    video = _anamorphic_video(tmp_path / "portrait.mp4", sar="1:1",
                              size="360x720")
    filt = vertical_crop_filter(video, start=0.0, end=1.5)
    assert filt.startswith("crop=360:720:x='"), filt

    out = tmp_path / "vertical.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-t", "1", "-vf", filt,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )


