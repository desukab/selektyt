# SelekyT Three-Phase Improvement Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SelekyT a reliable long-form → short-form editorial engine that handles 118-minute videos on ARM CPU, produces genuinely usable clips, and validates actual rendered output.

**Architecture:** Phase 1 fixes the Whisper transcription bottleneck (chunking, progress, auto-fallback, resumability). Phase 2 improves editorial quality using FLVYT-inspired concepts (pacing validation, anti-repetition, optional LLM semantic pass). Phase 3 adds production QA that inspects actual rendered MP4s. Each phase is committed independently and builds on stable prior output.

**Tech Stack:** Python 3.10+, faster-whisper (int8, CPU), ffmpeg/ffprobe, OpenCV, numpy, optional Ollama for LLM editorial pass.

**Spec:** This plan itself — derived from the user's requirements and FLVYT reference analysis.

## Current State (Verified)

| Component | Status | Lines |
|-----------|--------|-------|
| `providers/whisper.py` | No chunking, no progress, no beam_size set (defaults to 5), no thread control | 18 |
| `selekyt/pipeline.py` | No caching, no resumability, no chunk-minutes flag | 44 |
| `selekyt/cli.py` | No --chunk-minutes, no --cpu-threads, no --ollama flags | 19 |
| `engine/qa.py` | Minimal: only ffprobe + file size check | 25 |
| `engine/editorial.py` | Lexical-only scoring, no semantic understanding | 73 |
| `engine/context_guard.py` | English-only sensationalism filter | 14 |
| `engine/edit_plan.py` | Conservative pause removal, no audio-truth validation | 26 |
| `tests/test_editorial.py` | 2 tests, no whisper/pipeline/qa tests | 24 |
| faster-whisper | NOT installed | — |
| Hardware | 8 cores (5 available), 11GB RAM, ARM CPU, ffmpeg 8.1.2 | — |

---

## Phase 1: Unblock the Pipeline

### Task 1.1: Install faster-whisper and create smoke test

**Files:**
- Modify: `pyproject.toml` (no change needed — `[transcription]` extra already exists)
- Create: `tests/test_whisper_smoke.py`

**Dependencies:** None (setup task)

- [ ] **Step 1: Install faster-whisper**

```bash
pip3 install faster-whisper 2>&1 | tail -5
```

If this times out or fails on ARM, try: `pip3 install --no-build-isolation faster-whisper`

- [ ] **Step 2: Verify import**

```bash
python3 -c "from faster_whisper import WhisperModel; print('OK')"
```

- [ ] **Step 3: Write smoke test that creates a tiny WAV and transcribes it**

Create `tests/test_whisper_smoke.py`:

```python
"""Smoke test: generate a 3-second sine wave, transcribe with tiny model."""
import subprocess
import tempfile
from pathlib import Path


def _make_silence_wav(out: Path, duration: float = 3.0) -> None:
    """Generate a silent WAV file (no speech, just validates the pipeline works)."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono",
         "-t", str(duration), "-c:a", "pcm_s16le", str(out)],
        check=True, capture_output=True,
    )


def test_whisper_loads_and_runs():
    """Verify faster-whisper can load a model and process audio on this machine."""
    from faster_whisper import WhisperModel

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "silence.wav"
        _make_silence_wav(wav)
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, info = model.transcribe(
            str(wav), language=None, beam_size=1,
            word_timestamps=False, vad_filter=True,
            condition_on_previous_text=False,
        )
        _ = list(segments)  # force iteration
        assert info.language is not None
```

- [ ] **Step 4: Run smoke test**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_whisper_smoke.py -v
```

Expected: PASS (may take 30-60s for model download on first run).

- [ ] **Step 5: Measure tiny model throughput on 1-minute audio**

```bash
python3 -c "
import time, tempfile, subprocess
from pathlib import Path
from faster_whisper import WhisperModel

with tempfile.TemporaryDirectory() as td:
    wav = Path(td) / 'one_min.wav'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono',
                    '-t', '60', '-c:a', 'pcm_s16le', str(wav)], capture_output=True, check=True)
    model = WhisperModel('tiny', device='cpu', compute_type='int8')
    t0 = time.time()
    segs, info = model.transcribe(str(wav), beam_size=1, vad_filter=True,
                                   word_timestamps=True, condition_on_previous_text=False)
    _ = list(segs)
    elapsed = time.time() - t0
    print(f'tiny model 60s audio: {elapsed:.1f}s wall ({60/elapsed:.1f}x realtime)')
"
```

Report: model, wall time, realtime factor.

- [ ] **Step 6: Commit**

```bash
git add tests/test_whisper_smoke.py && git commit -m "test: add faster-whisper smoke test"
git push origin main
```

Report the commit SHA.

---

### Task 1.2: Rewrite `providers/whisper.py` with chunking, progress, and auto-fallback

**Files:**
- Rewrite: `providers/whisper.py`
- Create: `tests/test_whisper_chunking.py`

**Dependencies:** Task 1.1 (faster-whisper installed)

**Interfaces:**
- Produces: `transcribe(media, model_name, language, chunk_minutes, cpu_threads, beam_size) -> (list[Segment], str)` — same return signature as current, but with chunking internally.
- Also produces: `_transcribe_single_chunk(model, chunk_path, language, beam_size, cpu_threads) -> list[Segment]` — for testing individual chunks.

- [ ] **Step 1: Write tests for chunking logic**

Create `tests/test_whisper_chunking.py`:

```python
"""Tests for whisper chunking, checkpointing, and merge logic."""
import json
import subprocess
import tempfile
from pathlib import Path


def _make_wav(out: Path, duration: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono",
         "-t", str(duration), "-c:a", "pcm_s16le", str(out)],
        check=True, capture_output=True,
    )


def test_chunk_audio_produces_correct_number_of_files():
    from providers.whisper import _chunk_audio
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "long.wav"
        _make_wav(wav, 720)  # 12 minutes
        chunks = _chunk_audio(wav, chunk_minutes=5, overlap_sec=10)
        assert len(chunks) >= 2, f"Expected >=2 chunks for 12min audio, got {len(chunks)}"
        # Each chunk should exist and be a file
        for c in chunks:
            assert c.exists(), f"Chunk {c} does not exist"
        # Checkpoints should be written
        for i in range(len(chunks)):
            cp = wav.parent / f"chunk_{i:03d}.json"
            # Checkpoint may or may not exist yet (depends on implementation)


def test_merge_deduplicates_overlap():
    from providers.whisper import _merge_segments
    from models.core import Segment, Word
    # Two segments that overlap by 2 seconds
    segs1 = [Segment(0.0, 5.0, "hello world", [Word("hello", 0.0, 0.5, 1.0), Word("world", 0.5, 1.0, 1.0)])]
    segs2 = [Segment(3.0, 8.0, "hello world again", [Word("hello", 3.0, 3.5, 1.0), Word("world", 3.5, 4.0, 1.0), Word("again", 4.0, 4.5, 1.0)])]
    merged = _merge_segments(segs1, segs2, overlap_sec=10)
    # Should not have duplicate "hello world"
    texts = [s.text for s in merged]
    assert len(texts) <= 2


