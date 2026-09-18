"""SelekyT Python sidecar — JSON-RPC over stdin/stdout (one JSON object per line).

Mirrors the wire protocol of the Rust `selekyt-mech` sidecar so the Tauri
shell and tooling can speak to either implementation:

    request:  {"op": "<op>", "params": {...}}    # params optional
    response: {"ok": true, "op": "<op>", "data": ...}
              {"ok": false, "op": "<op>", "error": "..."}

This sidecar is the *intelligence* layer and never shells out to the Rust
binary. It offers the deterministic EditCore ops (segmentation, inversion)
plus the VAD analysis and the full audio stage with provenance fields
(`vad_model`, `fallback_reason`, `hysteresis`).
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from engine.captions import caption_presets
from engine import cut_algebra
from engine import timecode
from engine import fcpxml
from engine import jumpcut
from engine import vad as vad_mod
from engine.pipeline_audio import run_pipeline_audio
from selekyt import __version__

PROTOCOL = "selekyt-jsonrpc/1"
PROTOCOL_RUST_COMPAT = True  # same envelope as rust/sidecar/src/main.rs


# ── op handlers ──

def op_ping(params: dict) -> dict:
    backend = vad_mod.get_vad_backend()
    return {
        "pong": True,
        "protocol": PROTOCOL,
        "engine": "python",
        "version": __version__,
        "vad_backend": backend.name,
        "vad_model": getattr(backend, "vad_model", None),
    }


def op_segment(params: dict) -> dict:
    scores = params.get("scores")
    if not isinstance(scores, list):
        raise ValueError("'scores' must be an array")
    frame_ms = int(params.get("frame_ms", vad_mod.FRAME_MS))
    threshold = float(params.get("threshold", 0.5))
    min_speech_ms = int(params.get("min_speech_ms", 150))
    min_silence_ms = int(params.get("min_silence_ms", 100))
    release = params.get("release")
    time_offset = float(params.get("time_offset", 0.0))
    regions, meta = vad_mod.segment_with_meta(
        scores,
        frame_ms=frame_ms,
        threshold=threshold,
        release=release,
        min_speech_ms=min_speech_ms,
        min_silence_ms=min_silence_ms,
        time_offset=time_offset,
    )
    return {
        "segments": [{"start": r.start, "end": r.end} for r in regions],
        "meta": meta,
    }


def op_cutlist_from_segments(params: dict) -> dict:
    segments = params.get("segments")
    if not isinstance(segments, list):
        raise ValueError("'segments' must be an array")
    duration = float(params["source_duration"])
    pad = float(params.get("pad", 0.3))
    spans = cut_algebra.cutlist_from_intervals(
        [(float(s["start"]), float(s["end"])) for s in segments],
        source_duration=duration,
        pad=pad,
    )
    return {
        "intervals": [{"kind": s.kind, "start": s.start, "end": s.end} for s in spans],
        "source_duration": duration,
    }


def _regular_file_or_error(path: str, name: str) -> Path:
    """Resolve a caller-supplied path to a regular file.

    The sidecar is a local stdio process driven by trusted local tooling, so
    this is not an authorization boundary. It is still worth refusing a
    directory or a device file up front: passing one to ffmpeg yields a
    confusing deep-stack error, and a path that does not exist tells the
    operator nothing about which parameter was wrong.
    """
    if not path or not isinstance(path, str):
        raise ValueError(f"'{name}' must be a string")
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise ValueError(f"'{name}' is not a readable file: {path}")
    return resolved


def op_vad_analyze(params: dict) -> dict:
    path = params.get("path")
    if not path:
        raise ValueError("'path' must be a string")
    _regular_file_or_error(path, "path")
    force = params.get("force")
    backend = vad_mod.get_vad_backend(force=force)
    result = backend.analyze(Path(path))
    segments, meta = vad_mod.segment_with_meta(
        result.scores,
        result.sample_rate,
        result.frame_ms,
        threshold=float(params.get("threshold", 0.5)),
        min_speech_ms=int(params.get("min_speech_ms", 150)),
        min_silence_ms=int(params.get("min_silence_ms", 100)),
    )
    out: dict = {
        "backend": result.backend,
        "vad_model": result.vad_model,
        "fallback_reason": result.fallback_reason,
        "frame_ms": result.frame_ms,
        "sample_rate": result.sample_rate,
        "duration": round(result.duration, 3),
        "n_frames": len(result.scores),
        "segments": [{"start": r.start, "end": r.end} for r in segments],
        "meta": meta,
    }
    if params.get("return_scores"):
        out["scores"] = [round(float(s), 6) for s in result.scores]
    return out


def op_detect_rate(params: dict) -> dict:
    rate = timecode.detect_rate(float(params["fps"]), bool(params.get("drop_frame", False)))
    return {
        "nominal_fps": rate.nominal_fps,
        "is_drop_frame": rate.is_drop_frame,
        "frame_duration_num": rate.frame_duration_num,
        "frame_duration_den": rate.frame_duration_den,
    }


def op_parse_smpte(params: dict) -> dict:
    return {"seconds": timecode.parse_smpte(params.get("tc"), float(params["fps"]))}


def op_seconds_to_rational(params: dict) -> dict:
    rate = timecode.detect_rate(float(params["fps"]), bool(params.get("drop_frame", False)))
    return {"rational": timecode.seconds_to_rational(float(params["seconds"]), rate)}


def op_track_offset_from_timecode(params: dict) -> dict:
    """Where a linked track sits on the timeline when both it and the
    reference carry embedded timecode (AutoCut's ``open_track`` behavior)."""
    return {
        "offset": timecode.track_offset_from_timecode(
            params.get("reference_timecode"),
            params.get("track_timecode"),
            float(params["fps"]),
        )
    }


def _track_from_params(t: dict) -> fcpxml.Track:
    return fcpxml.Track(
        name=t["name"],
        duration=float(t["duration"]),
        has_video=bool(t.get("has_video", True)),
        has_audio=bool(t.get("has_audio", True)),
        start_timecode=t.get("start_timecode"),
        offset=float(t.get("offset", 0.0)),
        path=t.get("path"),
    )


def op_render_fcpxml(params: dict) -> dict:
    """FCPXML 1.11 with optional linked tracks, behavior-identical to the
    Rust sidecar's ``render_fcpxml`` op (asserted in test_sidecar_parity)."""
    wire = params["cutlist"]
    cutlist = jumpcut.cutlist_from_wire(
        wire["intervals"], wire["source_duration"]
    )
    xml = fcpxml.render(
        cutlist,
        fcpxml.FcpxmlParams(
            fps=float(params["fps"]),
            title=str(params["title"]),
            reference=_track_from_params(params["reference"]),
            linked=[_track_from_params(t) for t in params.get("linked", [])],
        ),
    )
    return {"xml": xml}


def _keeps_from_params(items: list) -> list[cut_algebra.Keep]:
    return [
        cut_algebra.Keep(
            start=float(k["start"]),
            end=float(k["end"]),
            disabled=bool(k.get("disabled", False)),
        )
        for k in items
    ]


def _edit_cutlist_from_params(cutlist: dict) -> cut_algebra.EditCutList:
    return cut_algebra.EditCutList(
        source_duration=float(cutlist["source_duration"]),
        intervals=[
            cut_algebra.EditCut(
                start=float(c["start"]),
                end=float(c["end"]),
                kind=str(c.get("kind", "remove")),
                disabled=bool(c.get("disabled", False)),
            )
            for c in cutlist.get("intervals", [])
        ],
    )


def op_omitted_intervals(params: dict) -> list[dict]:
    cutlist = _edit_cutlist_from_params(params["cutlist"])
    return [
        {"start": c.start, "end": c.end, "kind": c.kind, "disabled": c.disabled}
        for c in cut_algebra.omitted_intervals(cutlist)
    ]


def op_clamp_keep_edge(params: dict) -> dict:
    keeps = _keeps_from_params(params["keeps"])
    return {
        "clamped": cut_algebra.clamp_keep_edge(
            keeps,
            index=int(params["index"]),
            edge=str(params.get("edge", "in")),
            proposed=float(params["proposed"]),
            duration=float(params["duration"]),
        )
    }


def op_skip_target_for(params: dict) -> dict:
    omitted = [(float(i[0]), float(i[1])) for i in params["omitted"]]
    return {
        "target": cut_algebra.skip_target_for(
            omitted, time=float(params["time"]), lookahead=float(params["lookahead"])
        )
    }


def op_previous_keep_target(params: dict) -> dict:
    keeps = _keeps_from_params(params["keeps"])
    return {"target": cut_algebra.previous_keep_target(keeps, time=float(params["time"]))}


def op_next_keep_target(params: dict) -> dict:
    keeps = _keeps_from_params(params["keeps"])
    return {
        "target": cut_algebra.next_keep_target(
            keeps, time=float(params["time"]), duration=float(params["duration"])
        )
    }


def op_normalize_keeps(params: dict) -> list[dict]:
    keeps = _keeps_from_params(params["keeps"])
    return [
        {"start": k.start, "end": k.end, "disabled": k.disabled}
        for k in cut_algebra.normalize_keeps(keeps, float(params["duration"]))
    ]


def op_audio_analysis(params: dict) -> dict:
    media = params.get("media")
    project = params.get("project", "out/project")
    if not media:
        raise ValueError("'media' must be a string")
    _regular_file_or_error(media, "media")
    summary = run_pipeline_audio(
        Path(media),
        Path(project),
        words=None,
        source=params.get("source", str(media)),
        pacing={},
        min_speech_ms=int(params.get("min_speech_ms", 150)),
        min_silence_ms=int(params.get("min_silence_ms", 100)),
        silence_threshold=float(params.get("silence_threshold", 0.5)),
        silence_padding=float(params.get("silence_padding", 0.0)),
        keep_pauses=bool(params.get("keep_pauses", True)),
        remove_filler=bool(params.get("remove_filler", True)),
        export_fcpxml=bool(params.get("export_fcpxml", False)),
        vad_backend=params.get("vad_backend"),
        force=bool(params.get("force", False)),
    )
    return summary


def _pipeline_source(raw: str) -> str:
    """Accept a local file or an http(s) URL; refuse everything else.

    A ``file://`` URL or a bare drive letter is not a source the pipeline
    should fetch, and neither is an operator the shell would have to expand.
    """
    text = raw.strip()
    if not text:
        raise ValueError("'source' must be a local path or an http(s) URL")
    if "://" in text:
        scheme = text.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"unsupported source scheme '{scheme}'")
        return text
    # A local path, not a URL. Must exist — downloading is the caller's job
    # when they pass a URL, but a nonexistent local file is a user error we
    # can report before any model is loaded.
    _regular_file_or_error(text, "source")
    return text


