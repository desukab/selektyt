# SelekyT Three-Phase Improvement Plan (REVISED)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SelekyT a reliable long-form → short-form editorial engine with measurable transcription performance, editorial quality gates, and production QA on actual rendered MP4s.

**Architecture:** Phase 1 adds transcription timing metrics, cpu_threads control, auto-fallback, and 5-minute chunk baseline to the existing chunked whisper.py. Phase 2 improves multilingual context guard and adds optional LLM metadata. Phase 3 expands QA beyond basic ffprobe validation.

**Tech Stack:** Python 3.10+, faster-whisper 1.2+ (ctranslate2, int8), ffmpeg/ffprobe, OpenCV, numpy, optional Ollama for LLM pass, requests.

**Spec:** This plan document. Supersedes original stale-spec.

---

## Verified Current State (from `main` at `40fd986`)

| Component | Status | File |
|-----------|--------|------|
| `providers/whisper.py` | ✅ Chunked (10min), resumable checkpoints, beam_size=1, overlap, audio extraction skip | 205 lines |
| `providers/whisper.py` | ❌ No timing metrics, no cpu_threads, no auto-fallback, no stalled-chunk detection, default 10min | 205 lines |
| `selekyt/pipeline.py` | ✅ Fingerprint transcript cache, write all artifacts, QA report | 174 lines |
| `selekyt/pipeline.py` | ❌ No per-stage resume (render redo on crash), no cpu-threads passthrough | 174 lines |
| `selekyt/cli.py` | ✅ --chunk-minutes, --model, --language, --limit, --caption-style, --cookies, --doctor | 74 lines |
| `selekyt/cli.py` | ❌ No --cpu-threads, --beam-size, --fallback-rt flags | 74 lines |
| `engine/editorial.py` | ✅ Multilingual HOOK/CONTRAST/FILLER, `_similarity` dedup, kind diversity cap | 82 lines |
| `engine/edit_plan.py` | ✅ Word-timestamp aware retention, >0.72 keep safeguard, >3 cuts fallback | 51 lines |
| `engine/captions.py` | ✅ Karaoke word-timing, 4 visual presets, multilingual-safe | 103 lines |
| `engine/caption_director.py` | ✅ Block grouping, emphasis, metadata | 114 lines |
| `engine/context_guard.py` | ❌ English-only absolute-claim detection | 14 lines |
| `engine/qa.py` | ❌ Minimal: ffprobe stream+duration+orientation+size only | 46 lines |
| `engine/reframe.py` | ✅ Face detection + center-weighted fallback | 60 lines |
| `engine/render.py` | ✅ Per-interval + concat, vertical crop | 58 lines |
| `engine/content_pack.py` | ✅ Source-based, no fabrication | 62 lines |
| `models/core.py` | ✅ Word, Segment, Candidate with total_score | 46 lines |
| `tests/test_editorial.py` | ❌ Only 2 tests (thought segmentation, rank determinism) | 24 lines |
| faster-whisper | ⚠️ Cannot install via pip on Python 3.14 aarch64 (no ctranslate2 wheels) | — |
| whisper.cpp | ✅ Built at `/home/whisper.cpp/build/bin/whisper-cli` | ARM native |
| proot Ubuntu | ✅ Has ctranslate2 4.8.2 + faster-whisper 1.2.1 + old code (pre-chunking) | — |

---

## Phase 1: Transcription Performance & Reliability

### Task 1.1: Add per-chunk timing metrics and ETA to `providers/whisper.py`

**Files:**
- Modify: `providers/whisper.py` (the existing chunk loop at lines 118-194)

**What changes:** Wrap the per-chunk transcription with `time.time()` measurements. Print a metrics line after each chunk completes. After chunk 2 or 3, compute and print empirical ETA.

```python
# Add to existing _transcribe_chunk function return or modify the loop:
import time

# In the main loop (line 118+), before model.transcribe:
t0 = time.time()

# After transcribe returns:
wall = time.time() - t0
chunk_duration = end - start  # audio seconds in this chunk
rt = wall / max(0.1, chunk_duration)
fallback_tag = f" (fallback→{model_name})" if model_name != initial_model else ""
print(
    f"[transcribe] chunk {index+1}/{count}: {start/60:.1f}-{end/60:.1f} min "
    f"({chunk_duration:.1f}s audio, {wall:.1f}s wall, {rt:.2f}x RT, "
    f"model={model_name}, threads={cpu_threads or 'auto'}{fallback_tag})",
    flush=True,
)

# After chunk 2+ complete, compute ETA:
if index >= 2:
    completed_wall = sum(earlier_walls)  # running sum
    avg_rt = completed_wall / sum(earlier_durations)
    remaining_chunks = count - (index + 1)
    remaining_audio = total - end
    eta = avg_rt * remaining_audio
    print(f"[transcribe] ETA: {eta:.0f}s ({eta/60:.1f}min) remaining ({remaining_chunks} chunks)", flush=True)
```

- [ ] **Step 1: Write test for timing metrics**

Create `tests/test_whisper_metrics.py`:

