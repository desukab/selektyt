"""Classify silence regions into semantically meaningful pause types.

The classification is rules-based (no ML), deterministic, and pure —
given the same speech regions + duration + context, output is always
identical. Each silence region becomes a PauseType for editorial
decisions (intentional pauses preserved, dead air removed).

Sentence-boundary detection (priority order):
1. Whisper word terminal punctuation (`.?!।؟。`) — precise, when available
2. Speech region duration heuristic (> 1.5s region ends) — fallback
"""
from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from models.core import Word
from engine.vad import SpeechRegion, SilenceRegion


_TERMINAL_PUNCT = set(".?!।؟。")


class PauseType(str, Enum):
    DEAD_AIR = "dead_air"
    BREATHING = "breathing"
    HESITATION = "hesitation"
    INTENTIONAL = "intentional"
    UNCERTAIN = "uncertain"


@dataclass
class ClassifiedPause:
    start: float
    end: float
    duration: float
    pause_type: PauseType
    confidence: float = 1.0


class PauseClassifier:
    """Rules-based pause classifier.

    Parameters are tuned for conversational/educational content in
    English, Telugu, Hindi, Tamil (Indic scripts). Adjust thresholds
    via constructor kwargs to adapt to other content styles.
    """

    def __init__(
        self,
        dead_air_min: float = 1.5,
        breath_range: tuple[float, float] = (0.15, 0.6),
        intention_range: tuple[float, float] = (0.5, 2.0),
        hesitation_range: tuple[float, float] = (0.2, 0.9),
        punctuation_margin: float = 0.15,
        region_boundary_margin: float = 0.15,
    ):
        self.dead_air_min = dead_air_min
        self.breath_min, self.breath_max = breath_range
        self.intention_min, self.intention_max = intention_range
        self.hesitation_min, self.hesitation_max = hesitation_range
        self.punctuation_margin = punctuation_margin
        self.region_boundary_margin = region_boundary_margin

    def classify(
        self,
        silences: list[SilenceRegion],
        speech: list[SpeechRegion],
        duration: float,
        words: list[Word] | None = None,
    ) -> list[ClassifiedPause]:
        """Classify each silence region.

        When words are provided, terminal punctuation gives precise sentence
        boundaries. Without words, speech region durations (>1.5s) are used
        as a fallback heuristic.
        """
        result: list[ClassifiedPause] = []
        if words:
            boundaries = _punctuation_boundaries(words)
            margin = self.punctuation_margin
        else:
            boundaries = _region_duration_boundaries(speech)
            margin = self.region_boundary_margin

        for sil in silences:
            pause_type, conf = self._classify_one(sil, boundaries, margin)
            result.append(ClassifiedPause(
                start=sil.start, end=sil.end,
                duration=round(sil.duration, 3),
                pause_type=pause_type,
                confidence=round(conf, 2),
            ))
        return result

    def _classify_one(
        self,
        silence: SilenceRegion,
        boundaries: set[float],
        margin: float,
    ) -> tuple[PauseType, float]:
        d = silence.duration

        if d >= self.dead_air_min:
            return PauseType.DEAD_AIR, 0.95

        near_start = _near_boundary(silence.start, boundaries, margin)
        near_end = _near_boundary(silence.end, boundaries, margin)
        at_boundary = near_start or near_end

        if at_boundary and self.breath_min <= d <= self.breath_max:
            return PauseType.BREATHING, 0.85

        if not at_boundary and self.intention_min <= d <= self.intention_max:
            return PauseType.INTENTIONAL, 0.70

        if not at_boundary and self.hesitation_min <= d <= self.hesitation_max:
            return PauseType.HESITATION, 0.60

        if at_boundary:
            return PauseType.BREATHING, 0.55

        return PauseType.UNCERTAIN, 0.40


def _punctuation_boundaries(words: list[Word]) -> set[float]:
    """Detect sentence boundaries from Whisper word terminal punctuation.

    Marks both sentence ends (word ending with terminal punctuation) and
    sentence starts (word beginning immediately after a terminal-punctuation
    word), giving the pause classifier two-sided context for breathing
    detection.
    """
    boundaries: set[float] = set()
    prev_ended_with_punct = False
    for w in words:
        text = w.text.rstrip()
        if prev_ended_with_punct and w.start is not None:
            boundaries.add(round(w.start, 2))
        if text and text[-1] in _TERMINAL_PUNCT:
            boundaries.add(round(w.end, 2))
            prev_ended_with_punct = True
        else:
            prev_ended_with_punct = False
    return boundaries


def _region_duration_boundaries(speech: list[SpeechRegion]) -> set[float]:
    """Fallback: regions > 1.5s ending are approximate sentence boundaries."""
    boundaries: set[float] = set()
    for s in speech:
        if s.end - s.start > 1.5:
            boundaries.add(round(s.end, 2))
    return boundaries


def _near_boundary(t: float, boundaries: set[float], margin: float) -> bool:
    return any(abs(t - b) <= margin for b in boundaries)


def classify_all_pauses(
    silences: list[SilenceRegion],
    speech: list[SpeechRegion],
    duration: float,
    words: list[Word] | None = None,
) -> list[ClassifiedPause]:
    return PauseClassifier().classify(silences, speech, duration, words)
