# Product / Workflow Audit — SelekyT

> **Auditor:** product/workflow audit, run against the working tree at
> `/home/azureuser/selektyt` on 2026-09-19.
> **Scope:** what the product actually does today, as implemented — not what it
> is intended to do. Every claim below is either a line citation into this
> repository or an explicit statement that no evidence exists.
> **Venture status assumed:** zero users, zero revenue, zero pilots, zero
> investors (`docs/TIME_SAVINGS.md` §10.11). Nothing in this audit is scaled,
> projected, or extrapolated from usage, because there is no usage.
>
> **Post-audit note (kept so the snapshot stays honest):** the uncommitted work
> this section describes has since been committed, so the *verifiability* part
> of the concern is closed — the underlying findings are not, and none of them
> has been fixed at the time of writing.

### Baseline note (read first)

`docs/AI_EXECUTION_STATE.md` states the working tree is clean. It is not.
`git status` shows modified `selekyt/pipeline.py`, `providers/whisper.py`,
`providers/transcribe.py`, `engine/qa.py`, four test files and five docs, plus
untracked `docs/TRANSCRIPTION_EVALUATION.md`, `docs/NLE_VALIDATION.md`,
`docs/HUMAN_EDITORIAL_EVALUATION.md`, `tools/decode_probe.py`, `tools/wer_bench.py`
and `corpus/wer/`. The transcription-evaluation workstream that produced
`docs/TRANSCRIPTION_EVALUATION.md` changed live code and wrote
*“No default was changed and nothing was committed”* (§10).

This matters to the audit for one reason: **the newest and most consequential
evidence in the repository is uncommitted and partly unverifiable.** Where a
finding rests on it, I say so. The single worst failure mode found in this audit
(the silent partial transcript, §2 B1) is documented only in an uncommitted doc,
proposed-never-implemented in that same doc, and absent from the state doc's
outstanding-work list.

---

## §1 The user journey as the code actually implements it

### 1.1 The real invocation

Two entry points exist, and they disagree about input.

**CLI** (`selekyt/cli.py:79` `main()`), installed by the `selekyt` script entry
in `pyproject.toml`:

```
pip install -e '.[transcription]'
selekyt "<url-or-path>" --out <dir> [--model small] [--language te] [--limit 6]
```

**Web shell** (`web/index.html`, `web/app.mjs`, `web/server.mjs`): a browser UI
that takes a source as *either* an uploaded **base64 WAV only**
(`web/index.html:18` `accept="audio/wav,.wav"`, enforced server-side by
`resolveMedia` at `web/server.mjs:222-242`) *or* a path typed into `cuts-source`
that must resolve inside the project root (`guardedPath`, `web/server.mjs:203-213`).

There is no video upload in the web shell. `web/app.mjs:158-165` fills
`cuts-source` only from an uploaded WAV path or a bundled sample. The README's
headline use case — point it at a video — exists in the CLI and is impossible in
the web UI.

### 1.2 What the user must already have

`selekyt/cli.py:14` `_doctor()` checks `python3`, `ffmpeg`, `ffprobe`,
`faster-whisper` as REQUIRED and `python`, `yt-dlp` as OPTIONAL. Note the
asymmetry baked into the doctor itself: `yt-dlp` is optional, but a URL source
cannot be fetched without it (`providers/youtube.py`), so the doctor passes
clean on a host where the primary documented workflow then fails.

`--doctor` is the only onboarding mechanism. It verifies binaries. It does not
check for a model, a display, disk space, or a sample file. There is no
first-run walkthrough and no sample video shipped with the package.

### 1.3 What comes back

A project directory (`selekyt/pipeline.py:159` `run()`) containing:

