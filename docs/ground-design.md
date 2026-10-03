# GROUND — retrieved intervals decoded into source frames

The pipeline order is INDEX → RETRIEVE → **GROUND** → VERIFY → REASON. This document
is the GROUND stage. `docs/retrieval-design.md` §6.1 defines the handoff GROUND
receives; this is what happens on this side of it.

**Status: the media-grounding milestone is implemented.** The stage that exists turns
a retrieval answer into decoded frames with timestamps and provenance. The stage that
does *not* exist yet is the one that understands them — §7 names what is future and
§6.4 is explicit that no vision model is wired in.

---

## 0. Provenance of every number in this document

Everything in §4 and §6.3 was measured on 2026-10-02 with the code in
`engine/ground.py` as committed, against the committed corpus source
`corpus/media/pentagon-esper-briefing.mp4` (1013.85 s, 29.97 fps, 1280×720, 158 MB).
The measurement script is not committed; reproduce it with

```python
from pathlib import Path
from engine.ground import ground
r = ground([(120.0, 135.0), (400.0, 420.0), (700.0, 730.0)],
           Path("corpus/media/pentagon-esper-briefing.mp4"), out_dir=Path("/tmp/g"))
print(r.report)          # and r.metrics.to_dict()
```

Numbers that are design estimates rather than measurements are labelled as such
wherever they appear. The test suite pins the sampling policy and the timestamp
contract, so a regression in either shows up as a failing test before it shows up as
a wrong number here.

---

## 1. Why this module exists

`engine.retrieval` answers a query with a union of temporal intervals and stops. It
reads the transcript; it never looks at a pixel, and its evidence is `Modality.TEXT`.
That is the correct boundary and it is deliberate — retrieval is cheap precisely
because it does not decode.

The VISUAL adapters in `engine/memory/ingest.py` look, at first reading, like they
already do this job. They do not. `scene_segments`, `shot_boundaries`,
`face_presence` and `slide_rects` are **translators**: they convert the per-window
numbers that `engine.reframe` already produced into `Evidence`. Every one of them
takes a mandatory `offset` because `engine.reframe` reports seconds relative to the
window it analysed, and none of them decodes anything. They describe the whole source,
at whatever sample count reframe already used.

GROUND is the missing layer: **given intervals a caller chose, decode only those
windows.** Nothing in the repo did that before. `engine.reframe._sample_frames` is
the closest primitive — input-side seek, one frame at a time — but it is private to
reframe and returns times relative to the window start, which is precisely the
timebase hazard that makes `ingest.py`'s adapters take an `offset` in the first place.

GROUND therefore sits between retrieval and any future visual stage, and it exists so
that the expensive thing (decode) happens once, in a place with a tested contract,
rather than being re-implemented by every consumer that needs a frame.

---

## 2. The implemented interface

```python
from engine.ground import ground, GroundResult

result: GroundResult = ground(
    requested,          # a RetrievalResult, an IntervalSet, Intervals, or (start, end) pairs
    media,              # source path
    out_dir=frames_dir, # where the JPEGs are written
    max_frames=3,       # per-interval frame budget, applied after deduplication
    duration=None,      # media duration; probed when omitted
)
```

What comes back:

| Field | Meaning |
|---|---|
| `evidence` | One `Modality.VISUAL` `Evidence` per grounded interval, through the shared `engine.memory.evidence` contract — same dataclass, same `to_dict`, same store. No second evidence model. |
| `intervals` | One `GroundedInterval` per decoded window: `start`/`end` (what was decoded), `requested` (what was asked for), `frames`, `clamped`, `note`, `failures`. |
| `failures` | Human-readable lines naming the interval *and* the reason. |
| `metrics` | `GroundMetrics`: source duration, requested duration, frame count, elapsed. |
| `report` | The whole thing as prose, failures included. |

`GroundResult` is serializable: `save(path)` / `load(path)` round-trip the evidence,
the grounded intervals and the metrics, with a `schema` version the loader checks.

