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
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, extname, join, normalize, resolve, sep } from "node:path";
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

// ── persistent sidecar child with FIFO request queue ──

let child = null;
const pending = [];
let outBuf = "";

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
      const line = outBuf.slice(0, i).trim();
      outBuf = outBuf.slice(i + 1);
      if (!line) continue;
      const entry = pending.shift();
      if (!entry) continue;
      try {
        entry.resolve(JSON.parse(line));
      } catch {
        entry.resolve({ ok: false, error: `sidecar sent a non-JSON line: ${line}` });
      }
    }
  });
  c.stderr.setEncoding("utf8");
  c.stderr.on("data", (chunk) => {
    errBuf.push(chunk);
    if (errBuf.length > 16) errBuf.shift();
  });
  c.on("error", (err) => {
    const entry = pending.shift();
    if (entry) entry.resolve({ ok: false, error: `sidecar failed to start: ${err.message}` });
  });
  c.on("exit", (code, signal) => {
    child = null;
    const detail = errBuf.join("").trim();
    const error = detail
      ? `sidecar exited before answering (code ${code ?? signal})\n${detail.slice(-2000)}`
      : `sidecar exited before answering (code ${code ?? signal})`;
    let entry;
    while ((entry = pending.shift())) {
      entry.resolve({ ok: false, error });
    }
  });
  return c;
}

function rpc(op, params = {}) {
  if (!child) child = spawnChild();
  return new Promise((resolvePromise) => {
    pending.push({ resolve: resolvePromise });
    child.stdin.write(`${JSON.stringify({ op, params })}\n`);
  });
}

async function rpcData(op, params = {}) {
  const resp = await rpc(op, params);
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

function sanitizeName(name) {
  const base = String(name || "upload.wav").replace(/[^\w.\-]/g, "_");
  return base.length > 0 && !base.startsWith(".") ? base : "upload.wav";
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

async function route(req, res, url, method) {
  const expectedOrigin = `http://${req.headers.host || url.host}`;
  if ((method === "GET" || method === "POST") && url.pathname === "/api/ping") {
    return sendJson(res, { ok: true, op: "ping", data: await rpcData("ping") });
  }
  if (method === "GET" && url.pathname === "/api/sample.wav") {
    ensureSample();
    return send(res, 200, readFileSync(SAMPLE_PATH), {
      "Content-Type": "audio/wav",
      "Content-Length": readFileSync(SAMPLE_PATH).length,
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
    const data = await rpcData("audio_analysis", {
      media,
      project: join(OUT_WEB, "project"),
      source: body.source || String(media),
      min_speech_ms: body.min_speech_ms,
      min_silence_ms: body.min_silence_ms,
      silence_threshold: body.silence_threshold,
      silence_padding: body.silence_padding,
      keep_pauses: body.keep_pauses,
      remove_filler: body.remove_filler,
      export_fcpxml: body.export_fcpxml !== false,
    });
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
    const data = await rpcData("run_pipeline", {
      source,
      out: join(OUT_WEB, "pipeline"),
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
    });
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
      sendJson(res, { ok: false, op: url.pathname, error: String(err.message || err) });
    });
  });
  server.listen(port, host, () => {
    const addr = server.address();
    console.log(`LISTEN ${addr.address}:${addr.port}`);
  });
}

main();