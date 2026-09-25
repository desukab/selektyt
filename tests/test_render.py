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


def test_encoder_settings_are_the_measured_ones():
    """The deliverable's x264 settings were chosen by measurement, and a silent
    change back to the old pair would look like a tidy small-file improvement
    while costing 2.6 dB of picture quality.

    On real corpus footage (the 1280x720, 29.97 fps pentagon briefing) scored
    against a -crf 0 reference of the same filter chain, 240 frames through the
    tracked 406x722 crop:

        preset     crf    kbps    SSIM     PSNR
        veryfast    20    837.9  0.9991    40.54   <- the old settings
        slow        18   1211.9  0.9995    43.18   <- current

    veryfast/crf20 is the lowest-quality entry on the ladder; it is also the
    cheapest, so slow/crf18 is a deliberate quality purchase rather than a
    free win (+2.64 dB for +44% bitrate). Any edit that drops one of these three
    flags has to re-run that bench and update the docstring in ``_encoder_args``,
    not just the assertion.
    """
    from engine.render import _encoder_args

    args = _encoder_args()
    assert "-preset" in args and args[args.index("-preset") + 1] == "slow", \
        "preset is not 'slow'; the measured quality floor no longer holds"
    assert "-crf" in args and args[args.index("-crf") + 1] == "18", \
        "CRF is not 18; re-measure before changing this"
    assert "-pix_fmt" in args and args[args.index("-pix_fmt") + 1] == "yuv420p", \
        "yuv420p is the only plan every player accepts; a 4:2:2/4:4:4 master " \
        "would otherwise ship chroma subsampling half the decoders refuse"


def _video_with_audio(out: Path, duration: float = 4.0) -> Path:
    """A 1280x720 source with a genuine AAC track.

    The cv2 helper writes video-only, and half the properties worth testing about
    a delivered MP4 only exist when there is a second stream to be in sync *with*
    — chiefly whether the cut list shifted the picture relative to the sound.
    """
    import subprocess

    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=size=1280x720:rate=30:duration={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
         "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)],
        check=True, capture_output=True,
    )
    return out


def _stream_start_and_duration(path: Path, stream: str) -> tuple[float, float]:
    """``start_time`` and ``duration`` of one stream, honouring edit lists.

    Use this rather than reading packet timestamps directly. ``ffprobe
    -show_entries packet=dts_time,pts_time`` prints the two columns in the
    AVPacket struct's internal field order, which is pts *then* dts regardless of
    the order requested on the command line — the columns are silently swapped,
    and a monotonic decode-order DTS sequence reads back as a scrambled PTS one.
    That misreading once produced a "non-monotonic DTS" alarm that was entirely
    an artefact of the probe. ``start_time`` is what the muxer reports after
    applying edit lists, so it is the view a player actually gets.
    """
    import subprocess

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", "stream=start_time,duration", "-of",
         "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    start, duration = probe.stdout.strip().split(",")
    return float(start), float(duration)


