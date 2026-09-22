"""Render path — real ffmpeg, no mocking.

render_clip is the product output step (MP4 + optional burned captions, with
concat for multi-interval candidates). It needs a real encoder, so these skip
cleanly when ffmpeg is absent but otherwise run the genuine subprocess.

The burned-caption tests below decode the MP4's pixels rather than checking the
file exists. That distinction is the whole point: ``_render_one`` appends the
``subtitles`` filter conditionally, and a refactor that dropped it — or that
made the filtergraph silently invalid — would leave every existing size and
duration assertion green while shipping a video with no captions in it. The
delivery path is the one place the burn is claimed, so it is the one place the
burn is measured.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from engine.captions import (MARGIN_H, PLAYRES_X, ass_for_candidate,
                             caption_presets)
from engine.render import render_clip, require_ffmpeg
from models.core import Candidate, Word

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not available")


def _video(out: Path, frames: int = 30, size: int = 320, fps: int = 15) -> Path:
    import cv2
    import numpy as np

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out), fourcc, fps, (size, size))
    try:
        for i in range(frames):
            frame = np.zeros((size, size, 3), dtype=np.uint8)
            cv2.putText(
                frame, f"frame {i}", (20, size // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2,
            )
            writer.write(frame)
    finally:
        writer.release()
    return out


def _candidate_with_words(start: float, end: float) -> Candidate:
    words = [
        Word("hello", start + 0.0, start + 0.4),
        Word("world", start + 0.4, start + 0.8),
        Word("today", start + 0.8, start + 1.2),
    ]
    return Candidate(
        start, end, "hello world today", "story", "hello", "world today",
        editorial_score=0.8, virality_score=0.7, context_score=0.9,
        confidence=0.9, reason="t", visual_potential=0.8, words=words,
    )


def test_require_ffmpeg_raises_when_absent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg is required"):
        require_ffmpeg()


def test_render_horizontal_clip(tmp_path):
    source = _video(tmp_path / "source.mp4")
    out = render_clip(source, _candidate_with_words(0.0, 1.3), tmp_path / "fb" / "01.mp4")
    assert out.is_file()
    assert out.stat().st_size > 0
    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,sample_aspect_ratio",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    w, h, sar = probe.stdout.strip().split(",")
    w, h = int(w), int(h)
    # The landscape deliverable's frame must display at 16:9. A square-pixel
    # source gets pillarboxed rather than stretched or cropped.
    sar_w, sar_h = (int(p) for p in sar.split(":"))
    assert (w * sar_w) / (h * sar_h) == pytest.approx(16 / 9, abs=0.02)
    # F12: a sub-1080p source must not be *upscaled*. The frame is wider than
    # the source now — pillarbox bars, not a rescale — so the invariant is that
    # the frame is never taller than the source: the picture fits inside the
    # frame, so its height cannot exceed the source's own 320 lines.
    assert h <= 320, f"the source picture was upscaled to a {h}px-tall frame"
    # no leftover part directories on the single-interval path
    assert not (tmp_path / "fb" / ".01_parts").exists()


def test_render_downscales_larger_sources(tmp_path):
    """A source wider than the delivery size is scaled down to 1920."""
    import subprocess

    wide = tmp_path / "wide.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         "color=c=blue:s=2560x1440:d=1.5", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(wide)],
        check=True, capture_output=True,
    )
    out = render_clip(wide, _candidate_with_words(0.0, 1.2), tmp_path / "fb" / "01.mp4")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    w, h = (int(x) for x in probe.stdout.strip().split(","))
    assert w == 1920 and h == 1080, f"expected downscale to 1920x1080, got {w}x{h}"


def test_render_vertical_clip_is_9_16(tmp_path):
    source = _video(tmp_path / "source.mp4")
    out = render_clip(
        source, _candidate_with_words(0.0, 1.3),
        tmp_path / "sh" / "01.mp4", vertical=True,
    )
    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    w, h = (int(x) for x in probe.stdout.strip().split(","))
    assert h / w == pytest.approx(16 / 9, abs=0.02)


def _anamorphic_video(out: Path, size: str = "720x486", sar: str = "8/9") -> Path:
    """720x486 pixels at SAR 8:9, i.e. DAR 320:243 — real NTSC DV shape.

    Written with ffmpeg, not cv2: OpenCV cannot record a sample aspect ratio,
    and the whole point is that the pixels are not square. The ratio is spelled
    with a slash because ffmpeg's setsar misreads the colon form of a
    sub-unitary ratio and writes 8:1 instead.
    """
    import subprocess

    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=size={size}:rate=12:duration=2",
         "-vf", f"setsar={sar}", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "28", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_render_vertical_clip_is_9_16_in_display_space_for_anamorphic(tmp_path):
    """The real defect, end to end: an anamorphic source cropped at
    ``ih*9/16`` *pixels* came out 272x486 SAR 8:9, which displays at
    1088:2187 — nearer 1:2 than 9:16. The pixel count is only the right ratio
    when the pixels are square; the crop width has to be scaled by the sample
    aspect ratio so the *display* ratio lands on 9:16."""
    import subprocess

    source = _anamorphic_video(tmp_path / "ntsc.mp4")
    out = render_clip(
        source, _candidate_with_words(0.0, 1.3),
        tmp_path / "sh" / "01.mp4", vertical=True,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries",
         "stream=width,height,sample_aspect_ratio,display_aspect_ratio",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    w, h, sar, _dar = probe.stdout.strip().split(",")
    sar_w, sar_h = (int(p) for p in sar.split(":"))
    # The pixel dimensions are NOT 9:16 and must not be asserted to be: the
    # frame stays anamorphic, so the ratio that has to be right is the one a
    # player displays. 272x486 SAR 8:9 scores 0.497 here; 308x486 scores 0.563.
    dar = int(w) * sar_w / (int(h) * sar_h)
    assert dar == pytest.approx(9 / 16, abs=0.005), (
        f"vertical output displays at {dar:.4f}, not 9:16 "
        f"({w}x{h} SAR {sar})"
    )


def _flat_source(out: Path, size: int = 320, dur: float = 2.0) -> Path:
    """A flat grey source, used as a control for the burn.

    Every frame is a single colour, so the uncaptioned render is provably
    uniform: whatever non-background pixels appear in the captioned render can
    only have been drawn by the subtitle filter. ``_video`` puts its own text in
    the middle of the frame, which would make that inference impossible.

    The source is 16:9 because the landscape fit pads rather than crops: a
    square source lands in a 568x320 frame with black pillarbox bars, and the
    bars make the "control" frame span 0-64 on its own. At 16:9 the picture
    fills the frame exactly and the control is genuinely flat.
    """
    h = size * 9 // 16
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c=0x404040:s={size}x{h}:d={dur}:rate=15",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(out)],
        check=True, capture_output=True,
    )
    return out


def _frames(video: Path) -> "list":
    """Every frame of an MP4 as a 2-D grayscale array, decoded by ffmpeg.

    ffmpeg rather than cv2 because the control render is flat and the test
    needs the exact byte layout with no colourspace negotiation in between.
    """
    import numpy as np

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    )
    w, h = (int(x) for x in probe.stdout.strip().split(","))
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    ).stdout
    n = len(raw) // (w * h)
    return [
        np.frombuffer(raw, dtype=np.uint8, count=w * h, offset=i * w * h).reshape(h, w)
        for i in range(n)
    ], (w, h)


def _ink_mask(burned, control) -> "tuple":
    """Pixels the caption drew, as (ys, xs) index arrays.

    Compared per frame against the *same frame* of the uncaptioned control, so
    a difference has to be the subtitle filter and not the encoder's own
    variation between two runs of the same input.
    """
    import numpy as np

    assert len(burned) == len(control), (
        f"the captioned and uncaptioned renders disagree on frame count "
        f"({len(burned)} vs {len(control)}); the burn cannot be compared frame "
        f"by frame"
    )
    ys, xs = [], []
    for b, c in zip(burned, control):
        diff = np.abs(b.astype(np.int16) - c.astype(np.int16)) > 20
        if diff.any():
            fy, fx = np.where(diff)
            ys.append(fy)
            xs.append(fx)
    if not ys:
        return np.array([], dtype=int), np.array([], dtype=int)
    return np.concatenate(ys), np.concatenate(xs)


# The caption band, as a fraction of frame height. Every preset lays out on a
# PLAYRES_Y canvas with Alignment 2 (bottom-centred) and a bottom margin of
# 165-180, so the ink's top edge sits around (1920 - 180 - one line) / 1920 =
# 0.86. The bound is set at 0.70 deliberately: it has to be loose enough that a
# two-line cue or a larger substituted font still passes, and tight enough that
# a caption rendered at the top of the frame — a real failure mode for a
# mis-set alignment — is caught.
_CAPTION_BAND_TOP = 0.70


@pytest.mark.parametrize("style", sorted(caption_presets()))
def test_the_burned_captions_are_in_the_pixels(tmp_path, style, monkeypatch):
    """The delivery path really does composite the ASS into the MP4.

    ``docs/EDITORIAL_EVALUATION.md`` asserts captions are burned into both
    renders, and ``docs/product-gap-map.md`` prices the capability as
    "complete and verified" — but the check underneath that claim was this
    file asserting the MP4 was non-empty. A dropped ``subtitles`` filter would
    have passed it. So the pixels are decoded and compared against an
    uncaptioned render of the same source: the ink has to exist, sit in the
    caption band, and stay inside the horizontal safe area the ASS declares.
    """
    import numpy as np

    monkeypatch.setenv("SELEKYT_CAPTION_STYLE", style)
    source = _flat_source(tmp_path / "source.mp4")
    c = _candidate_with_words(0.0, 1.3)

    control, _ = _frames(
        render_clip(source, c, tmp_path / "fb" / "plain.mp4")
    )
    assert control, "the uncaptioned control render produced no frames"
    # The control is the precondition for the whole comparison: a source whose
    # own content changed between renders would make any difference ambiguous.
    flat = np.concatenate(control)
    assert flat.max() - flat.min() <= 24, (
        f"the uncaptioned render of a flat source is not flat "
        f"({flat.min()}-{flat.max()}); the captioned render cannot then be "
        f"attributed to the subtitle filter"
    )

    ass = ass_for_candidate(c, tmp_path / "captions" / "01.ass")
    burned, (w, h) = _frames(
        render_clip(source, c, tmp_path / "fb" / "burned.mp4", ass=ass)
    )
    ys, xs = _ink_mask(burned, control)
    assert ys.size, (
        f"{style}: the captioned MP4 is pixel-identical to the uncaptioned one; "
        f"the ASS was written but never composited"
    )
    assert ys.min() >= _CAPTION_BAND_TOP * h, (
        f"{style}: ink appears at row {ys.min()} of {h}, above the caption band "
        f"that starts at {_CAPTION_BAND_TOP:.0%} of the frame height"
    )
    # The ASS declares MARGIN_H of PLAYRES_X on each side, so the ink has to
    # stay inside it. A few pixels of slack for the outline and shadow, which
    # are drawn outside the glyph and are what makes the text readable.
    frac = MARGIN_H / PLAYRES_X
    assert xs.min() >= frac * w - 6, (
        f"{style}: ink at column {xs.min()} of {w} runs into the left safe-area "
        f"margin"
    )
    assert xs.max() < (1 - frac) * w + 6, (
        f"{style}: ink at column {xs.max()} of {w} runs into the right "
        f"safe-area margin"
    )


def test_the_burned_captions_survive_the_vertical_crop(tmp_path):
    """The vertical path crops and rescales before compositing, which is where
    an ASS written against a 1080x1920 canvas meets a frame of a different
    shape. The ink has to land in the band on that geometry too."""
    source = _flat_source(tmp_path / "source.mp4")
    c = _candidate_with_words(0.0, 1.3)
    control, _ = _frames(
        render_clip(source, c, tmp_path / "sh" / "plain.mp4", vertical=True)
    )
    assert control
    ass = ass_for_candidate(c, tmp_path / "captions" / "01.ass")
    burned, (w, h) = _frames(
        render_clip(source, c, tmp_path / "sh" / "burned.mp4", ass=ass,
                    vertical=True)
    )
    ys, _ = _ink_mask(burned, control)
    assert ys.size, "the vertical render is pixel-identical with and without captions"
    assert ys.min() >= _CAPTION_BAND_TOP * h, (
        f"ink appears at row {ys.min()} of {h} on the vertical render, above "
        f"the caption band"
    )


def test_an_absent_ass_produces_no_ink(tmp_path):
    """The other half of the contract: ``ass=None`` must not draw anything,
    because that is what the uncaptioned renders above are assumed to be, and
    what a caption-style "off" would have to mean."""
    import numpy as np

    source = _flat_source(tmp_path / "source.mp4")
    c = _candidate_with_words(0.0, 1.3)
    first, _ = _frames(render_clip(source, c, tmp_path / "fb" / "a.mp4"))
    second, _ = _frames(render_clip(source, c, tmp_path / "fb" / "b.mp4"))
    assert len(first) == len(second)
    spread = max(
        int(np.abs(a.astype(np.int16) - b.astype(np.int16)).max())
        for a, b in zip(first, second)
    )
    assert spread <= 24, (
        f"two uncaptioned renders of the same flat source differ by {spread} "
        f"levels; the control the burn test relies on is not reproducible"
    )


def test_render_rejects_empty_intervals(tmp_path):
    """An empty-text candidate has no renderable intervals (edit_plan returns
    [] only when text is blank or the span is non-positive); render must
    refuse rather than emit an empty file."""
    source = _video(tmp_path / "source.mp4")
    c = Candidate(0.0, 1.0, "", "story", "h", "p", 0.8, 0.7, 0.9, 0.9, "t")
    with pytest.raises(RuntimeError, match="no renderable intervals"):
        render_clip(source, c, tmp_path / "fb" / "01.mp4")


def test_render_restores_audio_when_input_seeking_drops_it(monkeypatch, tmp_path):
    """A part that comes out video-only is re-rendered with output seeking.

    Input seeking (``-ss`` before ``-i``) is the fast path, and on some
    containers it discards the audio stream entirely rather than seeking it —
    reproduced on a real AV1/Opus WebM, where seeking past ~15 s produced a
    video-only file from a container that provably holds audio for all 152 s.
    ffmpeg reports no error, so the only detector is the output itself. This
    test simulates that by making the first invocation write a video-only
    file, which is exactly the observable symptom the guard keys on; the
    synthetic AV1/Opus files this host can produce do not trigger the demuxer
    bug, so reproducing the container itself here is not possible.

    The second invocation re-renders with the seek moved after ``-i``, which
    decodes and discards and so cannot drop a stream.
    """
    import subprocess

    from engine import render as render_mod

    # cv2's VideoWriter emits video-only, but the guard fires only when the
    # source has an audio track to lose — add one, so the simulated drop is a
    # real loss rather than the faithful rendering of a silent source.
    silent_src = _video(tmp_path / "silent_src.mp4", frames=45, fps=15)
    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(silent_src),
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "64k", "-shortest", str(source)],
        check=True,
    )
    c = _candidate_with_words(2.0, 3.5)

    real = subprocess.run
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        # The input-seek form is identified by ``-ss`` preceding ``-i``. Make it
        # emit a video-only file with ``-an``: merely dropping the ``-c:a``
        # args does nothing, because ffmpeg maps a "best" audio stream and
        # encodes it with the container's default encoder anyway.
        if "-i" in cmd and cmd.index("-ss") < cmd.index("-i"):
            out = cmd[-1]
            return real([*cmd[:-1], "-an", out], **kw)
        return real(cmd, **kw)

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)
    render_clip(source, c, tmp_path / "fb" / "01.mp4", fps=15)

    # The fallback must actually have run, and its seek must be output-side.
    def _is_input_seek(cmd: list[str]) -> bool:
        return "-i" in cmd and cmd.index("-ss") < cmd.index("-i")

    input_seek = [x for x in calls if _is_input_seek(x)]
    output_seek = [x for x in calls if "-i" in x and not _is_input_seek(x)]
    assert input_seek and output_seek, "the audio-loss fallback did not fire"

    out = tmp_path / "fb" / "01.mp4"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert "audio" in probe.stdout, "the shipped deliverable has no audio stream"


def test_render_leaves_video_only_sources_alone(tmp_path, monkeypatch):
    """A source with no audio track must not trigger the fallback forever.

    The guard keys on 'output has no audio but the source does'; a video-only
    source satisfies neither half and so renders in one pass. Without this the
    retry would run on every video-only source for no reason.
    """
    import subprocess

    from engine import render as render_mod

    # Strip the audio track from a generated fixture.
    with_audio = _video(tmp_path / "with_audio.mp4", frames=30, fps=15)
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(with_audio), "-an", "-c:v", "copy",
         str(silent)], check=True,
    )
    c = _candidate_with_words(0.0, 1.2)

    calls: list[list[str]] = []
    real = render_mod.subprocess.run

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return real(cmd, **kw)

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)
    render_clip(silent, c, tmp_path / "fb" / "01.mp4", fps=15)

    renders = [x for x in calls if "-i" in x and "libx264" in x]
    assert len(renders) == 1, (
        f"a video-only source triggered {len(renders)} render passes, "
        f"but nothing to retry"
    )


def _probe_frames(path: Path) -> int:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=nb_frames",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip())


def test_render_multi_part_is_frame_accurate(tmp_path):
    """A multi-interval clip must render exactly the frames its exported
    timeline is stamped with.

    Passing a bare ``-t`` to ffmpeg emits every frame at or below the requested
    time, so a non-whole-frame interval rounds *up*. Each concatenated part
    does that independently, and the overshoot grew with the cut count while
    the EDL stayed on the frame grid — 2.63 frames on a two-part real-world
    clip, which tools/nle_check.py flagged as a render/timeline disagreement.
    The two candidates below ask for 5.3 and 7.3 frames at 15 fps, so the
    rounding is exercised exactly; the expected counts are what
    engine.timecode.format_smpte resolves the same intervals to, which is the
    invariant that makes an EDL describe the file that was actually shipped.
    """
    source = _video(tmp_path / "source.mp4", frames=120, fps=15)
    # 5.3 and 7.3 frames at 15 fps -> neither interval is a whole frame count,
    # which is the case the bare ``-t`` path rounded up: ffmpeg emits every
    # frame whose timestamp is at or below the request (6 and 8 frames here),
    # one more than the grid the timecode is stamped with (5 and 7). The rate
    # passed here is the same one the timeline is stamped with, mirroring how
    # the pipeline hands probe_fps(media) to both the render and the export.
    cases = [(1.0, 1.0 + 5.3 / 15.0, 5), (6.0, 6.0 + 7.3 / 15.0, 7)]
    for i, (start, end, expected) in enumerate(cases, start=1):
        out = tmp_path / "fb" / f"{i:02d}.mp4"
        # A word edge is placed exactly at the candidate's end. retention_intervals
        # now snaps a boundary that falls inside a word, so a candidate whose end
        # straddled one would have its interval moved and the frame counts below
        # would no longer be what the timeline is stamped with. Landing on an edge
        # keeps the fractional durations the rounding depends on.
        span = end - start
        c = Candidate(
            start, end, "hello world today", "story", "hello", "world today",
            editorial_score=0.8, virality_score=0.7, context_score=0.9,
            confidence=0.9, reason="t", visual_potential=0.8,
            words=[
                Word("hello", start, start + span * 0.45),
                Word("world", start + span * 0.45, end),
                Word("today", end, end + span * 0.4),
            ],
        )
        render_clip(source, c, out, fps=15)
        got = _probe_frames(out)
        assert got == expected, (
            f"clip {i} rendered {got} frames but its timeline is stamped with "
            f"{expected}: an NLE relinking this cut would not get the file that "
            f"was shipped"
        )
