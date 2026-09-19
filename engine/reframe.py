from __future__ import annotations
import cv2
import numpy as np
from pathlib import Path

from providers.ffmpeg import probe_sample_aspect


def _fallback_center(frame: np.ndarray, gray: np.ndarray | None = None) -> float:
    # ``gray`` is reused when the caller already converted for the Haar pass;
    # recomputing it here would double the per-sample work on the no-cascade
    # path, which is the path every constrained host takes.
    if gray is None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    profile = edges.mean(axis=0).astype(np.float32)
    kernel = np.ones(81, np.float32) / 81.0
    profile = np.convolve(profile, kernel, mode="same")
    total = float(profile.sum())
    if total <= 1e-6:
        return 0.5
    xs = np.arange(len(profile), dtype=np.float32)
    return float(np.clip((xs * profile).sum() / total / len(profile), 0.05, 0.95))


def _smooth_centers(
    centers: list[tuple[float, float]], window: int = 5
) -> list[tuple[float, float]]:
    """Symmetric moving average over the tracked center column.

    The crop expression interpolates between sampled centers, so a single
    misdetection (the largest face switching subjects, an edge-dense
    background region) becomes a visible crop jump. Smoothing the offline
    sample run keeps the subject lead while damping those spikes. Timing is
    left untouched; only the center column is averaged.
    """
    if len(centers) < 3 or window < 2:
        return list(centers)
    half = window // 2
    out: list[tuple[float, float]] = []
    for i, (t, _c) in enumerate(centers):
        lo = max(0, i - half)
        hi = min(len(centers), i + half + 1)
        mean = float(np.mean([c for _, c in centers[lo:hi]]))
        out.append((t, mean))
    return out


_CACHED_CASCADE: "cv2.CascadeClassifier | None | bool" = False


def _load_face_cascade() -> "cv2.CascadeClassifier | None":
    """Haar cascade is optional: some opencv builds (e.g. opencv-python-headless
    on constrained hosts) ship without ``cv2.data``. Absent or unusable cascade
    data must not crash reframing — the edge-density fallback tracks every
    frame instead.

    Loaded once per process: the XML is the same for every clip, and probing it
    again per clip (or per render of every 9:16 output) is pure duplicated IO.
    """
    global _CACHED_CASCADE
    if _CACHED_CASCADE is not False:
        return _CACHED_CASCADE  # type: ignore[return-value]
    try:
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
    except Exception:
        cascade = None
    else:
        cascade = None if cascade.empty() else cascade
    _CACHED_CASCADE = cascade
    return cascade


def _probe_and_track(
    video: Path, start: float, end: float, samples: int = 28
) -> tuple[int, int, list[tuple[float, float]]]:
    """Open the source once, read its dimensions and track the subject.

    ``crop_x_expression`` needs the frame size and the tracked centers; doing
    those in two separate ``VideoCapture`` opens decoded the seek-heavy
    metadata pass twice per vertical clip.
    """
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return 0, 0, []
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    centers: list[tuple[float, float]] = []
    cascade = _load_face_cascade()
    for t_abs in np.linspace(start, end, max(2, samples)):
        cap.set(cv2.CAP_PROP_POS_MSEC, float(t_abs * 1000))
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        center: float
        if cascade is not None:
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
            if len(faces):
                x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                center = (x + fw / 2) / frame.shape[1]
            else:
                center = _fallback_center(frame, gray)
        else:
            center = _fallback_center(frame, gray)
        centers.append((t_abs - start, float(center)))
    cap.release()
    return w, h, _smooth_centers(centers)


def track_subject_center(video: Path, start: float, end: float, samples: int = 28) -> list[tuple[float, float]]:
    _, _, centers = _probe_and_track(video, start, end, samples)
    return centers


def vertical_crop_width(height: int, sar_w: int, sar_h: int) -> int:
    """Pixel width of a 9:16 crop of ``height`` pixels at this sample aspect.

    Display width is ``w * sar_w`` and display height ``h * sar_h`` in the same
    units, so the pixel width that *displays* as 9:16 is
    ``h * 9/16 * sar_h / sar_w`` -- wider in pixels than the square-pixel
    answer when the pixels themselves are wide. Rounding to even keeps the
    scaler and libx264 happy; the ratio is within a frame of exact either way,
    which is as close as an integer pixel grid gets.
    """
    width = height * 9 * sar_h / (16 * sar_w)
    return max(2, int(width / 2 + 0.5) * 2)


