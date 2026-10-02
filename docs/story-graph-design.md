# The story graph layer: audit, gap and design

Status: **Phase 3 design document.** This is an audit-and-design track, not a
build: the corpus layer is committed, deterministic, tested and schema-locked at
`1.1`, and the mission's directive is explicit that it be extended rather than
rewritten. Every claim below was verified by reading the module cited; where a
claim is inferred rather than quoted it is marked **[inference]**.

Scope of the question: the mission asks for a story graph whose nodes are of
eight kinds — **setup / context / development / conflict / turn / peak / payoff /
resolution** — each one *evidence-backed*: a node carries the evidence that
supports it, not just a label. `engine/corpus.py` and its satellites already
compute a substantial story structure lexically and deterministically. So the
first job is to establish, per kind, what already exists, what exists under
another name, and what does not exist at all.

---

## 1. The audit

### 1.0 The records on trial

Every record below was read in full. Fields are quoted, not paraphrased.

| Record | Module | Role |
|---|---|---|
| `CorpusUnit` | `engine/corpus.py:110-135` | the spine; one coherent passage, indexed |
| `Topic` | `engine/topics.py:60-83` | subject cluster over the whole corpus |
| `StoryThread` | `engine/threads.py:323-339` | an ordered narrative chain |
| `DramaticBeat` | `engine/threads.py:282-303` | a unit's dramatic function |
| `SetupPayoffLink` | `engine/threads.py:252-270` | one typed setup→payoff edge |
| `RedundancyGroup` | `engine/redundancy.py:64-77` | passages that re-say each other |
| `SpeakerBlock` | `engine/rhythm.py:114-125` | speaker or turn stretch |
| `BrollMarker` | `engine/threads.py:306-320` | transcript-licensed visual opportunity |
| `RhythmProfile` | `engine/rhythm.py:94-111` | corpus-wide cadence + per-unit prosody |

The closed vocabularies, quoted from the code rather than guessed:

- `DramaticBeat.kind` — the comment at `engine/threads.py:294` reads
  `# setup | tension | turn | revelation | resolution | continuation`.
  **The code that assigns it never produces `"setup"`** — see §1.1.
- `DramaticBeat.polarity` — `engine/threads.py:295`,
  `# positive | negative | neutral`.
- `SetupPayoffLink.kind` — `engine/threads.py:262`,
  `# question_answer | problem_solution | causal | callback | contrast_turn`.
- `BrollMarker.kind` — `engine/threads.py:316`,
  `# demonstration | deictic | illustration | reaction`.
- `RedundancyGroup.relation` — `engine/redundancy.py:71`,
  `# restatement | elaboration | recap`.
- `StoryThread.arc` — `engine/threads.py:335`, a *string*,
  `"kind sequence, e.g. \"setup → tension → resolution\""`.

### 1.1 Verdicts per node kind

**setup — PARTIAL, and the partiality is a bug in the documentation, not the code.**
A setup is represented *positionally*, twice over: as the `setup_unit` side of a
`SetupPayoffLink` (`engine/threads.py:260`, with its own `setup_start` /
`setup_end` at `:264-265`), and as the head of a chain —
`starts = [i for i in range(n) if i not in has_predecessor]`
(`engine/threads.py:696`), i.e. a unit no edge points into. But `DramaticBeat.kind`
never takes the value `"setup"`. The assignment logic at
`engine/threads.py:487-496` is an exhaustive ladder — `revelation`, else
`tension`, else `turn`, else `resolution`, else `continuation` — with no branch
for setup, and no positional fallback. Verified empirically: across the
12-passage synthetic fixture in `tests/story_fixtures.py` the emitted kind set is
`{'continuation', 'resolution', 'tension'}`, and a purpose-built four-passage
transcript with an explicit setup produced `{'tension'}`. `"setup"` appears in
exactly four places in `engine/threads.py` — the module docstring (`:39`), the
`DramaticBeat.kind` comment (`:294`), the `StoryThread.arc` comment (`:335`), and
prose inside `_link_candidates` — and in zero assignments. The consequence is
not cosmetic: `engine/story_path.py:55-62` ranks roles for budget triage with
`"setup": 0`, its *highest* priority, and `role_in_thread`
(`engine/story_path.py:190-192`) returns `corpus.beats[unit.beat_id].kind`, which
can never be `"setup"`. **The top-priority role slot in the only join between the
graph and an edit is dead code.**

**context — PARTIAL, and the partiality is structural.** Subject context is
`Topic` (`engine/topics.py:60-83`): `unit_indices` (chronological members,
`:71`), `segment_indices` (source segments, `:72`), `runs` — "how many separated
stretches it occupies" (`:75`) — and `recurring` (`:81-83`, `runs > 1`). Visual
context is `BrollMarker` (`engine/threads.py:306-320`), but only where the
transcript overtly points at something: "Never invented: the `cue` is quoted
verbatim from the source" (`:310-312`), and "An empty list is the correct result
when none of these appear" (`:828-829`). What is missing is *span* honesty: a
`Topic` with `runs == 3` still reports one bounding span — `start: float # first
source second of the topic`, `end: float # last source second of the topic`
(`engine/topics.py:73-74`) — so the two gaps between the three runs are inside
the reported span and invisible. Separately, `provides_context` is one of the
*refused* relationship kinds: "topic co-membership alone is exactly the
hallucination the directive forbids... so it is never emitted"
(`engine/story_view.py:117-121`). So context exists as a clustered subject, not
as a node kind, and the layer's own taxonomy declares it unrepresentable as a
relation.

