// SelekyT desktop UI — a local-first editorial workstation.
//
// Every screen is driven by artifacts the engine really wrote or by lines it
// really printed. Nothing below is simulated: stages advance only on engine
// evidence, clips come from selections.json, the timeline comes from
// edit_plan.json, and video previews are the rendered mp4s themselves.
//
// Transport is the shell's `selekyt_rpc` command, which speaks the same
// {"op","params"} → {"ok",...} envelope as the Node harness in web/. Under a
// plain browser (no Tauri) it falls back to the local HTTP harness, so this
// same file is exercisable without a packaged binary.

const isTauri = Boolean(
  globalThis.window && globalThis.window.__TAURI__ && globalThis.window.__TAURI__.core,
);
const invoke = isTauri ? globalThis.window.__TAURI__.core.invoke : null;
const tauriFs = isTauri && globalThis.window.__TAURI__.fs ? globalThis.window.__TAURI__.fs : null;

const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// The pure helpers live in lib.mjs so a test can run them against a real
// project tree (see tests/ui_contract.mjs).
import {
  basename,
  fmtDuration,
  fmtScore,
  humanBytes,
  intervalLayout,
  omittedIntervals,
  parseSrt,
  pct,
  sameSource,
  stem,
} from "./lib.mjs";

// ╭─ transport ────────────────────────────────────────────────────────────╮

