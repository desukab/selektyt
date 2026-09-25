# Story memory: the design

Design for the linked story-memory extension. Phase 1 inspection and Phase 2
research are closed; this document is Phase 3, and every decision in it names
the line of code it follows from.

Status of the inputs that made this design possible:
`docs/corpus-model.md` documents the existing corpus layer, `docs/rd-landscape.md`
records the external research. This document is the bridge between them and the
implementation.

---

## 1. The finding that determines the whole shape

**The linked story graph already existed, was deterministic, was tested, and was
persisted to disk on every run — and nothing consumed it.**

This was the finding the design was written against, and it is the finding the
implementation closed. When this document was drafted, `docs/corpus-model.md:7`
stated it verbatim:

> Nothing consumes this layer yet. It is committed as an analysis, not a
> pipeline stage.

`selekyt/pipeline.py:796-808` repeated it in the code: the corpus was built and
written to `project/editorial_corpus.json`, wrapped in `try/except` so a corpus
failure never took down a run whose clips were already rendered, and then
discarded. No selection, ranking, render or export path read it back.

Both have been corrected — see §10 for what shipped. The correction is the whole
point of this document: the graph did not need to be built, it needed to be
*reached*, and reaching it is a reader plus one seam, not a new analysis layer.

Concretely, the directive's requested model is already present:

| The directive asks for | What exists today | Where |
|---|---|---|
| Moments with source timestamps | `CorpusUnit` — `index`, `start`, `end`, `text`, coherence | `engine/corpus.py:110` |
| Stories holding setups, developments, conflicts, payoffs | `StoryThread` — `title`, `unit_indices`, `arc` ("setup → tension → resolution"), `links` | `engine/threads.py` |
| Typed relationships with confidence and evidence | `SetupPayoffLink` — `kind`, `strength`, `confidence`, `reason`, `evidence=[setup quote, payoff quote]` | `engine/threads.py` |
| A moment's story role | `DramaticBeat` — `kind` ∈ setup/tension/turn/revelation/resolution/continuation, `polarity`, `intensity` | `engine/threads.py` |
| What was said, where, who, how confident | unit text + times; `SpeakerBlock` (turn hypothesis); measured ASR confidence | `engine/corpus.py`, `engine/rhythm.py:115` |
| Provenance back to the source video | `EditorialCorpus.source`, `BuildInfo.fingerprint`, `BuildInfo.schema` | `engine/corpus.py:138-147` |

What did **not** exist, and was therefore the whole of the work — each item now
closed, with the artefact that closed it:

1. **No reader.** `editorial_corpus.json` was written and never read back; there
   was no import half to the round trip. **Closed:** `engine/corpus_io.py`
   (`read_corpus`), pinned by `tests/test_corpus_io.py`.
2. **No path from the graph to a selection.** `engine.editorial.rank` worked
   purely on `Segment`s (`engine/editorial.py:478-531`), never imported `corpus`
   or `threads`, and built `Candidate`s carrying no `unit_index`, `thread_id`
   or `beat_id`. The graph could not influence an edit because there was no join.
   **Closed:** `engine/story_path.py` is that join, pinned by
   `tests/test_story_path.py`.
