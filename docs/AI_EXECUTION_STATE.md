# AI execution state — SelekyT

Checkpoint for a following Atria session. Factual as of the commit named below;
every line below was true on this host at that point.

## Current repository state

* Repository: `/home/azureuser/selektyt`, on `main`. Working tree state
  **changes within a session by design** — verify with `git status` rather than
  trusting "clean" here.
  Local `main` tracks `origin/main` — verify with `git log --oneline
  origin/main..HEAD` and `git log --oneline HEAD..origin/main`, both of which
  should be empty after a push, rather than trusting "in sync" here.
  Nothing stashed, nothing in `git stash list`.
  The two untracked leftovers noted by the previous checkpoint are resolved:
  `tools/caption_ocr.py` was already tracked and clean, and the WER run record
  `corpus/wer/runs/nasa-c4-small.json` was committed at `59e5327` alongside its
  tracked siblings.
* **The current line of work is the autonomous product/R&D loop**, not the
  desktop shell: identify the highest-value weakness in the gap map, make the
  smallest change that fixes it, pin it with a real test, and update
  `docs/product-gap-map.md`. The most recent substantive commits are
  `042f400` (the perception layer caches the frames *and* the perception
  derived from them at the one choke point, measured 1.99× on a composed clip)
  and `ad6bd4d` (scene-aware vertical composition, plus an ffmpeg decode
  fallback for AV1 that OpenCV cannot read), then `56253b5` (the vertical
  deliverable was re-mastered from `-preset veryfast -crf 20` to
  `-preset slow -crf 18` after measuring a ladder against a `-crf 0` reference
  on real corpus footage — its own section below), then `7223d30` (the story
  graph stopped being write-only — an edit is generated from it), then the
  **source-acquisition fix** in its own section below, and before that
  `92005ae` (the editorial scan now covers the whole source instead of
  `return`ing at a candidate cap, which had silently made the last ~35% of
  two corpus sources unreachable) and `a1e9150` (the vertical reframe keeps the
  active speaker, with the rendered output itself as the test), then the silero
  VAD work in its own section below (`909ca9e` implementing the neural backend
  and re-ordering the chain by measurement, `2884a1d` fixing the demo sample that
  its own backend scored "no speech found"), then the **coverage-gate fix** in
  the session section below (`4a25594`) — the pipeline's one hard
  transcript-vs-audio guard was reading its audio from `EnergyVAD()`, the
  chain's last-choice, level-blind backend, so it passed the corpus's only
  shipped failure while scoring a real human voice at 0% speech and reporting
  "not computable" in both directions.
  The fix routes the gate through the same backend chain as the selection and is
  measured, not thresholded, in the section below.
  Most recent is the **sentence-splitter and tracker fixes in the top session
  section**: a re-run of the two sources that still hold media closed every
  clip-level defect the corpus had — the sweep is 16 clean / 11 review / 4 fail
  with the four failures all project-level — and the four tracker changes are
  grounded in measurements on the real corpus windows rather than on fixtures.
  Before that, the **engine fix in the next session section**: the audit's
  outward-looking edge check found two orphan words in shipped corpus output, and
  the trace ended at a 2.8e-14 s float comparison and a discarded word list in the
  candidate constructor. A third candidate fix was measured redundant and
  reverted rather than shipped.
  Before that, the **editorial-audit comparison mode** in the next session
  section: the audit's sentence-boundary check only ever saw the words a clip
  kept, so a clip that opened mid-sentence passed by construction — looking
  outward at the dropped words found two real shipped defects, and adding a
  corpus sweep plus a recorded baseline gave the eval the comparison question it
  was missing.
  Run `git log -1` for the current hash rather than trusting a hash printed
  here, since amending this file changes its own commit hash.
  Before this loop, the line of work was the **Windows desktop application**,
  and its acceptance position in `docs/RELEASE.md` is still the authority on it.
  Three claims are kept strictly separate there and should not be blurred here:
  the engine path is proven on a real file through the shipped sidecar binary;
  the Windows build pipeline *produces* installers **and a green MSIX** on
  `windows-latest`; and **no installer has ever been installed or run on a
  Windows desktop.** Built is not packed, packed is not installed, installed is
  not run, and a green `build-windows` badge is not a Windows validation. That
  work is paused, not finished — it is the natural next thing to resume if the
  product loop returns to distribution.
