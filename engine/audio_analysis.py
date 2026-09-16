"""Audio analysis orchestrator — VAD → fusion → pause → jumpcut → export.

Runs the audio-truth pipeline as a cohesive stage and persists artifacts:
  vad_scores.json      cached per-frame probabilities
  vad_regions.json     speech + silence regions (derived from cached scores)
  pause_analysis.json  classified pauses
  audio_truth.json     derived fusion layer (Whisper words never modified)
  cutlist.json         jump-cut decisions + compression stats
  cutlist.fcpxml       optional Final Cut import

All thresholds are applied post-hoc on cached scores (decoupled from
scoring), so re-tuning never re-runs the VAD model.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from engine.vad import (
    get_vad_backend, segment_with_meta, silence_from_speech,
    save_scores, load_scores, VADResult, SpeechRegion,
)
from engine.pause_classifier import PauseClassifier
from engine.audio_fusion import fuse_audio_truth
from engine.jumpcut import JumpCutEngine
from engine.fcpxml import export_fcpxml
from engine import cut_algebra
from providers.audio import source_fingerprint, audio_fingerprint, extract_pcm_wav


class AudioAnalysisError(RuntimeError):
    pass


def run_audio_analysis(
    media: Path,
    project: Path,
    *,
    words: list | None = None,
    source: str = "",
    min_speech_ms: int = 150,
    min_silence_ms: int = 100,
    silence_threshold: float = 0.5,
    silence_padding: float = 0.0,
    keep_pauses: bool = True,
    remove_filler: bool = True,
    export_fcpxml: bool = False,
    vad_backend: str | None = None,
    threshold: float = 0.5,
    force: bool = False,
) -> dict[str, Any]:
    """Run the full audio-truth stage. Returns a summary dict of artifacts
    and statistics; never modifies Whisper words."""
    project.mkdir(parents=True, exist_ok=True)
    audio_dir = project / "audio"
    audio_dir.mkdir(exist_ok=True)

    fp = source_fingerprint(media, source)
    wav_path = audio_dir / f"{fp}.wav"

    # 1. PCM extraction (cached by fingerprint)
    pcm_cached = wav_path.exists()
    if not pcm_cached:
        extract_pcm_wav(media, wav_path, sample_rate=16000)
    else:
        print("[cache] reusing extracted PCM", flush=True)

    # 2. VAD scoring (cached; backend recorded for provenance). The backend is
    #    part of the cache key so switching backends never silently serves
    #    another backend's cached scores — the pluggable-VAD contract requires
    #    the report to name the backend that *actually* ran.
    backend = get_vad_backend(vad_backend, words=words)
    score_path = audio_dir / f"{fp}.{backend.name}.vad_raw.json"
    result = load_scores(score_path) if not force else None
    scores_cached = result is not None
    if scores_cached:
        print(f"[cache] reusing VAD scores ({result.backend})", flush=True)
    else:
        result = backend.analyze(wav_path)
        save_scores(result, score_path)
        print(f"[vad] backend={result.backend} frames={len(result.scores)}", flush=True)

    # 3. Segmentation (post-hoc on cached scores — never re-runs the model).
    #    segment_with_meta records the hysteresis params that actually ran.
    speech, seg_meta = segment_with_meta(
        result.scores,
        sample_rate=result.sample_rate,
        frame_ms=result.frame_ms,
        threshold=threshold,
        min_speech_ms=min_speech_ms,
        min_silence_ms=min_silence_ms,
    )
    if silence_padding > 0:
        padded = cut_algebra.pad_intervals(
            [(s.start, s.end) for s in speech],
            silence_padding,
            bounds=(0.0, result.duration),
        )
        speech = [SpeechRegion(*iv) for iv in padded]
    duration = result.duration
    silence = silence_from_speech(speech, duration)

    # 4. Pause classification
    classifier = PauseClassifier()
    pauses = classifier.classify(silence, speech, duration, words)

    # 5. Fusion (derived layer; Whisper words untouched)
    truth = fuse_audio_truth(
        words or [],
        speech,
        silence,
        pauses,
        duration,
        vad_backend=result.backend,
        vad_score_file=str(score_path),
    )

    # 6. Jump-cut decisions
    engine = JumpCutEngine(
        silence_threshold=silence_threshold,
        keep_intentional=keep_pauses,
        keep_breathing=keep_pauses,
        remove_hallucinations=True,
    )
    cut = engine.generate_cutlist(truth)

    # 7. Persist artifacts
    (audio_dir / "vad_regions.json").write_text(json.dumps({
        "backend": result.backend,
        "frame_ms": result.frame_ms,
        "duration": round(duration, 3),
        "speech": [[round(r.start, 3), round(r.end, 3), round(r.score, 3)] for r in speech],
        "silence": [[round(r.start, 3), round(r.end, 3), round(r.duration, 3)] for r in silence],
        "config": {
            "threshold": threshold,
            "min_speech_ms": min_speech_ms,
            "min_silence_ms": min_silence_ms,
            "silence_padding": silence_padding,
        },
    }, ensure_ascii=False, indent=2))
    (audio_dir / "pause_analysis.json").write_text(json.dumps(
        [asdict(p) for p in pauses], ensure_ascii=False, indent=2))
    (audio_dir / "audio_truth.json").write_text(json.dumps({
        "duration": round(truth.duration, 3),
        "vad_backend": truth.vad_backend,
        "vad_score_file": truth.vad_score_file,
        "speech_ratio": truth.speech_ratio,
        "silence_ratio": truth.silence_ratio,
        "dead_air_ratio": truth.dead_air_ratio,
        "hallucinated_count": truth.hallucinated_count,
        "fused_words": sum(1 for _ in truth.fused_words),
        "fused_regions": len(truth.fused_regions),
        "vad_speech": len(truth.vad_speech),
        "vad_silence": len(truth.vad_silence),
        "pauses": len(truth.pauses),
    }, ensure_ascii=False, indent=2))
    (audio_dir / "cutlist.json").write_text(json.dumps({
        "original_duration": cut.original_duration,
        "total_keep_duration": cut.total_keep_duration,
        "total_remove_duration": cut.total_remove_duration,
        "compression_ratio": cut.compression_ratio,
        "segments_removed": cut.segments_removed,
        "words_removed": cut.words_removed,
        "segments": [
            {"start": round(s.start, 3), "end": round(s.end, 3),
             "duration": round(s.duration, 3), "type": s.segment_type.value,
             "reason": s.reason}
            for s in cut.segments
        ],
    }, ensure_ascii=False, indent=2))

    fcpxml_path = None
    if export_fcpxml:
        fcpxml_path = export_fcpxml(cut, audio_dir / "cutlist.fcpxml", source_path=str(media))

    return {
        "vad_backend": result.backend,
        "vad_model": result.vad_model,
        "fallback_reason": result.fallback_reason,
        "frame_ms": result.frame_ms,
        "duration": round(result.duration, 3),
        "n_frames": len(result.scores),
        "hysteresis": seg_meta.get("hysteresis"),
        "scores_cached": scores_cached,
        "pcm_cached": pcm_cached,
        "vad_score_file": str(score_path),
        "vad_regions": str(audio_dir / "vad_regions.json"),
        "pause_analysis": str(audio_dir / "pause_analysis.json"),
        "audio_truth": str(audio_dir / "audio_truth.json"),
        "cutlist": str(audio_dir / "cutlist.json"),
        "fcpxml": str(fcpxml_path) if fcpxml_path else None,
        "speech_ratio": truth.speech_ratio,
        "silence_ratio": truth.silence_ratio,
        "dead_air_ratio": truth.dead_air_ratio,
        "compression_ratio": cut.compression_ratio,
        "speech_regions": len(speech),
        "silence_regions": len(silence),
        "hallucinated_count": truth.hallucinated_count,
    }