"""Regenerate the render throughput figures the cost model is built on.

Why this tool exists: the render real-time factor is the second-largest term in
`docs/BUSINESS_MODEL.md`'s throughput model (0.0833 of the 0.3584 total, behind
transcription), and for three days every published instance of it was wrong by
7x. Commit `56253b5` replaced `-preset veryfast -crf 20` with `-preset slow
-crf 18` as a deliberate quality purchase and the cost row was never re-run, so
the figure kept describing an encoder the pipeline no longer shipped. Nothing
in the repository could have caught it: `tests/test_render.py` pins the three
x264 flags, but nothing regenerates the *cost consequence* of changing them,
and the figure itself was measured ad hoc in a terminal against a source that
lived in a scratch directory. That is the process gap this closes.

Two measurements, because they answer different questions:

* **`ship`** renders the deliverables the pipeline actually ships — the same
  `engine.render._render_one` call `render_clip` makes per part, the same
  filters from `engine.reframe`, the same encoder read live from
  `_encoder_args()` — over a window of a real corpus source, and reports the
  real-time factor of each orientation. This is the row the cost model reads.
* **`ladder`** scores encoder settings against a `-crf 0` reference of the
  *same* filter chain: bitrate, SSIM, PSNR and wall time per entry. This is the
  table in `_encoder_args`' docstring, and it is what decides whether a
  settings change is a free win, a purchase, or a regression.

Everything the tool measures is recorded next to the number — source, window,
resolution, frame rate, the exact filter strings, the encoder flags, ffmpeg's
version, the core count and the load average — because the defect this exists
to prevent was not an arithmetic error. Two independent "cross-checks" agreed
with the stale figure to 3.6% and were read as corroboration, when both had
reproduced the figure's *measurement conditions* (the retired encoder, an SD
source) rather than the current ones. A number that cannot be read alongside
its conditions is a number that cannot be trusted, so they print together.

What this cannot measure: perceptual quality. SSIM and PSNR are proxy
instruments that agree with each other and can both miss what an eye catches —
banding on a gradient, ringing on a hard edge. A ladder result is read as
evidence about an encoder, never as a judgement about what the viewer sees.

This is a benchmark, not a test. It prints a table; it asserts nothing. A hard
RTF gate would be flaky on a shared host: the docs record a spread of at least
~1.7x from load alone on this machine, so `ship` repeats the render and reports
best, median and worst, and the spread is printed rather than hidden.

Usage::

    .venv/bin/python tools/render_bench.py ship \\
        --start 0 --end 30 --repeat 3 --tag pentagon-30s

    .venv/bin/python tools/render_bench.py ladder \\
        --start 0 --frames 240 --tag pentagon-240f

    .venv/bin/python tools/render_bench.py report

Defaults: the source is the checked-in 1280x720 29.97 fps pentagon briefing,
never a scratch file — a bench table whose source lives in a directory that can
be cleaned up is a table nobody can re-derive, which is how the stale figure
went unchallenged.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The default scratch dir is gitignored: the renders and the lossless reference
# are regenerable and large. The run records under corpus/render/runs/ ARE the
# evidence and stay in, so a quoted RTF can be audited without re-rendering.
SCRATCH = ROOT / "corpus" / "render"
RUNS = SCRATCH / "runs"

DEFAULT_SOURCE = ROOT / "corpus" / "media" / "pentagon-esper-briefing.mp4"
DEFAULT_WINDOW = (0.0, 30.0)

KEEP = False  # set by main(); the measurement helpers read it

# The ladder the docs carry, in the order `_encoder_args`' docstring lists it.
# The first entry is the retired pair the old cost figure was measured against;
# it stays in the default set because the distance between it and the shipped
# entry *is* the evidence for the encoder decision, and dropping it would leave
# a table that can no longer explain its own conclusion.
LADDER = [
    ("veryfast", 20),
    ("medium", 20),
    ("slow", 20),
    ("slow", 18),
    ("slow", 16),
]


# ── conditions: the half of the measurement that usually goes unrecorded ────

def _ffprobe(video: Path, stream: str, entries: str) -> str:
    """ffprobe one stream, as `key=value` lines.

    ``entries`` takes bare stream keys; the ``stream=`` section prefix is added
    here because ffprobe reads an unprefixed list as a section name and fails
    with "No match for section" — the house style in `providers/ffmpeg.py` and
    `tools/corpus_run.py` is to spell the section out.
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", stream,
             "-show_entries", f"stream={entries}", "-of",
             "default=noprint_wrappers=1:nokey=0", str(video)],
            capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        # stderr is surfaced rather than swallowed: a probe failure whose
        # message is discarded costs a debugging round trip on every recurrence.
        raise RuntimeError(
            f"ffprobe could not read {video} ({exc.returncode}): "
            f"{(exc.stderr or '').strip()[:400]}") from exc
    return proc.stdout.strip()


def _kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _emitted_duration(path: Path, frames: int) -> float:
    """How many seconds the file actually plays, not how many were requested.

    ``-frames:v`` without ``-r`` inherits the source's frame rate, so the emitted
    duration is ``frames / source_fps`` and a requested span computed from a
    different fps is simply not the duration of the file. Measured on the
    pentagon source (30000/1001 fps): asking for 240 frames at --fps 30, 29.97
    and 25 all emit 240 frames over 8.008 s, while the naive
    ``frames / requested_fps`` says 8.000, 8.008 and 9.600 s. The last is a 17%
    overstatement of the content, which understates every RTF and bitrate in the
    record by the same 17% — a systematic error that reads as the encoder having
    gotten cheaper.

    The probed format duration is authoritative; the frame-count fallback only
    covers a container that reports neither.
    """
    raw = _kv(_ffprobe(path, "v:0", "duration")).get("duration")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    fps = _rational(_kv(_ffprobe(path, "v:0", "r_frame_rate")).get("r_frame_rate", "30/1"))
    return frames / fps if fps else 0.0


