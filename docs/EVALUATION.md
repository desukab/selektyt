# SelekyT — Evaluation Protocol

Prepared 2026-09-19. This document is a **protocol**, not a results report. Almost nothing in it
has been measured, and it says so.

**Company status, stated plainly:** SelekyT has **zero users, zero revenue, zero pilots, and zero
investors**. Nothing in this document describes traction, customer feedback, or observed usage,
because none exists.

## Evidence conventions used throughout

Every claim below carries one marker. These are the only four values a claim may take.

| Marker | Meaning |
| --- | --- |
| **VERIFIED** | I ran it on this host on 2026-09-19. The exact command is given so it is reproducible. |
| **RESEARCHED** | Retrieved from a live public URL on 2026-09-19. URL and retrieval date given. |
| **ASSUMPTION** | My inference or a modelling input. Discardable. Not evidence of anything. |

The one further label used for corpus candidates is **UNCHECKED** — a URL I could not confirm
responds, or a property I could not confirm. It is not a negative result; it is the absence of one.

---

## 1. The honest starting position

SelekyT has been evaluated end-to-end on **exactly one source**: NASA 360 Episode 5.

Everything in this section is VERIFIED today, on the artifacts that exist:

```
ffprobe out/web/pipeline/project/source/NASA_360_program5_HD.mov
  duration=1469.769770  720x486  SAR 8:9  29.97 fps  h264  883,894,825 B
```

- Source duration **1469.77 s (24:29)**, 4:3 NTSC SD, 29.97 fps, PCM stereo audio. VERIFIED.
- The run produced **6 clips, each in 16:9 and 9:16**, i.e. 12 rendered files plus per-cut
  EDL, FCPXML 1.11, SRT, ASS and a content-pack JSON. VERIFIED (directory listing + audit runs below).
- `tools/nle_check.py out/web/pipeline/project` → **86 PASS, 0 FAIL, 6 SKIP** (the 6 skips are
  "edl independent parse", skipped on this run because `pycmx` was not then installed — it
  is now a declared dependency, so those 6 execute today), exit 0. VERIFIED at the time.
- `tools/editorial_audit.py out/web/pipeline/project` → **6/6 clips PASS, cross-clip PASS**. VERIFIED.
- `.venv/bin/python -m pytest -q` → **1080 passed, 12 skipped in 145 s** (re-run 2026-09-20;
  every skip is an environment skip on absent optional backends). VERIFIED.

**What that does and does not establish.** Six clips passing two audits and 1080 unit tests passing
is evidence that the *machinery* is sound and self-consistent. It is not evidence that a human
editor would ship those cuts, and it is not evidence that any of it generalises beyond one tape.

### 1.1 The most important gap: transcription accuracy is measured for clean English and *nothing else*

This section originally read "transcription accuracy is unmeasured *at all*" and asserted that no
WER tooling existed in the codebase. That was true when it was written and is **now false**: a
repo-wide search today finds `tools/wer_bench.py`, 17 committed run records under
`corpus/wer/runs/`, and the manifest they were scored against under `corpus/wer/librispeech-clean/`.
The correction is recorded here rather than silently rewritten because the protocol below is what
made the measurement possible.

**What is now measured.** 50 professionally transcribed LibriSpeech test-clean clips (6.6 min of
audio, 1036 reference words, 5686 reference characters) decoded with the *production* parameters
(`int8`, VAD on, `condition_on_previous_text` off, beam 1) scored **WER 3.38% / CER 1.35%** —
35 edit operations over 1036 words. Reproduced three times on three independent runs
(`c9-small`, `redteam-small-verify`, `verify-current-small`) with identical WER, CER, clip count,
audio duration and reference-word count. The manifest regenerates byte-identically from a fresh
download of the pinned OpenSLR tar, and the ratio behind the rate is now recorded in each run
record alongside it, so the figure is checkable by arithmetic rather than quotable.

