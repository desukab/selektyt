# Retrieval layer — INDEX and RETRIEVE at hour scale

**Status:** implemented. `engine/retrieval.py` ships the INDEX and RETRIEVE stages
designed here, `tests/test_retrieval.py` is the test surface in §7, and
`selekyt/pipeline.py` writes `project/retrieval-index.json` on every run whose
transcript carries word timestamps. §4's figures below are measurements of that
code, not the estimates this document carried while it was design-only — every
one of them changed, and §4 says by how much.

**Scope:** stages 1 and 2 of the INDEX → RETRIEVE → GROUND → VERIFY → REASON
pipeline. It designs through the *handoff* to GROUND and names the interface that
handoff must satisfy; it does not design GROUND.

---

## 0. Provenance of every number in this document

Three kinds of figure appear below, and they must not be mixed.

1. **Figures I measured on this host**, on the largest transcript committed to the
   repo (`corpus/emwcon-panel-2018_run/project/transcript.json`, **842 segments**,
   9,093 words, 3,332.78 s = 55.5 min). These are SeleKyT measurements. Each is
   reproducible from the repository as it stands — §4's are timings of
   `engine.retrieval.py` as shipped, and the script that produced them is a
   transcript load, a `build_index`, an `index.save` and a `retrieve` loop.
   Machine: 4 CPUs.
2. **Figures from `docs/product-gap-map.md` and `corpus/wer/runs/`** — the corpus
   reality. Telugu WER 151.08% (`small`) / 91.54% (`medium`), CER 1.274 / 0.6924,
   Polish 11.01% by contrast. These are SeleKyT measurements too, quoted.
3. **Figures from an external paper.** The operating point in §3 — top-**K = 3**,
   **NMS at 60 s**, **mIoU 0.354**, recall **96.7%** at K = 10 with mIoU falling to
   **0.295**, retrieval + grounding **6.7×** cheaper and **6.7×** better mIoU than
   a single whole-video Video-LLM pass — comes from **arXiv 2606.12300**
   (ExtremeWhenBench replication), supplied as the brief for this design.

   **I could not re-verify the paper from this host.** The identifier is dated June
   2026, beyond this host's search cutoff; a lookup returned nothing confirmable, so
   I will not state its title, authors or abstract, because I do not know them. The
   figures are cited exactly as supplied and are **not SeleKyT measurements**.
   Nothing in the cost model below depends on them — §4's cost numbers are all
   mine (category 1). The paper's numbers are used for one thing only: to justify
   the *shape* of the retrieve stage (K = 3, NMS, union-then-ground) rather than a
   whole-video pass.

**What transfers from the paper, and what does not.**

| Paper's setting | SeleKyT's setting | Transferable? |
|---|---|---|
| CLIP visual retriever scores frames | **No visual embedding model exists on this host.** This is the single largest non-transfer. | **No** |
| mIoU 0.354 at K = 3 | No measurement exists for SeleKyT; there is no ground truth (`docs/memory-perception-inventory.md:289` states no human has judged any clip) | **No** — the number is not ours |
| Top-K with small K, then NMS | The shape of the answer, not its score | **Yes** |
| NMS radius 60 s | A coarse editorial window — a chapter-scale suppression, not a tuned constant | **Yes, provisionally** |
| Union candidates, then ground the union | Exactly matches ffmpeg-decode-on-demand (§4, "why that ordering is load-bearing") | **Yes** |
| Retrieval+grounding 6.7× cheaper than whole-video | Directionally safe (decode is the cost); the factor is not ours | **Direction only** |

The honest summary: **the paper's conclusions about retrieval *shape* transfer; its
scores and its retriever do not.** SeleKyT's retrieval is lexical over a
word-timestamped transcript plus structured detection metadata — a weaker retriever
than CLIP in the general case, and the design below is built on the assumption that
it is weaker, not on any borrowed confidence.

---

## 1. What there is to index

### 1.1 The primary channel: a word-timestamped transcript

`selekyt/pipeline.py:703-717` writes `transcript.json` as

```python
{"language": detected, "asr": asr_provenance,
 "segments": [{"start", "end", "text", "words": [asdict(w) for w in s.words]},
 "container_recovery": note}
```

and `models/core.py` defines the indexed types:

```python
@dataclass
class Word:                       # models/core.py:6
    text: str
    start: float
    end: float
    confidence: float = 1.0

@dataclass
class Segment:                    # models/core.py:13
    start: float
    end: float
    text: str
    words: list[Word]
    coherence: float = 1.0
    completeness: float = 1.0
    @property
    def duration(self) -> float:  # models/core.py:22
        return max(0.0, self.end - self.start)
```

This is the one channel that is *both* structured *and* reliably on disk. Measured
on the real file: 842 segments, 9,093 words, 3,332.78 s, 163.7 words/minute, word
records carrying `text`/`start`/`end`/`confidence`, `asr` carrying
`{"engine": "faster-whisper", "requested_model": "small", "model": "small",
"word_timestamps": "per-token", "chunks": 12, "chunking": "5 min chunks (+15s
overlap)", "num_threads": 4}`. The word-level timestamps are what makes temporal
retrieval possible at all; segment text alone would limit the answer to
utterance granularity, which for a 75-minute source is ~3,600 spans of ~1.25 s
each — too coarse to place a phrase.

