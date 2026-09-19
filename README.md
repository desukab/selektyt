# SelekyT

**An AI-native editorial engine for turning long-form video into structured, editable stories and publishable social cuts.**

SelekyT is being built around a simple idea: **let the machine do the first-pass editorial work without taking the editor out of the loop.**

It analyzes long-form video, understands complete story units, ranks what is worth keeping, applies context/truth safeguards, removes dead air, reframes the subject for vertical video, and produces both finished media **and an editable timeline**.

The goal is not another black-box “AI clip generator”. SelekyT is an **auditable editorial pipeline** that can turn one long video into:

- publishable 16:9 and 9:16 cuts
- word-timed captions
- ranked story selections and editorial reports
- **FCPXML + CMX 3600 EDL timelines that an editor can open and continue working on**
- machine-readable cutlists and provenance

**Input:** one YouTube URL **or** one local video file.

**Output:** editorial decisions, editable timelines, captions, and rendered social-video cuts.

## One command

```bash
pip install -e '.[transcription]'
selekyt "https://www.youtube.com/watch?v=VIDEO_ID"
```

Local files work the same way and need no network and no `yt-dlp`:

```bash
selekyt ./my-talk.mp4 --limit 6
```

Optional controls:

```bash
selekyt "URL" --model small --language te --limit 6
```

`--language te` can be used for Telugu; the same option works for other Whisper-supported languages. By default the source language is detected and preserved.

## Check your environment first

```bash
selekyt --doctor
```

Reports which tools are present and, if any required one is missing, names it.
`python3`, `ffmpeg`, `ffprobe` and `faster-whisper` are required. `yt-dlp` and the
unversioned `python` are optional — `yt-dlp` is only needed for URL sources, and
a host with only `python3` is fine.

### Hosts where faster-whisper cannot run

faster-whisper needs a ctranslate2 wheel for your platform and Python; some pairs have none (e.g. Termux ARM64 on Python 3.14). There the pipeline falls back to sherpa-onnx's Whisper export, which ships as a plain wheel and runs on CPU, with a whisper model placed under `~/.cache/telugify/models/sherpa-onnx-whisper-base`:

```bash
pip install -e '.[sherpa]'
```

Word timestamps from this backend are distributed by character proportion within each whisper segment rather than read from cross-attention, and that is recorded in the run's provenance so it is never mistaken for per-token timing.

## What SelekyT actually does

SelekyT treats editing as a pipeline of **editorial decisions**, not just a rendering task.

1. Downloads a supplied YouTube video with `yt-dlp`, or accepts a local video.
2. Transcribes locally with faster-whisper using word timestamps and VAD.
3. Groups transcript segments into **complete thoughts and story units**, rather than arbitrary time windows.
4. Scores candidate stories for hook strength, curiosity, contrast, information density, surprise, emotional pull, payoff, context and visual potential.
5. Applies a **context/truth guard** so an attention-grabbing edit cannot outrun what the source actually says.
6. Removes conservative amounts of dead air and redundant pauses while preserving natural speech.
7. Produces separate 16:9 and 9:16 edits.
8. Tracks a face when available; otherwise estimates the visually active region and moves the crop over time.
9. Generates Unicode-safe word-timed captions.
10. Writes the editorial selections, transcript, provenance, QA data and rejection reasons as structured JSON.
11. Exports the **same edit decision as an editable FCPXML 1.11 timeline and CMX 3600 EDL**, using the exact intervals that were rendered.
12. Validates the exported timeline so the edit sent to an NLE is the edit SelekyT actually shipped.

The important distinction is that SelekyT can produce a finished video **without throwing away the editorial decision behind it**. The timeline, cutlist and provenance remain available for a human editor to inspect, modify and continue from.

With `--audio-analysis`, the same run additionally produces a VAD-driven jump-cut
edit: speech-region cutlist, pause classification, an audio-truth layer that
fuses transcript words against detected speech, and an FCPXML 1.11 timeline for
an NLE. `--export-fcpxml` writes that timeline; the CLI and the Rust core agree
on it byte-for-byte.