**What that still does not establish.** LibriSpeech test-clean is clean, read, studio English. The
gap that matters is unchanged by it: no video source in the corpus has a ground-truth transcript,
so the accuracy of the transcript that every downstream *editorial* decision depends on is still
unmeasured in-domain. The statement "`small` handled the NASA names, numerals and humour correctly
with no manual correction" in `docs/BUSINESS_MODEL.md` is still an **impression**, not a
measurement. The cases that actually break — non-English, crosstalk, compressed audio and field
noise — are exactly what test-clean does not probe; the Telugu coverage collapse recorded in
`docs/CORPUS_RESULTS.md` is the concrete evidence that they break.

---

## 2. (a) The evaluation corpus

### 2.1 Design axes

A one-source evaluation cannot probe any of these. The corpus must vary all five deliberately:

| Axis | Reference value (today) | Corpus must also contain |
| --- | --- | --- |
| **Genre** | two-host unscripted magazine dialogue | single-host monologue; adversarial interview; multi-speaker panel/hearing; narration-over-b-roll; a music-or-silence-dominated source (the pipeline already has a failure mode for this — see `test_speechless_source.py`) |
| **Language** | English only | at minimum Spanish, Portuguese, German, French; one non-Indo-European language; and code-switching. The context guard, sentence splitter and caption line-breaking are all language-sensitive |
| **Frame rate** | 29.97 only | 23.976, 24, 25, 50, 59.94. EDL/FCPXML timecode math and the "timeline == render" frame-tolerance check are all rate-dependent |
| **Aspect ratio / geometry** | 4:3 source, SAR 8:9 (anamorphic), padded to 16:9 and cropped to 9:16 | square-pixel 16:9; native 9:16 portrait; 21:9; mixed-aspect b-roll; sources already narrower than 9:16 (the code has a clamp for this in `engine/reframe.py:vertical_crop_filter`) |
| **Media quality** | SD 720×486, H.264, 3.3 Mb/s video + 1.5 Mb/s PCM | 1080p and 4K, H.265, low-bitrate compression artefacts, AAC at low bitrate, non-speech background music, wind noise. `docs/BUSINESS_MODEL.md` §9 calls ingest of 4K/H.265 "3–10× unmeasured" — this axis is the largest unquantified risk in the whole product |

### 2.2 Concrete candidate sources, actually checked today

Each row below was fetched on **2026-09-19** with a ranged GET (`curl -r 0-1023` or larger) unless
noted. "Fetch" reports the HTTP status and content type that came back. **A 206/200 confirms the
URL resolves and serves bytes; it is not a full download and not a checksum match** (except C1,
which is a byte-exact match — see below).

| # | Candidate | Fetch result today | Rights basis |
| --- | --- | --- | --- |
| **C1** | `https://archive.org/download/NASA_360_program5_HD/NASA_360_program5_HD.mov` | **206**, `video/quicktime`, size 883,894,825 B — **byte-exact match to the local reference file** | NASA; US-government work, public domain. `archive.org/metadata` shows `creator: NASA`, `licenseurl: None` (no explicit licence posted — the PD status is from the creator) |
| **C2** | `https://archive.org/download/hrs12RES1324_09022b/hrs12RES1324_09022b.mp4` | **206**, `video/mp4`, 332,197,458 B | House Natural Resources Committee hearing; `licenseurl: http://creativecommons.org/licenses/publicdomain/` |
| **C3** | `https://archive.org/download/mike-gill-interview-with-ben-sarro-of-wntk-and-wuvr/Mike%20Gill%20Interview%20with%20Ben%20Sarro%20of%20WNTK%20and%20WUVR.mp4` | **206**, `video/mp4` | `licenseurl: https://creativecommons.org/publicdomain/mark/1.0/` (filename must be URL-encoded — the raw form is rejected) |
| **C4** | `https://images-assets.nasa.gov/video/NHQ_2019_0311_Go%20Forward%20to%20the%20Moon/NHQ_2019_0311_Go%20Forward%20to%20the%20Moon~large.mp4` | **206**, `video/mp4` | NASA; US-government public domain. **A `.srt` sidecar exists at the same path minus the suffix — I read its text, so this item carries caption ground truth** |
| **C5** | `https://upload.wikimedia.org/wikipedia/commons/.../Alice_in_Wonderland_(1903).ogv` (480×360) | **206**, `application/ogg` | Wikimedia Commons; 1903 film, public domain |
| **C6** | `https://upload.wikimedia.org/wikipedia/commons/.../-Avançamos!_Depoimento...webm` (1920×1080, pt-BR) | **206** | Wikimedia Commons, Portuguese-language 1080p. **Per-file licence must be read at use time** — Commons licence is per-upload, not per-category |
| **C7** | `https://upload.wikimedia.org/wikipedia/commons/4/47/Astrophysics_Holiday_Vertical_Video_(SVS14800...).webm` (1080×1920) | **206** | NASA SVS vertical deliverable — **native 9:16 geometry**, the aspect axis |
| **C8** | `https://upload.wikimedia.org/wikipedia/commons/c/c4/OOI_Overview_CAPTIONS_Vertical.webm` (1080×1920) | **206** | NSF/OOI, vertical, has captions in the name; candidate for caption/WER ground truth on portrait video |
| **C9** | `https://www.openslr.org/resources/12/test-clean.tar.gz` and `.../12/dev-clean.tar.gz` | **206**, `application/x-gzip` | LibriSpeech, **CC BY 4.0** — *audio-only*; WER measurement, not pipeline evaluation |
| **C10** | `https://dl.fbaipublicfiles.com/mls/mls_german.tar.gz`, `.../mls_spanish.tar.gz`, `.../mls_french.tar.gz`, `.../mls_dutch.tar.gz`, `.../mls_italian.tar.gz`, `.../mls_polish.tar.gz` | all **206** | Multilingual LibriSpeech, **CC BY 4.0** — *audio-only*; the non-English WER axis |
| **C11** | `https://d34w7g4gy10iej.cloudfront.net/video/2506/DOD_111099339/DOD_111099339.mp4` | **206**, `binary/octet-stream` (URL scraped from the DVIDS page HTML at `https://www.dvidshub.net/video/968174`) | DVIDS / US Department of Defense, public domain. The only 1080p-class verified video source |

