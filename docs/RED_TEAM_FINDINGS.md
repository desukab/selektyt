# RED TEAM FINDINGS — SelekyT

Agent 8 (red team), 2026-09-19. Mission: try to disprove SelekyT's product
claims. Every verdict below is backed by a command I actually ran or a file I
actually read on this host. Nothing here is speculative, and nothing is
fabricated.

Environment facts confirmed first: `.venv/bin/python` is the only interpreter
(bare `python` does not exist); `ffprobe`/`ffmpeg` at `/usr/bin`; 4 cores, no
GPU, `DISPLAY` is empty and no X server or Xvfb binary exists on this host;
3.9 GB free of 29 GB (87% used). HEAD is `d5f270f`, 174 commits, and the
working tree is **not** clean — 12 modified tracked files
(`engine/nle_export.py`, `tests/test_nle_export.py`, `tools/corpus_run.py`,
`tools/nle_check.py` and 8 royal-society-cloke artifacts) plus untracked
`docs/PRODUCT_WORKFLOW_AUDIT.md`, `tests/test_corpus_run.py`,
`docs/RED_TEAM_FINDINGS.md`. The repo is live; other agents are editing it
during this session.

**The repo advanced mid-audit.** By the time this document was final, HEAD had
moved to `6345270` "Fix every corpus EDL importing with all clips offline"
(177 commits) and the then-uncommitted files named above had been committed by
another agent. Every measurement below was taken against the state named at
the point it was made, and the one finding that another agent's commit could
have invalidated (R1, the editorial-audit pass rate) was **re-verified under
the new HEAD** and is unchanged: 4 PASS / 3 FAIL of 7. The only file this audit
modified is this one, plus one retained reproduction artefact
(`corpus/wer/runs/redteam-small-verify.json`, the independent WER re-run in
S2). `engine/caption_director.py` was temporarily edited to reproduce the
pre-fix behaviour for R14/S9 and restored; `git diff` confirms it matches HEAD.

---

## RANKED LIST OF CLAIMS THAT DO NOT SURVIVE SCRUTINY

### R1. "7 clean through both audits" — FALSE, and the doc contradicts itself

- **Claim:** `docs/AI_EXECUTION_STATE.md:341` — "the machinery is sound across
  containers, frame rates, aspect ratios and languages (11 sources, **7 clean
  through both audits**, determinism byte-identical across independent
  downloads)". Repeated at `:366` — "(exceeded: 11 sources, **7 clean**)".
- **What I verified.** I ran the editorial audit on all seven OK sources
  myself:
  ```
  nasa360-ep5            ALL CHECKS PASS
  emwcon-panel-2018      FAILURES PRESENT  (clip 01: end 67.09s splits ' was,'/' at';
                                          clip 05: end 225.13s splits ' comes'/' I')
  pentagon-esper-briefing FAILURES PRESENT (clip 02: end 461.50s splits ' them'/' What';
                                           end 472.82s splits ' compound'/' Also,')
  royal-society-cloke    ALL CHECKS PASS
  ooi-vertical           FAILURES PRESENT  (clip 01: end 115.86s splits ' arrays,'/' traveling')
  hadhazy-protest        ALL CHECKS PASS
  telugu-tutorial        ALL CHECKS PASS
  ```
  So `tools/editorial_audit.py` **fails on 3 of the 7** (re-verified under the
  final HEAD `6345270`: 4 PASS / 3 FAIL — `emwcon-panel-2018`,
  `pentagon-esper-briefing`, `ooi-vertical` failing; the same three as under
  the earlier HEAD). Only 4 of 7 are clean through it. Separately, the
  `nle_check.py` **relink** checks only actually execute on
  `royal-society-cloke` — that is the only source whose media was retained
  (`corpus/media/royal-society-cloke.webm`, 85,493,260 B). On the other six the
  source media was deleted by the run, so those relink checks are NOT RUN.
  "7 clean through both audits" is false on both halves.
- **The self-contradiction.** The same document, ~90 lines earlier, says at
  `:258-260`: "`tools/editorial_audit.py` fails on **four of the seven**
  (`emwcon-panel-2018`, `ooi-vertical`, `pentagon-esper-briefing`,
  `royal-society-cloke`)". So `AI_EXECUTION_STATE.md` asserts both "fails on
  four of the seven" and "7 clean through both audits". (The four-figure was
  right at first-audit time; royal-society-cloke has since been fixed and now
  passes, so the current truth is 3 of 7.)
- **What would make it true.** Re-run `tools/editorial_audit.py` and
  `tools/nle_check.py --keep-media` over all seven OK sources and record the
  actual per-source result in the doc instead of an aggregate adjective.
  Three of the seven need real re-runs with the word-split fix, which
  `docs/CORPUS_RESULTS.md:97-99` already says and does not overclaim.

> **Postscript, 2026-09-22: this finding's three failures were false
> positives, and R1's conclusion is withdrawn.** The finding above was
> accurate as a measurement — the audit really did report those three sources
> as FAIL at `6345270` — but the check itself was wrong. It derived sentence
> membership by splitting the candidate's own `text` on terminal punctuation,
> and that text is `" ".join(segment.text)`, so transcript segments carrying
> no terminal punctuation merge into one "sentence"; a clean cut between two
> such utterances then reads as a mid-sentence split. Every one of the three
> failures above was that case: a cut between two transcript-adjacent words
> with 0.5–1.4 s of removed gap and **no word lost**. The check now fires only
> when a sentence loses a word to a boundary, and all seven sources audit
> clean. The 4-of-7 pass rate this finding reported was real at the time and
> is not the pass rate now; the two `nle_check` relink halves of the finding
> stand unchanged, because they are a property of which media the repo
> redistributes and not of the check. See `docs/TECHNICAL_AUDIT.md`, "CI does
> not run the tools", for the diagnosis and the mutation record.

### R2. The corpus failure rate is 36%, and the permitted "honest" statement hides it

- **Claim:** `docs/TIME_SAVINGS.md:752-755` (§10.11, the *permitted* statement)
  — "it turned **117.8 minutes of source video across 11 sources** into 27
  selected clips — 18.3 minutes of delivered video in two aspect ratios — in
  **38.8 minutes** of wall time including downloads, a 0.33 real-time factor."
