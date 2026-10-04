"""Chunked BYOK cloud transcription normalized to ``models.core.Segment``."""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Callable

from models.core import Segment, Word
from providers.atomic import write_json_atomic
from providers.whisper import _duration, _extract_audio

CHUNK_OVERLAP_SECONDS = 8
GROQ_MODEL = "whisper-large-v3"
GEMINI_MODEL = "gemini-3.5-transcribe"
_LANGUAGE_NAMES = {
    "english": "en", "hindi": "hi", "telugu": "te", "tamil": "ta",
    "bengali": "bn", "marathi": "mr", "kannada": "kn", "malayalam": "ml",
    "gujarati": "gu", "punjabi": "pa", "spanish": "es", "french": "fr",
    "german": "de", "italian": "it", "portuguese": "pt", "japanese": "ja",
    "korean": "ko", "chinese": "zh", "arabic": "ar", "russian": "ru",
}


def _value(obj, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _seconds(value) -> float | None:
    if value is None:
        return None
    if hasattr(value, "total_seconds"):
        return float(value.total_seconds())
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("s"):
            text = text[:-1]
        try:
            return float(text)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _language_code(value) -> str:
    raw = str(value or "unknown").strip()
    return _LANGUAGE_NAMES.get(raw.casefold(), raw)


def _word_from_groq(item, offset: float) -> Word | None:
    text = str(_value(item, "word", "") or _value(item, "text", "")).strip()
    start = _seconds(_value(item, "start"))
    end = _seconds(_value(item, "end"))
    if not text or start is None or end is None or end <= start:
        return None
    confidence = _value(item, "probability", 1.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 1.0
    return Word(text, start + offset, end + offset, confidence)


def _groq_segments(response, offset: float, core_start: float, core_end: float) -> list[Segment]:
    result: list[Segment] = []
    for item in _value(response, "segments", []) or []:
        start = _seconds(_value(item, "start"))
        end = _seconds(_value(item, "end"))
        text = str(_value(item, "text", "") or "").strip()
        if start is None or end is None or end <= start or not text:
            continue
        absolute_start, absolute_end = start + offset, end + offset
        midpoint = (absolute_start + absolute_end) / 2
        if midpoint < core_start or midpoint >= core_end:
            continue
        words = [w for raw in (_value(item, "words", []) or [])
                 if (w := _word_from_groq(raw, offset)) is not None]
        result.append(Segment(
            absolute_start, absolute_end, text, words,
            speaker=_value(item, "speaker_id") or _value(item, "speaker"),
        ))
    return result


def _gemini_segments(interaction, offset: float,
                     core_start: float, core_end: float) -> list[Segment]:
    words: list[Word] = []
    speakers: list[str | None] = []
    for step in (_value(interaction, "steps", []) or []):
        for content in (_value(step, "content", []) or []):
            for annotation in (_value(content, "annotations", []) or []):
                if _value(annotation, "type") != "word_info":
                    continue
                text = str(_value(annotation, "text", "") or "").strip()
                start = _seconds(_value(annotation, "start_offset"))
                end = _seconds(_value(annotation, "end_offset"))
                if not text or start is None or end is None or end <= start:
                    continue
                absolute_start, absolute_end = start + offset, end + offset
                midpoint = (absolute_start + absolute_end) / 2
                if core_start <= midpoint < core_end:
                    words.append(Word(text, absolute_start, absolute_end))
                    speakers.append(_value(annotation, "speaker"))
    if not words:
        if not str(_value(interaction, "output_text", "") or "").strip():
            return []  # a genuinely empty/silent chunk
        # The dedicated transcription endpoint returned no time annotations.
        # Do not invent segment or word offsets from chunk length.
        raise RuntimeError(
            "Gemini returned transcript text without the requested word timestamps; "
            "SeleKyT cannot align it safely to the source video."
        )

    result: list[Segment] = []
    group: list[tuple[Word, str | None]] = []
    for word, speaker in zip(words, speakers):
        split = bool(group) and (
            speaker != group[-1][1]
            or word.start - group[-1][0].end > 1.0
            or bool(re.search(r"[.!?][\"')\]]?$", group[-1][0].text))
        )
        if split:
            result.append(_gemini_group(group))
            group = []
        group.append((word, speaker))
    if group:
        result.append(_gemini_group(group))
    return result


def _gemini_group(group: list[tuple[Word, str | None]]) -> Segment:
    words = [x[0] for x in group]
    speaker = group[0][1]
    no_space_scripts = ("CJK", "HIRAGANA", "KATAKANA")
    letters = [char for word in words for char in word.text if char.isalpha()]
    no_spaces = bool(letters) and all(
        any(script in unicodedata.name(char, "") for script in no_space_scripts)
        for char in letters
    )
    return Segment(words[0].start, words[-1].end,
                   ("" if no_spaces else " ").join(word.text for word in words), words,
                   speaker=speaker)


def _groq_request(path: Path, key: str, language: str | None):
    try:
        from groq import Groq
    except ImportError as exc:
        raise RuntimeError(
            "Groq support is not installed. Run `pip install -e '.[cloud-transcription]'`."
        ) from exc
    client = Groq(api_key=key)
    with path.open("rb") as audio:
        return client.audio.transcriptions.create(
            file=(path.name, audio),
            model=GROQ_MODEL,
            response_format="verbose_json",
            timestamp_granularities=["segment", "word"],
            language=language,
            temperature=0,
        )


def _gemini_request(path: Path, key: str, language: str | None):
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "Gemini support is not installed. Run `pip install -e '.[cloud-transcription]'`."
        ) from exc
    client = genai.Client(api_key=key)
    uploaded = None
    try:
        uploaded = client.files.upload(file=str(path))
        language_codes = []
        if language:
            language_codes = [{
                "en": "en-US", "hi": "hi-IN", "te": "te-IN",
                "ta": "ta-IN", "bn": "bn-IN", "mr": "mr-IN",
                "kn": "kn-IN", "ml": "ml-IN", "gu": "gu-IN",
                "pa": "pa-IN", "es": "es-419",
                "pt": "pt-BR", "zh": "cmn-Hans-CN",
            }.get(language.casefold(), language)]
        return client.interactions.create(
            model=GEMINI_MODEL,
            input=[{"type": "audio", "uri": uploaded.uri,
                    "mime_type": uploaded.mime_type}],
            generation_config={
                "transcription_config": {
                    "language_codes": language_codes,
                    "mode": {"type": "verbatim",
                             "timestamp_granularities": ["word"]},
                }
            },
        )
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


def _checkpoint_load(path: Path, signature: str) -> tuple[list[Segment], str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("signature") != signature:
            return None
        segments = [
            Segment(float(s["start"]), float(s["end"]), str(s["text"]),
                    [Word(**w) for w in s.get("words", [])],
                    speaker=s.get("speaker"))
            for s in data.get("segments", [])
        ]
        return segments, str(data.get("language") or "unknown")
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _checkpoint_write(path: Path, signature: str, segments: list[Segment], language: str) -> None:
    write_json_atomic(path, {
        "signature": signature,
        "language": language,
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text,
             "words": [w.__dict__ for w in s.words], "speaker": s.speaker}
            for s in segments
        ],
    })