### 2.3 Candidates that are UNCHECKED, and why

Stated explicitly rather than papered over:

| Item | Status | What I actually saw |
| --- | --- | --- |
| **DVIDS bulk discovery** (`api.dvidshub.net/feeds/video/`) | UNCHECKED | `{"errors":["Bad Request - No API key was provided"]}`. The human-facing page HTML works (200) and C11 was scraped from it, but programmatic discovery of a *diverse* DVIDS set needs an API key. |
| **TED-LIUM** | UNCHECKED | Could not resolve a live URL. `https://www.openslr.org/51/` returns "Resource not found: 51"; `https://www.openslr.org/resources/51/TEDLIUM_release3.tgz` returns 404; SLR27 turned out to be Cantab *language models*, not audio. |
| **Mozilla Common Voice** | UNCHECKED | The site responds, but the unauthenticated `commonvoice.mozilla.org/api/v1/datasets` endpoint returns 400. No dataset URL verified. |
| **Pansori-TEDxKR (Korean)** | UNCHECKED | `https://storage.googleapis.com/pansori/corpus/pansori-tedxkr-corpus-1.0.tar.gz` returns **403**. |
| **archive.org FRANCE24 / TELECONCO items** | **EXCLUDED on rights** | These were the most obvious non-English video candidates, but their `archive.org/metadata` records show `licenseurl: None`. Not rights-cleared; do not use. |
| **Frame rate of every candidate above** | UNCHECKED | I confirmed dimensions for Commons items via the API, but **I did not probe frame rate for any candidate**. The reference (29.97) is the only verified frame rate. Confirm with `ffprobe` at ingestion. |
| **A vertical 9:16 talking-head interview** | UNCHECKED | C7/C8 are portrait, but they are narration/explainer pieces. A genuine portrait *interview* — the use case the 9:16 reframe's subject-tracking is built for — is not verified. |
| **4K / H.265 / high-bitrate sources** | UNCHECKED | No verified candidate at 4K or H.265. This is the axis `docs/BUSINESS_MODEL.md` §9 flags as the largest unquantified risk, and it remains open. |

**Discovery notes for whoever runs this** (verified today, and non-obvious):
- Wikimedia Commons **MIME search is disabled** (`action=query&list=allimages&aimime=video/ogg` →
  `mimesearchdisabled`). Use `generator=categorymembers` on `Category:Videos`, or
  `list=search&srsearch=filetype:video ...&srnamespace=6`, then resolve each file's real URL with a
  second `prop=imageinfo` call.
