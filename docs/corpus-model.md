# The corpus editorial model

`engine/corpus.py::build_corpus` is the **corpus-level editorial representation**
of SelekyT: the layer that looks at a whole 2–3 hour transcript as one body of
material rather than scoring its sentences independently.

**Nothing consumes this layer yet.** It is committed as an analysis, not as a
pipeline stage: no selection, ranking or render path reads it, so its output
cannot yet have affected any shipped clip. What is verified here is that the
analysis runs on the committed corpus and produces judgements a reader can
check against the transcript text (see "Evidence" below); what is not verified
is that acting on those judgements improves an edit. The section headed
"Consuming the corpus" is the integration point for a later change, and it
should be read as a proposal rather than as a contract that anything currently
honours.

The contract is nonetheless written to be **stable**: the `build_corpus`
signature, the field set of every dataclass, and the degradation rules are the
interface. Backwards-incompatible changes require a bump of
`engine.corpus.SCHEMA_VERSION`, which is carried in `EditorialCorpus.build.schema`.

## What it is for

Everything else in the editorial layer answers *"is this passage good?"*. The
corpus layer answers the questions a professional editor asks *before* that,
because they change what a good score means:

- Which subjects **recur**? A subject the speaker returns to is a spine of the
  recording; one mentioned once is an aside. No per-passage score can tell them
  apart.
- Where is each story **set up** and where does it **pay off**? A clip with a
  payoff and no setup lands flat; a clip with a setup and no payoff is a tease.
- Where does the recording **repeat itself**, and which repetition is the one
  worth keeping?
- What is the speaker's **rhythm** — rush, hesitation, restarts, truncation?
- And above every one of these: **what is the evidence**, and where in the
  source can a human editor go to check it?

## The constructor

```python
from engine.corpus import build_corpus

corpus = build_corpus(
    segments,              # list[models.core.Segment], any order, never mutated
    *,
    duration,              # source seconds; inferred from segments when <= 0
    language="unknown",    # caller-reported; script detection reported separately
    source=None,           # optional origin label, carried through, never used
) -> EditorialCorpus
```

Deterministic and pure: identical input yields identical output, byte for byte.
No network, no ML, no global state, no wall clock. The only input-derived field
is `build.fingerprint`, a digest of the normalised input recorded so a report
can state *which* input a corpus was built from.

### Cost over U units, S sentences, W words

| stage | time | memory |
|---|---|---|
| unit building | O(S) (+ O(W) with word alignment) | O(U + W) |
| topics | O(U·K), K = bounded open-topic window (8) | O(U + vocabulary) |
| threads + beats | O(U·W₊), W₊ = bounded forward window (6 units / 150 s) | O(U) |
| redundancy | O(U + P), P = blocked candidate pairs, bounded per unit | O(U + vocabulary) |
| rhythm | O(W) + one O(U log U) median sort | O(U) |

**No artifact holds a copy of the transcript.** Topics, threads and redundancy
groups hold *indices into `corpus.units`*; the only full text copy in the whole
corpus is `CorpusUnit.text`, exactly one per unit. Bounded cost is asserted by
`tests/test_corpus.py::test_three_hour_scale_completes_in_bounded_time_and_memory`.

## The spine: `units`

`EditorialCorpus.units` is a chronological list of `CorpusUnit` — one coherent
passage each, produced by `engine.story_units.segment_story_units` (the same
story-unit segmentation the candidate generator already trusts, so a corpus
unit and a story boundary are the same object). Every other artifact indexes
into this list.

```python
@dataclass
class CorpusUnit:
    index: int
    start: float
    end: float
    segment_indices: list[int]
    text: str
    sentence_count: int
    word_count: int
    coherence: float
    boilerplate: bool
    timing_source: str        # "word_aligned" | "character_proportional"
    restatement_count: int    # sentence pairs inside this unit that repeat
    topic_id: int = -1
    thread_id: int | None = None
    beat_id: int | None = None
    redundancy_group_id: int = -1
    speaker_block_id: int = -1
```

`t = corpus.units[i].start` / `.end` are source seconds. The cross-link fields
are filled by `build_corpus` once every analysis has run, so any artifact
resolves to any other by index.

## The analyses

### `topics` — `list[Topic]`

Subject clusters built by chronology-aware leader clustering with recency:
units are read in source order and each joins the most recently active open
topic whose subject it shares (idf-free containment, the same measure the
story-unit seam decisions use) or opens a new one. Only the `max_open` (8) most
recently touched topics are compared per unit, so cost is bounded regardless of
corpus length.