`transcript.json` is the index's source of truth, and it is the right one:
`docs/memory-perception-inventory.md:269` notes `REGENERABLE_DIRS = ("source",
"transcription")` means it survives intermediate cleanup, and
`selekyt/pipeline.py:1248` notes it is deliberately *not* regenerated on re-run.

### 1.2 The secondary channel: structured detection metadata — and its availability problem

`docs/memory-perception-inventory.md` is the contract for what is perceivable, and
its signal-by-signal disk reachability is the constraint retrieval must live with:

| Signal | Module | Reaches disk? | Indexable at hour scale? |
|---|---|---|---|
| 1. Face / subject tracks | `engine/reframe.py` (`_perceive_window`) | **Never** (`:25-66`) | No — would need recompute |
| 2. Shot / scene class | `engine/reframe_scenes.py` (`SceneClass`: SPEAKER / SLIDE / SPLIT only) | **Never** (`:67-116`) | No |
| 3. Speech regions (VAD) | `engine/vad.py` (silero → ffmpeg → energy → whisper) | Only behind `--audio-analysis`, which **defaults `False`** (`:117-172`) | Conditionally |
| 4. Pause / dead-air | `engine/pause_classifier.py` | Only ratios/counts reach `audio_truth.json`; `AudioTruth` is never serialised (`:173-212`) | No — **trap** |
| 5. Story structure | `engine/corpus.py` → `editorial_corpus.json` | Written in full, but **inside `try/except Exception`** (`:213-252`) | Yes, but silently unreliable |

`docs/memory-perception-inventory.md:271` states it plainly: **of the six signals,
only Signal 5 reaches disk, and it is the one wrapped in a blanket exception
handler.** Signals 1 and 2 — the two a visual retriever would have used — never
reach disk at all.

