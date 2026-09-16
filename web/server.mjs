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
import { dirname, extname, join, normalize, resolve } from "node:path";
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

function pickPython() {
  if (process.env.SELEKYT_PYTHON) return process.env.SELEKYT_PYTHON;
  for (const cand of ["python3", "python"]) {
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
  const c = spawn(PYTHON, ["-m", "selekyt", "--server"], {
    stdio: ["pipe", "pipe", "inherit"],
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
  c.on("error", (err) => {
    const entry = pending.shift();
    if (entry) entry.resolve({ ok: false, error: `sidecar failed to start: ${err.message}` });
  });
  c.on("exit", () => {
    child = null;
    let entry;
    while ((entry = pending.shift())) {
      entry.resolve({ ok: false, error: "sidecar exited before answering" });
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

function getJson(req) {
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
  const candidate = resolve(String(raw || ""));
  if (candidate.startsWith(ROOT)) {
    return candidate;
  }
  throw new Error("path must be inside the project");
}

function ensureSample() {
  if (!existsSync(SAMPLE_PATH)) {
    mkdirSync(OUT_WEB, { recursive: true });
    writeFileSync(SAMPLE_PATH, makeSampleWav());
  }
}

// ── routes ──

async function route(req, res, url, method) {
  if ((method === "GET" || method === "POST") && url.pathname === "/api/ping") {
    return sendJson(res, { ok: true, op: "ping", data: (await rpc("ping")).data });
  }
  if (method === "GET" && url.pathname === "/api/sample.wav") {
    ensureSample();
    return send(res, 200, readFileSync(SAMPLE_PATH), {
      "Content-Type": "audio/wav",
      "Content-Length": readFileSync(SAMPLE_PATH).length,
    });
  }
  if (method === "POST" && url.pathname === "/api/vad_analyze") {
    const body = await getJson(req);
    let path;
    if (body.wav) {
      mkdirSync(UPLOADS, { recursive: true });
      const name = sanitizeName(body.name);
      const target = join(UPLOADS, name);
      const wav = Buffer.from(body.wav, "base64");
      if (wav.length < 44) throw new Error("uploaded file is too small to be a WAV");
      writeFileSync(target, wav);
      path = target;
    } else if (body.sample || body.path === SAMPLE_NAME) {
      ensureSample();
      path = SAMPLE_PATH;
    } else if (body.path) {
      path = guardedPath(body.path);
      if (!existsSync(path)) throw new Error(`no such file: ${path}`);
    } else {
      throw new Error("provide 'path', 'sample', or a base64 'wav'");
    }
    const data = (await rpc("vad_analyze", {
      path,
      threshold: body.threshold,
      min_speech_ms: body.min_speech_ms,
      min_silence_ms: body.min_silence_ms,
      return_scores: true,
    })).data;
    return sendJson(res, { ok: true, op: "vad_analyze", data });
  }
  if (method === "POST" && url.pathname === "/api/segment") {
    const body = await getJson(req);
    return sendJson(res, await rpc("segment", body));
  }
  if (method === "POST" && url.pathname === "/api/cutlist_from_segments") {
    const body = await getJson(req);
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