* Test suite: **1804 passed, 16 skipped** (2026-09-28) — measured by
  re-running the whole suite and reading its summary line, not by trusting a
  background task's exit code (see the trap note in "Completed work", and the
  disk note above: a concurrent `cargo test` + `pytest` pair once hit 100% disk
  and both died reporting success-shaped exit codes). This run covers the
  engine fix in the top session section on top of the editorial-audit work —
  the edge check, the three-state verdict, and the sweep/comparison mode — the
  coverage-gate fix and the diagnosis-convergence fix in the earlier session
  sections.
  The net change from the engine fix is +1 in `tests/test_editorial.py`: one new
  test pinning the real-straddle case, three removed that pinned a fix measured
  redundant and reverted, and the existing text/words agreement test rewritten
  around the measured behaviour. The count is small because the fix is two
  lines; the three removed tests were testing a change that measurement showed
  should not ship, and keeping them would have pinned behaviour the engine no
  longer has.

  The engine fix also moved the corpus, and that is the second count worth
  reading. `tests/test_corpus_reproducibility.py` pins how many committed
  selections still reproduce under the current code, and the fix took that from
  four named exceptions to six: `pentagon-esper-briefing` and
  `royal-society-cloke` are the two runs whose clips the audit's edge check
  found opening and closing mid-sentence, and the fix that removes the defects
  is exactly what makes those two selections no longer byte-reproduce. The
  attribution was measured with the audit's own instrument rather than assumed
  — patching the two fixes off in one process returns **both** runs to
  `reproduces` and leaves every other run untouched, so the drift is the fix
  and not the corpus's pre-existing non-reproducibility arriving somewhere new.
  The tool-behaviour tests in that module used `pentagon-esper-briefing` as
  their "a run that reproduces" fixture and moved to `ooi-vertical` for the
  same reason.
  The prior baseline was 1797/16. The growth of seven is exactly the sweep and
  comparison suite added alongside the comparison mode: a count test pinning the
  corpus sweep to `{"runs": 11, "clean": 14, "review": 11, "fail": 6}`, a
  clean-clip/clean-run equivalence test, a five-way classification test over a
  hand-mutated baseline, a review-state test proving it reads as neither fail nor
  pass, a self-comparison test, and the baseline drift guard. The fifteen
  edge-check tests and the rewritten three-state corpus table landed inside the
  1797 figure's own file.
  The prior baseline was 1778/16, and the growth of five since is exactly
  accounted for: `tests/test_coverage.py` went 11 → 16 tests as the gate's contract
  was pinned (the collapse, the quiet source never failed, the chain's probe order,
  the whisper refusal, the union arithmetic and two pipeline tests at the
  corpus's own 340 s scale). `tests/test_audio_analysis.py` is the same count
  with its fixture now shared from `tests/speech_fixtures.py`.
  On top of that, two further additions: one report test in
  `tests/test_coverage.py` (now 17 tests) for the backend-naming fix below, and
  four in `tests/test_speechless_source.py` (7 → 11) for the
  diagnosis-convergence fix — two parametrized cases over the bypass flag, and
  one each for an `ok` coverage report and a missing one, so the corroboration
  clause is proven absent as well as present.
  No regressions. The skip count is unchanged at 16 — the two new
  coverage tests that are host-gated are gated *on* a listening backend being
  present, so on a host with neither silero nor ffmpeg they report the honest
  weaker property rather than failing.
  The prior baseline was 1758/16 at `042f400`, and the growth of fifteen is
  exactly accounted for: ten in the new `tests/test_vad_silero.py` (the
  reference parity, the contrast, the state-carry regression and the PCM
  conformance), three net in `tests/test_vad.py` (seven real backend tests
  replaced four stubs), and two in `tests/test_web_harness.py` (the served
  sample is the generator's current output, and it is voiced to every available
  backend). `tests/test_audio_analysis.py` is the same count with a rewritten
  fixture and a threshold-reach test that no longer depends on one backend's
  saturation point.
  No regressions. This run is also the first that exercises the chain in its
  shipped order on a host where the vendored model is present — the three
  failures the first pass of it found (an empty cutlist on a tone fixture) are
  the session section below, and are the reason the fixtures are voiced
  excitations now.
  Before that the baseline was 1721/14 at `56253b5`; the growth there was
  exactly the fourteen tests the two window caches added — six in
  `tests/test_reframe_frame_cache.py` (a window is not decoded twice) and eight
  in `tests/test_reframe_perception_cache.py` (a window is not perceived twice,
  and the properties that keep the win honest). The two extra skips are the
  environment-gated desktop-shell and ollama tests, not work removed.
  No regressions.
  Before that the baseline was 1675/14 at `7223d30` (the story-memory commit);
  that growth was exactly the **41 tests in
  `tests/test_youtube_format_selection.py**`,
  the source-acquisition policy covered in the session section below. No
  regressions.
  The count this file carried before that was itself stale — **1473/13**, quoted
  2026-09-24 — because the 41 tests behind the story-memory commit and the
  acquisition work landed after it without the line being refreshed. Treat the
  number here as a self-checkpoint that drifts by design, and re-run
  `.venv/bin/python -m pytest -q` rather than trusting it.
  Earlier: 1460/13 at `92005ae`, growing by exactly the six
  new `/api/upload` tests in `tests/test_web_harness.py` (which alone runs
  42/42 in ~48 s): a real video round-trip, a non-media rejection, name
  de-clobbering, a cross-origin rejection, a multipart content-type demand, and
  a truncated transfer that must leave no partial file behind. No regressions.
  That number is the suite, not the product — the honest limits of the new
  intake are written in `docs/product-gap-map.md`: the 32 GiB body cap has never
  been approached, and the route has only ever been exercised with a 3 s file,
  never a genuine 1–3 hour master end to end.
  The same caveat applies to the acquisition tests added this session: they pin
  the policy against format ladders modelled on the real one, not against the
  network, because the network is unreachable from this host. A green suite
  here is **not** the 720p download the fix exists for — see the session
  section for the verification that has not run.
  **18 Rust unit tests** in `apps/desktop/src-tauri` pass, including
  `cancel_unblocks_a_call_in_flight` (0.15 s — see the note below about why its
  fake sidecar is a shell builtin loop, not `sleep`),
  `a_chatty_stderr_does_not_deadlock_the_rpc`, which pins the pipe-buffer
  deadlock fix,
  `spawn_resolves_the_env_override_without_re_appending_the_triple`, which pins
  the `SELEKYT_PYTHON` override lookup (a bare name resolves against the staged
  sidecar directory; a path already ending in the triple is not suffixed
  twice), and six tests for `selekyt_delete_project` — the one destructive
  command the frontend can invoke, which had **no** test until this session.
  Its validation lives in `project_dir_under_root`, extracted so it can be
  tested without setting a process-wide `HOME` (which `dirs_or_err` reads and
  which concurrent tests would race on); both mutations were verified against
  them, removing the parent check failing three and skipping canonicalization
  failing two. `cargo clippy --all-targets -- -D warnings` is clean, which now
  matters because the new `shell` CI job denies warnings on Linux before the
  Windows job pays for a build. The skip count splits into three kinds, and the
  differences matter: **7** are in `tests/test_proxy_validation.py`, gated on the
  `SELEKYT_PROXY_MEDIA` env var, which expects a ~239 MB YouTube recording that
  is not redistributable with the repo. **They are opt-in real-media
  measurements, not missing optional backends** — the suite reports green on
  every CI run while the proxy validation it names does not happen, and that is
  a worse problem than an absent backend. **5** are in
  `tests/test_wer_bench.py`, skipped because `c9-small`, `c9-small-beam5`,
  `c9-medium`, `c9-large-v3` and `redteam-small-verify` predate the
  `wer_err_total` field in their run records — old committed data that cannot be
  re-derived without re-running the models, not a missing environment. The last
  **1** is `tests/test_wer_bench.py` "no run record for dutch" — a language the
  corpus simply has no measurement for yet, so it is a gap in the evidence, not
  a gap in the code.
  The earlier count in this file was 895/7, then 1034/13, then 1036/12, then 1080/12; the suite
  grows as defects get regression tests, and the skip count moves when a gated
  test's media appears or disappears — re-run `pytest -q -rs` before trusting any
  count here.
  **The count moved mid-session and the reason is worth knowing.**
  `corpus/wer/runs/italian-small.json` was written at 02:37 by a background
  process while the suite was being measured, so that parametrized case went
  from SKIPPED ("record not present") to PASSED between two runs of the same
  command against the same tree. `tests/test_wer_bench.py` pins committed WER
  figures against their records and skips when a record is absent, so **any
  count taken while a WER run is in flight is a snapshot, not a property of the
  commit.** A German MLS run is in flight as this is written; expect the count
  to move again when its record lands.
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
* Disk: **1.6 G free of 29 G, 95% used** (`df -h /`, 2026-09-21) — and it
  *filled completely* mid-session. The root filesystem hit 100% with 129 MB free
  while `cargo test` and `pytest` ran concurrently, and **both suites died with
  `No space left on device`, each reporting a misleading exit code**: cargo's
  failure surfaced as `[exited with code 0]` because the pipeline's exit code
  was `tail`'s, not cargo's. A green-looking test run that was killed by the
  filesystem is the same shape as the "background task reported exit 0 without
  running pytest" trap recorded further down — **always capture the real exit
  code into the log, never trust a piped one.** The reclaim was deleting
  `apps/desktop/src-tauri/target` (1.6 G of an *incomplete* debug build that
  cargo had died inside, so it was inconsistent anyway and fully regenerable).
  Do not clear `/tmp` to make room — it holds other projects' files. The disk
  remains the binding constraint: a full Tauri debug build is ~1.6 G, which is
  the entire free space, so Rust work here needs `RUSTFLAGS="-C debuginfo=0"`
  and sequential, not concurrent, suite runs.
  The corpus's downloaded media is deliberately *not* kept — `corpus/media/` is
  gitignored and regenerable from `corpus/manifest.json`, and it had to be
  removed mid-run in an earlier session when the filesystem hit 118 MB free and
  started failing every Bash call. **Two sources' media survives locally:
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

### This session — the language a model cannot transcribe now stops the run, and the table that says so is auditable

The gap map has carried the measurement for two sessions without acting on it:
Telugu scores WER **151.08%** on `small` and **91.54%** on `medium` (0 and 1 of
50 clips inside a 25% bar) where Polish scores 11.01% on that same `small`
model. At 151% the transcript carries more error words than the reference has
words, so every deliverable derived from it — captions, selection, cut points —
inherits the wrongness, and the run reported success. Nothing else in the
pipeline could see it: the coverage gate compares the transcript against the
*audio*, so a dense Telugu source scores a high coverage ratio, well-formed text
and a wrong one; the editorial gates read the transcript's text and cannot tell
a fluent hallucination from a faithful one.

`engine/language_fitness` is the one check that asks whether the *model* can do
this *language*, and it asks a table measured offline rather than asking the
thing under suspicion. It runs before the coverage gate — cheaper, and the
question is more specific — and refuses above `WER_REFUSE = 1.0`. Three
properties, each forced by a real record in the corpus:

* a **2-clip** record (`nasa-c4-small`) scores English at WER 1.0000. That is a
  reference corpus whose alignment failed, not a statement about English; loaded
  naively it makes the most verified language in the table *unusable* — the
  difference between a gate that helps and one that breaks on its first real
  user. Records under `MIN_CLIP_COUNT = 10` cannot score.
* a **beam-size sweep** (`c9-small-beam5`, 4.05% at beam 5) had taken the
  en/small slot on filename order and made English look 20% worse than it is —
  an optimistic-looking table built from a record answering a different
  question. Records off `PRODUCTION_BEAM = 1` cannot score.
* **absence is never a pass**: an unmeasured language, and a measured language
  on an unmeasured model, both read `not_measured`, reported as a caveat and
  never as `usable`. A gate that reports *usable* for every language it has
  never heard of is the failure mode this module exists to remove.

The bar, `WER_BAR = 0.25`, is a cut point in a measured empty interval rather
than a calibration: seven languages are measured and nothing falls between
Italian's 20.11% and Telugu-on-medium's 91.54% — a 4.5× gap with no data in it.
Italian is the language the bar decides against, and that is stated rather than
buried: at 20.11% WER and 4.53% CER its characters are mostly right where its
words are not, which is why it lands on the usable side of a bar drawn on words.

**Two things this work found about its own reasoning, both wrong as first
written.** (1) `poor` (25–100% WER) warns rather than refuses, and the rationale
was the comforting one — "at 13.59% WER the transcript is mostly right and an
editor can work with it, and refusing would block every Romance and Slavic
language." French at 13.59% is *below* the bar, i.e. `usable`, and every
measured Romance and Slavic language is `usable` too. The band's only measured
member is Telugu-on-medium at 91.54%, which is badly wrong and whose only
measured alternative is *worse* (small, 151.08%) — so a refusal would name a
fix the corpus does not have, and the rest of the band is unmeasured, so a
second cut point inside it would be guessing where the data is absent. (2) A
duplicate that lost the clip-count race was dropped with no audit trail — the
exact "a loader that quietly drops the records that disagree with it" failure
the module's own docstring claims to prevent. It now lands in a separate
`superseded` list, because a re-run that *disagrees* with the measurement it
replaces is the one record an auditor most needs to see. The two lists come out
of one scan of the directory rather than a second pass, so a rule added in one
place cannot be forgotten in the other.

Pinned by `tests/test_language_fitness.py` (34 tests), including four
pipeline-level tests that exercise the wiring rather than the arithmetic: the
refusal reaches no selection or render; the bypass leaves the verdict and both
audit lists in `report.json`; and the gate scores the model that **ran**
(provenance) rather than the one requested, because a silent downgrade can move
a language across the usable line. Every assertion was mutation-verified — 11
mutations against the bar, both refuse lines, both loader rules, the duplicate
resolver, the superseded lists, the alternatives ordering, `is_refusable`, and
the pipeline's gate wiring; the one mutation that escaped the first pass
(requested-vs-provenance model) is what made that fourth pipeline test necessary.

**What this still cannot see**, stated as a limit rather than a caveat: WER is a
property of the language-model pair, not of this file. A source in a
well-measured language can still be transcribed wrong — heavy accent,
crosstalk, music, a domain the references did not cover. The verdict is a prior;
the coverage gate and the confidence gate are the checks that listen to *this*
audio. They are complements, not substitutes, and this module deliberately does
not try to do their jobs.

### This session — the encoder ladder was a table of single-window figures, and no figure in it had a committed record

The previous entry measured what `slow`/crf18 costs. This one found the same
defect one layer down, in the place the encoder was chosen: the table in
`engine/render.py:_encoder_args` that justifies it. Its absolute quality and
bitrate figures matched **no committed record at all** — they were taken in a
session whose artifacts were never written down, so none could be re-derived,
re-checked, or swept. The previous entry's own 0.875 / 0.358 were the same shape.

Closed by making the measurement repeatable and then repeating it.
`tools/render_bench.py` is the bench; `corpus/render/runs/*.json` are its
committed evidence; `regenerates` in each record names every downstream site it
replaces, and `_write_record` refuses to overwrite a tag, so a re-run cannot
silently rewrite history.

**What the sweep changed, measured across three content windows on the corpus
source instead of one file.** Absolute quality and bitrate are **window
properties, not encoder properties**: the same entry moves 2.6 dB and 41% in
bitrate between windows, which is more than the entire gap between two presets.
Only the *gap against the shipped pair* and the *bitrate and RTF ratios* are
stable across windows. So the ladder is now published as ranges, for both
orientations (`corpus/render/runs/pentagon-240f-sweep.json` and
`pentagon-240f-16x9-sweep.json`):

    9:16, 406x722          dB vs shipped   bitrate ratio   RTF ratio
    veryfast/crf20           -3.18 .. -2.42   0.644 .. 0.700   0.34 .. 0.36
    medium/crf20             -1.43 .. -1.16   0.776 .. 0.804   0.69 .. 0.70
    slow/crf20               -1.35 .. -1.15   0.761 .. 0.765   0.90 .. 0.92
    slow/crf18                    shipped          1.0              1.0
    slow/crf16               +1.17 .. +1.40   1.298 .. 1.311   1.08 .. 1.11

    16:9, 1280x720         dB vs shipped   bitrate ratio   RTF ratio
    veryfast/crf20           -3.76 .. -2.91   0.685 .. 0.719   0.27 .. 0.29
    medium/crf20             -1.49 .. -1.32   0.818 .. 0.825   0.66 .. 0.72
    slow/crf20               -1.37 .. -1.27   0.786 .. 0.792   0.89 .. 0.93
    slow/crf18                    shipped          1.0              1.0
    slow/crf16               +1.24 .. +1.34   1.256 .. 1.276   1.07 .. 1.10

The throughput lever the previous entry sized at 3.16× is therefore **3.7× at
16:9 and 2.9× at 9:16** — and the fact that it is orientation-specific is exactly
what a single-figure table cannot express, and what a toggle sized from one
orientation would get wrong.

**Two doc claims with no record behind them, re-measured rather than dropped.**
Both survived, and both are now measurements instead of assertions. Emitting
1080×1920 from the 406×722 crop buys **+2.754 dB** for **4.577× bitrate and
5.092× RTF** (`pentagon-240f-upscaled.json`) — the direction and rough magnitude
the docstring claimed all along; it simply had no evidence. And burning a real ASS
from the run moves 16:9 from 0.8284 to **0.8221** and 9:16 from 0.3562 to
**0.3567**, both inside the 1.01× spread between timed repeats
(`pentagon-30s.json` → `pentagon-30s-ass.json`), so "captions cost under 1%" is
measured, not asserted. **The cost model now uses the captioned figures**, because
a real deliverable always carries captions.

**A systematic error in the bench's own denominator, found by the sweep.**
`-frames:v` without `-r` inherits the source's frame rate, so requesting 240
frames at `--fps 25` still emits 240 frames over 8.008 s. The naive
`frames / fps` read 9.600 s — a 17% overstatement of content, which understated
every RTF and every bitrate by 17%, and read to a reviewer as the encoder having
gotten cheaper. Fixed: `_emitted_duration` probes the emitted file's own duration,
and the record carries `span_requested_seconds` beside `span_emitted_seconds` so
the gap is visible rather than silent. **The production path was never affected** —
`pipeline.py` passes the source's real fps — but every bench figure taken before
the fix was wrong in the same direction and by the same factor.

**Figures this entry supersedes.** 0.875 → **0.8221**; 0.358 → **0.3567**;
0.277 → **0.220** (0.8221 × 0.27, the ratio the 16:9 ladder measures); 3.16× →
**3.7× / 2.9×, orientation-specific**; +3.46 dB → **2.9–3.8 dB at 16:9 and
2.4–3.2 dB at 9:16**; +2.64 dB → the same two ranges; total RTF 0.3584 →
**0.3547**; non-transcription RTF → **0.1896**; render share of the model 23% →
**22%**; `small` on `c7i.xlarge` $0.00107 → **$0.00106**. Each is recomputed in
`docs/BUSINESS_MODEL.md`, and `tests/test_render.py` now asserts the records exist,
that the shipped pair and window list are what the docstring cites, and that the
two orientation tables agree with each other — so the tables can no longer drift
from the evidence, which is the failure mode that produced this entry.

**One open question the sweep answered by posing.** `medium`/crf20 lands within
0.1 dB of `slow`/crf20 at roughly three quarters of the wall clock, which suggests
the preset itself buys nothing at a fixed crf — and that `medium`/crf18 might
match the shipped quality for less render cost. That is a two-command bench run,
and it is the next thing this entry's tooling makes cheap. Named in the docstring
as a follow-up rather than asserted there.

**Validation ladder for this work:** implemented (the bench tool, six fixes in it,
two of them found only by live probe runs rather than reasoning) → unit-tested
(45 tests in `tests/test_render_bench.py`, three of which reproduce the
denominator defect on real corpus media and a real elementary stream) → **real-media
validated** (five new committed records, all on the real 1280×720 29.97 fps corpus
source, each spanning three content windows per orientation) → document-corrected.
**Not** human evaluated; PSNR/SSIM are proxy metrics, not perceived quality. Full
suite at the commit: **1838 passed, 16 skipped**.

### This session — the cost model's largest measured term was stale by 7×, and the encoder is why

`56253b5` bought render quality by switching the encoder from
`libx264 -preset veryfast -crf 20` to `-preset slow -crf 18`, and **the cost row
in `docs/BUSINESS_MODEL.md` was never re-run after it.** Every downstream figure
traced to that one row was wrong by the same factor, in a document whose whole
purpose is that its numbers are measured. This session found it by noticing the
shipped 9:16 render measurement disagreed with the encoder ladder `56253b5`
itself had published, and closed it.

Re-measured 2026-09-28 on `corpus/media/pentagon-esper-briefing.mp4`
(1280×720, 29.97 fps), 30 s, best of 3 timed renders after one untimed warmup,
with a real ASS from the run burned in:

    16:9, shipped slow/crf18, 1280×720 output   0.8221 RTF
    9:16, shipped slow/crf18, 406×722 crop      0.3567 RTF
    16:9, pre-56253b5 veryfast/crf20            0.220 RTF (computed: 0.8221 × 0.27)

These three rows are **superseded as absolute figures** by the sweep in the entry
above — 0.875 → 0.8221 and 0.358 → 0.3567 — but the *attribution* below is
unchanged, because the encoder term it rests on is a ratio and ratios are what
survive a window change.

The 9:16 figure cross-validates against `56253b5`'s own ladder entry for
slow/crf18 (2.89 s for 240 frames = **0.361**) to within 1%, which is what
identified the stale figure in the first place. The old number's
**0.1232** is the pre-encoder-change measurement, and it is consistent with the
ladder's veryfast/crf20 entry (1.04 s for 240 frames = **0.130**).

**Attribution, because a 7× move needs two factors not one:** encoder alone,
measured head-to-head on identical 1080p footage and an identical filter chain
(900 frames: 10.13 s veryfast/crf20 vs 32.01 s slow/crf18) = **3.16×**. Source
resolution SD 720×486 → HD 1280×720 ≈ **2.6×**. Product
0.1232 × 3.16 × 2.63 ≈ **1.02** against a measured **0.8221**, with the
over-prediction explained by `landscape_filter` padding rather than scaling. The
controlled variable is the 3.16× encoder term; the resolution term is an
explanation of the gap, not a controlled measurement, and the original NASA
reference source is no longer on this host, so the re-measurement could not be
done on the same file. Stated as a limitation in the doc.

**The encoder was not reverted.** Re-measured at 1080p, the quality purchase is
real: PSNR **51.445** / SSIM **0.997573** for slow/crf18 against **47.987** /
**0.995991** for veryfast/crf20 = **+3.46 dB** — one window of a comparison the
entry above swept into a range (2.9–3.8 dB at 16:9, 2.4–3.2 dB at 9:16). Correct
the cost figure, not the encoder. Captions are provably excluded as a cause —
burning a real ASS moved 16:9 from 0.8284 → 0.8221 and 9:16 from 0.3562 →
0.3567, under 1%.

**The two presets can no longer share one figure**, and this is the geometry
finding the stale row concealed: the 16:9 deliverable from a 1280×720 source is a
1280×720 frame (0.92 M px, `landscape_filter` never enlarges and a 16:9 source
needs no padding), while the 9:16 deliverable is a 406×722 crop (0.29 M px) —
3.2× apart. Modelling them as one number would understate 16:9 by 3.6× while
overstating 9:16 by 2.4×.

**The move is 23% on the end-to-end cost, not 7×**, because the render terms are
multiplied by the 6.753% clip yield before they enter the model. Total RTF
0.2917 → **0.3547**; `small` on `c7i.xlarge` $0.000868 → **$0.00106** per
source-minute; the API mode $0.00476 → **$0.00496**. **That is why the stale
figure survived: it was invisible in the only number anyone would check.**

**What the correction actually changed.** The render term is now 22% of the model
— the largest term after transcription, and already past the 0.10 decode term
that is still an assumption. It is the only large term that is a *product
decision rather than an accuracy-gated* one: the ladder shows `veryfast`/crf20
costs **3.7× less wall clock at 16:9 and 2.9× less at 9:16** for **2.9–3.8 dB and
2.4–3.2 dB** respectively, at no accuracy and no architecture cost. So a
per-run quality/speed toggle, or a two-pass preview-fast/master-slow workflow, is
a genuine ~3× throughput lever that the stale figure hid — at 0.1232 the two
render rows were 5.7% of the model and not worth a setting. Recorded in
`docs/BUSINESS_MODEL.md` §4(b) as an option, **not implemented speculatively.**
One thing the sweep added and the single-figure version could not: the lever is
orientation-specific, so a toggle sized from one preset is sized from the wrong
half of its own range. And the planning figure used for pricing,
$0.004/source-min, no longer covers the API mode ($0.00496, 24% underwater) — the
one place the correction bites a decision rather than a number. Stated in the doc;
not silently repriced.

**The meta-finding, and the reason this propagated to six documents.** The
quantity least cross-checked in the cost model was the one that had become most
model-sensitive. `docs/EVALUATION.md` and `docs/TIME_SAVINGS.md` each carried an
independent 0.1276 "cross-check" that agreed with 0.1232 to 3.6% and was read as
corroboration — but both reproduced the *measurement conditions* (same retired
encoder, same SD source) rather than the *current conditions*, so they
corroborated the wrong thing. Their agreement is still real evidence that 0.1232
was right for what it measured; it is not evidence about the shipped encoder.
`docs/RED_TEAM_FINDINGS.md` R13 has the same shape one layer up: it correctly
caught an arithmetic inconsistency in `$0.021–$0.024` and the row was fixed — but
neither the auditor nor the fix noticed that the RTF underneath was stale. Both
rows are re-characterised rather than re-numbered, with the rule stated: **verify
the measurement conditions, not just the arithmetic that consumes them.**

Propagation, all on `main` and uncommitted until the commit that follows this
entry: `BUSINESS_MODEL.md` (§1 measurement table split into three rows, the
attribution note, §3 throughput table and model-size ladder, §4(a) cost table,
§4(b) GPU table restated on one stated rule plus its break-even arithmetic, §4(c)
render charge and API table, the all-in planning table, the §9 cost summary);
`EVALUATION.md` §6; `TIME_SAVINGS.md` §3 and §5; `DATA_ROOM.md`; `USE_OF_FUNDS.md`;
`RED_TEAM_FINDINGS.md` R13; `TRACTION_DASHBOARD.md` §B.

**Validation ladder for this work:** implemented n/a (a measurement and a
documentation correction, not code) → **real-media validated** (renders measured
on real corpus footage with a real burned-in ASS, cross-validated three ways:
against `56253b5`'s own encoder ladder, against the uncaptioned/captioned delta,
and against the two-factor product) → document-corrected. **Not** human
evaluated. No code changed, so no tests were added; the full suite from the
previous entry (1814 passed, 16 skipped) is unmodified and still stands.

### This session — the sentence splitter, the tracker, and the real-media re-run that closed every clip-level defect

The re-run the previous section called for has been run, and it did what a
re-run is for: it confirmed one fix, and it found the defect that fix could not
reach.

**Defect 1, found by the re-run — the splitter ended a sentence on any period.**
`pentagon-esper-briefing` clip 04 was the visible consequence. The source reads
*"Thank you, Mr. Secretary."* The splitter broke it at `Mr.`, the candidate then
opened on the fragment `Secretary.`, and its character-proportional bound
estimate landed inside the word `Mr.` and snapped back onto it. The clip opened
mid-sentence — for a reason the word-boundary fix could not reach, because the
sentence it was being aligned *to* was the wrong sentence.

The fix is a title/acronym/decimal table and a cursor walk over terminal marks
in `engine/story_units.py` (`_TITLES`, four-case `_is_boundary`, `split_sentences`).
An acronym, a title of address, a decimal or a tight pair of capitals no longer
ends a sentence where the text continues. Measured across the committed corpus,
three sources carry fewer, longer units — emwcon 112→108, nasa360 93→89,
pentagon 70→61 — and the topic and thread counts that follow from unit
boundaries moved with them. Pinned by 25 tests in `tests/test_story_units.py`.

**Defect 2 — the tracker read a pan as a cut, and a stranger as the subject.**
Four changes, each grounded in a measurement on the real corpus rather than on
fixtures:

1. `find_shot_boundaries` now reports only an *isolated* frame-difference
   crossing. A run of crossings is a pan: the motion ramps and decays over
   roughly 1.5 s. Measured over 10 real clip windows — cloke's 8 cuts are
   isolated spikes of 30.8–50.7 with neighbours under 10, while pentagon's three
   walking-speaker windows cross 2–3 consecutive samples at 22.8–35.6, one
   ramping 9.7→32.3→32.9→23.3→9.9. The false boundaries cost the reporter 2.6 s
   held outside the crop in a 34.8 s clip.
2. The association leash is a *rate* as well as a width (`_MAX_JUMP_RATE_S`).
   An absolute 2.5 face-widths is the wrong unit when the corpus sampling stride
   varies 4× (0.44–1.98 s). Measured over 171 consecutive same-subject sighting
   pairs in every selected window, a subject never moves faster than
   1.51 widths/s (p50 0.06, p95 1.17); the two false inheritances read 3.0
   widths/s. 2.0 sits in that gap and only binds where the stride is short
   enough for it to matter.
3. The election only treats a track as live if it has a sighting at or after the
   cut. `evidence(t)` averages a ±3 s window either side of the sample, so
   without the filter a track whose every sighting was in the previous shot
   still out-scores the new shot's first sighting.
4. The smoother's median window is centred even at the interval's edges, so the
   opening crop tracks a speaker walking in instead of landing a full crop width
   behind them.

Two existing tests were wrong in a way that mattered and were corrected rather
than left: `test_a_hard_cut_is_not_read_as_subject_motion` was passing because
the tracker chained the two subjects at the width leash, not because the
boundary marker prevented it — the boundary sat on the *last old frame*, so the
bind happened at an unguarded index. The boundary index is the first frame of
the new shot, which is what the detector reports. Two new tests were added: a
walking speaker stays one track, and a stranger at a 0.81 s stride does not
inherit it. 40 tests in `tests/test_reframe_regression.py`, including the five
real-media rendered shorts and short04's zero-tolerance
`not score["all_faces_lost"]`.

**The re-run, and what it closed.** Both sources re-run on real media
(`tools/corpus_run.py --only pentagon-esper-briefing,royal-society-cloke --force
--keep-media`; `--keep-media` is required or the sources are deleted and have to
be downloaded again):

* The corpus sweep is **16 clean, 11 review, 4 fail — and no clip-level failure
  at all**. The four failures are all project-level: five sources never reached
  selection because the VAD gate ate their transcripts.
* The A/B against the recorded pre-fix baseline reads `improvement 2`, no
  regressions: cloke clip 01 fail→clean on the word-boundary fix, pentagon clip
  04 fail→clean on the splitter fix. The intermediate reading (15 clean, 5 fail,
  pentagon clip 04 failing on `Mr.` rather than on `Syria.`) is recorded in the
  test docstrings and not in a baseline file, because the artifacts it was
  swept from were overwritten by the second re-run.
* The reproducibility tripwire is back to its four documented exceptions.
  Pentagon re-transcribed (277→278 segments, one unit and one topic more), which
  is the honest reason its selection moved: a different transcript, not an
  unexplained code change.
* `editorial_corpus.json` — the canonical story-memory graph the pipeline now
  writes — is committed for the two re-run sources. The other nine predate the
  write, and the corpus is frozen for them.

**What is still not claimed.** The audit measures mechanical defects; no human
has judged any clip, and a clip can pass every check and still be a bad cut.
The reframe measurements are detector scores, not viewed frames — this
environment cannot read images. A cut inside a pan, or two cuts within one
stride, still reads as a pan; a prominence-based discriminator was considered
and left out as diminishing returns, and the residual limit is stated in
`find_shot_boundaries` rather than papered over.

### This session — the two defects the edge check found were traced to two lines, and one candidate fix was measured out

The edge check in the section below found two real shipped defects. Both were
orphan words — a word that survived into a clip whose sentence did not — and
both were symptoms of one seam: the candidate is built on bounds *estimated*
proportionally to character span, while the word timing it is paired with is
*measured*, and nothing made the two agree before the clip was scored. The
trace ended at two lines, and a third candidate fix was implemented, measured
redundant, and reverted rather than shipped.

**Root cause B — a float-noise snap (`edit_plan._word_aligned`).** The snap
moves a bound back to a word's start when the bound falls inside the word:
`w.start < t < w.end`. Sentence bounds are estimated to character span, word
ends are measured, so the two disagree in the last ulp. On the committed
pentagon source the sentence `'Korea, has the deployment…'` starts at `364.84`
and the preceding token `' Syria.'` ends at `364.84000000000003` — a straddle
of **2.8e-14 s**, which the comparison read as real. The bound snapped back
0.34 s and the clip opened on the last word of the sentence the cut had halved.
A bound within `1e-6` of a word's end is now treated as on the boundary and
left alone. `editorial._words_in` is deliberately *not* made
containment-correct to match: it stays permissive, and this function decides
where the bound then sits, because the decision belongs in one place.

**Root cause C — a discarded word list (`editorial.score_candidate`).**
`score_candidate` called `align_span` for bounds and text and then built the
candidate on the segment's *original* word list, so a candidate could carry a
word its own text did not name. On the committed cloke source clip 01 ended at
`235.237`, 117 ms into `'Somebody'` (`235.22–235.38`): the text dropped it, the
word list kept it, and the captions — which are built from the word list —
burned a word the render truncated. `align_span` now returns the aligned word
list as a fourth value and `score_candidate` adopts it, so the score, the hook,
the text, the interval and the captions all describe one clip.

**Measured, not asserted.** A one-process control diff isolates attribution:
the same transcript is ranked with the two changes patched off in place, so the
pre-existing non-reproducibility of four sources' selections is excluded from
the comparison. Both defects are fixed by B and C alone:

    pentagon-esper-briefing clip 04
      pre-fix  [364.500, 393.980]  first='Syria.'   last='facility.'   << orphaned
      post-fix [364.840, 393.980]  first='Korea,'   last='facility.'   clean
    royal-society-cloke clip 01
      pre-fix  [181.735, 235.237]  first='The'      last='Somebody'    << orphaned
      post-fix [181.735, 235.237]  first='The'      last='advance.'    clean

The `align_span` correction is surgical, not a rebuild: on the committed corpus
it rewrites **3 of 27** candidates with word timing and leaves the other
twenty-four byte-identical.

**A third fix was measured out and reverted.** Making `_words_in`
containment-correct as well looked like the obvious completion of the pair. It
was implemented and measured: given the snap, it changed exactly one clip's
bounds by 0.27 s, on a source whose selection does not reproduce from the
committed transcript anyway, and it fixed nothing — the orphan word in the
cloke clip is admitted by *containment*, not by overlap, so the change does not
touch it. Duplicating the containment decision in two functions would have
been complexity with no effect, so it was reverted and the measurement is
recorded in `_words_in`'s docstring instead.

**What this does not claim.** The committed corpus artifacts still carry the
two defects — re-running the two sources that still hold media (pentagon 158
MiB, cloke 85 MiB) through the fixed pipeline is the real-media validation and
has not been run. What is verified here is that the fixed engine, given the
committed transcripts, no longer produces either orphan, and that the
pre-fix engine given the same transcripts does.

**Pinned in `tests/test_editorial.py`.** The existing
`test_scored_candidate_text_agrees_with_its_words` was rewritten around the
measured behaviour, and its docstring records that its *first* reading was
wrong and the audit proved it: the ulp straddle is float noise, not a real
overlap, and the test now asserts the bound stays put and the orphan word does
not survive into the candidate's word list. A new
`test_scored_candidate_keeps_a_genuinely_straddling_opening_word` pins the
mirror case — 120 ms into a 300 ms word is a real straddle, orders of magnitude
larger than the ulp case, and the bound snaps back to keep the word whole. The
two must not be treated alike, which is the whole content of the epsilon guard.
`test_an_already_aligned_candidate_is_left_alone` guards the drift: a span that
is already word-aligned comes back unchanged, so the fix does not rewrite every
candidate in the corpus and move the scoring.

### This session — the audit learned to compare, and found the edges it could not see

SelekyT Eval is P0 item 5, and the gap was not a missing check — it was a
missing *question*. The audit could say whether one run's clips were
publishable. It could not say whether a change to SeleKyT made its output better
or worse, because there was no comparison mode and no recorded corpus baseline to
compare against. Two pieces of work closed that, and the first one found a defect
the old audit was structurally incapable of finding.

**The edge check.** `check_cuts_respect_sentences` compares the words *both sides
of a cut that survive in the clip*, so a clip that begins partway into an
utterance leaves it nothing to compare on that edge — the words that would
convict it are the ones the selection dropped, and the selection's own word list
holds only the words it kept. The new `check_edges_respect_sentences` looks
*outward* at the transcript words the boundary dropped instead of inward at the
words it kept, and the criterion fell out of measurement rather than design: an
opening is clean iff the speech preceding the clip's first kept word ends in a
terminal sentence mark, and a closing is clean iff the clip's own last kept word
does.

That found two real shipped defects on committed corpus output, both of which the
old check passed silently. `pentagon-esper-briefing` clip 04 opens on `' Syria.'`
after dropping `'…the oil fields in the east of'`. `royal-society-cloke` clip 01
ends on `' Somebody'`, dropping `'says, you know, actually tomorrow'` — the clip
ends mid-clause and the audience hears a word that never resolves. These are
exactly the failures that matter editorially, and they are invisible to any check
that only inspects the words the clip kept.

**Undecidable is a third state, not a pass.** Measuring the corpus revealed why a
binary verdict was never going to be honest: 34–47% of the English transcript
segments carry no terminal punctuation at all, so on such a segment *every* edge
is consistent with mid-utterance and none is distinguishable from a clean one.
Reporting that as a pass would be claiming a measurement that was not taken;
reporting it as a fail would be blaming the clip for the transcript. It is
reported as **review** with a count of how many edges were undecidable, so the
proportion reads as a property of the transcript rather than of the clips. But
review **still fails the exit code** — a clip the tool could not measure is not a
clip it has passed. `main()` exits 0 only when `ok and not review`. The
distinction is enforced inside the check too: the undecidable note goes into a
separate `notes` list, never into `bad`, because a hard failure asserts the
shipped clip is wrong while this one asserts the transcript cannot say.

One subtlety worth recording, because it was a real bug in the first version: a
clip that spans into the following utterance moves its close edge out of reach of
the segment it opened in — the trailing continuation words live in the *next*
segment. The close edge is keyed on the utterance of the clip's *last* kept word,
not its first.

**The comparison mode.** `sweep()` audits every `corpus/*_run/project` as one
comparable record. Clean clips are counted but deliberately *not* recorded,
because `compare_sweeps()` treats an absent entry as clean, and recording clean
clips would make one state read two ways. `compare_sweeps(before, after)`
classifies each clip that moved as `regression` / `improvement` / `changed` /
`added` / `removed` over the state rank `clean < review < fail`, ordered by
severity, and `main()`'s exit code treats a regression, a removed run, or a
defect that changed shape as signal while an improvement is not. A SeleKyT
change can now be scored against the corpus the way it has to be scored: not "are
there defects" but "did this make defects worse."

**The recorded baseline** is `corpus/eval/audit-baseline-2026-09-27.json`, pinned
by a drift-guard test that re-sweeps the corpus and asserts it still matches. The
three-state corpus verdict, replacing the stale binary table that claimed five
projects clean:

    11 runs, 14 clean clips, 11 awaiting review, 6 failed

Two hard failures from the edge check, ten edge-undecidable reviews, one silence
review (`telugu-tutorial` clip 01, 46.3% word-free), and five project-level
failures where the VAD gate suppressed transcription before selection ever ran —
those five are the coverage collapse already recorded in the session below, now
visible in the eval instrument as well as in the pipeline.

### This session — the coverage gate was blind on the sources it existed for

The pipeline has exactly one *hard* guard that compares the transcript against
the audio it claims to describe: `engine.coverage`, which raises on
`coverage_collapse`. It exists because `telugu-tutorial` shipped as an OK
deliverable with a transcript covering **4.5%** of a 339.6 s file that is 73%
speech-energy audio, and every other audit in the pipeline compared the
transcript against itself, the selection or the render — never the audio — so
nothing objected. A user would have shipped wrong captions and read a success
message.

**The guard itself was the defect.** It constructed `EnergyVAD()` directly,
hardcoded. Energy is the backend the chain ranks **last**, for the measured
reason that its scorer clips frame RMS into fixed absolute thresholds
[0.02, 0.08), so a quiet source falls entirely below the base and the whole
file scores silent. That is precisely the regime the gate exists to inspect, so
the gate declined to compute on exactly the sources where a transcript is most
likely to have collapsed — and it declined by reporting `not_computable`,
which is honest, but it meant the gate's coverage of the corpus was the
*complement* of its purpose. Measured on `hadhazy-protest` (mean −43.3 dBFS,
transcript covering 87.1%): energy reads **0.0% speech**, so the ratio had no
denominator. A gate that cannot hear a quiet source is a gate that cannot catch
a collapse on one.

**The fix routes the gate through the same chain the pipeline prefers** —
silero, then ffmpeg, then energy — via a new `vad_backend` argument on
`measure`/`check` and `_select_backend`, and the pipeline forwards its own
`vad_backend` to it, so the instrument that decided what counts as speech for
the selection also supplies the gate's audio truth. Whisper is the one value
that cannot be forwarded: `WhisperVADBackend.analyze` ignores its audio argument
and marks a frame speech iff a transcribed word covers it, so the gate would
compare the transcript against itself, the ratio would sit at ≈1.0 by
construction, and a collapse would become undetectable. `_select_backend`
*refuses* it with an error rather than silently substituting, and a forced
backend that is not installed is refused the same way — `--vad-backend` must not
agree to a measurement taken with something else. A `--vad-backend whisper`
selection run still works: the pipeline forwards `None` in that case and the
gate falls back to its own chain, with the backend that actually ran recorded in
`report.json`.

**Hit for real on the source it was written for.** The corpus survey measures
the *retained extraction*; the abort had only been pinned on synthetic media at
the corpus's scale. A resumed run over the real `corpus/media/telugu-tutorial.webm`
— cached transcript and cached extraction, so no re-transcription and no
re-extract, the exact shape of an editor re-running a finished project — reaches
the gate and aborts:

    RuntimeError: Transcription coverage collapse: the transcript covers 4.5% of
    a 340s source that the silero VAD scores 77.4% speech (ratio 0.058, below
    0.25). The transcript is well-formed, which is why nothing else flagged it —
    verify it against the audio before shipping anything cut from this run.

The numbers are the survey's numbers. The bypass does not reach a deliverable
either: with `--skip-coverage-check` the same run is refused one stage later by
the editorial confidence gate — 5 eligible spans, every one refused, best factual
confidence 0.39 against a 0.75 floor, and its message names the transcription
rather than the stories as the suspect. Two independent guards converge on the
same source by different routes, so bypassing the coverage gate moves the
failure instead of removing it. That was not designed as a pair; it is a
measurement that happened to come out that way, and it is recorded rather than
claimed as a guarantee.

**The two guards now say so in one message.** They converged but did not
reference each other: the confidence refusal advised "re-run with a larger
model" without mentioning that the pipeline had already *measured* exactly
that defect three stages earlier, so an editor who hit the bypass got two
refusals about one transcription and no indication they were the same finding.
`_coverage_corroboration` appends the coverage gate's own measurement to the
confidence refusal, but only when that verdict is `coverage_collapse` — an `ok`
coverage report is never quoted into a confidence complaint, because reading it
there would imply corroboration the pipeline does not have. The clause quotes
the gate's `detail` verbatim rather than re-approximating its numbers, so a
figure in the refusal cannot drift from the figure the gate raised on. When the
bypass was used, the clause says so, which is the one case where the two guards
are visible to the editor in a single run.

The real source confirms the merged message end-to-end
(`corpus/eval/rerun-telugu-coverage-gate-2026-09-27.log`, moved into the eval
evidence directory with the audit baseline):

    RuntimeError: No complete standalone stories were selected. 5 eligible span(s)
    were scored, but every one was refused by the editorial/context gate while
    ranking, most often for: low factual confidence (best measured 0.39, against
    a 0.75 floor). The transcription is probably wrong rather than the stories
    being weak — re-run with a larger model, or with --language set if the
    detected language is not the one spoken. The coverage gate measured this
    independently: the transcript covers 4.5% of a 340s source that the silero
    VAD scores 77.4% speech (ratio 0.058, below 0.25). The transcript is
    well-formed, which is why nothing else flagged it — verify it against the
    audio before shipping anything cut from this run. --skip-coverage-check was
    passed, so the run continued past that gate to reach this one. No clip was
    lost to a budget — every one was refused on its merits.

This is a message-coherence fix, not a new guard: no run that passed before is
refused now, and no refusal is added or removed. It is pinned by four tests in
`tests/test_speechless_source.py` including the two negative cases.

**Re-measured across all 11 corpus sources** through the chain
(`corpus/wer/runs/coverage-2026-09-27.json`, `tools/coverage_measure.py` now
defaults to the same chain, so the survey and the production check cannot drift
apart):

| source | dur s | vad % | asr % | ratio | mean dBFS |
| --- | --- | --- | --- | --- | --- |
| ooi-vertical | 152.0 | 70.5 | 85.3 | 1.209 | −25.8 |
| hindi-baiga-interview | 53.8 | 85.6 | 93.0 | 1.087 | −29.6 |
| emwcon-panel-2018 | 3339.0 | 80.6 | 85.1 | 1.056 | −28.8 |
| pentagon-esper-briefing | 1013.9 | 77.9 | 82.2 | 1.056 | −38.8 |
| nasa360-ep5 | 1469.7 | 75.4 | 79.3 | 1.052 | −30.5 |
| royal-society-cloke | 626.0 | 93.7 | 93.3 | 0.996 | −19.7 |
| hadhazy-protest | 126.0 | 94.1 | 87.1 | **0.926** | −43.3 |
| **telugu-tutorial** | 339.6 | 77.4 | **4.5** | **0.058** | −19.4 |
| alice-1903 / roman-portrait-trailer / telugu-launch | — | 0.0 | 0.0 | n/a | no transcript and no speech |

The ratio separates the one failure from the seven good sources with **nothing
between them**: 0.058 against a lowest-good 0.926, a 16× margin. `COLLAPSE_RATIO`
0.25 sits inside that empty interval with 4× of room below and 4× above. With
one failure and seven good sources this is **not a calibrated threshold and is
not presented as one** — it is a cut point inside a measured empty interval.
The two verdict changes the fix delivers are both in it: `hadhazy-protest` and
`pentagon-esper-briefing` move `not_computable` → `ok`, because silero hears
94.1% and 77.9% speech where energy read 0.0% and 0.4%. No verdict moved the
other way, and the real collapse is caught by every backend in the chain.

**Two properties the measurement forced on the design, both now pinned:**

* **One-directional.** The ratio is only meaningful when the transcript covers
  *less* than the speech energy. `hadhazy-protest` is the case that makes it so:
  a level-blind instrument scored it 0% speech against a good transcript, so a
  symmetric check would have failed a source whose transcript was fine. The
  chain removes that particular failure, but the asymmetry stays as a principle
  — a transcript covering more than the audio's speech fraction is not evidence
  of anything.
* **Degrades instead of passing.** Below `MIN_VAD_FRACTION` (0.10) the ratio has
  no trustworthy denominator, so the verdict is `not_computable` and the detail
  names the backend that ran and why. A check that reports *pass* when it could
  not measure anything is the failure mode this module exists to remove; a
  `not_measured` verdict covers the case where the extraction is absent on a
  resumed run, and it does not break the run.

**The fixtures had to be re-derived, and the design space is now measured once
in a shared module.** A test needing a collapse needs audio the chain scores as
speech *and* a transcript covering a small fraction of it — arithmetic that the
collapse threshold makes narrower than it looks: a transcript covering 16 s of a
100 s file that is 50% speech is ratio 0.32, **above** the 0.25 threshold. The
old 220 Hz tone fixture scored 100% speech on energy and collapsed; the gated
voiced fixture scores ~50%, so the same test would have stopped collapsing and
failed for the right reason. Two things came out of measuring the replacement:

```
shape                              energy   ffmpeg   silero
220 Hz tone                           100%     100%      0.0%   <- scored, never segmented
continuous voiced stack                 8.8%     46.5%      0.2%
+ 4 Hz syllable envelope                8.8%     46.5%      8.8%
+ 0.5 s on / 0.5 s off gating          49.6%     43.5%     52.5%   <- the fixture

amp 0.9      mean -18 dBFS   energy 49.6%  ffmpeg 43.5%  silero 52.5%
amp 0.004    mean -65 dBFS   energy  0.0%  ffmpeg  0.0%  silero 60.8%
amp 0.0001   mean -97 dBFS   energy  0.0%  ffmpeg  0.0%  silero  0.0%
```

Only the gated shape is speech to all three backends at once, and the gating is
a genuine cue rather than a hack — bursty speech with inter-syllable pauses is
closer to what a VAD is for than a continuous drone. The `amp` dial is the
quiet-source stand-in and separates the backends cleanly: one level heard by all
three, one heard only by a model that listens (the corpus's quiet-source shape,
and the whole reason the fix matters), one heard by none — the one
host-independent way to reach "no denominator". Each test asserts the property
that holds on every host rather than one that pins the machine: the quiet source
is never *failed* everywhere and is `ok` only where a listening backend exists;
the inaudible source is `not_computable` regardless. The measured tables live in
`tests/speech_fixtures.py`, which `test_audio_analysis.py` now shares instead of
carrying its own copy.

Pinned by `tests/test_coverage.py` (17 tests, 16 s): the collapse fires and its
detail carries the numbers; a covering transcript passes; the quiet source is
never failed; the chain probes in the pipeline's preference order with energy
last, never first; whisper is refused; an unavailable forced backend is refused;
a wrong sample rate is rejected rather than confidently miscounted; the span
arithmetic unions instead of sums; the report's `as_dict()` carries the backend
that measured it; and two pipeline tests exercise the guard
through `run` at the corpus's own scale (a 340 s extraction against a 16 s
transcript, ratio ≈0.094) proving it aborts **before** selection and render and
that `--skip-coverage-check` still records the measurement.

