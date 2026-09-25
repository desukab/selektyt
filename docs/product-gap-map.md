# Product gap map

Where SelekyT is a finished product and where it is not, as of `466daf0`.

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
| YouTube URL | usable but observed once, with no artifact kept | the URL path above is the same code YouTube uses. One full download through SelekyT's own CLI was observed in a past session (640×360, 213 s, 11.8 MB, unauthenticated android client), but **no artifact from that fetch exists in this repo**: the only URL-sourced project is `out/web/pipeline/project`, whose `pipeline_state.json` `source` is the archive.org URL below, and no `youtube`/`youtu.be` reference appears in any project's `report.json` or `pipeline_state.json`. The observation is therefore unverifiable from anything committed — it is recorded here as an un-artifacted session note, not as evidence, and YouTube-specific behaviour (chapters, members-only, age gates) remains untested. **Source quality is now a deliberate policy, not yt-dlp's implicit ranking** (`providers/youtube.select_format_spec`): the reported 640×360 baseline was traced to `-f bv*+ba/b` matching *nothing* and falling through to itag 18, and the replacement picks the tallest native, locally-decodable stream at or under 1080 while excluding the streams YouTube labels AI-upscaled. It is pinned against the ladder that baseline reported — 41 tests, two of which resolve the emitted spec through yt-dlp's own `build_format_selector` — but **no live YouTube download has verified it**: from this host every client still hits the sign-in wall, and node is v18 against yt-dlp's required ≥ 22, so no JavaScript runtime is available either. Validation needs the device that produced the baseline |
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
| Candidate scan covers the whole source | complete and verified, after one structural defect | the scan used to `return` at a fixed candidate cap, and candidates are built in start order, so the cap deleted the tail outright rather than bounding the cost: the last 35.6% of `nasa360-ep5` and 30.3% of `emwcon-panel-2018` generated **zero** candidates, and at the product's 1–3 hour target the cut landed near the 18-minute mark of a 3-hour source — most of the video was never searched. The cap is now a coverage budget spent on strided starting positions, so degradation went from "the tail is unreachable" to "coverage is coarser everywhere". Measured: both affected sources go 4000 → 5782 / 6758 candidates at 64.4% / 69.7% reach → 100%, and on normal transcript density the stride is 1, so the output is a strict superset of the old one. Pinned by `tests/test_editorial_coverage.py`, which holds both properties at once — a 93-minute source must yield candidates in its final 10%, and a sub-second-segment transcript must stay bounded *and* still reach its tail. What this does **not** establish is that the newly reachable clips are better editorially; the corpus has no ground truth for that |

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
| Subject-aware reframing | complete and verified on the corpus, with measured limits | **was** genuinely broken, and the break was silent. The old face path (`cv2.CascadeClassifier`) is gone in OpenCV 5, which `opencv-python-headless>=4.9` resolves to on a clean install, so every vertical output fell back to the Canny edge-density centroid — which on a two-shot lands in the empty middle between the speakers. The corpus's own short04 is the evidence: two speakers detected at 0.94 confidence for the whole clip while the crop sat on nobody, 151 of 178 sampled frames losing *every* face. The path is now a YuNet detector via `cv2.FaceDetectorYN` (survives OpenCV 5), MIT-licensed weights from `opencv_zoo`, 232 KB, loaded lazily and optional — absent weights fall back to the edge path rather than failing. Two further defects were found only by rendering the deliverable, because source-space metrics could not see either: the crop expression was offset from the planner's fraction by up to a full crop width (406 px) — the instrument reported a centred subject while the shipped frame held nobody — and the speed limiter's duplicate-drop collapsed a held note into a linear ramp, so the crop began sliding toward a speaker who had not started talking. Both pinned by `tests/test_reframe_regression.py`, whose rendered tests use an oracle detector deliberately separate from the planner's so a shared blind spot cannot become a shared pass. After the fix, on the five corpus shorts: rendered face presence 0.053→1.000, 0.905→0.989, 0.295→0.968, 1.000, 0.962→1.000; short04's source-space framed fraction 0.007→1.000 with 151→0 all-faces-lost frames. Numbers and method: `corpus/wer/runs/reframe-before-after-2026-09-23.json`. **Limits that remain, stated as limits:** one source video (a single briefing, 1280×720) was exercised, so behaviour on handheld footage, whip pans, crowds and close interviews is inferred, not measured; the frames were *measured*, never *viewed* — this environment cannot read images, so visual QA is a frame-level detector score and nothing is claimed about composition aesthetics; the oracle shares YuNet's 48 px floor, so a face rendered smaller than that counts as absent and a genuinely-too-tight crop would read as a miss for the wrong reason; multi-speaker switching and hard cuts are tested on synthetic trajectories, the corpus having no shot change of its own; and the wider anamorphic crop is still not re-measured for subject framing on sources with real motion |
| Render caching, quarantine, `.bak` recovery | complete and verified | interrupted renders recover and only the missing clip is re-rendered |

## EXPORT