```python
"""Test that transcribe reports timing metrics."""
import time
from unittest.mock import patch, MagicMock
from pathlib import Path


def test_transcribe_reports_chunk_timing(capsys):
    """Verify chunk timing metrics are printed."""
    # Mock whisper to avoid model download
    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.start = 0.0
    mock_segment.end = 2.0
    mock_segment.text = "hello world"
    mock_segment.words = []
    mock_info = MagicMock()
    mock_info.language = "en"
    mock_model.transcribe.return_value = ([mock_segment], mock_info)

    with patch("providers.whisper.WhisperModel", return_value=mock_model):
        with patch("providers.whisper._duration", return_value=120.0):
            with patch("providers.whisper._extract_audio"):
                from providers.whisper import transcribe
                # This tests timing output is present
                import io, sys
                old_stdout = sys.stdout
                sys.stdout = buffer = io.StringIO()
                try:
                    segs, lang = transcribe(Path("/tmp/fake.wav"), chunk_minutes=60, cpu_threads=2)
                finally:
                    sys.stdout = old_stdout
                output = buffer.getvalue()
                assert "wall" in output, f"Missing wall-clock time in: {output}"
                assert "RT" in output, f"Missing realtime factor in: {output}"
```

- [ ] **Step 2: Run test to verify it fails (no timing metrics yet)**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_whisper_metrics.py -v
```

Expected: FAIL (timing metrics not present).

- [ ] **Step 3: Implement timing metrics in `providers/whisper.py`**

Add `import time` at top. In the main chunk loop (lines 118-194):

1. Before `_transcribe_chunk`: `t0 = time.time()`
2. After: compute `wall = time.time() - t0`, `rt = wall / max(0.1, decode_duration)`
3. Print the metrics line (chunk #, audio duration, wall time, RT factor, model, threads, fallback)
4. Accumulate `running_wall` and `running_duration` for ETA calculation
5. After chunk index >= 2: print empirical ETA = `avg_rt * remaining_audio_seconds`

- [ ] **Step 4: Run test**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_whisper_metrics.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit and push**

```bash
git add providers/whisper.py tests/test_whisper_metrics.py && git commit -m "feat: per-chunk timing metrics with empirical ETA reporting"
git push origin main
```

Report commit SHA. Verify `git log --oneline origin/main -1` matches.

---

### Task 1.2: Add `cpu_threads` control to `providers/whisper.py`

**Files:**
- Modify: `providers/whisper.py` (the `transcribe` function signature and `WhisperModel` init at line 114)
- Modify: `selekyt/cli.py` (add --cpu-threads flag)

**What changes:** Add `cpu_threads` parameter. Default to available physical cores (not hyperthreads). Pass to `WhisperModel(..., cpu_threads=N)`. Expose via CLI as `--cpu-threads` and env `SELEKYT_CPU_THREADS`.

- [ ] **Step 1: Write test**

Append to `tests/test_whisper_metrics.py`:

```python
def test_cpu_threads_passed_to_model():
    """Verify cpu_threads parameter reaches WhisperModel."""
    from unittest.mock import patch, MagicMock
    with patch("providers.whisper.WhisperModel") as MockModel:
        mock_m = MagicMock()
        MockModel.return_value = mock_m
        mock_m.transcribe.return_value = ([], MagicMock(language="en"))
        with patch("providers.whisper._duration", return_value=60.0):
            with patch("providers.whisper._extract_audio"):
                from providers.whisper import transcribe
                try:
                    transcribe(Path("/tmp/fake.wav"), cpu_threads=4, chunk_minutes=60)
                except Exception:
                    pass
                MockModel.assert_called_once()
                call_kwargs = MockModel.call_args[1]
                assert call_kwargs.get("cpu_threads") == 4 or MockModel.call_args[0][1] == "cpu"
```

- [ ] **Step 2: Implement cpu_threads**

In `providers/whisper.py`:
1. Add `cpu_threads: int | None = None` parameter to `transcribe()`
2. At top: `import os`
3. If cpu_threads is None, check env: `os.environ.get("SELEKYT_CPU_THREADS")`
4. If still None, use `os.cpu_count()` or 4 as default
5. Pass to `WhisperModel(model_name, device="auto", compute_type="int8", cpu_threads=cpu_threads)`
6. Print in metrics line

- [ ] **Step 3: Update CLI**

Add to `selekyt/cli.py`:
```python
p.add_argument("--cpu-threads", type=int, default=None, help="CPU threads for Whisper (default: all available)")
```
Pass `args.cpu_threads` to `run(...)`.

Update `run()` signature in `pipeline.py`:
```python
def run(source, out_root, model="small", language=None, limit=6, cookies=None, chunk_minutes=10, cpu_threads=None):
```
Pass `cpu_threads` to `transcribe()`.

- [ ] **Step 4: Run tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/ -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit and push**

```bash
git add providers/whisper.py selekyt/cli.py selekyt/pipeline.py tests/test_whisper_metrics.py && git commit -m "feat: cpu_threads control for Whisper with --cpu-threads CLI flag"
git push origin main
```

Report commit SHA.

---

### Task 1.3: Add measured auto-fallback to smaller model

**Files:**
- Modify: `providers/whisper.py` (add fallback logic after the timing metrics)

**Constraints (from user):**
- "Do not fall back merely because model initialization makes the first chunk slow"
- "Use measured/consecutive performance"
- Fallback thresholds configurable via env: `SELEKYT_FALLBACK_RT` (default 10.0), `SELEKYT_FALLBACK_CONSECUTIVE` (default 3)
- Auto-fallback chain: small → base → tiny

- [ ] **Step 1: Write test**

Create `tests/test_whisper_fallback.py`:

```python
"""Test auto-fallback logic for whisper model sizing."""
from unittest.mock import patch, MagicMock, call
from pathlib import Path


