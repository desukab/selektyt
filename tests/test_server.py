"""Python JSON-RPC sidecar protocol tests.

Pins the envelope contract (mirrored by the Rust `selekyt-mech` sidecar):
request `{"op", "params"}`, response `{"ok", "op", "data"|"error"}`.
"""
import io
import wave
import json

import numpy as np

from engine import vad as vad_mod
from engine.cut_algebra import cutlist_from_intervals
from selekyt.server import process_line, serve


def _handle(op: str, params: dict) -> dict:
    resp = json.loads(process_line(json.dumps({"op": op, "params": params})))
    assert resp["ok"] is True, resp
    assert resp["op"] == op
    return resp["data"]


def _write_tone_wav(tmp_path, duration=1.0, sr=16000):
    path = tmp_path / "tone.wav"
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    sig = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
        f.writeframes((sig * 32767).astype(np.int16).tobytes())
    return path


# ── envelope ──

def test_ping_reports_engine_and_backend():
    data = _handle("ping", {})
    assert data["pong"] is True
    assert data["protocol"].startswith("selekyt-jsonrpc")
    assert data["engine"] == "python"
    assert data["vad_backend"] in ("energy", "ffmpeg", "silero", "whisper")


def test_ping_without_params_ok():
    resp = json.loads(process_line('{"op": "ping"}'))
    assert resp["ok"] is True


def test_unknown_op_is_a_clean_error():
    resp = json.loads(process_line('{"op": "nope", "params": {}}'))
    assert resp["ok"] is False
    assert "unknown op" in resp["error"]


def test_bad_json_is_a_clean_error():
    resp = json.loads(process_line("not json"))
    assert resp["ok"] is False


def test_serve_loop_roundtrip():
    out = io.StringIO()
    req = '{"op": "ping", "params": {}}\n{"op": "bogus", "params": {}}\n\n'
    serve(io.StringIO(req), out)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert lines[0]["ok"] is True
    assert lines[1]["ok"] is False


# ── deterministic ops: parity with engine functions ──

def test_segment_matches_engine_direct():
    scores = [0.9] * 10 + [0.0] * 2 + [0.9] * 10
    params = {"scores": scores, "threshold": 0.5,
              "min_speech_ms": 150, "min_silence_ms": 100, "frame_ms": 32}
    data = _handle("segment", params)
    direct = vad_mod.segment_from_scores(scores, frame_ms=32, threshold=0.5,
                                         min_speech_ms=150, min_silence_ms=100)
    assert [(s["start"], s["end"]) for s in data["segments"]] == \
        [(r.start, r.end) for r in direct]
    assert data["meta"]["hysteresis"]["release"] == 0.35


def test_segment_hysteresis_contract():
    scores = [0.9, 0.4, 0.9]
    data = _handle("segment", {
        "scores": scores, "threshold": 0.5,
        "min_silence_ms": 0, "min_speech_ms": 0, "frame_ms": 32,
    })
    segs = data["segments"]
    assert len(segs) == 1


def test_cutlist_from_segments_matches_engine_direct():
    params = {
        "segments": [{"start": 1.0, "end": 2.0}, {"start": 2.2, "end": 3.0}],
        "source_duration": 5.0, "pad": 0.3,
    }
    data = _handle("cutlist_from_segments", params)
    direct = cutlist_from_intervals([(1.0, 2.0), (2.2, 3.0)],
                                    source_duration=5.0, pad=0.3)
    assert [(s["kind"], s["start"], s["end"]) for s in data["intervals"]] == \
        [(s.kind, s.start, s.end) for s in direct]


# ── media ops ──

def test_vad_analyze_reports_provenance(tmp_path):
    path = _write_tone_wav(tmp_path)
    data = _handle("vad_analyze", {"path": str(path), "force": "energy"})
    assert data["backend"] == "energy"
    assert data["vad_model"] == "energy-rms"
    assert data["frame_ms"] == vad_mod.FRAME_MS
    assert data["n_frames"] > 0
    assert len(data["segments"]) >= 1
    assert data["meta"]["speech_regions"] == len(data["segments"])


def test_vad_analyze_missing_path():
    resp = json.loads(process_line(json.dumps({"op": "vad_analyze", "params": {}})))
    assert resp["ok"] is False


def test_audio_analysis_delegates(tmp_path, monkeypatch):
    media = tmp_path / "v.mp4"
    media.write_bytes(b"0" * 100)
    summary = {"vad_backend": "energy", "speech_ratio": 0.5}
    import selekyt.server as server_mod
    called = {}

    def fake(media_path, project, **kw):
        called["media"] = str(media_path)
        called["project"] = str(project)
        return summary

    monkeypatch.setattr(server_mod, "run_pipeline_audio", fake)
    data = _handle("audio_analysis", {
        "media": str(media), "project": str(tmp_path / "project"),
        "silence_threshold": 0.7, "min_speech_ms": 200,
    })
    assert data == summary
    assert called["media"] == str(media)
    assert called["project"] == str(tmp_path / "project")


# ── wire integrity ──

def test_progress_prints_never_corrupt_the_json_stream(tmp_path):
    """Engine progress lines (print(..., flush=True)) must leave stdout so the
    one-JSON-object-per-line protocol stays parseable — same convention as the
    Rust sidecar."""
    path = _write_tone_wav(tmp_path)
    fake_stdout = io.StringIO()
    import selekyt.server as server_mod

    def loud_engine(media_path, project, **kw):
        print("[vad] this progress line must not reach the wire", flush=True)
        return {"vad_backend": "energy", "vad_model": "energy-rms"}

    orig = server_mod.run_pipeline_audio
    server_mod.run_pipeline_audio = loud_engine
    try:
        serve(io.StringIO(json.dumps({
            "op": "audio_analysis",
            "params": {"media": str(path), "project": str(tmp_path / "p")},
        }) + "\n"), fake_stdout)
    finally:
        server_mod.run_pipeline_audio = orig

    lines = fake_stdout.getvalue().splitlines()
    assert len(lines) == 1, f"stdout must carry exactly one JSON line, got {lines}"
    resp = json.loads(lines[0])
    assert resp["ok"] is True
    assert resp["data"]["vad_model"] == "energy-rms"