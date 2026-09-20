# RELEASE — SelekyT desktop application for Windows 11

**Acceptance status: the engine path is proven on a real file. The Windows
installer itself has never been executed on a Windows machine.** The two
claims are separate and this report does not merge them.

---

## Current commit

```
59e5327  desktop: real cancellation, contract-tested UI, and honest signing   ← tip
91f3a39  fix(windows): NSIS wants a language name, not a locale code
33e42f8  fix(windows): the portability bugs that would stop the app on Windows 11
b5078e5  feat(desktop): the SelekyT Windows desktop shell and its build pipeline
```

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
| `apps/desktop/src-tauri/scripts/sign.ps1` | Conditional signing: signs when the Azure secrets exist, declines and exits 0 when they do not (**UNVERIFIED — never executed**) |
| `apps/desktop/src-tauri/capabilities/main.json` | The ACL: fs on user dirs, file picker, no network |
| `apps/desktop/src-tauri/{Cargo.toml,build.rs,Cargo.lock}` | Deps; codegen that tolerates an unstaged dev tree |
| `apps/desktop/ui/{index.html,app.mjs,style.css,lib.mjs}` | The first-user-flow UI, plus the pure helpers the contract tests import |
| `apps/desktop/stage-windows.sh` | Stages sidecars, interpreter, ffmpeg, weights; pings before building |
| `.github/workflows/build-windows.yml` | The Windows x64 build pipeline, with honest signed/unsigned labelling |
| `.github/workflows/ci.yml` | Adds the Linux `shell` job and the UI syntax/grammar checks |
| `tests/ui_contract.mjs`, `tests/test_desktop_packaging.py` | The UI's contract with engine artifacts; the signing hook's contract with the build |
| `providers/whisper.py` | Intra-chunk thread cap (measured: 2 threads is the saturation point on 4 cores) |
| `selekyt/server.py` | `probe_media`/`read_artifact`/`stat_artifact`, UTF-8 stdio |
| `engine/nle_export.py` | Windows file-URI fix (the offline-import bug) |
| `providers/youtube.py`, `selekyt/cli.py`, `tools/*.py` | Windows portability fixes |
| 12 files under `selekyt/` `engine/` `providers/` | UTF-8 encoding sweep |
| `docs/packaging.md`, `docs/release-validation.md` | What shipped, what did not |

## Build command

From `apps/desktop`, after staging and `npm install`:

```
bash apps/desktop/stage-windows.sh
npm install
npx tauri build --bundles nsis,msi
```

GitHub Actions runs exactly this on `windows-latest` on every push to `main`
(`.github/workflows/build-windows.yml`), and uploads the NSIS `.exe` and MSI as
`selekyt-windows-x64-signed` or `selekyt-windows-x64-unsigned` depending on
whether `scripts/sign.ps1` actually signed the binaries — the name is derived
from the run, not asserted, so the artifact can never claim a trust state the
build did not achieve. Today, with no `SELEKYT_AZURE_*` secrets configured,
every artifact is `-unsigned` and the job posts a warning saying so.

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

1. Download the `selektyt-windows-x64` artifact from the Actions run, or the
   NSIS setup it contains.
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

- **1234 passed, 13 skipped** (full Python suite, ~3 min on this host), up from
  a 1215 / 13 baseline. The 19 new tests are the desktop packaging contract (5)
  and engine-side growth; nothing regressed.
- **11 Rust unit tests** in `apps/desktop/src-tauri`, including
  `a_chatty_stderr_does_not_deadlock_the_rpc`, which pins the pipe-buffer
  deadlock fix that this session found by hitting it for real, and
  `cancel_unblocks_a_call_in_flight`, which pins the cancellation fix. Both run
  in well under a second — the cancellation test uses a shell builtin busy loop
  rather than `sleep`, because SIGKILLing a shell leaves a forked `sleep`
  holding the write end of the pipe, and the test then blocks until the sleep
  expires. That is a property of the test double, not of the engine: every
  ffmpeg call in `providers/ffmpeg.py` pipes its own stdout, so the real
  sidecar has no orphaned child.
- `cargo clippy --all-targets -- -D warnings` is clean, and CI now denies
  warnings on Linux before the Windows job pays for a build.
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
  `build-windows` on `windows-latest`.
- The full suite after every change: 1234 / 13, no regressions; 11 Rust unit
  tests in the shell, clippy clean at `-D warnings`; the UI's pure logic passes
  `tests/ui_contract.mjs` against a real engine project tree.

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
5. **Installer size and install UX.** ~1 GB payload; NSIS LZMA will be slow.
6. **The GUI itself.** Headless host, no X server or xvfb. The binary was
   verified through its RPC surface, not its window. The acceptance flow was
   driven end to end against the real engine over that same boundary, but a
   reviewer must still click through it on Windows 11. **What is now tested
   without a window** is the pure rendering layer: `tests/ui_contract.mjs` runs
   the formatters, the SRT reader and the timeline arithmetic against a project
   tree the engine really wrote. That is how the single-digit-hours SRT bug was
   caught — the engine emits `0:00:00,787`, and a strict two-digit grammar
   silently rejects every cue in every caption file, which would have looked
   exactly like a missing captions file in the running app.
7. **WebView2 availability.** The config downloads the bootstrapper silently;
   untested on a machine without Edge.
8. **The signing hook has never executed.** `bundle.windows.signCommand` points
   at `scripts/sign.ps1`, which is correct-by-construction only: its argument
   contract, its sha256 + RFC 3161 timestamping and its "decline and exit 0
   without credentials" behaviour are pinned by `tests/test_desktop_packaging.py`,
   but no PowerShell exists on this host and the script has never run once.
   Until it has, the honest statement is **the build is unsigned**, which is
   also what the artifact name says. Windows SmartScreen will warn on it; that
   is expected and is not being hidden or worked around.
9. **MSIX / Microsoft Store packaging does not exist.** Tauri 2's `BundleType`
   has no `Appx` variant (verified in the vendored `tauri-utils` config source,
   not assumed from docs), so the Store path needs a separate MakeAppx +
   `AppxManifest.xml` stage that has not been written. The current NSIS + MSI
   pair covers direct distribution only.

## Known leftover

`origin/worktree-agent-a8b3e2de76a11eaa1` is a stale branch from an earlier
parallel attempt. It is an *old fork*: merging it would delete `engine/rhythm.py`,
`engine/threads.py` and ~4500 lines of work on `main`. Its two useful pieces —
the Tauri capability file and the packaging scripts — were reviewed and the
capability file was brought over by hand (with `dialog:allow-open` added, which
the branch's copy omitted and the UI requires). It was left in place rather
than deleted; it should be removed or archived.