def probe_source(video: Path) -> dict:
    """Everything about the source that the RTF is conditioned on.

    A figure measured on 1280x720 does not transfer to 3840x2160: render cost
    scales with output pixels, and the ladder below was measured on HD footage
    because that is what the vertical path targets. Recording this is what lets
    a later reader tell a re-measurement from a contradiction.
    """
    from providers.ffmpeg import probe_sample_aspect

    v = _kv(_ffprobe(video, "v:0", "width,height,r_frame_rate,pix_fmt,duration"))
    a = _ffprobe(video, "a:0", "index")
    sar_w, sar_h = probe_sample_aspect(video)
    try:
        size = video.stat().st_size
    except OSError:
        size = 0
    return {
        "path": str(video),
        "width": int(v.get("width", 0)),
        "height": int(v.get("height", 0)),
        "fps": _rational(v.get("r_frame_rate", "30/1")),
        "pix_fmt": v.get("pix_fmt"),
        "duration": _float(v.get("duration")),
        "sar": f"{sar_w}:{sar_h}",
        "has_audio": bool(a.strip()),
        # The source's own bitrate bounds what a re-encode of a window of it can
        # weigh; the ship path's disk estimate reads it below.
        "size_bytes": size,
    }


def _rational(s: str) -> float:
    num, _, den = s.partition("/")
    try:
        return float(num) / (float(den) if den else 1.0)
    except ValueError:
        return 30.0


def _float(s: str | None) -> float | None:
    try:
        return float(s) if s is not None else None
    except ValueError:
        return None


def _load_average() -> list[float] | None:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except (AttributeError, OSError):
        return None


@contextlib.contextmanager
def _hush_ffmpeg():
    """Send inherited subprocess stderr to a buffer, re-emitting it on failure.

    `engine.render._render_one` runs ffmpeg inheriting this process's stderr, so
    a benchmark render dumps the full banner and input dump — ~78 KB for one
    30-second ship run — and buries the table the tool exists to print. The fd
    is redirected rather than the subprocess rewritten, and the capture is only
    kept long enough to replay it should the render actually fail, so a genuine
    error is never made quieter by being measured.
    """
    import tempfile
    saved = os.dup(2)
    with tempfile.TemporaryFile(mode="w+") as cap:
        os.dup2(cap.fileno(), 2)
        try:
            yield
        except BaseException:
            cap.seek(0)
            os.dup2(saved, 2)
            sys.stderr.write(cap.read())
            raise
        else:
            os.dup2(saved, 2)
    os.close(saved)


def conditions() -> dict:
    """Host and build conditions, so a re-run on a different machine is a
    different row rather than a silent disagreement.

    Call this before the renders, not after: the load average is meant to say
    whether the host was idle when the clock started, and a figure sampled after
    four timed renders mostly describes the benchmark itself.
    """
    commit = None
    try:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    version = None
    try:
        version = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True,
            timeout=10).stdout.splitlines()[0].split("version", 1)[-1].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return {
        "date": time.strftime("%Y-%m-%d"),
        "ffmpeg_version": version,
        "cpu_count": os.cpu_count(),
        "load_average": _load_average(),
        "repo_commit": commit,
    }


# ── rendering ────────────────────────────────────────────────────────────────


def _encode(source: Path, start: float, frames: int, span: float, graph: str,
            out: Path, encoder: list[str], composed: bool = False) -> None:
    """One ffmpeg encode over a caller-supplied filtergraph.

    Audio is dropped (``-an``) — this path exists to compare encoders and to
    measure encode cost, and carrying audio would import the muxer and the
    audio retry path into a number that is about neither. `ship` uses the
    production entry point instead, which renders the real deliverable.
    """
    video_opt: list[str]
    if composed:
        # A multi-chain graph ending in [v] is rejected under -vf (measured and
        # documented in engine.render._render_one), so it takes -filter_complex
        # and the streams have to be mapped explicitly.
        video_opt = ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?"]
    else:
        video_opt = ["-vf", graph]
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source),
             "-frames:v", str(frames), "-t", f"{span:.6f}", *video_opt,
             "-c:v", "libx264", *encoder, "-an",
             "-movflags", "+faststart", str(out)],
            check=True, capture_output=True, encoding="utf-8")
    except subprocess.CalledProcessError as exc:
        # ffmpeg's reason is on stderr and check=True throws it away: the inputs
        # here are files this tool just wrote, so the one fact that separates a
        # corrupt input from an unbuildable filtergraph is the line it would
        # otherwise drop. Only the tail is kept — a full input dump makes the
        # message unusable rather than merely long.
        raise RuntimeError(
            f"ffmpeg failed encoding {out.name}: "
            f"{(exc.stderr or '').strip()[-1200:]}") from exc


