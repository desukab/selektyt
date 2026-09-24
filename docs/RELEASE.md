# RELEASE — SelekyT desktop application for Windows 11

**Acceptance status: the engine path is proven on a real file, and the Windows
packaging pipeline now produces a green MSIX. The installer itself has never
been executed or installed on a Windows machine.** The three claims are
separate and this report does not merge them: built is not packed, packed is
not installed, installed is not run. One further limit dates every claim on
this page: CI has not been able to start a job since 2026-09-22 17:29Z because
the account's spending limit was reached, so the green-MSIX claim is last
verifiable at the run named in the CI section and nothing after it has a CI
verdict. That is outside the repo, and it does not make the claim wrong — it
makes it untestable.

---

## Current commit

```
f4f90e2  docs(msix): the manifest is now schema-validated by a real MakeAppx run
55e6a06  docs: correct the third-run narrative and reflow the fourth
d428c78  fix(msix): pick the 64-bit makeappx, which the naive sort does not   ← first GREEN msix run (35555583046)
9ae48c8  docs(ci): repair the fused comment block on the build step's env
baf46a8  fix(msix): the payload and the executable were two forms of one directory
2d8be2d  fix(msix): anchor the manifest at the script's own directory, not its parent
4cf9c28  docs: the msix job ran once and failed 47 seconds in — record what it found
de8be7e  fix(msix): the installer arrives six directories deep, not flat in bundle/
ba7d8e5  fix(msix): the pack script was checking five of six referenced images
2941666  docs: the labelling chain is verified on a real runner, not just in tests
df0fa01  fix(windows): the sign dispatch input now fails the run instead of doing nothing
742bad8  ci(windows): run the MSIX pack as its own job, and document the parse-time trap
61ce51f  fix(windows): declare the build log path on each step, not on the job
74d148d  desktop: MSIX scaffolding for the Store path — manifest, assets, pack script
30ace62  ci: pyyaml is a test dependency — the packaging tests read the workflows
```

This file's own commit sits above `30ace62`, so the hash marked ← is already one
behind. Run `git log --oneline -8` rather than copying from here.

All pushed to `main` at `https://github.com/desukab/selektyt`. Run `git log -1`
rather than trusting the hash printed here — this file's own commit changes it.

## Architecture

A thin Tauri 2 webview over the existing engine. There is no second engine.

```
webview (ui/index.html + ui/app.mjs, importing ui/lib.mjs)
   │ invoke() — seven commands
   ▼
src-tauri/src/main.rs
   selekyt_rpc(target, op, params)  →  JSON-RPC over stdio
   selekyt_cancel(target)          →  kills the child; the engine has no abort
   selekyt_projects() / selekyt_delete_project() / selekyt_health() / selekyt_out_dir() / reveal_path()
   ├── selekyt-mech   (rust/ mechanical core)
   └── selekyt-python (bundled CPython 3.12 running `-m selekyt --server`)
        = the CLI's own intelligence layer, pip-installed from this repo
            → ffmpeg/ffprobe on PATH
```

One engine, one wire protocol (`{"op","params"}` → `{"ok","op","data"|"error"}`,
one JSON object per line), pinned from both directions by
`tests/test_sidecar_contract.py` and `tests/test_server.py`. The webview has no
network permission at all; `src-tauri/capabilities/main.json` is the entire ACL.

## Files changed