async function rpc(target, op, params = {}) {
  const envelope = isTauri
    ? await invoke("selekyt_rpc", { target, op, params })
    : await fetch(`/api/${op}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      }).then((r) => r.json());
  if (!envelope.ok) {
    throw new Error(envelope.error || `op '${op}' failed`);
  }
  return envelope.data;
}

/** A JSON document the engine wrote at the project root. */
async function readProjectDoc(project, name) {
  const data = await rpc("python", "read_artifact", { project, name });
  return data.json;
}

/** Absolute-path text read (captions/, timeline/), permitted on $HOME/**. */
async function readAbs(path) {
  if (tauriFs) return tauriFs.readTextFile(path);
  const data = await rpc("python", "read_artifact", { project: path, name: "" }).catch(() => null);
  return data ? JSON.stringify(data.json) : null;
}

async function fileSize(path) {
  const info = await rpc("python", "stat_artifact", { path });
  return info.size;
}

// ╭─ formatting ────────────────────────────────────────────────────────────╮

/** "just now" / "yesterday" / a local date — for the project list. */
function relTime(unixSec) {
  if (!Number.isFinite(unixSec) || !unixSec) return "—";
  const then = new Date(unixSec * 1000);
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  if (mins < 60 * 48) return "yesterday";
  return then.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ╭─ application state ─────────────────────────────────────────────────────╮

const state = {
  view: "projects",
  projects: [],
  // import
  source: null,
  sourceMeta: null,
  existingForSource: null,
  // the run
  runParams: null,
  projectDir: null, // ~/SelekyT/<name>
  project: null, // <projectDir>/project
  // the review
  report: null,
  selections: [],
  editPlan: [],
  qa: null,
  artifacts: [],
  clipIndex: 0,
  tab: "story",
  blobUrls: [],
};

const VIEWS = ["projects", "import", "processing", "review"];

function show(name) {
  state.view = name;
  for (const v of VIEWS) {
    $(`view-${v}`).hidden = v !== name;
  }
  renderCrumbs();
  // The error banner belongs to a run; arriving somewhere new clears it so a
  // stale failure does not follow the user around.
  if (name !== "processing") setError(null);
}

function renderCrumbs() {
  const parts = ["SelekyT"];
  if (state.view !== "projects") parts.push(state.projectDir ? stem(state.projectDir) : "new project");
  if (state.view === "review") {
    const c = state.selections[state.clipIndex];
    if (c) parts.push(`clip ${state.clipIndex + 1} of ${state.selections.length}`);
  }
  $("crumbs").innerHTML = parts.map((p, i) => (i === 0 ? esc(p) : `<b>›</b> ${esc(p)}`)).join(" ");
}

// ╭─ failure surface ───────────────────────────────────────────────────────╮

// A failure is shown, not swallowed and never reported as success. A *stopped*
// run is not a failure at all: it gets a neutral banner and a resume action,
// because the engine's per-chunk checkpoints mean the finished work survives.
function setError(message, { retry = false, title = "The run did not complete." } = {}) {
  const box = $("error");
  if (!message) {
    box.hidden = true;
    $("error-retry").hidden = true;
    return;
  }
  $("error-title").textContent = title;
  $("error-detail").textContent = String(message).trim();
  $("error-retry").hidden = !retry;
  box.hidden = false;
}

function fail(message) {
  // A failure mid-processing sends the user back to a screen they can act on.
  if (state.view === "processing") show("import");
  setError(message, { retry: Boolean(state.source) });
  $("analyze").disabled = false;
  $("pick").disabled = false;
}

// ╭─ stage model ───────────────────────────────────────────────────────────╮

// The pipeline's real stages, in the order the engine reaches them. Each rule
// names the stderr evidence a stage STARTED and FINISHED. A stage with no
// finished-evidence stays "running" until the reply lands, which is honest: the
// engine prints per-clip render lines but nothing at all for selection or
// captioning, so those are bounded by the run ending rather than by a line we
// invented.
const STAGES = [
  {
    key: "transcribe",
    label: "Transcription",
    started: (l) => /^\[transcribe\]/.test(l) || /^\[cache\] using unchanged transcript/.test(l),
    finished: (l) =>
      /^\[transcribe\] engine=/.test(l) || /^\[cache\] using unchanged transcript/.test(l),
  },
  {
    key: "select",
    label: "Story detection",
    // No engine line announces selection; it runs between the transcript and
    // the first render. Bounded only by the run completing.
    started: (l) => /^\[cache\] reusing rendered clip/.test(l),
    finished: null,
  },
  {
    key: "audio",
    label: "Audio / dead air",
    // engine/audio_analysis prints these when it extracts PCM and runs the
    // VAD. Only present with the audio-analysis stage; absent evidence is not
    // a failure, see renderStages.
    started: (l) =>
      /\[cache\] (reusing extracted PCM|reusing VAD scores)/.test(l) ||
      /^\[vad\] backend=/.test(l),
    finished: null,
    optional: true,
  },
  {
    key: "render",
    label: "Output generation",
    started: (l) => /^\[cache\] reusing rendered clip/.test(l) || /render/.test(l),
    // The reply itself is the evidence the renders shipped and validated.
    finished: null,
    finishedByReply: true,
  },
];

function renderStages() {
  const box = $("stages");
  box.innerHTML = "";
  for (const stage of STAGES) {
    const el = document.createElement("div");
    el.className = "stage pending";
    el.dataset.stage = stage.key;
    el.innerHTML = `<span class="stage-dot"></span>
      <span class="stage-label">${stage.label}</span>
      <span class="stage-state">queued</span>`;
    box.appendChild(el);
  }
}

function stageEl(key) {
  return $("stages").querySelector(`[data-stage="${key}"]`);
}

function setStage(key, state_) {
  const el = stageEl(key);
  if (!el) return;
  el.className = `stage ${state_}`;
  el.querySelector(".stage-state").textContent =
    state_ === "running" ? "running…" : state_ === "done" ? "done" : "queued";
}

function appendLog(line) {
  const log = $("log");
  log.textContent += `${line}\n`;
  log.scrollTop = log.scrollHeight;
  // This is the only place stage state is set from engine evidence.
  for (const stage of STAGES) {
    if (stage.started && stage.started(line)) {
      if (stageEl(stage.key).classList.contains("pending")) {
        setStage(stage.key, "running");
      }
    }
    if (stage.finished && stage.finished(line)) {
      setStage(stage.key, "done");
    }
  }
}

if (isTauri) {
  globalThis.window.__TAURI__.event
    .listen("engine-log", (event) => {
      const line = event?.payload?.line;
      if (typeof line === "string") appendLog(line);
    })
    .catch(() => {
      /* a missing listener must not break the UI; the log is decorative */
    });
}

// ╭─ projects ──────────────────────────────────────────────────────────────╮

async function loadProjects() {
  try {
    state.projects = await invoke("selekyt_projects");
  } catch (err) {
    state.projects = [];
    setError(`Cannot read the project list: ${err.message || err}`);
  }
  renderProjects();
}

function renderProjects() {
  const box = $("project-list");
  box.innerHTML = "";
  const list = state.projects;
  $("project-empty").hidden = list.length > 0;
  for (const p of list) {
    const el = document.createElement("button");
    el.className = "project-card";
    el.dataset.status = p.status;
    el.innerHTML = `
      <span class="pc-name">${esc(p.name)}</span>
      <span class="pc-src">${esc(basename(p.source || "—"))}</span>
      <span class="pc-meta">
        <span>${p.selected_count ?? 0} <b>clips</b></span>
        <span>${p.candidate_count ?? 0} <b>candidates</b></span>
        <span>${p.language || "—"}</span>
        <span class="pc-status chip ${p.status === "complete" ? "ok" : p.status === "incomplete" ? "bad" : ""}">${esc(p.status)}</span>
      </span>
      <span class="pc-meta">
        <span>${esc(relTime(p.created))}</span>
        <span>${p.renders} <b>renders</b></span>
      </span>
      <span class="pc-del ghost small" title="delete this project" data-del="${esc(p.dir)}">✕</span>`;
    el.addEventListener("click", (e) => {
      const del = e.target.closest("[data-del]");
      if (del) {
        e.stopPropagation();
        confirmDelete(del.dataset.del);
        return;
      }
      openProject(p.dir);
    });
    box.appendChild(el);
  }
}

async function confirmDelete(dir) {
  // Not a native confirm: those are modal, block the UI thread, and on Windows
  // read as a system prompt. This is an inline, clearly-worded action.
  const name = stem(dir);
  const dlg = globalThis.window.__TAURI__?.dialog;
  const ok = dlg
    ? await dlg.ask(
        `Delete the project “${name}” and everything it produced?\n\n` +
          "This removes the rendered clips, captions, timelines and transcripts for it. It cannot be undone.",
        { title: "Delete project", kind: "warning", okLabel: "Delete", cancelLabel: "Keep" },
      ).catch(() => false)
    : window.confirm(`Delete project “${name}”? This cannot be undone.`);
  if (!ok) return;
  try {
    await invoke("selekyt_delete_project", { dir });
    await loadProjects();
  } catch (err) {
    setError(`Could not delete the project: ${err.message || err}`);
  }
}

async function openProject(dir) {
  state.projectDir = dir;
  state.project = `${dir}/project`;
  state.source = null;
  try {
    state.report = await readProjectDoc(state.project, "report.json");
  } catch (err) {
    // A directory with no readable report is not a project the UI can review.
    setError(`This project has no report yet — ${err.message}`, { retry: false });
    show("import");
    return;
  }
  const incomplete = !state.report || !(state.report.selected_count > 0);
  if (incomplete) {
    setError(
      "This project did not finish selecting stories. Run it again to resume from the last completed chunk.",
      { retry: true, title: "Incomplete project." },
    );
    show("import");
    return;
  }
  await loadReviewData();
  show("review");
}

// ╭─ pick a video ──────────────────────────────────────────────────────────╮

async function pickViaDialog() {
  try {
    const selected = await invoke("plugin:dialog|open", {
      title: "Choose a video to begin",
      multiple: false,
      filters: [
        {
          name: "Video",
          extensions: [
            "mp4", "mkv", "mov", "webm", "avi", "m4v", "mpg", "mpeg",
            "ts", "flv", "wmv", "ogv",
          ],
        },
      ],
    });
    if (selected) await picked(String(selected));
  } catch (err) {
    setError(`The file picker could not be opened: ${err.message || err}`);
  }
}

async function picked(path) {
  state.source = path;
  const name = basename(path);
  $("chosen-name").textContent = name;
  $("chosen").hidden = false;
  // Metadata is probed from the real file, not remembered or guessed.
  const meta = $("chosen-meta");
  meta.innerHTML = "";
  const row = (k, v) => {
    const w = document.createElement("div");
    w.innerHTML = `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`;
    meta.appendChild(w);
  };
  row("file", name);
  try {
    const info = await rpc("python", "probe_media", { path });
    state.sourceMeta = info;
    row("duration", info.duration ? fmtDuration(info.duration) : "—");
    row("resolution", info.width && info.height ? `${info.width}×${info.height}` : "—");
    row("frame rate", info.fps ? `${info.fps.toFixed(3)} fps` : "—");
    row("audio", info.has_audio ? "present" : "no audio track");
    if (!info.has_audio) {
      $("analyze-hint").textContent =
        "No audio track was found: transcription needs speech, so this source cannot be analyzed.";
    }
  } catch (err) {
    // A probe failure must not block analysis — the pipeline probes what it
    // needs itself — but the user should see why the metadata row is empty.
    row("metadata", `unavailable (${err.message})`);
  }
  // Resume detection: an existing project for this exact source can be opened
  // or re-run to pick up where it stopped.
  state.existingForSource =
    state.projects.find((p) => p.source && sameSource(p.source, path)) || null;
  const hint = $("resume-hint");
  if (state.existingForSource) {
    const e = state.existingForSource;
    hint.innerHTML = `An existing project for this source has ${e.selected_count ?? 0} clips (${esc(e.status)}). <a href="#" data-open-existing>open it</a>, or run again to resume from where it stopped.`;
    hint.hidden = false;
    hint.querySelector("[data-open-existing]").addEventListener("click", (ev) => {
      ev.preventDefault();
      openProject(e.dir);
    });
  } else {
    hint.hidden = true;
    hint.textContent = "";
  }
  $("analyze").disabled = false;
  $("analyze").focus();
}


// ╭─ run the real pipeline ─────────────────────────────────────────────────╮

/** A fresh, unique project directory for this source and this moment. */
function newProjectName() {
  const stemName = stem(state.source || "untitled")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(
    now.getHours(),
  )}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `${stemName || "project"}-${stamp}`;
}

async function startRun(resumeFrom = null) {
  if (!state.source && !resumeFrom) return;
  setError(null);
  $("log").textContent = "";
  renderStages();
  show("processing");
  $("cancel-run").disabled = false;
  $("cancel-run").textContent = "Stop";
  $("processing-title").textContent = resumeFrom ? "Resuming" : "Processing";

  // Runs write to a per-project directory, never inside the app bundle and
  // never over a previous project's tree — unless the user is resuming, in
  // which case the same directory is what makes the chunk cache reusable.
  if (resumeFrom) {
    state.projectDir = resumeFrom;
    state.project = `${resumeFrom}/project`;
  } else {
    const outRoot = isTauri ? await invoke("selekyt_out_dir").catch(() => null) : null;
    state.projectDir = `${outRoot || "out"}/${newProjectName()}`;
    state.project = `${state.projectDir}/project`;
  }

  // cpu_threads is capped at 2 from measurement, not intuition: on a 4-core
  // machine one 5-minute chunk took 68.7s at 1 thread, 40.3s at 2 and 40.8s at
  // 4, so intra-chunk parallelism saturates at 2 and os.cpu_count()'s 8 on a
  // 4-core/8-thread CPU oversubscribes badly.
  const params = {
    source: state.source,
    out: state.projectDir,
    model: $("opt-model").value,
    limit: parseInt($("opt-limit").value, 10) || 6,
    caption_style: $("opt-caption-style").value,
    audio_analysis: $("opt-audio").value === "1",
    export_fcpxml: $("opt-fcpxml").value === "1",
    keep_pauses: $("opt-keep-pauses").value === "1",
    cpu_threads: 2,
  };
  const vad = $("opt-vad") ? $("opt-vad").value : "energy";
  if (vad) params.vad_backend = vad;
  state.runParams = params;

  try {
    const data = await rpc("python", "run_pipeline", params);
    // The reply is the sole evidence the run produced and validated its
    // outputs; the render stage is the last one it names.
    for (const stage of STAGES) {
      if (stage.finishedByReply) setStage(stage.key, "done");
    }
    // Any stage the engine never reached — including the optional audio stage
    // — is reported as skipped, never as completed.
    for (const stage of STAGES) {
      const el = stageEl(stage.key);
      if (el.classList.contains("pending")) {
        setStage(stage.key, "skipped");
        el.querySelector(".stage-state").textContent = stage.optional
          ? "not needed"
          : "skipped";
        el.classList.add("skipped");
      }
    }
    state.report = data.report || null;
    await loadProjects();
    await loadReviewData();
    show("review");
  } catch (err) {
    const msg = String(err.message || err);
    if (msg === "cancelled") {
      // A stop is not a failure. What was already written stays on disk, and
      // re-running resumes from the last completed chunk.
      setError(
        "Stopped. The transcription and renders that finished are kept; running again resumes from the last completed chunk.",
        { retry: true, title: "Stopped by you." },
      );
      show("import");
    } else {
      fail(msg);
    }
  } finally {
    $("analyze").disabled = false;
    $("pick").disabled = false;
    $("cancel-run").disabled = true;
  }
}

async function cancelRun() {
  $("cancel-run").disabled = true;
  $("cancel-run").textContent = "stopping…";
  try {
    await invoke("selekyt_cancel", { target: "python" });
  } catch (err) {
    setError(`Could not stop the run: ${err.message || err}`);
    $("cancel-run").disabled = false;
    $("cancel-run").textContent = "Stop";
  }
}

// ╭─ review data ───────────────────────────────────────────────────────────╮

async function loadReviewData() {
  const p = state.project;
  if (!p) return;
  const [report, selections, editPlan, qa] = await Promise.all([
    readProjectDoc(p, "report.json").catch(() => null),
    readProjectDoc(p, "selections.json").catch(() => []),
    readProjectDoc(p, "edit_plan.json").catch(() => []),
    readProjectDoc(p, "qa.json").catch(() => null),
  ]);
  state.report = state.report || report;
  state.selections = Array.isArray(selections) ? selections : [];
  state.editPlan = Array.isArray(editPlan) ? editPlan : [];
  state.qa = qa;
  state.artifacts = await listArtifacts();
  state.clipIndex = 0;
  renderReview();
}

/** Every file the run produced, derived from the tree the engine writes. */
async function listArtifacts() {
  const p = state.project;
  const r = state.report || {};
  const dirs = {
    "16:9 renders": r.outputs?.facebook || `${p}/facebook`,
    "9:16 renders": r.outputs?.shorts || `${p}/shorts`,
    captions: r.captions || `${p}/captions`,
    timeline: r.timeline || `${p}/timeline`,
  };
  const out = [];
  for (const [kind, dir] of Object.entries(dirs)) {
    let names = [];
    try {
      const entries = tauriFs ? await tauriFs.readDir(dir) : [];
      names = entries
        .filter((e) => !e.isDirectory)
        .map((e) => basename(e.name))
        .sort();
    } catch {
      // A missing directory means this run never produced that kind of output.
      continue;
    }
    for (const name of names) out.push({ kind, path: `${dir}/${name}` });
  }
  for (const name of [
    "report.json", "selections.json", "edit_plan.json", "transcript.json",
    "qa.json", "pacing_report.json", "editorial_report.json", "pipeline_state.json",
  ]) {
    out.push({ kind: "project data", path: `${p}/${name}` });
  }
  return out;
}

function renderReview() {
  renderRail();
  renderSummaryStrip();
  selectClip(state.clipIndex);
}

function renderSummaryStrip() {
  const r = state.report || {};
  const el = $("review-head");
  const n = state.selections.length;
  el.innerHTML = `
    <h2>${esc(stem(state.projectDir))}</h2>
    <span class="chip ok">${n} clip${n === 1 ? "" : "s"} shipped</span>
    <span class="chip">${esc(r.language || "—")}</span>
    <span class="chip">${r.candidate_count ?? 0} candidates</span>
    <span class="rh-actions">
      <button class="ghost small" id="rh-open">Open folder</button>
      <button class="ghost small" id="rh-rerun">Run again</button>
    </span>`;
  $("rh-open").addEventListener("click", () => reveal(state.project));
  $("rh-rerun").addEventListener("click", () => {
    state.source = state.report?.source || state.source;
    if (!state.source) {
      setError("This project's source path is unknown, so it cannot be re-run from here.");
      return;
    }
    picked(state.source).then(() => startRun(state.projectDir));
  });
}

function renderRail() {
  const box = $("clip-rail-list");
  box.innerHTML = "";
  $("rail-count").textContent = `${state.selections.length} clips`;
  if (!state.selections.length) {
    box.innerHTML = `<p class="hint" style="padding:14px">No selections were recorded for this run.</p>`;
    return;
  }
  for (const [i, c] of state.selections.entries()) {
    const el = document.createElement("button");
    el.className = "rail-item";
    el.dataset.index = String(i);
    el.innerHTML = `
      <span class="ri-top">
        <span class="ri-idx">${String(i + 1).padStart(2, "0")}</span>
        <span class="ri-type">${esc(c.story_type || "")}</span>
      </span>
      <span class="ri-text"></span>
      <span class="ri-dur">${fmtDuration(c.start)} → ${fmtDuration(c.end)} · ${fmtDuration(
        c.end - c.start,
      )}</span>`;
    el.querySelector(".ri-text").textContent = c.text || "";
    el.addEventListener("click", () => selectClip(i));
    box.appendChild(el);
  }
}

function selectClip(i) {
  state.clipIndex = Math.max(0, Math.min(i, state.selections.length - 1));
  for (const el of $$(".rail-item")) {
    el.classList.toggle("active", Number(el.dataset.index) === state.clipIndex);
  }
  renderStory();
  renderTimeline();
  renderCaptions();
  renderTranscript();
  renderCrumbs();
}

// Revoke any blob URLs we created: the browser will not GC them, and each
// clip switch would otherwise leak a whole rendered video.
function dropBlobs() {
  for (const u of state.blobUrls) URL.revokeObjectURL(u);
  state.blobUrls = [];
}

async function blobUrlFor(path) {
  if (!tauriFs) return null;
  const size = await fileSize(path).catch(() => Infinity);
  // A preview that loads a whole video into RAM is worse than no preview for a
  // big file; the shipped clips are a few MB, so bound it well above that.
  if (size > 64 * 1024 * 1024) return { tooBig: humanBytes(size) };
  const bytes = await tauriFs.readFile(path);
  const url = URL.createObjectURL(new Blob([bytes], { type: "video/mp4" }));
  state.blobUrls.push(url);
  return { url };
}

// ╭─ story pane ────────────────────────────────────────────────────────────╮

function renderStory() {
  const box = $("tab-story");
  const c = state.selections[state.clipIndex];
  if (!c) {
    box.innerHTML = `<p class="hint">Select a clip.</p>`;
    return;
  }
  const r = state.report || {};
  const i = state.clipIndex + 1;
  const dirs = r.outputs || {};
  const vertical = `${dirs.shorts || `${state.project}/shorts`}/${String(i).padStart(2, "0")}.mp4`;
  const landscape = `${dirs.facebook || `${state.project}/facebook`}/${String(i).padStart(2, "0")}.mp4`;

  box.innerHTML = `
    <div class="preview-frame">
      <div class="pv vertical" data-pv="9:16">
        <div class="pv-label"><span>9:16 · vertical</span><span class="pv-size muted"></span></div>
        <div class="pv-slot"></div>
      </div>
      <div class="pv" data-pv="16:9">
        <div class="pv-label"><span>16:9 · landscape</span><span class="pv-size muted"></span></div>
        <div class="pv-slot"></div>
      </div>
    </div>
    <div class="scores">
      ${scoreCard("editorial", c.editorial_score)}
      ${scoreCard("virality", c.virality_score)}
      ${scoreCard("context", c.context_score)}
      ${scoreCard("visual", c.visual_potential)}
      ${scoreCard("confidence", c.confidence)}
    </div>
    <div class="quote-grid">
      <div class="quote"><div class="qk">Hook</div><div class="qv"></div></div>
      <div class="quote"><div class="qk">Payoff</div><div class="qv"></div></div>
    </div>
    <div class="story-text"></div>
    <div class="story-reason"></div>`;

  box.querySelectorAll(".qv")[0].textContent = c.hook || "—";
  box.querySelectorAll(".qv")[1].textContent = c.payoff || "—";
  box.querySelector(".story-text").textContent = c.text || "";
  box.querySelector(".story-reason").textContent = c.reason || "";

  for (const [kind, path] of [
    ["9:16", vertical],
    ["16:9", landscape],
  ]) {
    const host = box.querySelector(`[data-pv="${kind}"] .pv-slot`);
    const sizeEl = box.querySelector(`[data-pv="${kind}"] .pv-size`);
    blobUrlFor(path).then((got) => {
      if (!got) return;
      if (got.tooBig) {
        host.innerHTML = `<div class="pv-none">Too large to preview inline (${got.tooBig}). Use “Open folder”.</div>`;
        return;
      }
      const v = document.createElement("video");
      v.src = got.url;
      v.controls = true;
      v.preload = "metadata";
      host.appendChild(v);
      fileSize(path)
        .then((s) => {
          sizeEl.textContent = humanBytes(s);
        })
        .catch(() => {});
    });
  }
}

function scoreCard(label, v) {
  const pctVal = typeof v === "number" && Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0;
  return `<div class="score">
    <div class="sk">${esc(label)}</div>
    <div class="sv">${fmtScore(v)}</div>
    <div class="sbar"><i style="width:${Math.round(pctVal * 100)}%"></i></div>
  </div>`;
}

// ╭─ timeline pane ─────────────────────────────────────────────────────────╮

function renderTimeline() {
  const box = $("tab-timeline");
  const i = state.clipIndex;
  const c = state.selections[i];
  const plan = state.editPlan.find((p) => p.index === i + 1) || state.editPlan[i];
  if (!c) {
    box.innerHTML = `<p class="hint">Select a clip.</p>`;
    return;
  }
  if (!plan || !Array.isArray(plan.intervals) || !plan.intervals.length) {
    box.innerHTML = `<p class="hint">No edit plan for this clip — it ships as one unbroken take.</p>`;
    return;
  }
  const layout = intervalLayout(plan);
  if (!layout) {
    box.innerHTML = `<p class="hint">No edit plan for this clip — it ships as one unbroken take.</p>`;
    return;
  }
  // Kept intervals are positioned within the clip's source range; the hatched
  // ground between them is exactly what the render cut out.
  const bars = layout.bars
    .map(
      (b) =>
        `<span class="tl-keep" style="left:${b.left.toFixed(2)}%;width:${b.width.toFixed(2)}%" title="${fmtDuration(
          b.start,
        )} → ${fmtDuration(b.end)} (${fmtDuration(b.end - b.start)})"></span>`,
    )
    .join("");

  box.innerHTML = `
    <div class="tl-card">
      <div class="tl-head">
        <h3>Kept intervals</h3>
        <span class="muted">${plan.intervals.length} segment${plan.intervals.length === 1 ? "" : "s"}</span>
        <span class="muted">${fmtDuration(layout.cut)} removed</span>
        <span class="tl-dur">${fmtDuration(plan.source_duration)} → ${fmtDuration(
          plan.edited_duration,
        )} shipped</span>
      </div>
      <div class="tl-bar">${bars}</div>
      <div class="tl-axis">
        <span>${fmtDuration(plan.source_start)}</span>
        <span>${fmtDuration((plan.source_start + plan.source_end) / 2)}</span>
        <span>${fmtDuration(plan.source_end)}</span>
      </div>
      <div class="tl-legend">
        <span class="lg-keep"><i></i>kept</span>
        <span class="lg-cut"><i></i>removed (dead air / pause)</span>
      </div>
      <table class="interval-table">
        <thead><tr><th>#</th><th>start</th><th>end</th><th>length</th><th>state</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>`;

  const tbody = box.querySelector("tbody");
  let n = 0;
  for (const [s, e] of plan.intervals) {
    n += 1;
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${n}</td><td>${fmtDuration(s)}</td><td>${fmtDuration(e)}</td>
      <td>${fmtDuration(e - s)}</td><td class="kept">kept</td>`;
    tbody.appendChild(tr);
  }
  // The gaps between kept intervals are the cuts; showing them is what makes
  // the numbers above auditable rather than decorative.
  for (const [prev, next] of omittedIntervals(plan)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>—</td><td>${fmtDuration(prev)}</td><td>${fmtDuration(next)}</td>
      <td>${fmtDuration(next - prev)}</td><td class="cut">removed</td>`;
    tbody.appendChild(tr);
  }
}