### 2.1 The traceable chain

The artifact-ownership rule the stage was built under is that no parallel video
abstraction is created. Every object points at the one before it:

```
source file
  └─ retrieval interval (source seconds, from the answer of record)
       └─ grounded interval   (clamped to the media, merged against overlaps)
            └─ decoded frame   (a JPEG on disk, named ground_interval000_00_at_001.000s.jpg)
                 └─ Evidence   (VISUAL, artifacts = the frame paths, rationale names
                                the window, the digest and the decode path)
```

Each `Evidence.artifacts` entry is a real file an editor can open; each frame's
filename carries the interval index, the sample position and the source time, so a
frame can be located without opening it.

---

## 3. The sampling policy — deterministic, and the reason it has to be

Sampling is a pure function of the interval bounds, in three named steps:

1. **Candidates**, in priority order: the **beginning**, the **end**, the **middle**.
   The endpoints bound the interval, so a caller with a two-frame budget gets the
   extent rather than an opening and a midpoint.
2. **Deduplication**: any candidate within `MIN_SAMPLE_GAP_S` (0.04 s, one frame at
   25 fps) of an already-kept sample is dropped. This is what keeps a very short
   interval from producing three writes of one identical frame.
3. **Cap**: the first `max_frames` survivors, then the kept times are sorted into
   temporal order.

A zero-length interval is sampled once, at its start, rather than rejected — an
instant is a legal query target even though it has no interior.

Every decision is recorded on the object: `GroundedInterval.note` reads, for a
clamped short window, *"sampled beginning; dropped end, middle within 0.04s of a kept
sample; clamped end to the source duration."* The policy is not a comment in the code;
it is visible on every result.

### 3.1 The timebase, and the one place GROUND moves a timestamp

`SampledFrame.time` is the time GROUND *requested*, in source seconds — the
deterministic quantity derived from the interval bounds, not a decode-dependent one.
ffmpeg's accurate input-side seek lands on the frame **at or after** that instant:
`tests/test_ground.py` verifies this against pixels, not against the module's own
bookkeeping — the fixture is a 10 fps grey ramp whose brightness encodes the frame
index, so a test decodes each returned frame, converts its mean brightness back to a
frame number, and asserts that number is the frame at or after the requested instant.
(Measured directly while writing this: a seek to 1.950 s returns frame 20, i.e. 2.000 s,
never frame 19.) Each frame may therefore be up to one frame period later than the
timestamp recorded. That caveat is written into every evidence `rationale`, and the ramp
fixture is what lets a test check the mapping by reading pixels rather than by trusting
the module's arithmetic.

There is exactly one place GROUND rewrites a sample time, and it is load-bearing. A
sample requested at exactly the source *duration* asks ffmpeg for a frame after the
last one and decodes nothing — which is the common case, because an interval clamped
to the source end has its end sample exactly there. `fit_to_stream` pulls such a
sample back to `duration − 1.5 / fps`, which always lands on the final frame
(`ceil((duration − 1.5/fps) · fps) == n_frames − 1`). Half a frame period is *not*
enough on a slow source. Interior samples are never moved.

---

## 4. Cost — measured, and it is the reason this module is shaped this way

GROUND exists to make decode proportional to the answer rather than to the source.
Measured on the corpus source:

| Windows | Requested | % of timeline | Frames decoded | Elapsed |
|---|---|---|---|---|
| 1 | 15.0 s | 1.5 % | 3 | 0.690 s |
| 3 | 65.0 s | 6.4 % | 9 | 2.078 s |
| 10 | 200.0 s | 19.7 % | 30 | 6.145 s |

Per-frame cost is stable at ≈ 0.20 s (a single isolated targeted decode at 500.0 s
measured 0.199 s), plus a fixed 0.129 s for the two `ffprobe` calls that establish
duration and frame rate. Cost is linear in *frames*, not in source length — the 1-window
row decodes 1.5 % of the timeline for 3 frames and the 10-window row 19.7 % for 30.

