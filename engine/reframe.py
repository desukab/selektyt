from __future__ import annotations
import cv2
import numpy as np
from pathlib import Path


def _fallback_center(frame: np.ndarray) -> float:
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


def _load_face_cascade() -> "cv2.CascadeClassifier | None":
    """Haar cascade is optional: some opencv builds (e.g. opencv-python-headless
    on constrained hosts) ship without ``cv2.data``. Absent or unusable cascade
    data must not crash reframing — the edge-density fallback tracks every
    frame instead."""
    try:
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
    except Exception:
        return None
    return None if cascade.empty() else cascade


def track_subject_center(video: Path, start: float, end: float, samples: int = 28) -> list[tuple[float, float]]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []
    centers: list[tuple[float, float]] = []
    cascade = _load_face_cascade()
    for t_abs in np.linspace(start, end, max(2, samples)):
        cap.set(cv2.CAP_PROP_POS_MSEC, float(t_abs * 1000))
        ok, frame = cap.read()
        if not ok:
            continue
        center: float
        if cascade is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
            if len(faces):
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                center = (x + w / 2) / frame.shape[1]
            else:
                center = _fallback_center(frame)
        else:
            center = _fallback_center(frame)
        centers.append((t_abs - start, float(center)))
    cap.release()
    return _smooth_centers(centers)


def crop_x_expression(video: Path, start: float, end: float) -> str:
    cap = cv2.VideoCapture(str(video))
    w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920
    h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080
    cap.release()
    crop_w = h * 9 / 16
    max_x = max(0.0, w - crop_w)
    points = track_subject_center(video, start, end)
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
