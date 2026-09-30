# Post-beta gap audit — SeleKyT against a modern local AI video editor

**Date:** 2026-09-30 · **Baseline:** `e7e9984` (CLI beta shipped) ·
**Suite at audit time:** 2550 passed, 18 failed (15 `test_verify.py`, 3
`test_pipeline_hardening.py` — both pre-existing WIP), 16 skipped.

This is the Phase 1 deliverable. It audits SeleKyT against the capabilities a
serious local AI video editor needs, and — because the directive forbids
duplicating abstractions — it first establishes what already exists, and
classifies every gap against that.

The HotClip comparison is architectural inspiration only. Its licence is
AGPL-3.0-only and SeleKyT is proprietary; **no HotClip code is imported, copied
or re-implemented line-for-line**, and §6 names the ideas deliberately rejected
regardless of merit.

---

## 0. Method and the status of every claim

Every status below is one of `EXISTS/VERIFIED`, `EXISTS/WEAK`,
`BUILT/UNWIRED`, `PARTIAL`, `MISSING`, `REGRESSION RISK`, with the file:line or
test that grounds it. Where I read the code rather than ran it, the entry says
so. Where a module is untracked WIP (never committed), that is stated, because
an uncommitted module is not a shipped capability.

Two facts shape the whole audit and were established by grep, not by guesswork:

1. `selekyt/pipeline.py` imports none of `engine.verify`, `engine.eval`,
   `engine.language_ir`, `engine.story_graph` (import block `pipeline.py:16-49`).
2. `engine.memory` is imported only as a *function-local* import inside the
   corpus stage (`pipeline.py:1180`), which means it is the one WIP module that
   IS on the run path — and the function-local form is why the CLI beta commit
   could ship without it.

So the dominant gap is **integration of built, tested, unwired modules**, not
the absence of capability.

---

## 1. The map

### 1.1 EXISTS / VERIFIED — shipped and test-pinned

| Capability | Ground |
|---|---|
| CLI: `analyze` verb, `--json` on stdout / panel on stderr, `--no-color`, `--quiet`, `--verbose`, narrow-width and resize, non-TTY degradation | `selekyt/cli.py`, `selekyt/ui.py`; 173 focused tests pass |
| Progress protocol: per-stage start/finish with detail dicts, chunk and render-part counters, fail event naming the stage before raising | `pipeline.py` `_notify`/`_stage`/`_chunk_progress`; `tests/test_pipeline_e2e.py` pins stage order |
| Deterministic editorial scoring | `engine/editorial.py:470` `score_candidate`, `:519` `rank`; 0.67 ms/decision measured |
| Context/truth guard | `engine/context_guard.py`, single `validate()` pass at `pipeline.py` (select stage) |
| Coverage gate: VAD truth vs ASR speech, with backend chain silero→ffmpeg→energy | `engine/coverage.py`; `pipeline.py` verify stage |
| Language fitness refusal on untranscribable languages | `engine/language_fitness.py`; `pipeline.py:989` stage raises |
| Corpus: topics, threads, redundancy groups, rhythm, written as the canonical graph | `engine/corpus.py` via `pipeline.py:1140`; `editorial_corpus.json` |
| 16:9 and 9:16 render with per-part progress | `engine/render.py:357`; `providers/*` chunk counters |
| Word-timed captions, script-coverage checked before encode | `engine/captions.py`; `render.py:102` `_check_caption_coverage` |
| Editable export: EDL on every run, FCPXML opt-in, both independently re-parsed and validated | `engine/nle_export.py`, `pipeline.py:1350-1355` |
| Reproducible report with provenance and run start time | `engine/report.py`; fixed in the beta commit |
| Transcript checkpointing, render cache keyed on selection digest, `--clean-project` reclaim | `pipeline.py` `_selection_digest:287`, `reclaim_intermediates:1620` |
| Corpus reality recorded: Telugu WER 151.08%/91.54%, Polish 11.01% | `corpus/wer/runs/`, quoted in `docs/retrieval-design.md` §0 |

### 1.2 BUILT / UNWIRED — implemented and tested, not on the run path

This is the most important section. These are not gaps to fill; they are
integrations to make.

