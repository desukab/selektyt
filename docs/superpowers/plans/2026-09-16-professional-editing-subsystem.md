# Phase 2 Extension — Professional Editing Subsystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate AutoCut-class mechanical editing into SelekyT: VAD, cut algebra, jump-cut engine, FCPXML export, multi-track model, B2B reports, professional CLI.

**Architecture:** Layered pipeline with pluggable VAD backends (Silero→FFmpeg→Whisper), standalone cut algebra module, concat-demuxer rendering, score-cached analysis.

**Tech Stack:** Python 3.14, numpy, ffmpeg, whisper.cpp

**Spec:** `docs/superpowers/specs/2026-09-16-professional-editing-subsystem-design.md`

## Global Constraints

- Platform: Termux/Android ARM64, Python 3.14.6, 5 cores, 11GB RAM
- Dependencies: numpy 2.4.4 only (no scipy/onnxruntime/silero)
- No AutoCut source code copied — original implementations only
- No fabricated measurements — proxy validation labeled as proxy
- Every coherent change committed and pushed to origin/main
- Tests must pass before every commit

## Non-Negotiable Design Rules

1. **VAD is pluggable** — Silero→FFmpeg→Whisper fallback chain. Never a hard dependency. Actual backend recorded in report.json.
2. **Audio fusion must NOT modify Whisper truth** — `audio_fusion.py` creates a *derived* layer. Original Whisper words, VAD regions, fused regions, final edit boundaries are all kept as separate representations for B2B auditability.
3. **Cut algebra is single source of truth** — `engine/cut_algebra.py` is used by jumpcut, FCPXML, renderer, multi-track, QA. No separate interval logic anywhere. Every final edit = deterministic source-time ranges.
4. **Report.json includes provenance + configuration** — source fingerprint, audio fingerprint, Whisper model/version, VAD backend/version, all configs, software version/git SHA, timestamps, cache status.

---

### Task 1: Cut Algebra Module

**Files:**
- Create: `engine/cut_algebra.py`
- Create: `tests/test_cut_algebra.py`

**Interfaces:**
- Consumes: `list[tuple[float,float]]` intervals
- Produces: interval operations returning `list[tuple[float,float]]`

- [ ] **Step 1: Write comprehensive cut algebra tests**

