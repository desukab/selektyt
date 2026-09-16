# Phase 2 Extension — Professional Editing Subsystem Design Spec

## Overview

Expand SelekyT from a clip selector into a professional B2B automated video editing engine, inspired by AutoCut's architecture but with original implementation. AutoCut's repository (cobanov/autocut) has no license file — all source code is proprietary. SelekyT reproduces AutoCut's *functionality and architecture* using original code; no source is copied.

## Environment Constraints

- Platform: Termux (Android), Python 3.14.6, ARM64, 5 CPU cores
- Available: numpy 2.4.4, ffmpeg 8.1.2, whisper.cpp native
- Unavailable: onnxruntime, silero_vad, scipy, soundfile (no ARM64 wheels, no bandwidth)
- Network: ~4KB/s (model downloads infeasible)
- Proxy validation source: `Es69N24I2Yo.mp4` (10min, 239MB) — NOT Z907ENCogm4
- Test WAV corpus: 50+ files in `.cache/telugify/` (speech, silence, dialogue, mixed)

## Architecture Principles

1. **Score caching decoupled from segmentation** — VAD probabilities computed once, cached; parameters changed post-hoc
2. **Concat-demuxer rendering** — not `select` filter (fails at hundreds of intervals)
3. **Pluggable VAD backends** — Silero preferred, FFmpeg fallback, Whisper last-resort
4. **Interval algebra as standalone module** — deterministic, extensively tested
5. **Pause classification beyond binary** — intentional/breathing/hesitation/dead_air/uncertain
6. **Editorial decides WHAT, mechanical editing decides HOW**

## Subsystem Modules

| ID | Section | Module | Responsibility |
|----|---------|--------|----------------|
| C  | Cut Algebra | `engine/cut_algebra.py` | All interval math |
| A  | Audio Extract | `providers/audio.py` | FFmpeg PCM extraction |
| A  | VAD | `engine/vad.py` | VAD backends + scoring + segmentation |
| A  | Pauses | `engine/pause_classifier.py` | Pause type classification |
| B  | Fusion | `engine/audio_fusion.py` | Whisper+VAD+pause fusion |
| E  | Jump-cut | `engine/jumpcut.py` | Mechanical editing engine |
| G  | FCPXML | `engine/fcpxml.py` | FCPXML 1.11 export |
| F  | Multi-track | `engine/multitrack.py` | Timeline track model |
| J  | Reports | `engine/report.py` | B2B machine-readable reports |
| K  | Error handling | `providers/ffmpeg.py` | FFmpeg error diagnostics |
| H  | Editorial | `engine/editorial.py` | Audio-derived scoring features |
| I  | Pacing | `engine/edit_plan.py` | VAD-derived pacing analysis |
| N  | CLI | `selekyt/cli.py` | Professional controls |
| L  | Pipeline | `selekyt/pipeline.py` | Full pipeline wiring |

## VAD Backend Hierarchy

```
Audio/VAD backend
├── SileroVADBackend       (preferred when installable)
├── FFmpegVADBackend       (silencedetect filter, always available)
└── WhisperTimestampBackend (last-resort, uses existing transcript)
```

Factory: `get_vad_backend()` tries Silero → FFmpeg → Whisper. Logs which is selected.

## Pause Classification Rules

| Type | Duration | Sentence Position | Preceding Word | Description |
|------|----------|------------------|----------------|-------------|
| dead_air | > 0.7s | anywhere | any | Long empty silence |
| breathing | 0.2-0.6s | sentence boundary | terminal punct | Natural breath pause |
| hesitation | 0.2-0.7s | mid-sentence | content word | Thinking pause |
| intentional | 0.5-2.0s | before key word | any | Dramatic beat |
| uncertain | any | ambiguous | filler word | Unclassified |

## CLI Flags (New)

```
--silence-threshold    VAD speech probability threshold (0.0-1.0, default 0.5)
--silence-padding      Seconds added around speech regions (default 0.3)
--min-silence          Minimum silence duration to keep (default 0.5s)
--min-speech           Minimum speech duration to keep (default 0.15s)
--keep-pauses          Comma-separated pause types to preserve
--remove-filler        Remove filler words from edit plan
--export-fcpxml        Export professional timeline alongside clips
--audio-analysis       Run VAD analysis and output analysis/ directory
```

## Pipeline Flow

```
SOURCE → Audio Extract → VAD Scores (cached) → Segmentation
                                              → Pause Classification
                → Whisper → Transcript
        → Audio/Truth Fusion → Fused Segments
        → Candidate Generation → Editorial Scoring → Context Guard
        → Jump-cut Planning → Word-safe Cut Algebra
        → Reframe → Captions → Concat-based Render
        → QA → Report (JSON + terminal)
        → Optional FCPXML Export
```

## Measurement Categories

1. **Unit tests** — deterministic, no media files required
2. **Proxy-source results** — Es69N24I2Yo.mp4 + WAV corpus, clearly labeled as proxy
3. **Actual Z907 results** — deferred until source is available; NOT fabricated

## Licensing Note

AutoCut (cobanov/autocut) has no license file — proprietary/all-rights-reserved. SelekyT:
- Does NOT copy AutoCut source code
- Implements equivalent functionality using original Python code
- Borrows architectural patterns (score caching, concat demuxer) that are general knowledge
- FCPXML 1.11 format is public specification
- Silero VAD is MIT-licensed but not used as dependency on this device