| Module | Lines | What it is | Tests |
|---|---|---|---|
| `engine/verify.py` | 1432 | The full VERIFY → DIAGNOSE → REPAIR → VERIFY-AGAIN loop: 13 checks (mid-word cut, mid-sentence cut, shaved tail, unsupported span, low-confidence-only, duplicate sentence, caption text/span drift, interchange drift, record overlap, render drift, render decodable, media resolvable), a `Diagnosis` per finding, `apply_repair` with SNAP/TRIM/REEMIT/REMUX, `run_loop` with `MAX_PASSES` and escalation to `UNDECIDABLE`, auditable `RepairRecord`s | `tests/test_verify.py` 34 pass / 12 fail (WIP) |
| `engine/story_graph.py` | 840 | 8 node kinds (setup/context/development/conflict/turn/peak/payoff/resolution), evidence-backed nodes via `NodeBasis`, `SetupPayoffAssertion`, built from the committed corpus | `tests/` (untracked) |
| `engine/eval.py` | 1362 | Evaluation, not inference: temporal IoU/mIoU, known-item retrieval, word-boundary conformance, span degeneracy, round-trip identity, severity | `tests/test_eval.py` |
| `engine/language_ir.py` | 979 | Script-level language IR: Telugu block detection, romanize/deromanize round-trip, WER normalisation, `AsrSignals` | `tests/test_language_ir.py` |
| `engine/memory/graph.py` | 854 | Typed Allen-algebra edges over `EvidenceSet`, witness-checking, integrity report | `tests/test_memory_graph.py` |

Note `docs/evidence-graph-design.md` says "`engine/memory/graph.py` does not
exist in the tree" — **that document is stale**; the module is 854 lines and
tested. The design doc should be marked superseded or deleted when the graph is
wired.

### 1.3 EXISTS / WEAK

- **Confidence is per-candidate, not per-decision, and its uncertainty is not
  modelled.** `editorial.py:499` sets `confidence = _asr_confidence(words)` —
  one number, derived from ASR word confidence alone. Nothing records *what
  would change the decision* (boundary ambiguity, conflicting signals, missing
  evidence). The `verify.py` check `check_low_confidence_only` and the
  `confidence_floor` in `VerifyContext` are the honest-uncertainty machinery
  already built, and they are unwired. **This is Phase 4's exact weakness and
  its fix is largely an integration.**
- **Boundary selection is a single-signal snap.** `engine/edit_plan.py`
  word-aligns boundaries (`_word_aligned`, `_BOUND_EPS = 1e-6`), which is
  correct and cheap, but there is no *provenance* on a boundary: no record of
  which signals agreed (ASR words, VAD, pause, shot) or disagreed. `verify.py`
  findings carry `instrument`, which is the field this would extend.
- **Render QA is "ffmpeg exited 0".** `render.py:404` runs the concat and
  returns the path. The only post-encode checks are caption script coverage
  (`render.py:102`) and, in the pipeline, an independent EDL/FCPXML re-parse
  (`pipeline.py:1354-1355`). No duration, resolution, aspect, black-frame,
  audio-presence or source-correspondence check exists on the rendered file.
  `verify.py`'s `check_render_drift` / `check_render_decodable` /
  `check_media_resolvable` are exactly this, unwired.
- **Resumability is partial and unversioned.** Transcript chunks and the render
  cache resume, but no stage carries `(version, input fingerprint, config,
  model, output integrity)`. A stale artefact whose *inputs* changed is not
  detected — the render cache keys on source+settings+selection, which covers
  the selection but not, e.g., a caption-style change.

### 1.4 MISSING

- **Evidence receipts as a first-class artifact.** No file says "this candidate
  was selected because *these* signals said so" in a form a reviewer can read
  without trusting a score. The ingredients exist (`memory/evidence.py`,
  `story_graph.py` evidence-backed nodes, `verify.py` findings with
  `instrument`) but no receipt is emitted per candidate. **Phase 2.**
- **Editor feedback representation.** No `ACCEPT`/`REJECT`/`MOVE_BOUNDARY`/
  `REORDER`/`ADD`/`REMOVE`/`MANUAL_OVERRIDE` event type exists anywhere in the
  tree (grep across `engine/`, `selekyt/`, `tests/` returns nothing). This is
  genuinely absent and is Phase 8.
- **Review experience.** No CLI surface exposes (candidate, source timestamps,
  story role, evidence, reason, confidence, boundary rationale, Edit IR,
  verification result) as one record. `selekyt analyze` produces artifacts; the
  editor must open five files to review one clip. **Phase 9.**
- **Short-form editorial hooks.** 9:16 and reframing exist, but there is no
  "strong opening" or "coherent ending" editorial property, and no
  subject-aware reframing (a face envelope / One Euro-style stabilised crop).
  `docs/product-gap-map.md` records reframing as present; the *stabilised*
  variant is not. **Phase 10, partially.**
