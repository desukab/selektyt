"""Content-pack title variants must stay extractive.

The 'no fabricated claims' policy requires every title to trace to spoken
source words, so no boilerplate frame ("the part worth knowing") may appear as
a variant. These pin that contract.
"""
from __future__ import annotations

from engine.content_pack import _densest_phrase, build_content_pack
from models.core import Candidate


def _candidate(text: str, hook: str = "", payoff: str = "") -> Candidate:
    return Candidate(
        0.0, 10.0, text, "story", hook, payoff,
        editorial_score=0.8, virality_score=0.7, context_score=0.9,
        confidence=0.9, reason="t", visual_potential=0.8,
    )


def test_titles_are_all_extractive_from_source():
    text = "We shipped the feature on Tuesday, and the metric moved 40% overnight."
    pack = build_content_pack(_candidate(text))
    titles = pack["titles"]
    assert titles, "at least one title"
    for t in titles:
        # every title is a substring span of the source text (case-insensitive)
        assert t.strip().lower() in text.lower(), f"fabricated: {t!r}"


def test_no_boilerplate_variants():
    text = "The launch went well; users loved the new editor."
    pack = build_content_pack(_candidate(text, story_type="revelation") if False else _candidate(text))
    banned = {"the detail most people miss", "what actually happened",
              "the question behind the story", "the part worth knowing"}
    got = {t.strip().lower() for t in pack["titles"]}
    assert not (got & banned)


def test_hook_and_payoff_preferred_when_present():
    text = "Body of the story, fairly long and unremarkable on its own."
    pack = build_content_pack(_candidate(text, hook="The hook line", payoff="The payoff line"))
    assert pack["titles"][0] == "The hook line"
    assert pack["titles"][1] == "The payoff line"


def test_densest_clause_picks_information_rich_span():
    text = (
        "So, anyway, we were talking about the roadmap for a while, "
        "and the team shipped three features before the deadline."
    )
    phrase = _densest_phrase(text)
    assert phrase
    assert phrase.lower() in text.lower()
    assert "team shipped three features" in phrase


def test_densest_phrase_ignores_one_word_clauses():
    text = "Anyway, the deployment succeeded on the third attempt."
    phrase = _densest_phrase(text)
    assert phrase.lower() in text.lower()
    assert "deployment" in phrase.lower()


def test_densest_phrase_empty_on_empty_text():
    assert _densest_phrase("") == ""
    assert _densest_phrase("   ") == ""


def test_pack_declares_source_based_policy():
    pack = build_content_pack(_candidate("A short story about shipping."))
    assert pack["source_based"] is True
    assert "fabricated" in pack["policy"]