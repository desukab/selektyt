"""Web shell harness tests - zero-dependency Node bridge to the Python sidecar.

Start `node web/server.mjs --port 0`, parse the LISTEN line, then drive the
HTTP surface with stdlib urllib only (no node packages, no browser). These run
in CI on ubuntu-latest where node is preinstalled; skipped if node is absent.
"""
import base64
import json
import re
import select
import shutil
import subprocess
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from os.path import dirname

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


@pytest.fixture(scope="module")
def base():
    proc = subprocess.Popen(
        [NODE, str(ROOT / "web" / "server.mjs"), "--port", "0"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    line = b""
    deadline = time.time() + 20
    while time.time() < deadline:
        r, _, _ = select.select([proc.stdout], [], [], 1)
        if not r:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited: {proc.stderr.read().decode()[-500:]}")
            continue
        chunk = proc.stdout.readline()
        if not chunk:
            break
        line += chunk
        if b"LISTEN" in line:
            break
    match = re.search(rb"LISTEN (\S+):(\d+)", line)
    if not match:
        proc.kill()
        raise RuntimeError(f"server did not report a port: {line!r}")
    host, port = match.group(1).decode(), int(match.group(2))
    base = f"http://{host}:{port}"
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return r.read()


def _post(base, op, params):
    req = urllib.request.Request(
        base + f"/api/{op}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _expect_ok(base, op, params):
    resp = _post(base, op, params)
    assert resp["ok"] is True, resp
    assert resp["op"] == op
    return resp["data"]


def test_serves_index(base):
    page = _get(base, "/")
    assert b"SelekyT" in page
    assert b"app.mjs" in page


def test_ping_reports_python_sidecar(base):
    data = _expect_ok(base, "ping", {})
    assert data["engine"] == "python"
    assert data["protocol"].startswith("selekyt-jsonrpc")
    assert data["pong"] is True


def test_sample_wav_is_valid_16k_mono(base):
    wav = _get(base, "/api/sample.wav")
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    sr = int.from_bytes(wav[24:28], "little")
    channels = int.from_bytes(wav[22:24], "little")
    n_bytes = int.from_bytes(wav[40:44], "little")
    assert sr == 16000
    assert channels == 1
    assert len(wav) == 44 + n_bytes


def test_vad_analyze_sample_reports_provenance(base):
    data = _expect_ok(base, "vad_analyze", {"sample": True})
    assert data["backend"] == "energy"
    assert data["vad_model"] == "energy-rms"
    assert data["frame_ms"] == 32
    assert data["sample_rate"] == 16000
    assert abs(data["duration"] - 8.0) < 0.2
    assert data["n_frames"] > 0
    assert len(data["segments"]) >= 1
    assert len(data["scores"]) == data["n_frames"]
    assert {"start", "end"} <= set(data["segments"][0])


def test_segments_follow_sample_bursts(base):
    data = _expect_ok(base, "vad_analyze", {"sample": True, "threshold": 0.5})
    segments = data["segments"]
    assert len(segments) >= 3, segments
    for seg in segments:
        assert 0.0 <= seg["start"] < seg["end"] <= data["duration"]


def test_segment_round_trip_and_threshold(base):
    data = _expect_ok(base, "vad_analyze", {"sample": True})
    low = _expect_ok(base, "segment", {"scores": data["scores"], "threshold": 0.2})
    high = _expect_ok(base, "segment", {"scores": data["scores"], "threshold": 0.9})
    assert len(low["segments"]) >= len(high["segments"])


def test_segment_error_envelope(base):
    resp = _post(base, "segment", {"scores": "not-an-array"})
    assert resp["ok"] is False
    assert isinstance(resp["error"], str)


def test_upload_wav_is_analyzed(base):
    wav = _get(base, "/api/sample.wav")
    data = _expect_ok(base, "vad_analyze", {
        "wav": base64.b64encode(wav).decode(),
        "name": "upload-custom.wav",
    })
    assert data["n_frames"] > 0
    assert len(data["segments"]) >= 1


def test_cutlist_from_segments(base):
    data = _expect_ok(base, "vad_analyze", {"sample": True})
    cut = _expect_ok(base, "cutlist_from_segments", {
        "segments": data["segments"],
        "source_duration": data["duration"],
    })
    assert cut["source_duration"] == data["duration"]
    assert cut["intervals"]
    assert all({"kind", "start", "end"} <= set(i) for i in cut["intervals"])


def test_cutlist_pad_widens_keeps(base):
    data = _expect_ok(base, "vad_analyze", {"sample": True})
    tight = _expect_ok(base, "cutlist_from_segments", {
        "segments": data["segments"],
        "source_duration": data["duration"],
        "pad": 0.0,
    })
    padded = _expect_ok(base, "cutlist_from_segments", {
        "segments": data["segments"],
        "source_duration": data["duration"],
        "pad": 0.3,
    })
    tight_keep = sum(i["end"] - i["start"] for i in tight["intervals"] if i["kind"] == "keep")
    padded_keep = sum(i["end"] - i["start"] for i in padded["intervals"] if i["kind"] == "keep")
    assert padded_keep > tight_keep


def test_export_produces_downloadable_artifacts(base):
    data = _expect_ok(base, "export", {"sample": True})
    assert data["vad_backend"] == "energy"
    assert data["compression_ratio"] > 0
    assert data["fcpxml"], "export must produce an FCPXML path"
    cut = _get(base, f"/api/file?path={urllib.parse.quote(data['cutlist'])}")
    assert b'"segments"' in cut
    fcpxml = _get(base, f"/api/file?path={urllib.parse.quote(data['fcpxml'])}")
    assert b"<fcpxml" in fcpxml


def test_out_of_project_paths_are_rejected(base):
    resp = _post(base, "export", {"path": "/etc/hostname"})
    assert resp["ok"] is False
    assert isinstance(resp["error"], str)


def test_bad_upload_is_a_clean_error(base):
    resp = _post(base, "vad_analyze", {"wav": "bm90LXdhdg==", "name": "tiny.wav"})
    assert resp["ok"] is False

def _broken_python3_shim(tmpdir: Path) -> Path:
    """A `python3` that passes `--version` but cannot run the sidecar.

    This is exactly the shape of an unactivated project venv plus a bare
    system python3: the interpreter answers --version (so the old
    resolution accepted it) and then dies on the sidecar's imports.
    """
    shim = tmpdir / "python3"
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "Python 0.0.0 (shim)"; exit 0; fi\n'
        'if [ "$1" = "-c" ]; then echo "ModuleNotFoundError: No module named \'selekyt\'" >&2; exit 1; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def test_sidecar_resolution_skips_an_interpreter_that_cannot_run_it(tmp_path):
    """Regression: the bridge must not pick a python that cannot start the
    sidecar. It used to take the first PATH entry that answered --version and
    every request then failed with an unhelpful "sidecar exited before
    answering"."""
    broken = _broken_python3_shim(tmp_path)
    # A directory later on PATH must hold a genuinely working interpreter,
    # otherwise there is nothing to fall forward to and the test cannot mean
    # anything.
    real = shutil.which("python3")
    if real is None or real == str(broken):
        pytest.skip("no second interpreter available to resolve to")
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}{os.pathsep}{dirname(real)}{os.pathsep}{env.get('PATH', '')}"
    # Force the probe path: never honour an inherited override here.
    env.pop("SELEKYT_PYTHON", None)
    proc = subprocess.Popen(
        [NODE, str(ROOT / "web" / "server.mjs"), "--port", "0"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        base = None
        deadline = time.time() + 20
        while time.time() < deadline:
            r, _, _ = select.select([proc.stdout], [], [], 1)
            if not r:
                assert proc.poll() is None, proc.stderr.read().decode()[-800:]
                continue
            m = re.search(rb"LISTEN (\S+):(\d+)", proc.stdout.readline())
            if m:
                base = f"http://{m.group(1).decode()}:{int(m.group(2))}"
                break
        assert base, f"server never reported a port: {proc.stderr.read().decode()[-800:]}"
        data = _expect_ok(base, "ping", {})
        assert data["engine"] == "python"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
