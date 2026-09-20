# AI execution state — SelekyT

Checkpoint for a following Atria session. Factual as of the commit named below;
every line below was true on this host at that point.

## Current repository state

* Repository: `/home/azureuser/selektyt`, on `main`, working tree **clean**.
* Up to date with `origin/main` (`https://github.com/desukab/selektyt.git`).
  Nothing unpushed, nothing stashed, nothing in `git stash list`.
* **Latest commit:** this file's own commit. The last *substantive* change is
  `142b436` — the credential-read fix on both sides, `tools/microbench.py`, and
  the document corrections those forced. Run `git log -1` for the current hash
  rather than trusting a hash printed here, since amending this file changes its
  own commit hash.
* Test suite: **1034 passed, 12 skipped in 114 s** at the last clean commit
  (2026-09-20). The 12 splits into two kinds, and the difference matters:
  **7** are in `tests/test_proxy_validation.py`, gated on the
  `SELEKYT_PROXY_MEDIA` env var, which expects a ~239 MB YouTube recording that
  is not redistributable with the repo. **They are opt-in real-media
  measurements, not missing optional backends** — the suite reports green on
  every CI run while the proxy validation it names does not happen, and that is
  a worse problem than an absent backend. The other **5** are in
  `tests/test_wer_bench.py`, skipped because `c9-small`, `c9-small-beam5`,
  `c9-medium`, `c9-large-v3` and `redteam-small-verify` predate the
  `wer_err_total` field in their run records — old committed data that cannot be
  re-derived without re-running the models, not a missing environment. The
  earlier count in this file was 895/7; the suite grows as defects get regression
  tests, and the skip count moves when a gated test's media appears or disappears
  — re-run `pytest -q -rs` before trusting any count here.
  **Do not describe the 12 as one homogeneous group of "environment skips"** —
  the doc set has already done that once, and it hides the 7 that are opt-in
  measurements a reader might assume are covered.
* Five commits landed on `origin/main` from outside this session
  (`36bdff4`..`edf3b30`): a switch from MIT to a proprietary license plus
  project-positioning docs. They were integrated by rebase, not overwritten.
  **Check `LICENSE` and the top-level positioning docs** before assuming the
  MIT-era wording in older files still applies.
* Preserved agent worktrees under `.claude/worktrees/` are **not** merged into
  `main` and must not be deleted (standing instruction). The timeline worktree
  (`agent-a2478f4bf3ccaf916`, `feat(timeline): edited-timeline model …`) is the
  only one whose subject matter overlaps live code paths — check whether it has
  already been integrated before reworking anything in `engine/timeline*`.

### How to run things here

* Tests need the project venv: `.venv/bin/python -m pytest -q`. A bare
  `python -m pytest` fails.
* The shell **cwd resets to `/home/azureuser` between Bash calls** — always `cd
  /home/azureuser/selektyt` or use paths from repo root.
* Web shell (when needed): `setsid env PATH="$PWD/.venv/bin:$PATH" nohup node
  web/server.mjs --port 8791 … & disown`. The venv must be on `PATH` or the
  sidecar cannot resolve `yt-dlp`. Plain `nohup … &` dies with the shell.
* No project server is listening right now. Scratch run scripts in `/tmp` were
  removed.
* Disk: **3.0 G free of 29 G, 90% used** (`df -h /`, 2026-09-20). This file
  previously said both "5.7 G" and "14 G" in different places; both were stale,
  and the disk is the binding constraint on what can be attempted next. The
  corpus's downloaded media is deliberately *not* kept — `corpus/media/` is
  gitignored and regenerable from `corpus/manifest.json`, and it had to be
  removed mid-run when the filesystem hit 118 MB free and started failing every
  Bash call. **Two sources' media survives locally:
  `corpus/media/royal-society-cloke.webm` (85 MB) and
  `corpus/media/pentagon-esper-briefing.mp4` (158 MB, kept for the re-run that
  confirmed the word-boundary fix and the text desync).** The text artifacts
  (transcript, selections, edit plan, EDL/FCPXML) are the evidence and they are
  committed. Check `df` before any long run. Note that 483 MB was reclaimed this
  session by deleting `corpus/wer/downloads/te_in_female.zip`, an untracked and
  regenerable MLS archive cache — the WER corpus's own downloads directory is
  the first place to look when a long run is blocked on disk.
* **The nasa360 source is NOT deleted, contrary to a note this file carried
  implicitly.** `out/web/pipeline/project/source/NASA_360_program5_HD.mov` is
  intact at 883,894,825 bytes, matching §2 of `docs/TIME_SAVINGS.md`'s verified
  `ffprobe` line byte-for-byte, and `out/` is gitignored rather than removed.
  That matters because it means §2's exact job *can* be re-run, and
  `tools/microbench.py` now does (§2b). `corpus/media/` being nearly empty is a
  git policy, not evidence the media is gone — check the path before concluding
  a measurement is impossible.

## Completed work

### This session — the audit was actioned, one finding was reversed, and the numbers became reproducible

A red-team pass over the repo's own claims (`docs/RED_TEAM_FINDINGS.md`) was
worked through item by item. Most findings were real; **one was wrong, and
reversing it is the most important thing that happened this session**, because
a reader who applied it would have made the repo worse.

* **R9 reversed — the "no reordering" finding was false.** The audit claimed no
  edit on disk reorders source speech, so the README's "SelekyT selects and
  reorders" was unsupported. Re-verification against `edit_plan.json` — the
  artifact that actually defines the delivered sequence, not `selections.json` —
  shows **six of the seven sources place at least one clip out of source
  order**. The audit's test compared each clip's `start` to the previous clip's
  `end`, which fails on any gap. The finding is kept in the report above its
  correction so the reversal is auditable, and the README sentence stands.
