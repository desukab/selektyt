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


# ── Phase 5: slides. A documented limitation, pinned with the geometry that
# makes it a limitation and not a tuning problem.

def _speaker_and_slide_video(out: Path) -> Path:
    """A lecture layout: a head-and-shoulders subject on the left, dense slide
    text on the right, each occupying its own half of a 1280-wide frame.

    This is the layout the vertical reframe cannot serve, and the reason it
    cannot is read off the geometry this clip sets up, so the assertion below
    is about positions and widths rather than about how the output looks.
    """
    import cv2
    import numpy as np

    width, height, frames = 1280, 720, 24
    frames_arr: list[np.ndarray] = []
    for _ in range(frames):
        frame = np.full((height, width, 3), 40, np.uint8)
        # Subject, centred around x=282.
        cv2.ellipse(frame, (282, 300), (70, 95), 0, 0, 360, (210, 190, 170), -1)
        cv2.rectangle(frame, (190, 400), (374, 660), (190, 170, 150), -1)
        # Slide text, occupying x=666..1228.
        for row in range(12):
            cv2.putText(frame, f"slide line {row:02d} of twelve",
                        (680, 90 + row * 45), cv2.FONT_HERSHEY_SIMPLEX,
                        1.1, (235, 235, 235), 3)
        frames_arr.append(frame)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out), fourcc, 12, (width, height))
    try:
        for frame in frames_arr:
            writer.write(frame)
    finally:
        writer.release()
    return out


def test_no_legal_crop_holds_both_speaker_and_slide(tmp_path):
    """The limitation the Phase 5 documentation describes, as a number.

    For a 1280x720 source the crop is 406 px wide, and the speaker and the slide
    sit in opposite halves ~560 px apart centre-to-centre. Enumerating *every*
    legal crop centre found none that keeps the speaker in frame and retains at
    least half the slide text. This is why the shipped vertical crop cannot
    serve a lecture clip by adjustment: the window is too narrow for the layout,
    and the choice is between the two regions, not between two settings.

    The assertion is deliberately on the geometry rather than on the shipped
    expression's output. A change that made this test pass by widening the crop
    would break the 9:16 contract that the rest of this module pins, and a
    change that biased the crop one way would flip which of the two regions is
    thrown away. The correct fix is a second content class in the election, and
    this test exists so that fix is visibly implemented rather than assumed.
    """
    from engine.reframe import vertical_crop_width

    source = _speaker_and_slide_video(tmp_path / "lecture.mp4")
    width = 1280
    crop_w = vertical_crop_width(720, 1, 1)
    assert crop_w == 406, "the geometry below is only meaningful at this width"

    subject_x, slide_lo, slide_hi = 282, 666, 1228

    def keeps_the_subject(cx: float) -> bool:
        return cx - crop_w / 2 <= subject_x <= cx + crop_w / 2

    def slide_retained(cx: float) -> float:
        overlap = min(cx + crop_w / 2, slide_hi) - max(cx - crop_w / 2, slide_lo)
        return max(0.0, overlap) / (slide_hi - slide_lo)

    legal = [cx for cx in range(0, width) if keeps_the_subject(cx)]
    assert legal, "the subject must be holdable by *some* legal crop"
    best = max(slide_retained(cx) for cx in legal)
    assert best < 0.5, (
        f"holding the subject keeps {best:.0%} of the slide text, so speaker and "
        f"slide are mutually exclusive inside a {crop_w} px window -- if this now "
        f"reads >=50%, the crop geometry changed and the Phase 5 limitation note "
        f"in ``_reframe_track`` is out of date"
    )

    # And the converse: a window centred on the slide throws the subject away
    # entirely, which is what the edge-density fallback selects when the slide
    # is the denser half.
    on_slide = max(range(0, width), key=slide_retained)
    assert not keeps_the_subject(on_slide), (
        "centring on the slide should exclude the subject; if it does not, the "
        "layout this clip encodes no longer represents the lecture case"
    )