Create `tests/test_cut_algebra.py` with tests covering:
- `merge_intervals`: empty, single, overlapping, adjacent, nested, unsorted
- `subtract_intervals`: basic, no overlap, complete containment, partial, multiple
- `pad_intervals`: zero pad, positive, negative, clamped to bounds
- `trim_intervals`: positive trim, negative trim, clamp
- `split_intervals`: at point, at silence region, multiple splits
- `word_safe_filter`: filter intervals that don't align with word boundaries
- `is_adjacent`: adjacent intervals, gap present, overlapping
- `is_nested`: nested, non-nested, equal
- `source_to_output_time`: mapping with and without gaps
- `concat_kept_ranges`: basic concatenation, with gaps
- `enforce_duration`: min/max duration enforcement
- Pathological: zero-length, negative-length, inverted, out-of-bounds

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_cut_algebra.py -v
```

- [ ] **Step 3: Implement cut_algebra.py**

Create `engine/cut_algebra.py` with all functions. Key functions:

```python
def merge_intervals(intervals: list[tuple[float,float]], gap: float = 0.0) -> list[tuple[float,float]]
def subtract_intervals(base: list[tuple[float,float]], remove: list[tuple[float,float]]) -> list[tuple[float,float]]
def pad_intervals(intervals: list[tuple[float,float]], pad: float, bounds: tuple[float,float]|None = None) -> list[tuple[float,float]]
def trim_intervals(intervals: list[tuple[float,float]], trim: float) -> list[tuple[float,float]]
def split_intervals(intervals: list[tuple[float,float]], points: list[float]) -> list[tuple[float,float]]
def word_safe_filter(intervals: list[tuple[float,float]], words: list, margin: float = 0.02) -> list[tuple[float,float]]
def is_adjacent(a: tuple[float,float], b: tuple[float,float], tolerance: float = 0.001) -> bool
def is_nested(inner: tuple[float,float], outer: tuple[float,float]) -> bool
def source_to_output_time(source_t: float, kept: list[tuple[float,float]]) -> float
def concat_kept_ranges(intervals: list[tuple[float,float]]) -> float
def enforce_duration(intervals: list[tuple[float,float]], min_dur: float = 0.0, max_dur: float = 60.0) -> list[tuple[float,float]]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_cut_algebra.py -v
```

- [ ] **Step 5: Commit**

```bash
git add engine/cut_algebra.py tests/test_cut_algebra.py
git commit -m "feat: deterministic cut algebra module — merge, subtract, pad, split, word-safe boundaries, source→output mapping"
git push origin main
```

---

### Task 2: Audio Extraction Provider

**Files:**
- Create: `providers/audio.py`
- Create: `tests/test_audio.py`

**Interfaces:**
- Consumes: `Path` to media file
- Produces: streaming 16kHz mono PCM (int16), or cached file

- [ ] **Step 1: Write audio extraction tests**

Test ffmpeg PCM extraction:
- extracts 16kHz mono PCM
- handles missing file gracefully
- fingerprint generation for caching
- streaming large files

- [ ] **Step 2: Implement providers/audio.py**

```python
def extract_pcm(media: Path, sample_rate: int = 16000, channels: int = 1) -> Iterator[bytes]
def pcm_to_file(media: Path, out: Path, sr: int = 16000) -> Path
def source_fingerprint(media: Path, source: str, model: str, language: str) -> str
```

- [ ] **Step 3: Run tests, commit**

---

### Task 3: VAD Backend Abstraction + Energy VAD

**Files:**
- Create: `engine/vad.py`
- Create: `tests/test_vad.py`

**Interfaces:**
- Protocol: `SpeechAnalyzer.analyze(samples, sample_rate) -> VADResult`
- `VADResult`: `{scores: list[float], sample_rate: int, backend: str}`
- `segment_from_scores(scores, params) -> list[SpeechRegion]`

- [ ] **Step 1: Write VAD tests**
- speech detection (known speech WAV)
- silence detection (silence WAV)
- hysteresis behavior
- threshold changes produce different regions
- padding extends speech regions
- min_silence drops short gaps
- min_speech drops short regions
- score caching (scores → segmentation is pure function)
- FFmpegBackend when Silero unavailable

- [ ] **Step 2: Implement engine/vad.py**
- `SpeechAnalyzer` protocol
- `EnergyVAD`: numpy RMS-based + zero-crossing rate
- `FFmpegVADBackend`: ffmpeg silencedetect filter
- `WhisperTimestampBackend`: uses existing transcript word timestamps
- `get_vad_backend()` factory
- `segment_from_scores(scores, threshold, hysteresis, min_silence, min_speech, padding, sample_rate) -> list[SpeechRegion]`
- Score persistence: save/load `vad_raw.json`

- [ ] **Step 3: Run tests, commit**

---

### Task 4: Pause Classification

**Files:**
- Create: `engine/pause_classifier.py`
- Create: `tests/test_pause_classifier.py`

**Interfaces:**
- Consumes: `SpeechRegion` + context (sentence boundaries, word types)
- Produces: `PauseClassification` enum per silence region

- [ ] **Step 1: Write tests**
- breathing at sentence boundary
- hesitation mid-sentence
- dead_air long silence
- intentional before key word
- uncertain for ambiguous cases

- [ ] **Step 2: Implement classification rules**

- [ ] **Step 3: Run tests, commit**

---

### Task 5: Audio/Truth Fusion

**Files:**
- Create: `engine/audio_fusion.py`
- Create: `tests/test_audio_fusion.py`

**Interfaces:**
- Consumes: whisper words, VAD regions, pause classifications
- Produces: fused segments with improved boundaries

- [ ] **Step 1: Write tests**
- word-aligned boundaries
- hallucination detection (whisper word outside VAD speech region)
- speech continuity preservation
- dead-air removal from word list

- [ ] **Step 2: Implement fusion logic**

- [ ] **Step 3: Run tests, commit**

---

### Task 6: Jump-Cut Engine

**Files:**
- Create: `engine/jumpcut.py`
- Create: `tests/test_jumpcut.py`

**Interfaces:**
- Consumes: fused segments, editorial policy config
- Produces: `list[tuple[float,float]]` keep intervals + edit metadata

- [ ] **Step 1: Write tests**
- dead-air removal preserves words
- intentional pause preservation
- filler removal with editorial policy
- word-safe boundaries
- sentence integrity
- configurable padding
- flash frame avoidance
- concat-based output planning

- [ ] **Step 2: Implement jumpcut engine**

- [ ] **Step 3: Run tests, commit**

---

### Task 7: FCPXML Export

**Files:**
- Create: `engine/fcpxml.py`
- Create: `tests/test_fcpxml.py`

**Interfaces:**
- Consumes: cut intervals, source metadata, timecode info
- Produces: valid FCPXML 1.11 string/file

- [ ] **Step 1: Write tests**
- valid XML structure
- source timecode mapping
- DaVinci/Premiere compatible attributes
- multi-track lanes
- rational time rendering
- empty timeline rejection

- [ ] **Step 2: Implement FCPXML emitter**

- [ ] **Step 3: Run tests, commit**

---

### Task 8: Multi-track Model

**Files:**
- Create: `engine/multitrack.py`
- Create: `tests/test_multitrack.py`

**Interfaces:**
- `TimelineTrack(name, lane, timecode_offset, intervals)`
- `MultiTrackProject(tracks, source_duration)`

- [ ] **Step 1: Write tests**
- single track alignment
- multi-track timecode alignment
- offset alignment
- missing/short track handling

- [ ] **Step 2: Implement multi-track model**

- [ ] **Step 3: Run tests, commit**

---

### Task 9: Report System

**Files:**
- Create: `engine/report.py`
- Create: `tests/test_report.py`

**Interfaces:**
- Consumes: all pipeline metadata
- Produces: `report.json` + terminal summary

- [ ] **Step 1: Write tests**
- report contains all required sections
- terminal summary formatting
- empty pipeline handling

- [ ] **Step 2: Implement report generation**

- [ ] **Step 3: Run tests, commit**

---

### Task 10: FFmpeg Error Handling

**Files:**
- Create: `providers/ffmpeg.py`
- Create: `tests/test_ffmpeg_error.py`

**Interfaces:**
- Wraps all ffmpeg calls with error capture

- [ ] **Step 1: Write tests**
- captures stderr tail on failure
- identifies failing stage
- shows source/target paths
- actionable error message

- [ ] **Step 2: Implement error handler**

- [ ] **Step 3: Run tests, commit**

---

### Task 11: Editorial Integration

**Files:**
- Modify: `engine/editorial.py`
- Modify: `engine/edit_plan.py`
- Create: `tests/test_editorial_audio.py`

**Interfaces:**
- Enhances `_score()` with audio-derived features
- Enhances `pacing_report()` with VAD data

- [ ] **Step 1: Write tests**
- silence_ratio in scoring
- filler_ratio in scoring
- pause_quality in scoring
- VAD-enhanced pacing report

- [ ] **Step 2: Implement enhancements**

- [ ] **Step 3: Run tests, commit**

---

### Task 12: CLI + Pipeline Wiring

**Files:**
- Modify: `selekyt/cli.py`
- Modify: `selekyt/pipeline.py`

- [ ] **Step 1: Add new CLI flags**

```
--silence-threshold, --silence-padding, --min-silence, --min-speech
--keep-pauses, --remove-filler, --export-fcpxml
--audio-analysis
```

- [ ] **Step 2: Wire VAD + jumpcut + FCPXML into pipeline**

- [ ] **Step 3: Verify all existing tests pass**

- [ ] **Step 4: Commit**

---

### Task 13: Proxy Validation Measurements

**Files:**
- Create: `tests/test_real_source.py` (or direct measurement script)

**Measurements (Es69N24I2Yo.mp4 proxy, clearly labeled):**
- VAD runtime
- Whisper runtime  
- Editorial analysis runtime
- Number of speech regions
- Total silence / silence percentage
- Number of candidate clips
- Selected clips
- Average selected duration
- Average cut count
- Total dead air removed
- Final output duration
- Render time
- QA results

- [ ] **Step 1: Run full pipeline against Es69N24I2Yo.mp4**

- [ ] **Step 2: Record all measurements**

- [ ] **Step 3: Commit with clear "proxy validation" label**

---

## Execution Order

Tasks 1-2 are independent foundations. Tasks 3-5 build on each other. Tasks 6-10 are mostly independent. Tasks 11-13 wire everything together.

Critical path: Cut Algebra → VAD → Pause Classification → Fusion → Jump-Cut → Pipeline Wiring → Proxy Validation