- `label` and `terms` are **signature terms quoted verbatim** from the
  transcript. They are a signature, not a summary: no term is ever synthesised
  or re-inflected.
- `runs` counts separated stretches. `recurring` is `runs > 1` — the
  corpus-level fact a sentence-level view cannot see. `span_share` is the
  fraction of `duration` the topic covers.
- A topic with one unit is returned with `confidence ≈ 0.30` and a reason that
  calls it a **passing mention**, not an established subject.
- Units with no recoverable content words are left in `unassigned` rather than
  forced onto a neighbour.

### `threads` — `list[StoryThread]`, `links`, `beats`

Chains of units joined by **setup → payoff** links. Each unit claims at most
one forward link within a bounded window (6 units / 150 s); each target honours
only the strongest claim, so chains are simple disjoint paths an editor can
verify against the source. Link `kind` and the evidence that licenses it:

| kind | evidence | strength |
|---|---|---|
| `causal` | payoff opens with a result connective ("so", "that's why", "అందుకే", "எனவே") on the same subject | 0.75 |
| `question_answer` | setup asks, payoff answers, shared subject | 0.65 |
| `problem_solution` | trouble vocabulary → resolution vocabulary, same subject | 0.60 |
| `contrast_turn` | payoff opens with a reversal marker, same subject | 0.55 |
| `callback` | a subject returns after ≥ 3 intervening units | 0.45 |

Temporal order alone is **never** treated as causation; a connective is the
minimum evidence for a causal claim. A unit with no inferable relation is a
chain of one with `confidence ≈ 0.25` and a reason that says so.

`beats` — `list[DramaticBeat]`, one per unit — annotate the dramatic function:
`kind` ∈ {`setup`, `tension`, `turn`, `revelation`, `resolution`,
`continuation`}, `polarity` ∈ {`positive`, `negative`, `neutral`} (neutral is
honest: no lexicon match is not a guessed emotion), `intensity` on a
**saturation scale** `1 - 1/(1+c)` from cue counts so that 0.6 means the same
thing in a 3-minute and a 3-hour corpus, plus `percentile` for within-corpus
comparison and `is_thread_peak` / `is_corpus_peak`. `cues` are the verbatim
phrases that fired.

`arc` is the chain's beat-kind sequence, e.g. `setup → tension → resolution`.

`broll` — `list[BrollMarker]` — only where the transcript itself points at
something visual: `demonstration` ("let me show you"), `deictic` ("as you can
see", "right here"), `illustration` ("imagine", "for example", confidence 0.45
because an image is possible not guaranteed), and `reaction` (a held silence
before a high-intensity beat — timing-derived, confidence 0.50, requires word
timestamps). **An empty list is the correct result when none of these appear.**
No shot is ever invented; each marker's `cue` is the verbatim phrase.

### `redundancy_groups` — `list[RedundancyGroup]`

Passages that say the same thing, with the recommended survivor. Pairs are
generated by a **rare-term inverted index** (document frequency ≤ 4, posting
lists capped at 64), scored by containment, grouped by union-find, and labelled
with a `relation`:

- `restatement` — same substance again; the straightforward cut;
- `elaboration` — the later passage carries ≥ 1.5× the content terms; it is the
  *revision*, not a copy;
- `recap` — the group spans > 120 s; often worth keeping in a long edit and
  cutting in a short one.

`representative` is chosen by substance (0.45) + completeness (0.30: does the
passage close on a full stop rather than trailing off) + the story unit's own
coherence (0.25). **Duration is never the primary criterion** — the longest
restatement is usually the padded one. The `reason` names the winner and why, so
an editor can challenge it on the record; the representative is a
recommendation, never an obligation.

`unit.restatement_count` carries restatement detected *inside* one unit: the
story-unit layer merges a repetition into a single passage when the subject
never changed (correct for segmentation, but it hides the repetition from an
editor). That count is the sentence-level view of the same question, at O(S).

### `rhythm` — `RhythmProfile`

Pauses, filler, false starts, truncation and speech rate. Inter-word gaps are
reconstructed into speech/silence regions and classified by the **same
`engine.pause_classifier.PauseClassifier`** the editorial loop trusts for
dead-air decisions — so "dead air" means the same thing here and there.
Speech rate is words per minute of **voiced time** (word durations plus
articulation gaps, pauses excluded), computed identically per unit and
corpus-wide, so a long pause-filled unit is not reported as slow speech.
`rush` (0..1) and `drag` are relative to the corpus median; `fluency` is a
saturating function of disfluency counts.

