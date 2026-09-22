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
import sys
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from os.path import dirname

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


@pytest.fixture(scope="module")
def base():
    env = dict(os.environ)
    env.pop("SELEKYT_PYTHON", None)
    with _bridge(env) as base:
        yield base


@contextmanager
def _bridge(env):
    """Run the bridge against a given environment and yield its base URL.

    ``SELEKYT_PYTHON`` is the hook a caller uses to point the bridge at a
    sidecar that is not the real one; popping it is what makes the default
    fixture immune to an override left in the ambient environment.
    """
    proc = subprocess.Popen(
        [NODE, str(ROOT / "web" / "server.mjs"), "--port", "0"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
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
        yield f"http://{host}:{port}"
    finally:
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


def _raw_post(base, op, params, headers):
    """A POST whose headers the caller controls, so a test can pose as a browser
    that did not originate the request — the shape of a CSRF attempt."""
    req = urllib.request.Request(
        base + f"/api/{op}",
        data=json.dumps(params).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def _expect_ok(base, op, params):
    resp = _post(base, op, params)
    assert resp["ok"] is True, resp
    assert resp["op"] == op
    return resp["data"]


def _post_or_error(base, op, params) -> dict:
    """Same call, but capture the failure envelope instead of raising.

    The bridge answers a bad request with HTTP 200 and ``{"ok": false, ...}``;
    only an internal error becomes a non-2xx. Both shapes must surface the
    error string a user would read.
    """
    req = urllib.request.Request(
        base + f"/api/{op}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


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


def test_a_sibling_directory_extending_the_project_name_is_rejected(base):
    """The prefix bypass this test exists for: ``guardedPath`` used to be a bare
    ``startsWith(ROOT)``, and a *sibling* directory whose name extends the
    project's own name string-matches it. Resolved absolute,
    ``/home/.../selektyt-secret/leak.wav`` starts with ``/home/.../selektyt``
    and would have been accepted as inside the jail — which is why the boundary
    now includes the separator.

    Two details make this test able to see the bypass:

    - The probe is sent as an **absolute** path. That is the shape of the
      bypass: no ``..`` anywhere in it, so a traversal filter would not catch
      it either.
    - The probe is a **real WAV**, not an arbitrary file. A probe the pipeline
      would reject anyway cannot distinguish "blocked by the jail" from
      "rejected for not being audio", and passes against a broken guard.
    """
    wav = _get(base, "/api/sample.wav")
    sibling = ROOT.parent / f"{ROOT.name}-secret"
    probe = sibling / "leak.wav"
    try:
        sibling.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(wav)
    except OSError as exc:
        pytest.skip(f"cannot create a sibling of the repo ({exc}); the jail "
                    f"cannot be probed from outside it")
    try:
        absolute = str(probe)
        assert absolute.startswith(str(ROOT)), "the probe must be the bypass shape"
        assert not absolute.startswith(str(ROOT) + os.sep), \
            "the probe must actually be outside the project"
        # vad_analyze is the cheapest route through guardedPath; it would
        # happily analyse this WAV if the jail let it through.
        resp = _post(base, "vad_analyze", {"path": absolute})
        assert resp["ok"] is False, f"{absolute} escaped the project jail"
        assert "inside the project" in resp["error"], resp
    finally:
        shutil.rmtree(sibling, ignore_errors=True)


def _get_envelope(base, path) -> dict:
    """A rejected path is still HTTP 200 — the bridge answers bad requests with
    an ``{"ok": false}`` envelope rather than a non-2xx — so a plain ``_get``
    returns the error body and the caller has to read the flag, not the code."""
    return json.loads(_get(base, path))


def test_api_file_rejects_a_repo_file(base):
    """The download route is unauthenticated and bound to localhost, so its
    jail must be ``out/web``, not the repo: ``.env``, ``.git/`` and
    ``pipeline_state.json`` (which carries the yt-dlp media blob) are all inside
    the repo root and none of them is a download."""
    for rel in ["web/server.mjs", "docs/TECHNICAL_AUDIT.md", ".git/HEAD"]:
        body = _get_envelope(base, f"/api/file?path={urllib.parse.quote(rel)}")
        assert body["ok"] is False, f"{rel} was served"
        assert "out/web" in body["error"], body


def test_api_file_rejects_a_sibling_directory(base):
    """``startsWith(ROOT)`` alone is bypassable: a sibling whose name extends
    the project's (``selektyt-backup``, ``selektyt.old``) string-matches and
    reads as inside. The boundary has to include the separator — and so does
    this test, which passed vacuously before the sibling case was in it."""
    sibling = ROOT.with_name(ROOT.name + "-backup")
    body = _get_envelope(
        base, f"/api/file?path={urllib.parse.quote(str(sibling / 'secret.txt'))}"
    )
    assert body["ok"] is False, "a sibling directory was served"
    assert "out/web" in body["error"], body


def test_a_cross_origin_post_is_refused(base):
    """The CSRF primitive this test exists for: a page at `evil.example` that
    the user has open in another tab submits a `fetch` to the local harness.
    The browser attaches `Origin: https://evil.example` to the POST and the page
    cannot strip it, so comparing it is the gate. Before the check existed the
    whole pipeline was drivable this way, and binding to 127.0.0.1 did not help
    — the browser making the request *is* on this host.

    `vad_analyze` is the cheapest mutating route, so it is the probe; a route
    that did real work would prove the same thing far more slowly."""
    resp = _raw_post(
        base, "vad_analyze", {"sample": True},
        {"Content-Type": "application/json", "Origin": "https://evil.example"},
    )
    assert resp["ok"] is False
    assert "cross-origin" in resp["error"], resp
    assert "evil.example" in resp["error"], resp


def test_a_cross_origin_referer_is_refused_when_origin_is_absent(base):
    """A page can set `Referrer-Policy: no-referrer`, but a browser still sends
    `Origin` on a POST — so a request with a hostile `Referer` and no `Origin`
    is not a browser POST at all in practice. It is still refused: nothing the
    harness serves should be reachable from another site's URL, and a client
    that sends one of the two headers but not the other is not a case to accept
    silently."""
    resp = _raw_post(
        base, "vad_analyze", {"sample": True},
        {"Content-Type": "application/json", "Referer": "https://evil.example/page"},
    )
    assert resp["ok"] is False
    assert "evil.example" in resp["error"], resp


def test_an_own_origin_post_is_accepted(base):
    """The flip side of the two refusals, and the reason they mean anything: a
    request that *does* carry the harness's own origin has to work, or the gate
    is a blanket refusal and both tests above pass against `return null`.

    The origin is built from the request's own `Host`, not from the bind
    address, because the page is whatever URL the user opened — `localhost:4242`
    and `127.0.0.1:4242` are the same server and a same-origin fetch from either
    has to pass."""
    own = urllib.parse.urlparse(base).geturl().rstrip("/")
    resp = _raw_post(
        base, "ping", {},
        {"Content-Type": "application/json", "Origin": own},
    )
    assert resp["ok"] is True, resp


def test_a_post_without_any_origin_header_still_works(base):
    """curl, a script and this suite send neither `Origin` nor `Referer`, and a
    localhost bind already treats any local process as trusted; the threat the
    gate closes is a *remote* page reaching a local service. A gate that
    demanded an `Origin` header would break every non-browser client for no
    security gain."""
    resp = _raw_post(base, "ping", {}, {"Content-Type": "application/json"})
    assert resp["ok"] is True, resp


def test_a_json_body_sent_as_text_plain_is_refused(base):
    """The other half of the primitive: an HTML form with
    `enctype="text/plain"` carrying JSON needs no CORS preflight, so it sailed
    past `getJson` straight into `JSON.parse`. A form cannot set
    `Content-Type: application/json`, so requiring it is what closes that.
    The `Origin` header here is the harness's own, so the content-type check is
    the only thing this request trips — if it passes, the gate is missing."""
    own = urllib.parse.urlparse(base).geturl().rstrip("/")
    resp = _raw_post(
        base, "vad_analyze", {"sample": True},
        {"Content-Type": "text/plain", "Origin": own},
    )
    assert resp["ok"] is False
    assert "application/json" in resp["error"], resp


def test_run_pipeline_rejects_an_out_of_project_path(base):
    """The new route hands a path to the pipeline, which downloads and renders.
    The traversal guard must apply to it too, not just to /api/file."""
    resp = _post_or_error(base, "run_pipeline", {"path": "/etc/hostname"})
    assert resp["ok"] is False
    assert "inside the project" in resp["error"], resp


def test_run_pipeline_rejects_a_missing_source(base):
    resp = _post_or_error(base, "run_pipeline", {"path": "definitely/not/here.mp4"})
    assert resp["ok"] is False
    assert isinstance(resp["error"], str)


def test_run_pipeline_requires_a_source(base):
    resp = _post_or_error(base, "run_pipeline", {})
    assert resp["ok"] is False
    assert isinstance(resp["error"], str)


def test_run_pipeline_rejects_a_non_http_url(base):
    """`file://` is not a source to fetch; handing it to a downloader is how
    local files get read through a URL-shaped hole."""
    resp = _post_or_error(base, "run_pipeline", {"source": "file:///etc/hostname"})
    assert resp["ok"] is False
    assert isinstance(resp["error"], str)


def test_run_pipeline_rejects_an_unknown_caption_style(base):
    """The shell offers caption styles as a dropdown, so a value the sidecar
    does not know must come back as a readable error rather than a silent
    fallback to minimal. This is the end-to-end proof the control is wired:
    the request crosses HTTP, the bridge and the sidecar before failing.

    The source has to be a file that exists — the bridge answers "no such file"
    itself, and that answer would never reach the style check."""
    resp = _post_or_error(base, "run_pipeline", {
        "path": "README.md",
        "caption_style": "EXPENSIVE",
    })
    assert resp["ok"] is False
    assert "caption_style" in resp["error"], resp


def test_index_exposes_the_controls_the_pipeline_supports(base):
    """The shell is the usable interface today, and a control the CLI has but
    the page lacks is an option the user cannot reach. Each id here is one the
    request in ``runCuts`` reads, so dropping the control breaks the request."""
    page = _get(base, "/").decode("utf-8")
    for ident in (
        "cuts-source", "cuts-limit", "cuts-model",
        "cuts-caption-style", "cuts-audio-analysis", "cuts-cookies", "cuts-run",
    ):
        assert f'id="{ident}"' in page, f"the page lost the {ident} control"
    # The dropdown must offer exactly the presets the caption writer knows; a
    # name it does not implement is refused by the sidecar.
    for style in ("minimal", "punch", "spotlight", "noir"):
        assert f'value="{style}"' in page, f"the page lost the {style} preset"


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
    with _bridge(env) as base:
        data = _expect_ok(base, "ping", {})
        assert data["engine"] == "python"


# ── the request queue: correlation, strays and a dead sidecar ──
#
# These run the bridge against a sidecar that is deliberately not the real one,
# because the failure shapes being pinned are timing and ordering ones the real
# sidecar cannot produce on demand: a line printed to stdout that is not a
# response, an envelope for an id nobody sent, and a child that dies between
# requests. ``mode`` is read by the fake and is a no-op parameter to the real
# sidecar, which is why ``/api/segment`` — the one route that forwards its body
# verbatim — is the route these use.

_SLOW_S = 15.0
# The probe has to come back well inside the slow request's duration. Measured
# against the deadline the route applies rather than against the sleep, so a
# loaded runner's jitter lands in a gap seconds wide on either side rather than
# next to a boundary: the fix returns at ~5s (the route's own deadline) and the
# old queue at ~``_SLOW_S``, and this sits between them.
_PROBE_BACK_BY = 10.0

_FAKE_SIDECAR = '''import json
import sys
import time

SLOW_S = %(slow)s


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\\n")
    sys.stdout.flush()


held = []


def answer(req, data):
    payload = {"ok": True, "op": req.get("op", ""), "data": data}
    if "id" in req:
        payload["id"] = req["id"]
    send(payload)


for line in sys.stdin:
    if not line.strip():
        continue
    try:
        req = json.loads(line)
    except ValueError:
        send({"ok": False, "op": "", "error": "invalid JSON"})
        continue
    op = req.get("op", "")
    mode = (req.get("params") or {}).get("mode", "")
    if op == "ping":
        answer(req, {"pong": True, "engine": "fake", "version": "0.0.0-fake",
                     "protocol": "selekyt-jsonrpc/fake", "vad_backend": "energy"})
    elif op == "segment" and mode == "slow":
        # Holds the sidecar long enough that a probe arriving while it runs
        # cannot be answered until it finishes.
        time.sleep(SLOW_S)
        answer(req, {"segments": [{"start": 1.0, "end": 2.0}], "marker": "slow"})
    elif op == "segment" and mode == "stray":
        # A line on stdout that is a response to nothing: the shape of a
        # library deep in the pipeline printing to fd 1 past the protocol.
        sys.stdout.write("PROGRESS 42/100\\n")
        sys.stdout.flush()
        answer(req, {"segments": [{"start": 3.0, "end": 4.0}], "marker": "stray"})
    elif op == "segment" and mode == "foreign":
        # A well-formed envelope for an id no request sent. Under a pure FIFO
        # this is simply the answer to whichever request is waiting.
        send({"ok": True, "op": "segment",
              "data": {"segments": []}, "id": 99999})
        answer(req, {"segments": [{"start": 5.0, "end": 6.0}], "marker": "foreign"})
    elif op == "segment" and mode == "first":
        # Held, not answered: the request after it is answered first, so the
        # two responses arrive back to front.
        held.append(req)
    elif op == "segment" and mode == "second":
        answer(req, {"segments": [{"start": 9.0, "end": 10.0}], "marker": "second"})
        while held:
            answer(held.pop(0),
                   {"segments": [{"start": 7.0, "end": 8.0}], "marker": "first"})
    elif op == "segment" and mode == "die":
        sys.stderr.write("fake sidecar: dying on purpose\\n")
        sys.stderr.flush()
        raise SystemExit(1)
    else:
        answer(req, {"segments": []})
''' % {"slow": _SLOW_S}


@pytest.fixture
def fake_base(tmp_path):
    """The bridge pointed at a sidecar script instead of the real one.

    ``SELEKYT_PYTHON`` replaces the interpreter wholesale, so the fake is a shim
    that execs a real python on a script the test wrote. It has to honour the
    bridge's import probe too, even though ``SELEKYT_PYTHON`` bypasses it, so a
    change in that resolution path cannot silently leave the fixture spawning a
    sidecar that never answers.
    """
    script = tmp_path / "fake_sidecar.py"
    script.write_text(_FAKE_SIDECAR, encoding="utf-8")
    real = shutil.which("python3") or sys.executable
    shim = tmp_path / "python3"
    shim.write_text(f'#!/bin/sh\nexec "{real}" "{script}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    env = dict(os.environ)
    env["SELEKYT_PYTHON"] = str(shim)
    with _bridge(env) as base:
        yield base


def test_ping_reports_busy_instead_of_hanging_behind_a_running_request(fake_base):
    """``/api/ping`` is the page's liveness probe and used to wait for whatever
    was in front of it. The sidecar is one thread, so while it serves a request
    it cannot even *read* a probe, let alone answer one — the probe's response
    could only arrive after that request completed, and the page read an engine
    that was working as unreachable for the whole run.

    What is fixed is the hang: the probe returns in bounded time with a report
    that says the engine is busy rather than dead, and the request that held it
    is neither cancelled nor disturbed. That the probe cannot be answered at
    all while a run is in flight is a property of the single-threaded loop, and
    is stated as still-open in the audit rather than claimed as solved here.

    The old FIFO fails this test twice over: the probe returned only after the
    sleep (so ``elapsed`` clears it), and it returned carrying the slow
    request's answer instead of its own."""
    box = {}

    def _slow():
        box["resp"] = _post(fake_base, "segment", {"mode": "slow"})

    worker = threading.Thread(target=_slow)
    worker.start()
    # Let the slow request reach the sidecar first, so the probe genuinely
    # arrives while it is being served rather than before it.
    time.sleep(0.75)
    started = time.time()
    ping = _post_or_error(fake_base, "ping", {})
    elapsed = time.time() - started
    assert elapsed < _PROBE_BACK_BY, (
        f"the probe waited {elapsed:.1f}s behind a request it cannot be served "
        "ahead of"
    )
    assert ping["ok"] is False, \
        "a probe behind a running request is busy, not successfully answered"
    assert "single-threaded" in ping["error"], ping
    # The request that held the sidecar still completes with its own answer —
    # the busy report cost it nothing.
    worker.join(timeout=30)
    assert worker.is_alive() is False, "the in-flight request was disturbed"
    assert box["resp"]["ok"] is True, box["resp"]
    assert box["resp"]["data"]["marker"] == "slow", \
        f"the slow request did not get its own answer: {box['resp']}"


def test_a_line_printed_past_the_protocol_does_not_steal_the_answer(fake_base):
    """A library that writes to fd 1 directly emits a line that is a response to
    nothing. Charging the next pending request for it is the bug: under the old
    queue that request was answered with an "invalid response" error about a
    line it never sent, and the answer that was really for it was then handed to
    the request behind it."""
    resp = _post(fake_base, "segment", {"mode": "stray"})
    assert resp["ok"] is True, resp
    assert resp["data"]["marker"] == "stray", resp


def test_an_envelope_for_an_id_no_one_sent_is_dropped(fake_base):
    """The other half of correlation: a line that parses and looks like a
    response but names no live request must not be matched to one, or the
    request waiting for the real answer gets an empty one and never complains."""
    resp = _post(fake_base, "segment", {"mode": "foreign"})
    assert resp["ok"] is True, resp
    assert resp["data"]["marker"] == "foreign", resp


def test_answers_arriving_out_of_order_still_reach_their_request(fake_base):
    """Correlation is by id rather than by position, so a response that arrives
    before an earlier request's has to land on the request that asked for it.
    Under a FIFO the first request is handed the second's answer and neither
    error surfaces — both responses are well-formed and the mismatch is silent."""
    box = {}

    def _first():
        box["resp"] = _post(fake_base, "segment", {"mode": "first"})

    worker = threading.Thread(target=_first)
    worker.start()
    time.sleep(0.2)
    second = _post(fake_base, "segment", {"mode": "second"})
    worker.join(timeout=30)
    assert second["data"]["marker"] == "second", second
    assert box["resp"]["data"]["marker"] == "first", \
        f"the first request received the second's answer: {box['resp']}"


def test_a_dead_sidecar_names_the_op_it_died_serving_and_is_replaced(fake_base):
    """A crash rejects every queued request, which is correct — the sidecar is
    gone and none of them will be answered. What the rejection has to carry is
    which op it was, because "the sidecar exited" answers a queue of different
    questions with the same words, and the stderr that explains why has to
    travel with it."""
    resp = _post_or_error(fake_base, "segment", {"mode": "die"})
    assert resp["ok"] is False, "the fake sidecar exits on this request"
    assert resp["op"] == "segment", "the failure must name the op it was serving"
    assert "dying on purpose" in resp["error"], resp
    # The dead child has to be replaced rather than remembered, or every later
    # request is written to a pipe nothing is reading.
    data = _expect_ok(fake_base, "ping", {})
    assert data["engine"] == "fake"