**Two traceability gaps the real run exposed, both fixed.** The abort message
said the source "is 77.4% *speech-energy* audio" while silero was the instrument
that measured it — wording inherited from the energy-only era that misdescribed
its own evidence, and exactly the confusion the gate exists to remove. The
collapse and pass details now name the backend that scored the speech, as the
`not_computable` detail already did. And `CoverageReport.as_dict()` omitted the
backend entirely, so `report.json` could say `ok` without saying which
instrument earned it — an `ok` from a neural model and an `ok` from fixed RMS
thresholds are not the same claim, particularly on a host where silero is
absent. The field is now carried through, which also makes the pipeline comment
that promised it true.

**What this still cannot see**, stated as a limit: no backend in the chain can
tell speech from music, so a source that is mostly score with a little dialogue
has a high speech fraction and a legitimately low transcript coverage and would
be flagged. The error cost is asymmetric and intentionally so — a false positive
is a loud message asking the editor to verify, a false negative ships wrong
captions silently. The check is therefore overridable, and the override leaves
the measurement in the report rather than deleting the evidence. A second limit:
both abort paths end before `report.json` is written, so on the corpus the
`vad_backend` field is verified by unit test only — a *successful* run on a real
source has not yet been observed carrying it.

### This session — silero VAD landed, and the fallback chain was re-ordered by measurement

Two things happened, in this order: the silero backend was implemented and
validated, and the measurement that validated it revealed that the *fallback
chain was ordered backwards*. The second is the more consequential finding,
because it changed what every host without the model gets by default.

**Silero is implemented, not stubbed.** `SileroVADBackend` drives
`silero_vad_v6.onnx` (vendored at `engine/models/`, sha256 prefix `4cbf549b`,
1 245 151 bytes) through onnxruntime. The model's contract is 512 new samples
+ 64 of left context per row, with `h`/`c` recurrent state of shape
`[1,1,128]`; one inference is exactly 32 ms, so `segment_from_scores` reads
silero's output on the same grid it reads the energy scorer's. State is
threaded across both ORT batch boundaries *and* 60 s read windows, so a long
source is one continuous stream.

**Validated against faster-whisper's own driver of the same model file**, not
against a paper description. On a source fitting one read window with a whole
number of steps, every frame but the last agrees **bit-exactly** (max|diff|
0.0). The last frame differs, and the cause is a defect in the reference, not
here: its context array is a *view* of the audio, so `context[-1] = 0` —
written to give row 0 zero context — also zeroes the final row's own last 64
samples in the array it then scores. Proven by running the model on the
reference's block with and without that mutation: it moves exactly one score,
the last, 0.0327 vs 0.0312, and no other. The test asserts bit-exact on
`[:-1]` and a bounded, explained difference on the last frame.

An earlier version of my own docstring claimed the reference "loses the raw
context at each 10 000-row boundary". **That was false** — the roll is computed
once over the whole array, so context carries correctly across batch
boundaries. What is true is only that the reference resets `h`/`c` per call.
The docstring was rewritten to state what was verified, which is the
discipline this repo holds itself to.

**Quality, measured against word-timestamped transcripts** on three corpus
sources (126 s / 331 words, 626 s / 1970 words, 1014 s / 2727 words).
Word recall and words clipped at a cut point — the two figures that decide
whether a cut is usable:

| source | backend | regions | word_recall | clipped % | analyze s |
| --- | --- | --- | --- | --- | --- |
| hadhazy | energy | 0 | 0.0000 | 100.00 | 0.013 |
| hadhazy | ffmpeg | 71 | 0.6461 | 37.31 | 0.164 |
| hadhazy | **silero** | 21 | **0.9755** | **3.02** | 0.231 |
| cloke | energy | 569 | 0.7623 | 20.15 | 0.034 |
| cloke | ffmpeg | 117 | 0.9428 | 2.97 | 0.372 |
| cloke | **silero** | 114 | **0.9582** | **2.64** | 1.062 |
| pentagon | energy | 17 | 0.0045 | 99.41 | 0.052 |
| pentagon | ffmpeg | 365 | 0.8126 | 12.85 | 0.567 |
| pentagon | **silero** | 335 | **0.8976** | **6.34** | 1.735 |

Silero leads on both figures on all three. The energy column is where the
second finding came from.

**The fallback chain was ordered backwards.** `get_vad_backend` preferred
energy over ffmpeg — "energy (best available on this device)". Measured,
ffmpeg beats energy on **every** source, and energy fails outright on two:
its scorer clips frame RMS into a fixed absolute window [0.02, 0.08), so a
quiet source falls entirely below the base and the whole file scores silent.
On hadhazy that is **0 of 331 words kept**, every word clipped, zero speech
regions — and no error raised; the pipeline reports `dead_air_ratio` 1.0 and
the editor sees a source with nothing in it. Pentagon is 12 of 2727. On cloke,
where the level is healthy, energy still loses (0.76 vs 0.94 recall).

The chain is now **silero → ffmpeg → energy → whisper**, and the reason is in
the docstring with the table. Energy keeps its slot above whisper because it
needs nothing but numpy — a host with neither the model nor ffmpeg still gets a
working backend — and its latency advantage (0.01–0.05 s vs ffmpeg's
0.16–0.57 s on the same sources) does not offset scoring a source silent that
is not. `extract_pcm_wav` already requires ffmpeg, so energy's no-ffmpeg niche
is conforming WAVs only.

**Four adaptive scoring rules were prototyped and measured, and none was
shipped.** This is recorded because the negative result is the point: the
obvious fix — derive thresholds from the source's own RMS distribution — was
not assumed to work, it was measured.

* `p10/p90` adaptive anchors: rescues hadhazy (0.00→0.51) and pentagon
  (0.0045→0.55) but **regresses cloke** (0.78→0.62), because on a loud source
  the p10 anchor is itself loud and the raised floor swallows real speech.
* Reference-level gain normalization: improves all three, but at 17× gain on
  hadhazy `speech_frac` reaches 0.982 — the noise floor is amplified past the
  speech threshold and silence detection is destroyed. Recall looks wonderful;
  the edit is useless.
* The **degenerate case is the real blocker**. Probed directly: a uniform
  noise bed has p5≈p95, so any percentile-derived gain explodes and scores it
  **99.2% speech** where the fixed curve correctly scores 0%. Dead-air
  detection would invert. Digital silence and a 16-bit dither floor behave the
  same. The guard that stops this is *descriptive, not tuned*: amplify only
  when the source's p5/p95 range proves two modes exist (silence spans two
  orders of magnitude, a noise bed spans ~1.1×).
* That guarded rule is safe on every degenerate input and lifts pentagon
  0.0045→0.87 and cloke 0.78→0.88 — but **hadhazy stays at 0.0000**, because
  its dynamic range is 5.1×, below the two-mode threshold. A source with no
  speech/silence structure cannot be recovered by any linear gain, and that is
  exactly what the model is for.

So the scorer was left alone and the routing was fixed instead. Shipping the
guarded rule would also have forced a coordinated Python + Rust rewrite —
`rust/core/src/fallback.rs` documents its fixed curve as an intentional
decision so parity tests can assert exact numbers, and the sidecar parity
suite (66 tests) runs green here, so the coupling is live, not theoretical.
The measured boundary, and the reason each candidate was rejected, is recorded
in `docs/product-gap-map.md` so the next session does not re-derive it.

**Synthetic speech is not speech — a testing finding worth keeping.** Silero
was trained on voices. A 220 Hz sine scores in-speech mean 0.090 against
silence 0.006: no usable contrast, so any test built on a tone is vacuous. A
six-harmonic stack at 120 Hz reaches 0.40; adding a 4 Hz syllable-rate
amplitude envelope reaches 0.94 against silence 0.035. The envelope modulation
is a genuine cue to syllable rate, not a hack to defeat the test. The tests
therefore assert the *contrast* between speech and silence frames, never an
absolute threshold.

**Level invariance, the defect the energy backend has:** scaling the whole
source down must not flatten the speech/silence gap. Silero holds it at every
level — at scale 0.01 the gap is +0.879 (speech 0.935, silence 0.057) — while
the energy backend collapses: at scale 0.05 and 0.01 it emits speech 0.000,
silence 0.000, gap 0.000, silently, with no error.

Pinned by `tests/test_vad_silero.py` (10 tests, 0.50 s): the contrast, the
segmentation, windowed reads agreeing with a single window to 1e-5 (the
regression test for a state-carry bug that reset `h`/`c` at every read
window), the tail-padding rule, sub-step input, PCM conformance of stereo
48 kHz, and the reference parity. Plus `tests/test_vad.py` gains the test that
pins the measured chain order, so it cannot revert silently.

### This session — the demo's own sample scored "no speech found", and its cache kept it that way

Shipping silero as the preferred backend had a victim the corpus could not have
revealed: **the demo itself.** `web/sample.mjs` generated the 8 s clip served by
`GET /api/sample.wav`, and it was a 300 Hz fundamental with two harmonics and a
shallow 15% amplitude modulation. The energy and ffmpeg backends scored it 60%
and 57% voiced; silero scored it at a **maximum of 0.31 across the whole 8 s**,
mean 0.0047. Silero is correct — a tone is not speech, and it was trained on
voices — but the consequence is that the chain preferring silero made the
demo report *no speech found* on a signal that is plainly audible, and every
downstream test that needed a selection fell over with it. Eight tests failed;
six of them had nothing wrong with them at all.

This is why the change is a product fix and not a test fix. The analysis path is
for speech, so the sample the demo ships has to be something a speech model
recognizes — otherwise the first thing every user does demonstrates the
product's central failure mode.

**The replacement was designed by measurement, not by guess.** The design space
was swept (fundamental × harmonic count × syllable modulation depth × spectral
tilt × noise floor) with each candidate scored through all three backends, and
two properties turned out to be what silero keys on. Fundamental frequency and
harmonic count alone barely move it; **syllable modulation depth is decisive**
(0.2 depth → 0.04 voiced, 0.8 depth → 0.60). The plateau, f0 100–140 Hz with 8
equal harmonics and a depth of 0.4–0.9, gives 3 segments from *all three*
backends; f0 160 Hz and above falls off (0.01/1 segment at depth 0.4), and a
noise floor above roughly −50 dB makes ffmpeg score the whole file speech
(−40 dB → 1.00 voiced, 1 region), so the silence between bursts is left
digital. The shipped design — f0 100 Hz (seed varies it 100/120/140), 8
harmonics, 4 Hz syllable envelope at depth 0.8, peak-normalized to 0.9 — sits
in the middle of that plateau.

```
f0  am_depth ->  silero:voiced/nseg  energy:..  ffmpeg:..
100 d0.4=si:0.59/3 en:0.60/3 ff:0.57/3 | d0.8=si:0.61/3 en:0.60/3 ff:0.57/3 | d0.9=si:0.60/3 en:0.60/3 ff:0.57/3
140 d0.4=si:0.57/3 en:0.60/3 ff:0.57/3 | d0.8=si:0.61/3 en:0.59/3 ff:0.57/3 | d0.9=si:0.61/3 en:0.60/3 ff:0.57/3
160 d0.4=si:0.01/1 en:0.60/3 ff:0.57/3 | d0.8=si:0.56/3 en:0.60/3 ff:0.57/3 | d0.9=si:0.58/3 en:0.60/3 ff:0.57/3

noise floor sweep at f0=120 d=0.7
  noise=None: si:0.60/3 en:0.55/3 ff:0.57/3
  noise=-50:  si:0.61/3 en:0.55/3 ff:0.60/3
  noise=-40:  si:0.61/3 en:0.55/3 ff:1.00/1
```

The peak normalization is load-bearing, not cosmetic: the harmonic sum's peak is
not predictable from the harmonic count, and the energy backend compares RMS
against fixed absolute thresholds, so an unnormalized level leaves the demo's
behaviour to chance on the one host whose default backend is level-blind.

**Validated against the shipped artifact, not a replica.** The JS module itself
was invoked for seeds 0/1/2/42 and each output scored through all three
backends — 3 segments from each, every segment landing on one of the three
bursts. The numpy prototype agreeing was not the evidence; the file the server
actually serves was.

**Then the tests still failed, and the second defect was worse than the first.**
`ensureSample()` in `web/server.mjs` wrote `out/web/sample.wav` **only if it was
absent** — a cache with no invalidation. Any host that had already generated the
sample would keep serving the old tone forever, and no edit to `web/sample.mjs`
would ever reach it. A fix to the demo's central signal would be silently
undeliverable to every existing user while working perfectly for every new one.
It now regenerates on every call (the synthesis is ~128k samples, milliseconds,
and the callers are not hot paths) and writes through a `.part` +
`renameSync` so a concurrent reader sees the old file whole rather than a
truncated header — the same contract the upload path already holds.

Pinned by two tests in `tests/test_web_harness.py`. The first renders
`web/sample.mjs` with node and asserts the served bytes are byte-identical to
the generator's current output, which fails on exactly a stale cache and nowhere
else; the second asserts three bursts produce three segments under whichever
backend the host selects, with each segment sitting on a burst. Both were
verified to fail against a stale cache and pass after the fix — the negative
case was run, not assumed.

Two other tests were corrected rather than added: they asserted
`backend == "energy"` and `vad_model == "energy-rms"`, which was latently
host-dependent and would have failed on any host with the model. They now assert
the *contract* — the backend and its model string are a consistent pair — so
they pin what must hold everywhere rather than which machine they ran on.

**The same defect was in the Python-side suite, and it exposed a third one.**
`tests/test_audio_analysis.py`'s fixture was a 220 Hz sine gated on and off —
scored by the probe alongside a speech-like alternative:

| fixture | backend | peak | segments at every threshold 0.3–0.9 |
| --- | --- | --- | --- |
| tone 220 Hz | silero | 0.503 | **0** |
| tone 220 Hz | ffmpeg / energy | 1.000 | 2 |
| speech-like | silero | 0.981 | 2 |
| speech-like | ffmpeg / energy | 1.000 | 2 |

The tone's 0.503 is the trap: it reads as "just above the default 0.5 threshold"
but yields zero regions at *every* threshold, so three tests died with
`nothing to keep: the cutlist removes the whole timeline`. The fixture is now
the same voiced excitation design as the web sample, and the three tests pass on
whichever backend the host selects.

The third defect was in the test that survived the fixture change by luck.
`test_reproducible_segmentation_across_runs` hard-coded thresholds 0.3 and 0.5
and its docstring claimed they "straddle the boundary where the segmentation
actually moves" — but the measurement above shows why no fixed pair can: **every
backend's score series on a voiced fixture is bimodal** (frames are either near
zero or near the peak), so any two thresholds inside that gap give identical
regions. The old pair only appeared to straddle because one backend's peak sat at
0.503, i.e. the test was measuring a knife-edge on one host's preferred backend.
It now derives `lo` and `hi` from the scores the first run actually cached — `lo`
below the peak so speech survives, `hi` above it so none does — which is a real
difference on every backend by construction, and asserts as a guard that the
fixture scored speech at all, which is the part that broke last time. `hi` is
deliberately allowed above 1.0: the segmenter's test is `s >= threshold`, so
capping `hi` at 1.0 on a backend that saturates at exactly 1.0 makes it inclusive
and gives the same regions as `lo` — the failure mode would have been invisible
until a host without silero ran it. Verified to straddle and to reproduce on
silero, ffmpeg and energy forced in turn (2 regions vs 0, identical on re-run).

### This session — half the perception time was a re-read of a window just read

`scene_filter` asks for the same 28-sample window twice in one call:
`analyse_scene` reads it to decide the layout, and the composition pass reads
it again — on the plain-speaker path, `_probe_and_track` re-reads it through a
third name. `_sample_frames` had no memory of the *frames*: `_REFAME_CACHE`
holds only the trajectory derived from them, `_SCENE_CACHE` only the resulting
filter string. So the second ask re-ran the whole seek-and-decode pass. This
was not inferred from the call graph — it was found by counting reader calls,
and then measured against the clock.

**Baseline, on the corpus AV1 source** (`telugu-tutorial.webm`, 1920×1080):
248 ms per sample, so one 28-sample window is 6.9 s, and one
`scene_filter(30.0-45.0)` call took **18.64 s across two byte-identical read
requests — one redundant pass, ~half the time**. Where the per-sample cost
sits, from a component breakdown: ffmpeg process startup 43 ms; a
per-invocation fixed cost ≈306 ms of seek-plus-decode-from-keyframe;
marginal frames only ≈7 ms each; sequential AV1 software decode 96.6 fps.

**The fix**: a bounded LRU frame cache keyed on the existing
`_ReframeCacheKey` (video, size, mtime, start, end, samples) at that one choke
point, so every consumer is repaired at once rather than threading one
caller's frames through another's signature. The size/mtime in the key mean an
externally edited file is never served a stale window. **Measured before →
after on the same window, best of three, same process** (bound 0 evicting on
insert reproduces the pre-change behaviour exactly):
18.43 s → **11.17 s, 1.65×, saving 7.26 s per clip**, one ffmpeg decode pass
instead of two, and **the shipped filter is byte-identical** — the cache
changes nothing about what the editor receives, only how much of it is
recomputed. Pinned by `tests/test_reframe_frame_cache.py`, whose three
load-bearing tests were verified to *fail* with the cache disabled (the defect
pin reproduces "2 source opens for 1 distinct window"), and which separately
pin the properties the win could be used to break: distinct windows are not
served from each other, a restamped file is not served stale, and the cache
never grows past its own entry bound. The bound is 4 entries ≈ 700 MB at
1080p, deliberately not unbounded — one window is 174 MB.

**Three optimizations were measured and rejected, and the record matters as
much as the one that shipped:**

