# Verify-Repair Design

Subtitle: an audit of the Edit IR and export stack, and a specification for VERIFY → DIAGNOSE → REPAIR → VERIFY AGAIN built entirely on components that already exist.

## 0. How to read this document

Every claim is tagged:

- **[CODE]** — stated by the code itself (docstring, comment, signature or behaviour I read). File:line given.
- **[INFER]** — my conclusion from reading the code. Where I infer, I say so, and I do not infer where the code already speaks.

The brief said: *if you claim duplication exists, quote the code that shows it*. Section 2 quotes it.

---

## 1. Current Edit IR and export stack

### 1.1 Three interval vocabularies exist, and they are not one thing

The codebase carries three distinct interval representations for "what is kept":

| Vocabulary | Module | Record shape | Consumed by |
|---|---|---|---|
| `CutList` | `engine/jumpcut.py:40` | `CutSegment` (`engine/jumpcut.py:28`) typed by `SegmentType` (`engine/jumpcut.py:20`: KEEP / REMOVE_SILENCE / REMOVE_FILLER / REMOVE_HALLUCINATION); totals computed in `__post_init__` (`engine/jumpcut.py:50`) | `engine/fcpxml.py` |
| `CutSpan` / `EditCutList` | `engine/cut_algebra.py:13` / `:384` | `namedtuple("CutSpan", ["kind","start","end"])`; `Keep` (`:364`), `EditCut` (`:374`) | the Rust core's wire format; `cutlist_from_wire` (`engine/jumpcut.py:63`) |
| `Timeline` / `Clip` | `engine/timeline.py:210` / `:87` | frozen dataclass clips placed on tracks with source-in/timeline-in pairs | `engine/nle_export.py`, `engine/timeline_captions.py` |

`engine/cut_algebra.py:388-389` declares `EditCutList` to be *"This is the single representation the Python sidecar, the Rust core, and eventually the Tauri shell speak."* **[INFER]** That claim is aspirational, not descriptive: `engine/jumpcut.py` defines its own `CutList` and `engine/timeline.py` its own `Clip`, and no module converts between the three. The three vocabularies coexist because they serve three entry points (sidecar ops, dead-air removal, editorial export), not because one was promoted.

### 1.2 The deliverable path — one plan, two consumers

For the shipped-clip path there genuinely is one editorial driver. `engine/edit_plan.py:153` defines

```
retention_intervals(c, min_pause=0.70, keep_pause=0.18, keep_edge=0.06) -> list[tuple[float,float]]
```

which builds conservative source intervals from word timestamps — cutting only between words, shortening long pauses rather than deleting them, and falling back to the intact span when the result would fragment (`len(intervals) > 3 or kept / original < 0.72`). Two consumers call the same function on the same candidate:

- `engine/render.py:356-358` — `render_clip(...)`: `intervals = retention_intervals(candidate)`, then concatenates those intervals (single-part move, else concat demuxer) and slices the ASS per part with `offset = sum(b - a for a, b in intervals[:i])` (`engine/render.py:376`).
- `engine/timeline_build.py:23,43` — `timeline_for_candidate(...)` calls `intervals = retention_intervals(candidate)` and accumulates the record cursor over kept durations only (`:56-71`), producing the `Timeline` that `engine/nle_export.py` exports.

Its docstring states the contract **[CODE]**: *"so an exported EDL or FCPXML describes the video that was actually written: the same intervals, the same order, the same accumulated record cursor."*

The pipeline persists those intervals as the shared truth: `(project / "edit_plan.json").write_text(...)` at `selekyt/pipeline.py:1111`, deliberately free of file paths *"No file paths here: edit_plan.json is a deterministic artifact"* (`:1105`) so two runs agree byte for byte. **[INFER]** `edit_plan.json` is therefore the natural ground truth for any verify stage, and `tools/nle_check.py` already uses it that way (`:481-505`, `_retained_span`).

### 1.3 Record shapes, invariants and where they are enforced

