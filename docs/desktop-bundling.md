# Desktop dependency bundling plan

How a clean Windows 11 or macOS machine ends up with a working SelekyT having
installed **nothing** but the app itself. This is the plan the CI workflows in
`.github/workflows/desktop-*.yml` execute and that
`apps/desktop/scripts/*` implements.

Everything below is *configuration and assembly logic that lives in this
repository*. No binary is committed; every binary is fetched or built at
packaging time, sha256-verified, and its provenance written next to it.

## What has to be bundled

| Component | What it is | Origin | Size class |
| --- | --- | --- | --- |
| Python interpreter | CPython 3.12.14 | `python-build-standalone` release `20260901` | ~25 MB |
| Intelligence layer | `selekyt/`, `engine/`, `providers/`, `models/` + deps | `pip install` **of this repository** | ~250 MB with `opencv-python-headless` |
| ASR engine | `faster-whisper` + `ctranslate2` | PyPI wheels | ~60 MB |
| Mechanical core | `selekyt-mech` (Rust) | built from `rust/` in the same CI job | ~3 MB |
| ffmpeg / ffprobe | LGPL static builds | Windows: BtbN; macOS: built from source | ~80–160 MB |
| Webview shell | Tauri 2 + `web/` frontend | built from `apps/desktop/` | ~15 MB |
| Model files | Whisper model | **not bundled** — see below | 0 |

## The interpreter: `python-build-standalone`, not PyInstaller

The decision, and why:

**Chosen:** a relocatable [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
CPython, extracted into `apps/desktop/resources/python/`, with this repository
`pip install`ed into that interpreter's `site-packages`.

**Rejected:** PyInstaller (freezing `selekyt` into a self-contained executable).

1. **"The packaged app uses the same engine/API as the CLI" must hold
   structurally, not by convention.** With a real interpreter the desktop
   sidecar is literally `python -m selekyt --server` — the same module, the
   same entry point, the same provenance fields. A freeze would need a second
   build graph and an ongoing guarantee that the frozen copy behaves like the
   source copy.
2. **No hook maintenance.** PyInstaller needs hidden-import hooks for
   `numpy`, `opencv-python-headless`, `ctranslate2` and `sherpa-onnx`, each of
   which has its own native loader behaviour. python-build-standalone needs
   none, because imports resolve exactly as they do for the CLI.
3. **Debuggability.** A user reporting a problem can be told to run the exact
   command the app runs, from inside the bundle, with the exact same
   interpreter. That is worth a lot for a local-first tool.
4. **Cost accepted:** the bundle is larger and less tidy than a frozen
   executable (~25 MB interpreter where a freeze shares the runtime), and the
   runtime tree must ship as a *resource* so its `Lib/`/`lib/` directory stays
   a sibling of the interpreter binary — that is how python-build-standalone
   locates its prefix. `tauri.conf.json` therefore declares the interpreter as
   an `externalBin` sidecar *whose path is inside the resource directory*,
   which keeps the permission surface tight and the prefix resolution intact
   regardless of where Tauri places sidecars.

Python is pinned to **3.12**, not 3.13/3.14: every binary dependency publishes
3.12 wheels for all four targets (`x86_64-pc-windows-msvc`,
`aarch64-apple-darwin`, `x86_64-apple-darwin`, and the Linux triple used only
to verify on the dev host). `pyproject.toml` requires `>=3.10`.

## The intelligence layer

`build_python.py` runs `pip install .` inside the bundled interpreter — a real
install, not editable, so the packaged app imports `selekyt.server` from
`site-packages`: byte-identical to the CLI at build time, and there is exactly
one copy of the engine in the repository.

The extras installed are `transcription,llm` (see `pyproject.toml`), which
pulls `faster-whisper` and `requests`. `sherpa-onnx` (the fallback engine for
hosts with no ctranslate2 wheel) is intentionally *not* installed by default:
it doubles the native dependency surface for an engine Windows and macOS never
need. `--extras sherpa` produces that variant.

## The Rust mechanical core

`cargo build --release --locked` in `rust/` produces `selekyt-mech`, which CI
copies to `apps/desktop/resources/selekyt-mech-<target-triple>` — the exact
name Tauri's `externalBin` mechanism requires. Both sidecars are declared in
`tauri.conf.json`:

```jsonc
"externalBin": ["binaries/selekyt-mech", "binaries/selekyt-python"]
```

and the capability grants `shell:allow-execute` for precisely those two names
with `sidecar: true`. Nothing else in the webview may execute anything.

## ffmpeg / ffprobe — license, size, discovery

### License: LGPL, non-negotiable

SelekyT is **MIT-licensed**. FFmpeg is dual LGPL/GPL. **Shipping a GPL build
would place the combined work under the GPL**, which the project does not
claim. Every binary bundled is therefore an LGPL build:

- **Windows** — [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds),
  static LGPL build pinned to the *dated* tag
  `autobuild-2026-09-17-13-19`, not the moving `latest` tag, so the pinned
  sha256 is meaningful. Static = no side-by-side DLLs, so plain PATH
  discovery works.
- **macOS** — **built from source** in `desktop-macos.yml` via
  `build_ffmpeg_macos.sh`, from the pinned `ffmpeg-8.1.2` tarball, configured
  `--disable-gpl --disable-version3 --disable-nonfree --disable-network`. This
  costs CI minutes but is the only defensible option: *every published static
  macOS binary we could verify is GPL*. `osxexperts.net` passes `--enable-gpl`
  (with x264/x265); `evermeet.cx` passes `--enable-gpl --enable-version3` and
  publishes Intel only. Neither is acceptable for an MIT app.
  `--disable-network` is deliberate too: the engine only ever feeds ffmpeg
  local files, and a tool that cannot open a socket cannot leak media.
- **Linux** — the BtbN `linux64-lgpl` build, used only so the packaging
  pipeline can be exercised on this development host. No Linux desktop bundle
  is shipped.

The license decision and the exact source are recorded in
`resources/bin/ffmpeg.sources.txt` next to the binaries, because the product
records provenance for its own outputs and its bundled toolchain deserves the
same.

### Size

Static LGPL ffmpeg/ffprobe is roughly 80–160 MB per platform — the single
largest item in the bundle after the Python wheel set. Alternatives considered
and rejected: dynamic builds (need the whole av* DLL/dylib set next to the
binary, which defeats PATH discovery and complicates macOS signing) and
system-provided ffmpeg (defeats "install nothing").

### Runtime discovery

`providers/ffmpeg.py` resolves ffmpeg and ffprobe with `shutil.which`, i.e.
**from PATH**. The engine is not modified for packaging. The desktop shell
instead prepends the bundled `resources/bin` to the PATH of the process that
hosts the sidecars (`prepend_bin_to_path` in
`apps/desktop/src-tauri/src/main.rs`), so the engine finds the bundled LGPL
binaries with no code change and no duplicated logic. The smoke test asserts
`shutil.which` inside the packaged runtime resolves to the bundled file, not a
system one.

## Model files — deliberately not bundled

A `base` Whisper model is ~150 MB per language family and belongs to the
user's first-run choice of size and language, so models are **fetched into the
user's cache on first transcription** (`~/.cache/telugify/models`, the same
path the CLI uses — see `providers/sherpa.py` `DEFAULT_MODELS_DIR`) with a
visible progress indication.

