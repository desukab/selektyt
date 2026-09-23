# Corpus benchmark status and provenance

**Date: 2026-09-21; state updated 2026-09-23.** This records what
`corpus/*_run/` is, whether it still reproduces, and what a reader may and may
not conclude from it. It is the deliberate disposition the corpus was waiting
for: until it existed, the committed benchmark evidence had no stated expiry
date and no check against the current code.

It is companion to two files rather than a replacement for either:
`docs/CORPUS_RESULTS.md` (what the first corpus run found, written when it ran)
and `corpus/results.jsonl` (the raw record, one object per source including
every failure).

---

## What the corpus is

Eleven sources across the eight axes `corpus/manifest.json` requires, run once
by `tools/corpus_run.py` on 2026-09-19. Each `corpus/<id>_run/project/`
directory is a complete pipeline output: transcript, selections, edit plan,
captions, content pack, QA report, and — for the seven that delivered —
rendered exports and NLE interchange files. The manifest records the rights
declaration for each source and notes that frame rate was **not probed** at
inclusion time; it is probed at run time.

The corpus is **frozen as a historical benchmark**. It is not a living test
fixture and it is not regenerated when the code moves. Regenerating it would
destroy the evidence it exists to hold — including the evidence of defects the
code has since fixed, which is the most useful thing in it. The decision
recorded here is to keep the committed artifacts byte-identical, publish the
reproducibility state next to them, and let the state be re-checked
automatically.

---

## Whether it still reproduces

`tools/corpus_reproducibility.py` re-scores each run's committed
`transcript.json` with the current editorial code and compares the result with
the selection that run shipped. Selection is a function of the transcript
alone, so this needs no media and runs in about a minute:

```sh
.venv/bin/python tools/corpus_reproducibility.py
```

Exit code is nonzero while any run with clips fails to reproduce, so it is
suitable for CI. `tests/test_corpus_reproducibility.py` pins the current state
and fails on any change to it.

**Current result — 3 of 7 delivering runs reproduce exactly:**

| Run | Language | Clips | Status |
| --- | --- | --- | --- |
| `ooi-vertical` | en | 3 / 3 | reproduces |
| `pentagon-esper-briefing` | en | 5 / 5 | reproduces |
| `royal-society-cloke` | en | 4 / 4 | reproduces |
| `emwcon-panel-2018` | en | 5 shipped / 5 now | **moved** (scan reached the tail) |
| `nasa360-ep5` | en | 6 shipped / 6 now | **moved** (scan reached the tail) |
| `hadhazy-protest` | hu | 3 shipped / 1 now | **no longer ships** |
| `telugu-tutorial` | te | 1 shipped / 0 now | **no longer ships** |

The four runs with no selections (`hindi-baiga-interview`, `telugu-launch`,
`alice-1903`, `roman-portrait-trailer`) recorded failures in `results.jsonl`
and have nothing to reproduce.

### What "reproduces" means here

The three reproducing runs come back with **the same spans to the millisecond,
the same story types and the same scores**. Two fields differ on every clip —
`confidence` and `reason` — and both are fields a later commit rewrote on
purpose.

`d79bef3` replaced the literal confidence `0.90` with the measured per-word
probability the transcriber actually emitted. It also moved the reason's
clip-specific sentence from behind a fixed label to in front of it: the
committed artifacts read `Structural editorial score: opening=0.75; …`, so
every clip's reason opened identically for its first 28 characters, and the
current code reads `conflict candidate that lands a clear closing thought.
Structural editorial score: opening=0.75; …`. The digits are byte-identical —
the audit compares the numeric block rather than the whole string, because
comparing whole strings would flag every clip and bury a genuine change to the
measurement in the noise.

The committed `selections.json` carries the pre-measurement confidence and the
label-first reason; the audit names both causes in its report rather than
leaving a reader to infer whether the editorial judgement moved. **It did
not.**

### The two that no longer ship