- `archive.org` filenames with spaces **must be URL-encoded** or the request is rejected as
  malformed / returns 404.
- GCP compute pricing is excluded from every cost figure in this repo for the same reason:
  `cloud.google.com/compute` pricing pages render client-side and returned no quotable table.

### 2.4 The minimal first corpus

A defensible first target: **20 sources, ≈20 min each (≈400 source-minutes)**, chosen to cover
every row of §2.1 with at least 3 sources per axis, and to include at least:

- 3 multi-speaker adversarial sources (C2 class)
- 3 single-host monologues
- 2 sources with substantial non-speech or silence
- 4 non-English (≥2 non-Roman-script if obtainable)
- 3 native 9:16 / portrait (C7, C8 class)
- 1 each of 23.976, 25, 50, 59.94 fps
- 1 4K/H.265 source — **required even though it is UNCHECKED today**, because it is the biggest
  unquantified risk
- plus the WER corpora C9/C10, which are audio-only and score the transcription layer separately

---

## 3. (b) Metrics, by dimension

Each metric below is specified so it can be computed by a script, and each has a **threshold
decided before any results exist** (§4). Metrics that require human judgement are labelled as such
and are the only ones that can close the demand-side gap.

### D1 — Editorial quality (machine-side)

Computed from artifacts that already exist, so the reference run can be scored today and every
corpus item scored the same way.

| Metric | Definition | Source of the number |
| --- | --- | --- |
| `silence_share` per clip | fraction of the *kept* timeline that is **word-free**, per `tools/editorial_audit.py:check_silence_share` — computed from transcript word timings, never from decoded audio | MEASURED on all 27 committed clips, recomputed by `tests/test_silence_share.py`: median 7.8%, 25/27 at or under 20%. Two over the 30% bar — `nasa360-ep5` clip 05 at 40.8% (a loud music bed, mean −31.4 dB, 0.00 s of true silence, not dead air) and `telugu-tutorial` clip 01 at 46.3% |
| `hook_latency` | seconds from clip start to first complete standalone sentence | VERIFIED on reference: avg **0.45 s** (`pacing_report.json`) |
| `payoff_presence_rate` | fraction of clips whose `payoff` field is non-empty | VERIFIED on reference: **1.00** |
| `duplicate_overlap_rate` | fraction of selected clips re-using the same source sentence | VERIFIED on reference: **0.00** |
| `duration_distribution` | bucketed clip lengths | VERIFIED: 2 clips <15 s, 4 in 15–30 s, 0 in 30–45 s, 0 in 45–55 s; avg **16.74 s** |
| `words_per_second` | speech density of the kept timeline | VERIFIED on reference: **3.62** |
| `source_coverage_pct` | fraction of source duration covered by at least one selection | VERIFIED on reference: **6.8%** (100.44 s of 1469.77 s), 0 overlaps |
| `diversity` | visual-diversity score across selected clips | VERIFIED on reference: **0.442** |
| `gate rejection reasons` | distribution of `report.json:rejection_reasons` | VERIFIED on reference: **empty** — 0 rejected by the context guard, `truncated_by_limit: 5`, `candidate_count: 4000` |

**Flagged now, not after the fact:** clip 05 has a **40.8% silence share** yet passes the audit and
is the *largest file* of the six (2.83 MB 16:9, 1.51 MB 9:16, both the highest bitrates of the set).
The audit's silence-share check as written is a per-clip pass/fail, not a ranking signal. A 40.8%
dead-air clip being shipped as a "short" is exactly the kind of thing a human editor would reject.
This is the single most concrete editorial-quality question the corpus run should answer, and it is
visible in the artifacts **today** — it did not need a new run to find.

### D2 — Aspect and geometry correctness

Measured with `ffprobe` on every emitted file, no exceptions:

| Metric | Definition |
| --- | --- |
| `DAR` of 16:9 output | must be exactly 16:9 |
| `DAR` of 9:16 output | must display as 9:16; SelekyT achieves this *anamorphically* — coded 308×486 with SAR 8:9, giving DAR 1232:2187 (VERIFIED on `shorts/01.mp4`) |
| `SAR` consistency | the 16:9 output forces square pixels (`setsar=1`); the 9:16 output deliberately carries SAR 8:9 and must not be "corrected" by the player |
| Content preservation | for a 4:3 source the 16:9 path must **pad**, never crop — a head must never be cut to satisfy a ratio |
| Subject framing (9:16 only) | the tracked subject's face/edge-density centre must remain inside the crop window across the whole clip |
| Rate consistency | output frame rate must equal source frame rate, for every rate in §2.1 |

VERIFIED on the reference: `facebook/01.mp4` is 864×486, SAR 1:1, DAR 16:9, 29.97 fps;
`shorts/01.mp4` is 308×486, SAR 8:9, DAR 1232:2187, 29.97 fps.

### D3 — Interchange validity

`tools/nle_check.py` already implements this and is the contract. On the reference it returns
**86 PASS / 0 FAIL / 6 SKIP** (VERIFIED). The corpus run must additionally:

- ~~install `pycmx`~~ — **DONE**: `pycmx>=1.5` is in the `nle`, `dev` and `all` extras and 1.5.1
  is installed, so the "edl independent parse" checks are no longer skipped by default. They
  are the only checks in the suite that verify the EDL with a parser SelekyT did not write;
- add a **round-trip check**: re-import each exported EDL and FCPXML and confirm the imported
  timeline's record duration matches the render. `docs/BUSINESS_MODEL.md` §5 states plainly that
  **no NLE has ever imported an exported SelekyT timeline**; an independent parse is the cheapest
  substitute and is currently uninstalled.

### D4 — Transcription accuracy (WER)

The metric that has never been computed, on any source. Specified now:

- **Metric:** word error rate = (S + D + I) / N, against a human or official reference transcript.
- **Corpus:** LibriSpeech `test-clean` (C9) and MLS German/Spanish/French/Dutch/Italian/Polish (C10)
  for pure-audio WER at known model sizes; NASA items C4 and C8 for in-domain video WER using the
  official `.srt` sidecars as reference (note: SRT timing, not word timing, so align with a
  gentle-asymmetric edit distance rather than strict word pairing).
- **Must report per-language, per-model-size** (`small`, `medium`, `large-v3`), because
  `docs/BUSINESS_MODEL.md` §9 identifies the required model size as **the single assumption that
  can break the business** — CPU cost moves from 0.1505 RTF at `small` to ~1.505 at `large-v3`
  (VERIFIED `small`; the ×3 and ×10 steps are ASSUMPTION, from `docs/BUSINESS_MODEL.md`).

### D5 — Retention proxy

**Named "proxy" because SelekyT has no viewers and therefore no retention data.** No audience
curve exists and none can exist before a public posting with analytics. Any claim about retention
is an ASSUMPTION until that day. What *can* be computed as a proxy:

- `hook_latency` (D1) and first-3-s speech density — a machine-readable stand-in for "would a
  viewer scroll past the first 3 seconds";
- `silence_share` after dead-air removal (D1) — the variable SelekyT directly controls;
- `source_coverage_pct` — an anti-metric: a very low value means the system is leaving almost all
  of a tape on the floor (reference: 6.8%), which on a monologue source may indicate over-rejection.

**Explicitly not claimed:** correlation between these proxies and measured audience retention. That
correlation is untested. `README.md` cites YouTube's *own* creator guidance about retention dips and
pacing as the motivation for the heuristics; that is a design rationale (RESEARCHED, general
creator guidance), **not** evidence that these particular heuristics move a retention curve.

### D6 — Human-editor agreement

The only dimension that can close the demand-side gap, and the only one requiring people. No editor
has ever been asked. See `docs/TIME_SAVINGS.md` §7 for the study design; the metric here is:

- **Selection overlap:** for the same tape, `|SelekyT selections ∩ editor selections| / |union|`,
  per source, plus the distribution of offset between the two sets.
- **Blind ranking:** a panel of 3 editors, unaware which cuts came from which source, ranks
  SelekyT's cuts and their own on a fixed rubric (hook, coherence, no clipped words, pacing, payoff).
- **Adoption rate:** fraction of SelekyT's per-cut EDL/FCPXML that the editor imports and keeps
  unmodified, versus re-cuts from scratch.

