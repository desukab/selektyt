# SeleKyT canonical-edit architecture — Phase 0 reconnaissance

Read from the code at `8f8d5bd` on 2026-10-01, by three parallel read-only passes
over the runtime path, the memory/story layers and the timeline/export stack,
each verified against the source rather than against documentation.

Every claim below is one of:

- **[CODE]** — stated by the code. File:line given.
- **[RUN]** — exercised by `selekyt/pipeline.py::run()`, the real path.
- **[IDLE]** — implemented and tested, but no code on the runtime path calls it.
- **[MISSING]** — not in the tree at all.

---

## 1. Where footage understanding becomes six clips

The funnel is real, and it is the whole product today. `run()` executes, in
order **[RUN]**:

```
transcribe → verify → build_corpus → make_thoughts → rank → select
          → (per clip) captions, content pack, 2× MP4, EDL, FCPXML → report
```

The six-clip boundary is set twice, identically, and by nothing else:
`cli.py:346-347` (`--limit`, default 6) and `pipeline.py:771` (`limit: int = 6`)
**[CODE]**. Range-checked at `pipeline.py:794-795`. `limit` then bounds exactly
one thing — the cardinality of `selected`:

- into ranking: `rank(thoughts, limit=max(limit*3, limit), select_limit=limit)`
  (`pipeline.py:1118`) — the bench is 3× the budget, the budget itself is the
  policy's real constraint;
- into the policy: `_apply_selection_policy(passed, limit=limit, corpus=...)`
  (`pipeline.py:1152-1154`) → `engine.selection.select`;
- into the report: `truncated_by_limit = max(0, len(passed) - limit)`
  (`pipeline.py:1147`).

**Nothing else on the runtime path bounds the output's duration, order, or
shape.** There is no `--duration`, no `--brief`, no `--aspect`, no `--format`,
no `--order`, no `--shorts` **[MISSING]**. Aspect ratio is hardcoded to exactly
two outputs (`pipeline.py:1395-1396`); format is hardcoded to MP4/x264
(`engine/render.py:277`).

**Editorial Selection 2.0 (`8f8d5bd`) is inside this funnel and stays.** It made
the *set* selection-aware: `engine/selection.py` re-scores the surviving pool
after each pick against the clips already chosen, penalising local-scene
clustering, content repetition and weak openings, and crediting a span that
closes a corpus setup→payoff arc. It did not — and was not asked to — change
what the funnel *produces*: the output is still six independent short films.

---

## 2. Where editorial decisions become render outputs

The render loop assigns deliverable indices straight off policy-pick order
**[CODE, `pipeline.py:1370`]**:

```python
for idx, c in enumerate(selected, 1):
```

So `01.mp4` is "the clip the policy scored highest", not "the opening". Inside
that loop, each clip becomes a self-contained mini-edit: `retention_intervals`
cuts the pauses, captions ship, two MP4s render, and an EDL and FCPXML are
written and validated — **per clip** (`pipeline.py:1499-1548`) **[RUN]**.

Critically, `engine/timeline_build.py::timeline_for_candidate` — the function
that produces the timeline the export reads — builds **one track, `"V1"`, one
`SourceMedia`, per candidate** (`timeline_build.py:56-71`) **[CODE]**. Nothing
on the runtime path ever creates a second track **[RUN]**.

So the timeline model exists, and is correct as far as it goes, but the runtime
uses it as a *per-clip interchange serializer*, not as the edit's source of
truth. Six clips → six timelines → six pairs of EDL/FCPXML. There is no single
object describing the whole edit.

---

## 3. What already exists (and is therefore not to be rewritten)

`engine/timeline.py` — a genuine multi-track edit model **[CODE, 440 lines]**:
`SourceMedia`, `Track` (video/audio), `Clip` (source in/out, timeline in/out,
track, reason), `RenderManifest`, and `Timeline` with `source_to_timeline` /
`timeline_to_source` traceability maps, per-track overlap validation, and
`format_smpte`. It round-trips through `parse_smpte` under test. It carries
**source media, source in/out, sequence position, video track, audio track,
reason** — 5 of the ~20 fields the Edit Decision Graph requires.