`hadhazy-protest` and `telugu-tutorial` shipped 3 clips and 1 clip. Their
transcripts measure **0.639** and **0.319** mean per-word confidence against
a confidence floor of `0.75`, and the committed artifacts record `0.9` for
both, because the pre-measurement literal was a value the context gate could
never fail.

The spans still score **byte-identically** — 0.6005 and 0.4175 — so the
editorial judgement of those transcripts has not changed. What changed is
that the gate now measures the signal it was written to gate on, and
correctly declines to vouch for a transcript the old one did.

This is the confidence fix working, and the two runs are the clearest
benchmark evidence for it: they are sources where a false `0.9` put clips into
a deliverable. **They are not regenerated and they are not deleted.** They
stay as committed, with this document stating the discrepancy.

What it does *not* mean is that those runs' shipped outputs are safe to use.
`docs/CORPUS_RESULTS.md` already reached the same conclusion about both
sources by a different route — `telugu-tutorial` measures WER 151.08% against
OpenSLR SLR66, and `hadhazy-protest`'s accuracy is not established by anything
in this repo. The confidence gate now refuses them for the same reason those
sections give, which is two independent arguments reaching one conclusion.

### The two whose selection moved when the scan stopped truncating

`emwcon-panel-2018` and `nasa360-ep5` still ship the same *number* of clips —
5 and 6 — but the clips themselves moved. Their committed selections were made
by a candidate scan that stopped once it had emitted a fixed number of
candidates. Candidates are built in start order, so on a long source that cap
did not bound the *cost*, it deleted the tail: nothing after the cap's reach
could generate a candidate, so nothing there could ever be selected. At the
product's 1–3 hour target the cut landed near the 18-minute mark of a 3-hour
source.

The fix spends the budget on even coverage instead — when a source would
explode the budget, starting positions are strided so the last hour is sampled
like the first — which means these two runs' tails were reachable for the
first time. The measured effect on the committed transcripts:

| | `emwcon-panel-2018` | `nasa360-ep5` |
| --- | --- | --- |
| Source length | 55.5 min | 24.4 min |
| Old scan — candidates / reach | 4000 / 2323.6 s (69.7%) | 4000 / 941.2 s (64.4%) |
| New scan — candidates / reach | 6758 / 3332.8 s (100.0%) | 5782 / 1462.0 s (100.0%) |

The attribution is not an inference. Every span the current code ships that the
committed artifact does not **starts past the old scan's reach**, and every
span it dropped starts within it:

| | shipped now, past the old reach | dropped, inside the old reach |
| --- | --- | --- |
| `emwcon-panel-2018` | 2471.9→2488.8, 3027.5→3038.4, 3098.8→3129.2 | 205.8→234.5, 793.7→813.6, 1168.5→1181.6 |
| `nasa360-ep5` | 1023.2→1044.1, 1069.7→1099.0, 1330.8→1341.9 | 327.9→346.3, 507.1→516.2, 606.0→620.0 |

Three clips in and three clips out on each run, and the swap is entirely a
blind spot closing rather than a scorer moving. On both runs one committed
clip keeps its span exactly and differs only in the re-measured `confidence`.

Two things this is *not*, stated because both would be the wrong reading:

- **It is not more coverage.** Clip count is fixed by the request, so the union
  of shipped intervals barely moves — `nasa360-ep5` 6.9% → 8.2%,
  `emwcon-panel-2018` 3.7% → 3.6%. What changed is *which* minutes ship: the
  selected span now extends into the tail (`nasa360-ep5` last clip 671 s →
  1342 s; `emwcon-panel-2018` 1182 s → 3129 s).
- **It is not evidence that the new clips are better editorially.** The corpus
  has no ground truth for that. What it is evidence of is that the selection is
  no longer structurally prevented from considering the back half of a long
  source — and for a product whose stated input is 1–3 hours, that is the
  property that matters.