**development — ABSENT as a kind; present only as untyped adjacency.** There is
no beat kind, link kind or field for development anywhere in the corpus. What
exists is succession inside a chain: `StoryThread.unit_indices`
(`engine/threads.py:330`, "chronological chain"). The layer's own export is
candid about what that is worth — `story_view.py:96-101`, for the kind
`develops`: "co-membership inside one story's chain, plus any redundancy group
both moments belong to; labelled as chain membership, **never as an inferred
edge**". A development node cannot be built today without inventing the very
inference the export refuses to claim.

**conflict — ABSENT, and the nearest existing thing measures a different
quantity.** `DramaticBeat.polarity` (`engine/threads.py:295`) is lexical *valence*:
`_polarity` (`engine/threads.py:434-442`) counts positive/negative lexicon matches
plus the mere presence of solution/problem cues, and returns
`positive` / `negative` / `neutral`. Valence is "is this passage phrased
upbeat or downbeat"; conflict is "who wants what that someone else opposes".
Nothing in the corpus holds the latter. `creates_conflict` is a refused kind:
"would need a stance model to see conflict that is not yet on the page"
(`engine/story_view.py:128-132`). The closest supported signal is *reversal* —
the `contrast_turn` link kind (`engine/threads.py:580-584`), fired by a leading
contrast connective — which is a turn, not an opposition. And the "who" is
structurally unavailable: `SpeakerBlock.speaker` is documented `# None when the
transcript has no labels` (`engine/rhythm.py:121`) and the common case has none
(see §1.2), so even a genuine disagreement cannot be attributed to two parties.

**turn — COVERED, on lexical evidence.** `DramaticBeat.kind == "turn"` is
assigned when a contrast cue is present and no revelation or problem cue outranks
it (`engine/threads.py:491-492`). The cue must *lead* the unit: contrast is
matched against `text[:160]` (`engine/threads.py:647-648`), and the comment is
explicit that "a *leading* contrast/result is what marks a turn". The edge
counterpart is `contrast_turn` (`engine/threads.py:580-584`), strength `0.55`,
reason text naming the overlap and the marker. Evidence-backed: `cues`
(`engine/threads.py:300`) are the verbatim phrases that fired, capped at six
(`:499`).

**peak — COVERED, and the best-instrumented kind in the corpus.** Three
independent measurements: `intensity` on "a saturation scale (0..1) independent
of corpus length" (`engine/threads.py:286`, computed `:506` as
`1.0 - 1.0/(1.0 + raw)`); `percentile` — the within-corpus rank, from one sort
(`:474-477`); and the two flags `is_thread_peak` / `is_corpus_peak`
(`engine/threads.py:298-299`), set at `:785-787` and `:479`/`:509`. Peaks surface
in the editor's first-read paragraph too: `_arc_summary` reports "highest
dramatic intensity at {pos}% of runtime ({peak.kind}, intensity {...})"
(`engine/corpus.py:655-661`). The raw weights are open at
`engine/threads.py:465-476` — revelation `0.45`, problem `0.35`, a real question
mark `0.35`, contrast `0.25`, intensifier `0.15` capped at three occurrences.

**payoff — COVERED positionally, with the strongest evidence contract in the
layer.** `SetupPayoffLink.payoff_unit` (`engine/threads.py:261`) with its own
`payoff_start` / `payoff_end` (`:266-267`); a chain's terminal member bounds the
thread (`engine/threads.py:726`); and every edge carries `kind`, `strength`
(`:263`), `confidence` (`:268`), `reason` (`:269`) and
`evidence: list[str] = [setup quote, payoff quote]` (`:270` — the comment states
the two-element contract). Note the honest ceiling: confidence is
`round(min(0.95, strength), 3)` (`:679`) — a link can never report certainty,
because lexical evidence is not certainty.

**resolution — COVERED.** `DramaticBeat.kind == "resolution"` fires on solution or
result cues (`engine/threads.py:493-494`); the edge counterpart is
`problem_solution` (`engine/threads.py:574-578`), strength `0.60`, requiring
trouble vocabulary in the setup and resolution vocabulary in the payoff on the
same subject. Also the `callback` kind (`:586-591`) — a subject returning after
`abs(j - i) - 1` intervening units — which is a *delayed* resolution and the
corpus's own answer to "set up in one region, paid off in another", within the
reach limit (§2.2).

### 1.2 The availability contract — what degrades, and how the graph must not hide it

The corpus's own contract, `engine/corpus.py:33-44`:

> Signals the transcript does not carry are reported as `None` or an empty list
> — never as a fabricated value.

Two guarantees a story graph over this corpus must respect, because they hold on
the corpus's real sources:

1. **`RhythmProfile.available` is `False` without word timestamps**
   (`engine/rhythm.py:98`, `# False when no word timestamps at all`). The
   pipeline note at `engine/corpus.py:552-556` spells out the consequence:
   "pause classification, speech rate and pause-derived B-roll markers are
   unavailable rather than estimated". Two further degradations follow that the
   notes *do not* name, and a graph that assumes prosody exists will be silently
   wrong on them:
   - **Beat intensity loses its prosodic component.** `_raw_intensity`
     (`engine/threads.py:455-476`) adds `0.15 * rhythm.rush` and a further
     `0.15` when `emphatic_pause >= 0.9` — but only `if rhythm is not None`
     (`:471-475`). On an untimed transcript every beat is up to `0.30` quieter,
     so `percentile`, `is_thread_peak` and `is_corpus_peak` are all computed
     against a compressed scale. Peaks still exist; they are *less
     discriminating*, and nothing in the emitted record says the scale was
     prosody-free.
   - **The `reaction` B-roll kind disappears entirely.** `mark_broll` gates the
     timing-derived kind behind `if rhythm_by_unit and beat_by_unit`
     (`engine/threads.py:865`), with the reason inline: "Requires word
     timestamps (rhythm), so it degrades to absent when they are missing."
2. **`SpeakerBlock.speaker` is always `None` without speaker labels.**
   `models.core.Segment` has no speaker field (`engine/corpus.py:39-41` notes
   this is "the common case"); a label attached by an ASR backend is carried
   through defensively at `engine/corpus.py:248-252`. When absent, the blocks are
   documented as *turn* hypotheses (`engine/corpus.py:41-42`, and the run note at
   `:557-562`). The export names the consequence honestly: with no labels every
   moment is `_UNATTRIBUTED` with basis "this source has no speaker labels, so
   every moment is unattributed" (`engine/story_view.py:349-352`, and the
   report-level note at `:483-486`).

A third degradation is visible in the unit record rather than the analyses:
`CorpusUnit.timing_source` is `"word_aligned"` only when words exist *and* every
sentence anchored (`engine/corpus.py:451-453`); otherwise it is
`"character_proportional"`, and `engine/corpus.py:458-463` reports that ASR text
and the word stream can disagree. **A story graph node that quotes a span is
quoting a span of measured or estimated precision, and the node must say which.**

### 1.3 Does anything actually consume `editorial_corpus.json`?

`docs/product-gap-map.md` says `engine.corpus` "was originally a finished, tested
analysis that nothing consumed". **Verified from the code: the claim is true as
history, outdated as a statement of readers, and still true for the render path.**

- The write is at `selekyt/pipeline.py:894-907`. `build_corpus` runs, the dict is
  written to `project/editorial_corpus.json`, and the whole block is inside
  `try/except Exception` that prints to stderr and continues (`:906-907`) —
  deliberately, so a corpus failure cannot take down a run whose clips are
  already selected and rendered. The report then names the path only if the file
  landed: `"editorial_corpus": str(corpus_path) if corpus_path.is_file() else None`
  (`:1145`). The built object is not retained; nothing after line `:907` in the
  pipeline uses it.
- Readers *do* exist: `engine/corpus_io.read_corpus`
  (`engine/corpus_io.py:333`, accepts a path, JSON string or dict, supports
  schemas `1.0` and `1.1`), `engine/story_path.story_path`
  (`engine/story_path.py:203`) and `engine/story_view.story_view` /
  `write_story_memory`.
- **But no production code calls them.** Every call site of `story_path(`,
  `story_view(` and `write_story_memory(` outside their own definitions is in
  `tests/` — `tests/test_story_path.py`, `tests/test_story_bench.py`,
  `tests/test_story_memory.py`, `tests/test_story_view.py`. There is no call site
  in `selekyt/` (`pipeline.py`, `cli.py`, `server.py`), in `tools/`, or in the
  render/export modules (`engine/render.py`, `engine/editorial.py`,
  `engine/edit_plan.py`, `engine/nle_export.py`). `engine/story_path.py:3-4`
  calls itself "the only place the story graph and an edit meet", and it is —
  but today that place is reached only by a test. `docs/corpus-model.md:9-16`
  states the correction ("Something consumes this layer now") and is right about
  the *code* and wrong about the *pipeline*.

**Conclusion for the design:** the gap is not "no reader". It is that the graph
has never influenced a shipped clip. A story graph layer designed today must
assume it will be consumed, and must therefore carry its own evidence — because
the consumer will not be the builder, and a consumer that cannot see the evidence
cannot be expected to trust the label.

---

## 2. The gap — what a story graph over this corpus cannot express

### 2.1 There is no node record

The corpus has *moments* (`CorpusUnit`) and *per-moment functions*
(`DramaticBeat`), and it has *edges* (`SetupPayoffLink`) and *paths*
(`StoryThread`). It does not have nodes in the mission's sense, for three
reasons that compound:

1. **The kind set is closed and does not match.** `DramaticBeat.kind` takes five
   realised values (§1.0) where the mission names eight kinds. `development`,
   `conflict` and `context` have no kind at all; `setup` is documented but
   phantom (§1.1).
2. **Kinds are per-unit and exclusive.** `build_beats` emits exactly one beat per
   unit (`engine/threads.py:481-515`), and `CorpusUnit.beat_id`
   (`engine/corpus.py:133`) holds one id. A passage that is simultaneously a
   story's payoff and another's setup — the case `unit_threads` exists for,
   `engine/corpus.py:191-193` — has one beat with one kind. The corpus solved the
   *membership* problem (a unit may belong to several threads,
   `engine/threads.py:349-353`) and left the *function* problem unsolved: a
   moment's role is still singular.
