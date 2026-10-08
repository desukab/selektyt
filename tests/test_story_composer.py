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


def _declining_pool(n: int = 18, slope: float = 0.05):
    """A source whose moments get steadily weaker, which is what a real one
    looks like. Uniform pools are the degenerate case: when every moment is
    equally valuable, no goal should truncate, because the only thing a
    shorter program loses is material."""
    return [
        candidate(start=i * 10, text=f"moment {i} topic {i % 6}",
                  score=max(0.25, 0.9 - i * slope))
        for i in range(n)
    ]


def _duration(cands):
    return sum(max(0.0, c.end - c.start) for c in cands)


def test_a_make_goal_shortens_the_program_a_goalless_make_does_not():
    """The duration goal has to reach the *choice*, not just the score.

    ``--make`` is the only duration control the CLI exposes, and for a long time
    the term was a constant offset in practice: it shifted every variant by the
    same amount and never reordered them, so ``make=short`` and ``make=story``
    shipped byte-identical programs. A preference that cannot change a decision
    is not a preference.

    On a declining pool the goalless make takes every moment, and asking for a
    short has to return a shorter program than that. The assertion is the
    comparison rather than an absolute duration, because the goal competes with
    narrative value rather than overriding it — the exact number has to move
    with how good the material is.
    """
    from engine.story_composer import compose_story

    pool = _declining_pool()
    goalless = compose_story(pool, corpus=None, limit=24, max_moments=12)
    short = compose_story(
        pool, corpus=None, limit=24, max_moments=12, target_seconds=60.0
    )

    assert _duration(short.candidates) < _duration(goalless.candidates), (
        f"make=short shipped {_duration(short.candidates)}s against the "
        f"goalless make's {_duration(goalless.candidates)}s, so the 60 s goal "
        "reaches the composer's scoring but never its choice")


def test_a_make_goal_does_not_truncate_a_source_whose_moments_are_all_strong():
    """The other half of the contract, and the reason the goal is a preference.

    A uniform pool is the case where every additional moment is worth its cost,
    so truncating it would only destroy evidence. Imposing a 60 s program here
    would be exactly the arbitrary runtime cap the product rejects; the goal has
    to lose to the material when the material is uniformly good.
    """
    from engine.story_composer import compose_story

    pool = _declining_pool(slope=0.0)
    plan = compose_story(
        pool, corpus=None, limit=24, max_moments=12, target_seconds=60.0
    )

    assert len(plan.candidates) == 12, (
        f"a source of uniformly strong moments was truncated to "
        f"{len(plan.candidates)} by a 60 s goal; the goal is a tie-breaker "
        "against narrative value and should not override it")


def test_an_overshoot_inside_the_terms_old_dead_zone_now_costs_points():
    """The specific defect this replaces.

    The earlier form gave a flat ``-0.08`` past a ratio of 1.80 and nothing
    between 1.15 and 1.80. A 90 s program against a 60 s goal is ratio 1.5 --
    squarely inside that dead zone -- so it scored exactly as if no goal had
    been passed at all. This is the band the shipped construction actually
    lands in, so a goal that does nothing there does nothing in practice. The
    check scores one construction directly, because what is being pinned is the
    arithmetic of the term rather than the search's response to it.
    """
    from engine.story_composer import _features, _index, _whole_score

    pool = _declining_pool(slope=0.0)
    construction = pool[:9]
    features = _index(pool, None)

    with_goal = _whole_score(
        construction, None, features, "chronological", target_seconds=60.0
    )
    without_goal = _whole_score(
        construction, None, features, "chronological", target_seconds=None
    )

    assert _duration(construction) == 90.0, "the fixture drifted off ratio 1.5"
    assert with_goal < without_goal, (
        f"a 90 s program against a 60 s goal (ratio 1.5) scores "
        f"{with_goal:.4f} with the goal and {without_goal:.4f} without it, so "
        "the goal has no effect anywhere in the 1.15-1.80 band the shipped "
        "construction lands in"
    )


def test_a_story_goal_does_not_penalise_a_source_shorter_than_the_target():
    """Undershoot must not cost points.

    A source that only contains 115 s of story sits at 0.64 of a 180 s target.
    Charging that overshoot-in-reverse would push the composer to pad the
    program with weaker moments to reach a runtime the evidence does not
    support, which inverts the product rule that duration emerges from
    narrative evidence. The band's reward already declines to zero at 0.70,
    which is the whole signal a short source needs.
    """
    from engine.story_composer import _index, _whole_score

    pool = _declining_pool(slope=0.0)
    construction = pool[:8]
    features = _index(pool, None)

    with_goal = _whole_score(
        construction, None, features, "chronological", target_seconds=180.0
    )
    without_goal = _whole_score(
        construction, None, features, "chronological", target_seconds=None
    )

    assert with_goal == without_goal, (
        f"a program shorter than its target is penalised {without_goal - with_goal:.4f} "
        "points for undershooting, which rewards padding a short source with "
        "weaker moments"
    )