| Path | What |
| --- | --- |
| `apps/desktop/src-tauri/src/main.rs` | The shell: commands, sidecar lifecycle, unconditional stderr drain, process-boundary cancellation, project listing and deletion |
| `apps/desktop/src-tauri/tauri.conf.json` | NSIS + MSI targets, `externalBin`, resource map, `signCommand` hook |
| `apps/desktop/src-tauri/scripts/sign.ps1` | Conditional signing: signs when the Azure secrets exist, declines and exits 0 when they do not (**only the decline path has ever run** — 154 times, on the Windows runner) |
| `apps/desktop/src-tauri/capabilities/main.json` | The ACL: fs on user dirs, file picker, no network |
| `apps/desktop/src-tauri/{Cargo.toml,build.rs,Cargo.lock}` | Deps; codegen that tolerates an unstaged dev tree |
| `apps/desktop/ui/{index.html,app.mjs,style.css,lib.mjs}` | The first-user-flow UI, plus the pure helpers the contract tests import |
| `apps/desktop/stage-windows.sh` | Stages sidecars, interpreter, ffmpeg, weights; pings before building |
| `.github/workflows/build-windows.yml` | The Windows x64 build pipeline; signed/unsigned labelling derived from the signing hook's own output and fail-closed |
| `.github/workflows/ci.yml` | Adds the Linux `shell` job and the UI syntax/grammar checks |
| `tests/ui_contract.mjs`, `tests/test_desktop_packaging.py` | The UI's contract with engine artifacts; the signing hook's contract with the build |
| `providers/whisper.py` | Intra-chunk thread cap (measured: 2 threads is the saturation point on 4 cores) |
| `selekyt/server.py` | `probe_media`/`read_artifact`/`stat_artifact`/`read_text`, UTF-8 stdio |
| `selekyt/pipeline.py` | Resolves the project directory before recording it in `report.json`, so the paths it quotes are absolute for every reader |
| `engine/nle_export.py` | Windows file-URI fix (the offline-import bug) |
| `providers/youtube.py`, `selekyt/cli.py`, `tools/*.py` | Windows portability fixes |
| 12 files under `selekyt/` `engine/` `providers/` | UTF-8 encoding sweep |
| `apps/desktop/package-msix.ps1` | The Store / sideload pack stage: installs the built NSIS silently into an empty dir and packs *that*, so the MSIX payload provably matches the installer's (**now run green — run `35555583046`**) |
| `apps/desktop/src-tauri/msix/AppxManifest.xml` | Full-trust desktop-bridge manifest, 10.0.17763, EntryPoint form (**packed and accepted with validation ON**) |
| `apps/desktop/src-tauri/msix/Assets/*.png` | The six manifest-referenced images, generated and pixel round-trip verified |
| `apps/desktop/src-tauri/icons/make_icons.py` | Gains the `MSIX_ASSETS` set and a non-square render path for wide tiles and the splash screen |
| `docs/packaging.md`, `docs/release-validation.md` | What shipped, what did not |

## Build command

From `apps/desktop`, after staging and `npm install`:

```
bash apps/desktop/stage-windows.sh
npm install
npx tauri build --bundles nsis,msi
```

GitHub Actions runs exactly this on `windows-latest` on every push to `main`
(`.github/workflows/build-windows.yml`) as the `build` job, and uploads the NSIS
`.exe` and MSI as `selektyt-windows-x64-signed` or
`selektyt-windows-x64-unsigned` depending on whether `scripts/sign.ps1`
actually signed the binaries — the name is derived from the run, not asserted,
so the artifact can never claim a trust state the build did not achieve. Today,
with no `SELEKYT_AZURE_*` secrets configured, every artifact is `-unsigned` and
the job posts a warning saying so.

A second job, `msix`, packs the Store / sideload package from the installer the
`build` job produced and uploads `selekyt-windows-msix-unsigned-unverified`. It
is separate and `needs: build`, so that when the MSIX tooling was still unproven
a failure there could not take the installers down with it — that separation
was load-bearing: it failed four times before it went green, and the installers
shipped on every one of those runs. It has been green on every run since, on
commits this report did and did not author. The current streak is a property of
the run history, not of this paragraph — check it with
`gh run list --workflow build-windows.yml --limit 10` rather than trusting a
count quoted here, because every count in this document set has been stale
within a commit of being written.
See item 9 of the blocked list.

> **Checked 2026-09-23: CI is not running at all, and the streak above cannot
> be re-verified past 2026-09-22 15:40Z.** Every run since — both workflows,
> Windows and Linux alike — fails in three to six seconds with the annotation
> "The job was not started because recent account payments have failed or your
> spending limit needs to be increased." That is a billing state outside the
> repo, not a failure of this pipeline or of any commit; the last green Windows
> build is `35748995999` at "parse every exported EDL with an independent
> reader" (`build` 23 m, `msix` 4 m, both success), and seven commits after it
> have no CI verdict of any kind. The claim "green on every run since" was true
> when written and is now untestable rather than wrong: run the `gh` command
> above, and read the *annotation*, not the conclusion — a `failure` there is a
> spending limit, and a `success` is the pipeline. Verification of anything in
> this document against a later commit than `35748995999` is local-only until
> the spending limit is lifted.