def test_the_slide_layout_is_reframed_rather_than_rejected(tmp_path):
    """The gap is a quality gap, not a crash: a lecture clip must still produce a
    9:16 expression. The election picks one region, and this pins that the
    choice is made and the render proceeds -- the deliverable exists, it just
    cannot show both regions, which is what the docstring in
    :func:`_reframe_track` records.
    """
    from engine.reframe import crop_x_expression, vertical_reframe_filter
    import subprocess

    source = _speaker_and_slide_video(tmp_path / "lecture2.mp4")
    expr = crop_x_expression(source, start=0.0, end=2.0)
    assert expr, "a mixed layout must still be reframed"
    # The expression has to survive the parser in the shipped position, or the
    # documented limitation would be moot -- the render would fail outright.
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), "-vf",
         vertical_reframe_filter(source, 0.0, 2.0), "-f", "null", "-"],
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


def test_the_detector_is_loaded_once_per_process(tmp_path, monkeypatch):
    """F16 (IO half): the detector's weights are the same for every clip and
    every 9:16 render, so re-parsing the model per call is duplicated work on a
    constrained host -- which was the point when the detector was Haar and is
    the same point now that it is YuNet.

    Counts model *constructions* on the primary backend rather than stubbing a
    legacy one, because that is the path every render actually takes.
    """
    import engine.reframe_perception as perception_mod

    constructed = []

    class _StubDetector:
        def __init__(self, *a, **kw):
            constructed.append(a)

        def empty(self):
            return False

        def setInputSize(self, size):
            pass

        def detect(self, frame):
            return True, np.zeros((0, 15))

    monkeypatch.setattr(perception_mod.cv2, "FaceDetectorYN_create", _StubDetector,
                         raising=False)
    monkeypatch.setattr(perception_mod.cv2, "FaceDetectorYN", _StubDetector,
                         raising=False)
    # Drop the process-level memo so the count is this test's alone.
    monkeypatch.setattr(perception_mod, "_SINGLETON", None)

    videos = [_moving_square_video(tmp_path / f"square2_{i}.mp4") for i in range(3)]
    for video in videos:
        crop_x_expression(video, start=0.0, end=1.8)
    assert len(constructed) == 1, (
        f"the model must be loaded once and reused, loaded {len(constructed)}"
    )
    # the memo survives later calls in the same process
    crop_x_expression(videos[0], start=0.0, end=1.8)
    assert len(constructed) == 1


def test_no_usable_backend_still_reframes(tmp_path, monkeypatch):
    """A host with no usable perception backend -- OpenCV 5.x, where the Haar
    class is gone, plus a build or container without the model file -- must
    still produce a crop expression rather than raising. The edge-density
    tracker takes over."""
    import engine.reframe_perception as perception_mod

    class _Boom:
        def __init__(self, *a, **kw):
            raise AttributeError("this build has no face detector at all")

    monkeypatch.setattr(perception_mod, "_SINGLETON", None)
    monkeypatch.setattr(perception_mod.cv2, "FaceDetectorYN_create", _Boom,
                         raising=False)  # not every build ships the symbol
    monkeypatch.setattr(perception_mod.cv2, "FaceDetectorYN", _Boom,
                         raising=False)
    monkeypatch.setattr(perception_mod.cv2, "CascadeClassifier", _Boom,
                         raising=False)

    video = _moving_square_video(tmp_path / "square3.mp4")
    detector = perception_mod.FaceDetector()
    assert detector.available is False, (
        f"with no backend the detector must report unavailable, got {detector.kind}"
    )
    expr = crop_x_expression(video, start=0.0, end=1.8)
    assert expr, "an unusable backend must not prevent reframing"
    assert "if(lt(t," in expr, "without a detector the fallback must still move the crop"


# ── anamorphic sources: the 9:16 crop must be 9:16 in *display* space ──


