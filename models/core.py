from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

@dataclass
class Word:
    text: str
    start: float
    end: float
    confidence: float = 1.0

@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word]

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

@dataclass
class Candidate:
    start: float
    end: float
    text: str
    story_type: str
    hook: str
    payoff: str
    editorial_score: float
    virality_score: float
    context_score: float
    confidence: float
    reason: str
    visual_potential: float = 0.0

    @property
    def total_score(self) -> float:
        return 0.42 * self.editorial_score + 0.28 * self.virality_score + 0.20 * self.context_score + 0.10 * self.visual_potential

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_score"] = round(self.total_score, 4)
        return d
