# SelekyT Demo Runbook

**Date: 2026-09-19.** Every command in this runbook was run by the author on the
repo at `/home/azureuser/selektyt` and its real output is quoted verbatim below.
Where something was not measured, it says so.

---

## The one-line status of the venture

> **SelekyT has zero users, zero revenue, zero pilots, and zero investors. It has
> been run end-to-end exactly once, on one public-domain NASA video, producing six
> clips and twelve renders. No non-linear editor has ever opened one of its
> timelines. Everything below is a demonstration of that one run and the audits
> over it — not of a product in use.**

Say this first. Say it in the demo, not in a footnote. If you cannot say it, you
are not ready to demo.

---

## What this demo is, and what it is not

**It is** a verification walk over a real, finished artifact that already exists
in the repo: the NASA 360 Episode 5 run at `out/web/pipeline/project/`. Every
step below proves a specific, checkable property of that artifact, using tools
that were written to *falsify* the pipeline's output rather than to praise it.

**It is not** a live pipeline run. Re-running the pipeline end-to-end
(download 884 MB, transcribe 24:29 of speech on CPU, render twelve MP4s) costs
real wall-clock and ~952 MB of disk. This host has **4.6 GB free of 29 GB**
(`df -h /`, VERIFIED 2026-09-19) and **no GPU**, so one fresh run is possible but
two are not, and the transcription wall-time is **NOT MEASURED** — the author did
not re-run the pipeline to time it. Do not improvise a number on the call. If a
live run is required, start it at the beginning of the meeting and show the
artifact walk while it works.

The source is public-domain US-government material (`archive.org/details/NASA_360_program5_HD`),
which is why it is safe to demo and to screen-share. **VERIFIED** — the state
file records this URL as the run's source.

---

## Prerequisites — check these first

All of these were verified on this host. Run the doctor before you demo anything,
so a missing dependency is your news, not the audience's.

```sh
cd /home/azureuser/selektyt && .venv/bin/python -m selekyt --doctor
```

Real output, exit code 0:

```
SelekyT doctor
  python3            OK
  python             OPTIONAL
  ffmpeg             OK
  ffprobe            OK
  yt-dlp             OPTIONAL
  faster-whisper     OK
Ready.
```

What this proves: `python3`, `ffmpeg`, `ffprobe` and `faster-whisper` are present
and the pipeline can transcribe. `yt-dlp` is OPTIONAL — it is only needed for URL
sources, and the demo artifact is already downloaded, so its absence would not
stop you.

**If the doctor fails:** `python` (unversioned) being OPTIONAL is fine. If
`ffmpeg`/`ffprobe` are missing, stop — the demo cannot run. If `faster-whisper` is
missing, the artifact walk still works (nothing below needs a model) but you
cannot demo a live transcription, so say so.

---

## Step 0 — Show what the CLI surface is

```sh
cd /home/azureuser/selektyt && .venv/bin/python -m selekyt --help
```

Real output (exit code 0), truncated to the parts worth reading aloud:

```
usage: selekyt [-h] [--out OUT] [--model MODEL] [--language LANGUAGE]
               [--limit LIMIT] [--chunk-minutes CHUNK_MINUTES]
               [--beam-size BEAM_SIZE] [--cpu-threads CPU_THREADS]
               [--caption-style {minimal,punch,spotlight,noir}]
               [--cookies COOKIES] [--doctor] [--ollama-url OLLAMA_URL]
               [--audio-analysis]
               [--vad-backend {energy,ffmpeg,silero,whisper}]
               [--silence-threshold SILENCE_THRESHOLD]
               ...
               [source]

Turn long-form video into retention-aware social cuts.
```

What this proves: the product is a real, installed, documented CLI with a
one-command entry point — `selekyt ./my-talk.mp4 --limit 6` as the README puts
it. There is no sign-in, no dashboard, no credit balance: it is local-first.

