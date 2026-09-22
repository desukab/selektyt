// The desktop UI's contract with the engine's artifacts, checked against a
// real project tree rather than a fixture.
//
// Every assertion below is about a shape the UI code assumes and the engine
// writes. A change on either side is invisible until the app runs, which here
// means a Windows install and a real transcription before anyone sees the
// breakage; this fails in a second instead.
//
// Run with node:  node tests/ui_contract.mjs <project-dir>
//
// The project dir is what `run_pipeline` writes under <out>/project. The
// default points at a run the engine actually produced.

import { existsSync, readFileSync, readdirSync } from "node:fs";;
import path from "node:path";
import process from "node:process";

import {
  basename,
  fmtDuration,
  fmtScore,
  humanBytes,
  intervalLayout,
  omittedIntervals,
  overlapsSegment,
  parseSrt,
  pct,
  sameSource,
  stem,
} from "../apps/desktop/ui/lib.mjs";

const project = process.argv[2] || "";

// Without a project tree (the CI case) only the project-independent checks
// below run: the formatters, the synthetic SRT grammar and the path helpers.
// The artifact-shape sections need a tree the engine really wrote, and are the
// point of passing one in. A committed fixture would not be that.
const haveProject = project !== "" && existsSync(path.join(project, "report.json"));

let failures = 0;
let skipped = 0;
function check(ok, message) {
  if (!ok) {
    failures += 1;
    console.error(`  FAIL  ${message}`);
  }
}
function readJson(name) {
  return JSON.parse(readFileSync(path.join(project, name), "utf8"));
}
function section(name) {
  console.log(`\n─ ${name}`);
}

console.log(haveProject ? `checking ${project}` : `no project tree given — running project-independent checks only`);

// ─── the pure formatters cannot produce NaN or "undefined" on real values ───

section("formatters never emit undefined or NaN");
for (const v of [0, 1.5, 1013.853, -1, NaN, Infinity, undefined, null]) {
  check(fmtDuration(v) !== "undefined", `fmtDuration(${v}) is not undefined`);
  check(humanBytes(v) !== "undefined", `humanBytes(${v}) is not undefined`);
  check(fmtScore(v) !== "undefined", `fmtScore(${v}) is not undefined`);
  check(pct(v) !== "undefined", `pct(${v}) is not undefined`);
}
check(fmtDuration(3661) === "1:01:01", `hours render as h:mm:ss, got ${fmtDuration(3661)}`);
check(fmtDuration(65) === "1:05", `minutes render as m:ss, got ${fmtDuration(65)}`);
check(humanBytes(1849730) === "1.8 MB", `binary units, got ${humanBytes(1849730)}`);
check(pct(0.8196) === "82%", `rounds to a whole percent, got ${pct(0.8196)}`);

// ─── the SRT grammar, on synthetic input (needs no project tree) ────────────

section("the SRT grammar on synthetic input");
// The known failure mode: a parser that splits on blank lines and forgets the
// index line emits an extra empty cue. Both the one-digit hours the engine
// writes and the two-digit form other tools emit are accepted, because a
// strict two-digit grammar silently rejects every real caption file and the
// captions tab then reports a missing file for media that has one.
const synthetic = "1\n0:00:00,000 --> 0:00:01,000\nhello\n\n2\n00:00:01,000 --> 00:00:02,000\nworld\n";
check(parseSrt(synthetic).length === 2, `two-cue SRT parsed to ${parseSrt(synthetic).length}`);
check(parseSrt("").length === 0, "an empty SRT parses to nothing, not to an error");
check(
  parseSrt("garbage with no timestamps at all").length === 0,
  "a file with no timestamps parses to nothing",
);
check(parseSrt("1\n0:00:00,000 --> 0:00:01,000\n\n").length === 0, "a cue with no text is dropped, not shown empty");

// ─── selections.json: every field a clip card or the rail renders from ──────