* **R17 fixed on both sides.** `/api/file` was jailed to the repo root, which is
  the wrong boundary for an unauthenticated localhost route — `pipeline_state.json`
  sits inside it and carries the yt-dlp info dict verbatim, including
  `http_headers` and its `Cookie` line. `guardedArtifact()` now scopes downloads
  to `out/web`, and `_scrub_credentials()` strips the credential keys before
  persistence. **No credentials were ever captured here**: the only URL source
  ever run is the unauthenticated archive.org one, and a grep for `Cookie`
  across `out/` and every corpus project returns zero hits. Latent defect, not
  a leak.
* **R13 closed with an instrument, not a caveat.** §2 of
  `docs/TIME_SAVINGS.md` quoted per-stage machine times from a terminal session
  whose output was never saved. `tools/microbench.py` re-derives them from
  committed artifacts. **The instrument caught its own first error**: the
  whisper backend caches chunk checkpoints in the audio file's parent, so every
  measured run after the warm-up read a JSON off disk and reported **796 ms for
  a transcription that takes 39 s** — a 49× understatement. Pointed at §2's own
  surviving source and transcript, the table reproduces (transcription −1.9%,
  render 16:9 +0.5%, render 9:16 −3.9%, exports within 6%; editorial to 19% with
  a named workload difference). See §2a and §2b.
* **Number corrections, each re-derived rather than argued.** "Three of seven
  sources slower than real time" → **two** (1.140 and 1.746). The 252 s
  real-time crossover relabelled as the *fit's* property, R² = 0.390 over n = 7,
  with one of the only two sources below it faster than real time. The
  cloud-compute row recomputed to $0.0195 from $0.021–$0.024, where $0.024
  matched nothing at all. The "32% more machine time" for unusable sources
  replaced with 4.15× (+315%), because the 32% understated by ~10×.
* **README and gap-map honesty pass.** The NLE affordance now says plainly that
  no editor has ever imported a timeline and names the cheapest route to real
  evidence; the input line now says the YouTube path is verified against
  archive.org and that the one YouTube observation left no artifact. The
  no-fabrication bullet concedes the claim holds at the editorial layer and
  *not yet* at the transcription layer, where one source has a measurable
  coverage collapse and a second's transcript is simply unverified.
* **The no-speech item was stale, and the staleness was the finding.** The
  outstanding-work list said the no-speech error was "open, diagnosed,
  unfixed". It is fixed and tested. What is *actually* open is narrower and
  more interesting: no real run has ever gone through the recovery path, and
  the three real `NO_SPEECH_DETECTED` corpus rows predate the fix, so their
  media deleted and their `log_tail` carrying the old undistinguishing message,
  they cannot now be classified. `tools/corpus_run.py` therefore records
  `no_speech_cause` as a structured field, pinned by three tests that were
  verified to fail without it.

What did **not** change: no NLE has opened an artifact, no editor has been
contacted, no participant has been recruited, and no revenue or user exists.
This session moved evidence from "asserted" to "reproducible"; it did not move
any of the four unproven things into the proven column.

---

A real-world release validation pass, run on real input through the web shell
rather than by reading code. Five defects were found and fixed, each with a
regression test; three were verified to fail on the pre-fix code.

* `5265ed2` fix(edl): advance record spans from the previous event's snapped
  out-point — a one-frame overlap the validator caught only *after* renders and
  captions were written.
* `ec1b84e` fix(download): keep yt-dlp progress off the JSON wire; accept a
  muxed `.mpg`. The wire bug is the *inherited child* case that a Python-level
  `sys.stdout` swap cannot catch.
* `af1e686` fix(pipeline): name the missing speech when a source has no audible
  track, instead of letting it surface as an editorial rejection.
* `2571154` fix(reframe): the 9:16 crop is now SAR-aware, so it is 9:16 in
  *display* space. On the real NTSC source the output went from displaying
  1088:2187 (≈1:2) to 1232:2187 (≈9:16).
* `2dd4ff1` docs: validation record, updated gap map, and
  `tools/editorial_audit.py` (the repeatable audit).

Real end-to-end evidence lives in `docs/release-validation.md`; the
per-capability status table is `docs/product-gap-map.md`. A genuine
rights-permitted URL (`archive.org/details/NASA_360_program5_HD`, 24:29 of
two-host unscripted speech) ran the whole chain green: download → transcript →
selection → both renders → captions → EDL/FCPXML → QA, plus the editorial audit.

### This session — the interchange was independently validated, not just emitted

Priority was the NLE gap (G2), which no amount of code can close without an
NLE present. What could be closed was the *evidence* gap, and four real
defects fell out of looking for it. Each is a class, not an instance, and each
is pinned by a regression test:

* **A relative asset URI would have imported every clip offline.** The
  exporter wrapped the media path in `file://` using `PurePath`, which never
  touches the filesystem, so a CLI invocation with a relative source path
  emitted `file://out/...` — and an NLE resolves that against the *timeline
  file's* directory, so the sequence opens with every clip missing and the
  user faces a relink dialog for a file that never moved. Found on six
  freshly regenerated timelines, so it was the live path, not a stale
  artifact. The same latent bug existed in `engine/fcpxml.py` as a silent
  fallback. Both now resolve the path and fail loudly with the path named.
* **An asset declared the clips' extent, not the media's.** Cut 03's asset
  declared 671.371 s while its clip read to 671.404 s — one frame past the end
  of the asset hosting it. The timeline never carried the probed media
  length, so the exporter inferred it from the furthest clip and rounded down.
  The pipeline now probes the source duration once and passes it through.
* **The landscape deliverable shipped 4:3 labelled 16:9.** Exactly the same
  class of bug as the vertical one the previous session fixed, on the other
  axis, and also asserted by a comment in the code that was false. The render
  now fits the source into an exact display aspect by *padding rather than
  cropping* — cropping is an editorial decision the pipeline is not entitled
  to make unsupervised, and on a talking-head source it takes heads off. QA
  asserts the display aspect derived from the sample aspect ratio, and
  because the render cache keys on QA validity, strengthening QA re-renders
  stale deliverables automatically.
