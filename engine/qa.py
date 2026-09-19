from __future__ import annotations
import json
import re
import shutil
import subprocess
from pathlib import Path


def probe(path: Path) -> dict:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe is required and must be on PATH")
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, text=True, stdout=subprocess.PIPE,
    )
    return json.loads(p.stdout)


def _display_aspect(stream: dict) -> float | None:
    """The stream's display aspect from the container's SAR and dimensions.

    ``display_aspect_ratio`` is not always populated; deriving it from the
    sample aspect ratio is what the encoder actually honoured.
    """
    w, h = int(stream.get("width", 0) or 0), int(stream.get("height", 0) or 0)
    if not w or not h:
        return None
    sar = stream.get("sample_aspect_ratio") or "1:1"
    try:
        sar_w, sar_h = (int(p) for p in sar.split(":", 1))
    except ValueError:
        sar_w, sar_h = 1, 1
    if not sar_w or not sar_h:
        sar_w, sar_h = 1, 1
    return w * sar_w / (h * sar_h)


def validate_output(path: Path, vertical: bool) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not path.is_file() or path.stat().st_size < 1000:
        return False, ["missing or empty output"]
    try:
        data = probe(path)
    except Exception as exc:
        return False, [f"ffprobe failed: {exc}"]
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        errors.append("no video stream")
    if not audio:
        errors.append("no audio stream")
    if video:
        w, h = int(video.get("width", 0)), int(video.get("height", 0))
        if vertical and not h > w:
            errors.append(f"expected vertical video, got {w}x{h}")
        if not vertical and not w > h:
            errors.append(f"expected landscape video, got {w}x{h}")
        # The display aspect, not the pixel count, is the contract: a 9:16
        # *pixel* count is only 9:16 for square pixels, and an anamorphic
        # source inherits a sample aspect ratio that makes a correct-looking
        # frame display at the wrong ratio. Measured on a real run, the
        # landscape deliverable shipped at DAR 320:243 — 4:3 — labelled 16:9,
        # and nothing else in the pipeline would have noticed.
        dar = _display_aspect(video)
        if dar:
            want = 9 / 16 if vertical else 16 / 9
            if abs(dar - want) / want > 0.01:
                errors.append(
                    f"expected a {9 if vertical else 16}:{16 if vertical else 9} "
                    f"display aspect, got {w}x{h} at DAR {dar:.4f}"
                )
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    if duration < 1.0:
        errors.append("output duration is too short")
    return not errors, errors


