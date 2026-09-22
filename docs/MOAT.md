# The SelekyT feedback / data flywheel — designed, not claimed

A design document for the editorial learning loop. Written against the real
repository state on `main` at `ac8a658` (`fix(youtube): cycle player clients
past the per-video sign-in wall`), with the real end-to-end artifacts from the
one genuine source run (`out/url-e2e/project/`, NASA 360 Episode 5, 24:29 of
two-host unscripted speech, US-government public domain) read line by line.

## The one-line verdict

**There is no moat today, and there is no flywheel today.** What exists is a
deterministic, entirely local editorial pipeline whose every run emits a rich,
well-provenanced *feature* record and a completely empty *label* record. The
pipeline computes thousands of scored editorial candidates per source and
discards all but a handful; nothing anywhere records what a human did with any
of them. There are zero users, zero channels, zero published outputs and zero
performance observations. Phase 0 of a flywheel is not "a small flywheel" — it
is a flywheel with no second half.

The value of this document is that it says exactly which half is missing, where
in the architecture it has to be bolted on, and which of the proposed moats are
real compounding assets versus engineering work anyone can copy.

## How to read this document

Every substantive claim carries one of three markers:

* **VERIFIED** — exists in the repository or in a real artifact on this host
  today, with a file path and usually a line number.
* **DESIGNED** — proposed but unbuilt. No code exists for it. This is the
  honest category for every phase of the flywheel past the present one.
* **RESEARCHED** — external knowledge or cited guidance, not repo state.

Two honesty notes about the ground truth itself. The brief for this document
says 822 tests pass; `docs/AI_EXECUTION_STATE.md:17` records **819 passed, 10
skipped** at its own checkpoint and explicitly instructs a reader to re-run
before trusting it. Either number may be right — 50 test files exist under
`tests/` — the point is that the project's own discipline is to treat such
numbers as drift-prone, and this document does the same. Second, the run
artifacts cited here are from *one* real source; `docs/AI_EXECUTION_STATE.md:92-93`
is the project's own statement of that limit: three clips from one source
passing the audit "is evidence the machinery is sound, not that the choices
would survive comparison with a human editor across sources and languages."

---

## 1. What the pipeline already records

Everything below is **VERIFIED**. It is the substrate a learning loop would
consume, and it is genuinely substantial — the surprise of this inventory is
how much of the eventual training data already exists unlabelled.

### 1.1 The artifact tree of one real run

`out/url-e2e/project/` (a real run, 2026-09-19):

| Artifact | What it carries | Written by |
| --- | --- | --- |
| `pipeline_state.json` (+ `.bak`, quarantine) | download cache, transcript fingerprint, per-clip render cache, `media_info` (the yt-dlp info dict **minus its credential keys** — see 6.1) | `selekyt/pipeline.py:36`, `:106-124`, `:363-385`, `:687-732`, `:888` |
| `transcript.json` | `language`, `asr` provenance block, 425 segments with per-word `text`/`start`/`end`/`confidence` | `selekyt/pipeline.py:438-453` (retry path `:490`) |
| `selections.json` | the **selected** candidates only: span, text, `story_type`, `hook`, `payoff`, five scores, `reason`, `visual_potential`, full word list, `coherence_score`, `completeness`, `total_score` | `selekyt/pipeline.py:578-580` |
| `editorial_report.json` | per-selected-clip scores plus the decomposed `reason` string | `selekyt/pipeline.py:325-345` |
| `edit_plan.json` | per clip: `source_start/end`, kept `intervals`, `edited_duration`, `removed_seconds` — deterministic, no paths | `selekyt/pipeline.py:455-470` |
| `pacing_report.json` | aggregate pacing: `avg_duration`, `duration_distribution`, `silence_pct`, `filler_pct`, `avg_words_per_second`, `avg_hook_latency`, `payoff_presence_rate`, `duplicate_overlap_rate` | `selekyt/pipeline.py:347-376`, `engine/edit_plan.py:63-130` |
| `report.json` | `candidate_count`, `selected_count`, `rejected_by_context_guard`, `truncated_by_limit`, `rejection_reasons`, `policy` string, audio provenance or explicit `{"enabled": false}` | `selekyt/pipeline.py:476-501` |
| `qa.json` | per-file `silent_tail`, `black_frame_pct`, `size`, plus `diversity` (0.528 in the real run) and `source_coverage` (`coverage_pct` 4.1) | `engine/qa.py` via `selekyt/pipeline.py:475` |
| `captions/NN.{ass,srt,json}` | cue-level `start`/`end`/`text`/`emphasis` per clip | `selekyt/pipeline.py:391-396` |
| `content_pack.json`, `content_pack/NN.json` | `titles[]`, `instagram_caption`, `hashtags[]`, `story_type`, `source_based`, `policy` | `selekyt/pipeline.py:397-403`, `engine/content_pack.py` |
| `timeline/NN.{edl,fcpxml}` | the AI's proposed timeline, source in/out in SMPTE | `selekyt/pipeline.py:437-452`, `engine/nle_export.py`, `engine/timeline_build.py:23` |
| `audio/audio_truth.json`, `vad_regions.json`, `cutlist.json` | VAD backend, speech/silence ratios, per-region speech/silence spans, per-segment `keep`/`remove_filler` with `reason` (present in `out/web/project/audio/`) | `engine/pipeline_audio.py`, `engine/audio_fusion.py` |

