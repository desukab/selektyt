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
    coherence: float = 1.0
    completeness: float = 1.0

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
    words: list[Word] | None = None
    coherence_score: float = 1.0
    completeness: float = 1.0
    llm_metadata: dict | None = None

    @property
    def total_score(self) -> float:
        # Coherence carries its own weight: a clip that splices two stories
        # cannot be rescued by density or virality, so it is scored as a
        # first-class term rather than only through the editorial multiplier.
        return (
            0.30 * self.editorial_score
            + 0.22 * self.coherence_score
            + 0.20 * self.virality_score
            + 0.16 * self.context_score
            + 0.12 * self.visual_potential
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_score"] = round(self.total_score, 4)
        return d

    def to_bench_dict(self) -> dict[str, Any]:
        """The lean record for a candidate that passed every gate and lost only
        to the ``--limit`` budget.

        ``to_dict`` carries the word-level timings and any attached LLM
        metadata, which is what a clip needs to be re-rendered. The bench only
        has to answer "is this worth pulling up?", so the word list is dropped:
        a few dozen bench entries then cost about the space of one selection,
        and an editor comparing a bench entry against a shipped clip still sees
        the same scores in the same places.
        """
        d = {k: v for k, v in asdict(self).items()
             if k not in ("words", "llm_metadata")}
        d["total_score"] = round(self.total_score, 4)
        return d