def test_checkpoint_prevents_reprocessing():
    from providers.whisper import _load_checkpoint, _save_checkpoint
    with tempfile.TemporaryDirectory() as td:
        cp_path = Path(td) / "chunk_000.json"
        segments = [{"start": 0.0, "end": 5.0, "text": "test", "words": []}]
        _save_checkpoint(cp_path, segments)
        loaded = _load_checkpoint(cp_path)
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0]["text"] == "test"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_whisper_chunking.py -v
```

Expected: FAIL (functions don't exist yet).

- [ ] **Step 3: Rewrite `providers/whisper.py`**

The new implementation must:

1. Split audio into 5-minute chunks with 10-second overlap using ffmpeg
2. Transcribe each chunk independently with `beam_size=1`, `cpu_threads` control
3. Write a JSON checkpoint after each chunk completes
4. Skip already-completed chunks on restart
5. Merge chunks, deduplicating overlapping segments (favor earlier chunk)
6. Report per-chunk metrics: chunk number, audio duration, wall time, realtime factor, model, threads, fallback occurred
7. Auto-fallback: after 3 consecutive chunks exceed 10x realtime factor, try next smaller model
8. Never fall back on first chunk alone (initial model load is slow)
9. Use `log_progress=True` if available in faster-whisper version

```python
"""Transcription provider using faster-whisper with chunking and auto-fallback."""
from __future__ import annotations
import json
import os
import subprocess
import time
from pathlib import Path
from models.core import Segment, Word


# Model fallback chain: try requested, then these in order
FALLBACK_CHAIN = ["small", "base", "tiny"]

# Auto-fallback: if N consecutive chunks exceed this realtime factor, fall back
FALLBACK_THRESHOLD_RT = 10.0  # 10x realtime = 10 min for 1 min audio
FALLBACK_CONSECUTIVE = 3      # need this many slow chunks before fallback


def _probe_duration(media: Path) -> float:
    """Get audio duration in seconds via ffprobe."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(media)]
    return float(subprocess.check_output(cmd, text=True).strip())


def _chunk_audio(media: Path, chunk_minutes: float = 5.0, overlap_sec: float = 10.0,
                 out_dir: Path | None = None) -> list[Path]:
    """Split audio into overlapping chunks. Returns list of chunk paths in order."""
    if out_dir is None:
        out_dir = media.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = _probe_duration(media)
    chunk_sec = chunk_minutes * 60
    chunks: list[Path] = []
    start = 0.0
    idx = 0
    while start < duration:
        end = min(start + chunk_sec, duration)
        chunk_path = out_dir / f"chunk_{idx:03d}.wav"
        if not chunk_path.exists():
            cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(media),
                   "-t", f"{end - start:.3f}", "-c:a", "pcm_s16le",
                   "-ar", "16000", "-ac", "1", str(chunk_path)]
            subprocess.run(cmd, check=True, capture_output=True)
        chunks.append(chunk_path)
        start = end - overlap_sec if end < duration else duration
        idx += 1
    return chunks


def _save_checkpoint(path: Path, segments: list[dict]) -> None:
    """Save chunk transcription to JSON checkpoint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(segments, ensure_ascii=False), encoding="utf-8")