## Windows artifact produced

**By the workflow:** NSIS setup + MSI, both carrying the Tauri shell, the
`selekyt-mech` Rust sidecar, a bundled python-build-standalone 3.12 with the
SelekyT package installed into it, LGPL ffmpeg/ffprobe, and the staged
faster-whisper `small` weights — so a first run transcribes with no network.

**Physically in hand on this host: none.** The development host is Linux x64
with no Windows SDK and no mingw target. The Linux shell binary was built and
exercised instead (`/dev/shm/selekyt-target/debug/debug/SelekyT`, ~197 MB), and
the desktop sidecar binary it spawns is the one the installer ships. **No
installer file survives on this host to measure** — the local copy under
`/dev/shm/win-artifact/` was deleted when `/dev/shm` filled to 100% during a
test run, so every size below is an estimate from the staged payloads, not a
measurement. Re-derive the real figures from the Actions artifact before
quoting a number anywhere user-facing.

Approximate installer payload, by component: ffmpeg+ffprobe ≈ 210 MB, whisper
`small` ≈ 484 MB, interpreter+deps ≈ 250 MB → near 1 GB before compression.
Under NSIS's 2 GB ceiling, but LZMA over 1 GB is slow.

## Installation and run instructions (Windows 11)

1. Download the `selektyt-windows-x64-unsigned` artifact from the Actions run,
   or the NSIS setup it contains. The `-signed` variant does not exist yet
   — no Azure Trusted Signing credentials are configured.
2. Run the installer. Nothing else is required: the interpreter, ffmpeg and the
   whisper weights are bundled. There is no cloud signup, no Azure dependency
   and no login.
3. Launch **SelekyT**.
4. **Select Video** → choose a local `.mp4`/`.mov`/`.mkv`. The window shows the
   real duration, frame rate, coded resolution and whether an audio track
   exists (from `probe_media`).
5. **Analyze with SelekyT** → the processing screen shows real engine progress
   relayed from the sidecar's stderr as `engine-log` events: transcription,
   story detection, editorial analysis, audio/dead-air, output generation.
6. **Results** → transcript, segments, selections, and every artifact the run
   wrote, each with its real size.
7. **Open Output Folder** → reveals the project directory.

Failures are surfaced, not swallowed: an `ok: false` envelope stops the run and
shows the error text, and a sidecar that dies is reported rather than the UI
claiming success.

## Tests

- **1251 passed, 13 skipped** (full Python suite, 187.7 s on this host, re-measured
  2026-09-21), up from a 1215 / 13 baseline. The growth is the desktop packaging
  contract (20 tests in `tests/test_desktop_packaging.py` alone, including the
  four that pin the release label and `test_the_pack_script_picks_the_64bit_makeappx`,
  which the green MSIX run vindicated), the `read_text` op's envelope and
  traversal surface, and engine-side growth; nothing regressed. The count
  was measured by re-running the whole suite and reading its summary line — a
  background run earlier in the session reported exit 0 without having executed
  pytest at all (see `docs/AI_EXECUTION_STATE.md`), and the count is the one
  *after* the labelling tests landed, not the one quoted in their own
  commit message. Re-measure before quoting this; it moves with every test added.
- **18 Rust unit tests** in `apps/desktop/src-tauri`, including
  `a_chatty_stderr_does_not_deadlock_the_rpc`, which pins the pipe-buffer
  deadlock fix that this session found by hitting it for real,
  `cancel_unblocks_a_call_in_flight`, which pins the cancellation fix,
  `spawn_resolves_the_env_override_without_re_appending_the_triple`, which pins
  the `SELEKYT_PYTHON` override lookup: a bare name must resolve against the
  staged sidecar directory, and a path that already ends in the triple must not
  get it appended a second time, and six tests for `selekyt_delete_project`
  covering each escape route the validation guards — a parent traversal, a
  symlink out of the root, a nested subdirectory, a missing path and a file.
  Those last six were missing entirely until this session: delete is the one
  destructive command the frontend can invoke and it was untested. The
  validation was extracted into `project_dir_under_root` so it could be tested
  without setting a process-wide `HOME`, which `cargo test` would race on.
  Both mutations were verified against them: removing the parent check fails
  three, skipping canonicalization fails two (the symlink case is what proves
  canonicalization is load-bearing, not decoration).
  All run in well under a second — the cancellation test uses a shell builtin
  busy loop rather than `sleep`, because SIGKILLing a shell leaves a forked
  `sleep` holding the write end of the pipe, and the test then blocks until the
  sleep expires. That is a property of the test double, not of the engine:
  every ffmpeg call in `providers/ffmpeg.py` pipes its own stdout, so the real
  sidecar has no orphaned child.