| Artifact | Written at | Content |
|---|---|---|
| `facebook/`, `shorts/` MP4s | render loop, `pipeline.py:412-478` | the deliverable cuts |
| `captions/NN.json` | `pipeline.py:416` | per-clip caption text + timings |
| `timeline/*.edl`, `timeline/*.fcpxml` | `pipeline.py:462-477` | NLE interchange |
| `selections.json` | `pipeline.py:334` | the chosen clips |
| `editorial_report.json` / `pacing_report.json` | `pipeline.py:356` / `387` | scoring detail |
| `qa.json` | `pipeline.py:500` | rendered-file checks |
| `report.json` | `pipeline.py:526` | the summary a user reads |

On success the CLI prints exactly one line: `SelekyT complete: <project>`
(`selekyt/cli.py`). No pointer to which file to open first, no preview, no
diff against a previous run.

### 1.4 Where a non-expert gets stuck, or has a problem and cannot know it

Each of these is an **audit finding**, not a feature request. They are points
where the product's own output is insufficient for the user to proceed or to
tell something is wrong.

**J1 — The troubleshooting table sends the user to an empty field.**
`README.md:142` advises: on `no stories passed the gate`, read `report.json` →
`rejection_reasons`. The real run in `out/web/pipeline/project/report.json` has
`rejected_by_context_guard: 0` and `rejection_reasons: {}`. That is not a bug in
the run — it is the *normal* outcome when candidates pass and the limit
truncates (`truncated_by_limit: 5`). So the documented recovery path yields
nothing precisely in the case a user is most likely to hit (too few clips).
**J2 — The report invites an action that no code can perform.** `pipeline.py:508-511`:
*"these passed every editorial gate and were dropped only because --limit capped
the output. An editor reading this report can challenge the rejections and pull
from this bench."* There is no flag, no API, and no code path that can pull from
that bench. I searched: `rank()` (`engine/editorial.py:383-411`) is deterministic
with no seed and no include/exclude hook, and nothing downstream consumes
`truncated_by_limit`. The comment describes a workflow that does not exist.
**J3 — The selection is invisible until after the expensive part.** Ranking
(`pipeline.py:301`), gating (310-329) and rendering (412-478) are one
uninterrupted function. The user cannot see the six chosen clips before the
render, and the render is the costly step (`docs/TIME_SAVINGS.md` §10.6 measures
one discard-and-substitute at 18.6 min).
**J4 — The summary omits what model actually transcribed the audio.** Downgrade
notices print to stdout (`pipeline.py:259-264`) and provenance is persisted only
in `transcript.json` (`pipeline.py:265-280`, key `asr` at line 269). `report.json`
(`pipeline.py:501-521`) has **no `asr` key**. A user who reads only the summary
cannot tell `small` from `base`, or attention timestamps from
character-proportional ones — and the difference changes caption timing.
**J5 — The reported title is null.** `report.json` `title: null` on a real URL
run. A user sorting project directories by title has nothing to sort on.
**J6 — The two entry points disagree about what a source is.** The CLI accepts
any path (`pipeline.py` source resolution, `Path(source).expanduser()`); the web
shell confines local media to the project root and accepts only base64 WAV
uploads. A user who learns the tool one way and switches gets an unrelated
rejection from `guardedPath` with no remedy documented.
**J7 — "Silence share" is not measured from audio.**
`tools/editorial_audit.py:205-222` `check_silence_share` computes
`share = 1.0 - spoken / kept` purely from transcript word timings; it never
decodes a frame of audio. `qa.json`'s pacing block reports `silence_pct: 8.2`
from this lineage. A user reading "8.2% silence" is reading a property of the
transcript, not of the media — and the metric that would tell them apart
(EnergyVAD) returns **0 s of speech for all 126 s** of the Hungarian source
(`docs/TRANSCRIPTION_EVALUATION.md` §8 rec 4; mean −41.8 dBFS).
**J8 — A near-empty transcript is reported as a clean run.** The only
transcript-level guard is `pipeline.py:286` `if not transcript` — the *empty*
case. A transcript with 4 segments / 80 characters out of a 339.6 s,
73%-speech-energy file passes every check the pipeline performs and ships a
clip (see §2 B1). `qa.json`'s `source_coverage` looks like it covers this and
does not: `engine/qa.py:583-634` measures *selected clips vs source duration*
(6.8% in the real run), never *transcript vs speech present*.
**J9 — No correction loop exists anywhere.** There is no reject, regenerate,
re-rank, or override control in the CLI, the web shell, or the artifact format.
`web/app.mjs:247-258` renders results as a flat list of `download` links — no
preview, no inspection, no rejection. The only remediation is re-running the
whole pipeline or hand-editing the EDL.

