"""The perception layer must read every codec the render layer can.

A subject-blind crop has one root cause worse than a missed detection: the
planner never saw a frame at all. OpenCV 5.0.0's bundled ffmpeg negotiates a
hardware pixel format for AV1 that this host cannot supply, so *every* frame of
an AV1 source fails with ``Failed to get pixel format`` while ``engine.render``
decodes the same file with the same binary without complaint. The planner then
has a non-empty time list and an all-``None`` frame list, elects nothing, and
ships the static centred default -- silently, because nothing reported that the
source was unreadable.

``engine.reframe._sample_frames`` now falls through to an ffmpeg rawvideo path
when OpenCV yields no frame. This module pins that on sources generated at run
time rather than on corpus media, so it ships with the repo and runs anywhere.

The numbers these thresholds come from were measured, not chosen:

* OpenCV on the AV1 clip: 0 frames decoded, on two independent AV1 encoders
  (libsvtav1 and libaom-av1) and on the shipped corpus AV1 source, so the gap is
  at the codec, not the file.
* After the fallback: 5 of 5 samples on the corpus AV1 source (0 of 5 before),
  6 of 6 on the clip built here.
* Pixel agreement where both paths work: 0.00 mean absolute difference on VP9,
  at most 2.41 on an MPEG-4 Part 2 source, and the per-sample pattern is exact
  zeros alternating with a small constant -- two timestamps landing one frame
  apart, not two decoders disagreeing about layout. The threshold below is set
  above that and far below a genuine disagreement (~100).

What this does and does not prove. The end-to-end assertion renders the
deliverable and reads its content, which is the outcome that actually broke.
It does not prove the *tracking* is right on AV1 -- the clip has no face, so the
assertion is about the layout decision, which is the part the blindness
corrupted.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.reframe import (  # noqa: E402
    _sample_frames, _sample_frames_ffmpeg, vertical_reframe_filter,
)

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
_HAVE_FFPROBE = shutil.which("ffprobe") is not None


def _av1_encoder() -> str | None:
    """The first AV1 encoder ffmpeg offers, or ``None`` to skip the module."""
    if not _HAVE_FFMPEG:
        return None
    encoders = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode()
    for name in ("libsvtav1", "libaom-av1"):
        if name in encoders:
            return name
    return None


_AV1 = _av1_encoder()
needs_av1 = pytest.mark.skipif(
    _AV1 is None, reason="no AV1 encoder available to build the test source")
needs_tools = pytest.mark.skipif(
    not (_HAVE_FFMPEG and _HAVE_FFPROBE),
    reason="ffmpeg and ffprobe are required")

W, H, FRAMES, RATE = 640, 360, 36, 12
# The sampled window stops short of the last frame on purpose: a sample whose
# timestamp lands exactly at end-of-stream decodes on neither path (measured:
# 5 of 6 at end == duration, 6 of 6 at 0.1 s inside), which is the reader
# answering honestly, not a fallback defect. Every sample in a strictly
# interior window must decode.
WINDOW_END = (FRAMES - 2) / float(RATE)


def _run(cmd: list[str], stdin: bytes = b"") -> subprocess.CompletedProcess:
    """Quiet subprocess: SVT-AV1 prints a config banner to stderr that is not a
    diagnostic, and a decoder that fails is the *expected* outcome of the
    control test below, so its stderr must not become noise in the report."""
    return subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)


def _off_centre_av1(out: Path) -> Path:
    """An AV1 clip whose static bright content covers the right half and whose
    left half is moving noise.

    The two halves disagree about where the vertical crop should go: the static
    centre the blind planner defaults to straddles both, while the content
    region is entirely on the right. That disagreement is what makes the
    end-to-end test meaningful -- a layout decision that was computed from
    frames lands somewhere a blind default cannot.
    """
    rng = np.random.default_rng(0)
    half = W // 2
    frames = []
    for _ in range(FRAMES):
        f = np.full((H, W, 3), 24, dtype=np.uint8)
        f[:, :half] = rng.integers(0, 40, (H, half, 3), dtype=np.uint8)
        f[:, half:] = 210
        frames.append(f.tobytes())
    proc = _run(
        ["ffmpeg", "-y", "-loglevel", "error", "-hide_banner", "-nostats",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(RATE), "-i", "-",
         "-c:v", _AV1, "-preset", "8", "-crf", "30",
         "-pix_fmt", "yuv420p", "-an", str(out)],
        stdin=b"".join(frames))
    if proc.returncode != 0:
        raise RuntimeError(f"could not build the AV1 test source: "
                           f"{proc.stderr.decode()[-400:]}")
    return out


@needs_av1
def test_opencv_decodes_zero_frames_of_an_av1_source(tmp_path):
    """The control: this is the condition the fallback exists for.

    Written to fail rather than pass vacuously, and written to fail *loudly* if
    a future OpenCV build decodes AV1 -- at that point the fallback is no longer
    needed on this host and someone should re-read whether it is still earning
    its complexity, rather than leaving a second decode path running forever.
    """
    import cv2

    clip = _off_centre_av1(tmp_path / "offcentre.mp4")
    cap = cv2.VideoCapture(str(clip))
    try:
        assert cap.isOpened(), "the capture must open; the failure is in decoding"
        decoded = 0
        while True:
            ok, _ = cap.read()
            if not ok:
                break
            decoded += 1
    finally:
        cap.release()
    assert decoded == 0, (
        f"OpenCV decoded {decoded} frames of the AV1 source; the fallback "
        f"exists because it decoded none, so this test's premise is gone")


@needs_av1
@needs_tools
def test_the_fallback_reads_the_frames_opencv_returns_none_for(tmp_path):
    """The measured fix: every requested sample decodes, with the dimensions
    and the shape every consumer of ``_sample_frames`` assumes."""
    clip = _off_centre_av1(tmp_path / "offcentre.mp4")
    times, frames, w, h = _sample_frames(clip, 0.0, WINDOW_END, 6)

    assert len(times) == 6
    assert all(f is not None for f in frames), (
        "the fallback must supply a frame for every sample; a None here is a "
        "sample the planner silently does without")
    assert (w, h) == (W, H), "dimensions must come from the file, not a default"
    assert all(f.shape == (H, W, 3) for f in frames)
    assert times[0] == 0.0 and abs(times[-1] - WINDOW_END) < 0.01


@needs_av1
@needs_tools
def test_an_unreadable_codec_no_longer_ships_a_content_blind_crop(tmp_path):
    """End to end: the deliverable for an AV1 source is composed from its
    frames, not defaulted from its absence.

    Before the fallback this source planned the static centred crop, which
    straddles the noise and the panel and delivered a frame of mean brightness
    113 against a panel of 210. The composition now centres on the content
    region and the delivered frame is the panel. That is the outcome the
    blindness broke, and it is asserted on rendered pixels rather than on the
    filter string, because the string is not what the editor sees.
    """
    import cv2

    clip = _off_centre_av1(tmp_path / "offcentre.mp4")
    filt = _vertical_filter(clip)
    assert "crop=" in filt, f"the planner must still produce a crop: {filt!r}"

    out = tmp_path / "delivered.mp4"
    proc = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clip),
                 "-frames:v", "12", "-vf", filt, "-c:v", "libx264",
                 "-preset", "veryfast", "-crf", "20", "-an",
                 "-pix_fmt", "yuv420p", str(out)])
    assert proc.returncode == 0, (
        "the deliverable failed to render: "
        f"{proc.stderr.decode()[-300:]}")

    cap = cv2.VideoCapture(str(out))
    ok, frame = cap.read()
    dims = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    assert ok and frame is not None, "the deliverable decoded no frames at all"
    assert dims[1] > dims[0], (
        f"the deliverable must be portrait, got {dims[0]}x{dims[1]}")

    mean = float(frame.mean())
    assert mean >= 150.0, (
        f"the delivered window has mean brightness {mean:.0f} against a content "
        f"region of 210 and noise of ~30; the crop straddles both halves, which "
        f"is what a planner that never saw the frames produces")


def _vertical_filter(clip: Path) -> str:
    """The composed vertical filter for the whole clip.

    Imported here rather than at module scope: the module's own skip conditions
    are about the *source*, and importing the planner eagerly would load the
    detector for the control test that does not need it.
    """
    from engine.reframe import vertical_reframe_filter

    return vertical_reframe_filter(clip, 0.0, WINDOW_END)


@needs_tools
def test_the_ffmpeg_path_agrees_with_opencv_where_both_work(tmp_path):
    """Non-regression: the fallback must not change results on the codecs the
    primary path already reads.

    The two paths are compared directly, not through the planner, so a
    disagreement is attributable to the reader rather than to anything
    downstream that might mask or amplify it.
    """
    from test_reframe import _moving_square_video

    source = _moving_square_video(tmp_path / "square.mp4")
    _t, primary, w, h = _sample_frames(source, 0.0, 1.8, 8)
    _t2, fallback, w2, h2 = _sample_frames_ffmpeg(source, 0.0, 1.8, 8)

    assert (w, h) == (w2, h2), "the two readers disagree on the frame size"
    assert len(primary) == len(fallback)
    assert all(a is not None and b is not None for a, b in zip(primary, fallback))
    worst = max(float(np.abs(a.astype(int) - b.astype(int)).mean())
                for a, b in zip(primary, fallback))
    assert worst <= 4.0, (
        f"the fallback's frames differ from the primary path's by {worst:.2f} "
        f"mean absolute difference per sample; the measured residual on this "
        f"source is at most 2.41 and is a frame-landing offset, so anything "
        f"larger is a reader disagreement, not a seek artefact")


@needs_tools
def test_a_source_neither_backend_reads_reports_no_frames(tmp_path):
    """The contract the planner's fallback depends on.

    ``_reframe_track`` branches on an empty time list, so a reader that
    returned a list of ``None`` frames instead would put it on the all-frames-
    are-unusable path with a frame size it cannot know. Both readers must agree
    that an unreadable file has no samples, not samples with no content.
    """
    not_a_video = tmp_path / "notes.txt"
    not_a_video.write_text("this is not a video stream\n" * 8)

    times, frames, w, h = _sample_frames(not_a_video, 0.0, 1.0, 4)
    assert times == [], (
        f"an unreadable file yielded {len(times)} sample times; the planner "
        f"treats an empty list as 'compose nothing', and times with no frames "
        f"is a third state it has no branch for")
    assert frames == []
    assert (w, h) == (0, 0), (
        "an unreadable file must not report a guessed frame size; the crop "
        "width is derived from it, so a default here would silently build a "
        "9:16 window of the wrong width")


@needs_tools
def test_a_source_no_backend_reads_raises_rather_than_ships_an_unrenderable_crop(
        tmp_path):
    """The failure the decode work exposes.

    A source neither reader can decode used to reach the renderer as
    ``crop=1:0:x='0':y=0,scale=2:4`` -- measured, by re-injecting the contract
    the pre-fix ``analyse_scene`` returned and rendering it -- and ffmpeg
    rejects that while configuring the crop pad: exit 234, a zero-byte output
    file, and nothing in the message to say the *source* was the problem. That
    is the worst shape this failure can take: an editor diagnosing it is pointed
    at the render, not the ingest.

    Now it raises :class:`~engine.reframe_scenes.UnreadableSource`, which names
    the cause and keeps the two failures apart -- "no subject detected" is a
    composition question answered with a centred crop, "no frame decoded" is an
    ingest question that composition cannot answer.

    The landscape path is deliberately *not* made to raise: it has a documented
    1080p default for unreadable geometry, so a landscape deliverable still
    ships rather than failing the whole render over one unreadable part.
    """
    from engine.reframe import landscape_filter
    from engine.reframe_scenes import UnreadableSource

    not_a_video = tmp_path / "notes.txt"
    not_a_video.write_text("this is not a video stream\n" * 8)

    with pytest.raises(UnreadableSource, match="no frame decoded"):
        vertical_reframe_filter(not_a_video, 0.0, 2.0)

    # The landscape deliverable must not regress to the 1x1 frame this source
    # used to produce. A guard that documents a 1920x1080 default but never
    # fired, because the planner clamped to 1x1 first, was measured here as
    # ``scale=2:2``.
    landscape = landscape_filter(not_a_video)
    assert "1920:1080" in landscape, (
        f"the landscape path must fall back to its documented 1080p frame for "
        f"an unreadable source, got {landscape!r}")


@needs_tools
def test_a_truncated_source_decodes_what_survives_without_partial_frames(
        tmp_path):
    """A real container that stops early: the failure mode a network capture
    produces.

    How many samples survive depends on where the cut lands, which is not
    something to pin. What is pinned is that the result is one of the two states
    the planner has a branch for -- either a frame for a sample, or no sample at
    all -- and never a buffer shaped to dimensions the data no longer supports.
    """
    from test_reframe import _moving_square_video

    source = _moving_square_video(tmp_path / "whole.mp4")
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(source.read_bytes()[: max(1024, source.stat().st_size // 4)])

    times, frames, w, h = _sample_frames(truncated, 0.0, 1.8, 8)
    if times:
        assert all(isinstance(t, float) for t in times)
        assert all(f is None or f.shape[2] == 3 for f in frames)
        # Every decoded frame must match the dimensions the reader reported, or
        # the crop width derived from them would be built against the wrong frame.
        for f in frames:
            if f is not None:
                assert (f.shape[1], f.shape[0]) == (w, h)
    else:
        assert frames == []
        assert (w, h) == (0, 0)
