const $ = (id) => document.getElementById(id);
const canvas = $("scores");
const ctx = canvas.getContext("2d");

// Mirrors the server's own sanitizeName so the path we suggest is the path
// the file was actually written under.
const sanitizeName = (name) => {
  const base = String(name || "upload.wav").replace(/[^\w.\-]/g, "_");
  return base.length > 0 && !base.startsWith(".") ? base : "upload.wav";
};

const state = {
  scores: [],
  segments: [],
  cutlist: [],
  duration: 0,
  frameMs: 32,
  meta: null,
  media: { sample: true },
};

async function post(op, params) {
  const res = await fetch(`/api/${op}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  const json = await res.json();
  if (!json.ok) throw new Error(json.error || `op failed: ${op}`);
  return json.data;
}

function setStatus(text, ok = true) {
  const el = $("status");
  el.textContent = text;
  el.classList.toggle("muted", ok);
  el.classList.toggle("error", !ok);
}

function draw() {
  const w = canvas.width;
  const h = canvas.height;
  ctx.fillStyle = "#101318";
  ctx.fillRect(0, 0, w, h);
  const th = parseFloat($("threshold").value);
  if (!state.scores.length) return;
  const n = state.scores.length;
  const barW = w / n;
  for (let i = 0; i < n; i++) {
    const v = state.scores[i];
    const y = h - Math.max(1, v * (h - 8));
    ctx.fillStyle = v >= th ? "#4ec9a8" : "#55606e";
    ctx.fillRect(i * barW, y, Math.max(1, barW - 1), h - y);
  }
  ctx.strokeStyle = "#f0a65a";
  ctx.beginPath();
  ctx.moveTo(0, h - th * (h - 8));
  ctx.lineTo(w, h - th * (h - 8));
  ctx.stroke();
  ctx.fillStyle = "rgba(78, 201, 168, 0.25)";
  for (const seg of state.segments) {
    const x0 = (seg.start / state.duration) * w;
    const x1 = (seg.end / state.duration) * w;
    ctx.fillRect(x0, 0, Math.max(1, x1 - x0), h);
  }
  ctx.strokeStyle = "#8a94a6";
  ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
}

function renderAudio(meta) {
  state.meta = meta;
  state.segments = meta.segments;
  state.scores = meta.scores || [];
  state.duration = meta.duration;
  state.frameMs = meta.frame_ms;
  $("m-backend").textContent = meta.backend;
  $("m-model").textContent = meta.vad_model || "—";
  $("m-fallback").textContent = meta.fallback_reason || "none";
  $("m-hyst").textContent = JSON.stringify(meta.meta?.hysteresis ?? {});
  $("m-frames").textContent = meta.n_frames;
  $("m-dur").textContent = `${meta.duration}s`;
  $("m-segs").textContent = meta.segments.length;
  $("meta-detail").textContent = JSON.stringify({ ...meta, scores: undefined }, null, 2);
  draw();
  renderCutlist();
  setStatus(
    `analyzed with ${meta.backend}${meta.vad_model ? " (" + meta.vad_model + ")" : ""} · ${meta.segments.length} segments`,
  );
}

function fmt(sec) {
  return `${Math.floor(sec / 60)}:${(sec % 60).toFixed(2).padStart(5, "0")}`;
}

async function renderCutlist() {
  const box = $("cutlist");
  const blocks = $("cutlist-blocks");
  blocks.innerHTML = "";
  if (!state.segments.length || !state.duration) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const pad = parseFloat($("pad").value) || 0;
  let data;
  try {
    data = await post("cutlist_from_segments", {
      segments: state.segments,
      source_duration: state.duration,
      pad,
    });
  } catch (err) {
    $("cutlist-summary").textContent = `cutlist failed: ${err.message}`;
    return;
  }
  state.cutlist = data.intervals;
  const kept = state.cutlist.filter((i) => i.kind === "keep");
  const kept_s = kept.reduce((a, i) => a + (i.end - i.start), 0);
  $("cutlist-summary").textContent =
    `keep ${fmt(kept_s)} of ${fmt(state.duration)} · ${Math.round(
      (100 * kept_s) / state.duration,
    )}% · ${kept.length} clips`;
  for (const iv of state.cutlist) {
    const el = document.createElement("div");
    el.className = `block ${iv.kind}`;
    el.style.left = `${(100 * iv.start) / state.duration}%`;
    el.style.width = `${(100 * (iv.end - iv.start)) / state.duration}%`;
    el.title = `${iv.kind} ${fmt(iv.start)} → ${fmt(iv.end)}`;
    el.textContent = `${iv.kind === "keep" ? "keep" : "cut"} ${fmt(iv.end - iv.start)}`;
    blocks.appendChild(el);
  }
}

async function resegment() {
  if (!state.scores.length) return;
  const th = parseFloat($("threshold").value);
  $("threshold-out").textContent = th.toFixed(2);
  try {
    const data = await post("segment", {
      scores: state.scores,
      threshold: th,
      min_speech_ms: 150,
      min_silence_ms: 100,
      frame_ms: state.frameMs,
    });
    state.segments = data.segments;
    draw();
    $("m-segs").textContent = data.segments.length;
    renderCutlist();
  } catch (err) {
    setStatus(`resegment failed: ${err.message}`, false);
  }
}

async function analyze(params) {
  setStatus("analyzing…");
  state.media = params;
  // An uploaded file lands at out/web/uploads/<name>. Surfacing that path here
  // is what connects "analyze this" to "now make cuts from it" — otherwise the
  // two halves of the shell cannot see each other's files.
  if (params.wav && params.name) {
    $("cuts-source").value = `out/web/uploads/${sanitizeName(params.name)}`;
  } else if (params.sample) {
    $("cuts-source").value = "out/web/sample.wav";
  }
  try {
    renderAudio(await post("vad_analyze", params));
  } catch (err) {
    setStatus(err.message, false);
  }
}

async function exportCut() {
  const panel = $("export-panel");
  const links = $("export-links");
  links.innerHTML = "";
  panel.hidden = false;
  $("export-summary").textContent = "running editorial stage…";
  try {
    const data = await post("export", state.media);
    const pct = (v) => `${Math.round(100 * (v || 0))}%`;
    $("export-summary").textContent =
      `compression ${data.compression_ratio?.toFixed(2)}× · speech ${pct(data.speech_ratio)} · dead air ${pct(data.dead_air_ratio)} · ${data.speech_regions} speech regions · backend ${data.vad_backend}`;
    const artifacts = [
      ["cutlist", data.cutlist],
      ["FCPXML", data.fcpxml],
    ];
    for (const [label, p] of artifacts) {
      if (!p) continue;
      const a = document.createElement("a");
      a.href = `/api/file?path=${encodeURIComponent(p)}`;
      a.download = "";
      a.textContent = `download ${label}`;
      links.appendChild(a);
    }
    if (!data.fcpxml) {
      const note = document.createElement("span");
      note.className = "muted";
      note.textContent = "FCPXML off";
      links.appendChild(note);
    }
  } catch (err) {
    $("export-summary").textContent = `export failed: ${err.message}`;
  }
}

async function runCuts() {
  // The full product path: transcribe → select → cut → render. Until this
  // existed the shell could analyse audio and export a cutlist but could not
  // produce a single edited video, which is what the tool is for.
  const panel = $("cuts-links");
  panel.innerHTML = "";
  const source = $("cuts-source").value.trim();
  const summary = $("cuts-summary");
  if (!source) {
    summary.textContent = "enter a path inside the project, or an http(s) URL";
    summary.classList.add("error");
    return;
  }
  summary.classList.remove("error");
  summary.textContent = "running… this transcribes and renders, so it takes a while";
  $("cuts-run").disabled = true;
  try {
    const params = {
      limit: parseInt($("cuts-limit").value, 10) || 6,
      model: $("cuts-model").value,
      caption_style: $("cuts-caption-style").value,
      audio_analysis: $("cuts-audio-analysis").checked,
    };
    // A cookies file only matters for a URL the pipeline has to fetch, and the
    // field is deliberately a path rather than a file upload: the bytes would
    // have to be written to disk inside the project, one `git add` away from
    // being leaked.
    const cookies = $("cuts-cookies").value.trim();
    if (cookies) params.cookies = cookies;
    if (/^https?:\/\//i.test(source)) {
      params.source = source;
    } else {
      params.path = source;
    }
    const data = await post("run_pipeline", params);
    const r = data.report;
    summary.textContent =
      `${r.selected_count} clip${r.selected_count === 1 ? "" : "s"} from ` +
      `${r.candidate_count} candidates · ${r.language ?? "?"} · ` +
      `${data.artifacts.filter((p) => p.endsWith(".mp4")).length} renders`;
    for (const p of data.artifacts) {
      const a = document.createElement("a");
      a.href = `/api/file?path=${encodeURIComponent(p)}`;
      a.download = "";
      // shorts/01.mp4 and facebook/01.mp4 have the same basename; labelling
      // both "01.mp4" gives two links nobody can tell apart.
      a.textContent = p.startsWith(data.project)
        ? p.slice(data.project.length + 1)
        : p.split(/[/\\]/).pop();
      a.title = p;
      panel.appendChild(a);
    }
  } catch (err) {
    summary.textContent = `cuts failed: ${err.message}`;
    summary.classList.add("error");
  } finally {
    $("cuts-run").disabled = false;
  }
}

async function init() {
  try {
    const ping = await post("ping", {});
    $("engine").textContent =
      `${ping.engine} v${ping.version} · backend ${ping.vad_backend} · ${ping.vad_model ?? ""}`;
  } catch (err) {
    $("engine").textContent = `sidecar unreachable: ${err.message}`;
  }
  $("analyze-sample").addEventListener("click", () => analyze({ sample: true }));
  $("export-fcpxml").addEventListener("click", exportCut);
  $("file-input").addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () =>
      analyze({ wav: String(reader.result).split(",")[1], name: file.name });
    reader.readAsDataURL(file);
  });
  $("threshold").addEventListener("input", resegment);
  $("pad").addEventListener("change", renderCutlist);
  $("cuts-run").addEventListener("click", runCuts);
  draw();
}

init();