3. **Positional kinds leave no evidence trail.** `setup` and `payoff` are
   relations on an edge, not properties of a moment. The evidence lives on
   `SetupPayoffLink` (`engine/threads.py:268-270`), and a query that asks "what
   evidence supports calling *this moment* a setup" must first find every edge
   that names it — the corpus offers no index for that, only a linear scan.

### 2.2 Can it represent a thread set up in one region and paid off in another?

**Partially, and only within a bounded reach.** Yes mechanically: a
`SetupPayoffLink` carries both halves with independent timecodes
(`engine/threads.py:264-267`), and `Topic.unit_indices` is chronological across
the whole corpus (`engine/topics.py:71`), so a subject discussed at minute 4 and
again at minute 90 is one topic with `runs == 2`. The `callback` kind exists
precisely for this (`engine/threads.py:586-591`).

But the reach is hard-capped, and the cap is editorial, not computational:
`build_threads(..., window_units=6, window_seconds=150.0, min_strength=0.40)`
(`engine/threads.py:604-606`), with the forward loop breaking when either bound
is exceeded (`:666-668`). The docstring defends this — "a payoff three hours
after its setup is not inferable as a relation, and an unbounded search would be
O(U²)" (`engine/threads.py:612-614`). **[inference]** That trade-off is correct
for inference and wrong for *representation*: a graph that cannot even *hold* a
3-hour setup→payoff cannot represent the structure of a long-form interview even
where a human editor would assert it from outside the lexical evidence. A node
layer should be able to carry such a relation with `basis = unsupported` rather
than be unable to carry it at all.

A second, quieter limit: `callback` requires `same_topic and separated` where
`separated = abs(j - i) >= 3` (`engine/threads.py:557, 587`) — a *unit-distance*
test. On a source with long units, three units can be twenty minutes; on one with
short units, three units can be six seconds. The threshold is not time-aware.

### 2.3 Can it represent conflict at all, or only polarity?

**Only polarity, and polarity is not conflict.** Restated adversarially:

- `_polarity` (`engine/threads.py:434-442`) counts lexicon hits. A passage with
  two negative words and one solution cue scores `negative`. It says nothing
  about an opposing party, a stake, or an unresolved question — and it is
  recomputed per unit in isolation, so it cannot express *tension between* two
  passages even in principle.