* **The path guard's sibling-prefix bypass.** `startsWith(ROOT)` string-matches
  a sibling directory sharing the project's name prefix. The boundary must
  include the separator.

The validator itself was fixed twice over: its overlap cursor was reset *per
event*, and an event is one edit split across channels, so the cursor was
empty for every channel on every event and **the check could never fire** —
caught by the audit's own test suite, which is the argument for testing the
validator as well as the product. A deliberately single-preset export now
SKIPs rather than fails.

Reproduce with `PYTHONPATH=/tmp/nlelibs:tools .venv/bin/python
tools/nle_check.py out/web/pipeline/project` — 92 checks pass, 0 fail. The
tool deliberately does **not** use SelekyT's own parsers to validate
SelekyT's own output: the EDL is parsed by `pycmx`, an independent CMX 3600
reader installed outside the project venv so SelekyT's dependencies cannot
influence it, and the renders are measured by `ffprobe`. Status, including
what is still *not* verified, is recorded in `docs/NLE_COMPATIBILITY.md`.

**Re-ran the pipeline on the real source** to regenerate all twelve
deliverables. Selections are deterministic — cuts 01–03 are byte-identical to
the previously documented evidence — but the run now yields six clips rather
than three, so the run is 6×(2 aspect ratios). Both presets measure correctly:
`shorts` 308×486 SAR 8:9 → DAR 1232:2187 (0.5633, ≈9:16); `facebook` 864×486
SAR 1:1 → exactly 16:9.

### This session — the document set was measured, not merged

The multi-agent instruction was to synthesise agent findings rather than merge
them. That meant re-verifying every load-bearing number the agents produced,
and the verification found real defects — in the agents' output and in the
repo's own record. Five committed doc corrections resulted, plus five new
protocol documents.

**A metric that did not measure what it claimed.** `tools/editorial_audit.py:check_silence_share`
reports "silence share" but computes `1 - spoken_overlap/kept_duration` purely
from transcript word timings over the edit plan's intervals; **it never decodes
audio.** Clip 05's 40.8% looked like 7 s of dead air in a 17.68 s clip, and the
docs of record explained it as "a deliberate pause". Measuring the window
showed the explanation was invented:

* mean −31.4 dB, max −14.1 dB, **0.00 s** of silence at `-40dB:d=0.25`;
* spectral flatness 0.007–0.012 sustained across all 7 s — a **music bed**;
* standalone re-transcription with the same `small`/int8/beam-1 model returns
  only "Ever.", so the pipeline dropped no speech and the transcript is sound.

The metric's *name* is the defect. `docs/METRICS.md` A2.4 now says so and asks
for a rename; the runbook tells a presenter not to quote the column as a
silence measurement.

**A measurement that was a tool artifact.** The runbook claimed all six renders
held 0.00 s of silence. That figure came from running `silencedetect` at
`-v error`, which **suppresses the filter's output** — an empty result that
read as "no silence found". Re-run at default verbosity, clip 04 holds 0.66 s
(0.28 + 0.37) of 14.01 s. The honest floor is ≤4.7%. Two lessons: every number
here now names its command, and a probe that returns nothing is a result about
the probe, not about the system.

**A cost model describing a run that no longer existed.** `docs/BUSINESS_MODEL.md`
was written before the artifacts were regenerated at 07:36 and shipped 3 clips
per preset; the artifacts carry 6. Every downstream figure was stale. Yield
3.98%→6.753%, delivered 116.95 s→198.507 s, total RTF 0.2849→0.2917, all-in
$0.00098→$0.001035 per source-minute. **The conclusion survived and
strengthened**: compute for a 300-minute month is $1.43 against a $29 plan, a
95.1% gross margin. The drift's *direction* is the point — yield rose 70% while
cost rose 5.6%, because yield multiplies only the render stage.

**Two integrity defects in investor-facing documents.** `INVESTOR_READINESS.md`
quoted a 3.6–14.7% silence range for the six-clip run, quietly omitting clips 05
(40.8%) and 06 (17.9%) — a selective range in a document a diligence reader
would trust. And RED_TEAM stated "31 of 140 commits are machine-authored" when
the measured figure is **45 of 154**. Both corrected, with the command to
reproduce them.

**What the verification also confirmed:** the `pycmx` finding is real — without
it installed, six independent-parse checks fail hard rather than pass, so the
audit reads 86/0/6 instead of 92/0/0. `pycmx` is declared in `pyproject.toml`'s
`nle`, `dev` and `all` extras, and `.github/workflows/ci.yml` installs
`.[dev,llm,nle]`, so the independent-parse check is *able* to run in CI. An
earlier note here said it was in no extra at all — wrong; see
`docs/TECHNICAL_AUDIT.md` §CI for the correction. The gap that remains is not
the dependency: CI runs the suite but never invokes `tools/editorial_audit.py`
or `tools/nle_check.py`, so a change that breaks the render/interchange
agreement is caught only when a human audits a project directory.

The five new documents are `EVALUATION.md`, `TIME_SAVINGS.md`,
`TRACTION_DASHBOARD.md`, `USE_OF_FUNDS.md` and `INVESTOR_PIPELINE.md`. Each was
checked before committing: the budget sums to exactly $1,000K; the 393.6 s
machine-time figure is a verifiable sum of components; the 18×–73× ratio is
labelled a model with an unobserved denominator and carries a falsifier
declared in advance; and outreach remains unauthorized with no investor named
anywhere.

### This session — the corpus ran, and it changed what is known

The phase's instruction was to stop treating documentation as the primary
work and start producing external product evidence. The corpus is that work:
**11 sources across all eight required axes, run end to end, outcomes
recorded in `corpus/results.jsonl` including every failure.** 7 produced
deliverables and 4 did not, and each of the four failures is diagnosed in
`docs/CORPUS_RESULTS.md` rather than dropped.

Three things changed about what SelekyT can claim.