def test_the_shipped_frame_is_8bit_420(tmp_path):
    """``yuv420p`` is asserted in the argument list, but the argument list can
    lie — the encoder could reject it, or a source with unusual chroma could
    override it. Decoding the deliverable is what actually gets shipped."""
    import subprocess

    source = _video(tmp_path / "source.mp4")
    out = render_clip(
        source, _candidate_with_words(0.0, 1.3),
        tmp_path / "sh" / "01.mp4", vertical=True,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt", "-of",
         "default=noprint_wrappers=1:nokey=1", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "yuv420p", \
        f"shipped pixel format is {probe.stdout.strip()!r}, not yuv420p"


def test_concatenated_parts_keep_audio_and_video_aligned(tmp_path):
    """A multi-part deliverable must not drift between its streams.

    Each part is encoded independently and then copied through the concat
    demuxer, so every part boundary is an opportunity for the muxer to offset the
    picture from the sound by a frame or two. A two-frame error is inaudible in
    isolation but compounds across a 15-part supercut, and the deliverable would
    still open, play, and look fine — nothing in the pipeline would notice.
    Measured at 1/2/3/5 parts: head and tail drift both 0.000 s throughout, so
    this is a property worth pinning rather than a defect being patched.
    """
    source = _video_with_audio(tmp_path / "audio_source.mp4")

    # Three intervals, so the render goes through the concat path rather than the
    # single-part shutil.move shortcut.
    words = [
        Word("one", 0.10, 0.40), Word("two", 0.90, 1.20),
        Word("three", 2.00, 2.30),
    ]
    candidate = Candidate(
        0.05, 2.40, "one two three", "story", "one", "three",
        editorial_score=0.8, virality_score=0.7, context_score=0.9,
        confidence=0.9, reason="t", words=words,
    )
    out = render_clip(
        source, candidate, tmp_path / "av" / "concat.mp4", vertical=True,
    )

    v_start, v_dur = _stream_start_and_duration(out, "v:0")
    a_start, a_dur = _stream_start_and_duration(out, "a:0")

    assert v_start == pytest.approx(a_start, abs=0.04), (
        f"the picture starts {v_start - a_start:+.3f} s from the sound; a cut "
        f"list shifted one stream and not the other"
    )
    assert v_dur == pytest.approx(a_dur, abs=0.04), (
        f"video is {v_dur:.3f} s but audio is {a_dur:.3f} s; the two streams "
        f"disagree about how long this clip is, so the tail drifts out of sync"
    )
    # The deliverable must still carry audio at all: a part that came out
    # video-only would make the comparison above vacuously pass.
    assert a_dur > 0.0, "the deliverable has no audio stream"


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


# ── the burn must not draw placeholder boxes ──────────────────────────────
#
# The tests above ask "is there ink in the caption band?" and libass's
# .notdef fallback answers yes for a script no installed font carries: a row of
# identical hollow rectangles, one per codepoint, still lands in the band and
# inside the margins. Those tests therefore pass on a deliverable nobody can
# read, and so would any measurement derived from them. This was not a
# hypothetical — the shipped Telugu corpus render was burned on a host with no
# Telugu font, and its cue band is 13 even-pitched boxes (verified against a
# burn of the same text under a deliberately-missing font, which was
# byte-identical). The guard under test is what makes that state fail loudly
# instead of shipping.

_TELUGU_CUE = "నినాన్త్డందాన ంపెర్లిస్లిని"


def _candidate_with_text(start: float, end: float, text: str,
                         word: str) -> Candidate:
    return Candidate(
        start, end, text, "story", word, word, 0.8, 0.7, 0.9, 0.9, "t",
        words=[Word(word, start, end - 0.1)],
    )


def _ass_with_cue(out: Path, text: str) -> Path:
    """An ASS carrying exactly one cue of ``text``, bypassing the emitter.

    The guard reads whatever ASS it is handed, including one the emitter did
    not write (a cached sidecar, a hand-edited file), so the test has to be
    able to reach it without a Candidate.
    """
    out.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,"
        "SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
        "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: Default,Noto Sans,54,&H00FFFFFF,&H00FFFFFF,&H99000000,"
        "&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,70,70,170,1\n\n[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,"
        f"Text\nDialogue: 0,0:00:00.10,0:00:01.20,Default,,0,0,0,,{text}\n",
        encoding="utf-8")
    return out


def test_render_refuses_captions_the_host_cannot_draw(tmp_path, monkeypatch):
    """A script no installed font covers must fail the render, not burn boxes.

    The check is against the whole installed font set, so it has to be
    exercised with something genuinely uncovered: a Private Use Area codepoint
    is by Unicode definition not assigned, so no conformant font carries it,
    and the result does not depend on which Noto packages this host happens to
    have installed.
    """
    import engine.render as render_mod

    source = _flat_source(tmp_path / "source.mp4")
    c = _candidate_with_words(0.0, 1.3)
    ass = _ass_with_cue(tmp_path / "cue.ass", "secret word")
    with pytest.raises(RuntimeError, match="placeholder boxes") as excinfo:
        render_clip(source, c, tmp_path / "fb" / "01.mp4", ass=ass)
    # The message has to name what to install, or the failure is not actionable.
    assert "fonts-noto-core" in str(excinfo.value), (
        "the refusal does not tell the user how to fix it")
    assert not (tmp_path / "fb" / "01.mp4").exists(), (
        "a refused burn left a partial deliverable behind")
    # The probe is memoised; a later render of the same text is still refused.
    with pytest.raises(RuntimeError, match="placeholder boxes"):
        render_clip(source, c, tmp_path / "fb" / "02.mp4", ass=ass)
    assert render_mod.uncovered_script("plain latin text") == [], (
        "the guard now rejects text it should render")