False starts are detected from (a) an immediately repeated 1–4 token stem with
an abandonment mark (comma, semicolon, dash) between the two attempts —
"I think the clock, I think the clock" is a restart, while "the clock the
clock" is a rhetorical figure and is not counted — and (b) a passage whose
opening stem repeats the next passage's opening. Incomplete thoughts are
passages whose final sentence lacks terminal punctuation.

**Degradation:** without usable word timestamps, `rhythm.available is False`,
and `speech_rate_wpm`, `median_rate_wpm`, `pause_counts`, and the
`reaction` B-roll kind are all `None`/absent. Filler, false-start and
truncation counts come from text and are still reported, and each per-unit
`reason` says which signals were unavailable.

### `redundancy_groups` and in-unit restatement

Redundancy operates between story units, and the story-unit layer deliberately
merges a restatement into one passage when the subject never changed (that is
correct for segmentation — it is one continuous thought). So repetition that
lives *inside* one unit is reported as `CorpusUnit.restatement_count` — the
sentence-level view of the same question, at O(S) — rather than as a group.
Both signals are worth reading: the group says "cut one of these passages", the
count says "this passage says its own point twice".

### `speaker_blocks` — `list[SpeakerBlock]`

Speaker identity is **not** inferable from an unlabelled transcript, so:

- if segments carry a `speaker` attribute (read with `getattr`; `models.core.Segment`
  has no such field by design, but some ASR backends attach one), `has_speaker_labels`
  is True and a block is attributed to a speaker when a single speaker dominates
  its duration;
- a unit that **straddles** a speaker change (two speakers inside one sentence
  group — common in fast interview cadence, where the story-unit layer keeps a
  turn-change inside one passage) is deliberately attributed to *no one*, at
  confidence 0.35, with a reason that names the straddle. The block boundary is
  still reported, so an editor can attribute it by hand;
- with no labels anywhere, boundaries are **turn hypotheses** from a ≥ 0.9 s
  silence followed by a subject change, `speaker` is `None` on every block, and
  the `reason` says the speaker is deliberately not guessed.

`arc_summary` is the editor's first-read paragraph, assembled only from
computed fields (every clause traceable to a field). `notes` lists the caveats a
human editor should read first: repaired input, unavailable signals, unassigned
units, standalone passages.

## Degradation rules — the contract that matters most

Absent signals degrade to `None` or an empty list, **never** to a fabricated
value:

| missing input | consequence |
|---|---|
| no segments | valid corpus, all analyses empty, `arc_summary` says "Empty corpus" |
| no word timestamps | `rhythm.available is False`; all timing fields `None`; `reaction` B-roll absent |
| no speaker labels | every `SpeakerBlock.speaker` is `None`; turn hypotheses only |
| no visual/deictic cues | `broll == []` |
| no terminal punctuation | whole-segment units; `boilerplate` false; sentence splitting falls back |
| no recoverable content words | unit left unassigned to a topic, with a note |
| end < start, NaN, ±inf | repaired, clamped to ≥ 0, and noted |
| transcript longer than `duration` | reported as transcribed, noted, not silently clamped |

Every artifact — every topic, thread, beat, link, group, block and marker —
carries `start`/`end` (source seconds), `confidence` (0..1, scaled by how
explicit the evidence was) and a `reason` naming that evidence, plus a bounded
`evidence` list of verbatim quotations. `tests/test_corpus.py` pins these
obligations on every artifact type.

## Editorial principles encoded

These are the decisions the layer refuses to make silently:

1. **Never manufacture context.** Labels are verbatim signature terms; a topic
   is a cluster, not a generated headline; a speaker is never guessed.
2. **Never infer causation from sequence.** A causal link requires a result
   connective the speaker actually used.
3. **Scale must not change meaning.** Beat intensity is a saturation scale; a
   long transcript cannot inflate it.
4. **Precision is not license.** Redundancy removes material, so it fires only
   above a deliberately high threshold and reports which passage to keep and
   why.
5. **Evidence is auditable.** Every judgement names its cue and its timecode;
   the human editor remains the final creative authority.

## Consuming the corpus (integration point, currently unused)

