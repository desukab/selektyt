from types import SimpleNamespace

from engine.story_composer import compose_story
from models.core import Candidate


def candidate(start, text, score=0.8):
    return Candidate(
        start=start,
        end=start + 10,
        text=text,
        story_type="story",
        hook=text,
        payoff=text,
        editorial_score=score,
        virality_score=score,
        context_score=score,
        confidence=0.9,
        reason="test",
        coherence_score=score,
        visual_potential=score,
        opening_score=score,
    )


def corpus_for_story():
    units = [
        SimpleNamespace(index=0, start=0, end=10, thread_id=0, beat_id=0),
        SimpleNamespace(index=1, start=20, end=30, thread_id=0, beat_id=1),
        SimpleNamespace(index=2, start=40, end=50, thread_id=0, beat_id=2),
    ]
    beats = [
        SimpleNamespace(kind="setup"),
        SimpleNamespace(kind="tension"),
        SimpleNamespace(kind="resolution"),
    ]
    link = SimpleNamespace(setup_unit=0, payoff_unit=2)
    thread = SimpleNamespace(
        thread_id=0,
        title="test story",
        unit_indices=[0, 1, 2],
        links=[link],
    )
    return SimpleNamespace(units=units, beats=beats, threads=[thread])


def test_composer_can_build_a_story_from_more_than_the_final_budget():
    corpus = corpus_for_story()
    candidates = [
        candidate(0, "Here is how the problem started."),
        candidate(20, "Then everything went wrong."),
        candidate(40, "And this is how we finally solved it."),
        candidate(80, "A separate unrelated moment."),
    ]

    plan = compose_story(candidates, corpus=corpus, limit=3)

    assert len(plan.candidates) == 3
    assert {c.start for c in plan.candidates} >= {0, 40}
    assert plan.style in {"chronological", "cold_open", "mystery", "payoff_first"}
    assert plan.score > 0


def test_composer_searches_multiple_editorial_constructions():
    candidates = [
        candidate(0, "We started with a difficult problem."),
        candidate(20, "Then we discovered something unexpected."),
        candidate(40, "The answer finally became clear."),
    ]

    plan = compose_story(candidates, corpus=None, limit=3)

    assert len(plan.candidates) == 3
    assert any("compared" in reason for reason in plan.reasons)