- `cargo clippy --all-targets -- -D warnings` is clean, and CI denies
  warnings on Linux before the Windows job pays for a build.
  **That Linux job has not always been able to say so.** It was added in
  `59e5327` and was red on its first three runs, because
  `tauri_build::build()` runs from `build.rs` on every `cargo check` and
  hard-errors when the paths `tauri.conf.json` declares are not on disk —
  and those paths are produced by `stage-windows.sh`, which downloads an
  interpreter, ffmpeg and ~500 MB of weights. The job's whole purpose was to
  be cheap, so it now sets
  `TAURI_CONFIG='{"bundle":{"externalBin":null,"resources":null}}'` to patch
  those two keys out for that job only. The values are `null` rather than
  empty because tauri-build merges as an RFC 7386 patch and an empty object
  *recurses*, changing nothing — that was the first attempt, and it still
  failed on `resources/python`. Verified locally by hiding the staged tree
  and re-running check, clippy and the 12 unit tests against the patched
  config. What the job gives up is still covered by the Windows job, which
  stages for real and then bundles against the unpatched config.
- `tests/ui_contract.mjs` passes in both modes: against a real engine project
  tree (captions, selections, edit-plan arithmetic, transcript overlap,
  report/qa shape) and in its project-independent CI mode (formatters, the SRT
  grammar, the path helpers).
- Skips are pre-existing and each is labelled with its reason by `-rs`: 7 are
  the opt-in `SELEKYT_PROXY_MEDIA` measurements, 5 are committed WER records
  that predate the `wer_err_total` field, 1 is a language with no run record.
  They are not "environment skips" and must not be described as one group.

## Was a real video processed?

**Yes — through the shipped sidecar binary, not a `python -m` invocation.**

Input: `corpus/media/pentagon-esper-briefing.mp4`, a real 1013.9 s, 1280×720,
29.97 fps local file with an audio track — the first-user-flow input shape.

`run_pipeline` returned `ok: true` in 61.3 s and produced **32 artifacts**:

- 8 renders, all h264 + aac, verified with ffprobe: 4 landscape 1280×720
  (`facebook/0{1..4}.mp4`) and 4 vertical 406×720 (`shorts/0{1..4}.mp4`)
- 8 EDL + 8 FCPXML timelines, XML-parsed as valid
- 12 caption files (`.ass`, `.json`, `.srt` per selection)
- `transcript.json` — language `en`, 283 segments; `selections.json` — 4
  selections from **3211 candidates**
- `qa.json` — **`ok: true`, 0 errors, 0 warnings**: audio present on every
  render, no silent tails, 0% black frames
- editorial, pacing, audio-truth, VAD-region and pause-analysis reports

The run was repeated after fixing the stderr-pipe deadlock; the first attempt
hung at 55 minutes and the second completed in 61 seconds on the same input.

## What worked

- The full chain on a real local file through the staged sidecar binary:
  probe → pipeline → 32 artifacts → QA clean.
- The deadlock fix. Found by running the real path, not by inspection: an
  undrained sidecar stderr lets ffmpeg fill a 64 KiB pipe buffer and block in
  `anon_pipe_write` before its reply is written, deadlocking the RPC reader.
  The same bug in the Rust shell would hang the app identically; it is now
  drained unconditionally in `Sidecar::spawn_in` and pinned by a test.
- `probe_media` reporting **coded** dimensions (1280×720) instead of the pixel
  aspect ratio, which is 1:1 on most sources and showed every file as "1x1".
- The ffmpeg `subtitles` colon-escaping for a Windows drive letter, proven with
  a real `:`-in-path render: unescaped fails with `Invalid argument`, escaped
  renders a valid mp4.