The per-cut EDL carries no frame-rate field (CMX 3600 has none), so it is
validated on the rate the source was actually probed at rather than the 30 fps
its `:` separators imply. Relinking at the wrong rate shows a spurious few-frame
retime on an otherwise correct list; the shipped files are clean at their own
rate.

## Architecture

SelekyT is three layers, each independently usable:

| Layer | What it is | Entry point |
| --- | --- | --- |
| CLI | the one-command pipeline above | `selekyt "URL"` |
| Rust core (`rust/`) | mechanical parity layer: AutoCut-derived cut algebra, energy VAD scoring, FCPXML/ffconcat export | `selekyt-mech` (JSON-RPC over stdio) |
| Python sidecar (`selekyt/server.py`) | intelligence layer: pluggable VAD backends, provenance, audio stage | `selekyt --server` |

Both sidecars speak the same JSON-RPC envelope (
`{"op","params"}` → `{"ok","op","data"|"error"}`), so tooling can target
either implementation. Cross-language agreement is pinned by
`tests/test_sidecar_parity.py`; the parity contract is documented in
`docs/autocut-parity.md`.

## Shell harness

`web/` holds a minimal zero-dependency shell: a Node process spawns the Python
sidecar and serves a UI over local HTTP. It has two halves:

```bash
node web/server.mjs      # → http://127.0.0.1:4242
```

**Audio analysis** — click *Analyze sample WAV* (a deterministic generated clip,
no external media) or choose your own WAV; the threshold slider re-segments the
cached scores live and exports a cutlist plus FCPXML.

