from __future__ import annotations
from models.core import Candidate


def virality_angle(c: Candidate) -> dict:
    score = c.virality_score
    if score >= 0.85:
        angle = "high-curiosity"
    elif score >= 0.70:
        angle = "revelation"
    else:
        angle = "useful-insight"
    return {
        "angle": angle,
        "curiosity": round(min(1.0, c.virality_score + 0.05), 3),
        "stakes": round(c.editorial_score, 3),
        "tension": round(c.virality_score, 3),
        "payoff": c.payoff,
        "truth_confidence": c.confidence,
    }