- The Windows build pipeline is live: the push to `main` triggered
  `build-windows` on `windows-latest`, which completes in ~22 minutes.
- **Two CI defects that the run badges were hiding.** Both were found by
  reading the runs rather than the workflow files, and both had been silently
  wrong while this report described them as working:
  the Linux `shell` job added in `59e5327` was red on its first three runs
  (`tauri_build` hard-errors on paths only `stage-windows.sh` produces), and
  the Windows job was naming **every** unsigned build `...-signed` because its
  labelling step grepped `$GITHUB_STEP_LOG` — its own step log, not the build
  step's, which is where the marker is. The second is the more serious: the
  artifact name is the only trust claim a downloaded file carries before it is
  run. Fixed on both, with the labelling step now fail-closed and four new
  tests, one of which executes the real labelling script in all three
  reachable states and was verified to fail against the pre-fix workflow.
- **The GUI rendered.** Not on Windows — on this headless host, under
  `Xvfb :99` at 1440×2400, driven with `xdotool` and read with `tesseract`.
  The window opened, the project card loaded, and all six review tabs
  (Story, Timeline, Captions, Transcript, Quality, Artifacts) rendered real
  content from the project the engine had actually written: 15 SRT cues with
  cue text and in/out times, the transcript's first line, the QA row, and the
  artifact listing for 5 landscape and 2 vertical renders. Before this the
  shell had only ever been exercised through its RPC surface.
- **The captions bug, which was invisible from the RPC surface.** The Captions
  tab said "No captions were written for this clip" for a file that existed.
  Two defects, each sufficient on its own:
  `selekyt/pipeline.py` recorded `report.json` paths as
  `str(project / sub)` *without resolving* `project`, so a CLI run from the
  repo root wrote repo-root-relative paths; and separately the UI preferred
  that recorded string over the directory the Rust shell had already
  resolved, which is absolute. The sidecar's working directory is the app's
  own, so the relative path pointed somewhere that did not exist. Both are
  fixed — the engine resolves before recording, and the four UI sites that
  need a project subpath now build it from `state.project`. The lesson is in
  `docs/AI_EXECUTION_STATE.md`: **the RPC contract was green the whole time,
  because the failure was in which path the UI chose to ask about.**
- The full suite after every change (counts below), no regressions; the Rust
  unit tests in the shell, clippy clean at `-D warnings`; the UI's pure logic
  passes `tests/ui_contract.mjs` against a real engine project tree.

## What remains blocked specifically on Windows

**None of the following has run on a Windows machine.** Each is a claim about
code that could not be executed here.

1. **The installed launcher finds the interpreter.** A python-build-standalone
   interpreter resolves `Lib/` relative to its own executable, but Tauri drops
   `externalBin` at the install root while `resources/python` maps to a
   subdirectory. The staged `selekyt-python-*.exe` is a std-only Rust launcher
   forwarding argv to `python/python.exe`; its fallback lookup is exercised on
   Linux, the installed layout is not.
2. **Resource layout at runtime.** The launcher and `sitecustomize.py` assume
   `python/`, `bin/`, `models/`, `share/` sit beside the main exe. Matches what
   `prepend_bin_to_path` assumes; never exercised against a real install.
3. **Model discovery offline.** `WhisperModel("small")` resolves from staged HF
   cache; `HF_HUB_OFFLINE` is deliberately unset, so offline first runs rely on
   huggingface_hub's connection-error → local-cache fallback. Test with the
   network unplugged.
4. **`Systran/faster-whisper-small` ships no `preprocessor_config.json`.** A
   real transcription has not confirmed faster-whisper's fallback to its
   built-in 16 kHz defaults.
5. **Installer size and install UX.** Measured: the MSIX payload is
   2057.5 MiB uncompressed / 1525.2 MiB in the package, across 9550 files. NSIS
   LZMA over that is slow — the silent install alone takes 69 seconds on the
   runner — and the artifact is 1.5 GB to download. The single biggest lever is
   not ffmpeg or the interpreter: the whisper `small` weights were staged
   **three times byte-for-byte** (1383 MiB of the 2057.5 MiB total; compressed,
   1275 MiB of the 1525.2 MiB package), a `stage-windows.sh` layout defect
   rather than a packaging one. **Fixed** — `stage_model()` collapses the cache
   to one real file per snapshot entry, verified by asking the installed
   `huggingface_hub` to resolve a collapsed cache offline on this host (see
   `tests/test_collapse_hub_cache.py`). The size win is predicted, not yet
   measured: no Windows run has staged through the new step, so a real
   `build-windows.yml` run should be watched for the payload to fall to
   roughly 1135 MiB uncompressed.
