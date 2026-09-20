// Pure helpers for the SelekyT desktop UI: formatting, and the parsers that
// turn engine artifacts into things the views can render.
//
// Extracted from app.mjs for one reason — these are the functions whose
// correctness cannot be checked by looking at them. The timeline bar places
// kept intervals by arithmetic on edit_plan.json's source range, and the SRT
// reader imposes a grammar on a file the engine wrote; both are only provably
// right when run against a real project tree. Keeping them importable lets a
// test do exactly that (see tests/ui_contract.mjs).

export function fmtDuration(sec) {
  if (!Number.isFinite(sec)) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

export function humanBytes(n) {
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

export function fmtScore(v) {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(3) : "—";
}

export function pct(v) {
  return typeof v === "number" && Number.isFinite(v) ? `${Math.round(v * 100)}%` : "—";
}

export function basename(path) {
  return String(path || "").split(/[/\\]/).pop();
}

export function stem(path) {
  return basename(path).replace(/\.[^.]+$/, "");
}

export function sameSource(a, b) {
  // Absolute paths from different machines differ; the basename is the stable
  // identity a returning user will recognise, compared case-insensitively so a
  // re-encoded copy in a differently-cased folder still matches.
  return basename(a).toLowerCase() === basename(b).toLowerCase();
}

/** Percent positions of the kept intervals within a clip's source range.
 *
 * The bar renders the clip's [source_start, source_end] span as 100% and lays
 * the kept intervals on it; whatever is left between them is exactly what the
 * render cut out. Returns null when the plan is unusable rather than emitting
 * NaN-width bars, which the caller shows as "ships as one take".
 */
export function intervalLayout(plan) {
  if (!plan || !Array.isArray(plan.intervals) || plan.intervals.length === 0) return null;
  const span = (plan.source_end - plan.source_start) || 1;
  const bars = plan.intervals.map(([s, e]) => {
    const left = ((s - plan.source_start) / span) * 100;
    const width = ((e - s) / span) * 100;
    return {
      left: Math.max(0, Math.min(100, left)),
      width: Math.max(0.4, Math.min(100, width)),
      start: s,
      end: e,
    };
  });
  // Coverage must fill the bar once the gaps are included; if it does not, the
  // plan and the selection disagree and the bar would be lying about what ships.
  const kept = plan.intervals.reduce((acc, [s, e]) => acc + (e - s), 0);
  return {
    bars,
    span,
    kept,
    coverage: kept / span,
    cut: Math.max(0, span - kept),
  };
}

/** The gaps between kept intervals — the cuts the editor made. */
export function omittedIntervals(plan) {
  if (!plan || !Array.isArray(plan.intervals) || plan.intervals.length < 2) return [];
  const out = [];
  for (let i = 1; i < plan.intervals.length; i += 1) {
    const prev = plan.intervals[i - 1][1];
    const next = plan.intervals[i][0];
    if (next - prev > 0.001) out.push([prev, next]);
  }
  return out;
}

/** Minimal SRT reader: index line, timestamp line, text body.
 *
 * The engine also writes .ass and .json captions; this reads the format the UI
 * shows. A cue with no timestamp line or no text is skipped rather than
 * emitted as an empty bubble.
 *
 * Hours are 1-2 digits on purpose: the engine's own writer emits `0:00:00,787`
 * for sub-hour media, and a strict two-digit grammar silently rejects every
 * cue in every real caption file — which looks, in the UI, exactly like a
 * missing captions file.
 */
export function parseSrt(text) {
  const cues = [];
  const blocks = String(text).replace(/\r/g, "").split(/\n\n+/);
  const ts = /(\d{1,2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2},\d{3})/;
  for (const block of blocks) {
    const lines = block.split("\n").filter((l) => l.trim());
    const timeLine = lines.find((l) => ts.test(l));
    if (!timeLine) continue;
    const m = ts.exec(timeLine);
    const body = lines.filter((l) => !ts.test(l) && !/^\d+$/.test(l)).join(" ");
    if (!body.trim()) continue;
    cues.push({ start: m[1], end: m[2], text: body });
  }
  return cues;
}

/** True when a transcript segment overlaps the selected clip's source range. */
export function overlapsSegment(seg, clip) {
  if (!clip) return false;
  return seg.start < clip.end && seg.end > clip.start;
}