**One: the strongest evidence in the repo is now a negative result.** Two of
the seven "OK" sources — Telugu (`telugu-tutorial`) and Hungarian
(`hadhazy-protest`) — produced transcripts that read as fluent-looking phonetic
noise. (An earlier draft of this sentence said three and named Hindi; that
confused `hindi-baiga-interview`, which is one of the four *failures* —
`NO_STORIES_PASSED`, zero clips — with the seven that delivered.) Every
mechanical audit passes on those two. The
caption-transcript equality check compares captions against the transcript,
not against reality, so a wrong transcript is faithfully reproduced into wrong
captions that pass. Geometry, timing, EDL/FCPXML agreement: all green, all
meaningless. This is the direct, concrete justification for the blinded
protocol, and it promotes the WER criteria (P8/P9) from an
academic item to a highest-priority follow-up. P8's English half has since been
closed at 3.38% on LibriSpeech test-clean. P9, non-English, is now *measured*
rather than merely observed: Telugu scores WER **151.08%** on OpenSLR SLR66
under the same harness — 0 of 50 utterances inside the 25% bar, best 71.4%.
The reading judgment above is confirmed by a reference transcript, which is
worth stating plainly because the same paragraph also admits that reading
judgments are not evidence. Of the six C10 languages the criterion actually
names, one is now measured and passes — Polish at WER 11.01% with 34 of 35 clips
inside the bar — and five remain unmeasured; Telugu is a language outside that
set, chosen because it is the one the pipeline demonstrably cannot handle.

**Two: two real defects, found by running new sources rather than by reading
code.** Both fixed, both pinned by a test that fails against the pre-fix code.
* The rendered deliverable shipped **with no audio stream** on the OOI source
  (AV1/Opus WebM): input seeking dropped the audio on 7 of 8 sampled positions
  while the container provably held 7601 audio packets across all 152 s, and
  ffmpeg reported nothing. `engine/qa.py` caught it and failed the run — the
  reporting layer was already correct; what was missing was producing a
  correct file.
* `tools/nle_check.py` compared each EDL's source in/out against the
  **selection span** rather than the retained intervals the render
  concatenated, and reported a 26-frame disagreement about an export that was
  correct. Verified the fix still catches a real error: perturbing one clip's
  intervals FAILs it while its neighbours stay PASS.

