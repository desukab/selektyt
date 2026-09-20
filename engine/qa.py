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
        check=True, text=True, encoding="utf-8", stdout=subprocess.PIPE,
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


def _stream(data: dict, codec_type: str) -> dict | None:
    return next((s for s in data.get("streams", []) if s.get("codec_type") == codec_type), None)


def _source_has_audio(source: Path) -> bool | None:
    """Whether the source carries an audio stream.

    Returns ``None`` when the answer is unknown — the source cannot be probed
    at all. That case must never be read as "the source has no audio", because
    it would license a video-only render through the check below. Callers keep
    the conservative failure when the answer is unknown.
    """
    try:
        data = probe(source)
    except Exception:
        return None
    return _stream(data, "audio") is not None


def decode_sample(path: Path, *, max_frames: int = 8, audio_seconds: float = 2.0,
                  timeout: int = 120, channels: int | None = None) -> dict:
    """Decode a sample of frames and audio packets rather than trust headers.

    A container can declare a stream that will not decode, and a stream can
    decode to nothing, so the counts here are the evidence that the output is
    really playable. Both halves are bounded: ``max_frames`` of video to null,
    and ``audio_seconds`` of audio decoded to raw PCM, whose byte count is what
    separates "sound was written" from "a stream object exists".

    ``channels`` is the audio track's channel count; pass it when the caller
    has already probed the file, since converting the PCM byte count to a
    sample count needs it and probing again is the cost this call exists to
    avoid. It is read from the stream when absent.

    Returns ``{video_frames, audio_samples, video_error, audio_error}``. The
    error fields are ``None`` only when that half actually ran to completion.
    """
    out: dict = {"video_frames": 0, "audio_samples": 0,
                 "video_error": None, "audio_error": None}
    # Video: decode to the null muxer and read the frame counter ffmpeg prints
    # on its progress channel. An exit code alone is not enough — a file whose
    # video is undecodable can still exit 0 with a zero frame count.
    vcmd = ["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-i", str(path),
            "-map", "0:v:0", "-frames:v", str(max_frames),
            "-progress", "pipe:1", "-f", "null", "-"]
    try:
        res = subprocess.run(vcmd, capture_output=True, text=True, encoding="utf-8",
                                timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        out["video_error"] = f"video decode did not run: {exc}"
        return out
    if res.returncode != 0:
        tail = (res.stderr or "").strip().splitlines()
        out["video_error"] = "video decode failed: " + (
            tail[-1].strip() if tail else f"exit status {res.returncode}")
        return out
    frames = 0
    for line in (res.stdout or "").splitlines():
        if line.startswith("frame="):
            try:
                frames = int(float(line.split("=", 1)[1].strip()))
            except ValueError:
                pass
    out["video_frames"] = frames

    # Audio: decode to signed 16-bit PCM on stdout and count the samples. A
    # stream that is declared but carries no payload decodes to nothing, which
    # is the silent failure this count exists to catch. ``-map 0:a:0`` fails
    # outright when there is no audio stream to decode, which is a distinct
    # condition reported as such rather than as a zero sample count.
    acmd = ["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-i", str(path),
            "-map", "0:a:0", "-t", f"{audio_seconds}",
            "-f", "s16le", "-c:a", "pcm_s16le", "-"]
    try:
        ares = subprocess.run(acmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        out["audio_error"] = f"audio decode did not run: {exc}"
        return out
    if ares.returncode != 0:
        tail = (ares.stderr or "").strip().decode(errors="replace").splitlines()
        out["audio_error"] = "no audio stream to decode: " + (
            tail[-1].strip() if tail else f"exit status {ares.returncode}")
        return out
    if channels is None:
        # The channel count is needed only to convert bytes to samples, and
        # only when there is something to convert.
        data = probe(path)
        audio = _stream(data, "audio")
        channels = int((audio or {}).get("channels", 2) or 2)
    # Two bytes per sample at s16; the count is halved by channel count so a
    # mono and a stereo deliverable are compared on samples, not bytes.
    out["audio_samples"] = int(len(ares.stdout) // (2 * max(1, channels)))
    return out


def audio_condition(path: Path, source: Path | None = None,
                    sample: dict | None = None) -> dict:
    """Classify why an output does or does not carry sound.

    The two failure modes look identical from the container alone — "no audio"
    — but call for opposite user responses, so they get distinct states and
    distinct messages:

    * ``present`` — audio is declared and decoding produces samples.
    * ``absent_expected`` — there is no audio, and the source has none either.
      This is a video-only deliverable by design, not a broken render.
    * ``absent_broken`` — there is no audio, but the source does have one. The
      render dropped or never retained the stream.
    * ``declared_but_empty`` — an audio stream is present in the container but
      decoding yields no samples, or its duration is negligible against the
      container. The header looks correct and the file is silent.

    ``absent_expected`` is only reported when the source was actually probed;
    an unprobeable source yields ``absent_broken`` so a broken render can never
    be passed off as a faithful video-only render of an unreadable source.

    ``sample`` is an already-run ``decode_sample``; the classification needs
    its audio counts, and a caller that also needs the video frame count should
    not pay for the decode twice.
    """
    try:
        data = probe(path)
    except Exception as exc:
        return {"state": "unprobeable", "message": f"ffprobe failed: {exc}"}
    audio = _stream(data, "audio")
    container = float(data.get("format", {}).get("duration", 0) or 0)
    if audio is None:
        # Probed once, here and nowhere else in this branch: the source's audio
        # state is the single fact that separates a faithful video-only render
        # from a broken one, and it is an ffmpeg invocation over the source.
        has_source_audio = None if source is None else _source_has_audio(source)
        if has_source_audio is False:
            return {"state": "absent_expected",
                    "message": ("no audio stream, and the source has no audio "
                                "stream either: a video-only deliverable by design")}
        if has_source_audio is True:
            return {"state": "absent_broken",
                    "message": ("NO AUDIO RETAINED: the source has an audio stream but "
                                "this output has none — the render dropped it. This is "
                                "a broken render, not a video-only source.")}
        # ``None`` is the unprobeable case, and it is deliberately not read as
        # "no audio": a missing probe has no evidence either way, and treating
        # it as absent_expected would license a broken render through.
        return {"state": "absent_broken",
                "message": ("no audio stream and the source could not be probed to "
                            "confirm it is video-only: treat as a broken render rather "
                            "than assume the source was silent")}
    sample = decode_sample(path, channels=int(audio.get("channels", 2) or 2)) \
        if sample is None else sample
    # A duration far below the container is the same silent failure as no
    # samples at all: the stream exists on paper and carries nothing.
    audio_dur = float(audio.get("duration", 0) or 0)
    if sample["audio_error"]:
        # ``-map 0:a:0`` fails both when there is no audio stream to select and
        # when a present stream will not decode, and those are different
        # defects with different fixes. Here a stream provably exists — this
        # branch is only reached past the ``audio is None`` guard above — so
        # the failure is a corrupt or undecodable track, never a missing one.
        return {"state": "declared_but_empty",
                "message": ("AUDIO EXISTS BUT COULD NOT BE DECODED: an audio stream is "
                            f"declared ({audio.get('codec_name', 'audio')}) and present "
                            f"in the container, but decoding it failed "
                            f"({sample['audio_error']}). This is a corrupt or "
                            f"undecodable track, not a video-only render.")}
    if sample["audio_samples"] <= 0:
        return {"state": "declared_but_empty",
                "message": ("AUDIO EXISTS BUT COULD NOT BE DECODED: an audio stream is "
                            f"declared ({audio.get('codec_name', 'audio')}) but decoding "
                            f"produced no samples. The render is broken.")}
    if container >= 2.0 and 0.0 < audio_dur < 0.5 * container:
        return {"state": "declared_but_empty",
                "message": (f"AUDIO EXISTS BUT COULD NOT BE RETAINED: the audio stream "
                            f"is {audio_dur:.3f}s against a {container:.3f}s container, "
                            f"so most of the program has no sound. The render is broken.")}
    return {"state": "present",
            "message": f"audio decodes ({sample['audio_samples']} sampled samples)"}


def _parse_rate(rate: str) -> float | None:
    """ffmpeg reports rates as rationals like ``30000/1001``; as a plain float."""
    try:
        num, _, den = (rate or "0/1").partition("/")
        den = den or "1"
        val = float(num) / float(den)
        return val if val > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def validate_output(path: Path, vertical: bool, *,
                    source: Path | None = None,
                    expected_fps: float | None = None,
                    audio_cond: dict | None = None,
                    sample: dict | None = None) -> tuple[bool, list[str]]:
    """Validate one deliverable.

    ``audio_cond`` is an already-computed ``audio_condition(path, source)`` and
    ``sample`` an already-run ``decode_sample``. Pass either when the caller
    needs it for its own record: a probe plus a full decode sample is not free,
    and this function would otherwise run the whole thing a second time to
    reach the same answer. ``audio_cond`` takes precedence when both are given,
    since it already carries the sample's findings.
    """
    errors: list[str] = []
    if not path.is_file() or path.stat().st_size < 1000:
        return False, ["missing or empty output"]
    try:
        data = probe(path)
    except Exception as exc:
        return False, [f"ffprobe failed: {exc}"]
    streams = data.get("streams", [])
    video = _stream(data, "video")
    audio = _stream(data, "audio")
    if not video:
        errors.append("no video stream")
    if video:
        w, h = int(video.get("width", 0)), int(video.get("height", 0))
        if not w or not h:
            # A zero dimension is the container lying: the stream object exists
            # and no frame could be displayed from it. Reported before the
            # aspect checks because neither of them can mean anything here.
            errors.append(f"video stream has no dimensions (reported {w}x{h})")
        else:
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

        # Decoding a sample is the only proof the video is really there. A
        # header can declare an H.264 stream over bytes that will not decode,
        # and a truncated encode can leave a container that ffprobe reads
        # happily. Zero decoded frames on a stream that claims to exist is a
        # broken render regardless of what the container says.
        sample = decode_sample(path, channels=int(audio.get("channels", 2) or 2)
                               if audio else None) if sample is None else sample
        if sample["video_error"]:
            errors.append(sample["video_error"])
        elif sample["video_frames"] == 0:
            errors.append(
                "video stream is declared but decoding produced no frames — the "
                "output is not playable")

        # The frame rate the render asked for is stamped into the timeline and
        # every interchange file cut against it, so a deliverable at a
        # different rate has already desynced from its own EDL before anyone
        # opens it. ``r_frame_rate`` is what frames are actually delivered at.
        if expected_fps is not None and expected_fps > 0:
            rate = video.get("r_frame_rate") or video.get("avg_frame_rate") or "0/1"
            got = _parse_rate(rate)
            if got is not None and abs(got - expected_fps) / expected_fps > 0.02:
                errors.append(
                    f"expected {expected_fps:.3f} fps, got {got:.3f} fps ({rate}); "
                    f"the timeline and interchange files were cut at "
                    f"{expected_fps:.3f} fps")

    # Audio is the failure this check exists for. The container alone cannot
    # tell a faithful video-only render from a render that dropped the stream,
    # so the condition is classified against the source and the two cases get
    # different messages because they call for different user responses: a
    # video-only source is fine as-is, a dropped stream is a broken render.
    cond = audio_cond if audio_cond is not None else audio_condition(path, source, sample)
    if cond["state"] in ("absent_broken", "declared_but_empty", "unprobeable"):
        errors.append(cond["message"])
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    if duration < 1.0:
        errors.append("output duration is too short")
    if video is not None and audio is not None and duration >= 1.0 \
            and cond["state"] == "present":
        # A stream present at 0.0 s is exactly the shape the shipped defect
        # took: the container held a track that carried nothing, the duration
        # check above passed on the *video*, and nothing else looked twice.
        # Guarded on ``present`` because the classified conditions above
        # already name the same defect in the terms a user can act on, and a
        # second generic message about it would only bury the specific one.
        audio_dur = float(audio.get("duration", 0) or 0)
        video_dur = float(video.get("duration", 0) or 0)
        for label, dur in (("audio", audio_dur), ("video", video_dur)):
            if 0.0 < dur < 0.5 * duration:
                errors.append(
                    f"{label} stream is {dur:.3f}s against a {duration:.3f}s "
                    f"container: the stream is present but carries almost "
                    f"nothing")
    return not errors, errors


def validate_outputs(files: list[Path], *,
                      source: Path | None = None,
                      expected_fps: float | None = None) -> list[str]:
    errors: list[str] = []
    for f in files:
        ok, detail = validate_output(f, "shorts" in f.parts,
                                     source=source, expected_fps=expected_fps)
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
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                   timeout=60)
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


def validate_outputs_expanded(outputs: list[Path], *,
                               source: Path | None = None,
                               expected_fps: float | None = None) -> dict:
    """Full QA: ffprobe + decode sampling + silent tail + black frames + diversity.

    Two tiers of finding, and only one of them fails the run:

    * ``errors`` — the output is technically wrong or could not be validated:
      missing/empty file, no video or audio stream, a stream that will not
      decode, wrong orientation or frame rate, a track whose duration is a
      fraction of the container, or a probe (ffprobe/silencedetect) that could
      not run. A render we cannot verify is a render we cannot ship, so a
      probe failure is an error rather than a pass.
    * ``warnings`` — the output is a real, playable video with an editorial
      weakness worth a human editor's attention: a trailing silence run, a
      high near-black-frame ratio, low visual diversity between clips.

    Advisory findings are reported but never raise, so a usable render that
    merely has a quiet tail is not discarded — and, just as important, the
    render cache is not poisoned against it.

    ``source`` is the media the outputs were cut from. It is what separates a
    video-only deliverable (the source has no audio either) from a render that
    dropped its audio (the source does have one). Without it the check cannot
    make that distinction and reports the conservative failure instead.
    ``expected_fps`` is the rate the render was asked to run at, and comes from
    the source's own frame rate.
    """
    report: dict = {"ok": True, "errors": [], "warnings": [], "per_file": {}, "diversity": 0.0}
    for f in outputs:
        is_vertical = "shorts" in f.parts
        # The decode sample and the audio classification are computed once and
        # handed to validate_output: without this the function re-probes and
        # re-decodes to reach the same conclusion, and on the corpus critical
        # path that doubled cost is per output file.
        sample = decode_sample(f)
        cond = audio_condition(f, source, sample)
        ok, errors = validate_output(f, is_vertical, source=source,
                                     expected_fps=expected_fps,
                                     audio_cond=cond, sample=sample)
        tail, tail_sec, tail_error = detect_silent_tail(f)
        black = black_frame_pct(f)
        file_report: dict = {
            "errors": errors,
            "audio_condition": cond["state"],
            "audio_message": cond["message"],
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
    source = Path(source_path) if source_path else None
    # The frame rate the renders were asked to run at is the source's own rate,
    # which the pipeline stamps into every timeline and interchange file. Read
    # it here rather than at the call site so the QA contract stays a property
    # of the media rather than of whatever the caller remembered to pass.
    expected_fps: float | None = None
    if source is not None and source.is_file():
        try:
            data = probe(source)
            v = _stream(data, "video")
            if v:
                expected_fps = _parse_rate(v.get("r_frame_rate") or v.get("avg_frame_rate") or "")
        except Exception:
            expected_fps = None
    report = validate_outputs_expanded(outputs, source=source, expected_fps=expected_fps)
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
