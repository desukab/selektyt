// SelekyT shell harness - zero-dependency Node HTTP bridge to the Python
// sidecar (`python3 -m selekyt --server`, JSON-RPC over stdin/stdout).
//
//   node web/server.mjs [--port PORT]
//
// Serves a static UI from web/ and proxies analysis ops to the sidecar. The
// UI and this bridge are designed to be reused unchanged inside the Tauri
// webview later (the eventual Rust layer replaces this HTTP process, not the
// frontend). Print "LISTEN host:port" on stdout once ready.

import { createServer } from "node:http";
import { spawn, spawnSync } from "node:child_process";
import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
  writeSync,
} from "node:fs";
import { basename, dirname, extname, join, normalize, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import {
  encodeWav,
  makeSampleWav,
} from "./sample.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(ROOT, "web");
const OUT_WEB = join(ROOT, "out", "web");
const UPLOADS = join(OUT_WEB, "uploads");
const SAMPLE_NAME = "sample.wav";
const SAMPLE_PATH = join(OUT_WEB, SAMPLE_NAME);
const MAX_BODY = 32 * 1024 * 1024;
// A source the user points the shell at is the product's input, and a 1-3 hour
// master is single-digit gigabytes, so the JSON body cap above cannot apply to
// it — that cap exists to keep a hostile or buggy client from making the server
// buffer a body in memory, and the upload route streams to disk instead. The
// guard that replaces it is a cap on what lands on disk, and it is generous by
// default and overridable rather than silently unbounded: filling this host's
// only volume is a real failure mode, and an upload that never ends should not
// be able to cause it.
const MAX_UPLOAD_BYTES = Number(process.env.SELEKYT_MAX_UPLOAD_GB || 32) * 1024 ** 3;
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".wav": "audio/wav",
  ".js": "text/javascript; charset=utf-8",
};

// What `python -m selekyt --server` actually has to import. Probing this
// rather than merely `--version` is what catches an interpreter that is on
// PATH but cannot run the sidecar — the classic case is a project venv that
// was never activated: `node web/server.mjs` then picked a bare `python3`
// that had no cv2/yt-dlp, and the only diagnostic was a traceback on the
// server's own stderr and an HTTP "sidecar exited before answering".
const SIDECAR_IMPORT = "from selekyt.cli import main";

function probePython(cand) {
  try {
    const r = spawnSync(cand, ["-c", SIDECAR_IMPORT], {
      stdio: ["ignore", "pipe", "pipe"],
      encoding: "utf8",
    });
    return r.status === 0;
  } catch {
    return false;
  }
}

function pickPython() {
  if (process.env.SELEKYT_PYTHON) return process.env.SELEKYT_PYTHON;
  const candidates = [];
  // A project-local virtualenv is the usual install target and is not
  // necessarily on PATH (created without activation), so it is tried first.
  for (const rel of [".venv/bin/python", "venv/bin/python"]) {
    const p = join(ROOT, rel);
    if (existsSync(p)) candidates.push(p);
  }
  candidates.push("python3", "python");
  for (const cand of candidates) if (probePython(cand)) return cand;
  // Nothing could import the sidecar. Return the first existing interpreter
  // so the caller still gets a real error from the child instead of ENOENT.
  for (const cand of candidates) {
    try {
      spawnSync(cand, ["--version"], { stdio: "ignore" });
      return cand;
    } catch {
      /* try next */
    }
  }
  return "python3";
}

const PYTHON = pickPython();

// ── persistent sidecar child with a correlated request queue ──

// Every request carries an id the sidecar echoes back, so a response is
// matched to the request that asked for it rather than to whichever request
// happened to be first in line. That difference is not theoretical: the wire
// is stdout, and a library deep in the pipeline that prints to fd 1 directly —
// CTranslate2 has done it from under faster-whisper — emits a line that is a
// response to nothing. Under a pure FIFO that line was consumed as one and the
// request behind it silently received the answer to the request in front of
// it, or an "invalid response" error about a line it never sent. With ids, a
// line that names no pending request is dropped and logged instead.
let child = null;
const pending = [];
let nextId = 1;
let outBuf = "";

function _settle(entry, resp) {
  if (entry.timer) clearTimeout(entry.timer);
  entry.resolve(resp);
}