`engine/edit_plan.py` — `retention_intervals`, word-aligned bounds
(`_BOUND_EPS = 1e-6`), the single source of both the render and the timeline.
Pinned by `test_cue_times_account_for_removed_pauses`.

`engine/cut_algebra.py` — `omitted_intervals` returns *typed removed spans* with
a `kind` and a `disabled` flag. **[IDLE]** on the runtime path — reachable only
via the sidecar (`server.py:334-337`) and the opt-in audio cutlist.

`engine/corpus.py` → `EditorialCorpus` — units, topics, threads, dramatic
beats, `SetupPayoffLink` (5 typed kinds, both halves timecoded), redundancy
groups, speaker blocks, b-roll markers, rhythm. Built before selection
(`pipeline.py:1105`) so the *policy* can read it **[RUN]** — this wiring is live
and load-bearing.

`engine/memory/` — a full evidence algebra: `Evidence` (claim, modality,
source, `params_digest`, confidence, rationale, grounded, artifacts, typed
Allen edges), `EvidenceSet`, `EvidenceGraph` (integrity report, cycle
detection, trace chains), `IntervalSet` (all Allen relations, O(N+M) algebra).
**Write-only on the runtime path**: `populate.build` (`pipeline.py:1276`) writes
`memory.json`; `VideoMemory.load` has **zero non-test callers** **[IDLE]**.

`engine/verify.py` — 1432 lines, VERIFY→DIAGNOSE→REPAIR→VERIFY-AGAIN with
`RepairRecord`. **[IDLE]** — imported by nothing in `selekyt/`. Its 53 tests
now pass (the 12 failures the gap audit named are already resolved).

`engine/story_path.py` — `StoryPath`, a `Sequence[Candidate]` for one thread
under a `target_seconds` budget, source-ordered, with roles, gate verdicts and
dropped moments carrying the guard's own reasons. **[IDLE]**, ready to wire.

`engine/story_view.py` — the view layer, with an explicit refusal table of 8
relationship kinds it will not claim. **[IDLE]**.

`engine/nle_export.py` — self-contained CMX 3600 EDL and FCPXML 1.11 writers
with structural validators, plus `validate_edl` / `validate_fcpxml`. **[RUN]**,
and validation is fatal (`pipeline.py:1544-1548`).

`tools/nle_check.py` — a post-hoc audit over files using third-party readers
(pycmx, vendored OTIO adapter). **Not invoked by `run()`** — `pipeline.py:1490-
1498` documents this explicitly.

---

## 4. The honest gap list

**G1 — There is no sequencing stage.** Nothing orders the selection into a
program. `selected` exits the policy in greedy pick order; the render loop
numbers from that order. `--limit` is a *count*. **[CODE]**

**G2 — There is no canonical edit.** The unit of deliverable is the clip. No
structure describes the whole edit, no file could serve as a preview source, and
nothing concatenates the clips into one program. **[CODE]**

**G3 — Removed ranges are recorded without reasons.** `edit_plan.json` carries
kept `intervals` and a scalar `removed_seconds`; the complement is only
implicit, and no span carries *why* it was cut. `cut_algebra.omitted_intervals`
expresses this properly and is off the runtime path. **[CODE]**

**G4 — No multi-track edit is ever assembled.** `timeline.py` models lanes and
`nle_export.py` supports connected clips above/below the spine, but the runtime
builds one track per clip. No music, no b-roll, no second camera, no title
layer, no cutaway — though the corpus *does* compute b-roll markers, which
`editorial_corpus.json` records and nothing reads. **[CODE/RUN]**

**G5 — Captions are burned, not tracked.** The ASS is composited into the MP4
(`render.py:280-296`), so a style change re-renders the video. `nle_export`
emits video/audio only; `engine/timeline_captions.py` (captions *from* a
`Timeline`) is **[IDLE]**.