---

## 4. (c) Pass/fail criteria — decided before results exist

These are thresholds, not aspirations. They are set now, while the corpus has not been run, so a
later failure cannot be redefined as success. A criterion marked **CURRENT** can be checked against
the reference run immediately.

| # | Criterion | Threshold | Status today |
| --- | --- | --- | --- |
| P1 | `tools/nle_check.py` exit code 0, **with `pycmx` installed** so the 6 EDL-parse skips become real checks | 0 FAIL on **100%** of corpus | 86/0/6 when `pycmx` was absent — the dependency gap is closed
  (`pycmx>=1.5` in `nle`/`dev`/`all`, 1.5.1 installed) but the corpus has not been re-audited
  with it present, so 0-FAIL is still **UNMEASURED at corpus scale** |
| P2 | `tools/editorial_audit.py` all clips pass | 100% of clips, on **100%** of corpus | 6/6 — CURRENT, one source |
| P3 | Geometry: every 16:9 file DAR exactly 16:9; every 9:16 file displays 9:16 | 100% of files | CURRENT, one source |
| P4 | Frame rate of output == frame rate of source | 100% of files, at every rate in §2.1 | CURRENT at 29.97 only; **untested at 23.976/25/50/59.94** |
| P5 | Per-clip `silence_share` after dead-air removal | ≤ **20%** on ≥ 90% of clips; no clip > 30% | **FAILS on 2 of 27 committed clips** — `nasa360-ep5` clip 05 at 40.8% and `telugu-tutorial` clip 01 at 46.3%, both now flagged by the audit (threshold corrected from 0.55). The 90% clause itself *passes*: 25/27 = 92.6% are at or under 20% |
| P6 | `duplicate_overlap_rate` | 0.00 | CURRENT: 0.00 |
| P7 | Caption text == transcript text (no invented words) | 100% of cues (`editorial_audit.py:check_caption_text_is_transcript_text`) | CURRENT, one source |
| P8 | WER, English, `small` model | ≤ **15%** on C9 `test-clean`; ≤ **20%** on in-domain video with official captions | C9 `test-clean` **DONE: 3.38%** (well under the 15% threshold, three runs, `tools/wer_bench.py`); in-domain video half **STILL NEVER MEASURED** |
| P9 | WER, non-English, `small` model | ≤ **25%** on at least 3 of the 6 C10 languages | **4 of the 6 C10 languages MEASURED, and ALL FOUR PASS — the criterion as written is MET, with one to spare; 2 remain UNMEASURED.** Polish (Multilingual LibriSpeech test, 35 clips, 7.79 min, 1054 reference words) scores **WER 11.01% / CER 2.45%**, **34 of 35** clips inside the bar with diacritics. Italian (MLS test, 40 clips, 9.80 min, 1258 reference words) scores **WER 20.11% / CER 4.53%**, **26 of 40** clips inside the bar. Spanish (MLS test, 40 clips, 10.08 min, 1503 reference words) scores **WER 8.58% / CER 2.64%**, **38 of 40** clips inside the bar. German (MLS test, 40 clips, 10.04 min, 1388 reference words) scores **WER 10.66% / CER 3.20%**, **36 of 40** clips inside the bar, with a worst clip of 42.9% — the same tail the others show, so it is a corpus-level pass rather than a uniformly easy one. All four with the same `small` model that scores 3.38% English. Italian is the hardest of the four: the corpus is Dante, so the reference is archaic verse against a modern ASR, and the worst clips reach 46%; Spanish is the best at 8.58%. Four independent European languages passing is what shows Polish was not one easy audiobook, and it is one more than P9 asks for. Telugu, which is *not* one of the six C10 languages, still **FAILS**: 50 clips on OpenSLR SLR66 give **WER 151.08%** on `small` and **91.54%** on `medium` (CER 127.40% / 69.24%), with **0 of 50** and **1 of 50** clips inside 25% — localising the failure to Indic rather than to "everything outside English". **The claim stays bounded**: the two unmeasured languages (French, Dutch) are registered and not run, so this is "P9 holds on the four measured languages", not "transcription is multilingual" — and all four are clean-read audiobook prose, the easiest condition, so a noisy or compressed source in any of the six is a different measurement that has not been made |
| P10 | Selection overlap with a professional editor (D6) | ≥ **0.30** on ≥ 60% of sources | **NEVER MEASURED — no editor has been asked** |
| P11 | Blind ranking: SelekyT cuts ranked above the editor's own cuts | ≥ **25%** of the time, on ≥ 60% of sources | **NEVER MEASURED** |
| P12 | Pipeline does not crash on a music-only / silent source | 100% (already covered by `test_speechless_source.py`) | CURRENT, unit-tested |
| P13 | Determinism: same source + same params → byte-identical selections | 100% (`test_determinism.py`) | CURRENT, unit-tested |