1. `-hwaccel none` to skip the failing hardware negotiation: **1.01×**, i.e.
   noise. The hardware-decode warnings on stderr ("Your platform doesn't
   support hardware accelerated AV1 decoding") look like a cost centre and are
   cosmetic — suppressed at `-loglevel error`, timing unchanged.
2. Batching many samples into one ffmpeg invocation: a **net loss**. The
   per-invocation fixed cost is the seek (154 ms), and two samples sit 1.07 s
   apart at the default 28 samples, so decoding between them costs ≈224 ms —
   more than just seeking again. The current one-process-per-sample shape is
   already the cheaper one.
3. One seek plus a sequential decode of the window: 29 s for a 30 s window,
   far worse than 28 targeted seeks. Sequential AV1 decode is 96.6 fps but
   the window is sparse by construction.

**The same defect one layer up.** The frame cache made the second *read* free,
but the perception derived from those frames was still computed twice:
`analyse_scene` runs the detector over all 28 frames, and the composition pass
runs it over the same frames again. Found the same way — by counting, not by
reading the call graph: one `scene_filter` call made **56 detector calls for 28
frames**, and one inference pass measures 1.73 s at 1080p (plus 60 ms of
`find_shot_boundaries`, recomputed by the same two callers).

**The fix**: a second bounded LRU at the same choke point, `_perceive_window`,
which reads once and perceives once, returning the frames alongside the boxes
and the boundaries. Both duplicate sites in `reframe_scenes` now go through it,
which removed `get_detector` and `find_shot_boundaries` from that module's
imports entirely — the perception layer now lives in one place rather than
being invoked from two. The entry carries the detector that built it, because
`get_detector` is a process singleton that a caller may replace with a
different model; the identity check makes that a miss rather than serving boxes
the live model does not agree with. Boundaries come back frozen, since every
consumer does membership tests and set arithmetic over them and a shared
mutable set would let one consumer's arithmetic change the next consumer's
shot count.

**Measured before → after on the same window, best of three, same process**
(the perception cache disabled in-place while the frame cache stays live, so
the two wins are isolated rather than conflated):
11.14 s → **9.33 s, 1.19×, saving 1.81 s per clip, 28 inferences instead of
56**, and again **byte-identical filter**. Cumulative from the 18.64 s
baseline: **1.99×, 9.31 s saved per clip**. Pinned by
`tests/test_reframe_perception_cache.py`, whose load-bearing tests were
verified to fail with the cache dead — the defect pin reproduces
"16 inferences for 8 samples" — and which separately pin distinct windows
being perceived separately, a replaced detector invalidating the entry, the
bound not being exceeded, and the boundaries being frozen.

**The same duplicate at a third site, on the path the legacy chain was built
for.** `scene_filter` decides a shot is a plain speaker from
`analyse_scene`'s segments — which already perceived the window — and then
calls `_probe_and_track`, which perceived it again with its own detector loop
and its own `find_shot_boundaries`. So the talking-head clip, the common case,
still did 56 inferences for 28 frames after the fix above. Found by
instrumenting `scene_filter` on the corpus's `pentagon-esper-briefing.mp4`
with all three caches cleared (an earlier pass of the measurement missed it
because `_REFAME_CACHE` survived between iterations and short-circuited the
probe before it could infer — the duplicate only shows up on a cold cache).
`_probe_and_track` now perceives through the shared layer too. **Measured
before → after, same process: 5.04 s → 4.18 s, 1.21×, saving 0.86 s per
clip, 28 inferences instead of 56, filter byte-identical.** Pinned by
`test_the_plain_speaker_path_does_not_re_infer`, which builds a one-region
clip so the classifier sees a single SPEAKER class and the plain path is the
one taken — the composed-path test cannot reach this code, and a test that
only exercised the SPLIT path would have left the common case regressed.


**Where the remaining time goes, measured rather than assumed.** Of the 9.33 s:
7.23 s is one decode pass, 1.73 s is one inference pass, 0.37 s is planning,
composition and probe calls. The decode is 77% of the residual, and at a fixed
sample count it is now irreducible — the sample count *is* the perception
budget, and cutting it changes what the editor sees. That is a
results-affecting decision, not an efficiency one, so it is left here as a
measured boundary rather than attacked. Two `test_reframe_scenes.py` fixtures
had to follow the perception layer into `engine.reframe` (they patched
`rs.get_detector`, which no longer exists there); the fixtures now pin one
shared detector instance, because the identity key means a fresh marker per ask
would be correct but would make every ask a miss.

**What this cost, stated honestly.** The perception is computed eagerly where it
used to be computed only when a caller wanted boxes: the no-detections fallback
path in `_probe_and_track` now also pays for `find_shot_boundaries`, roughly
60 ms it does not use. That is about 1% of a path that already decoded 28
frames, and it is the reason the fix is one line rather than a lazily-computed
perception object.

**The caches are one cache.** The first pass of this work had `_FRAME_CACHE`
and `_PERCEPTION_CACHE` as two `OrderedDict`s sharing a key and a bound by
convention — correct, but a fourth consumer would have had to learn the
convention, and the two could drift. They are now a single entry:
`[times, frames, w, h, perception]`, with the perception filled in place on
first ask and left `None` by the readers that only want pixels (most of them —
28 YuNet inferences are 1.73 s, which a caller that never asks for boxes must
not pay). `_sample_frames` returns a tuple of the first four slots rather than
the entry, so a reader gets no handle on the slot `_perceive_window` fills.
`_clear_frame_cache` is now the only clearing point, and the earlier
`_PERCEPTION_CACHE` name is gone from the module. Re-measured after the
unification: composed path 9.35 s at 28 inferences, plain path 4.23 s at 28,
both filters byte-identical to before it.



**Limits stated as limits.** The speedup is measured on one window of one
source; the redundancy it removes is structural (two requests, identical
arguments) and therefore source-independent, but the *magnitude* scales with
per-frame decode cost, so a cheap codec gains proportionally less. The 11.17 s
that remains is not all decode: one pass is ~7 s and the residual ~4 s is
detection and planning across 28 1080p frames — that is the next measured
target, not yet investigated. The cache shares arrays between consumers,
which is safe only because every consumer reads (`YuNet.detect` copies into
its own buffer, `cv2.cvtColor` returns a new array, the differ only reads);
a future caller that writes in place would corrupt the next consumer of the
same window, and the comment at the cache says so.

### This session — an editor's hand on the shipped ASS was overwritten, and then ignored

The shipped `.ass` is a **render input**, not just a deliverable:
`selekyt/pipeline.py` hands it to `render_clip`, which burns it in via
`subtitles=` at `engine/render.py:199`. That makes the caption document part of
the render contract, and it had three coupled defects, each of which alone would
have left the package internally inconsistent:

1. `selekyt/pipeline.py` wrote the `.ass` **unconditionally on every run**, so an
   editor's moved cue was overwritten before the render cache was even
   consulted.
2. The per-clip render cache key omitted the ASS **contents**, so even a
   preserved edit shipped the cached MP4 with the old cue burned in.
3. `srt_for_candidate` regenerated the sidecar from the candidate, so a
   preserved edit left the package carrying two caption documents that
   disagreed.

**The fix.** `engine.captions.ship_captions(c, ass_path, srt_path,
prev_digest)` is the guard for 1 and 3; the `ass_digest` now recorded in the
render record is the guard for 2. The preserve-vs-regenerate rule is the part
that has to be right, and it keys on the recorded digest: the on-disk document is
preserved verbatim only when it differs from a fresh generation **and** differs
from what we wrote there last time — a change that is neither ours nor the
clip's is someone else's, so the SRT is re-projected from the ASS rather than
independently regenerated. A document that differs from fresh but *matches* the
recorded digest was not edited by an editor; the **clip** changed under it, so
the captions are regenerated to match the clip's new words. Absent `prev_digest`
— every `pipeline_state.json` written before this fix — the difference cannot be
attributed, so the documents regenerate, prioritising "never ship a caption
document that does not match the clip's words" over "never lose a hand edit once
on upgrade". That is the same call the `caption_style` precedent made
(`tests/test_caption_style_cache.py`: absence reads as unknown, the clip
re-renders once).

**A format constraint found while writing the tests, not assumed.** ASS stores
centiseconds and SubRip milliseconds, so `srt_from_ass(ass_content(c))` is
lossy against `srt_for_candidate(c)` by up to 5 ms in the last digit
(`_ts(2.367)` → `0:00:02.37` → 370 ms; `_srt_ts(2.367)` → 367 ms). Byte
equality is therefore **not** a valid invariant for the round trip, and
asserting it would have been wrong in a way that invites a silent fudge; the
pinned invariant is cue-for-cue text and index equality with times agreeing to
the ASS's own resolution (`tests/test_caption_handoff.py`).

**The multi-interval half, now closed.** The defect above had a second surface
that the first fix did not reach: a candidate keeping more than one retention
interval renders as a concatenation of parts, and the per-part ASS was
*regenerated* from a sub-candidate instead of sliced from the shipped document —
so on a multi-cut clip an editor's retime was preserved on disk and then
ignored, and the shipped MP4 disagreed with the file beside it in a way that was
invisible, because both were valid ASS. `engine.captions.slice_ass(text, start,
end)` is the replacement: it keeps the cues that overlap a window, clips a
straddling cue to the window's edges, shifts the survivors onto part-local time,
and passes every non-`Dialogue` line through untouched — an editor's text and
override tags are carried, only the times are retargeted. `engine/render.py`
slices the shipped document on the timeline the document itself keeps, which for
a shipped ASS is the *edited* one (`ass_content` already emits through
`_source_to_edited`, with every removed gap closed), so part `i`'s window is
`[offset, offset + duration)` with `offset` the sum of the durations before it —
not the interval's source time. `pysubs2` was the nominated dependency for this
(`docs/rd-landscape.md` ranking #1); it is no longer needed, and the operation
is 35 lines in-tree rather than a dependency.

Pinned by `tests/test_caption_multi_part.py` (new): six unit tests on the slice
itself (shift and duration preserved, cues wholly outside dropped, cues
straddling an edge clipped, editor text and tags byte-identical, header passed
through, a cue crossing a part boundary burned in both parts), the invariant
that slicing every part's window and re-offsetting recovers exactly the
document's declared cue spans, a spy that captures what each part is handed
(`render_clip` deletes its parts directory on the way out, so the ASS is read
inside the spy) and asserts the editor's cues reach the burn and the
transcript's words do not, and a pixel-level burn test asserting each cue lights
a contiguous run wholly inside its own part, of exactly its own duration in
frames, with the concatenation seam staying clean.

**Recorded, not fixed — and deliberately left alone.** One timing detail the
pixel test surfaced and was investigated rather than asserted away: input
seeking (`-ss` before `-i`) opens a part on the first source frame at or after
the cut, so a part whose interval starts at 1.94 s on a 15 fps grid opens on the
frame at 2.00 s and the container *labels* that frame 0.066 s rather than 0.
libass times the cue against the media the part actually holds, so the burn
lands on the frames carrying the speech the cue describes and the pixels are
right — only the container's own frame labels are a frame off, and the concat
demuxer renormalises the deliverable's presentation times onto a clean
contiguous grid, so a viewer sees the subtitle with the speech. Aligning
`retention_intervals` to the frame grid would make the labels agree, but it
ripples into the EDL/FCPXML export and the frame-accuracy tests this module
already pins, for a defect nobody can see. The tests therefore measure run
length and part containment, not absolute frame indices.

Regression tests: `tests/test_caption_handoff.py` (new) pins `ship_captions`
semantics and the SRT projection — a hand edit kept verbatim, the sidecar
re-projected from a preserved ASS, regeneration when the clip changed and when
the recorded digest is absent, stability on an unedited run, tag stripping and
cue renumbering in the projection, and an empty sidecar for a cue-stripped
document. `tests/test_caption_style_cache.py` gains the integration half: an
edited ASS invalidates the render cache, and the record carries the style and
digest actually burned in.

### This session — the candidate scan was deleting the back half of every long source

`make_thoughts` carried `CAP = 4000` and `return`ed once it had emitted that
many candidates. Candidates are built in start order, so on a long source that
cap did not bound the cost — it **deleted the tail**. Nothing past the cap's
reach could generate a candidate, so nothing there could ever be selected, and
no editor would ever see a story from it. On the committed corpus that hid the
last **35.6%** of `nasa360-ep5` (reach 941.2 s of 1462.0 s) and **30.3%** of
`emwcon-panel-2018` (2323.6 s of 3332.8 s). At the product's stated 1–3 hour
target the cut landed near the 18-minute mark of a 3-hour source: most of the
video was never searched, by construction.

**Why the cap existed.** ASR on fast or noisy audio can emit sub-second
segments, and 30 minutes of those yields hundreds of thousands of overlapping
windows (measured: 541,874 candidates, 305 s). The cap was the wrong answer to
a real problem — bounding the cost by *not searching the tail* reintroduces the
defect under a different name.

**The fix** (`engine/editorial.py`) spends the budget on even coverage instead.
The explosion is driven by how many sentences can *end* within one `max_sec`
window, so that fan-out is measured with a sliding two-pointer (both ends
advance monotonically, so it is O(starts)), and only when
`starts × fan_out` exceeds the budget are starting positions strided:
`stride = ceil(starts × fan_out / BUDGET)`. Degradation goes from "the tail is
unreachable" to "coverage is coarser everywhere", and at normal transcript
density the stride is 1, so the output is a strict superset of the old one.

**Measured, before/after**: all 11 committed runs run at stride 1 — the new
code's first 4000 candidates are identical to the old code's entire output, so
on real media the change is purely additive and no selection made within the
old reach can move. The two sources that exceeded the cap go 4000 → 5782
candidates (64.4% → 100.0% coverage) and 4000 → 6758 (69.7% → 100.0%). The
pathological cases stay bounded *and* complete: 30 min at 0.4 s segments,
1365 candidates, 1.64 s, 99.8% of the source covered. Both measurements are
reproducible: the "before" side is `a1e9150:engine/editorial.py` exec'd under a
throwaway module name, and the probe asserts the `CAP = 4000` marker is present
at that revision so it fails loudly rather than comparing the new code against
itself. The probes are session scratch, not committed artifacts — the
committed evidence is `tests/test_editorial_coverage.py` (the properties) and
`tools/corpus_reproducibility.py` (the state).

**Effect on the corpus, and why it is attributable rather than incidental.**
Those two runs stop reproducing. Their committed selections were made by the
truncated scan, so the newly reachable tail outcompeted earlier clips it had
settled for. Both keep their clip count (5 and 6). Every span the current code
ships that the committed artifact does not **starts past the old scan's reach**,
and every span it dropped starts within it — e.g. `nasa360-ep5` now ships
1023.2→1044.1, 1069.7→1099.0 and 1330.8→1341.9 against a reach of 941.2 s, and
dropped 327.9→346.3, 507.1→516.2, 606.0→620.0. A blind spot closing, not a
scorer moving. This is *not* more shipped coverage — clip count is fixed, so
the union of intervals barely moves (6.9% → 8.2% and 3.7% → 3.6%); what moves
is *which* minutes ship, with the span extending to 1342 s and 3129 s.

**Disposition: recorded, not regenerated.** The corpus is frozen as historical
benchmark evidence, and the media for both runs is not committed, so
re-rendering is impossible here and regenerating only the JSON sidecars would
break their agreement with the committed pixels. `tests/test_corpus_reproducibility.py`
now names all four non-reproducing runs in one expectation — the two confidence
runs and the two scan runs, with the distinction stated in the docstring — and
its `_RUN` fixture moved from `nasa360-ep5` to `pentagon-esper-briefing`, which
still reproduces and is one of the two runs whose media *is* committed.
`docs/CORPUS_BENCHMARK_STATUS.md` carries the numbers above and now reads
**3 of 7** delivering runs reproduce exactly.

Regression tests: `tests/test_editorial_coverage.py` pins both properties at
once — a 93-minute synthetic source must yield candidates in its final 10%
(that source exceeds the old cap, so the test means what it says), and a
sub-second-segment transcript must stay under 60,000 candidates *and* still
reach its tail.

### This session — a transcript with no punctuation was never searched, and every refusal said the same thing

Two defects with one symptom. Both made a run end with no clips and both
reported it as "No complete standalone stories passed the editorial/context
gate", which is a sentence about an editorial judgement — so a user could not
tell a file with no speech, a transcript too short to clip, a transcript the
scan could not search, and a transcript the gate genuinely refused apart from
each other, and the message implied a judgement that in three of the four cases
never ran.

**The structural one, found in the committed corpus.** `hindi-baiga-interview`
has 53.8 s of real Hindi in 3 segments and **no terminal punctuation anywhere**
— no danda, no period, no question mark. `_sentence_end` can never fire on text
carrying no terminal mark, so the scan's only remaining way to close a span was
the `max_sec - 2` relief valve. Every whole ASR segment became one "sentence",
the accumulated span measured 52.76 s, and the valve sits at 53.00 s — **0.24 s
short, so zero candidate spans were ever constructed.** Nothing was scored and
nothing was judged; the source was never searched.

Verified rather than inferred. The span length matters and is easy to get wrong
— reading only the last segment's `end` (53.74) against the valve gives a false
"short by 0.74 s"; the span is measured from the *first* sentence's start
(0.98), so it is 53.74 − 0.98. The pre-fix result was confirmed by executing
`HEAD:engine/editorial.py` under a throwaway module name against the committed
transcript: **0 candidates pre-fix, 6 post-fix.** An earlier probe that patched
`_sentence_end` to return False was **invalid** and initially looked like a
contradiction of the conclusion — the new fallback calls `_scan(False)`, which
ignores `_sentence_end` entirely, so it still emitted 6. Load the old module;
do not try to recreate the old behaviour by patching the new one.

**The fix** (`engine/editorial.py`) extracts the scan into a nested
`_scan(require_terminal)` and, only when that returns nothing *and* sentences
exist, re-runs it as `_scan(False)` — closing a span at the length and word
floors instead of waiting for a mark that will not come. The fallback is
strictly additive: a punctuated transcript is scanned exactly as before, and
punctuation can only ever lose a span the fallback found, never gain one. That
property is asserted, because a fallback that regressed the common case would
be worse than the defect.

**The user-visible outcome for this source is unchanged: it still ships no
clips.** The 6 spans include 5 that clear rank's length and word floors, and
every one is refused by the context guard for **low factual confidence, best
measured 0.53 against the 0.75 floor** — mean per-word confidence 0.399, the
same property that retired `hadhazy-protest` (0.639) and `telugu-tutorial`
(0.319). What changed is that the refusal is now true and checkable instead of
a structural failure dressed as a verdict. This is the point worth holding: a
fix can be correct and still not change any shipped output, and the honest
record of it says so.

**The message one.** `selekyt/pipeline.py` gained `_no_story_diagnosis`, which
picks the message by what actually happened rather than one sentence for all
cases: no candidate spans were built at all; spans were built but none cleared
the length/word floors (the measured duration and word count travel with it);
spans were eligible and every one was refused, naming the dominant reason and,
for confidence, the measured number; or the ranking set clips aside for
variety. A defect was found while writing the tests: the confidence remedy —
"re-run with a larger model, or with `--language`" — existed only on the
post-ranking refusal, while the during-ranking refusal, which is the branch
this corpus source actually hits, gave the number with no actionable next step.
An editor reading 0.50 cannot tell a transcription defect from weak material,
so the remedy now travels with both.

**What the corpus now says.** `docs/CORPUS_BENCHMARK_STATUS.md` replaced its
two-line dismissal of the four no-selection runs with the split that matters:
three are `NO_SPEECH_DETECTED` with 0 segments (correct rejections, nothing to
reproduce) and one is `NO_STORIES_PASSED` with 3 segments — real speech, and
the only run in the corpus where the editorial layer was reached and nothing
was selected. That distinction was previously invisible in the document set,
and the four runs were all implied to be the same kind of nothing.

Tests: `tests/test_unicode_pipeline.py` 7 → 10 (the fixture really carries no
terminal mark *and* is short enough that the relief valve cannot fire either,
so nothing but the fallback is exercised; an unpunctuated transcript still
yields rankable, guard-passing, script-pure candidates; punctuating cannot lose
a span). `tests/test_speechless_source.py` 3 → 7, pinning each refusal shape
against its own message and asserting the wording never claims a gate ran when
one did not. One of those tests stubs nothing below the transcription boundary
— `make_thoughts`, `rank` and `validate` are the real ones, so it exercises the
refusal path a user would actually get.

### This session — the MSIX job went green on the fifth run, and the artifact was verified off the runner

The `msix` job existed to verify a manifest that had never been packed
anywhere. **It has now done that: run `35555583046` (commit `d428c78`) is
GREEN** — 27m10s, both jobs 0 failed / 0 skipped, artifact
`selekyt-windows-msix-unsigned-unverified`. The four failures below are the
history of how it got there; each was one line of arithmetic no Linux host
could have caught, and each produced an error message that pointed at the wrong
layer.

**Run `35548174430`** failed 47 seconds in:
`actions/download-artifact` reproduces the directory tree the upload captured,
so the NSIS installer arrived at
`bundle/apps/desktop/src-tauri/target/release/bundle/nsis/*.exe` — six
directories deeper than the flat `bundle/` the pack script's `-BundleDir`
contract documents. Nothing on this host knows what the download action does
with a nested `path:`. Fixed with `merge-multiple: true` plus a recursive
lookup, because either layout is legal and the cost of looking is nothing.

**Run `35549741215`** failed one line later, and this one was my own arithmetic.
The installer arrived flat, the 578 MB silent install completed in 69 seconds,
and the script then threw

```
AppxManifest.xml is missing at D:\a\selektyt\selektyt\apps\src-tauri\msix\AppxManifest.xml
```

That path is one directory short of the real file. The script lives at
`apps/desktop/`, the manifest at `apps/desktop/src-tauri/msix/`, and the line
read `$tauri = Split-Path -Parent $here` — which resolves to `apps/`, a
directory containing no `src-tauri` at all. `$here` itself resolved correctly,
so `$MyInvocation.MyCommand.Path` is fine under the `pwsh -Command ". 'script'"`
invocation GitHub uses; the anchor was wrong, not the resolution.

**Why it lasted a whole session:** the failure message names a path that looks
plausible. A missing-manifest error points at the manifest; nobody reads
"`apps\src-tauri`" and sees "I split one directory too many". And nothing off
the runner resolves PowerShell path arithmetic against the repository tree, so
there was no local signal at all — the same shape as the asset check that
matched five of six images: prose in the path, and the wrong count reported as
a complete set.

**The pin:** `tests/test_desktop_packaging.py` now extracts the expression the
script assigns to `$tauri`, resolves it against the real tree, and requires the
manifest to exist at the end of it. An expression the test does not recognise
fails rather than passing unmodelled. Run against the pre-fix script it
reproduces the runner's own error message verbatim, which is what makes it a
regression test rather than a test that happens to pass.

**Run `35551339487`** got past all of that, and failed on arithmetic again —
but arithmetic on two *forms* of one path. The reconciliation reported that the
committed manifest disagreed with the payload and rewrote the packed copy to

```
Executable="ad\SelekyT.exe"
```

`ad` is the last two letters of "payload". `$env:TEMP` on a GitHub Windows
runner is the 8.3 short form, `C:\Users\RUNNER~1\...`, while `Get-ChildItem`
reports the canonical `C:\Users\runneradmin\...`. Slicing the discovered
path by the *string's* length cut three characters early — exactly the
difference in length between those two spellings of one directory — and
landed inside the payload directory's own name. The committed manifest was
never wrong; the warning that said it was, was the defect. Fixed by resolving
the directory once with `Get-Item` and using its `FullName` for both the
enumeration and the length, which makes the two consistent by construction.

The guard I wrote first was wrong too, and I caught it by reading my own
comment rather than by running anything: it checked the *shape* of the derived
path, and `ad\SelekyT.exe` fails a shape test — but so does
`SelekyT\SelekyT.exe`, which is a perfectly legal package-relative path if
Tauri ever nests the install. The guard would then have been the thing
breaking the build. It now tests the operation itself: rejoining the derived
path to the payload must resolve to the file it was derived from. That holds
for a nested layout and fails for a sliced one, in either path form.

**Run `35553464487`** is the first run where the script's own arithmetic was
right end to end — `payload executable: SelekyT.exe` with no correction
warning, all six assets present — and it failed inside MakeAppx:

```
MakeAppx : error: Failure at (packageWriter3->AddPayloadFiles(
    addedFiles, preparedFiles, performanceOptions.memoryLimit))
  - 0x8007000e - Not enough memory resources are available to complete
  this operation.
```

The tool selected was `...\bin\10.0.26100.0\x86\makeappx.exe`, the **x86**
binary. The SDK ships x86 and x64 side by side under one version directory,
the script took the first hit of a descending name sort, and `x86` sorts above
`x64` because `'8'` is greater than `'6'`. A 32-bit process has 2 GB of address
space against a 578.7 MB payload's worth of files, and the writer's
prepared-file list exhausted it. Nothing in this repository was wrong. The
fix is to prefer the 64-bit binary and to *warn* rather than throw when only a
32-bit one exists — that tool is still correct for a payload small enough to
fit, and hard-failing would be a regression on a machine with an older SDK.

**Why the pattern matters more than any single one of these:** all four
failures were invisible from this host, and all four produced error text that
pointed at the wrong layer. "Manifest is missing" pointed at a manifest that
existed. `Executable="ad\..."` pointed at a manifest that was correct. An
memory error code pointed at a package that was fine. Each required reading
the failure as a *symptom of a computation* rather than as a report about a
file — and each was fixed by making the computation's inputs consistent by
construction, then pinning the property with a test that fails against the
pre-fix code. Four runs, four mutation-verified pins, and the boundary of what
is verified moved from "the download happened" to "the pack starts".

**Run `35555583046` (commit `d428c78`) was the fifth run, and it was GREEN.**
27m10s, `completed success`, both `build` and `msix` 0 failed / 0 skipped. The
fix above — preferring `x64`/`arm64` over the naive descending-name sort — was
the last thing standing in the way, and MakeAppx packed the payload with
validation ON from
`C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\makeappx.exe`.
Artifacts: `selekyt-windows-x64-unsigned` (1245.54 MiB) and
`selekyt-windows-msix-unsigned-unverified` (1527.44 MiB).

**It has not been a one-off.** Three further runs have since gone green on
commits made outside this session — `35594437455` (`d288189`, the landing page,
27m29s), `35594441067` (`1d16498`, the Pages deploy, 37m0s) and `35604822603`
(`2cf32a7`, the README rewrite, 27m51s). The last of those was inspected
job-by-job: `build` and `msix` both `success`, 0 steps failed, 0 steps skipped.
Four consecutive greens on four different commits, including commits this
session did not author, is a *stable* pipeline rather than a lucky run —
which is the difference between "the fix worked" and "the fix worked once".
The streak has continued since, including on this session's own docs commits;
it is a property of the run history, so check
`gh run list --workflow build-windows.yml --limit 10` before quoting a count.

The MSIX artifact was then verified **off the runner, from this Linux host**,
by reading the GitHub artifact's bytes directly over HTTP ranges and parsing
the zip64 central directory by hand. Results: 9552 entries, zero duplicate
names, 2057.5 MiB uncompressed → 1525.2 MiB compressed. Those two are not the
same quantity as the artifact's reported 1527.44 MiB and the difference is not
rounding: 1525.2 MiB is the sum of the members' compressed streams, while
1527.44 MiB is the whole `SelekyT.msix` file, which additionally carries ~2.2
MiB of local headers, the 1.21 MiB central directory and the zip64 records.
They reconcile to within 0.15%. Present: `AppxManifest.xml` (4704 B),
`[Content_Types].xml` (94152 B), `AppxBlockMap.xml` (4006955 B),
`SelekyT.exe` (4791296 B) and all six `Assets\*.png`. The packed manifest still
reads `Executable="SelekyT.exe"` — the committed copy and the installed payload
agreed, so the reconciliation logic never fired, which is the outcome it exists
to produce. Identity matches `tauri.conf.json` on all four fields
(`Name`/`productName`, `Version`/`version` in dotted-quad form,
`Publisher`/`bundle.publisher` with the `CN=` prefix,
`Executable`/`mainBinaryName`).

**Two non-obvious facts about reading a GitHub artifact, both of which cost a
round trip each.** (1) The outer artifact zip stores the MSIX with method 0, so
**artifact offsets are MSIX offsets plus 42** (30-byte local header + 12-byte
name `SelekyT.msix`). Every range read before that correction came back 42
bytes early and reported all six assets missing. (2) MakeAppx writes a *full
zip64* central directory: every 32-bit size/offset field is the `0xFFFFFFFF`
sentinel and the real values live in the `0x0001` extra block, which starts at
`o+46+nlen` — **not** `o+46`, or every size reads as 4 GiB. It also uses
trailing data descriptors (flag bit 3) rather than local-header sizes, so
inflating a member needs `zlib.decompressobj(-15)` and `unused_data`, not a
length from the header. Separately, the signed Azure blob URL behind the
artifacts API **expires about 10 minutes after issue** — resolve the redirect,
then finish all reads inside one window.

**One real defect found in the payload, and FIXED on this host:** the whisper
`small` weights were in the package **three times byte-for-byte**
(crc32 `81093b67`, 483546902 bytes each) — the snapshot `model.bin`, the repo
blob, and a `models/hub/blobs/42/...` copy. 1383 MiB of a 2057.5 MiB payload,
67%, was one file; compressed, the three copies were 1275 MiB of the package's
1525.2 MiB, 84%. A `stage-windows.sh` staging-layout question, not a manifest or
MakeAppx one.

`stage_model()` now runs `apps/desktop/collapse_hub_cache.py` after staging, on
every invocation and *outside* the reuse guard that protects the download. The
step materialises each snapshot entry as a real file and prunes any blob whose
sha256 matches a shipped file — content-driven, because the number of copies
depends on the runner's symlink privilege (symlinks on POSIX, byte copies where
`_create_symlink` falls back to `shutil.copyfile`). Verified on this host in
both layouts by asking the installed `huggingface_hub` to resolve the collapsed
cache offline: `tests/test_collapse_hub_cache.py` (7 tests) builds both layouts,
checks resolution, idempotence, and that a weight-sized blob which is not a copy
of any shipped file fails the build. Writing that test found a genuine gap in
the step: a blob whose *size* matched a shipped file but whose *content* did not
fell through the prune and would have been silently shipped — so the hard error
only ever fired on a size mismatch. It now fires on a content mismatch at
weight scale.

Still UNVERIFIED: the size win is predicted (~2057.5 → ~1135 MiB uncompressed)
and not measured, because no Windows run has staged through the new step. That
is the next thing a real `build-windows.yml` run settles.

**What remains UNVERIFIED, and this is now the whole remaining gap:**
everything *after* the pack. The MSIX has never been installed, the app has
never started under the `windows.fullTrustApplication` activation, the tile and
splash have never rendered, and no Partner Center submission has been made.
This host has no Windows machine, no PowerShell and no SDK, so none of those
can be tested here — the artifact name says `-unverified` because that is true.
Schema-valid, structurally consistent and MakeAppx-accepted is not installable.

### This session — a fix that broke the file at parse time, and the MSIX half of the contract

**The labelling fix, when first applied, made GitHub reject the workflow file.**
The previous fix tees the build log to a path the labelling step reads, and it
put that path on the *job's* `env:` — the natural "visible to every step" place.
It is visible there. It is also illegal: **the `runner` context is available in a
step's `env:` and not in a job's**, and using it at the job level does not fail a
step, it fails the file:

```
Invalid Argument - failed to parse workflow:
(Line: 53, Col: 26): Unrecognized named-value: 'runner'.
Located at position 1 within expression: runner.temp          (HTTP 422)
```

Run `35545641450` therefore reported failure with **zero jobs executed**. The
signature is distinctive and worth knowing, because nothing else in the pipeline
fails this way: `created_at` equals `updated_at` to the second, the jobs API
reports `total_count: 0`, every log download 404s, the rerun API answers `403
This workflow run cannot be retried`, and `gh run view` prints only that the run
"likely failed because of a workflow file issue".

The diagnosis took the long route because every local check passed. The YAML
parses, it lints clean (no tabs, no CRLF, no duplicate keys, no anchors, one
document), and the file fetched back from GitHub's contents API was
**byte-identical** to the local commit. None of that establishes that GitHub
will accept the file. `workflow_dispatch` on the ref is the only offline probe
that reports the real error, and it is now the tool to reach for first: it
answers in under a second and costs nothing.

Fixed by declaring the path on each of the two steps that need it — step env
does not cross steps in either direction, so both copies are required; one
declaration leaves the other reading an empty string, which is the original bug.

**The test suite passed on the file GitHub rejects, and that is the interesting
failure.** The helper the tests use merges the job's env into each step's,
which is a correct model of *visibility* and useless as a model of *legality*:
a job-level declaration satisfies every assertion and is rejected anyway. The
rule is now pinned explicitly — job-level env may not use the `runner` context —
and the new assertion was verified to discriminate by re-injecting the
job-level declaration, which fails exactly one test with a message naming the
parse-time rejection. Six mutations against this workflow file have now each
been confirmed to fail exactly the test written for them.

**The MSIX / Store half of the distribution contract now exists, and is
UNVERIFIED.** Tauri 2 has no `Appx` bundle type (verified in the vendored
`tauri-utils` source, not assumed), so the Store path needs its own stage:
`apps/desktop/package-msix.ps1` installs the NSIS installer the `build` job
produced — silently, into an empty directory — and packs *that*, so the MSIX
payload provably matches what the installer installs rather than a
hand-assembled guess at Tauri's layout. `src-tauri/msix/AppxManifest.xml` is a
full-trust desktop-bridge manifest on 10.0.17763 using the EntryPoint form; the
equivalent `uap10:RuntimeBehavior` form only exists from 10.0.19041, and
declaring both is an error when they contradict. A new `msix` job runs it.

The job is **separate and `needs: build`** so that a failure in unverified MSIX
tooling cannot take the installers down with it, and it downloads whichever
artifact the build job uploaded by reading its `signed` output. Those two
artifact-name expressions are pinned against each other by a test, because two
hand-written copies of a two-valued label drift apart silently and the failure
is an MSIX job that downloads nothing while the build job looks healthy — three
mutations against it (hardcoded label, dropped dependency, wrong output name)
each fail exactly that test.

**It has now run on a Windows machine.** The job went green on its fifth run
(`35555583046`) and has repeated green three times since on commits this session
did not author. The manifest was packed with validation ON and the artifact
verified off the runner, so what was speculative above is now observed —
except the last sentence, which is unchanged: **a green pack is not an
install**, and the artifact is still named
`selekyt-windows-msix-unsigned-unverified` so it cannot be mistaken for one.
What has *not* run anywhere is the install itself, the full-trust activation,
the tile and splash, or a Partner Center submission.

### This session — the Windows build ran, and it had been mislabelling every release

Two CI defects, both silent, both found only by reading the runs rather than
the code:

**The Linux `shell` job had never passed.** It was added in `59e5327` and was
red on its first three runs, because `tauri_build::build()` runs from
`build.rs` on every `cargo check` and hard-errors when
`tauri.conf.json`'s declared paths are absent — and those are produced by
`stage-windows.sh`, which downloads an interpreter, ffmpeg and ~500 MB of
weights. Requiring that in the cheap job defeated its purpose. Fixed with
`TAURI_CONFIG='{"bundle":{"externalBin":null,"resources":null}}'` for that job
only. **The values are `null`, not empty**, because tauri-build merges as an
RFC 7386 patch and an empty object *recurses* and changes nothing — that was
the first attempt, and it still failed on `resources/python`. Verified locally
by hiding the staged tree and re-running check, clippy and the 12 unit tests,
then confirmed green on the runner.

**The Windows job was publishing every unsigned build as `signed`.**
`sign.ps1` declines by writing `##[warning]SELEKYT_UNSIGNED` to the *build*
step's log. The labelling step grepped `$GITHUB_STEP_LOG`, which is its *own*
log, so the marker was never found and `signed=true` was unconditional. The
run for `de0c151` shows the evidence plainly: **154 marker occurrences** in
the build job, and an artifact named `selekyt-windows-x64-signed` (1.22 GiB).
The artifact name is the only trust claim a downloaded file carries before it
is run, so this is the one defect in the whole pipeline that actively
misrepresents the trust state — and `docs/RELEASE.md` was asserting at the
same time that the name could be trusted. Fixed by teeing the build log to a
file the labelling step reads, plus a fail-closed branch: no marker and no
credentials fails the run rather than being labelled. Four new tests in
`tests/test_desktop_packaging.py` cover it — one **executes the real
labelling script** in all three reachable states instead of reimplementing
its branching, which is the trap the file's own docstring warns against. Both
new tests were verified to fail against the old workflow and pass against the
fixed one; a test that passes on both is not a regression test.

**A stale, mislabelled artifact is still downloadable** and I could not
delete it: `selekyt-windows-x64-signed`, 1.22 GiB, run `35538322486`. It is
**unsigned**. The deletion was denied as irreversible, which is the right
call — it is not mine to remove unilaterally. **A user should be asked before
it is deleted**, or it can be superseded by a re-run of the fixed workflow.
Until then, treat any artifact from that run as unsigned regardless of its
name, and re-check `gh api repos/desukab/selektyt/actions/runs/35538322486/artifacts`
before citing it.

What is still true and unchanged: `sign.ps1` has only ever *declined*. No
Azure Trusted Signing credentials exist, so every build the pipeline can
currently produce is unsigned, and SmartScreen will warn on all of them.

### This session — the window ran, and it found what the contract could not

The shell had been verified only through its JSON-RPC surface. That changed:
the binary was launched under `Xvfb :99` at 1440×2400 and driven with
`xdotool`, reading the rendered result back with `tesseract`. All six review
tabs rendered real content from a project the engine had written — 15 SRT cues
with cue text and in/out times, the transcript's opening line, the QA row, and
the artifact listing. This is still a **Linux** statement; see Outstanding item
4 for what it does and does not license.

**Running the window found a bug the contract was structurally incapable of
finding.** The Captions tab said "No captions were written for this clip" for
a file that existed. Every request on the wire was being answered correctly —
`read_text` resolved a real path and returned real text — so no envelope test
could fail. The defect was in *which path the UI chose to ask about*, and it
had two independent halves, either of which was sufficient:

1. `selekyt/pipeline.py` recorded `report.json` paths as
   `str(project / sub)` where `project = out_root / "project"` was **never
   resolved**. A CLI run from the repo root therefore wrote repo-root-relative
   paths into the report. The sidecar's cwd is the app's own directory, where
   no `corpus/` exists, so those paths silently pointed at nothing.
2. The UI preferred that recorded string over the directory the Rust shell had
   *itself resolved* — `state.project`, which is absolute. Four sites did
   `r.captions || ${state.project}/captions` and similar, an inverted
   precedence: the authoritative value was the fallback.

Fixed on both sides, and the two-sided fix is the point — either alone would
have left the other waiting to bite. The engine now resolves `project` before
recording anything; `listArtifacts`, `renderStory` and `renderCaptions` build
every project subpath from `state.project`; `readProjectText`'s parameter was
renamed to `projectDir` to make the contract legible at the call site. The
standing rule for any future UI site that needs a project subpath is in the
agent memory as `report-paths-are-cwd-relative`.

**A verification trap worth remembering.** A background "full suite" run
earlier in this work reported `[exited with code 0]` and I recorded it as
green. It had not run the suite at all: the command carried `--timeout=900`,
`pytest-timeout` is not installed, and pytest printed its usage error to
stderr — which the tail of the output file did not show — while the shell's
own exit code was 0. **An exit code and a rootdir line are not a pass
count.** The suite was re-run properly and the number below re-measured. This
is the same class of mistake as [[passed-unit-tests-do-not-mean-the-change-works]]:
green *evidence* is not green *behavior*.

### This session — cancellation at the process boundary, a contract-tested UI, and signing that says what it is (`59e5327`)

The desktop app gained the three things that separate a wrapper from a
workstation: you can stop a run, the UI is proven against artifacts the engine
really wrote, and the installer's trust state is labelled rather than implied.

**Cancellation.** The engine has no abort primitive — `serve()` in
`selekyt/server.py` is strictly sequential, and `_HANDLERS` has no cancel entry.
So `selekyt_cancel` kills the process: `Sidecar` fields are individually wrapped
in `Arc`/`Mutex` precisely so `cancel()` can reach `child` and `cancelled`
without taking any lock `call()` holds. That is not an aesthetic concern; a
single coarse lock makes Cancel freeze the window for as long as the run would
have taken. Killing is safe rather than messy because **every ffmpeg call in
`providers/ffmpeg.py` uses `stdout=subprocess.PIPE`** (all four sites), so the
RPC reader gets an immediate EOF instead of blocking on an orphaned child still
holding the write end. A cancelled run returns the distinct error string
`"cancelled"`, which the UI renders as a Resume action rather than a failure
banner; resume works because the engine already writes per-chunk atomic
checkpoints (`chunk_NNNN.json`), so restarting `run_pipeline` on the same out
dir picks up where it stopped.

**The UI's pure logic became testable, and the test found a real bug.**
Formatting, the timeline arithmetic and the SRT reader moved into
`apps/desktop/ui/lib.mjs`, imported by both the app and
`tests/ui_contract.mjs`. That test runs the parsers against a real project tree
when given one and against synthetic input otherwise, so CI stays meaningful
without a fixture. On its **first real run** it failed all three caption files:
the engine writes single-digit hours (`0:00:00,787`), and my regex demanded
`\d{2}`, so it rejected every cue in every real caption file. The Captions tab
would have reported a missing file for media that had one — invisible until
someone installed the app. Fixed to `\d{1,2}` in both the parser and the test.
The timeline section also asserts `kept + omitted == span` and that the
renderer's derived cut length matches `removed_seconds`, so a bar that
disagrees with the number next to it fails here rather than in front of a user.

**Signing is wired but does not fake anything.** `tauri.conf.json` sets
`bundle.windows.signCommand` to run `scripts/sign.ps1` on every build. With no
`SELEKYT_AZURE_*` secrets the script logs `##[warning]SELEKYT_UNSIGNED` and
exits 0; the Windows workflow greps that marker and names the artifact
`selekyt-windows-x64-unsigned` or `-signed`. Add the secrets and the same job
signs and verifies — no config change, no second pipeline, no "signing step"
that exists only on a slide. `tests/test_desktop_packaging.py` pins the hook's
argument contract, asserts sha256 + RFC 3161 timestamping, asserts the config
declares a real application identity, and **asserts no credential is
committed** (no 40-hex thumbprint, no GUID, no `PRIVATE KEY` / `MII` bytes, no
`-kvu $azure.VaultUri` expansion in committed text).

**The Tauri shell compiles on Linux.** `webkit2gtk-4.1` and `gtk+-3.0` are on
this box, so `cargo check`, `cargo test` and `cargo clippy` all run locally —
and `tauri::generate_context!` runs the codegen that validates
`tauri.conf.json` against `deny_unknown_fields`. That last part was verified to
actually reject a bogus `azureTrustedSigning` key, so it is a real validator
rather than a vacuous one. CI gained a `shell` job that does this on Linux
before the Windows job spends ~25 minutes and a ~1 GB download.

**Two things to carry forward:**

* **MSIX is impossible in Tauri 2, so the Store path needs a packaging stage
  outside it.** `BundleType` in the vendored
  `tauri-utils-2.9.3/src/config.rs` is exactly `{Deb, Rpm, AppImage, Msi, Nsis,
  App, Dmg}` — read from the crate source, not from docs that imply more. That
  stage now **exists and is green**: `apps/desktop/package-msix.ps1` + MakeAppx +
  `AppxManifest.xml`, verified on the runner and off it.
  `scripts/sign.ps1` is still **UNVERIFIED** — there is no PowerShell on this
  Linux box, and it has never been executed. Its contract is pinned by a test
  that reads it, which is the strongest statement that can honestly be made
  here.
* **The desktop artifact tree is the spec.** Every UI assumption —
  `selections.json` fields, `edit_plan.json` arithmetic, the SRT grammar, the
  two aspect buckets — was written from files a real run produced, and the one
  time I assumed instead of reading, the parser silently rejected everything.
  The lesson generalises: before writing a renderer for an engine artifact,
  dump the artifact.

**Not done, and said plainly:** the app has not been run end-to-end in this
environment, and no Windows install has ever completed. The local installer
copy at `/dev/shm/win-artifact/` was deleted to free disk when `/dev/shm`
filled to 100% during a test run; the build is reproducible from `main` via
`.github/workflows/build-windows.yml`, so this cost nothing but the convenience
of a local copy. What *was* verified this session is the pure logic layer
against a real project tree, the Rust shell's unit behaviour including
cancellation, clippy at `-D warnings`, and the full Python suite.

### Prior session — the first SelekyT Windows desktop application

The mission was to build the first real desktop app for Windows 11, with the
acceptance test being a real video through the real pipeline rather than "it
compiles." The outcome is in `docs/RELEASE.md`; the short version is that the
**engine path is proven and the Windows binary is not**.

What actually happened, in an order worth knowing:

1. The Tauri 2 shell (`apps/desktop/`) was written: four commands over two
   sidecars, the same JSON-RPC envelope the CLI's `selekyt --server` speaks.
2. It compiled, and `cargo test` then **hung** on a test that wrote to stderr.
   That was not a flaky test: `relay_logs` was the only stderr drainer and it
   was wired from the UI path, so the deadlock guarantee depended on the UI
   being set up. `Sidecar::spawn_in` now drains stderr unconditionally into an
   mpsc channel. This was found by a test, not by inspection.
3. A real end-to-end run against `corpus/media/pentagon-esper-briefing.mp4`
   (1013.9 s, 1280×720) **hung for 55 minutes** at 0% CPU. `cat /proc/<pid>/wchan`
   said `anon_pipe_write` on fd 2: the verification harness had
   `stderr=subprocess.PIPE` and no reader, so ffmpeg's progress filled the 64 KiB
   pipe buffer and blocked before its reply could be written — the same deadlock
   class as step 2, reproduced independently. Draining stderr took the identical
   run to **61.3 s**. The engine was never at fault; the harness was. Any future
   driver of the sidecar must drain stderr continuously or it will hang the same
   way once a render is long enough.
4. That run returned `ok: true` with **32 artifacts**: 8 h264+aac renders
   (4 landscape 1280×720, 4 vertical 406×720), 8 EDL + 8 FCPXML, 12 caption
   files, a 283-segment `en` transcript, `candidate_count: 3211` narrowed to
   `selected_count: 4`, and `qa.json` `ok: true` with 0 errors.
5. Three real Windows portability bugs, each silent on Linux: the NLE
   file-URI encoder emitted `file://C:%5C...` (drive letter became the URI host,
   so the whole sequence imported offline), yt-dlp JSON decoded as cp1252, and
   `--doctor` required a `python3` that a python.org Windows install never has.
6. The `build-windows` workflow ran on a real `windows-latest` runner: staging
   succeeded, both sidecars answered `ping`, and the Rust compile was minutes in
   — then **my own push cancelled it**, because `concurrency` was keyed on the
   ref alone with `cancel-in-progress: true`. The group is now keyed on the run
   id as well, which was verified by the next push not cancelling the build it
   superseded. **The installer has since completed** — `build-windows` for
   `de0c151` finished in 22m27s and uploaded NSIS + MSI artifacts. What has
   never happened is anyone installing one. That distinction is load-bearing
   for everything in `docs/RELEASE.md`'s blocked list, so do not collapse it.

Two findings that are easy to get wrong next time:

* **`origin/worktree-agent-a8b3e2de76a11eaa1` is an old fork, not unmerged
  work.** Merging it would delete `engine/rhythm.py`, `engine/threads.py` and
  ~4500 lines that are on `main`. Its two useful pieces — the Tauri capability
  file and the packaging scripts — were reviewed and the capability file was
  brought over by hand, with `dialog:allow-open` added, which the branch's copy
  omitted and the UI requires. The branch should be deleted or archived.
* **The UI's stage indicators must be verified against real stderr.** The
  "Audio / dead air" stage's `started` rule matched 0 of 1260 real lines because
  it tested for markers the engine never prints; the rules are now derived from
  what `engine/audio_analysis.py` actually emits. If you add a stage, grep the
  captured log at `/home/azureuser/.claude_tmp/realrun.stderr.log` (1260 lines)
  rather than assuming a marker exists.

### Prior session — ~4,200 lines of finished editorial work were sitting uncommitted in a worktree, and are now on main

The worktree audit (next-tasks item 10) found that the previous classification
of `agent-a0d0fba273d1b37e1` as "no unmerged work" was wrong in a specific way:
it counted commits ahead of `main` and never listed untracked files. The
worktree held nine untracked files — six `engine/` modules plus two tests and
a design doc — that existed nowhere on `main`.

**The work is real, and now integrated.** `engine/{lexical,rhythm,threads,
topics,redundancy,corpus}.py` build a corpus-level editorial representation:
topic spines (which subjects recur), story threads (where each is set up and
pays off), redundancy groups (which repetition to keep), and conversational
rhythm. Every derived artifact carries `start`/`end`, `confidence`, `reason`
and bounded verbatim `evidence`, so a judgement can be jumped to in the source
media and checked by a reader. `docs/corpus-model.md` documents the contract.

**Integration touched no existing file.** All five shared dependencies
(`context_guard`, `pause_classifier`, `story_units`, `vad`, `models.core`) are
identical to `main`'s except `engine/vad.py`, where `main` has since added
`_conforming_wav` — a function the layer does not use. Verified by copying the
six modules in and running the suite: 1107 → **1186 passed**, 12 skipped, no
regressions.

**Measured across the committed corpus** (11 sources, 1,771 segments,
18,751 word-timestamp entries): 314 units, 167 topics, 211 threads,
9 redundancy groups. The corpus was re-run on 2026-09-28 for the two sources
that still hold media, which moved pentagon-esper-briefing one unit and one
topic lower, so those four counts are the pre-re-run figure; the segment and
word-entry counts are current. One judgement is checkable against the text: on
`nasa360-ep5` the layer groups the show's three sign-off boilerplate passages
and reasons *"keep unit 85 … the later passage is a recap of an earlier point —
keep in a long edit, cut in a short one."*

**The doc's own overclaim was corrected rather than carried over.** It asserted
"Agents B (selection), C (long-form assembly) and D (short-form) build against
the contract documented here" — false on `main`, where nothing reads the layer.
It now states plainly that nothing consumes it, that the integration section is
a proposal, and that the table establishes the analysis *runs*, not that acting
on it *improves an edit*. `tests/test_corpus_model_measured.py` pins the table
to the corpus; all three mutations tried (a table row, the totals line, the
disclaimer) fail the gate.

**Two other worktree findings.** `agent-aca62967bb6c0054b` holds an untracked
`bench/` package whose own `RESULTS.md` states it is synthetic media and
synthetic transcript — "NOT REAL FOOTAGE. NOT REAL ASR" — so if it is ever
integrated its numbers must not be quoted as measurements; `tools/microbench.py`
on `main` measures the real pipeline instead. It remains unmerged. The timeline
worktree is confirmed fully superseded: `0328858` on `main` introduces exactly
its seven files with matching line counts. `docs/agent-worktrees.md` carries all
of this.

### This session — a second cluster of untraceable figures, and the gates that read the docs back

The VAD retry's fabricated number turned out not to be an instance. Sweeping the
repo's quoted constants turned up a second, independent cluster in the NLE
frame-delta claims, which behaved the same way: specific measured figures cited
in three documents and one tool, none of them traceable to any committed
artifact.

**What was wrong.** `docs/METRICS.md`, `docs/NLE_COMPATIBILITY.md`,
`docs/TECHNICAL_AUDIT.md` and the tolerance comment in `tools/nle_check.py` all
cited "1.63 frames" as the worst-case timeline-vs-render gap, with a per-clip
table (24.858/24.892, 17.084/17.138) and frame counts (746 rendered against a
745-frame timeline, 513 vs 512, 493 vs 493). **None of those numbers match any
committed timeline or render.** The artifacts were fine; the prose had drifted
off them.

**Re-measured over the whole committed corpus** (11 sources, 27 clips, every
timeline checked against both its renders — 54 clip/render pairs):

* The exported timeline is systematically a fraction of a frame **shorter** than
  the rendered file, and always in that direction. Worst case **0.69 frames**;
  32 of 54 pairs exact; the 22 non-exact ones fall into three narrow bands
  (0.53 / 0.63 / 0.69). The gap is ~21 ms regardless of frame rate, which is
  container overhead rather than a cut disagreement.
* EDL vs FCPXML agree to **0.00 frames on all 27 clips** — two emitters on
  separate code paths describing the same cut.
* The 2-frame tolerance is **~2.9×** the largest gap, not ~3×. That correction
  was forced by a gate that caught my own overclaim while I was writing it.

**Two provenance facts this surfaced, which the docs now state instead of
hiding.** First, **no rendered MP4 is committed** — `git ls-files "*.mp4"`
returns zero; the 54 renders are gitignored (27 clips × 2 presets; four further
MP4s in `microbench_render/` are microbenchmark output, not renders). So A4.2
(timeline vs render) is
measurable on this machine only and is *not* reproducible from a clean
checkout, while A4.3 (EDL vs FCPXML) is. The A4.2 test skips with a stated
reason rather than passing vacuously, and the doc says "verified here" rather
than "verified." Second, `tools/nle_check.py` and `tools/editorial_audit.py`
are **post-hoc**: they live in a tool, not in the pipeline, and are not run in
CI. That is now recorded in `docs/TECHNICAL_AUDIT.md`, where a reader would
look for it.

**The gates, and the one that was vacuous.** `tests/test_nle_frame_deltas.py`
recomputes both figures from the corpus and reads the cited numbers back out of
the prose. The first version of the doc gates searched the *concatenation* of
both documents, and mutation-testing showed that was worthless: both files cite
both figures, so changing one left the other still matching and the gate stayed
green. Rewritten per-document — each figure pinned to the file that states it.
**All four gates have been confirmed to fire by mutation** (27→26, 0.69→0.68 in
each file, and a live 0.69→1.63 regression), and the restored control run is
green. Recording the lesson: *a gate over a set of documents is not a gate over
any of them.*

**Also fixed:** the Telugu script-share argument in
`tests/test_vad_empty_recovery.py` said "41 of 137 letters" — 137 is the whole
string including spaces, the letter count is 75. The implied share read as ~30%
when the decode actually gives 54.7%. Conclusion unchanged; the number used to
reach it was not. Both counts are now derived from the fixture and pinned.

**Four constants were verified clean** and are worth recording as clean:
`SILENCE_SHARE_MAX` (0.30), `MIN_VAD_FRACTION` (0.10), `COLLAPSE_RATIO` (0.25),
`EMPTY_RETRY_PEAK_DB`. Each reproduces exactly from the corpus. The repo's
citation discipline is better than the VAD retry suggested — the failures are
clusters, not the norm.

### This session — the VAD retry's own justification was a fabricated number, and the retry is removed

The retry decoded a chunk a second time without the VAD gate and merged whatever
came back, justified by a docstring claiming it recovered "16 segments / 105.9 s
of speech the committed run lost" on telugu-launch. **That number was never
measured.** Re-measuring it on the retained 16 kHz audio with the production
model and decode parameters:

| decode | segments | speech | letters in Telugu script |
|---|---|---|---|
| `vad_filter=True`, forced `te` | **0** | 0.0 s | — |
| `vad_filter=False`, forced `te` | **5** | 24.88 s | 41 of 75 (**54.7%**) |
| `vad_filter=False`, auto-detect | 5 | 24.46 s | 0 — all Latin, detected as Sindhi |
| repeat of row 2, same file | **23** | 59.88 s | varies |

So the retry does not recover speech: it returns nonsense that is *majority
Telugu script*, is unstable run to run on the same input, and injecting it would
have written fabricated text into a transcript whose correct content is empty.
And **every committed chunk checkpoint in the corpus has no `note` field at
all** — they all predate the retry — so it had never fired on a real run, never
recovered anything, and never merged anything.

**The merge is gone; the measurement stays.** An empty decode over loud audio is
still distinguished from silence on disk (the part that is verifiable and
useful), and carried into the transcript's provenance as `vad_empty_chunks`
(renamed from `vad_recovered_chunks`, which never appeared in a committed
transcript). What is not done is any second decode.

Two guards were tried and rejected on measurement, and both rejections are
recorded in the code so they are not re-attempted:

- *Script consistency fails.* Requiring the requested language's script to be a
  majority of the recovered letters **passes** the measured garbage, because the
  garbage is 54.7% Telugu. It is nonsense in the *correct* script, which is what
  no local check can detect.
- *Token confidence would reject the wrong cases.* Low average probability
  correlates with hard audio, and hard audio is the only place a recovery could
  ever matter; that bar suppresses recoveries precisely where they are needed.

`roman-portrait-trailer`, the risk that motivated the guard in the first place,
is **closed**: the VAD returns 0 segments and the unguarded decode also returns
0, in 7.3 s. The retry is inert there.

What this does *not* claim: telugu-launch still has no transcript, and this
change does not give it one. It removes a path that would have filled it with
fabricated text. The source remains untranscribable at this model size —
`telugu-medium` at WER 91.54% is the measured distance to the bar.

### This session — three defects in the streaming WER prepare, one of which hung a run for hours

The MLS prepare streams archives of 50–115 GiB because they cannot fit on the
3 GB of free disk. Three defects found by running it against the german archive:

1. **Pass 2 had no early break.** It re-read the entire archive even after the
   40-clip sample and its transcript were complete. The german prepare had all
   40 clips extracted and kept streaming for hours. Fixed: the loop stops once
   the wanted members and their references are in hand, which is provably
   everything the pass produces.
2. **Pass 1's listing was never cached.** A prepare that dies in pass 2 repays
   the expensive half — a full re-read just to rebuild a list. The listing for a
   fixed URL does not change between runs, so it is now written beside the
   output and reused, with shape validation that does not depend on a log line
   that happens to enforce it.
3. **The network stream had no stall detection** — the one that actually hung
   the run. An established connection that stops sending data raises nothing:
   curl sits in `poll` forever and a 115 GiB prepare hangs with no exit code to
   retry on. Observed: two hours into pass 2, zero bytes received in a 30 s
   window, socket ESTABLISHED with an empty receive queue. `--retry` does not
   help, because a stall is not a connection failure. Fixed with
   `--speed-limit`/`--speed-time` below the lowest legitimate rate measured
   (2.9 MiB/s under throttling) plus `--retry-all-errors`.

The german prepare was restarted with all three. A fresh connection resumed at
17.2 MiB/s, confirming the stall was a dead connection rather than a throttled
server, and the stall detection now turns that case into a retry instead of an
infinite hang.

Each fix is mutation-tested: the early break (both directions — removing it, and
breaking unconditionally), the cache hit, and the fallback on a malformed cache.
One mutation revealed the shape check was being enforced *accidentally* by the
log line indexing `data[1]`; the check is now explicit and the comment says so.

### This session — criterion P9 was satisfied: Spanish is the third C10 language

**WER 8.58% / CER 2.64% on Multilingual LibriSpeech Spanish test — 40 clips,
10.08 min, 1503 reference words, 129 word errors, 38 of 40 clips inside the 25%
bar** (`corpus/wer/runs/spanish-small.json`, manifest at
`corpus/wer/mls-spanish-test/manifest.jsonl`). The 50 GiB archive was streamed
twice and never touched disk, which is the only way it fit against ~3 GB free.

P9 asks for ≤25% WER on at least 3 of the 6 C10 languages. Polish (11.01%),
Italian (20.11%), Spanish (8.58%) and German (10.66%) are all inside the bar, so
**the criterion is met with one to spare** — the first of the eight P-criteria
to close on measured non-English data.

What the claim is deliberately *not* promoted to. Four of six is the count, but
the four are the clean-read audiobook half of C10: French and Dutch stay
unmeasured, and a bar satisfied only on its easiest half is weaker than the same
bar met on six. Spanish's 8.58% is also the *best* of the four and the closest
to English's 3.38%, so the five results are a spread (3.38 / 8.58 / 10.66 /
11.01 / 20.11) whose position tracks corpus and register — Italian is Dante,
Spanish is modern prose — rather than a graded descent from English. Nothing
here says "transcription is multilingual"; it says adequate on four European
audiobook languages at this model size, and §7.1's Telugu failure at 151.08% is
still the
counterexample that bounds it.

