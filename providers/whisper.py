from __future__ import annotations
from pathlib import Path
from models.core import Segment, Word


def transcribe(media: Path, model_name: str = "small", language: str | None = None) -> tuple[list[Segment], str]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install transcription support: pip install -e '.[transcription]'") from exc

    model = WhisperModel(model_name, device="auto", compute_type="int8")
    segments, info = model.transcribe(str(media), language=language, word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
    out: list[Segment] = []
    for s in segments:
        words = [Word(w.word, float(w.start), float(w.end), float(getattr(w, "probability", 1.0))) for w in (s.words or [])]
        out.append(Segment(float(s.start), float(s.end), s.text.strip(), words))
    return out, info.language
