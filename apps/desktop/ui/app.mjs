// SelekyT desktop UI — the first-user flow.
//
// Home → Select Video → Processing → Results. Every stage indicator below is
// derived from a line the engine actually printed (see the STAGE_RULES map);
// nothing is timed, nothing is faked, and no stage is marked complete unless
// the engine's own output says it is.
//
// The transport is the shell's `selekyt_rpc` Tauri command, which speaks the
// same `{"op","params"} → {"ok",...}` envelope as the Node harness in web/.
// Under a plain browser (no Tauri) it falls back to the local HTTP harness, so
// this same file is testable without a packaged binary.

const isTauri = Boolean(
  globalThis.window && globalThis.window.__TAURI__ && globalThis.window.__TAURI__.core,
);
const invoke = isTauri
  ? globalThis.window.__TAURI__.core.invoke
  : null;

const $ = (id) => document.getElementById(id);

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

// ╭─ view switching ────────────────────────────────────────────────────────╮

const VIEWS = ["home", "processing", "results"];

function show(name) {
  for (const v of VIEWS) {
    $(`view-${v}`).hidden = v !== name;
  }
  if (name === "processing") {
    $("error").hidden = true;
  }
}

// ╭─ failure surface ───────────────────────────────────────────────────────╮

// A failure is shown, not swallowed and never reported as success. The engine
// reports `ok: false` with a message; a sidecar that died reports a shell-level
// error string. Both reach the same place so the user always sees the cause.
function fail(message) {
  const detail = String(message || "unknown failure").trim();
  $("error-detail").textContent = detail;
  $("error").hidden = false;
  // Keep the user oriented: a failed run lands back on the home view with the
  // banner visible, not on a results view that would imply completion.
  const processingActive = !$("view-processing").hidden;
  if (processingActive) {
    show("home");
  }
  $("analyze").disabled = false;
  $("pick").disabled = false;
}

// ╭─ stage model ───────────────────────────────────────────────────────────╮

// The pipeline's real stages, in the order the engine reaches them. Each rule
// names the stderr evidence that a stage STARTED and the evidence it FINISHED.
// A stage with no finished-evidence stays "running" until the reply lands,
// which is honest: the engine prints per-clip render lines but prints nothing
// at all for selection or captioning, so those are bounded by the run ending
// rather than by a line we invented.
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
    // Only present with the audio-analysis stage, which the desktop flow
    // enables. Absent evidence is not a failure; see renderStages.
    started: (l) => /\[audio\]|dead air|speech region/i.test(l),
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
  return $(`stages`).querySelector(`[data-stage="${key}"]`);
}

function setStage(key, state) {
  const el = stageEl(key);
  if (!el) return;
  el.className = `stage ${state}`;
  el.querySelector(".stage-state").textContent =
    state === "running" ? "running…" : state === "done" ? "done" : "queued";
}

// ╭─ engine log ────────────────────────────────────────────────────────────╮

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

// ╭─ pick a video ──────────────────────────────────────────────────────────╮

let chosenPath = null;

function fmtDuration(sec) {
  if (!Number.isFinite(sec)) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

function humanBytes(n) {
  if (!Number.isFinite(n)) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

async function picked(path) {
  chosenPath = path;
  const name = path.split(/[/\\]/).pop();
  $("chosen-name").textContent = name;
  $("chosen").hidden = false;
  // Metadata is probed from the real file, not remembered or guessed.
  const meta = $("chosen-meta");
  meta.innerHTML = "";
  const row = (k, v) => {
    const w = document.createElement("div");
    w.innerHTML = `<dt>${k}</dt><dd>${v}</dd>`;
    meta.appendChild(w);
  };
  row("file", name);
  try {
    const info = await rpc("python", "probe_media", { path });
    row("duration", info.duration ? fmtDuration(info.duration) : "—");
    row("resolution", info.width && info.height ? `${info.width}×${info.height}` : "—");
    row("frame rate", info.fps ? `${info.fps.toFixed(3)} fps` : "—");
    row("audio", info.has_audio ? "present" : "no audio track");
  } catch (err) {
    // A probe failure must not block analysis — the pipeline probes what it
    // needs itself — but the user should see why the metadata row is empty.
    row("metadata", `unavailable (${err.message})`);
  }
  $("analyze").disabled = false;
  $("analyze").focus();
}

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
    fail(`The file picker could not be opened: ${err.message || err}`);
  }
}