@contextlib.contextmanager
def _caption_style(style):
    """Apply a caption style for the duration of one run, then restore it.

    The caption writer reads ``SELEKYT_CAPTION_STYLE`` at write time, so a caller
    without argv (the web shell) has no other way to choose one. An unknown name
    is refused: ``preset_for`` would fall back to ``minimal`` and a typo in a
    dropdown would silently render the wrong style.

    The sidecar is one long-lived process serving many requests, so the value is
    restored afterwards — without that, one ``noir`` run would make every later
    run noir and nothing in the request would say why.
    """
    if not style:
        yield
        return
    name = str(style).strip().lower()
    if name not in caption_presets():
        raise ValueError(
            f"unsupported caption_style '{name}'; expected one of "
            f"{', '.join(caption_presets())}"
        )
    previous = os.environ.get("SELEKYT_CAPTION_STYLE")
    os.environ["SELEKYT_CAPTION_STYLE"] = name
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("SELEKYT_CAPTION_STYLE", None)
        else:
            os.environ["SELEKYT_CAPTION_STYLE"] = previous


def op_run_pipeline(params: dict) -> dict:
    """The product's main path: transcribe, select, cut, render.

    ``op_audio_analysis`` is a diagnostic on one file; this runs the whole
    editorial pipeline and writes a project tree of renders, captions and
    reports. Returns ``report.json`` plus the artifact directories so a UI can
    link to them without rescanning the disk.
    """
    from selekyt.pipeline import run as run_pipeline

    with _caption_style(params.get("caption_style")):
        source = _pipeline_source(str(params.get("source") or ""))
        out_root = Path(str(params.get("out") or "out"))
        project = run_pipeline(
            source,
            out_root,
            model=str(params.get("model", "small")),
            language=params.get("language") or None,
            limit=int(params.get("limit", 6)),
            cookies=params.get("cookies") or None,
            chunk_minutes=int(params.get("chunk_minutes", 5)),
            beam_size=int(params.get("beam_size", 1)),
            cpu_threads=params.get("cpu_threads"),
            ollama_url=params.get("ollama_url") or None,
            audio_analysis=bool(params.get("audio_analysis", False)),
            vad_backend=params.get("vad_backend") or None,
            silence_threshold=float(params.get("silence_threshold", 0.5)),
            silence_padding=float(params.get("silence_padding", 0.0)),
            min_speech_ms=int(params.get("min_speech_ms", 150)),
            min_silence_ms=int(params.get("min_silence_ms", 100)),
            keep_pauses=bool(params.get("keep_pauses", True)),
            remove_filler=bool(params.get("remove_filler", True)),
            export_fcpxml=bool(params.get("export_fcpxml", False)),
        )
    report = json.loads((project / "report.json").read_text(encoding="utf-8"))
    # The report names directories; a UI needs the files inside them to link to.
    artifacts: list[str] = []
    for sub in ("shorts", "facebook", "captions", "timeline"):
        d = project / sub
        if d.is_dir():
            artifacts += sorted(str(p) for p in d.iterdir() if p.is_file())
    artifacts += [
        str(project / n)
        for n in ("report.json", "editorial_report.json", "pacing_report.json", "qa.json")
        if (project / n).is_file()
    ]
    return {"project": str(project), "artifacts": artifacts, "report": report}