if (haveProject) {
section("selections.json has the fields the UI renders");
const selections = readJson("selections.json");
check(Array.isArray(selections) && selections.length > 0, "the run produced at least one selection");
const required = [
  "start", "end", "text", "story_type", "hook", "payoff",
  "editorial_score", "virality_score", "context_score", "confidence", "reason",
];
for (const [i, c] of selections.entries()) {
  for (const key of required) {
    check(
      key in c && c[key] !== null && c[key] !== undefined,
      `selection ${i + 1} is missing ${key} — a clip card would show an empty cell`,
    );
  }
  check(
    Number.isFinite(c.start) && Number.isFinite(c.end) && c.end > c.start,
    `selection ${i + 1} has a non-increasing range`,
  );
  // The timeline tab indexes clips from 1; the rail shows them from 1 too, so a
  // score that is not a 0–1 number would draw a nonsense bar.
  for (const key of ["editorial_score", "virality_score", "context_score", "confidence"]) {
    check(
      Number.isFinite(c[key]) && c[key] >= 0 && c[key] <= 1,
      `selection ${i + 1}.${key} is ${c[key]}, outside 0–1`,
    );
  }
}

// ─── edit_plan.json: the arithmetic the timeline bar is drawn from ───────────

section("edit_plan.json arithmetic, computed with the renderer's own function");
const plan = readJson("edit_plan.json");
check(Array.isArray(plan) && plan.length === selections.length, "one edit plan per selection");
for (const [i, p] of plan.entries()) {
  check(
    p.index === i + 1,
    `plan ${i} has index ${p.index}; the UI finds plans by 1-based index, so a mismatch renders the wrong timeline`,
  );
  const layout = intervalLayout(p);
  if (!layout) continue;
  // Every bar must sit inside the clip's own source range, or the timeline
  // would claim the clip covers media it does not.
  for (const bar of layout.bars) {
    check(bar.left >= 0 && bar.left <= 100, `plan ${i + 1} bar left ${bar.left.toFixed(2)} is off the bar`);
    check(bar.width > 0 && bar.left + bar.width <= 100.5, `plan ${i + 1} bar overruns 100%`);
  }
  // kept + omitted must reconstruct the source span; otherwise the bar and the
  // "N s removed" label would disagree.
  const omitted = omittedIntervals(p);
  const omittedTotal = omitted.reduce((a, [s, e]) => a + (e - s), 0);
  check(
    Math.abs(layout.kept + omittedTotal - layout.span) < 0.05,
    `plan ${i + 1}: kept ${layout.kept.toFixed(2)} + omitted ${omittedTotal.toFixed(2)} != span ${layout.span.toFixed(2)}`,
  );
  check(
    Math.abs(layout.cut - p.removed_seconds) < 0.05,
    `plan ${i + 1}: renderer derived ${layout.cut.toFixed(2)}s removed but the engine reports ${p.removed_seconds}s`,
  );
  check(
    Math.abs(layout.kept - p.edited_duration) < 0.05,
    `plan ${i + 1}: renderer derived ${layout.kept.toFixed(2)}s shipped but the engine reports ${p.edited_duration}s`,
  );
  // The caption timing and the transcript highlight both key off these ranges.
  check(p.source_end > p.source_start, `plan ${i + 1} source range is not increasing`);
}

// ─── captions: the real SRT grammar, on a file the engine really wrote ───────

section("captions parse from a real written SRT");
const capDir = path.join(project, "captions");
let srtFiles = [];
try {
  srtFiles = readdirSync(capDir).filter((f) => f.endsWith(".srt")).sort();
} catch {
  console.error("  SKIP  no captions directory");
}
check(srtFiles.length > 0, "the engine wrote at least one .srt");
for (const file of srtFiles.slice(0, 3)) {
  const text = readFileSync(path.join(capDir, file), "utf8");
  const cues = parseSrt(text);
  check(cues.length > 0, `${file} parsed to zero cues`);
  // Every cue must carry both timestamps and text, or the captions view would
  // render empty rows that look like a missing file.
  for (const [j, cue] of cues.entries()) {
    check(/^\d{1,2}:\d{2}:\d{2},\d{3}$/.test(cue.start), `${file} cue ${j + 1} start is not a timestamp: ${cue.start}`);
    check(/^\d{1,2}:\d{2}:\d{2},\d{3}$/.test(cue.end), `${file} cue ${j + 1} end is not a timestamp: ${cue.end}`);
    check(cue.text.trim().length > 0, `${file} cue ${j + 1} has no text`);
  }
  // A cue count far beyond what a ~30 s clip can hold means the parser is
  // splitting mid-cue and inventing rows.
  check(cues.length < 400, `${file} parsed to ${cues.length} cues, suspiciously many`);
}

// ─── transcript.json: the highlight range and the segment shape ──────────────

section("transcript.json segments overlap the selections");
const transcript = readJson("transcript.json");
check(Array.isArray(transcript.segments), "transcript has a segments array");
check(transcript.segments.length > 0, "the transcript is not empty");
let anyOverlap = false;
for (const s of transcript.segments) {
  check(Number.isFinite(s.start) && Number.isFinite(s.end), "a segment has non-numeric times");
  check(typeof s.text === "string", "a segment has no text");
  if (selections.some((c) => overlapsSegment(s, c))) anyOverlap = true;
}
check(anyOverlap, "no transcript segment overlaps any selection — the highlight would mark nothing");

// ─── report.json / qa.json: the fields the summary and quality panes read ────

section("report.json and qa.json expose the summary fields");
const report = readJson("report.json");
for (const key of ["source", "language", "selected_count", "candidate_count", "outputs", "captions", "timeline"]) {
  check(key in report, `report.json is missing ${key}`);
}
check(report.outputs && report.outputs.facebook && report.outputs.shorts, "both aspect buckets exist");
for (const bucket of ["facebook", "shorts"]) {
  const dir = report.outputs[bucket];
  let n = 0;
  try {
    n = readdirSync(dir).filter((f) => f.endsWith(".mp4")).length;
  } catch {
    // Missing is reported by the shell's project listing, not here.
  }
  check(
    n === report.selected_count,
    `${bucket} has ${n} mp4s for ${report.selected_count} selections`,
  );
}
const qa = readJson("qa.json");
for (const key of ["ok", "per_file"]) {
  check(key in qa, `qa.json is missing ${key}`);
}
for (const [file, entry] of Object.entries(qa.per_file || {})) {
  for (const key of ["audio_condition", "silent_tail", "black_frame_pct", "size"]) {
    check(key in entry, `qa entry for ${basename(file)} is missing ${key}`);
  }
  check(
    entry.errors === undefined || Array.isArray(entry.errors),
    `qa entry for ${basename(file)} has a non-array errors field`,
  );
}

// ─── path helpers: the identity a resumed project is matched on ──────────────

// ─── the bench and the ASR model the summary strip renders as chips ─────────

// Both come from report.json and both are surfaced without a second file open:
// the bench because the "+N on the bench" chip promises clips the count names,
// the transcription block because a downgrade changes what the word timing is
// worth. A run that ships everything writes an empty bench, and a transcript
// from before the block existed writes nulls — both are valid, so the checks
// are on shape, not on presence of a value.
check("bench" in report, "report.json is missing bench — the summary chip reads r.bench");
check(Array.isArray(report.bench), `bench is a ${typeof report.bench}, the chip iterates it`);
if (report.bench.length) {
  for (const [i, b] of report.bench.entries()) {
    for (const key of ["start", "end", "text", "story_type", "hook", "payoff",
                      "editorial_score", "virality_score", "context_score",
                      "confidence", "reason", "total_score"]) {
      check(key in b, `bench entry ${i + 1} is missing ${key} — it would render an empty cell next to a shipped clip`);
    }
    check(!("words" in b), `bench entry ${i + 1} carries word timings; the bench is a ranking view, not a re-render`);
    check(Number.isFinite(b.total_score), `bench entry ${i + 1} total_score is ${b.total_score}, not a number`);
  }
} else {
  console.log(`  SKIP  bench is empty (${report.selected_count} shipped, nothing lost to the clip budget)`);
}
check("transcription" in report, "report.json is missing transcription — the model chip reads r.transcription");
for (const key of ["engine", "requested_model", "model", "downgrade_reason", "word_timestamps", "fallback_reason"]) {
  check(key in report.transcription, `transcription is missing ${key} — a trust judgement needs it`);
}
// The block is a copy of transcript.json's `asr`, and a resumed run reads it
// back from there rather than from a value the transcription stage never set.
// A mismatch means the copy drifted from the source of truth.
if (transcript.asr) {
  for (const key of ["engine", "requested_model", "model", "downgrade_reason", "word_timestamps", "fallback_reason"]) {
    check(
      JSON.stringify(report.transcription[key]) === JSON.stringify(transcript.asr[key]),
      `report.transcription.${key} is ${report.transcription[key]} but transcript.json says ${transcript.asr[key]}`,
    );
  }
} else {
  console.log("  SKIP  transcript.json predates the asr block, so the model is not recorded");
}
// The recovery marker: the summary strip renders a warning chip on a non-null
// value, and a resumed run reads it back from transcript.json rather than from
// a value the transcription stage never set. The key is on every report so
// that null reads as "the transcript came from the file you handed in".
check("container_recovery" in report, "report.json is missing container_recovery — the chip tests r.container_recovery");
if (report.container_recovery !== null) {
  check(typeof report.container_recovery === "string", `container_recovery is a ${typeof report.container_recovery}, the chip renders it`);
  check(report.container_recovery === transcript.container_recovery, "report.json's recovery note disagrees with transcript.json, the source of truth on a resume");
} else if ("container_recovery" in transcript) {
  check(transcript.container_recovery === null, "transcript.json records a recovery the report does not carry");
}

}
section("project identity helpers");
check(sameSource("/a/b/Pentagon.mp4", "C:\\x\\PENTAGON.MP4") === true, "case and OS differ but the basename matches");
check(sameSource("/a/b/x.mp4", "/a/b/y.mp4") === false, "different files do not match");
check(stem("/a/b/pentagon.mp4") === "pentagon", "stem strips one extension");
check(basename("C:\\Users\\me\\v.mp4") === "v.mp4", "basename splits backslashes");

console.log(
  `\n${failures === 0 ? "PASS" : `${failures} FAILURE(S)`} — ${project}`,
);
process.exit(failures === 0 ? 0 : 1);
