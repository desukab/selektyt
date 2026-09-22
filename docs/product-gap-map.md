# Product gap map

Where SelekyT is a finished product and where it is not, as of `3fc11e1`.

The distinction this map exists to keep clear: **a stage whose code exists and
whose unit tests pass is not the same as a stage a user can rely on.** Each row
says what was actually exercised on this host, not what the code is capable of.

Host: Linux x86_64 (Azure datacenter), rustc 1.98.1, node present (no npm),
ffmpeg/ffprobe present, python3 + faster-whisper present. `yt-dlp` is installed
through the project venv — `yt-dlp>=2026.8.19` is a base dependency, not an
extra — and is only absent from a bare `PATH`, where `--doctor` correctly
reports it OPTIONAL. ollama, silero and sherpa-onnx are not installed here.
Disk held ~15 GB free across this pass, enough to keep two real sources on disk
at once.

## INPUT

| Capability | Status | Basis |
| --- | --- | --- |
| Local video file | complete and verified | a real 535.5s local source ran the whole pipeline |
| URL source | complete and verified, except YouTube | a real rights-permitted URL (`archive.org/details/NASA_360_program5_HD`, US-government public domain) ran the whole pipeline through the web shell: download → transcript → selection → render → captions → EDL/FCPXML → QA, all green. YouTube specifically is **IP-bot-blocked from this host**: every `yt-dlp` variant (`--js-runtimes`, `--extractor-args player_client=android\|ios\|tv`, a Deno runtime) still returned "Sign in to confirm you're not a bot", while a plain `curl` to youtube.com returned 200. That is a block on the datacenter IP range, not a code defect — and the failure is surfaced as actionable text naming cookies |
| YouTube URL | usable but observed once, with no artifact kept | the URL path above is the same code YouTube uses. One full download through SelekyT's own CLI was observed in a past session (640×360, 213 s, 11.8 MB, unauthenticated android client), but **no artifact from that fetch exists in this repo**: the only URL-sourced project is `out/web/pipeline/project`, whose `pipeline_state.json` `source` is the archive.org URL below, and no `youtube`/`youtu.be` reference appears in any project's `report.json` or `pipeline_state.json`. The observation is therefore unverifiable from anything committed — it is recorded here as an un-artifacted session note, not as evidence, and YouTube-specific behaviour (chapters, members-only, age gates) remains untested |
| Cookies for sign-in walls | code-reviewed only | `providers/youtube._cookie_args` passes the path as a subprocess list (no shell) and never echoes it; not exercised against a real sign-in wall, so the conversion of a browser header file is untested against live YouTube |

## TRANSCRIPTION

| Capability | Status | Basis |
| --- | --- | --- |
| faster-whisper, word timestamps + VAD | complete and verified | real transcription of the real local source |
| Chunked decode for long sources | complete and verified | the real URL source was decoded and transcribed in chunks (5 × 5 min of genuine two-host human speech), with `chunking`/`chunks` carried in provenance. No boundary artefacts appeared in the transcript or in any downstream cut |
| sherpa-onnx fallback | not verified | no wheel + model on this host; the fallback is recorded in provenance when it runs, and it does not run here |
| silero VAD | not implemented | the backend is a stub: `analyze` raises and `is_available` reports False whatever the host has. `fallback_reason` reports the energy backend, which is the real default. Asking for `--vad-backend silero` now says "not implemented" and names the backends that work; implementing it needs a model file and an inference path this environment cannot exercise |
| Forced language (`--language te`) | partially verified | language forcing is exercised through the transcription fixtures, and real non-English audio has since been transcribed — Telugu (OpenSLR SLR66) and Polish (MLS test). The forcing works. The *quality* does not extend to every language: Telugu scores WER 151.08% on `small` / 91.54% on `medium` (0 and 1 of 50 clips inside a 25% bar) where Polish scores 11.01% on that same `small` model. So the gap is language fit, not the forcing path |

The real source used for the final pass is genuine unscripted human speech —
NASA 360 Episode 5, two hosts on camera, 25 minutes of it, fetched from a URL.
Transcription, word timing and every downstream stage are exercised for real on
natural speech. Whisper handled names ("Jonathan"), numerals and humour
correctly; no manual correction was needed. Earlier passes used a TTS-generated
source, which is still the basis of some fixture assertions and is fine for
timing, but it is not a measure of accuracy on real studio audio.

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
| 9:16 vertical cut | complete and verified, after one geometry fix | the real source's vertical render was read back with ffprobe and was **not** 9:16: the source is anamorphic (720×486, SAR 8:9, DAR 320:243) and a crop of `ih*9/16` pixels is only 9:16 for square pixels, so the output displayed at 1088:2187 ≈ 1:2. The crop width is now scaled by the sample aspect ratio so the *display* ratio is 9:16; re-verified on the real source (308×486 SAR 8:9 → 1232:2187 = 0.5633 vs a 0.5625 target). Pinned end to end by `tests/test_render.py` |
| Subject-aware reframing | usable with limitations | the real source exercised the active-region estimation path; face tracking is unit-tested but whether a source yields a detectable face is source-dependent, and a scene with no clear subject can still reframe poorly. The wider anamorphic crop is not re-measured for subject framing on sources with real motion |
| Render caching, quarantine, `.bak` recovery | complete and verified | interrupted renders recover and only the missing clip is re-rendered |