_HANDLERS = {
    "ping": op_ping,
    "segment": op_segment,
    "cutlist_from_segments": op_cutlist_from_segments,
    "vad_analyze": op_vad_analyze,
    "audio_analysis": op_audio_analysis,
    "run_pipeline": op_run_pipeline,
    "detect_rate": op_detect_rate,
    "parse_smpte": op_parse_smpte,
    "seconds_to_rational": op_seconds_to_rational,
    "track_offset_from_timecode": op_track_offset_from_timecode,
    "render_fcpxml": op_render_fcpxml,
    # Editorial transport algebra (AutoCut src/lib/cuts.ts)
    "normalize_keeps": op_normalize_keeps,
    "omitted_intervals": op_omitted_intervals,
    "clamp_keep_edge": op_clamp_keep_edge,
    "skip_target_for": op_skip_target_for,
    "previous_keep_target": op_previous_keep_target,
    "next_keep_target": op_next_keep_target,
}


def handle(op: str, params: dict) -> Any:
    fn = _HANDLERS.get(op)
    if fn is None:
        raise ValueError(f"unknown op '{op}'")
    return fn(params)


@contextlib.contextmanager
def _progress_to_stderr():
    """Keep the JSON-RPC stdout stream pure.

    Engine stages emit human progress lines via ``print(..., flush=True)``.
    On the wire those would corrupt the one-JSON-object-per-line protocol, so
    for the duration of a request the process's ``sys.stdout`` is pointed at
    stderr. ``serve()``'s response writer holds the real stdout object and is
    unaffected, same convention as the Rust sidecar (stdout = JSON only).
    """
    real = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = real