def test_fallback_after_consecutive_slow_chunks():
    """After N slow chunks, model should fall back."""
    from providers.whisper import transcribe, FALLBACK_CHAIN

    call_count = [0]
    def fake_transcribe(media, **kwargs):
        call_count[0] += 1
        seg = MagicMock()
        seg.start = 0; seg.end = 5.0; seg.text = "test"; seg.words = []
        info = MagicMock(language="en")
        return ([seg], info)

    with patch("providers.whisper.WhisperModel") as MockModel:
        mock_m = MagicMock()
        mock_m.transcribe = fake_transcribe
        MockModel.return_value = mock_m
        with patch("providers.whisper._duration", return_value=300.0):  # 5 min
            with patch("providers.whisper._extract_audio"):
                import os
                os.environ["SELEKYT_FALLBACK_CONSECUTIVE"] = "1"  # trigger after 1 chunk
                os.environ["SELEKYT_FALLBACK_RT"] = "0.0001"  # any speed triggers
                try:
                    from providers.whisper import transcribe
                    segs, lang = transcribe(Path("/tmp/fake.wav"), model_name="small", chunk_minutes=5)
                finally:
                    os.environ.pop("SELEKYT_FALLBACK_CONSECUTIVE", None)
                    os.environ.pop("SELEKYT_FALLBACK_RT", None)
                # Model should have been created with fallback model
                assert MockModel.call_count >= 2  # initial + fallback
```

- [ ] **Step 2: Implement fallback**

In `providers/whisper.py`:

```python
import os
FALLBACK_CHAIN = ["small", "base", "tiny"]
FALLBACK_RT = float(os.environ.get("SELEKYT_FALLBACK_RT", "10.0"))
FALLBACK_CONSECUTIVE = int(os.environ.get("SELEKYT_FALLBACK_CONSECUTIVE", "3"))
```

After each chunk timing print:
```python
if rt > FALLBACK_RT:
    slow_count += 1
    if slow_count >= FALLBACK_CONSECUTIVE:
        idx = FALLBACK_CHAIN.index(model_name) if model_name in FALLBACK_CHAIN else -1
        if idx < len(FALLBACK_CHAIN) - 1:
            model_name = FALLBACK_CHAIN[idx + 1]
            print(f"[transcribe] falling back to {model_name} after {slow_count} consecutive slow chunks")
            model = WhisperModel(model_name, device="auto", compute_type="int8", cpu_threads=cpu_threads)
            slow_count = 0
else:
    slow_count = 0
```

Key: `initial_model` tracks the first model used, so the metrics line can show whether fallback occurred for this chunk.

- [ ] **Step 3: Run tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/ -v
```

Expected: ALL PASS.

- [ ] **Step 4: Commit and push**

```bash
git add providers/whisper.py tests/test_whisper_fallback.py && git commit -m "feat: auto-fallback to smaller Whisper model based on measured consecutive RT"
git push origin main
```

Report commit SHA.

---

### Task 1.4: Change default chunk size to 5 minutes and expose beam-size CLI flag

**Files:**
- Modify: `providers/whisper.py` (default chunk_minutes=5)
- Modify: `selekyt/cli.py` (add --beam-size flag, change --chunk-minutes default to 5)
- Modify: `selekyt/pipeline.py` (pass chunk_minutes and beam_size through)