- **Verification of *renders*, as opposed to plans.** `verify.py` verifies the
  plan against the transcript and context; the render step itself is still
  trusted. Phase 7's compile→render→verify→repair→rerender arc needs the
  render-side checks wired and a rerender path.

### 1.5 REGRESSION RISK

- **The 3 failing `test_pipeline_hardening.py` tests are the memory WIP's
  contract** (`pipeline.py` lines 384-480 in the test file: memory written from
  artifacts already held, memory failure reported not fatal, unmeasured source
  skips memory honestly). They fail because the memory WIP is mid-flight, not
  because the pipeline regressed. Any memory change must keep these three
  honest or the beta's "no fabricated evidence" promise is what breaks.
- **`--json` stdout purity** is load-bearing for the beta contract. Any new
  stage that prints to stdout breaks it; everything new must emit through the
  progress protocol or the report.
- **The 0.67 ms/decision scoring and the 3.1 s population** figures are the
  performance baseline. Any uncertainty model that runs inference per candidate
  will break this by orders of magnitude. Phase 4 must stay deterministic.
- **Disk.** `/` was at 536 MB free after the baseline run. Every real-media
  operation must check `df -h` first; the `--basetemp` discipline used for the
  baseline run is what kept it from filling.

---

## 2. Architecture comparison (ideas only, no code)

The useful architectural ideas from comparable systems, expressed as SeleKyT
design decisions rather than feature lists:

1. **The LLM never guesses a timestamp.** Every time boundary must come from an
   instrument that measured something (ASR words, VAD, energy, shot cut), and
   an LLM may only *select among* instrument-proposed boundaries. SeleKyT's
   `edit_plan` word-alignment already obeys this; the discipline must hold when
   boundary selection gets multi-signal.
2. **Corroboration, and fail-open on disagreement.** Two independent signals
   that disagree should *report* the disagreement (that is the uncertainty),
   not silently pick one. SeleKyT's `verify.py` `UNDECIDABLE` + escalation is
   this principle; `coverage.py`'s backend chain preferring silero over energy
   is the same idea at the instrument level.
3. **Bounded self-repair, one round.** A repair that does not fix the fault on
   re-verification is escalated, never patched twice. `run_loop`'s `MAX_PASSES`
   and "a recorded artifact repair whose fault recurs is converted to
   UNDECIDABLE" (`verify.py:1312` docstring) is exactly this.
4. **Verify the decode, not just the plan.** A render must be probed
   (duration, streams, resolution, decodability) because `ffmpeg` exit 0 is not
   a claim about the output's contents. SeleKyT has `probe_duration`/
   `probe_fps` already; wiring them to the rendered file is Phase 7.
5. **Atomic output.** An artifact is either complete or absent, never
   half-written. SeleKyT has `providers/atomic.write_json_atomic`; renders
   should follow the same write-to-temp-then-rename discipline.
6. **Range-scoped evidence cache.** Analysis results keyed by source range, so
   a re-cut reuses the decode and the listen, not just the transcript. This is
   what `memory/` plus deterministic fingerprints is for.

## 3. Transferable ideas, mapped to a SeleKyT architectural home

| Idea | SeleKyT home | New module? |
|---|---|---|
| Instrument-only boundaries | `engine/edit_plan.py` extension | No — extend |
| Boundary provenance (signals, agreement, adjustment reason) | new field on the plan/candidate; the `verify.py` `Finding.instrument` pattern | No — extend |
| Bounded repair loop | `engine/verify.py` `run_loop` | **Exists, unwired** |
| Render decode verification | `engine/verify.py` render checks + `providers/ffmpeg` probes | No — extend |
| Atomic render | `engine/render.py` write-temp-rename | No — extend |
| Evidence-backed story nodes | `engine/story_graph.py` | **Exists, unwired** |
| Evaluation as measurement | `engine/eval.py` (IoU/mIoU/retrieval/conformance) | **Exists, unwired** |
| Language IR / script round-trip | `engine/language_ir.py` | **Exists, unwired** |
| Range-scoped analysis cache | `engine/memory` + deterministic fingerprints | Partially exists |
| Review record | new, small — one record type over existing evidence | **New, small** |
| Editor feedback events | new enum + append-only log | **New, small** |
| Subject-aware stabilised reframing | `engine/render.py` crop path | Extend (no face tracker exists) |

**No entry in this table requires a new subsystem.** The two genuinely new
things are small: a review record and a feedback event log. Everything else is
wiring or extending.

## 4. Priority

Ordered by (product value) × (how much is already built) / (risk):