Two consequences worth naming rather than leaving as arithmetic:

- **Grounding a whole hour-long source would not be a grounding problem.** 3600 frames
  at 0.20 s is ~12 minutes — and nobody asks for that, because the point of GROUND is
  that the answer is small. The cost is bounded by the retrieval answer's size, which
  is bounded by K.
- **The fixed probe cost dominates a small answer.** For a single window, 0.129 s of
  the 0.690 s is probe. A caller grounding many small windows in one call amortises
  it; a caller grounding one window per call does not, and should not be surprised.

---

## 5. Failure semantics — the boundary this module is most likely to get wrong

The rule: **a failed decode is never returned as an empty success.** A decode that
yields nothing and a window in which nothing visual happened are different answers,
and the result type keeps them apart — `evidence == () and failures == ()` means
"nothing was asked for", while `evidence == () and failures != ()` means "the decode
failed".

| Condition | Behaviour |
|---|---|
| `ffmpeg` absent | Every frame is reported as `decoder unavailable`; no evidence; no frame files are written. |
| Source missing / unreadable | Reported once as `source unreadable` with the probe error; no evidence. |
| Interval starts at or past the duration | Rejected by name — the signature of a stale index over a re-trimmed source. |
| Interval overhangs the duration | Clamped to the duration, `clamped=True`, `requested` retains the original ask. |
| Non-finite / negative / inverted span | Rejected by name rather than silently repaired. `Interval` repairs an inverted span; GROUND reports it, because an inverted span reaching this layer is a caller bug. |
| A frame decodes to nothing | Reported per frame; the interval is still returned, empty, with its `failures` populated. |
| The retrieval answer names a poor transcript | Its verdict travels onto the evidence `rationale` (below), so a confident decode is not read as endorsement of a window chosen over a poor transcript. |

**The verdict travels.** The second constraint §6.1 places on GROUND is that a
grounded conclusion over a `poor` transcript must not be presented with the confidence
of one over a `usable` transcript. GROUND's `confidence` of 1.0 describes the decode —
a frame exists where it was sought — and would be wrong to lower for a reason the
pixels know nothing about. Instead, when the caller passes a `RetrievalResult`, its
`report` (which names the fitness verdict and any channel the query could not consult)
is carried onto every evidence `rationale`, alongside an explicit statement that the
confidence covers the decode only. A consumer reading the evidence cannot mistake one
for the other.

Failures are reported, not swallowed — the same convention the pipeline's corpus and
memory blocks already use.

---

## 6. What was deliberately not done

### 6.1 No semantic claims

A decoded frame is not an interpretation. The claim GROUND makes is *"3 sampled visual
observations exist in 1.000-3.000s, at 1.000s, 2.500s and 3.000s"* — existence and
time. The rationale states in words that no vision model has evaluated the frames.
These are called **sampled visual observations** throughout the code, the tests and
this document, because calling them anything stronger would let a later stage inherit
a meaning this one never established. The test suite asserts the banned vocabulary
(`person`, `face`, `shows`, `depicts`, `contains`) does not appear in a claim.

### 6.2 No second evidence model, no parallel video abstraction

GROUND reuses `Evidence`, `Modality`, `Source`, `IntervalSet` and `VideoMemory`
unchanged. Its output records directly into the memory store and lands on the
`Level.GROUND` rung (level 5) of the perception ladder in `engine/memory/store.py` — a
rung the ladder already defined as "span-level verification against raw frames / audio",
with an entry keyed on a `ground` instrument that no module had supplied. `engine.ground.
sample_window` is now registered there explicitly rather than relying on the table's
fallback, which records any unknown instrument at this level.

### 6.3 No model dependency

No VLM, no CLIP, no embeddings, no GPU framework, no network. The first GROUND
milestone is media grounding, not visual intelligence; attaching a model here would
move the cost from decode (measured, §4) to inference (unmeasured) before the bridge
itself was trusted.