**Three: the interchange strategy question is settled with sources, not
reputation.** FCPXML is imported by 2 of the 4 editors (Premiere "does not
recognize" it; Avid's 1317-page guide has no FCPXML path at all); EDL covers
3; the existing dual export is what covers the set. Reel-name length is
already ≤8 and already tested, so the research validated code rather than
requiring a change. Recorded in `docs/NLE_INTERCHANGE.md`.

Determinism was re-verified on a **fresh download** of the reference source:
selections byte-identical between two independent runs weeks of commits apart
(P13 holds on a real re-run, not only in a unit test).

**A claim in this section was wrong and is corrected here.** An earlier version
said all seven OK sources passed `tools/nle_check.py` with 0 FAIL. The tool
could not have reported that: `tools/corpus_run.py` deletes the source media
after each run, so every relink check was failing on a missing file the harness
itself removed, and every frame-rate check reported "could not probe the
source". `nle_check.py` now records probe facts the run itself cut on and marks
those checks NOT RUN with the reason, rather than presenting them as passes or
as export defects. Separately, `tools/editorial_audit.py` fails on **four of
the seven** (`emwcon-panel-2018`, `ooi-vertical`, `pentagon-esper-briefing`,
`royal-society-cloke`), every failure a selection bound landing inside a word —
a real defect, fixed in `engine/edit_plan.py`, whose corpus artifacts predate
the fix. There were never "six pycmx checks that used to skip": `pycmx` is in
the `nle`, `dev` and `all` extras and is installed in CI, and the real skip
population is 9 media-fixture skips in `tests/test_proxy_validation.py` plus 1
in `tests/test_sidecar_parity.py`, which no-op on every CI run.

**The honest limit, unchanged:** no NLE has opened an artifact. An NLE binary
*does* now exist on this host (Blender 4.0.2), but it was tested and has no
CMX 3600 EDL import operator at all — `bpy.ops.sequencer` exposes no import
operator in 4.0.2, so that route is closed and recorded as a negative result
rather than left as a possibility. What is established is mechanical
self-consistency across containers, frame rates, aspect ratios and languages —
which is necessary and is not sufficient.

## Outstanding work

In rough order of how much it would cost a user (fuller list in
`docs/release-validation.md`):

1. **YouTube is intermittently fetchable, not reliably — and the one positive
   observation is unverifiable from this repo.** The blanket
   "Sign in to confirm you're not a bot" block of the previous session was
   *not* a permanent state: one full download through SelekyT's own CLI
   succeeded (640×360, 213 s, 11.8 MB, unauthenticated android
   client). But a second video was blocked while the first transferred, so the
   blocking is **per-video and intermittent**, not a hard IP ban and not a
   code defect. **No artifact from that fetch survives**: the only URL-sourced
   project in the repo is `out/web/pipeline/project`, whose `source` is the
   archive.org URL, and no `youtube`/`youtu.be` reference appears in any
   project's `report.json` or `pipeline_state.json`. A diligence reader cannot
   check the claim against anything committed, so `docs/product-gap-map.md`
   now carries it as an *un-artifacted session note* rather than as evidence,
   and the two documents no longer contradict each other about it. Treat
   YouTube as *partially* observed: one end-to-end fetch was seen, reliability
   has not been measured, and the observation itself would need to be repeated
   — and the artifact kept — before it counts. Chapters, members-only and age
   gates remain entirely untested.
2. **No NLE has opened an exported timeline — still.** What changed is the
   evidence behind that "structurally valid": an independent CMX 3600 reader
   now accepts every EDL, the EDL and FCPXML agree with each other and with
   both rendered MP4s to within 1.63 frames, and source in/out points agree
   with the selection to 0.0–0.7 frames. That is the strongest evidence
   obtainable *without* an NLE, and it is not the same as compatibility —
   see `docs/NLE_COMPATIBILITY.md`. The FCPXML is also **not** validated
   against Apple's XSD: the schema could not be retrieved, so the vocabulary
   check is a guard over what SelekyT emits, not a claim Apple's full schema
   was applied. The gap closes with one zero-capital experiment named in that
   doc.
3. **No video upload in the web shell** — a video must already be inside the
   project tree (`guardedPath` confines it), while the CLI has no such
   restriction, so the two entry points disagree about what a user can point at.
4. **Desktop shell is unbuilt** — Tauri compiles and passes clippy, but no
   binary is packaged and the webview has never rendered (no webkit2gtk).
5. **Optional backends untested here** — sherpa-onnx, silero, ollama are wired
   and degrade cleanly by design, but unit-tested only.
6. **Editorial judgement is unmeasured at scale — and the corpus proved the
   mechanical audits cannot substitute.** Two of the seven "OK" sources have
   transcripts this repo cannot stand behind, and they are different kinds of
   claim: `telugu-tutorial` has a **measured** coverage collapse (4.5% of a
   339.6 s file), while `hadhazy-protest` is **unverified** — grammatical
   Hungarian over 87% of its audio, with no reference transcript or WER to say
   whether the words are right. Both passed every audit, because the
   caption-transcript equality check compares captions to the transcript, not
   to the audio. `docs/BLINDED_PROTOCOL.md` is the design that would settle it;
   **it has not been run, and no editor has been contacted.**
7. **The no-speech error is fixed in code but has never fired on real media.**
   This item previously said "open, diagnosed, unfixed", and that is stale: the
   pipeline now measures `peak_dbfs` on the extraction, *remuxes and retries*
   when the audio has real level but no transcript, and the message names which
   of the two causes applied (`_no_speech_diagnosis`), with
   `tests/test_container_recovery.py` covering both branches. **What has not
   happened is a real run through that path.** No committed transcript carries a
   `container_recovery` marker, and the three real `NO_SPEECH_DETECTED` rows in
   `corpus/results.jsonl` predate the fix — their `log_tail` shows the old
   message that did not distinguish anything. Their media is gone, so they are
   unclassifiable now, which is why `tools/corpus_run.py` records
   `no_speech_cause` (`silent_track` / `damaged_container` / `unknown`) as a
   structured field from this point on. The claim "this distinguishes a damaged
   container from a silent track on real media" is **still unvalidated** — it
   has only ever been shown on synthetic files.
8. **Non-English transcription quality is now measured, and it is broken.**
   The `small` model produced garbage in Telugu, Hungarian and Hindi. English is
   the exception: WER is measured for clean read English at **3.38%** on
   LibriSpeech test-clean (`tools/wer_bench.py`, three runs, production decode
   parameters) — P8's C9 half is closed. Non-English was, until this session,
   an *observation from reading transcripts*; it is now a score. Telugu (OpenSLR
   SLR66, 50 utterances, 3.46 min, same model and decode parameters) measures
   **WER 151.08% / CER 127.40%**, with **0 of 50** utterances inside the 25% bar
   and a best single utterance of 71.4% (`corpus/wer/runs/telugu-small.json`,
   manifest and provenance committed at `corpus/wer/telugu-slr66-female/`).
   WER above 100% means the output carries more error words than the reference
   contains — the model is not transcribing Telugu, it is emitting plausible
   script. The observation from reading was correct, but a correct guess is not
   a measurement, and the two are now recorded separately. Telugu is not one of
   P9's six C10 languages, so the criterion is still formally unmeasured on its
   own corpus; what this does establish is that the failure is a decode failure
   at this model size and not a transcription-pipeline failure, because the
   English path on the same harness scores 3.38%. The `medium` run on the same
   Telugu corpus has finished, and it bounds the question in the worse direction:
   **WER 91.54% / CER 69.24%, with 1 of 50** utterances inside the 25% bar
   (`corpus/wer/runs/telugu-medium.json`). Capacity is a real lever — 151.08% to
   91.54% — and it does not reach the bar, so this is a *language-fit* gap rather
   than a size gap. Buying compute does not close it, which is what keeps the
   CPU-only cost story coherent on both sides of the language boundary.
9. **P9's own criterion is one-sixth measured, and that sixth passes.** Polish —
   one of the six C10 languages the criterion names — scores **WER 11.01% /
   CER 2.45%** on the same `small` model, with **34 of 35** clips inside the 25%
   bar and diacritics intact (Multilingual LibriSpeech test, 35 clips, 7.79 min,
   1054 reference words; `corpus/wer/runs/polish-small.json`). Telugu is *not* a
   C10 language, so the two results read together rather than as one finding: the
   failure is localised to Indic, not to "everything outside English". The
   criterion needs **3 of 6**, so German, Spanish, French, Dutch and Italian
   remain unmeasured and P9 cannot be claimed. The Polish number does not assume
   any of them, and clean read audiobook prose is the easiest condition the bar
   will be faced with — so it is evidence the bar is reachable, not evidence it
   is met.
10. **A candidate's text described a different clip from the one that ships.**
    Selection bounds are estimated proportionally to character span, so a bound
    can land inside a word; the word list is real timing and the cut snaps to it,
    which left the *text* built on the pre-snap bound. On the pentagon re-run,
    clip 04 shipped opening on "Syria." (the snap had pulled the bound back
    364.84 → 364.50 s to keep the word whole) while its own `text` field began at
    "has" — so the content pack's hook described a clip that was not the rendered
    one, and `tools/editorial_audit.py`'s caption-vs-text check fired on a real
    desync rather than a false positive. Fixed in `align_span`, which snaps both
    bounds and rebuilds the text from the words kept inside them. Measured on the
    committed corpus, 3 of 27 candidates with word timing were corrected and the
    other 24 are byte-identical — a repair, not a rewrite. Verified by re-running
    the source: clip 04's text now opens "Syria.", matching its first caption cue.
11. **Vertical reframing verified on one anamorphic + synthetic sources only** —
   the SAR fix is provably correct *geometry*, but whether the wider crop keeps
   a moving subject well framed is not measured. A 9:16 cut from SD footage is
   sub-SD (308×486), a property of the input.
12. **Frame-rate and codec coverage is narrow.** The corpus added 25, 30 and
    29.97 fps. 23.976, 50 and 59.94 remain untested, and no 4K/H.265 source was
    attempted — still the largest unquantified risk in `docs/BUSINESS_MODEL.md`
    §9.

**One OPEN finding that is noise, recorded so it is not mistaken for a defect
later.** `tools/editorial_audit.py`'s `check_cuts_respect_sentences` flags six
committed cut edges across five clips (emwcon 01 and 05, pentagon 02 and 04,
ooi 01) whose adjacent words the sentence splitter holds in one sentence. They
are not one cause, and the split matters:

- **Three are ASR under-punctuation.** The splitter merges what should be two
  sentences and the cut lands at a boundary the transcript does not mark:
  "...continuous contact with them What was used to destroy the compound"
  (pentagon 02, both edges) and "...once everybody comes I wanted to add..."
  (emwcon 05). A real turn boundary is being cut at, and the audit is right
  that the transcript does not show it.
- **Three are commas inside one long question or clause:** "begun, and" and
  "was, at" and "arrays, traveling" (pentagon 04, emwcon 01, ooi 01). Here the
  transcript's *own* punctuation says one sentence, so the check is enforcing a
  stricter rule than the transcript supports. Cutting at a comma between clauses
  is a defensible edit, though not a sentence edge.

The largest removal in the group (a 5.76 s gap in pentagon clip 02) is a real
turn boundary and the cut is right either way; the others trim inside a clause.
The signal is too noisy to tune a threshold against — fixing it would mean
trusting sentence boundaries from a transcript that demonstrably omits some and
over-joins others — so it stays as an audit caveat, not a bug to close.

## Current strategic objective

The phase's instruction was explicit: **stop treating documentation as the
primary work; the bottleneck is insufficient external product evidence.** The
corpus was the answer to that, and it moved the objective rather than merely
progressing it.

Where the objective stood before — "discover whether it actually produces good
short-form edits from real-world human speech and real URL sources" — the
answer is now *partially in and uncomfortably specific*: the machinery is
sound across containers, frame rates, aspect ratios and languages (11 sources,
7 delivering deliverables, determinism byte-identical across independent
downloads), **but two of those seven produced transcripts that are phonetic
noise, and no mechanical check can see it.** "Clean" is not the right word for
the seven: **five of them** (`nasa360-ep5`, `emwcon-panel-2018`,
`pentagon-esper-briefing`, `ooi-vertical`, `telugu-tutorial`) still fail
`tools/editorial_audit.py` as of 2026-09-20, and the failures are of two
different kinds that must not be bundled — three sources fail on
`check_cuts_respect_sentences` (six cut edges; analysed as ASR under-punctuation
and comma-clause cuts in the OPEN note above, i.e. audit noise), while
`nasa360-ep5` clip 05 and `telugu-tutorial` clip 01 fail on *silence share*
(40.8% and 46.3% dead air in the rendered clip), which is a real pacing property
of those two clips and not an emitter bug. Only `royal-society-cloke` and
`hadhazy-protest` pass everything. Separately, **six of the seven** still carry
relative EDL asset paths; only `royal-society-cloke` is absolute, because it is
the only one re-emitted after the URI fix. The sentence-split and EDL-path items
are artifact debt — the files on disk were written by emitters that have since
been fixed — but the dead-air figures are properties of the selections
themselves and would survive a re-run.

So the objective splits into two things that must not be conflated:

* **Mechanical correctness** — largely established, and the remaining hole is
  NLE import, which is zero capital.
* **Editorial quality** — not merely unmeasured, but demonstrated to be
  *unmeasurable by the audits that exist*. That is what
  `docs/BLINDED_PROTOCOL.md` exists for, and it has not been run. No editor has
  been contacted; the standing instruction forbids contacting anyone without
  founder approval.

The next milestone is still the one the phase named: **a real editor uses
SelekyT, saves measurable time, and would choose to use it again.** Nothing in
this session's work should be reported as progress toward that milestone. What
this session produced is the evidence that the milestone is not yet met, and
the cheapest tests that would move it.

## Next 10 highest-leverage tasks

Ordered by evidence gained per unit of work, not by lines of code.

*Closed this session:* "run the editorial audit across 3–5 more real sources"
(exceeded: 11 sources audited — 2 pass every check, 5 fail on sentence-split or
dead-air checks, see the note above) and "add a second source to the
determinism check" (exceeded: two independent *downloads*, byte-identical
selections).