def _anamorphic_video(out: Path, sar: str = "8/9", size: str = "720x486",
                      duration: str = "2") -> Path:
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
         f"-i", f"testsrc=size={size}:rate=12:duration={duration}",
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
    wrong, because a 9:16 *pixel* count is only 9:16 for square pixels.

    This burns the shipped chain — :func:`vertical_reframe_filter` — rather than
    a recomposition of it, so it moves when the reframe moves. ``testsrc`` is
    static and structured enough that the scene planner holds it as a SLIDE, so
    the chain here is the composed slide layout and the pins below are its
    invariants rather than a transcription of its text.

    The frame is 308x486 *pixels*, which is only 9:16 because it keeps SAR 8:9,
    so two things have to hold at once: the SAR survives the composition, and
    the source window the layout cuts has the same *pixel* aspect as the frame
    it is scaled onto. The second is the no-squeeze condition, and it is the one
    that silently broke when the band geometry carried a sample-aspect factor
    that only a square-pixel band would need (see
    ``test_an_anamorphic_slide_is_not_squeezed``).
    """
    from engine.reframe import vertical_reframe_filter

    video = _anamorphic_video(tmp_path / "ntsc.mp4")
    filt = vertical_reframe_filter(video, start=0.0, end=1.5)
    assert filt.startswith("crop="), filt
    crop = filt.split(",")[0][len("crop="):]
    cw_s, ch_s = crop.split(":")[:2]
    cw, ch = int(cw_s), int(ch_s)

    # the SAR is carried into the delivered frame: without this the same pixel
    # count measures DAR 0.6337 instead of 0.5625
    assert filt.endswith("setsar=8/9"), filt
    # the scale stage lands on the SAR-aware target height
    assert "scale=456:720" in filt, filt
    # the window's pixel aspect matches the frame's, so the scale onto it is a
    # pure resize and the content is not squeezed sideways
    assert abs(cw / ch - 308 / 486) < 0.01, (
        f"the slide window {cw}x{ch} must have the frame's pixel aspect, got "
        f"{cw / ch:.4f} vs {308 / 486:.4f}"
    )

    out = tmp_path / "vertical.mp4"
    dar, w, h = _rendered_dar(video, filt, out)
    assert (w, h) == (456, 720), (
        f"the anamorphic crop is already sub-1080, so it must not be "
        f"rescaled, got {w}x{h}"
    )
    pw, ph, sar_w, sar_h = _probe_geometry(out)
    assert (sar_w, sar_h) == (8, 9), (
        f"the delivered frame must keep the source's SAR 8:9, got {sar_w}:{sar_h}"
    )
    assert abs(dar - 9 / 16) < 0.005, (
        f"the vertical output must display at 9:16, got {w}x{h} -> DAR {dar:.4f}"
    )


def test_an_anamorphic_slide_is_not_squeezed(tmp_path):
    """The second defect the anamorphic source exposed, measured in pixels
    rather than in ratios.

    The slide layout cuts a source window and scales it onto the delivered
    band. The band keeps the source's SAR, so the SAR appears on both sides of
    the no-squeeze condition and cancels: the window needs the band's *pixel*
    aspect, ``out_w/out_h``, and no aspect factor. Carrying the factor the
    other way — ``out_w * sar_h / (out_h * sar_w)`` — is the shape a
    *square-pixel* band would need, and on a 720x486 SAR 8:9 source it squeezes
    the content about 11% sideways: a square in the frame comes out a tall
    rectangle.

    A source holding one white square on black, long enough for the slide
    detector to hold it, is rendered and the delivered blob's width/height is
    read back out. A square in must be a square out. Both sides of the
    comparison carry the same SAR, so it cancels in the measurement too, and
    the assertion is on the content rather than on the container.
    """
    import cv2
    import numpy as np

    from engine.reframe import vertical_reframe_filter

    w, h = 720, 486
    fps = 25
    seconds = 4
    # A square in source pixels, clear of the frame edges: the exact-aspect
    # window the layout cuts around it is centred on the box, so a box clear of
    # the edges is clear of the window.
    b0, b1 = (250, 120), (470, 340)
    blob_w, blob_h = b1[0] - b0[0] + 1, b1[1] - b0[1] + 1

    frame = np.zeros((h, w, 3), np.uint8)
    cv2.rectangle(frame, b0, b1, (255, 255, 255), -1)
    raw = tmp_path / "square.rgb"
    raw.write_bytes(frame.tobytes())
    video = tmp_path / "square.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-stream_loop", "-1", "-f", "rawvideo",
         "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", str(raw),
         "-frames:v", str(fps * seconds), "-vf", "setsar=8/9",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", str(video)],
        check=True, capture_output=True,
    )

    # The source is held as a slide: the chain is the composed slide layout,
    # which is where the squeezed geometry lived.
    from engine.reframe_scenes import analyse_scene, SceneClass

    segments, _slides, _sw, _sh = analyse_scene(video, 0.0, 3.0)
    assert segments and all(s.cls is SceneClass.SLIDE for s in segments), (
        "the synthetic square must be held as a slide for this test to "
        f"exercise the slide layout, got {[(s.cls, s.slide) for s in segments]}"
    )

    filt = vertical_reframe_filter(video, start=0.0, end=3.0)
    out = tmp_path / "squeezed.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-t", "0.6", "-vf", filt,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )

    cap = cv2.VideoCapture(str(out))
    ok, delivered = cap.read()
    cap.release()
    assert ok, "the render produced no frame to measure"
    gray = cv2.cvtColor(delivered, cv2.COLOR_BGR2GRAY)
    ys, xs = np.where(gray > 127)
    delivered_w = xs.max() - xs.min() + 1
    delivered_h = ys.max() - ys.min() + 1

    pw, ph, sar_w, sar_h = _probe_geometry(out)
    assert (pw, ph, sar_w, sar_h) == (456, 720, 8, 9), (
        f"the anamorphic deliverable is {pw}x{ph} SAR {sar_w}:{sar_h}, "
        "expected 308x486 SAR 8:9"
    )
    squeeze = (delivered_w / delivered_h) / (blob_w / blob_h)
    assert abs(squeeze - 1.0) < 0.02, (
        f"a {blob_w}x{blob_h} square came out {delivered_w}x{delivered_h} px, "
        f"a squeeze of {squeeze:.4f} (1.0000 is undistorted)"
    )



def _rendered_dar(video: Path, filt: str, out: Path,
                  extra: list[str] | None = None) -> tuple[float, int, int]:
    """Run one filter and report the output's display aspect and pixel size."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), *(extra if extra is not None else ["-t", "1"]),
         "-vf", filt,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    w, h, sar_w, sar_h = _probe_geometry(out)
    return w * sar_w / (h * sar_h), w, h