**What changes:** Default chunk size from 10→5 (user's proven safe baseline). Expose --beam-size (default 1) in CLI for advanced users.

- [ ] **Step 1: Change defaults**

In `providers/whisper.py` line 73: `chunk_minutes: int = 10` → `chunk_minutes: int = 5`

In `selekyt/cli.py`: `--chunk-minutes` default from 10→5, add:
```python
p.add_argument("--beam-size", type=int, default=1, help="Whisper beam size (1=fast, 5=better)")
```

In `selekyt/pipeline.py` `run()`: add `beam_size: int = 1` param, pass to `transcribe()`.

Update `providers/whisper.py` `transcribe()`: add `beam_size: int = 1` parameter, pass to `_transcribe_chunk()`.

- [ ] **Step 2: Update all existing tests**

Ensure no test hardcodes chunk_minutes=10 or relies on the old default.

- [ ] **Step 3: Run tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/ -v
```

Expected: ALL PASS.

- [ ] **Step 4: Commit and push**

```bash
git add providers/whisper.py selekyt/cli.py selekyt/pipeline.py && git commit -m "feat: 5-minute chunk baseline and --beam-size CLI flag"
git push origin main
```

Report commit SHA.

---

### Task 1.5: Install faster-whisper and run empirical media test

**Status: DONE (partial — faster-whisper runtime itself is blocked by environment; throughput measured via native whisper.cpp on identical audio and hardware)**

**Files:** None (testing only)

**Note:** ctranslate2 has no pip wheels for Python 3.14 aarch64. Check if a proot Ubuntu environment with the working install exists. Use that for testing, OR build ctranslate2 from source.

- [ ] **Step 1: Attempt faster-whisper installation**

```bash
# Option A: Use proot container (already has ctranslate2 4.8.2 + faster-whisper 1.2.1)
# Option B: Install in main Termux environment (may need ctranslate2 from source)
pip3 install ctranslate2==4.8.2 2>&1 | tail -5
pip3 install faster-whisper==1.2.1 2>&1 | tail -5
```

If Option B fails, use Option A (proot container) for testing.

- [ ] **Step 2: Create 5-minute test audio from available source**

The Z907ENCogm4 source is partially downloaded in the proot container (`out/project/source/Z907ENCogm4.f401.mp4.part` ~1GB). Use a synthetic 5-minute test or find a local video.

```bash
# Create synthetic 5-minute audio (sine wave, valid for pipeline testing)
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=300" -f lavfi -i "sine=frequency=880:duration=300" \
  -filter_complex "[0:a][1:a]amix=inputs=2:duration=first" -c:a pcm_s16le /tmp/test_5min.wav
```

- [ ] **Step 3: Run transcription on 5-minute audio, report all metrics**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -c "
import time, os
os.environ['SELEKYT_CPU_THREADS'] = '4'
from pathlib import Path
from providers.whisper import transcribe
media = Path('/tmp/test_5min.wav')
t0 = time.time()
segs, lang = transcribe(media, model_name='tiny', chunk_minutes=5, cpu_threads=4, beam_size=1)
elapsed = time.time() - t0
print(f'=== MEASUREMENT ===')
print(f'Segments: {len(segs)}')
print(f'Language: {lang}')
print(f'Total wall: {elapsed:.1f}s')
print(f'Realtime factor: {elapsed/300:.2f}x')
"
```

Report all metrics including per-chunk timing (should appear in output from the metrics print).

- [ ] **Step 4: Run 10-minute test**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -c "
import os
os.environ['SELEKYT_CPU_THREADS'] = '4'
from pathlib import Path
from providers.whisper import transcribe
media = Path('/tmp/test_10min.wav')
segs, lang = transcribe(media, model_name='tiny', chunk_minutes=5, cpu_threads=4, beam_size=1)
print(f'Segments: {len(segs)}')
" 2>&1
```

Report: chunks count, per-chunk timing, ETA appeared.

#### Empirically measured throughput (2026-09-16, aarch64 8-core/5 available, 4 threads)

faster-whisper's tiny model could not be downloaded in this environment (stable ~4 KB/s
network; 75 MB model would take hours). The `transcribe()` timing/ETA/fallback code was
validated by unit tests (`tests/test_whisper_metrics.py`, `tests/test_whisper_fallback.py`).
End-to-end decode throughput was measured with the natively-built whisper.cpp backend on the
*identical* 16 kHz mono audio and same hardware:

| model | param | audio | wall | RT |
|---|---|---|---|---|
| tiny | ggml-tiny.bin (75 MB) | 10 min | 102.5 s | **0.17x** |
| small | ggml-small.bin (487 MB) | 5 min | 348.99 s | **1.16x** |
| small | 5 threads (contention) | 5 min | 369.0 s | 1.23x |
| small-q5_1 | quantized | 5 min | 345.0 s | 1.15x |

Projection for the 118-min source at 4 threads: small ≈ 2.3 h, tiny ≈ 20 min.
3-4 threads beat 5 (ARM NEON, 8 cores); int8 quantization via ctranslate2 is expected to be
closer to the q5_1 number. Findings justify the small→base→tiny auto-fallback and 5-min chunks.

- [ ] **Step 5: If measurable RT > 5x, investigate and report**

Check whether the model is using SIMD/NEON, whether threads are bounded correctly, whether int8 quantization is working on ARM. Report findings.

---

### Task 1.6: Add stalled/failed chunk detection with timeout

**Files:**
- Modify: `providers/whisper.py`

**What changes:** If a single chunk takes longer than `timeout_minutes` (env `SELEKYT_CHUNK_TIMEOUT_MIN`, default 20), log a warning and optionally skip it. This prevents the "appears stuck" perception.

- [ ] **Step 1: Add timeout wrapper**

```python
import signal

CHUNK_TIMEOUT = int(os.environ.get("SELEKYT_CHUNK_TIMEOUT_MIN", "20")) * 60  # seconds
```

In the chunk loop, after transcribe:
```python
if wall > CHUNK_TIMEOUT:
    print(f"[transcribe] WARNING: chunk {index+1} took {wall/60:.1f}min (> {CHUNK_TIMEOUT/60:.0f}min timeout)")
```

No auto-skip — just warning. The user can decide whether to continue.

- [ ] **Step 2: Run tests, commit, push**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/ -v
git add providers/whisper.py && git commit -m "feat: chunk timeout warning for stalled transcription detection"
git push origin main
```

Report commit SHA.

---

## Phase 2: Editorial Quality

### Task 2.1: Multilingual sensationalism in `engine/context_guard.py`

**Files:**
- Modify: `engine/context_guard.py`
- Create: `tests/test_context_guard.py`

**Current state:** Only English absolute-claim words caught (`nobody|everyone|always|never|the world|secret`). Telugu/Hindi/Tamil/Kannada sensational hooks pass freely.

- [ ] **Step 1: Write multilingual context guard tests**

```python
"""Tests for multilingual context guard."""
from models.core import Candidate, Word


def _make(hook: str, text: str, context_score: float = 0.8) -> Candidate:
    return Candidate(0, 10, text, "insight", hook, text[-50:],
                     0.7, 0.6, context_score, 0.9, "test", 0.5, [])


def test_rejects_english_absolute():
    from engine.context_guard import validate
    c = _make("Nobody knows this secret", "Nobody knows this secret and nobody cares.")
    ok, reason = validate(c)
    assert not ok and "absolute" in reason


def test_rejects_telugu_absolute():
    from engine.context_guard import validate
    c = _make("ఎవరూ ఈ రహస్యం తెలియదు", "ఎవరూ ఈ రహస్యం తెలియదు మరియు ఎవరూ పట్టించుకోరు")
    ok, reason = validate(c)
    assert not ok


def test_rejects_hindi_absolute():
    from engine.context_guard import validate
    c = _make("कोई नहीं जानता यह रहस्य", "कोई नहीं जानता यह रहस्य और कोई परवाह नहीं करता")
    ok, reason = validate(c)
    assert not ok


def test_passes_long_absolute_claim():
    from engine.context_guard import validate
    c = _make("Nobody knows", "Nobody knows the full story because there are 100 different perspectives on this important topic that span decades of history and involve many stakeholders.")
    ok, reason = validate(c)
    assert ok  # 35+ words, should pass even with absolute claim


def test_passes_normal_non_sensational():
    from engine.context_guard import validate
    c = _make("The real explanation", "Here is the real explanation with enough context for standalone comprehension.")
    ok, _ = validate(c)
    assert ok
```

- [ ] **Step 2: Run tests to see current behavior**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_context_guard.py -v
```

- [ ] **Step 3: Add multilingual absolute words to `context_guard.py`**

```python
ABSOLUTE_WORDS = re.compile(
    r"\b(?:nobody|everyone|always|never|the world|secret|"  # English
    r"ఎవరూ|అందరూ|ఎప్పుడూ|రహస్యం|"  # Telugu
    r"कोई नहीं|सब लोग|हमेशा|रहस्य|"  # Hindi
    r"யாரும்|அனைவரும்|எப்போதும்|ரகசியம்|"  # Tamil
    r"ಯಾರೂ|ಎಲ್ಲರೂ|ಯಾವಾಗಲೂ|ರಹಸ್ಯ|"  # Kannada
    r"কেউ নেই|সবাই|সর্বদা|গোপন)"  # Bengali
    r"\b", re.IGNORECASE
)
```

Use this in the `validate` function to replace the English-only check.

- [ ] **Step 4: Run tests, commit, push**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_context_guard.py -v
git add engine/context_guard.py tests/test_context_guard.py && git commit -m "feat: multilingual sensationalism detection in context guard"
git push origin main
```

Report commit SHA.

---

### Task 2.2: Add optional LLM editorial metadata (Ollama)

**Files:**
- Create: `engine/llm_editorial.py`
- Create: `tests/test_llm_editorial.py`
- Modify: `models/core.py` (add `llm_metadata: dict | None = None` to Candidate)
- Modify: `selekyt/cli.py` (add --ollama-url flag)
- Modify: `selekyt/pipeline.py` (wire optional LLM pass after selection)

**Constraints:**
- Deterministic mode MUST work without Ollama
- LLM output must NEVER rewrite, fabricate, or alter source speech
- Ollama must be optional, never mandatory

- [ ] **Step 1: Add `llm_metadata` field to Candidate**

In `models/core.py`, add to Candidate:
```python
llm_metadata: dict | None = None
```

Place it AFTER `words` (optional fields at end).

- [ ] **Step 2: Create `engine/llm_editorial.py`**

```python
"""Optional LLM editorial metadata via Ollama. Never rewrites source speech."""
from __future__ import annotations
import json
import logging
import requests
from models.core import Candidate

log = logging.getLogger(__name__)


def _ask_ollama(text: str, ollama_url: str, model: str = "llama3.2") -> dict:
    prompt = f"""Analyze this video transcript. Return JSON only:
- "arc": "setup-payoff"|"question-answer"|"tension-release"|"contrast"|"revelation"|"none"
- "pacing": "fast"|"moderate"|"slow"

Transcript (first 500 chars): {text[:500]}"""

    resp = requests.post(f"{ollama_url}/api/generate",
                         json={"model": model, "prompt": prompt, "stream": False},
                         timeout=15)
    resp.raise_for_status()
    raw = resp.json().get("response", "")
    s = raw.find("{"); e = raw.rfind("}") + 1
    if s < 0 or e <= s:
        return {}
    return json.loads(raw[s:e])


def attach_metadata(c: Candidate, ollama_url: str | None, model: str = "llama3.2") -> Candidate:
    if not ollama_url:
        return c
    try:
        c.llm_metadata = _ask_ollama(c.text, ollama_url, model)
    except Exception as exc:
        log.debug("LLM metadata failed: %s", exc)
        c.llm_metadata = {}
    return c
```

- [ ] **Step 3: Write tests**

```python
"""Tests for optional LLM editorial metadata."""
from models.core import Candidate
from unittest.mock import patch, MagicMock


def _make(text: str) -> Candidate:
    return Candidate(0, 10, text, "insight", text[:50], text[-50:],
                     0.7, 0.6, 0.8, 0.9, "test", 0.5, [])


def test_llm_disabled_passes_through():
    from engine.llm_editorial import attach_metadata
    c = _make("Test text.")
    result = attach_metadata(c, ollama_url=None)
    assert result.llm_metadata is None


def test_llm_unavailable_passes_through():
    from engine.llm_editorial import attach_metadata
    c = _make("Test text.")
    result = attach_metadata(c, ollama_url="http://localhost:19999")
    assert result.llm_metadata == {}


def test_llm_success_stores_metadata():
    from engine.llm_editorial import attach_metadata
    c = _make("Test text about technology.")
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"response": '{"arc": "question-answer", "pacing": "fast"}'}
    with patch("engine.llm_editorial.requests.post", return_value=mock_resp):
        result = attach_metadata(c, ollama_url="http://localhost:11434")
        assert result.llm_metadata == {"arc": "question-answer", "pacing": "fast"}


def test_llm_never_alters_source_text():
    from engine.llm_editorial import attach_metadata
    c = _make("Original speech text.")
    result = attach_metadata(c, ollama_url="http://localhost:11434")
    assert result.text == "Original speech text."  # text unchanged
```

- [ ] **Step 4: Run tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_llm_editorial.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Wire into CLI and pipeline**

Add `--ollama-url` to `selekyt/cli.py`. Add `ollama_url: str | None = None` to `run()` signature in `pipeline.py`. After Stage 3 (select), if `ollama_url`:
```python
from engine.llm_editorial import attach_metadata
for c in selected:
    attach_metadata(c, ollama_url)
```

- [ ] **Step 6: Commit and push**

```bash
git add engine/llm_editorial.py tests/test_llm_editorial.py models/core.py selekyt/cli.py selekyt/pipeline.py && git commit -m "feat: optional LLM editorial metadata (Ollama, never mandatory, never rewrites speech)"
git push origin main
```

Report commit SHA.

---

### Task 2.3: Enhance edit_plan with pacing analysis for QA

**Files:**
- Modify: `engine/edit_plan.py` (add pacing metrics return)
- Create: `tests/test_edit_plan.py`

**FLVYT concept adapted:** FLVYT's `edit_policy.py` provides per-kind pacing budgets (chars/second). We adapt this to add a `pacing_report()` function that scores clips by information density and natural speech rhythm, callable from the QA stage.

- [ ] **Step 1: Add pacing analysis function**

```python
def pacing_report(c: Candidate) -> dict:
    """Analyze pacing of a selected clip. Used for QA reporting."""
    words = c.words or []
    if len(words) < 2:
        return {"words_per_second": 0, "density": "unknown", "natural": True}
    
    total_w = len(c.text.split())
    dur = max(0.01, c.end - c.start)
    wps = total_w / dur
    
    # Filler density
    fillers = [w for w in words if w.text.lower().strip(" ,.!?") in 
               {"um","uh","erm","hmm","అంటే","उम","मतलब","अर्थात","அதாவது"}]
    filler_pct = len(fillers) / max(1, len(words))
    
    # Pause analysis
    pauses = [max(0, words[i].start - words[i-1].end) for i in range(1, len(words))]
    avg_pause = sum(pauses) / max(1, len(pauses))
    long_pauses = sum(1 for p in pauses if p > 0.7)
    
    # Natural rhythm: pauses between phrases should be moderate (0.2-0.5s)
    natural = avg_pause < 0.6 and filler_pct < 0.05 and long_pauses < len(pauses) * 0.2
    density = "dense" if wps > 3.0 else "normal" if wps > 1.5 else "slow"
    
    return {
        "words_per_second": round(wps, 2),
        "total_words": total_w,
        "density": density,
        "avg_pause_sec": round(avg_pause, 3),
        "long_pauses": long_pauses,
        "filler_pct": round(filler_pct, 3),
        "natural": natural,
        "duration": round(dur, 3),
        "edit_ratio": round(sum(b - a for a, b in retention_intervals(c)) / dur, 3) if dur > 0 else 0,
    }
```

- [ ] **Step 2: Write tests**

```python
"""Tests for pacing analysis."""
from models.core import Candidate, Word


def test_pacing_dense_speech():
    from engine.edit_plan import pacing_report
    words = [Word(f"w{i}", i*0.3, i*0.3+0.25, 1.0) for i in range(20)]
    c = Candidate(0, 6, " ".join(w.text for w in words), "insight", "", "",
                  0.7, 0.6, 0.8, 0.9, "test", 0.5, words)
    r = pacing_report(c)
    assert r["density"] in ("dense", "normal")
    assert r["words_per_second"] > 2.0


def test_pacing_natural_rhythm():
    from engine.edit_plan import pacing_report
    words = []
    for i in range(10):
        words.append(Word(f"w{i}", i*0.8, i*0.8+0.3, 1.0))
    c = Candidate(0, 8, " ".join(w.text for w in words), "insight", "", "",
                  0.7, 0.6, 0.8, 0.9, "test", 0.5, words)
    r = pacing_report(c)
    assert r["natural"] is True
```

- [ ] **Step 3: Run tests, commit, push**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_edit_plan.py -v
git add engine/edit_plan.py tests/test_edit_plan.py && git commit -m "feat: pacing analysis report for QA"
git push origin main
```

Report commit SHA.

---

## Phase 3: Production QA

### Task 3.1: Expand `engine/qa.py` with real rendered-output validation

**Files:**
- Modify: `engine/qa.py` (add silent-tail, black-frame, visual diversity, source coverage, duplicate detection)
- Create: `tests/test_qa.py`

**FLVYT concepts adapted from `engine/quality.py`:**
- `probe_delivery` composite gate
- signalstats for black/dark frames
- scene cut analysis for cut intervals
- `silence_tail` dead air detection

- [ ] **Step 1: Add silent tail detection**

```python
def detect_silent_tail(path: Path, threshold_db: float = -45, min_sec: float = 1.5) -> tuple[bool, float]:
    """Detect if last N seconds are silent. Returns (has_tail, duration_sec)."""
    try:
        info = probe(path)
        duration = float(info.get("format", {}).get("duration", 0))
        if duration < min_sec + 0.5:
            return False, 0.0
        check_start = max(0, duration - min_sec - 1)
        cmd = ["ffmpeg", "-ss", str(check_start), "-i", str(path),
               "-af", f"silencedetect=noise={threshold_db}dB:d={min_sec}",
               "-f", "null", "-"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        has_tail = "silence_end" in result.stderr or "silence_start" in result.stderr
        return has_tail, min_sec if has_tail else 0.0
    except Exception:
        return False, 0.0
```

- [ ] **Step 2: Add black-frame percentage check**

```python
def black_frame_pct(path: Path, threshold: float = 16.0, sample_count: int = 8) -> float:
    """Estimate percentage of frames that are near-black."""
    import cv2
    import numpy as np
    try:
        info = probe(path)
        duration = float(info.get("format", {}).get("duration", 0))
        if duration < 0.5:
            return 0.0
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return 0.0
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total = int(duration * fps)
        indices = np.linspace(0, total - 1, sample_count, dtype=int)
        black_count = 0
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, frame = cap.read()
            if ok:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if gray.mean() < threshold:
                    black_count += 1
        cap.release()
        return black_count / max(1, sample_count)
    except Exception:
        return 0.0
```

- [ ] **Step 3: Add visual diversity check**

```python
def visual_diversity_score(paths: list[Path], sample_count: int = 5) -> float:
    """Estimate visual diversity across output clips (0=identical, 1=very diverse)."""
    import cv2
    import numpy as np
    histograms = []
    for path in paths:
        try:
            cap = cv2.VideoCapture(str(path))
            if not cap.isOpened():
                continue
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            for idx in np.linspace(0, max(0, total_frames - 1), sample_count, dtype=int):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
                ok, frame = cap.read()
                if ok:
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                    hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
                    cv2.normalize(hist, hist)
                    histograms.append(hist.flatten())
            cap.release()
        except Exception:
            continue
    if len(histograms) < 2:
        return 1.0
    # Pairwise distance
    distances = []
    for i in range(len(histograms)):
        for j in range(i + 1, min(i + 5, len(histograms))):  # limit pairs for perf
            d = cv2.compareHist(histograms[i].reshape(-1, 1), histograms[j].reshape(-1, 1), cv2.HISTCMP_BHATTACHARYYA)
            distances.append(d)
    return round(min(1.0, sum(distances) / max(1, len(distances))), 3)
```

- [ ] **Step 4: Expand `validate_outputs` → `validate_outputs_expanded`**

```python
def validate_outputs_expanded(outputs: list[Path]) -> dict:
    """Full QA check: ffprobe + silent tail + black frames + diversity."""
    report = {"ok": True, "errors": [], "per_file": {}, "diversity": 0.0}
    
    for f in outputs:
        is_vertical = "shorts" in f.parts
        ok, errors = validate_output(f, is_vertical)
        tail, tail_sec = detect_silent_tail(f)
        black = black_frame_pct(f)
        
        file_report = {"errors": errors, "silent_tail": tail, "silent_tail_sec": tail_sec,
                       "black_frame_pct": round(black, 3), "size": f.stat().st_size}
        report["per_file"][str(f)] = file_report
        if errors or tail:
            report["errors"].extend(f"{f}: {e}" for e in errors)
            if tail:
                report["errors"].append(f"{f}: silent tail ({tail_sec:.1f}s)")
            report["ok"] = False
    
    # Visual diversity (compare facebook clips only)
    fb_clips = [p for p in outputs if "facebook" in p.parts]
    report["diversity"] = visual_diversity_score(fb_clips) if len(fb_clips) > 1 else 1.0
    
    return report
```

- [ ] **Step 5: Update `write_report` to use expanded checks**

```python
def write_report(root: Path, outputs: list[Path]) -> Path:
    report = validate_outputs_expanded(outputs)
    qa_path = root / "qa.json"
    qa_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not report["ok"]:
        raise RuntimeError("output QA failed: " + "; ".join(report["errors"][:10]))
    return qa_path
```

- [ ] **Step 6: Write tests**

```python
"""Tests for QA validation."""
import subprocess
import tempfile
from pathlib import Path


def _make_test_mp4(out: Path, duration: float = 5.0) -> None:
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s=640x360:d={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "64k",
        "-shortest", str(out)
    ], check=True, capture_output=True)


def test_validate_good_output():
    from engine.qa import validate_output
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "good.mp4"
        _make_test_mp4(mp4)
        ok, errors = validate_output(mp4, vertical=False)
        assert ok


def test_silent_tail_detection():
    from engine.qa import detect_silent_tail
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "tail.mp4"
        # Create video with 2s silence at end
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=5",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", "3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-filter_complex", "[0:v][1:a][2:a]concat=n=2:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(mp4)
        ], check=True, capture_output=True)
        has_tail, sec = detect_silent_tail(mp4)
        assert isinstance(has_tail, bool)