**Consequence for the design.** The secondary channel cannot be assumed present.
The index therefore has exactly one *mandatory* input (the transcript) and a set of
*optional* inputs whose presence is recorded per-input rather than assumed. An index
that silently indexed VAD regions when they exist and behaved as though they never
existed when they do not would produce a retrieval layer whose answers change
character between runs on the same source — the same failure `engine/corpus.py:33`
describes from the other direction ("Signals the transcript does not carry are
reported as `None` or an empty list — never as a fabricated value").

The `try/except` around `editorial_corpus.json` is treated as a **hazard, not a
feature**: the index may consume it but must record whether it loaded, and a query
that would have used it must say it could not.

### 1.3 What does not exist to be indexed

From `docs/memory-perception-inventory.md:275-290`: no diarisation (so no
speaker-scoped retrieval), no OCR in the pipeline path, no music/speech separation,
no acoustic emotion, no object detection (YOLO evaluated and rejected), no gesture
or gaze, no camera-motion parameters, no pixel-derived B-roll, no slide content, no
reframe-quality verification, and — decisively for §0 — **no ground truth of any
kind**. Any retrieval-score figure for SeleKyT would therefore be unscoreable; the
absence of an mIoU number for SeleKyT in this document is a fact about the corpus,
not an omission.

---

## 2. The index

### 2.1 On-disk shape

A single file, `project/retrieval-index.json`, written beside `transcript.json`:

```
{
  "schema": "1.0",
  "source_fingerprint":   "<SHA1[:16] of (media bytes digest, transcript.json)>",
  "language":             "te",
  "fitness":              {"verdict": "poor", "wer": 0.9154, "cer": 0.6924, ...},
  "built_at_params":      {"n_words": 12276, "n_terms": 1838, "tokeniser": "casefold"},
  "postings":             { "<term>": [[start, end, word_idx], ...], ... },
  "segments":             [[start, end, first_word_idx, last_word_idx, n_terms], ...],
  "ngram_postings":       { "<ngram>": [[start, end, word_idx], ...], ... },
  "word_confidence":      [0.94, 0.88, ...],
  "word_terms":           ["the", "metric", ...],
  "optional": {
     "vad":                {"loaded": false, "region_count": 0},
     "editorial_corpus":   {"loaded": true, "n_units": 412, "n_topics": 38}
  }
}
```

`ngram_postings` is empty unless the fitness verdict is not `usable` (§5), and
`word_terms` is *not stored* — it is rebuilt from `postings` on load, in one pass
over every hit's `word_idx`, because it is derivable and a stored copy is a copy
that can disagree with the postings it was derived from.

**One paragraph.** The index is an inverted file: a `postings` map from each
normalised word type to a list of `[start, end, word_idx]` triples in ascending
start order, plus a parallel `segments` array mapping each utterance to its
word-index range so a candidate can be expanded from word granularity back to
utterance granularity. Derived on read and never stored: the start/end parallel
arrays (rebuilt in one linear pass), segment-to-word containment, phrase-adjacency
results, and all scores. Two header fields carry provenance rather than data —
`source_fingerprint` binds the index to the exact media and transcript it was built
from, and `fitness` carries the language verdict so the index can never present a
91%-WER transcript as authoritative (§5). Everything is pure and deterministic:
identical input yields a byte-identical index, no wall clock, no network, no model.

Measured size, on the largest transcript committed to the repo
(`corpus/emwcon-panel-2018_run/project/transcript.json`, 55.5 min, 9,093 words,
1,838 terms, fitness `usable`): **885 KiB** on disk, JSON, UTF-8 — 254 KiB
postings, 29 KiB segments, 177 KiB `word_confidence`, the rest header and
provenance. The design's 373 KB figure was drawn before `word_confidence` was a
stored field; the word-level confidence is what lets a result report doubt the
ASR itself measured (§3.5), and it costs 20% of the file to carry.

Scaled to this section's 75-minute basis (×1.35, word-count-linear): **~1.2 MB**.
Against ~2.0 GB free disk on this host the index is not a resource problem at any
plausible scale; a 10-hour source is ~10 MB. On a `poor` transcript the n-gram
fallback index is written too and roughly triples the file.

### 2.2 What is stored, what is derived on read, and why

**Stored.** Postings triples and the segment array. These are the irreducible
temporal facts: which word types occur where, and where utterance boundaries fall.
Storing them costs one linear build pass (~16 ms measured, §4) and makes every
query a pair of `bisect` lookups instead of a scan of the transcript. The
implementation also stores the transcript's per-word `confidence` as
`word_idx`-addressable side data, which §3.5 needs and which is the single largest
addition to the on-disk shape this document first drew.

**Derived on read.**

- **Parallel start/end arrays** — rebuilt from postings in one O(W) pass. Storing
  them would double the file for a gain of ~1 ms; not worth it, and it would create
  a second copy of the same numbers that could drift.
- **Segment containment** — computed by walking the segment array with the word
  indices, since both are sorted. Storing `first_word_idx`/`last_word_idx` per
  segment makes this O(log S) per candidate.
- **Phrase adjacency** — never stored, because the set of phrases is unbounded. It
  is the join in §3.3 and costs ~100 µs worst case.
- **All scores** — computed per query from idf weights that are themselves derived
  on read from the postings map (`engine/lexical.py:41` `df_table`, `:53`
  `idf_weights`). A stored score would be a stored *opinion*; the postings are the
  only stored fact.

**Why the split.** The index is rebuildable from `transcript.json` alone in ~16 ms,
which means it is safe to delete and safe to ignore — a stale index is a bug the
build catches by fingerprint, not a corrupted artefact. Anything derivable is
derived, so there is exactly one place each fact lives. That includes
`word_terms`: the implementation derives it from `postings` on load rather than
storing it, so the vocabulary can never disagree with the postings it came from.

### 2.3 Normalisation, and the tokeniser that is script-blind

The stored term is `word.text.strip().casefold()`. That is deliberately the whole of
normalisation for the stored form: no stemming, no lemmatisation, no stopword
removal (§3.4 explains how idf handles stopwords instead — by scoring them low,
which is reversible, whereas deleting them is not).

There is a property of the existing tokeniser that matters here and is easy to miss.
`engine/story_units.py:285`:

```python
def _content_tokens(text: str) -> set[str]:
    toks = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
    return {t for t in toks if len(t) > 2 and t not in _STOP and not t.isdigit()}
```

`\w+` is Unicode-aware, so for a spaceless or agglutinative script it does not split
words so much as return whole runs. For Telugu that is mostly harmless at index
time (the "term" is a script run, which is still a stable string), but it means the
index's notion of "word" is script-dependent, and any language-specific
re-tokenisation belongs in the *query* normaliser, where it can be changed without
rebuilding the index. **The index stores the script run as spoken-as-transcribed;
query-side adaptation is a separate concern** — this is the same split the Language
IR spec makes between the canonical record and the lossy query-side alias.

### 2.4 Determinism and rebuildability

- `source_fingerprint` is built with the project's own `engine/lexical.py:124`
  `fingerprint` (SHA1[:16]), over the media digest *and* `transcript.json` bytes, so
  a re-transcribed source produces a different fingerprint and the index is rebuilt.
- The index is a pure function of its inputs. No `time.time()`, no `random`, no
  network. Two builds on the same transcript are byte-identical, which is what makes
  "the index is stale" a testable predicate rather than a judgement call.
- Invalidated, not repaired: a build with a mismatched fingerprint is refused and
  rebuilt, mirroring the principle in `engine/memory/evidence.py:86-99` that a
  replaced instrument invalidates a conclusion rather than being absorbed by it.

---

## 3. The retrieve stage

### 3.1 The query language — deliberately crude

A query is a list of terms, optionally marked as an ordered phrase:

```
["అనగా", "హిందీ"]                    # all terms must occur (AND)
[("అనగా", "హిందీ", "అధికార")]      # ordered phrase, adjacency within 0.5 s
```

That is the entire grammar. No boolean operators, no field selectors, no wildcard,
no relevance-feedback, no query expansion. Anything more expressive is a
*reasoning*-stage concern, expressed against the evidence set retrieval returns.

This is the honesty model already stated in `engine/memory/evidence.py`, and the
retrieve stage inherits it verbatim:

> Text-only and deliberately crude: the semantic retrieval layer builds on the
> interval algebra, not on this, and a keyword match that is honest about being a
> keyword match is worth more here than a fuzzy matcher that implies understanding
> it does not have. (`EvidenceSet.about`, `engine/memory/evidence.py:329-339`)

Query normalisation mirrors the index term normalisation (`strip().casefold()`), and
the query side alone may apply lossy adaptations — including the ASCII-folded
`search_alias` from `docs/telugu-language-ir-spec.md`, which the spec deliberately
places on the query side because folding is lossy and must not corrupt the stored
record. **Lossy transformation belongs where it can be discarded: the query, not the
index.**

### 3.2 What a query returns: a set of intervals, never one span

The answer to a retrieval query is a **variable-cardinality set of intervals**, not a
span. A phrase spoken four times in a 75-minute source has four answers; a consumer
that takes the first is wrong three times out of four. This is not a packaging
choice — `engine/memory/evidence.py:138-141` states the reason as a property of the
data:

> `intervals` is the answer to "where". Its cardinality is data: a claim supported at
> four spans is a different claim from one supported at one, and a consumer that
> assumes a single span will take the first and be wrong.

So the return type is the interval algebra, by interface:

> **Required interface (described, not imported).** The retrieve stage returns an
> object exposing: `cardinality` and `duration`; `union` / `intersect` / `subtract`
> against another such set, ascending-start normalisation, and a containment test
> against a query span; plus construction from `[(start, end), ...]` in source
> seconds and a `to_dict`/`from_dict` round trip. `engine/memory/intervals.py`
  already defines exactly this shape (`IntervalSet`, `MERGE_EPS = 1e-6`, O(N+M)
> set operations, `at(t)` in O(log N) via `bisect.bisect_right`). The design depends
> on that *interface*; it does not import or assume internals, since `engine/memory`
> is under concurrent development.

### 3.3 Scoring

Scoring reuses the stdlib vocabulary already in the repo — **no new dependency, no
numpy, no FAISS, no sentence transformers**:

- **Candidate windows** from postings, expanded to utterance granularity via the
  segment array, then merged within `MERGE_EPS`.
- **Window score** = `weighted_containment(query_terms, window_terms, idf)`
  (`engine/lexical.py:81`), i.e. idf-weighted overlap — the weighting is what makes
  a query of two content words outrank a window containing "the" and "to".
- **Term weighting** = `idf_weights(df_table(...), n_docs)` (`engine/lexical.py:41`,
  `:53`), smoothed `log((n_docs+1)/(c+1))`. Computed per retrieval session from the
  postings map, never stored.
- **Span-length tiebreak** = `containment(a, b)` (`engine/lexical.py:65`) favouring
  the tighter window when scores tie, so a phrase spanning 3 s beats one spanning 40 s
  at equal term overlap.
- **Optional-channel re-ranking.** Where VAD regions or story units loaded
  (`optional.*.loaded` is true), a window inside a speech region scores up by a
  recorded factor and a window inside an editorial topic unit scores up by another.
  Both factors are recorded in the returned evidence's `rationale`, never folded
  silently into the score. Where they did not load, **the re-ranking is absent, not
  defaulted**.

### 3.4 Top-K, with K = 3 — and why idf handles stopwords instead of a stoplist

Retrieval returns **K = 3** candidate windows after **NMS at 60 s**.

The paper's justification (arXiv 2606.12300, §0 provenance): at K = 3 mIoU was
0.354, and raising K to 10 lifted recall to 96.7% but *dropped* mIoU to 0.295 —
more candidates dilute precision faster than they recover recall. That is a property
of the paper's CLIP retriever and its ground truth, neither of which SeleKyT has, so
the number is not ours; the **shape** — small K, suppress near-duplicates, then
ground the union — is adopted provisionally and must be re-tuned the first time
SeleKyT can measure retrieval quality at all.

NMS at 60 s: candidates are sorted by score descending, and a candidate is dropped if
its start is within 60 s of an already-kept candidate's start. 60 s is a
*chapter-scale* suppression: it stops the same passage spoken twice eight minutes
apart from filling all three slots, while allowing genuinely distinct chapters
through. It is a starting value, not a tuned constant, and it must be a named
parameter on the retrieve call rather than a buried literal.

**Stopwords are handled by idf, not by deletion.** Deleting stopwords from the index
would make the phrase query impossible (a phrase containing "the" could not be
matched) and would make the index's term set depend on an English stoplist in a
corpus that is mostly Telugu. Scoring them near-zero via idf achieves the same
ranking effect and keeps every term retrievable. This is the reversible-vs-irreversible
distinction again: low weight can be overridden, deletion cannot.

**What implementation added here.** The formula above is
`log((n_docs + 1) / (df + 1))`, which is **exactly 0.0** — not near-zero, zero —
for a term occurring in every utterance. That is a saturation, not a judgement: a
term present everywhere is evidence-free *by that measure*. But a window matched
only by such terms then scores 0.0 from `weighted_containment` and a genuine hit
ranks indistinguishably from no hit at all. The implementation floors idf at
`IDF_FLOOR = 0.1` (`engine/retrieval.py`), which says what is true: a term present
everywhere in *this* source is common, but it is not evidence-free — it still
contributes, weakly, and the floor is a floor rather than a bonus because a term
that common must never outrank a term that is rare.

Two further facts the implementation pinned down, both visible in §3.3's scoring:
the base score saturates at 1.0 for any window containing every query term, so
among complete matches ranking is decided by the optional-channel boosts and the
span-length tiebreak, not by term overlap; and `df` is built from the index's own
postings rather than from `engine.lexical.df_table` over the transcript, because
`df_table` shares the stopword-filtering `content_tokens` uses and would remove
the very terms §3.4 keeps.

### 3.5 What the answer carries

Each returned candidate becomes an `Evidence`-shaped record — claim, intervals,
modality, source, confidence, rationale — with the fields the retrieve stage can
honestly populate:

- `claim`: a declarative sentence, e.g. *"The phrase 'X Y' occurs at 3 spans in this
  source."* The claim is about what the transcript *contains*, not about what the
  video is about.
- `intervals`: the candidate set (§3.2).
- `modality`: `TEXT` for lexical matches; `EDITORIAL` where the optional story-unit
  re-ranking applied. `engine/memory/evidence.py:72-82` is explicit that modality
  order is the pipeline's production order, **not a trust ranking** — the confidence
  field carries quality, and a consumer who ranks modalities a priori will rank wrong
  on this corpus's own Telugu sources.
- `source`: `Source.of("engine.retrieval", "retrieve", k=3, nms_s=60, ...)`, so the
  `params_digest` changes when K or NMS changes and a cached conclusion from a
  different operating point is invalidated rather than inherited
  (`engine/memory/evidence.py:86-99`, `:115-130`).
- `confidence`: derived from the window score *and* the transcript's own word-level
  `confidence` values — a window whose words the ASR itself scored low is a window
  the retrieval layer should not present as solid. This is where the transcript's
  per-word `confidence` field (`models/core.py:9`), which the index stores as
  `word_idx`-addressable side data, finally pays for itself.
- `rationale`: names the scoring path actually taken, including which optional
  channels were absent.

---

## 4. Cost model — measured against the implementation

All figures in this section are **mine** (category 1, §0), and are now measurements
of `engine/retrieval.py` as shipped, on the largest transcript committed to the
repo (`corpus/emwcon-panel-2018_run/project/transcript.json`, **842 segments,
9,093 words, 1,838 distinct terms, 3,332.8 s = 55.5 min**, language `en`, model
`small`, fitness verdict `usable`). Timings are the minimum of 20 (build) or 7
(retrieve) repetitions on this 4-CPU host; a 75-minute figure, where one is given,
is the 55.5-minute measurement scaled ×1.35 by word count, and is labelled as
scaling rather than as a measurement.

**The design's own pre-implementation estimates sit in the right-hand column
below, because the gap is the point.** This document estimated ~0.1 ms for
a full retrieve call on a 75-minute source; the implementation costs **5.5 ms on
55.5 minutes** — ~50× the estimate. The estimate measured the *posting lookups*
(bisects and an intersection), which is what existed to time when no code was in
the tree; a real retrieve call also sweeps candidate windows over every utterance
touching the query terms, scores each with idf, and builds an `Evidence` record per
kept window. That work is linear in the number of utterances the terms touch, and
"the"/"and" touch nearly all of them.

| Operation | Measured (55.5 min, 9,093 words) | Design's estimate | Basis |
|---|---|---|---|
| **Build index once** (postings + segments + word side data, `usable`) | **16.1 ms** | 8.1 ms | min of 20 |
| Build once with the n-gram fallback index written too (`poor` transcript) | **76.3 ms** | not estimated | min of 20; 8,177 n-gram postings |
| **Full retrieve call**, worst case `["the", "and"]` (df 291 + 271) | **5.45 ms** → 3 intervals | ~114 µs | min of 7 |
| Full retrieve call, `["the", "a"]` | 4.84 ms → 3 | — | min of 7 |
| Full retrieve call, common discourse pair `["okay", "so"]` | 2.74 ms → 3 | — | min of 7 |
| Full retrieve call, content-word pair `["panel", "question"]` | 2.33 ms → 2 | single-digit µs | min of 7 |
| Full retrieve call, term absent from the index | **0.01 ms** → 0 | — | min of 7; no windows to sweep |
| Index size on disk, `usable` | **885 KiB** | 373 KB | `os.path.getsize`; §2.1 |

**The number that matters: a retrieve call costs ~5 ms at its worst on a real
55-minute transcript, ~7 ms scaled to 75 minutes, and the index costs ~16 ms to
build once and ~0.9 MB on disk.** A batch of 1,000 exploratory queries is ~5 s of
CPU — not the ~0.5 s this document estimated, and still not the expensive stage.

The budget in §7 (`test_retrieve_completes_under_50ms_on_a_75_minute_index`) is
therefore a real margin rather than the ~500× headroom this document assumed: the
measured worst case sits at roughly a tenth of it, and a query that regresses
10× is caught. **Retrieval is not the expensive stage, and never will be** — the
claim survives the correction, by a smaller margin than claimed.

**Why that ordering is load-bearing.** The expensive thing on this host is ffmpeg
*decode*. A 75-minute source at ~1,620 s of 720p to decode for grounding dwarfs
everything above. That is precisely what K = 3 and NMS buy: they cut the number
of decode windows before any decode happens. The paper's 6.7×-cheaper claim is
directionally the same comparison — decode-the-union beats decode-everything — but
SeleKyT's factor is unmeasured here and I will not quote the paper's as if it were
ours.

**The pathological case the index must survive.** `docs/product-gap-map.md` records
`telugu-tutorial`: **9 words over 332 s — 2 words per minute** — in a file that is
77.4% speech. A 75-minute source at that rate is ~135 words. The index must build,
query, and return honest emptiness on such a source without error, without looping,
and without inventing spans; the test surface names it (§7, `test_retrieval_index_
survives_a_near_empty_transcript`).

---

## 5. What 91.54% WER breaks — and what the design does about it

**This is the largest single threat to the design, and it is not a tuning problem.**

At WER 91.54% (`telugu-medium`, `corpus/wer/runs/telugu-medium.json`; 91.54% WER,
69.24% CER, 50 clips; `small` is worse at 151.08% WER, 1.274 CER), **a word-level
exact-match postings index has near-zero recall, because the indexed terms are not
the words that were spoken.** A query term survives into the transcript only by
accident. Retrieval over a transcript at 91% WER is a *different system* from
retrieval over one at 11% WER, and the difference is not a threshold — it is a
change in what kind of object the transcript is.

The mechanism is documented in `docs/telugu-language-ir-spec.md`. ASR output script
share: Telugu 56.4% (small) / 68.7% (medium), Devanagari 23.9% / 13.0%, Latin
8.8% / 12.4% — against references at 98.83% Telugu. The spec's worked clip
`tef_01033_00007107192`:

- reference: `అనగా మన దేశానికి హిందీ అధికార భాష`
- hypothesis: `अनागा मनदेसान की फिंदी अदिकार बाशा`
- **WER 1.00, CER 0.645**

The phonetics are right and the script is entirely wrong. **WER is the wrong unit for
this failure**: a word-matching index sees zero overlap where a character-level view
sees substantial overlap. CER degrades more gracefully than WER, and that asymmetry
is the design's lever.

**Mitigations, in the order they apply.**

1. **Gate before indexing.** `selekyt/pipeline.py:758-766` already runs:

   ```python
   fit = language_fitness(detected or language, asr_provenance.get("model") or model)
   if language_is_refusable(fit) and not skip_language_check:
       raise RuntimeError(f"Transcription is not usable on this language: {fit.detail} ...")
   ```

   With `WER_REFUSE = 1.0` (`engine/language_fitness.py:84`), Telugu at 91.54% is
   *not* refused — it lands in `poor`, because `docs/product-gap-map.md` shows the
   band's only measured member has no better measured alternative, and
   `is_refusable` (`:329`) returns `True` only for `unusable`. Telugu at 151.08%
   (`small`) *is* refused. So the index is built on a transcript the gate has
   already labelled `poor`, and that label must travel with it — which is mitigation 2.

2. **The verdict travels with the index, never laundered into an authoritative key.**
   `retrieval-index.json` carries `fitness` verbatim. This is Invariant 4 of the
   Language IR spec, quoted at `docs/telugu-language-ir-spec.md:657-660`:

   > A `poor` or `not_measured` verdict is carried into the record, not promoted to
   > an absent one. An index built on `canonical.iso15919` must not launder a
   > 91.54%-WER transcript into an authoritative-looking key; the verdict travels
   > with it.

   Every `Evidence` record the retrieve stage emits carries the verdict in its
   `rationale`. An editor reading a retrieval result over Telugu sees "this came from
   a `poor` transcript" on the result itself, not in a log file.

3. **Character n-gram indexing as the lossy-but-recall-preserving fallback.** Where
   the fitness verdict is not `usable` (WER above `WER_BAR = 0.25`,
   `engine/language_fitness.py:78`), the index *additionally* stores character
   n-gram postings (n = 3–5) over normalised text. This is stdlib — slicing strings —
   and it exploits the measured WER/CER gap: at 91.54% WER / 69.24% CER there is
   character-level signal where there is almost no word-level signal. It is
   explicitly **lossy** and explicitly **query-side**: the n-gram index is a second
   postings map consulted when the word-level index returns nothing, and its matches
   are marked lower-confidence and `grounded: False` in the returned evidence
   (`engine/memory/evidence.py:148`), because an n-gram match is a *hypothesis about
   what was probably said*, not an observation of what the transcript contains.

4. **Degrade to channels that do not depend on lexical accuracy.** When lexical
   retrieval finds nothing, retrieval does not retry harder — it reports which other
   channels are queryable. Speech regions (Signal 3) and editorial units (Signal 5)
   carry temporal structure that is not word-shaped, and a query like "where does the
   speaker stop talking for a long time" is answerable from the VAD channel alone with
   zero dependence on transcription accuracy. §6 specifies this as a *reported*
   fallback: the returned evidence names the channel it actually used.

5. **Emptiness, never a guess** — §6.

**What does NOT appear, and why.** No fuzzy string matcher with a tuned
edit-distance threshold, because a threshold tuned on a corpus with no ground truth
is a fabricated constant; no phonetic encoder presented as understanding; no
retry-with-looser-matching loop, because loosening until something matches is how a
fabricated answer is born.

---

## 6. The degrade path: a query that finds nothing reports nothing

This project's rule, stated in two places, is that an absent signal is reported
rather than fabricated:

> Signals the transcript does not carry are reported as `None` or an empty list —
> never as a fabricated value. (`engine/corpus.py:33-34`)

> An empty answer is data, it says "nowhere in this source", and it is reported
> rather than fabricated. (`engine/memory/intervals.py`, `IntervalSet` docstring)

The retrieve stage inherits this without exception:

- **Zero hits returns an empty interval set and an empty evidence set**, with
  `is_empty` true and `cardinality` 0 — not a null, not a "best effort" span, not a
  fallback to the nearest match unless the *caller* explicitly asks for nearest
  (which is a distinct call, `nearest(t)`, that says what it does).
- **No silent loosening.** A query that fails is not retried with fewer terms, a
  shorter phrase, or a wider window. Any relaxation is an explicit caller decision,
  because automatic relaxation is how "not found" becomes "found something
  unrelated" with no record of the change.
- **Missing optional channels are reported, not defaulted.** If VAD regions did not
  load (`optional.vad.loaded` is false because `--audio-analysis` defaults off,
  `docs/memory-perception-inventory.md:117-172`), or if `editorial_corpus.json` was
  swallowed by its `try/except Exception` (`:213-252`), the returned evidence says
  which channel was unavailable. This is the principle in
  `docs/telugu-language-ir-spec.md:661-662`: *"A silent `None` is the same shape as
  a silent fabrication."*
- **Confidence is recorded, never adjusted.** No smoothing, no inflation, no
  imputation. A filter that drops low-confidence items reports what it dropped — the
  same contract `engine/memory/evidence.py` states for `EvidenceSet` filters.
- **The pathological source degrades cleanly.** The 2-wpm `telugu-tutorial` case
  (§4) builds a near-empty index and answers queries with emptiness, not with an
  exception and not with a span invented to avoid returning nothing.

### 6.1 The handoff to GROUND — the interface this document must satisfy

Retrieval stops at the union. It does not decode, does not look at pixels, does not
verify. The contract GROUND receives:

> **Handoff interface.** A retrieval result is: (a) a variable-cardinality set of
> closed intervals in source seconds, merged at `MERGE_EPS`, ascending-start
> normalised, with `duration` and `cardinality`; (b) one `Evidence`-shaped record per
> interval carrying `claim` / `modality` / `source` (with `params_digest` covering K,
> NMS and the tokeniser) / `confidence` / `rationale` (naming the fitness verdict and
> any absent channel) / `artifacts` (paths an editor can open to check);
> (c) for every optional channel the retrieval consumed, a `loaded` boolean so GROUND
> knows what it may cross-reference. GROUND receives the **union** of the K
> post-NMS intervals, expanded by a caller-specified pad, and grounds exactly that —
> no more, because decode is the cost.

Two constraints on GROUND, stated here because the boundary is where failures
typically enter:

- **GROUND must not expand the answer silently.** If it needs more context it must
  request it, so the interval set stays the answer of record.
- **The verdict travels.** GROUND inherits the fitness verdict in the evidence's
  `rationale`; a grounded conclusion over a `poor` transcript must not be presented
  with the confidence of one over a `usable` transcript (§5, mitigation 2).

---

## 7. Test surface

Failure-first: each test names the hazard it guards against in its docstring,
following the convention already in `tests/test_memory_intervals.py` and
`tests/test_memory_evidence.py` (`test_interval_nan_is_clamped_not_propagated`,
`test_modality_order_is_not_a_quality_ranking`,
`test_source_digest_distinguishes_parameters`).

**Index build**

- `test_retrieval_index_is_byte_identical_across_builds` — *hazard: a nondeterministic
  index makes "stale" untestable and caching unsound.*
- `test_retrieval_index_is_refused_when_the_source_fingerprint_differs` — *hazard: an
  index built for one transcript is served for another, and retrieval answers a
  question about the wrong video.*
- `test_retrieval_index_survives_a_near_empty_transcript` — *hazard: the 2-wpm
  `telugu-tutorial` case crashes or loops on a 9-word index.*
- `test_retrieval_index_reports_absent_optional_channels` — *hazard: VAD absent is
  silently treated as VAD-empty, so two runs on the same source answer in different
  characters.*
- `test_retrieval_postings_are_sorted_by_start` — *hazard: an unsorted postings list
  makes `bisect` silently return wrong windows, not an error.*

**Retrieve**

- `test_retrieve_returns_a_set_of_intervals_not_one_span` — *hazard: a consumer
  assumes a single span and takes the first of four matches, wrong three times in
  four.*
- `test_retrieve_phrase_query_does_not_match_terms_in_reverse_order` — *hazard:
  "adjacent" collapses to "both present" and the answer widens to unrelated passages.*
- `test_retrieve_reports_emptiness_when_nothing_matches` — *hazard: a zero-hit query
  returns a nearest span and the editor clips something that was never said.*
- `test_retrieve_does_not_silently_loosen_a_failing_query` — *hazard: automatic
  relaxation turns "not found" into "found something unrelated" with no trace.*
- `test_retrieve_nms_suppresses_repeats_within_60s` — *hazard: one passage spoken
  twice fills all K slots and three distinct chapters are never returned.*
- `test_retrieve_respects_k_and_does_not_return_extras` — *hazard: K is advisory and
  downstream code assumes exactly three windows.*
- `test_retrieve_carries_the_language_verdict_in_every_result` — *hazard: a `poor`
  transcript's retrieval results are presented with the confidence of a `usable`
  one's, laundering 91.54% WER into authority.*
- `test_retrieve_low_confidence_words_lower_window_confidence` — *hazard: the ASR
  itself said these words are doubtful and retrieval presents them as solid.*
- `test_retrieve_source_digest_changes_with_k_or_nms` — *hazard: a conclusion from
  K = 3 is inherited by a K = 10 run and treated as the same measurement.*
- `test_retrieve_stopwords_are_scored_low_not_deleted` — *hazard: a deleted stopword
  makes every phrase containing one unmatchable, and the term set silently depends on
  an English stoplist in a Telugu corpus.*

**High-WER degrade path**

- `test_retrieve_character_ngram_fallback_is_marked_ungrounded` — *hazard: a
  probable-but-not-observed match is presented as an observed one.*
- `test_retrieve_ngram_fallback_only_runs_when_word_index_is_empty` — *hazard: the
  lossy index shadows the exact one on sources where the exact one is correct.*
- `test_retrieve_reports_channel_instead_of_fabricating_on_empty_lexical_hit` —
  *hazard: lexical failure is papered over with a span instead of handed to a channel
  that does not depend on transcription accuracy.*

**Cost**

- `test_retrieve_completes_under_50ms_on_a_75_minute_index` — *hazard: retrieval
  quietly becomes the expensive stage and the pipeline's order stops paying for
  itself.* (Budget is ~7× the measured worst case of ~7 ms scaled to 75 minutes —
  a regression alarm with real margin, not the ~500× this document assumed before
  the code existed.)
- `test_retrieve_index_build_is_cheap_on_a_75_minute_transcript` — *hazard: the
  index is built on every run, so a quadratic build makes the pipeline slower for
  a file the run then writes and no one reads.*
- `test_retrieval_index_size_is_modest_for_a_long_source` — *hazard: the index is
  written next to the transcript on every run and quietly grows past the source it
  indexes.*

**What §7 named and the suite also had to add.** Every name in the three lists above
exists in `tests/test_retrieval.py`; the suite ships 37 tests, and the extras are the
hazards implementation exposed —
`test_retrieve_phrase_query_rejects_terms_too_far_apart` (adjacency is a time
bound, not just an order one), `test_retrieve_ties_break_toward_the_tighter_span`
and `test_retrieve_returns_intervals_in_ascending_start_order` (the ranking's
tiebreak and the output's order are both observable contracts),
`test_retrieve_boosts_a_window_inside_measured_speech` /
`_an_editorial_unit` (the two optional channels re-rank, and a channel that does
not change the score is a channel that is not wired),
`test_retrieve_names_the_absent_channel_in_the_rationale` and
`test_retrieve_result_reports_the_queryable_channels_on_empty` (an empty answer
must still say what *is* answerable), and
`test_retrieval_index_round_trip_loses_nothing` /
`test_retrieval_index_segment_array_covers_every_word` /
`test_load_transcript_reads_the_pipeline_record` (the index must re-read what the
pipeline wrote, byte for byte). A pipeline-level test,
`tests/test_pipeline_e2e.py::test_a_run_writes_a_retrieval_index_that_answers_its_own_transcript`,
loads what a real run wrote and queries it — an index that round-trips in unit
tests but not through `selekyt.pipeline.run` is a missing index.

---

## 8. Summary of the design's bets

1. **The transcript is the only indexable channel that is both structured and
   reliably on disk.** Signals 1 and 2 never reach disk; 3 and 4 are behind a
   default-off flag or unserialised; 5 is the one that reaches disk and it is wrapped
   in a blanket `try/except`. Every optional input is recorded per-input, not assumed.
2. **The index is a ~0.9 MB inverted file, rebuildable in ~16 ms from the transcript
   alone.** Everything derivable is derived on read; only postings, the segment
   array and the per-word confidence side data are stored. Deterministic,
   fingerprint-bound, refused-when-stale. (The design bet on 373 KB / 8 ms; the
   word-confidence field that makes §3.5's doubt-reporting possible costs 20% of
   the file and the bet was worth it.)
3. **A retrieve call costs ~5 ms at its worst on a real 55-minute transcript, not
   the ~0.1 ms this document estimated.** Retrieval is still not and will never be
   the bottleneck; ffmpeg decode is — the claim survives, by a smaller margin than
   claimed. K = 3 with 60 s NMS exists to shrink the decode set, and its values are
   provisional because SeleKyT cannot yet measure retrieval quality — there is no
   ground truth.
4. **The query language is a keyword matcher that says it is a keyword matcher.**
   No semantic embedding, no fuzzy matcher, no new dependency. Scoring is the
   project's existing stdlib `idf` / `containment` / `weighted_containment`.
5. **The answer is a variable-cardinality interval set, always.** One span is a
   special case the caller asks for explicitly, never the default shape.
6. **91.54% WER breaks word-level recall, and the answer is the gate (refuse the
   unusable), the verdict (carry `poor` on every result), the character-level
   fallback (lossy, marked ungrounded, consulted only when the word index is empty),
   and the non-lexical channels — in that order, and never a fabricated match.**