**These runs are not regenerated.** The corpus is frozen, and their media is
not committed (see [§Media availability](#media-availability-and-the-validation-that-follows-from-it)),
so re-rendering is not possible in this environment and regenerating only the
JSON sidecars would break their agreement with the committed pixels. The
selection they record is the one the truncated scan made, which is itself part
of the evidence — it is what a 4000-candidate prefix-truncation looks like on
real media.

---

## What a reader may and may not conclude

**May.** That the editorial selection layer is deterministic and reproducible:
given the same transcript, the current code selects the same clips. That three
real sources across documentary, news and magazine genres produce shippable
spans under the current scoring. That the confidence gate rejects
low-confidence transcripts rather than passing them silently. That selection is
no longer structurally prevented from reaching the back half of a long source —
measured on two runs whose committed selections predate the fix.

**May not.** That the rendered outputs in `hadhazy-protest` and
`telugu-tutorial` are current deliverables — the current code would not ship
them. That the renders and NLE files for `emwcon-panel-2018` and `nasa360-ep5`
reflect the current selection — they were rendered from the committed
selection, which the scan fix has since moved, and cannot be re-rendered
without media. That clip counts from the corpus are stable across versions of
the editorial code; they are stable *within* the committed state and the audit
says when they are not. That any of this measures editorial *quality* — it
measures reproducibility and gate behaviour, which is what a benchmark of a
deterministic engine can measure.

**May not, and specifically about the renders:** the committed renders and
NLE files for the three reproducing runs were produced by the code as it stood
on 2026-09-19 and have not been re-rendered since. Selection reproduces from
the transcript; the render path is not covered by this audit, because
re-rendering needs media that is not committed (see below).

---

## Media availability, and the validation that follows from it

Only two sources are present on this machine: `corpus/media/pentagon-esper-briefing.mp4`
(158 MB) and `corpus/media/royal-society-cloke.webm` (85 MB). The other nine
were removed out of band for disk, not by `corpus_run.py` — `docs/CORPUS_RESULTS.md`
records why that distinction matters and what it fixed in the harness.

Consequence, stated plainly: **the render, caption-burn and NLE-relink
validation in `docs/NLE_VALIDATION.md` can be re-run on two of eleven sources
without re-downloading.** Everything the transcript determines — selection,
scoring, confidence, the corpus layer — re-checks on all eleven. Everything
the media determines does not.

The exact commands to close that gap are in
[§Remaining external validation](#remaining-external-validation) below.

---

## Remaining external validation

Ordered by what unblocks the most.

1. **Re-download the nine absent sources and re-run** so that the
   reproducing runs' renders and interchange files are regenerated by the
   current code, and the NLE relink audit runs against media that exists.
   For `emwcon-panel-2018` and `nasa360-ep5` this would also re-render the
   moved selection and close the gap recorded in [§The two whose selection
   moved](#the-two-whose-selection-moved-when-the-scan-stopped-truncating):

   ```sh
   .venv/bin/python tools/corpus_run.py corpus/manifest.json --only nasa360-ep5,emwcon-panel-2018,ooi-vertical,hadhazy-protest,telugu-tutorial --keep-media
   .venv/bin/python tools/nle_check.py corpus/nasa360-ep5_run/project
   ```

   Network access to `archive.org` and `upload.wikimedia.org` is required and
   is not available in the current environment.

2. **Import the regenerated FCPXML and EDL into a real NLE** (Premiere,
   Resolve or Final Cut). The exports are parsed by independent parsers in
   tests — `pycmx` for the EDL, this repo's own FCPXML reader — but parsing is
   not importing. See `docs/NLE_VALIDATION.md` for the exact remaining
   requirement.

3. **Run the signed MSIX on a real Windows machine.** Structure, manifests and
   signing configuration are validated on this host; the runtime is not. See
   `docs/packaging.md`.

None of the three is blocked by another, and none of them is a reason to hold
the audit above.