def test_render_burns_telugu_when_a_font_covers_it(tmp_path):
    """The positive half of the guard: on a host that carries the script, the
    same Telugu cue that would have drawn boxes renders as real type.

    This is the test that was impossible to write before. It is host-dependent
    by necessity — whether any font covers Telugu is a property of the machine,
    not of the code — so it reports the coverage it found rather than skipping,
    and it stays honest about that: a host without a Telugu font still runs and
    asserts what it can (that the guard is what stands between it and tofu).
    """
    import engine.render as render_mod

    source = _flat_source(tmp_path / "source.mp4")
    ass = _ass_with_cue(tmp_path / "te.ass", _TELUGU_CUE)
    missing = render_mod.uncovered_script(_TELUGU_CUE)
    print(f"  Telugu coverage on this host: "
          f"{'covered' if not missing else 'MISSING ' + ', '.join(missing)}")
    if missing:
        # Not a skip: the guard is the whole point on such a host. It has to
        # refuse the cue that would draw boxes, and the reason it names has to
        # be the script the host lacks.
        with pytest.raises(RuntimeError) as excinfo:
            render_clip(source, _candidate_with_words(0.0, 1.3),
                        tmp_path / "fb" / "01.mp4", ass=ass)
        assert "Telugu" in str(excinfo.value), (
            f"the refusal names {missing} but not the Telugu script the cue "
            f"is written in")
        return

    control, _ = _frames(
        render_clip(source, _candidate_with_words(0.0, 1.3),
                    tmp_path / "fb" / "plain.mp4")
    )
    burned, (w, h) = _frames(
        render_clip(source, _candidate_with_words(0.0, 1.3),
                    tmp_path / "fb" / "burned.mp4", ass=ass)
    )
    ys, xs = _ink_mask(burned, control)
    assert ys.size, "the Telugu cue produced no ink at all"

    # Tofu's signature is uniformity: N identical boxes, each as wide as the
    # last, merging into one solid or near-solid band. Real Telugu is
    # neither — the syllable clusters vary in width and leave gaps between
    # them — so the ink column profile has to carry that variation.
    import numpy as np
    band = np.zeros((h, w), bool)
    band[ys, xs] = True
    profile = band.sum(axis=0)
    nz = profile[profile > 0]
    assert len(nz) > 0
    # Tofu is a row of abutting boxes: on this canvas 13 codepoints of
    # .notdef span one solid band. Real Telugu leaves gaps between clusters,
    # so a band with no interior gap is the shape of boxes, not of type.
    solid = int(np.diff(np.nonzero(np.r_[True, profile == 0, True])[0]).max()) \
        if (profile == 0).any() else w
    print(f"  Telugu ink: {ys.size} px across {len(nz)} columns; "
          f"widest solid run {solid}px of {w}")
    assert solid < w, (
        "the Telugu ink is one unbroken band — the shape of abutting "
        "placeholder boxes, not of syllable clusters with gaps between them")


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


# ── the committed corpus: is the burn in the pixels we actually shipped? ──────

_REPO = Path(__file__).resolve().parent.parent
_CORPUS = _REPO / "corpus"

# The runs whose source media is at hand when this runs. Comparing a shipped
# MP4 against the frames it was cut from needs the original, and neither the
# renders nor the media are committed — .gitignore keeps only the corpus's
# text artifacts, because the binary is regenerable and very large. The other
# runs' media was discarded after rendering; re-fetching it is 1.3 GB across
# four hosts, so for those the control is simply absent and the test skips
# rather than substituting a stand-in for it.
_MEDIA_FOR_PROJECT = {
    "pentagon-esper-briefing": "pentagon-esper-briefing.mp4",
    "royal-society-cloke": "royal-society-cloke.webm",
    "telugu-tutorial": "telugu-tutorial.webm",
}

# A change only counts as ink past this many levels. The synthetic-control test
# above uses 20 against a flat uncaptioned render; 20 is not enough here, where
# the control is the original source and the shipped MP4 is a re-encode of it.
# Measured on these ten clips: at 20 the re-encode's own contribution to the
# top of the caption band is 1.0-2.0% of pixels on five of them, indistinguish
# able from a faint mis-burn; at 60 it falls to at most 0.19% while the ink
# itself stays 0.53-1.61%. The ink is what survives the higher bar.
_INK_LEVEL = 60