### 6.4 No pipeline wiring

`selekyt/pipeline.py` builds a retrieval index but issues no query at run time, so
there is no call site for GROUND in the default run. Inventing a query would be a
retrieval decision, not a grounding one. The composition — `retrieve` then `ground` —
is exercised end-to-end in `tests/test_ground.py::test_a_real_retrieval_answer_flows_through_ground`,
which runs the real `build_index` and `retrieve` over a word-timestamped transcript and
grounds the resulting `RetrievalResult` into real frames.

### 6.5 GROUND does not replace the ingest adapters

`engine.memory.ingest` remains the path for *whole-source* VISUAL memory: it translates
reframe's analysis of the entire media. GROUND is the path for *caller-chosen* windows.
They are complementary; neither supersedes the other, and a consumer that wants both
records both.

---

## 7. What is design, what is future

**Implemented** — interval validation, clamping, merging, the sampling policy, targeted
decode, timestamped VISUAL evidence, provenance, failure reporting, measured metrics,
serialization. 45 tests in `tests/test_ground.py`, all passing.

**Designed but not implemented** — nothing. The scope was deliberately the minimum
viable stage, and nothing in this document describes a GROUND feature that is absent
from the code.

**Future, and explicitly out of scope here** —

- **Visual verification.** Consuming these frames to test a retrieval claim — does the
  window the transcript calls an interview actually contain a face? That is VERIFY, and
  it is the stage this one exists to feed. It will need a vision model, which §6.3
  defers.
- **Frame-level rather than interval-level evidence.** Currently one `Evidence` per
  grounded interval, with all frame paths as artifacts. That was a choice, not a
  constraint: `IntervalSet` represents a zero-length span faithfully (an instant is
  what `engine.memory.ingest.shot_boundaries` already emits per cut), so one item per
  frame — an instant interval at the frame's timestamp, that frame as its only
  artifact — needs no contract change. It is deferred because no consumer has asked,
  and because interval-level items keep the evidence count proportional to the
  retrieval answer rather than to the frame budget.
- **Re-decode avoidance across calls.** No caching of decoded frames between `ground()`
  calls; a caller grounding the same window twice decodes it twice.
- **Adaptive sampling.** The policy is begin/end/middle regardless of content. A window
  spanning a shot change would be better served by sampling the change, which needs a
  detector — out of scope for the same reason §6.3 gives.

---

## 8. Known limitations

- **Frames are lossy JPEGs** (`-q:v 3`). A VERIFY stage needing pixel fidelity should
  re-decode from the source at the timestamps the evidence carries, not trust the
  artifacts; the artifacts exist for an editor and for a quick check.
- **The recorded timestamp is the requested instant, not the frame's presentation
  time.** ffmpeg rounds up to the next frame, so a frame can be up to one frame period
  later than its timestamp. Named in every rationale.
- **`duration` may be given explicitly.** A caller passing a wrong duration can clamp
  incorrectly; the probe is the authority and is used by default.
- **No frame is decoded before the first sample of an interval.** An interval's
  *context* — the frames just outside it — is not captured. Retrieval's pad is where
  that context is supposed to come from, per §6.1 of the retrieval design; GROUND
  receives the padded union and does not pad itself.
- **Deduplication uses a fixed frame period** (`MIN_SAMPLE_GAP_S` = 0.04 s), not the
  source's actual fps. At very low frame rates two samples could be deduplicated that
  are genuinely adjacent frames; `fit_to_stream` uses the real fps, the dedup does not.

---

## 9. What the stage does not claim

GROUND does not mean SeleKyT understands the video visually. It means there is now a
trustworthy physical bridge from a retrieved temporal answer to actual source frames:
the intervals asked for are the intervals decoded, the timestamps are the times sought,
every frame is a file with provenance, and every failure is a reported failure. That
bridge is the foundation for VERIFY.