Pinned in `tests/test_wer_bench.py`: `spanish-small` joins the parametrized list
at 0.0858, and the count test is raised from two to three, so the passing count
cannot drift up in prose without a run record per increment. `docs/EVALUATION.md`'s
P9 row, `docs/TRANSCRIPTION_EVALUATION.md` §7 (new §7.4), and the state file's
finding 9 were rewritten with the bounded claim rather than the bare one.

### This session — the audit became a sweep, and two of its own claims were wrong

`editorial_audit.py` is the benchmark the release validation phase asked for, but
until this session six of its seven checks had no test at all and the sweep it
exists to support could not finish. Four defects, all found by running the audit
across the whole corpus rather than by reading the code:

**1. The sweep stopped at the first incomplete project.** `audit()` returned
`(False, {})` when a project had no `selections.json`, and `main()` then raised
`KeyError: 'findings'`. Four of the eleven committed sources have no
`selections.json` — the VAD gate suppressed their transcripts (see the section
below) — so `editorial_audit.py corpus/<id>_run/project` traced back on
`alice-1903` and never reached the other ten. A run that produced nothing to
audit is a finding, not an exception, so it is returned as one, and the sweep now
completes: **2 of 11 projects pass every check** (`hadhazy-protest`,
`royal-society-cloke`), the other 9 report and exit nonzero, and the exit code
composes with CI as the module docstring claims. The four with no transcript now
say which stage stopped the run — `transcription returned no segments (te);
selection never ran` — because "no selections" is the symptom and sends an editor
to the wrong stage. Six checks that had no test are pinned on semantics rather
than implementation, plus a corpus sweep test asserting the audit raises on none
of the eleven projects.