**G6 — There is no editorial brief.** No duration target, no format, no
platform, no audience, no tone, no purpose anywhere in the committed code. The
closest thing to an editorial parameter is `--limit`, an integer. This is the
cleanest gap to fill. **[MISSING]**

**G7 — The story layer is write-only on the runtime path.** `story_path` and
`story_view` are complete and idle, fed by a corpus the pipeline already
writes. **[IDLE]**

**G8 — Two FCPXML emitters with two vocabularies.** `engine/fcpxml.py`
(keeps/removes cutlist vocabulary, `--audio-analysis` and the sidecar) and
`engine/nle_export.py` (clips/storyline vocabulary, the per-clip runtime path).
They do not share a model. **[CODE]**

**G9 — The caption layer for the edit exists and has never run.**
`engine/timeline_captions.py` (757 lines) derives SRT + ASS + a traceability
JSON *from a `Timeline`*, honouring sentence structure, reading rhythm, line
width, safe areas, clip boundaries and Unicode scripts — with an honest
`approximate` flag for cues whose timing had to be estimated. Nothing on the
runtime path imports it **[IDLE]**. The captions that actually ship come from
`engine/captions.py::ship_captions`, which captions one `Candidate` in source
time. This is the single largest piece of reusable dead code in the repo.

**G10 — No export round-trips into a model.** Validation is one-way: the
emitted text is checked, and `validate_fcpxml` cross-checks it back against the
`Timeline` that produced it. But no `parse_edl` / `parse_fcpxml` returns a model
anywhere in `engine/` or `tools/` **[MISSING]**. `tools/nle_check.py`
triangulates the export against rendered media and `edit_plan.json` — the
strongest check in the repo — but it never re-imports.

---

## 5. What the reconnaissance changed about the plan

Three findings redirect Phase 1, and all three are "build less new code, wire
more existing code":

1. **The canonical edit model already exists.** `engine/timeline.py` is not a
   stub — it has tracks, lanes, traceability maps and validation. Phase 1 is
   therefore *not* "write a timeline model"; it is **extend `Timeline` to carry
   the decision fields the EDG requires and make the pipeline build one**.

2. **The sequencing gap is a stage, not an algorithm.** G1 is solved by a new
   stage between `select` (`pipeline.py:1182`) and `render` (`pipeline.py:1368`)
   that takes `selected` + `corpus_report` and produces one ordered, multi-track
   `Timeline` — then having the render loop, the interchange export, the
   captions and the content packs derive from that object instead of each
   re-deriving from `Candidate` + `retention_intervals` independently.

3. **The evidence layer is the honest-confidence foundation.** `Evidence`'s
   `confidence: float = 1.0` has no explicit-unknown, which is the one design
   flaw to fix before building decision confidence on it — and
   `story_view._confidence_of` returning `(float | None, basis)` is the
   in-house precedent for the fix.

---

## 6. Prerequisites that are already met

`docs/post-beta-gap-audit.md` §8 set two gates before any wiring work:
the 12 `test_verify.py` failures made honest, and the 3
`test_pipeline_hardening.py` memory tests passing. Both are already green as of
`8f8d5bd` — **53 passed** in `test_verify.py` and **29 passed** in
`test_pipeline_hardening.py`, measured 2026-10-01. The gate is open.

## 7. Constraints on the build

- **Disk: 262 MB free of 29 GB, 100%.** Every real-media operation must check
  `df` first. Three corpus sources survive locally (pentagon 158 MB, cloke 85
  MB, telugu 38 MB). A full suite run needs ~1–2 GB of headroom and is not
  affordable per-change; targeted single-file runs with `-p no:cacheprovider`
  are the discipline.
- **No professional NLE is available on this host**, so FCPXML/EDL import can
  only be structurally validated. That limit must be stated, not papered over.
- **The 10 untracked review artifacts stay untracked** pending the editor's
  decision; nothing in this build depends on them.