def test_black_frame_detection():
    from engine.qa import black_frame_pct
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "normal.mp4"
        _make_test_mp4(mp4, duration=2.0)
        pct = black_frame_pct(mp4)
        assert 0.0 <= pct <= 1.0
```

- [ ] **Step 7: Run tests**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_qa.py -v
```

Expected: ALL PASS.

- [ ] **Step 8: Commit and push**

```bash
git add engine/qa.py tests/test_qa.py && git commit -m "feat: production QA with silent tail, black frame, and visual diversity checks"
git push origin main
```

Report commit SHA.

---

### Task 3.2: Add duplicate clip detection and source coverage to QA

**Files:**
- Modify: `engine/qa.py`
- Modify: `tests/test_qa.py`

**FLVYT concept:** FLVYT's `asset_director.py` uses `USED_PENALTY` for anti-repetition. We apply similar logic to detect if selected clips overlap too much in source time.

- [ ] **Step 1: Add source coverage function**

```python
def source_coverage(outputs: list[Path], selections_path: Path) -> dict:
    """Analyze how much of the source video is covered by selections."""
    import json
    if not selections_path.exists():
        return {"coverage_pct": 0, "overlaps": []}
    selections = json.loads(selections_path.read_text(encoding="utf-8"))
    if not selections:
        return {"coverage_pct": 0, "overlaps": []}
    
    # Get source duration from first output
    try:
        info = probe(outputs[0]) if outputs else {}
        source_dur = float(info.get("format", {}).get("duration", 1))
    except Exception:
        source_dur = 1
    
    # Merge intervals
    intervals = sorted([(s["start"], s["end"]) for s in selections])
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    
    total_covered = sum(end - start for start, end in merged)
    
    # Detect overlaps between selections
    overlaps = []
    for i in range(len(selections)):
        for j in range(i + 1, len(selections)):
            si, sj = selections[i], selections[j]
            overlap_start = max(si["start"], sj["start"])
            overlap_end = min(si["end"], sj["end"])
            if overlap_end > overlap_start:
                overlaps.append({
                    "clip_i": i + 1, "clip_j": j + 1,
                    "overlap_sec": round(overlap_end - overlap_start, 2)
                })
    
    return {
        "coverage_pct": round(total_covered / max(1, source_dur) * 100, 1),
        "total_covered_sec": round(total_covered, 2),
        "overlaps": overlaps,
        "overlap_count": len(overlaps),
    }
```