6. **The GUI has now rendered — but on Linux under xvfb, never on Windows.**
   This is the item that moved this session, and it moved in a way that
   strengthens the case for the rest of the list: running the window is what
   surfaced the captions bug under "What worked", which no amount of RPC-level
   testing had found, because the RPC answered every request correctly. What
   was verified: the binary launches a window under `Xvfb :99`, the project
   list populates, clicking a card loads the run, and all six review tabs
   render real engine output read by OCR. What was *not* verified: **nothing
   about Windows.** webkit2gtk is not WebView2, `xdotool` clicks are not a
   pointer on Windows 11, and the installer has never been *installed* — the
   distinction matters, because the pipeline does now produce one: `build-windows`
   completes on `windows-latest` in ~22 minutes and uploads NSIS + MSI
   artifacts. Being built by a runner is not being run by a user. No Windows
   desktop has ever seen the installer's UI, its Start Menu entry, its uninstall
   behaviour, or its sidecar resolution from the installed layout (items 1 and
   2). Every item above and below this one still applies in full.
7. **WebView2 availability.** The config downloads the bootstrapper silently;
   untested on a machine without Edge.
8. **The signing hook has now executed — and it exposed a labelling bug that
   made this very report's claim false.** `sign.ps1` ran on the Windows
   runner, once per bundle target, and declined to sign 154 times in the
   build log (`SELEKYT_UNSIGNED`), exactly as designed. The workflow's
   labelling step then named that artifact **`selekyt-windows-x64-signed`**,
   because it grepped `$GITHUB_STEP_LOG` — which is the *labelling step's
   own* log, not the build step's, where the marker lives. So every unsigned
   build was being published as signed, with a green checkmark on the run,
   while this report asserted "the artifact name says" the build is unsigned.
   Both were wrong at the same time, and neither was wrong in a way a test
   could see, because no test covered the workflow at all.
   Fixed, in two rounds — and the second round is the one worth recording,
   because it is a class of failure no local check can see.

   *Round 1:* the build step tees its output to a file the labelling step reads,
   and the step now fails closed — an absent marker with no configured
   credentials cannot be labelled signed, it fails the run. That fix placed the
   log path on the *job's* `env:`, on the reasonable assumption that job env is
   the "visible to every step" place. It is visible there. It is also illegal.

   *Round 2:* the `runner` context is unavailable in a job's `env:` — it is
   legal in a *step's* `env:` and not at the job level — so GitHub rejected the
   entire workflow file at parse time, before any job ran:

   ```
   Invalid Argument - failed to parse workflow:
   (Line: 53, Col: 26): Unrecognized named-value: 'runner'.
   Located at position 1 within expression: runner.temp          (HTTP 422)
   ```

   Run `35545641450` therefore reported failure with **zero jobs executed**, no
   logs to download, and `This workflow run cannot be retried` from the rerun
   API. Every local check stayed green: the YAML parses, it lints clean, and the
   byte-identical file comes back from GitHub's contents API. None of those
   checks know which contexts are legal where. `workflow_dispatch` on the ref is
   the only offline probe that surfaces it. The path is now declared on each of
   the two steps that need it, and `tests/test_desktop_packaging.py` pins the
   rule — job-level env may not use the `runner` context — after the suite had
   passed on a file GitHub rejects. The test was verified to discriminate by
   re-injecting the job-level declaration, which fails exactly one test with a
   message naming the parse-time rejection.
   **The stale artifact from that mislabelled run is still downloadable**
   (attempted deletion was denied as irreversible; see
   `docs/AI_EXECUTION_STATE.md`). It is 1.22 GiB, named `signed`, and it is
   **unsigned**. Do not trust the name of an artifact from run
   `35538322486`; trust the one the fixed workflow produces.
   What is still true: the hook has only ever *declined*. No Azure Trusted
   Signing credentials exist, so **every build the pipeline can currently
   produce is unsigned**, and Windows SmartScreen will warn on all of them.
   That is expected and is not being hidden or worked around.
