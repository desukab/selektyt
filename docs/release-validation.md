# Release validation record

What was actually run, what actually worked, what failed, and what was fixed.
Every claim below was made on this host against real files; nothing here is a
statement about what the code is *capable of*.

Host: Linux x86_64 (Azure datacenter), 8 cores. rustc 1.98.1, node (no npm),
ffmpeg/ffprobe, faster-whisper `small`, `yt-dlp 2026.08.19` via the project
venv. ~15 GB free disk held two real sources at once.

## Inputs actually run

| Input | Type | Result |
| --- | --- | --- |
| `https://archive.org/details/NASA_360_program5_HD` | URL, 24:29, two hosts on camera, genuine unscripted human speech, US-government public domain | **complete success** — 25 artifacts, QA `ok: true`, 0 errors |
| `https://archive.org/details/SVS-1335` | URL, 2:04, Mars visualization | **correctly rejected** — audio is digital silence (-91 dB), transcript empty |
| `out/web/uploads/source.mp4` | local file, 535.5s | used by the earlier web-shell passes that surfaced the EDL defect |
| `youtube.com/watch?v=…` | URL | **blocked at the network** — see below |

The URL runs went through the web shell (`POST /api/run_pipeline`), not just
the CLI, because the web shell is the path the three defects were found on.

## What actually worked

The full chain ran end to end on the NASA source through the web shell:

download → fingerprint → chunked transcription (5 chunks × 5 min) → story
units → editorial scoring → context guard → retention intervals → render 16:9
**and** 9:16 → ASS/SRT/JSON captions → per-cut EDL **and** FCPXML → QA.

Verified as files, not as metadata:

* `shorts/01.mp4` is 272×486, `facebook/01.mp4` is 720×486, both 24.89s with
  an audio stream. **The 9:16 claim on that shorts file was wrong**, and is
  defect 5 below: the source is anamorphic, so 272 pixels is not 9:16 in
  display space. Re-rendering the same clip after the fix gives 308×486 at the
  same SAR, which displays at 1232:2187 = 0.5633 against a 9:16 target of
  0.5625.
* All 6 exported timelines re-validated by hand after the run:
  `01..03.edl` VALID, `01..03.fcpxml` VALID.
* Record spans advance monotonically on the real multi-clip EDL
  (`02.edl`: frames `0→457`, then `457→512` — no overlap, no gap).
* `tools/editorial_audit.py` on the project: **ALL CHECKS PASS** — cuts land
  between words, no sentence is split, no shaved tail word, cue text matches
  transcript text, cues fit the render, silence share 3.6–14.7%.
* Hooks are real sentences a viewer can understand without prior context:
  "Welcome to the world's largest clean room.",
  "very much like the electronic camera you have at home."
* Pacing: avg 19.91s, 2.9% silence, 3.74 words/sec, hook latency 0.63s,
  payoff present in 3/3.
* Transcript quality on real human speech: 425 segments, names ("Jonathan"),
  numerals and humour transcribed correctly. No manual correction needed.
* Determinism: the web-shell run reproduced the earlier CLI run's selection,
  durations and cue counts exactly.

## What failed, and what was fixed

Five defects, all found by running real input rather than by reading code:

**1. yt-dlp progress corrupted the sidecar wire.** The web shell returned
`sidecar sent a non-JSON line: [archive.org] Extracting URL: …`. The sidecar
swaps `sys.stdout` for `sys.stderr`, but an *inherited child* writes to the
real fd 1 and bypasses any Python-level swap. `providers.youtube.download` now
points the child at fd 2 explicitly. Pinned by
`tests/test_youtube_download.py` and by `test_subprocess_progress_never_corrupts_the_json_stream`,
which uses a real child process — the case `print()` redirection cannot catch.

**2. A `.mpg` source was rejected after a successful download.**
`--merge-output-format mp4` only applies when yt-dlp actually merges streams; a
source whose best muxed format is already MPEG-PS is written as `.mpg`, and a
fixed `{mp4, mkv, webm, mov}` allowlist then rejected a download that had
succeeded. Discovery now accepts any file that is not `.json`/`.part`/`.temp`.
Pinned by `test_download_accepts_a_muxed_mpg_source`.

**3. A one-frame EDL record-span overlap.** Each event's `rec_in` was rounded
independently from `clip.timeline_in`, so the accumulated spans drifted a frame
and an event could *start* before its predecessor ended. The validator caught
it — `channel V: event 2 record span overlaps event 3` — but only after renders
and captions were already written. Fixed by advancing each event from the
previous event's snapped out-point on its own channel, which also keeps the V
and A events of one cut on a shared record in-point. Pinned by
`tests/test_edl_record_advance.py` (4 tests), which was **verified to fail on
the pre-fix code** with the original error string.

