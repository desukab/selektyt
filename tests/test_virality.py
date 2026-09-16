"""Virality angle classification — pure function over a Candidate's scores.

Pins the angle thresholds and the field mapping so the editorial report's
hook framing stays in lockstep with the scoring.
"""
from __future__ import annotations

from engine.virality import virality_angle
from models.core import Candidate


def _candidate(virality: float, editorial: float = 0.6, confidence: float = 0.8) -> Candidate:
    return Candidate(
        0.0, 10.0, "the story text", "story", "the hook", "the payoff",
        editorial_score=editorial, virality_score=virality,
        context_score=0.7, confidence=confidence, reason="t",
        visual_potential=0.5, words=None,
    )


def test_high_curiosity_band():
    out = virality_angle(_candidate(0.9))
    assert out["angle"] == "high-curiosity"
    assert out["tension"] == 0.9
    assert out["stakes"] == 0.6


def test_revelation_band():
    assert virality_angle(_candidate(0.74))["angle"] == "revelation"


def test_useful_insight_band():
    assert virality_angle(_candidate(0.5))["angle"] == "useful-insight"


def test_threshold_boundaries():
    assert virality_angle(_candidate(0.85))["angle"] == "high-curiosity"
    assert virality_angle(_candidate(0.84))["angle"] == "revelation"
    assert virality_angle(_candidate(0.70))["angle"] == "revelation"
    assert virality_angle(_candidate(0.69))["angle"] == "useful-insight"


def test_curiosity_is_capped_and_boosted():
    out = virality_angle(_candidate(0.9))
    assert out["curiosity"] == 0.95
    capped = virality_angle(_candidate(0.99))
    assert capped["curiosity"] == 1.0


def test_payoff_and_confidence_pass_through():
    out = virality_angle(_candidate(0.8))
    assert out["payoff"] == "the payoff"
    assert out["truth_confidence"] == 0.8