// ╭─ captions pane ─────────────────────────────────────────────────────────╮

async function renderCaptions() {
  const box = $("tab-captions");
  const i = state.clipIndex + 1;
  const r = state.report || {};
  const dir = r.captions || `${state.project}/captions`;
  const path = `${dir}/${String(i).padStart(2, "0")}.srt`;
  box.innerHTML = `<p class="hint">loading captions…</p>`;
  let text;
  try {
    text = await readAbs(path);
  } catch (err) {
    box.innerHTML = `<p class="hint">captions unavailable (${esc(err.message || err)})</p>`;
    return;
  }
  if (!text) {
    box.innerHTML = `<p class="hint">No captions were written for this clip.</p>`;
    return;
  }
  const cues = parseSrt(text);
  if (!cues.length) {
    box.innerHTML = `<p class="hint">The caption file is empty or unreadable.</p>`;
    return;
  }
  box.innerHTML = `<p class="hint">${cues.length} cues · SRT (SRT/ASS/JSON also written next to it)</p>`;
  for (const cue of cues) {
    const el = document.createElement("div");
    el.className = "cue";
    el.innerHTML = `<span class="cue-time">${esc(cue.start)} → ${esc(cue.end)}</span><span class="cue-text"></span>`;
    el.querySelector(".cue-text").textContent = cue.text;
    box.appendChild(el);
  }
}