1. **Import a timeline into a real NLE. This is the single highest-leverage
   action available and it is still open.** It is zero capital and one
   engineer-day: install DaVinci Resolve's free tier on any machine with a
   display (macOS or Windows — Resolve on Linux needs an unsupported
   workaround), import `out/web/pipeline/project/timeline/01.edl` and
   `01.fcpxml`, and record whether the project opens, whether the clips are
   online or offline, whether the timeline duration matches the rendered
   `facebook/01.mp4` (24.892 s), whether the source in-points match the
   selection (414.30 s), and whether a relink dialog appears. The pass
   criterion is decided in advance: **the sequence opens with all clips online
   and its duration matches the render to within two frames.** Anything less
   is a defect to fix, not a result to report. Until this is done, every
   external statement about NLE compatibility must say what
   `docs/NLE_COMPATIBILITY.md` says: structurally validated by an independent
   reader, duration verified against the render, **never opened in an NLE**.
2. **Re-run the three non-English sources at `medium` and `large-v3`.** This is
   the cheapest test that addresses the finding the mechanical audits cannot
   see (item 6 in Outstanding work). It is a model-choice question answerable
   by re-running `tools/corpus_run.py --only telugu-tutorial,hadhazy-protest,
   hindi-baiga-interview` with a different `--model`, and it decides whether
   the product can claim multilingual support at all. Budget compute: `medium`
   is several× `small` on a 4-core CPU-only host.
