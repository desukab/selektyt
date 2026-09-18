"""Transcription backend selector.

faster-whisper is the preferred engine (attention-based, per-token
timestamps). It cannot run everywhere: ctranslate2 has no wheel for some
platform/Python pairs (e.g. Termux ARM64 on Python 3.14). sherpa-onnx ships
as a plain wheel and runs on CPU, so it is the fallback that keeps the
pipeline working on this class of device.

Selection is recorded in the returned provenance so a run never silently
uses a different ASR engine than the operator thinks it did.
"""
from __future__ import annotations

from pathlib import Path

from models.core import Segment


def _faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe(
    media: Path,
    model_name: str = "small",
    language: str | None = None,
    checkpoint_dir: Path | None = None,
    chunk_minutes: int = 5,
    cpu_threads: int | None = None,
    beam_size: int = 1,
) -> tuple[list[Segment], str, dict]:
    """Transcribe with the best engine available on this host.

    Returns (segments, detected_language, provenance). Provenance records
    which engine ran and why, plus the word-timestamp fidelity.
    """
    if _faster_whisper_available():
        from providers.whisper import transcribe as _fw
        segments, lang, prov = _fw(
            media, model_name, language, checkpoint_dir,
            chunk_minutes=chunk_minutes, cpu_threads=cpu_threads, beam_size=beam_size,
        )
        return segments, lang, {
            "engine": "faster-whisper",
            "requested_model": model_name,
            "model": prov["model"],
            # None when the requested model ran all the way through; a reason
            # when it was downgraded mid-run for being too slow.
            "downgrade_reason": prov.get("downgrade_reason"),
            "word_timestamps": "per-token",
            "fallback_reason": None,
            "chunks": prov.get("chunks"),
            "chunking": prov.get("chunking"),
            "num_threads": prov.get("num_threads"),
        }

    from providers.sherpa import transcribe as _so, is_available
    if is_available(model_name):
        segments, lang, prov = _so(
            media, model_name, language, checkpoint_dir,
            chunk_minutes=chunk_minutes, cpu_threads=cpu_threads, beam_size=beam_size,
        )
        return segments, lang, {
            "engine": "sherpa-onnx-whisper",
            "requested_model": model_name,
            "model": prov["model"],
            # "small" is not always installed; sherpa falls back to base, and
            # the run has to be able to say which model actually produced the
            # words and why.
            "downgrade_reason": prov.get("downgrade_reason"),
            # sherpa's whisper export has no cross-attention outputs, so word
            # times are distributed by character proportion within each
            # whisper segment — an approximation, not attention timestamps.
            "word_timestamps": "character-proportional",
            "fallback_reason": "faster-whisper unavailable (no ctranslate2 wheel for this platform)",
            "chunking": prov.get("chunking"),
            "num_threads": prov.get("num_threads"),
        }

    raise RuntimeError(
        "no transcription engine available: install faster-whisper "
        "(pip install -e '.[transcription]'), or install sherpa-onnx "
        "(pip install -e '.[sherpa]') and place a sherpa-onnx whisper model "
        "under ~/.cache/telugify/models/sherpa-onnx-whisper-base"
    )