```python
corpus = build_corpus(segs, duration=duration, language=lang)

# "Which subjects are spines?" — rank recurring topics by coverage
spines = sorted((t for t in corpus.topics if t.recurring),
                key=lambda t: -t.span_share)

# "Where does this story pay off?" — follow a thread's links
for thread in corpus.threads:
    for link in thread.links:
        assert link.payoff_unit > link.setup_unit   # always forward in time

# "What is safe to remove?" — groups whose later members restate
for g in corpus.redundancy_groups:
    if g.relation == "restatement":
        removable = [u for u in g.members if u != g.representative]

# "Where is the pace broken?" — per-unit cadence by index
for u in corpus.rhythm.per_unit:
    if u.drag or u.false_start_count or u.incomplete:
        ...

# "Can I cut to a visual here?" — only where the transcript licensed it
for m in corpus.broll:
    if m.confidence >= 0.7:
        ...
```

`EditorialCorpus.to_dict()` returns a JSON-serialisable view (no custom encoders
needed) for report export.

## Limitations — what this layer cannot infer

- **Semantics.** It has no world model. Theme, irony, character arc and
  subtext are not inferred; only the linguistic markers that create structure in
  speech are. A metaphor that carries no cue vocabulary is invisible to it.
- **Speaker identity without labels.** Turn boundaries are hypotheses; who is
  speaking is not derivable from an unlabelled transcript.
- **Emotion.** Beat polarity is a lexicon match, not affect modelling. "Neutral"
  means no lexicon match, not "no emotion".
- **Causation beyond connectives.** Cause and effect are inferred only when the
  speaker asserted them.
- **Actual visuals.** B-roll markers are transcript-licensed *opportunities*.
  Whether anything was on screen is a question for a vision layer that does not
  exist here; `illustration` markers say so in their reason.
- **Audio truth.** Pauses are reconstructed from word gaps. A VAD measurement is
  strictly preferable when audio is available; rhythm confidence is capped to
  say so.
- **In-unit restatement** is reported as a count, not as sub-unit cut points.
- **Speaker changes inside one unit.** When the story-unit layer keeps a
  turn-change inside a single passage, the block is reported as a turn boundary
  and attributed to no speaker, rather than guessing.
- **Language coverage.** Cue lexicons cover English and Telugu, Hindi, Marathi,
  Tamil, Kannada, Malayalam and Bengali. An unmatched script yields neutral
  polarity and no dramatic cues rather than a guess.

## Measured on the committed corpus

Run across every committed transcript (11 sources, 1,771 segments,
18,748 word-timestamp entries) on 2026-09-20:

| Source | Segments | Units | Topics | Threads | Redundancy groups |
|---|---|---|---|---|---|
| emwcon-panel-2018 | 842 | 112 | 52 | 69 | 1 |
| nasa360-ep5 | 425 | 93 | 50 | 59 | 6 |
| pentagon-esper-briefing | 277 | 71 | 43 | 54 | 2 |
| royal-society-cloke | 156 | 22 | 9 | 15 | 0 |
| hadhazy-protest | 38 | 6 | 4 | 5 | 0 |
| ooi-vertical | 26 | 7 | 7 | 7 | 0 |
| telugu-tutorial | 4 | 2 | 1 | 2 | 0 |
| hindi-baiga-interview | 3 | 1 | 1 | 0 | 0 |
| alice-1903, roman-portrait-trailer, telugu-launch | 0 | — | — | — | — |

The three empty rows are the sources whose transcripts are empty for other
reasons (see `docs/AI_EXECUTION_STATE.md` outstanding item 7), not a failure of
this layer; an empty transcript has no units to build.

**One judgement, checkable against the text.** On `nasa360-ep5` the layer groups
three units as the show's recurring sign-off boilerplate — "I'm Jennifer Pulley.
And I'm Johnny Alonzo." (unit 1), "For Jennifer Pulley, I'm Johnny Alonso. We'll
catch you next time on NASA 360." (unit 85) and "I love Johnny Lontl. And I'm
Jennifer Pulley." (unit 90) — and reasons "keep unit 85 … the later passage is a
recap of an earlier point — keep in a long edit, cut in a short one." A reader
can confirm that from the transcript alone. That is the standard this layer is
held to: every derived artifact carries `start`/`end`, `confidence`, `reason`
and bounded verbatim `evidence`, so a judgement can be jumped to in the source
media and checked.

What this table does **not** establish is that acting on any of it improves an
edit. Nothing consumes the layer yet.