def process_line(line: str) -> str:
    """Handle one request line, return one JSON response line."""
    try:
        req = json.loads(line)
    except ValueError:
        return json.dumps({"ok": False, "op": "", "error": "invalid JSON"})
    op = req.get("op", "") if isinstance(req, dict) else ""
    params = req.get("params") if isinstance(req, dict) else None
    if not isinstance(params, dict):
        params = {}
    try:
        with _progress_to_stderr():
            data = handle(op, params)
        return json.dumps({"ok": True, "op": op, "data": data}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 — JSON-RPC boundary converts to envelope
        return json.dumps({"ok": False, "op": op, "error": str(exc)}, ensure_ascii=False)


def serve(stream: TextIO, out: TextIO) -> int:
    """Run the sidecar loop. Returns the number of requests that failed.

    Every request is answered on the wire, so a failure is an ``ok: false``
    response rather than a dropped one; the count is what makes a caller that
    pipes a batch through the sidecar able to tell a clean run from a run that
    half-failed. An unwritable response stream is the one thing that cannot be
    reported on the wire and aborts immediately.
    """
    errors = 0
    for line in stream:
        if not line.strip():
            continue
        response = process_line(line.strip())
        out.write(response + "\n")
        out.flush()
        try:
            if not json.loads(response).get("ok", False):
                errors += 1
        except ValueError:
            # process_line always emits one JSON object; if it somehow did
            # not, the stream itself is broken and must not be ignored.
            return 1
    return errors


def serve_stdio() -> int:
    """Run the sidecar loop against this process's stdin/stdout."""
    return serve(sys.stdin, sys.stdout)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if "-v" in args or "--version" in args:
        print(f"selekyt-server {PROTOCOL} (python {__version__})")
        return 0
    return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())