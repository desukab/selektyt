const $ = (id) => document.getElementById(id);
const canvas = $("scores");
const ctx = canvas.getContext("2d");

const state = { scores: [], segments: [], duration: 0, frameMs: 32, meta: null };

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
  setStatus(
    `analyzed with ${meta.backend}${meta.vad_model ? " (" + meta.vad_model + ")" : ""} · ${meta.segments.length} segments`,
  );
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
  } catch (err) {
    setStatus(`resegment failed: ${err.message}`, false);
  }
}

async function analyze(params) {
  setStatus("analyzing…");
  try {
    renderAudio(await post("vad_analyze", params));
  } catch (err) {
    setStatus(err.message, false);
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
  $("file-input").addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () =>
      analyze({ wav: String(reader.result).split(",")[1], name: file.name });
    reader.readAsDataURL(file);
  });
  $("threshold").addEventListener("input", resegment);
  draw();
}

init();