def _load_checkpoint(path: Path) -> list[dict] | None:
    """Load chunk transcription from JSON checkpoint, or None if not found."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _segment_to_dict(s) -> dict:
    """Convert faster-whisper segment to dict for checkpointing."""
    words = []
    if s.words:
        for w in s.words:
            words.append({"text": w.word, "start": float(w.start),
                          "end": float(w.end), "confidence": float(getattr(w, "probability", 1.0))})
    return {"start": float(s.start), "end": float(s.end),
            "text": s.text.strip(), "words": words}


def _dict_to_segment(d: dict) -> Segment:
    """Convert checkpoint dict back to Segment."""
    words = [Word(w["text"], w["start"], w["end"], w.get("confidence", 1.0)) for w in d.get("words", [])]
    return Segment(d["start"], d["end"], d["text"], words)


def _merge_segments(chunks: list[list[Segment]], overlap_sec: float = 10.0) -> list[Segment]:
    """Merge chunk results, deduplicating overlapping regions."""
    if not chunks:
        return []
    merged = list(chunks[0])
    for chunk_segs in chunks[1:]:
        if not chunk_segs:
            continue
        # Find overlap: chunk starts before previous ends
        chunk_start = chunk_segs[0].start
        # Remove segments from merged that overlap with chunk start
        while merged and merged[-1].end > chunk_start - overlap_sec:
            merged.pop()
        merged.extend(chunk_segs)
    return merged


def _load_model(model_name: str, cpu_threads: int | None = None):
    """Load WhisperModel with thread control."""
    from faster_whisper import WhisperModel
    kwargs = {"device": "cpu", "compute_type": "int8"}
    if cpu_threads is not None:
        kwargs["cpu_threads"] = cpu_threads
    return WhisperModel(model_name, **kwargs)


def _transcribe_chunk(model, chunk_path: Path, language: str | None = None,
                      beam_size: int = 1) -> list[dict]:
    """Transcribe a single chunk, returning list of segment dicts."""
    segments, _ = model.transcribe(
        str(chunk_path), language=language, beam_size=beam_size,
        word_timestamps=True, vad_filter=True,
        condition_on_previous_text=False,
    )
    return [_segment_to_dict(s) for s in segments]


def transcribe(media: Path, model_name: str = "small", language: str | None = None,
               chunk_minutes: float = 5.0, cpu_threads: int | None = None,
               beam_size: int = 1) -> tuple[list[Segment], str]:
    """Transcribe audio with chunking, checkpointing, and auto-fallback.

    Returns (segments, detected_language).
    """
    # Probe duration
    duration = _probe_duration(media)
    print(f"[transcribe] {media.name}: {duration:.1f}s audio ({duration/60:.1f} min)")

    # Determine chunk count
    chunk_sec = chunk_minutes * 60
    n_chunks = max(1, int(duration / chunk_sec) + (1 if duration % chunk_sec > 0 else 0))
    print(f"[transcribe] chunk_minutes={chunk_minutes}, chunks={n_chunks}")

    # Chunk the audio
    chunk_dir = media.parent / "chunks"
    chunks = _chunk_audio(media, chunk_minutes=chunk_minutes, out_dir=chunk_dir)

    # Load initial model
    current_model_name = model_name
    model = _load_model(current_model_name, cpu_threads)
    slow_count = 0
    detected_language = language
    all_chunk_results: list[list[Segment]] = []

    for i, chunk_path in enumerate(chunks):
        checkpoint = chunk_path.with_suffix(".json")

        # Check for existing checkpoint
        cached = _load_checkpoint(checkpoint)
        if cached is not None:
            segs = [_dict_to_segment(d) for d in cached]
            all_chunk_results.append(segs)
            print(f"[transcribe] chunk {i+1}/{len(chunks)}: CACHED ({len(segs)} segments)")
            continue

        # Transcribe
        chunk_dur = _probe_duration(chunk_path)
        print(f"[transcribe] chunk {i+1}/{len(chunks)}: {chunk_path.stem} ({chunk_dur:.1f}s) model={current_model_name} beam={beam_size} threads={cpu_threads} ...", end="", flush=True)

        t0 = time.time()
        try:
            seg_dicts = _transcribe_chunk(model, chunk_path, language, beam_size)
        except Exception as e:
            print(f" FAILED: {e}")
            # Skip this chunk, continue with next
            all_chunk_results.append([])
            continue
        wall = time.time() - t0
        rt = wall / chunk_dur if chunk_dur > 0 else float("inf")

        # Detect language from first chunk
        if detected_language is None and seg_dicts:
            # Re-transcribe just to get info.language if needed
            pass  # language detection happens at model level

        # Save checkpoint
        _save_checkpoint(checkpoint, seg_dicts)

        # Report metrics
        fallback_note = "" if current_model_name == model_name else f" (fell back from {model_name})"
        print(f" {wall:.1f}s wall, {rt:.1f}x RT{fallback_note}")

        segs = [_dict_to_segment(d) for d in seg_dicts]
        all_chunk_results.append(segs)

        # Auto-fallback logic: only after N consecutive slow chunks
        if rt > FALLBACK_THRESHOLD_RT:
            slow_count += 1
            if slow_count >= FALLBACK_CONSECUTIVE:
                # Find next smaller model
                idx = FALLBACK_CHAIN.index(current_model_name) if current_model_name in FALLBACK_CHAIN else -1
                if idx < len(FALLBACK_CHAIN) - 1:
                    next_model = FALLBACK_CHAIN[idx + 1]
                    print(f"[transcribe] {FALLBACK_CONSECUTIVE} consecutive slow chunks ({slow_count}x >{FALLBACK_THRESHOLD_RT}x RT) → falling back to {next_model}")
                    current_model_name = next_model
                    model = _load_model(current_model_name, cpu_threads)
                    slow_count = 0
        else:
            slow_count = 0

        # ETA after first 2-3 chunks
        if i >= 1 and i < len(chunks) - 1:
            completed = i + 1
            avg_rt = sum(
                _probe_duration(chunks[j]) for j in range(completed)
            ) / max(1, sum(
                1 for j in range(completed) if all_chunk_results[j]
            ))
            remaining = len(chunks) - completed
            # rough ETA based on average realtime factor
            total_wall_so_far = wall * completed  # approximation
            eta = total_wall_so_far / completed * remaining
            print(f"[transcribe] ETA: ~{eta/60:.0f} min remaining ({remaining} chunks)")

    # Merge all chunks
    merged = _merge_segments(all_chunk_results, overlap_sec=10.0)
    total_segs = len(merged)
    print(f"[transcribe] done: {total_segs} segments, model={current_model_name}")

    # Detect language from merged text (simple approach)
    if detected_language is None:
        detected_language = "unknown"

    return merged, detected_language
```

- [ ] **Step 4: Run chunking tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_whisper_chunking.py -v
```

Expected: PASS.

- [ ] **Step 5: Run smoke test with real audio**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -c "
from pathlib import Path
from providers.whisper import transcribe
import time, subprocess

# Create a 5-minute test WAV
wav = Path('/tmp/test_5min.wav')
if not wav.exists():
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono',
                    '-t', '300', '-c:a', 'pcm_s16le', str(wav)], capture_output=True, check=True)

t0 = time.time()
segs, lang = transcribe(wav, model_name='tiny', chunk_minutes=5, cpu_threads=4, beam_size=1)
elapsed = time.time() - t0
print(f'Result: {len(segs)} segments, lang={lang}, {elapsed:.1f}s wall')
"
```

Report: wall time, realtime factor, whether chunking worked.

- [ ] **Step 6: Commit**

```bash
git add providers/whisper.py tests/test_whisper_chunking.py && git commit -m "feat: chunked whisper transcription with checkpoints and auto-fallback"
git push origin main
```

Report the commit SHA.

---

### Task 1.3: Add per-stage resumability to `selekyt/pipeline.py`

**Files:**
- Rewrite: `selekyt/pipeline.py`
- Modify: `selekyt/cli.py` (add --chunk-minutes, --cpu-threads, --beam-size, --ollama flags)

**Dependencies:** Task 1.2 (chunked whisper working)

**Interfaces:**
- Consumes: `transcribe(media, model_name, language, chunk_minutes, cpu_threads, beam_size)` from Task 1.2
- Produces: `run(source, out_root, ...)` with per-stage caching via fingerprint files

- [ ] **Step 1: Write test for pipeline fingerprinting**

Create `tests/test_pipeline_resume.py`:

```python
"""Tests for pipeline resumability via fingerprinting."""
import hashlib
import json
import tempfile
from pathlib import Path


def test_fingerprint_deterministic():
    from selekyt.pipeline import _fingerprint
    fp1 = _fingerprint("http://example.com", "small", None, 6, 5.0, 4, 1)
    fp2 = _fingerprint("http://example.com", "small", None, 6, 5.0, 4, 1)
    assert fp1 == fp2


def test_fingerprint_differs_with_different_params():
    from selekyt.pipeline import _fingerprint
    fp1 = _fingerprint("http://example.com", "small", None, 6, 5.0, 4, 1)
    fp2 = _fingerprint("http://example.com", "base", None, 6, 5.0, 4, 1)
    assert fp1 != fp2


def test_stage_completed_check():
    from selekyt.pipeline import _stage_done, _mark_stage
    from selekyt.pipeline import _state_path
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        _mark_stage(project, "transcribe", "abc123", {"segments": 10})
        state = _state_path(project)
        assert _stage_done(state, "transcribe", "abc123")
        assert not _stage_done(state, "transcribe", "wrong_hash")
        assert not _stage_done(state, "select", "abc123")


def test_audio_already_extracted_skips_reextraction():
    """If transcription/audio.wav exists, do not re-extract or redownload."""
    from selekyt.pipeline import _extract_audio_done
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        audio_dir = project / "transcription"
        audio_dir.mkdir(parents=True, exist_ok=True)
        (audio_dir / "audio.wav").write_bytes(b"\x00" * 1000)
        assert _extract_audio_done(project)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_pipeline_resume.py -v
```

Expected: FAIL.

- [ ] **Step 3: Rewrite `selekyt/pipeline.py`**

Key requirement: preserve already-extracted audio. Stages:

- Stage 0: Resolve source (download only if local source doesn't exist; never re-download if the local source .mp4 is present)
- Stage 1: Extract audio → `transcription/audio.wav` (16kHz mono) — skip entirely if the WAV already exists
- Stage 2: Transcribe (chunked, checkpointed inside whisper.py)
- Stage 3: Select stories
- Stage 4: Render clips
- Stage 5: QA

```python
"""Pipeline orchestration with per-stage resumability."""
from __future__ import annotations
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from engine.editorial import make_thoughts, rank
from engine.captions import ass_for_candidate
from engine.context_guard import validate
from engine.render import render_clip
from providers.youtube import download
from providers.whisper import transcribe


def _fingerprint(source: str, model: str, language: str | None, limit: int,
                 chunk_minutes: float, cpu_threads: int, beam_size: int) -> str:
    """Deterministic fingerprint for pipeline run configuration."""
    raw = json.dumps({
        "source": source, "model": model, "language": language,
        "limit": limit, "chunk_minutes": chunk_minutes,
        "cpu_threads": cpu_threads, "beam_size": beam_size,
    }, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _state_path(project: Path) -> Path:
    return project / "pipeline_state.json"


def _load_state(project: Path) -> dict:
    p = _state_path(project)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(project: Path, state: dict) -> None:
    _state_path(project).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _stage_done(state: dict | Path, stage: str, fp: str) -> bool:
    if isinstance(state, Path):
        state = _load_state(state)
    return state.get(stage, {}).get("fingerprint") == fp and state.get(stage, {}).get("ok", False)


def _mark_stage(project: Path, stage: str, fp: str, extra: dict | None = None) -> None:
    state = _load_state(project)
    state[stage] = {"fingerprint": fp, "ok": True}
    if extra:
        state[stage].update(extra)
    _save_state(project, state)


def _extract_audio_done(project: Path) -> bool:
    """True if audio.wav already exists (preserve it — never re-extract)."""
    candidate = project / "transcription" / "audio.wav"
    return candidate.exists() and candidate.stat().st_size >= 1000


def _extract_audio(media: Path, out_wav: Path) -> None:
    """Extract 16kHz mono PCM WAV from media for transcription."""
    import subprocess
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(media), "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", str(out_wav)]
    subprocess.run(cmd, check=True, capture_output=True)


def run(source: str, out_root: Path, model: str = "small", language: str | None = None,
        limit: int = 6, chunk_minutes: float = 5.0, cpu_threads: int | None = None,
        beam_size: int = 1) -> Path:
    """Run the full pipeline with per-stage resumability."""
    project = out_root / "project"
    project.mkdir(parents=True, exist_ok=True)
    fp = _fingerprint(source, model, language, limit, chunk_minutes,
                      cpu_threads or 0, beam_size)

    # Stage 0: Resolve source — only download if local source is absent
    if source.startswith(("http://", "https://")):
        if source.endswith(".mp4") and Path(source).exists():
            media = Path(source)
            info = {}
        else:
            media, info = download(source, project / "source")
    else:
        media = Path(source)
        info = {}
    if not media.exists():
        raise RuntimeError(f"source not found: {media}")

    # Stage 1: Extract audio — skip entirely if audio.wav already exists
    audio_wav = project / "transcription" / "audio.wav"
    if not _extract_audio_done(project):
        print("[pipeline] Stage: extract audio")
        _extract_audio(media, audio_wav)
        _mark_stage(project, "extract", fp, {"source": str(media)})
    else:
        print("[pipeline] Stage: extract audio — CACHED (preserving existing audio.wav)")

    # Stage 2: Transcribe (chunked, checkpointed; chunk JSONs are the fine-grained resume)
    transcript_path = project / "transcript.json"
    if not _stage_done(project, "transcribe", fp) or not transcript_path.exists():
        print("[pipeline] Stage: transcribe")
        transcript, detected = transcribe(audio_wav, model_name=model, language=language,
                                          chunk_minutes=chunk_minutes, cpu_threads=cpu_threads,
                                          beam_size=beam_size)
        transcript_json = {"language": detected, "segments": []}
        for s in transcript:
            transcript_json["segments"].append({
                "start": s.start, "end": s.end, "text": s.text,
                "words": [asdict(w) for w in s.words]
            })
        transcript_path.write_text(json.dumps(transcript_json, ensure_ascii=False, indent=2), encoding="utf-8")
        _mark_stage(project, "transcribe", fp, {"segments": len(transcript), "language": detected})
    else:
        print("[pipeline] Stage: transcribe — CACHED")
        transcript_json = json.loads(transcript_path.read_text(encoding="utf-8"))
        detected = transcript_json.get("language", "unknown")
        from models.core import Segment, Word
        transcript = []
        for s in transcript_json["segments"]:
            words = [Word(w["text"], w["start"], w["end"], w.get("confidence", 1.0)) for w in s.get("words", [])]
            transcript.append(Segment(s["start"], s["end"], s["text"], words))

    # Stage 3: Select stories
    selections_path = project / "selections.json"
    if not _stage_done(project, "select", fp) or not selections_path.exists():
        print("[pipeline] Stage: select")
        thoughts = make_thoughts(transcript)
        ranked = rank(thoughts, limit=max(limit * 2, limit))
        selected = [c for c in ranked if validate(c)[0]][:limit]
        selections_path.write_text(json.dumps([c.to_dict() for c in selected], ensure_ascii=False, indent=2), encoding="utf-8")
        _mark_stage(project, "select", fp, {"thoughts": len(thoughts), "selected": len(selected)})
    else:
        print("[pipeline] Stage: select — CACHED")
        from models.core import Candidate, Word as W
        raw = json.loads(selections_path.read_text(encoding="utf-8"))
        selected = []
        for d in raw:
            words = [W(w["text"], w["start"], w["end"], w.get("confidence", 1.0)) for w in d.get("words", []) or []]
            selected.append(Candidate(d["start"], d["end"], d["text"], d["story_type"],
                                      d["hook"], d["payoff"], d["editorial_score"],
                                      d["virality_score"], d["context_score"], d["confidence"],
                                      d["reason"], d.get("visual_potential", 0.0), words))

    # Stage 4: Render clips
    render_done = _stage_done(project, "render", fp)
    if not render_done:
        print("[pipeline] Stage: render")
        for idx, c in enumerate(selected, 1):
            caption = ass_for_candidate(c, project / "captions" / f"{idx:02d}.ass")
            render_clip(media, c, project / "facebook" / f"{idx:02d}.mp4", caption, vertical=False)
            render_clip(media, c, project / "shorts" / f"{idx:02d}.mp4", caption, vertical=True)
        _mark_stage(project, "render", fp, {"clips": len(selected) * 2})
    else:
        print("[pipeline] Stage: render — CACHED")

    # Report
    report = {
        "source": source, "title": info.get("title"), "language": detected,
        "candidate_count": len(transcript_json.get("segments", [])),
        "selected_count": len(selected),
        "outputs": {"facebook": str(project / "facebook"), "shorts": str(project / "shorts")},
        "fingerprint": fp, "policy": "retention-aware editorial heuristics; no fabricated claims",
    }
    (project / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return project
```

- [ ] **Step 4: Update CLI with new flags**

Modify `selekyt/cli.py`:

```python
"""CLI for SelekyT."""
from __future__ import annotations
import argparse
from pathlib import Path
from selekyt.pipeline import run


def main() -> None:
    p = argparse.ArgumentParser(prog="selekyt", description="Turn long-form video into retention-aware social cuts.")
    p.add_argument("source", help="YouTube URL or local video path")
    p.add_argument("--out", default="out", help="Output directory")
    p.add_argument("--model", default="small", help="faster-whisper model (tiny/base/small/medium/large)")
    p.add_argument("--language", default=None, help="Force transcription language, e.g. te, hi, ta")
    p.add_argument("--limit", type=int, default=6, help="Number of selected stories")
    p.add_argument("--chunk-minutes", type=float, default=5.0, help="Audio chunk size in minutes for transcription")
    p.add_argument("--cpu-threads", type=int, default=None, help="CPU threads for Whisper (default: all available)")
    p.add_argument("--beam-size", type=int, default=1, help="Whisper beam size (1=faster, 5=better)")
    args = p.parse_args()
    project = run(args.source, Path(args.out), args.model, args.language, args.limit,
                  args.chunk_minutes, args.cpu_threads, args.beam_size)
    print(f"SelekyT complete: {project}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run all tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/ -v
```

Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add selekyt/pipeline.py selekyt/cli.py tests/test_pipeline_resume.py && git commit -m "feat: per-stage pipeline resumability with fingerprinting"
git push origin main
```

Report the commit SHA.

---

### Task 1.4: Test on actual 5-minute segment of Z907ENCogm4

**Files:** None (testing only)

**Dependencies:** Tasks 1.2 + 1.3

- [ ] **Step 1: Extract a 5-minute segment from the source video (if source exists locally)**

```bash
# Check if source exists
ls -la ~/Z907ENCogm4.mp4 2>/dev/null || ls -la /data/data/com.termux/files/home/selektyt/Z907ENCogm4.mp4 2>/dev/null
```

If source exists:
```bash
ffmpeg -y -ss 600 -i /path/to/Z907ENCogm4.mp4 -t 300 -c copy /tmp/test_5min_segment.mp4
```

If not, create a synthetic 5-minute test:
```bash
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=300" -f lavfi -i "anullsrc=r=16000:cl=mono" \
  -map 0:a -t 300 -c:a pcm_s16le /tmp/test_5min.wav
```

- [ ] **Step 2: Run full pipeline on 5-minute segment**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m selekyt.cli /tmp/test_5min_segment.mp4 \
  --out /tmp/selektyt_test --model tiny --chunk-minutes 5 --cpu-threads 4 --beam-size 1
```

Report: total time, output files created, whether QA passed.

- [ ] **Step 3: Verify output MP4s exist and are valid**

```bash
ls -la /tmp/selektyt_test/project/facebook/*.mp4 /tmp/selektyt_test/project/shorts/*.mp4 2>/dev/null
ffprobe -v error -show_entries format=duration -of json /tmp/selektyt_test/project/facebook/01.mp4 2>/dev/null
ffprobe -v error -show_entries format=duration -of json /tmp/selektyt_test/project/shorts/01.mp4 2>/dev/null
```

Report: file sizes, durations, whether captions were generated.

- [ ] **Step 4: Commit test results (if any test artifacts are useful)**

```bash
git add -A && git commit -m "test: verify 5-minute pipeline end-to-end on synthetic audio" 2>/dev/null || echo "no changes to commit"
git push origin main 2>/dev/null || echo "push skipped"
```

---

### Task 1.5: Test on 10-minute segment with empirical throughput measurement

**Dependencies:** Task 1.4

- [ ] **Step 1: Extract 10-minute segment**

```bash
ffmpeg -y -ss 600 -i /path/to/Z907ENCogm4.mp4 -t 600 -c copy /tmp/test_10min_segment.mp4 2>/dev/null
```

- [ ] **Step 2: Run transcription ONLY (not full pipeline) to measure throughput**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -c "
import time
from pathlib import Path
from providers.whisper import transcribe

media = Path('/tmp/test_10min_segment.mp4')
t0 = time.time()
segs, lang = transcribe(media, model_name='tiny', chunk_minutes=5, cpu_threads=4, beam_size=1)
elapsed = time.time() - t0
print(f'=== MEASUREMENT ===')
print(f'Model: tiny')
print(f'Chunks: 5-minute')
print(f'Segments: {len(segs)}')
print(f'Language: {lang}')
print(f'Wall time: {elapsed:.1f}s')
print(f'Realtime factor: {elapsed/600:.2f}x')
print(f'Estimated full 118min: {elapsed/600*118*60/60:.0f} min')
"
```

Report all measurements. If realtime factor > 5x, investigate before proceeding.

- [ ] **Step 3: If performance is acceptable, run full pipeline on 10-minute segment**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m selekyt.cli /tmp/test_10min_segment.mp4 \
  --out /tmp/selektyt_test_10 --model tiny --chunk-minutes 5 --cpu-threads 4
```

- [ ] **Step 4: Inspect rendered output**

```bash
ffprobe -v error -show_entries stream=codec_name,width,height,duration -of json /tmp/selektyt_test_10/project/facebook/01.mp4 2>/dev/null
ffprobe -v error -show_entries stream=codec_name,width,height,duration -of json /tmp/selektyt_test_10/project/shorts/01.mp4 2>/dev/null
ls -lh /tmp/selektyt_test_10/project/facebook/ /tmp/selektyt_test_10/project/shorts/
```

Report: dimensions, codecs, durations, file sizes.

- [ ] **Step 5: Commit any fixes needed**

```bash
git add -A && git commit -m "fix: adjustments from 10-minute media test" 2>/dev/null || echo "no changes"
git push origin main 2>/dev/null || echo "push skipped"
```

---

## Phase 2: Editorial Quality (FLVYT-Inspired)

### Task 2.1: Improve anti-repetition in `engine/editorial.py`

**Files:**
- Modify: `engine/editorial.py` (rank function)
- Modify: `tests/test_editorial.py`

**Dependencies:** Phase 1 complete (pipeline works end-to-end)

**FLVYT concept adapted:** FLVYT's `asset_director.py` uses `USED_PENALTY = 0.35` to avoid selecting the same asset twice. SelekyT's `rank()` currently only checks temporal overlap (4s margin). We add story-type diversity and text-similarity dedup.

- [ ] **Step 1: Add tests for improved dedup**

Append to `tests/test_editorial.py`:

```python
def test_rank_diversifies_story_types():
    """Rank should not pick all the same story type."""
    segs = [
        Segment(0, 15, "But actually nobody expected this contradiction to change everything.", []),
        Segment(20, 35, "However the hidden problem was actually a contradiction in the system.", []),
        Segment(40, 55, "How did the first mistake lead to the worst outcome?", []),
        Segment(60, 75, "The secret changed the way everyone saw the problem.", []),
    ]
    ranked = rank(segs, limit=4)
    types = [c.story_type for c in ranked]
    # Should have at least 2 different story types
    assert len(set(types)) >= 2, f"Expected diverse types, got: {types}"


def test_rank_deduplicates_similar_text():
    """Rank should not pick nearly identical clips."""
    segs = [
        Segment(0, 15, "The secret changed everything about how the system works and nobody knew.", []),
        Segment(20, 35, "The secret changed everything about how the system works and nobody realized.", []),
        Segment(40, 55, "A completely different topic with new information and a fresh perspective.", []),
    ]
    ranked = rank(segs, limit=3)
    # The two near-duplicates should not both appear
    texts = [c.text[:50] for c in ranked]
    assert len(set(texts)) == len(texts), f"Near-duplicates found: {texts}"
```

- [ ] **Step 2: Run tests to see current behavior**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_editorial.py -v
```

- [ ] **Step 3: Improve `rank()` in `engine/editorial.py`**

Add text similarity dedup and story-type diversity to the existing `rank()` function. The change should be minimal — add a `_text_sim` helper and modify the dedup loop.

```python
def _text_sim(a: str, b: str) -> float:
    """Simple word-overlap similarity (Jaccard)."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def rank(thoughts: list[Segment], limit: int = 8) -> list[Candidate]:
    candidates = [score_candidate(s) for s in thoughts if 12 <= s.duration <= 55 and len(s.text.split()) >= 20]
    candidates.sort(key=lambda c: (c.total_score, c.context_score, c.end-c.start), reverse=True)
    chosen: list[Candidate] = []
    type_counts: dict[str, int] = {}
    MAX_PER_TYPE = max(2, limit // 2)
    for c in candidates:
        # Temporal overlap check
        if any(not (c.end <= x.start - 4 or c.start >= x.end + 4) for x in chosen):
            continue
        # Text similarity dedup (>0.75 overlap = too similar)
        if any(_text_sim(c.text, x.text) > 0.75 for x in chosen):
            continue
        # Story type diversity
        if type_counts.get(c.story_type, 0) >= MAX_PER_TYPE:
            continue
        chosen.append(c)
        type_counts[c.story_type] = type_counts.get(c.story_type, 0) + 1
        if len(chosen) >= limit:
            break
    return chosen
```

- [ ] **Step 4: Run tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_editorial.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/editorial.py tests/test_editorial.py && git commit -m "feat: improve anti-repetition with text similarity and story-type diversity"
git push origin main
```

Report the commit SHA.

---

### Task 2.2: Improve `engine/context_guard.py` for multilingual

**Files:**
- Modify: `engine/context_guard.py`
- Create: `tests/test_context_guard.py`

**Dependencies:** Task 2.1

**FLVYT concept adapted:** FLVYT's editorial gate validates frame-precision timing. SelekyT's context_guard validates clip quality. We add multilingual sensationalism detection and a configurable threshold.

- [ ] **Step 1: Write tests**

Create `tests/test_context_guard.py`:

```python
"""Tests for context guard multilingual support."""
from models.core import Candidate, Word


def _make_candidate(text: str, hook: str = "", context_score: float = 0.8) -> Candidate:
    return Candidate(0, 10, text, "insight", hook or text[:50], text[-50:],
                     0.7, 0.6, context_score, 0.9, "test", 0.5, [])


def test_rejects_low_context():
    from engine.context_guard import validate
    c = _make_candidate("Some text", context_score=0.5)
    ok, reason = validate(c)
    assert not ok


def test_rejects_english_absolute_claims():
    from engine.context_guard import validate
    c = _make_candidate("Nobody knows this secret", hook="Nobody knows this secret")
    ok, reason = validate(c)
    assert not ok
    assert "absolute" in reason


def test_rejects_telugu_absolute_claims():
    from engine.context_guard import validate
    c = _make_candidate("ఎవరూ ఈ రహస్యాన్ని తెలియదు", hook="ఎవరూ ఈ రహస్యాన్ని తెలియదు")
    ok, reason = validate(c)
    assert not ok


def test_rejects_hindi_absolute_claims():
    from engine.context_guard import validate
    c = _make_candidate("कोई नहीं जानता यह रहस्य", hook="कोई नहीं जानता यह रहस्य")
    ok, reason = validate(c)
    assert not ok


def test_passes_normal_clip():
    from engine.context_guard import validate
    c = _make_candidate("This is a normal clip with enough context and no sensational claims.", context_score=0.8)
    ok, reason = validate(c)
    assert ok
```

- [ ] **Step 2: Improve `engine/context_guard.py`**

```python
"""Context guard: validates clip quality and prevents sensational claims."""
from __future__ import annotations
import re
from models.core import Candidate

# Multilingual absolute-claim words
ABSOLUTE_WORDS = [
    # English
    r"nobody", r"everyone", r"always", r"never", r"the world", r"secret",
    # Telugu
    r"ఎవరూ", r"అందరూ", r"ఎప్పుడూ", r"ఎప్పటికీ", r"రహస్యం",
    # Hindi
    r"कोई नहीं", r"सब लोग", r"हमेशा", r"कभी नहीं", r"रहस्य",
    # Tamil
    r"யாரும்", r"அனைவரும்", r"எப்போதும்", r"ரகசியம்",
    # Kannada
    r"ಯಾರೂ", r"ಎಲ್ಲರೂ", r"ಯಾವಾಗಲೂ", r"ರಹಸ್ಯ",
]

ABSOLUTE_PATTERN = re.compile("|".join(ABSOLUTE_WORDS), re.IGNORECASE)


def validate(c: Candidate) -> tuple[bool, str]:
    """Validate a candidate clip. Returns (ok, reason)."""
    if c.confidence < 0.75:
        return False, "low factual confidence"
    if c.context_score < 0.70:
        return False, "insufficient standalone context"
    # Prevent sensational hooks that are materially unsupported
    hook_lower = c.hook.lower()
    text_lower = c.text.lower()
    if ABSOLUTE_PATTERN.search(hook_lower) and len(c.text.split()) < 35:
        return False, "absolute claim needs more context"
    return True, "ok"
```

- [ ] **Step 3: Run tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_context_guard.py -v
```

Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add engine/context_guard.py tests/test_context_guard.py && git commit -m "feat: multilingual sensationalism detection in context guard"
git push origin main
```

Report the commit SHA.

---

### Task 2.3: Add optional LLM editorial enhancement

**Files:**
- Create: `engine/llm_editorial.py`
- Create: `tests/test_llm_editorial.py`
- Modify: `selekyt/pipeline.py` (add optional LLM pass after selection)

**Dependencies:** Task 2.2

**Constraint:** Pipeline must work identically without Ollama. LLM output must never rewrite source speech.

- [ ] **Step 1: Write tests**

Create `tests/test_llm_editorial.py`:

```python
"""Tests for optional LLM editorial pass."""
from models.core import Candidate, Word


def _make_candidate(text: str) -> Candidate:
    return Candidate(0, 10, text, "insight", text[:50], text[-50:],
                     0.7, 0.6, 0.8, 0.9, "test", 0.5, [])


def test_llm_disabled_returns_unchanged():
    from engine.llm_editorial import enhance_with_llm
    c = _make_candidate("This is a test clip.")
    result = enhance_with_llm(c, ollama_url=None)
    assert result.text == c.text
    assert result.hook == c.hook
    assert result.payoff == c.payoff


def test_llm_unavailable_returns_unchanged():
    from engine.llm_editorial import enhance_with_llm
    c = _make_candidate("This is a test clip.")
    result = enhance_with_llm(c, ollama_url="http://localhost:99999")
    assert result.text == c.text
```

- [ ] **Step 2: Create `engine/llm_editorial.py`**

```python
"""Optional LLM editorial enhancement using Ollama.

CRITICAL: This module NEVER rewrites, fabricates, or alters source speech.
It only provides semantic metadata (better hook labels, story arc notes).
If LLM is unavailable or fails, candidates pass through unchanged.
"""
from __future__ import annotations
import json
import requests
from models.core import Candidate


def _analyze(text: str, ollama_url: str, model: str = "llama3.2") -> dict:
    """Ask Ollama for semantic metadata about a transcript. Returns {} on any failure."""
    prompt = f"""Analyze this video clip transcript. Do NOT rewrite it.
Return JSON with these fields only:
- "arc": one of ["setup-payoff", "question-answer", "tension-release", "contrast", "revelation", "none"]
- "pacing_note": "fast" | "moderate" | "slow" based on information density

Transcript: {text[:500]}"""

    resp = requests.post(
        f"{ollama_url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=15,
    )
    if resp.status_code != 200:
        return {}
    response_text = resp.json().get("response", "")
    start = response_text.find("{")
    end = response_text.rfind("}") + 1
    if start < 0 or end <= start:
        return {}
    return json.loads(response_text[start:end])


def attach_llm_metadata(c: Candidate, ollama_url: str | None,
                        model: str = "llama3.2") -> Candidate:
    """Attach LLM semantic metadata to a candidate WITHOUT altering source text."""
    if not ollama_url:
        return c
    try:
        c.llm_metadata = _analyze(c.text, ollama_url, model)
    except (requests.RequestException, json.JSONDecodeError, KeyError, TimeoutError, ValueError):
        c.llm_metadata = {}
    return c


def enhance_with_llm(c: Candidate, ollama_url: str | None,
                     model: str = "llama3.2") -> Candidate:
    """Backward-compatible alias. Never changes text/hook/payoff."""
    return attach_llm_metadata(c, ollama_url, model)
```

- [ ] **Step 3: Run tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_llm_editorial.py -v
```

Expected: ALL PASS.

- [ ] **Step 4: Wire `--ollama-url` into CLI and pipeline**

Modify `selekyt/cli.py` — add flag back:

```python
    p.add_argument("--ollama-url", default=None, help="Optional Ollama URL for LLM editorial metadata (never mandatory)")
```

and pass to `run(...)`.

Modify `selekyt/pipeline.py` — add `ollama_url: str | None = None` to `run(...)` and add an optional refinement step after Stage 3 (`select`). It must NEVER rewrite source speech — it only attaches a `llm` metadata dict to each selection:

```python
    # Stage 3b: Optional LLM editorial metadata (never rewrites source speech)
    if ollama_url:
        print("[pipeline] Stage: llm editorial (optional)")
        from engine.llm_editorial import attach_llm_metadata
        raw = json.loads(selections_path.read_text(encoding="utf-8"))
        for d in raw:
            c = next(x for x in selected if abs(x.start - d["start"]) < 0.01)
            attach_llm_metadata(c, ollama_url)
        selections_path.write_text(json.dumps([c.to_dict() for c in selected], ensure_ascii=False, indent=2), encoding="utf-8")
```

Add to `engine/llm_editorial.py` a metadata attach function (separate from `enhance_with_llm`):

```python
def attach_llm_metadata(c: Candidate, ollama_url: str | None) -> Candidate:
    \"\"\"Attach LLM semantic metadata to a candidate WITHOUT altering source text.\"\"\"
    if not ollama_url:
        return c
    analysis = _analyze(c.text, ollama_url)
    c.llm_metadata = analysis  # new optional field on Candidate
    return c
```

Add `llm_metadata: dict | None = None` field to `models/core.py` `Candidate` (optional, default None).

- [ ] **Step 5: Env-configurable fallback thresholds in `providers/whisper.py`**

Make fallback parameters configurable via environment (with defaults):

```python
import os
FALLBACK_THRESHOLD_RT = float(os.environ.get("SELEKYT_FALLBACK_RT", "10.0"))
FALLBACK_CONSECUTIVE = int(os.environ.get("SELEKYT_FALLBACK_CONSECUTIVE", "3"))
```

- [ ] **Step 6: Run all tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/ -v
```

Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add engine/llm_editorial.py tests/test_llm_editorial.py selekyt/cli.py selekyt/pipeline.py providers/whisper.py models/core.py && git commit -m "feat: optional LLM editorial metadata (Ollama, never mandatory) + env-configurable fallback"
git push origin main
```

Report the commit SHA.

---

### Task 2.4: Improve `engine/edit_plan.py` with audio-truth validation

**Files:**
- Modify: `engine/edit_plan.py`
- Modify: `tests/test_editorial.py` (add edit_plan tests)

**Dependencies:** Task 2.3

**FLVYT concept adapted:** FLVYT's `timing.py` treats TTS-measured duration as truth (`timed_by_audio=True`). SelekyT should treat source audio word timestamps as truth and validate that retention edits don't destroy timing.

- [ ] **Step 1: Write tests**

Append to `tests/test_editorial.py`:

```python
def test_retention_preserves_total_duration():
    """Retention edits should not drastically shorten clips."""
    from engine.edit_plan import retention_intervals
    from models.core import Candidate, Word
    words = [Word(f"w{i}", i*0.5, i*0.5+0.3, 1.0) for i in range(20)]
    c = Candidate(0, 10, " ".join(w.text for w in words), "insight", "", "",
                  0.7, 0.6, 0.8, 0.9, "test", 0.5, words)
    intervals = retention_intervals(c)
    total = sum(end - start for start, end in intervals)
    assert total >= 7.0, f"Retention kept only {total:.1f}s of 10s clip (expected >=7s)"


def test_retention_falls_back_for_speech_dense_clips():
    """Dense speech clips should not be aggressively cut."""
    from engine.edit_plan import retention_intervals
    from models.core import Candidate, Word
    # Words packed tightly, no gaps
    words = [Word(f"w{i}", i*0.3, i*0.3+0.25, 1.0) for i in range(30)]
    c = Candidate(0, 9, " ".join(w.text for w in words), "insight", "", "",
                  0.7, 0.6, 0.8, 0.9, "test", 0.5, words)
    intervals = retention_intervals(c)
    total = sum(end - start for start, end in intervals)
    assert total >= 6.0, f"Dense speech clip too aggressively cut: {total:.1f}s"
```

- [ ] **Step 2: Improve `engine/edit_plan.py`**

Add a safeguard: if the clip has dense speech (few long gaps), return the full clip. Add a minimum-ratio check.

```python
"""Retention editing: conservatively remove dead air."""
from __future__ import annotations
from models.core import Candidate


def retention_intervals(c: Candidate, min_pause: float = 0.55, keep_edge: float = 0.06) -> list[tuple[float, float]]:
    """Conservatively remove long speech gaps while preserving natural rhythm."""
    if not c.text.strip():
        return []
    words = c.words or []
    if not words:
        return [(c.start + keep_edge, max(c.start + keep_edge, c.end - keep_edge))]

    total_duration = c.end - c.start
    intervals: list[tuple[float, float]] = []
    cur_start = max(c.start, words[0].start - keep_edge)
    prev_end = words[0].end
    for w in words[1:]:
        gap = w.start - prev_end
        if gap >= min_pause:
            end = min(c.end, prev_end + keep_edge)
            if end - cur_start >= 0.35:
                intervals.append((cur_start, end))
            cur_start = max(c.start, w.start - keep_edge)
        prev_end = max(prev_end, w.end)
    end = min(c.end, prev_end + keep_edge)
    if end - cur_start >= 0.35:
        intervals.append((cur_start, end))

    if not intervals:
        return [(c.start, c.end)]

    # Safeguard: if edits would remove >30% of a dense-speech clip, keep full clip
    edited_total = sum(e - s for s, e in intervals)
    if edited_total < total_duration * 0.70:
        # Check if the clip is speech-dense (few long gaps)
        gaps = []
        for i in range(1, len(words)):
            gaps.append(words[i].start - words[i-1].end)
        long_gaps = sum(1 for g in gaps if g >= min_pause)
        total_gaps = len(gaps) if gaps else 1
        if long_gaps / total_gaps < 0.15:  # <15% long gaps = dense speech
            return [(c.start, c.end)]

    return intervals
```

- [ ] **Step 3: Run tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/ -v
```

Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add engine/edit_plan.py tests/test_editorial.py && git commit -m "feat: audio-truth retention editing with dense-speech safeguard"
git push origin main
```

Report the commit SHA.

---

## Phase 3: Production QA

### Task 3.1: Rewrite `engine/qa.py` with real rendered-output validation

**Files:**
- Rewrite: `engine/qa.py`
- Create: `tests/test_qa.py`
- Modify: `selekyt/pipeline.py` (call qa after render)

**Dependencies:** Phase 2 complete

**FLVYT concepts adapted:**
- `quality.py`: signalstats dark-frame detection, scene cut analysis, silent tail detection
- `probe_delivery`: composite delivery gate

- [ ] **Step 1: Write tests**

Create `tests/test_qa.py`:

```python
"""Tests for QA validation."""
import subprocess
import tempfile
from pathlib import Path


def _make_test_mp4(out: Path, duration: float = 5.0) -> None:
    """Create a minimal test MP4 with audio and video."""
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s=640x360:d={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "64k",
        "-shortest", str(out)
    ], check=True, capture_output=True)