## EXPORT

| Capability | Status | Basis |
| --- | --- | --- |
| ASS captions, word-timed, burned in | complete and verified on synthetic media | the burn is now measured, not assumed: `tests/test_render.py::test_the_burned_captions_are_in_the_pixels` decodes the rendered MP4 and diffs it frame by frame against an uncaptioned control of the same source, requiring ink in the caption band and inside the safe area the ASS declares — on all four presets, with a vertical-crop variant, and mutation-checked by deleting the `subtitles` filter. The six corpus renders are covered only by the one real pixel comparison in `docs/RED_TEAM_FINDINGS.md` R11, not by the suite |
| SRT fallback | complete and verified | cue-for-cue parity with ASS pinned by tests, including after pause retiming |
| Per-cut CMX 3600 EDL | structurally verified, one real bug found and fixed | written from the same interval list the render concatenated; validated at write time on the source's real frame rate. A real multi-clip export hit that validator: independent per-event rounding of `timeline_in` let event 3 start one frame *before* event 2 ended, and the whole run failed at timeline export after renders and captions were already written. Fixed by advancing each event from the previous event's snapped out-point on its own channel; pinned by `tests/test_edl_record_advance.py`, which was verified to fail on the pre-fix code with the original error. **Not imported into a live NLE** — none is available here |
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
| Web shell, URL path | complete and verified, except YouTube | same code path as the CLI, exercised end to end against a real archive.org URL. Two real defects were found and fixed on this path, both specific to serving a download behind a JSON-RPC sidecar: yt-dlp's progress wrote to fd 1 and corrupted the wire (the caller saw `sidecar sent a non-JSON line`), and a source delivered as MPEG-PS `.mpg` was rejected because `--merge-output-format mp4` only applies when yt-dlp actually merges. Both pinned by `tests/test_youtube_download.py` and by a child-process wire test in `tests/test_server.py`. YouTube itself remains unfetchable from this host — see the INPUT row |
| Web shell, video upload | missing | only `/api/vad_analyze` accepts an uploaded file, and it is WAV-only. A video must be placed inside the project tree and referenced by relative path, because `guardedPath` deliberately confines local media to the project root. The CLI has no such restriction, so the two entry points disagree about what a user can point at |
| Desktop shell (Tauri) | incomplete, by environment | compiles and passes clippy, but no binary has been packaged on any platform and the webview has never rendered (no webkit2gtk). See `docs/agent-worktrees.md` |

## The critical path

Can a normal user provide a real long-form video and reliably receive usable
short-form edited videos?

**Yes — for a local file *and* for a URL, on a host with python3, ffmpeg,
faster-whisper and yt-dlp.** Both the CLI and the web shell complete the path,
the URL run used genuine unscripted human speech (NASA 360 Episode 5, 24:29),
and the outputs were inspected as files, not just as metadata: renders,
captions, per-cut EDL/FCPXML, QA and the editorial audit in
`tools/editorial_audit.py` were all green.

**Not demonstrated for a YouTube URL specifically.** The URL code path is
exercised end to end, but YouTube bot-blocks most videos from this host's IP
range. **One fetch has been observed** (an unauthenticated android client,
640×360, 213 s), while a second video was blocked during the same session, so
the block is per-video and intermittent rather than a hard IP ban — but **no
artifact from that fetch survives anywhere in this repo**, so it is an
un-artifacted session note, not evidence the path works. A user on a
residential IP should be unaffected; a user on a cloud VM will need
`--cookies`. That is the one input path this environment cannot verify.

Two further honest limits. The editorial gate has one observed failure mode
worth knowing about: a source with no detectable speech is rejected with
"No complete standalone stories passed the editorial/context gate" rather than
a message naming the actual cause (an empty transcript). That was hit on
archive.org's `SVS-1335`, whose audio is digital silence (-91 dB); the empty
transcript and the rejection were both correct, but the error does not tell a
user that their file has no speech track. And no NLE on this host has opened an
exported timeline, so format validity is claimed, not import compatibility.
