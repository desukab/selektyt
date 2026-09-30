# Memory perception inventory — SeleKyT

What the pipeline already perceives, in what units, where it is discarded, and what an
ingest adapter must intercept to capture it. This is the contract for
`engine/memory/ingest.py`.

Conventions: **[code]** = stated in source or docstrings; **[inference]** = a judgement
made by reading the modules, not the code speaking. All shapes verified by reading the
modules, not by assumption.

---

## 0. The hypothesis, checked

The hypothesis is confirmed **[code]**: the reframe perception chain decodes a window, runs a face detector over every sampled frame, computes shot boundaries and slide geometry, then reduces all of it to an ffmpeg filtergraph *string*. Nothing under `engine/reframe.py` or `engine/reframe_scenes.py` writes any perception to disk — the rendered pixels in `shorts/NN.mp4` / `facebook/NN.mp4` are the only durable trace of the perception. The in-process caches (`_REFAME_CACHE`, `_FRAME_CACHE`, `_SCENE_CACHE` in `engine/reframe.py` / `engine/reframe_scenes.py`) survive only as long as the process. `docs/product-gap-map.md` states this independently: `_sample_frames` is the single choke point serving `track_subject_center`, `_probe_dims`, `analyse_scene`, `scene_filter` and the benchmark, with the caches measured at 1.99×/9.31 s saved per clip.

For audio, the picture is more nuanced than "thrown away": the raw VAD score vector *is* cached to disk — but only when `--audio-analysis` is passed, and that flag **defaults to `False`** in `selekyt/pipeline.run` **[code]**. On a default run, every audio artifact is absent.

For story structure, nothing is discarded: `editorial_corpus.json` is written in full **[code]** — but inside a `try/except Exception` that prints to stderr and continues, so it can silently vanish **[code]**.

The four modalities already defined in `engine/memory/evidence.py` (`TEXT`, `AUDIO`, `VISUAL`, `EDITORIAL`) map cleanly onto signals 5/3+4/1+2/5 respectively, so the adapter layer can name its sources with the vocabulary the store already has.

---

## 1. Face / subject tracks

**Module:** `engine/reframe.py`
**Detector:** `engine/reframe_perception.py` — `FaceDetector` singleton via `get_detector(model_path=None) -> FaceDetector`; `kind` ∈ `{"yunet", "haar", "none"}`; `.available -> bool`; `.detect(frame) -> list[tuple[float, float, float, float, float]]` returning `(x, y, w, h, score)` **in source pixels, best score first**. YuNet score is `row[14]`, threshold `_YUNET_SCORE = 0.5`, NMS `0.3`, min face `_MIN_FACE_PX = 48`. Haar (OpenCV 4.x only) has no score — box area stands in. Chain degrades to `"none"`, never to a silently-wrong heuristic **[code]**. Score is comparable *within* a backend only **[code]**.

**Signatures (verified):**
```python
def _sample_frames(video, start, end, samples) -> tuple[list[float], list[np.ndarray | None], int, int]
def _perceive_window(video, start, end, samples) -> tuple[list[float], list[np.ndarray | None], int, int,
                                        list[list[tuple[float, float, float, float, float]]], frozenset[int]]
def plan_crop(times, detections, boundaries: set[int], w, h, crop_w) -> list[tuple[float, float]]
def track_subject_center(video, start, end, samples: int = 28) -> list[tuple[float, float]]
```

**`_perceive_window` return (the richest shape in the chain) — field list and units:**
1. `times: list[float]` — seconds **relative to the interval start** (`t_abs - start`).
2. `frames: list[np.ndarray | None]` — BGR24 source-pixel frames; `None` on decode failure.
3. `w, h: int` — source pixel dimensions.
4. `per_frame: list[list[(x, y, w, h, score)]]` — detections **in source pixels**, one list per sampled time.
5. `boundaries: frozenset[int]` — indices `i` where `frames[i]` starts a new shot.

**Sampling grid:** `np.linspace(start, end, max(2, samples))` **[code]**, so stride ≈ `(end - start) / (samples - 1)`. Default `samples=28` → ~1.2 s on a 30 s clip; 0.44–1.98 s across the corpus **[code, docstring]**.