**P5 is the load-bearing one.** It is the only criterion the existing artifacts already fail, and
it fails on the one source the whole product has been demonstrated on. That is a finding, not a
future risk.

---

## 5. (d) What the minimal first corpus costs in compute, on this host

Host: **4 cores, 15 GB RAM, CPU-only**. All rates below are real-time factor (machine minutes per
source minute). This table is built from measurements I ran today on the real artifacts, plus the
measured transcription rate — it does **not** assume `docs/BUSINESS_MODEL.md`'s numbers where I
measured different ones (see §6).

| Component | RTF | Basis |
| --- | --- | --- |
| Audio extraction / decode to 16 kHz mono | 0.10 | **ASSUMPTION** (carried from `docs/BUSINESS_MODEL.md` §3; not measured today) |
| Transcription, `small` / int8 / beam 1 / 4 threads | **0.1505** | **VERIFIED** — 36.12 s for 240.0 s of the reference run's real 16 kHz mono, model warmed |
| Editorial pass (story units + ranking + gate) | **0.00241** | **VERIFIED** — 3.546 s for a 1469.77 s source, 4000 thoughts → 11 gate-passed candidates |
| Captions SRT+ASS, 6 clips | **0.000012** | **VERIFIED** — 17.6 ms |
| Timeline build + EDL + FCPXML, 6 clips | **0.000002** | **VERIFIED** — 0.4 + 1.4 + 1.5 = 3.3 ms |
| Render 16:9 (99.254 s of output) | **0.00862** | **VERIFIED** — 12.66 s |
| Render 9:16, anamorphic (99.254 s of output) | **0.00625** | **VERIFIED** — 9.18 s |

Cost for the **20-source / 400-source-minute** corpus:

