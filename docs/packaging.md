# Desktop packaging — SelekyT for Windows x64

**Honest status:** the Windows x64 *build configuration and artifact workflow
ship in this repository. **No native Windows build has ever been executed on
this development host** — it is Linux x64, headless, with no Windows SDK, no
mingw target and no display. What *was* physically verified is listed under
"Verification evidence"; what was not is under "What remains blocked on
Windows". Treat every Windows-specific claim below as *designed*, not
*observed*.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  SelekyT.exe   (Tauri 2 shell, apps/desktop/)                     │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │  webview: ui/index.html + ui/app.mjs                        │   │
│  │     invoke("selekyt_rpc", {target, op, params})             │   │
│  │     invoke("plugin:dialog|open", ...)  native file picker   │   │
│  └───────────────┬────────────────────────────────────────────┘   │
│                  │ Tauri commands (src-tauri/src/main.rs)          │
│  ┌───────────────▼────────────────────────────────────────────┐   │
│  │ selekyt_rpc(target, op, params) → JSON-RPC over stdio       │   │
│  │ selekyt_health()  → ping both sidecars                      │   │
│  │ selekyt_out_dir() / reveal_path() → open the results        │   │
│  │ prepend_bin_to_path() → resources/bin on PATH for the engine│   │
│  └───────┬───────────────────────┬──────────────────────────────┘   │
│          │ externalBin            │ externalBin                     │
│  ┌───────▼──────────┐   ┌─────────▼────────────────────────────┐   │
│  │ selekyt-mech     │   │ selekyt-python (python-build-        │   │
│  │ (rust/ sidecar)  │   │  standalone 3.12) running            │   │
│  │ mechanical core  │   │  `-m selekyt --server`               │   │
│  └──────────────────┘   │ = the CLI's intelligence layer,      │   │
│                         │   pip-installed from this repo        │   │
│                         └───────────────┬──────────────────────┘   │
│                                         │ subprocess, PATH-scoped  │
│                         ┌───────────────▼────────────────────────┐   │
│                         │ resources/bin/ffmpeg.exe, ffprobe.exe │   │
│                         └────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

Three properties the design is built around:

1. **One engine.** The packaged app runs `selekyt.server` — the same module the
   CLI and `selekyt --server` use — from the bundled interpreter. There is no
   second copy of the scoring, segmentation, cut-algebra or rendering logic
   anywhere in the app. `tests/test_sidecar_contract.py` and
   `tests/test_server.py` pin the wire envelope both sides speak.
2. **A minimal permission surface.** The webview may read and write the user's
   own media/output directories, open a native file picker, and invoke the
   four custom commands. It has **no network permission of any kind**.
   `src-tauri/capabilities/main.json` is the entire ACL; `read_artifact`
   accepts a bare filename only, so it cannot be aimed outside a project tree.
3. **Local-first by construction.** ffmpeg, the interpreter and the whisper
   `small` weights are all staged into the installer, so a first run
   transcribes with no network. Nothing in the desktop path requires Azure or
   any cloud service.

## Build command

From `apps/desktop`, after `bash apps/desktop/stage-windows.sh` and `npm install`:

```
npx tauri build --bundles nsis,msi
```

GitHub Actions runs exactly this, end to end, in
`.github/workflows/build-windows.yml` (push to `main`, or
`workflow_dispatch`), and uploads the NSIS `.exe` and the MSI as the
`selektyt-windows-x64` artifact.

`stage-windows.sh` stages, into the exact paths `tauri.conf.json` declares:

| `tauri.conf.json` key | staged at | contents |
| --- | --- | --- |
| `externalBin binaries/selekyt-mech` | `binaries/selekyt-mech-x86_64-pc-windows-msvc.exe` | `cargo build --release` from `rust/` |
| `externalBin binaries/selekyt-python` | `binaries/selekyt-python-x86_64-pc-windows-msvc.exe` | a Rust launcher forwarding argv to `python/python.exe` |
| `resources resources/python` | `python/` | python-build-standalone 3.12 + `pip install ".[transcription,llm]"` |
| `resources resources/bin` | `bin/` | gyan.dev LGPL `ffmpeg.exe`, `ffprobe.exe` |
| `resources resources/models` | `models/` | `Systran/faster-whisper-small` in the HF cache layout |
| `resources resources/share` | `share/` | kept present so the mapping resolves |