def transcribe(
    media: Path,
    provider: str,
    api_key: str,
    *,
    language: str | None = None,
    checkpoint_dir: Path | None = None,
    chunk_minutes: int = 5,
    progress: Callable[[dict], None] | None = None,
) -> tuple[list[Segment], str, dict]:
    if provider not in {"groq", "gemini"}:
        raise ValueError(f"unsupported cloud provider: {provider}")
    if chunk_minutes < 1:
        raise ValueError("chunk_minutes must be >= 1")
    model = GROQ_MODEL if provider == "groq" else GEMINI_MODEL
    checkpoint_dir = checkpoint_dir or media.parent / "transcription"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    audio = checkpoint_dir / "audio.wav"
    if not audio.is_file() or audio.stat().st_size < 10_000:
        print("[transcribe] extracting 16 kHz mono audio", flush=True)
        _extract_audio(media, audio)
    total = _duration(audio)
    step = float(chunk_minutes * 60)
    count = max(1, math.ceil(total / step))
    source_stat = media.stat()
    signature = hashlib.sha256(
        f"cloud-canonical-v1|{provider}|{model}|{language or 'auto'}|{chunk_minutes}|"
        f"{CHUNK_OVERLAP_SECONDS}|{total}|{media.resolve()}|{source_stat.st_size}|"
        f"{source_stat.st_mtime_ns}|{audio.stat().st_size}|{audio.stat().st_mtime_ns}"
        .encode()
    ).hexdigest()[:20]
    cache_dir = checkpoint_dir / f"cloud-{provider}-{signature}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Segment] = []
    languages: list[str] = []
    done = 0
    overlap = float(CHUNK_OVERLAP_SECONDS)

    for index in range(count):
        core_start = index * step
        core_end = min(total, core_start + step)
        decode_start = max(0.0, core_start - (overlap if index else 0.0))
        decode_end = min(total, core_end + (overlap if index + 1 < count else 0.0))
        checkpoint = cache_dir / f"chunk_{index:04d}.json"
        cached = _checkpoint_load(checkpoint, signature)
        if cached is not None:
            chunk_segments, detected = cached
            print(f"[transcribe] {provider} chunk {index + 1}/{count}: cached", flush=True)
        else:
            with tempfile.TemporaryDirectory(prefix="selekyt-cloud-") as temp:
                wav = Path(temp) / f"chunk_{index:04d}.wav"
                subprocess.run([
                    "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{decode_start:.3f}",
                    "-i", str(audio), "-t", f"{decode_end - decode_start:.3f}",
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(wav),
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    if provider == "groq":
                        response = _groq_request(wav, api_key, language)
                        chunk_segments = _groq_segments(response, decode_start, core_start, core_end)
                        detected = _language_code(_value(response, "language", "unknown"))
                    else:
                        response = _gemini_request(wav, api_key, language)
                        chunk_segments = _gemini_segments(response, decode_start, core_start, core_end)
                        detected = _language_code(
                            _value(response, "language", None)
                            or _value(response, "detected_language", None)
                            or language or "unknown"
                        )
                except Exception as exc:
                    message = str(exc).replace(api_key, "[redacted]")
                    raise RuntimeError(
                        f"{provider.title()} transcription failed: {message or type(exc).__name__}. "
                        "No local transcript was substituted."
                    ) from None
                _checkpoint_write(checkpoint, signature, chunk_segments, detected)
                print(f"[transcribe] {provider} chunk {index + 1}/{count}: done", flush=True)
        segments.extend(chunk_segments)
        if detected and detected != "unknown":
            languages.append(detected)
        done += 1
        if progress:
            progress({"index": done, "count": count, "eta": None})

    segments.sort(key=lambda s: (s.start, s.end))
    # Neighboring overlapping requests may each emit the same timed phrase if
    # its midpoint is very close to the ownership boundary. Keep the earlier
    # copy only when text and timing both identify the same material.
    merged: list[Segment] = []
    for segment in segments:
        if merged:
            previous = merged[-1]
            same_text = " ".join(previous.text.casefold().split()) == " ".join(segment.text.casefold().split())
            overlap_s = min(previous.end, segment.end) - max(previous.start, segment.start)
            if same_text and overlap_s > 0 and overlap_s / min(previous.duration, segment.duration) >= 0.6:
                continue
        merged.append(segment)
    detected = language or (Counter(languages).most_common(1)[0][0] if languages else "unknown")
    return merged, detected, {
        "provider": provider,
        "engine": "groq-whisper-large-v3" if provider == "groq" else "gemini-3.5-transcribe",
        "requested_model": model,
        "model": model,
        "word_timestamps": "per-word" if provider == "gemini" or any(s.words for s in merged) else "segment-only",
        "fallback_reason": None,
        "downgrade_reason": None,
        "chunks": count,
        "chunking": f"{chunk_minutes} min chunks (+/-{CHUNK_OVERLAP_SECONDS}s overlap)",
        "vad_filter": False,
    }