**2. A finding that looked like a false positive *was* a false positive —
corrected 2026-09-22.**
`check_cuts_respect_sentences` reported emwcon clip 01 splitting `' was,'`/`' at'`
across a gap of 1.68 s. The paragraph below argued it was correct on the
grounds that both words belong to one under-punctuated transcript sentence, so
a cut between them is mid-sentence by the criterion the check exists to
enforce. That reasoning is right about the *text* and wrong about the *criterion*:
the sentence membership was derived from the candidate's `text`, which is
`" ".join(segment.text)` and merges under-punctuated segments, so the "one
sentence" the argument rests on is an artifact of the join rather than a fact
about the transcript. Two segments six seconds apart were read as one sentence
and a clean cut between them as a hole in it. The check now takes sentence
membership from the transcript's own segment spans, joined by word start time,
and fires only when a sentence *loses a word* to a boundary. All six of the
flagged cut edges were ASR under-punctuation read through that join, not audit
error and not a real mid-sentence cut — no word was lost at any of them.
Corrected in `docs/TECHNICAL_AUDIT.md`, "CI does not run the tools", which
carries the diagnosis and the mutation record; the corpus baseline test pins
the corrected verdict.

**3. The f-string prefix was real.** Chasing the suspected false positive turned
up an actual typo: the start-boundary message printed `'was'/f'at'` — a literal
`f` from a misquoted f-string field. The end-boundary message never had it, so
the first test written for this only exercised the clean line and passed while
the typo lived. Both boundaries are now covered.

**4. A docstring claim that could not be true.** `check_silence_share` said a
negative share "means word spans run outside the intervals, itself a bug". That
value is unreachable: every overlap term is clamped against its own interval, so
`spoken` cannot exceed `kept` and share is bounded in [0, 1]. Worse than dead
text — an unclamped computation is the one edit that makes the sentence true, and
on that input a negative share *passes* the 0.30 bar while looking like a
diagnostic. The sentence was pointing at a failure mode whose fix would have
introduced a wrong PASS. Corrected, and the range is pinned by a test that
mutation-testing with the unclamped sum fails. This is the same class of defect
as the silence-share naming problem: a number and a description that had not been
checked against each other.

Also: the six documents quoting "1036 tests" as ground truth were refreshed to
the measured **1080 passed / 12 skipped (145 s)** — `docs/DATA_ROOM.md`'s own
drift warning was itself stale, which is the failure mode it describes. Only
test-count references changed; `1036` is also the LibriSpeech sample's
*reference-word* length in five places, where it is a property of a committed
manifest and must not move. The drift history is extended, not rewritten.

### This session — three of eleven sources have no transcript because the VAD gate ate them

This is a defect on the RELIABLE TRANSCRIPT step, found by measuring rather
than by reading, and it changes what the corpus is.

`editorial_audit.py` could not run on four sources at all — no
`selections.json`, because nothing was ever selected. Three of the four
(`alice-1903`, `roman-portrait-trailer`, `telugu-launch`) transcribed to
**zero segments**, and each chunk was checkpointed with `status: "ok"`. A
suppressed decode and genuine silence produce byte-identical records, so the
failure hardened permanently into the transcript and the run proceeded as
though there were nothing to transcribe.

The two are separable by measuring the audio the chunk was decoded from:

| source | peak | committed segments | correct reading |
|---|---|---|---|
| `alice-1903` | −120 dB (the silence floor) | 0 | silence — the empty result is right |
| `roman-portrait-trailer` | 0.0 dB | 0 | loud, 91.5% of energy below 500 Hz — a bed, not speech |
| `telugu-launch` | −1.8 dB | 0 | loud, speech-shaped spectrum — **suppressed, and this model cannot transcribe it with or without the gate** |

`telugu-launch` is the one that is a real failure: it is loud and its
band-energy profile matches `hindi-baiga-interview`, which *did* transcribe,
yet it transcribed to nothing. Decoding it without the VAD does not fix it.

**What the unguarded decode actually returns, measured 2026-09-20** on the
173.7 s source with the small model, forced Telugu, beam 1:

| decode | segments | speech covered | letters in Telugu script |
|---|---|---|---|
| `vad_filter=True`, forced `te` | **0** | 0.0 s | — |
| `vad_filter=False`, forced `te` | **5** | 24.88 s | 41 of 75 (**54.7%**) |
| `vad_filter=False`, auto-detect | 5 | 24.46 s | 0 — all Latin, detected as Sindhi |
| repeat of the second row | **23** | 59.88 s | varies |

The earlier text here claimed "16 segments / 105.9 s of speech the committed
run lost". **That number was never measured** — it is the same class of defect
this whole audit exists to catch, a plausible figure standing in for evidence.
The real decode is 5 segments of nonsense that is *majority Telugu script*
(54.7%), the rest romanized noise plus one Chinese character, and it is not
even reproducible: 5 segments on one run, 23 on the next, of the same file.

**The fix therefore does not merge a second decode at all.** An earlier version
measured the peak, and above a −45 dB bar decoded again without the gate and
merged whatever came back. That merge is removed, on measurement rather than on
caution:

- *A script-consistency guard was tried and does not work.* Requiring the
  requested language's script to be a majority of the recovered letters passes
  the measured garbage, because the garbage is 54.7% Telugu. It is nonsense in
  the correct script, which is exactly what a local check cannot detect.
- *A confidence threshold would reject the wrong cases.* Low average token
  probability correlates with hard audio, and hard audio is the only place a
  recovery could ever matter; that bar would suppress recoveries precisely
  where they are needed.
- *Nothing measured is lost by removing the merge.* Every committed chunk
  checkpoint in the corpus carries **no `note` field at all** — all predate the
  retry — so the retry has never fired on a real run, never recovered anything,
  and never merged anything. Removing it deletes a fabrication path, not a
  recovery.

What is kept is the part that is verifiable: the peak measurement, taken before
the chunk WAV is deleted, and the `note` that separates suppression from
silence on disk. An empty transcript over loud audio now reads *"no segments
over non-silent audio (peak −1.8 dB); the VAD gate may have suppressed the
chunk, and this model could not transcribe it with or without the gate"* rather
than reading as silence, and is carried into the transcript's provenance as
`vad_empty_chunks`.

`roman-portrait-trailer`, the feared risk in the other direction, is **closed**:
`vad_filter=True` → 0 segments and `vad_filter=False` → also 0 segments, in
7.3 s. The retry is inert on it, so no music is hallucinated there.

Two ordering bugs the end-to-end run caught, both pinned by tests. The first
version measured the chunk audio *after* `wav.unlink()` had deleted it, so
every chunk hit the unreadable-file fallback, read as silent, and the retry
never fired at all. The second moved the measurement but left the retry reading
the deleted path, raising `FileNotFoundError`. **Both passed the unit tests.**
The peak is now measured and the retry completed before the file is unlinked,
and the ordering has its own test — this is the second time in this repo that
a "passed" change was dead on arrival because the test never exercised the
real path (the first was the `silencedetect` filter invoked with `-v error`,
§Outstanding work), so the lesson is to run the thing.

**The committed corpus is untouched.** This changes what a *fresh* transcription
records, not any shipped transcript. The three sources were not re-run.

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
agreement is caught only when a human audits a project directory. (The audit's
*checks* are now pinned by `tests/test_editorial_audit.py`, and a corpus sweep
test asserts it raises on none of the eleven projects, so a regression in the
audit itself is caught in CI — but a change that loosens a check silently is
still only caught by a human reading a project's output. Wiring the tools into
CI as a gate is deliberately not done: 9 of 11 committed projects fail the audit
for documented editorial reasons, so the gate would be permanently red. The
honest wiring would be a `--since` mode that audits only what a given commit
touched, which is not written.)

