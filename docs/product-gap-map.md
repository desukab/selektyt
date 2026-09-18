# Product gap map

Where SelekyT is a finished product and where it is not, as of `3f9e78b`.

The distinction this map exists to keep clear: **a stage whose code exists and
whose unit tests pass is not the same as a stage a user can rely on.** Each row
says what was actually exercised on this host, not what the code is capable of.

Host: Linux x86_64, rustc 1.98.1, node present (no npm), ffmpeg/ffprobe present,
python3 + faster-whisper present. **yt-dlp, ollama, silero and sherpa-onnx are
not installed here.** Disk was at 99% full during the final pass, and the real
run's output directory has since been deleted to free space — the observations
below were made while the run was live, not re-derived afterwards.

## INPUT

| Capability | Status | Basis |
| --- | --- | --- |
| Local video file | complete and verified | a real 535.5s local source ran the whole pipeline |
| YouTube URL | usable but unverified here | `yt-dlp` is not installed; `--doctor` correctly reports it OPTIONAL, and the code path exists, but no URL source was fetched in this environment |
| Cookies for sign-in walls | code-reviewed only | `providers/youtube._cookie_args` passes the path as a subprocess list (no shell) and never echoes it; not exercised against a real sign-in wall, so the conversion of a browser header file is untested against live YouTube |

## TRANSCRIPTION

| Capability | Status | Basis |
| --- | --- | --- |
| faster-whisper, word timestamps + VAD | complete and verified | real transcription of the real local source |
| Chunked decode for long sources | implemented, not separately measured | `chunk_minutes` reaches the whisper provider and is surfaced in provenance as `chunking`/`chunks`; validated as `>= 1` before any network or ASR work. No multi-chunk real file was transcribed here to measure the boundary behaviour |
| sherpa-onnx fallback | not verified | no wheel + model on this host; the fallback is recorded in provenance when it runs, and it does not run here |
| silero VAD | not verified | model file unavailable; `fallback_reason` reports the energy backend instead |
| Forced language (`--language te`) | partially verified | language forcing is exercised through the transcription fixtures; no real non-English speech audio was available on this host |

The real source is TTS-generated from public-domain text. Transcription,
word timing and every downstream stage are exercised for real — but not on
natural human speech, so Whisper accuracy on real studio audio is not
measured here.

## EDITORIAL INTELLIGENCE

| Capability | Status | Basis |
| --- | --- | --- |
| Deterministic multi-signal scoring | complete and verified | `report.json` from the real run carries per-candidate scores and rejection reasons |
| Context / truth guard | complete and verified | observed rejecting candidates on the real run; unit-tested |
| Story-unit grouping (complete thoughts, not fixed windows) | complete and verified | `engine/story_units.py`, exercised by the real run |
| Pause-aware retention intervals | complete and verified | single source of both the render and the timeline; pinned by `test_cue_times_account_for_removed_pauses` |
| Ollama LLM editorial metadata | wired, untestable | CLI → pipeline → `attach_metadata`; ollama is absent, so it degrades to deterministic scoring by design. The web shell does not expose the URL at all — a UI gap, not a broken path |

Retention is encoded as editorial principles, not learned from a channel's
real analytics. Channel-specific learning remains on the roadmap.

## EDITING

| Capability | Status | Basis |
| --- | --- | --- |
| Dead-air removal between words | complete and verified | conservative trimming, not a fixed cut rate; verified in the real run's durations |
| Audio-analysis jump-cut stage | verified on generated media | the deterministic sample WAV and the fixture harness; **not re-run on the real 535s source** in the final pass because disk was at 99% |
| Pause classification + audio-truth fusion | complete and verified | `engine/pause_classifier.py`, `engine/audio_fusion.py`, exercised through fixtures |

## RENDER

| Capability | Status | Basis |
| --- | --- | --- |
| 16:9 Facebook cut | complete and verified | real MP4 decoded by ffprobe: duration, dimensions, frame rate and audio/video streams all checked |
| 9:16 vertical cut | complete and verified | as above |
| Subject-aware reframing | usable with limitations | the real source exercised the active-region estimation path; face tracking is unit-tested but whether a source yields a detectable face is source-dependent, and a scene with no clear subject can still reframe poorly |
| Render caching, quarantine, `.bak` recovery | complete and verified | interrupted renders recover and only the missing clip is re-rendered |

## EXPORT

| Capability | Status | Basis |
| --- | --- | --- |
| ASS captions, word-timed, burned in | complete and verified | real ASS files with distinct per-preset Style lines produced by the same code |
| SRT fallback | complete and verified | cue-for-cue parity with ASS pinned by tests, including after pause retiming |
| Per-cut CMX 3600 EDL | structurally verified | written from the same interval list the render concatenated; validated at write time on the source's real frame rate. **Not imported into a live NLE** — none is available here |
| Per-cut FCPXML 1.11 | structurally verified | as above; frame-grid tiling and rate handling unit-tested |
| Audio cutlist + FCPXML | structurally verified | CLI and Rust core agree on it byte-for-byte |

The honest line on NLE compatibility: the files are valid, self-consistent and
derived from the exact intervals that were rendered, but no editor on this host
has opened them.

## USER EXPERIENCE

| Capability | Status | Basis |
| --- | --- | --- |
| CLI | complete and verified | `--doctor` verified both pass and fail paths; every flag forwarded, pinned by tests |
| Web shell, local-file path | complete and verified | caption style, audio analysis and cookies now exposed; two styles produced genuinely different burned-in styling through `process_line` |
| Web shell, URL path | usable but unverified | the bridge forwards the URL and cookies; not exercised against a real YouTube fetch |
| Desktop shell (Tauri) | incomplete, by environment | compiles and passes clippy, but no binary has been packaged on any platform and the webview has never rendered (no webkit2gtk). See `docs/agent-worktrees.md` |

## The critical path

Can a normal user provide a real long-form video and reliably receive usable
short-form edited videos?

**Yes, for a local file, on a host with python3, ffmpeg and faster-whisper.**
Both the CLI and the web shell complete that path, and the outputs were
inspected as files, not just as metadata.

**Not demonstrated for a YouTube URL**, because `yt-dlp` is absent from this
host. That is the one input path no environment available here could verify.