Two consequences stated plainly:

- "Nothing to install" means *no toolchain*. First transcription still needs
  network to fetch a model, exactly as the CLI does.
- Pre-seeding a model is **not currently a config switch**: `providers/sherpa.py`
  resolves its model directory with no environment override. Doing that needs
  a small engine change, which is out of scope here and is listed in
  `docs/packaging.md`.

The webview itself has **no network permission of any kind**
(`apps/desktop/src-tauri/capabilities/main.json`); the model download happens
inside the Python sidecar process, which is a separate trust boundary from the
rendered UI.

## Licensing appendix

| Component | License |
| --- | --- |
| SelekyT (`selekyt/`, `engine/`, `models/`, `providers/`, `web/`) | MIT |
| Rust core (`rust/`) | MIT |
| CPython (python-build-standalone) | PSF-2.0 |
| numpy, opencv-python-headless, faster-whisper, ctranslate2, yt-dlp, requests | BSD / Apache-2.0 / MIT as applicable |
| ffmpeg / ffprobe | **LGPLv2.1+** — no GPL libraries linked |
| Tauri + webview (WebView2 on Windows, WKWebView on macOS) | MIT / Apache-2.0 (WebView2 is governed by its own EULA, redistributed by the bootstrapper) |

Anyone redistributing the bundle must be able to reproduce it: `fetch_ffmpeg.py`
and `build_ffmpeg_macos.sh` pin every external artifact by URL and sha256, and
`python.sources.txt` / `ffmpeg.sources.txt` record what shipped and from where.
