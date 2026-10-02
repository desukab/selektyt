# SeleKyT evaluation framework — the intelligence layer

**Status:** framework defined 2026-09-29. Every benchmark below either carries a
measured number obtained on this host, or says explicitly that it cannot be
stated yet and what blocks it. No number in this document is invented.

**Scope.** This framework evaluates the *new* intelligence layer only: video
memory, retrieval, temporal grounding, the evidence graph, and editorial
reasoning. It does **not** restate what the existing audit measures. The
deterministic editorial pipeline — cut placement, caption fidelity, render
geometry, NLE interchange — is already benchmarked by `tools/editorial_audit.py`,
`tools/nle_check.py`, `engine/qa.py` and documented in `docs/EVALUATION.md`,
`docs/CORPUS_RESULTS.md`, `docs/CORPUS_BENCHMARK_STATUS.md` and
`docs/product-gap-map.md`. Those are closed questions for the purpose of this
document, and are cited here only where they define a *gap*.

**The governing rule.** A framework full of unmeasurable metrics is worse than
one that names its gaps. Each benchmark below therefore states, as a structured
block: **dataset, population, metric, baseline, limitations, cost.** Where a
field cannot be filled, it says so and names the blocker.

---

## 0. How to read this document

Evidence labels, following `docs/EVALUATION.md`'s convention:

| Label | Meaning |
|---|---|
| **[code]** | Read directly from the source of this repository. Cited as `file:line`. |
| **[measured]** | Produced by execution on this host on 2026-09-29 by this framework pass. The script logic is described so it can be re-run. |
| **[design]** | Stated in a design document in `docs/`, not yet backed by code that exists. |
| **[paper]** | Comes from external research. Not measured on this host, not reproducible here. Labelled for what does and does not transfer (§6). |
| **[cannot be stated]** | The metric is not computable on what exists. The blocker is named. |

Host: 4 CPUs, 15 GB RAM, CPU-only, no `torch`, no ollama, no GPU
**[code]** (`docs/product-gap-map.md`). Free disk measured today: **1.3 GB**
**[measured]** — the `docs/product-gap-map.md` figure of 2.1 GB was correct at the
end of its pass and is now stale; the binding figure is 1.3 GB, and it constrains
every benchmark in §4 that would need media.

---

## 1. The layer this framework evaluates

Five sub-layers, and the honest state of each:

| Sub-layer | Exists as code | Does not exist | Citation |
|---|---|---|---|
| Perception → memory ingest | six perception signals are computed by the pipeline | `engine/memory/ingest` — documented in `engine/memory/__init__.py:41-47` **[design]**, not on disk | `docs/memory-perception-inventory.md` |
| Memory store | — | `engine/memory/store` (`VideoMemory`, *"the persistent, queryable world for one source"*) **[design]** | `docs/verify-repair-design.md:169-176` |
| Evidence graph (values) | `engine/memory/evidence.py`, `engine/memory/intervals.py` | imported by nothing outside `engine/memory/__init__.py` and `tests/test_memory_evidence.py` | `docs/verify-repair-design.md:172-176` |
| Index → retrieve | lexical primitives: `engine/lexical.py`, `engine/topics.py`, `engine/redundancy.py` | no index or retrieval module; no query path | `engine/lexical.py:30-134` |
| Editorial reasoning | `engine/corpus.py`, `engine/threads.py`, `engine/corpus_io.py`, `engine/story_path.py`, `engine/story_view.py` | no verify, diagnose or repair anywhere in `engine/`, `selekyt/`, `tools/` | `docs/verify-repair-design.md:160-167` |

The single most consequential fact in that table: **`Evidence` is a designed-in
ground truth, not a populated one. No pipeline stage currently emits an
`Evidence`.** **[design]** (`docs/verify-repair-design.md:176`). The ground-truth
ladder is therefore:

| Rung | Artefact | Populated? | Citation |
|---|---|---|---|
| G1 | `edit_plan.json` — intervals the render concatenated | **yes** | `tools/nle_check.py:481-505`, `engine/qa.py:327-341`, `selekyt/pipeline.py:1111` |
| G2 | transcript word timestamps + utterance spans | **yes** | `tools/editorial_audit.py:729-755` |
| G3 | `Evidence` intervals + confidence | **no — specified only** | `docs/verify-repair-design.md:193-195` |

Every evidence-backed benchmark in §4 degrades explicitly to G2 word coverage
until G3 is populated. That degradation is stated per-benchmark, not hidden.

---

## 2. The gaps the existing benchmarks leave

This is the reason the framework exists. Each gap names what the existing audit
cannot see, and each benchmark in §4 is anchored to one.

**G-A — The audit is self-consistency, not truth.** `check_caption_text_is_transcript_text`
(`tools/editorial_audit.py:406`) compares captions against the transcript the
clip was cut from. A transcript that is phonetic noise produces a clean audit;
`docs/CORPUS_RESULTS.md` records exactly that case, and `docs/product-gap-map.md`
states the limit in one line: *"the audit has no ground truth — it compares a
clip against the transcript it was cut from."* **[code]** Nothing existing asks
whether a *retrieved* moment is the right moment.

**G-B — The audit measures the output, never the memory.** The corpus verdicts
are 11 runs / 16 clean clips / 11 awaiting review / 4 failures, with **no
clip-level failures at all**; all four failures are project-level VAD gates that
suppressed the transcript **[code]** (`docs/product-gap-map.md`,
`docs/verify-repair-design.md:158`). The layer that decides *what to consider*
has no failure mode the audit can catch, because it produces no clips to audit.

**G-C — Most perception signals never reach disk.** Of six perception signals,
only Signal 5 (story structure, `engine/corpus.py` `build_corpus`) and an
aggregate-only `coverage` figure are persisted. Signals 1 and 2 (face/subject
tracks, shot/scene classification) never reach disk; Signals 3 and 4 (speech
regions, pause structure) are computed only behind `--audio-analysis`, which
defaults to `False`. **[code]** (`docs/memory-perception-inventory.md`). Any
benchmark of *"does the memory reflect the video"* has nothing to compare
against for four of six channels.