# The floors, all measured on the committed artifacts rather than chosen to
# pass (see the survey notes in the docstring below):
#   - ink inside the safe area, per clip, median over its cues: 0.53%-1.61%.
#     The highest change found in the caption band at a moment no cue was
#     displayed was 0.037%, so 0.15% sits 4x above the no-caption baseline and
#     3.5x below the faintest real burn measured.
_BURN_FLOOR = 1.5e-3
#   - ink outside the horizontal safe area the ASS style declares: 0.0% at the
#     median on every clip measured, so the same floor read as "quiet" works.
_SAFE_SHARE_FLOOR = 0.9
_PAIRING_RATIO = 4.0
# Every cue would be a stronger sample, but the clips carry up to 33 of them
# and one frame seeked per cue is the bulk of the runtime; the floors above
# were set against per-clip medians, and an evenly spaced slice keeps such a
# sample spanning the whole clip.
_MAX_CUES_PER_CLIP = 9


@pytest.mark.parametrize("project", sorted(_MEDIA_FOR_PROJECT))
def test_the_committed_renders_carry_the_burn(project):
    """The corpus's shipped MP4s really do have captions in their pixels.

    ``docs/RED_TEAM_FINDINGS.md`` R11 established the burn by hand, comparing
    one render against its source frame, and ``docs/product-gap-map.md`` priced
    the capability on that single comparison. Nothing in the suite made the
    same measurement, so the renders the corpus's metrics cite were covered by
    a check that only asserted the files were non-empty. This test makes R11's
    measurement over every clip of every run whose source media is at hand,
    including the Telugu run, which is the one place a burn can fail for a
    reason other than a dropped filter (a font with no glyphs for the script
    produces ink too — empty boxes — so a legibility claim is *not* what is
    asserted here; presence and position are).

    Where it can run, and where it cannot. The renders and the source media are
    deliberately not in the repository: ``.gitignore`` commits only the corpus's
    text artifacts, because several hundred megabytes of regenerable binary per
    run would make the repo unclonable for no evidentiary gain. So this test
    skips in a bare clone and runs only where the corpus was actually produced —
    it is a reproducible measurement for that environment, not a CI guard. It
    does not pretend to be the latter, and the skip message names the tool that
    regenerates what it needs.

    Each rendered clip is the kept intervals concatenated, so a cue's midpoint
    in render time is mapped back through the edit plan to the source second
    it was cut from, and the shipped frame is diffed against that frame. The
    assertions are on the *shape* of the difference rather than its size, which
    is what makes them portable across footage and font sizes: ink appears in
    the bottom band, inside the horizontal margins the caption style declares,
    and nowhere else. A burn in the wrong place — the failure mode of a
    mis-set alignment or a PlayResX that disagrees with the render width — is
    what the margins and band bounds catch; a stale *style* burned into the
    pixels is not, and is pinned separately by ``test_caption_style_cache.py``.

    Thresholds were set from a survey of all ten clips (both ink levels, every
    cue) before the assertions were written, and the ink floor is calibrated
    against a negative control built from the real artifacts rather than from a
    synthetic fixture: re-cutting pentagon's clip from its source with no
    subtitles filter measures 0.047% of the band changed, the floor sits at
    0.15%, and the faintest real burn measured 0.53%. The unburned re-cut is
    the failure this test exists to catch, and it does fail on it.
    """
    import json
    import subprocess

    import numpy as np

    source = _CORPUS / "media" / _MEDIA_FOR_PROJECT[project]
    if not source.is_file():
        pytest.skip(
            f"the source media for {project} is not present — the renders and "
            f"media are not committed (see .gitignore); tools/corpus_run.py "
            f"regenerates them")
    project_dir = _CORPUS / f"{project}_run" / "project"
    if not project_dir.is_dir():
        pytest.skip(f"the {project} run is not in this checkout")

    edit_plan = {e["index"]: e
                 for e in json.loads((project_dir / "edit_plan.json").read_text())}
    assert edit_plan, f"{project} has no clips to measure"

    shape_cache: dict[Path, tuple[int, int]] = {}

    def _shape(path: Path) -> tuple[int, int]:
        """(width, height), cached: the probe is the second subprocess in a
        frame lookup and it never changes mid-run."""
        if path not in shape_cache:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0",
                 str(path)],
                capture_output=True, text=True, check=True,
            )
            shape_cache[path] = tuple(int(x) for x in out.stdout.strip().split(","))
        return shape_cache[path]

    def _frame_at(path: Path, t: float) -> "np.ndarray | None":
        """One grayscale frame, sought rather than decoded in full: the corpus
        sources run to 158 MB and this samples a handful of times per clip."""
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True,
        )
        if not raw.stdout:
            return None
        w, h = _shape(path)
        if len(raw.stdout) != w * h:
            return None
        return np.frombuffer(raw.stdout, np.uint8).reshape(h, w)

    measured = 0
    for index in sorted(edit_plan):
        render = project_dir / "facebook" / f"{index:02d}.mp4"
        ass = project_dir / "captions" / f"{index:02d}.ass"
        if not (render.is_file() and ass.is_file()):
            continue

        cues = [
            (_ass_stamp(line.split(",", 9)[1]),
             _ass_stamp(line.split(",", 9)[2]))
            for line in ass.read_text(encoding="utf-8").splitlines()
            if line.startswith("Dialogue:")
        ]
        if not cues:
            continue

        # Every cue would be a stronger sample, but the clips carry up to 33 of
        # them and the median over a spread sample is what the floors were set
        # against; an evenly spaced slice keeps the sample spanning the clip.
        if len(cues) > _MAX_CUES_PER_CLIP:
            stride = len(cues) / _MAX_CUES_PER_CLIP
            sampled = [cues[int(i * stride)] for i in range(_MAX_CUES_PER_CLIP)]
        else:
            sampled = cues

        inside, outside, above, ink_px, margin_px = [], [], [], [], []
        for start, end in sampled:
            t = (start + end) / 2
            source_t = _render_time_to_source(edit_plan[index]["intervals"], t)
            if source_t is None:
                continue
            shipped, control = _frame_at(render, t), _frame_at(source, source_t)
            if shipped is None or control is None:
                continue
            if shipped.shape != control.shape:
                pytest.fail(
                    f"{project} facebook/{index:02d}.mp4 is {shipped.shape[1]}x"
                    f"{shipped.shape[0]} against a {control.shape[1]}x"
                    f"{control.shape[0]} source, so the two cannot be compared "
                    f"frame to frame")
            changed = np.abs(shipped.astype(np.int16) - control.astype(np.int16))
            band = changed > _INK_LEVEL
            h, w = shipped.shape
            band_top = int(_CAPTION_BAND_TOP * h)
            margin = int(MARGIN_H / PLAYRES_X * w)
            in_band = band[band_top:, margin:w - margin]
            out_band = np.concatenate(
                [band[band_top:, :margin], band[band_top:, w - margin:]], axis=1)
            inside.append(in_band)
            outside.append(out_band)
            above.append(band[:band_top])
            ink_px.append(int(in_band.sum()))
            margin_px.append(int(out_band.sum()))

        if not inside:
            continue
        measured += 1

        def _median_changed(masks: "list") -> float:
            return float(np.median([m.mean() for m in masks]))

        ink = _median_changed(inside)
        share = float(np.median([
            a / (a + b) for a, b in zip(ink_px, margin_px) if a + b
        ])) if any(a + b for a, b in zip(ink_px, margin_px)) else 0.0

        assert ink >= _BURN_FLOOR, (
            f"{project} facebook/{index:02d}.mp4: the caption band changes "
            f"{ink:.4%} of its pixels against the source at the cue times its "
            f"sidecar declares, below the {_BURN_FLOOR:.4%} a real burn "
            f"measures — the shipped file may carry no captions, or the cues "
            f"may not line up with the cut")
        assert ink >= _PAIRING_RATIO * _median_changed(above), (
            f"{project} facebook/{index:02d}.mp4: the caption band changes "
            f"{ink:.4%} but the 70% of the frame above it changes "
            f"{_median_changed(above):.4%}, so the 'ink' is a re-encode or a "
            f"seek mismatch rather than a caption, and the comparison above is "
            f"not measuring anything")
        assert share >= _SAFE_SHARE_FLOOR, (
            f"{project} facebook/{index:02d}.mp4: only {share:.1%} of the ink "
            f"in the caption band falls inside the horizontal margins its ASS "
            f"style declares ({MARGIN_H}/{PLAYRES_X}), so the burn is wider "
            f"than the layout that claims it")

    assert measured, (
        f"{project} has {len(edit_plan)} clip(s) in its edit plan but no "
        f"facebook render paired with a cues-bearing ASS sidecar was measured")


def _ass_stamp(stamp: str) -> float:
    """An ASS ``h:mm:ss.cc`` cue time, as seconds."""
    hours, minutes, rest = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _render_time_to_source(intervals: list, t: float) -> "float | None":
    """Where in the source the render's clock points at ``t``.

    A rendered clip is the kept intervals concatenated in order, so render time
    walks them; a jump cut is exactly the discontinuity this skips over. ``None``
    means ``t`` falls past the end, which a truncated sidecar can ask for."""
    elapsed = 0.0
    for start, end in intervals:
        if elapsed <= t < elapsed + (end - start):
            return start + (t - elapsed)
        elapsed += end - start
    return None