// ╭─ transcript pane ───────────────────────────────────────────────────────╮

async function renderTranscript() {
  const box = $("tab-transcript");
  const c = state.selections[state.clipIndex];
  let segs;
  try {
    const data = await readProjectDoc(state.project, "transcript.json");
    segs = (data && data.segments) || [];
  } catch (err) {
    box.innerHTML = `<p class="hint">transcript unavailable (${esc(err.message || err)})</p>`;
    return;
  }
  box.innerHTML = "";
  if (!segs.length) {
    box.textContent = "The transcript is empty.";
    return;
  }
  // Segments overlapping the selected clip are highlighted, so the transcript
  // view doubles as a source-position check on the editorial pick.
  const lo = c ? c.start : -Infinity;
  const hi = c ? c.end : Infinity;
  for (const s of segs) {
    const overlaps = s.start < hi && s.end > lo;
    const el = document.createElement("div");
    el.className = `tseg${overlaps ? " hit" : ""}`;
    el.innerHTML = `<span class="tseg-time">${fmtDuration(s.start)}</span><span class="tseg-text"></span>`;
    el.querySelector(".tseg-text").textContent = (s.text || "").trim();
    box.appendChild(el);
  }
}

// ╭─ quality pane ──────────────────────────────────────────────────────────╮

