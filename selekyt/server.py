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
import sys
from pathlib import Path
from typing import Any, TextIO

from engine import vad as vad_mod
from engine.cut_algebra import cutlist_from_intervals
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
    regions, meta = vad_mod.segment_with_meta(
        scores,
        frame_ms=frame_ms,
        threshold=threshold,
        release=release,
        min_speech_ms=min_speech_ms,
        min_silence_ms=min_silence_ms,
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
    spans = cutlist_from_intervals(
        [(float(s["start"]), float(s["end"])) for s in segments],
        source_duration=duration,
        pad=pad,
    )
    return {
        "intervals": [{"kind": s.kind, "start": s.start, "end": s.end} for s in spans],
        "source_duration": duration,
    }


def op_vad_analyze(params: dict) -> dict:
    path = params.get("path")
    if not path:
        raise ValueError("'path' must be a string")
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
    return {
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


def op_audio_analysis(params: dict) -> dict:
    media = params.get("media")
    project = params.get("project", "out/project")
    if not media:
        raise ValueError("'media' must be a string")
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


_HANDLERS = {
    "ping": op_ping,
    "segment": op_segment,
    "cutlist_from_segments": op_cutlist_from_segments,
    "vad_analyze": op_vad_analyze,
    "audio_analysis": op_audio_analysis,
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
    errors = 0
    for line in stream:
        if not line.strip():
            continue
        out.write(process_line(line.strip()) + "\n")
        out.flush()
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