| Transcription model | Total RTF | Machine time for the whole corpus |
| --- | --- | --- |
| `small` (today's) | 0.2678 | **107 min ≈ 1.8 h** |
| `medium` (×3 on transcription — ASSUMPTION) | 0.5688 | **228 min ≈ 3.8 h** |
| `large-v3` (×10 on transcription — ASSUMPTION) | 1.6223 | **649 min ≈ 10.8 h** |

Storage: VERIFIED 0.951 GB per 24.5-min project on disk (of which 0.884 GB is the source copy and
0.047 GB the transient 16 kHz WAV); the deliverables are **19.13 MB** (11.90 MB 16:9 + 7.20 MB 9:16
+ 0.03 MB captions + 0.009 MB timelines). So 20 sources need **≈19 GB of staging**, reducible to
**0.38 GB** if only deliverables are kept.

**Reading:** the corpus is cheap. Even at `large-v3` the entire first corpus is a single overnight
run on this 4-core box, and the transcription layer is the only component whose cost moves
materially. **There is no compute-cost excuse for not running this corpus.** The binding constraint
is the human-judgement metrics (D6, P10, P11), which cost editor time, not CPU time — see
`docs/TIME_SAVINGS.md` §7 for the dollar cost of that.

---

## 6. Corrections to previously published numbers

`docs/BUSINESS_MODEL.md` was written 2026-09-19 06:20; the reference artifacts were regenerated at
2026-09-19 07:36, **after** it. Re-measuring today against the artifacts that actually exist
contradicts several of its numbers. This document uses the corrected values throughout.

| Quantity | `docs/BUSINESS_MODEL.md` says | Re-measured today | Status |
| --- | --- | --- | --- |
| Delivered duration | 116.95 s total, 58.48 s per preset, 3 clips per preset (`facebook/0{1,2,3}.mp4`) | **99.254 s per preset, 6 clips per preset, 198.507 s total** | **CONTRADICTED** |
| Clip yield | 7.96% of source (3.98% per preset) | **6.753% per preset, 13.506% both presets** | **CONTRADICTED** |
| Delivered bytes | 9.9 MB | **19.13 MB** (11.90 + 7.20 + 0.03 + 0.009) | **CONTRADICTED** |
| 9:16 coded geometry | 272×486, SAR 8:9, DAR 1088:2187 | **308×486, SAR 8:9, DAR 1232:2187** | **CONTRADICTED** |
| 16:9 output | "720×486 → cap 1080 wide" | **864×486 square-pixel** (the 4:3 content is padded into an exact 16:9 frame; it is never upscaled to 1080) | **CONTRADICTED** |
| Transcription RTF | 0.1651 | **0.1505** (my cross-check) | corroborated, 8.8% more pessimistic than reality |
| Render RTF 16:9 | 0.1232 | **0.1276** (my cross-check) | corroborated, 3.6% optimistic |
| Editorial + export RTF | 0.01 combined (ASSUMPTION) | **0.00243 measured** | the assumption is **4.1× more pessimistic** than measured — safe direction |

Note the direction: the two contradicted *measure* rows (duration, yield, bytes, geometry) are the
ones that changed when the artifacts were regenerated with 6 clips instead of 3; the contradicted
*assumption* row (editorial cost) was simply conservative. Nothing here makes the cost story worse —
measured editorial cost is 4.1× cheaper than assumed. But **no document in this repo should quote
the old 116.95 s / 7.96% / 9.9 MB figures again; they describe a run that no longer exists.**

`docs/AI_EXECUTION_STATE.md` is known-stale and was not used as a source for anything in this
document.

---

## 7. The confound a one-source evaluation cannot rule out

**Overfitting of the editorial heuristics to one tape — and, more subtly, circularity in the
audit that is supposed to be checking them.**

Concretely, the reference source is: two-host unscripted English dialogue, clean studio audio, 4:3
NTSC SD, NASA subject matter dense with named entities and numerals. Every editorial weight — hook
score, virality score, context score, visual potential, coherence, the 0.30/0.22/0.20/0.16/0.12
blend in `models/core.py:Candidate.total_score` — was tuned while looking at *this kind of tape*.
The thresholds that decide what ships are the same thresholds that the passing audit reports.

A one-source evaluation cannot distinguish three possibilities:

1. the heuristics encode general editorial principles;
2. the heuristics encode properties of NASA 360 Episode 5, and will silently mis-rank a
   fast-cut monologue, a hostile interview, or a non-English panel;
3. the audits are checking invariants that the generator itself guarantees, so they pass
   tautologically on any input — they measure self-consistency, not editorial value.

Only a diverse corpus (possibility 2) plus independent human judgement (possibility 3) can separate
these. **The 1080 passing tests and the 86 passing NLE checks are evidence for neither 1 nor 2 —
they are evidence for machinery, and they would pass identically on a source whose every cut was
editorially wrong.** P10 and P11 in §4 are the criteria that actually discriminate.

A second, quieter confound: `silence_share` is computed from the same word timings the transcript
produced. If transcription degrades on a noisy or accented source, dead-air detection degrades with
it, and the audit's silence metric becomes unreliable in the same direction as the underlying
problem. WER (D4) must therefore be measured *before* any silence-share comparison across sources
with different audio quality is believed.

---

## 8. What this document does not claim

- It does not claim any evaluation has been performed on more than one source, because it has not.
- It does not claim any human editor has ever seen a SelekyT output. None has.
- It does not claim any number for retention, audience, satisfaction, or willingness to pay. There
  are no users to measure.
- It does not claim the verified corpus candidates are *representative* of what paying customers
  would upload. US-government and Commons media are chosen because they are rights-permitted and
  fetchable, which is a proxy for diversity, not a sample of a market.
- It does not claim rights clearance has been done by a lawyer. The rights column in §2.2 records
  what the licence metadata says and what US-government PD status implies; Commons licences are
  per-upload and must be re-read at use time, and C1 carries no explicit licence at all.