function renderQA() {
  const box = $("tab-qa");
  const qa = state.qa;
  if (!qa) {
    box.innerHTML = `<p class="hint">No validation report: the run did not reach output checking.</p>`;
    return;
  }
  const perFile = qa.per_file || {};
  const names = Object.keys(perFile);
  const head = document.createElement("div");
  head.className = "stat-grid";
  const r = state.report || {};
  head.innerHTML = `
    ${statCard("verdict", qa.ok ? "passed" : "FAILED", qa.ok ? "var(--good)" : "var(--bad)")}
    ${statCard("errors", qa.errors?.length ?? 0, qa.errors?.length ? "var(--bad)" : "var(--ink)")}
    ${statCard("warnings", qa.warnings?.length ?? 0)}
    ${statCard("language", r.language || "—")}
    ${statCard("coverage", pct(r.coverage?.asr_speech_frac))}`;
  box.innerHTML = "";
  box.appendChild(head);
  if (qa.errors?.length) {
    const w = document.createElement("div");
    w.className = "qa-card bad";
    w.innerHTML = `<div class="qa-head"><strong>Errors</strong></div>` +
      qa.errors.map((e) => `<div class="qa-warn">${esc(e)}</div>`).join("");
    box.appendChild(w);
  }
  for (const path of names) {
    const f = perFile[path];
    const bad = (f.errors && f.errors.length) || (f.audio_condition && /missing|absent/i.test(f.audio_condition));
    const el = document.createElement("div");
    el.className = `qa-card${bad ? " bad" : ""}`;
    el.innerHTML = `
      <div class="qa-head">
        <span class="qh-file">${esc(relOf(path))}</span>
        <span class="qh-ok ${f.errors?.length ? "bad" : "muted"}">${f.errors?.length ? "error" : "ok"}</span>
      </div>
      <div class="qa-row"><span>audio</span><b>${esc(f.audio_condition || "—")}</b></div>
      <div class="qa-row"><span>silent tail</span><b>${f.silent_tail ? `${f.silent_tail_sec?.toFixed(2)} s` : "none"}</b></div>
      <div class="qa-row"><span>black frames</span><b>${pct(f.black_frame_pct)}</b></div>
      <div class="qa-row"><span>size</span><b>${humanBytes(f.size)}</b></div>
      ${f.warnings?.map((w_) => `<div class="qa-warn">${esc(w_)}</div>`).join("") || ""}
      ${f.errors?.map((e) => `<div class="qa-warn bad">${esc(e)}</div>`).join("") || ""}`;
    box.appendChild(el);
  }
  if (!names.length) {
    box.insertAdjacentHTML("beforeend", `<p class="hint">No outputs were validated.</p>`);
  }
}