### 1.2 Provenance is a first-class discipline, not an afterthought

This matters more than it looks: a data flywheel is only *allowed* to exist if
every observation can be traced back to the exact model version and source that
produced it. That machinery already exists.

* **Transcription provenance.** `transcript.json`'s `asr` block records
  `engine`, `requested_model`, `model`, `downgrade_reason`, `word_timestamps`,
  `fallback_reason`, `chunks`, `chunking`, `num_threads`. The code comment at
  `selekyt/pipeline.py:243-251` states the principle: "Provenance must name the
  model that actually ran, not the one that was asked for."
* **Source identity.** `selekyt/pipeline.py:117-121` fingerprints on
  `source|resolved_path|size|mtime_ns|model|language`, so a cached transcript
  is never silently reused for a different model or language.
* **Selection identity.** `selekyt/pipeline.py:142-151` digests the chosen
  clips so a re-run that picks new clips cannot ship the previous run's renders.
* **Timeline traceability.** `engine/timeline.py:161-177` — every `Clip` can
  answer "which source timecode does my in-point correspond to?", and
  `Timeline.source_to_timeline` / `timeline_to_source` (`:273`, `:287`) map both
  directions. `Clip.reason` (`engine/timeline.py:117-118`) carries free-form
  editorial provenance onto the timeline itself. `Timeline.to_dict()`
  (`:349-360`) serialises all of it including a free-form `metadata` field.
* **Determinism contract.** `edit_plan.json` is deliberately path-free
  (`selekyt/pipeline.py:465-469`) so two runs over the same input agree byte for
  byte, while path-bearing metadata lives in the non-deterministic `report.json`.

### 1.3 The feature vector already exists, fully decomposed

This is the single most important inventory finding. Look at the `reason`
string from the real run's top candidate (`editorial_report.json`, clip 1):

```
Structural editorial score: opening=0.75; progression=0.95; payoff=1.00;
density=1.00; fillers=0; standalone=1.00; repetition=0.18; coherence=1.00;
completeness=1.00; audio_pace=0.88; silence=0.147; dead_air=0.00.
```

That is a **pre-explained feature vector**, emitted as a side effect of
`engine/editorial.py:323-334`. Eleven numeric features plus a `story_type` in
{conflict, revelation, question, fact, story, insight} (`engine/editorial.py:318-322`),
plus five composite scores (`editorial_score`, `virality_score`,
`context_score`, `visual_potential`, `confidence`) combined into `total_score`
by fixed hand-set weights in `models/core.py` (`0.30/0.22/0.20/0.16/0.12`).

Nothing further needs to be computed to start learning. A supervised loop needs
`(features, label)` pairs; the features are already on disk, per candidate, per
word, per run. Only the label is absent.

### 1.4 The funnel, and how much of it is thrown away

From the real run's `report.json`:

```
candidate_count            4000     ← generated and fully scored
(selected by rank)            9     ← survived diversity/dedup filters
rejected_by_context_guard     0     ← gate rejections (identity discarded)
selected_count                3     ← shipped
truncated_by_limit            6     ← passed every gate, dropped by --limit
```

Three facts follow, and all three are VERIFIED from the code:

1. **The candidate pool is capped, not exhaustive.** `make_thoughts` returns
   `thoughts[:4000]` (`engine/editorial.py:255`). A 24:29 source generated
   *exactly* 4000 candidates — i.e. it hit the cap. The true pool is larger
   than what is recorded, so even the "everything we scored" number is a floor.
2. **All 4000 are scored and then nearly all dropped.** `rank()`
   (`engine/editorial.py:383-411`) scores every candidate via `score_candidate`
   (`:386`), then applies overlap, text-similarity (`>= .78`), hook-similarity
   (`>= .65`), topic-token overlap (`> 12`) and per-kind diversity caps
   (`:397-406`) — and returns only what survives. In this run, 9 of 4000.
3. **The identity of every unshipped candidate is recorded, and was not
   always.** `selected = passed[:limit]` (`selekyt/pipeline.py`) keeps 3; the
   other 6 passed candidates used to exist only as a count
   (`truncated_by_limit`), and the gate-rejected ones only as reason counts
   in `rejection_reasons`. `selections.json` holds 3 entries and `report.json`
   now carries the 6 in a `bench` key — the same fields minus the word timings
   a re-render would need — so the count is a claim the artifact backs. That
   closes the half of the gap that was fixable: a candidate can now be
   identified without re-running the source, but it still cannot be *pulled
   forward* without one, because `rank()` has no include/exclude hook (see
   `docs/PRODUCT_WORKFLOW_AUDIT.md` J2).

So the honest accounting of existing signal: **one real run produces ~4000
fully-featured editorial candidates and persists 3 of them, with no label
attached to any.**

### 1.5 The audit loop that already exists and needs no users

`tools/editorial_audit.py` is a repeatable benchmark that "reads only the
artifacts a normal run already writes" and scores falsifiable properties of
shipped clips: mid-word cut, mid-sentence cut, short final word, orphaned cue,
cue overrun, span mismatch, silence share, duplication, selection size. It
exits non-zero on hard failure so it composes with CI.