---

## §2 BLOCKERS

A blocker must state what concretely breaks. Each entry below does.

### B1 — The pipeline ships a transcript that covers almost none of the speech, and reports success
*(Delegated decision (a): **this IS a BLOCKER**, and the reasoning splits the
finding into a blocker and a limitation, because the two halves have different
fixes.)*

**What concretely breaks.** `corpus/telugu-tutorial` was recorded as an OK run
that delivered 1 clip (`docs/CORPUS_RESULTS.md`). The later measurement work
found that its transcript is **4 segments / 80 characters / 15.3 s — 4.5% of a
339.6 s file that is 73% speech-energy audio**, with chunk 0 (the first 300 s)
returning `ok` and **zero segments** (`docs/TRANSCRIPTION_EVALUATION.md`). The
80 characters are fluent-looking Telugu script that a non-speaker cannot
distinguish from a correct transcript. The run was **accepted**. No audit in the
pipeline or in `tools/` flagged it. A user would have shipped wrong captions and
believed the pipeline succeeded.

**Why this is the blocker and the model capacity is only a limitation.** The two
claims are separable and the repo's own measurements separate them:

- *Limitation (not a blocker):* Whisper at the default `small` size
  under-decodes a low-resource language. `docs/TRANSCRIPTION_EVALUATION.md` §6.5
  eliminated causes H1-H4 by measurement and named **H5 — decoding capacity on a
  low-resource language** — as the root cause. Hindi is measurably improved by
  `medium` (3 → 11 segments, mean clip 16.67 s → 4.50 s, opening with
  `मेरा नाम` / "my name"). Raising default model size is a tuning choice with a
  known cost; that is a limitation, and §8 rec 1 explicitly recommends keeping
  `small`.
- *Blocker:* **nothing in the product detects the failure.** This is not an ASR
  quality question, it is a missing-signal question. `selekyt/pipeline.py:286`
  guards only `if not transcript`. `engine/qa.py:583-634` measures selection
  coverage, not transcript coverage. So the *product* fails closed-mouthed on
  exactly the class of input the README explicitly invites, at
  `README.md:37,40`: `selekyt "URL" --model small --language te`.

**Why "non-English is out of scope" does not discharge it.** The README
advertises Telugu by name. A feature in the documented interface that silently
produces 4.5% coverage and reports success is not a scope boundary, it is a
defect — and the original framing in the corpus record is itself now known to be
wrong: Hungarian was characterized as "fluent-looking phonetic noise" and
`docs/TRANSCRIPTION_EVALUATION.md` refutes that directly — 38 segments / 2021
characters / 109.7 s = **87% of a 126 s file**, and *“that characterization does
not survive the shape measurement.”* So of the three sources in the question:
**Telugu is a blocker-class coverage collapse; Hungarian is not a defect at all
(the earlier characterization was wrong); Hindi is a model-capacity limitation.**

**What would resolve it.** The coverage check is already designed and
unimplemented: `docs/TRANSCRIPTION_EVALUATION.md` §8 **recommendation 3 —
"add a coverage check to the pipeline: proposed, NOT implemented."** It is
machine-computable, needs no human and no spend, and extends the existing guard
pattern at `pipeline.py:286-298` rather than adding architecture.