**4. A speech-free source reported the wrong cause.** `SVS-1335` has no audio:
transcription correctly returned zero segments, then the run ranked an empty
transcript and died with "No complete standalone stories passed the
editorial/context gate" — indistinguishable from an editorial rejection and
giving the user nothing to act on. The pipeline now fails as soon as the empty
transcript is in hand, naming the missing speech. Pinned by
`tests/test_speechless_source.py` (3 tests).

**5. The vertical render was not 9:16 on an anamorphic source.** Found while
writing this record, by reading back the rendered geometry rather than
trusting the code's intent: the NASA source is NTSC DV, 720×486 at SAR 8:9
(DAR 320:243), and `crop=ih*9/16:ih` cut 272 pixels — a count that is only 9:16
when pixels are square. The output carried the source's SAR, so it displayed
at 1088:2187 ≈ 1:2. Content was never distorted; the ratio was simply wrong,
and a platform that ignores container SAR would have shown a near-1:2 frame.
The crop width is now scaled by the sample aspect ratio
(`height*9*sar_h/(16*sar_w)`), so the *display* ratio lands on 9:16 while the
frame stays anamorphic and the pixels stay unscaled. For a square-pixel source
the two agree within the pixel-grid rounding, so the common case is unchanged.
Pinned by `tests/test_render.py`
::test_render_vertical_clip_is_9_16_in_display_space_for_anamorphic, whose own
assertion was verified against the pre-fix filter string on the same synthetic
source (0.4975 → fail, 0.5633 → pass), and by four geometry tests in
`tests/test_reframe.py`. Re-verified on the real source by re-rendering clip 01
vertically through the fixed path: 308×486 SAR 8:9 → 0.5633.

## Test counts

Full suite: **1034 passed, 12 skipped** (113 s), re-run 2026-09-20. The 12
skips are none of the
optional backends — they are all `tests/test_proxy_validation.py` gated on
the `SELEKYT_PROXY_MEDIA` env var, i.e. opt-in real-media measurements that
no-op on every run. (An earlier version of this note said the skips were
sherpa/silero/ollama; that was wrong, and the count has moved 10 → 7 since.)

New this pass: `tests/test_edl_record_advance.py` (4),
`tests/test_youtube_download.py` (3), `tests/test_speechless_source.py` (3),
plus 1 new test in `tests/test_server.py`, 4 new tests in
`tests/test_reframe.py` and 1 in `tests/test_render.py`. Total +16.

## What remains before this is a production-ready v1

In rough order of how much it would actually cost a user:

1. **YouTube is unreliable from this host, not unreachable.** Every `yt-dlp`
   variant (`--js-runtimes`, `--extractor-args player_client=android|ios|tv`, a
   Deno runtime) returned "Sign in to confirm you're not a bot" on the videos
   tried, while a plain `curl` to youtube.com returned 200. This is a block on
   the datacenter IP range, not a code defect. **One fetch has since been
   observed** — an unauthenticated android client, 640×360, 213 s — while a
   second video was blocked during the same session, so the block is per-video
   and intermittent. **No artifact from that fetch survives**, so it cannot be
   checked against anything committed: treat the URL path as verified against
   archive.org and as *partially observed* against YouTube. A residential user
   is probably unaffected; a cloud user needs `--cookies`. Either way,
   chapters, members-only and age-gated behaviour are entirely untested.
2. **No NLE has opened an exported timeline.** The EDL/FCPXML are
   format-valid and self-consistent, but import compatibility (Premiere,
   Resolve, FCP, Avid) is unclaimed. No NLE is installed here.
3. **No video upload in the web shell.** A video must already be inside the
   project tree, because `guardedPath` deliberately confines local media to the
   project root. The CLI has no such restriction, so the two entry points
   disagree about what a user can point at.
4. **Desktop shell is unbuilt.** Tauri compiles and passes clippy, but no
   binary has been packaged on any platform and the webview has never rendered
   (no webkit2gtk). Architecturally sound: a thin webview over two sidecars,
   with ffmpeg found via PATH, so the webview's process-permission surface is
   zero.
5. **Optional backends untested here.** sherpa-onnx, silero and ollama are
   wired and degrade cleanly by design, but none was installed, so their paths
   are unit-tested only.
6. **Editorial judgement is unmeasured at scale.** Three clips from one source
   passed the audit; that is evidence the machinery is sound, not that the
   editorial choices would survive comparison with a human editor across many
   sources and languages.
7. **Vertical reframing is verified on one anamorphic and a few synthetic
   sources only.** The SAR-aware crop is geometry, so it is provably correct or
   incorrect for a given (SAR, height) pair and is pinned that way — but
   whether the tracked subject stays well framed after the wider crop, on
   sources with real motion, is not measured. The 9:16 output of an anamorphic
   source is also sub-SD (308×486) because the source itself is; that is a
   property of the input, not a defect, but it means the vertical cut from SD
   footage is not a delivery-grade asset.