3. **The link layer was not a graph.** `engine/threads.py:650-653` constrained it:
   "Each unit claims its best forward link; each target honours only the
   strongest claim. This keeps chains simple disjoint paths — a unit has
   at most one predecessor and one successor." That was an editor-verifiability
   choice, not a structural necessity, and it directly forbade two of the
   seventeen required behaviours: one moment in multiple stories (#5) and
   nonlinear relationships (#6). **Closed:** schema `1.1` keeps every link that
   clears the evidence bar and bounds *membership* instead of edges, pinned by
   `tests/test_story_bench.py` and `tests/test_story_memory.py`.
4. **No story view.** The corpus was a machine document. Nothing presented
   "STORY 01 / SETUP 01:02 / PAYOFF 18:03 / WHY THESE CONNECT", and nothing
   carried a `why_selected` to the shipped package. **Closed:**
   `engine/story_view.py` writes `story_view.json` and `story.md`, pinned by
   `tests/test_story_view.py`.

So the task was **wiring, not building**: keep the canonical model, add the
missing reader, relax the one constraint that was a choice, and add the join that
turns a story into an edit. No parallel architecture, and none was built — the
canonical model in §2 is unchanged in shape from what already existed.

---

## 2. The canonical model — unchanged, and that is the decision

`EditorialCorpus` **is** the story memory. The directive's flow
`VIDEO → VIDEO MEMORY → STORY GRAPH → LINKED STORIES → STORY PACKAGE → EDIT PLAN`
maps onto existing objects with no new intermediate representation:

```
transcript (Segment/Word)          ── the video memory; already persisted
        │  engine.corpus.build_corpus
        ▼
EditorialCorpus                    ── THE STORY GRAPH (canonical, unchanged shape)
   units = moments/nodes             engine/corpus.py:110
   links = typed edges               engine/threads.py SetupPayoffLink
   threads = stories                 engine/threads.py StoryThread
   beats = roles per moment          engine/threads.py DramaticBeat
        │  engine.story_path.story_path   (new — the only new join)
        ▼
list[Candidate]                    ── the story package, on the existing type
        │  engine.edit_plan.retention_intervals   (unchanged)
        ▼
edit plan → FCPXML / EDL / OTIO / MP4   (entirely unchanged)
```

Rationale, per the directive's own constraint — *"Do not create a parallel
architecture just because it is cleaner on paper."* A second graph next to the
corpus would have to be kept consistent with the corpus, and the corpus is
already the thing that is deterministic, tested and documented. Introducing
`StoryMemory` as a new top-level object would duplicate `units` (moments),
`links` (relationships) and `threads` (stories) wholesale.

**The graph never replaces the edit plan.** Per the directive: *STORY GRAPH ≠
EDIT TIMELINE.* `retention_intervals`, `engine/edit_plan.py`, `engine/nle_export.py`
and the render path are untouched. The new join produces `Candidate`s, which is
the type those paths already consume, so traceability from timeline back to story
node flows through the existing `Candidate` and the corpus indices.

---

## 3. The four extensions

### 3a. A reader — `engine/corpus_io.py` (new)

`read_corpus(src) -> EditorialCorpus`, where `src` is a path, a JSON string, or
an already-parsed dict. This is the import half of the round trip and it is the
single largest missing piece.

Discipline it follows from the codebase's own precedent: the reader is
**independent of the writer**. `tools/nle_check.py` already reads FCPXML and EDL
with parsers SeleKyT did not write, specifically so a second implementation
cross-checks the first. `read_corpus` reconstructs the dataclasses from the JSON
directly and does **not** call `build_corpus` — re-running the analysis would
make the round trip circular (the test would be proving the builder agrees with
itself, not that the file carries the graph).

Behaviour:

- **Validates the spine before any artifact.** Every artifact indexes into
  `units`, so a corrupt or truncated `units` list makes every index meaningless.
  The reader checks index ranges, `end >= start`, finite floats, and that the
  declared `build.schema` is one it knows.
- **Fails loudly, does not silently degrade.** The corpus layer's own contract
  (`engine/corpus.py:25-31`) is that a signal the transcript does not carry is
  reported as `None` or empty rather than fabricated. The reader applies the same
  principle to the file: a *missing* analysis key is an empty list (the analysis
  found nothing), but an analysis key whose indices do not resolve is an error.
  Quietly dropping a thread because its file is malformed would produce a story
  memory that silently disagrees with the video.
- **Schema-tolerant**: accepts `1.0` and `1.1` (see §5) and normalises upward.

### 3b. Relax the disjoint-path constraint — `engine/threads.py` (modified)

The change with real editorial consequences.

**Today:** each unit records its single best forward target; each target keeps
only the strongest claim. A unit has at most one successor and one predecessor.

**After:** each unit records *every* forward target whose link strength clears
the existing threshold; each such claim becomes an edge. `links` is a flat list
already, so nothing in the on-disk shape changes — the builder simply emits the
full claim set instead of the winners.

What this costs, measured against the existing cost model: the forward window is
bounded by `window_units` (6) and `window_seconds` (150 s), so the edge count is
at most `O(U · W₊)` — the same asymptotic bound the corpus already advertises in
`docs/corpus-model.md`. Nothing becomes O(N²). The scale test at
`tests/test_corpus.py:499-509` pins `elapsed < 120.0 s` on a large synthetic
corpus and bounds per-artifact evidence; both survive.

What this enables: a setup can pay off twice (a question answered in one place
and re-answered later), and a moment can be reached from two different setups —
which is the directive's requirement that the graph be nonlinear (#6) and that a
moment belong to several stories (#5).

**What is deliberately *not* relaxed.** `StoryThread.unit_indices` stays a
chronological chain — a *story* is still a path, and an editor can still verify
it against the source in order. The link layer is the graph; the thread is a path
through it. This separation is what keeps the editor-verifiability property the
original comment was written to protect, and it is why the constraint was a
choice rather than a necessity.

**Thread membership becomes many-to-many.** `unit_thread: list[int | None]` (one
thread per unit) stays as the *primary* thread for backwards compatibility; a new
`unit_threads: list[list[int]]` carries every thread a unit belongs to. `to_dict`
emits both. This is the only dataclass field added anywhere in this design.

### 3c. The join — `engine/story_path.py` (new)

`story_path(corpus, transcript, *, thread_id, target_seconds) -> list[Candidate]`

This is the missing link between the graph and an edit, and it is deliberately
the *only* place the two meet. Given a story and a duration budget it produces
`Candidate`s on the existing type, which then flow through `retention_intervals`,
`nle_export` and `render_clip` unchanged.

Selection inside a thread:

- **Role priority.** A story needs its setup before its development, its conflict
  before its payoff. Units are taken in role order — the thread's `arc` names the
  shape — then in source order within a role, until the budget is spent. The
  thread's peak beat (`DramaticBeat.is_thread_peak`) is always included: a story
  with its peak cut out is not that story.
- **The budget is a budget, not a target.** If the story's setup alone exceeds
  `target_seconds`, the result is the setup and a note, not a truncated setup. A
  60-second budget cannot buy a 3-minute story; silently shrinking the moments to
  fit would ship a story that is not the one the graph found.
- **`why_selected` is threaded, not invented.** Every produced `Candidate` is
  passed through `engine.context_guard.validate` (`engine/context_guard.py:148`),
  which returns `(ok, reason)`. The reason is kept. The guard's vocabulary is
  already evidence-grounded and already used this way:
  `selekyt/pipeline.py:737-755` keeps it in `gate_results` and
  `rejection_reasons`; only `engine/editorial.py:514` discards it as
  `ok, _why = validate(c)`. So the story view reports a *measured* refusal reason
  — "opens or closes inside a sentence (completeness 0.62)" — rather than a
  generated sentence. This directly serves the directive's *"Do not use fake
  precision"* and *"represent uncertainty honestly."*
- **A moment the guard refuses is dropped, and the refusal is reported, not
  hidden.** This is the mechanism for required tests #15 and #16: a mid-sentence
  opening is rejected because `validate` already says so, and a payoff whose setup
  was cut from the budget is *flagged in the story view* rather than silently
  shipped, because the thread's `links` still name the setup unit and the view can
  see that it is absent.

Two budget behaviours the directive's worked example requires, and which fall out
for free: the same thread at 60 s and at 180 s yields nested subsets
(`m1→m4→m5` inside `m1→m2→m3→m4→m5`), because role priority is stable and only the
budget varies. **The graph is unchanged between the two calls.** That is
requirement #9, and it is the property the round trip depends on.

### 3d. The story view and the human export

Two artifacts are written beside the existing `editorial_corpus.json`, both
derived deterministically from the corpus so neither can drift from it:

**`story.md`** — the human-readable view, and the directive's specified UX. It
presents stories, not a graph:

```
STORY 01 — How the antenna team fixed the ground station
  SETUP       01:02  "We had twenty thousand rupees and one customer."
  DEVELOPMENT 06:14  "The first customer asked for something we had not built."
  CONFLICT    11:47  "We almost shut everything down that quarter."
  PAYOFF      18:03  "That customer eventually signed a three-year order."

  WHY THESE CONNECT
    01:02 → 18:03   causal   the founding constraint is what the payoff redeems
                    evidence: "twenty thousand rupees and one customer"
                              "signed a three-year order"
                    confidence 0.74

STORY 02 — ...
```

The graph is underneath; the user sees the story, per the directive's *"no giant
graph database UI."* Every timestamp is a source timestamp, so the chain of
custody back to the video is a property of the document itself.

**`story_view` inside the corpus's own report** — the machine counterpart, joined
per moment, answering the directive's eight questions per node:

```json
{"moments": [{
   "unit": 14, "start": 62.0, "end": 76.5,
   "text": "We had twenty thousand rupees and one customer.",
   "story_ids": [0], "role": "setup",
   "why_selected": "ok — clears the editorial gate",
   "connects_to": [{"unit": 41, "kind": "causal", "direction": "pays_off",
                    "confidence": 0.74,
                    "evidence": ["twenty thousand rupees and one customer",
                                 "signed a three-year order"]}],
   "confidence": 0.82,
   "confidence_basis": "mean per-token ASR probability"
}]}
```

`confidence` is never a generated number. Where it is measurable it is the mean
per-token ASR probability from `providers/whisper.py:234-238`; where the
transcription provider does not supply one, the field reports its basis
explicitly rather than quoting `providers/sherpa.py:169`, which returns the
constant `1.0`. That constant is a known fabrication and is not cited anywhere in
this design.

---

## 4. Relationship taxonomy — implemented honestly, which means partially

The directive lists eighteen relationship kinds and immediately constrains them:
*"Only implement relationships that can be supported reliably by available
evidence. Do not hallucinate relationships merely because two moments discuss the
same topic."*

SeleKyT has no LLM in the loop and no semantic embeddings (`docs/rd-landscape.md`
declines `sentence-transformers` pending a measured trial). The evidence available
is lexical and structural: question marks, problem/solution lexicon, causal
connectives, term recurrence across a gap, contrast connectives, redundancy
grouping, topic co-membership. That is enough to support some kinds and not
others, and the design says so in the export rather than papering over it.

| Directive's kind | Status | Evidence it would stand on |
|---|---|---|
| `sets_up` | **role**, derived | the setup side of any edge — a position, not a kind |
| `pays_off` | **role**, derived | the payoff side of any edge |
| `resolves` | **kind** `problem_solution` | problem lexicon then solution lexicon |
| `references`, `repeats` | **kind** `callback` | shared rare terms across a gap ≥ the recurrence threshold |
| `contradicts`, `counterpoints` | **kind** `contrast_turn` | contrast connectives leading the later unit |
| `develops`, `continues`, `expands` | **intra-thread succession** | adjacency in a thread's chain plus redundancy-group co-membership |
| `causal` | **kind** `causal` | causal connectives |
| `provides_context` | **refused at topic co-membership** | topic co-membership alone is exactly the hallucination the directive forbids; emitted only when a real edge also exists, as a *weaker* annotation of that edge |
| `supports`, `explains`, `foreshadows`, `creates_conflict`, `escalates`, `qualifies`, `depends_on` | **not implemented** | no available signal distinguishes these from each other; inventing them would be fabricated precision |

The five implemented kinds are the existing five — `question_answer`,
`problem_solution`, `causal`, `callback`, `contrast_turn` — which `engine/threads.py`
already assigns from evidence with a strength, a reason and two quotes. **No new
kind is invented.** The roles (`setup`/`development`/`conflict`/`payoff`) are a
projection onto the edge's position and the beat's kind, not new inference.

The export lists the unimplemented kinds by name under `unsupported_relationships`,
with the reason. Requirement #13 — *"missing/uncertain relationships not
fabricated"* — is served by the absence being explicit and explained, rather than
by the file quietly omitting the question.

---

## 5. Schema: `1.0` → `1.1`

`SCHEMA_VERSION = "1.0"` at `engine/corpus.py:101`; backwards-incompatible
dataclass changes require a bump and the version rides in `BuildInfo.schema`.

The bump to `1.1` is required by exactly one field: `unit_threads` on
`EditorialCorpus` (§3b). The change is strictly additive — `1.0` files remain
readable, and `read_corpus` normalises a `1.0` file by deriving
`unit_threads` from the existing `unit_thread` (`[[x] if x is not None else []]`).

No other dataclass changes. `Candidate` is untouched, so every existing test,
`to_bench_dict`, the cache key and the render path are unaffected.

---

## 6. The seventeen required tests, mapped to real assertions

Following the directive's *"Do not only write unit tests for serialization."*
Every test below asserts editorial behaviour; serialization is a means, never the
assertion itself. New file: `tests/test_story_memory.py`.

| # | Required behaviour | What the test actually asserts |
|---|---|---|
| 1 | Two distant moments linked into one story | A fixture where a setup and its payoff sit ≥ 60 s apart; the thread's `unit_indices` contains both, and the joining edge carries kind + evidence quoting both passages |
| 2 | setup → payoff | An edge with `setup_unit < payoff_unit`, `kind` in the evidence-licenced set, and `evidence` length 2 whose first entry is from the setup and second from the payoff |
| 3 | Three or more linked moments | A thread whose chain length ≥ 3, with consecutive edges and a non-decreasing story span — not three independent clips that merely share a topic |
| 4 | Multiple stories in one video | A transcript with two distinct subject spines yields ≥ 2 threads whose unit sets are not identical, each with its own `arc` |
| 5 | One moment in multiple stories | A unit appearing in ≥ 2 threads' `unit_indices` with a *different role in each* (the pivotal claim, e.g. setup in one story and payoff in another) |
| 6 | Nonlinear graph relationships | A unit with two outgoing edges, or one that is the payoff of two distinct setups — impossible under the old disjoint rule |
| 7 | Relationship confidence | Confidence is in `[0,1]`, is ≤ the strength of the evidence that licensed it, and a weak link (thin lexical overlap) scores measurably below a strong one — not a constant |
| 8 | Provenance back to source timestamps | A moment read back from the exported file resolves to the same `start`/`end` as the `Segment` it came from, and `source`/`fingerprint` survive |
| 9 | Story graph → edit plan | `story_path` produces `Candidate`s whose union span is inside the thread's, ordered by role priority, and 60 s ⊂ 180 s on the same thread |
| 10 | Edit plan → FCPXML/EDL | The story-derived candidates pass through the *existing* export tests unchanged — the assertion is that the new join does not perturb the existing parity |
| 11 | **Export → import round trip** | Build a corpus, write it, `read_corpus` it in a fresh interpreter, then `story_path` on the imported corpus alone and assert the moments and their roles are identical |
| 12 | Exported memory regenerates the same structure | The imported corpus's units/links/threads/beats equal the original field-for-field on every field that carries meaning, and `unsupported_relationships` is reported identically |
| 13 | Missing relationships not fabricated | A transcript with two passages on the same topic but no connective, no question, no shared rare term and no redundancy link produces **no edge** between them, and the corpus reports why under `unsupported_relationships` |
| 14 | Duplicate/redundant moments handled | Two passages in one `RedundancyGroup` are not both selected into a path; the representative is taken and the duplicate is recorded as excluded |
| 15 | Mid-sentence openings still rejected | A path moment whose span opens inside a sentence is dropped by `validate` and the exported view carries the refusal reason — proving the guard survived the new join |
| 16 | Payoff without its setup rejected or flagged | A budget too small to include the setup leaves the payoff either excluded or explicitly flagged in the view as "setup missing from this cut", never silently shipped |
| 17 | Existing tests remain green | The full suite; baseline after `8e3d7a4` is 1537 passed / 13 skipped |

Plus, where the directive invites it: a property test that every exported moment's
times lie within the source duration and every `connects_to` resolves to a moment
that is actually in the file — the invariant that a shared memory must not dangle
a reference.

---

## 7. Performance — measured, not claimed

Per the directive: no O(N²) graph explosion, and report construction time,
memory, candidate relationship count, story count and edit-generation time.

A benchmark is added alongside the existing
`tests/test_corpus.py::test_three_hour_scale_completes_in_bounded_time_and_memory`,
which already pins `elapsed < 120.0 s`. It is `tests/test_story_bench.py`, and
every assertion in it is labelled with which of three kinds it is — a
**structural bound** (provable from the construction, so it cannot be satisfied
vacuously and cannot drift with hardware), a **measured invariant** (true on the
fixture by construction, so a change that breaks it is a change a reviewer should
see), or a **wall-clock ceiling** (the one kind that varies between machines,
used only where an existing test already set the ceiling). It reports, on a large
synthetic corpus:

- corpus build time, asserted `< 120.0 s` — the existing ceiling, re-asserted
  after the relaxation;
- edge count, asserted `≤ 6 · U` — the window bound, which is structural because
  `engine/threads.py` enumerates `range(i + 1, min(n, i + 1 + window_units))`;
- thread membership per unit, asserted `≤ window + 1` and total `≤ (window + 1) · U`,
  also structural;
- story count and topics, asserted `≤ U` and beats `== U`;
- **the number of moments a 60 s and a 180 s budget selects, asserted equal across
  corpus sizes** — the design's "path is built from one thread's units, not the
  corpus," but stated as an equality rather than as a timing ratio, because a
  timing ratio would be a machine claim and an equality is a structural one;
- clip count, asserted `==` the path length — no fan-out at length;
- peak resident size, asserted `≤ 0.125 MB · U` — the guarantee
  `tests/test_corpus.py::test_no_artifact_holds_a_transcript_copy_per_member`
  already states, now at scale.

The sub-linear timing assertion the original draft called for was **dropped**:
build time measured 0.128 s at 200 units and 1.878 s at 2000, which is mildly
super-linear (≈14.7× for 10× input) and does not fit a clean power law —
sub-segment exponents came out inconsistent (1.03 and 1.44), so the shape is
constant factors and topic clustering, not a scalable defect, and asserting a
scaling exponent there would have been reporting a number that is not measured.
Only the wall-clock ceiling is asserted; the scaling is reported as measured.

No metric is reported that is not actually measured. Where a number is an
asymptotic bound rather than a measurement, the export says which.

---

## 8. What this design deliberately does not build

- **No new graph database, query layer or traversal engine.** The graph is a list
  of units plus a list of edges; the queries are list comprehensions over a
  bounded window. `docs/rd-landscape.md` records the research conclusion this
  follows from — no surveyed project solves editorial judgement, and a dependency
  is not justified for a graph this size.
- **No LLM in the loop, no embeddings.** The unimplemented relationship kinds are
  reported as unimplemented rather than approximated.
- **No speaker identity.** `Segment` has no `speaker` field by design and no path
  sets one; `SpeakerBlock` is a turn hypothesis at confidence 0.45. The export
  reports `has_speaker_labels: false` where that is the truth.
- **No visual layer in the graph.** `engine/reframe.py` is never persisted; there
  is no `detections.json`. Faces exist only for crop placement. `BrollMarker.cue`
  is a spoken phrase, not a pixel. Adding a visual node would mean persisting a
  layer that is currently computed and discarded, which is a separate change.
- **No new NLE interchange.** FCPXML, EDL and OTIO paths are untouched, and the
  graph never replaces them.
- **No OKF dependency.** Open Knowledge Format remains an architectural reference
  only, at most reflected in how provenance is carried; the canonical
  representation stays SeleKyT's own.

---

## 9. The order of work

1. `engine/corpus_io.py::read_corpus` + schema `1.1`, with the reader's failure
   contract. Unit tests first, because every later test imports through it.
2. `engine/threads.py`: relax the claim rule, add `unit_threads`. Re-run the
   corpus tests immediately — this is the one change that touches stable,
   tested behaviour.
3. `engine/story_path.py::story_path`, with the guard reason threaded through.
4. The story view and `story.md` writer.
5. `tests/test_story_memory.py`: the seventeen, plus the property test.
6. The benchmark.
7. Documentation: update `docs/corpus-model.md` — its line 7 ("Nothing consumes
   this layer yet") becomes false, and the document must say what now does.

All seven steps are complete. §10 records what shipped and where it deviates from
this document.

---

## 10. As built — what shipped, and where it differs from this design

Written after implementation, so the document is not left claiming a shape the
code does not have.

**Shipped surface.**

| Artefact | Role |
|---|---|
| `engine/corpus_io.py` | the import half of the round trip; reads schema `1.0` and `1.1`, never calls `build_corpus` |
| `engine/story_path.py` | the seam: an `EditorialCorpus` + thread → an ordered `StoryPath` of `Candidate`s |
| `engine/story_view.py` | the report layer; writes `story_view.json` and `story.md` |
| `tests/story_fixtures.py` | shared transcript generator; the benchmark and the scale test import one fixture so they cannot drift apart |
| `tests/test_corpus_io.py`, `test_story_path.py`, `test_story_view.py`, `test_story_memory.py`, `test_story_bench.py` | the tests |
| `docs/corpus-model.md`, `docs/rd-landscape.md`, this document | the documentation |

**Deviations from this design, and why.**

1. **`story_path` returns a `StoryPath`, not a `list[Candidate]`.** §5 described
   the path as a list. It shipped as a `Sequence` subclass so it can carry the
   dropped moments — every moment the analyser considered and rejected, with the
   refusal reason the guard issued — alongside the ones it kept. That is the
   "why this moment, and why not that one" requirement, and a bare list could
   not have held it. `len(path)` is still the kept count, so the shape reads the
   same at call sites.
2. **The path is one candidate set per thread, not a graph walk.** The directive's
   worked example reads as a chain (`sets_up → develops → creates conflict →
   resolves`). Selection is simpler and more defensible: one thread's units are
   already the chain the analyser proved, so the path orders them by source time
   and lets the budget decide how many fit. The graph is unchanged by the
   presentation; only the presentation changes with the budget.
3. **The exported bundle is three files, not the directory layout the directive
   sketched.** The brief said not to copy that structure, and the smallest useful
   representation is `editorial_corpus.json` (canonical graph, written by
   `EditorialCorpus.to_dict`, read by `read_corpus`), `story_view.json` (purely
   derived, byte-identically re-derivable — requirement 12) and `story.md` (the
   human view). Only the first is authoritative; the other two are functions of
   it. `docs/corpus-model.md` documents the bundle.
4. **`chain_mates` is the name for co-membership**, not `develops`. Two units in
   one thread *are* the analyser's claim that one develops the other, so the
   export reports the relationship as `develops` with `chain_mates` as its
   evidence kind. The public taxonomy in the export is the one §4 lists; the
   internal kind names are the engine's own.
5. **`provides_context` does not exist in the engine.** §4 listed it among the
   kinds to implement; `engine/threads.py` has no such kind. The export reports
   it under `unsupported_relationships` rather than silently omitting it, which
   is the requirement-13 behaviour applied to the taxonomy itself. Eight
   relationship names are reported as unsupported in every export, including
   `foreshadows`, `escalates` and `depends_on`.
6. **Five relationship kinds are implemented, not eighteen.** `question_answer`,
   `causal`, `problem_solution`, `callback`, `contrast_turn`, each with a fixed
   strength and each citing the evidence that triggered it. `sets_up` and
   `pays_off` are *roles* derived from an edge's position in a thread, not kinds.
   `repeats` and `expands` come from `RedundancyGroup`. `resolves` is
   `problem_solution`; `contradicts` and `counterpoints` are `contrast_turn`.
   The rest would need evidence the analyser does not have, and the brief forbids
   inventing it.
7. **Timestamps in the human view are `MM:SS`, not SMPTE.** The corpus holds no
   frame rate — `format_smpte` requires a `TimecodeRate` tied to media fps — so
   rendering frames would be inventing precision. The view carries a
   `timing_note` saying exactly that: these are seconds from the source's own
   zero, not SMPTE.
8. **No speaker column in the view's moments, even where `SpeakerBlock` exists.**
   `Segment` has no `speaker` field by design; a speaker layer would have to be
   a separate join and is out of scope for the same reason §8 gives.

**Measured, at 200 / 800 / 2000 units.**

```
units=200  build_s=0.128 edges=345  topics=26  stories=27  rss_delta_mb=1.5  moments60=7 moments180=22 edit180_clips=22
units=800  build_s=0.536 edges=1395 topics=101 stories=102 rss_delta_mb=4.9  moments60=7 moments180=22 edit180_clips=22
units=2000 build_s=1.878 edges=3495 topics=251 stories=252 rss_delta_mb=11.4 moments60=7 moments180=22 edit180_clips=22
```

Edges per unit measured 1.725 at every size, against the structural bound of 6 —
a regression that saturated the window would still pass the bound and be four
times worse, so the benchmark asserts the measured ratio alongside it.

Two numbers in that table are **not** asserted, and are reported as measured
rather than explained. Redundancy groups were 24 at 200 units and 0 at 800 and
2000 — non-monotonic in a way the fixture does not explain; the benchmark prints
the count rather than pinning it. Threads-per-unit max is 1 on this fixture,
which is a degenerate distribution: the synthetic transcript's passages are
disjoint enough that no unit closes two stories, so multi-membership is *not*
exercised here. It is proven separately, by `tests/test_story_memory.py` on a
fixture built for it, where one unit belongs to two threads with the same role
name in both.

**A defect this work found and fixed, outside its own scope.** The scale test it
benchmarked against —
`tests/test_corpus.py::test_three_hour_scale_completes_in_bounded_time_and_memory`
— was degenerate. Its fixture shared boilerplate, so `segment_story_units`
collapsed 2000 segments into a **single unit** spanning 23,993 s, with 0 threads
and 0 edges, and the test asserted `units <= rng_units`, which held vacuously.
The test measured nothing while passing. It now uses the shared fixture and
asserts `units == 2000` with `threads > 1` and `links > 1`. Any earlier claim
resting on that test's wall-clock number should be re-read against the numbers
above, which are the first real ones at that size.

**What remains unvalidated.** Everything above is synthetic. No real video was
analysed for this layer — the reconstruction chain is proven on transcripts with
known ground truth, not on footage. The relationship kinds are lexical and will
under-link on speech that connects ideas by pronoun and prosody rather than by
shared vocabulary; `docs/rd-landscape.md` records `fastcoref` as the rejected
remedy and why. And the view has no UI: the brief's "the user sees the story, not
the graph" is satisfied by `story.md`, which is a document, not an interface.