- A unit whose polarity is `neutral` is explicitly reported as such rather than
  guessed (`engine/threads.py:531-532`: "polarity neutral: no lexicon match, not
  guessed"). On the corpus's own Telugu and Hindi sources, where the lexicon is
  script-range-bounded, `neutral` is the common outcome — so even valence is
  thin there. **[inference, from the multilingual lexicon ranges at
  `engine/threads.py:87-214` and the script-detection note at
  `engine/corpus.py:511-513`]**
- Opposition requires at least two attributed parties. `SpeakerBlock` provides
  boundaries but `speaker: str | None = None` on this corpus
  (`engine/rhythm.py:121`, §1.2). So the precondition for representing conflict —
  *who* — is unavailable, and the layer correctly declines to guess.

The honest graph says `conflict` is a kind it *cannot* support, and says why,
rather than relabelling valence.

### 2.4 Is `arc` on `StoryThread` a value or a label?

**A label — and a lossy one, whose own docstring example is unreachable.**
The assignment, `engine/threads.py:779-784`:

```python
kinds = []
for bi in t.beat_indices:
    k = beats[bi].kind
    if not kinds or kinds[-1] != k:
        kinds.append(k)
t.arc = " → ".join(kinds) or "continuation"
```

Three properties, all load-bearing for a node design:

1. **It is a string**, not a structure. There are no ids in it, no per-step
   evidence, no link references. It cannot be queried or joined; it can only be
   read.
2. **It cannot contain `"setup"`**, because it is a join of beat kinds and
   `"setup"` is never a beat kind (§1.1). The comment at
   `engine/threads.py:335` — `"kind sequence, e.g. \"setup → tension →
   resolution\""` — describes an output the code cannot produce. Measured on the
   synthetic fixture: arcs are `tension → resolution`, `continuation`,
   `continuation`, `continuation`.
3. **The adjacent-duplicate collapse (`kinds[-1] != k`) destroys structure.**
   A chain `tension → tension → resolution` and a chain
   `tension → resolution` produce the identical arc. For a story graph this is
   the one place the corpus *had* the variable-cardinality information — two
   separated tension runs — and flattened it into a label indistinguishable from
   one tension run. That is exactly the variable-cardinality temporal shape
   `engine/memory/intervals.py:30-34` says every temporal answer should be, and
   the corpus has it and throws it away.

### 2.5 Nothing in the corpus holds a set of intervals

Every temporal record in the layer is a single span. `CorpusUnit.start/end`
(`engine/corpus.py:121-122`), `Topic.start/end` (`engine/topics.py:73-74` — one
bounding span for `runs` stretches), `StoryThread.start/end`
(`engine/threads.py:333-334`), `DramaticBeat.start/end` (`:292-293`),
`BrollMarker.start/end` (`:314-315`), `SpeakerBlock.start/end`
(`engine/rhythm.py:120-121`). `SetupPayoffLink` has two spans but they are two
named halves of one relation, not a set.

The one place the corpus is genuinely variable-cardinality is `unit_indices` — a
thread's chain, a topic's members, a redundancy group's `members`
(`engine/redundancy.py:70`). But those are *index* lists whose temporal extent
must be reconstructed by walking `corpus.units`, and the reconstruction is where
the `Topic.start/end` bounding-span problem lives: the spans between a topic's
runs are not recoverable as *gaps* without re-deriving the unit times.

---

## 3. The design — an evidence-bearing story node

Design constraints, all inherited rather than chosen: deterministic and pure
(`engine/corpus.py:48-52`); ML-free and buildable without an ollama server (the
`engine/llm_editorial.attach_metadata` path is a no-op without one — see
`engine/llm_editorial.py`); no new vocabulary that competes with the temporal
algebra already landed in `engine/memory/`; and no parallel architecture beside
`EditorialCorpus`, which `docs/story-memory-design.md:75-99` already commits to as
the canonical graph.

The node layer is therefore a **projection over the corpus**, built by a pure
function from an `EditorialCorpus`, holding no transcript text and no copy of any
corpus record — the same discipline the corpus applies to itself
(`engine/corpus.py:62-66`: "No topic, thread or group holds a copy of the
transcript — they hold indices").

### 3.1 The record

```python
# engine/story_nodes.py  (proposed; documented here, not built)

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

from engine.memory.intervals import ALLEN_RELATIONS, Interval, IntervalSet
# Interval/IntervalSet are the temporal vocabulary this layer reuses. The
# interface needed, all of which already exists:
#   IntervalSet.of(*spans)          engine/memory/intervals.py:260
#   IntervalSet.cardinality         :278  (cardinality is data, not an optimisation)
#   IntervalSet.duration            :285
#   IntervalSet.gaps(bounds)        :376  (the Topic.runs problem, solved)
#   IntervalSet.overlaps(span)      :343
#   IntervalSet.intervals_in(span)  :347
#   allen(a, b) / converse(name)    :227 / :90  (typed node links)


class NodeKind(str, Enum):
    """The eight kinds the mission names. ``str`` so a node serialises without a
    custom encoder, following ``engine.memory.evidence.Modality`` (:64)."""
    SETUP = "setup"
    CONTEXT = "context"
    DEVELOPMENT = "development"
    CONFLICT = "conflict"
    TURN = "turn"
    PEAK = "peak"
    PAYOFF = "payoff"
    RESOLUTION = "resolution"


class NodeBasis(str, Enum):
    """How the kind was established. This is the honesty the audit demands: the
    corpus's own taxonomy already separates kinds it infers from evidence, roles
    it derives from position, and kinds it refuses (engine/story_view.py:66-80).
    A node that hid the difference would relabel position as inference."""
    QUOTED = "quoted"            # a record's own field says this
    POSITIONAL = "positional"    # derived from edge position / chain membership
    DERIVED = "derived"          # a new computation over existing records
    UNSUPPORTED = "unsupported"  # asserted absent, with the reason kept


@dataclass(frozen=True)
class NodeLink:
    """One typed edge between nodes. ``relation`` is an Allen name, validated on
    construction exactly as ``Evidence.link`` validates its relation
    (engine/memory/evidence.py:200-223), so a typo in the graph is a loud error
    rather than an unmatchable string."""
    target_node_id: str
    relation: str               # one of ALLEN_RELATIONS (intervals.py:74-77)
    evidence_id: str            # the evidence backing the *link*, not just the node
    converse_recorded: bool     # the reverse edge was built via converse(), not hand-written

    def __post_init__(self) -> None:
        if self.relation not in ALLEN_RELATIONS:
            raise ValueError(f"not an Allen relation: {self.relation!r}")


@dataclass(frozen=True)
class StoryNode:
    """One evidence-backed story node: a kind, the spans where it holds, and the
    evidence that backs it — never a kind alone."""

    node_id: str                # stable: "{kind}:{digest of unit indices and basis}"
    kind: NodeKind
    basis: NodeBasis            # quoted | positional | derived | unsupported

    # ── where: variable cardinality by construction ──
    intervals: IntervalSet      # the spans this node holds; cardinality is data
    unit_indices: tuple[int, ...]   # spine members, chronological
    span_resolution: str        # the reproducible query that produced `intervals`

    # ── the evidence ──
    evidence_ids: tuple[str, ...]   # ids in the evidence store; see §3.2
    derives_from: tuple[str, ...]   # e.g. "engine.threads.DramaticBeat#kind@294"
    confidence: float           # as measured by the source record, never adjusted
    rationale: str              # the sentence that makes the number checkable
    cues: tuple[str, ...]       # verbatim phrases, capped as the corpus caps them

    # ── the graph ──
    links: tuple[NodeLink, ...] = ()

    # ── the honest absences ──
    unsupported_reason: str = ""        # non-empty iff basis == UNSUPPORTED
    requires: tuple[str, ...] = ()      # signals this node degrades without
    degraded_by: tuple[str, ...] = ()   # which of `requires` the input lacked
    timing_precision: str = ""          # word_aligned | character_proportional
```

### 3.2 How a node's evidence is referenced

Two ways, because they answer different questions, and the layer needs both:

**By id.** The evidence record that lands in `engine.memory.evidence` is an
`Evidence` — a claim bound to `intervals` (an `IntervalSet`), a `Modality`, a
`Source` carrying `module` / `method` / `params_digest`
(`engine/memory/evidence.py:133-152`), a `confidence` and a `rationale`. It gets a
stable id only when a store assigns one, via `Evidence.with_id`
(`:183-198`). A node holds `evidence_ids` and never the evidence itself, so a
re-measured piece of evidence invalidates the node without the node being
re-derived, and an instrument change (`Source.params_digest`, `:94-98`) is visible
at the node. This is the mechanism `engine/memory/evidence.py:24-30` was written
for: "``source`` — the module, method and parameter digest that produced it, so a
conclusion is traceable to the instrument and a replaced instrument invalidates
the conclusion rather than being absorbed by it."

**By span query.** The corpus's own discipline is that artifacts hold indices, and
text is consulted by index (`engine/corpus.py:62-66`). A node carries
`span_resolution` — a string naming the reproducible resolution, e.g.
`"units[3,17,42].sentence_spans"` or
`"threads[0].chain ∩ redundancy_groups[2].members"` — so a consumer can re-derive
the spans from the corpus alone and check them against `intervals`. The query is
run through `IntervalSet` operations that already exist: `gaps(bounds)`
(`engine/memory/intervals.py:376`) reconstructs the inter-run gaps a `Topic` hides
inside its bounding span; `overlaps` (`:343`) and `intervals_in` (`:347`) answer
"does this node touch this proposed clip"; `restrict` on the evidence set
(`engine/memory/evidence.py:352-372`) clips evidence to a selection.

**What a node must not do:** copy text. The one full text copy per unit is
`CorpusUnit.text` (`engine/corpus.py:64-66`), deliberately. A node quotes via
`cues` — verbatim, bounded, exactly as `DramaticBeat.cues` is capped at six
(`engine/threads.py:499`) and `SetupPayoffLink.evidence` at two quotes
(`engine/threads.py:270`).

### 3.3 How nodes link to each other

`NodeLink.relation` is one of the thirteen Allen names (`ALLEN_RELATIONS`,
`engine/memory/intervals.py:74-77`), and the reverse edge is built with
`converse()` (`:90-98`) so the graph never carries two spellings of one relation —
the same rule `Evidence.link` states (`engine/memory/evidence.py:205-208`). This
replaces the corpus's implicit link vocabulary, which has no name for how two
*nodes* relate: `SetupPayoffLink.kind` (`engine/threads.py:262`) names the
*evidence* for a relation (question_answer, causal, …) rather than the temporal
shape of it, which is why `engine/story_view.py:146-148` had to argue that
`depends_on` would be "a second name for the same fact". Allen relations name the
shape; the evidence id on the link names the support; the two no longer compete.

Kinds that the corpus expresses positionally become first-class nodes with
`basis == POSITIONAL`: a `setup` node is built from the `setup_unit` side of every
edge that names this unit, a `payoff` node from the `payoff_unit` side
(`engine/threads.py:260-261`), and the two are linked with the Allen relation
computed from their actual spans rather than assumed. This is what makes the
phantom `"setup"` beat value (§1.1) representable without touching
`DramaticBeat` — and what lets `engine/story_path.py:56`'s dead top-priority role
slot finally be reached.

### 3.4 Field-by-field derivation

| Node field | Derived from | Derivation |
|---|---|---|
| `kind` | the eight mission kinds; `DramaticBeat.kind` (`engine/threads.py:294`) realises five | **mechanical** for turn/peak/resolution; **positional** for setup/payoff (edge sides, `:260-261`); **new** for context/development/conflict — and they are asserted `UNSUPPORTED` with the reason kept, not invented |
| `intervals` | `CorpusUnit.start/end` (`engine/corpus.py:121-122`), `Topic.unit_indices` + `runs` (`engine/topics.py:71,75`) | **mechanical** — a fold over unit spans into `IntervalSet.of`; `gaps()` then recovers what `Topic.start/end` (`:73-74`) flattened |
| `unit_indices` | `StoryThread.unit_indices` (`engine/threads.py:330`), `Topic.unit_indices`, `RedundancyGroup.members` (`engine/redundancy.py:70`) | **mechanical** — index lists already exist |
| `evidence_ids` | `SetupPayoffLink.evidence/reason/confidence` (`engine/threads.py:268-270`), `DramaticBeat.cues/reason` (`:300,303`), `Topic.evidence/reason` (`engine/topics.py:77-78`) | **new but cheap** — each record already carries quotes, reason and a measured confidence; the work is wrapping them as `Evidence` with `Source.of("engine.threads", "build_beats", …)` (`engine/memory/evidence.py:116-130`) and `Modality.EDITORIAL` (`:75`) |
| `confidence` | the source record's own | **mechanical, and must not be adjusted** — `engine/memory/evidence.py:43-45`: "Confidence is a number produced by the source instrument and recorded, never adjusted here" |
| `rationale` | `reason` fields, quoted verbatim | **mechanical** |
| `cues` | `DramaticBeat.cues` (`engine/threads.py:300`), `BrollMarker.cue` (`:317`) | **mechanical**, same caps |
| `links` | `SetupPayoffLink` pairs + `allen()` on their spans | **mechanical** — spans are on the record (`:264-267`); the Allen name is computed, not guessed |
| `basis` | new field, no source | **new** — but it encodes a distinction the corpus already makes (`engine/story_view.py:66-80`) |
| `requires` / `degraded_by` | `RhythmProfile.available` (`engine/rhythm.py:98`), `has_speaker_labels` (`engine/corpus.py:177`), `CorpusUnit.timing_source` (`:128`) | **mechanical** — the three availability signals are already on the corpus |
| `timing_precision` | `CorpusUnit.timing_source` | **mechanical** — propagates the measured/estimated distinction (§1.2) |
| `unsupported_reason` | `engine/story_view.py:104-149`'s refused-kind reasons | **mechanical** — the prose already exists and is already honest |

The one derivation that is a genuine new computation, and it is the whole reason
a node layer is worth building rather than a view: **`intervals` for a recurring
subject.** `Topic.start/end` (`engine/topics.py:73-74`) reports a bounding span;
the node layer must walk `unit_indices`, take each unit's span, and build an
`IntervalSet` whose `cardinality` is the topic's run count and whose `gaps()`
are the interruptions. Nothing in the corpus does this today, and it is the same
variable-cardinality failure §2.4 identifies in `arc`.

---

## 4. What this layer deliberately does not build

`engine/memory/evidence.py:32-37` states the rule:

> What an `Evidence` is *not*: it is not a judgement. "Clip this span" is an
> editorial decision that weighs evidence against a story's needs; `Evidence` is
> the input to that weighing.

The story node inherits that rule exactly. The boundary, stated precisely:

**The node layer answers "what is the story, and where in the source is the
support for saying so." It never answers "what ships."**

Concretely, a `StoryNode` has and will never have:

- **No `should_cut`, `keep`, `drop` or priority field.** Selection priority is
  already owned and measured elsewhere: `engine/editorial.py:478-531` (`rank`,
  per-passage strength with diversity rules) and `engine/story_path.py:48-62`
  (`_ROLE_RANK`, budget triage by narrative role). A node that ranked would
  duplicate `rank` with weaker evidence.
- **No budget, no duration target, no ordering of moments by anything but source
  time.** `story_path`'s contract is that "The budget is a budget, not a target"
  (`docs/story-memory-design.md:206-210`) and that "Moments are never shrunk to
  fit — a truncated moment is a different moment"
  (`engine/story_path.py:220-223`). Shaping belongs to the seam, not the graph.
- **No gate verdict.** A moment that cannot stand alone is refused by
  `engine.context_guard.validate` (`engine/context_guard.py:148`), and the
  refusal is a *measured* fact the node reports but does not make. The existing
  discipline is stated at `engine/story_path.py:25-27`: "a refusal reason is the
  guard's measured vocabulary, not a generated sentence."
- **No new perception.** The node layer computes nothing the corpus did not
  already compute, except the interval reconstruction of §3.4. It does not add a
  stance model for conflict (refused, `engine/story_view.py:128-132`), does not
  add embeddings for `supports`/`explains` (refused, `:105-116`), and does not
  add a forward-projection model for `foreshadows` (refused, `:122-126`). An
  `UNSUPPORTED` node with `unsupported_reason` populated is a *correct* output of
  this layer — it is how a graph says what it cannot see without pretending.
- **No mutation of the corpus.** `story_path`'s contract applies verbatim
  (`engine/story_path.py:213-215`): the corpus "is never modified, so the same
  graph yields every presentation of it."

The invariant that follows, and the one a test should pin: **removing the node
layer must not change a single shipped frame.** `selekyt/pipeline.py:894-907`
already holds this property by accident — the corpus write cannot take down a
render. The node layer must hold it by design.

---

## 5. Test surface — failure-first, in the style of the existing files

Named for the hazard each pins, following `tests/test_story_memory.py`'s convention
of a module docstring that says what the file proves and per-test docstrings that
state the failure mode. All would live in a new `tests/test_story_nodes.py`
(documented here; the constraint is that no file under `tests/` be modified, so
this is the surface, not the code).

1. `test_setup_node_is_positional_and_never_a_beat_kind`
   — *`DramaticBeat.kind` is documented to take `"setup"` at
   `engine/threads.py:294` but `build_beats` never assigns it (`:487-496`); a
   setup node must therefore be built from edge position, and a graph that
   trusted the comment would find zero setup nodes on any corpus.*
2. `test_arc_docstring_example_is_unreachable`
   — *`StoryThread.arc` is a join of beat kinds (`engine/threads.py:779-784`) so
   it can never contain `"setup"`; the example at `:335` describes impossible
   output, and a consumer parsing arcs for a setup position would silently
   never match.*
3. `test_arc_duplicate_collapse_loses_cardinality`
   — *`tension → tension → resolution` and `tension → resolution` produce the
   identical `arc` string (`engine/threads.py:782`); a node's `intervals` must
   have `cardinality == 2` for the two-run case where the label is identical.*
4. `test_conflict_kind_is_declared_unsupported_and_never_inferred_from_polarity`
   — *`_polarity` (`engine/threads.py:434-442`) is lexical valence; a conflict
   node with `basis == DERIVED` from `DramaticBeat.polarity` would relabel
   sentiment as opposition. The node must carry `basis == UNSUPPORTED` and the
   stance-model reason.*
5. `test_context_node_exposes_topic_gaps_not_the_bounding_span`
   — *`Topic.start/end` (`engine/topics.py:73-74`) spans the gaps between `runs`;
   for a topic with `runs == 3` the node's `intervals.cardinality` must be 3 and
   `gaps().duration` must be the interruption, not zero.*
6. `test_development_node_is_chain_membership_never_an_inferred_edge`
   — *`engine/story_view.py:96-101` declares `develops` an untyped co-membership
   "never as an inferred edge"; a development node must carry
   `basis == POSITIONAL` and must not synthesise a `SetupPayoffLink`.*
7. `test_peak_node_degrades_when_prosody_is_absent`
   — *`_raw_intensity` adds up to `0.30` from rush and emphatic pause only when
   rhythm is present (`engine/threads.py:471-475`); a peak node built without
   word timestamps must record `degraded_by == ("word_timestamps",)` and its
   `confidence` must not exceed the prosody-free measurement.*
8. `test_reaction_broll_is_absent_not_estimated_without_word_timing`
   — *`mark_broll` gates the timing-derived kind behind rhythm
   (`engine/threads.py:865`); a node must report the absence rather than emit a
   `reaction` marker with an invented pause.*
9. `test_unattributed_speaker_is_reported_not_guessed`
   — *`SpeakerBlock.speaker` is `None` without labels (`engine/rhythm.py:121`);
   a node's speaker field must read unattributed with the basis at
   `engine/story_view.py:349-352`, never a speaker id.*
10. `test_node_links_carry_allen_names_and_reject_misspellings`
    — *a typo in a `NodeLink.relation` must raise, exactly as `Evidence.link`
    raises (`engine/memory/evidence.py:209-210`), rather than becoming an
    unmatchable string in the graph.*
11. `test_converse_edges_are_built_not_hand_written`
    — *the reverse of a `before` link must be `after` via `converse()`
    (`engine/memory/intervals.py:90-98`); a graph carrying both spellings of one
    relation is the failure `Evidence.link` was written to prevent.*
12. `test_node_holds_no_transcript_text`
    — *the one text copy per unit is `CorpusUnit.text` (`engine/corpus.py:64-66`);
    a node holding text would duplicate the spine and could drift from it.*
13. `test_distant_setup_payoff_outside_the_inference_window_is_representable`
    — *`build_threads` caps reach at 6 units / 150 s (`engine/threads.py:604-606`)
    for inference; a node must still be able to *hold* a setup→payoff at 3 hours
    with `basis == UNSUPPORTED`, because the cap is editorial, not structural.*
14. `test_no_production_consumer_means_no_shipped_clip_depends_on_the_graph`
    — *`selekyt/pipeline.py:894-907` writes the corpus under `try/except` and
    `:1145` names the path only if it landed; `story_path`/`story_view` are
    called only from tests. A node layer must inherit that property: removing it
    changes no shipped frame.*
15. `test_node_layer_is_deterministic_and_ml_free`
    — *identical corpus yields byte-identical nodes; no wall clock, no network,
    no inference (`engine/corpus.py:48-52`), and buildable with no ollama server,
    which is the `engine/llm_editorial.attach_metadata` no-op condition.*
16. `test_round_trip_through_read_corpus_preserves_node_spans`
    — *a node built over a corpus read back via `engine/corpus_io.read_corpus`
    (`engine/corpus_io.py:333`) must resolve to the same `intervals` as one built
    over the freshly built corpus; a reader-vs-builder disagreement is a silent
    memory that disagrees with the video.*

---

## 6. Summary

**Audit verdicts (one line each):**

| Kind | Verdict | Where |
|---|---|---|
| setup | partial — positional only; `"setup"` is a phantom `DramaticBeat.kind` value | `engine/threads.py:260,294,487-496` |
| context | partial — `Topic` subject context with one lossy bounding span; `provides_context` refused as a relation | `engine/topics.py:73-74`; `engine/story_view.py:117-121` |
| development | absent as a kind; untyped chain co-membership only | `engine/threads.py:330`; `engine/story_view.py:96-101` |
| conflict | absent; `polarity` is lexical valence, not opposition, and parties are unattributed | `engine/threads.py:295,434-442`; `engine/rhythm.py:121` |
| turn | covered — contrast cue must lead the unit | `engine/threads.py:491-492,647-648,580-584` |
| peak | covered and best instrumented — intensity, percentile, two flags | `engine/threads.py:296-299,465-509,785-787` |
| payoff | covered positionally, with the strongest evidence contract in the layer | `engine/threads.py:261,266-270` |
| resolution | covered — solution/result cues plus `problem_solution` and `callback` edges | `engine/threads.py:493-494,574-591` |

**The single most surprising thing in `engine/corpus.py` and its satellites:**
`DramaticBeat.kind`'s own comment lists six values —
`setup | tension | turn | revelation | resolution | continuation`
(`engine/threads.py:294`) — but the assignment ladder five lines later
(`:487-496`) produces only five. `"setup"` appears in four docstrings and zero
assignments. That one discrepancy propagates three ways: `StoryThread.arc`'s
documented example `"setup → tension → resolution"` (`:335`) is unreachable —
measured arcs are `tension → resolution` and `continuation`;
`engine/story_path.py:56`'s highest-priority role `"setup": 0` is unreachable
through `role_in_thread` (`:190-192`), so the most important slot in the only
join between the graph and an edit is dead code; and the mission's first node
kind is represented today only by the *absence of an incoming edge*. The corpus
has the setup; it just never named it.