function _fail(entry, error) {
  // Removing the entry matters as much as resolving it: a promise settled here
  // would still be in the queue, and the next response to arrive would settle
  // it a second time. That is a no-op for the promise, but it silently shifts
  // every response after it by one.
  const at = pending.indexOf(entry);
  if (at >= 0) pending.splice(at, 1);
  _settle(entry, { ok: false, op: entry.op, error });
}

function spawnChild() {
  // stderr is captured, not inherited: a sidecar that cannot start (missing
  // dependency, wrong interpreter, broken install) dies before it can answer,
  // and "sidecar exited before answering" alone tells nobody why. The tail is
  // attached to the error the HTTP caller receives.
  const errBuf = [];
  const c = spawn(PYTHON, ["-m", "selekyt", "--server"], {
    stdio: ["pipe", "pipe", "pipe"],
  });
  c.stdout.setEncoding("utf8");
  c.stdout.on("data", (chunk) => {
    outBuf += chunk;
    let i;
    while ((i = outBuf.indexOf("\n")) >= 0) {
      const raw = outBuf.slice(0, i).trim();
      outBuf = outBuf.slice(i + 1);
      if (!raw) continue;
      let resp;
      try {
        resp = JSON.parse(raw);
      } catch {
        // The stream is JSON-only by contract, so an unparseable line is not a
        // malformed answer: it is a line that was never an answer at all. It
        // belongs to no request, and charging one for it is the mismatch above.
        console.error(
          `sidecar: dropping a non-JSON line from the JSON stream: ${raw.slice(0, 200)}`
        );
        continue;
      }
      const id = resp?.id;
      let entry = null;
      if (id !== undefined && id !== null) {
        const at = pending.findIndex((e) => e.id === id);
        if (at < 0) {
          console.error(
            `sidecar: a response for id ${id} matched no pending request; dropping it`
          );
          continue;
        }
        entry = pending.splice(at, 1)[0];
      } else {
        // A sidecar that does not echo ids — an older release, or the Rust
        // worker spoken to directly — still answers strictly in order, which
        // is what the single-threaded loop guarantees.
        entry = pending.shift();
        if (!entry) {
          console.error(
            `sidecar: a response arrived with no request waiting: ${raw.slice(0, 200)}`
          );
          continue;
        }
      }
      _settle(entry, resp);
    }
  });
  c.stderr.setEncoding("utf8");
  c.stderr.on("data", (chunk) => {
    errBuf.push(chunk);
    if (errBuf.length > 16) errBuf.shift();
  });
  c.on("error", (err) => {
    const entry = pending.shift();
    if (entry) _fail(entry, `sidecar failed to start: ${err.message}`);
  });
  c.stdin.on("error", (err) => {
    // The pipe to the child broke, so nothing queued can be sent. Leaving those
    // promises unresolved is worse than an error: a hung request is
    // indistinguishable from a working one, and the page waiting on it never
    // says why. The child itself is left alone — a broken stdin is usually the
    // aftermath of the child having died, in which case its own exit handler
    // below has already cleared the queue, and if it has not, the process reads
    // EOF and exits on its own. Killing it here could instead cut short a
    // render that was still progressing.
    for (const entry of pending.splice(0)) {
      _settle(entry, { ok: false, op: entry.op, error: `the sidecar pipe failed: ${err.message}` });
    }
    child = null;
  });
  c.on("exit", (code, signal) => {
    child = null;
    const detail = errBuf.join("").trim();
    const error = detail
      ? `sidecar exited before answering (code ${code ?? signal})\n${detail.slice(-2000)}`
      : `sidecar exited before answering (code ${code ?? signal})`;
    let entry;
    while ((entry = pending.shift())) {
      // The op is named per request because "the sidecar died" answers a
      // queue of different questions with the same words otherwise, and the
      // one a user was watching is not identifiable from the error.
      _settle(entry, { ok: false, op: entry.op, error });
    }
  });
  return c;
}

