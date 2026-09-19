"""Python JSON-RPC sidecar protocol tests.

Pins the envelope contract (mirrored by the Rust `selekyt-mech` sidecar):
request `{"op", "params"}`, response `{"ok", "op", "data"|"error"}`.
"""
import io
import os
import sys
import subprocess
import wave
import json
from pathlib import Path

import numpy as np
import pytest

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


def test_vad_analyze_can_return_scores(tmp_path):
    path = _write_tone_wav(tmp_path)
    data = _handle("vad_analyze", {"path": str(path), "force": "energy", "return_scores": True})
    assert isinstance(data["scores"], list)
    assert len(data["scores"]) == data["n_frames"]
    assert all(isinstance(s, float) for s in data["scores"])


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


# ── the product's main path: video → social cuts ──

def test_pipeline_source_rejects_non_http_schemes():
    """`file://` and friends are not sources to fetch; the shell would have to
    expand them and a path through them is not something the pipeline should
    download."""
    from selekyt.server import _pipeline_source

    for bad in ("file:///etc/passwd", "ftp://x/y", "gopher://x", "s3://bucket/key"):
        with pytest.raises(ValueError, match="unsupported source scheme"):
            _pipeline_source(bad)


def test_pipeline_source_rejects_a_missing_local_file():
    from selekyt.server import _pipeline_source

    with pytest.raises(ValueError):
        _pipeline_source("/definitely/not/a/real/source.mp4")


def test_pipeline_source_rejects_an_empty_source():
    from selekyt.server import _pipeline_source

    with pytest.raises(ValueError, match="must be a local path"):
        _pipeline_source("   ")


def test_pipeline_source_accepts_a_url_and_a_real_file(tmp_path):
    from selekyt.server import _pipeline_source

    assert _pipeline_source("  https://youtu.be/abc  ") == "https://youtu.be/abc"
    media = tmp_path / "v.mp4"
    media.write_bytes(b"0" * 100)
    assert _pipeline_source(str(media)) == str(media)


def test_run_pipeline_writes_a_project_and_reports_its_artifacts(tmp_path, monkeypatch):
    """The op must return the artifacts it actually wrote, not just the
    directories they live in — a UI links to files."""
    import selekyt.pipeline as pipeline_mod

    def fake_run(source, out_root, **kw):
        project = Path(out_root) / "project"
        (project / "shorts").mkdir(parents=True, exist_ok=True)
        (project / "shorts" / "01.mp4").write_bytes(b"mp4")
        (project / "captions").mkdir(parents=True, exist_ok=True)
        (project / "captions" / "01.srt").write_text("cues\n", encoding="utf-8")
        (project / "timeline").mkdir(parents=True, exist_ok=True)
        (project / "timeline" / "01.edl").write_text("EDL\n", encoding="ascii")
        (project / "report.json").write_text(
            json.dumps({"selected_count": 1, "candidate_count": 2, "language": "en"}),
            encoding="utf-8",
        )
        return project

    monkeypatch.setattr(pipeline_mod, "run", fake_run)
    media = tmp_path / "v.mp4"
    media.write_bytes(b"0" * 100)

    data = _handle("run_pipeline", {"source": str(media), "out": str(tmp_path / "out")})
    assert data["project"].endswith("project")
    assert data["report"]["selected_count"] == 1
    names = [Path(p).name for p in data["artifacts"]]
    assert "01.mp4" in names and "01.srt" in names and "report.json" in names
    # The editable interchange is as much a shipped artifact as the render; a UI
    # that cannot link to it cannot offer the edit.
    assert "01.edl" in names


def test_run_pipeline_validates_params_before_any_render(tmp_path):
    """A bad --limit used to survive to a confusing late failure; the op must
    refuse it up front, as a clean wire error. ``run`` checks its parameters
    before it touches the network, so this needs no mocking — the failure is
    the wanted behaviour."""
    media = tmp_path / "v.mp4"
    media.write_bytes(b"0" * 100)
    resp = json.loads(process_line(json.dumps(
        {"op": "run_pipeline", "params": {"source": str(media), "limit": 0}}
    )))
    assert resp["ok"] is False
    assert "limit must be" in resp["error"]


# ── caption style over the wire ──

def _stub_media(tmp_path: Path) -> Path:
    p = tmp_path / "v.mp4"
    p.write_bytes(b"0" * 100)
    return p


def _stub_project(out_root) -> Path:
    project = Path(out_root) / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "report.json").write_text("{}", encoding="utf-8")
    return project


