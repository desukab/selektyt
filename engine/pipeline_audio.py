"""Pipeline-level audio integration: VAD analysis + VAD-enhanced pacing.

Bridges engine.audio_analysis into selekyt.pipeline without coupling:
- runs the audio-truth stage on demand (opt-in, off by default)
- re-enriches the aggregate pacing report with VAD fields
- returns a provenance dict merged into report.json
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.audio_analysis import run_audio_analysis


def run_pipeline_audio(
    media: Path,
    project: Path,
    words: list | None,
    source: str,
    pacing: dict,
    *,
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
    """Run VAD analysis, enrich pacing, return audio provenance for report.json."""
    summary = run_audio_analysis(
        media,
        project,
        words=words,
        source=source,
        min_speech_ms=min_speech_ms,
        min_silence_ms=min_silence_ms,
        silence_threshold=silence_threshold,
        silence_padding=silence_padding,
        keep_pauses=keep_pauses,
        remove_filler=remove_filler,
        export_fcpxml=export_fcpxml,
        vad_backend=vad_backend,
        threshold=threshold,
        force=force,
    )

    pacing["vad"] = {
        "vad_speech_ratio": summary["speech_ratio"],
        "vad_silence_ratio": summary["silence_ratio"],
        "vad_dead_air_ratio": summary["dead_air_ratio"],
        "vad_speech_regions": summary["speech_regions"],
        "compression_ratio": summary["compression_ratio"],
        "vad_backend": summary["vad_backend"],
    }
    return summary