function rpc(op, params = {}, { timeoutMs = 0 } = {}) {
  if (!child) child = spawnChild();
  const id = nextId++;
  return new Promise((resolve) => {
    const entry = { id, op, resolve };
    pending.push(entry);
    if (timeoutMs > 0) {
      entry.timer = setTimeout(() => {
        _fail(
          entry,
          `the sidecar has not answered '${op}' in ${timeoutMs / 1000}s. It is ` +
            `single-threaded, so a request still running holds it: this is a ` +
            `busy report, not a crash, and the in-flight request is untouched.`
        );
      }, timeoutMs);
    }
    try {
      child.stdin.write(`${JSON.stringify({ op, params, id })}\n`);
    } catch (err) {
      // A write to a dead pipe throws synchronously inside the executor, which
      // would reject this promise and still leave its entry in the queue — the
      // response after it then settles an already-rejected promise and the one
      // after that gets the answer meant for this one.
      _fail(entry, `could not send '${op}' to the sidecar: ${err.message}`);
    }
  });
}

async function rpcData(op, params = {}, options = {}) {
  const resp = await rpc(op, params, options);
  if (!resp.ok) throw new Error(resp.error || `op failed: ${op}`);
  return resp.data;
}

// ── HTTP helpers ──

function send(res, status, body, headers = {}) {
  res.writeHead(status, headers);
  res.end(body);
}