**Video → social cuts** — the same pipeline as the CLI, driven from the page.
Type a path inside the project (an uploaded file's path is filled in for you)
or paste an `https://` URL, pick a clip count, model and caption preset, and
*Make cuts*. The resulting renders, captions, timelines and reports are listed
as downloads. *Audio analysis* turns on the same `--audio-analysis` stage the
CLI has.

If YouTube asks for sign-in, put the path to a cookies file in the field. Unlike
a media path it is deliberately **not** confined to the project directory: the
bridge never reads or uploads it, it only hands the path to `yt-dlp`, and a
cookie file stored inside the repo is one `git add` away from being leaked.

The frontend is designed to be reused unchanged inside the Tauri webview once a
desktop webkit2gtk host is available (`docs/tauri-termux-blocker.md`).

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `no stories passed the gate` | Every candidate was rejected by the editorial or context guard. Read `report.json` → `rejection_reasons`; usually the transcript is too short or mostly music. Try a longer source or `--limit` lower. |
| `--limit must be >= 1` / `--chunk-minutes must be >= 1` | A bad numeric flag. These are checked before any network or model work, so nothing is wasted. |
| `YouTube rejected automated traffic` | YouTube asks for sign-in. Pass `--cookies` a Netscape cookies.txt **or** a browser `Cookie:`-header file. Credentials stay local; do not commit them. |
| `selekyt: command not found` after install | The venv was not activated. `node web/server.mjs` probes `.venv/bin/python` first for exactly this reason; the CLI needs activation. |
| captions lag behind the rendered cut | They should not. Cue timing accounts for the pauses the edit removes; if you see drift, that is a bug — the cue bounds and the edited duration are pinned in `tests/test_captions.py`. |
| `python MISSING` from `--doctor` | Not on a modern host. `python3` is the required name; the unversioned `python` is optional. |
| a render is cut short or black | Re-run. Partial renders and corrupted checkpoints are quarantined, recovered from `.bak`, and only the missing clip is re-rendered. |

## Limitations

- **Transcription fidelity.** faster-whisper on CPU with `--model small` is the
  default for speed, not accuracy. Use `--model medium` for quality; word
  timestamps from the sherpa fallback are character-proportional, not from
  cross-attention, and that is recorded in provenance.
- **VAD backend.** `silero` and `sherpa-onnx` need wheels + a model file that
  are not present on every host. When they are unavailable the pipeline falls
  back to the energy backend and says so in `fallback_reason` — it never reports
  a backend it did not use.
- **Vertical reframing.** Face tracking is used when a face is found; otherwise
  the active region is estimated and the crop moves over time. A scene with no
  clear subject can still be reframed poorly.
- **No fabrication.** SelekyT selects and reorders source speech. It does not
  write new claims, and its editorial reports are auditable rather than opaque.
- **Desktop shell.** The Tauri build compiles and its sidecar plumbing is
  tested, but no packaged binary has been produced on any platform. The web
  shell is the usable local interface today.

## Development

```bash
python -m pytest -q                          # Python + sidecar + shell tests
cargo test --manifest-path rust/Cargo.toml   # Rust mechanical core
node web/server.mjs                          # manual shell check
```

CI (`.github/workflows/ci.yml`) runs pytest on Python 3.11–3.13 plus
`cargo test` and `cargo clippy -- -D warnings` on every push.

## Output

```text
out/project/
├── source/                       (URL sources only)
├── transcript.json
├── selections.json
├── report.json                   (the run summary: see below)
├── qa.json                       (per-output decode + coverage checks)
├── facebook/
│   ├── 01.mp4
│   └── 02.mp4
├── shorts/
│   ├── 01.mp4
│   └── 02.mp4
├── captions/
│   ├── 01.ass                    (word-timed, burned into the render)
│   ├── 01.srt                    (same cues, no styling)
│   └── 01.json
├── timeline/                     (one EDL + FCPXML per shipped cut)
│   ├── 01.edl
│   └── 01.fcpxml
└── audio/                        (with --audio-analysis)
    ├── cutlist.json
    ├── cutlist.fcpxml            (with --export-fcpxml)
    ├── audio_truth.json
    └── vad_regions.json
```

`report.json` records what was selected and what was rejected and why, plus the
audio provenance above or an explicit `audio: {enabled: false}` so a downstream
consumer can tell "not run" from "failed silently".

## Retention philosophy

SelekyT does **not** claim that viewers universally want a specific cut every 2 or 3 seconds. YouTube's own analytics guidance says audience retention reveals dips, spikes, top moments and gradual declines, and recommends improving the opening and moving compelling later material earlier when appropriate. YouTube also explicitly advises editing for pacing/rhythm and using retention data to understand where viewers leave. SelekyT encodes these as editorial principles: hook quickly, remove dead air and redundant setup, preserve complete thoughts, vary visual information, and pay off the opening promise.

For vertical delivery, Meta recommends native 9:16 creative with audio and important creative elements inside safe zones. SelekyT therefore treats reframing as a subject-composition problem rather than a blind center crop.

## Roadmap

The next phase is about making SelekyT a **repeatable editorial system**, not simply adding more effects:

- Local Ollama editorial passes for deeper semantic story selection.
- Better person/object tracking for vertical reframing.
- Motion-aware cut points and B-roll-aware visual rhythm.
- Channel-specific retention learning from exported analytics.
- Editorial feedback loops so repeated decisions can improve future selections.
- Automated title/hook variants without fabricating claims.
- Stronger NLE interoperability and timeline workflows.

Already shipped, not roadmap: native SRT alongside ASS with four caption
presets (`--caption-style`), FCPXML 1.11 and CMX 3600 EDL export, structured
cutlists/provenance, audio analysis, and the web shell's video pipeline.

## License

SelekyT is **proprietary and source-available**. The source code is provided for authorized evaluation, development and collaboration only.

No permission is granted to copy, modify, distribute, sublicense, publish, sell, lease, host as a competing service, or create derivative commercial products from this software without prior written permission from the copyright holder.

Access to the repository does not transfer ownership or grant commercial rights. Separate written agreements may grant additional rights.

See [LICENSE](./LICENSE) for the full terms.


## License

**SelekyT is proprietary and source-available, not open source.**

The source code is provided for authorized evaluation, development, and collaboration only. Copying, redistribution, commercial use, public hosting, sublicensing, or creation of competing products requires prior written permission.

See [LICENSE](./LICENSE) for the full terms.

Third-party dependencies remain subject to their own licenses.