def _score(entry: Path, reference: Path, frames: int) -> dict:
    """SSIM and PSNR of an encode against the lossless reference.

    ffmpeg's ``ssim`` and ``psnr`` filters both run in one pass over the two
    inputs and summarise to stderr. Luma only is reported: chroma follows luma
    closely enough on this footage that reporting both adds columns without
    adding a decision, and the ladder in the docs has always been luma. That is
    also an assumption baked into the parse below: ffmpeg labels the summary
    after the planar component it measured, so a YUV input prints ``SSIM Y:``
    and an RGB one prints ``SSIM R:``. Every entry this tool emits pins yuv420p,
    so the Y case is the only live one; if an RGB file ever reaches here the
    parse fails rather than silently scoring the wrong channel, and the message
    below says which one it expected.

    A lossless entry scores ``PSNR y:inf``, which is not a parse failure — it is
    the correct answer to "how different are these two files" — so it is read as
    a finite sentinel and flagged. Verified empirically against two identical
    inputs before any real run, because assuming a ladder entry can never be
    lossless is the kind of guess that becomes a stack trace later.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(entry),
             "-i", str(reference), "-frames:v", str(frames),
             "-lavfi", "[0][1]ssim;[0][1]psnr", "-f", "null", "-"],
            capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        # A scoring failure is almost always a filtergraph ffmpeg rejects or an
        # input it cannot decode, and either reason is on the stderr check=True
        # discards.
        raise RuntimeError(
            f"ffmpeg failed scoring {entry.name} against {reference.name}: "
            f"{(exc.stderr or '').strip()[-1200:]}") from exc
    log = proc.stderr
    ssim = re.search(r"SSIM Y:([0-9.]+)", log)
    psnr = re.search(r"PSNR y:([0-9.inf-]+)", log)
    if ssim is None or psnr is None:
        raise RuntimeError(
            f"could not parse SSIM Y:/PSNR y: from ffmpeg. This reports luma "
            f"only, so it assumes a YUV planar input; an RGB pix_fmt would "
            f"print SSIM R: instead and every entry this tool emits pins "
            f"yuv420p, so that is the case to check first. The other cause is "
            f"the ssim/psnr filters being absent from this build. ffmpeg "
            f"said:\n{log[-1200:]}")
    psnr_text = psnr.group(1)
    infinite = psnr_text in ("inf", "-inf")
    return {"ssim": round(float(ssim.group(1)), 6),
            "psnr_db": 100.0 if infinite else round(float(psnr_text), 3),
            "psnr_infinite": infinite}


def _bitrate_kbps(path: Path, duration: float) -> int:
    """Video stream bitrate in kbps, falling back to the file size over duration.

    The stream's own ``bit_rate`` is the encoder's output and is what the ladder
    compares; the fallback includes container overhead, which for a video-only
    file is small but not zero, and exists because a stream that ffmpeg does not
    tag should not read as zero bitrate.
    """
    raw = _kv(_ffprobe(path, "v:0", "bit_rate"))
    if raw.get("bit_rate"):
        return round(int(raw["bit_rate"]) / 1000)
    if duration > 0:
        return round(path.stat().st_size * 8 / duration / 1000)
    return 0


def _geometry(path: Path) -> dict:
    dims = _kv(_ffprobe(path, "v:0", "width,height,display_aspect_ratio"))
    w, h = int(dims.get("width", 0)), int(dims.get("height", 0))
    return {"width": w, "height": h,
            "dar": dims.get("display_aspect_ratio"),
            # The two presets no longer share one RTF figure because they no
            # longer share one output size: 16:9 ships 0.92 Mpx, 9:16 ships
            # 0.29 Mpx on the same source. Per-pixel cost is reported so a
            # reader can see that the encoder is not the only term that moves.
            "megapixels": round(w * h / 1e6, 4)}


def _has_audio(path: Path) -> bool:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of",
             "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60)
        return bool(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


# ── disk: the lossless reference is the one genuinely large artifact ────────


def _worst_case_bytes(frames: int, out_w: int, out_h: int, entries: int) -> int:
    """An upper bound on what a *ladder* run will put on disk at once, before it
    does.

    The `-crf 0` reference is the term that matters: uncompressed-equivalent
    yuv420p is 1.5 bytes per pixel, so 240 frames at 1280x720 is ~330 MB. The
    reference is resident for the whole window, but the entries are not: each
    is unlinked as soon as it has been scored, so the peak is the reference
    plus one entry, never the reference plus all of them. Entries are H.264 and
    land far below a lossless rate, but bounding an entry at the pixel rate
    keeps this a true ceiling rather than a tight estimate — deliberately, since
    the host it protects has hit disk pressure before and a ceiling that can be
    exceeded is not a ceiling. The ``entries`` argument is kept for the call
    signature and ignored, because sizing the peak on the entry count is the
    mistake that made this refuse measurements that fit.

    The dimensions are the *output's*, measured by a one-frame probe, not the
    source's. The vertical chain crops to roughly a third of the source's
    pixels, and sizing the estimate on the source refused a run that actually
    needed a third of it — the gate would have protected the disk by preventing
    the measurement.
    """
    per_frame = out_w * out_h * 3 // 2
    _ = entries  # the entry count does not change the peak: see above
    return frames * per_frame * 2


def _ship_bytes(source: dict, seconds: float, renders: int) -> int:
    """An upper bound on what a *ship* run will put on disk.

    The ship path writes H.264 deliverables, not lossless references, so the
    bytes-per-pixel figure the ladder uses is wrong here by close to three
    orders of magnitude. A re-encode of a window of the source cannot weigh much
    more than that window of the source, which is the source's own measured
    bitrate; 3x over that covers the audio the deliverable also carries and the
    fact that a hard source compresses less the second time. Every render writes
    the same per-orientation path and the previous one is unlinked, so the peak
    is one deliverable regardless of how many repeats are timed — multiplying by
    the repeat count refused a run whose real peak was ~56 MB for needing 6 GB.
    """
    if not source.get("size_bytes") or not source.get("duration"):
        # No basis for an estimate: fall back to the ladder's ceiling, which is
        # large but is not invented.
        return _worst_case_bytes(int(seconds * 30), source["width"],
                                 source["height"], entries=0)
    per_second = source["size_bytes"] / source["duration"]
    _ = renders
    return int(per_second * seconds * 3)


def _probe_output_dims(source: Path, start: float, graph: str,
                       composed: bool, work: Path) -> tuple[int, int]:
    """Render one frame and measure what came out.

    The vertical chain's crop width is decided inside `engine.reframe` from the
    scene, so the output size is not derivable from the source and the preset
    alone. Encoding a single frame costs ~1 MB and removes a 3.7x assumption
    from the only estimate that gates the run.
    """
    probe = work / "probe.mp4"
    work.mkdir(parents=True, exist_ok=True)
    _encode(source, start, frames=1, span=1.0 / 30.0, graph=graph, out=probe,
            encoder=["-preset", "ultrafast", "-crf", "0", "-pix_fmt", "yuv420p"],
            composed=composed)
    try:
        dims = _geometry(probe)
        return dims["width"], dims["height"]
    finally:
        probe.unlink(missing_ok=True)


def _require_disk(need: int, where: Path, force: bool) -> None:
    free = shutil.disk_usage(where).free
    if free >= need * 2:
        return
    msg = (f"need ~{need / 1e6:.0f} MB of scratch for this run "
           f"({need * 2 / 1e6:.0f} MB headroom requested) "
           f"and only {free / 1e6:.0f} MB is free on {where}")
    if force:
        print(f"[render_bench] WARNING: {msg}; continuing because --force-disk "
              f"was passed — ffmpeg will fail if it actually runs out",
              file=sys.stderr)
    else:
        raise SystemExit(f"{msg}\npass --force-disk to run anyway")


# ── ship: the deliverables the pipeline actually produces ────────────────────


def _measure_ship(source: Path, start: float, end: float, vertical: bool,
                  ass: Path | None, fps: float, repeat: int, warmup: int,
                  out: Path) -> dict:
    from engine.render import _frame_count, _render_one

    name = "9:16" if vertical else "16:9"
    walls: list[float] = []
    measured: dict = {}
    try:
        # One untimed render first: the first encode on an idle machine pays
        # for the page cache and the frequency ramp, and charging that to the
        # number makes a cold host look slower than a warm one for a reason
        # that has nothing to do with the encoder.
        with _hush_ffmpeg():
            for _ in range(max(0, warmup)):
                _render_one(source, start, end, out, ass, vertical, fps)
            for _ in range(max(1, repeat)):
                t0 = time.perf_counter()
                _render_one(source, start, end, out, ass, vertical, fps)
                walls.append(time.perf_counter() - t0)
        # Probed while the file still exists, so a run that deletes its output
        # keeps the geometry and the bitrate it was measured on.
        frames, span = _frame_count(start, end, fps)
        # The duration the file actually plays, which is what RTF and bitrate
        # are a rate *over*. See _emitted_duration: the requested span is only
        # this when the requested fps is the source's.
        emitted = _emitted_duration(out, frames)
        denom = emitted or span
        measured = {
            "output": _geometry(out),
            "bitrate_kbps": _bitrate_kbps(out, denom),
            "audio_in_deliverable": _has_audio(out),
        }
    finally:
        if not KEEP:
            out.unlink(missing_ok=True)

    best = min(walls)
    return {
        "orientation": name,
        "vertical": vertical,
        "start": start, "end": end,
        "frames": frames,
        "content_seconds": round(denom, 4),
        "fps_requested": fps,
        "repeats": len(walls),
        "wall_seconds": [round(w, 2) for w in walls],
        "best_wall_seconds": round(best, 2),
        # RTF is encode wall over content produced. The reported figure is the
        # best of N: that is the maximum throughput the settings can deliver on
        # this host, which is what a capacity model wants, and it is the only
        # one of the three that two different runs can be expected to reproduce.
        "rtf": round(best / denom, 4),
        "median_rtf": round(_median(walls) / denom, 4),
        "worst_rtf": round(max(walls) / denom, 4),
        "spread": round(max(walls) / best, 3),
        # The denominator is probed rather than assumed, so a run whose --fps was
        # not the source's records the discrepancy instead of quietly carrying a
        # rate computed over time the file does not contain.
        "span_requested_seconds": round(span, 4),
        "span_emitted_seconds": round(emitted, 4),
        # A doubled wall on a source with audio means the output-side seek retry
        # in _render_one fired — input seeking dropped the stream on this
        # container, and the fallback decode-and-discard path is real shipped
        # cost the RTF has just absorbed without naming it.
        **measured,
        "filter": _filter_for(source, start, end, vertical, ass),
    }


def _filter_for(source: Path, start: float, end: float, vertical: bool,
                ass: Path | None = None) -> str:
    """The filtergraph the production path would build for this window.

    Recorded verbatim rather than described, because the geometry decisions
    live in it — the tracked crop width, the pad that produces an exact 16:9 —
    and a re-measurement that silently changed one of them would read as the
    encoder having gotten slower.

    ``ass`` matters here for the same reason: with a caption file the shipped
    graph has a ``subtitles=`` term folded into it, and recording the geometry
    alone would make the record describe a deliverable that carries no captions.
    The term is built by the render's own helper rather than reconstructed, so
    the record cannot drift from what was rendered.
    """
    if vertical:
        from engine.reframe import vertical_reframe_filter
        from engine.render import _subtitle_terms
        return vertical_reframe_filter(source, start, end,
                                       tail=tuple(_subtitle_terms(ass)))
    from engine.render import _subtitle_terms
    from engine.reframe import landscape_filter
    terms = _subtitle_terms(ass)
    return ",".join([landscape_filter(source), *terms]) if terms else landscape_filter(source)




# ── ladder: the encoder decision, scored against a lossless reference ────────


def _measure_ladder(source: Path, start: float, frames: int, entries: list,
                    vertical: bool, fps: float, work: Path,
                    graph: str) -> dict:
    from engine.reframe_scenes import is_composed

    composed = is_composed(graph)
    span = frames / fps

    reference = work / "reference_crf0.mp4"
    # The reference is encoded losslessly and then *decoded* for comparison, so
    # its own encoder settings cannot affect the score; a fast preset keeps the
    # reference cheap to produce without perturbing any number.
    _encode(source, start, frames, span, graph, reference,
            ["-preset", "veryfast", "-crf", "0", "-pix_fmt", "yuv420p"],
            composed=composed)
    # The emitted duration is read from the reference after encoding: it carries
    # the same -frames:v as every entry and plays at the source's fps, so it is
    # the duration the RTF and bitrate below are a rate *over*. ``span`` is only
    # equal to it when --fps is the source's — see _emitted_duration — and is
    # kept in the record to show the request rather than trusted as the answer.
    denom = _emitted_duration(reference, frames) or span

    shipped_pair = _shipped_pair()
    rows: list[dict] = []
    try:
        for preset, crf in entries:
            entry = work / f"{preset}_crf{crf}.mp4"
            t0 = time.perf_counter()
            _encode(source, start, frames, span, graph, entry,
                    ["-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"],
                    composed=composed)
            wall = time.perf_counter() - t0
            score = _score(entry, reference, frames)
            rows.append({
                "preset": preset, "crf": crf,
                "shipped": (preset, str(crf)) == shipped_pair,
                **score,
                "kbps": _bitrate_kbps(entry, denom),
                "wall_seconds": round(wall, 3),
                "rtf": round(wall / denom, 4),
                "output": _geometry(entry),
            })
            if not KEEP:
                entry.unlink(missing_ok=True)
    finally:
        if not KEEP:
            reference.unlink(missing_ok=True)

    shipped = next((r for r in rows if r["shipped"]), None)
    return {
        "filter": graph,
        # Which window of the sweep this is. Without it a sweep's per_window list
        # is three anonymous tables and the only way to name one is its position
        # — which is also the only way to get the pairing below wrong.
        "start_seconds": round(start, 3),
        "frames": frames,
        "content_seconds": round(denom, 4),
        # The requested span is kept beside the emitted one because a gap between
        # them is a --fps that was not the source's, and the RTF above is
        # computed on the emitted figure either way.
        "span_requested_seconds": round(span, 4),
        "span_emitted_seconds": round(denom, 4),
        "orientation": "9:16" if vertical else "16:9",
        "reference_encoder": ["libx264", "preset veryfast", "crf 0 (lossless)"],
        "shipped_pair": list(shipped_pair) if shipped_pair else None,
        "rows": rows,
        # The two numbers the encoder decision rests on, pre-computed so a
        # reader does not have to re-derive them from the table.
        "quality_vs_cost": _deltas(rows, shipped),
    }


def _deltas(rows: list[dict], shipped: dict | None) -> dict:
    """The dB gap and bitrate ratio of every entry against the shipped one.

    These are the only figures that survive a change of content window — the
    absolute dB and kbps columns are window properties, and a 3.5 dB swing
    between windows is wider than the gaps between most entries. If a margin
    here inverts, the decision in `_encoder_args`' docstring has inverted with
    it and the docstring is what has to move.
    """
    if shipped is None:
        return {"note": "the shipped encoder is not in the ladder that was run"}
    out = {}
    for row in rows:
        key = f"{row['preset']}/crf{row['crf']}"
        out[key] = {
            "psnr_db_vs_shipped": round(row["psnr_db"] - shipped["psnr_db"], 3),
            "ssim_vs_shipped": round(row["ssim"] - shipped["ssim"], 6),
            "kbps_ratio_vs_shipped": round(row["kbps"] / max(1, shipped["kbps"]), 3),
            "rtf_ratio_vs_shipped": round(row["rtf"] / max(1e-6, shipped["rtf"]), 3),
        }
    return out


def _encoder_flags() -> list[str]:
    """The live production encoder, read from the module the pipeline imports.

    Reading it rather than hardcoding it is the whole point: the defect this
    tool exists for was a doc figure that disagreed with this function, and a
    bench that hardcoded the answer would disagree the same way.
    """
    from engine.render import _encoder_args
    return _encoder_args()


def _shipped_pair() -> tuple[str, str] | None:
    """The (preset, crf) the pipeline ships, parsed out of `_encoder_args`.

    That function returns x264 *flags* — ``["-preset", "slow", "-crf", "18",
    ...]`` — and a ladder entry is a ``(preset, crf)`` pair, so the two cannot
    be compared directly. Parsing here rather than comparing against a hardcoded
    ``("slow", "18")`` keeps the shipped marker honest if the flags ever move.
    """
    flags = _encoder_flags()
    try:
        preset = flags[flags.index("-preset") + 1]
        crf = flags[flags.index("-crf") + 1]
    except (ValueError, IndexError):
        return None
    return preset, crf


# ── reporting ────────────────────────────────────────────────────────────────


# The rows this regenerates, named once so the run record and the stdout agree.
REGENERATES_SHIP = [
    "docs/BUSINESS_MODEL.md §1 — the 16:9 and 9:16 render RTF rows",
    "docs/BUSINESS_MODEL.md §4 — the throughput model that multiplies them",
    "docs/AI_EXECUTION_STATE.md — the entry dated 2026-09-28",
    "docs/EVALUATION.md §6 — the render RTF cross-check rows",
    "docs/TIME_SAVINGS.md §3 and §5 — the compute figures",
]
REGENERATES_LADDER = [
    "engine/render.py — the ladder table in _encoder_args' docstring",
    "tests/test_render.py:134 — the quality figures in the pinned-settings test",
]


def _write_record(tag: str | None, record: dict) -> Path | None:
    if tag is None:
        return None
    # A tag names committed evidence, and reusing one replaces the record it
    # points at with no prompt and no backup — the evidence a quoted figure
    # rests on would be gone before anyone noticed. A path with a separator in
    # it would also escape RUNS/ entirely, so both are refused here rather than
    # silently written around.
    if any(part in ("", ".", "..") for part in tag.split("/")) or tag.startswith("/"):
        raise SystemExit(
            f"refusing to write a run record to {tag!r} — a tag names a file "
            f"under corpus/render/runs/, not a path")
    path = RUNS / f"{tag}.json"
    if path.exists():
        raise SystemExit(
            f"{path} already exists — the committed evidence it holds would be "
            f"overwritten. Move it aside first, or use a new tag.")
    RUNS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return path


def _print_ship(record: dict) -> None:
    print(f"[ship] {record['source']['path']}")
    print(f"[ship] {record['source']['width']}x{record['source']['height']} "
          f"{record['source']['fps']:.3f} fps {record['source']['pix_fmt']} "
          f"sar {record['source']['sar']}"
          f"{'' if record['source']['has_audio'] else ' (no audio)'}")
    enc = " ".join(record["encoder"])
    print(f"[ship] encoder (live from engine.render): {enc}")
    print(f"[ship] ffmpeg {record['conditions']['ffmpeg_version']}, "
          f"{record['conditions']['cpu_count']} cores, "
          f"load {record['conditions']['load_average']}, "
          f"commit {record['conditions']['repo_commit']}")
    for r in record["renders"]:
        g = r["output"]
        print(f"[ship] {r['orientation']:4s} {g['width']}x{g['height']} "
              f"({g['megapixels']} Mpx) dar {g['dar']}: "
              f"RTF best {r['rtf']:.3f} / median {r['median_rtf']:.3f} / "
              f"worst {r['worst_rtf']:.3f} "
              f"(spread {r['spread']:.2f}x over {r['repeats']} runs, "
              f"{r['frames']} frames)")
        if r.get("bitrate_kbps"):
            print(f"[ship]       {r['bitrate_kbps']} kbps"
                  f"{'' if r.get('audio_in_deliverable') else ', no audio in the deliverable'}")
    if len(record["renders"]) == 2:
        a, b = record["renders"]
        if a["rtf"] and b["rtf"]:
            print(f"[ship] {a['orientation']} is {a['rtf'] / b['rtf']:.2f}x "
                  f"{b['orientation']}'s RTF on the same source and encoder")
    print("[ship] if any of these disagree with a doc, the doc is stale:")
    for line in REGENERATES_SHIP:
        print(f"[ship]   {line}")


def _print_ladder(record: dict) -> None:
    print(f"[ladder] {record['source']['path']}")
    if record.get("kind") == "ladder_sweep":
        _print_ladder_sweep(record)
    else:
        _print_ladder_single(record)
    print("[ladder] luma only; SSIM/PSNR are proxies that agree with each other "
          "and can both miss what an eye catches")
    print("[ladder] if the table disagrees with a docstring, the docstring is "
          "stale:")
    for line in REGENERATES_LADDER:
        print(f"[ladder]   {line}")


def _print_ladder_single(record: dict) -> None:
    print(f"[ladder] {record['source']['width']}x{record['source']['height']} "
          f"{record['source']['fps']:.3f} fps, {record['frames']} frames "
          f"({record['content_seconds']}s) from t={record['starts'][0]:.0f}s "
          f"through the {record['orientation']} "
          f"chain, scored against a -crf 0 reference of the same chain")
    print(f"[ladder] {'preset':9s} {'crf':>3s} {'kbps':>7s} {'SSIM':>9s} "
          f"{'PSNR':>7s} {'RTF':>7s} {'dB vs ship':>10s} {'kbp ship':>9s}")
    for row in record["rows"]:
        d = record["quality_vs_cost"].get(f"{row['preset']}/crf{row['crf']}", {})
        mark = " <- shipped" if row["shipped"] else ""
        # 100.00 dB is the sentinel for an identical-to-reference entry, not a
        # measurement; printing it bare would invite a reader to quote it.
        psnr = "  inf" if row.get("psnr_infinite") else f"{row['psnr_db']:7.2f}"
        print(f"[ladder] {row['preset']:9s} {row['crf']:>3d} {row['kbps']:>7d} "
              f"{row['ssim']:>9.4f} {psnr} {row['rtf']:>7.3f} "
              f"{d.get('psnr_db_vs_shipped', 0):>+10.2f} "
              f"{d.get('kbps_ratio_vs_shipped', 0):>8.2f}x{mark}")


def _fmt_range(vals, spec: str) -> str:
    """Print a [min, max] pair as `a-b`, or `a` when the sweep did not move."""
    lo, hi = vals
    if lo == hi:
        return format(lo, spec)
    return f"{format(lo, spec)}-{format(hi, spec)}"


def _print_ladder_sweep(record: dict) -> None:
    starts = ", ".join(f"{s:.0f}" for s in record["starts"])
    print(f"[ladder] {record['source']['width']}x{record['source']['height']} "
          f"{record['source']['fps']:.3f} fps, {record['frames']} frames "
          f"({record['content_seconds']}s) at t={starts}s through the "
          f"{record['orientation']} chain; every field below is the range the "
          f"windows spanned")
    print(f"[ladder] {'preset':9s} {'crf':>3s} {'kbps':>13s} {'SSIM':>19s} "
          f"{'PSNR':>14s} {'dB vs ship':>13s} {'kbp ship':>11s}")
    for row in record["rows"]:
        d = record["quality_vs_cost"].get(f"{row['preset']}/crf{row['crf']}", {})
        mark = " <- shipped" if row["shipped"] else ""
        print(f"[ladder] {row['preset']:9s} {row['crf']:>3d} "
              f"{_fmt_range(row['kbps'], '>7d'):>13s} "
              f"{_fmt_range(row['ssim'], '.4f'):>19s} "
              f"{_fmt_range(row['psnr_db'], '.2f'):>14s} "
              f"{_fmt_range(d.get('psnr_db_vs_shipped', [0, 0]), '+.2f'):>13s} "
              f"{_fmt_range(d.get('kbps_ratio_vs_shipped', [1, 1]), '.2f') + 'x':>11s}"
              f"{mark}")


# ── commands ─────────────────────────────────────────────────────────────────


def cmd_ship(argv: list[str]) -> int:
    global KEEP
    p = argparse.ArgumentParser(
        description="Render the shipped 16:9 and 9:16 deliverables and report "
                    "the real-time factor of each.")
    p.add_argument("video", type=Path, nargs="?", default=DEFAULT_SOURCE)
    p.add_argument("--start", type=float, default=DEFAULT_WINDOW[0])
    p.add_argument("--end", type=float, default=DEFAULT_WINDOW[1])
    p.add_argument("--fps", type=float, default=30.0,
                   help="the grid the exported timeline stamps cuts on")
    p.add_argument("--ass", type=Path, default=None,
                   help="burn this ASS into the deliverable; by default none, "
                        "and the overhead is within run-to-run noise "
                        "(16:9 0.8284 vs 0.8221, 9:16 0.3562 vs 0.3567 RTF "
                        "best-of-3, see corpus/render/runs/pentagon-30s.json "
                        "and pentagon-30s-ass.json)")
    p.add_argument("--repeat", type=int, default=3,
                   help="timed renders per orientation; the reported RTF is "
                        "the best, and the spread is printed")
    p.add_argument("--warmup", type=int, default=1,
                   help="untimed renders before the clock starts")
    p.add_argument("--orientations", default="16x9,9x16",
                   help="comma list of 16x9 and/or 9x16")
    p.add_argument("--keep", action="store_true",
                   help="leave the rendered MP4s in the scratch dir")
    p.add_argument("--force-disk", action="store_true")
    p.add_argument("--tag", default=None,
                   help="write the run record to corpus/render/runs/<tag>.json")
    args = p.parse_args(argv)
    KEEP = args.keep

    if not args.video.is_file():
        raise SystemExit(f"source not found: {args.video}")
    if args.end <= args.start:
        raise SystemExit(f"--end ({args.end}) must be after --start ({args.start})")

    wanted = [o.strip() for o in args.orientations.split(",") if o.strip()]
    unknown = [o for o in wanted if o not in ("16x9", "9x16")]
    if unknown:
        raise SystemExit(f"unknown orientations {unknown}; expected 16x9 and/or 9x16")
    order = [("16x9", False), ("9x16", True)]
    pairs = [(label, vert) for label, vert in order if label in wanted]

    from engine.render import require_ffmpeg
    require_ffmpeg()
    # Sampled before the renders so the load figure says whether the host was
    # idle when the clock started, not how busy the benchmark made it.
    cond = conditions()
    src = probe_source(args.video)
    # `ladder` has its own dir; sharing one meant either run deleted the other's
    # --keep artifacts on startup.
    work = SCRATCH / "scratch-ship"
    # Created before the disk gate probes it, so a first run with an absent
    # scratch dir is not a FileNotFoundError. The gate is on the filesystem the
    # work lands in, which is what the caller needs to know has room.
    work.mkdir(parents=True, exist_ok=True)
    # The deliverables are H.264, so they are bounded by the source's own
    # bitrate over the window, not by uncompressed pixel volume — the latter
    # overestimates this run by close to three orders of magnitude and would
    # have refused a measurement that needs tens of MB.
    _require_disk(_ship_bytes(src, args.end - args.start,
                              len(pairs) * (args.repeat + max(0, args.warmup))),
                  work, args.force_disk)

    encoder = _encoder_flags()
    # Skipped under --keep: the deliverables are what that flag asks to leave
    # behind, and clearing here deleted them the moment the next run started.
    if not KEEP:
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
    renders = []
    try:
        for label, vertical in pairs:
            out = work / f"ship_{label}.mp4"
            renders.append(_measure_ship(args.video, args.start, args.end,
                                         vertical, args.ass, args.fps,
                                         args.repeat, args.warmup, out))
    finally:
        if not KEEP:
            shutil.rmtree(work, ignore_errors=True)

    record = {
        "kind": "ship",
        "encoder": encoder,
        "source": src,
        "conditions": cond,
        "ass": str(args.ass) if args.ass else None,
        "renders": renders,
        "regenerates": REGENERATES_SHIP,
    }
    path = _write_record(args.tag, record)
    _print_ship(record)
    if path is not None:
        print(f"[ship] wrote {path}")
    _warn_spread(renders)
    return 0


def cmd_ladder(argv: list[str]) -> int:
    global KEEP
    p = argparse.ArgumentParser(
        description="Score encoder settings against a lossless reference of "
                    "the same filter chain.")
    p.add_argument("video", type=Path, nargs="?", default=DEFAULT_SOURCE)
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--frames", type=int, default=240,
                   help="frames per entry; 240 is the table the docstring carries")
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--entries", default=None,
                   help="comma list of preset:crf; default is the documented ladder")
    p.add_argument("--orientation", choices=["16x9", "9x16"], default="9x16",
                   help="the ladder in the docs is the 9:16 chain")
    p.add_argument("--windows", default=None,
                   help="comma list of start offsets to sweep, e.g. 0,300,600; "
                        "overrides --start and reports the range per entry")
    p.add_argument("--keep", action="store_true")
    p.add_argument("--force-disk", action="store_true")
    p.add_argument("--tag", default=None)
    args = p.parse_args(argv)
    KEEP = args.keep

    if not args.video.is_file():
        raise SystemExit(f"source not found: {args.video}")
    entries = LADDER if args.entries is None else _parse_entries(args.entries)
    if not entries:
        raise SystemExit("no entries to score")

    from engine.render import require_ffmpeg
    from engine.reframe_scenes import is_composed
    require_ffmpeg()
    src = probe_source(args.video)
    # Sampled before the renders so the load figure says whether the host was
    # idle when the clock started, not how busy the benchmark made it.
    cond = conditions()

    vertical = args.orientation == "9x16"
    if args.windows:
        starts = _parse_floats(args.windows)
        if not starts:
            raise SystemExit("--windows needs at least one start offset")
    else:
        starts = [args.start]

    work = SCRATCH / "scratch"
    work.mkdir(parents=True, exist_ok=True)
    # A ladder run overwrites its own per-entry filenames, so clearing is only
    # about reclaiming space, and it is skipped under --keep: that flag exists to
    # leave the deliverables behind for inspection, and an unconditional rmtree
    # here deleted every kept artifact the moment the next run started.
    if not KEEP:
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
    # The chain's crop width is decided per scene, so the disk ceiling and the
    # filtergraph are rebuilt per window rather than once for the sweep.
    per_window: list[dict] = []
    try:
        for start in starts:
            graph = _filter_for(args.video, start,
                                start + args.frames / args.fps, vertical)
            out_w, out_h = _probe_output_dims(args.video, start, graph,
                                              is_composed(graph), work)
            _require_disk(_worst_case_bytes(args.frames, out_w, out_h,
                                            entries=len(entries)),
                          work, args.force_disk)
            per_window.append(_measure_ladder(args.video, start, args.frames,
                                              entries, vertical, args.fps,
                                              work, graph))
    finally:
        if not KEEP:
            shutil.rmtree(work, ignore_errors=True)

    if len(per_window) == 1:
        record = {"kind": "ladder", "source": src, "starts": starts,
                  "conditions": cond, **per_window[0]}
    else:
        record = {"kind": "ladder_sweep", "source": src,
                  "conditions": cond, "starts": starts,
                  **_aggregate_sweep(per_window)}

    record["regenerates"] = REGENERATES_LADDER
    path = _write_record(args.tag, record)
    _print_ladder(record)
    if path is not None:
        print(f"[ladder] wrote {path}")
    return 0


def _mm(vals) -> list:
    """[min, max] of a sequence, the form the sweep reports every field in."""
    vals = list(vals)
    return [min(vals), max(vals)]


def _median(vals) -> float:
    """The median, averaging the two middle samples of an even-length run.

    A ``--repeat 4`` has no single middle sample; picking one index and calling
    it "the median" reports a value that is not the median, and the number feeds
    a cost model rather than a label.
    """
    ordered = sorted(vals)
    lo, hi = (len(ordered) - 1) // 2, len(ordered) // 2
    return (ordered[lo] + ordered[hi]) / 2


def _parse_floats(spec: str) -> list[float]:
    try:
        return [float(x) for x in spec.split(",") if x.strip()]
    except ValueError:
        raise SystemExit(f"could not parse --windows {spec!r}; expected e.g. 0,300,600")


def _aggregate_sweep(per_window: list[dict]) -> dict:
    """One row per entry carrying its min and max across the swept windows.

    A single window's kbps and PSNR are not properties of the encoder: crf
    spends its budget on the scene in front of it, and on the same source, chain
    and reference method the shipped entry moved 932->1345 kbps and 46.83->43.74
    dB across three 8-second windows. A table built from one window presents
    that variance as if it were the encoder's. What does survive the sweep is
    the *gap* to the shipped entry and the *ratio* to it, which is why they are
    aggregated too and printed as ranges rather than points.
    """
    by_entry: dict[tuple[str, int], list[dict]] = {}
    order: list[tuple[str, int]] = []
    for window in per_window:
        for row in window["rows"]:
            key = (row["preset"], row["crf"])
            if key not in by_entry:
                by_entry[key] = []
                order.append(key)
            by_entry[key].append(row)

    rows = []
    for key in order:
        preset, crf = key
        group = by_entry[key]
        rows.append({
            "preset": preset, "crf": crf,
            "shipped": any(g["shipped"] for g in group),
            "windows": len(group),
            "kbps": _mm(g["kbps"] for g in group),
            "ssim": _mm(g["ssim"] for g in group),
            "psnr_db": _mm(g["psnr_db"] for g in group),
            "rtf": _mm(g["rtf"] for g in group),
            "output": group[0]["output"],
        })

    quality_vs_cost: dict[str, dict] = {}
    for row in rows:
        gaps, ratios, rtf_ratios = [], [], []
        for window in per_window:
            shipped = next((g for g in window["rows"] if g["shipped"]), None)
            mine = next((g for g in window["rows"]
                         if g["preset"] == row["preset"] and g["crf"] == row["crf"]),
                        None)
            if shipped is not None and mine is not None:
                gaps.append(mine["psnr_db"] - shipped["psnr_db"])
                ratios.append(mine["kbps"] / max(1, shipped["kbps"]))
                rtf_ratios.append(mine["rtf"] / max(1e-6, shipped["rtf"]))
        if gaps:
            quality_vs_cost[f"{row['preset']}/crf{row['crf']}"] = {
                "psnr_db_vs_shipped": [round(x, 3) for x in _mm(gaps)],
                "kbps_ratio_vs_shipped": [round(x, 3) for x in _mm(ratios)],
                # The wall-clock saving is the one figure a reader reaches for
                # first, so it is pre-computed like the other two. It has to be
                # paired *per window* — the min/max ``rtf`` columns above are
                # independent, so dividing one entry's fastest window by the
                # other's slowest reports a speedup no window ever showed
                # (measured: 0.646 paired against 0.656 from min/min, which is a
                # different window's answer presented as the range's edge).
                "rtf_ratio_vs_shipped": [round(x, 3) for x in _mm(rtf_ratios)],
            }

    return {
        "filter": per_window[0]["filter"],
        "frames": per_window[0]["frames"],
        "content_seconds": per_window[0]["content_seconds"],
        "orientation": per_window[0]["orientation"],
        "reference_encoder": per_window[0]["reference_encoder"],
        "shipped_pair": per_window[0].get("shipped_pair"),
        "rows": rows,
        "quality_vs_cost": quality_vs_cost,
        "per_window": per_window,
    }


def _parse_entries(spec: str) -> list[tuple[str, int]]:
    out = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        preset, _, crf = item.partition(":")
        try:
            out.append((preset.strip(), int(crf)))
        except ValueError:
            raise SystemExit(f"could not parse entry {item!r}; expected preset:crf")
    return out


def _warn_spread(renders: list[dict]) -> None:
    """A spread much above the documented ~1.7x means the host was not idle and
    the best-of-N figure is the machine's good moment, not its typical one."""
    for r in renders:
        if r["spread"] > 2.0:
            print(f"[ship] WARNING: {r['orientation']} spread {r['spread']:.2f}x "
                  f"across {r['repeats']} runs — the host was loaded; the best "
                  f"figure is optimistic and median_rtf is the honest one",
                  file=sys.stderr)