**Evidence status, stated plainly.** This finding rests on an uncommitted
document whose own decode probe is incomplete: `corpus/wer/probe_small_telugu_chunk0.json`
contains only an onnxruntime PCI-bus warning line, not a probe record, and §6.5b
still carries an unfilled `<!-- PROBE_RESULT -->` placeholder. The shape
measurements (4/80/15.3 s, chunk 0 empty, four configurations unstable) are the
load-bearing evidence and they are reproducible from `tools/wer_bench.py`; the
root-cause attribution to H5 is the part that is not yet nailed down. The
blocker does not depend on the root cause — a 4.5%-coverage transcript that
ships as OK is a defect under any explanation.

### B2 — "No speech" is reported for a file that is mostly speech
*(Delegated decision (b): **YES, a blocker** — second rank, because it fails
loudly instead of shipping wrong work.)*

**What concretely breaks.** `selekyt/pipeline.py:286-298` raises one message for
two unrelated conditions:

```python
raise RuntimeError(
    "Transcription produced no speech: the source has no audible "
    "spoken content. Check that the file has an audio track and that "
    "it contains speech (a silent or music-only source has nothing to select)."
)
```

The message directs the user at their *content* ("a silent or music-only
source"), when the actual cause on `corpus/telugu-launch` was a **damaged
container** around audio that the same measurement work scores at 73%
speech-energy. The remedy is not re-recording or finding a different source; it
is remuxing into a clean container. `docs/AI_EXECUTION_STATE.md` names this
*“the most misleading failure mode in the product”* and states the fix:
*“remux to a clean container and retry before reporting no speech.”*

**Why it is a blocker and not a mere annoyance.** It tells a user something
false about media they own, and the false thing it tells them points them at the
wrong remedy. The other two `NO_SPEECH_DETECTED` results in the corpus
(`alice-1903`, a silent film; `roman-portrait-trailer`, a music reel) were
*correct* — which proves the guard is doing real work and is worth making
precise, not worth removing. It ranks **below B1** on a single criterion: B2
fails loudly, so the user knows something is wrong; B1 fails silently, so they
do not.

**What would resolve it.** Remux to a clean container and retry *before* raising
the no-speech error — i.e. test the container when the transcript comes back
empty, not after the user has already been told their media has no speech. This
is logic inside the existing error path, not a redesign.

**Evidence status.** The container-damage diagnosis is asserted by the repo's own
state doc; the probe that would confirm the mechanism is the incomplete §6.5b
noted in B1. I am ranking it a blocker on the strength of the *message-vs-cause*
conflation at `pipeline.py:286-298`, which is directly verifiable in the code
regardless of how the probe resolves.

### B3 — No NLE has ever opened a SelekyT artifact

**What concretely breaks.** The deliverable that separates SelekyT from a
caption-and-clip tool is the CMX 3600 EDL / FCPXML 1.11 interchange layer. Its
external validation status, in the repo's own words (`docs/NLE_VALIDATION.md`):
*"MECHANICAL SELF-CONSISTENCY ESTABLISHED. NO NLE HAS OPENED AN ARTIFACT."* §5
is explicit: *"Nothing in this repository claims an NLE has ever opened a
SelekyT artifact."* Established evidence is two *libraries* accepting the files
(`pycmx` outside the venv, plus SelekyT's own round-trip), agreeing with the
render to `RENDER_FRAME_TOLERANCE` via `tools/nle_check.py`. The negative routes
are recorded as closed: Blender 4.0.2 has no EDL import operator; the
OpenTimelineIO PyPI wheel ships no CMX/FCPXML adapters.

**Why it is a blocker.** The pipeline's terminal artifacts are asserted against a
format contract that has never been exercised by the class of program that
consumes it. A library parsing a file is not an NLE relinking media, honouring
timecode, or playing the intended cut — the doc says this itself (§1).

**Why it is the *cheapest* blocker.** It closes with no code and no spend:
`docs/NLE_VALIDATION.md` §4 ranks first *“a human editor with an NLE already
installed”* answering three questions — did it import, did the media relink, does
the played cut match the render. It is blocked on a person and a display, not on
engineering (see §5).

### B4 — There is no way to correct a bad selection, and the report pretends there is

**What concretely breaks.** `docs/TIME_SAVINGS.md` §10.6 states the value
mechanism plainly: *"selection quality IS the time-saving mechanism,"* with one
discard-and-substitute measured at **18.6 min (6.5×)**. The product's own value
model therefore assumes a correction loop. That loop does not exist:

- `engine/editorial.py:383-411` `rank()` is deterministic — no seed, no user
  control, no include/exclude hook. Filtering is hardcoded (8-55 s, ≥20 words,
  ±4 s overlap, `_similarity >= .78`, `_hook_similarity >= .65`, >12 topic
  tokens, `kinds[story_type] >= 2`).
- `truncated_by_limit` is computed (`pipeline.py:324`), reported (511), and
  consumed by **nothing** (finding J2).
- The web shell's result view is download links (`web/app.mjs:247-258`); there is
  no preview, no rejection, no re-roll.
- `docs/TIME_SAVINGS.md` §10.9 names F7, the correction-budget falsifier: if
  fixing a bad cut costs more than the saving, the claim collapses.

So the one thing that determines whether the product saves time is the one thing
the user cannot influence, cannot inspect before the render, and cannot correct
afterwards except by re-running everything.

**What would resolve it.** Expose the bench the report already claims exists —
let `truncated_by_limit` clips be inspectable and selectable — and surface the
selection before the render loop at `pipeline.py:412`. This is the one blocker
where the honest answer is that it is *partially* a design gap; I rank it a
blocker because the repo's own time-savings argument is falsified without it.

---

## §3 NICE-TO-HAVE — deliberately deprioritized

The value of this section is that it is the list of things I **chose not to
escalate**, each with the reason it does not meet the blocker bar.

| Item | Why it is not a blocker |
|---|---|
| Desktop/Tauri packaging, an installed app | The CLI and web shell both run today. Nothing breaks without an installer; distribution is a growth question, not a usability one, and there is no user base to distribute to. |
| Ollama LLM editorial pass (`--ollama-url`, `pipeline.py:332-333`) | Optional and already off by default. The deterministic scorer works without it. |
| Renaming `check_silence_share` / making it decode audio (finding J7) | Real defect, but it mislabels a metric in a report; it does not cause a wrong artifact to ship. Fix it whenever the QA layer is touched. |
| EnergyVAD loudness-relative thresholds | Measured defect (`docs/TRANSCRIPTION_EVALUATION.md` §8 rec 4: 0 s speech in 126 s at mean −41.8 dBFS), but `--audio-analysis` is opt-in and the default path does not use it. |
| Frame-rate / codec coverage beyond the tested presets | `docs/AI_EXECUTION_STATE.md` already flags narrow coverage. No failure in the corpus is attributed to an untested frame rate. |
| A second YouTube fetch strategy | `providers/youtube.py` already rotates three player clients and reports bot markers; the state doc records fetchability as per-video, not a hard ban. A retry is a marginal gain. |
| Web video upload (findings J6) | Consistency defect between two entry points, not an unusable product — the CLI path is documented and works. |
| Reference transcripts for Hindi (`docs/TRANSCRIPTION_EVALUATION.md` §8 rec 5) | Would raise confidence in non-English quality; the blocker (B1) is the missing *check*, not the missing *reference*. Add the check first. |
| Non-English WER measurement (P9, 3 of 6 C10 languages measured, all passing) | Polish (11.01%), Italian (20.11%, on archaic Italian) and Spanish (8.58%) all pass, which is the count the criterion needs. German, French and Dutch are still unmeasured, and the three that pass are clean-read audiobook prose — so P9 is met as written on its easiest half, not shown to hold generally. B1 does not depend on it — a coverage check catches the failure class without a ground truth. |

---

## §4 The trust gap

What a user is asked to trust without being able to verify it, and where the
repository itself admits it is unverified.

**T1 — That the selected clips are the right clips.** No ground truth exists and
no human has judged any cut. `docs/HUMAN_EDITORIAL_EVALUATION.md` §14: *"Nothing
in this document is a result, because nothing has been run."* Its eleven axes
(E1-E11) and the T-gate are **DESIGNED, NOT RUN**, and §13 lists eight
prerequisites, **none met**. A user opening `editorial_report.json` sees scores;
they cannot verify the scores mean anything, because nothing has ever been
measured against a human judgement.

**T2 — That the captions are what was said.** Every audit in the repo compares
captions to *the transcript*, not to the audio. English WER was measured for the
first time in this workstream (`docs/TRANSCRIPTION_EVALUATION.md`): **3.38% at
`small`, 2.99% at `medium`, 3.38% at `large-v3`** on a 50-clip / 1036-word
LibriSpeech sample — statistically indistinguishable, and passing the ≤15% bar
with large margin. Non-English WER is now measured on three of the six C10
languages, all passing: Polish scores **11.01% WER / 2.45% CER** (34 of 35 inside
the bar), Italian **20.11% WER / 4.53% CER** (26 of 40 inside it, on Dante's
archaic Italian), and Spanish **8.58% WER / 2.64% CER** (38 of 40 inside it), the
best of the three, while Telugu on reference ground truth scores **151.08%
at `small` and 91.54% at `medium`** — 0 and 1 of 50 utterances inside it. The
three remaining C10 languages are still unmeasured and the criterion needs 3 of
6; three (Polish, Italian, Spanish) now meet it, so P9 is claimable as written, though on the clean-read half of the corpus only. The MLS tarballs that would enable the rest are
6.2-114.5 GiB against ~3.0 GB of free disk, which §7.2 of that document routes
around by streaming a strided sample rather than downloading. So: English
captions are backed by a number, three European languages are backed by passing
numbers, and the Telugu caption that shipped in B1 is backed by a measured
failure.

**T3 — That an NLE will open the output.** See B3. The repo states this plainly
and I add nothing to it: no NLE has opened an artifact.

**T4 — Which model actually transcribed the audio.** `pipeline.py:259-264` prints
a downgrade notice to stdout; provenance is written to `transcript.json`
(`pipeline.py:269`); `report.json` (`pipeline.py:501-521`) has **no `asr` key**.
The summary a user reads cannot tell them `small` from `base`, or
`per-token` from `character-proportional` word timestamps
(`providers/transcribe.py:56, 84`) — a difference that changes caption timing.

**T5 — The time-savings number.** `docs/TIME_SAVINGS.md` §10.11 **forbids
quoting any single speedup figure** — not 73× and not 36× either — because the
denominator has never been measured: no editor has ever been timed on this
task. §10.5 is more careful than an earlier draft of it was, and this page
should not overstate what it settled. It does not call 73× impossible; it calls
it *protocol-dependent*: 198.307 s of delivered content must be watched, so
under a both-aspect-ratios review the ceiling at a 2 h baseline is 36.3×, while
under the one-geometry protocol the artifacts actually support (the 9:16 tree
is a re-crop of the same 27 selections, duration deltas of 0.000 s across all
clip pairs) it is 72.6×. Either way the figure assumes zero correction budget,
and §10.6 shows one discarded clip costs more than the whole budget allows.
What *is* measured is the machine side: RTF spread 0.180–1.746 (9.7×), two of
seven sources slower than real time (1.140 and 1.746), and a regression
`elapsed_s = 225.2 + 0.1067 × duration_s (R²=0.390)` implying ~225 s of fixed
per-run overhead that dominates short sources. A user is being asked to trust
a headline ratio the repository has itself reduced to a bound with no measured
denominator.

**T6 — The reported silence figure.** `silence_pct` descends from a metric that
never decodes audio (finding J7). Low-stakes, but it is a published number that
does not measure what its name says.

**The honest summary of the gap:** of the six things a user must trust, three
(T1, T3, T5) are explicitly and responsibly flagged as unverified in the repo's
own docs, one (T2) is verified for English, partly verified for non-English
(Polish and Italian pass, Telugu fails on ground truth, four C10 languages
unmeasured), and
two (T4, T6) are artifacts of the report format that could be closed by writing
existing data into the summary.

---

## §5 What the workflow is missing that only a human can supply

Separated into what needs code and what needs a person.

### Needs code — no human, no spend, no approval required

1. **Transcript coverage check** (B1). Designed at `docs/TRANSCRIPTION_EVALUATION.md`
   §8 rec 3, unimplemented. Machine-computable.
2. **Remux-then-retry before the no-speech error** (B2). Logic change inside the
   existing error path at `pipeline.py:286-298`.
3. **`asr` provenance in `report.json`** (T4, finding J4). The data already
   exists at `pipeline.py:269`; it is a matter of carrying it into the summary at
   `pipeline.py:501-521`.
4. **Selection preview and bench access** (B4, findings J2/J3/J9). Surface the
   ranked set before the render loop at `pipeline.py:412`.
5. **A transcript-coverage entry in `qa.json`** so `source_coverage`
   (`engine/qa.py:583`) stops being mistaken for it (finding J8).

### Needs a person — code cannot substitute

1. **Any editorial-quality verdict.** The T-gate (TRUE / PARTLY_TRUE / NOISE /
   WRONG_LANGUAGE) and the eleven axes E1-E11 are human judgements by
   construction; `docs/HUMAN_EDITORIAL_EVALUATION.md` §3 even routes NOISE out of
   the study. `docs/AI_EXECUTION_STATE.md` states editorial judgement is
   **unmeasured and proven unmeasurable by mechanical audits** — the strongest
   such statement available, and the reason T1 cannot be closed with more tooling.
2. **The NLE import test** (B3). `docs/NLE_VALIDATION.md` §3: *"there is no free,
   headless, no-display path from a CMX 3600 EDL or an FCPXML 1.11 to a real NLE
   on this machine."* §4's cheapest route is a human editor with an NLE already
   installed answering three questions; the alternative is Resolve on a
   display-capable host. Blocked on hardware, not engineering.
3. **The Tier-1 timing session.** `docs/TIME_SAVINGS.md` §10.10 recommends Tier 1
   (≈$110, one editor) as the first real measurement of the time-saving claim.
   Until then T5 stays open and no figure of any size may be quoted — the
   ceiling is protocol-dependent (36.3× or 72.6×), not a settled number.
4. **Founder approval to contact anyone at all.** `docs/HUMAN_EDITORIAL_EVALUATION.md`
   §13 makes this the hard gate, and `docs/NLE_VALIDATION.md` §4 records that
   *"the mission forbids contacting anyone without explicit founder approval."*
   Every person-dependent item above is behind this one decision. It costs
   nothing and unblocks everything in this column.

---

## §6 The one-sentence verdict

**Implement the transcript coverage check — the one already designed but
unimplemented check (`docs/TRANSCRIPTION_EVALUATION.md` §8 rec 3) that extends the
existing empty-transcript guard at `selekyt/pipeline.py:286` — because it is the
only top-rank finding that closes with no human, no display and no spend, it
would have caught the corpus's single most misleading shipped failure (a 4.5%
transcript reported as OK on a source the README advertises by name), and it
converts an invisible wrong-output failure into a loud one, whereas every other
blocker is either already loud (B2), blocked on a person and a machine (B3), or
partly a design gap (B4).**

Supporting arithmetic for that ranking, in one line each:

- **B1** is the only blocker where the user cannot tell anything is wrong — the
  definition of a trust-destroying failure, and the reason it ranks first.
- **B2** is the same failure class with the opposite polarity: it fails loudly,
  so the user knows to ask. Loud-but-wrong ranks below silent-and-wrong.
- **B3** is the highest-*value* blocker but the lowest-*cost* to close — it needs
  a person for an afternoon, so it is not the thing *this codebase* should do
  next.
- **B4** is real but partially a design decision; the repo's own §10.6 argument
  makes it urgent, yet a partial fix (preview the bench) is a larger change than
  B1 or B2, and it does not stop wrong work from shipping the way B1 does.