| Capability | Status | Basis |
| --- | --- | --- |
| ASS captions, word-timed, burned in | complete and verified on synthetic media; measured on the real renders wherever the renders exist | the burn is measured, not assumed: `tests/test_render.py::test_the_burned_captions_are_in_the_pixels` decodes the rendered MP4 and diffs it frame by frame against an uncaptioned control of the same source, requiring ink in the caption band and inside the safe area the ASS declares — on all four presets, with a vertical-crop variant, and mutation-checked by deleting the `subtitles` filter. That test runs in any clone. The corpus's own renders are a separate matter and the earlier claim here mis-framed it: `.gitignore` commits only the corpus's *text* artifacts (transcript, selections, edit plan, captions, timelines) on the grounds that several hundred MB of regenerable binary per run makes the repo unclonable for no evidentiary gain — so the renders and the source media are not in the repository and a suite test cannot cover them. What exists instead is `tests/test_render.py::test_the_committed_renders_carry_the_burn`, which performs R11's comparison (render frame against the source frame it was cut from, at the cue times the render's own sidecar declares) over every clip of every run whose media is at hand, and **skips in a bare clone** with a message naming the tool that regenerates what it needs. It is a reproducible measurement for the environment that produced the corpus, not a CI guard, and is documented as such. Its ink floor is calibrated against a negative control built from the real artifacts: re-cutting pentagon's clip from its source with no subtitles filter measures 0.047% of the band changed, the floor is 0.15%, the faintest real burn measured 0.53%. Neither test claims legibility or correct script shaping — a font with no glyphs for the script still produces ink (empty boxes), and the Telugu run's ink is verified present and positioned, not verified readable. That disclaimer is not hypothetical and is no longer the resting state: the shipped Telugu render **was** tofu. It was burned on a host with no font covering the Telugu block, and its cue band is 13 evenly-pitched hollow rectangles — verified against a burn of the same cue text under a deliberately-missing font, which was pixel-identical, and against a burn after installing `fonts-noto-core`, which renders real Telugu with varying glyph widths and gaps between clusters. The host's font set has since been fixed, but a host with the wrong fonts still produces this silently, because libass's `.notdef` fallback puts ink in the frame. So the pipeline now refuses rather than shipping it: `engine/render._check_caption_coverage` scans fontconfig's coverage once per process and raises, naming the uncovered script and the package that fixes it, before any encoder runs. Pinned by `tests/test_render.py::test_render_refuses_captions_the_host_cannot_draw` (a Private Use Area codepoint, which no conforming font carries, so the test does not depend on this host's installed fonts) and `test_render_burns_telugu_when_a_font_covers_it`, which reports the coverage it found and asserts either real type or a refusal — never a silent burn. Both are mutation-verified, including that the existing ink test cannot see this class of defect, because ink is ink whether or not it is readable. The stale-*style* case, where the shipped pixels carry a different style from the sidecar, is pinned separately by `tests/test_caption_style_cache.py` |
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
| Web shell, video upload | complete and verified for intake, not for a full-length source | `/api/upload` now streams a real video or audio file into `out/web/uploads` and hands back the path the cuts control already used, so an upload and a typed path are interchangeable from there on. The body is never buffered (a multi-GB master goes straight to a `.part`, renamed only once the transfer is whole), `guardedPath` admits the result as a source with **no boundary widening**, and ffprobe gates the file: a text file is refused rather than left for the next caller to transcribe. Verified against a real ffmpeg-generated MP4 carrying both streams, plus the failure paths — non-media refused, a repeated name de-clobbered, a cross-origin POST rejected, and a connection that dies mid-body leaving no partial behind. Two things this does **not** establish: the largest body the route will accept is unmeasured here (the 32 GiB cap has never been approached — disk has ~1.3 GB free for most of this work), and the new intake has never been exercised with a genuine 1–3 hour master end to end through the pipeline, only with a 3 s file through the route itself |
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

Two further honest limits. The editorial gate used to have one observed
failure mode worth knowing about: a source with no detectable speech was
rejected with "No complete standalone stories passed the editorial/context
gate" rather than a message naming the actual cause. That was hit on
archive.org's `SVS-1335`, whose audio is digital silence (-91 dB); the empty
transcript and the rejection were both correct, but the error did not tell a
user that their file had no speech track. **It is fixed and no longer a
limit:** an empty transcript now fails before ranking, and the message is
chosen by what the audio actually showed — a peak-level measurement separates
a genuinely silent source from a damaged container, and reports the number so
the diagnosis is checkable rather than merely confident. Pinned by
`tests/test_speechless_source.py`, which asserts the expensive downstream
stages are never reached and that the wording does not imply the gate judged
anything.

A second failure mode with the same misleading message, found in the committed
corpus rather than on a live run: the Hindi interview carries **no terminal
punctuation anywhere** — no danda, no period, no question mark — so the
candidate scan had no signal that a thought had ended and closed no span at all
(52.76 s accumulated against a 53.00 s relief valve). The source reported that
no story passed the gate, when in fact nothing had been constructed to be
judged. **Also fixed:** the scan falls back to closing a span at its length and
word floors when no terminal mark is present, so the source is searched instead
of failing structurally. Verified against the committed transcript by executing
the pre-fix module alongside the current one (0 candidates → 6). The source
still ships no clips — the spans it now finds measure 0.53 best confidence
against a 0.75 floor, and are refused for a stated, measurable reason rather
than a structural one. Recorded in `docs/CORPUS_BENCHMARK_STATUS.md` and pinned
in `tests/test_unicode_pipeline.py`.

And no NLE on this host has opened an exported timeline, so format
validity is claimed, not import compatibility.