The honest companion line: **there is no packaged desktop binary and no video
upload in the web shell** (`docs/product-gap-map.md`, G3/G4, VERIFIED). A demo
audience sees a CLI. For a creator that is a defect; for a professional editor it
is acceptable. Choose your audience accordingly — see `docs/PILOT_PROGRAM.md`.

---

## Step 1 — Show the artifact you are about to audit

Do not audit a black box. List it first.

```sh
ls out/web/pipeline/project/
```

Real result:

```
captions/            pacing_report.json   timeline/
content_pack/        pipeline_state.json  transcript.json
content_pack.json    qa.json              transcription/
edit_plan.json       report.json
editorial_report.json  selections.json
facebook/            shorts/              source/
```

Then the deliverables, which is what an audience came to see:

```sh
ls out/web/pipeline/project/facebook out/web/pipeline/project/shorts out/web/pipeline/project/timeline out/web/pipeline/project/captions
```

Real result: **6 MP4s in `facebook/` (864x486, 16:9), 6 MP4s in `shorts/`
(308x486 storage, anamorphic display 1232:2187 ≈ 9:16), 6 `.edl` + 6 `.fcpxml`
in `timeline/`, and `.ass` + `.srt` + `.json` captions per clip.** All
measurements in the next step.

The provenance line worth saying out loud: **one 24:29 source became 6 selected
story units, 12 rendered cuts, 12 timeline files and 18 caption files, and the
source speech was never rewritten** — `report.json` states the policy as
`"retention-aware editorial heuristics; source speech is not rewritten; no
fabricated claims"` and every `content_pack.json` item carries
`source_based: true`. VERIFIED.

---

## Step 2 — The editorial audit: what was selected, and is it clean?

```sh
cd /home/azureuser/selektyt && .venv/bin/python tools/editorial_audit.py out/web/pipeline/project
```

Real output, exit code 0:

```
clip 01     PASS  silence share 14.7%; rendered 24.89s, 20 cues
clip 02     PASS  silence share 10.3%; rendered 17.14s, 13 cues
clip 03     PASS  silence share 3.6%; rendered 16.45s, 12 cues
clip 04     PASS  silence share 7.6%; rendered 14.01s, 11 cues
clip 05     PASS  silence share 40.8%; rendered 17.68s, 10 cues
clip 06     PASS  silence share 17.9%; rendered 9.08s, 8 cues
cross-clip  PASS  no duplicated sentences

ALL CHECKS PASS
```

What this proves:

- **The selections are watchable.** Read the `silence share` column carefully:
  it is **not a measure of audio at all**, and clip 05's 40.8% is not dead air.
  The metric is computed from transcript word timings over the edit plan's
  intervals and never decodes audio (VERIFIED — `tools/editorial_audit.py:check_silence_share`,
  lines 205–219). I probed the contested window directly: the 6.26 s word-free
  span in clip 05 is a **music bed**, mean −31.4 dB, max −14.1 dB, spectral
  flatness 0.007–0.012, and 0.00 s of true silence. Re-transcribing that window
  standalone with the same `small`/int8/beam-1 model returns only the single
  word "Ever." — so the transcript is right and the pipeline dropped nothing;
  the metric's *name* is the defect. Full measurement in `docs/METRICS.md` A2.4.
  **Do not quote this column as a silence measurement in a demo.**
- **The actual acoustic floor, measured on the renders themselves**
  (`ffmpeg -af silencedetect=noise=-40dB:d=0.25` over all six `shorts/NN.mp4`,
  VERIFIED): five clips contain 0.00 s of silence; **clip 04 contains 0.66 s**
  (0.28 s at 3.25–3.54 s, 0.37 s at 6.75–7.13 s) of a 14.01 s render, i.e. 4.7%.
  So the honest one-line claim is "≤4.7% measured silence across the six
  delivered clips", not "zero". The earlier blanket figure of 0.00 s for all
  six was wrong — one probe in the earlier pass was run at `-v error`, which
  suppresses `silencedetect`'s output and returns an empty result that looks
  like "no silence found". That is exactly the kind of tool artifact that
  becomes a false measurement, and it is why every number here names its
  command.