**`_Track` (internal, `@dataclass`):**
```python
samples: list[tuple[float, float, float, float, float, float]]  # (t, x, y, w, h, score)
def center(self, t: float) -> tuple[float, float] | None
def mean_face_w(self) -> float
def evidence(self, t: float, window: float = _EVIDENCE_WINDOW_S) -> float
```
Docstring: *"``samples`` are ``(t, x, y, w, h, score)`` in source pixels with ``t`` relative to the interval start. **Identity is positional only.**"* **[code]** — there is no face recognition or re-identification anywhere.

**`plan_crop` / `track_subject_center` return:** `list[tuple[float, float]]` keypoints documented as *"`(t, centre_fraction)`"* **[code]**, where the fraction is `float(np.clip(x / w, 0.0, 1.0))`. So: **t = seconds relative to interval start; centre = normalised 0..1 of source width.** Not pixels, not absolute source seconds. This is the answer to the units question — confirmed in code, not inferred.

**Current consumer:** `reframe.vertical_reframe_filter` delegates to `reframe_scenes.scene_filter`, called exactly once per vertical clip at `engine/render.py:320` (`graph = vertical_reframe_filter(source, start, end, tail=tuple(sub))`). Also `tools/reframe_benchmark.py:608`. The consumer reads the perception and hands back a filter string; the string is all the caller keeps.

**Reaches disk:** **No.** Nothing in `engine/reframe.py`, `engine/reframe_scenes.py`, `engine/reframe_perception.py` or `engine/render.py` writes face boxes, tracks, boundaries or keypoints anywhere.

**Adapter note:** intercept `_perceive_window` — it is the single choke point for every consumer **[code, product-gap-map]** — and mirror the `_FRAME_CACHE` entry layout `[times, frames, w, h, perception]` to a store keyed by (video fingerprint, start, end, samples); drop the raw frames (they are the bulky, least-reusable part) and keep `times`, `per_frame`, `boundaries`, `w`, `h` plus `detector.kind`. Use `Source.of("engine.reframe", "_perceive_window", samples=samples, detector=kind)` so the params digest invalidates a 28-sample memory against a 56-sample re-run — exactly what `params_digest` exists for **[code]**.

---

## 2. Shot / scene classification

**Module:** `engine/reframe_scenes.py`

**Correction to the stated belief — this matters:** the module docstring describes **four** classes (SPEAKER / SLIDE / SPLIT / TRANSITION), but the code's enum has only **three** members **[code]**:
```python
class SceneClass(Enum):
    SPEAKER = "speaker"
    SLIDE = "slide"
    SPLIT = "split"
```
`TRANSITION` is **not a class.** It is the `xfade` fade the renderer inserts *between* segments whose classes differ, produced by `transition_points(segments) -> list[tuple[float, SceneClass, SceneClass]]` (duration `_TRANSITION_S = 0.5`) **[code]**. An adapter must not model TRANSITION as a class value — it is a relation between adjacent classes.

**Records (all `@dataclass(frozen=True)`):**
```python
class SlideRect:  x: float; y: float; w: float; h: float   # source pixels; .area, .cx
class FaceBox:    x: float; y: float; w: float; h: float   # source pixels; .cx
class SceneSegment:
    start: float; end: float; cls: SceneClass
    slide: SlideRect | None = None; joint: bool = False
```
Docstring: *"`start`/`end` are seconds from the start of the analysed interval, which is the timeline ffmpeg's filtergraph sees when the render seeks into the source: the same convention as `plan_crop`'s keypoints."* **[code]** — i.e. **relative seconds, matching Signal 1's grid exactly**, which makes the two joinable without a timebase conversion.

**Signatures:**
```python
def _cell_maps(frames, w, h) -> tuple[np.ndarray, np.ndarray, np.ndarray]  # staticness, edge density, brightness; CELL = 32
def detect_slides(frames, w, h) -> list[SlideRect]
def analyse_scene(video, start, end, samples: int = 28) -> tuple[list[SceneSegment], list[SlideRect], int, int]
def scene_filter(video, start, end, tail: tuple[str, ...] = (), samples: int = 28) -> str
def classify_scene(face, slide, crop_w, src_w, src_h) -> SceneClass
def plan_scenes(times, classes, boundaries=None) -> list[SceneSegment]
def joint_crop_x(face, slide, crop_w, src_w) -> float | None
def both_fit_in_crop(face, slide, crop_w, src_w) -> bool
def face_crop_window(face, crop_w, src_w) -> tuple[float, float]
def src_band_w(src_w, src_h, sar_w, sar_h, out_w, band_h) -> int
class UnreadableSource(RuntimeError)
```

