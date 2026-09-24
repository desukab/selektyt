"""Layer 1 of the reframe: perception. Turn frames into face detections.

The chain is YuNet -> Haar -> none, and the reason it exists in this order is a
measured platform break, not a preference.

``opencv-python-headless>=4.9`` is unpinned in ``pyproject.toml``. OpenCV 5.0
removed ``cv2.CascadeClassifier``, ``cv2.HOGDescriptor`` and ``cv2.legacy``
entirely, and ships ``cv2.data.haarcascades`` containing no XML at all. On any
host where 5.0 resolves — every fresh install today — the Haar path raises
``AttributeError`` at construction and the previous code silently fell through
to the Canny edge-density fallback for *every* frame. The edge-density
heuristic returns the middle of a two-shot, because the middle is where the
edge profile balances, which is exactly the empty gap between two speakers. A
whole class of vertical output was being reframed by a heuristic that
systematically picks the wrong place, and nothing in the pipeline reported it.

``cv2.FaceDetectorYN`` has been compiled in since OpenCV 4.5 and survives 5.0,
so it is the primary on every supported version. It needs an ONNX weight file,
which is vendored (232 KB, MIT-licensed by Shiqi Yu, from ``opencv/opencv_zoo``) so a
local-first tool does not need the network at render time; a user-placed copy in
``~/.selektyt/models`` overrides the vendored one. If the weights are missing or
the symbol is absent, the chain degrades to Haar on the OpenCV 4.x hosts that
still have it, and then to no detections — never to a crash, and never to a
silently-wrong heuristic pretending it detected something.

Reference projects were studied and not copied: Google AutoFlip (crop-region
proposal plus shot-boundary detection from signal variance) is the design this
module's caller follows, but its saliency model is MediaPipe-licensed and its
approach is re-deriveable from the detection stream alone. Ultralytics YOLO was
benchmarked conceptually and rejected as a *dependency*: the library is
AGPL-3.0, which is incompatible with SeleKyT's own licence for a linked
component, and it pulls PyTorch (several hundred MB) for a task a 232 KB
detector covers. PySceneDetect's content-valued shot detection is reproduced
here by a mean-absolute-difference test on the sample grid, which is the part of
it that matters for tracking resets; ``scenedetect`` is not a dependency.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

# Vendored next to this module; a user copy here wins, so an operator can drop
# in a different YuNet release without touching the package.
_MODEL_NAME = "face_detection_yunet.onnx"
_YUNET_INPUT = (320, 320)
_MIN_FACE_PX = 48  # the previous Haar pass's floor; kept so small/false faces
# at the far edge of a wide shot do not win the subject election.

# Below this score a YuNet box is not a face. The model is well calibrated on
# talking-head footage (0.93-0.95 on real speakers), so 0.5 leaves a wide margin
# while still rejecting partial-profile false positives at 0.2-0.3.
_YUNET_SCORE = 0.5
_YUNET_NMS = 0.3


def _candidate_paths() -> list[Path]:
    here = Path(__file__).resolve().parent / "models" / _MODEL_NAME
    user = Path.home() / ".selektyt" / "models" / _MODEL_NAME
    # The user copy takes precedence: it is the operator's explicit choice.
    return [user, here]


class FaceDetector:
    """One detector instance per process, whichever backend resolved.

    ``available`` is False when no backend could be built, which is a legitimate
    host state (no model file and an OpenCV 5.x build with no Haar either). The
    caller must handle it by reframing without detections rather than by
    guessing.
    """

    def __init__(self, model_path: str | Path | None = None) -> None:
        self.kind: str = "none"
        self._yunet = None
        self._haar = None
        self._last_size: tuple[int, int] | None = None
        path = Path(model_path) if model_path else None
        for cand in ([path] if path else _candidate_paths()):
            if self._try_yunet(cand):
                return
        self._try_haar()

    def _try_yunet(self, path: Path | None) -> bool:
        if path is None or not path.is_file():
            return False
        create = getattr(cv2, "FaceDetectorYN_create", None) or getattr(cv2, "FaceDetectorYN", None)
        if create is None:
            # OpenCV build without the DNN face detector compiled in.
            return False
        try:
            det = create(str(path), "", _YUNET_INPUT,
                         score_threshold=_YUNET_SCORE,
                         nms_threshold=_YUNET_NMS, top_k=5000)
        except Exception:
            return False
        if getattr(det, "empty", lambda: False)():
            return False
        self._yunet = det
        self.kind = "yunet"
        return True

    def _try_haar(self) -> None:
        cls = getattr(cv2, "CascadeClassifier", None)
        data = getattr(cv2, "data", None)
        haar_dir = getattr(data, "haarcascades", None)
        xml = Path(str(haar_dir)) / "haarcascade_frontalface_default.xml" if haar_dir else None
        if cls is None or not xml or not xml.is_file():
            # OpenCV 5.x: the class is gone, and the data directory ships no
            # XML. There is no Haar path on such a host, which is why the YuNet
            # weights are vendored rather than fetched.
            return
        try:
            cascade = cls(str(xml))
        except Exception:
            return
        if getattr(cascade, "empty", lambda: False)():
            return
        self._haar = cascade
        self.kind = "haar"

    @property
    def available(self) -> bool:
        return self._yunet is not None or self._haar is not None

    def detect(self, frame: np.ndarray) -> list[tuple[float, float, float, float, float]]:
        """Faces as ``(x, y, w, h, score)`` in source pixels, best score first.

        Score is comparable *within* a backend only. Haar has no score, so its
        box area stands in — the caller uses score to rank candidates from one
        detector, never to compare across backends, and the Haar branch is a
        legacy host's fallback rather than a co-equal path.
        """
        if self._yunet is not None:
            h, w = frame.shape[:2]
            size = (w, h)
            if self._last_size != size:
                # YuNet caches the input geometry; feeding a frame of a
                # different size without re-declaring it silently returns boxes
                # in the declared coordinate space, i.e. mis-scaled detections.
                self._yunet.setInputSize(size)
                self._last_size = size
            try:
                _ok, faces = self._yunet.detect(frame)
            except Exception:
                return []
            out = []
            if faces is not None:
                for row in faces:
                    x, y, fw, fh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
                    if fw < _MIN_FACE_PX or fh < _MIN_FACE_PX:
                        continue
                    out.append((x, y, fw, fh, float(row[14])))
            out.sort(key=lambda f: f[4], reverse=True)
            return out
        if self._haar is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            try:
                boxes = self._haar.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=5, minSize=(_MIN_FACE_PX, _MIN_FACE_PX))
            except Exception:
                return []
            out = [(float(x), float(y), float(w), float(hh), float(w * hh))
                   for x, y, w, hh in boxes]
            out.sort(key=lambda f: f[4], reverse=True)
            return out
        return []


_SINGLETON: FaceDetector | None = None


def get_detector(model_path: str | Path | None = None) -> FaceDetector:
    """The process-wide detector. Model load is the expensive part and the
    weights never change mid-run, so building one per clip re-parses the same
    ONNX for every vertical output and is the cost the previous per-clip Haar
    construction had too. ``model_path`` is for tests, which must be able to
    point at a known-good file."""
    global _SINGLETON
    if _SINGLETON is None or model_path is not None:
        _SINGLETON = FaceDetector(model_path)
    return _SINGLETON


def frame_difference(a: np.ndarray, b: np.ndarray) -> float:
    """Mean absolute pixel difference of two frames, 0-255.

    Used to find shot boundaries on the sample grid. This is the whole part of
    PySceneDetect that matters here: the tracker needs to know when a cut
    happened so it does not read a subject's position in one shot as motion into
    the next. A raw MAD test on downsampled grayscale is adequate at the
    sampling cadence used (a few frames per second), where a cut is a 30-60
    point jump and motion within a shot is a few points.
    """
    if a is None or b is None or a.shape != b.shape:
        return 255.0
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY) if a.ndim == 3 else a
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY) if b.ndim == 3 else b
    ga = cv2.resize(ga, (160, 90), interpolation=cv2.INTER_AREA)
    gb = cv2.resize(gb, (160, 90), interpolation=cv2.INTER_AREA)
    return float(np.abs(ga.astype(np.int16) - gb.astype(np.int16)).mean())


def find_shot_boundaries(frames: Sequence[np.ndarray | None],
                         threshold: float = 22.0) -> list[int]:
    """Indices ``i`` where ``frames[i]`` starts a new shot.

    The threshold comes from the corpus, not from intuition. Measured on the
    pentagon source at ~4 samples/s over 90 s of output: a locked-off
    talking-head shot reads p50 1.0-2.5 and p90 3-10, topping out at 5.1; a
    whip pan climbs smoothly to 30-35 and comes back down over about 1.5 s; a
    hard cut is a single-frame spike with no ramp. 22 sits above all within-shot
    motion observed and below the pan peak, so it fires on pans and cuts and
    nothing else on this footage.

    That distinction matters for what the tracker does next: a pan is the same
    shot, and the subject comes back, whereas a cut means every previous
    position is void. ``find_shot_boundaries`` cannot tell them apart on
    magnitude alone — a detector-driven or adaptive threshold (PySceneDetect's
    approach) could, and this is the place to add one if a later corpus shows a
    clip where the two need separating. Callers treat every boundary as a reset,
    which is safe for a pan because re-establishing the same subject costs one
    hold and produces no visible crop move.
    """
    out = []
    prev: np.ndarray | None = None
    for i, frame in enumerate(frames):
        if frame is None:
            continue
        if prev is not None and frame_difference(prev, frame) > threshold:
            out.append(i)
        prev = frame
    return out