3. **Fix the no-speech misdiagnosis** (Outstanding work item 7): remux to a
   clean container and retry before reporting "no audible spoken content", so
   an unreadable container stops being described as a speechless source.
4. **Score WER against `corpus/manifest.json`'s C10 non-English audio-only
   corpora** (`docs/EVALUATION.md` §2.2) — the ground truth the video sources
   lack. The English half (C9, LibriSpeech test-clean) is **done**: 3.38% WER
   on `small`, three runs, production decode parameters (`tools/wer_bench.py`,
   `corpus/wer/runs/`). P9 — non-English — has never been measured, and the
   corpus's non-English findings are still impressions from reading
   transcripts, not scores. That is now the specific hole, not "WER in
   general".
5. **Get a *second* YouTube fetch observed** to establish whether the
   intermittent block is per-video, per-hour, or random. One success is not
   reliability. Use `--cookies` / a browser cookie file, a residential proxy,
   or a non-datacenter network if the block recurs.
6. **Test the `.mpg` path end to end.** The accept-any-container fix (defect 2)
   is pinned at the discovery step only; a full run through a real MPEG-PS
   source would exercise transcription, render and export on that wrapper.
7. **Add a 23.976, a 50 and a 59.94 source, and one 4K/H.265 source.** The
   corpus covered 25/30/29.97; the rest is untested and is the largest
   unquantified risk named in `docs/BUSINESS_MODEL.md` §9.
8. **Resolve the web-shell / CLI input disagreement** (G3). Decide between
   exposing a video upload route or documenting the project-root confinement —
   the previous session decided URL-first with no code change, which is why
   upload is still missing.
9. **Package the Tauri shell once, on one platform** (G4), or record explicitly
   that v1 ships CLI + web shell only. Do not spend main effort here until G1
   and G2 are settled.
10. **Audit the preserved worktrees for now-merged or superseded work** and
    record the verdict in `docs/agent-worktrees.md`. The timeline worktree in
    particular may duplicate or conflict with live `engine/timeline*`.

## Important decisions

* **URL-first web UX, no code change.** The web shell already accepts a URL in
  the same field and dispatches it to the same sidecar path as the CLI, so a
  separate video-upload route was judged redundant work. This is *why* the
  upload gap in G3 was left open deliberately, not overlooked.
* **The 9:16 fix keeps the frame anamorphic rather than emitting square
  pixels.** Scaling the crop width by SAR gives a correct display ratio with
  zero resampling. The alternative (`setsar=1`) gets the ratio right only by
  lying about the pixel shape and would stretch content ~12.5 % horizontally.
  If a platform is later found to ignore container SAR, revisit this.
* **The speech-free guard is placed outside the transcription branch** so a
  *cached* empty transcript is also caught, not just a fresh one.
* **`tools/editorial_audit.py` reads only artifacts a normal run already
  writes** and prints failing checks, so it is the repeatable benchmark the
  phase asked for rather than a one-off script. Prefer extending it over
  writing ad-hoc checks.
* **No `Co-authored-by` or generated-by metadata in commit messages**, per the
  standing user instruction. Commit subjects use conventional prefixes and the
  body carries root cause plus pinning test. NOTE: an automated reminder in
  this environment periodically instructs the opposite (append
  `Co-Authored-By: Claude Code <noreply@anthropic.com>`). The user's own
  standing instruction takes precedence and has been followed; **flag this
  conflict to the user** rather than silently picking one.
* **Never `git push --force`, never reset or rewrite history, never delete the
  preserved agent worktrees.**
* **EDL is the primary interchange format, not FCPXML.** The research
  (`docs/NLE_INTERCHANGE.md`) inverted the assumption the phase's instruction
  warned against: FCPXML is imported by 2 of the 4 editors — Premiere "does not
  recognize" it and Avid's 1317-page guide has no FCPXML path — while CMX 3600
  EDL covers 3. The existing dual export already covers the union, so the
  finding validated the code rather than changing it, and the 8-character reel
  limit is the CMX standard rather than an arbitrary tidy choice. The decision
  to keep both formats stands; the decision to *describe* EDL as primary is
  new.
* **A corpus failure is a result, not an interruption.** `tools/corpus_run.py`
  never raises on a difficult source and writes the record including the log
  tail; the only non-zero exit is a harness failure. Two of the four
  non-OK sources are *correct* rejections (a silent film and a music reel),
  and the distinction between "correctly rejected" and "misdiagnosed" is only
  visible because the failures were recorded rather than retried or dropped.
* **Downloaded media is not evidence.** `corpus/media/` is gitignored and
  regenerable; the transcript, selections, edit plan and EDL/FCPXML are the
  evidence and they are committed. Keeping 11 sources on disk filled the
  filesystem to 118 MB free and took every Bash call down with it.

## Known risks

* **The classifier that gates Bash can time out**, and a timeout mid-`git stash
  pop` once left the working tree on pre-fix code. It was caught and restored.
  Do not stash a fix mid-verification unless the restore runs in the same tool
  round; prefer reproducing the defect with an explicit pre-fix command or a
  scratch checkout.
* **`git stash` + classifier is not the only fragile pattern**: the shell cwd
  resets between Bash calls, so a command that assumes it is still in the repo
  root will silently run against `/home/azureuser`.
* **The anamorphic fix changed the vertical crop width for every source**, not
  just anamorphic ones — square-pixel sources now round to an even width
  (e.g. 274 px at 486 tall instead of 273). This is why the existing
  square-pixel 9:16 test still passes but on slightly different geometry.
* **Disk is the binding constraint on real runs.** 3.0 G free, and one real
  source is 883 MB. A second long source plus its renders will not fit
  comfortably; check `df` before any end-to-end run and never delete project
  data, git history, worktrees or caches blindly. Reclaimable caches come first:
  `corpus/wer/downloads/` holds regenerable archive zips and is gitignored.
