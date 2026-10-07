from types import SimpleNamespace

from engine.story_composer import compose_story, compose_story_variants
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
    # ``_features`` reads ``text`` and ``topic_id`` off every unit a candidate
    # overlaps, so the units have to carry both or the composer dies on the
    # first lookup — the three tests above were all failing here, which meant
    # the behaviour they name was never actually run.
    units = [
        SimpleNamespace(index=0, start=0, end=10, thread_id=0, beat_id=0,
                        topic_id=0, text="Here is how the problem started."),
        SimpleNamespace(index=1, start=20, end=30, thread_id=0, beat_id=1,
                        topic_id=0, text="Then everything went wrong."),
        SimpleNamespace(index=2, start=40, end=50, thread_id=0, beat_id=2,
                        topic_id=0, text="And this is how we finally solved it."),
    ]
    beats = [
        SimpleNamespace(kind="setup"),
        SimpleNamespace(kind="tension"),
        SimpleNamespace(kind="resolution"),
    ]
    link = SimpleNamespace(setup_unit=0, payoff_unit=2, confidence=0.8,
                           kind="question_answer")
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

    plan = compose_story(candidates, corpus=corpus, limit=3, max_moments=3)

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


def test_story_length_can_exceed_the_social_output_budget():
    corpus = corpus_for_story()
    candidates = [
        candidate(0, "The setup begins here."),
        candidate(20, "The first complication appears."),
        candidate(40, "The pressure keeps building."),
        candidate(60, "A useful turn changes the direction."),
        candidate(80, "The key revelation arrives."),
        candidate(100, "The final answer resolves the problem."),
        candidate(120, "An extra supporting detail."),
    ]

    plan = compose_story(
        candidates,
        corpus=corpus,
        limit=3,
        max_moments=6,
        target_seconds=60,
    )

    assert len(plan.candidates) > 3


def test_composer_returns_ranked_story_variants():
    candidates = [
        candidate(0, "We started with a difficult problem."),
        candidate(20, "Then something unexpected happened."),
        candidate(40, "The answer finally became clear."),
    ]

    variants = compose_story_variants(candidates, max_moments=3)

    assert len(variants) == 4
    assert variants[0].score >= variants[-1].score
    assert {p.style for p in variants} == {
        "chronological", "cold_open", "mystery", "payoff_first"
    }


def test_composer_prefers_a_connected_arc_over_an_unrelated_high_score_clip():
    corpus = corpus_for_story()
    candidates = [
        candidate(0, "Here is how the problem started.", score=0.72),
        candidate(20, "Then everything went wrong.", score=0.72),
        candidate(40, "And this is how we finally solved it.", score=0.72),
        candidate(80, "An unrelated but extremely catchy sentence.", score=0.99),
    ]

    plan = compose_story(
        candidates,
        corpus=corpus,
        limit=3,
        max_moments=3,
    )

    # The claim is membership, not order. The composer searches four
    # constructions and three of them legitimately open mid-story, so pinning
    # [0, 20, 40] would pin one construction and fail the other three for
    # telling the same material a different way — which is exactly what the
    # constructions exist to do. What has to hold is that the arc ships and the
    # catchy sentence does not: it scores 0.99 against the arc's 0.72 per clip,
    # and a composer that shipped it would be choosing moments, not a story.
    assert {c.start for c in plan.candidates} == {0, 20, 40}, (
        "the connected arc ships, not the unrelated high-scoring clip")
    assert 80 not in {c.start for c in plan.candidates}, (
        "the unrelated clip was preferred over the arc's own members")
    assert len(plan.candidates) == 3
