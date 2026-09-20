# RELEASE — SelekyT desktop application for Windows 11

**Acceptance status: the engine path is proven on a real file. The Windows
installer itself has never been executed on a Windows machine.** The two
claims are separate and this report does not merge them.

---

## Current commit

```
33e42f8  fix(windows): the portability bugs that would stop the app on Windows 11
b5078e5  feat(desktop): the SelekyT Windows desktop shell and its build pipeline
af0aa55  French WER: the fifth C10 language, and a doc-drift gate   ← prior tip
```

Both pushed to `main` at `https://github.com/desukab/selektyt`.

## Architecture

A thin Tauri 2 webview over the existing engine. There is no second engine.

```
webview (ui/index.html + ui/app.mjs)
   │ invoke() — four commands only
   ▼
src-tauri/src/main.rs
   selekyt_rpc(target, op, params)  →  JSON-RPC over stdio
   selekyt_health() / selekyt_out_dir() / reveal_path()
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
| `apps/desktop/src-tauri/src/main.rs` | The shell: 4 commands, sidecar lifecycle, unconditional stderr drain |
| `apps/desktop/src-tauri/tauri.conf.json` | NSIS + MSI targets, `externalBin`, resource map |
| `apps/desktop/src-tauri/capabilities/main.json` | The ACL: fs on user dirs, file picker, no network |
| `apps/desktop/src-tauri/{Cargo.toml,build.rs,Cargo.lock}` | Deps; codegen that tolerates an unstaged dev tree |
| `apps/desktop/ui/{index.html,app.mjs,style.css}` | The first-user-flow UI |
| `apps/desktop/stage-windows.sh` | Stages sidecars, interpreter, ffmpeg, weights; pings before building |
| `.github/workflows/build-windows.yml` | The Windows x64 build pipeline |
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
the `selektyt-windows-x64` artifact.

## Windows artifact produced

**By the workflow:** NSIS setup + MSI, both carrying the Tauri shell, the
`selekyt-mech` Rust sidecar, a bundled python-build-standalone 3.12 with the
SelekyT package installed into it, LGPL ffmpeg/ffprobe, and the staged
faster-whisper `small` weights — so a first run transcribes with no network.

**Physically in hand on this host: none.** The development host is Linux x64
with no Windows SDK and no mingw target. The Linux shell binary was built and
exercised instead (`/dev/shm/selekyt-target/debug/debug/SelekyT`, ~197 MB), and
the desktop sidecar binary it spawns is the one the installer ships.

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

- **1215 passed, 13 skipped** (full Python suite, 3 min on this host), up from
  a 1204 / 13 baseline. The 11 new tests are the desktop ops and the contract
  coverage for them; nothing regressed.
- **6 Rust unit tests** in `apps/desktop/src-tauri`, including
  `a_chatty_stderr_does_not_deadlock_the_rpc`, which pins the pipe-buffer
  deadlock fix that this session found by hitting it for real.
- Skips are pre-existing and environmental: corpus media not staged, optional
  backends (sherpa-onnx, silero, ollama) not installed.

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
- The full suite after every change: 1215 / 13, no regressions.

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
   reviewer must still click through it on Windows 11.
7. **WebView2 availability.** The config downloads the bootstrapper silently;
   untested on a machine without Edge.

## Known leftover

`origin/worktree-agent-a8b3e2de76a11eaa1` is a stale branch from an earlier
parallel attempt. It is an *old fork*: merging it would delete `engine/rhythm.py`,
`engine/threads.py` and ~4500 lines of work on `main`. Its two useful pieces —
the Tauri capability file and the packaging scripts — were reviewed and the
capability file was brought over by hand (with `dialog:allow-open` added, which
the branch's copy omitted and the UI requires). It was left in place rather
than deleted; it should be removed or archived.