def test_an_unknown_caption_style_is_refused_before_any_render(tmp_path, monkeypatch):
    """The CLI falls back to ``minimal`` for an unknown style, which is right
    for a script; the sidecar serves a dropdown, where a typo would silently
    render the wrong style and nobody would know why. It must be refused up
    front, with the names a user can actually use."""
    import selekyt.pipeline as pipeline_mod

    def must_not_run(*a, **k):
        raise AssertionError("an invalid style must not reach the pipeline")

    monkeypatch.setattr(pipeline_mod, "run", must_not_run)
    monkeypatch.delenv("SELEKYT_CAPTION_STYLE", raising=False)
    resp = json.loads(process_line(json.dumps(
        {"op": "run_pipeline",
         "params": {"source": str(_stub_media(tmp_path)), "caption_style": "BOLD"}}
    )))
    assert resp["ok"] is False
    assert "caption_style" in resp["error"]
    assert "minimal" in resp["error"]
    # The environment is left untouched: a later run must not inherit a value
    # this run was refused.
    assert "SELEKYT_CAPTION_STYLE" not in os.environ


def test_a_known_caption_style_reaches_the_render(tmp_path, monkeypatch):
    """The caption writer reads the style from the environment at write time, so
    a caller with no argv — the web shell — has no other way to choose one."""
    import selekyt.pipeline as pipeline_mod

    seen = {}

    def fake_run(source, out_root, **kw):
        seen["style"] = os.environ.get("SELEKYT_CAPTION_STYLE")
        return _stub_project(out_root)

    monkeypatch.setattr(pipeline_mod, "run", fake_run)
    monkeypatch.setenv("SELEKYT_CAPTION_STYLE", "minimal")
    _handle("run_pipeline", {
        "source": str(_stub_media(tmp_path)),
        "out": str(tmp_path / "out"),
        "caption_style": "noir",
    })
    assert seen["style"] == "noir"
    # Restored after the run: the sidecar is one process serving many requests,
    # so a style set for one of them must not apply to the next.
    assert os.environ["SELEKYT_CAPTION_STYLE"] == "minimal"


def test_no_caption_style_param_leaves_the_environment_alone(tmp_path, monkeypatch):
    """A run that does not name a style inherits whatever the operator set in
    the environment — the CLI's own path — rather than resetting it."""
    import selekyt.pipeline as pipeline_mod

    seen = {}

    def fake_run(source, out_root, **kw):
        seen["style"] = os.environ.get("SELEKYT_CAPTION_STYLE")
        return _stub_project(out_root)

    monkeypatch.setattr(pipeline_mod, "run", fake_run)
    monkeypatch.setenv("SELEKYT_CAPTION_STYLE", "punch")
    _handle("run_pipeline", {
        "source": str(_stub_media(tmp_path)),
        "out": str(tmp_path / "out"),
    })
    assert seen["style"] == "punch"
    assert os.environ["SELEKYT_CAPTION_STYLE"] == "punch"


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


def test_subprocess_progress_never_corrupts_the_json_stream(tmp_path):
    """A child process writing to fd 1 is the wire-integrity case that
    ``print()`` redirection cannot catch.

    The sidecar swaps ``sys.stdout`` for ``sys.stderr`` to keep its own progress
    off the wire, but an inherited child writes to the *real* fd 1 and bypasses
    that swap entirely. ``providers.youtube.download`` runs yt-dlp as a child, so
    its progress used to land on the wire and the caller saw
    ``sidecar sent a non-JSON line``. This reproduces that failure with any
    child and pins the fix.
    """
    path = _write_tone_wav(tmp_path)
    fake_stdout = io.StringIO()
    import selekyt.server as server_mod

    def loud_child(media_path, project, **kw):
        # A child emitting progress to fd 1 the way yt-dlp does.
        subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.stdout.write('[archive.org] progress 50%\\n'); sys.stdout.flush()"],
        )
        return {"vad_backend": "energy", "vad_model": "energy-rms"}

    orig = server_mod.run_pipeline_audio
    server_mod.run_pipeline_audio = loud_child
    try:
        serve(io.StringIO(json.dumps({
            "op": "audio_analysis",
            "params": {"media": str(path), "project": str(tmp_path / "p")},
        }) + "\n"), fake_stdout)
    finally:
        server_mod.run_pipeline_audio = orig

    lines = fake_stdout.getvalue().splitlines()
    assert len(lines) == 1, f"a child must not put non-JSON on the wire: {lines}"
    resp = json.loads(lines[0])
    assert resp["ok"] is True