**G-D — No ground truth of any kind.** The inventory's own list of what is not
perceived includes, as item 11, *no ground truth of any kind* **[code]**
(`docs/memory-perception-inventory.md`). G1 and G2 are populated; G3 is not
(§1). The evidence graph's purpose is to *become* ground truth, so it cannot
currently be scored against anything but itself.

**G-E — Two corpus languages have no measured transcription-error floor.** The
corpus spans four languages — `en` ×7, `te` ×2, `hi` ×1, `hu` ×1 **[code]**
(`corpus/manifest.json`). The language-fitness table measures seven languages:
`de`, `en`, `es`, `fr`, `it`, `pl`, `te` **[measured]** (executed
`engine.language_fitness.load_table()` today). **`hi` and `hu` have no entry.**
Since `WER_BAR = 0.25` and `WER_REFUSE = 1.0` gate transcripts
(`engine/language_fitness.py:78,84`), the two non-English languages with the
least-established ASR support carry an *unquantified* error floor. Any recall or
grounding metric over those transcripts inherits an error nobody has bounded.

**G-F — Hour scale is asserted, not measured.** The product's stated input is
1–3 hours **[code]** (`docs/CORPUS_BENCHMARK_STATUS.md:171`). The corpus's
longest source is 55.5 min (`emwcon-panel-2018`); the next is 24.4 min
(`nasa360-ep5`) **[measured]** (`docs/CORPUS_BENCHMARK_STATUS.md:181`). **No
source in the corpus reaches one hour**, and only two sources have media on disk
at all (§3). The property the corpus *does* establish — that selection is no
longer structurally prevented from reaching the back half of a long source
**[code]**, `docs/CORPUS_BENCHMARK_STATUS.md:227-229`) — is not the same property
as hour-scale retrieval, and `docs/CORPUS_BENCHMARK_STATUS.md:238-240` says so:
*"any of this measures editorial quality — it measures reproducibility and gate
behaviour."*

---

## 3. What is actually available to measure on

Population inventory, measured today by reading the two corpus runs whose media
survive on disk **[measured]**:

| | `pentagon-esper-briefing` | `royal-society-cloke` | total |
|---|---|---|---|
| Media on disk | 158 MB / 1013.85 s | 85 MB / 626.02 s | 243 MB / 26.4 min |
| Language | `en` | `en` | — |
| Transcript words (G2) | 2745 | 1980 | 4725 |
| Transcript span | 959.7 s | 625.6 s | 1585.3 s |
| `has_word_timestamps` | `True` | `True` | — |
| `has_speaker_labels` | `False` | `False` | — |
| Corpus units | 61 | 22 | 83 |
| Story links | 26 | 31 | 57 |
| Threads | 45 | 11 | 56 |
| Topics / beats | 38 / 61 | 9 / 22 | 47 / 83 |
| Redundancy groups | 2 | 0 | 2 |
| Unit `timing_source` | 52 `character_proportional`, 9 `word_aligned` | 21 / 1 | — |
| ASR | faster-whisper `small`, 5-min chunks, VAD on | same | — |

Media availability **[code]**: only `corpus/media/pentagon-esper-briefing.mp4`
and `corpus/media/royal-society-cloke.webm` are present; the other nine were
removed out of band for disk (`docs/CORPUS_BENCHMARK_STATUS.md:250-255`). Both
runs carry the full artefact set — `transcript.json`, `edit_plan.json`,
`editorial_corpus.json`, `selections.json`, `qa.json` — so a benchmark that works
from those JSON files needs **no decode at all**. That distinction drives every
cost field below.

Two honest constraints on this population, before any metric is stated:

1. **Both available sources are English.** The Telugu sources cannot be used:
   `telugu-launch` returned `NO_SPEECH_DETECTED` and shipped 0 clips
   (`docs/CORPUS_RESULTS.md:37`), and `telugu-tutorial` no longer ships
   (confidence 0.319 against the 0.75 floor, `docs/CORPUS_BENCHMARK_STATUS.md:141-151`).
   The Telugu WER of 91.54% (medium) / 151.08% (small) was measured on SLR66, not
   on corpus media.
2. **The two non-shipping corpus sources are not current deliverables.**
   `hadhazy-protest` (0.639) and `telugu-tutorial` (0.319) are refused by the
   confidence gate; their committed artefacts are retained as evidence of what a
   truncated scan and a low-confidence transcript look like, not as ground truth
   (`docs/CORPUS_BENCHMARK_STATUS.md:231-240`).

---

## 4. The benchmarks

Nine are stated with all six fields (B1–B9). Three are declined with their
blockers named (B10–B12). An index table is in §8.

---

### B1 — Memory round-trip determinism

**The gap:** G-D. If the memory layer cannot be rebuilt byte-identically from
its own source fingerprint, every other benchmark on it is measuring noise.

| Field | Value |
|---|---|
| **dataset** | The two on-disk corpus runs' `editorial_corpus.json` (§3). No decode needed. |
| **population** | All 83 units, 57 links, 56 threads, 47 topics, 83 beats and every scalar field they carry. Census, not sample. |
| **metric** | `read_corpus(d).to_dict() == d` for each run, re-run in three fresh interpreters. Unit: fraction of fields identical. Also the wall-clock of the round trip. |
| **baseline** | 1.000 is the target, and it is a **structural** baseline (asserted, not measured): the schema is a dataclass round trip, so any deviation is a defect. The measured precedent for corpus-level determinism is 3 of 7 delivering runs reproducing exactly (`docs/CORPUS_BENCHMARK_STATUS.md:51`) — that is the honest expectation for a *re-run*, which is a weaker property than this in-memory round trip. |
| **limitations** | Round-trip identity proves serialisation is lossless. It does **not** prove the corpus is correct, only that it is stable. A wrong-but-stable corpus passes. |
| **cost** | **[measured]** 10.01–17.86 ms (`pentagon`, 61 units) and 4.39–4.58 ms (`cloke`, 22 units), single CPU, no decode, no disk write. |