**`analyse_scene` return:** `(segments, slides, w, h)` — `segments` are `SceneSegment`s covering the interval (first/last extended to the requested `start`/`end` **[code]**), `slides` the `SlideRect`s found, `w`/`h` source pixels. `scene_filter` returns only a **`str`** — the filtergraph — which is where the structure is destroyed. Note `scene_filter` re-perceives the same window via `_perceive_window` at `reframe_scenes.py:1059` after `analyse_scene` already perceived it at `:919`; the `_FRAME_CACHE` exists precisely to make that second call free **[code]**.

Scene classification is geometry, not semantics: slides are found by cell staticness (`_STATIC_MIN=0.80`), edge density (`_EDGE_MIN=0.02`) and brightness (`_BRIGHT_MIN=0.35`) with `_MIN_SLIDE_AREA_FRAC=0.06`; "both fit in one crop" is closed-form interval intersection (`joint_crop_x`) **[code]**. Shot boundaries come from `find_shot_boundaries` (`threshold=22.0`, isolated crossings only — a run of crossings is a pan, not a cut) **[code]**.

**Current consumer:** `reframe.vertical_reframe_filter` → `scene_filter`, then `engine/render.py:320` (see Signal 1).

**Reaches disk:** **No.** Structurally identical discard to Signal 1.

**Adapter note:** `analyse_scene` — not `scene_filter` — is the capture point, because it returns the structured `SceneSegment`/`SlideRect` records rather than a filter string. Emit one `VISUAL` Evidence per `SceneSegment` (claim = class, `intervals` = [start, end]) and one per `SlideRect`, then a second pass over `transition_points` to record class changes as relations (`relates_to` + an Allen relation) rather than as pseudo-classes.

---

## 3. Speech regions (VAD chain)

**Modules:** `engine/vad.py` (producer), `engine/coverage.py` (consumer that throws the regions away), `engine/pipeline_audio.py` (bridge).

**Chain (measured order, `get_vad_backend`) [code]:** silero → ffmpeg → energy → whisper. `get_vad_backend(force: str | None = None, words: list | None = None, duration: float = 0.0) -> VADBackend`. Silero vendored at `engine/models/silero_vad_v6.onnx` **[code]**. Whisper is last because it infers speech from transcript word timings.

**Records:**
```python
@dataclass
class SpeechRegion:  start: float; end: float; score: float = 1.0
@dataclass
class SilenceRegion: start: float; end: float; duration: float
@dataclass
class VADResult:
    scores: list[float]; sample_rate: int; backend: str; frame_ms: int; duration: float
    vad_model: str | None = None; hysteresis: dict | None = None; fallback_reason: str | None = None
```
**Units:** all region times are **source seconds**. Scores are per-frame speech probabilities on a fixed `FRAME_MS = 32` grid (512 samples @ 16 kHz) **[code]**.

**Segmentation (post-hoc, pure, deterministic):**
```python
def segment_from_scores(scores, sample_rate=16000, frame_ms=FRAME_MS, *,
        threshold=0.5, release=None, min_speech_ms=150, min_silence_ms=100, time_offset=0.0) -> list[SpeechRegion]
def segment_with_meta(scores, sample_rate=16000, frame_ms=FRAME_MS, **kwargs)
        -> tuple[list[SpeechRegion], dict]
def silence_from_speech(regions, duration) -> list[SilenceRegion]
def save_scores(result: VADResult, path: Path) -> None
def load_scores(path: Path) -> VADResult | None
```
`segment_with_meta`'s meta dict: `hysteresis{threshold, release, min_speech_ms, min_silence_ms}`, `frame_ms`, `n_frames`, `time_offset`, `speech_regions` (count), `speech_seconds`.

