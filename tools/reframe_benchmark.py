"""Benchmark the vertical reframe against the frames it actually ships.

The reframe's contract is behavioural: the 9:16 crop has to keep the active
subject in frame, and it has to move like an intentional camera operator. A
unit test that asserts ``track_subject_center`` returns coordinates inside
[0, 1] proves nothing about either — the coordinates can be plausible and the
rendered Short still lose the speaker.

This tool is the missing measurement. It decodes the source over a cut
interval, asks an *independent* detector where the people actually are, and
scores the crop trajectory the reframe chose against that ground truth. The
detector used for scoring is deliberately not the one the reframe uses to
choose: an oracle that shares the pipeline's blind spots cannot see the
pipeline's failures. YuNet (``cv2.FaceDetectorYN``) is used when its weights
are available and the Haar cascade otherwise — YuNet is strictly stronger than
Haar, so a Haar-era failure it misses is a measurement limit, not a pass.

Two evaluations, because they fail differently:

* **source-space** evaluates the crop window against faces detected in the
  *source* frame. Fast (no encode), and it isolates the reframe from the
  render. A failure here means the reframe chose badly.
* **rendered-space** renders the vertical output with a caller-supplied filter
  and scores faces inside the delivered 9:16 frame. Slower, and it is the only
  evaluation that catches a reframe that chose well and rendered wrong — an
  escaped filtergraph expression or an off-by-one in the expression's time
  keying looks perfect in source space and loses the speaker in the product.

What this cannot measure: which of two people is *supposed* to be the active
speaker. On a two-shot it reports that a face is in frame, not that it is the
right face. That judgement is made by eye on the frames this tool can dump
(``--dump-frames``), and any benchmark number it prints is read alongside that
inspection, never instead of it.

That caveat has one concrete consequence in what is counted. ``primary_*``
fields follow the strongest detection, which on a two-shot is often the
speaker the crop *correctly* chose not to follow, so a good editorial decision
scores as a miss. The aggregates that decide pass/fail are therefore the
``framed_*`` ones, built on the face nearest the window's centre -- the crop's
answer to "who is this about" -- and on whether *anybody* at all survives the
window. A frame where the oracle sees faces and the window contains none is a
true loss regardless of which speaker it was.

Usage:
    .venv/bin/python tools/reframe_benchmark.py source.mp4 583.7 611.64 \
        --trajectory old --json out.json

    .venv/bin/python tools/reframe_benchmark.py source.mp4 583.7 611.64 \
        --render --filter "crop=...:x='...':y=0" --dump-frames frames/
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── oracle detector ────────────────────────────────────────────────────────
#
# YuNet weights are looked up, never fetched here: the benchmark is a
# measurement instrument, and an instrument that downloads a model on first
# use measures a different thing on a network-isolated host than on this one.
# ``engine.reframe_perception`` owns the lookup order and the provenance; this
# tool accepts whatever it can find and says clearly when it found nothing.

_YUNET_CANDIDATES = (
    # Vendored with the pipeline (the production path).
    Path(__file__).resolve().parent.parent / "engine" / "models" / "face_detection_yunet.onnx",
    # A copy the operator left somewhere obvious.
    Path.home() / ".selektyt" / "models" / "face_detection_yunet.onnx",
)


def _yunet_path(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() else None
    for cand in _YUNET_CANDIDATES:
        if cand.is_file():
            return cand
    return None


class _Oracle:
    """Face boxes in source pixel coordinates, with a confidence each.

    ``detector`` is ``None`` only when no detector is available at all; the
    benchmark reports ``oracle: none`` and every face metric becomes
    ``not_measured`` rather than zero, because zero faces in frame is a
    finding and "could not look" is not.
    """

    def __init__(self, yunet: Path | None, fallback_haar: bool = True):
        import cv2

        self.cv2 = cv2
        self.detector = None
        self.kind = "none"
        self._haar = None
        if yunet is not None:
            try:
                self.detector = cv2.FaceDetectorYN.create(
                    str(yunet), "", (320, 320), score_threshold=0.5,
                    nms_threshold=0.3, top_k=5000)
                self.kind = "yunet"
                return
            except cv2.error:
                self.detector = None
        if fallback_haar:
            try:
                cascade = cv2.CascadeClassifier(
                    cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            except Exception:
                cascade = None
            if cascade is not None and not cascade.empty():
                self._haar = cascade
                self.kind = "haar"

    @property
    def available(self) -> bool:
        return self.detector is not None or self._haar is not None

    def detect(self, frame: np.ndarray) -> list[tuple[float, float, float, float, float]]:
        """(x, y, w, h, score) per face, sorted best-score-first."""
        if self.detector is not None:
            h, w = frame.shape[:2]
            try:
                self.detector.setInputSize((int(w), int(h)))
                _ok, faces = self.detector.detect(frame)
            except self.cv2.error:
                return []
            if faces is None or len(faces) == 0:
                return []
            out = [(float(f[0]), float(f[1]), float(f[2]), float(f[3]),
                    float(f[14])) for f in faces]
            return sorted(out, key=lambda f: -f[4])
        if self._haar is not None:
            gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
            boxes = self._haar.detectMultiScale(gray, scaleFactor=1.1,
                                                minNeighbors=5,
                                                minSize=(48, 48))
            # Haar gives no confidence; area stands in so callers that pick the
            # "primary" face behave the same way for both backends.
            return sorted([(float(b[0]), float(b[1]), float(b[2]), float(b[3]),
                            float(b[2] * b[3])) for b in boxes],
                          key=lambda f: -f[4])
        return []


# ── decoding ───────────────────────────────────────────────────────────────


def _sample_times(start: float, end: float, fps: float,
                  max_samples: int) -> list[float]:
    """Wall-clock sample times spread across the interval.

    Capped so a long cut cannot turn a benchmark into a decode of the whole
    source; the crop moves slowly relative to the frame rate, so a sample every
    few frames is far finer than the trajectory's own resolution.
    """
    span = max(0.0, end - start)
    step = max(1.0 / fps, span / max_samples)
    n = int(span / step) + 1
    return [start + i * step for i in range(n)]


def _decode_frames(video: Path, times: list[float]) -> list[tuple[float, np.ndarray]]:
    """Decode one frame per requested time, in order, with a single capture.

    Seeks are approximate at the container level, so the timestamp actually
    read is taken from the capture and reported alongside rather than assumed
    — a seek that lands a frame early shifts every measurement after it, and
    the caller should be able to see that rather than trust an idealised grid.
    """
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []
    out: list[tuple[float, np.ndarray]] = []
    for want in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, float(want * 1000.0))
        ok, frame = cap.read()
        if not ok:
            # A seek past the last keyframe-adjacent decode can return EOF on
            # the final sample; skipping it keeps the rest of the run usable.
            continue
        got = float(cap.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
        out.append((min(got, want + 1.0), frame))
    cap.release()
    return out


def _frame_diff(a: np.ndarray, b: np.ndarray) -> float:
    """Mean absolute pixel difference between two frames, 0-255.

    Used for shot boundaries only. A whole-frame threshold is a crude
    instrument — a fast pan and a hard cut both move every pixel — so the
    boundary list is a *diagnostic*, never a pass/fail: it exists so a jump in
    the crop trajectory can be read as "there was a cut here" or "the reframe
    twitched" depending on whether a boundary coincides.
    """
    ga = np.asarray(a, dtype=np.float32)
    gb = np.asarray(b, dtype=np.float32)
    if ga.shape != gb.shape:
        ga = ga[: min(ga.shape[0], gb.shape[0]), : min(ga.shape[1], gb.shape[1])]
        gb = gb[: ga.shape[0], : ga.shape[1]]
    return float(np.mean(np.abs(ga - gb)))


# ── trajectory ─────────────────────────────────────────────────────────────


def trajectory_from_centers(centers: list[tuple[float, float]], t: float) -> float:
    """Centre fraction at time ``t``, linearly interpolated like the filter does.

    ``engine.reframe._x_expression`` keys the crop to ``t`` with the same
    piecewise-linear interpolation between sampled centres, so evaluating the
    trajectory with different math would measure a crop the pipeline does not
    produce. Times before the first sample clamp to it, as ffmpeg's ``t``
    starts at the interval's own zero.
    """
    if not centers:
        return 0.5
    if t <= centers[0][0]:
        return centers[0][1]
    if t >= centers[-1][0]:
        return centers[-1][1]
    lo = 0
    for i in range(1, len(centers)):
        if centers[i][0] >= t:
            lo = i - 1
            break
    t0, c0 = centers[lo]
    t1, c1 = centers[lo + 1]
    span = max(1e-6, t1 - t0)
    return float(np.clip(c0 + (c1 - c0) * (t - t0) / span, 0.0, 1.0))


def _crop_window(center_frac: float, w: int, crop_w: int) -> tuple[float, float]:
    max_x = max(0.0, float(w - crop_w))
    x0 = float(np.clip(w * center_frac - crop_w / 2.0, 0.0, max_x))
    return x0, x0 + float(crop_w)


# ── metrics ────────────────────────────────────────────────────────────────

# A face closer to the crop edge than this fraction of its own width is
# "in frame but badly framed" — the ear or cheek is at the boundary and a
# quarter-turn of the head takes it out. Measured framing failure, not a
# visibility failure, so it is reported separately from a true miss.
MARGIN_FRACTION_OK = 0.35

# A crop step larger than this fraction of the crop width in one sample
# interval is visible as a camera jump unless it coincides with a cut.
JUMP_FRACTION = 0.25


def evaluate_source_space(
    video: Path,
    start: float,
    end: float,
    centers: list[tuple[float, float]],
    crop_w: int,
    oracle: _Oracle,
    max_samples: int = 180,
) -> dict:
    """Score the crop trajectory against faces found in the source frames."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return {"verdict": "could_not_open", "detail": "VideoCapture refused the source"}
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    cap.release()

    times = _sample_times(start, end, fps, max_samples)
    frames = _decode_frames(video, times)

    rows: list[dict] = []
    prev_gray: np.ndarray | None = None
    boundaries: list[float] = []
    prev_x: float | None = None
    travel = 0.0
    big_jumps: list[dict] = []
    second_diffs: list[float] = []
    xs: list[float] = []

    for got, frame in frames:
        t_rel = max(0.0, got - start)
        faces = oracle.detect(frame)
        center = trajectory_from_centers(centers, t_rel)
        x0, x1 = _crop_window(center, w, crop_w)
        xs.append(x0)

        primary = faces[0] if faces else None
        # The face the crop is actually about: nearest centre to the window's
        # own centre. Distinct from ``primary`` on a two-shot, where the
        # strongest detection can be the speaker the crop deliberately did not
        # follow. Scoring ``primary`` there scores a correct decision as a miss.
        cx_win = (x0 + x1) / 2.0
        framed = (min(faces, key=lambda f: abs((f[0] + f[2] / 2.0) - cx_win))
                  if faces else None)
        row = {
            "t": round(t_rel, 3),
            "n_faces": len(faces),
            "crop_x0": round(x0, 1),
            "crop_x1": round(x1, 1),
            "center_frac": round(center, 4),
        }
        if primary is not None:
            fx, fy, fw, fh, fscore = primary
            fcx = fx + fw / 2.0
            inside = x0 <= fcx <= x1
            margin = min(fcx - x0, x1 - fcx)
            # Fraction of faces of any kind that the window keeps: on a
            # two-shot, losing the second face is a framing choice; losing
            # *every* face is the failure this tool exists to catch.
            kept = sum(1 for f in faces if x0 <= (f[0] + f[2] / 2.0) <= x1)
            row.update({
                "face_cx": round(fcx, 1),
                "face_w": round(fw, 1),
                "face_score": round(fscore, 3),
                "primary_inside": bool(inside),
                "primary_margin_frac": round(margin / max(1.0, fw), 3),
                "faces_kept": kept,
                "faces_total": len(faces),
                "clipped": bool(fx < x0 or fx + fw > x1),
            })
        else:
            row.update({
                "face_cx": None, "face_w": None, "face_score": None,
                "primary_inside": None, "primary_margin_frac": None,
                "faces_kept": 0, "faces_total": 0, "clipped": None,
            })
        if framed is not None:
            gxf, gyf, gwf, ghf, gsf = framed
            gcx = gxf + gwf / 2.0
            gmargin = min(gcx - x0, x1 - gcx)
            row.update({
                "framed_cx": round(gcx, 1),
                "framed_w": round(gwf, 1),
                "framed_inside": bool(x0 <= gcx <= x1),
                "framed_margin_frac": round(gmargin / max(1.0, gwf), 3),
                "framed_clipped": bool(gxf < x0 or gxf + gwf > x1),
            })
        else:
            row.update({
                "framed_cx": None, "framed_w": None, "framed_inside": None,
                "framed_margin_frac": None, "framed_clipped": None,
            })
        rows.append(row)

        if prev_gray is not None:
            d = _frame_diff(prev_gray, frame)
            row["frame_diff"] = round(d, 2)
            # 18 is an empirical whole-frame MAD for a hard cut between
            # differently-lit shots; flagged as a diagnostic only.
            if d > 18.0:
                boundaries.append(t_rel)
        prev_gray = frame

        if prev_x is not None:
            step = abs(x0 - prev_x)
            travel += step
            if step > JUMP_FRACTION * crop_w:
                big_jumps.append({"t": round(t_rel, 3), "step_px": round(step, 1),
                                  "at_shot_boundary": bool(
                                      any(abs(t_rel - b) < 0.5 for b in boundaries))})
        prev_x = x0

    for i in range(1, len(xs) - 1):
        second_diffs.append(abs(xs[i + 1] - 2.0 * xs[i] + xs[i - 1]))

    seen = [r for r in rows if r["primary_inside"] is not None]
    inside = [r for r in seen if r["primary_inside"]]
    well_framed = [r for r in inside if r["primary_margin_frac"] >= MARGIN_FRACTION_OK]
    unexplained = [j for j in big_jumps if not j["at_shot_boundary"]]
    face_frames = len(seen)

    # The contract: on frames where people are visible at all, is somebody
    # inside the window, and is that somebody comfortably inside rather than
    # bisected by the edge. ``all_faces_lost`` is the unambiguous failure --
    # no metric definition can argue a face out of an empty window.
    framed_seen = [r for r in rows if r["n_faces"] > 0]
    framed_kept = [r for r in framed_seen if r["framed_inside"]]
    framed_well = [r for r in framed_kept
                   if (r["framed_margin_frac"] or 0.0) >= MARGIN_FRACTION_OK]
    framed_clipped = [r for r in framed_kept if r["framed_clipped"]]
    lost = [round(r["t"], 3) for r in framed_seen if not r["framed_inside"]]
    nf = len(framed_seen)

    return {
        "verdict": "measured",
        "oracle": oracle.kind,
        "source_w": w, "source_h": h, "fps": round(fps, 3),
        "crop_w": crop_w,
        "n_samples": len(rows),
        "shot_boundaries": [round(b, 3) for b in boundaries],
        "n_shot_boundaries": len(boundaries),
        # Every metric below is a fraction of frames *where a face was
        # detectable*, not of all frames. A scene the oracle cannot read is
        # excluded from the denominator rather than counted as a success.
        "face_frames": face_frames,
        "primary_inside_frac": (len(inside) / face_frames) if face_frames else None,
        "primary_well_framed_frac": (len(well_framed) / face_frames) if face_frames else None,
        "min_margin_frac": (min(r["primary_margin_frac"] for r in seen)
                            if seen else None),
        "mean_faces_per_face_frame": (sum(r["faces_total"] for r in seen) / face_frames
                                      if face_frames else None),
        # The aggregates a pass/fail decision is made on. See the module
        # docstring for why these and not the primary_* ones.
        "framed_frac": (len(framed_kept) / nf) if nf else None,
        "framed_well_framed_frac": (len(framed_well) / nf) if nf else None,
        "framed_clipped_frac": (len(framed_clipped) / nf) if nf else None,
        "n_frames_all_faces_lost": len(lost),
        "all_faces_lost": lost[:24],
        # Motion. Travel is total path, not displacement, because a camera
        # that ends where it started but went there and back moved twice and
        # looks twice as restless.
        "crop_travel_frac": round(travel / max(1.0, float(w)), 4),
        "crop_max_step_frac": round(max((abs(xs[i + 1] - xs[i]) for i in range(len(xs) - 1)),
                                        default=0.0) / max(1.0, float(w)), 4),
        "crop_jitter_frac": round(float(np.mean(second_diffs)) / max(1.0, float(crop_w)), 5)
        if second_diffs else 0.0,
        "n_large_jumps": len(big_jumps),
        "n_large_jumps_unexplained": len(unexplained),
        "large_jumps": big_jumps[:12],
        "frames": rows,
    }