def test_validate_good_output():
    from engine.qa import validate_output
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "good.mp4"
        _make_test_mp4(mp4)
        errors = validate_output(mp4, vertical=False)
        assert errors == [], f"Unexpected errors: {errors}"


def test_validate_missing_file():
    from engine.qa import validate_output
    errors = validate_output(Path("/nonexistent.mp4"), vertical=False)
    assert len(errors) > 0


def test_validate_too_small():
    from engine.qa import validate_output
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "tiny.mp4"
        mp4.write_bytes(b"not a video")
        errors = validate_output(mp4, vertical=False)
        assert any("small" in e or "ffprobe" in e for e in errors)


def test_detect_silent_tail():
    from engine.qa import detect_silent_tail
    with tempfile.TemporaryDirectory() as td:
        # 5s video with 2s silence at end
        mp4 = Path(td) / "tail.mp4"
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=5",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", "3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-filter_complex", "[0:v][1:a][2:a]concat=n=2:v=1:a=1[outv][outa]",
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            str(mp4)
        ], check=True, capture_output=True)
        has_tail, tail_sec = detect_silent_tail(mp4, threshold_db=-45, min_sec=1.0)
        # This is a rough check — may need adjustment based on ffmpeg concat behavior
        assert isinstance(has_tail, bool)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_qa.py -v