This is a **Phase 0.5 learning loop with zero users**, and it is the cheapest
real loop in the repo. It measures the *product's* output, not a human's
opinion of it, so it can run on every source today.

---

## 2. What is missing — the events that are not captured

Five events, ordered by the value of the signal they would carry. For each:
what it is, where in the architecture it must be captured, and how hard.

### 2.1 Human keep / reject decisions on candidates — **DESIGNED**

Nothing records a human's opinion of any candidate. There is no review surface
at all: the web shell runs the pipeline and renders outputs, and the closest
thing to a human-in-the-loop control anywhere in the UI is `renderCutlist`
(`web/app.mjs:95-145`), which *displays* keep/cut intervals for the standalone
VAD analysis tool — it has no approve/reject affordance, and it is not wired to
the story-selection stage.

**Where it would have to be captured.** A new review step between `rank()`
(`selekyt/pipeline.py:288`) and `selected = passed[:limit]` (`:316`). The
bench already exists conceptually — `truncated_by_limit` is documented in the
code as "an editor reading this report can challenge the rejections and pull
from this bench" (`selekyt/pipeline.py:483-486`, the `report.json` comment on `truncated_by_limit`).
To make it real, `rank()`'s returned candidates (all 9, not the shipped 3)
must be persisted before the limit is applied, each with a `decision` field
defaulting to `unreviewed`. That is a ~15-line change plus a UI.

**How hard.** Easy in code, hard in product. The code change is trivial; the
hard part is that a review step costs the user time on every run, and a user
who has to approve three clips will simply not do it unless the review is
faster than re-cutting by hand. This is the loop's central design problem, not
its engineering.

### 2.2 Human edits after the AI output — the AI→published diff — **DESIGNED**

This is the highest-value signal that does not exist today, and it is the
flywheel's reason for being. A human editor who takes the AI's timeline, moves
a boundary, deletes a clip, reorders two beats, or shortens a pause is
*correcting the model*, and the diff is a labelled correction.

**The blunt finding: half of this comparison cannot currently be constructed by
the software at all.**

* The AI side exists and is precise. `engine/timeline_build.py:23` builds the
  proposed timeline from the same `retention_intervals` the render concatenated
  — the docstring states the invariant: "an exported EDL or FCPXML describes
  the video that was actually written."
* The published side has **no parser.** There is no FCPXML or EDL *import*
  anywhere in the codebase. `grep` for `from_fcpxml`, `from_edl`, `read_edl`,
  `load_timeline`, `def parse` across the Python sources returns only
  `selekyt/cookies.py:17` (a cookie-header parser) and `engine/timecode.py:70`
  (`parse_smpte`, a SMPTE *string* parser). `engine/nle_export.py` is export
  only, and its own header states the limit plainly: "NLE import (Premiere
  Pro, DaVinci Resolve, Final Cut Pro, Avid Media Composer) is **not verified
  on this host**."
* And per `docs/product-gap-map.md`, **no NLE has ever opened an exported
  timeline** on any host — the EDL/FCPXML are "structurally verified" with no
  import compatibility claim. So not only is there no diff capture; the
  round trip the diff depends on has never been exercised end to end even once.

**Where it would have to be captured.** An FCPXML/EDL *re-import* path into
`engine/timeline.py` (the model is already built for it — `Timeline.validate()`
at `:302` and the frozen `Clip` invariants at `:107-125` would enforce that a
returned timeline is well-formed), then a structural diff against the
`Timeline.to_dict()` of the proposal. `Timeline.metadata` (`:349-360`, a
free-form dict already serialised) is the natural seat for the recorded diff.