**Verified since the last revision of this list — run `35547280561`,
commit `61ce51f`.** The labelling step executed on the Windows runner and did
what it was written to do. Its own log line reads:

```
marker hits: 152; credentials configured: false
##[warning]this build is UNSIGNED; Windows SmartScreen will warn
```

and the artifact left the run as `selekyt-windows-x64-unsigned`. That closes
the loop on item 8: the name the artifact carries is now derived from evidence
in the same run, and it is the first installer this pipeline has produced whose
name agrees with its trust state. Nothing else on this list changed — the
build succeeded, but built is still not installed.

9. **MSIX / Microsoft Store packaging now exists — the pack is green, the
   install is not.** Tauri
   2's `BundleType` has no `Appx` variant (verified in the vendored
   `tauri-utils` config source, not assumed from docs), so the Store path needs
   its own MakeAppx + `AppxManifest.xml` stage. That stage is written:
   `apps/desktop/package-msix.ps1` installs the NSIS installer the `build` job
   produced, silently, into an empty directory and packs *that* — so the MSIX
   payload provably matches what the installer installs, rather than a
   hand-assembled guess at Tauri's install layout. `src-tauri/msix/AppxManifest.xml`
   is a full-trust desktop-bridge manifest on 10.0.17763 using the EntryPoint
   form (the equivalent `uap10` RuntimeBehavior form only exists from
   10.0.19041, and declaring both is an error when they contradict). The six
   manifest assets come from `apps/desktop/src-tauri/icons/make_icons.py`, and a
   new `msix` job runs the whole thing on every push.

   **It has run once, and it failed 47 seconds in — which is the job doing its
   job.** Run `35548174430` (commit `742bad8`) reached the pack step, and
   `actions/download-artifact` handed it the installer six directories deep
   (`bundle/apps/desktop/src-tauri/target/release/bundle/nsis/*.exe`) where the
   script looked for a flat `bundle/`. Nothing on this host could have predicted
   that: nothing here knows what the download action does with a nested `path:`.
   Fixed (`merge-multiple: true` plus a recursive lookup), so the next run gets
   past it.

   **It ran again, and failed one line later — run `35549741215` (commit
   `de8be7e`).** This time the installer arrived flat, the 578 MB silent install
   completed in 69 seconds, and the script threw on the manifest lookup with a
   path one directory short of the real file:
   `D:\a\selektyt\selektyt\apps\src-tauri\msix\AppxManifest.xml`. The script
   lives at `apps/desktop/` and the manifest at
   `apps/desktop/src-tauri/msix/`, so `Split-Path -Parent` of the script's own
   directory is `apps/`, which contains no `src-tauri` at all. `$here` itself
   resolved correctly — `$MyInvocation.MyCommand.Path` is fine under GitHub's
   dot-sourcing — so the anchor, not the resolution, was wrong. Fixed
   (`2d8be2d`), and pinned by a test that resolves the script's path arithmetic
   against the real tree and fails if the manifest is not where it lands — a
   test that reproduces the runner's own error message when run against the
   pre-fix script.

   **It ran a third time, and the manifest was no longer the problem — run
   `35551339487` (commit `2d8be2d`).** The installer installed, `SelekyT.exe`
   was found, and the reconciliation *disagreed with the committed manifest*,
   rewriting it to `Executable="ad\SelekyT.exe"`. `ad` is the last two
   letters of "payload": `$env:TEMP` on the runner is the 8.3 short form
   (`C:\Users\RUNNER~1\...`) while `Get-ChildItem` reports the canonical
   one, so slicing the discovered path by the *string's* length cut three
   characters early and landed inside the payload directory's own name. Both
   strings name the same directory; only the arithmetic was wrong. The
   committed manifest was never incorrect, and the warning that said it was
   was itself the bug. Fixed (`baf46a8`) by resolving the directory once and
   using one form for both the enumeration and the length, guarded by a
   round-trip check that rejoins the derived path and requires it to resolve.

   **It ran a fourth time, and reached MakeAppx — run `35553464487` (commit
   `baf46a8`).** This is the first run where the script's own work was correct
   end to end: `payload executable: SelekyT.exe` with no correction warning,
   and all six assets present. MakeAppx validated the manifest, accepted the
   application identity, and walked every payload file before dying in its
   writer with `0x8007000e - Not enough memory resources are available to
   complete this operation`. The tool it selected was the **x86** binary: the
   SDK ships x86 and x64 side by side, a descending name sort puts `x86`
   first, and a 32-bit process cannot hold this payload's file list in 2 GB.
   Nothing about the package was wrong — the wrong architecture of a correct
   tool was chosen. Fixed by preferring the 64-bit binary, warning instead of
   failing when only a 32-bit one exists, and pinning both with a test that
   fails against the naive sort.

   The pack has now executed. Run `35555583046` (commit `d428c78`) completed
   `success` in 27m10s, both jobs 0 failed / 0 skipped, with MakeAppx running
   validation ON from the x64 binary. The first green `msix` job was the
   verification step — and it passed.

   The artifact was then verified off the runner by reading its zip64 central
   directory directly: 9552 entries, zero duplicates, 2057.5 MiB uncompressed
   → 1525.2 MiB compressed. That is the sum of the members' compressed streams;
   the whole `SelekyT.msix` file is 1527.44 MiB once headers, the 1.21 MiB
   central directory and the zip64 records are included. The two are different
   quantities, not the same one rounded.
   `AppxManifest.xml`, `[Content_Types].xml`, `AppxBlockMap.xml`,
   `SelekyT.exe` and all six `Assets\*.png` are present, and the packed manifest
   still says `Executable="SelekyT.exe"` — the committed copy and the installed
   payload agreed, so no correction fired. Identity matches `tauri.conf.json`
   on all four fields. One real defect found in the payload: the whisper
   `small` weights were in the package three times byte-for-byte, 1383 MiB of a
   2057.5 MiB payload — a staging-layout problem, since fixed in
   `stage_model()` by collapsing the cache to one real file per snapshot entry.
   The fix is verified against the installed `huggingface_hub` on this host
   (offline resolution of a collapsed cache), but the *size* effect is not
   measured until a Windows run stages through it.

   This host has no MakeAppx, no Windows SDK and no PowerShell, and that is
   the whole point of the history above: five runs, four defects and then a
   green, and not one of the four was reachable from a Linux host. What the host
   *can* reach is
   pinned by the packaging tests: identity, version,
   executable name and every image the manifest references agree with
   `tauri.conf.json` and with the generator, and each PNG is round-trip decoded
   and compared pixel-for-pixel against the buffer that produced it. MakeAppx's
   schema validation was the one check that did not exist here, which is why the
   pack script runs it with validation deliberately ON.

   **What the green run does not establish:** the package has never been
   installed or run. Schema-valid, structurally consistent and MakeAppx-accepted
   is not installable. This host has no Windows machine, so activation, the tile
   and splash, and Partner Center submission are all untested, and the artifact
   name says `-unverified` for exactly that reason.

   Signing is a separate release stage and is not faked: the artifact is named
   `selekyt-windows-msix-unsigned-unverified`, and an unsigned MSIX will not
   install without developer mode or sideloading.

## Known leftover

`origin/worktree-agent-a8b3e2de76a11eaa1` is a stale branch from an earlier
parallel attempt. It is an *old fork*: merging it would delete `engine/rhythm.py`,
`engine/threads.py` and ~4500 lines of work on `main`. Its two useful pieces —
the Tauri capability file and the packaging scripts — were reviewed and the
capability file was brought over by hand (with `dialog:allow-open` added, which
the branch's copy omitted and the UI requires). It was left in place rather
than deleted; it should be removed or archived.

**A mislabelled artifact is still published.** Run `35538322486` (head
`de0c151`) uploaded its installer as `selekyt-windows-x64-signed` (artifact id
`10614207051`, 1.22 GiB, created 2026-09-20T21:41Z) when every build in that era
was unsigned — this is exactly the labelling bug item 8 above describes, and the
artifact keeps the name the bug gave it. It is **unsigned despite its name**, it
will trip SmartScreen, and it should be deleted from the run's artifacts page.
It has not been deleted, because that is irreversible and the decision belongs
to whoever owns the release. Do not download it on the assumption the name is
accurate.