def validate_outputs(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for f in files:
        ok, detail = validate_output(f, "shorts" in f.parts)
        if not ok:
            errors.extend(f"{f}: {x}" for x in detail)
    return errors


def detect_silent_tail(
    path: Path, threshold_db: float = -45, min_sec: float = 1.5
) -> tuple[bool, float, str | None]:
    """Detect if the last min_sec seconds of audio are silent.

    Returns ``(has_tail, duration_sec, error)``.

    ``error`` is non-None only when the probe itself could not run — ffmpeg
    exited non-zero, timed out, or produced no output at all. That case is
    *not* the same as "no silent tail": a probe that did not run has no
    evidence either way, and reporting it as clean would let a genuinely
    broken render pass QA. Callers must treat a non-None error as a failure
    to validate, never as a pass.

    Runs silencedetect over the whole audio (no seeking — seeking shifts the
    reported timestamps so EOF-tail silence is indistinguishable from middle
    silence). A tail is a silence interval whose ``silence_end`` runs out to
    EOF (``>= duration - eps``) and whose span from ``silence_start`` to EOF
    is at least min_sec.
    """
    try:
        data = probe(path)
    except Exception as exc:
        # ffprobe refusing the file is a probe failure, not evidence of a clean
        # tail. Returning it as an error lets the caller fail the file instead
        # of passing it — the old code swallowed this and reported (False, 0.0),
        # which read as "verified no tail" on a file that could not be probed.
        return False, 0.0, f"ffprobe failed: {exc}"
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    if duration < min_sec + 0.5:
        return False, 0.0, None
    d = max(0.2, min_sec * 0.8)
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", f"silencedetect=noise={threshold_db}dB:d={d}",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False, 0.0, "silencedetect timed out"
    except OSError as exc:
        return False, 0.0, f"could not run ffmpeg: {exc}"

    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()
        detail = tail[-1].strip() if tail else f"exit status {result.returncode}"
        return False, 0.0, f"silencedetect failed: {detail}"

    stderr = result.stderr or ""
    # A single line can carry both a silence_start and its silence_end (a
    # silence shorter than the report interval), so both are parsed per line
    # rather than elif-chained, then paired in order of appearance.
    starts: list[float] = []
    ends: list[float] = []
    for line in stderr.splitlines():
        if "silence_start:" in line:
            starts.append(float(line.split("silence_start:")[1].strip()))
        if "silence_end:" in line:
            ends.append(float(line.split("silence_end:")[1].split("|")[0].split()[0]))
    if not starts and not ends:
        # No silence events at all over the whole file: either it is all
        # speech, or the filter produced no output we can read. Distinguish
        # them by whether ffmpeg reported anything on stderr.
        if not stderr.strip():
            return False, 0.0, "silencedetect produced no output"
        return False, 0.0, None

    pending = list(starts)
    sil_regions: list[tuple[float, float]] = []
    for e in ends:
        if pending:
            s = pending.pop(0)
            sil_regions.append((s, e))
    # A silence that is still open at EOF has no silence_end; it is the tail.
    if pending:
        sil_regions.append((pending[-1], duration))
    for s, e in sil_regions:
        if e >= duration - 0.05 and duration - s >= min_sec - 1e-9:
            return True, round(duration - s, 3), None
    return False, 0.0, None


def black_frame_pct(path: Path, threshold: float = 16.0, sample_count: int = 8) -> float:
    """Estimate percentage of frames that are near-black via uniform sampling."""
    import cv2
    import numpy as np
    try:
        info = probe(path)
        duration = float(info.get("format", {}).get("duration", 0) or 0)
        if duration < 0.5:
            return 0.0
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return 0.0
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total = int(duration * fps)
        indices = np.linspace(0, max(0, total - 1), sample_count, dtype=int)
        black_count = 0
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, frame = cap.read()
            if ok:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if gray.mean() < threshold:
                    black_count += 1
        cap.release()
        return round(black_count / max(1, sample_count), 3)
    except Exception:
        return 0.0


def visual_diversity_score(paths: list[Path], sample_count: int = 5) -> float:
    """Pairwise HSV histogram Bhattacharyya distance across output clips.

    Computes one representative histogram per clip (mean of sampled frames),
    then averages Bhattacharyya distance across clip pairs. Returns 0.0
    (identical visuals) to 1.0 (very diverse). A single clip has no peer to
    compare against and scores a default 1.0.
    """
    import cv2
    import numpy as np
    clip_histograms: list = []
    for path in paths:
        try:
            cap = cv2.VideoCapture(str(path))
            if not cap.isOpened():
                continue
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            frames: list = []
            for idx in np.linspace(0, max(0, total_frames - 1), sample_count, dtype=int):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
                ok, frame = cap.read()
                if ok:
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                    hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
                    cv2.normalize(hist, hist)
                    frames.append(hist.flatten())
            cap.release()
            if frames:
                clip_histograms.append(np.mean(frames, axis=0))
        except Exception:
            continue
    if len(clip_histograms) < 2:
        return 1.0
    distances: list[float] = []
    for i in range(len(clip_histograms)):
        for j in range(i + 1, len(clip_histograms)):
            d = cv2.compareHist(
                clip_histograms[i].reshape(-1, 1),
                clip_histograms[j].reshape(-1, 1),
                cv2.HISTCMP_BHATTACHARYYA,
            )
            distances.append(d)
    return round(min(1.0, sum(distances) / max(1, len(distances))), 3)


def validate_outputs_expanded(outputs: list[Path]) -> dict:
    """Full QA: ffprobe + silent tail + black frames + visual diversity.

    Two tiers of finding, and only one of them fails the run:

    * ``errors`` — the output is technically wrong or could not be validated:
      missing/empty file, no video or audio stream, wrong orientation, a
      duration under a second, or a probe (ffprobe/silencedetect) that could
      not run. A render we cannot verify is a render we cannot ship, so a
      probe failure is an error rather than a pass.
    * ``warnings`` — the output is a real, playable video with an editorial
      weakness worth a human editor's attention: a trailing silence run, a
      high near-black-frame ratio, low visual diversity between clips.

    Advisory findings are reported but never raise, so a usable render that
    merely has a quiet tail is not discarded — and, just as important, the
    render cache is not poisoned against it.
    """
    report: dict = {"ok": True, "errors": [], "warnings": [], "per_file": {}, "diversity": 0.0}
    for f in outputs:
        is_vertical = "shorts" in f.parts
        ok, errors = validate_output(f, is_vertical)
        tail, tail_sec, tail_error = detect_silent_tail(f)
        black = black_frame_pct(f)
        file_report: dict = {
            "errors": errors,
            "silent_tail": tail,
            "silent_tail_sec": tail_sec,
            "black_frame_pct": black,
            "size": f.stat().st_size if f.is_file() else 0,
        }
        hard = list(errors)
        warnings: list[str] = []
        if tail_error:
            hard.append(f"silent-tail probe failed: {tail_error}")
            file_report["probe_error"] = tail_error
        elif tail:
            warnings.append(f"silent tail ({tail_sec:.1f}s)")
        if black > 0.5:
            # Mostly-black frames are usually a real composition problem, but
            # the video is still structurally valid — an editor decides.
            warnings.append(f"{black:.0%} near-black frames")
        file_report["warnings"] = warnings
        report["per_file"][str(f)] = file_report
        if hard:
            report["errors"].extend(f"{f}: {e}" for e in hard)
            report["ok"] = False
        if warnings:
            report["warnings"].extend(f"{f}: {w}" for w in warnings)
    fb_clips = [p for p in outputs if "facebook" in p.parts]
    report["diversity"] = visual_diversity_score(fb_clips) if len(fb_clips) > 1 else 1.0
    if len(fb_clips) > 1 and report["diversity"] < 0.15:
        report["warnings"].append(
            f"selected clips are visually similar (diversity {report['diversity']:.2f})"
        )
    return report


def source_coverage(
    outputs: list[Path],
    selections_path: Path,
    source_duration: float | None = None,
) -> dict:
    """Analyze source time covered by candidate selections.

    Detects overlapping clips in source-time and reports coverage percentage.
    source_duration can be provided (from real source) for accuracy; if None,
    falls back to probing the first output's format.duration.
    """
    if not selections_path.exists():
        return {"coverage_pct": 0, "overlaps": [], "overlap_count": 0, "total_covered_sec": 0}
    try:
        selections = json.loads(selections_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"coverage_pct": 0, "overlaps": [], "overlap_count": 0, "total_covered_sec": 0}
    if not selections:
        return {"coverage_pct": 0, "overlaps": [], "overlap_count": 0, "total_covered_sec": 0}
    if source_duration is None:
        try:
            info = probe(outputs[0]) if outputs else {}
            source_duration = float(info.get("format", {}).get("duration", 1))
        except Exception:
            source_duration = 1.0
    intervals = sorted(
        [(float(s["start"]), float(s["end"])) for s in selections if "start" in s and "end" in s]
    )
    merged: list[tuple[float, float]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    total_covered = sum(end - start for start, end in merged)
    overlaps: list[dict] = []
    for i in range(len(selections)):
        for j in range(i + 1, len(selections)):
            si, sj = selections[i], selections[j]
            o_start = max(float(si["start"]), float(sj["start"]))
            o_end = min(float(si["end"]), float(sj["end"]))
            if o_end > o_start:
                overlaps.append({
                    "clip_i": i + 1, "clip_j": j + 1,
                    "overlap_sec": round(o_end - o_start, 2),
                })
    return {
        "coverage_pct": round(total_covered / max(1e-9, source_duration) * 100, 1),
        "total_covered_sec": round(total_covered, 2),
        "overlaps": overlaps,
        "overlap_count": len(overlaps),
    }


def write_report(root: Path, outputs: list[Path], source_path: str | None = None) -> Path:
    """Run expanded QA checks, compute source coverage + embed pacing, write qa.json."""
    report = validate_outputs_expanded(outputs)
    selections_path = root / "selections.json"
    sd = None
    if source_path:
        try:
            sd = float(probe(Path(source_path)).get("format", {}).get("duration", 0) or 0) or None
        except Exception:
            sd = None
    report["source_coverage"] = source_coverage(outputs, selections_path, sd)
    pacing_path = root / "pacing_report.json"
    if pacing_path.exists():
        try:
            report["pacing"] = json.loads(pacing_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    qa_path = root / "qa.json"
    qa_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not report["ok"]:
        raise RuntimeError("output QA failed: " + "; ".join(report["errors"][:10]))
    return qa_path