**How hard.** Hard. It is the single largest unbuilt item here: a
correct-by-construction FCPXML/EDL parser, plus getting a real NLE to open the
exports in the first place (open item G2 in `docs/product-gap-map.md`, and task
2 in `docs/AI_EXECUTION_STATE.md`'s own ranked list). Until that round trip
works once, this signal is unreachable regardless of any learning design.

### 2.3 Post-publish performance per platform — **DESIGNED**

No platform integration, no analytics ingest, no performance field anywhere in
any artifact. The product's stated intent is explicit and unbuilt: `README.md`
roadmap lists "Channel-specific retention learning from exported analytics",
and `docs/EDITORIAL.md`'s "Future learning loop" section specifies the design
trigger: priors "should override generic heuristics only when enough real
observations exist."

Notably, `docs/EDITORIAL.md` and the README's retention philosophy already
encode *publicly documented* platform guidance as heuristics — YouTube's
retention report (dips, spikes, top moments, gradual declines; improve the
opening, move compelling material earlier) and Meta's native-9:16/safe-zone
guidance. So today the system is **RESEARCHED-knowledge-conditioned, not
data-conditioned.** That distinction matters for honesty: the current
"retention-aware" scoring is a hand-coded reading of published advice, not
learning from any channel's real numbers.

**Where it would have to be captured.** A new ingest stage entirely outside the
pipeline: platform APIs → per-clip retention/AVD → joined back to
`selections.json` / `edit_plan.json` by source fingerprint and selection digest
(both of which already exist, `selekyt/pipeline.py:117-151`, so the join key is
not the problem). The hard part is identity: a published clip's platform ID has
to be recorded at publish time, and nothing today marks a clip as published at
all — `complete: true` in `pipeline_state.json` means the *pipeline* finished,
never that anything was shipped to an audience.

**How hard.** Medium for the plumbing, very hard for the join. Platform APIs
rate-limit, scope and change; and the mapping from "the clip that rendered" to
"the asset that got 40k views" requires a publish event the product does not
have.

### 2.4 Per-channel / per-tenant editorial identity — **DESIGNED**

There is no concept of a channel. `pipeline.run()` (`selekyt/pipeline.py:154`)
takes `source`, `out_root`, model and tuning knobs — nothing about *who* is
editing. Every run is keyed only on the source fingerprint, so two different
creators cutting the same footage share one cache. There is no database, no
user table, no tenant ID anywhere in the Python or Rust sources (verified by
search for `sqlite|postgres|database|user_id|tenant|channel_id|auth`: the only
auth-adjacent code is yt-dlp cookie handling in `providers/youtube.py`, and
`selekyt/server.py:97` states outright "this is not an authorization
boundary").

**Where it would have to be captured.** A channel/workspace identity threaded
from the entry points (`selekyt/cli.py`, `selekyt/server.py`) through
`pipeline.run()` into a per-channel store keyed on it. `out_root` already
partitions runs on disk, which is a usable crude namespace for a first version.

**How hard.** Medium. Identity is plumbing; the difficulty is that adding a
tenant concept to a tool that is today resolutely local-first and single-user
changes its deployment shape (see §6).

### 2.5 Editor identity and skill — **DESIGNED**

Nothing. No accounts, no sessions, no per-editor attribution. In a newsroom this
matters disproportionately: the senior editor's keep/reject decisions are worth
far more than an intern's, and a loop that pools them undiluted is a worse
product. Any capture design that treats "human decision" as one homogeneous
label will learn the average of a bimodal population.

**Where it would have to be captured.** Same threading as §2.4, plus a
decision-provenance field on every captured event (who decided, when, from
which client).

**How hard.** Easy mechanically; socially delicate. An editor's individual
decision history is exactly the kind of record a newsroom works hard not to
keep about staff performance.

### 2.6 One structural leak worth naming separately

**The negative class is discarded in aggregate.** `rejected_by_context_guard`
is a count; `rejection_reasons` maps reason strings to counts
(`selekyt/pipeline.py:310-315`). The candidates behind those numbers — their
text, spans, and full feature vectors — are dropped. A learning loop cannot
learn "why was this rejected" from a histogram. In the real run this was 0
candidates, so on this particular source the loss is nil; on a source with real
gate activity it would be the majority of the signal. Fixing it is a few lines
in `selekyt/pipeline.py` and is the cheapest high-value capture change in the
codebase, because those candidates are already fully scored in memory at
`rank()`.

---

## 3. The flywheel, phased

Each phase states what is built, what data exists, the **user-visible benefit**
(the loop must make the product better for the person supplying the data, or it
is surveillance with no payoff), and the honest failure mode.

### Phase 0 — Where we are: deterministic heuristics, no data (**VERIFIED**)

* **Built.** The full pipeline: transcription with per-word timing, story-unit
  detection, 4000-candidate generation, multi-signal scoring, context/truth
  guard, dead-air removal, captions, 16:9 + 9:16 renders, EDL/FCPXML, QA, and
  the editorial audit. All local-first; faster-whisper local, optional local
  Ollama, optional local VAD backends.
* **Data exists.** Rich per-candidate features (`reason` decomposition, five
  composite scores, per-word confidences) and per-run provenance. Zero labels.
* **User-visible benefit.** A usable cut from a local file or URL in minutes,
  with a per-decision audit trail. That is a real product benefit today and it
  does not depend on any learning.
* **Honest failure mode.** Every user gets identical editorial judgement, and
  that judgement is unvalidated against human editors — the project's own
  `docs/AI_EXECUTION_STATE.md:92-93` says the choices are not yet shown to
  "survive comparison with a human editor across sources and languages." The
  scoring weights in `engine/editorial.py:300-317` and `models/core.py` are
  hand-set constants with no empirical justification behind their ratios.

### Phase 0.5 — Self-audit loop, zero users (**VERIFIED that the tool exists; DESIGNED that it closes a loop**)

* **Built.** `tools/editorial_audit.py` over every real source; its failing
  checks recorded per run. Extend it to feed a small regression suite of
  sources so a scoring change that improves one source's audit and degrades
  another's is caught.
* **Data exists.** Audit outcomes against the existing artifacts — no human in
  the loop, no new capture needed.
* **User-visible benefit.** Indirect: the audit's nine falsifiable properties
  are exactly the defects a viewer notices (mid-word cuts, dead air, duplicated
  sentences). Fewer of them is a better product.
* **Honest failure mode.** **A self-consistent machine can be confidently
  wrong.** The audit measures whether the output obeys the pipeline's own
  editorial theory; it cannot measure whether a human would have chosen this
  clip at all. An audit-clean selection of boring clips scores identically to an
  audit-clean selection of interesting ones. This loop refines execution, never
  taste, and it must never be presented as evidence of editorial quality —
  only of editorial *discipline*.

### Phase 1 — Capture human decisions on every cut (**DESIGNED**)

* **Built.** Persist the full ranked set with feature vectors (not just the
  shipped 3); a review affordance that records keep/reject per candidate and
  per-edit-plan interval; the negative class persisted with identity, not just
  counts; a local decision ledger per project.
* **Data exists.** Hundreds to low-thousands of `(features, decision)` pairs
  per source, from a handful of users.
* **User-visible benefit — and this must be the design's centre of gravity.**
  The immediate payoff to the person deciding is *not* a better model; it is a
  **persistent bench and undo history**: "the 6 clips you rejected last Tuesday
  stay rejected," "the interval you trimmed is trimmed the same way on the next
  source." That is a workflow benefit, visible on the first run, requiring no
  model training at all. The learning is a byproduct the user never has to
  trust. If Phase 1's user benefit is described as "helps us train the model,"
  it is surveillance with no payoff and users will correctly refuse it.
* **Honest failure mode.** **Review fatigue kills the loop before the data
  compounds.** If approving three clips takes longer than ignoring them, the
  capture rate goes to zero and the ledger stays empty. Mitigation is to make
  the decision *cheaper than the alternative* (single-keystroke keeps, sensible
  defaults, decisions recorded from edits the user was going to make anyway —
  see Phase 2) rather than to add incentives.

### Phase 2 — Per-channel editorial profiles (**DESIGNED**)

* **Built.** A channel/workspace identity; a per-channel prior over the
  hand-set weights — hook emphasis, clip length distribution, cut density,
  caption density, `story_type` mix, how aggressively `retention_intervals`
  trims (`min_pause` / `keep_pause` / `keep_edge` in `engine/edit_plan.py:20`,
  and the `0.72` kept-ratio safeguard at `:53`). Critically, these should be
  *parameters to the existing deterministic scorer*, not a replacement for it —
  the scorer stays interpretable and the prior only reweights terms.
* **Data exists.** Many decisions from one channel. `docs/EDITORIAL.md`'s own
  stated guard applies verbatim: priors "should override generic heuristics
  only when enough real observations exist."
* **User-visible benefit.** The cut starts sounding like *this* channel's cut.
  This is the first phase whose benefit is personalisation, and it is also the
  first phase with real switching cost (§5).
* **Honest failure mode.** Two of them. **Overfitting to one editor's
  quirks** — a channel profile that has learned one senior editor's tics will
  fight every other editor in the same newsroom, which is a worse product than
  no profile. And **stale priors**: a channel's style changes; a profile that
  was fitted in month one and never decays will quietly contradict month nine.
  The design needs recency weighting and an explicit "reset priors" affordance.

### Phase 3 — Performance-conditioned ranking (**DESIGNED**)

* **Built.** Platform analytics ingest; join to published clips by the existing
  fingerprint + selection-digest keys; condition selection on observed retention.
* **Data exists.** Published-clip outcomes — real audience behaviour, not
  editor opinion.
* **User-visible benefit.** The system stops proposing the clip shape that
  consistently underperforms for this channel. This is the first phase whose
  benefit is denominated in the currency the user actually cares about (views,
  retention) rather than in editorial tidiness.
* **Honest failure mode.** **Goodhart, and the platform confound.** Optimising
  on retention will select for the clip shape that retains, which is not the
  same as the clip that is true to the source — this collides directly with the
  product's stated no-fabrication contract (§6). Worse, a clip's performance is
  confounded by publish time, thumbnail, algorithm state and audience overlap,
  none of which the pipeline controls; a "learning" system that attributes a
  40k-view clip to its cut density is mis-attributing variance. Phase 3 needs
  far more data than Phase 2 to see any signal through that noise, and it is the
  phase most likely to produce a confidently wrong model.

### Phase 4 — Proprietary editorial model (**DESIGNED**)

* **Built.** A trained editorial ranker (fine-tune or calibrated ensemble)
  replacing or overriding the hand-set weights, on labelled decisions plus
  outcomes.
* **Data exists.** Tens of thousands of labelled decisions across channels and
  languages.
* **User-visible benefit.** Judgement on sources and languages the heuristics
  were never tuned for — the project's own acknowledged blind spot
  (`docs/AI_EXECUTION_STATE.md:92-93`).
* **Honest failure mode.** **A model at this scale is usually worse than the
  heuristic plus an LLM, and the data to prove otherwise is the moat's price of
  admission.** With 10k labelled decisions the honest expectation is parity
  with `engine/llm_editorial.py`'s optional Ollama pass, which already exists
  and returns structured `arc`/`pacing`/`hook_quality`/`payoff_quality`
  metadata. Phase 4 is only worth building when the captured data is clearly
  better than that free local alternative — and a model that *replaces* the
  interpretable scorer also destroys the auditability that made Phase 0.5
  valuable.

### Phase gates (the honest preconditions)

| Phase | Gate that must be true to enter it |
| --- | --- |
| 0.5 | At least 3 real sources of differing length/language/style under the audit |
| 1 | A review/decision affordance whose cost to the user is lower than the value of its undo/bench benefit |
| 2 | One channel with enough decisions that a prior beats the global prior on held-out decisions — otherwise it is noise |
| 3 | A publish event exists in the data model, and per-clip platform IDs are captured at publish time |
| 4 | Captured-decision quality measurably exceeds the existing deterministic scorer plus the existing Ollama pass |

---

## 4. Cold start — why the first 10 users' data produces anything

**The shortest answer: the first 10 users' data produces improvement only if
you do not wait for 10 users, and only if you do not try to learn per-channel
anything yet.**

### 4.1 The minimum viable learning unit

The minimum viable learning unit is **one decision about one candidate**, and
its feature vector is already computed and already on disk. Concretely, one
row:

```
(candidate features from engine/editorial.py:323-334)  →  (keep | reject | edited)
```

The eleven decomposed sub-scores, `story_type`, the five composite scores, and
the per-word timing are all present in `selections.json` / `editorial_report.json`
today. The label is a single bit plus an optional edit. Nothing in this row is
channel-specific, which is the crucial cold-start property: **a global prior
over "which sub-scores predict a human keep" is learnable from the very first
decision**, and it transfers across every channel.

So the correct cold-start ordering is:

1. **Learn the global prior first** — reweight `models/core.py`'s fixed
   `0.30/0.22/0.20/0.16/0.12` and `engine/editorial.py:300-317`'s coefficient
   ratios against captured decisions. This uses every user's data in one pool.
2. **Only then learn per-channel priors**, when a single channel's held-out
   decisions show a channel prior beating the global one. `docs/EDITORIAL.md`
   already commits to exactly this discipline.
3. **Only then condition on performance**, which needs published outcomes, not
   decisions.

Skipping to step 2 or 3 with 10 users produces noise dressed as learning.

### 4.2 Why the first 10 users produce anything at all

* The features are pre-extracted and source-independent, so even a small pool
  of decisions is informative about *which signals the scorer over-weights*.
  The current weights are hand-set; the bar to beat is low.
* The bench is a natural source of weak labels. `truncated_by_limit: 6` in the
  real run means 6 candidates passed every editorial gate and were dropped by a
  budget constraint, not by judgement. A user who later promotes one of them
  from the bench supplies a clean positive label for a candidate the scorer had
  already ranked as publishable — low-conflict, high-signal data.
* The audit loop (Phase 0.5) generates labelled *execution* data with no users
  at all, so the flywheel's first turns do not depend on acquiring customers.

### 4.3 What will NOT improve with the first 10 users — said out loud

* **Per-channel personalisation.** Ten users is not a channel profile; it is
  ten anecdote-sized samples. Any per-channel benefit claimed at this scale is
  curve-fitting.
* **Performance conditioning.** There are no published outcomes. Zero.
* **Language or genre coverage.** One real source is English unscripted
  two-host speech (`docs/product-gap-map.md`); the multilingual rules in
  `engine/context_guard.py` and `engine/editorial.py` are exercised by fixtures,
  not by real non-English audio.
* **Anything requiring the NLE round trip.** Until one export opens in one real
  NLE, the highest-value signal (§2.2) is unreachable in principle, not merely
  unmeasured.

### 4.4 The honest cold-start risk

**The first users get a worse product than later ones, and they are not
compensated.** A Phase-1 user supplies decisions, and the model that benefits
from those decisions serves the *next* user. The only design that is not
exploitative is the one in §3 Phase 1: the user's own decisions must improve
*their own* product immediately (bench, undo, persisted rejections), with the
aggregate learning as an unadvertised byproduct. If the user-visible benefit of
Phase 1 is described as model improvement, the flywheel is asking people to
donate labels, and it deserves to fail.

---

## 5. Defensibility honestly assessed and ranked

Criteria: does it **compound with usage** (each user makes it better), is it
**hard to copy** (not reproducible by reading the code or calling the same
APIs), and does it create **switching cost** (leaving costs the user something).

| Rank | Asset | Compounds | Hard to copy | Switching cost | Verdict |
| --- | --- | --- | --- | --- | --- |
| 1 | **Per-channel editorial profile conditioned on the AI→human diff** | yes | yes — the diff only exists inside the loop | yes — the profile knows the channel's taste | **The only genuine moat candidate.** Does not exist at all today. |
| 2 | **The in-the-loop position (the timeline the editor finishes)** | yes, positionally | partially — earned by interchange correctness | yes — the editor's workflow is built on it | **A positional moat, and currently fragile**: no NLE has ever opened an export (`docs/product-gap-map.md`), so the position is claimed, not held. |
| 3 | **Captured decision/performance corpus** | yes | yes if proprietary | no | **A moat only at scale.** At 10 users it is a spreadsheet. Compounds, but the first thousand rows are worthless in isolation. |
| 4 | **Editorial feature extraction + provenance schema** | no | no — readable in `engine/editorial.py` | no | **Engineering work, copyable.** But it is the *substrate* every phase above depends on, and it is well built. A precondition, not a moat. |
| 5 | **Deterministic editorial scorer** | no | no — and partly derived: see `docs/autocut-provenance.md` and the public platform guidance cited in `docs/EDITORIAL.md` | no | **Not a moat.** Explicitly within the founder's "generic LLM/Whisper/ffmpeg is not a moat" category. |
| 6 | **A fine-tuned editorial model** | only via the corpus | only via the corpus | no | **A moat in its data, not its existence.** A model trained on 10k decisions is a commodity; the corpus is the asset. |

### What this ranking actually says

The defensible asset is **the diff between what the machine proposed and what
the human shipped**, accumulated per channel. It is the one signal that cannot
be scraped, bought, or reconstructed by a competitor, because it is created
only by being the tool the editor is working inside. Everything else in the
pipeline — the Whisper transcription, the ffmpeg renders, the heuristics, the
FCPXML writer — is reproducible by anyone with this repository and a weekend.

The corollary the founder's framing demands: **the moat is downstream of the
NLE round trip.** Ranking 1 and ranking 2 both depend on the export being
finished in a real editor and read back. That is item G2 in
`docs/product-gap-map.md` and task 2 in `docs/AI_EXECUTION_STATE.md` — an
unbuilt, unverified-once capability. So the highest-leverage work toward a moat
is not a data warehouse or a training run; it is **one FCPXML round-tripped
through one real NLE**, which is pure engineering and produces the capability
the entire flywheel hangs from.

---

## 6. Privacy and IP constraints — what the architecture must guarantee for the loop to be *allowed* to exist

Editing confidential footage is the norm for newsrooms and enterprises. A
learning loop is only permissible if it never becomes a confidentiality risk.

### 6.1 A real defect found in the current artifacts — **VERIFIED, and fixed**

`pipeline_state.json` persists `media_info` — the complete yt-dlp info dict — and
the raw dict contains, on a real download:

* a live session cookie (`media_info.cookies`, value
  `ia-csrf=51ce7e1cead7cd41bdb5c5713da82f78; Domain=.archive.org; …`), and
* `http_headers` including a full browser `User-Agent`.

This is not a training concern today, because nothing leaves the machine. But
it is the exact field that a future telemetry/feedback upload would serialize
by accident. `providers/youtube.py:31` is careful to pass cookie files as a
subprocess list and never echo them; without a scrub at the write, that care is
undone by the persistence of `media_info` into state.

**The fix is in the code, not the roadmap.** `selekyt/pipeline.py:371` persists
`_scrub_credentials(info)` rather than `info`, dropping `http_headers`,
`cookies`, `cookie_manager`, `cookiejar`, `_cookies` and `cookies_str` at every
depth. The rationale is the one above and it is deliberate: a privacy guarantee
applied only at the network boundary will eventually be bypassed by a refactor;
applied at the write it is structural. Nothing downstream reads those keys — the
pipeline reads duration, title and format ids — so the scrub costs no
functionality.

**What holds it.** `tests/test_pipeline_hardening.py` runs the pipeline with a
mocked download returning a dict carrying `http_headers` and `cookies`, then
reads `pipeline_state.json` off disk and asserts neither key is present. The
assertion is on the file, not on an in-memory dict, because a scrub that only
held in memory would be exactly the bug this is about. Two limits, stated: the
list of dropped keys is a known-bad list, so a future yt-dlp key carrying a
credential under a new name is not caught by it, and the recursion is
depth-bounded at 8 rather than unbounded. Both are the scrub's real shape, not
a claim of completeness.

### 6.2 The local-first architecture is the enabler, not the obstacle — **VERIFIED**

The pipeline is already local-first: CLI and local web shell, local
faster-whisper, optional local Ollama (`engine/llm_editorial.py`), optional
local VAD backends, no telemetry, no accounts, no database. That posture is
what makes an enterprise data loop possible at all, and it dictates the design:

**Learn from decisions, not from content.** The loop should ship *priors* —
small numeric parameter sets reweighting an existing deterministic scorer — not
transcripts, not footage, not audio. A newsroom's confidential interview never
leaves its network; only the statistical residue of how its editor cut does.
This is the difference between a tool that newsrooms can deploy and one they
cannot, and it is a design constraint that must be imposed on Phase 1 capture
from the first line, because retrofitting it is far harder than building it.

Concretely, a decision ledger should record `{features, decision, channel_id,
editor_id, provenance}` and **never** `{transcript_text, source_path, media}`.
Features as currently emitted are mostly lexical statistics; even those should
be reviewed, since `hook`/`payoff` strings in `editorial_report.json` are
verbatim source speech.

### 6.3 Consent and the no-fabrication contract — **VERIFIED as a product contract**

Two policy strings are written into the real artifacts and constitute a promise
to the user:

* `report.json`: `"retention-aware editorial heuristics; source speech is not
  rewritten; no fabricated claims"`
* `content_pack.json`: `"Generated from selected source speech; no fabricated
  claims."`

A learning loop must not be allowed to break these, or the artifacts become
lies. The design rule it imposes: **learning adjusts *selection and ranking*,
never *text generation*.** A Phase 4 model that learns to produce higher-retention
hooks by rewriting them would violate the stated contract. This is also the
honest bound on Phase 3: performance-conditioned selection is defensible,
performance-conditioned rewriting is not.

Consent must be per-channel and per-decision-type, not a single blanket toggle,
and on-by-default-off. Enterprise customers require that training on their data
never happens without explicit written opt-in; anything captured without it is
a legal and trust liability that would end the loop's permission to exist.

### 6.4 Provenance retention is an IP obligation, not just a feature — **VERIFIED**

`docs/autocut-provenance.md` records that AutoCut reuse rests on the author's
**explicit permission, not a license** — AutoCut carries no OSI-approved
license, and the doc instructs: "Prefer—and preserve—the in-tree attribution
headers over any license claim." Files derived from AutoCut include
`rust/core/src/{audio,cutlist,export_fcpxml,export_mp4,probe,sync,timecode,vad,waveform}.rs`
and the Python ports `engine/timecode.py` and `engine/fcpxml.py`.

Two consequences for the data loop. First, any model trained on outputs
produced by this pipeline inherits a provenance obligation that must be carried
into the model card, not dropped when the training job starts. Second, the
attribution discipline that exists today must survive the refactor that adds
capture — a feedback loop that silently severs attribution is an IP problem
disguised as a schema change. Separately, `LICENSE` is proprietary
(source-available, no redistribution or competing service without written
permission), which is licence-compatible with a hosted service model but not
with an assumption of open-source contribution.

### 6.5 The guarantees, stated as a checklist

The loop is allowed to exist only if the architecture guarantees:

1. **On-prem / self-hosted is a first-class deployment, not a degraded mode.**
   Local-first is already true (`VERIFIED`); it must stay true as features land.
2. **No training on customer data without explicit, separate, written opt-in** —
   per channel, and revocable, with a deletion path that actually removes the
   channel's decisions from every prior.
3. **Content never leaves the customer's boundary by default.** Ship priors, not
   transcripts (§6.2). Verbatim speech (`hook`, `payoff`, `text`) must be
   excluded from the feedback record by schema, not by policy.
4. **Secrets are scrubbed at write time, not at upload time** (§6.1 — the
   concrete `media_info.cookies` / `http_headers` defect).
5. **Provenance is retained end to end** — ASR model and fallback
   (`transcript.json` `asr`), source fingerprint, selection digest, scorer
   version — so any observation can be re-derived or retracted. This already
   exists (`VERIFIED`) and must be carried into the decision ledger.
6. **The no-fabrication contract holds under learning** (§6.3): the loop adjusts
   ranking, never source speech.
7. **Editor-attributable records are optional, minimal and deletable** (§2.5) —
   they are staff-performance data in a newsroom context.

---

## 7. The one thing that would make this flywheel fail

**The AI→published diff is unreachable, and everything above it is a
consequence of that.**

Rankings 1 and 2 in §5 — the only genuine moat candidates — both depend on the
machine's proposal and the human's finished cut existing in the same data model
so they can be differenced. Today the proposal half is precise and
well-modelled (`engine/timeline.py`, `engine/timeline_build.py:23`,
`engine/nle_export.py`), and the finished half **does not exist as parseable
data**. There is no FCPXML or EDL import path anywhere in the codebase, and per
`docs/product-gap-map.md` no NLE has ever opened a SelekyT export on any host.

So the failure mode is not "the model is under-trained" or "we lack enough
channels." It is that the loop's highest-value signal is structurally
unobservable, and the one capability that would make it observable is
unverified and unbuilt — while being, paradoxically, pure engineering: format
parsing, the existing `Timeline` model already has the invariants
(`engine/timeline.py:107-125`, `:302`) to accept a re-imported cut.

Everything else in this document is contingent. Phase 1 can be built and will
produce a usable decision ledger; Phase 0.5 runs today. But if the NLE round
trip never works, SelekyT's data loop is permanently limited to approving the
machine's own candidates — a strictly weaker signal than watching what a human
editor did to them — and the defensible position collapses into "a good local
editing tool," which is engineering, not a moat.

The single highest-value next action toward the flywheel is therefore not
data infrastructure. It is **one real FCPXML exported, opened and modified in
DaVinci Resolve or Premiere, and the modified file parsed back through
`engine/timeline.py`** — which is also task 2 in the project's own ranked list
in `docs/AI_EXECUTION_STATE.md`. Nothing in this document is more leveraged
than that.

---

## Appendix — signal map: artifact → what a loop would learn from it

| Artifact (real run) | Field | Learning use | Label present? |
| --- | --- | --- | --- |
| `editorial_report.json` | `reason` sub-scores | pre-explained feature vector | no |
| `editorial_report.json` | `story_type`, `hook`, `payoff` | categorical features; **verbatim speech, privacy-sensitive** | no |
| `selections.json` | `total_score` + five composite scores | ranking calibration target | no |
| `selections.json` | per-word `confidence`, timing | ASR-quality conditioning of features | no |
| `report.json` | `truncated_by_limit` (6) | the bench: clean weak-positive labels | no |
| `report.json` | `rejection_reasons`, `rejected_by_context_guard` | negative class — **identity discarded, only counts survive** | no |
| `edit_plan.json` | `intervals`, `removed_seconds` | dead-air-removal tuning target | no |
| `pacing_report.json` | `avg_hook_latency`, `avg_words_per_second`, `duplicate_overlap_rate` | pacing priors | no |
| `qa.json` | `diversity`, `source_coverage`, `silent_tail`, `black_frame_pct` | execution-quality loop (Phase 0.5) | self-scored |
| `timeline/NN.fcpxml` | source in/out, `Clip.trace()` | **the AI side of the diff** | no |
| `audio/cutlist.json` | per-segment `type`/`reason` | VAD and filler-removal tuning | no |
| `pipeline_state.json` | `fingerprint`, `selection_digest` | join keys for any later outcome data | n/a |
| `transcript.json` | `asr` block | provenance for every observation | n/a |
| *(nothing)* | human keep/reject | **Phase 1 label — absent** | — |
| *(nothing)* | human-edited timeline | **Phase 2+ label — absent, and unparseable today** | — |
| *(nothing)* | published-clip performance | **Phase 3 label — absent** | — |
| *(nothing)* | channel / editor identity | **the grouping key — absent** | — |
