"""Audio/Truth Fusion — non-destructive fusion layer.

Creates a DERIVED audio-truth representation by combining:
- Whisper word timestamps (never modified)
- VAD speech/silence regions
- Classified pauses

The original Whisper transcript is preserved as-is. The fusion produces
a new representation used for:
- Improved clip boundaries (VAD-aligned)
- Dead-air detection (silence classification)
- Speech continuity validation
- Hallucination detection (words outside VAD speech regions)

This module NEVER overwrites, mutates, or reorders Whisper truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from models.core import Word
from engine.vad import SpeechRegion, SilenceRegion
from engine.pause_classifier import ClassifiedPause


@dataclass
class FusedWord:
    """Whisper word enriched with VAD/pause context. Original Word preserved."""
    word: Word
    vad_speech: bool          # word is inside a VAD speech region
    leading_silence: float    # seconds of silence before this word
    trailing_silence: float   # seconds of silence after this word
    pause_before: ClassifiedPause | None = None
    pause_after: ClassifiedPause | None = None


@dataclass
class FusedRegion:
    """A speech region enriched with word-level detail."""
    speech: SpeechRegion
    words: list[FusedWord] = field(default_factory=list)
    word_count: int = 0
    total_word_duration: float = 0.0


@dataclass
class AudioTruth:
    """Complete derived audio-truth representation.

    All original Whisper truth is preserved untouched:
    - whisper_words: original Word objects, never modified
    - vad_speech: VAD-derived speech regions (from cached scores)
    - vad_silence: complement of speech regions
    - pauses: classified silence regions

    Derived representations are computed from these but never
    written back to the originals.
    """
    whisper_words: list[Word]
    vad_speech: list[SpeechRegion]
    vad_silence: list[SilenceRegion]
    pauses: list[ClassifiedPause]
    fused_words: list[FusedWord]
    fused_regions: list[FusedRegion]
    duration: float
    vad_backend: str = ""
    vad_score_file: str = ""
    speech_ratio: float = 0.0
    silence_ratio: float = 0.0
    dead_air_ratio: float = 0.0
    hallucinated_count: int = 0


def fuse_audio_truth(
    whisper_words: list[Word],
    vad_speech: list[SpeechRegion],
    vad_silence: list[SilenceRegion],
    pauses: list[ClassifiedPause],
    duration: float,
    vad_backend: str = "",
    vad_score_file: str = "",
) -> AudioTruth:
    """Create the derived audio-truth layer.

    Steps:
    1. Each Whisper word is classified: inside/outside VAD speech region.
    2. Leading/trailing silence around each word computed.
    3. Pause context attached to each word boundary.
    4. Fused regions group words by VAD speech region.
    5. Hallucination count: Whisper words NOT inside any VAD speech region.
    """
    speech_durations = sum(s.end - s.start for s in vad_speech)
    silence_durations = sum(s.end - s.start for s in vad_silence)
    dead_air_secs = sum(p.duration for p in pauses if p.pause_type.value == "dead_air")
    fused_words = _fuse_words(whisper_words, vad_speech, pauses)
    fused_regions = _build_fused_regions(fused_words, vad_speech)
    hallucinated = sum(1 for fw in fused_words if not fw.vad_speech)

    return AudioTruth(
        whisper_words=whisper_words,
        vad_speech=vad_speech,
        vad_silence=vad_silence,
        pauses=pauses,
        fused_words=fused_words,
        fused_regions=fused_regions,
        duration=duration,
        vad_backend=vad_backend,
        vad_score_file=vad_score_file,
        speech_ratio=round(speech_durations / max(0.01, duration), 3),
        silence_ratio=round(silence_durations / max(0.01, duration), 3),
        dead_air_ratio=round(dead_air_secs / max(0.01, duration), 3),
        hallucinated_count=hallucinated,
    )


def _fuse_words(
    words: list[Word],
    speech: list[SpeechRegion],
    pauses: list[ClassifiedPause],
) -> list[FusedWord]:
    pause_map = _build_pause_map(pauses)
    result: list[FusedWord] = []
    for w in words:
        in_speech = _in_region(w.start, speech)
        leading = _silence_before(w.start, speech, pause_map)
        trailing = _silence_after(w.end, speech, pause_map)
        result.append(FusedWord(
            word=w,
            vad_speech=in_speech,
            leading_silence=leading,
            trailing_silence=trailing,
            pause_before=pause_map.get("before", {}).get(_round2(w.start)),
            pause_after=pause_map.get("after", {}).get(_round2(w.end)),
        ))
    return result


def _build_fused_regions(
    fused_words: list[FusedWord], speech: list[SpeechRegion]
) -> list[FusedRegion]:
    """Group fused words by their enclosing VAD speech region."""
    regions: list[FusedRegion] = []
    word_idx = 0
    sorted_words = sorted(fused_words, key=lambda fw: fw.word.start)

    for sr in speech:
        region_words: list[FusedWord] = []
        while word_idx < len(sorted_words) and sorted_words[word_idx].word.end <= sr.end + 0.05:
            if sorted_words[word_idx].word.start >= sr.start - 0.05:
                region_words.append(sorted_words[word_idx])
            word_idx += 1
        total_dur = sum(fw.word.end - fw.word.start for fw in region_words)
        regions.append(FusedRegion(
            speech=sr,
            words=region_words,
            word_count=len(region_words),
            total_word_duration=round(total_dur, 3),
        ))
    return regions


def _in_region(t: float, regions: list[SpeechRegion]) -> bool:
    return any(s.start - 0.05 <= t <= s.end + 0.05 for s in regions)


def _silence_before(t: float, speech: list[SpeechRegion], pause_map: dict) -> float:
    """Leading silence = time from previous speech region end to word start."""
    prev_end = 0.0
    for s in speech:
        if s.start > t + 0.01:
            break
        prev_end = max(prev_end, s.end)
    return round(max(0.0, t - prev_end), 3)


def _silence_after(t: float, speech: list[SpeechRegion], pause_map: dict) -> float:
    """Trailing silence = time from word end to next speech region start."""
    for s in speech:
        if s.start >= t - 0.01:
            return round(max(0.0, s.start - t), 3)
    return 0.0


def _build_pause_map(pauses: list[ClassifiedPause]) -> dict:
    before: dict[float, ClassifiedPause] = {}
    after: dict[float, ClassifiedPause] = {}
    for p in pauses:
        before[round(p.start, 2)] = p
        after[round(p.end, 2)] = p
    return {"before": before, "after": after}


def _round2(t: float) -> float:
    return round(t, 2)