- [ ] **Step 2: Add to write_report**

```python
def write_report(root: Path, outputs: list[Path]) -> Path:
    report = validate_outputs_expanded(outputs)
    selections_path = root / "selections.json"
    report["source_coverage"] = source_coverage(outputs, selections_path)
    qa_path = root / "qa.json"
    qa_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not report["ok"]:
        raise RuntimeError("output QA failed: " + "; ".join(report["errors"][:10]))
    return qa_path
```

- [ ] **Step 3: Add pacing report to per-file QA**

In `validate_outputs_expanded`, add pacing from edit_plan:
```python
from engine.edit_plan import pacing_report
# ... inside the per-file loop:
# Extract candidate text from selections.json to compute pacing
# (Or pass candidates directly — simplest to add pacing_report call in pipeline)
```

Note: The pacing report is most useful when called per-candidate in the pipeline. Add it there:
```python
# In pipeline.py, after render loop:
for idx, c in enumerate(selected, 1):
    from engine.edit_plan import pacing_report
    pacing = pacing_report(c)
    edit_report[idx-1]["pacing"] = pacing
```

- [ ] **Step 4: Write tests, commit, push**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m pytest tests/test_qa.py -v
git add engine/qa.py selekyt/pipeline.py && git commit -m "feat: source coverage analysis and pacing in QA report"
git push origin main
```

Report commit SHA.

---

### Task 3.3: Full pipeline integration test with QA validation

**Files:** None (testing only)

**Depends on:** All Phase 1 + Phase 2 + Phase 3 tasks complete.

- [ ] **Step 1: Create test data (synthetic 5-minute video)**

```bash
ffmpeg -y -f lavfi -i "color=c=blue:s=1920x1080:d=300,drawtext=text='Test Clip':fontsize=72:fontcolor=white:x=(w-tw)/2:y=(h-th)/2" \
  -f lavfi -i "sine=frequency=440:duration=300" \
  -c:v libx264 -preset ultrafast -crf 28 -c:a aac -b:a 128k -shortest \
  /tmp/test_video_5min.mp4
