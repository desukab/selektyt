# SelekyT

Local-first long-form → social video editorial engine.

**Input:** one YouTube URL **or** one local video file.

**Output:** a small ranked set of publishable Facebook cuts and 9:16 Shorts/Reels, with minimal language-preserving subtitles and subject-aware reframing.

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

1. Downloads a single supplied YouTube video with `yt-dlp`.
2. Transcribes locally with faster-whisper using word timestamps and VAD.
3. Groups transcript segments into complete thoughts rather than arbitrary 30-second windows.
4. Scores stories for hook strength, curiosity, contrast, information density, surprise, emotional pull, payoff, context and visual potential.
5. Applies a context/truth guard so sensational framing cannot outrun the source.
6. Removes conservative amounts of dead-air between recognized words instead of applying a fake universal cut rate.
7. Produces a normal 16:9 Facebook edit and a separate 9:16 vertical edit.
8. Tracks a face when available; otherwise estimates the visually active region and moves the crop over time. This avoids the common "center crop chopped the speaker" failure.
9. Burns minimal subtitles using a Unicode-capable font profile. Set `SELEKYT_FONT` if the machine uses a different installed font.
10. Writes selections, transcript and report JSON alongside the MP4s.
11. Writes a CMX 3600 EDL and an FCPXML 1.11 timeline per shipped cut, built from
    the same interval list the render concatenated, so an NLE relinks the cut
    that was actually shipped. Both are validated at write time.

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

- Local Ollama editorial pass for deeper semantic story selection.
- Better person/object tracking for vertical reframing.
- Motion-aware cut points and B-roll-aware visual rhythm.
- Channel-specific retention learning from exported analytics.
- Automated title/hook variants without fabricating claims.

Already shipped, not roadmap: native SRT alongside ASS with four caption
presets (`--caption-style`), FCPXML 1.11 and cutlist export
(`--audio-analysis --export-fcpxml`), and the web shell's video pipeline.

## Rights

Use SelekyT only with videos you own, have permission to edit, or are otherwise legally entitled to process and republish. A public YouTube URL does not itself grant republication rights.