1. **Wire `verify.py` into the render path** — Phase 7. Highest value: it is
   the difference between "ffmpeg said it worked" and "the cut is verified".
   Module is built; the work is the call site, a `VerifyContext` assembler from
   what the pipeline already holds, and making its 12 failing tests honest.
2. **Evidence receipts per candidate** — Phase 2. The North Star ("explain why
   it selected it") is unsatisfied without this, and every ingredient exists.
3. **Honest uncertainty** — Phase 4. Largely wiring `confidence_floor`,
   `check_low_confidence_only`, and `UNDECIDABLE` into the editorial record.
4. **Review record + CLI** — Phase 9. Small, high visibility, built on 1-3.
5. **Stage versioning for resume** — Phase 6. Unsexy; prevents silent staleness.
6. **Language IR wiring** — Phase 11. `language_ir.py` exists; the gap is
   surfacing script-mixed/low-quality transcription as downgraded confidence.
7. **Editor feedback events** — Phase 8. Deliberately last among the builds:
   the directive says do not train from it, so its value is calibration later,
   and it depends on the review record existing first.

## 5. Dependencies

- Phase 7 (verify wiring) needs a `VerifyContext` built from the pipeline's
  transcript, utterances, coverage speech regions and source duration — all
  already computed. It does **not** need memory.
- Phase 2 (receipts) needs `memory/evidence.py` and `story_graph.py` on the run
  path, which needs the memory WIP's 3 failing tests honest first.
- Phase 4 (uncertainty) needs Phase 7's `UNDECIDABLE` and Phase 2's evidence to
  have anything honest to say; otherwise it is a fabricated number.
- Phase 9 (review) needs 2 and 4 to have content worth reviewing.

## 6. Explicitly rejected

- **Any virality score as a selection signal.** The directive forbids it, and
  SeleKyT's `virality_score` field exists but the product direction is explicit
  that editorial correctness outranks it. If it is ever surfaced it must be
  labelled heuristic and kept out of the ranking path.
- **A large neural model to manufacture confidence.** Phase 4 must be
  deterministic cheap signals; the 0.67 ms/decision baseline is the constraint.
- **Importing HotClip code.** AGPL-3.0-only into a proprietary project is
  incompatible; the licence discipline is in the directive's Phase 8.
- **A frontend rewrite.** The review experience is CLI-first; a web shell
  already exists and is out of scope for this track.
- **Rewriting the corpus layer.** `docs/story-graph-design.md` establishes it as
  schema-locked at 1.1 and tested; the story graph *extends* it.

## 7. What NOT to rewrite

`engine/editorial.py` scoring, `engine/corpus.py`, `engine/edit_plan.py`
word alignment, `engine/context_guard.py`, `engine/coverage.py`, the
`providers/` transcription checkpointing, and the CLI/ui split shipped in the
beta. Each is measured, tested and load-bearing for the performance and honesty
baselines. The audit above treats all of them as extension points.

## 8. Tests required by this track

- The 12 `test_verify.py` failures made honest (test-fixture or engine defect,
  per case) before any wiring — an unwired module with a red suite must not be
  put on the run path.
- The 3 `test_pipeline_hardening.py` memory tests passing, since Phase 2
  depends on memory being trustworthy.
- Per-phase: receipt round-trip (evidence → receipt → readable reason),
  uncertainty propagation (a boundary ambiguity reaches the record as
  `UNCERTAIN`, not as a changed score), resume correctness (a changed input
  invalidates, an unchanged one reuses), render verification on synthetic media
  with an injected defect (truncated file, wrong duration, missing audio), and
  one real-media end-to-end run before each commit.

## 9. Expected benefit and risk

| Work | Benefit | Risk |
|---|---|---|
| Verify wiring | Cuts that are checked, not assumed; the shipped-corpus defect class (mid-sentence cuts, `docs/product-gap-map.md` §critical path) becomes impossible to ship | A false-positive-heavy check suite makes every run need a human; must be held to the "manufactures the defect class" standard `verify.py` already documents |
| Receipts | "Why did SeleKyT select this?" becomes answerable | Receipt bloat; must stay a record, not a document |
| Uncertainty | A reviewer can trust or escalate | Fabricated confidence if rushed; must stay deterministic |
| Language IR | Telugu-level failures surface as downgrades, not silent bad cuts | Script handling is a large surface; keep it to what the corpus has measured |

---

## 10. Definition of Done for this track

Not "tests pass". The track is done when: the verify loop runs on every render
and its failures are reported per clip; every shipped candidate carries a
readable receipt; uncertainty is a recorded state rather than a rescaled score;
a dead run resumes without recomputing; and a real source has been taken
end-to-end with the artifacts inspected, not just the exit code.
