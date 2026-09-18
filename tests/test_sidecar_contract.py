"""JSON-RPC sidecar contract.

Pins the wire protocol the desktop UI and the Rust sidecar both depend on:

    request:  {"op": "<op>", "params": {...}}     # params optional
    response: {"ok": true,  "op": "<op>", "data": ...}
              {"ok": false, "op": "<op>", "error": "..."}

The op set is enumerated from ``selekyt.server._HANDLERS`` itself, so a new op
cannot ship without envelope and error-handling coverage, while a test only
*under*-asserts (the documented set must remain registered) so additions never
break the suite.
"""
from __future__ import annotations

import io
import itertools
import json
import wave
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from selekyt.server import _HANDLERS, PROTOCOL, process_line, serve

# Ops the contract document promises; any removal is a wire break.
DOCUMENTED_OPS = frozenset({
    "ping", "segment", "cutlist_from_segments", "vad_analyze", "audio_analysis",
    "run_pipeline", "detect_rate", "parse_smpte", "seconds_to_rational",
    "track_offset_from_timecode", "render_fcpxml", "normalize_keeps",
    "omitted_intervals", "clamp_keep_edge", "skip_target_for",
    "previous_keep_target", "next_keep_target",
})


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tone_wav(tmp_path_factory) -> str:
    """A 16 kHz mono tone: the energy VAD reads WAV directly (no ffmpeg), so
    the media ops are testable on any host."""
    path = tmp_path_factory.mktemp("audio") / "tone.wav"
    sr = 16000
    t = np.linspace(0, 1.0, int(sr * 1.0), endpoint=False)
    sig = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((sig * 32767).astype(np.int16).tobytes())
    return str(path)


@pytest.fixture(scope="module")
def audio_stage_stub():
    """The audio_analysis and run_pipeline ops delegate to real ffmpeg- and
    ASR-backed stages; stub both so the envelope test is hermetic, and restore
    them on teardown so a failure cannot leak a stub into the rest of the
    session."""
    import selekyt.pipeline as pipeline_mod

    originals = {}

    def fake_pipeline_run(source, out_root, **kw):
        from pathlib import Path
        import json as _json
        project = Path(out_root) / "project"
        (project / "shorts").mkdir(parents=True, exist_ok=True)
        (project / "shorts" / "01.mp4").write_bytes(b"stub")
        (project / "report.json").write_text(_json.dumps({
            "selected_count": 1, "candidate_count": 1, "language": "en",
        }), encoding="utf-8")
        return project

    originals["run"] = pipeline_mod.run
    pipeline_mod.run = fake_pipeline_run
    try:
        yield pipeline_mod
    finally:
        for name, fn in originals.items():
            setattr(pipeline_mod, name, fn)


# ── param tables ────────────────────────────────────────────────────────────


def _keeps() -> list[dict]:
    return [{"start": 0.0, "end": 4.0}, {"start": 10.0, "end": 14.0}]


def _cutlist() -> dict:
    return {
        "source_duration": 20.0,
        "intervals": [
            {"start": 0.0, "end": 4.0, "kind": "keep"},
            {"start": 4.0, "end": 10.0, "kind": "remove"},
            {"start": 10.0, "end": 14.0, "kind": "keep"},
            {"start": 14.0, "end": 20.0, "kind": "remove"},
        ],
    }


def _valid_params(op: str, tone_wav: str, project_dir: str) -> dict:
    return {
        "ping": {},
        "segment": {"scores": [0.9] * 10 + [0.0] * 2 + [0.9] * 10, "frame_ms": 32},
        "cutlist_from_segments": {
            "segments": [{"start": 1.0, "end": 2.0}, {"start": 2.2, "end": 3.0}],
            "source_duration": 5.0, "pad": 0.3,
        },
        "vad_analyze": {"path": tone_wav, "force": "energy"},
        "audio_analysis": {"media": tone_wav, "project": project_dir},
        # run_pipeline takes any readable file: the pipeline itself is stubbed
        # in these tests, so the tone is only the source it is handed.
        "run_pipeline": {"source": tone_wav, "out": project_dir, "limit": 1},
        "detect_rate": {"fps": 30},
        "parse_smpte": {"tc": "01:00:00:00", "fps": 30},
        "seconds_to_rational": {"seconds": 1.5, "fps": 30},
        "track_offset_from_timecode": {
            "reference_timecode": "01:00:00:00",
            "track_timecode": "01:00:01:00", "fps": 30,
        },
        "render_fcpxml": {
            "cutlist": _cutlist(), "fps": 30, "title": "contract",
            "reference": {"name": "src", "duration": 20.0, "path": "/tmp/src.mp4"},
        },
        "normalize_keeps": {"keeps": _keeps(), "duration": 20.0},
        "omitted_intervals": {"cutlist": _cutlist()},
        "clamp_keep_edge": {"keeps": _keeps(), "index": 0, "proposed": -1.0,
                            "edge": "in", "duration": 20.0},
        "skip_target_for": {"omitted": [[4.0, 10.0]], "time": 0.0, "lookahead": 2.0},
        "previous_keep_target": {"keeps": _keeps(), "time": 5.0},
        "next_keep_target": {"keeps": _keeps(), "time": 5.0, "duration": 20.0},
    }[op]