The five new documents are `EVALUATION.md`, `TIME_SAVINGS.md`,
`TRACTION_DASHBOARD.md`, `USE_OF_FUNDS.md` and `INVESTOR_PIPELINE.md`. Each was
checked before committing: the budget sums to exactly $1,000K; the 393.6 s
machine-time figure is a verifiable sum of components; the 18×–73× ratio is
labelled a model with an unobserved denominator and carries a falsifier
declared in advance; and outreach remains unauthorized with no investor named
anywhere. (Both the 393.6 s and the 18×–73× were later superseded — the
render rows they were built from measured the retired encoder, and the
current figures are 488.8 s and 14.7×–58.9×; see the entry below titled
"§2's render rows measured the retired encoder". The sentence above is left as written because it records what
was checked at that commit, not what is true now.)

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
names, four are now measured and all four pass — Polish at WER 11.01% with 34 of 35
clips inside the bar, Italian at WER 20.11% with 26 of 40 inside it (the latter
on Dante's archaic Italian, a harder condition), Spanish at WER 8.58% with
38 of 40 inside it, the best of the four, and German at WER 10.66% with 36 of 40
inside it. P9 asks for three, so four is the criterion met with one to spare;
French and Dutch remain unmeasured, and the four that pass are all
clean-read audiobook prose, so this is the criterion satisfied on its easiest
half, not transcription shown to be multilingual. Telugu is a language outside that set, chosen because it is the one
the pipeline demonstrably cannot handle.

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
as export defects. Separately, `tools/editorial_audit.py` was failing on **four
of the seven** (`emwcon-panel-2018`, `ooi-vertical`, `pentagon-esper-briefing`,
`royal-society-cloke`), every failure a selection bound landing inside a word —
a real defect, fixed in `engine/edit_plan.py`. **That fix took effect:** the
sweep as of 2026-09-20 reports `royal-society-cloke` passing every check, and
`hadhazy-protest` with it — 2 of the 11 committed projects are clean. ~~The
three still-failing sources fail on *editorial* findings~~ — **corrected
2026-09-22: those three editorial findings were false positives** (the
under-punctuation join described above), so the failing population is the two
silence-share findings plus the four with no transcript, and the five sources
with selections audit clean. The passage that followed named
`emwcon-panel-2018` as splitting sentences at six cut edges; none of those six
lost a word. `nasa360-ep5` and
`telugu-tutorial` each carry one clip over the 30% word-free bar (40.8% and
46.3%), and the four sources with no transcript at all are reported as
findings rather than raising. Mid-word cuts are gone from the corpus. There were
never "six pycmx checks that used to skip": `pycmx` is in
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

### This session — the story graph stopped being write-only, and one scale test had been measuring nothing

`editorial_corpus.json` is now read back. Three modules ship —
`engine/corpus_io.py` (the reader, schema `1.0` and `1.1`), `engine/story_path.py`
(the join from a corpus thread to an ordered selection of `Candidate`s) and
`engine/story_view.py` (writes `story_view.json` and `story.md`). The design is
`docs/story-memory-design.md`; the research it was built on is the new "Story
memory" section of `docs/rd-landscape.md`. **No dependency was added** — networkx
was the one unimpeachably-licensed candidate (BSD-3-Clause, zero deps) and was
still rejected, because the graph is structurally bounded and the two operations
it needs are twenty lines of indexed list code.

The premise this session corrected is the one the prior session had just pinned.
Above, this document records that `docs/corpus-model.md` "now states plainly that
nothing consumes it" and that `tests/test_corpus_model_measured.py` pinned that
claim. That pin was correct and the claim was false-by-construction once the
story-memory work landed, so both flipped: the doc now names what consumes the
layer, and the test now asserts it names them **and** still refuses to convert
wiring into a quality claim. The hedge survived — "something consumes this layer
now" is followed by "that would need blind A/B comparison against real sources,
which has not been done." Consumption is established; improved edits are not.

**The thread recount.** The corpus table in `docs/corpus-model.md` was re-run
against all 11 committed transcripts after the schema `1.1` change, and the thread
counts moved: `emwcon-panel-2018` 69→51, `nasa360-ep5` 59→45,
`royal-society-cloke` 15→11, `hadhazy-protest` 5→4 — **211 → 174 threads**. Units,
topics and redundancy groups are unchanged. Schema `1.1` keeps every forward link
that clears the evidence bar instead of letting each unit claim one best link, so
chains run their full length and fewer, longer stories result. The doc now states
this where the table prints it. The 211 figure above is left in place as the
record of the pre-`1.1` measurement.

**A defect found by benchmarking, outside the layer's own scope.**
`tests/test_corpus.py::test_three_hour_scale_completes_in_bounded_time_and_memory`
was degenerate: its fixture shared boilerplate, so `segment_story_units`
collapsed 2000 segments into **one unit** spanning 23,993 s, with 0 threads and 0
edges, and the test asserted `units <= rng_units`, which held vacuously. It
passed while measuring nothing. It now uses the shared `tests/story_fixtures.py`
generator and asserts `units == 2000`, `threads > 1`, `links > 1`. **Any earlier
wall-clock number that test reported was the cost of building one unit and should
not be quoted as a 2000-segment measurement.** The real numbers, measured for the
first time at that size: 1.878 s build, 3495 edges, 251 topics, 252 stories,
11.4 MB peak. Edges per unit measured 1.725 at every size against the structural
bound of 6, and a 180 s budget selects 22 moments at 200, 800 and 2000 units alike.

**What is not established.** Everything above is synthetic transcripts with known
ground truth — no real video was analysed for this layer, and the reconstruction
round trip is proven on fixtures, not footage. The five implemented relationship
kinds are lexical; speech that connects ideas by pronoun and prosody will be
under-linked, and `fastcoref` was the rejected remedy (MIT code, MIT weights, CPU —
but its errors would produce links with no checkable evidence, and the layer's
whole contract is that a link cites what caused it). The view is a document, not
an interface: the brief's "the user sees the story, not the graph" is satisfied by
`story.md`.

### This session — the baseline's quality complaint was one upstream format decision

The clean Termux run shipped six verticals at **~202×360 and 110–140 kbps of
video**, soft, with the speaker over-cropped and the slides cropped away. Those
are rendering symptoms, and the first instinct — reframe, re-encode, re-anything
in the render — was wrong. The source it was all built from was **640×360 H.264
at ~65 kbps**. For a video that also offers native 854×480 and 1280×720.

The arithmetic is mechanical. 360 source lines go through
`vertical_crop_width(360)` = 360×9/16 = 202.5, even-rounded to **202**. A frame
that small at CRF 20 simply emits ~120 kbps. No encoder setting was wrong and no
reframing bug was needed to produce either reported number. Fixing the render
would have produced a sharper 202×360.

**The acquisition defect, reproduced offline.** `providers/youtube.py` sent
yt-dlp `-f bv*+ba/b` — its own default. That clause does not *lose* to a better
stream; on this ladder it matches **nothing**, because yt-dlp cannot find ffmpeg
to mux the two halves (or because the player response came back degraded), and
selection falls to `/b`: the best stream carrying both video and audio, which on
YouTube is itag 18 at 640×360. Proven rather than assumed, by driving yt-dlp's
own `build_format_selector` against the ladder the user's `yt-dlp -F` reported:

| spec | resolves to | best height |
|---|---|---|
| `bv*+ba/b` (what was sent) | itag `18` alone | 360 |
| the new policy's first arm | `136+140` | 720 |

The failure is inaudible — the download succeeds and the file looks complete
until it is measured, which is why nothing reported the loss.

**The fix.** The metadata lookup the download already performs now picks an
explicit format instead of asking yt-dlp to rank implicitly: the tallest
*native* (not AI-upscaled) stream the local ffmpeg can decode, at or under 1080,
with the video and audio halves joined only when a merge is actually possible.
Arms are ordered best-first with `/` and always end in the legacy default, so
metadata this code cannot read still downloads. H.264 ranks above VP9 and AV1 at
equal resolution — phones decode it in hardware and it muxes into MP4 untouched.
YouTube's `xtags=sr` marker is read to exclude the upscaled 1080p streams, which
carry no more detail than the 720p they were made from; `SELEKYT_ALLOW_SUPER_RESOLUTION=1`
opts in. The ceiling comes from `SELEKYT_MAX_SOURCE_HEIGHT` and is a preference,
not a refusal — a 1440p-only source still downloads, at the best it has.

Two real bugs surfaced while writing the policy and are fixed and pinned: there
was no `mp4a → aac` alias, so YouTube's AAC audio (`mp4a.40.2` — the container
name, not the codec) looked undecodable and the policy silently reproduced the
exact 360p fallback it exists to prevent; and the "ffmpeg is not on PATH" note
was only emitted on the no-specs path, so a host without a muxer got a
low-resolution file with no indication that a higher one existed and was
unreachable. Both are now covered in `tests/test_youtube_format_selection.py`,
which pins the choice against format ladders modelled on the real one rather
than against the network — **41 tests**, including the two that resolve specs
through yt-dlp's own engine so a spec this module emits that yt-dlp cannot read
fails in the suite rather than silently falling through on a real run.

**What is not established, and this host cannot establish it.** The live
re-download of `https://youtu.be/fxbCHn6gE3U` has not run. Every player client
returns "Sign in to confirm you're not a bot" from this datacentre IP range and
no cookies exist on the host. Independently, this machine has **node v18.19.1**
while `yt_dlp/utils/_jsruntime.py` sets `NodeJsRuntime.MIN_SUPPORTED_VERSION =
(22, 0, 0)` — so there is no supported JavaScript runtime here either, and
yt-dlp warns "some formats may be missing". That warning is likely the
contributing cause on Termux as well, which is why `download()` now records it
in the project state as an actionable diagnosis (install deno, or node ≥ 22)
when the format list it came with has nothing usable — the state file was the
only place the cause could be read from, since the download succeeds anyway.
The user's verification step must run on the Termux device that produced the
baseline, where the ladder was fully visible:

```
.venv/bin/yt-dlp -f "$(the policy's spec)" --merge-output-format mp4 \
  -o source.mp4 https://youtu.be/fxbCHn6gE3U
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,codec_name \
  -of default=noprint_wrappers=1 source.mp4
```

A 1280×720 readout is what closes this; anything else is a new format ladder to
reason about, not a rendering defect.

### This session — the render *was* independently defective, and two of its own published numbers were wrong (`56253b5`)

The brief's condition was met without the network: the corpus file
`corpus/media/pentagon-esper-briefing.mp4` (1280×720, 29.97 fps) is exactly the
profile the vertical path targets, so the render could be measured against a
high-quality source on this host. It was, and the encoder was the defect.

Measured against a `-crf 0` reference of the *same filter chain* — the crop, the
scale and the captions all applied, so the ladder scores the encode alone — 240
frames through the tracked 406×722 crop:

    preset      crf    kbps    SSIM     PSNR    wall
    veryfast     20    837.9  0.9991    40.54    1.04 s
    medium       20    962.5  0.9994    41.92    1.95 s
    slow         20    929.6  0.9994    41.93    2.60 s
    slow         18   1211.9  0.9995    43.18    2.89 s   <- shipped
    slow         16   1576.9  0.9996    44.43    3.23 s

**This table is itself the defect the 2026-09-28 sweep closed.** Its figures match
no committed record — they came from the `56253b5` session's own bench, whose
artifacts were never written down, so nothing here could be re-derived. The same
window re-measured and committed as
`corpus/render/runs/pentagon-240f.json` reads:

    preset      crf    kbps    SSIM     PSNR    wall
    veryfast     20     646   0.9875   44.402   0.97 s
    medium       20     749   0.9897   45.661   1.94 s
    slow         20     712   0.9898   45.678   2.55 s
    slow         18     932   0.9917   46.825   2.79 s   <- shipped
    slow         16    1222   0.9933   47.992   3.08 s

Every absolute figure moved between the two, including the SSIMs by a wide margin
— the two windows are not the same footage, and absolute quality and bitrate are
window properties rather than encoder ones. **The decision the table was used to
make did not move**, and that is the point of publishing ratios rather than
absolutes: `veryfast`/crf20 is still the lowest-quality entry and still the
cheapest, and the quality gap and the bitrate and wall ratios against the shipped
pair are stable across both windows and across the third window the sweep added.

The old setting, `-preset veryfast -crf 20`, is the **lowest-quality entry on the
ladder**, 2.42 dB behind what now ships in the committed window (2.64 dB in the
uncommitted one; the range across three swept windows is 2.4–3.2 dB at 9:16).
That is a visible softness on a
phone-held Short, not a chart artefact. It is also the *cheapest* entry, so the
change is an honest quality purchase rather than a free one: +2.42 dB for +44%
bitrate (932 against 646) and +1.82 s of wall clock on 8 s of footage. That matters because the
pipeline also runs on the Termux/ARM host, where the encode stays tolerable.
`crf 16` buys one more dB for another 30% bitrate and is not worth carrying.

`-pix_fmt yuv420p` is now explicit. Without it, a 4:2:2 or 4:4:4 master would
carry its chroma subsampling into an MP4 that half the decoders in the world
refuse to open — a playback defect, not a quality one, and invisible in any
metric that reads the file with the same ffmpeg that wrote it.

**Two conclusions this work published first were wrong, and both reversed.** Both
came from a synthetic bench source in the job's scratch dir that was silently
overwritten mid-session, so the table being quoted described a file that no
longer existed. (1) veryfast/crf20 was reported as 172.6 kbps and "the worst
entry on both axes at once"; it reproduces at 16.9 kbps on the overwritten file
and 837.9 on real footage, where it is the cheapest entry, not the most
expensive. (2) Pre-upscaling the 406×722 crop to 1080×1920 was reported as
*strictly worse* (44.59 vs 49.25 dB at 3.2× bitrate); on real footage it is
*genuinely better* (45.97 vs 43.43 dB) but at 4.3× the bitrate (5219 vs 1212
kbps). The pipeline still ships the source-preserving crop — now restated as a
**cost** decision, not a quality one, since the same perceptual budget buys far
more in the encoder than in a pre-upscale the player performs for free. The
reversal is recorded in the `_encoder_args` docstring so it is not mistaken for a
settled result, and the standing instruction for any future edit there is in the
memory file: re-run the bench and update both that docstring and
`test_encoder_settings_are_the_measured_ones`, whose docstring carries the same
table. **Never trust a bench table whose source file lives in a scratch dir.**

**Phase 5 (slides) is shipped and measured on real footage, after one measured
regression was caught by the same measurement.** The reframe election was
subject-centred with no notion of a second content class, and the reason it was
documented rather than tuned was a measured fact: on a 1280-wide frame with the
speaker and slide ~560 px apart centre-to-centre, all 1280 legal crop centres
left either the speaker's face or at least half the slide's text out of frame.
That geometry is unchanged — it is a property of a 406 px window, and widening it
breaks the 9:16 contract. What changed is that the election now *answers* it.

`engine/reframe_scenes.py` adds the second content class the gap called for.
Slide regions are found by per-cell temporal staticness plus edge-or-brightness;
each sample is classified SPEAKER / SLIDE / SPLIT / TRANSITION on the (face,
slide) pair; the question "can one crop hold both" is answered in closed form by
intersecting the face window with the slide's own window, so a shot composes to
one tracking crop when they overlap and to two stacked bands when they do not.
Class changes are joined with an `xfade` cascade on segment-local clocks.

Measured on the one real corpus source that exercises it
(`corpus/media/royal-society-cloke.webm`, 1920x1080 VP9 — a tight head shot
alternating with a wide shot holding a speaker whose face is 52-66 px wide beside
a static region covering up to 98% of the frame):

- The composition **keeps the speaker on 26 of 26 samples** in every interval
  where the source has a face, against 25 of 26 for the pre-composition reframe
  on the same samples. Rendered `face_present` is 1.000 on all four intervals
  scored with the benchmark's YuNet oracle.
- The talking-head path is **byte-identical** to the pre-existing implementation
  on all five real cut intervals of the pentagon briefing, so the gain on lecture
  footage costs nothing on footage that has no slides.
- The rule that regressed was found by this measurement, not by reasoning:
  `classify_scene` returned SLIDE for any region covering 80% of the frame
  *before consulting the face*, so the wide shot planned a crop centred on the
  slide and the speaker left the deliverable on 6 of 26 samples in one interval
  and 5 of 26 in another (rendered `face_present` 0.750 and 0.765, versus the
  legacy chain's 1.000). Removing the short-circuit — classifying on the pair and
  letting the closed-form geometry decide — closed it to zero losses. Pinned in
  `tests/test_reframe_scenes_real_media.py`, which fails at 35% delivered face
  presence under the old rule and passes at 100% under the fix; the synthetic
  geometry in `tests/test_reframe_scenes.py` alone could not have caught it,
  because the rule was geometrically self-consistent and wrong only about real
  footage.
- **The one trade-off, stated rather than hidden:** in the wide shots the speaker
  renders smaller than the legacy chain would deliver (minimum rendered face
  width 33.5 px against 53.3 px on one interval), because a two-band layout uses
  a wider source window scaled to the same output width. Presence was bought with
  size, deliberately — a smaller speaker is an editorial choice, a missing one is
  a defect.

**What remains unmeasured, said plainly.** No corpus clip is a lecture whose slide
is captured alongside a *foreground* speaker at lecture scale, so the two-band
path is proven on synthetic geometry and on one interview's wide shot, not on a
screen-shared tutorial. The codec gap that the same measurement exposed is now
**closed**, and the two are worth keeping apart — one is an editorial coverage
limit, the other was a hard defect:

**The perception layer's AV1 blindness is fixed and measured.** OpenCV 5.0.0's
bundled ffmpeg negotiates a hardware AV1 pixel format the host cannot supply, so
*every* frame of an AV1 source fails with `Failed to get pixel format` while
`engine.render` decodes the same file with the same binary. Confirmed as
codec-level rather than file-level: two independent AV1 encoders (libsvtav1 and
libaom-av1) both decode 0 frames, as does the corpus's own AV1 source. The
planner then held a non-empty sample list in which *every* frame was `None`,
elected nothing, and shipped the static centred default without reporting it.

`engine.reframe._sample_frames` now falls through to an ffmpeg rawvideo path when
OpenCV yields no frame, reached through one choke point that already serves
`track_subject_center`, `_probe_dims`, `analyse_scene`, `scene_filter` and the
benchmark — so four downstream consumers were repaired by one reader. Measured,
before → after:

| source | OpenCV alone | with the fallback |
|---|---|---|
| corpus h264 briefing | 5/5, 0.79 s | 5/5, 0.79 s (path not taken) |
| corpus VP9 interview | 5/5, 0.73 s | 5/5, 0.73 s (path not taken) |
| corpus AV1 tutorial | **0/5**, 0.01 s | **5/5**, 1.29 s |

Where both paths work they agree: 0.00 mean absolute difference on VP9, at most
2.41 on an MPEG-4 Part 2 source, and the per-sample pattern is exact zeros
alternating with a small constant — two timestamps landing one frame apart, not
two decoders disagreeing about layout. So the fallback changes results only on
codecs OpenCV cannot read, which is the only case it exists for.

**The outcome it fixed, end to end.** A source the perception layer cannot read
used to ship a content-blind crop; on a generated AV1 clip whose static content
covers the right half, the centred default straddled both halves and the
delivered frame read mean brightness 113 against a panel of 210. With the
fallback the composition centres on the content region and delivers 209 — the
crop moved because the planner saw the frames. Pinned by
`tests/test_reframe_decode_fallback.py`, which generates its AV1 at run time
rather than depending on corpus media, so it ships with the repo.

**The same defect also had a second, worse shape.** A source *neither* reader can
decode used to reach the renderer as `crop=1:0:x='0':y=0,scale=2:4`, which ffmpeg
rejects while configuring the crop pad — exit 234, a zero-byte output file, and
nothing in the message naming the source as the problem. That points an editor at
the render instead of the ingest, which is the worst place for a diagnostic to
land. `engine.reframe_scenes.UnreadableSource` now raises at the geometry
decision instead, keeping the two failures apart: "no subject detected" is a
composition question answered with a centred crop, "no frame decoded" is an
ingest question composition cannot answer. The landscape path is deliberately
*not* made to raise — it has a documented 1080p default for unreadable geometry,
which the pre-fix `max(w, 1)` clamping had made dead code (measured:
`_probe_dims` returned 1×1 and the deliverable rendered 2×2). Both are now what
their comments say.

**What remains a limit, rather than a defect:** the gap sits at OpenCV's build of
ffmpeg, so a host with a differently built OpenCV may not exhibit it, and the
control test in the new module is written to fail loudly if that day comes, so
the second decode path does not outlive its reason.

**Phase 7's "non-monotonic DTS" alarm was closed as a measurement artefact**, not
a defect. `ffprobe -show_entries packet=dts_time,pts_time` prints the columns in
`AVPacket`'s internal field order — pts then dts — regardless of the order
requested, so a correct B-frame decode-order sequence reads back as scrambled.
Re-measured via `-show_packets`: DTS is monotonic, and audio/video head and tail
drift are 0.000 s across 1/2/3/5 concatenated parts. Nothing was suppressed; the
trap is documented on the test helper so it is not rediscovered, and the real
invariant is pinned instead.

**The reframing width is still deliberately untouched**, and the reason is now
sharper than when the last session left it. `vertical_scale_filter` never
enlarges, so a 720p source ships a **406×720** vertical and a 1080p source a
**608×1080** one. That is a deliberate source-preserving choice, and the
encoder re-measurement above is the argument for it: the bits that a pre-upscale
would consume buy 2.5 dB more actual detail when spent on the encode instead.
Whether a 406×720 deliverable reads as sharp on a phone is a question the ladder
cannot answer — it measures fidelity to the source, not perceived sharpness at
viewing size — and it stays open until a real 720p acquisition is available to
judge against.

### This session — §2's render rows measured the retired encoder, and the CPU column of the instrument itself was broken

The previous two entries made the encoder measurement reproducible. This one
found that the *cost model built on it* had not been rebuilt: `docs/TIME_SAVINGS.md`
§2's two render rows still carried **0.00862 / 0.00625**, measured against the
`veryfast`/crf20 encoder that `56253b5` retired. Every downstream figure in that
document — the 393.6 s total, the 0.2678 RTF, the 18×–73× speed ratio, the 5.5%
render share, the $0.0195 cloud figure — traced to those two rows and was wrong
in the same direction and by the same ~6×. The error was a silent understatement
of the pipeline's machine time at the exact moment the document is most quoted.

**Re-deriving the table's own convention before touching a number.** The Wall
column is not the measured wall of a benchmark window: it is the window's wall
scaled to the job's **99.254 s of delivered output per preset**, and the RTF column
is that scaled wall ÷ the source's 1469.77 s. An earlier pass of this work computed
the new rows by dividing a raw 60 s window wall by the source length and got
0.03131 / 0.01408 — wrong, because it mixed a 60 s window with a convention that
assumes the job's own output length, and wrong by 1.8×. Caught by re-reading the
table rather than by measurement, and no incorrect figure was written.

**What the rows are now.** 0.05552 / 0.02409, i.e. **81.60 s / 35.40 s**, from
`corpus/render/runs/pentagon-30s-ass.json` — the captioned shipped pair, **0.8221 /
0.3567**, on the corpus's real 1280×720 source with a real ASS from the run burned
in. New total **488.8 s (8.1 min)**, RTF **0.3325**, ratio **14.7×–58.9×**, and
render's share of the budget moves from **5.5% to 24%** — the second-largest stage
after transcription, where before it was a rounding error. Propagated to
`EVALUATION.md` §5 (0.2678→0.3325, the 20-source corpus cost 107→133 min, and the
`medium` / `large-v3` rows recomputed), `TRACTION_DASHBOARD.md`,
`RED_TEAM_FINDINGS.md` and this document.

**One thing this correction cannot do, and the document now says so.** The
reference source used for §2 — the NASA 360 SD tape the whole cost model is built
on — has been **deleted from this host** to free disk. §2b's claim that the job was
still re-runnable was true when written and is not now. So the two render rows are
**MEASURED, DIFFERENT SOURCE** and the total is labelled an upper bound: the
pentagon source is 2.19× the pixels of this job's 864×486 16:9 output. The pixel
ratio is known and the arithmetic to scale by it exists, and it was deliberately
**not** done — render time is not linear in pixel count, and inventing that scaling
law is exactly what `tools/microbench.py`'s own docstring refuses to do. §2c states
the direction of the bias instead. It remains true that the source is
byte-retrievable from archive.org, so the row is re-measurable in principle, just
not on this host.

**The instrument's CPU column was broken, independent of the numbers.**
`tools/microbench.py:_bench` measured CPU time with `RUSAGE_SELF` alone, which
excludes waited-for children — and rendering *is* ffmpeg. On a 45.9 s 16:9 render it
reported **0.4 ms of CPU**, a thousandth of the actual work, because every
instruction executed was a child and none of it counted as self. Fixed to sum
`RUSAGE_SELF` + `RUSAGE_CHILDREN` in an extracted `_cpu_time()`, which is what
"CPU time of a stage that is a subprocess" has to mean. Corrected readings on the
60 s window: **3.5 cores (16:9) / 3.4 cores (9:16)** on a 4-core host. The
wall/CPU ratio is what distinguishes "the pipeline is slow" from "the host is
contended", and that column had been reporting nothing at all.

**`tools/render_bench.py` was deriving its speed range from unpaired bounds.** The
`rtf` field of a sweep record is a `[min, max]` over windows, not a per-window list,
so dividing one entry's fastest window by another entry's slowest states a speedup
no single window showed. Added a per-window `rtf_ratio_vs_shipped` to
`quality_vs_cost`, pairing within a window; the `engine/render.py:_encoder_args`
docstring now publishes the per-window range. `_write_record` still refuses to
overwrite a tag, so neither new record was hand-edited.

**The `medium`/crf18 question the previous entry posed is answered.** Two new
committed records (`corpus/render/runs/pentagon-240f-medium18.json` and its 16:9
counterpart) sweep it against the shipped pair on the same three windows per
orientation. It is **30–55% faster** (9:16 34–55%, 16:9 30–40%), and it is not
free: within **0.03–0.15 dB** of the shipped quality in every window, but at
**2–5% *higher* bitrate**. `medium` is strictly dominated on the
rate-distortion curve at this crf — what it sells is wall clock, not bytes. The
shipped pair stays on `slow`/crf18, documented as a product judgement past the
edge of what PSNR can settle, with a per-run speed toggle as the honest exposure
rather than a silent swap.

**That range was wrong as first written here, and the correction is a correction
of the *direction*, not just the digits.** The records' paired RTF ratios are
0.646–0.767, and a speedup is `1/ratio − 1`, not `1 − ratio`: `1 − 0.646` is
35% and `1/0.646 − 1` is 55%. The understated figure made the option the
paragraph declines look *less* attractive than it is, which is the wrong
direction for an error to take in a paragraph whose conclusion is "we declined
it." The same entry's `medium`/crf20 parenthetical — "three quarters of the wall
clock" — was also resting on a paired-vs-shipped field measured against
`slow`/crf18 rather than `slow`/crf20, which is the *unpaired-bounds* failure
one paragraph down in a different costume. Both are now derived inside the test
from `per_window` rows and cross-checked against the stored
`rtf_ratio_vs_shipped`, and `_encoder_args`' docstring states the formula
explicitly so the trap has a sign on it.

**What pins it.** `tests/test_render.py::test_the_medium_crf18_answer_is_pinned_to_its_records`
asserts the shipped pair and window list are what the docstring cites, that the
PSNR gap is within ±0.15 dB, that the bitrate ratio is ≥ 1.0 on both legs, and
re-derives the speed claim from `per_window`. `tests/test_render_bench.py` gains a
test that the paired figure is what the aggregator emits, with daylight margins
against the unpaired derivation. No existing assertion was weakened.

**The finding that outlives the numbers.** Render was 5.5% of the machine budget
and is 24%. Any future claim that this pipeline is "transcription-bound" is now an
assumption awaiting re-measurement rather than a property of the architecture — and
the two things that moved it, an encoder change and a source deletion, are both
invisible to a reader who trusts the table without reading its basis column.

**The structural fix, not just the three arithmetic ones.** Those three errors —
the retired encoder, the unpaired bounds, `1 - ratio` — had one shape: a figure
printed in prose that no machine recomputed from the record it named. Each was
found by a human re-reading a record a test could have read. `docs/TIME_SAVINGS.md`
§2 is the most-quoted table in the repository and the test suite did not open it
once; that is now closed by `tests/test_cost_model.py`, which finds the two render
rows by label, and recomputes the wall column and the RTF column from
`corpus/render/runs/pentagon-30s-ass.json` under the table's own stated scaling
(99.254 s of delivered output per preset, 1469.77 s of source). It also asserts
the record is `kind: ship` with the shipped encoder, because a record of a retired
preset is how the first error entered; and it recomputes the 24% render share that
the whole entry is about. The two scaling constants are held in the test as named
values rather than re-derived, because re-deriving them would mean re-running a
job whose source has been deleted to free disk — what is checked is everything
*downstream* of them, which is where all three errors actually lived.

**The new tests were mutation-verified before being trusted.** Each assertion was
checked by corrupting the real doc or record, confirming the failure, and
restoring — including record-side mutations, since a test that reads a file it
never breaks on is a test that checks nothing. One trap worth recording: reassigning
the module's `DOC` constant from a harness and re-running pytest does *not* move
the pointer, because pytest's assertion-rewriting import machinery gives the
module a fresh namespace; only mutating the file on disk produced a genuine
failure. Mutation-testing against the live file is therefore the only reliable
form here, and it is what was used.

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
4. **Desktop shell has now rendered — under Linux xvfb, still never on
   Windows.** The "never rendered a frame" line that stood here is now false,
   and the reason it changed is the more useful fact: running the window found
   a bug that the entire RPC contract had missed. The Captions tab reported "No
   captions were written for this clip" for a file that existed on disk, and
   every `read_text` call on the wire was answering correctly. The cause was
   path *precedence*, not a missing file: `selekyt/pipeline.py` wrote
   `report.json` paths as `str(project / sub)` without resolving `project`, so
   a CLI run from the repo root recorded repo-root-relative paths; the sidecar's
   cwd is the app's own directory, so those paths pointed at nothing; and the
   UI was preferring that recorded string over `state.project`, the absolute
   directory the Rust shell had resolved itself. Fixed on both sides — the
   engine resolves before recording, and `listArtifacts` / `renderStory` /
   `renderCaptions` build subpaths from `state.project`. See
   [[report-paths-are-cwd-relative]] in the agent memory and
   `docs/RELEASE.md` "What worked".
   What was actually verified: `Xvfb :99` at 1440×2400, `xdotool` clicks at
   tab coordinates derived from row-darkness profiling plus `tesseract --psm 7
   tsv` bounding boxes, and OCR-confirmed real content in all six review tabs
   (15 SRT cues with text and in/out times, the transcript's opening line, the
   QA row, 5 landscape + 2 vertical render entries). What was not: **a Windows
   installation.** The build pipeline has produced installers on
   `windows-latest` since (22m27s for `de0c151`), but built is not installed —
   webkit2gtk is not WebView2, and no Windows desktop has run the installer, its
   Start Menu entry or its uninstall path. The `sign.ps1` hook has run 154 times
   on that same runner, and every one of them was a decline, so the *signing*
   path itself remains **UNVERIFIED**. **The MSIX half of this item has since
   closed**: the `msix` job has gone green four times in a row and the artifact
   was verified off the runner — but the pack being green is still not the app
   being installed, and this item stays open until a Windows machine actually
   runs it. Revised honest summary: the app's *logic* is
   tested and its *Linux rendering* is now observed; its *Windows installation*
   is not, and running the window once is exactly what proves that gap is worth
   closing.
5. **Optional backends untested here** — sherpa-onnx and ollama are wired and
   degrade cleanly by design, but unit-tested only. **Silero no longer belongs
   on this list**: it is implemented, vendored, validated bit-exactly against
   faster-whisper's own driver, and quality-measured against transcripts on
   three corpus sources — see the session section above.
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
7. **The no-speech diagnosis: the silent-track branch is now validated on real
   media, and the damaged-container branch is still not.** The pipeline measures
   `peak_dbfs` on the extraction, *remuxes and retries* when the audio has real
   level but no transcript, and the message names which of the two causes
   applied (`_no_speech_diagnosis`), with `tests/test_container_recovery.py`
   covering both branches on synthetic files.

   **The silent-track half moved to real media this session.**
   `tests/test_no_speech_on_real_media.py` (opt-in, gated on
   `corpus/media/royal-society-cloke.webm` being present, skipped with a stated
   reason otherwise) takes the real recording, muxes its video against a genuine
   digital-silence track, and runs it through the production extraction and
   decoder. Peak measures `-inf`, the transcript is empty, and the diagnosis
   names a missing audio track rather than blaming the container. It asserts a
   control first — the unmodified excerpt must carry speech — so a media file
   that cannot be decoded cannot make the test pass vacuously. Mutation-tested:
   changing the silent-track message fails it.

   **The damaged-container half did not, and the reason is recorded rather than
   left as "still untested."** Three corruption strategies were tried against the
   same real excerpt — zeroing 1 MiB of the muxed body, truncating to 45% of the
   file, corrupting the region after the EBML head. None reproduced the trigger
   condition (zero transcript with real peak): they yielded 10, 7 and 12
   segments against the control's 12, with **byte-identical peak** (−0.525 dBFS
   to 15 digits). WebM/Opus decodes around mid-body damage. The historical case
   that motivated the recovery path was a structurally damaged `.ogv` — a
   different and more fragile muxer — and reproducing it needs either that file
   or a targeted corruption this host has not found. So the claim "a remux
   recovers a damaged container" remains validated on synthetic files only, and
   must keep being stated that way.

   **What has never happened on either branch: a real run that exercised the
   recovery path end to end.** No committed transcript carries a
   `container_recovery` marker, and the three real `NO_SPEECH_DETECTED` rows in
   `corpus/results.jsonl` predate the fix — their `log_tail` shows the old
   message that did not distinguish anything. Their media is gone, so they are
   unclassifiable now, which is why `tools/corpus_run.py` records
   `no_speech_cause` (`silent_track` / `damaged_container` / `unknown`) as a
   structured field from this point on. The claim still standing is the narrow
   one: **the diagnosis distinguishes a silent track from a damaged container,
   and the silent-track half of that is now shown on real media while the
   damaged-container half is not.**

   **One half of the *reporting* gap is closed, and the observational gap is
   not.** Until now a run that recovered wrote the marker to `transcript.json`
   under a key nothing read: the editor was shipped clips cut from a repair and
   told none of it. `report.json` now carries `container_recovery` on every run
   (null meaning "the transcript came from the file you handed in"), the desktop
   summary renders a "source repaired" warning chip on a non-null value, and a
   resumed run reads the marker back from `transcript.json` because the resume
   path never enters the recovery branch. `tests/test_container_recovery.py`
   now drives the recovery end to end on a real remux — the original decode
   yields nothing, the stream-copy remux transcribes, the run renders from the
   repair, and the report a resumed run writes still names it. That closes the
   *surfacing* defect. It does not close item 7 above: the remux in that test is
   a healthy file, so the test proves a recovered run reports itself, not that a
   remux of a *damaged* container recovers. No committed transcript carries the
   marker, and nothing here changes that.
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
   P9's six C10 languages, so this is not a measurement of the criterion's own
   corpus — three of those (Polish, Italian, Spanish) are now measured and pass,
   which is what satisfies P9; this result is the counterexample that bounds them;
   what this does establish is that the failure is a decode failure
   at this model size and not a transcription-pipeline failure, because the
   English path on the same harness scores 3.38%. The `medium` run on the same
   Telugu corpus has finished, and it bounds the question in the worse direction:
   **WER 91.54% / CER 69.24%, with 1 of 50** utterances inside the 25% bar
   (`corpus/wer/runs/telugu-medium.json`). Capacity is a real lever — 151.08% to
   91.54% — and it does not reach the bar, so this is a *language-fit* gap rather
   than a size gap. Buying compute does not close it, which is what keeps the
   CPU-only cost story coherent on both sides of the language boundary.