**Result measured today: 1.000 on both runs.** **[measured]** The round trip is
byte-identical, including the 1.1 schema's derived `unit_threads`.

---

### B2 — Known-item lexical retrieval (the only retrieval benchmark computable now)

**The gap:** G-A plus the absence of any retrieval module. This is the benchmark
that exists once an index exists, run here in prototype against the lexical
primitives the index will be built on, and its result is the finding.

| Field | Value |
|---|---|
| **dataset** | The two on-disk corpus runs (§3): 4725 word-timestamped tokens over 1585.3 s, 83 units. |
| **population** | Leave-one-out over all 83 units. Each unit in turn supplies a query; the other 82 plus itself are the candidate pool. Census, not sample. |
| **metric** | Two, both at K=3 (the researched operating point, §6): (a) **recall@3** — fraction of queries whose own unit is in the top 3; (b) **temporal mIoU** — intersection-over-union between the union of the top-3 candidate spans and the ground-truth unit span. Both unitless. Query = the idf-weighted rarest half of the unit's own `content_tokens`; scoring = `weighted_containment` (`engine/lexical.py:81`); idf from `df_table`/`idf_weights` (`engine/lexical.py:41,53`). |
| **baseline** | Two, both **measured on this host**, neither asserted: a leave-one-out exclusion baseline, and a duration-matched random-window baseline for mIoU. The paper's mIoU 0.354 is **not** used as a baseline — see §6 for why it does not transfer. |
| **limitations** | **This benchmark is circular and its headline number proves it.** The query is constructed by the same idf weighting the retrieval scores with, so recall@3 measures the index's machinery, not its judgement. A second variant — query = the unit's full term bag with the unit itself excluded — returned **0/83** hits, proving the population contains no near-duplicate passages; that variant is therefore degenerate here, and measures restatement frequency, not retrieval. Neither variant can see whether a retrieved span is *semantically* right, only whether the terms line up. Semantic (embedding) retrieval is unmeasured by design (`docs/rd-landscape.md`: the `sentence-transformers` trial is *"not yet measured"*). |
| **cost** | **[measured]** index build 1.9 ms / 1.2 ms; full 83-query retrieval pass 3.0 ms / 1.0 ms. Single CPU, no decode, no disk write. Sub-millisecond per query at this population size. |

**Results measured today** **[measured]**:

| | recall@3 (partial cue) | mean rank when found | mIoU (union of top-3) | recall@3 (leave-one-out) |
|---|---|---|---|---|
| `pentagon-esper-briefing` (61 units) | 61/61 = **1.000** | 1.02 | **0.0446** | 0/61 = **0.000** |
| `royal-society-cloke` (22 units) | 22/22 = **1.000** | 1.00 | **0.1086** | 0/22 = **0.000** |

**What these numbers mean, stated plainly.** A recall of 1.000 on a
self-constructed query is not evidence that retrieval works. It is evidence that
the lexical index is deterministic and that this population has no restatements
to confuse it. The mIoU of 0.045–0.109 is not comparable to the paper's 0.354
(§6): the target spans here are a few seconds of speech and the top-3 union is
minutes, so the denominator dominates. **No editorial quality claim may be made
from B2.** Its legitimate use is as a regression guard: a future change to the
index that moves recall off 1.000, or mIoU outside this measured band, has
changed retrieval behaviour and a reviewer should see it.

---

### B3 — Grounding word-boundary conformance

**The gap:** G-A. Grounding must land on speech, and the only ground truth for
where speech is, is G2's word timestamps.

| Field | Value |
|---|---|
| **dataset** | The two on-disk corpus runs (§3). No decode. |
| **population** | Every interval bound the memory and story layers emit: unit, link and thread start/end — 506 bounds total (316 + 190). Census. |
| **metric** | `min(|bound − word_edge|)` over all transcript word starts and ends. Reported as the share of bounds within `MERGE_EPS = 1e-6` s, and as the maximum deviation. Unit: fraction, and seconds. |
| **baseline** | 1.000 for the share is the **structural** target — the design intent is that story bounds are word-anchored. It is asserted, not measured. No measured baseline exists for the deviation; only the measured value is reported. |
| **limitations** | Conformance is not correctness: a span can land on the *wrong* word exactly. And the metric is blind to the 12 unperceived channels (no speaker identity, no OCR in the pipeline, no slide content — `docs/memory-perception-inventory.md`), so "conforms to words" is not "conforms to the video". |
| **cost** | **[measured]** <1 s for all 506 bounds across both runs, single CPU, no decode. |

**Results measured today** **[measured]**:

| | bounds | within `1e-6` s of a word edge | farthest bound | zero-width units |
|---|---|---|---|---|
| `pentagon-esper-briefing` | 316 | 293 (92.7%) | 0.0820 s | 0 |
| `royal-society-cloke` | 190 | 98 (51.6%) | 0.4180 s | 0 |

The non-conforming bounds are attributable, and the attribution matters: they
come from units whose `timing_source` is `character_proportional` rather than
`word_aligned` (52 of 61 units in `pentagon`, 21 of 22 in `cloke`), and from the
link times derived from them **[measured]**. **The memory layer's interval bounds
are a mixture of two timing provenances.** A consumer that assumes every bound is
word-anchored is wrong on the majority of units in both corpora. This is a
design input to the ingest layer, not a defect in the corpus.

---

### B4 — Interval-algebra adversarial regression

**The gap:** G-D. The evidence graph's value types are the one part of the new
layer that already exists, and they have a documented adversarial history.