**The exact discard point [code]** — in `coverage.measure`:
```python
vad = backend.analyze(audio)
_regions, meta = segment_with_meta(vad.scores, ...)
vad_seconds = meta["speech_seconds"]
```
The full `VADResult.scores` vector and the entire region list are bound to throwaway names; only the aggregate `speech_seconds` survives into a `CoverageReport`:
```python
@dataclass
class CoverageReport:
    duration_s: float; vad_speech_s: float; vad_speech_frac: float
    asr_speech_s: float; asr_speech_frac: float
    verdict: str  # ok | coverage_collapse | not_computable | not_measured
    detail: str; coverage_ratio: float | None = None; vad_backend: str = ""
```
That report reaches `report.json` under the key `coverage` — **aggregates and a verdict only, no regions, no score vector** **[code]**. `MIN_VAD_FRACTION = 0.10`, `COLLAPSE_RATIO = 0.25` inside the measured empty interval [0.058, 0.925] **[code]**.

**Current consumers:** `selekyt/pipeline.run` → `coverage.check` (the gate; skippable with `skip_coverage_check`), and `pipeline_audio.run_pipeline_audio(...)-> dict` which enriches `pacing["vad"]` — but only when `audio_analysis=True`.

**Reaches disk: conditionally, and off by default.** `save_scores` writes `project/audio/{fingerprint}.{backend}.vad_raw.json` and `audio_analysis.py` writes `vad_regions.json` (`{backend, frame_ms, duration, speech:[[start,end,score]], silence:[[start,end,duration]], config:{...}}`) — **only under `--audio-analysis`**, which defaults to `False` **[code]**. On a default run there is no on-disk trace of speech regions at all, even though the coverage gate just computed them.

**Adapter note:** the score vector is the expensive, reusable artifact and `save_scores` already serialises it; the adapter is a keying layer, not a recompute — store `VADResult` by (audio fingerprint, backend, `params_digest`) and re-derive regions on read via the pure `segment_from_scores` rather than re-running the backend. For the coverage path specifically, capture `_regions` before it is discarded — that is a two-line change at the named discard point, and it is the only place where speech regions are computed on a default run.

---

## 4. Pause / dead-air structure

**Module:** `engine/pause_classifier.py` (producer, rules-based, deterministic, no ML).
```python
class PauseType(str, Enum):  DEAD_AIR; BREATHING; HESITATION; INTENTIONAL; UNCERTAIN
@dataclass
class ClassifiedPause:  start: float; end: float; duration: float; pause_type: PauseType; confidence: float = 1.0
class PauseClassifier:
    def __init__(self, dead_air_min=1.5, breath_range=(0.15, 0.6), intention_range=(0.5, 2.0),
                 hesitation_range=(0.2, 0.9), punctuation_margin=0.15, region_boundary_margin=0.15)
    def classify(self, silences: list[SilenceRegion], speech: list[SpeechRegion], duration: float,
                 words: list[Word] | None = None) -> list[ClassifiedPause]
def classify_all_pauses(silences, speech, duration, words=None) -> list[ClassifiedPause]
```
**Units:** `start`/`end` are **source seconds** (they come straight from `SilenceRegion`). Confidence is a fixed per-type value, not a measurement: DEAD_AIR 0.95, BREATHING 0.85/0.55, INTENTIONAL 0.70, HESITATION 0.60, UNCERTAIN 0.40 **[code]**. Boundary detection is `_punctuation_boundaries(words)` on terminal punct `.?!।؟。` with fallback `_region_duration_boundaries(speech)` (regions > 1.5 s) **[code]** — so pause *typing* is partly text-driven, which matters for how confidently an adapter can ground it.

**Fusion (where the richer object lives):** `engine/audio_fusion.py`
```python
@dataclass class FusedWord:   # Whisper word enriched with VAD/pause context; original Word preserved
    word: Word; vad_speech: bool; leading_silence: float; trailing_silence: float
    pause_before: ClassifiedPause | None = None; pause_after: ClassifiedPause | None = None
@dataclass class FusedRegion: speech: SpeechRegion; words: list[FusedWord]; word_count: int; total_word_duration: float
@dataclass class AudioTruth:
    whisper_words: list[Word]; vad_speech: list[SpeechRegion]; vad_silence: list[SilenceRegion]
    pauses: list[ClassifiedPause]; fused_words: list[FusedWord]; fused_regions: list[FusedRegion]
    duration: float; vad_backend: str = ""; vad_score_file: str = ""
    speech_ratio: float = 0.0; silence_ratio: float = 0.0; dead_air_ratio: float = 0.0; hallucinated_count: int = 0
def fuse_audio_truth(whisper_words, vad_speech, vad_silence, pauses, duration,
                     vad_backend="", vad_score_file="") -> AudioTruth
```
`AudioTruth` is the single richest audio-perception object in the system — and it is **never serialised**. `AudioTruth` docstring: *"All original Whisper truth is preserved untouched... Derived representations are computed from these but never written back to the originals."* **[code]**