function statCard(k, v, color) {
  return `<div class="stat"><span class="stat-k">${esc(k)}</span><span class="stat-v" ${
    color ? `style="color:${color}"` : ""
  }>${esc(v)}</span></div>`;
}

function relOf(path) {
  const p = state.project;
  return path && path.startsWith(p) ? path.slice(p.length + 1) : basename(path);
}

// ╭─ artifacts pane ────────────────────────────────────────────────────────╮

async function renderArtifacts() {
  const box = $("tab-artifacts");
  box.innerHTML = "";
  const list = state.artifacts;
  if (!list.length) {
    box.textContent = "No artifacts were produced.";
    return;
  }
  const ul = document.createElement("ul");
  ul.className = "artifacts";
  for (const a of list) {
    const li = document.createElement("li");
    const isVideo = /\.(mp4|mkv|mov|webm)$/i.test(a.path);
    const isTimeline = /\.(fcpxml|edl)$/i.test(a.path);
    const kind = isVideo ? "video" : isTimeline ? "timeline" : "file";
    li.innerHTML = `<span class="art-kind">${kind}</span>
      <span class="art-path"></span>
      <span class="art-size muted"></span>
      <button class="art-open ghost small">show</button>`;
    li.querySelector(".art-path").textContent = relOf(a.path);
    li.querySelector(".art-open").addEventListener("click", () => reveal(a.path));
    box.appendChild(li);
    // Size is read from disk, not estimated.
    fileSize(a.path)
      .then((s) => {
        li.querySelector(".art-size").textContent = humanBytes(s);
      })
      .catch(() => {
        li.querySelector(".art-size").textContent = "missing";
        li.querySelector(".art-size").classList.add("bad");
      });
    ul.appendChild(li);
  }
  box.appendChild(ul);
}