def cmd_report(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Print all recorded render runs as a table.")
    p.add_argument("--runs", type=Path, default=RUNS)
    args = p.parse_args(argv)
    if not args.runs.is_dir():
        print(f"[report] no runs in {args.runs}")
        return 2
    files = sorted(args.runs.glob("*.json"))
    if not files:
        print(f"[report] no run records in {args.runs}")
        return 2
    print(f"{'run':<34}{'source':>22}{'window':>11}{'orient':>8}"
          f"{'RTF':>8}{'median':>8}{'WxH':>13}{'kbps':>7}")
    for f in files:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[report] {f.name}: unreadable ({exc})")
            continue
        if r.get("kind") == "ship":
            for render in r["renders"]:
                g = render.get("output") or {}
                src = Path(r["source"]["path"]).stem[:22]
                print(f"{f.stem[:34]:<34}{src:>22}"
                      f"{render['start']:>5.0f}-{render['end']:<5.0f}"
                      f"{render['orientation']:>8}{render['rtf']:>8.3f}"
                      f"{render['median_rtf']:>8.3f}"
                      f"{(str(g.get('width')) + 'x' + str(g.get('height'))):>13}"
                      f"{render.get('bitrate_kbps') or 0:>7}")
        elif r.get("kind") in ("ladder", "ladder_sweep"):
            sweep = r.get("kind") == "ladder_sweep"
            starts = r.get("starts") or []
            window = (",".join(f"{s:.0f}" for s in starts)[:11]
                      if starts else "ladder")
            for row in r["rows"]:
                mark = "*" if row["shipped"] else " "
                src = Path(r["source"]["path"]).stem[:22]
                rtf = row["rtf"]
                rtf_text = (f"{rtf[0]:.3f}-{rtf[1]:.3f}"
                            if sweep else f"{rtf:.3f}")
                kbps = row["kbps"]
                kbps_text = (f"{kbps[0]}-{kbps[1]}" if sweep else f"{kbps}")
                print(f"{(f.stem[:32] + mark):<34}{src:>22}"
                      f"{window:>11}{row['preset']:>8}"
                      f"{rtf_text:>16}{'':>0}"
                      f"{(str(row['output'].get('width')) + 'x' + str(row['output'].get('height'))):>13}"
                      f"{kbps_text:>9}")
        else:
            print(f"{f.name:<34}{'unknown record shape':>40}")
    print("[report] * = the shipped encoder entry")
    return 0


def main(argv: list[str] | None = None) -> int:
    cmds = {"ship": cmd_ship, "ladder": cmd_ladder, "report": cmd_report}
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in cmds:
        print(f"unknown command: {cmd} (expected one of {sorted(cmds)})",
              file=sys.stderr)
        return 2
    return cmds[cmd](rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