// ╭─ run the real pipeline ─────────────────────────────────────────────────╮

async function runPipeline() {
  if (!chosenPath) return;
  $("analyze").disabled = true;
  $("pick").disabled = true;
  $("error").hidden = true;
  $("log").textContent = "";
  renderStages();
  show("processing");

  // The runs go to a per-user output directory, not into the repo, so a
  // packaged install never writes inside its own bundle.
  const outRoot = isTauri
    ? await invoke("selekyt_out_dir").catch(() => null)
    : null;
  const out = outRoot || "out";

  const params = {
    source: chosenPath,
    out,
    model: $("opt-model").value,
    limit: parseInt($("opt-limit").value, 10) || 6,
    caption_style: $("opt-caption-style").value,
    audio_analysis: true,
    export_fcpxml: true,
  };

  try {
    const data = await rpc("python", "run_pipeline", params);
    // The reply is the sole evidence that the run produced and validated its
    // outputs; the render stage is the last one it names.
    for (const stage of STAGES) {
      if (stage.finishedByReply) setStage(stage.key, "done");
    }
    // Any stage the engine never reached — including the optional audio stage
    // — is reported as skipped, never as completed.
    for (const stage of STAGES) {
      const el = stageEl(stage.key);
      if (el.classList.contains("pending")) {
        setStage(stage.key, stage.optional ? "skipped" : "skipped");
        el.querySelector(".stage-state").textContent = stage.optional
          ? "not needed"
          : "skipped";
        el.classList.add("skipped");
      }
    }
    showResults(data);
  } catch (err) {
    fail(err.message || String(err));
  } finally {
    $("analyze").disabled = false;
    $("pick").disabled = false;
  }
}

// ╭─ results ───────────────────────────────────────────────────────────────╮

let lastProject = null;

function showResults(data) {
  const r = data.report || {};
  lastProject = data.project || r.outputs?.facebook?.replace(/[/\\]facebook$/, "");

  const summary = $("results-summary");
  summary.innerHTML = "";
  const card = (k, v) => {
    const el = document.createElement("div");
    el.className = "stat";
    el.innerHTML = `<span class="stat-k">${k}</span><span class="stat-v">${v}</span>`;
    summary.appendChild(el);
  };
  card("clips shipped", r.selected_count ?? "—");
  card("candidates considered", r.candidate_count ?? "—");
  card("language", r.language ?? "—");
  card("rejected by guard", r.rejected_by_context_guard ?? "—");
  card("renders", (data.artifacts || []).filter((p) => p.endsWith(".mp4")).length);
  card("audio analysis", r.audio && r.audio.enabled !== false ? "ran" : "not run");

  renderClips(r, data);
  renderTranscript();
  renderArtifacts(data);
  show("results");
}

function relOf(path, project) {
  return path.startsWith(project) ? path.slice(project.length + 1) : path;
}

function renderClips(r, data) {
  const box = $("tab-clips");
  box.innerHTML = "";
  const selections = data.selections || r.selections || [];
  if (!selections.length) {
    box.textContent = "No selections were recorded for this run.";
    return;
  }
  for (const [i, c] of selections.entries()) {
    const el = document.createElement("article");
    el.className = "clip";
    const dur = c.end - c.start;
    el.innerHTML = `
      <header>
        <span class="clip-idx">Clip ${String(i + 1).padStart(2, "0")}</span>
        <span class="clip-time">${fmtDuration(c.start)} → ${fmtDuration(c.end)} · ${fmtDuration(dur)}</span>
        <span class="clip-type">${c.story_type || ""}</span>
      </header>
      <p class="clip-text"></p>
      <dl class="clip-meta">
        <div><dt>hook</dt><dd></dd></div>
        <div><dt>payoff</dt><dd></dd></div>
        <div><dt>editorial score</dt><dd>${fmtScore(c.editorial_score)}</dd></div>
      </dl>`;
    el.querySelector(".clip-text").textContent = c.text || "";
    el.querySelectorAll(".clip-meta dd")[0].textContent = c.hook || "—";
    el.querySelectorAll(".clip-meta dd")[1].textContent = c.payoff || "—";
    box.appendChild(el);
  }
}

function fmtScore(v) {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(3) : "—";
}