def test_an_anamorphic_source_renders_to_a_genuinely_16_9_frame(tmp_path):
    """The defect this pins: the landscape path used to be a plain downscale,
    so this source — 720x486 SAR 8:9, which is 4:3 — shipped as a '16:9'
    deliverable at DAR 320:243, and nothing in the pipeline checked."""
    from engine.reframe import landscape_filter

    video = _anamorphic_video(tmp_path / "ntsc.mp4")
    out = tmp_path / "landscape.mp4"
    dar, w, h = _rendered_dar(video, landscape_filter(video), out)
    assert abs(dar - 16 / 9) < 0.005, (
        f"the landscape output must display at 16:9, got {w}x{h} -> DAR {dar:.4f}"
    )


def test_the_landscape_frames_aspect_residue_stays_under_qas_tolerance():
    """The landscape frame cannot be exactly 16:9 in pixel terms: 16:9 on an
    even grid needs a height divisible by 9, and most sources are not, so the
    even rounding leaves a residue. This pins the measured bound so a change
    that widened it past ``engine.qa``'s 1% display-aspect tolerance fails here
    rather than shipping a deliverable the pipeline's own QA then rejects.

    Computed rather than rendered: the residue is a property of the rounding
    alone, so every even source height is checked, not the few a render would
    sample. The bound is 0.338% (worst case: a 148-line source ships 264x148,
    a DAR of 1.7838), and tightening it to exactness is not free -- a 480-line
    source would have to give up 12 lines to lose its 0.078% residue, so the
    residue is bounded, not eliminated.
    """
    from engine.reframe import _even

    worst = 0.0
    where = None
    for h in range(144, 2161, 2):  # 144p is the lowest real delivery height
        frame_w = _even(min(h * 16 / 9, 1920))
        frame_h = _even(frame_w * 9 / 16)
        residue = abs(frame_w / frame_h - 16 / 9) / (16 / 9)
        if residue > worst:
            worst, where = residue, (frame_w, frame_h)

    assert worst < 0.01, (
        f"the worst landscape aspect residue over real source heights is "
        f"{worst:.4%} at {where[0]}x{where[1]}, past QA's 1% tolerance"
    )
    # The bound the docstring quotes, stated separately: if the rounding ever
    # moves far enough to make the docstring's 0.338% a lie, this fires first.
    assert worst < 0.0034, (
        f"the docstring promises a worst residue of 0.338%; measured {worst:.4%} "
        f"at {where[0]}x{where[1]} -- update both together"
    )