- **What I verified.** Recomputed from `corpus/results.jsonl`:
  ```
  7 OK : source=117.77 min  wall=38.84 min  RTF=0.3298  -> 27 clips
  11 all: source=131.35 min  wall=42.21 min  RTF=0.3214
  failed: 4 of 11 = 36.4% of sources, 202.2 s of wall, 0 clips delivered
  ```
  The 117.8 min, the 38.8 min and the 27 clips are all **seven-source**
  numbers. The sentence says "across 11 sources" while excluding 4 of the 11
  from both numerator and denominator — and those 4 are the ones that
  produced nothing. `docs/TIME_SAVINGS.md:366-367` does the same ("an
  end-to-end wall clock per source over 11 sources" per the §10.12 summary).
  Nowhere in §10 is the failure rate stated. It is first-order: on an
  arbitrary source this pipeline has roughly a 1-in-3 chance of delivering
  nothing, which zeroes any time saving on that source.
- **What would make it true.** Say "across 11 attempted sources, 7 produced
  deliverables" and state the 36.4% failure rate alongside the RTF.

### R3. "Three of the seven OK sources are unusable" — arithmetic error, in two docs

- **Claim:** `docs/AI_EXECUTION_STATE.md:213-214` — "Three of the seven 'OK'
  sources — Telugu, Hungarian, Hindi — produced transcripts that are
  fluent-looking phonetic noise." `docs/HUMAN_EDITORIAL_EVALUATION.md:38` —
  "It also reports that **three of those seven 'OK' sources are not usable**".
- **What I verified.** `corpus/results.jsonl` shows
  `hindi-baiga-interview` = `NO_STORIES_PASSED`, `exit_code 1`, `clips 0`. It
  is one of the **four sources that did not produce deliverables**, not one of
  the seven OK sources. The seven OK are nasa360-ep5, emwcon-panel-2018,
  pentagon-esper-briefing, royal-society-cloke, ooi-vertical, hadhazy-protest,
  telugu-tutorial. So it is **two** of the seven OK sources that produced
  phonetic noise. `docs/TIME_SAVINGS.md:423-425` gets this right ("two of the
  seven 'OK' sources"), so the error is not repo-wide — but the two documents
  that state it most prominently (the one addressed to evaluators and the one
  addressed to the next session) both have it wrong.
- **Consequence.** `docs/HUMAN_EDITORIAL_EVALUATION.md:782-784` builds a gate
  (the T-gate) on this list and names `hindi-baiga-interview` as a source "the
  corpus has completed successfully" — which it did not.
- **What would make it true.** Replace "three" with "two" and drop
  `hindi-baiga-interview` from the list of OK-but-unusable sources; it belongs
  in the failure column.

### R4. "`tools/editorial_audit.py` passing on all seven" — FALSE, misquotes its own source

- **Claim:** `docs/HUMAN_EDITORIAL_EVALUATION.md:36-38` — "`docs/CORPUS_RESULTS.md`
  (2026-09-19) reports 7 of 11 corpus sources producing deliverables, with
  `tools/nle_check.py` at **0 FAIL** and `tools/editorial_audit.py` passing on
  all seven."
- **What I verified.** Both characterisations are false as descriptions of
  `CORPUS_RESULTS.md`. That file says at `:26-33` "The honest summary is not
  '0 FAIL everywhere': the interchange audit reported relink failures on all
  seven, because `tools/corpus_run.py` deletes the source media after each
  run" and at `:35` "`tools/editorial_audit.py` **failed on four of the
  seven**". My own re-run (see R1) shows 3 of 7 failing today. Note the irony:
  this protocol document's entire thesis is that mechanical audits are
  self-referential and cannot be trusted, yet it inflates the audit pass rate
  above what its own cited source reports.
- **What would make it true.** Quote `CORPUS_RESULTS.md` accurately: 0 FAIL on
  the checks that ran, with relink checks NOT RUN on six of seven, and an
  editorial audit that fails on three of seven.

### R5. The "73× is arithmetically infeasible, ceiling 36×" result rests on double-counting a reframe

- **Claim:** `docs/TIME_SAVINGS.md:474-477` (§10.5) — "The delivered set for
  the reference source is **198.307 s = 3.305 min** of video ... So Arm B
  human time ≥ 3.305 min even in the limit of zero corrections". And
  `:500-507` — "73× cannot be achieved ... **The 73× figure must not be quoted
  again** ... The defensible ceiling ... is 36×." Reissued as a prohibition at
  `:775` — "'73×', or any figure above 36×, quoted as a speedup, in any
  document, deck or pricing page."
- **The weakest assumption, and I verified it.** The floor charges the
  reviewer **both presets, in full, at 1×**. I ffprobe'd both preset trees for
  every source: facebook and shorts are **per-clip duration-identical** on all
  seven sources (nasa360: 24.858192/17.083750/16.449783/14.014014/17.651000/
  9.075742 in *both* trees; identical to 0.02 s on every source). The 9:16
  output is an anamorphic recode of the same six selections at the same in/out
  points (308×486, SAR 8:9, DAR 1232:2187 — verified). The second preset
  carries **no new selection, timing or caption information**; only the crop
  differs. The floor for reviewing the *editorial content* is one preset =
  99.154 s = **1.653 min**, not 3.305 min.
  Recomputing §10.5's own table on a one-preset floor at the 2 h baseline:
  | Floor assumption | Floor | Ceiling @ 2 h |
  |---|---|---|
  | Both presets at 1× (the doc's) | 3.305 min | 36.3× |
  | **One preset at 1× (what the artifacts support)** | **1.653 min** | **72.6×** |
  The headline figure is 73×; at a one-preset floor 73× goes from
  "arithmetically infeasible" to "feasible with zero correction". The §10.11
  prohibition is therefore not an arithmetic impossibility — it is a
  *conditional* conclusion that depends on an unstated review protocol
  (both presets played end-to-end at 1×).
- **Three further weaknesses in the same floor:**
  1. **Playback speed is inconsistent across the document.** §10.5 floors
     *output review* at 1× while §10.6 prices a *source logging pass* at 2×.
     No justification is given for why an editor who logs a tape at 2× would
     review 3 minutes of finished shorts at 1×. Apply 2× and the ceiling
     doubles.
  2. **§10.5's "defensible ceiling" is not defensible even on its own terms.**
     Its own table at `:490` labels 36× "impossible in practice — allows
     literally zero correction". A ceiling no achievable scenario reaches is
     the no-correction limit, not a defensible ceiling.
  3. **The bound is source-specific but quoted as general.** `:775` forbids
     "any figure above 36× ... in any document", but for `telugu-tutorial`
     (23.59 s delivered) the floor is 0.39 min and the 2 h ceiling is ~300×.
     The 36× bound has no basis outside one source at one baseline.
- **What would make it true.** Restate §10.5/§10.11/§10.12 as: "under a
  review protocol that plays both presets end-to-end at 1×, the ceiling at a
  2 h baseline is 36×; under a one-preset protocol it is 72.6×, at which the
  headline 73× is feasible but leaves no correction budget."

### R6. "On sources shorter than about four minutes the pipeline runs slower than real time" — contradicted by the doc's own data

- **Claim:** `docs/TIME_SAVINGS.md:758-759` (§10.11 permitted statement) and
  `:419-421` (§10.3) — "The crossover — the source length below which the
  pipeline is slower than real time — is **252 s ≈ 4.2 minutes**. Sources
  shorter than that ... cost more machine time than they contain content."
- **What I verified.** I reproduced the fit exactly
  (`elapsed = 225.2 + 0.1067 × duration`, R² = 0.390, crossover 252.1 s — byte
  for byte what the doc reports). But the *conclusion* is contradicted by the
  table it summarises. Of the two sources below the 252 s crossover:
  ```
  hadhazy-protest   dur= 126.0  RTF=1.140  slower than real time
  ooi-vertical      dur= 152.0  RTF=0.922  FASTER than real time
  ```
  The intercept-driven extrapolation mispredicts `ooi-vertical` by 42%
  (predicts 241.4 s, actual 140.1 s). Half the sub-crossover evidence
  contradicts the conclusion.
- **What would make it true.** Either drop the crossover claim or state it as
  "the fit's crossover is 252 s, but only one of the two sources below it was
  actually slower than real time; n=7 does not support the extrapolation".

### R7. "Three of seven sources ran slower than real time" and the "32%" — both wrong as stated

- **Claim (a):** `docs/TIME_SAVINGS.md:406-408` — "RTF spans 0.180 to 1.746 —
  a 9.7× spread — and **three of seven sources ran slower than real time**."
- **What I verified.** Recomputing RTF from `corpus/results.jsonl`: RTF > 1.0
  for exactly **two** sources — `hadhazy-protest` (1.140) and
  `telugu-tutorial` (1.746). `ooi-vertical` is 0.922, i.e. *faster* than real
  time. The claim only reaches three by substituting §10.7's fetch-free OOI
  re-run RTF (1.589) for the table's own 0.922 — a different measurement from
  the table being summarised.
- **Claim (b):** `docs/TIME_SAVINGS.md:432-435` — "the two unusable sources
  consumed **32% more machine time per delivered second** than the usable
  ones — 592.9 s of machine time for a single 23.6 s clip in the Telugu case."
- **What I verified.** All-7 average = 2.123 s machine per delivered second;
  usable-5 average = 1.614 s; ratio 1.316 → **31.6%**. That is the
  *all-7-average-versus-usable-5-average* gap, not a property of the two
  unusable sources. Those two themselves run at
  (592.9+143.6)/(23.59+86.26) = 736.5/109.85 = **6.704 s/s = 4.15× the usable
  rate (+315%)**. The sentence attaches the wrong number to the wrong set and
  *understates* the bad sources by roughly 10×. (592.9 s for 23.6 s is the
  accurate and damning part; the "32%" label on it is not.)
- **What would make it true.** "Two of seven" for (a); and for (b) "the two
  unusable sources ran at 6.7 s of machine time per delivered second, 4.15×
  the usable rate".

### R8. §10.7's component table double-counts, and its causal diagnosis is unsupported

- **Claim (a):** `docs/TIME_SAVINGS.md:573-580` — the component decomposition
  rows Cold model load 1.60 s / Warm transcription 28.30 s / In-run
  transcription 40.9 s / Decode+editorial+render "~200.6 s remainder" / total
  241.55 s, with a "Share of fetch-free wall" column (0.7% / 11.7% / 16.9% /
  83.1% / 100%).
- **What I verified.** The additive rows sum to 513.0 s and the shares sum to
  212.4%. The "~200.6 s remainder" is 241.55 − 40.9, so it *already contains*
  the 1.60 s load; the 28.30 s row is a separate warmed microbenchmark, not a
  component of the 241.55 s at all. The mutually exclusive decomposition is
  1.60 + 40.9 + 199.05 = 241.55 s. The headline **83.1% share is itself
  correct** (200.65/241.55 = 83.06%) — the defect is presentational, but it
  makes the table read as though the components sum to 2.1× the wall.
- **Claim (b):** `:562-566` — "The cause is host contention: the 185% CPU
  figure on a 4-core host means the run did not get the machine to itself ...
  `/proc/loadavg` during the measurement read 0.94 / 2.54 / 2.41."
- **What I verified (logic, not the measurement).** 185% CPU on a 4-core box
  is a *utilisation* figure for a multithreaded int8 + AV1-decode job, not
  evidence of contention. A 1-minute load average of **0.94 on a 4-core host
  is low**, not high. And the corpus runs were strictly serial, so the re-run
  was not competing with them. n=2, no repetition, no control arm. The 1.72×
  gap is real; the diagnosed cause is uncontrolled.
- **Also:** `:591` quotes `docs/BUSINESS_MODEL.md` §9 as "3–10× unmeasured";
  the source text says 4K/H.265 "would raise decode and I/O cost several-fold"
  — the precision "3–10×" is not in the source.
- **What would make it true.** Rebuild the table as mutually exclusive
  components (1.60 + 40.9 + 199.05), drop the 28.30 s row to a clearly
  labelled separate microbenchmark, and downgrade the contention claim to
  "uncontrolled, n=2, cause not established".

### R9. README's "reorders source speech" — not supported by any edit on disk

- **Claim:** `README.md:163` — "**No fabrication.** SelekyT selects and
  reorders source speech. It does not write new claims".
- **What I verified.** I checked all 27 edit plans in the corpus: **0 of 27
  clips have non-monotonic source intervals**. Every shipped edit is a pure
  subsequence of the source in original order. The word "reorders" describes
  a capability the product does not exhibit in any corpus artifact. (The
  "selects" half is true and the "does not write new claims" half is true at
  the editorial layer — but for non-English sources the *transcription* layer
  demonstrably emits text that is not what was said, which is the claim's
  weak flank.)
- **What would make it true.** Either implement interval reordering or change
  the sentence to "selects and sequences source speech in original order".

**THIS FINDING DID NOT SURVIVE RE-VERIFICATION, and is kept above only so the
correction below is auditable.** The check was run against `selections.json`
compared by `start` against the previous clip's `end`; the files that failed
that test were the ones with a *gap*, not a reorder — a monotonicity test on
the wrong quantity. Re-run against the artefact that actually defines the
delivered sequence, `edit_plan.json`, ordered by `index`:

```
emwcon-panel-2018       source order of clip 1..5: 61.9, 793.7, 719.3, 1168.5, 205.8   REORDERED
hadhazy-protest         source order of clip 1..3: 52.6, 70.9, 32.8                    REORDERED
nasa360-ep5             source order of clip 1..6: 414.3, 327.9, 654.9, 606.0, 95.0, 507.1  REORDERED
ooi-vertical            source order of clip 1..3: 112.5, 41.4, 89.7                    REORDERED
pentagon-esper-briefing source order of clip 1..5: 583.7, 452.3, 633.6, 910.0, 403.1    REORDERED
royal-society-cloke     source order of clip 1..4: 181.7, 311.9, 242.3, 12.3            REORDERED
telugu-tutorial         single clip — trivially monotonic
```

**Six of the seven sources place at least one clip out of source order.** The
README sentence is therefore supported by artifacts on disk and must **not** be
"corrected" to "in original order" — that would have introduced a false
humility into the one place the product does more than trim. What remains true
from the original finding is the weak flank it named in parentheses: the
*transcription* layer emits text that is not what was said on the two
phonetic-noise sources, so "no fabrication" holds at the editorial layer and
not yet at the transcription layer. `README.md` says "no fabrication" about the
editorial layer and does not say which, so the sentence is defensible but
under-specified — that is the residual defect, and it is one of wording, not
of capability.

### R10. The README's NLE affordance claim is exactly what the repo's own docs disclaim

- **Claim:** `README.md:14` — "**FCPXML + CMX 3600 EDL timelines that an
  editor can open and continue working on**".
- **What I verified.** I tried to break the negative result in
  `docs/NLE_VALIDATION.md` and could not:
  - `melt` (MLT 7.22) **is** on this host at `/usr/bin/melt` — a headless NLE
    core the doc does not mention by name. I ran it on a real EDL:
    `melt corpus/royal-society-cloke_run/project/timeline/01.edl` →
    `Failed to load "...01.edl"`. `melt -query producers` lists `xml`,
    `xml-string`, `xml-nogl`, `kdenlivetitle` — no EDL or FCPXML producer. So
    the doc's claim that the headless entry points consume only MLT XML is
    correct, and this door is genuinely closed.
  - Blender 4.0.2 is present; I reproduced the doc's negative exactly:
    `SEQUENCER_IMPORT_OPS: []`, `ALL_EDL: []`.
  - OpenTimelineIO 0.18.1 is installed outside the venv at `/tmp/nlelibs`;
    its adapter list is `['otio_json', 'otiod', 'otioz']` — no CMX 3600, no
    FCPXML.
  - No X server and no Xvfb binary exist on this host, so the GUI NLEs are
    genuinely unreachable.
  So **no NLE has opened an artifact**, and `docs/NLE_VALIDATION.md:3` states
  this plainly. The README's "that an editor can open and continue working on"
  is an unverified affordance claim about software that has never been tried.
  (`README.md:11` "publishable" and `:231-233` "Already shipped ... FCPXML
  1.11 and CMX 3600 EDL export" are fine — the *emission* is real; the *opening*
  is not.)
- **What would make it true.** One human editor with Resolve/Premiere opening
  a `--keep-media` export, answering: did it import, did the media relink,
  does the played cut match the rendered MP4. `docs/NLE_VALIDATION.md` §4
  already names this as the cheapest path and records it as not done.

### R11. The render cache ships an MP4 whose burned caption style disagrees with its sidecar

- **Claim (implicit):** that the shipped MP4 and its `captions/NN.ass` sidecar
  describe the same video.
- **What I verified.** The render cache key is `selekyt/pipeline.py:117`
  `_fingerprint` (`source|path|size|mtime|model|language`) plus
  `:147` `_selection_digest` (`start,end,text` per clip) — **neither includes
  `SELEKYT_CAPTION_STYLE`**. The render loop at `:412-419` writes
  `captions/NN.ass` unconditionally and only *then* consults the cache at
  `:438-448`, taking the hit on a match. And I confirmed the style genuinely
  changes the bytes — all four real presets (`minimal`, `punch`, `spotlight`,
  `noir`) produce distinct ASS contents (619 bytes each, four different
  sha256s; e.g. `punch` is fontsize 58 bold 1 outline 3, `noir` is fontsize 52
  colour `&H00EAEAEA`). So a re-run with a different `--caption-style`
  rewrites the sidecar to the new style, takes the cache hit, and **ships the
  MP4 that has the old style burned into its pixels**. Nothing downstream
  detects this: `engine/qa.py` never checks captions at all.
- **Corroboration that the burn is real.** I compared the facebook render
  against the source frame at the matching source second: bottom 15% band
  mean|diff| = 7.74 with 3.8% of pixels changed >60, versus the upper 30% of
  the frame at mean|diff| = 0.98 with 0.0% changed — the exact signature of
  burned-in caption text at the bottom. So captions really are in the pixels,
  which is what makes the stale-style bug a genuine shipped-product defect.
- **Status: FIXED.** `selekyt/pipeline.py` now reads `caption_style` before the
  cache lookup, requires `cached.get("caption_style") == caption_style` to
  reuse a render, and writes the style back into the record. A record with no
  style key — the shape of every pre-fix `pipeline_state.json` — misses rather
  than matching `minimal`, so the corpus re-renders once under a validated key.
  Pinned by `tests/test_caption_style_cache.py`: a style change forces a
  re-render, the same style still reuses the cache, a bare record misses, and
  the record carries the style that was burned in.

### R12. The technical audit's headline security finding is already fixed; its caption-style finding is not

- `docs/TECHNICAL_AUDIT.md:159-171` reports `guardedPath` in `web/server.mjs`
  as a bypassable `startsWith(ROOT)` prefix check and marks it VERIFIED. I
  read the **current** code at `web/server.mjs:203-211`: it now reads
  `candidate === ROOT || candidate.startsWith(ROOT + sep)` with a comment
  explaining the sibling-prefix bypass. The fix is commit `5a7272a`
  "fix(web): close the sibling-directory bypass in the path guard". I
  reproduced the bypass only by re-typing the *old* vulnerable form; the
  deployed code is immune. **The audit's #1 finding is stale.**
- The audit's caption-style cache finding (`docs/TECHNICAL_AUDIT.md` §3,
  "captions and renders can disagree") **was live when I wrote this and is now
  fixed** — `selekyt/pipeline.py` keys the render cache on `caption_style` and
  treats a record with no style as a miss, pinned by
  `tests/test_caption_style_cache.py`. R11's corroboration of the burn itself
  is unaffected.
- `docs/TECHNICAL_AUDIT.md:23` reports the suite as "824 passed, 10 skipped".
  I just ran it: **854 passed, 7 skipped in 161 s**. The suite has grown
  since; the audit figure is stale but was not wrong when written.

### R13. Unverified or unsourced numbers in the machine-side story

Each of these is asserted with a precision the repo cannot back:

| Number | Where | Problem |
|---|---|---|
| Cold model load 1.60 s; warm transcription 28.30 s; in-run transcription 40.9 s; "~200.6 s remainder" | `TIME_SAVINGS.md:575-578` | **No artifact records them.** `corpus/results.jsonl` has only total `elapsed_s`; `corpus/*_run/project/` has no per-stage timing. Only the totals (241.55 s wall / 447.13 s CPU / 185%) are checkable. The decomposition comes from an unrecorded terminal session. |
| "92.6 MB network fetch" | `TIME_SAVINGS.md:543` | Unverifiable — the OOI media was deleted after the run; `corpus/media/` holds only `royal-society-cloke.webm`. Would need a re-download from the manifest URL. |
| Audio decode 147.0 s / 0.10 RTF | `TIME_SAVINGS.md:57` (§2) | Explicitly an ASSUMPTION carried from `BUSINESS_MODEL.md` §3; `TIME_SAVINGS.md` §10.2 A2 admits "Still assumed. Not isolated by any artifact." Never measured. |
| §2's microbenchmarks (36.12 s / 7.654 s / 5.552 s / 3,546 ms) | `TIME_SAVINGS.md:57-63` | No benchmark script or saved output is checked in. |
| Cloud compute "$0.021 – $0.024" | `TIME_SAVINGS.md` §5 | **Critique stood and was actioned — but it actioned the wrong layer, and the audit should say so.** Recomputed with the stated inputs ($0.1785/h × 0.2678 RTF × 1469.77 s / 3600) = **$0.0195**, not $0.021. $0.021 was `BUSINESS_MODEL.md` §4(a)'s figure derived from that doc's **0.2917** RTF, and **$0.024 matched nothing in either document.** `TIME_SAVINGS.md` §5 now quotes $0.0195–$0.0261 with each RTF named. But the figure underneath both rows was itself stale: `BUSINESS_MODEL.md`'s 0.2917 total rested on a render RTF of 0.1232 measured against the pre-`56253b5` `veryfast`/crf20 encoder, and the shipped encoder is `slow`/crf18 — re-measured 2026-09-28 at **0.875 (16:9) / 0.358 (9:16)**, moving the total to **0.3584** and the job cost to **$0.0261**. This row caught an arithmetic inconsistency in a number whose *measurement basis* was wrong, and neither the original nor the correction noticed that. The generalizable audit rule: verify the measurement conditions, not just the arithmetic that consumes them. |

### R14. One test is fully vacuous and its name claims a property it never measures

`tests/test_editorial.py:16-24` `test_rank_is_deterministic_and_non_overlapping`:

```python
first = [c.to_dict() for c in rank(segs, 3)]
second = [c.to_dict() for c in rank(segs, 3)]
assert first == second
```

I verified by AST and by execution: the body contains **exactly one assertion**
(`first == second`); **non-overlap is never asserted** despite the name; and
`rank(segs, 3)` returns **0 candidates** for this exact input, so the test
compares `[] == []`. It would pass unchanged if `rank` were
`def rank(*a): return []`. It pins nothing. This is the single weakest test in
the suite and it is actively misleading — a future maintainer reading the name
will believe overlap-freedom is covered.

Three more near-vacuous (weaker than their names, not zero-value):
`tests/test_report.py:9-25` `test_report_all_sections` is a constructor
round-trip (`ReportBuilder.set_*` at `engine/report.py:76-79` is literally
`if hasattr(...): setattr(...)`, so the test asserts the values it just wrote);
`tests/test_ffmpeg_error.py:15-34` asserts `FfmpegError`'s own constructor
arguments back out, though its last two assertions do test `__str__` formatting
(~40% real); `tests/test_cli_entry.py:189-192` asserts a function is callable.
`tests/test_cli_pipeline_audio.py:100-142` patches `run_pipeline_audio` with
`return_value=fake_summary` and then asserts `report["audio"] == fake_summary` —
the "expected" value is the mock's own return value (its sibling
`test_pipeline_audio_off_by_default` at `:57` is the good one, asserting a
genuine absence).

**What would make it true.** Give `test_rank_is_deterministic_and_non_overlapping`
an input for which `rank` actually returns candidates, and add a real
non-overlap assertion (`all(a.end <= b.start for a, b in pairwise(candidates))`).

**Resolved.** Both halves of the demand are in place at
`tests/test_editorial.py:17-40`. The three segments now carry 25+ words each,
above the 20-word floor, so `rank` returns real candidates; an explicit
`assert first` guards the determinism comparison against silently comparing
two empty lists again; and the overlap check the name always promised is
there, walking consecutive shipped spans and asserting `b <= c` in source
time. The test's docstring records the vacuity it replaced, so a reader can
tell why the non-empty check is load-bearing.

That repair exposed a second-order problem the red team did not name, and it
is the more useful of the two. Once the non-overlap assertion existed I
mutation-tested it by deleting the de-overlap guard from `rank()`, and the
test still passed: its three segments are already disjoint in source time,
so a guardless `rank` returns the same answer. An assertion cannot cover a
property its own input does not exercise. `test_rank_rejects_a_time_overlapping_candidate`
now supplies two candidates that genuinely overlap (0-20 s and 10-32 s).
Its wording is the other half of what makes it work — with three clips on
one topic, `rank`'s similarity and topic-token filters drop the second
candidate unaided, so the overlap guard never fires; the three passages are
deliberately about separate subjects, which I confirmed by instrumenting
`rank` step by step and finding the overlapping candidate rejected by the
overlap check and by nothing else. Deleting the guard now fails this test
and this test alone (`1 failed, 7 passed`), which is the signature of a
test that covers the defect.

Honest limit, stated because it is the difference between a real test and
theatre: the assertion detects a *removed* guard. A guard rewritten to a
different but still-restrictive condition can still leave these three clips
disjoint, and the suite will not notice — the correction above pins the
property the cut list needs to be cuttable, it does not prove the guard's
arithmetic.

The three near-vacuous tests named below are **not** resolved, and the
distinction matters. They are weak relative to their names, not empty —
`test_report.py:9-25` does confirm the report's sections survive a
round-trip, `test_ffmpeg_error.py:15-34` does test `__str__` formatting, and
`test_cli_entry.py:189-192` does confirm the entry point is importable and
callable. Strengthening them into tests that would catch a real regression
in those three modules is genuine remaining work; none of them certifies a
property that is false today.

### R15. `tests/test_web_harness.py` guards the path jail against only the obvious case

`tests/test_web_harness.py:218` asserts `/etc/hostname` is rejected by
`guardedPath`, and it passes. It does not test the sibling-prefix case
(`selektyt-backup`, `selektyt.old`) that the audit identified — though the
underlying code has since been fixed (see R12), so this is now a test-coverage
gap rather than a live vulnerability. Add the sibling-prefix case as a
regression test so the fix cannot silently regress.

**Resolved.**
`tests/test_web_harness.py::test_a_sibling_directory_extending_the_project_name_is_rejected`
builds a real sibling of the repo, sends its *absolute* path to
`/api/vad_analyze`, and asserts the refusal. The absolute form matters: it is
the shape of the bypass, and it contains no `..`, so a traversal-based filter
would not have caught it either. `vad_analyze` is the route chosen because a
probe the pipeline would reject anyway proves nothing: the sibling is filled
with a real WAV fetched from `/api/sample.wav`, so the only thing that can
make the request fail is the jail.
Verified by reverting `guardedPath` to the bare
`startsWith(ROOT)` — the new test fails, the rest of the suite still passes,
which is exactly the signature of a test that covers the defect rather than
the happy path.

### R16. Two documents of record contradict each other about YouTube, and neither can be settled

- **Claim A:** `docs/AI_EXECUTION_STATE.md:277-281` — "The blanket 'Sign in to
  confirm you're not a bot' block of the previous session was *not* a permanent
  state: one full download through SelekyT's own CLI succeeded this session
  (640×360, 213 s, 11.8 MB, unauthenticated android client)."
- **Claim B:** `docs/product-gap-map.md:23` — "the URL path above is the same
  code YouTube uses; **no YouTube fetch was ever observed on this host**, so
  YouTube-specific behaviour (chapters, members-only, age gates) is untested".
- **What I verified.** There is **no YouTube artifact anywhere in the repo**:
  the only URL-sourced project is
  `out/web/pipeline/project`, whose `pipeline_state.json` `source` field is
  `https://archive.org/details/NASA_360_program5_HD`. No `youtube` or
  `youtu.be` reference appears in any project's `report.json` or
  `pipeline_state.json`. So the one positive claim about YouTube cannot be
  checked against any artifact, and the gap map — the document a diligence
  reader is pointed at for what was *actually exercised* — still asserts the
  opposite. `AI_EXECUTION_STATE.md:483-486` explicitly imposes the duty to
  update that row when a verified path changes, and cites the gap map's
  authority ("says 'as of `2dd4ff1`'") — except the gap-map header actually
  reads "as of `b1dcfa7`" (both commits exist). So the citation points at the
  wrong commit, and the duty it names was not discharged for the row at issue.
- **What would make it consistent.** Either land the downloaded YouTube
  artifact in the repo as evidence, or correct one of the two rows. A claim of
  the form "one end-to-end fetch has been observed" needs the artifact or it is
  unverifiable by construction.

### R17. `/api/file` serves repo files to any local caller, and the credential-leak mechanism is live

- **Claim:** `docs/TECHNICAL_AUDIT.md:178-186` — "`/api/file` serves the whole
  repo, not the output directory ... `.git/`, `selekyt/`, `.env`, and every
  other user's `out/web/uploads/` file are readable by any unauthenticated
  caller."
- **What I verified.** I booted the shell and probed it:
  ```
  curl "http://127.0.0.1:4242/api/file?path=docs/TECHNICAL_AUDIT.md"  → HTTP 200
  ```
  No token, no session, no `Origin`/`Referer` check. The path jail is now
  correct (the sibling-prefix bypass was fixed in commit `5a7272a`, see R12),
  so this is confined to the repo root rather than arbitrary escape — but
  "confined to the repo" still includes `pipeline_state.json`, which carries
  the full yt-dlp `media_info` blob. I confirmed `media_info` is present in
  `out/web/pipeline/project/pipeline_state.json`.
- **The important nuance.** I grepped for `Cookie` across `out/` and every
  corpus project: **zero hits**. No credentials have ever been captured on this
  host, because the only URL source ever run is the unauthenticated archive.org
  one. So the *mechanism* is live (blob persisted, served unauthenticated) but
  the *exposure* has never materialised — and per R16 no YouTube fetch is
  evidenced here at all. This is a latent defect, not a demonstrated leak, and
  it should be described that way.
- **What would make it true.** Either scope `/api/file` to `OUT_WEB`, or strip
  credential-bearing fields from `media_info` before `selekyt/pipeline.py:206`
  persists it.

**Resolved, on both sides named above.** `/api/file` now resolves through
`guardedArtifact`, whose boundary is `OUT_WEB + sep`, so `pipeline_state.json`
is not inside it; and `media_info` passes through `_scrub_credentials` before it
is ever written, dropping `http_headers`/`cookies`/`_cookies`/`cookie_manager`/
`cookiejar`/`cookies_str` at every depth. The latency claim in this finding
("the mechanism is live") was correct at the time and is the reason the fix was
applied to the write path as well as the read path — confining the read alone
would have left the blob on disk for any other consumer. Pinned by
`tests/test_pipeline_hardening.py::test_scrub_credentials_removes_every_credential_field`.

### R18. Any web page can drive the local pipeline — the CSRF chain is now cut at all three links

- **Claim:** `docs/TECHNICAL_AUDIT.md` defect #1 — the web shell has "no
  authentication, no origin policy, and a Content-Type-blind JSON POST", which
  combined with the prefix bypass (#2) and the credential blob (#3) into a
  chain a remote page could walk to the user's cookies.
- **What I verified.** I booted the shell and reproduced the primitive: a
  `text/plain` POST carrying JSON reached `JSON.parse` with no preflight, and
  no `Origin` or `Referer` was inspected anywhere in `route`. The chain needed
  all three links; the last one standing was the control plane itself.
- **What is true now.** `assertSameOrigin` runs before any mutating route
  parses a body and refuses a foreign `Origin` (or, in its absence, a foreign
  `Referer`); `getJson` requires `application/json`, which an HTML form cannot
  send. I probed both as an attacker would and both are refused with the
  offending origin named.
- **The honest residual.** A request carrying *neither* header is still
  accepted, and there is still no token. That is curl or a local script, and
  the localhost bind already trusts local processes; the threat this finding
  described was a *remote* page using the user's browser. Closing the local
  case means an unforgeable local secret, which is a different product. Stated
  rather than implied, in `docs/TECHNICAL_AUDIT.md` row #1 and the "no
  authentication" section.

---

## CLAIMS THAT SURVIVED THE ATTACK — these are worth keeping

### S1. The rendered outputs are real, playable, and have audio

All 30 MP4s under `corpus/*_run/project/{facebook,shorts}/` are well-formed
h264+AAC in an MP4 container, with non-zero frame counts and sizes. I measured
non-trivial audio RMS on every clip I sampled (OOI clips −23 to −43 dB), so
the "shipped with no audio stream" defect described in
`docs/CORPUS_RESULTS.md:163-169` is **not** present in the artifacts on disk —
the fix held. Both presets are correct per source (e.g. royal-society-cloke
1920×1080 facebook vs 608×1080 shorts; ooi 1920×1080 vs 1080×1920). Two
anomalies the docs do not mention: `nasa360-ep5` clips carry an extra
`codec_type=data` stream carried through from source, and
`ooi-vertical` facebook/02 and /03 have audio streams 0.66–1.63 s shorter than
their video (audio is present and loud, so this is a trailing-length mismatch,
not a drop).

### S2. English transcripts are coherent and WER is genuinely measured, and I reproduced it

- Random segments sampled from the middle of all five English transcripts
  (`royal-society-cloke`, `pentagon-esper-briefing`, `nasa360-ep5`,
  `emwcon-panel-2018`, `ooi-vertical`) are fluent, grammatical and topically
  consistent with their sources (a Pentagon press briefing on military
  operations; a Royal Society climate interview; NASA; an ocean-observatory
  documentary; a MediaWiki panel).
- The headline WER number is real and **I reproduced it**. `docs/
  TRANSCRIPTION_EVALUATION.md` claims `small` = 3.38% WER / 1.35% CER on a
  fixed 50-clip, 1036-reference-word LibriSpeech `test-clean` subsample. I
  re-ran the same command independently:
  ```
  [bench] redteam-small-verify: WER=3.38% CER=1.35% on 50 clips / 6.56 min
  ```
  bit-identical to the documented figure (RTF differed — 0.457× vs the
  documented 0.649× — which is machine load, not the scored quantity).
- The reference is genuine ground truth: `tools/wer_bench.py:309-324` reads
  each utterance's line from the LibriSpeech chapter `.trans.txt` shipped in
  the tarball, not from any model output. Corpus-level WER is summed edits
  over summed reference length, not a mean of per-clip rates, and an empty
  hypothesis scores 100% rather than dividing by zero.
- The `asr` block in every `transcript.json` records
  `{"requested_model": "small", "model": "small", "downgrade_reason": null,
  "fallback_reason": null}`, so the attribution of the non-English failure to
  the `small` model is supported at the artifact level. **Gap to note:**
  `corpus/results.jsonl`'s `asr_model` field is `null` for all 11 sources, so
  the per-run record alone does not prove which model ran.

### S3. Non-English transcripts really are phonetic noise, and the audits really cannot see it

Verified from `corpus/results.jsonl` and the on-disk `transcript.json`:
`telugu-tutorial` = 4 segments, 83 chars of
`రిన్త్న న్స్హ్లుయిసి . నినాన్త్డందాన ంపెర్లిస్లిని...`;
`hindi-baiga-interview` = 3 segments of Devanagari noise (NO_STORIES_PASSED);
`hadhazy-protest` = 38 individually-plausible Hungarian fragments. And the
telugu clip **passes `tools/editorial_audit.py` with ALL CHECKS PASS** at
46.3% silence share — the doc's central thesis
(`CORPUS_RESULTS.md:101-128`) is confirmed by direct execution, not just
asserted. `medium` materially improves Hindi: the
`describe_d-medium-forced-hindi-baiga-interview` run produces 11 coherent
segments ("मेरा नाम लक्मनाल अगर्याइज..." — "my name is..."), versus the
`small` model's 3 segments of noise.

### S4. The interchange files are mechanically sound, with two genuinely independent parsers

`PYTHONPATH=/tmp/nlelibs:tools .venv/bin/python tools/nle_check.py
corpus/royal-society-cloke_run/project` → **ALL CHECKS PASS**, with `pycmx`
1.5.1 parsing every EDL outside the project venv (independent parse: 3 events
/ 5 channel records on `01.edl`), EDL↔FCPXML agreeing to 0.00 frames, and
spine vs both renders to 0.53 frames. The relink checks are real on this
source — I confirmed the retained media exists (85,493,260 B) and that the
FCPXML asset `src` resolves to it. `tools/nle_check.py:288-348` genuinely
compares the EDL source in/out against the **edit plan's retained span**
(`_retained_span`), not the raw selection span — the bug
`CORPUS_RESULTS.md:170-177` describes and says was fixed really was fixed, and
the code comments record why the fallback to the selection span would be a
false negative. `nle_check.py` on `out/web/pipeline/project` also reports
ALL CHECKS PASS (the "92 checks, 0 fail" claim in
`AI_EXECUTION_STATE.md:126-127`).

### S5. Determinism is verified on a real second run

`out/web/pipeline/project/selections.json` and
`corpus/nasa360-ep5_run/project/selections.json` — two independent runs, one
of them the corpus run — have **byte-identical selection span lists**:
`414.300-439.160, 327.860-346.280, 654.940-671.380, 606.020-620.020,
94.980-112.640, 507.110-516.170`. The three spans
`CORPUS_RESULTS.md:184-185` quotes as the prior run match exactly. P13 holds
on a real re-run.

### S6. Geometry claims are exact

`out/web/pipeline/project/shorts/01.mp4`: 308×486, SAR 8:9 → DAR 1232:2187
(0.5633, ≈9:16). `facebook/01.mp4`: 864×486, SAR 1:1 → exactly 16:9. Both
exactly as `AI_EXECUTION_STATE.md:136-138` states.

### S7. The §10.3 machine-side table is arithmetically airtight

I re-ran the doc's own command from `TIME_SAVINGS.md:364-380`. Every row
reproduced exactly — all seven per-source rows (length, wall, RTF, clips,
delivered, yield) and all six aggregates (7066.2 s source, 2330.4 s wall, RTF
0.3298, 1097.5 s delivered, 15.53% yield, 28.3 s per machine minute, 6.44
source-minutes per delivered minute). Only `royal-society-cloke` delivered
differs from the doc (254.89 s vs 254.97 s — 0.03%, immaterial). The
least-squares fit also reproduces exactly: `225.2 + 0.1067·d`, R² = 0.390,
crossover 252.1 s. The doc's *arithmetic* is clean; its *interpretations*
(R6, R7) are where it breaks.

### S8. The honesty hedges are real and load-bearing

These quotes are the parts of the corpus that survive attack and should be
kept verbatim:

- `TIME_SAVINGS.md:3-4` — "This is the **weakest claim in the venture**, and
  this document treats it adversarially rather than as a selling point."
- `TIME_SAVINGS.md:6-7` — "SelekyT has **zero users, zero revenue, zero
  pilots, and zero investors**."
- `TIME_SAVINGS.md:112-114` — "No human editor has ever been observed
  performing this task, in any condition, by anyone in this venture ... The
  denominator of the ratio is therefore an assumption, full stop."
- `TIME_SAVINGS.md:178-181` — "If the selection is unusable, the machine time
  is not a saving — it is **pure additive waste** ... Under that outcome **the
  sign of the saving flips**."
- `CORPUS_RESULTS.md:192-196` — "**No human has judged any of this.** Seven
  sources passing mechanical audits is evidence the machinery is sound ... It
  is zero evidence that a human editor would ship any of these cuts."
- `HUMAN_EDITORIAL_EVALUATION.md:3-4` — "**STATUS: DESIGNED, NOT RUN — no
  participant contacted, founder approval for external contact not given.**"
- `NLE_VALIDATION.md:3-4` — "**NO NLE HAS OPENED AN ARTIFACT.**"

I searched the repo for any human-evaluation data: there is **no** `key.csv`,
no envelope directory, no `mechanical_*.json` rating file, zero `.csv` files
outside `.venv`/`.git`, no participant identifier anywhere. The protocol
document's "DESIGNED, NOT RUN" banner is scrupulously accurate, and no claim
in the repo implies otherwise.

### S9. The test suite is not a mock theatre

854 passed, 7 skipped, 0 xfail. The 7 skips are all in
`tests/test_proxy_validation.py`, each gated on `SELEKYT_PARITY_MEDIA`
naming a ~239 MB non-redistributable recording, each stating its own un-skip
condition. No `xfail` markers exist anywhere, so nothing is hiding failure.

**The documented regression claims are real, and I reproduced one.**
`docs/CORPUS_RESULTS.md:55` claims "Regression test in `tests/test_captions.py`,
verified to fail on the pre-fix code". I confirmed it: with the straddle filter
at `engine/caption_director.py:86` (`words = [w for w in words if
w.end <= candidate.end]`) neutralised, the test fails at
`tests/test_captions.py:143` (`AssertionError`); restored, all 11 pass. The
test is mock-free real SRT/ASS emission, asserts the straddle precondition
(`trailing.start < c.end < trailing.end`), and additionally requires no cue to
run past the rendered duration. Genuinely load-bearing.

Strong tests worth keeping, by class: `tests/test_render.py` (real ffmpeg +
ffprobe, anamorphic DAR math, frame-accurate multi-part rendering),
`tests/test_qa.py` (real ffmpeg fixtures for *both* opposite causes of "no
audio"), `tests/test_captions.py` (mock-free, real retiming math),
`tests/test_sidecar_parity.py` (real compiled Rust `selekyt-mech` over stdio
JSON-RPC), `tests/test_sidecar_contract.py` (parametrized over every op in
`selekyt.server._HANDLERS`), `tests/test_project_lifecycle.py` (cross-artifact
consistency with independently recomputed expected values),
`tests/test_engine_hardening.py` (differential: `remove_filler=True` vs `False`
must change the cut list), `tests/test_nle_check.py` / `test_nle_export.py`
(real media, real EDL/FCPXML generation and validation),
`tests/test_web_harness.py` (real `node web/server.mjs` HTTP server).

Mock-heavy but legitimate: `test_whisper_metrics.py`, `test_whisper_fallback.py`,
`test_sherpa.py`, `test_transcription_recovery.py` mock the ASR library (which
is not installed) and ffmpeg, but exercise the *real* wrapper logic — chunk
loop, ETA, stall detection, model downgrade, checkpoint status, provenance.
They mock transcription content, which they never claim to validate.

### S10. The "one command" and the web shell actually work

`README.md:24-26`'s one-command claim and `README.md:120-128`'s web-shell
claim both hold:

```
$ .venv/bin/selekyt --doctor
SelekyT doctor
  python3            OK
  python             OPTIONAL
  ffmpeg             OK
  ffprobe            OK
  yt-dlp             OPTIONAL
  faster-whisper     OK
Ready.
$ node web/server.mjs   →  LISTEN 127.0.0.1:4242,  HTTP 200
```

The served page contains the real shell ("SelekyT", "Analyze sample"), and the
corpus's own reference project was produced through the web path
(`out/web/pipeline/project`, source `archive.org/details/NASA_360_program5_HD`)
— a genuine end-to-end run through the JSON-RPC sidecar, not a CLI-only
artefact. `--doctor` is also honest about what is optional (`yt-dlp`, the
unversioned `python`) versus required.

---

## CORRECTIONS TO MY OWN SUB-AUDITS

Three parallel sub-audits ran during this session. Two of their findings did
not survive my own re-verification, and are recorded here so the record is
honest about the disagreement:

1. **The sub-audit of `HUMAN_EDITORIAL_EVALUATION.md` claimed
   `royal-society-cloke` FAILs `editorial_audit.py` on the committed artifact
   and "passes only because of an UNCOMMITTED working-tree fix to
   `edit_plan.json`".** I tested this directly, three ways: on the working tree,
   on the committed `edit_plan.json` alone, and on all eight committed
   royal-society-cloke files restored together into a scratch copy. **It passes
   in all three cases** (ALL CHECKS PASS, all four clips + cross-clip). The
   claim is wrong; the uncommitted `edit_plan.json` diff (235.237 → 235.22,
   `edited_duration` 52.422 → 52.405) is a refinement, not the thing that flips
   the verdict. The current truth is 3 of 7 failing, and
   `royal-society-cloke` is not one of the three.
2. **The same sub-audit reported the editorial audit passing on "3 of 7
   committed, 4 of 7 with the uncommitted fix".** The 4/3 split is right but
   the mechanism is not — it is 4 of 7 on committed state.
3. The TIME_SAVINGS sub-audit's §10.7 arithmetic findings reproduce exactly as
   reported, and I independently re-derived the preset-identity result that
   undercuts the playback floor (R5).
4. **R9 is wrong, and correcting it would have damaged the README.** The
   sub-audit reported that no edit on disk reorders source speech, on the
   strength of a monotonicity check run against `selections.json` with each
   clip's `start` compared against the *previous clip's `end`* — which fails
   on any two clips separated by a gap, i.e. on every edit that trims
   anything. Re-run against `edit_plan.json` ordered by `index`, **six of the
   seven sources place at least one clip out of source order** (see R9 for the
   per-source sequence). The README's "selects and reorders source speech" is
   therefore supported by artifacts, and the recommended fix — changing it to
   "in original order" — would have introduced a false claim in the opposite
   direction. Recorded here because this is the case where the audit's own
   drift ran *against* the product rather than for it, and a reader who
   applied the finding would have made the repo worse.

---

## CLAIMS THAT ARE STALE RATHER THAN FALSE

- `docs/AI_EXECUTION_STATE.md:126` "92 checks pass, 0 fail" — **true**, I
  reproduced it.
- `docs/AI_EXECUTION_STATE.md:17` / `:529` "837 passed, 10 skipped" — **stale**;
  actual is 854 passed, 7 skipped. The doc's own instruction at `:529` is
  "confirming 837/10. A number other than that means something has drifted" —
  so by its own test it has drifted.
- `docs/AI_EXECUTION_STATE.md:533-535` "the repo is at **165 commits, 56 of
  them machine-authored**" — **stale**; actual 174 commits, 65 Ubuntu-authored
  (60 Devika Boddu, 49 desukab).
- `docs/AI_EXECUTION_STATE.md:191` "`pycmx` is in no dependency extra, so CI
  cannot run the independent check at all" — **false**, and corrected 70 lines
  later at `:259-260`, but the false sentence is left standing in the
  "Completed work" section. `pyproject.toml` lists pycmx in `nle`, `dev` and
  `all`; it is installed in `/tmp/nlelibs` and present in the venv. *Later note:
  both the standing sentence and the correction were superseded in a later
  revision of `AI_EXECUTION_STATE.md`, which now matches
  `docs/TECHNICAL_AUDIT.md` §CI — pycmx is in three extras and CI installs
  `.[dev,llm,nle]`, so the check can run; the real gap is that CI never invokes
  the audit tools at all.*
- `docs/CORPUS_RESULTS.md:197-200` "WER is still unmeasured" — **stale**;
  `docs/TRANSCRIPTION_EVALUATION.md` §3 measures English WER (which I
  reproduced, S2). `CORPUS_RESULTS.md` is 19:23, *newer* than
  `TRANSCRIPTION_EVALUATION.md` at 19:04, so the newer doc contradicts the
  measurement the repo already holds.
- Disk figures: `AI_EXECUTION_STATE.md:51` "5.7 G free of 29 G" and `:479`
  "14 G free" — both stale and in opposite directions; actual 3.9 G / 87%.
- `docs/AI_EXECUTION_STATE.md:8` "working tree **clean**" — **false**; 12
  modified tracked files and several untracked ones right now.

---

## NET ASSESSMENT

The direction of every substantive error I found is the same: the product
looks slightly more verified than it is. A "7 clean through both audits" that
is really 4-of-7-on-one-audit-with-relink-untested-on-six; a "three of the
seven OK sources" that is really two; a "0 FAIL" that is really NOT RUN on six
of seven; an "across 11 sources" that is really 7 with a 36% failure rate
hidden; a "73× is arithmetically infeasible" that is really conditional on
double-counting a duration-identical reframe; a suite that contains one test whose own name lies
about what it measures. None of these are fabricated — they are aggregations
and summaries that drifted favourable, in a repo whose entire stated purpose
is to stop exactly that substitution.

What is genuinely strong, and what I would keep: the actual pixels on disk
(S1), the measured and independently reproduced English WER (S2), the
non-English failure being found and stated rather than hidden (S3), the
two-parser interchange check (S4), determinism on a real re-run (S5), exact
geometry (S6), the arithmetically clean machine-side table (S7), and a working
CLI and web shell (S10). The mechanical layer is in better shape than the
claims about it, which is the inverse of the usual failure mode and is the
single most useful thing this red team found.

The one claim that is *under*-stated rather than over-stated: the English WER
measurement. `docs/CORPUS_RESULTS.md:197-200` still says "WER is still
unmeasured" while `docs/TRANSCRIPTION_EVALUATION.md` §3 already reports 3.38%
— and `CORPUS_RESULTS.md` is the *newer* file. That is the one place the repo
should be bolder, and it is notable that the error runs the other way here.

---

## STATUS AGAINST THE REPO AT 2026-09-20

This section is not part of the red team's audit. It is a later reconciliation,
added so that a reader can tell which findings above were accepted and which
still stand. Every line was re-checked by re-running the named tool, not by
reading the doc.

> **Re-reconciled 2026-09-22: the R1 line below is stale, and the number in it
> is the one this document now corrects.** The "5 FAILURES PRESENT" it reports
> was real on 2026-09-20, but three of those five were the false positives R1's
> postscript describes: `check_cuts_respect_sentences` derived sentence
> membership from the candidate's joined text and so read a clean cut between
> two under-punctuated utterances as a hole in one sentence. The check fires on
> word loss now, and re-running `tools/editorial_audit.py` over the seven
> projects that ship selections gives **5 ALL CHECKS PASS**
> (`emwcon-panel-2018`, `hadhazy-protest`, `ooi-vertical`,
> `pentagon-esper-briefing`, `royal-society-cloke`) and **2 FAILURES PRESENT**
> (`nasa360-ep5` and `telugu-tutorial`, each one clip over the silence-share
> bar and awaiting human review). The four remaining projects
> (`alice-1903_run`, `hindi-baiga-interview_run`, `roman-portrait-trailer_run`,
> `telugu-launch_run`) ship no `selections.json` and report no auditable
> artifacts. The bullet is left standing unedited for the same reason the
> "STALE RATHER THAN FALSE" list is: it is the measurement as taken, and the
> correction is meaningless without it. The per-source counts that follow it
> below were not re-checked line by line and are the 2026-09-20 figures.

- **R1's "7 clean through both audits" — the claim it criticised is gone from
  `AI_EXECUTION_STATE.md`, but the underlying audit result has moved and not in
  one direction.** Re-running `tools/editorial_audit.py` over all seven OK
  sources today gives **2 ALL CHECKS PASS** (`royal-society-cloke`,
  `hadhazy-protest`) and **5 FAILURES PRESENT**. The failing sources split by
  check: `emwcon-panel-2018`, `pentagon-esper-briefing` and `ooi-vertical` fail
  on `check_cuts_respect_sentences` (six cut edges — three are ASR
  under-punctuation, three are comma-clause cuts; analysed in
  `AI_EXECUTION_STATE.md` as audit noise), while `nasa360-ep5` clip 05 and
  `telugu-tutorial` clip 01 fail on **silence share** — 40.8% and 46.3% dead air
  in the rendered clips, which is a real property of those selections and not
  artifact debt. R1's demand is still unmet in the direction that matters:
  record the per-source result, and do not call the set "clean".
- **R5 was accepted.** `TIME_SAVINGS.md` §10.5 was rewritten after this audit
  and now states the protocol dependence itself — its heading reads "why the
  top of the range is protocol-dependent" and its body says plainly that "the
  headline 73× is not arithmetically infeasible" and that the earlier blanket
  prohibition "was stronger than the evidence". §10.11 now forbids quoting *any
  single* speedup figure, 73× or 36×. R5's recommendation is quoted nearly
  verbatim in the corrected §10.5 conclusion.
- **R2's 36% failure-rate caveat was accepted** and now appears in §10.11's own
  permitted statement, which says the 38.8-minute figure "covers only the 7
  sources that delivered, not all 11" and names the 36% rate.
- **R8 was accepted.** §10.7's component table is now disjoint and sums to the
  wall clock (1.60 + 39.3 + 200.7 = 241.55 s), the warm-only pass is reported
  separately from the in-run figure rather than as a peer row, and the document
  records in place that an earlier version of the table listed it as a fourth
  row, "which made the shares total 112.4%". The 83.1% headline share was always
  correct and is unchanged.
- **R7 was accepted in full.** §10.3 now says **"two of the seven sources ran
  slower than real time"** and names them (`hadhazy-protest` 1.140,
  `telugu-tutorial` 1.746), with a parenthetical recording that an earlier draft
  said three and exactly how it reached that count. R7(b) is also corrected:
  §10.3 now states **"the two unusable sources ran at 6.70 s of machine time per
  delivered second — 4.15× the usable rate, +315%"** and records in place that
  the earlier "32%" figure was the all-7 vs usable-5 pooled-average gap, not a
  property of those two sources, and that labelling it as one understated them
  by ~10×. Both corrections are R7's recommendation verbatim.
- **R6 was accepted in the direction that matters.** §10.3's fit and crossover
  are unchanged at 252 s, but §10.11's permitted phrasing softens the assertion
  to "on sources *shorter than about four minutes* the pipeline runs slower than
  real time". R6's specific objection — that `ooi-vertical`, one of only two
  sources below the crossover, ran at RTF 0.922 and was *faster* than real time —
  is now recorded in §10.3's own parenthetical, though the crossover number
  itself still stands.
- **The "STALE RATHER THAN FALSE" list above is itself now stale, by design.**
  The disk figures (3.9 G / 87%), the test count (854/7) and the commit count
  (174 / 65) were true on 2026-09-19 and have all moved since: 3.0 G / 90%,
  1034 passed / 12 skipped, 194 commits / 85 Ubuntu-authored. They are left
  standing as the audit's own timestamp, not corrected in place.
- **One finding this audit made that later work turned into a fix.** The
  caption-vs-text desync visible in R1's `check_caption_text_is_transcript_text`
  was traced to `_segment_sentences`' proportional timing estimate and fixed at
  the seam (`engine/edit_plan.align_span`, called from
  `engine/editorial.score_candidate`); three of 27 candidates with word timing
  had a text disagreeing with the words their shipped captions are built from,
  and the fix corrects exactly those three. See
  `docs/AI_EXECUTION_STATE.md` item 10.