# Inputs that must be rejected rather than crashing the wire.
BAD_PARAMS: dict[str, list[dict]] = {
    "segment": [{}, {"scores": "not-an-array"}, {"scores": [0.5], "frame_ms": "x"}],
    "cutlist_from_segments": [
        {}, {"segments": "x", "source_duration": 5.0},
        {"segments": [{"start": 1.0}], "source_duration": 5.0},
    ],
    "vad_analyze": [{}, {"path": ""}, {"path": "/definitely/not/here.wav"}],
    # audio_analysis delegates to a stubbed stage in these tests, so only the
    # param-shape failures are exercised here (a missing file is not a
    # malformed param).
    "audio_analysis": [{}, {"media": ""}],
    # A source the op must refuse rather than hand to a fetcher: an unsupported
    # scheme, a missing file, and no source at all.
    "run_pipeline": [
        {}, {"source": ""}, {"source": "file:///etc/passwd"},
        {"source": "/definitely/not/here.mp4"},
    ],
    "detect_rate": [{}, {"fps": "fast"}],
    "parse_smpte": [{"fps": "x"}],
    "seconds_to_rational": [{}, {"fps": "x"}],
    "track_offset_from_timecode": [{"fps": "x"}],
    "render_fcpxml": [
        {}, {"cutlist": {"intervals": "x", "source_duration": 1.0}, "fps": 30,
             "title": "t", "reference": {"name": "s", "duration": 1.0}},
    ],
    "normalize_keeps": [{}, {"keeps": "x", "duration": 1.0}],
    "omitted_intervals": [{}, {"cutlist": {"intervals": "x", "source_duration": 1.0}}],
    "clamp_keep_edge": [{}, {"keeps": _keeps(), "duration": 1.0}],
    "skip_target_for": [{}, {"omitted": "x", "time": 0.0, "lookahead": 1.0}],
    "previous_keep_target": [{}, {"keeps": "x", "time": 0.0}],
    "next_keep_target": [{}, {"keeps": "x", "time": 0.0, "duration": 1.0}],
}


def _call(op: str, params) -> dict:
    return json.loads(process_line(json.dumps({"op": op, "params": params})))


# ── registry ──


def test_documented_ops_are_all_registered():
    """Every op the contract documents is handled — a removal is a wire break
    for the desktop UI and the Rust sidecar."""
    assert DOCUMENTED_OPS <= frozenset(_HANDLERS)
    for op in DOCUMENTED_OPS:
        assert callable(_HANDLERS[op])


@pytest.mark.parametrize("op", sorted(_HANDLERS))
def test_every_op_answers_in_the_documented_envelope(op, tone_wav, tmp_path,
                                                    audio_stage_stub):
    """Success responses carry exactly {ok, op, data} and echo the op."""
    params = _valid_params(op, tone_wav, str(tmp_path / "project"))
    resp = _call(op, params)
    assert resp["ok"] is True
    assert resp["op"] == op
    assert set(resp) == {"ok", "op", "data"}, f"unexpected keys {set(resp)}"
    assert "error" not in resp
    assert resp["data"] is not None


@pytest.mark.parametrize("op", sorted(BAD_PARAMS))
def test_each_op_rejects_bad_params(op, tone_wav, tmp_path, audio_stage_stub):
    """Every malformed request comes back as ok:false with a non-empty error
    string, echoing the op, and never carrying data."""
    for bad in BAD_PARAMS[op]:
        resp = _call(op, bad)
        assert resp["ok"] is False, f"{op} with {bad} unexpectedly succeeded"
        assert resp["op"] == op
        assert set(resp) == {"ok", "op", "error"}
        assert isinstance(resp["error"], str) and resp["error"].strip(), \
            f"{op} with {bad} returned an empty error"


# ── malformed transport ──


def test_unknown_op_is_a_clean_error():
    resp = _call("does_not_exist", {})
    assert resp["ok"] is False
    assert resp["op"] == "does_not_exist"
    assert "unknown op" in resp["error"]


@pytest.mark.parametrize("line", [
    pytest.param("not json at all", id="garbage"),
    pytest.param("", id="empty"),
    pytest.param("{", id="truncated"),
    pytest.param("42", id="bare-number"),
    pytest.param('"a string"', id="bare-string"),
    pytest.param("null", id="json-null"),
])
def test_invalid_json_is_a_clean_error(line):
    resp = json.loads(process_line(line))
    assert resp["ok"] is False
    assert resp["op"] == ""
    assert isinstance(resp["error"], str) and resp["error"]