function sendJson(res, payload) {
  send(res, 200, JSON.stringify(payload), {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
}

function readBody(req) {
  return new Promise((resolvePromise, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new Error("body too large"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => resolvePromise(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

// A browser attaches `Origin` to every POST it sends — same-origin fetches
// included — and it cannot be suppressed or forged from a page, so comparing
// it is the CSRF gate. Before this, a page at any origin could drive the whole
// pipeline: `getJson` parsed whatever body arrived, so a plain HTML form with
// `enctype="text/plain"` carrying JSON reached `JSON.parse` with no CORS
// preflight, and the mutating routes (`run_pipeline`, `export`,
// `vad_analyze`, …) were all open. 127.0.0.1 binding does not help, because
// the browser driving that form *is* on this host.
//
// Requests with neither `Origin` nor `Referer` are accepted: those are curl, a
// script or the test suite, and a localhost bind already treats any local
// process as trusted — the threat closed here is a *remote* page reaching a
// local service, not a local client.
const JSON_CT = /^application\/json(?:\s*;|$)/i;

function _within(origin, url) {
  return url === origin || url.startsWith(origin + "/");
}

function assertSameOrigin(req, expectedOrigin) {
  const origin = req.headers.origin;
  const referer = req.headers.referer;
  if (origin) {
    if (!_within(origin, expectedOrigin)) {
      throw new Error(
        `refusing a cross-origin POST from ${origin}: this server serves the ` +
          `page and its API from one origin (${expectedOrigin}) and accepts no other`
      );
    }
    return;
  }
  // `Referrer-Policy: no-referrer` on an attacker's page can suppress Referer
  // but not Origin on a POST, so a Referer check alone would be weaker; it is
  // the fallback for the browsers and clients that send only it.
  if (referer && !_within(referer, expectedOrigin)) {
    throw new Error(`refusing a cross-origin POST referred from ${referer}`);
  }
}

function getJson(req, expectedOrigin) {
  // The Content-Type gate is the other half: it makes the form-POST primitive
  // above a non-starter, since a form cannot send application/json.
  const ct = req.headers["content-type"] || "";
  if (!JSON_CT.test(ct)) {
    throw new Error(
      `POST bodies must be application/json (got ${ct || "no Content-Type"}); ` +
        "a cross-origin HTML form cannot send that, which is the point"
    );
  }
  return readBody(req).then((raw) => {
    try {
      return JSON.parse(raw || "{}");
    } catch {
      throw new Error("invalid JSON body");
    }
  });
}

function sanitizeName(name, fallback = "upload.wav") {
  const base = String(name || fallback).replace(/[^\w.\-]/g, "_");
  return base.length > 0 && !base.startsWith(".") ? base : fallback;
}

function guardedPath(raw) {
  // ``startsWith(ROOT)`` alone is bypassable: a sibling directory that shares
  // the project's name prefix (``/project-secret`` next to ``/project``)
  // string-matches and reads as inside. The boundary must include the
  // separator, and the project root itself must still pass.
  const candidate = resolve(String(raw || ""));
  if (candidate === ROOT || candidate.startsWith(ROOT + sep)) {
    return candidate;
  }
  throw new Error("path must be inside the project");
}

// ``guardedPath`` is for a source file the user points the pipeline at, so it
// is the repo root. ``guardedArtifact`` is for a file the shell hands out over
// HTTP, and the two must not be the same boundary: anything inside the repo
// root is then readable by any unauthenticated localhost caller, which
// includes ``.env``, ``.git/`` and ``pipeline_state.json`` with its yt-dlp
// blob. Only artifacts the pipeline wrote under ``out/web`` are downloads.
function guardedArtifact(raw) {
  const candidate = resolve(String(raw || ""));
  if (candidate.startsWith(OUT_WEB + sep)) {
    return candidate;
  }
  throw new Error("path must be an artifact under out/web");
}

// ── video upload: multipart streamed to disk ──

// The shell's intake used to be "type a path inside the project", which is not
// something a consumer of the desktop shell can do — the path has to be one the
// shell itself wrote, and nothing could write one. The route below is that
// writer. It lands the file under out/web/uploads, which is inside the project
// root, so the existing `guardedPath` check admits it as a pipeline source with
// no widening of that boundary: an uploaded file gets exactly the same treatment
// as one the user found on disk.
//
// The body is never buffered. A 32 MB JSON cap is not a sane cap for a master,
// and buffering gigabytes would make the shell unusable on the machine it is
// serving, so the part is scanned off the wire one chunk at a time and only the
// tail needed to recognise a boundary is held back.

function _dispositionFilename(headers) {
  // `filename` is quoted and may itself contain an escaped quote; the basename
  // is all that survives sanitisation anyway, so a permissive match is right.
  const disp = headers.find((h) => h.startsWith("Content-Disposition:"));
  if (!disp) return null;
  const m = /filename\s*=\s*(?:"([^"]*)"|([^;\s]+))/i.exec(disp);
  return m ? (m[1] ?? m[2]) : null;
}

function _partHeaders(block) {
  // The header block ends at the first blank line; the bytes after it are body.
  const at = block.indexOf("\r\n\r\n");
  if (at < 0) return null;
  const raw = block.slice(0, at).split("\r\n");
  return {
    headers: raw,
    filename: _dispositionFilename(raw),
    bodyFrom: at + 4,
  };
}

// Streams the first file part of a multipart body to `target`, resolving with
// the number of bytes written and the filename the sending browser announced
// for it. Everything after the part's closing delimiter is discarded, which is
// why the caller's form is expected to carry one file field.
function _streamFilePart(req, boundary, target, maxBytes) {
  return new Promise((resolvePromise, reject) => {
    const delim = Buffer.concat([Buffer.from("\r\n--"), Buffer.from(boundary)]);
    // A delimiter may straddle a chunk, so the tail that could still be the
    // start of one is withheld from every write and reconsidered with the next
    // chunk. This is the whole reason the scanner is incremental.
    let pending = Buffer.alloc(0);
    let headers = null;
    let total = 0;
    let fd = null;
    let done = false;

    const finish = (err) => {
      if (done) return;
      done = true;
      req.removeListener("data", onData);
      req.removeListener("end", onEnd);
      req.removeListener("error", onErr);
      if (fd !== null) closeSync(fd);
      if (err) {
        // A rejected upload leaves a partial file behind, and a partial file is
        // indistinguishable from a master by name alone — the next caller would
        // transcribe half a video and report timing for the whole thing.
        try {
          unlinkSync(target);
        } catch {
          /* already gone */
        }
        reject(err);
      } else {
        resolvePromise({ bytes: total, filename: headers?.filename || null });
      }
    };

    const onEnd = () =>
      finish(new Error("upload ended before the file part was complete"));

    const onErr = (e) => finish(new Error(String(e.message || e)));

    const onData = (chunk) => {
      if (done) return;
      pending = Buffer.concat([pending, chunk]);
      if (!headers) {
        const head = _partHeaders(pending.toString("latin1"));
        if (!head) return; // headers not fully received yet
        headers = head;
        if (!head.filename) {
          return finish(
            new Error(
              "the uploaded form field is not a file; send the video as a " +
                "file input, not a text field"
            )
          );
        }
        pending = pending.slice(head.bodyFrom);
      }

      while (!done) {
        // No delimiter so far: write everything except the tail that might be
        // the beginning of one.
        let at = pending.indexOf(delim);
        if (at < 0) {
          const keep = Math.min(pending.length, delim.length - 1);
          const writeable = pending.subarray(0, pending.length - keep);
          if (writeable.length) {
            if (total + writeable.length > maxBytes) {
              return finish(
                new Error(
                  `upload exceeds the ${Math.round(maxBytes / 1024 ** 3)} GiB cap; ` +
                    "raise SELEKYT_MAX_UPLOAD_GB to accept a longer master"
                )
              );
            }
            if (fd === null) fd = openSync(target, "w");
            writeSync(fd, writeable);
            total += writeable.length;
          }
          pending = pending.subarray(pending.length - keep);
          return;
        }
        // Delimiter found: everything up to it is the file, and the rest of the
        // body is other parts, which this shell does not use.
        const writeable = pending.subarray(0, at);
        if (writeable.length) {
          if (total + writeable.length > maxBytes) {
            return finish(new Error("upload exceeds the size cap"));
          }
          if (fd === null) fd = openSync(target, "w");
          writeSync(fd, writeable);
          total += writeable.length;
        }
        req.removeListener("data", onData);
        return finish(null);
      }
    };

    req.on("data", onData);
    req.on("end", onEnd);
    req.on("error", onErr);
  });
}

function _boundary(contentType) {
  const m = /boundary\s*=\s*(?:"([^"]+)"|([^;\s]+))/i.exec(contentType || "");
  return m ? m[1] ?? m[2] : null;
}

// An upload should be rejected as "not a video" here rather than five minutes
// into a transcribe, and the probe that decides it is the one the encoder
// family already provides — ffprobe is a documented requirement of the
// pipeline, not a new dependency.
//
// Two failures look identical in ``spawnSync``'s output and have opposite
// correct answers, so they are separated here: an absent ffprobe is the host
// missing a requirement and the pipeline probes the source itself and reports
// the same problem from its own side, whereas a *present* ffprobe that cannot
// read the bytes is a file that is not media, and keeping it would leave a
// document in the uploads directory that the next caller transcribes. ``null``
// means "could not check" and an object means "checked, and this is the answer".
function _probeMedia(path) {
  try {
    const probe = spawnSync(
      "ffprobe",
      ["-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
      { stdio: ["ignore", "pipe", "ignore"], encoding: "utf8", timeout: 30000 }
    );
    if (probe.error) return null;
    if (probe.status !== 0 || !probe.stdout) return { duration: 0, video: false, audio: false };
    const parsed = JSON.parse(probe.stdout);
    const streams = Array.isArray(parsed.streams) ? parsed.streams : [];
    return {
      duration: Number(parsed.format?.duration || 0) || 0,
      video: streams.some((s) => s.codec_type === "video"),
      audio: streams.some((s) => s.codec_type === "audio"),
    };
  } catch {
    return null;
  }
}

// The page posts this string back as a pipeline source, and it is the same form
// the path-typing control has always used (`out/web/uploads/...`), so the two
// intake paths produce interchangeable values. `guardedPath` resolves it from
// the server's working directory either way.
function _relativeToRoot(abs) {
  return relative(ROOT, abs).split(sep).join("/");
}

function _storedName(original) {
  // The sanitiser strips directory components and anything a shell would
  // interpret, so a hostile `filename` cannot place the upload outside the
  // uploads directory. A collision is de-duplicated rather than clobbered: two
  // uploads of `interview.mp4` silently overwriting each other would look like
  // the first upload's selection coming from the second video's timings.
  const base = sanitizeName(original, "upload.mp4");
  let name = base;
  let n = 1;
  while (existsSync(join(UPLOADS, name))) {
    const ext = extname(base) || "";
    const stem = basename(base, ext);
    name = `${stem}-${n}${ext}`;
    n += 1;
  }
  return name;
}

function ensureSample() {
  if (!existsSync(SAMPLE_PATH)) {
    mkdirSync(OUT_WEB, { recursive: true });
    writeFileSync(SAMPLE_PATH, makeSampleWav());
  }
}

async function resolveMedia(body) {
  if (body.wav) {
    mkdirSync(UPLOADS, { recursive: true });
    const name = sanitizeName(body.name);
    const target = join(UPLOADS, name);
    const wav = Buffer.from(body.wav, "base64");
    if (wav.length < 44) throw new Error("uploaded file is too small to be a WAV");
    writeFileSync(target, wav);
    return target;
  }
  if (body.sample || body.path === SAMPLE_NAME) {
    ensureSample();
    return SAMPLE_PATH;
  }
  if (body.path) {
    const path = guardedPath(body.path);
    if (!existsSync(path)) throw new Error(`no such file: ${path}`);
    return path;
  }
  throw new Error("provide 'path', 'sample', or a base64 'wav'");
}

// ── routes ──

// A project directory is a run's state machine: pipeline_state.json records
// what has been rendered so a resume picks up where it left off. Two runs into
// one directory do not share that machine, they fight over it — the second's
// state write lands over the first's renders list, and the first then resumes
// against a state that no longer describes its own work. The atomic write means
// neither payload is corrupted, which is what made this a silent disagreement
// instead of a crash: last writer wins, and nothing says which run's answer the
// state file describes. Holding a directory while a run owns it makes the
// second caller fail loudly instead.
const runsByDir = new Map();

function _acquireRun(dir, op) {
  if (runsByDir.has(dir)) {
    throw new Error(
      `a ${runsByDir.get(dir)} is already running in ${dir}; wait for it to ` +
        `finish before starting another. Two runs into one project directory ` +
        `each overwrite the other's state, so the second was refused rather ` +
        `than allowed to corrupt the first.`
    );
  }
  runsByDir.set(dir, op);
}

function _releaseRun(dir) {
  runsByDir.delete(dir);
}

async function _runExclusive(dir, op, fn) {
  _acquireRun(dir, op);
  try {
    return await fn();
  } finally {
    _releaseRun(dir);
  }
}

async function route(req, res, url, method) {
  const expectedOrigin = `http://${req.headers.host || url.host}`;
  if ((method === "GET" || method === "POST") && url.pathname === "/api/ping") {
    // Ping is the page's liveness probe and is the one request that cannot
    // wait for the pipeline: the sidecar is a single thread, so while it
    // transcribes it answers nothing, and without a deadline the probe would
    // hang for the length of a run and report an engine that is working as
    // unreachable. The report when the deadline fires is a busy one — the
    // in-flight request is not cancelled, disturbed or killed, because a hang
    // and a long transcribe are not distinguishable from here.
    return sendJson(res, {
      ok: true,
      op: "ping",
      data: await rpcData("ping", {}, { timeoutMs: 5000 }),
    });
  }
  if (method === "GET" && url.pathname === "/api/sample.wav") {
    ensureSample();
    return send(res, 200, readFileSync(SAMPLE_PATH), {
      "Content-Type": "audio/wav",
      "Content-Length": readFileSync(SAMPLE_PATH).length,
    });
  }
  if (method === "POST" && url.pathname === "/api/upload") {
    // This is the "here is my long video" intake, and it is the one route that
    // cannot use getJson: a 1-3 hour master is single-digit gigabytes of
    // multipart body, and getJson would buffer all of it into memory before it
    // could be refused. The body is streamed to disk instead — see
    // _streamFilePart, which never holds more than a chunk in memory — and the
    // same-origin gate that closes the CSRF hole on the other POSTs applies
    // here too, so a hostile page cannot make the browser post a form body at
    // this route and land a file on disk.
    assertSameOrigin(req, expectedOrigin);
    const boundary = _boundary(req.headers["content-type"] || "");
    if (!boundary) {
      throw new Error(
        "expected multipart/form-data with a boundary; set the request's " +
          "Content-Type to multipart/form-data"
      );
    }
    mkdirSync(UPLOADS, { recursive: true });
    // The filename the browser announces arrives inside the body, not in the
    // request headers, so the part is streamed under a scratch name and the
    // stored name is taken from what the scanner parsed out of the first chunk.
    const part = join(UPLOADS, `.upload-${process.pid}-${Date.now()}.part`);
    // A transfer that dies at the transport layer is reported to the
    // ``clientError`` handler, which sees the socket but not this route — and
    // the parser never delivers the ``error`` event this route would need to
    // clean up after itself, so the path is handed to the socket. Without this a
    // dropped connection leaves a .part behind that the next caller would
    // transcribe as though it were a whole master.
    req.socket.__uploadPart = part;
    let stored, probed;
    try {
      const { filename } = await _streamFilePart(req, boundary, part, MAX_UPLOAD_BYTES);
      // Everything is written to a .part and only renamed once the stream is
      // whole and the probe has confirmed it is media: a truncated or non-video
      // upload leaves no file behind, so nothing incomplete can later be picked
      // up as a pipeline source by its name alone.
      const name = _storedName(filename);
      probed = _probeMedia(part);
      if (probed && !probed.video && !probed.audio) {
        throw new Error(
          `${name} is not a media file ffprobe can read; the upload was not kept`
        );
      }
      stored = join(UPLOADS, name);
      renameSync(part, stored);
      // The transfer arrived whole, so the socket no longer has a partial to
      // clean up on this route's behalf.
      delete req.socket.__uploadPart;
    } catch (err) {
      if (existsSync(part)) unlinkSync(part);
      delete req.socket.__uploadPart;
      throw err;
    }
    // The stored path is what the page puts back into the cuts control, and it
    // is inside ROOT, so guardedPath admits it as a pipeline source without the
    // upload route having to widen any boundary.
    return sendJson(res, {
      ok: true,
      op: "upload",
      data: {
        path: _relativeToRoot(stored),
        name: basename(stored),
        duration: probed?.duration ?? null,
        video: probed?.video ?? null,
        audio: probed?.audio ?? null,
      },
    });
  }
  if (method === "POST" && url.pathname === "/api/vad_analyze") {
    assertSameOrigin(req, expectedOrigin);
    const body = await getJson(req, expectedOrigin);
    const path = await resolveMedia(body);
    const data = await rpcData("vad_analyze", {
      path,
      threshold: body.threshold,
      min_speech_ms: body.min_speech_ms,
      min_silence_ms: body.min_silence_ms,
      return_scores: true,
    });
    return sendJson(res, { ok: true, op: "vad_analyze", data });
  }
  if (method === "POST" && url.pathname === "/api/export") {
    assertSameOrigin(req, expectedOrigin);
    const body = await getJson(req, expectedOrigin);
    const media = await resolveMedia(body);
    const project = join(OUT_WEB, "project");
    const data = await _runExclusive(project, "export", () =>
      rpcData("audio_analysis", {
        media,
        project,
        source: body.source || String(media),
        min_speech_ms: body.min_speech_ms,
        min_silence_ms: body.min_silence_ms,
        silence_threshold: body.silence_threshold,
        silence_padding: body.silence_padding,
        keep_pauses: body.keep_pauses,
        remove_filler: body.remove_filler,
        export_fcpxml: body.export_fcpxml !== false,
      })
    );
    return sendJson(res, { ok: true, op: "export", data });
  }
  if (method === "POST" && url.pathname === "/api/run_pipeline") {
    assertSameOrigin(req, expectedOrigin);
    const body = await getJson(req, expectedOrigin);
    // A local file must stay inside the project (same guard as /api/file).
    // A URL is fetched by the pipeline itself, which is what the product is
    // for; only the http(s) schemes survive the sidecar's own check.
    let source;
    if (/^https?:\/\//i.test(String(body.source || ""))) {
      source = String(body.source);
    } else if (body.path) {
      source = guardedPath(body.path);
      if (!existsSync(source)) throw new Error(`no such file: ${source}`);
    } else {
      throw new Error("provide 'path' (a file inside the project) or an http(s) 'source' URL");
    }
    const out = join(OUT_WEB, "pipeline");
    const data = await _runExclusive(out, "run_pipeline", () =>
      rpcData("run_pipeline", {
        source,
        out,
        model: body.model,
        language: body.language,
        limit: body.limit,
        chunk_minutes: body.chunk_minutes,
        beam_size: body.beam_size,
        cpu_threads: body.cpu_threads,
        caption_style: body.caption_style || undefined,
        audio_analysis: body.audio_analysis ?? false,
        export_fcpxml: body.export_fcpxml ?? true,
        // A cookies file is a path the pipeline hands to yt-dlp; it is never read
        // here, never echoed into a response, and deliberately NOT confined to
        // the project — a cookie file inside the repo is one `git add` away from
        // being leaked, so the user's own home is where it belongs.
        cookies: body.cookies || undefined,
      })
    );
    return sendJson(res, { ok: true, op: "run_pipeline", data });
  }
  if (method === "GET" && url.pathname === "/api/file") {
    const file = guardedArtifact(url.searchParams.get("path") || "");
    if (!existsSync(file)) throw new Error(`no such file: ${file}`);
    return send(res, 200, readFileSync(file), {
      "Content-Type": MIME[extname(file)] || "application/octet-stream",
      "Content-Disposition": `attachment; filename="${sanitizeName(basename(file))}"`,
    });
  }
  if (method === "POST" && url.pathname === "/api/segment") {
    assertSameOrigin(req, expectedOrigin);
    const body = await getJson(req, expectedOrigin);
    return sendJson(res, await rpc("segment", body));
  }
  if (method === "POST" && url.pathname === "/api/cutlist_from_segments") {
    assertSameOrigin(req, expectedOrigin);
    const body = await getJson(req, expectedOrigin);
    return sendJson(res, await rpc("cutlist_from_segments", body));
  }
  if (method === "GET") {
    const rel = url.pathname === "/" ? "/index.html" : url.pathname;
    const file = join(WEB, normalize(rel));
    if (file.startsWith(WEB) && existsSync(file)) {
      return send(res, 200, readFileSync(file), {
        "Content-Type": MIME[extname(file)] || "application/octet-stream",
      });
    }
    return send(res, 404, "not found", { "Content-Type": "text/plain" });
  }
  return send(res, 405, "method not allowed", { "Content-Type": "text/plain" });
}

function main() {
  const args = process.argv.slice(2);
  const portFlag = args.indexOf("--port");
  const port = portFlag >= 0 ? Number(args[portFlag + 1]) : 4242;
  const host = process.env.SELEKYT_HOST || "127.0.0.1";
  const server = createServer((req, res) => {
    const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
    route(req, res, url, req.method).catch((err) => {
      // A request that failed at the transport layer — a body that ended early —
      // has already been answered by the `clientError` handler, and writing to
      // that socket again would throw inside this catch and take the server down
      // with it. Nothing this process can tell the client would reach it anyway.
      if (res.headersSent || res.writableEnded || res.socket?.writableEnded) return;
      sendJson(res, { ok: false, op: url.pathname, error: String(err.message || err) });
    });
  });
  // A transfer that dies mid-body — the connection dropped, or the user closed
  // the tab — is caught by Node's HTTP parser, not by the route, and Node's
  // default answer is a bare 400 with no body the page can read. Answering with
  // the same envelope every other route uses means the browser shows what
  // actually happened instead of a generic network failure it cannot tell apart
  // from the server having vanished.
  server.on("clientError", (err, socket) => {
    // ``err.message`` here is the parser's own ("Parse Error"), which says
    // nothing a user could act on, and the one thing that distinguishes a
    // truncated upload from a genuinely malformed request is not in the
    // message. The claim this can make is the one that matters: nothing was
    // kept from a request that did not arrive whole.
    const body = JSON.stringify({
      ok: false,
      op: "upload",
      error: "the request did not arrive complete; the upload was not kept",
    });
    // The route cannot clean up after a transport-level failure — the parser
    // does not hand it the event — so it left the path on the socket for this
    // handler to find.
    const part = socket.__uploadPart;
    if (part) {
      try {
        unlinkSync(part);
      } catch {
        /* already gone */
      }
      delete socket.__uploadPart;
    }
    socket.end(
      "HTTP/1.1 400 Bad Request\r\n" +
        "Content-Type: application/json; charset=utf-8\r\n" +
        `Content-Length: ${Buffer.byteLength(body)}\r\n` +
        "Connection: close\r\n\r\n" +
        body,
    );
  });
  server.listen(port, host, () => {
    const addr = server.address();
    console.log(`LISTEN ${addr.address}:${addr.port}`);
  });
}

main();