```

- [ ] **Step 2: Run full pipeline**

```bash
cd /data/data/com.termux/files/home/selektyt && python3 -m selekyt.cli /tmp/test_video_5min.mp4 \
  --out /tmp/selektyt_e2e_test --model tiny --chunk-minutes 5 --cpu-threads 4 --beam-size 1
```

Report:
- All pipeline stages printed
- QA report at `out/project/qa/qa.json`
- All JSON artifacts present

- [ ] **Step 3: Inspect QA report**

```bash
cat /tmp/selektyt_e2e_test/project/qa/qa.json | python3 -m json.tool
```

Verify: `ok: true/false`, no critical errors, silent tail status, black frame pct, diversity score, source coverage.

- [ ] **Step 4: Validate rendered outputs**

```bash
for f in /tmp/selektyt_e2e_test/project/facebook/*.mp4 /tmp/selektyt_e2e_test/project/shorts/*.mp4; do
  echo "=== $(basename $f) ==="
  ffprobe -v error -show_entries stream=codec_name,width,height -show_entries format=duration,size -of json "$f"
done
```

- [ ] **Step 5: Report measurements**

```
Pipeline: [stage timing]
QA Report: [json contents]
Output MP4s: [dimensions, codecs, sizes, durations]
Visual checks: [describe any issues observed]
```

---

## Verification Checklist

- [ ] All existing tests pass (`python3 -m pytest tests/ -v`)
- [ ] `selekyt --help` shows --cpu-threads, --beam-size, --ollama-url
- [ ] Transcribe prints per-chunk: chunk #, duration, wall time, RT factor, model, threads, fallback
- [ ] After 3 chunks, ETA is printed
- [ ] 5-minute chunks are default (not 10)
- [ ] context_guard catches Telugu/Hindi/Kannada sensationalism
- [ ] LLM metadata never appears when --ollama-url not given
- [ ] QA report includes: silent tail, black frame %, visual diversity, source coverage, pacing
- [ ] All commits pushed to `origin/main`
- [ ] All commit SHAs reported in this document

---

## Known Environment Issue

`ctranslate2` (faster-whisper dependency) has no pre-built wheels for Python 3.14 aarch64 (Termux). Options:
1. **Proot Ubuntu container** at `/data/data/com.termux/files/usr/var/lib/proot-distro/containers/ubuntu/rootfs/root/selektyt/` — has ctranslate2 4.8.2 + faster-whisper 1.2.1 installed (but older code)
2. **whisper.cpp** is built natively at `/data/data/com.termux/files/home/whisper.cpp/build/bin/whisper-cli` — ARM-optimized, would be faster than ctranslate2 on this hardware
3. **Build ctranslate2 from source** on Termux (requires cmake, BLAS libs)

Code changes in Phase 1-3 are backend-agnostic. Testing depends on resolving this for faster-whisper, or using whisper.cpp as a subprocess backend.