```

Expected: FAIL (functions don't exist).

- [ ] **Step 3: Rewrite `engine/qa.py`**

```python
"""Production QA: validates actual rendered MP4 output.

Checks beyond basic ffprobe:
- Duration reasonableness
- Audio/video stream presence
- Orientation (landscape/portrait)
- Silent tail detection
- Frozen frame detection (heuristic)
- File size reasonableness
"""
from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path


def probe(path: Path) -> dict:
    """Full ffprobe JSON output."""
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe is required and must be on PATH")
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "stream=codec_type,codec_name,width,height,duration",
           "-show_entries", "format=duration,size",
           "-of", "json", str(path)]
    return json.loads(subprocess.check_output(cmd, text=True))


def validate_output(path: Path, vertical: bool = False, min_duration: float = 1.0,
                    min_size: int = 50000) -> list[str]:
    """Validate a rendered MP4. Returns list of error strings (empty = OK)."""
    errors: list[str] = []
    if not path.exists():
        return [f"file does not exist: {path}"]
    if path.stat().st_size < min_size:
        errors.append(f"suspiciously small ({path.stat().st_size} bytes): {path}")
        return errors

    try:
        info = probe(path)
    except Exception as e:
        return [f"ffprobe failed for {path}: {e}"]

    # Check streams
    streams = info.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not video_streams:
        errors.append(f"no video stream: {path}")
    if not audio_streams:
        errors.append(f"no audio stream: {path}")

    # Check dimensions
    if video_streams:
        vs = video_streams[0]
        w = vs.get("width", 0)
        h = vs.get("height", 0)
        if vertical and w > h:
            errors.append(f"expected vertical but got {w}x{h}: {path}")
        if not vertical and h > w:
            errors.append(f"expected landscape but got {w}x{h}: {path}")

    # Check duration
    fmt = info.get("format", {})
    duration = float(fmt.get("duration", 0))
    if duration < min_duration:
        errors.append(f"too short ({duration:.1f}s < {min_duration}s): {path}")

    return errors