| Record | Where invariants are enforced | What is enforced |
|---|---|---|
| `Clip` (`engine/timeline.py:87`, frozen) | `__post_init__` `engine/timeline.py:107-123` | `source_out > source_in`; `timeline_out > timeline_in`; `timeline_in >= 0`; name defaults to source name. `source_timecode_in/out` (`:137/:148`) and `_source_frames()` (`:154`) force whole-frame bracketing. |
| `Timeline` (`engine/timeline.py:210`) | `__post_init__` `:233`; `validate()` `:302` | clips sorted by `(timeline_in, track)`; tracks auto-declared; non-positive fps raises. `validate()` checks source-vs-timeline duration drift within half a frame (exact when `strict_timing`), `source_out <= media.duration + 1e-6`, `source_in >= 0`, per-track no-overlap. Its docstring: *"a re-timed clip is not representable by this model on purpose."* |
| `EditCutList` (`engine/cut_algebra.py:384`) | `normalize_keeps` `:394` | clips to `[0, duration]`, drops degenerate, sorts, fuses genuine overlaps only (touching stays two keeps so indices don't renumber); active wins over disabled on overlap (`:427`). |
| `CutList` (`engine/jumpcut.py:40`) | `__post_init__` `:50` | `total_keep_duration` / `total_remove_duration` / `compression_ratio` derived, never settable. |
| `IntervalSet` (`engine/memory/intervals.py:233`) | `_normalise` `:483` | sorted, disjoint, merged at `MERGE_EPS = 1e-6` (`:67`). |
| `Evidence` (`engine/memory/evidence.py:132`) | `__post_init__` `:153-160` | confidence clamped to `[0,1]`; `relation` validated against the thirteen `ALLEN_RELATIONS` (`engine/memory/intervals.py:74`) or `ValueError`. |

### 1.4 The export stack

**`engine/fcpxml.py` (379 lines) — FCPXML emitter #1, cutlist-driven.** Consumes `engine.jumpcut.CutList`. `kept_intervals` (`:97`), `ensure_exportable` (`:107`, raises if nothing to keep), `_xml_escape` (`:115`), `_to_file_uri` (`:125`), `render` (`:199`) with a frame-counting record cursor and `dur_frames = max(1, int(round(cut_duration / frame_secs)))`. Entered via `selekyt/server.py:289-298` (`op_render_fcpxml`) as the Python half of the Rust sidecar op, byte-identical with `rust/core/src/export_fcpxml.rs::render` as asserted by `tests/test_sidecar_parity.py`. Its own docstring is candid **[CODE]**: all 27 committed timelines declare `asset start="0s"` and `tcFormat="NDF"`; the drop-frame branch is verified against constructed input only; *"No NLE has yet imported one of these documents in this project's history."*

**`engine/nle_export.py` (1040 lines) — FCPXML emitter #2 + EDL, clip-driven.** Consumes `engine.timeline.Timeline`. EDL: `_EdlEvent` (`:187`), `_channel_code` (`:201`), `_edl_events` (`:239`, record-advance fix `rec_in_frames = max(own_in, max((rec_end.get(ch, 0) for ch in channels), default=0))`), `render_edl` (`:313`), `export_edl` (`:348`). FCPXML: `_asset_id_map` (`:366`), `_asset_xml` (`:370`), `_clip_xml` (`:392`), `_gap_xml` (`:413`), `render_fcpxml` (`:433`, storyline with explicit `<gap>` filler, connected clips nested inside the covering element), `export_fcpxml` (`:528`), `_require_exportable` (`:535`, calls `timeline.validate()` and refuses on problems). `export(...)` (`:1004`) writes both formats.

**`engine/render.py` (397 lines)** — `require_ffmpeg` (`:16`); `_frame_count` (`:137`) whole-frame resolution so a 15-part supercut stays frame-accurate against its own EDL; caption font-coverage refusal (`_covered_codepoints` `:38`, `uncovered_script` `:87`, `_check_caption_coverage` `:101` — raises rather than burn tofu); `_encoder_args` (`:177`, `["-preset","slow","-crf","18","-pix_fmt","yuv420p"]`); `_render_one` (`:298`) with the input-seek-vs-output-seek audio-drop retry at `:349-353`.

**`engine/timeline.py` (441 lines)** — the placement IR; `format_smpte` (`:391`) is the write half that inverts `parse_smptime`, drop-frame snapped through the parser until it round-trips.

**`engine/timecode`** — shared frame/rational source of truth for both FCPXML emitters and the EDL.

---

## 2. Shared vs duplicated — the verdict

**Verdict: for the deliverable path there is genuinely one IR, and it is `retention_intervals`. Duplication is real but confined to the two FCPXML emitters, it is deliberate and documented, and it is duplication of *plumbing*, not of *editorial decisions*.**

### 2.1 What is shared (code fact)

Both halves of the shipped artifact are computed from one function call:

```python
# engine/render.py:358
intervals = retention_intervals(candidate)
```
```python
# engine/timeline_build.py:43
intervals = retention_intervals(candidate)
```

No exporter re-derives which seconds to keep. `render.render_clip` and `timeline_build.timeline_for_candidate` are two projections of one list. **[INFER]** This is the single most important structural fact for the verify-repair design: because there is one plan, verification can be phrased as "does every artifact agree with the plan", and repair can be phrased as "re-emit from the plan" — no exporter holds private editorial state worth patching.

### 2.2 What is duplicated (code fact, quoted)

`engine/nle_export.py` states the split in its own module docstring, `:13-16` **[CODE]**:

> It deliberately does **not** reuse ``engine/fcpxml.py``'s cutlist-driven emitter: that one speaks AutoCut's keeps/removes vocabulary, and this one speaks an editor's clips. They share ``engine/timecode`` for every frame and rational.

That is a fork with a reason, not an accident. But two helpers are duplicated near-verbatim:

`engine/fcpxml.py:115-122`:
```python
def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
```

`engine/nle_export.py:78-85`:
```python
def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
```

Byte-identical. And `_to_file_uri` exists twice — `engine/fcpxml.py:125-150` and `engine/nle_export.py:88-127` — with the same RFC-3986 byte-encoding loop, the same `RuntimeError` on an unresolvable path, and the same rationale comment about a relative URI resolving against the timeline's own directory. The `nle_export` copy is the superset: it adds Windows drive/UNC handling via `resolved.as_uri()` (`:119-120`) that the `fcpxml` copy lacks. **[INFER]** So the two copies have already drifted apart — the stronger argument for consolidating them into one shared helper.

Asset/clip element emission, the FCPXML document skeleton, and drop-frame detection are likewise written independently in both modules.

### 2.3 Asymmetry in validation between the two emitters (code fact)

- `engine/nle_export.py` has text-level validators (`validate_edl` `:736`, `validate_fcpxml` `:582`, `_cross_check_fcpxml` `:695`) and independent third-party readers (`independent_parse_edl` `:891` via pycmx; `independent_parse_fcpxml` `:919` via the vendored OpenTimelineIO adapter under `third_party/otio_fcpxml_adapter/`).
- `engine/fcpxml.py` has `ensure_exportable` (`:107`) only, and relies on `tests/test_fcpxml_multitrack.py` and `tests/test_sidecar_parity.py`. It has no independent-reader check. **[INFER]** If the sidecar FCPXML path ever needs a verify stage, its validators must be built or imported; the editorial path's cannot be reused as-is because they speak `Timeline`, not `CutList`.

### 2.4 Minor duplication worth noting

`_within_frames` (`engine/nle_export.py:990`) and `_frame_delta_verdict` (`tools/nle_check.py:114`) implement the same slack-frames arithmetic in two processes. Harmless — they are separated by a process boundary — but the *tolerance values* are the real contract: 1.5 frames for interchange-vs-plan, `RENDER_FRAME_TOLERANCE` (2.0) for render-vs-timeline, 1% for container duration (`engine/qa.py:335`). **[INFER]** A verify stage should name these constants once and import them, rather than re-spelling a fourth tolerance.

---

## 3. Existing verification inventory

### 3.1 What exists (three layers)

**Layer 1 — in-model, at construction.** `Clip.__post_init__` (`engine/timeline.py:107-123`); `Timeline.__post_init__` (`:233`) and `Timeline.validate(strict_timing=False)` (`:302`); `EditCutList` via `normalize_keeps` (`engine/cut_algebra.py:394`); `cutlist_from_intervals` degenerate-drop/merge (`engine/cut_algebra.py:16-60`); `Evidence` confidence clamp and Allen validation (`engine/memory/evidence.py:153-160`); `IntervalSet._normalise` (`engine/memory/intervals.py:483`).

**Layer 2 — in-pipeline, on emitted artifacts.** In `selekyt/pipeline.py`'s export block: `validate_edl(edl_text, fps=source_fps)`, `independent_parse_edl`, `validate_fcpxml(text, tl)`, `independent_parse_fcpxml`, and on any problem `raise RuntimeError(f"clip {idx:02d} exported an interchange file that failed its own validation: ...")`. A comment there documents the design as *"two legs meeting at the plan"*: the validators check timeline-vs-plan on the frame grid, `write_report` probes rendered MP4 durations against `edited_duration` at 1%, and `tools/nle_check.py` does the direct render-vs-timeline comparison at two-frame tolerance but is **deliberately not invoked in-pipeline**.

Also in-pipeline: the coverage gate (`selekyt/pipeline.py:795-802`, raises on `coverage_collapse`, `--skip-coverage-check` to pass once a human has checked the transcript); the language-fitness gate (`:758-759`, `is_refusable`); `engine/qa.py` `validate_output` (`:227`) / `validate_outputs` (`:361`) / `validate_outputs_expanded` (`:554`) covering dimensions, display aspect (not pixel count — *"the shipped landscape deliverable shipped at DAR 320:243 — 4:3 — labelled 16:9"*), decoded-frame proof, fps stamping, audio condition classification, and the 1% plan-duration drift check that *"is the only check that can see a wrong cut as opposed to a broken file"* (`:327-341`); and `engine/coverage.py:check` (`:256`) / `engine/language_fitness.py:fitness` (`:274`).

**Layer 3 — out-of-band auditors.**

`tools/editorial_audit.py` (1103 lines) — the corpus benchmark. Per `:13-49` it measures: mid-word cut, mid-sentence cut (against the transcript's own utterance spans, not the candidate text, *"the text joins under-punctuated segments and reads clean cuts as splits"*), short final word (<200ms), orphaned cue, cue overrun, span mismatch, silence share (with the shipped render **decoded** and measured acoustically when the timings are ambiguous — *"the finding states a measurement instead of a guess"*), duplication, selection size, and orphaned edge. `check_edges_respect_sentences` (`:260`) is the check that found both real defects in the corpus, because it looks *outward* at the words the boundary dropped: pentagon-esper-briefing clip 04 opening on " Syria." and royal-society-cloke clip 01 ending mid-word on " Somebody" (`:284-289`). Three-state verdicts via `_state_of` (`:838`): **clean / review / fail**, and *"REVIEW still fails the exit code: a clip the tool could not measure is not a clip it has passed."* `sweep` (`:846`) and `compare_sweeps` (`:892`) give the corpus-level drift comparison against `corpus/eval/audit-baseline-2026-10-05.json`, classifying movement as improvement / regression / changed / added / removed — a clip that *"swapped one defect for another is not a wash"*.

`tools/nle_check.py` (892 lines) — the NLE auditor. Per `:1-38` it checks the failures that only appear when someone opens the project in Premiere/Resolve/FCP: media an NLE cannot relink, overlapping record spans, timeline-vs-render duration disagreement, and *"a cut that keeps several intervals, exported with one of them missing or out of order. The total duration is unchanged, so the check above passes and only an interval-by-interval comparison notices."* Both halves are parsed by readers SeleKyT did not write (pycmx; the vendored OTIO adapter, *"the only third-party FCPXML reader found that resolves an asset's ``src`` from ``<media-rep>``"*), and *"Validating our output with our own parser proves nothing"* — a missing reader FAILS rather than skips. `_interval_findings` (`:449`) compares intervals one at a time and in order against `edit_plan.json`; `_check_edl` `:289`; `_check_fcpxml` `:507`; plus an EDL-vs-FCPXML mutual agreement check (`:829-851`).

`engine/story_view.py:743` `verify_story_view` — cross-checks an exported view against the imported corpus, returning disagreements; *"it never means the corpus is wrong, since the corpus is the memory and the view is a report of it."*

### 3.2 The corpus numbers, and what the audit does not have

`docs/product-gap-map.md` records 11 runs / 16 clean clips / 11 awaiting review / 4 project-level failures, with **no clip-level failures at all**; the four failures are project-level VAD/coverage collapse where the transcript came back empty. It also states the honest limit: the audit *"has no ground truth — it compares a clip against the transcript it was cut from."*

### 3.3 What does not exist (code fact)

I grepped `engine/`, `selekyt/`, `tools/` for `def verify`, `def diagnose`, `def repair`, `FaultClass`, `REPAIR`, `DIAGNOSE`. There is **no verify-repair loop, no fault-class enum, no diagnosis mapping and no repair registry anywhere.** Every hit is a docstring or comment anticipating one:

- `engine/memory/intervals.py:363` — `covers`: *"The verify stage asks this: 'is the whole proposed clip backed by evidence, or does part of it sit in a gap I have no support for?'"*
- `engine/memory/intervals.py:376` — `gaps`: *"Inverting the question is how dead air, unsupported spans and unsearched regions are found."*
- `engine/memory/evidence.py:257` — *"Filters report rather than repair."*
- `engine/memory/evidence.py:311` — `below_confidence`: *"'what did I discard' is a question the verify stage asks."*

### 3.4 Ground truth: what is populated and what is not (critical, code fact)

The memory layer is **partially built**. `engine/memory/__init__.py:41-47` documents four modules — `intervals`, `evidence`, `store` (`VideoMemory`, *"the persistent, queryable world for one source"*), and `ingest` (*"Adapters that capture signals the pipeline already computes into memory"*). On disk there are exactly two: `evidence.py` and `intervals.py`. `store` and `ingest` do not exist. And `engine.memory.evidence` is imported by nothing outside `engine/memory/__init__.py` and `tests/test_memory_evidence.py`.

**[INFER]** Consequence for this spec: `Evidence` is a designed-in ground truth, not a populated one. No pipeline stage currently emits an `Evidence`. The verify spec below therefore defines a **ground-truth ladder** with an explicit rung for evidence that is active only once an ingest layer exists, and every evidence-backed check degrades explicitly to transcript-word coverage until then. This is the boundary between "what the code does" and "what it anticipates".

---

## 4. The verify-repair specification

### 4.1 Design principle, taken from the code

Three existing behaviours set the tone the loop must obey:

1. **Repair is already a known, recorded operation here** — the transcription stage already repairs a damaged container with a stream-copy remux (`selekyt/pipeline.py:452-497`), is explicit that *"a remux of a silent file is a silent file"*, that *"The remux is a stream copy, never a re-encode"*, and records that the user got *"a repaired container, not the file they handed in"* (`:714`), with the fact and the reason persisted (`:1159-1164`).
2. **Filters report rather than repair** (`engine/memory/evidence.py:257`) — a dropped low-confidence item is reported, never quietly smoothed.
3. **Evidence is not a judgement** (`engine/memory/evidence.py:30-40`) — *"'Clip this span' is an editorial decision... Evidence is the input to that weighing."* **[INFER]** The loop must therefore never manufacture editorial decisions; it may snap, shrink, re-emit or escalate, and nothing else.

### 4.2 Ground truth ladder

Verification compares against the most authoritative truth available, in this order:

| Rung | Truth | Used by today | Status |
|---|---|---|---|
| G1 | `edit_plan.json` — the intervals the render concatenated | `tools/nle_check.py:481-505`, `engine/qa.py:327-341` | **populated** (`selekyt/pipeline.py:1111`) |
| G2 | transcript word timestamps + utterance spans | `tools/editorial_audit.py:729-755` | **populated** |
| G3 | `Evidence` intervals + confidence (`IntervalSet.covers` `:363`, `EvidenceSet.below_confidence` `:309`) | nothing yet | **specified, unpopulated** — see 3.4 |

A check at G3 that finds no evidence store **does not pass**. It degrades to G2 word coverage and is marked as such, mirroring the audit's rule that an undecidable clip fails the exit code rather than being recorded as a pass nothing supports.

### 4.3 Fault classes (explicit enum)

```python
class Fault(str, Enum):
    """What a failed check is. Each maps to one check, one diagnosis
    and a set of permitted repairs. UNDECIDABLE is the escape hatch
    that keeps the loop terminating."""
    MID_WORD_CUT        = "mid_word_cut"          # boundary inside a spoken word
    MID_SENTENCE_CUT    = "mid_sentence_cut"      # boundary splits an utterance
    SHAVED_TAIL_WORD    = "shaved_tail_word"      # last kept word < 200ms
    UNSUPPORTED_SPAN    = "unsupported_span"      # kept time with no backing evidence
    LOW_CONFIDENCE_ONLY = "low_confidence_only"   # backed only below the floor
    DUPLICATE_SENTENCE  = "duplicate_sentence"    # same sentence in two clips
    CAPTION_TEXT_DRIFT  = "caption_text_drift"    # cue text absent from the transcript
    CAPTION_SPAN_DRIFT  = "caption_span_drift"    # cue ends past the render
    INTERCHANGE_DRIFT   = "interchange_drift"     # EDL/FCPXML interval off the plan
    RECORD_OVERLAP      = "record_overlap"        # non-monotonic record per channel
    RENDER_DRIFT        = "render_drift"          # MP4 duration off the plan
    RENDER_UNDECODABLE  = "render_undecodable"    # declared stream decodes to nothing
    MEDIA_UNRESOLVABLE  = "media_unresolvable"    # FROM CLIP / asset src not relinkable
    UNDECIDABLE         = "undecidable"           # the check cannot decide — REVIEW
```

### 4.4 Each fault: check, diagnosis, permitted repair

| Fault | VERIFY (existing component) | DIAGNOSE | REPAIR (permitted set) |
|---|---|---|---|
| `MID_WORD_CUT` | `check_cuts_land_between_words` (`tools/editorial_audit.py:91`); producer-side twin `edit_plan._word_aligned` (`engine/edit_plan.py:28`) | boundary lies inside a word span | **SNAP** to the word's start or end via `edit_plan.align_span` (`engine/edit_plan.py:92`), which returns aligned bounds, text and word list; `_BOUND_EPS = 1e-6` (`:13`) leaves an ulp-straddling bound alone |
| `MID_SENTENCE_CUT` | `check_cuts_respect_sentences` (`:173`) + `check_edges_respect_sentences` (`:260`), against transcript utterance spans | boundary drops a word the clip kept, or the clip opens on the tail of a cut sentence | **SNAP** to the utterance edge from `transcript.json` segments; if the utterance carries **no terminal punctuation** (34-47% of English segments, `:300-310`) → `UNDECIDABLE` (REVIEW), never a guess |
| `SHAVED_TAIL_WORD` | `check_last_word_is_not_shaved` (`:384`) | last kept word < 200ms | **SNAP** `end` to the word's end, clamped to `source_duration` (`cut_algebra.py:394` clamps the same way) |
| `UNSUPPORTED_SPAN` | `IntervalSet.covers(span)` (`engine/memory/intervals.py:363`) on `EvidenceSet.intervals` (`engine/memory/evidence.py:284`); degrades to `IntervalSet` of word spans | part of a kept interval sits in an evidence gap, found precisely by `gaps(bounds)` (`:376`) | **TRIM** the uncovered sub-span via `IntervalSet.subtract` (`:415`), or **SNAP** to `IntervalSet.nearest(t)` (`:325`, *"the grounding operation"*); if the result is empty → escalate |
| `LOW_CONFIDENCE_ONLY` | `EvidenceSet.below_confidence(floor)` (`engine/memory/evidence.py:309`), `above_confidence` (`:306`) | support exists but only below the floor | **TRIM** to `above_confidence(floor).intervals`, or **ESCALATE**; the dropped items are reported, per *"Filters report rather than repair"* (`:257`) — confidence is never inflated |
| `DUPLICATE_SENTENCE` | `check_no_duplicate_sentences` (`tools/editorial_audit.py:630`) | the same sentence ships in two clips | **TRIM** the lower-ranked clip's overlapping interval; the ranking already exists (`editorial_score`/`virality_score` on `Candidate`). Never re-score |
| `CAPTION_TEXT_DRIFT` | `check_caption_text_is_transcript_text` (`:406`) | a cue's text is not in the candidate's transcript | **RE-EMIT** captions from `edit_plan.join_words` (`engine/edit_plan.py:61` — the only correct way to rebuild text from whisper tokens). Never patch a cue string |
| `CAPTION_SPAN_DRIFT` | `check_cues_fit_the_render` (`:422`), `_probe_duration` (`:827`) | a cue ends past the rendered duration | **RE-EMIT** with per-part ASS slicing at `offset = sum(b - a for a, b in intervals[:i])` (`engine/render.py:376`); font coverage is a hard refusal, not a repair (`engine/render.py:101`) |
| `INTERCHANGE_DRIFT` | `_interval_findings` (`tools/nle_check.py:449`), `validate_edl` (`engine/nle_export.py:736`), `validate_fcpxml` (`:582`), `_cross_check_fcpxml` (`:695`) | an EDL/FCPXML interval disagrees with `edit_plan.json` | **RE-EMIT** from the plan: `timeline_for_candidate` (`engine/timeline_build.py:23`) then `nle_export.export` (`:1004`). **Never edit emitted text** — the exporter is a pure function of the plan |
| `RECORD_OVERLAP` | `validate_edl` per-channel advance (`engine/nle_export.py` record cursor, mirrored at `tools/nle_check.py:329-341`); pinned by `tests/test_edl_record_advance.py` | record grid does not advance monotonically per channel | **RE-EMIT**; the cursor accumulates over kept durations only (`engine/timeline_build.py:56-71`), so a correct plan cannot produce an overlap |
| `RENDER_DRIFT` | `engine/qa.py:327-341` (1% of plan) and `tools/nle_check.py:374-379` (2.0 frames) | MP4 duration disagrees with the plan | **RE-RENDER** via `engine/render.py:356` (deterministic; render cache keyed on fingerprint/selection_digest/caption_style/ass_digest already exists). Never patch the file |
| `RENDER_UNDECODABLE` | `decode_sample` in `engine/qa.py:296-301` — *"Zero decoded frames on a stream that claims to exist is a broken render"* | container declares a stream that decodes to nothing | **REMUX** stream copy (`selekyt/pipeline.py:452-497`); if that yields nothing → escalate (the same code path already establishes *"a remux of a silent file is a silent file"*) |
| `MEDIA_UNRESOLVABLE` | `_to_file_uri` `RuntimeError` (`engine/nle_export.py:88-127`), `_referenced_media_present` (`tools/nle_check.py:259`), EDL `FROM CLIP` presence (`engine/nle_export.py:736`) | asset `src` / `FROM CLIP` path does not resolve | **RE-RESOLVE** the path the way `tools/nle_check.py:746-768` already does (off the EDL's own `FROM CLIP`), then **RE-EMIT**. If the file is genuinely absent → `UNDECIDABLE` (the audit already treats deliberately-deleted media as NOT RUN, not a defect: `tools/nle_check.py:806-815`) |
| `UNDECIDABLE` | any check whose evidence is missing or ambiguous | the question has no answer on this input | **ESCALATE** to human REVIEW. No repair is permitted. Exit code stays nonzero, exactly as `tools/editorial_audit.py:47-49` and `sweep`'s REVIEW state (`:852-855`) already mandate |

### 4.5 The permitted repair vocabulary (closed set)

**[INFER]** Every repair above is one of five operations. This closure is the whole safety argument, because it is what makes the loop terminate and what makes it auditable:

| Repair | Meaning | Existing precedent |
|---|---|---|
| `SNAP` | move a boundary onto a word/utterance edge | `edit_plan.align_span` `:92`, `_word_aligned` `:28` |
| `TRIM` | remove spans from the plan | `IntervalSet.subtract` `intervals.py:415`, `cut_algebra.subtract_intervals` `:103` (documented as mutually consistent — *"a cut list and an evidence set never disagree about whether a span is in or out"*) |
| `REEMIT` | regenerate an artifact from the unchanged plan | `render_clip`, `timeline_for_candidate`, `nle_export.export`, caption regeneration |
| `REMUX` | stream-copy container repair | `selekyt/pipeline.py:452-497` |
| `ESCALATE` | record and stop | REVIEW state, `tools/editorial_audit.py:838-843` |

**Forbidden, always:** editing emitted interchange text by hand; patching an MP4; re-encoding to hit a duration; re-scoring candidates to make a check pass; inflating or smoothing a confidence (`engine/memory/evidence.py:38-45`); inventing a span the plan does not contain.

### 4.6 VERIFY AGAIN, and the repair record

Each repair produces a new plan or artifact, and the loop re-runs **the same check suite** — not a subset. Every repair is appended to a `repairs.jsonl` beside `edit_plan.json` carrying `(fault, stage, before, after, instrument)`.

**[CODE] precedent for the record shape already exists:** the pipeline persists `asr_provenance` and `fallback_reason` (`selekyt/pipeline.py:1159-1164`) and tells the user they got *"a repaired container, not the file they handed in"* (`:714`). **[INFER]** A repair that is not recorded is, by this project's own standard, a silent rewrite of the editor's decision — which the brief forbids verbatim.

---

## 5. Termination argument

The brief: *"a repair loop that cannot terminate is a bug."* The argument rests on four invariants.

**I1 — Strictly decreasing measure.** Each pass strictly decreases the lexicographic tuple

```
(fault_count, kept_duration, boundary_distance_to_nearest_word_edge, span_count)
```

- `SNAP` decreases `boundary_distance` to 0 and cannot create a fault.
- `TRIM` decreases `kept_duration` and `span_count`; `TRIM` never increases `fault_count` (removing time cannot introduce a mid-word cut).
- `REEMIT` and `REMUX` are idempotent — see I2.

**I2 — Re-derivation is a fixed point, not a step.** `REEMIT` regenerates an artifact from a plan that the repair did not change. A second VERIFY against the same plan yields the same verdict. Therefore: **a `REEMIT` that does not clear its fault is not another pass — it converts the fault to `UNDECIDABLE` and escalates.** This is the guard that prevents an exporter bug from looping forever, and it is the reason no repair may patch emitted text.

**I3 — Bounded passes.** `MAX_PASSES = len(words) + 3`. Justification **[INFER]**: a clip has at most `len(words) + 1` boundaries, each `SNAP` pins at least one to zero and never unpins another (word edges are fixed by the transcript), and the extra three cover one `REEMIT` and one `REMUX` each plus a final confirm. `span_count` bounds `TRIM`. Hitting the cap is itself reported as a fault — the loop's own failure is observable, which is what makes it a bug rather than a hang.

**I4 — No repair may invent a span or invent confidence.** The vocabulary in 4.5 is closed; a repair that would leave the plan unchanged is a no-op and escalates instead of retrying. `UNDECIDABLE` is a terminal state.

**Corollary:** the only non-terminating shapes are (a) an emitter whose output is not a function of its input — which `RENDER_DRIFT`/`INTERCHANGE_DRIFT` would surface immediately as `UNDECIDABLE` under I2, and (b) a check that changes its verdict on identical input — which is non-determinism in a component this project has already committed to eliminate (`docs/product-gap-map.md` on `edit_plan.json`: *"two runs agree byte for byte"*).

---

## 6. Out of scope

1. **Building `engine/memory/store` or `engine/memory/ingest`.** They are documented (`engine/memory/__init__.py:41-47`) and absent (3.4). The G3 rung of the ladder waits on them; the spec degrades explicitly rather than blocking on them.
2. **Any editorial decision.** Per `engine/memory/evidence.py:30-40`, *"What an Evidence is not: it is not a judgement."* The loop snaps, trims, re-emits and escalates. It does not choose what the story needs.
3. **Consolidating the two FCPXML emitters.** Section 2 shows the duplication and the drift in `_to_file_uri`; merging them is real work with its own parity surface (`tests/test_sidecar_parity.py` asserts byte-identity with Rust) and is a separate change from this loop.
4. **The four project-level corpus failures.** Those are VAD/coverage collapse at the transcription stage — `selekyt/pipeline.py:795-802` and `tools/editorial_audit.py:671-707` already diagnose them precisely as *transcription returned no segments*. They are pre-selection; there is no plan to verify yet.
5. **`tools/nle_check.py` in-pipeline.** It is deliberately out-of-band (`selekyt/pipeline.py` comment on the two legs). The spec imports its *checks*, not its *process*.
6. **LLM-driven repair, cross-source/corpus-level repair, re-encoding, and any tolerance relaxation.** The tolerances (1.5 frames interchange, 2.0 render, 1% container) are the contract, not knobs.
7. **The `engine/fcpxml.py` sidecar path's validators.** It has `ensure_exportable` (`:107`) only and no independent reader (2.3). Extending it is a follow-up, scoped to the sidecar, and must speak `CutList` rather than `Timeline`.

---

## Appendix — components the spec reuses, by stage

| Stage | Reuses |
|---|---|
| VERIFY | `tools/editorial_audit.py` check functions (`:91, :173, :260, :384, :406, :422, :445, :630`); `engine/nle_export.py` `validate_edl` `:736`, `validate_fcpxml` `:582`, `_cross_check_fcpxml` `:695`, `independent_parse_*` `:891/:919`; `engine/qa.py` `validate_output` `:227`, `decode_sample`; `tools/nle_check.py` `_interval_findings` `:449`; `engine/memory/intervals.py` `covers` `:363`, `gaps` `:376`, `nearest` `:325`; `engine/memory/evidence.py` `below_confidence` `:309`, `restrict` `:341` |
| DIAGNOSE | the fault enum in 4.3; `Modality` (`engine/memory/evidence.py:63`) to say *which channel* failed; existing three-state verdicts (`tools/editorial_audit.py:838`) |
| REPAIR | `edit_plan.align_span` `:92` / `_word_aligned` `:28` / `join_words` `:61`; `IntervalSet.subtract` `:415`; `cut_algebra.subtract_intervals` `:103`; `render.render_clip` `:356`; `timeline_build.timeline_for_candidate` `:23`; `nle_export.export` `:1004`; `providers.ffmpeg.remux` (via `selekyt/pipeline.py:476`) |
| VERIFY AGAIN | the same VERIFY suite, unchanged; `sweep`/`compare_sweeps` (`tools/editorial_audit.py:846/:892`) for corpus-level confirmation that a repair improved rather than swapped a defect |