- **No clip repeats another clip's sentence.** The cross-clip check is the one
  that catches the classic AI-clipper failure — five "clips" that are the same
  sentence re-cut. Six clips here are six distinct story units.
- **The captions and the render agree about length.** Cue counts are per clip, not
  a global guess.

The deeper editorial evidence is in `editorial_report.json`: six ranked story
units with per-candidate scores, hooks, payoffs, `story_type` labels
(`conflict`, `conflict`, `revelation`, `revelation`, `fact`, `story`) and a
human-readable `reason` string decomposing every score
(`opening=0.75; progression=0.95; payoff=1.00; ...`). **This is the artifact to
put on screen** — it is the machine showing its working, which is the entire
positioning. Read one `reason` line aloud.

Also worth showing: `report.json` records `candidate_count: 4000`,
`selected_count: 6`, `truncated_by_limit: 5`. Four thousand candidate story units
were scored to ship six. VERIFIED.

---

## Step 3 — The NLE interchange audit: the load-bearing step

This is the most important step in the demo and the one most likely to be
over-claimed. Read `docs/NLE_COMPATIBILITY.md` before you present it. Its
headline, verbatim: **"No NLE has ever opened a SelekyT EDL or FCPXML."**

```sh
cd /home/azureuser/selektyt && PYTHONPATH=/tmp/nlelibs:tools .venv/bin/python tools/nle_check.py out/web/pipeline/project
```

Real output, exit code 0. **92 checks, all PASS.** The lines that matter:

```
source frame rate                      PASS  NASA_360_program5_HD.mov @ 29.9700 fps
01 edl independent parse               PASS  1 event(s), 1 channel record(s), title='very much like the electronic camera you have at home.'
01 edl record spans advance            PASS  1 event(s), no overlap
01 edl media exists                    PASS  1 clip path(s)
01 timeline == facebook render         PASS  timeline 24.858s vs render 24.892s (1.00 frames)
01 timeline == shorts render           PASS  timeline 24.858s vs render 24.892s (1.00 frames)
01 source interval == selection        PASS  src 414.31-439.17s vs selection 414.30-439.16s (start 0.4f, end 0.4f)
...
01 fcpxml well-formed                  PASS  parsed
01 fcpxml version                      PASS  version="1.11"
01 fcpxml vocabulary                   PASS  all elements in the 1.11 set
01 fcpxml media relinkable             PASS  every asset src is an absolute URI to an existing file
01 fcpxml clips within asset           PASS  no clip reads past its asset
01 fcpxml spine advances               PASS  clips in non-decreasing start order
01 fcpxml duration == facebook render  PASS  spine 24.858s vs render 24.892s (1.00 frames)
...
ALL CHECKS PASS
```