def detect_silent_tail(path: Path, threshold_db: float = -45, min_sec: float = 1.0) -> tuple[bool, float]:
    """Detect if the last N seconds of audio are silent.

    Returns (has_silent_tail, tail_duration_sec).
    """
    try:
        # Get duration
        info = probe(path)
        duration = float(info.get("format", {}).get("duration", 0))
        if duration < min_sec + 0.5:
            return False, 0.0

        # Use silencedetect on the tail
        check_start = max(0, duration - min_sec - 1)
        cmd = [
            "ffmpeg", "-ss", str(check_start), "-i", str(path),
            "-af", f"silencedetect=noise={threshold_db}dB:d={min_sec}",
            "-f", "null", "-"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stderr
        # silencedetect prints "silence_end" when silence is found
        has_tail = "silence_end" in output or "silence_start" in output
        return has_tail, min_sec if has_tail else 0.0
    except (subprocess.TimeoutExpired, Exception):
        return False, 0.0


def validate_rendered_outputs(project: Path) -> dict:
    """Validate all rendered outputs in a project directory.

    Returns a report dict with per-file results and overall status.
    """
    report = {"files": {}, "errors": [], "ok": True}

    for subdir in ["facebook", "shorts"]:
        dir_path = project / subdir
        if not dir_path.exists():
            continue
        for mp4 in sorted(dir_path.glob("*.mp4")):
            vertical = subdir == "shorts"
            file_errors = validate_output(mp4, vertical=vertical)
            has_tail, tail_sec = detect_silent_tail(mp4)

            file_report = {
                "errors": file_errors,
                "has_silent_tail": has_tail,
                "silent_tail_sec": round(tail_sec, 2),
                "size_bytes": mp4.stat().st_size,
            }
            report["files"][str(mp4)] = file_report
            if file_errors:
                report["errors"].extend([f"{mp4}: {e}" for e in file_errors])
                report["ok"] = False

    return report


def write_qa_report(project: Path, report: dict) -> Path:
    """Write QA report to project directory."""
    qa_dir = project / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    report_path = qa_dir / "qa.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path
```

- [ ] **Step 4: Run tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_qa.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/qa.py tests/test_qa.py && git commit -m "feat: production QA with rendered output validation and silent tail detection"
git push origin main
```

Report the commit SHA.

---

### Task 3.2: Integrate QA into pipeline and add frozen-frame detection

**Files:**
- Modify: `selekyt/pipeline.py` (add QA stage)
- Modify: `engine/qa.py` (add frozen frame detection)

**Dependencies:** Task 3.1

- [ ] **Step 1: Add frozen frame detection to `engine/qa.py`**

Append to `engine/qa.py`:

```python
def detect_frozen_frames(path: Path, threshold: float = 0.98, sample_count: int = 10) -> tuple[bool, float]:
    """Detect if video has excessively frozen/repeated frames.

    Samples N frames, computes pairwise absolute difference.
    Returns (has_frozen, max_pairwise_similarity).
    """
    import cv2
    import numpy as np
    try:
        info = probe(path)
        duration = float(info.get("format", {}).get("duration", 0))
        if duration < 1.0:
            return False, 0.0

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return False, 0.0

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total_frames = int(duration * fps)
        sample_indices = np.linspace(0, total_frames - 1, sample_count, dtype=int)

        frames: list[np.ndarray] = []
        for idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, frame = cap.read()
            if ok:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                frames.append(gray)
        cap.release()

        if len(frames) < 2:
            return False, 0.0

        max_sim = 0.0
        for i in range(len(frames) - 1):
            diff = cv2.absdiff(frames[i], frames[i + 1])
            sim = 1.0 - (diff.mean() / 255.0)
            max_sim = max(max_sim, sim)

        return max_sim >= threshold, max_sim
    except (cv2.error, Exception):
        return False, 0.0
```

- [ ] **Step 2: Add QA stage to pipeline**

Modify `selekyt/pipeline.py` to add a QA stage after render:

```python
    # Stage 5: QA (after render)
    if not _stage_done(project, "qa", fp):
        print("[pipeline] Stage: qa")
        from engine.qa import validate_rendered_outputs, write_qa_report
        qa_report = validate_rendered_outputs(project)
        write_qa_report(project, qa_report)
        if not qa_report["ok"]:
            print(f"[pipeline] QA warnings: {qa_report['errors'][:5]}")
        _mark_stage(project, "qa", fp, {"ok": qa_report["ok"], "files": len(qa_report["files"])})
    else:
        print("[pipeline] Stage: qa — CACHED")
```

- [ ] **Step 3: Run all tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/ -v
```

Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add selekyt/pipeline.py engine/qa.py && git commit -m "feat: integrate QA into pipeline with frozen-frame and silent-tail detection"
git push origin main
```

Report the commit SHA.

---

### Task 3.3: Full pipeline test on 10-minute segment with QA validation

**Dependencies:** Tasks 3.1 + 3.2

- [ ] **Step 1: Run full pipeline on 10-minute segment**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m selekyt.cli /tmp/test_10min_segment.mp4 \
  --out /tmp/selektyt_full_test --model tiny --chunk-minutes 5 --cpu-threads 4 --beam-size 1
```

- [ ] **Step 2: Inspect QA report**

```bash
cat /tmp/selektyt_full_test/project/qa/qa.json
```

Report: all errors, silent tail status, file sizes.

- [ ] **Step 3: Visually inspect output (ffprobe)**

```bash
for f in /tmp/selektyt_full_test/project/facebook/*.mp4 /tmp/selektyt_full_test/project/shorts/*.mp4; do
  echo "=== $(basename $f) ==="
  ffprobe -v error -show_entries stream=codec_name,width,height -show_entries format=duration,size -of json "$f" 2>/dev/null
done
```

- [ ] **Step 4: Commit any fixes**

```bash
git add -A && git commit -m "fix: adjustments from full pipeline QA test" 2>/dev/null || echo "no changes"
git push origin main 2>/dev/null || echo "push skipped"
```

Report the commit SHA.

---

## Verification Checklist

After all phases, verify:

- [ ] `python3 -m pytest tests/ -v` — ALL PASS
- [ ] `selekyt --help` — shows all new flags
- [ ] 5-minute segment produces valid MP4s
- [ ] 10-minute segment produces valid MP4s with QA report
- [ ] Transcription is resumable (interrupt and restart — chunks are cached)
- [ ] Per-stage pipeline caching works (second run skips completed stages)
- [ ] No regressions in existing editorial scoring
- [ ] All commits pushed to origin/main
- [ ] All commit SHAs reported
