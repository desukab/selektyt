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
`workflow_dispatch`). Two jobs, in this order:

| job | produces | artifact |
| --- | --- | --- |
| `build` | the NSIS `.exe` and the MSI | `selekyt-windows-x64-unsigned` today; `-signed` once the Azure Trusted Signing secrets exist |
| `msix` | the Store / sideload `.msix` | `selekyt-windows-msix-unsigned-unverified` |

**Verified on the runner** (run `35547280561`, commit `61ce51f`): the
labelling step read `marker hits: 152; credentials configured: false`, emitted
`signed=false`, posted the unsigned warning, and the artifact was uploaded as
`selekyt-windows-x64-unsigned`. That is the first installer the pipeline has
produced whose name matches its trust state — every run before the fix was
named `signed` regardless.

The artifact name is *derived*, not written: the `build` job greps the build
log for the `SELEKYT_UNSIGNED` marker that `scripts/sign.ps1` emits when it
declines to sign, and the name flips with it. A build that intended to sign but
could not fails the job, so the only way to reach the upload unsigned is "no
credentials configured". The `msix` job downloads whichever artifact that job
uploaded, by reading its `signed` output — the two expressions are pinned
against each other by `tests/test_desktop_packaging.py`, because two
hand-written copies of a two-valued label drift apart silently and the failure
is an MSIX job that downloads nothing while the build job looks healthy.

The `msix` job is separate and `needs: build` for a reason: the MSIX tooling
has never run anywhere, and a job that fails must not take the installers down
with it. A failure there shows up as its own red row.

The workflow file has one non-obvious constraint worth recording, because it
cost two runs. **The `runner` context is unavailable in a job's `env:`** — it
is legal in a *step's* `env:` and not at the job level, and putting it there
does not fail a step: it makes GitHub reject the whole file at parse time.

```
Invalid Argument - failed to parse workflow:
(Line: 53, Col: 26): Unrecognized named-value: 'runner'.
Located at position 1 within expression: runner.temp          (HTTP 422)
```

Zero jobs execute, there are no logs to download, the rerun API answers
`This workflow run cannot be retried`, and `gh run view` only hints that "this
run likely failed because of a workflow file issue". Everything local stays
green — the YAML parses, it lints clean, and the byte-identical file comes back
from the contents API — because none of those checks know which contexts are
legal where. `workflow_dispatch` on the ref is the only offline check that
surfaces it, and `tests/test_desktop_packaging.py` now pins the rule so the
suite cannot pass on a file GitHub rejects.

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

## The Store / sideload path (MSIX)

The NSIS + MSI installers above are the direct-download half of the
distribution contract. `apps/desktop/package-msix.ps1` is the other half, and
it does not assemble its payload by hand: it runs the NSIS installer that
`tauri build --bundles nsis` already produced, silently, into an empty
directory, and packs *that*. Reconstructing where Tauri places the sidecars,
the interpreter, ffmpeg and the weights is a layout that can only be observed
on Windows; getting it wrong would ship an MSIX whose contents do not match
what the installer installs.

`src-tauri/msix/AppxManifest.xml` is a full-trust desktop-bridge manifest on
`10.0.17763`. It uses `EntryPoint="windows.fullTrustApplication"` rather than
`uap10:RuntimeBehavior="packagedClassicApp"` + `uap10:TrustLevel="mediumIL"`,
which are the same thing — but the `uap10` attributes only exist from
10.0.19041, so a 17763 manifest must use the EntryPoint form. Declaring both at
once is an error when they contradict, so only one appears.

Two things the script does not trust:

- **The committed `Executable` value is reconciled against the payload.** The
  manifest's path is package-relative and the Tauri install layout is not
  knowable from this host, so the script finds `SelekyT.exe` in the installed
  tree, repairs the copy being packed if it disagrees, and warns loudly. A
  silent repair would hide real drift; a hard failure would block the build on
  a fact nobody could have checked.
- **Every image the manifest references must be in the payload**, because
  MakeAppx checks that and does *not* check pixel dimensions. The six assets
  come from `MSIX_ASSETS` in `icons/make_icons.py`, each at Microsoft's
  published scale-100 size — a 310x150 wide tile is not a rescaled square, so
  they are composed individually rather than cropped.

**Status: UNVERIFIED, and the CI job is the verification.** The `msix` job has
now run once (run `35548174430`, commit `742bad8`) and it **failed 47 seconds
in**, exactly as designed — `actions/download-artifact` reproduces the tree the
upload captured, so the NSIS setup arrived at
`bundle/apps/desktop/src-tauri/target/release/bundle/nsis/*.exe` while the
script looked for a `.exe` directly under `bundle/`. Nothing matched and the
script threw. That is a bug no amount of local checking could have found —
nothing on this host knows what the download action does with a nested
`path:` — and it cost 47 seconds, not a release.

Fixed: the download flattens (`merge-multiple: true`, which is what the
`-BundleDir` contract documents) and the script searches recursively as well,
because either layout is legal and the cost of looking is nothing.

What has *not* yet run is everything after that line: the silent install, the
executable reconciliation, the asset check and MakeAppx itself.
 The manifest has never been packed, installed or run. What *is*
verified on Linux is the structural contract — identity, version, executable
name and every referenced asset agree with `tauri.conf.json` and with the
generator — and every PNG is round-trip decoded and compared pixel-for-pixel
against the buffer that produced it. MakeAppx's schema validation is the only
check that does not exist here, which is why the pack script runs it with
validation deliberately ON and the CI step is expected to be where a manifest
or layout problem first surfaces. Treat the first green `msix` job as the
verification step, not as a release.

Signing is a separate stage and is not faked. An unsigned MSIX will not install
without developer mode or sideloading, and the artifact name says so.

### Environment overrides


`PBS_TAG` pins a python-build-standalone release; `SELEKYT_SKIP_MODEL=1` builds
the installer without the ~500 MB weights download (smoke runs).

## Verification evidence (runs on THIS Linux host)

- `cargo build` and `cargo test` in `apps/desktop/src-tauri` pass: 6 Rust unit
  tests, including `a_chatty_stderr_does_not_deadlock_the_rpc`, which pins the
  pipe-buffer deadlock fix (stderr is drained unconditionally inside
  `Sidecar::spawn_in`, not by the UI relay).
- `1248 passed, 13 skipped` across the full Python suite (measured at the
  commit this file ships in), including the sidecar envelope contract, the
  desktop shell ops (`probe_media`, `read_artifact`, `stat_artifact`) and the
  15 packaging tests that pin the workflow, the signing hook, the MSIX manifest
  and the two jobs' artifact contract. Re-run `.venv/bin/pytest -q` before
  quoting this number; it moves with every test added.
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
6. **The MSIX manifest and pack script.** See "The Store / sideload path"
   above. `package-msix.ps1` and `AppxManifest.xml` are written and structurally
   pinned by tests, but MakeAppx has never seen them. The CI `msix` job is the
   first time they run anywhere.
7. **The GUI itself.** The host is headless (no X server, no xvfb), so the
   binary was verified through its RPC surface, not its window. The acceptance
   path — launch, pick a file, watch progress, read results — was driven
   end-to-end against the real engine over that same RPC boundary.
