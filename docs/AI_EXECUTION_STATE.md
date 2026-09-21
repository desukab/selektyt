# AI execution state — SelekyT

Checkpoint for a following Atria session. Factual as of the commit named below;
every line below was true on this host at that point.

## Current repository state

* Repository: `/home/azureuser/selektyt`, on `main`. Working tree state
  **changes within a session by design** — this checkpoint is written while the
  MSIX scaffolding and the workflow fix are staged for commit, so expect two
  commits above `011ec03`; verify with `git status` rather than trusting "clean"
  here.
  Up to date with `origin/main` (`https://github.com/desukab/selektyt.git`).
  Nothing stashed, nothing in `git stash list`.
  The two untracked leftovers noted by the previous checkpoint are resolved:
  `tools/caption_ocr.py` was already tracked and clean, and the WER run record
  `corpus/wer/runs/nasa-c4-small.json` was committed at `59e5327` alongside its
  tracked siblings.
* **The Windows desktop application is the current line of work.** See
  `docs/RELEASE.md` for the acceptance position, which keeps three claims
  strictly separate: the engine path is proven on a real file through the
  shipped sidecar binary; the Windows build pipeline now *produces* installers
  on `windows-latest` in ~22 minutes; and **no installer has ever been
  installed or run on a Windows desktop.** Built is not run, and a green
  `build-windows` badge is not a Windows validation.
  The last substantive commits are `61ce51f` (the per-step env fix) and
  `74d148d` (the MSIX scaffolding). Earlier relevant: `011ec03` (the labelling
  fix that broke parsing at the job level — see "Completed work"),
  `968e9c1` (the destructive-command test), `59e5327` (cancellation,
  contract-tested UI, honest signing), `b5078e5` (the Tauri 2 shell).
  Run `git log -1` for the current hash rather than trusting a hash printed
  here, since amending this file changes its own commit hash.
* Test suite: **1246 passed, 13 skipped** (2026-09-21, 184.1 s) — measured by
  re-running the whole suite and reading its summary line, not by trusting a
  background task's exit code (see the trap note in "Completed work"). The
  prior baseline was 1240/13; the growth is entirely
  `tests/test_desktop_packaging.py`, which went 9 → 15 this session: five MSIX
  contract tests and the two that pin the workflow's context-availability and
  artifact-name rules. No regressions, no engine changes.
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
* Disk: **2.0 G free of 29 G, 94% used** (`df -h /`, 2026-09-20). This file
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

None of this has run on a Windows machine. The host has no MakeAppx, no Windows
SDK and no PowerShell, so the manifest has never been packed and the script has
never run. What the host *can* reach is pinned: identity, version, executable
name and every referenced asset agree with `tauri.conf.json` and the icon
generator, and every PNG is round-trip decoded and compared pixel-for-pixel
against the buffer that produced it. **The first green `msix` job is the
verification step, not a release**, and the artifact is named
`selekyt-windows-msix-unsigned-unverified` so it cannot be mistaken for one.

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

* **MSIX is impossible in Tauri 2.** `BundleType` in the vendored
  `tauri-utils-2.9.3/src/config.rs` is exactly `{Deb, Rpm, AppImage, Msi, Nsis,
  App, Dmg}` — read from the crate source, not from docs that imply more. So the
  Microsoft Store path needs a packaging stage outside Tauri (MakeAppx +
  `AppxManifest.xml`). **That stage is UNVERIFIED and does not exist yet.**
  `scripts/sign.ps1` is also **UNVERIFIED** — there is no PowerShell on this
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
18,748 word-timestamp entries): 314 units, 167 topics, 211 threads,
9 redundancy groups. One judgement is checkable against the text: on
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
returns zero; all 59 renders are gitignored. So A4.2 (timeline vs render) is
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

**2. A finding that looked like a false positive was correct.**
`check_cuts_respect_sentences` reported emwcon clip 01 splitting `' was,'`/`' at'`
across a gap of 1.68 s — 0.18 s past one word's end, 1.50 s before the next
began. Both words belong to one under-punctuated transcript sentence ("…we had
earlier was, at first, we were handing out wikis…"), so a cut between them is
mid-sentence by the criterion the check exists to enforce. The gap is large, not
the words' relationship, and `check_cuts_land_between_words` correctly stays
silent on the same boundary because it is inside neither word. Pinned as a test
rather than tuned away. Clip 05 is the same shape ("once everybody comes" [1.66 s
pause] "I wanted to add"), so all six flagged cut edges are ASR under-punctuation,
not audit error.

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
`hadhazy-protest` with it — 2 of the 11 committed projects are clean. The three
still-failing sources fail on *editorial* findings, not on mid-word cuts:
`emwcon-panel-2018` splits sentences at six cut edges (all six are the same ASR
under-punctuation shape — "…we had earlier was, at first, we were handing out
wikis…" written as one sentence, so a cut in the 1.68 s pause inside it is
mid-sentence by the transcript's own account), `nasa360-ep5` and
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
   on that same runner, and every one of them was a decline; the *signing* path
   and any MSIX/Appx stage remain **UNVERIFIED** (no PowerShell here, Tauri 2
   cannot emit MSIX at all). Revised honest summary: the app's *logic* is
   tested and its *Linux rendering* is now observed; its *Windows installation*
   is not, and running the window once is exactly what proves that gap is worth
   closing.
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
9. **Complete one real Windows install.** This was previously ranked low
   ("package the Tauri shell once, or record that v1 ships CLI + web shell
   only"), and the founder's directive overrides that ranking: the Windows
   desktop app *is* the current line of work. The packaging is built and the
   pipeline is honest about its trust state, so the remaining work is the part
   that cannot be done on this Linux host, in this order: (a) let
   `build-windows.yml` complete on a `windows-latest` runner to produce a real
   NSIS + MSI pair, (b) install the unsigned artifact on a clean Windows 11
   machine, run a real local video through the real pipeline in the webview, and
   inspect real artifacts, (c) write the MSIX/Appx packaging stage Tauri cannot
   emit, (d) set the `SELEKYT_AZURE_*` secrets and confirm `scripts/sign.ps1`
   actually signs and verifies — currently **UNVERIFIED**, never executed. Until
   (b) happens, no claim about the running application is supportable; the
   contract tests cover the logic, not the app.
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
  and confirming the count in "Current repository state" above (**1240 passed /
  13 skipped**, 183.9 s, 2026-09-20). A number other than that means something
  has drifted. The count was refreshed across the doc
  set after RED_TEAM, AI_EXECUTION_STATE and an agent appendix disagreed within
  one day (824, 819, 834); it has moved 819 → 824 → 834 → 837 → 890 → 895 → 898
  → 1031 → 1034 → 1036 → 1080 → 1215 → 1236 → 1240 as regression tests landed, and the skip count moved
  10 → 7 → 12 → 13 → 12 → 13 as gated real-media tests, backend-gated checks and
  WER run records arrived and departed. **A quoted count is
  stale the moment it is written** — this session quoted 1234, then 1236, then
  1240, each correct at the time of writing and each wrong by the next commit,
  and the four labelling tests were shipped with a commit message quoting the
  count from before they existed. — the "How to run things" note above carries
  the same caveat, and the note there explains why the count moved twice during
  this session alone. The same applies to commit counts — the repo is at **194
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