9. **P9's own criterion is two-sixths measured, and both sixths pass.** Polish —
   one of the six C10 languages the criterion names — scores **WER 11.01% /
   CER 2.45%** on the same `small` model, with **34 of 35** clips inside the 25%
   bar and diacritics intact (Multilingual LibriSpeech test, 35 clips, 7.79 min,
   1054 reference words; `corpus/wer/runs/polish-small.json`). Italian — a second
   one of the six — scores **WER 20.11% / CER 4.53%** with **26 of 40** clips
   inside the bar (MLS test, 40 clips, 9.80 min, 1258 reference words;
   `corpus/wer/runs/italian-small.json`). The Italian corpus is Dante, so the
   reference is archaic Italian against a modern ASR and the worst clips reach
   46% — a materially harder condition than Polish's modern prose, and still a
   pass at the corpus level. **Spanish — a third one of the six — scores WER
   8.58% / CER 2.64% with 38 of 40 clips inside the bar** (MLS test, 40 clips,
   10.08 min, 1503 reference words; `corpus/wer/runs/spanish-small.json`), the
   best of the four and the closest to English's 3.38%. Two independent
   European languages passing is what
   shows the first was not one easy audiobook. Telugu is *not* a C10 language, so
   these read against it as a set: the failure is localised to Indic, not to
   "everything outside English". **The criterion needs 3 of 6, so with Polish,
   Italian, Spanish and German all inside the bar, P9 is met with one to spare.** The claim is
   deliberately bounded in both directions: French and Dutch remain
   unmeasured, and all four passing measurements are clean-read audiobook prose
   — the easiest condition the bar will be faced with — so this is "P9 holds on
   the three measured C10 languages", not "transcription is multilingual". A bar
   met only on its easiest half is weaker than the same bar met on six, and
   §7.1's Telugu failure at 151.08% remains the counterexample that bounds every
   one of these numbers.
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
12. **Frame-rate and codec coverage — closed, 2026-09-28.** 23.976, 50, 59.94 and
    a real 3840×2160 H.265 transcode were built from corpus speech footage and
    run through the full pipeline, and all four pass `tools/nle_check.py` with
    canonical un-reduced `frameDuration` rationals and frame-grid agreement
    within 0.81 frame. The measurement also found and fixed two defects in the
    auditor that made it impossible to take (see the gap map's EXPORT row), and
    it produced the 4K cost number §9 was missing: **36.5 s of perception for
    27 s of clip, 9.0× the 720p cost, 79% of it frame decode** — 4K ingest works
    and is correct but does not perceive in realtime on this CPU. What remains
    open is not coverage but *viewing*: no render was judged by a person, and
    the render-RTF half of the cost row is still an extrapolation.
13. **The slide layout is solved and shipped, and the codec gap it exposed is
    closed.** The speaker-left / slide-right layout is no longer an unsolved
    geometry problem — the election now has a second content class and answers
    the geometry in closed form (see the Phase 5 paragraph above), measured at
    26-of-26 speaker retention on the real source that exercises it, with the
    regression the same measurement caught pinned to a test that fails at 35%
    under the old rule. What that work surfaced was a narrower and more
    actionable defect, and it is now fixed rather than open: the whole reframe
    perception read frames through OpenCV, which decoded **0 of 5 frames** of the
    corpus's AV1 source (`telugu-tutorial.webm`) while the ffmpeg render path
    decoded it fine, so on that codec the planner was blind —
    `track_subject_center`, `analyse_scene` and `scene_filter` all saw no frames,
    the benchmark could not score the source, and the pipeline silently fell back
    to a default crop with nothing reporting it. `_sample_frames` now falls
    through to an ffmpeg rawvideo path, measured 0/5 → 5/5 on the AV1 source
    while h264 and VP9 are untouched on the path they already used; a generated
    AV1 source whose content is off-centre now composes onto its content region
    (delivered brightness 113 → 209 against a panel of 210) instead of onto the
    static default. The same fix removed a second, worse failure shape: a source
    *neither* reader decodes used to ship `crop=1:0:...`, which ffmpeg rejects at
    configure time (exit 234, zero bytes written), so it now raises
    `engine.reframe_scenes.UnreadableSource` at the geometry decision instead.
    Both are pinned by `tests/test_reframe_decode_fallback.py`, which builds its
    AV1 at run time rather than depending on corpus media. **The residual is a
    coverage limit, not a defect:** the gap sits at OpenCV's build of ffmpeg, so
    a differently built OpenCV may not exhibit it, and 4K/H.265 (item 12) is
    still unattempted.

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
(40.8% and 46.3%). Neither is an emitter bug, but the two are not the same
finding and must not be bundled as "dead air":

- **nasa360-ep5 clip 05 is a measured music bed, not silence.** The 6.26 s
  window at 101.56→107.82 s measures mean −31.4 dB, 0.00 s of silence at
  `-40dB:d=0.25`, and spectral flatness 0.0066–0.0123 — loud and tonal
  throughout (`docs/METRICS.md` A2.4, `ffmpeg volumedetect` / `silencedetect`
  / per-second FFT). A standalone re-transcription of that window with the
  same model returns only "Ever.", so the transcript dropped nothing.
  `retention_intervals` is **right** to keep the clip intact here, and a
  version that split on the gap would have cut music out of the shipped cut —
  the guard's 0.72 wall-clock retention floor exists for exactly this case,
  and this was checked before changing it. The defect is the metric's name,
  not the cut.
- **telugu-tutorial clip 01 is unmeasurable from this repository.** Its source
  media is not retained, so the 46.3% has no acoustic verification either way,
  and the source is one of the two whose transcript is phonetic noise — so a
  word-coverage deficit there is consistent with a transcription failure rather
  than with either silence or music. It is recorded as unresolved, not as dead
  air.

Only `royal-society-cloke` and `hadhazy-protest` pass everything.

Separately, **five of the seven** still carry relative EDL asset paths
(`emwcon-panel-2018`, `hadhazy-protest`, `nasa360-ep5`, `ooi-vertical`,
`telugu-tutorial` — 25 edges in total, each resolving only from the repository
root). `royal-society-cloke` was already absolute, and the pentagon re-run
carried out to confirm the text fix also re-emitted its timelines through the
fixed emitter, so all **ten** of its edges are now absolute URIs and
`tools/nle_check.py` reports `fcpxml media relinkable` PASS on all five clips
where it previously skipped. The remaining five cannot be repaired this way:
`tools/regenerate_timelines.py` refuses to run without the source media, and
only pentagon and royal-society retain theirs. The sentence-split and
EDL-path items are artifact debt — the files on disk were written by emitters
that have since been fixed — but the dead-air figures are properties of the
selections themselves and would survive a re-run.

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
   is several× `small` on a 4-core CPU-only host. **This task gained a consumer
   this session:** the language-fitness gate's refusal on Telugu names `medium`
   and its measured 91.54% as the routing suggestion, so a `medium` re-run is
   no longer measurement for its own sake — it is the fix the tool already
   recommends, and a `large-v3` run that clears the bar would turn a refusal
   into a working path. Note the direction of what is being tested: `medium` on
   Telugu is 91.54%, still above the 25% bar, so the honest expectation is
   *better but not usable*, and the run's value is in saying so with a number
   rather than in clearing the bar.
3. **Fix the no-speech misdiagnosis** (Outstanding work item 7): remux to a
   clean container and retry before reporting "no audible spoken content", so
   an unreadable container stops being described as a speechless source.
   **DONE — `945ba98` (Gap E).** The diagnosis now separates a genuinely silent
   extraction from one with real level and no transcript, and only the latter
   reaches for the container. Pinned by `tests/test_container_recovery.py`,
   whose docstring records the corpus source it was found on (95.5% speech
   energy rejected as speechless; a clean remux of the same file transcribed to
   11 segments).
4. **Score WER against `corpus/manifest.json`'s C10 non-English audio-only
   corpora** (`docs/EVALUATION.md` §2.2) — the ground truth the video sources
   lack. **DONE, and the criterion is met with two to spare: 5 of the 6 C10
   languages are measured and all five pass the ≤25% bar** on the same `small`
   model that scores 3.38% English — Polish 11.01%, Italian 20.11%, Spanish
   8.58%, German 10.66%, French 13.59% (`docs/EVALUATION.md` P9). The failure
   that survived is bounded and named: Telugu is not one of the six and scores
   WER 151.08% on `small`, localising the gap to Indic. Dutch is registered
   and not run, and all five measured languages are clean-read audiobook
   prose, so the claim stays "P9 holds on the five measured languages", not
   "transcription is multilingual". The English in-domain half (video with
   official captions) is still never measured.
5. **Get a *second* YouTube fetch observed** to establish whether the
   intermittent block is per-video, per-hour, or random. One success is not
   reliability. Use `--cookies` / a browser cookie file, a residential proxy,
   or a non-datacenter network if the block recurs.
6. **Test the `.mpg` path end to end.** The accept-any-container fix (defect 2)
   is pinned at the discovery step only; a full run through a real MPEG-PS
   source would exercise transcription, render and export on that wrapper.
7. **Add a 23.976, a 50 and a 59.94 source, and one 4K/H.265 source.** The
   corpus covered 25/30/29.97; the rest is untested and is the largest
   unquantified risk named in `docs/BUSINESS_MODEL.md` §9. **DONE — 2026-09-28,
   `cb65bb8`.** All four built as real transcodes of corpus speech footage and
   run through the production pipeline; the export grid is correct at every
   rate (canonical `frameDuration` rationals, 0.81-frame interval agreement),
   and the 4K cost the model had no number for is now measured — 135% of
   realtime, 79% of it decode. See the gap map's EXPORT row and
   `docs/BUSINESS_MODEL.md` A13. The task also found and fixed two defects in
   the auditor itself that made the measurement unrunnable.
8. **Resolve the web-shell / CLI input disagreement** (G3). Decide between
   exposing a video upload route or documenting the project-root confinement —
   the previous session decided URL-first with no code change, which is why
   upload was missing. **DONE — the route is built, but the disagreement is only
   narrowed, not closed.** `/api/upload` streams a multipart body to disk
   instead of buffering it, so the web shell now accepts a master the same way
   the CLI does, and the six tests named in the suite note above pin it. What
   remains is scale, not shape: the 32 GiB cap has never been approached and the
   route has only ever been exercised with a 3 s file (see
   `docs/product-gap-map.md`), so a genuine 1–3 hour master through this route
   is still **UNVERIFIED**, and the intake still cannot accept a file outside
   the project tree, which the CLI allows.
9. **Complete one real Windows install.** This was previously ranked low
   ("package the Tauri shell once, or record that v1 ships CLI + web shell
   only"), and the founder's directive overrides that ranking: the Windows
   desktop app *is* the current line of work. Progress, as of the green MSIX:
   **(a) DONE** — `build-windows.yml` completes on `windows-latest` and has now
   gone green four times in a row on four different commits (`35555583046`,
   `35594437455`, `35594441067`, `35604822603`), the last inspected job-by-job
   with 0 steps failed. **(c) DONE** — the MSIX stage is written, packed with
   validation ON by the x64 MakeAppx, and its artifact verified off the runner
   (9552 entries, all manifest-referenced assets and the executable present,
   identity matching `tauri.conf.json` on all four fields). **(b) and (d)
   remain open and are now the entire gap:**
   - **(b) install and run.** Install the unsigned artifact on a clean Windows
     11 machine, run a real local video through the real pipeline in the
     webview, and inspect real artifacts. **This is blocked on hardware, not on
     code** — this host is Linux with no Windows machine, no PowerShell and no
     SDK, so it cannot be done here. Until it happens, no claim about the
     *running* application is supportable; the contract tests cover the logic,
     not the app, and the artifact name says `-unverified` for exactly that
     reason.
   - **(d) signing.** Set the `SELEKYT_AZURE_*` secrets and confirm
     `scripts/sign.ps1` actually signs and verifies — currently **UNVERIFIED**,
     never executed. Note the trust problem is not solved by signing alone:
     an Azure Trusted Signing cert makes the package install without developer
     mode, but SmartScreen reputation still has to be earned, and this is a
     release/distribution problem to state plainly, not a bypass to document.
   - **New, found while verifying the green artifact, and FIXED:** the whisper
     `small` weights were staged into the payload **three times byte-for-byte**
     — 1383 MiB of a 2057.5 MiB package was one 461 MiB file, and compressed the
     three copies were 1275 MiB of the 1525.2 MiB package. `stage_model()` now
     collapses the cache to one real file per snapshot entry
     (`apps/desktop/collapse_hub_cache.py`, run on every invocation and outside
     the reuse guard). The mechanism is verified against the installed
     `huggingface_hub` on this host rather than assumed — see
     `tests/test_collapse_hub_cache.py` — including that a blob matching a
     shipped file's size but not its content fails the build, which the first
     draft of the step missed. What remains is measurement, not correctness: the
     predicted ~1135 MiB payload needs a Windows run that stages through the new
     step, and `huggingface_hub`'s cache layout is what it imports from.
10. **Audit the preserved worktrees for now-merged or superseded work** and
    record the verdict in `docs/agent-worktrees.md`. **DONE** — and it paid off.
    The timeline worktree is confirmed fully superseded (`0328858` on `main`
    introduces exactly its seven files with matching line counts). But two
    worktrees the audit had filed as "no unmerged work" held untracked files
    that existed nowhere on `main`, because the check counted commits and never
    listed untracked files. One of them was ~4,200 lines of finished editorial
    work, now integrated (see the Completed work section). The other
    (`agent-aca62967bb6c0054b`, the synthetic `bench/` package) remains
    unmerged, and its own `RESULTS.md` says plainly that its numbers are not
    real measurements. The worktrees are all preserved under the standing
    instruction; the verdicts are in `docs/agent-worktrees.md`.

## Important decisions

* **URL-first web UX — superseded by a streamed upload route.** The web shell
  already accepted a URL in the same field and dispatched it to the same sidecar
  path as the CLI, so a separate video-upload route was first judged redundant
  work, which is why the G3 gap was left open deliberately rather than
  overlooked. That judgement was reversed once it was tested against a real
  intake: a URL is not a substitute for a master a user already has on disk, and
  requiring the file to be inside the project tree meant the web shell could not
  do the one thing the CLI could. `/api/upload` now streams the multipart body
  to disk rather than buffering it, so the 32 GiB cap costs memory only on the
  kernel side. The route is built and pinned; only its scale behaviour is
  unverified (see task 8 above).
* **The 9:16 fix keeps the frame anamorphic rather than emitting square
  pixels.** Scaling the crop width by SAR gives a correct display ratio with
  zero resampling. The alternative (`setsar=1`) gets the ratio right only by
  lying about the pixel shape and would stretch content ~12.5 % horizontally.
  If a platform is later found to ignore container SAR, revisit this.
* **The speech-free guard is placed outside the transcription branch** so a
  *cached* empty transcript is also caught, not just a fresh one. That guard
  catches an empty transcript reaching selection; it does not ask *why* the
  transcript is empty, and the VAD finding above is the answer for three of the
  eleven sources — an empty result over loud audio is not the same condition as
  an empty result over silence, and conflating them is how suppressed speech
  gets recorded as a successful `status: "ok"`.
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
* **Disk is the binding constraint on real runs.** 2.0 G free as of
  2026-09-20, and one real
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
  and confirming the count in "Current repository state" above (**1251 passed /
  13 skipped**, 187.7 s, 2026-09-21). A number other than that means something
  has drifted. The count was refreshed across the doc
  set after RED_TEAM, AI_EXECUTION_STATE and an agent appendix disagreed within
  one day (824, 819, 834); it has moved 819 → 824 → 834 → 837 → 890 → 895 → 898
  → 1031 → 1034 → 1036 → 1080 → 1215 → 1236 → 1240 → 1251 as regression tests landed, and the skip count moved
  10 → 7 → 12 → 13 → 12 → 13 as gated real-media tests, backend-gated checks and
  WER run records arrived and departed. **A quoted count is
  stale the moment it is written** — this session quoted 1234, then 1236, then
  1240, each correct at the time of writing and each wrong by the next commit,
  and the four labelling tests were shipped with a commit message quoting the
  count from before they existed. — the "How to run things" note above carries
  the same caveat, and the note there explains why the count moved twice during
  this session alone. The same applies to commit counts — the repo is at
  **271
  commits, 159 of them machine-authored under the "Ubuntu" identity**
  (`git log --format='%an' | grep -ci ubuntu`).
  This line is itself a self-checkpoint and it has to be edited whenever the
  count changes — a previous session let it sit at 819 through fifteen new
  tests, and the drift was found only because a red-team agent audited it.
  The same applied to the two numbers in this bullet: it said "194 commits, 85
  machine-authored" and the real figures are **271 commits, 159 machine-authored**
  (`git rev-list --count HEAD`, `git log --format='%an' | grep -ci ubuntu`).
  That is a 77-commit and 74-machine-authored drift, in a bullet whose entire
  purpose is to be re-measured — measure both numbers, do not carry them.
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
