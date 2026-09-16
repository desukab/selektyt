# SelekyT

Local-first long-form → social video editorial engine.

**Input:** one YouTube URL.

**Output:** a small ranked set of publishable Facebook cuts and 9:16 Shorts/Reels, with minimal language-preserving subtitles and subject-aware reframing.

## One command

```bash
pip install -e '.[transcription]'
selekyt "https://www.youtube.com/watch?v=VIDEO_ID"
```

Optional controls:

```bash
selekyt "URL" --model small --language te --limit 6
```

`--language te` can be used for Telugu; the same option works for other Whisper-supported languages. By default the source language is detected and preserved.

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
sidecar and serves a UI that renders segmentation, the score curve and the
provenance record (`backend`, `vad_model`, `fallback_reason`, `hysteresis`).
The frontend is designed to be reused unchanged inside the Tauri webview once a
desktop webkit2gtk host is available (`docs/tauri-termux-blocker.md`).

```bash
node web/server.mjs      # → http://127.0.0.1:4242
```

Click **Analyze sample WAV** (a deterministic generated clip, no external
media) or choose your own 16 kHz mono WAV; the threshold slider re-segments the
cached scores live.

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
├── source/
├── transcript.json
├── selections.json
├── report.json
├── facebook/
│   ├── 01.mp4
│   └── 02.mp4
├── shorts/
│   ├── 01.mp4
│   └── 02.mp4
└── captions/
    ├── 01.ass
    ├── 01.srt
    ├── 01.json
    ├── 02.ass
    ├── 02.srt
    └── 02.json
```

## Retention philosophy

SelekyT does **not** claim that viewers universally want a specific cut every 2 or 3 seconds. YouTube's own analytics guidance says audience retention reveals dips, spikes, top moments and gradual declines, and recommends improving the opening and moving compelling later material earlier when appropriate. YouTube also explicitly advises editing for pacing/rhythm and using retention data to understand where viewers leave. SelekyT encodes these as editorial principles: hook quickly, remove dead air and redundant setup, preserve complete thoughts, vary visual information, and pay off the opening promise.

For vertical delivery, Meta recommends native 9:16 creative with audio and important creative elements inside safe zones. SelekyT therefore treats reframing as a subject-composition problem rather than a blind center crop.

## Roadmap

- Local Ollama editorial pass for deeper semantic story selection.
- Better person/object tracking for vertical reframing.
- Motion-aware cut points and B-roll-aware visual rhythm.
- Channel-specific retention learning from exported analytics.
- Native SRT export and platform-specific caption presets.
- Automated title/hook variants without fabricating claims.

## Rights

Use SelekyT only with videos you own, have permission to edit, or are otherwise legally entitled to process and republish. A public YouTube URL does not itself grant republication rights.