**Current consumer:** `engine/audio_analysis.run_audio_analysis(...)` orchestrates VAD → fusion → pause → jumpcut → export and returns only a summary dict.

**Reaches disk: conditionally, off by default.** Under `project/audio/` when `audio_analysis=True` **[code]**: `pause_analysis.json` (`[asdict(p) for p in pauses]` — **full** `ClassifiedPause` records), `vad_regions.json` (full region lists), `.vad_raw.json` (cached scores), `cutlist.json`, optional `cutlist.fcpxml`, and `audio_truth.json`. Note the trap: **`audio_truth.json` stores only ratios and COUNTS** — `fused_words`/`fused_regions`/`vad_speech`/`vad_silence`/`pauses` appear as integers, not objects **[code]**. An adapter that reads `audio_truth.json` and thinks it has the fused truth has been fooled; read `pause_analysis.json` + `vad_regions.json` instead.

**Adapter note:** capture at `fuse_audio_truth`'s return, not at `classify`'s — the `AudioTruth` object is one structure holding words, regions, pauses and their cross-references, and it exists already; emit `AUDIO` Evidence per `ClassifiedPause` and per `SpeechRegion`, and one `TEXT`/`AUDIO` combined claim per `FusedWord` carrying `vad_speech=False` (the hallucination signal). Set `confidence` from the fixed per-type table and `grounded=True` — these are observed, not inferred.

---

## 5. Story structure

**Module:** `engine/corpus.py` — `build_corpus(segments, *, duration, language="unknown", source=None) -> EditorialCorpus`. `SCHEMA_VERSION = "1.1"`. Deterministic, pure, **no ML** **[code]**.

```python
@dataclass class CorpusUnit:
    index: int; start: float; end: float; segment_indices: list[int]; text: str
    sentence_count: int; word_count: int; coherence: float; boilerplate: bool
    timing_source: str           # "word_aligned" | "character_proportional"
    restatement_count: int = 0; topic_id: int = -1; thread_id: int | None = None
    beat_id: int | None = None; redundancy_group_id: int = -1; speaker_block_id: int = -1
@dataclass class BuildInfo:
    schema: str = SCHEMA_VERSION; fingerprint: str = ""; unit_count: int = 0
    segment_count: int = 0; duration_source: str = ""; note: str = ""
@dataclass class EditorialCorpus:
    source: str | None; language: str; detected_languages: list[str]; duration: float; build: BuildInfo
    has_word_timestamps: bool; has_speaker_labels: bool
    units: list[CorpusUnit]; topics: list[Topic]; threads: list[StoryThread]; beats: list[DramaticBeat]
    links: list[SetupPayoffLink]; redundancy_groups: list[RedundancyGroup]
    speaker_blocks: list[SpeakerBlock]; broll: list[BrollMarker]; rhythm: RhythmProfile
    arc_summary: str
    unit_threads: list[list[int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    def to_dict(self) -> dict   # asdict(self)
```

Sub-shapes (all times **source seconds**, so they join Signals 3/4 without conversion):
- `Topic` (`topics.py`): `topic_id, label, terms, unit_indices, segment_indices, start, end, runs, span_share, confidence, reason, evidence`; property `recurring`.
- `StoryThread` / `DramaticBeat` / `SetupPayoffLink` / `BrollMarker` (`threads.py`): field lists read in full — `StoryThread(thread_id, title, terms, unit_indices, links, beat_indices, start, end, arc, span_share, confidence, reason, evidence)`; `DramaticBeat(unit_index, start, end, kind, polarity, intensity, percentile, is_thread_peak, is_corpus_peak, cues, thread_id, confidence, reason)`; `SetupPayoffLink(setup_unit, payoff_unit, kind, strength, setup_start, setup_end, payoff_start, payoff_end, confidence, reason, evidence)`; `BrollMarker(start, end, kind, cue, unit_index, confidence, reason)`.
- `RedundancyGroup` (`redundancy.py`): `group_id, members, representative, relation, similarity, start, end, confidence, reason, evidence`.
- `RhythmProfile` / `UnitRhythm` / `SpeakerBlock` (`rhythm.py`): `RhythmProfile(available, total_words, speech_seconds, speech_rate_wpm, median_rate_wpm, pause_counts, pause_rate_per_minute, filler_count, filler_rate_per_minute, false_start_count, incomplete_count, per_unit, confidence, reason)`; `UnitRhythm(unit_index, start, end, word_count, speech_seconds, pause_seconds, speech_rate_wpm, rush, drag, pause_counts, longest_pause, emphatic_pause, pre_pause, filler_count, false_start_count, incomplete, fluency, confidence, reason)`; `SpeakerBlock(block_id, start, end, speaker, unit_indices, boundary_reason, confidence, reason)`.