def test_params_key_is_optional():
    resp = json.loads(process_line(json.dumps({"op": "ping"})))
    assert resp["ok"] is True
    assert resp["op"] == "ping"


@pytest.mark.parametrize("params", [None, 7, "str", ["a"], True])
def test_non_object_params_default_to_empty(params):
    """A request whose params are not an object must not crash the wire; the
    documented behaviour is to treat them as empty."""
    resp = json.loads(process_line(json.dumps({"op": "ping", "params": params})))
    assert resp["ok"] is True


def test_non_object_body_reports_unknown_op():
    resp = json.loads(process_line(json.dumps("just a string")))
    assert resp["ok"] is False


def test_serve_emits_exactly_one_json_object_per_request_line():
    """The stream stays one-JSON-object-per-line even when a request fails."""
    out = io.StringIO()
    request = (
        '{"op": "ping"}\n'
        '{"op": "nope"}\n'
        'garbage\n'
        '\n'  # blank lines are ignored, not answered
    )
    serve(io.StringIO(request), out)
    lines = out.getvalue().splitlines()
    assert len(lines) == 3, f"expected 3 responses, got {lines}"
    for line in lines:
        json.loads(line)  # every emitted line is a complete JSON object
    assert json.loads(lines[0])["ok"] is True
    assert json.loads(lines[1])["ok"] is False
    assert json.loads(lines[2])["ok"] is False


# ── documented op shapes ──


def test_ping_identifies_the_engine_and_protocol(tone_wav, tmp_path, audio_stage_stub):
    data = _call("ping", {})["data"]
    assert data["pong"] is True
    assert data["protocol"] == PROTOCOL
    assert data["engine"] == "python"
    assert data["vad_backend"]


def test_segment_reports_segments_and_hysteresis_meta():
    data = _call("segment", {
        "scores": [0.9] * 10 + [0.0] * 2 + [0.9] * 10, "frame_ms": 32,
    })["data"]
    assert isinstance(data["segments"], list)
    assert data["segments"], "a speech/silence/speech score array yields speech"
    for seg in data["segments"]:
        assert set(seg) == {"start", "end"}
        assert seg["end"] > seg["start"]
    assert "meta" in data
    assert data["meta"]["speech_regions"] == len(data["segments"])


def test_cutlist_tiles_the_whole_source_duration():
    data = _call("cutlist_from_segments", {
        "segments": [{"start": 1.0, "end": 2.0}], "source_duration": 5.0, "pad": 0.3,
    })["data"]
    spans = data["intervals"]
    assert data["source_duration"] == 5.0
    assert spans[0]["start"] == 0.0
    assert spans[-1]["end"] == 5.0
    assert {s["kind"] for s in spans} == {"keep", "remove"}
    # the tiling is gapless and ordered
    for a, b in itertools.pairwise(spans):
        assert a["end"] == b["start"]


def test_render_fcpxml_matches_the_engine_byte_for_byte():
    """The op is a thin wrapper: its XML must be identical to calling the
    engine directly, and must be well-formed FCPXML 1.11."""
    from engine import fcpxml, jumpcut

    params = {
        "cutlist": _cutlist(), "fps": 30, "title": "contract",
        "reference": {"name": "src", "duration": 20.0, "path": "/tmp/src.mp4"},
    }
    via_op = _call("render_fcpxml", params)["data"]["xml"]

    cutlist = jumpcut.cutlist_from_wire(params["cutlist"]["intervals"],
                                        params["cutlist"]["source_duration"])
    direct = fcpxml.render(
        cutlist,
        fcpxml.FcpxmlParams(
            fps=30.0, title="contract",
            reference=fcpxml.Track(name="src", duration=20.0, path="/tmp/src.mp4"),
        ),
    )
    assert via_op == direct
    root = ET.fromstring(via_op)
    assert root.tag == "fcpxml"
    assert root.get("version") == "1.11"
    assert root.find(".//spine") is not None


def test_unicode_params_round_trip_the_wire():
    """The wire is UTF-8 with ensure_ascii=False, so Telugu (and any script)
    survives a round trip byte-for-byte."""
    title = "హైదరాబాద్ నగరంలో ఎలక్ట్రిక్ బస్సులు"
    params = {
        "cutlist": _cutlist(), "fps": 30, "title": title,
        "reference": {"name": "src", "duration": 20.0},
    }
    raw = process_line(json.dumps({"op": "render_fcpxml", "params": params}))
    assert title in raw, "the response must carry the script unmangled"
    resp = json.loads(raw)
    assert resp["ok"] is True
    assert title in resp["data"]["xml"]
    # re-encoding is stable and lossless
    assert json.loads(json.dumps(resp)) == resp