# ── rendered-space evaluation ─────────────────────────────────────────────


def render_vertical(video: Path, start: float, end: float, filt: str,
                    out: Path, fps: float = 30.0) -> Path:
    """Burn one vertical clip with a caller-supplied filtergraph.

    Takes the filter as a string rather than calling the reframe itself so the
    benchmark can score an *old* filter captured from the current
    implementation against a new one without forking the code. The encode
    settings are ``engine.render``'s, so the deliverable measured here is the
    deliverable the pipeline would ship.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round((end - start) * fps)))
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(video),
         "-frames:v", str(frames), "-t", f"{frames / fps:.6f}",
         "-vf", filt, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-an", "-movflags", "+faststart", str(out)],
        check=True, capture_output=True, encoding="utf-8")
    return out


def evaluate_rendered(clip: Path, oracle: _Oracle, max_samples: int = 120,
                      dump: Path | None = None) -> dict:
    """Score faces inside the delivered 9:16 frame.

    This is the product-level check: the window is the whole output frame, so
    "inside" means the viewer sees the face, and the margin is measured
    against the frame edge. A crop that put the speaker's nose at the boundary
    scores as a near-miss here even though source-space geometry was exact.
    """
    import cv2

    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        return {"verdict": "could_not_open"}
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    step = max(1, int(total / max(1, max_samples)))
    rows: list[dict] = []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            faces = oracle.detect(frame)
            primary = faces[0] if faces else None
            # Nearest the frame centre, for the same reason as source space:
            # on a rendered two-shot the strongest detection is often the
            # speaker the crop left out on purpose.
            framed = (min(faces, key=lambda f: abs((f[0] + f[2] / 2.0) - w / 2.0))
                      if faces else None)
            row = {"frame": i, "t": round(i / max(1e-6, fps), 3),
                   "n_faces": len(faces)}
            if primary is not None:
                fx, fy, fw, fh, fscore = primary
                fcx = fx + fw / 2.0
                margin = min(fcx, float(w) - fcx)
                row.update({
                    "face_cx": round(fcx, 1), "face_w": round(fw, 1),
                    "face_score": round(fscore, 3),
                    "primary_inside": bool(0.0 <= fcx <= float(w)),
                    "primary_margin_frac": round(margin / max(1.0, fw), 3),
                    "clipped": bool(fx < 0 or fx + fw > w),
                })
            else:
                row.update({"face_cx": None, "face_w": None, "face_score": None,
                            "primary_inside": None, "primary_margin_frac": None,
                            "clipped": None})
            if framed is not None:
                gxf, gyf, gwf, ghf, gsf = framed
                gcx = gxf + gwf / 2.0
                row.update({
                    # In rendered space the window *is* the frame, so a detected
                    # face is inside by construction. What is not tautological
                    # is whether the oracle can see a face at all -- a face
                    # shrunk below the detector's floor by a narrow crop reads
                    # here as an empty frame -- and how close to the edge it sits.
                    "framed_inside": True,
                    "framed_margin_frac": round(
                        min(gcx, float(w) - gcx) / max(1.0, gwf), 3),
                    "framed_clipped": bool(gxf < 0 or gxf + gwf > w),
                    "framed_w": round(gwf, 1),
                })
            else:
                row.update({"framed_inside": None, "framed_margin_frac": None,
                            "framed_clipped": None, "framed_w": None})
            rows.append(row)
            if dump is not None:
                dump.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(dump / f"f{i:05d}.png"), frame)
        i += 1
    cap.release()

    seen = [r for r in rows if r["primary_inside"] is not None]
    inside = [r for r in seen if r["primary_inside"]]
    well = [r for r in inside if r["primary_margin_frac"] >= MARGIN_FRACTION_OK]
    n = len(seen)
    # The rendered-space signal that is not tautological: a face the oracle can
    # actually detect, comfortably clear of both edges.
    present = [r for r in rows if r["n_faces"] > 0]
    comfortable = [r for r in present
                   if (r["framed_margin_frac"] or 0.0) >= MARGIN_FRACTION_OK]
    clipped = [r for r in present if r["framed_clipped"]]
    npresent = len(present)
    return {
        "verdict": "measured",
        "oracle": oracle.kind,
        "out_w": w, "out_h": h, "out_dar": (f"{w // math.gcd(w, h)}:"
                                            f"{h // math.gcd(w, h)}") if w and h else None,
        "n_frames_sampled": len(rows),
        "face_frames": n,
        "primary_inside_frac": (len(inside) / n) if n else None,
        "primary_well_framed_frac": (len(well) / n) if n else None,
        "min_margin_frac": min(r["primary_margin_frac"] for r in seen) if seen else None,
        "face_present_frac": (npresent / len(rows)) if rows else None,
        "face_comfortable_frac": (len(comfortable) / npresent) if npresent else None,
        "face_clipped_frac": (len(clipped) / npresent) if npresent else None,
        "min_rendered_face_w": (min(r["framed_w"] for r in present)
                                if present else None),
        "frames": rows,
    }


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video", type=Path)
    ap.add_argument("start", type=float)
    ap.add_argument("end", type=float)
    ap.add_argument("--yunet", type=Path, default=None,
                    help="path to the YuNet ONNX; default is the vendored lookup")
    ap.add_argument("--trajectory", default="current",
                    help="'current' (engine.reframe as installed) or a JSON file "
                         "of [[t, centre_fraction], ...]")
    ap.add_argument("--render", action="store_true",
                    help="also render the vertical output and score the deliverable")
    ap.add_argument("--filter", default=None,
                    help="filtergraph for --render; default is the current "
                         "vertical_reframe_filter")
    ap.add_argument("--dump-frames", type=Path, default=None,
                    help="write sampled rendered frames here for visual inspection")
    ap.add_argument("--max-samples", type=int, default=180)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    from engine.reframe import (track_subject_center, vertical_crop_width,
                                crop_x_expression, vertical_reframe_filter)
    from providers.ffmpeg import probe_sample_aspect

    sar_w, sar_h = probe_sample_aspect(args.video)
    import cv2
    cap = cv2.VideoCapture(str(args.video))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    cap.release()
    crop_w = min(vertical_crop_width(h, sar_w, sar_h),
                 int(cv2.VideoCapture(str(args.video)).get(
                     cv2.CAP_PROP_FRAME_WIDTH) or 1920))

    if args.trajectory == "current":
        centers = track_subject_center(args.video, args.start, args.end)
    else:
        raw = json.loads(Path(args.trajectory).read_text(encoding="utf-8"))
        centers = [(float(a), float(b)) for a, b in raw]

    report: dict = {
        "source": str(args.video), "start": args.start, "end": args.end,
        "duration": round(args.end - args.start, 3),
        "crop_w": crop_w,
        "trajectory": [[round(a, 3), round(b, 4)] for a, b in centers],
    }

    oracle = _Oracle(_yunet_path(args.yunet))
    if not oracle.available:
        print("warning: no face detector available — face metrics will be "
              "not_measured; falling back to motion metrics only",
              file=sys.stderr)
    report["source_space"] = evaluate_source_space(
        args.video, args.start, args.end, centers, crop_w, oracle,
        max_samples=args.max_samples)

    if args.render:
        # The default is the pipeline's own chain, not a hand-composed copy of
        # it: a copy here would silently keep shipping 1080x1918 after
        # engine.render moved to vertical_reframe_filter.
        filt = args.filter
        if filt is None:
            filt = vertical_reframe_filter(args.video, args.start, args.end) \
                + ",setsar=1"
        clip = args.video.parent / f".reframe_bench_{args.start:.0f}.mp4"
        t0 = time.perf_counter()
        render_vertical(args.video, args.start, args.end, filt, clip)
        report["render_seconds"] = round(time.perf_counter() - t0, 2)
        report["rendered"] = evaluate_rendered(clip, oracle,
                                               dump=args.dump_frames)
        clip.unlink(missing_ok=True)

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    _print_summary(report)
    return 0


def _print_summary(report: dict) -> None:
    s = report["source_space"]
    print(f"source {report['source']}  {report['duration']:.1f}s  crop_w={report['crop_w']}")
    print(f"  oracle={s['oracle']} samples={s['n_samples']} "
          f"shot-boundaries={s['n_shot_boundaries']}")
    for k, label in (("framed_frac", "framed face inside crop"),
                     ("framed_well_framed_frac", "  ... comfortably framed"),
                     ("framed_clipped_frac", "  ... bisected by the edge"),
                     ("primary_inside_frac", "strongest detection inside"),
                     ("min_margin_frac", "  worst margin (x face width)")):
        v = s.get(k)
        print(f"  {label:32s} {_pct(v)}")
    print(f"  frames where ALL faces were lost: "
          f"{s['n_frames_all_faces_lost']} {s['all_faces_lost'][:8]}")
    print(f"  travel={s['crop_travel_frac']:.3f}x w  "
          f"max_step={s['crop_max_step_frac']:.3f}x w  "
          f"jitter={s['crop_jitter_frac']:.4f}x crop")
    print(f"  large jumps={s['n_large_jumps']} "
          f"(unexplained by a cut: {s['n_large_jumps_unexplained']})")
    r = report.get("rendered")
    if r:
        print(f"  rendered {r['out_w']}x{r['out_h']} DAR {r['out_dar']} "
              f"in {report.get('render_seconds')}s")
        print(f"    face present in frame     {_pct(r.get('face_present_frac'))}")
        print(f"    comfortably framed        {_pct(r.get('face_comfortable_frac'))}")
        print(f"    bisected by frame edge    {_pct(r.get('face_clipped_frac'))}")
        print(f"    smallest face detected    "
              f"{r.get('min_rendered_face_w')} px (detector floor is 48 px)")
        print(f"    strongest detection       {_pct(r.get('primary_inside_frac'))}")
    misses = [f for f in s["frames"]
              if f["primary_inside"] is False]
    if misses:
        print(f"  {len(misses)} sample(s) with a detectable face OUTSIDE the crop:")
        for m in misses[:8]:
            print(f"    t={m['t']:7.2f}s face_cx={m['face_cx']} "
                  f"crop=[{m['crop_x0']},{m['crop_x1']}] n_faces={m['n_faces']}")


def _pct(v) -> str:
    if v is None:
        return "not_measured"
    return f"{v * 100:5.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