* **The docs record is only as good as the last real run.** `docs/product-gap-
  map.md` carries an "as of `<hash>`" header, and that header had drifted: the
  map was last substantively updated in `2dd4ff1` but still read "as of
  `b1dcfa7`", so a reader following this very instruction found a doc whose
  freshness marker pointed two updates back. The header is now `3fc11e1`; any
  future change to a verified path must bump that header *and* update the row
  with what was actually exercised, not what the code now claims to do.
* **A probe that returns no output is a result about the probe.** `silencedetect`
  at `-v error` returns nothing and looks like "no silence found". Two doc
  errors in this repo came from exactly that. Filter *after* capturing, never
  by suppressing the tool's own output channel.
* **The same class applies to empty transcripts.** `NO_SPEECH_DETECTED` can
  mean the source is speechless (correct — a silent film, a music reel) *or*
  that the container is unreadable (incorrect — a damaged `.ogv` that
  transcribes to 11 segments after a clean remux). The error message does not
  distinguish them, and the second case tells a user something false about
  their own media. Open defect, diagnosed in `docs/CORPUS_RESULTS.md`.
* **`ffmpeg` failing silently is a real failure mode, not a hypothetical.**
  Input seeking dropped the audio stream on an AV1/Opus WebM and exited 0. The
  only reason the artifact did not ship is that `engine/qa.py` probes the
  result. Any new render path must be covered by a QA probe, not by an
  ffmpeg exit code.
* **Every measurement carries its command, and counts drift within a day.** The
  doc set held three different test counts (819, 824, 834) and two commit counts
  (140, 154) simultaneously, and the machine-authored share was stated as 31
  when it was 45. A quoted count is stale the moment it is written; a
  re-runnable command is not.
* **Agent output is material to verify, not material to merge.** Two of five
  new documents contained figures contradicted by the artifacts they described,
  and one external citation (a BLS median) was for the wrong occupational group.
  The multi-agent instruction was explicit on this point and it was followed:
  every load-bearing number was re-measured before any of it was committed.

## What another Atria session must know

* **Do not trust claims in the code about its own output.** Two of the five
  defects (the 9:16 ratio, the "is 9:16" comment in `engine/render.py`) were
  *asserted by the code and false*. The `crop=ih*9/16:ih` filter even carried a
  comment saying it had produced "an exact 9:16 frame". Verify geometry,
  durations and cue counts by probing the rendered files.
* **Read the four docs first**: `docs/CORPUS_RESULTS.md` (what 11 sources
  actually did, including the finding the audits cannot see),
  `docs/BLINDED_PROTOCOL.md` (the evaluation that would settle editorial
  quality — designed, not run), `docs/NLE_INTERCHANGE.md` (why EDL rather than
  FCPXML is the primary interchange, and what is *not* verified) and
  `docs/release-validation.md` (what was actually run, what failed, what was
  fixed). All four distinguish "code exists and unit tests pass" from "a user
  can rely on this".
* **Start any work by re-running the suite** (`.venv/bin/python -m pytest -q`)
  and confirming **1034 passed / 12 skipped** (113 s, 2026-09-20). A number other
  than that means something has drifted. The count was refreshed across the doc
  set after RED_TEAM, AI_EXECUTION_STATE and an agent appendix disagreed within
  one day (824, 819, 834); it has moved 819 → 824 → 834 → 837 → 890 → 895 → 898
  → 1031 → 1034 as regression tests landed, and the skip count moved 10 → 7 → 12
  as gated real-media tests and backend-gated checks arrived. **A quoted count is
  stale the moment it is written** — the "How to run things" note above carries
  the same caveat. The same applies to commit counts — the repo is at **194
  commits, 85 of them machine-authored under the "Ubuntu" identity**
  (`git log --format='%an' | grep -ci ubuntu`).
  This line is itself a self-checkpoint and it has to be edited whenever the
  count changes — a previous session let it sit at 819 through fifteen new
  tests, and the drift was found only because a red-team agent audited it.
* **Two FCPXML exporters exist and only one is live.** `engine/fcpxml.py` is
  the multitrack exporter used by `engine/audio_analysis.py` and
  `engine/multitrack.py`; `engine/nle_export.py` is the one the pipeline
  actually calls (`selekyt/pipeline.py:438`). Both have their own
  `_to_file_uri`. Fixing the wrong one has no effect on any shipped artifact —
  this session did exactly that, and proved it by re-running the pipeline and
  re-auditing. Trace the call site before touching either.
* **A passing audit is not a correct result.** The corpus produced sources whose
  transcripts cannot be stood behind and which pass every check in
  `tools/nle_check.py` and `tools/editorial_audit.py`, because the
  caption-transcript equality check compares captions to the transcript, not
  to the audio. One of them is a measurement (`telugu-tutorial`, 4.5% coverage
  of a 339.6 s file) and one is an admitted gap (`hadhazy-protest`,
  grammatical Hungarian over 87% of its file with no reference to check it
  against — an earlier note here called it "phonetic noise" and that
  characterization had no evidence behind it). Never describe the audit result
  as evidence that a transcript is accurate; describe it as evidence that the
  artifacts agree with each other. This is the single most important caveat in
  the repo and it is the reason P9 (non-English WER) outranks
  every other open item — P8's English half is closed at 3.38%, and Telugu is
  now measured at 151.08%, which is what
  makes the remaining gap a decode problem rather than a transcription problem.
* **The corpus is re-runnable and append-only.** `tools/corpus_run.py
  corpus/manifest.json` skips sources already recorded OK and appends new
  records, so a later session can grow it without re-running it whole. Media
  is deliberately not kept; re-downloading is cheap, and keeping it filled the
  disk once already.
* **The previous phase's own framing applies**: do not simply report that
  SelekyT is complete. **G2 (NLE import) remains the only claim in the venture
  with zero real-world evidence behind it**, and it is the cheapest gap to
  close. The corpus closed several others and opened one that matters more:
  editorial quality is not just unmeasured, it is unmeasurable by the tools
  that exist.