def test_a_4k_source_renders_to_exactly_1080x1920(tmp_path):
    """The defect this file exists for, measured on the deliverable: a 3840x2160
    source through the vertical reframe used to ship **1080x1918**. The crop at
    that height is 1215 px, which the even constraint rounds to 1216, and
    ``scale=min(1080,iw):-2`` preserved that 0.08% instead of correcting it —
    amplified by the scale to a 0.1% deficit in a frame the package advertises
    as 9:16. QA's display-aspect tolerance is 1%, so nothing rang.

    A real 4K burn takes minutes on four cores, so this builds the smallest
    source that carries the property: a 2160-high frame whose crop hits the
    same 1216. The arithmetic does not care about the width.
    """
    from engine.reframe import vertical_reframe_filter

    video = _anamorphic_video(tmp_path / "uhd.mp4", sar="1:1",
                              size="1440x2160", duration="1")
    filt = vertical_reframe_filter(video, start=0.0, end=0.9)
    assert filt.startswith("crop=1216:2160:x='"), filt
    assert "scale=1080:1920" in filt, (
        f"the scale must land the 1216-wide crop on an exact 9:16 frame; "
        f"got {filt}"
    )

    out = tmp_path / "vertical.mp4"
    dar, w, h = _rendered_dar(video, filt, out)
    assert (w, h) == (1080, 1920), (
        f"a 2160-high source must deliver 1080x1920, got {w}x{h} — the "
        f"1080x1918 defect is back"
    )
    assert abs(dar - 9 / 16) < 0.001


def test_a_low_resolution_landscape_source_is_delivered_at_720_lines(tmp_path):
    """A 426x240 source must not ship as a 240p deliverable: the caption burn
    needs a real delivery canvas even when the source itself is low resolution."""
    from engine.reframe import landscape_filter

    video = _anamorphic_video(tmp_path / "low.mp4", sar="1/1", size="426x240")
    out = tmp_path / "landscape_low.mp4"
    dar, w, h = _rendered_dar(video, landscape_filter(video), out)
    assert (w, h) == (1280, 720), f"low-res landscape must deliver 720 lines, got {w}x{h}"
    assert abs(dar - 16 / 9) < 0.005


def test_a_16_9_source_is_left_alone(tmp_path):
    """The common case must be a no-op: an already-16:9 square-pixel source is
    neither padded nor needlessly rescaled."""
    from engine.reframe import landscape_filter

    video = _anamorphic_video(tmp_path / "wide.mp4", sar="1/1", size="1280x720")
    out = tmp_path / "landscape.mp4"
    dar, w, h = _rendered_dar(video, landscape_filter(video), out)
    assert (w, h) == (1280, 720), f"an exact 16:9 source should be untouched, got {w}x{h}"
    assert abs(dar - 16 / 9) < 0.005


def test_a_source_wider_than_16_9_is_letterboxed_not_cropped(tmp_path):
    """The padding is symmetric: a 2.35:1 source keeps its full width and is
    letterboxed, because cropping is an editorial decision the pipeline does
    not get to make unsupervised. The frame is 16:9 but the picture inside it
    must still be 2.35:1 — measured from the rendered pixels, not the filter
    string."""
    import cv2
    import numpy as np

    from engine.reframe import landscape_filter

    video = _anamorphic_video(tmp_path / "scope.mp4", sar="1/1", size="1920x816")
    out = tmp_path / "landscape.mp4"
    dar, w, h = _rendered_dar(video, landscape_filter(video), out)
    assert abs(dar - 16 / 9) < 0.005

    cap = cv2.VideoCapture(str(out))
    ok, frame = cap.read()
    cap.release()
    assert ok, "could not read a frame back"
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    rows = np.where(grey.max(axis=1) > 16)[0]
    top, bottom = rows.min(), rows.max()
    picture = (bottom - top + 1) / w
    assert abs(picture - 816 / 1920) < 0.02, (
        f"the 2.35:1 picture was rescaled or cropped to {picture:.3f}, "
        f"expected {816 / 1920:.3f}"
    )
    assert top > 1, "there must be a bar above the picture, not a full frame"