The script is idempotent, exits non-zero on any missing piece, and **pings both
sidecars the way the shell does** before the long `tauri build` runs, so a
half-staged tree fails loudly and early instead of shipping a broken installer.

### Why the python sidecar is a launcher, not a copy

Tauri drops `externalBin` entries next to `SelekyT.exe` at the install root,
but `resources/python` maps to a *subdirectory*. A python-build-standalone
interpreter resolves `Lib/` relative to its own executable, so a copy of
`python.exe` sitting at the install root would find no stdlib and die at
startup. The staged `selekyt-python-*.exe` is therefore a zero-dependency
std-only Rust launcher that forwards argv to `python/python.exe` with inherited
stdio and the child's exit code. Its fallback lookup
(`../resources/python/python.exe`) exists so the staged binary is pingable
before an installer exists.

### Environment overrides

`PBS_TAG` pins a python-build-standalone release; `SELEKYT_SKIP_MODEL=1` builds
the installer without the ~500 MB weights download (smoke runs).

## Verification evidence (runs on THIS Linux host)

- `cargo build` and `cargo test` in `apps/desktop/src-tauri` pass: 6 Rust unit
  tests, including `a_chatty_stderr_does_not_deadlock_the_rpc`, which pins the
  pipe-buffer deadlock fix (stderr is drained unconditionally inside
  `Sidecar::spawn_in`, not by the UI relay).
- `1215 passed, 13 skipped` across the full Python suite, including the
  sidecar envelope contract and the desktop shell ops (`probe_media`,
  `read_artifact`, `stat_artifact`).
- A real end-to-end run of the actual staged `selekyt-python` sidecar binary
  against a real 1013.9 s / 1280×720 source, over the same JSON-RPC the Tauri
  `selekyt_rpc` command uses. See `docs/release-validation.md`.
- `probe_media` verified against that same file to report the **coded**
  dimensions (1280×720), not the pixel aspect ratio, which is 1:1 on most
  sources and rendered every file as "1x1".
- The ffmpeg `subtitles` filter's colon escaping for a Windows drive letter
  was proven with a real `:`-in-path render: unescaped fails with
  `Invalid argument`, escaped renders a valid mp4.
- `stage-windows.sh` pieces exercised against the real upstream archives: the
  python-build-standalone release resolves to a real tag; its Windows archives
  are `.tar.gz`, not `.zip`; gyan.dev extracts `ffmpeg.exe` + `ffprobe.exe`;
  both sidecars answer `ping` with `{"pong": true, ...}`.

## What remains blocked on Windows

Nothing below has run on a Windows machine. Each is a claim about code that
could not be executed here, not a verified result.

1. **The installed launcher finds the interpreter.** The launcher's
   `../resources/python/python.exe` fallback is exercised on Linux; the
   installed-layout resolution (`resource_dir()` beside `SelekyT.exe`) is not.
2. **Resource layout at runtime.** Both the launcher and `sitecustomize.py`
   assume `python/`, `bin/`, `models/`, `share/` sit beside the main exe. That
   matches what `main.rs`'s `prepend_bin_to_path` already assumes, but no real
   install has been exercised.
3. **Model discovery.** `providers/whisper.py` calls `WhisperModel("small")` by
   name; the weights are staged in the HF cache layout and `sitecustomize.py`
   sets `HF_HOME`/`HF_HUB_CACHE` via `setdefault` (a user-set value wins).
   `HF_HUB_OFFLINE` is deliberately unset, so offline first runs rely on
   huggingface_hub's connection-error → local-cache fallback. Test a
   transcription with the network unplugged.
4. **`Systran/faster-whisper-small` ships no `preprocessor_config.json`.**
   faster-whisper should fall back to its built-in 16 kHz defaults, but a real
   transcription has not confirmed it.
5. **Installer size.** ffmpeg+ffprobe ≈ 210 MB, `small` ≈ 484 MB,
   interpreter+deps ≈ 250 MB → payload near 1 GB. Under NSIS's 2 GB ceiling,
   but LZMA over 1 GB is slow and the artifact is big. `SELEKYT_SKIP_MODEL=1`
   or a `tiny`/`base` model are the levers.
6. **The GUI itself.** The host is headless (no X server, no xvfb), so the
   binary was verified through its RPC surface, not its window. The acceptance
   path — launch, pick a file, watch progress, read results — was driven
   end-to-end against the real engine over that same RPC boundary.