// ╭─ reveal ────────────────────────────────────────────────────────────────╮

async function reveal(path) {
  if (!isTauri) return;
  try {
    await invoke("reveal_path", { path });
  } catch (err) {
    setError(`Could not open the file manager: ${err.message || err}`);
  }
}

// ╭─ tabs ──────────────────────────────────────────────────────────────────╮

const TAB_RENDER = {
  story: renderStory,
  timeline: renderTimeline,
  captions: renderCaptions,
  transcript: renderTranscript,
  qa: renderQA,
  artifacts: renderArtifacts,
};

document.addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (!tab) return;
  for (const t of tab.parentElement.querySelectorAll(".tab")) {
    t.classList.toggle("active", t === tab);
  }
  const name = tab.dataset.tab;
  state.tab = name;
  for (const panel of Object.keys(TAB_RENDER)) {
    $(`tab-${panel}`).hidden = panel !== name;
  }
  // Re-render on arrival rather than up front: captions and transcript are
  // only read when looked at, and a 3182-candidate transcript is not worth
  // parsing for a tab nobody opens.
  TAB_RENDER[name]?.();
});

// ╭─ boot ──────────────────────────────────────────────────────────────────╮

function wire() {
  $("pick").addEventListener("click", pickViaDialog);
  $("chosen-change").addEventListener("click", pickViaDialog);
  $("analyze").addEventListener("click", () => startRun(null));
  $("cancel-run").addEventListener("click", cancelRun);
  $("new-project").addEventListener("click", () => {
    state.source = null;
    state.sourceMeta = null;
    state.existingForSource = null;
    $("chosen").hidden = true;
    $("chosen-meta").innerHTML = "";
    $("resume-hint").hidden = true;
    $("analyze-hint").textContent = "";
    $("import-title").textContent = "Choose a video to begin.";
    show("import");
  });
  $("brand").addEventListener("click", async () => {
    await loadProjects();
    show("projects");
  });
  $("error-dismiss").addEventListener("click", () => setError(null));
  $("error-retry").addEventListener("click", () => {
    setError(null);
    if (state.projectDir) startRun(state.projectDir);
  });
  $("toggle-log").addEventListener("click", (e) => {
    const log = $("log");
    const hidden = log.hidden;
    log.hidden = !hidden;
    e.target.textContent = hidden ? "hide" : "show";
  });
  document.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act='new-project']");
    if (act) $("new-project").click();
  });
}

async function boot() {
  wire();

  try {
    const health = isTauri
      ? await invoke("selekyt_health")
      : { python: await rpc("python", "ping").then((d) => ({ ok: true, data: d })) };
    const py = health.python;
    if (py && py.ok && py.data) {
      const d = py.data;
      $("engine").textContent = `engine ready · ${d.engine} v${d.version} · vad ${d.vad_backend}`;
      $("engine").classList.remove("muted");
      $("engine").classList.add("ok");
    } else {
      $("engine").textContent = "engine unavailable — processing will fail";
      $("engine").classList.add("bad");
      $("pick").disabled = true;
      setError(
        py && py.error
          ? `The SelekyT engine could not start: ${py.error}`
          : "The SelekyT engine did not answer a health check. Reinstall the application.",
      );
    }
  } catch (err) {
    $("engine").textContent = "engine unreachable";
    $("engine").classList.add("bad");
    setError(`Cannot reach the SelekyT engine: ${err.message || err}`);
  }

  if (isTauri) {
    await loadProjects();
    show("projects");
  } else {
    show("import");
  }
}

boot();
