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


def track_subject_center(video: Path, start: float, end: float, samples: int = 28) -> list[tuple[float, float]]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []
    centers: list[tuple[float, float]] = []
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    for t_abs in np.linspace(start, end, max(2, samples)):
        cap.set(cv2.CAP_PROP_POS_MSEC, float(t_abs * 1000))
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
        if len(faces):
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            center = (x + w / 2) / frame.shape[1]
        else:
            center = _fallback_center(frame)
        centers.append((t_abs - start, float(center)))
    cap.release()
    return centers


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
