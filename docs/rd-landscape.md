# External landscape: what the R&D sweep found, and what was verified

Verified 2026-09-24 against the GitHub API. Every star count and licence in this
document was fetched, not recalled.

Four domain sweeps were run — long-video understanding, visual perception,
timeline/NLE interchange, and reframing/captions — producing **88 distinct
repo-level verdicts**. This document is the deduplicated record of those
sweeps, restricted to what is actionable for SeleKyT: what to integrate, what
is already integrated, what was already considered and declined, and which
popular projects are unusable on licensing grounds.

| Domain | Evaluated | INTEGRATE | INTEGRATE (conditional) | REFERENCE-ONLY | REJECT |
|---|---|---|---|---|---|
| Understanding | 27 | 4 | — | 15 | 8 |
| Perception | 20 | 8 | 3 | 8 | 1 |
| Timeline / NLE | 16 | 5 | — | 4 | 7 |
| Reframe / captions | 25 | 4 | — | 10 | 11 |

The conditional column is perception-only and matters: three of its eight
INTEGRATE verdicts are qualified (`INTEGRATE-trial`, `INTEGRATE-eligible`,
`INTEGRATE-as-port`) because the *code* is permissive but the *weights* are not,
or have no licence at all. See [Weight provenance](#weight-provenance).

---

## Verification method and accuracy audit

166 bolded `owner/repo` tokens were extracted from the four reports, deduplicated,
and queried through `gh api` (8-way parallel, 3 retries on 503). 162 returned
well-formed records; the remaining 11 were verified individually. A cross-checker
then parsed every star/licence *claim* out of the report prose — in both claim
formats the reports use — and compared them to the API values, tolerating 2% on
stars and mapping licence aliases.

**Result: zero fabricated claims.** 44 claims checked, all accurate. Two defects,
neither of them a fabrication:

1. **google/mediapipe — "~50k+"** against an actual **37,067**. The claim is
   hedged with a `+`, so it is an over-estimate rather than a wrong number, and
   the repo has since moved to `google-ai-edge/mediapipe` — a redirect that
   keeps the stars but changes the canonical name.
2. **`gdamiskinos/unsupervised_topic_segmentation`** is a one-letter typo for
   `gdamaskinos/...`. The claimed figures (106, MIT, last push 2021-07-06) are
   exact; only the bolded owner name is misspelled.

Every figure in the tables below is the **verified** value, so the typo and the
hedge do not propagate.

---

## Understanding: story segmentation, retrieval, redundancy

| Repo | Stars | Licence | Last push | Status for SeleKyT |
|---|---|---|---|---|
| Breakthrough/PySceneDetect | 5,196 | BSD-3-Clause | 2026-09-21 | **Already considered and declined** — see below |
| huggingface/sentence-transformers | 19,117 | Apache-2.0 | 2026-09-24 | Trial candidate for paraphrase-level redundancy |
| rapidfuzz/RapidFuzz | 4,138 | MIT | 2026-09-12 | Trial candidate for fuzzy string matching |
| retkowski/chunkseg | 5 | MIT | 2026-06-11 | Reference for chunking shape; negligible adoption |
| gdamaskinos/unsupervised_topic_segmentation | 106 | MIT | 2021-07-06 | Reference only — unmodified since 2021 |

**PySceneDetect was already answered.** `engine/reframe_perception.py:33-35`
records the decision: PySceneDetect's content-valued shot detection is
reproduced in-tree by a mean-absolute-difference test on the sample grid, which
is the part of it the tracker needs, and `scenedetect` is deliberately **not** a
dependency. The sweep's INTEGRATE verdict therefore lands on a closed question —
but the same file names the one place the verdict still applies, at
`engine/reframe_perception.py:219-225`: a MAD test cannot separate a *pan* (the
subject leaves frame and returns; earlier positions stay valid) from a *cut*
(every earlier position is void), because both are large magnitude jumps. Both
are currently treated as a reset, which is safe for a pan and conservative for a
cut. PySceneDetect's `AdaptiveDetector` is the specific thing that would fix
that, and the code already marks the spot. That is the actionable item from this
row, not "add the library."

**sentence-transformers needs to be argued against the existing design, not
assumed to fill a gap.** `engine/lexical.py` and `engine/topics.py` implement
topic clustering and redundancy grouping as **pure lexical term-overlap** —
deterministic, zero-dependency, no model download, and deliberately sharing
tokenisation with `engine.story_units` so a corpus-level judgement cannot drift
from the unit boundaries it is built on. Adding an embedding model trades that
determinism and the zero-download property for genuine semantic matching
(paraphrase, synonymy across a two-hour recording). The trial worth running is
narrow: does embedding-based similarity catch redundancy that lexical overlap
misses on real corpus, enough to justify a model download on Android/Termux?
Not yet measured.

---

## Perception: detection, tracking, identity

| Repo | Stars | Licence | Last push | Status for SeleKyT |
|---|---|---|---|---|
| k2-fsa/sherpa-onnx | 14,950 | Apache-2.0 | 2026-09-22 | **Already integrated** — `providers/sherpa.py`; weight audit open |
| modelscope/FunASR | 20,493 | MIT | 2026-09-24 | Weight provenance risk — see below |
| FoundationVision/ByteTrack | 6,705 | MIT | 2024-06-19 | Tracker algorithm reference; C++/Rust path only |
| tryolabs/norfair | 2,685 | BSD-3-Clause | 2025-04-30 | Best-fitting tracker: no weights, no GPU, no licence risk |
| Smorodov/Multitarget-tracker | 2,418 | Apache-2.0 | 2026-09-03 | If tracker internals must be fully owned |
| soCzech/TransNetV2 | 1,045 | MIT | 2023-12-04 | Incumbent shot-boundary standard; single-purpose |
| davisking/dlib-models | 1,615 | CC0-1.0 | 2025-05-28 | Best-licenced face recognition weights, per-model |
| TaoRuijie/TalkNet-ASD | 505 | MIT | 2023-10-23 | Port the net to ONNX, do not import the repo |
| google-ai-edge/mediapipe | 37,067 | Apache-2.0 | 2026-09-24 | Keypoints (torso centre, face) per speaker, permissively |
| deepinsight/insightface | 29,818 | **NONE** | 2026-09-09 | Code repo carries no licence at all — do not bundle |
| pyannote/pyannote-audio | 10,592 | MIT | 2026-09-22 | Embedder provenance needs auditing per checkpoint |

`sherpa-onnx` is already a transcription provider (`providers/sherpa.py`), so
that row's verdict is satisfied for the code; the open item it carries is the
**weight audit**: the Apache-2.0 licence covers the code, and each individual
checkpoint has its own terms that must be checked before bundling.

**deepinsight/insightface is the trap the sweep correctly caught.** 29.8k stars,
actively pushed, and the repository's own licence field is **empty** — GitHub
reports no SPDX id at all. The sweep did not recommend it; it recommended the
*dlib SFace* path through `opencv_zoo`, whose weights are verified CC0/Apache
in-directory. That distinction — permissively-licenced weights reached via a
repo that has no licence on its code — is exactly the provenance discipline the
product needs, and it is the reason the row stays in this table.

---

## Timeline and NLE interchange

| Repo | Stars | Licence | Last push | Status for SeleKyT |
|---|---|---|---|---|
| AcademySoftwareFoundation/OpenTimelineIO | 1,987 | Apache-2.0 | 2026-09-22 | **Already a build dep** — `engine/nle_export.py` |
| slhck/ffmpeg-quality-metrics | 561 | MIT | 2026-09-17 | The one QA tool that is genuinely library-shaped |
| OpenTimelineIO/otio-cmx3600-adapter | 9 | Apache-2.0 | 2025-12-12 | Worth adding as a *third* EDL reader |
| iluvcapra/pycmx | — | — | — | **Already integrated** — `tools/nle_check.py` |
| FCPXML fixtures (v1.6/v1.8/v1.11) | — | — | — | **Already vendored** — `third_party/PROVENANCE.md` at `20d9fa5` |

Three of the five INTEGRATE verdicts in this domain are **already done and done
correctly**: OpenTimelineIO is imported in `engine/nle_export.py` and
`tools/nle_check.py`; `pycmx` is the existing EDL reader; the FCPXML fixture set
is vendored byte-identically with origin, commit SHA and Apache-2.0 text
recorded in `third_party/PROVENANCE.md`, and its v1.11 samples are files Final
Cut Pro actually wrote — which is the strongest available evidence that the
parser handles real FCP output, and is exactly the version SeleKyT emits.

The genuinely new item is the **otio-cmx3600-adapter as a third EDL reader**.
SeleKyT writes EDL itself and reads EDL with `pycmx`; a second, fully
independent parser reading the same file gives a cross-check on clip count and
per-clip timing that a single parser cannot provide. Nine stars — this is the
case the brief makes explicit: adoption is not the criterion.

Two findings worth recording from the REJECT rows:

- **DaVinci Resolve is not a first-class concern of any open-source EDL tool.**
  `pycmx` ships `avid` (default), `nucoda`, `premiere` style variations and
  *no* `resolve` style. If Resolve interchange becomes a requirement it will
  need its own validation pass, not a flag.
- **`justinishere/pyCMX` does not exist** (404) despite being the commonly-cited
  fork. The real package is `pycmx` from `iluvcapra/pycmx`. Any doc or install
  instruction pointing at the former is broken.

---

## Story memory: graphs, links, narrative structure

Researched for the linked story-memory layer (`docs/story-memory-design.md`,
shipped as `engine/story_path.py`, `engine/story_view.py`,
`engine/corpus_io.py`). The headline result of this section is a negative one and
it shaped the whole design: **no dependency was added.**

| Repo / project | Stars | Licence | Last push | Status for SeleKyT |
|---|---|---|---|---|
| google/open-knowledge-format | 588 | Apache-2.0 | 2026-09 | **Pattern, not dependency** — see below |
| AcademySoftwareFoundation/OpenTimelineIO | 1,987 | Apache-2.0 | 2026-09-22 | **Already a build dep**; the graph lives *outside* it — see below |
| networkx | 16,300 | BSD-3-Clause | 2026-09-24 | **Deliberately not added** — see below |
| shon-akermiss/fastcoref | 265 | MIT (code) | 2026-08 | **Deliberately not integrated** — see below |
| isanlp/isanlp_rst | — | MIT (code) / **CC-BY-NC-4.0 (weights)** | — | **REJECT — the trap in this domain** |
| igraph, graph-tool, leidenalg, netgraph, grandalf | — | GPL-2.0 / GPL-3.0 / LGPL-3.0 | — | **REJECT — copyleft** |
| schema.org | — | **CC-BY-SA 3.0** | — | **REJECT — share-alike on the vocabulary itself** |

**OKF is the right pattern and the wrong format.** Open Knowledge Format is real,
vendor-neutral, Apache-2.0 and at v0.2 — it is not vapour. But its links are
explicitly **untyped**: a document can say "these two moments are connected" and
cannot say "this one sets up that one." A typed `sets_up → pays_off` edge is the
entire substance of a story graph, so importing OKF would mean importing a
structure that cannot express the one thing the layer exists to express. What was
taken instead is the *shape* of it: source-grounded nodes, per-edge provenance,
and a bundle layout a consumer can walk without understanding the analyser. The
directive's "do not blindly implement OKF or become dependent on a Google system"
is satisfied by that distinction — SeleKyT keeps its own canonical representation,
and `docs/story-memory-design.md` §8 records the comparison in full.

**OpenTimelineIO is a tree, and the story graph is not.** An OTIO timeline is a
nested `Stack → Track → Clip` structure; a story is a graph where one moment
belongs to several stories with different roles. The graph therefore lives
outside OTIO, keyed by the source ranges OTIO clips are built from, and every
generated timeline stays traceable back to story nodes and original source
ranges. What was taken from OTIO is its persistence discipline —
`Schema{name,version}` plus a type registry plus an `unknownSchema` fallback, so
a file written by a newer SeleKyT stays readable by an older one for everything
the older one understands. `tests/test_corpus_io.py` pins that behaviour.

**networkx was the one candidate with an unimpeachable licence** (BSD-3-Clause,
zero further dependencies), and it is still not a dependency. The graph this
layer holds is small and structurally bounded — `engine/threads.py` emits edges
with `for j in range(i + 1, min(n, i + 1 + window_units))`, so the edge set is
O(U × W) and never O(U²) — and the operations the layer performs are a window
walk and a chain walk, which are clearer as twenty lines of indexed list code
than as a library call. Adding a general graph library to express two bounded
loops is exactly the "dependency because it is interesting" the brief rules out.
`tests/test_story_bench.py` asserts the bound rather than trusting it.

**fastcoref is MIT code with MIT weights on Hugging Face, runs on CPU, and was
not integrated.** Coreference would let a link follow a pronoun back to its
antecedent rather than requiring lexical overlap. It was not added because the
implemented link kinds are all *evidence-grounded by construction* — each cites
the lexical overlap, the question form, or the result connective that caused it —
and a coreference resolver's errors would produce links with no checkable
evidence. `tests/test_story_memory.py` pins the refusal: a transcript with two
unrelated passages about satellites produces **no** link, not a weak one. If
coreference is ever added, it belongs in the evidence layer with its own
confidence, not as a silent replacement for lexical overlap.

**isanlp_rst is the trap this domain sets.** MIT code, RST discourse parsing —
exactly the "how does this moment relate to the next" question the layer asks —
and weights under **CC-BY-NC-4.0**, non-commercial. The licence is on the
artefact the code is useless without, so shipping it pollutes the product. This
is recorded because the attractive half (MIT code) is what a licence scan that
only reads `LICENSE` would report.

**schema.org is rejected on its vocabulary.** The terms are share-alike
(CC-BY-SA 3.0), so adopting the vocabulary would put licence obligations on
SeleKyT's own data model. The five implemented relationship kinds are SeleKyT's
own names, declared at `engine/threads.py:262`.

**What no repo in this domain solves, and SeleKyT therefore implements itself:**
typed setup→payoff edges grounded in transcript evidence (not discourse-parse
labels), per-story role assignment that a human editor can override, and the
round trip — analyse once, export, re-import into a clean process, and generate
the same edits. That last one is `tests/test_story_memory.py`, and it is the test
the brief calls the most important of the seventeen.

---

## Reframe and captions

| Repo | Stars | Licence | Last push | Status for SeleKyT |
|---|---|---|---|---|
| tkarabela/pysubs2 | 440 | MIT | 2026-08-16 | The caption format layer; ASS-native is the right internal model |
| sandflow/ttconv | 238 | BSD-2-Clause | 2026-09-17 | For broadcast TTML specifically, alongside pysubs2 |
| cdown/srt | 535 | MIT | 2024-03-19 | Redundant with pysubs2; ~2.5y since last commit |
| bubblesub/ass_tag_parser | 16 | MIT | 2024-06-23 | Only if emitted ASS tags must be *read back* |
| chireiden/python-ass | 46 | MIT | 2025-11-29 | Rejected — its unmaintained renderer is a trap |

None of these are current dependencies. `pysubs2` is the row that matters:
zero-dep, ASS-native, actively maintained. SeleKyT emits positioned captions
through ffmpeg/libass, which is correct for rendering, but the *editable*
caption document is not yet round-trippable in a library — an editor's cue
repositioning cannot be read back and re-applied by code.

This is a live defect, not a hypothetical, and it is **closed** as of
2026-09-24 — in-tree, without `pysubs2`. The shipped `.ass` is a render input:
`selekyt/pipeline.py` hands it to `render_clip`, which burns it in via
`subtitles=` at `engine/render.py:199`. Before that date the pipeline rewrote
the `.ass` unconditionally on every run, clobbering the edit before the cache
was consulted; the per-clip render cache key omitted the document's contents, so
a preserved edit still shipped the cached MP4 with the old cue burned in; and the
per-part ASS for a multi-interval candidate was *regenerated* from a
sub-candidate, so an editor's retime on a multi-cut clip was preserved on disk
and then discarded by the render. All three are fixed:
`engine.captions.ship_captions` keeps a document that is neither the fresh
generation nor our own last write and re-projects the sidecar from it; the
record carries an `ass_digest` that joins the cache key (pinned by
`tests/test_caption_handoff.py` and `tests/test_caption_style_cache.py`); and
`engine.captions.slice_ass` keeps the cues overlapping a window, clips a
straddling cue to the window's edges, and shifts the survivors onto part-local
time, so the shipped document is what every part is burned from
(`tests/test_caption_multi_part.py`).

`pysubs2` was the nominated dependency for the slicing, and the reason it ranks
first below. That specific reason no longer applies: the operation turned out to
be 35 lines of in-tree code on the dialect this module already owns, because the
only thing a part needs from the document is cue selection and time retargeting —
an editor's text and override tags are passed through verbatim. The verdict's
*remaining* half is what keeps the row alive and is not yet addressed: the
document is still not round-trippable in a library, because reading an editor's
cue *repositioning* back and re-applying it requires a parser for ASS override
tags. That is `ass_tag_parser`'s exact scope, and it would be the dependency to
reach for if that capability is ever needed — `pysubs2` for the document model,
`ass_tag_parser` if only the tag reading is.

The dependency discipline this row ended up exercising is worth recording: a
recommendation made on a real defect was satisfied without the dependency, and
the recommendation survived the fix because its other half was stated precisely
enough to see. Verdicts written as "nice to have" would not have behaved that
way.


Two deliberate non-features of `ttconv` are recorded here so they are not
rediscovered as bugs: it does **not** parse or validate timecodes and does
**not** enforce frame rates — by design, to stay tolerant of mixed-rate EDLs.
It will not catch a drop-frame error in SeleKyT's own output; that check must
live in `engine/timecode.py`.

---

## Weight provenance

The recurring finding across all four domains: **permissive code with a
restrictive or absent weight licence is the default, not the exception.**

| Project | Stars | Code licence | Weight licence | Verdict |
|---|---|---|---|---|
| serengil/deepface | 23.5k | MIT | "check the license types of those models" | REJECT — the exact trap |
| KaiyangZhou/deep-person-reid | 4.9k | MIT | Not stated; Google Drive/HF zoo | REFERENCE-ONLY |
| pyvideotrans / VideoCaptioner | 19.1k / 16.1k | GPL-3.0 | — | REJECT |
| aTrain | 1,213 | AGPL-3.0 | — | REJECT |
| Aseiel/VideoHighlighter | 133 | AGPL-3.0 | — | REJECT |
| deepinsight/insightface | 29.8k | **NONE** | Per-model | Do not bundle the code repo |
| CMU openpose / OpenFace / qctools | — | NOASSERTION | — | Unusable without manual review |

The deeper provenance warning, which applies to the whole face-recognition
domain and not to one repo: **most strong face recognizers — the ArcFace family
— are trained on MS1MV2/Glint360k, which are themselves derived from
research-only celebrity data.** A clean code licence and a clean checkpoint
licence do not clear this. The sweep's recommendation is the correct one: use
SFace (`dlib-models`, CC0) and Tencent Youtu re-ID weights (`opencv_zoo`,
Apache-2.0 verified in-directory) — weaker than ArcFace, but the chain of
custody is intact, which for a commercial product is the binding constraint.

---

## What no repo in the sweep solves

Recorded so the gaps are not mistaken for search failures:

1. **Editorial judgement.** No project scores a story's hook, setup, turn and
   payoff. That is `engine/editorial.py` and `engine/story_units.py`, and the
   sweep produced nothing to import there — only references for what a
   coherence scorer could measure. The linked-memory layer narrows this gap
   further but does not close it: it consumes the judgements the corpus already
   made rather than producing new ones (see [Story memory](#story-memory-graphs-links-narrative-structure)).
2. **DaVinci Resolve EDL dialect.** No open-source tool treats Resolve as a
   first-class target (see above).
3. **Cut-vs-pan discrimination on the sample grid.** Available from
   PySceneDetect's adaptive threshold, but not as a weight-free 50-line
   library function — the integration is the work.
4. **Speaker-identity persistence across a long recording with permissively
   licensed weights.** `sherpa-onnx` gives diarization; cross-shot identity
   re-association at acceptable licence quality remains open (SFace is the
   nominated, untrialled path).

---

## Ranking the remaining work

Ordered by real user value × fit, with the dependency cost stated:

1. **`pysubs2` (or `ass_tag_parser`) for reading an edited document back** —
   the multi-interval slicing it was originally nominated for is now in-tree
   (`engine.captions.slice_ass`), so the remaining case is narrower and still
   open: an editor's cue repositioning cannot be parsed back and re-applied by
   code. Zero-dep MIT, either way. See [Reframe and captions](#reframe-and-captions).
2. **`slhck/ffmpeg-quality-metrics` for sampled per-frame SSIM** between a
   rendered preview and expected frames. MIT, library-shaped, and it
   strengthens `engine/qa.py` with a metric that is currently absent.
3. **Cut-vs-pan adaptive threshold in `engine/reframe_perception.py`** — the
   one PySceneDetect idea that is still open, implementable in-tree without
   adopting the library.
4. **`otio-cmx3600-adapter` as a third EDL reader** in `tools/nle_check.py` —
   independent cross-check on SeleKyT's own EDL output.
5. **SFace / opencv_zoo re-ID trial** — the only permissively-licensed identity
   path; needs corpus measurement before any trust.
6. **`sentence-transformers`** — only if the trial in [Understanding](#understanding-story-segmentation-retrieval-redundancy)
   shows lexical overlap materially missing redundancy on real corpus.