async function renderTranscript() {
  const box = $("tab-transcript");
  box.innerHTML = "<p class='sub'>loading transcript…</p>";
  try {
    const data = await rpc("python", "read_artifact", {
      project: lastProject,
      name: "transcript.json",
    });
    const segs = (data.json && data.json.segments) || [];
    box.innerHTML = "";
    if (!segs.length) {
      box.textContent = "The transcript is empty.";
      return;
    }
    for (const s of segs) {
      const el = document.createElement("div");
      el.className = "tseg";
      el.innerHTML = `<span class="tseg-time">${fmtDuration(s.start)}</span><span class="tseg-text"></span>`;
      el.querySelector(".tseg-text").textContent = (s.text || "").trim();
      box.appendChild(el);
    }
  } catch (err) {
    box.innerHTML = `<p class='sub'>transcript unavailable (${err.message})</p>`;
  }
}

async function renderArtifacts(data) {
  const box = $("tab-artifacts");
  box.innerHTML = "";
  const list = data.artifacts || [];
  if (!list.length) {
    box.textContent = "No artifacts were produced.";
    return;
  }
  const ul = document.createElement("ul");
  ul.className = "artifacts";
  for (const p of list) {
    const li = document.createElement("li");
    const rel = relOf(p, data.project);
    const isVideo = /\.(mp4|mkv|mov|webm)$/i.test(p);
    li.innerHTML = `<span class="art-kind">${isVideo ? "video" : "file"}</span>
      <span class="art-path"></span>
      <span class="art-size muted"></span>`;
    li.querySelector(".art-path").textContent = rel;
    box.appendChild(li);
    // Size is read from disk, not estimated.
    try {
      const info = await rpc("python", "stat_artifact", { path: p });
      li.querySelector(".art-size").textContent = humanBytes(info.size);
    } catch {
      li.querySelector(".art-size").textContent = "";
    }
    ul.appendChild(li);
  }
  box.appendChild(ul);
}

// ╭─ tabs ──────────────────────────────────────────────────────────────────╮

document.addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (!tab) return;
  for (const t of tab.parentElement.querySelectorAll(".tab")) {
    t.classList.toggle("active", t === tab);
  }
  const name = tab.dataset.tab;
  for (const panel of ["clips", "transcript", "artifacts"]) {
    $(`tab-${panel}`).hidden = panel !== name;
  }
});

// ╭─ open output folder ────────────────────────────────────────────────────╮

async function openOutput() {
  if (!lastProject) return;
  try {
    await (isTauri
      ? invoke("reveal_path", { path: lastProject })
      : Promise.resolve());
    if (!isTauri) {
      $("error-detail").textContent = lastProject;
      $("error").hidden = false;
    }
  } catch (err) {
    fail(`Could not open the output folder: ${err.message || err}`);
  }
}

// ╭─ boot ──────────────────────────────────────────────────────────────────╮

async function boot() {
  $("pick").addEventListener("click", pickViaDialog);
  $("analyze").addEventListener("click", runPipeline);
  $("open-output").addEventListener("click", openOutput);
  $("start-over").addEventListener("click", () => {
    chosenPath = null;
    $("chosen").hidden = true;
    $("chosen-meta").innerHTML = "";
    show("home");
  });
  $("toggle-log").addEventListener("click", (e) => {
    const log = $("log");
    const hidden = log.hidden;
    log.hidden = !hidden;
    e.target.textContent = hidden ? "hide" : "show";
  });
  $("error-dismiss").addEventListener("click", () => {
    $("error").hidden = true;
  });

  try {
    const health = isTauri
      ? await invoke("selekyt_health")
      : { python: await rpc("python", "ping").then((d) => ({ ok: true, data: d })) };
    const py = health.python;
    if (py && py.ok && py.data) {
      const d = py.data;
      $("engine").textContent = `engine ready · ${d.engine} v${d.version} · vad ${d.vad_backend}`;
      $("engine").classList.remove("muted");
    } else {
      $("engine").textContent = "engine unavailable — processing will fail";
      $("engine").classList.add("bad");
      $("pick").disabled = true;
      fail(
        py && py.error
          ? `The SelekyT engine could not start: ${py.error}`
          : "The SelekyT engine did not answer a health check. Reinstall the application.",
      );
    }
  } catch (err) {
    $("engine").textContent = "engine unreachable";
    $("engine").classList.add("bad");
    fail(`Cannot reach the SelekyT engine: ${err.message || err}`);
  }
}

boot();