| Field | Value |
|---|---|
| **dataset** | The 20 constructed adversarial inputs in `docs/memory-failure-modes.md`, each reproduced by execution on 2026-09-29. Synthetic; no corpus media involved. |
| **population** | All 20. Severity census: 4 × S1 (#1–#4), 10 × S2 (#5–#14), 5 × S3 (#15–#18, #20), 1 × verified-clean (#19). |
| **metric** | Per-case pass rate against the post-fix expected behaviour the document records (its "Verified by execution" table, `docs/memory-failure-modes.md:406-441`). Unit: fraction. The S1 subset must be 1.000; an S1 failure blocks release. |
| **baseline** | 1.000 for S1 and S2, **asserted** from the severity definitions: S1 *"can put a wrong clip in front of an editor or make the verify stage lie about backing"*, S2 *"a confident wrong answer whose blast radius is bounded"* (`docs/memory-failure-modes.md:8-10`). S3 has no pass/fail baseline — it is a design gap, tracked, not gated. |
| **limitations** | These are **constructed inputs, not real outputs.** The document's own "Could not verify" section names the limit precisely: the *frequency* of sub-`MERGE_EPS` geometries in this pipeline's real outputs is unmeasured, and defects #3 and #5 both *depend on one occurring to bite* (`docs/memory-failure-modes.md:445-450`). B4 proves the algebra is correct on adversarial input; B5 is what measures whether the precondition ever arises. |
| **cost** | **[measured by the failure-mode pass]** sub-second, pure CPU, no decode. Constants in play: `MERGE_EPS = 1e-6` s, `engine.cut_algebra.DEGENERATE_SECONDS = 0.01` s, `is_instant` at `1e-6` s — three different notions of "zero width" (failure mode #16). |

---

### B5 — Real-output precondition rate for the S1/S2 algebra defects

**The gap:** the specific thing B4 cannot see, and the specific item
`docs/memory-failure-modes.md` listed as unverifiable. This benchmark closes that
gap with a measurement, and the measurement changes the reading of B4.

| Field | Value |
|---|---|
| **dataset** | The two on-disk corpus runs (§3), using G2 word timestamps as the reference grid. No decode. |
| **population** | All 506 story/link/thread interval bounds (the same census as B3), plus all 83 units for the zero-width test. |
| **metric** | (a) share of bounds within `MERGE_EPS` of a word edge — the precondition for #3 (merge rule disagrees with `cut_algebra`), #5 (`subtract` moving a surviving span) and #6 (`intersect` manufacturing a sliver); (b) count of zero-width spans passed to `covers()` — the precondition for #2. Unit: fraction, and count. |
| **baseline** | Zero-width count: **0, structurally** (units are built from transcript segments, so a zero-width unit is a defect). The bound-proximity share has **no asserted baseline** — only the measured value, because whether the precondition is common or exotic was exactly the open question. |
| **limitations** | Only 2 sources, both English, both single-ASR-engine (`faster-whisper small`). The store and ingest layers do not exist, so the caller paths that would *actually* hand a zero-width span to `covers()` (#2) or a raw string to `for_modality` (#7) do not exist either — those remain *"hazards for the retrieval and grounding layers rather than live bugs"* (`docs/memory-failure-modes.md:460-462`). This measures the data, not the eventual callers. |
| **cost** | **[measured]** <1 s for both runs, single CPU, no decode. |

**Results measured today** **[measured]** — the numbers are in B3's table, and
the finding is in how they read: the precondition for three S1/S2 defects is
**common, not exotic**. 92.7% of bounds in one corpus and 51.6% in the other
coincide with a word edge to within a microsecond, i.e. they are ASR-derived
floats of exactly the kind `engine.edit_plan._word_aligned` produces — the
`364.84` / `364.84000000000003` precedent `docs/memory-failure-modes.md:447`
cites. **B4's defects #3, #5 and #6 are live hazards on this corpus's actual
data, on between half and nine-tenths of its interval bounds.** The zero-width
count is 0, so #2's precondition is currently unmet.

---

### B6 — Story-graph structural bounds at scale

**The gap:** G-B. This benchmark already exists and is **cited, not reinvented**:
`tests/test_story_bench.py`, defined in `docs/story-memory-design.md:370-411`.

| Field | Value |
|---|---|
| **dataset** | A large synthetic corpus built by the existing corpus fixtures, up to 2000 units. Synthetic by necessity: no real corpus source is within an order of magnitude of the scale the graph must survive. |
| **population** | All units, edges, thread memberships and candidates the builder produces at each scale. |
| **metric** | Structural bounds, each labelled by kind — edge count `≤ 6 · U`; thread membership per unit `≤ window + 1` and total `≤ (window + 1) · U`; story count `≤ U`; beats `== U`; clip count `==` path length; peak resident size `≤ 0.125 MB · U`; and the equality *"the number of moments a 60 s and a 180 s budget selects is the same across corpus sizes"*. Plus one wall-clock ceiling: build time `< 120.0 s`. |
| **baseline** | The wall-clock ceiling is **measured-then-asserted**: it is the existing ceiling in `tests/test_corpus.py::test_three_hour_scale_completes_in_bounded_time_and_memory`, re-asserted after the schema-1.1 link relaxation. The structural bounds are **provable from the construction** (`engine/threads.py` enumerates `range(i + 1, min(n, i + 1 + window_units))`), so they cannot be satisfied vacuously. |
| **limitations** | Stated by the design itself (`docs/story-memory-design.md:402-408`): a wall-clock ceiling is a machine claim, and the sub-linear scaling assertion the original draft called for was **dropped** — build measured 0.128 s at 200 units and 1.878 s at 2000, which is mildly super-linear (≈14.7× for 10× input) and fits no clean power law (sub-segment exponents 1.03 and 1.44). Asserting a scaling exponent there would have been reporting a number that is not measured; the scaling is reported as measured instead. |
| **cost** | **[measured]** seconds at 2000 units on this host, 4 CPUs, no decode. The measured build times above. |

---

### B7 — Story → edit-plan behaviour coverage

**The gap:** G-B. The join from graph to selection is the newest seam in the
system; it needs a behavioural gate, not a performance one. Already defined in
`docs/story-memory-design.md:337-361` as `tests/test_story_memory.py`, 17
required behaviours.

| Field | Value |
|---|---|
| **dataset** | Synthetic fixtures constructed to expose each behaviour, plus the two on-disk corpora for the round-trip cases (#8, #11, #12). |
| **population** | All 17 required behaviours: distant moments linked (#1), setup→payoff ordering (#2), chains ≥3 (#3), multiple stories (#4), one moment in multiple stories with a *different role in each* (#5), nonlinear edges (#6), confidence ≤ evidence strength and non-constant (#7), provenance to source timestamps (#8), graph→plan containment and role ordering (#9), export parity (#10), export→import round trip in a fresh interpreter (#11), field-for-field regeneration (#12), no fabricated relationship (#13), duplicates excluded (#14), mid-sentence openings rejected (#15), payoff-without-setup flagged or excluded, never silently shipped (#16), existing suite green (#17). |
| **metric** | Per-requirement pass rate, 1.000 required. Unit: fraction. |
| **baseline** | 1.000, **asserted** as a gate rather than a measured distribution — these are invariants, and a partial pass is a defect, not a lower score. The baseline-after reference point is stated in the design: 1537 passed / 13 skipped after `8e3d7a4`. |
| **limitations** | 15 of 17 are fixture-based, so they verify the *guard fires*, not that a human editor would agree with its verdict. #16 in particular — *"payoff without its setup"* — has no editorial ground truth; the test asserts the flag appears, not that the flagged case is genuinely a missing setup. Requirement #13's negative assertion (no edge between topically-similar passages with no licensing cue) is the strongest of the set, because it is falsifiable without judgement. |
| **cost** | **[measured by the existing suite]** seconds on 4 CPUs, no decode. |

---

### B8 — Memory reference integrity (no dangling `connects_to`)

**The gap:** G-D. A shared memory that dangles a reference is worse than no
memory. This is the property test the design invites
(`docs/story-memory-design.md:363-366`), stated here as a benchmark so it is not
dropped.

| Field | Value |
|---|---|
| **dataset** | Every exported memory artefact: `editorial_corpus.json` and `story_view.json` from all corpus runs, plus future `VideoMemory` stores. |
| **population** | Every exported moment, every `connects_to` / `unit_indices` / `beat_indices` reference, every timestamp. Census. |
| **metric** | (a) fraction of references that resolve to a record present in the same file; (b) fraction of moments whose `[start, end]` lies within `[0, source_duration]`; (c) fraction of `thread_id`/`topic_id`/`beat_id` joins that are bidirectionally consistent with the index tables. Unit: fraction. |
| **baseline** | 1.000 for all three, **structural** — a dangling reference or an out-of-range moment is a defect, full stop. No measured distribution is wanted. |
| **limitations** | Integrity is not truth. A reference can resolve to the wrong record and pass. This benchmark catches corruption, not hallucination; the latter needs G3 or a human (B11). |
| **cost** | **[measured]** <1 s per artefact at the §3 population, single CPU, no decode. On the two on-disk corpora today, run informally as part of B1's round trip: all 83 units, 57 links and 56 threads resolved, and all spans lay within the 959.7 s / 625.6 s transcript span. |

---

### B9 — Perception → memory ingest coverage

**The gap:** G-C. Before the memory layer can be scored on fidelity, it has to
receive the signals at all.

| Field | Value |
|---|---|
| **dataset** | A single corpus run executed with `--audio-analysis` enabled (the switch that currently defaults to `False`, `docs/memory-perception-inventory.md`), on one of the two sources with media. |
| **population** | The six documented perception signals: (1) face/subject tracks from `engine/reframe.py` `_perceive_window`; (2) shot/scene classification from `engine/reframe_scenes.py` `analyse_scene`; (3) speech regions from the `engine/vad.py` chain; (4) pause/dead-air structure from `engine/pause_classifier.py` + `engine/audio_fusion.py` `AudioTruth`; (5) story structure from `engine/corpus.py` `build_corpus`; (6) the `selekyt/pipeline.py` persistence list. |
| **metric** | `signals_present_in_memory / 6`, per signal, with the signal's record count and time base. Unit: fraction plus counts. |
| **baseline** | The current state, **measured from the inventory**: 2 of 6 — Signal 5, and `coverage` as an aggregate only. That is the floor the benchmark beats. The target of 6 is **asserted**, and it is asserted against the inventory's own design, not against an external standard. |
| **limitations** | **Presence is not fidelity.** A signal reaching disk in the wrong units, on the wrong time base, or with its confidence stripped passes this benchmark. Fidelity needs a per-signal ground truth that does not exist (G-D). This is a coverage ladder, and it must be read as one. |
| **cost** | Requires `engine/memory/ingest` to exist — **it does not** (`docs/verify-repair-design.md:172`). The cost of the benchmark is therefore the cost of building the ingest layer, which is out of scope for a document. The measurement itself, once ingest exists: one corpus run. Transcription RTF is 0.1505 **[code]** (`docs/EVALUATION.md`), so a 17-minute source costs ≈2.5 CPU-minutes of transcription plus perception; editorial is 0.00241 RTF. No decode beyond what the run already does. |

---

### B10 — Retrieval at hour scale — **CANNOT BE STATED**

**The gap this would fill:** G-F, the single largest gap in the framework. It is
declined, and the blockers are concrete.

| Field | Value |
|---|---|
| **dataset** | **Cannot be stated.** No corpus source reaches one hour (longest: 55.5 min, `docs/CORPUS_BENCHMARK_STATUS.md:181`), and only 26.4 minutes of media are on disk (§3). |
| **population** | **Cannot be stated.** The available population is 83 units over 1585.3 s — two orders of magnitude below the 1–3 h target. There is no hour-scale population to sample. |
| **metric** | **Cannot be stated as a quality metric.** The only computable surrogate is cost: index build and per-query latency extrapolated from B2's measured 1.9 ms / 61 units and 3.0 ms / 83 queries, which is linear in corpus size to within the measured constants. That is a *performance* statement, and it is the only hour-scale statement this framework will make. |
| **baseline** | **Cannot be stated.** No measured baseline exists at hour scale, and the paper's mIoU figures are not a valid baseline here — §6 labels what does and does not transfer. |
| **limitations** | The limitation is the whole benchmark: hour-scale retrieval quality is unmeasurable on this corpus, and saying otherwise would be manufacturing a metric. |
| **cost** | N/A — the benchmark cannot run. |

**What would make it computable, minimally:** (1) one rights-clear source between
60 and 180 minutes, committed under a manifest axis; (2) roughly 1 GB of free
disk for its media — the host has **1.3 GB** today **[measured]**, so this is
feasible but leaves no headroom, and it is the binding constraint, not
computation; (3) a ground truth for what the right moments *are* in that source —
even 20 human-labelled editorial moments would give recall and mIoU a denominator
that is not self-constructed; (4) an index module, which does not exist. Items (3)
and (4) are the real blockers; (1) and (2) are solvable today.

---

### B11 — Editorial-reasoning quality against human judgement — **CANNOT BE STATED**

**The gap this would fill:** G-A and G-D together. This is the benchmark that
would settle whether the intelligence layer is any good, and it is the one the
framework most firmly declines to invent.

| Field | Value |
|---|---|
| **dataset** | **Cannot be stated.** No corpus clip has been judged by a human. `docs/product-gap-map.md:117` records the human-editorial-judgement row with verdict *"absent, and the map says so"* and the plain statement *"no human has judged any corpus clip."* **[code]** |
| **population** | **Cannot be stated.** The instrument is designed but unexecuted: `docs/HUMAN_EDITORIAL_EVALUATION.md` carries **STATUS: DESIGNED, NOT RUN** — *"no participant contacted, founder approval for external contact not given"*; `docs/BLINDED_PROTOCOL.md` is superseded by it and carries the same standing rule: no editor will be recruited without explicit founder approval. **[code]** |
| **metric** | **Cannot be stated.** No rating exists, no score exists, no time has been measured, and no pilot has been conducted — the protocol document states each of these explicitly and contains no number implying otherwise. **[code]** |
| **baseline** | **Cannot be stated.** With zero ratings there is no distribution to compare against, and the protocol's own design forbids the alternative: mechanical and editorial scores *"can never be combined"* as a structural invariant, so a mechanical audit cannot stand in for a human one. **[code]** |
| **limitations** | The absence is total, and its effect is specific: **any claim that the intelligence layer selects the right moments, or links the right stories, is unsupported by evidence in this repository.** The corpus's own most damaging finding is the proof of why: two of seven "OK" sources had transcripts the repository cannot stand behind, and every mechanical check passed both, because the checks compare captions against the transcript rather than against reality (`docs/CORPUS_RESULTS.md`, `docs/HUMAN_EDITORIAL_EVALUATION.md:34-40`). A mechanical quality claim on the reasoning layer would repeat exactly that failure. |
| **cost** | N/A — not executable. The designed protocol's cost is stated in `docs/HUMAN_EDITORIAL_EVALUATION.md`, not here; this framework does not restate it. |

**What would make it computable, minimally:** the instrument already exists and is
executable *"the moment a participant exists"* (`docs/HUMAN_EDITORIAL_EVALUATION.md`).
The blocker is not measurement design — it is founder approval for external
contact, and then a participant. Until then, the framework treats every editorial
quality claim as **UNCHECKED**, which is the honest label, not a low score.

---

### B12 — Non-roman-script retrieval and grounding — **CANNOT BE STATED**

**The gap this would fill:** G-E, and the code-switching/script-instability
problem `docs/telugu-language-ir-spec.md` documents.

| Field | Value |
|---|---|
| **dataset** | **Cannot be stated.** Both Telugu corpus sources are unusable as a retrieval population: `telugu-launch` returned `NO_SPEECH_DETECTED` with 0 shipped clips (`docs/CORPUS_RESULTS.md:37`), and `telugu-tutorial` no longer ships (confidence 0.319 vs the 0.75 floor, `docs/CORPUS_BENCHMARK_STATUS.md:141-151`). The Telugu WER of 91.54% (medium) / 151.08% (small) was measured on SLR66, not on corpus media **[code]** (`docs/telugu-language-ir-spec.md`). |
| **population** | **Cannot be stated.** Zero usable non-roman-script units with word timestamps exist in a shipping corpus run. The `hi` and `hu` sources have no measured WER at all (§2, G-E), so their transcripts have an unquantified error floor. |
| **metric** | **Cannot be stated.** A retrieval or grounding metric over a transcript with ≥91% WER measures the ASR, not the index — and `docs/telugu-language-ir-spec.md` §7.3 states the same refusal for its own domain: *"No measurement of code-switch detection quality, and no invented one."* |
| **baseline** | **Cannot be stated.** No measured baseline exists for retrieval in any non-roman script on this host. |
| **limitations** | This is the gap with the clearest engineering consequence and the least ability to close it here: the language IR that would make Telugu tractable (`WordSpan`, `ScriptSpan`, `CanonicalForm`, `TransformRecord`, `LanguageIR`) is specified, not built, and script purity measured 56.4% / 68.7% on Telugu output with 23.9% / 13.0% Devanagari contamination **[code]** (`docs/telugu-language-ir-spec.md`). Lexical retrieval over a script-unstable transcript is not measuring what it appears to measure. |
| **cost** | N/A — no population. |

**What would make it computable, minimally:** one non-roman-script source whose
transcript passes the 0.75 confidence floor with per-word timestamps — which
requires either a better Telugu/Hindi model than `small`, or the language IR that
canonicalises script before indexing. Neither is available on this host, and
downloading a model is blocked by disk (1.3 GB free, **[measured]**).

---

## 5. What the framework deliberately does not define

Each of these would be a plausible-sounding benchmark and each is refused:

| Refused benchmark | Why |
|---|---|
| Retrieval precision@K against "relevant" moments | Defining relevance requires a judge. There is none (B11). A self-labelled relevance set is the circularity B2 already exhibits, and presenting it as precision would be manufacturing a metric. |
| Story-link accuracy (are these really setup/payoff pairs?) | No editorial ground truth. `engine/threads.py` infers links only from linguistic markers — question/answer, problem/solution, causal connectives, callback, contrast turn — and explicitly *"does not guess at themes the speaker never named"*; the unlicenced relationship kinds (`supports`, `explains`, `foreshadows`, …) are listed as `unsupported_relationships` rather than emitted (`docs/story-memory-design.md:286-296`). Accuracy of the emitted set is unverifiable without a judge; the *honest* benchmark is the negative one, requirement #13 (B7). |
| Grounding visual quality (does the reframed frame look right?) | No visual channel on this model, and no post-render reframe verification exists (`docs/memory-perception-inventory.md`, unperceived item 10). The framework will not assert a visual metric it cannot compute. |
| Evidence-graph confidence calibration | G3 is unpopulated — no `Evidence` exists (`docs/verify-repair-design.md:176`). Calibration needs emitted confidences and outcomes; there are none. |
| Speaker-aware retrieval | `has_speaker_labels` is `False` on both available corpora (§3) and there is no diarisation in the pipeline (`docs/memory-perception-inventory.md`, unperceived item 1). |
| Semantic/embedding retrieval quality | Deliberately deferred by the research conclusion: `sentence-transformers` is a trial candidate whose benefit is *"not yet measured"* (`docs/rd-landscape.md`), and adopting it trades the lexical path's determinism and zero-download property. Measuring its quality before deciding to adopt it would require downloading it — blocked by disk. |

---

## 6. The retrieval operating point, and what transfers

The operating point is **researched and is not reinvented here**. It is quoted as
given, with its provenance labelled.

**The operating point** **[paper]**: lexical/temporal indexing over the
word-timestamped transcript as the available channel (no visual embedding model
exists on this host — `docs/rd-landscape.md` and `docs/product-gap-map.md`);
**K = 3** candidates; **NMS at 60 s**; **union-then-ground**. Alternative: K = 10
raises recall to 96.7% but mIoU falls to 0.295.

**The comparison figures** **[paper]**: mIoU **0.053** for a Video-LLM pass alone
over the whole video; mIoU **0.354** for retrieve-then-ground, at **6.7× lower
cost** than a single whole-video Video-LLM pass.

**Provenance of those figures, stated plainly.** They come from the
ExtremeWhenBench paper (**arXiv 2606.12300**). They are **not** in this
repository's code or documents — verified by search across `docs/`, `engine/`,
`tools/` and `corpus/` on 2026-09-29 — and they are **not** reproducible on this
host: the host has 4 CPUs, no `torch`, no GPU and 1.3 GB of disk, so a Video-LLM
pass over a whole video cannot be run here at all, which means the 6.7× cost
comparison has no denominator on this machine. The figures are quoted from the
retrieval-layer design brief for this same project, and they are labelled
**[paper]** everywhere they appear.

**What does transfer:**

- **The shape of the K trade-off.** Retrieval width buys recall and costs
  localisation precision. That is a structural property of union-then-ground, and
  B2's own measured mIoU (0.045 at K = 3 against narrow targets) is the same
  trade-off visible at a different scale. The K = 3 / K = 10 choice should be made
  per target-span width, not copied.
- **The architectural claim.** Index → retrieve → ground is cheaper than
  whole-video inference, and on this host it is not merely cheaper — it is the
  *only* option, since whole-video inference is impossible. The 6.7× figure is
  therefore unnecessary to SeleKyT's case and should not be cited as if it were
  measured here.
- **NMS at 60 s as a deduplication scale.** It is a sensible constant given the
  minute-scale span of a story unit (§3's units and threads are second- to
  minute-scale), and it is adjustable without architectural change.

**What does not transfer:**

- **The absolute mIoU values.** They measure a Video-LLM's moment localisation
  against human-annotated spans. SeleKyT has no Video-LLM, no human annotations,
  and a lexical index whose target spans are one to two orders of magnitude
  narrower. B2's measured mIoU of 0.045–0.109 is **not** 0.354, and the
  difference is not a quality gap — it is a difference in what is being measured.
  The two numbers must never be placed in the same table as if comparable.
- **The query modality.** The paper's queries are natural language; SeleKyT's
  are term bags scored by `weighted_containment` (`engine/lexical.py:81`). A
  natural-language query exercises semantic matching that the lexical channel
  structurally cannot perform, so the paper's retrieval quality is an upper bound
  SeleKyT's channel does not approach — and measuring that gap requires the
  embedding model the research deliberately has not adopted.
- **The cost ratio's denominator.** Video-LLM inference does not exist on this
  host, so "6.7× cheaper" is a claim about a comparison that cannot be run. On
  this host the honest cost statement is B2's measured one: index build 1.2–1.9 ms,
  83 queries in 1.0–3.0 ms, no decode.

---

## 7. Honest limits

Stated plainly, because the framework's usefulness depends on these being
believed.

**No human has judged any corpus clip.** `docs/product-gap-map.md` records this
in its human-editorial-judgement row, and `docs/HUMAN_EDITORIAL_EVALUATION.md`
confirms the protocol is designed but unexecuted, with founder approval for
external contact not given. **Effect:** every quality claim about the
intelligence layer in this repository is a mechanical self-consistency claim.
B2's recall of 1.000, B7's 17/17, B6's structural bounds — none of them is
evidence that the system makes editorially good choices. They are evidence that
it behaves as designed.

**There is no editorial ground truth.** G1 and G2 are populated (the edit plan
and the word-timestamped transcript); G3 — the `Evidence` layer that would let a
verify stage ask *"is this whole clip backed, or does part of it sit in a gap I
have no support for?"* (`docs/verify-repair-design.md:160-167`) — is specified
and unpopulated. **Effect:** the framework's evidence-backed benchmarks
explicitly degrade to word coverage (B3, B5) rather than silently pretending
evidence exists. When G3 is populated, B3 and B5 re-run against it unchanged in
shape; that is by design.

**The only retrieval benchmark computable today is circular, and its result says
so.** B2's recall@3 of 1.000 is achieved with a query built from the same idf
weighting the retrieval scores with. The framework reports it as a **regression
guard**, not a quality metric, and reports alongside it the leave-one-out variant
that returned 0/83 — the honest signal that the population has no restatements
and that the metric has no discriminating power on it.

**Two of the corpus's four languages have no measured error floor.** `hi` and `hu`
are absent from the language-fitness table **[measured]**, while `te` is measured
at 91.54% WER (medium) — nearly 4× the 25% usable bar. **Effect:** any retrieval
or grounding metric over a non-English transcript inherits an error that is
either unquantified (`hi`, `hu`) or known to be catastrophic (`te`). B12 declines
for this reason.

**The corpus cannot measure hour scale, and neither can this host's disk.** No
source reaches one hour; 26.4 minutes of media survive; 1.3 GB is free. **Effect:**
B10 is declined, and the framework will not extrapolate a quality metric across
two orders of magnitude of unmeasured scale. What *is* stated is performance
linearity from B2's measured constants, which is a different and weaker claim.

**What the mechanical layer is good for, in one line.** Reproducibility, gate
behaviour, determinism, structural integrity, and regression detection. That is
what a benchmark of a deterministic engine can measure — the same conclusion
`docs/CORPUS_BENCHMARK_STATUS.md:238-240` reaches for the editorial layer, applied
here to the intelligence layer. Where the framework reports a number, it reports
that kind of number.

---

## 8. Benchmark index

| # | Benchmark | State | Headline number | Anchored gap |
|---|---|---|---|---|
| B1 | Memory round-trip determinism | **defined, run** | 1.000 on both corpora (4.4–17.9 ms) | G-D |
| B2 | Known-item lexical retrieval | **defined, run** | recall@3 1.000 *(circular)*; mIoU 0.045–0.109; leave-one-out 0/83 | G-A |
| B3 | Grounding word-boundary conformance | **defined, run** | 92.7% / 51.6% within 1e-6 s; farthest 0.082 / 0.418 s | G-A |
| B4 | Interval-algebra adversarial regression | **defined** (20 cases exist) | 20 cases, 4 S1 / 10 S2 / 5 S3 / 1 clean | G-D |
| B5 | Real-output precondition rate | **defined, run** | precondition met on 52–93% of bounds; zero-width 0 | G-D |
| B6 | Story-graph structural bounds at scale | **exists** (`tests/test_story_bench.py`) | edges ≤ 6·U; build 1.878 s @ 2000 units | G-B |
| B7 | Story → edit-plan behaviour coverage | **exists** (`tests/test_story_memory.py`) | 17 behaviours, 1.000 required | G-B |
| B8 | Memory reference integrity | **defined** | 1.000 required (structural) | G-D |
| B9 | Perception → memory ingest coverage | **defined, blocked on ingest** | floor 2/6 signals | G-C |
| B10 | Retrieval at hour scale | **DECLINED** | no source ≥ 1 h; 1.3 GB disk; no ground truth | G-F |
| B11 | Editorial quality vs human judgement | **DECLINED** | no human has judged any clip; protocol DESIGNED, NOT RUN | G-A, G-D |
| B12 | Non-roman-script retrieval | **DECLINED** | 0 usable non-roman units; `te` WER 91.54% | G-E |

**Defined:** B1–B9. **Declined with named blockers:** B10–B12. **Reinvented:
nothing** — B6 and B7 already exist as committed tests and are cited rather than
restated; the retrieval operating point is quoted from research rather than
re-derived.

---

## 9. What a reader may and may not conclude

**May.** That the memory layer's value types are deterministic and round-trip
clean (B1, measured). That the lexical index is deterministic and its machinery
locates a passage from its own rare terms without error on this population (B2,
measured, and circular by construction). That the memory layer's interval bounds
are a mixture of `word_aligned` and `character_proportional` provenance, and that
a word-anchoring assumption fails on the majority of units in both available
corpora (B3, measured). That the preconditions for three S1/S2 interval-algebra
defects are present in real outputs on 52–93% of bounds, so those defects are
live hazards rather than theoretical ones, and that zero-width spans are currently
absent (B5, measured). That the story graph's growth is bounded structurally and
its build time is mildly super-linear, reported as measured rather than as an
exponent (B6, measured by the existing bench).

**May not.** That B2's recall of 1.000 means retrieval works — it means the index
is deterministic on a self-constructed query over a population with no
restatements. That B2's mIoU is comparable in any way to the paper's 0.354 (§6).
That B7's 17/17 means the story links are editorially correct — it means the
guards fire as designed, and no human has judged whether they should have. That
any benchmark in this framework measures editorial quality. That hour-scale,
non-roman-script, or human-judged performance can be inferred from any number
here — B10, B11 and B12 are declined, and their blockers are disk, approval and
transcription quality, in that order.

**May not, and about the disk figure:** `docs/product-gap-map.md` states 2.1 GB
free at the end of its pass; this framework measured **1.3 GB** today. The 2.1 GB
figure is stale, and 1.3 GB is the one that binds — it is the single number in
this document that moved between the last document and this one, and it is what
makes B10's media blocker and B12's model-download blocker binding rather than
hypothetical.