**Availability contract [code]** (load-bearing for an adapter — it must not assume these exist): no word timestamps ⇒ `rhythm.available is False` and all timing-derived fields `None`; no speaker labels ⇒ **every** `SpeakerBlock.speaker` is `None` (blocks are *turn* hypotheses, not speaker identities); no visual/deictic cues ⇒ `broll` is empty.

**Current consumer / reaches disk:** `editorial_corpus.json` **is written** (`corpus_report.to_dict()`, i.e. full `asdict`) — the one signal that already persists in full. Reader: `engine/corpus_io.read_corpus(src) -> EditorialCorpus`, supported schemas `("1.0", "1.1")`, raises `CorpusReadError`. **But:** the write sits inside a `try/except Exception` that prints to stderr and continues, and `report["editorial_corpus"]` is set only if `corpus_path.is_file()` **[code]** — a failed write is invisible in `report.json`. `docs/product-gap-map.md` adds that `engine.corpus` "was originally a finished, tested analysis that nothing consumed" and now writes a file; **[inference]** no render-path consumer of it was found.

**Adapter note:** this signal needs no perception capture, only a translator — read via `corpus_io.read_corpus` (never via raw `json.load`, which skips the schema/finite/span validation `read_corpus` performs) and emit `EDITORIAL` Evidence per topic/thread/beat/link/redundancy group, with `arc_summary` as a rationale string and `BuildInfo.fingerprint` feeding `params_digest`. Link `BrollMarker`s to the `VISUAL` evidence from Signal 2 when their spans overlap — that is the join the pipeline never makes, and it is the one an editor would want first.

---

## 6. What `selekyt/pipeline.py` actually persists — the definitive list

`run(source, out_root, model="small", language=None, limit=6, cookies=None, chunk_minutes=5, beam_size=1, cpu_threads=None, ollama_url=None, audio_analysis=False, vad_backend=None, silence_threshold=0.5, silence_padding=0.0, min_speech_ms=150, min_silence_ms=100, keep_pauses=True, remove_filler=True, export_fcpxml=False, skip_coverage_check=False, skip_language_check=False) -> Path` **[code]**

**Written to the run directory:**
- `project/pipeline_state.json` — plus `.bak` backup and `.corrupt.N` quarantine (`write_json_atomic`)
- `transcript.json` — `{language, asr, segments:[{start, end, text, words}], container_recovery?}`; deliberately excluded from `REGENERABLE_DIRS`
- `selections.json`, `editorial_report.json`
- `editorial_corpus.json` — **inside `try/except Exception`** (see Signal 5)
- `pacing_report.json` — enriched with `pacing["vad"]` only when audio analysis runs
- per clip: `captions/NN.{ass,srt,json}`, `content_pack/NN.json`, `facebook/NN.mp4`, `shorts/NN.mp4`, `timeline/` (EDL + FCPXML)
- `edit_plan.json`, `content_pack.json`, QA report, `report.json`
- under `project/audio/` — `{fingerprint}.{backend}.vad_raw.json`, `vad_regions.json`, `pause_analysis.json`, `audio_truth.json`, `cutlist.json`, `cutlist.fcpxml` — **only when `audio_analysis=True`**

**`report.json` keys [code]:** `source, title, language, candidate_count, selected_count, rejected_by_context_guard, truncated_by_limit, bench, rejection_reasons, outputs, captions, timeline, content_pack, editorial_report, editorial_corpus, pacing_report, qa, policy, transcription, container_recovery, coverage` (`coverage.as_dict()`), `language_fitness, audio` (`audio_summary or {"enabled": False}`), `provenance, config`.