def _x_expression(max_x: float, crop_w: float,
                  points: list[tuple[float, float]]) -> str:
    """Centre the tracked subject in a ``crop_w``-wide window, keyed to time.

    With no track the window sits at the middle of the available range. The
    expression interpolates between sampled centres, so it is built from the
    last sample backwards: each earlier sample becomes an ``if(lt(t,...))``
    that hands off to the segment after it.
    """
    if not points:
        return str(int(max_x / 2))
    expr = str(max(0.0, min(max_x, max_x * points[-1][1] - crop_w / 2)))
    for i in range(len(points) - 2, -1, -1):
        t0, c0 = points[i]
        t1, c1 = points[i + 1]
        x0 = max(0.0, min(max_x, max_x * c0 - crop_w / 2))
        x1 = max(0.0, min(max_x, max_x * c1 - crop_w / 2))
        expr = f"if(lt(t,{t1:.3f}),{x0:.1f}+({x1:.1f}-{x0:.1f})*(t-{t0:.3f})/{max(0.001,t1-t0):.3f},{expr})"
    return expr


def _probe_dims(video: Path) -> tuple[int, int]:
    """Pixel dimensions, or the 1080p default when ffprobe says nothing."""
    w, h, _points = _probe_and_track(video, 0.0, 0.0)
    if not w or not h:
        return 1920, 1080
    return w, h


def _even(n: float) -> int:
    return max(2, int(n / 2 + 0.5) * 2)


def landscape_filter(video: Path, target: tuple[int, int] = (16, 9),
                     max_width: int = 1920) -> str:
    """The ffmpeg filter that fits a whole source into an exact ``target``
    display aspect, padding rather than cropping.

    Why pad: a 16:9 deliverable made from a 4:3 source has to lose content or
    add bars, and cropping is an editorial decision the pipeline is not
    entitled to make unsupervised — on a talking-head source it takes heads
    off. Padding keeps every frame the source recorded and still delivers a
    frame that measures exactly 16:9, which is the contract the render
    promised. A wider-than-target source is letterboxed by the same rule.

    The content is scaled to fit and never enlarged: a 720x486 NTSC source
    becomes a 640x486 square-pixel picture inside an 864x486 frame, not an
    upscaled 1080p one. Square pixels are forced (``setsar=1``) so the aspect
    does not depend on the player honouring container metadata.
    """
    w, h = _probe_dims(video)
    sar_w, sar_h = probe_sample_aspect(video)
    tw, th = target

    # The largest frame of this aspect whose height does not exceed the
    # source's, capped at the delivery width. Height is derived from the
    # rounded width so the two are exactly proportional.
    frame_w = _even(min(h * tw / th, max_width))
    frame_h = _even(frame_w * th / tw)

    # Square-pixel extent of the source picture at its own pixel height.
    content_w = w * sar_w / sar_h
    scale = min(frame_w / content_w, frame_h / h, 1.0)
    cw, ch = _even(content_w * scale), _even(h * scale)

    return (
        f"scale={cw}:{ch}:flags=lanczos,"
        f"pad={frame_w}:{frame_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    )


def vertical_crop_filter(video: Path, start: float, end: float) -> str:
    """The ffmpeg ``crop`` filter for the 9:16 reframe of one interval.

    Returns the whole filter, width and tracked x together, because the two
    must agree: the width has to account for the source's sample aspect ratio
    while the subject is centred against that same width, and recomposing it
    at the call site leaves two places to get one geometry wrong. The width is
    clamped to the source width so a source already narrower than 9:16 is
    passed through at its own ratio instead of failing the filter.
    """
    w, h, points = _probe_and_track(video, start, end)
    if not w or not h:
        w, h = 1920, 1080
    sar_w, sar_h = probe_sample_aspect(video)
    crop_w = min(vertical_crop_width(h, sar_w, sar_h), w)
    max_x = max(0.0, w - crop_w)
    return f"crop={crop_w}:{h}:x='{_x_expression(max_x, crop_w, points)}':y=0"


def crop_x_expression(video: Path, start: float, end: float) -> str:
    """The ``x`` component alone, for callers that compose their own filter."""
    w, h, points = _probe_and_track(video, start, end)
    if not w or not h:
        w, h = 1920, 1080
    sar_w, sar_h = probe_sample_aspect(video)
    crop_w = min(vertical_crop_width(h, sar_w, sar_h), w)
    max_x = max(0.0, w - crop_w)
    return _x_expression(max_x, crop_w, points)