**What this proves, and it is real:** the EDL is accepted by `pycmx`, an
*independent* CMX 3600 reader that is deliberately not SelekyT's own parser
(`tools/nle_check.py` loads it from `/tmp/nlelibs`, outside the project venv, so
SelekyT's dependencies cannot influence it — `pycmx` 1.5.1, VERIFIED present).
The FCPXML is well-formed 1.11 XML using only 1.11-vocabulary elements. Every
asset `src` is an absolute `file:///` URI to a file that exists. Three separate
code paths — the EDL emitter, the FCPXML emitter and the ffmpeg render chain —
agree with each other to within **1.63 frames** (the worst case, clip 02; four of
six clips agree to 0.00 frames). Nothing else in the environment provides this
evidence.

**What this does not prove, and you must say so:** none of these files has ever
been imported into Final Cut Pro, DaVinci Resolve, Premiere Pro or Avid. No NLE
is installed in this environment. The FCPXML has never been validated against
Apple's XSD schema (candidate URLs returned 404/403 — see `NLE_COMPATIBILITY.md`).
The vocabulary check is a guard over the elements SelekyT emits, not a claim that
Apple's schema was applied. **A file can pass every check above and still be
rejected by Final Cut Pro.**

The exact script to use, verbatim:

> "These timelines are structurally validated by an independent reader and their
> durations match the rendered MP4s to within two frames. They have never been
> opened in an NLE — not once, by anyone, in any product. The next step for this
> company is to install DaVinci Resolve's free tier and import one file. That
> costs nothing and we have not done it yet."

If that sentence is uncomfortable to say, the gap is in the work, not in the
sentence.

---

## Step 4 — Prove the renders are what you claim they are

Do not describe a video. Probe it.

```sh
ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,sample_aspect_ratio,display_aspect_ratio,avg_frame_rate -show_entries format=duration -of default=noprint_wrappers=1 out/web/pipeline/project/facebook/01.mp4
```

Real output:

```
codec_name=h264
codec_type=video
width=864
height=486
sample_aspect_ratio=1:1
display_aspect_ratio=16:9
avg_frame_rate=2997/100
codec_name=aac
codec_type=audio
avg_frame_rate=0/0
duration=24.891558
duration=24.891558
```

```sh
ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,sample_aspect_ratio,display_aspect_ratio,avg_frame_rate -show_entries format=duration -of default=noprint_wrappers=1 out/web/pipeline/project/shorts/01.mp4
```

Real output:

```
codec_name=h264
codec_type=video
width=308
height=486
sample_aspect_ratio=8:9
display_aspect_ratio=1232:2187
avg_frame_rate=2997/100
codec_name=aac
codec_type=audio
avg_frame_rate=0/0
duration=24.891558
duration=24.891558
```

What this proves:

- **The 16:9 cut really is 16:9** — 864x486 square-pixel, 29.97 fps, h264 + aac,
  24.89 s. A deliverable, not a promise.
- **The vertical cut really is 9:16 — but the storage dimensions will confuse a
  careful viewer, so get this right.** It is **308x486 storage with SAR 8:9 and
  DAR 1232:2187**. That is *anamorphic*: it displays as 9:16 (0.5633 vs the 0.5625
  target) without wasting encode resolution on padding. If someone asks "why is
  this not 1080x1920?", the answer is that the source is itself anamorphic
  (720x486, SAR 8:9, DAR 320:243 — VERIFIED by probing `source/`), and a naive
  `ih*9/16` crop produced a 1:2 display until the crop was scaled by the sample
  aspect ratio. `docs/product-gap-map.md` records this as a real bug found and
  fixed. **This is a good story to tell**: it shows the geometry was measured
  rather than assumed, and it is pinned by `tests/test_render.py`.
- **Audio is present in both presets**, which is the difference between a social
  cut and a broken render.

---

## Step 5 — The test suite

```sh
cd /home/azureuser/selektyt && .venv/bin/python -m pytest -q
```

Real output, exit code 0:

```
898 passed, 7 skipped in 112 s
```

What this proves: there is a large, green, fast suite. Say the number, then say
immediately what it does not cover — because the honest version is stronger than
the number:

- **Ten tests skip.** The meaningful ones are `test_sidecar_parity.py` (the only
  behavioural test of the Rust sidecar) and `test_web_harness.py` (the only
  end-to-end test of the HTTP layer). VERIFIED from the suite output and
  `docs/TECHNICAL_AUDIT.md`.
- **No test in the suite verifies that a caption track is actually burned into
  the pixels.** `tests/test_render.py:170-176` asserts the file exists and is
  non-empty. TECHNICAL_AUDIT calls this out by name.
- **The audit tools you just ran are not wired into CI.** `.github/workflows/ci.yml`
  runs `pytest` and nothing else. The green suite and the green audit are two
  different signals, produced by two different sets of tools, and you ran both.

---

## Step 6 — Show the editor's deliverable, then stop

Open `out/web/pipeline/project/timeline/01.edl` and `01.fcpxml` in a text editor.
Read the EDL out loud: source in/out, record in/out, a title that is the clip's
opening sentence. Then open `captions/01.srt` alongside `facebook/01.mp4` and play
it if the room allows.

Then open `editorial_report.json` again and leave it on screen during questions.
It is the best artifact in the repo for exactly the situation a demo turns into:
an expert asking "why this clip?"

---

## Anticipated failure modes and recovery

| Failure | Cause | Recovery |
| --- | --- | --- |
| `--doctor` reports `faster-whisper` missing | Transcription extra not installed (`pip install -e '.[transcription]'`) | The artifact walk needs no model. Run it and say the live-transcription path is unavailable today. |
| `nle_check.py` prints `SKIP` for the EDL parse | `pycmx` not on `PYTHONPATH` — it lives in `/tmp/nlelibs`, outside the venv, on purpose | Re-run with exactly `PYTHONPATH=/tmp/nlelibs:tools`. Without it the audit still runs; it just loses its most valuable check, which is the entire point of the tool. Do not present a `SKIP` as a pass. |
| `nle_check.py` fails a duration check by > 2 frames | A render was deleted or re-encoded after the timelines were written | The timelines are derived from the same interval list the render concatenated; re-rendering regenerates both. Delete the affected `facebook/NN.mp4` and re-run the pipeline, which checkpoints per clip and will only redo the missing one. |
| `editorial_audit.py` fails a silence-share check | Source with a music bed, cutaway, or any word-free-but-loud audio | **Do not call it silence.** The metric is computed from transcript word timings and never decodes audio — it measures word *coverage*, not acoustic silence. If challenged on clip 05's 40.8%, the measured answer is: that window is a music bed at mean −31.4 dB with 0.00 s of true silence (`docs/METRICS.md` A2.4). Say the metric's name overstates what it measures and that renaming it is on the list — do not offer it as a measure of dead air |
| `ffprobe` reports the shorts render as 308x486, someone calls it "not 9:16" | Anamorphic storage, SAR 8:9, DAR 1232:2187 | Show the `display_aspect_ratio` line. 0.5633 against a 0.5625 target. This is correct behaviour, documented in `product-gap-map.md`. |
| A question about YouTube | `yt-dlp` bot-blocks this datacenter IP range on most videos; **one fetch has ever been observed, and no artifact from it survives** | Say exactly that. The URL path is the same code YouTube uses and it is verified end-to-end against archive.org, but YouTube itself is only partially observed here. Do not claim it works. |
| A question about the web shell | `web/server.mjs` has **no authentication, no origin check, and a prefix-based path jail that is bypassable** (`docs/TECHNICAL_AUDIT.md`, defects #1–#3) | Do not demo the web shell on a shared network. Say plainly that the web layer is a local single-user tool today and would fail an enterprise security review. This run's `pipeline_state.json` contains no cookies (VERIFIED), but the code path persists them in general. |
| A request to show DaVinci Resolve or Premiere import | No NLE is installed | See the "what NOT to say" list. The honest answer is the experiment in `NLE_COMPATIBILITY.md` that has not been run. |

---

## What NOT to say

These are claims the demo cannot support. Each one has a true replacement that is
stronger because it is checkable.

1. **Do not say "it works in Resolve / Premiere / Final Cut."** No NLE has ever
   opened a SelekyT EDL or FCPXML. This is the single biggest unverified claim in
   the venture (`docs/NLE_COMPATIBILITY.md`, `docs/CUSTOMER_DISCOVERY.md` falsifier
   #2). *Say instead:* "structurally validated by an independent CMX 3600 reader,
   duration-verified against the render, never opened in an NLE."
2. **Do not say "validated against Apple's FCPXML schema."** Apple's XSD could not
   be retrieved; the check is a vocabulary guard over emitted elements. *Say
   instead:* "well-formed 1.11 XML using only 1.11-vocabulary elements."
3. **Do not quote a time saving.** No editor has ever used SelekyT. No hours-saved
   figure has ever been measured. **NOT MEASURED.** Any number is invented. *Say
   instead:* "we have not measured editor time; that is the point of the pilot."
   (See `docs/PILOT_PROGRAM.md`.)
4. **Do not quote retention, views, engagement or any distribution outcome.** None
   of the six clips has ever been published anywhere. **NOT MEASURED.**
5. **Do not say "our users" or "our customers."** Zero users. Zero revenue. Zero
   pilots. Zero investors. Use the past tense of the work, not the present tense
   of a business.
6. **Do not imply endorsement by NASA, archive.org, Blackmagic, Adobe, Apple or
   Descript.** The source is public-domain US-government material; using it is not
   an endorsement, and nothing about it may be presented as one. Competitors are
   cited in `docs/CUSTOMER_DISCOVERY.md` from their own published pages, which is
   research, not partnership.
7. **Do not claim a quality advantage over OpusClip, Descript or the NLEs.** No
   head-to-head comparison has been run. **NOT MEASURED.** The verified claim is
   narrower and still interesting: SelekyT is the only product in the intersection
   of *autonomous editorial selection* and *editable NLE timeline export* — and
   even that rests on reading competitors' product pages, not on testing them.
8. **Do not claim editorial judgement is good.** It has been exercised on exactly
   one video. `docs/AI_EXECUTION_STATE.md` records editorial judgement as
   unmeasured at scale. *Say instead:* "one run, six clips, one source — the
   sample size is one."
9. **Do not claim the captions are verified burned in.** The suite asserts the
   file exists, not that the pixels carry captions. You can *see* them by playing
   the MP4, which is the honest demonstration — do that rather than citing a test.
10. **Do not claim Avid support.** SelekyT emits neither AAF nor OMF
    (`NLE_COMPATIBILITY.md`). Not supported. Not planned in this demo.

---

## What to say instead — the three true claims

1. **One real run, end to end, on real unscripted human speech.** A 24:29
   two-host NASA episode, 4,236 word-timed transcript tokens across 425 segments
   decoded in 5-minute chunks, 4,000 candidates scored, six story units selected,
   twelve renders, twelve timeline files, eighteen caption files. Audited green
   by two tools built to falsify it.
2. **The machine shows its working.** Every selection carries a decomposed score,
   a hook, a payoff and a reason string. Every cut has provenance. The deliverable
   is a timeline the editor finishes, not a black-box clip. That is the
   positioning, and it is visible in `editorial_report.json` in a way no
   competitor's output is.
3. **The gap is known, named, and cheap to close.** One free DaVinci Resolve
   install and one import. Zero capital. The company has not done it yet, and
   saying so is more credible than claiming compatibility that has never been
   tested.

---

## Reproducing, in order

```sh
cd /home/azureuser/selektyt
.venv/bin/python -m selekyt --doctor
.venv/bin/python -m selekyt --help
ls out/web/pipeline/project/
.venv/bin/python tools/editorial_audit.py out/web/pipeline/project
PYTHONPATH=/tmp/nlelibs:tools .venv/bin/python tools/nle_check.py out/web/pipeline/project
ffprobe -v error -show_entries stream=codec_name,width,height,sample_aspect_ratio,display_aspect_ratio,avg_frame_rate -show_entries format=duration -of default=noprint_wrappers=1 out/web/pipeline/project/facebook/01.mp4
ffprobe -v error -show_entries stream=codec_name,width,height,sample_aspect_ratio,display_aspect_ratio,avg_frame_rate -show_entries format=duration -of default=noprint_wrappers=1 out/web/pipeline/project/shorts/01.mp4
.venv/bin/python -m pytest -q
```

All eight were run by the author on 2026-09-19. All eight exited 0.