**`REGENERABLE_DIRS = ("source", "transcription")`** — `reclaim_intermediates` deletes only those; `transcript.json` is intentionally NOT regenerable **[code]**.

**The answer to the question this section exists for:** of the six signals, only Signal 5 (`editorial_corpus.json`) and the aggregate-only `coverage` entry in `report.json` reach disk. Signals 1 and 2 reach disk **never**; Signals 3 and 4 reach disk **only behind a flag that defaults off**. The vertical render path — `render_clip(..., vertical=True)` at `engine/render.py:320` → `vertical_reframe_filter` → `scene_filter` → `analyse_scene` + `_perceive_window` — is the largest single perception expenditure in the pipeline and persists nothing but pixels.

---

## What is NOT currently perceived at all

Stated plainly, per the constraint not to invent capabilities. Each entry is something an editor would plausibly ask memory for, that no existing module computes.

1. **Speaker identity / diarisation.** No diarisation module exists. `SpeakerBlock.speaker` is `None` whenever the transcript has no labels **[code]**, and `_Track` identity "is positional only" **[code]**. The system cannot say *who* is speaking, nor that the face in shot A is the speaker in shot B.
2. **OCR / on-screen text.** `tools/caption_ocr.py` performs OCR, but it is an offline WER-measurement tool: `rapidocr-onnxruntime` is documented there as *"a measurement-only dependency; it is not used by the pipeline"* **[code]**. No pipeline run reads on-screen text. (`engine/memory/evidence.py`'s `Modality.TEXT` comment lists "OCR" as an example of the channel — that is vocabulary, not a capability **[code]**.)
3. **Music vs speech vs sound effects.** No backend distinguishes speech from score. `engine/coverage.py`'s own docstring states this as a known blind spot: a source that is mostly score with little dialogue would be falsely flagged **[code]**.
4. **Acoustic emotion, sentiment or emphasis.** Nothing measures pitch, intensity contour or affect. `RhythmProfile` captures rate, fillers, false starts and pause counts **[code]** — all lexical or durational, none affective.
5. **General object / scene semantics.** No object detector. Ultralytics YOLO was evaluated and *"rejected as a dependency"* (AGPL-3.0, and it would pull PyTorch) **[code]**. Beyond faces and static bright cells, the pipeline has no visual taxonomy.
6. **Gesture, gaze or expression.** Not computed by any module. YuNet returns boxes only.
7. **Camera motion (pan/tilt/zoom) magnitude.** `find_shot_boundaries` classifies a sample-to-sample difference as cut vs pan (isolated crossing vs run) **[code]**, but produces no motion parameters, no direction, no speed — only a binary label per sample index.
8. **B-roll from the image.** `BrollMarker` is licensed strictly by *"the cue is quoted verbatim from the source"* **[code]** — transcript words only. The pipeline never looks at the pixels to find cutaways.
9. **Slide content / reading order.** `SlideRect` is a bounding rectangle plus cell statistics. No module reads what is on the slide.
10. **Reframe quality after the render.** Nothing verifies the shipped crop actually kept the speaker; `crop_x_expression` and the keypoints are never checked against the output.
11. **Ground truth of any kind.** `docs/product-gap-map.md` states no human has judged any corpus clip and the audit has no ground truth. There is no WER against a reference transcript in the pipeline (only the offline OCR tool), and no editorial correctness oracle.
12. **Any true language understanding in the corpus build.** `build_corpus` is explicitly ML-free and deterministic **[code]**. The optional LLM path is `engine/llm_editorial.attach_metadata(c, ollama_url, model="llama3.2")`, which is a **no-op when `ollama_url` is None** **[code]** — and this host has no ollama server, so in this environment `Candidate.llm_metadata` is always `{}`. Story structure is lexical and positional: cue-phrase matching, containment similarity and positional clustering. It is a strong, auditable heuristic, not comprehension.

**[inference]** The strategically cheapest captures, ranked by perception-per-line-changed: (a) `_perceive_window` in `engine/reframe.py` — one interception yields faces, shot boundaries and slide geometry for every consumer in the pipeline; (b) the `coverage.measure` discard point — two lines, and it is the only place speech regions are computed on a *default* run; (c) `fuse_audio_truth`'s return — already-assembled `AudioTruth`, no recompute. Signals 1, 2, 3 and 4 together need no new decoder, model or re-encode: every number an editor would ask for is in memory at the moment the pipeline throws it away.