def test_a_square_pixel_source_is_unchanged_by_the_aspect_correction(tmp_path):
    """The fix has to be a no-op for the common case: a square-pixel source
    must still render to 9:16 with its pixels undistorted.

    Pinned on invariants rather than on one crop size: this fixture is a static
    structured pattern, so the scene analysis holds it as a slide and cuts the
    exact-aspect window around the detected region instead of taking the full
    frame. Which window that is is the slide layout's business and moves with
    the detector; the aspect correction's business is that whatever window is
    cut has the delivered frame's pixel aspect, so the scale is a pure resize.
    """
    from engine.reframe import vertical_reframe_filter

    video = _anamorphic_video(tmp_path / "square_px.mp4", sar="1:1",
                              size="640x480")
    filt = vertical_reframe_filter(video, start=0.0, end=1.5)
    assert filt.startswith("crop="), filt
    crop = filt.split(",")[0][len("crop="):]
    cw, ch = (int(p) for p in crop.split(":")[:2])
    scale = filt.split(",scale=")[1].split(":")[0:2]
    sw, sh = int(scale[0]), int(scale[1].split(":")[-1])
    # 480 * 9/16 = 270, and 270 * 16/9 = 480 exactly, so the delivered frame is
    # square-pixel and the SAR the graph declares has to be 1/1 to match.
    assert (sw, sh) == (270, 480), f"the frame is 270x480 by construction; got {sw}x{sh}"
    assert filt.endswith("setsar=1/1"), filt
    # The no-squeeze condition: the window's pixel aspect is the frame's.
    assert abs(cw / ch - sw / sh) < 0.01, (
        f"crop {cw}x{ch} scaled onto {sw}x{sh} squeezes a square-pixel source "
        f"by {cw / ch / (sw / sh):.4f}")

    out = tmp_path / "vertical.mp4"
    dar, w, h = _rendered_dar(video, filt, out)
    assert (w, h) == (406, 722), f"the low-resolution crop must be raised to the 720-line delivery floor; got {w}x{h}"
    assert abs(dar - 9 / 16) < 0.005


def test_the_vertical_scale_lands_on_an_exact_9_16_display_ratio():
    """The unit form of the defect: the scale's target height is derived in
    *display* space, so the delivered frame measures 9:16 whatever the source's
    sample aspect — square pixels, NTSC's wide ones, and the 4K height that
    exposed the bug, where the crop is forced a fraction wide by the even
    constraint and the scale has to absorb that residue.
    """
    from engine.reframe import vertical_scale_filter

    cases = [
        # (crop_w, sar_w, sar_h, expected frame)
        (270, 1, 1, (406, 722)),    # 480 * 9/16 is exact
        (274, 1, 1, (406, 722)),    # 274 * 16/9 = 487.1 -> 488
        (308, 8, 9, (456, 720)),    # NTSC: the SAR cancels the crop's own width
        (406, 1, 1, (406, 722)),    # 720-high source: 406 * 16/9 = 721.8 -> 722
        (1216, 1, 1, (1080, 1920)), # 4K: the crop rounds to 1216, the frame is exact
        (720, 1, 1, (720, 1280)),   # portrait passes through untouched
    ]
    for crop_w, sar_w, sar_h, expect in cases:
        filt = vertical_scale_filter(crop_w, sar_w, sar_h)
        got = tuple(int(p) for p in filt.removeprefix("scale=").split(":")[:2])
        assert got == expect, (
            f"crop {crop_w} SAR {sar_w}:{sar_h} -> {filt}, expected {expect}"
        )
        dar = got[0] * sar_w / (got[1] * sar_h)
        assert abs(dar - 9 / 16) < 0.002, (
            f"crop {crop_w} SAR {sar_w}:{sar_h} -> {got} displays at {dar:.4f}, "
            f"not 9:16 (0.5625); the residue must stay under 0.4%"
        )


def test_the_vertical_scale_raises_low_resolution_delivery_to_720_lines():
    """Low-resolution inputs are deliberately upscaled for delivery so the
    burned-in ASS captions have a useful physical canvas on phones."""
    from engine.reframe import vertical_scale_filter

    assert vertical_scale_filter(270, 1, 1).startswith("scale=406:")
    assert vertical_scale_filter(406, 1, 1).startswith("scale=406:")
    assert vertical_scale_filter(1216, 1, 1).startswith("scale=1080:")


    assert vertical_scale_filter(406, 1, 1).startswith("scale=406:")
    assert vertical_scale_filter(1216, 1, 1).startswith("scale=1080:")


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


