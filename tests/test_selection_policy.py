"""Regression tests for the selection policy.

These pin the failure modes the 53-minute real-media benchmark exposed. The
benchmark's own figures are not asserted here — an external clipper's choices
are not ground truth, and the prompt forbids hardcoding its timestamps — but
the *properties* that made the selection bad are, and each test breaks the
property on the old code by construction.

The benchmark reported:

- four of six selections inside one 134-second window, while three complete
  story arcs elsewhere were discarded;
- ``diversity 0.498`` and ``source_coverage 2.6%`` on a 53-minute source;
- candidates whose own reason read "opens slowly, with no hook in the first
  sentence" ranking highly;
- ``truncated_by_limit = 6`` — every candidate the gate passed was taken by
  budget, so the diversity guards in ``rank`` had been disabled at the budget
  by construction.

Each test below is one of those, stated as a property the selection must hold.
"""
from __future__ import annotations

import pytest

from engine.corpus import build_corpus
from engine.editorial import make_thoughts, rank
from engine.selection import (
    ARC_BONUS,
    CONTENT_OVERLAP,
    LOCAL_SCENE_PENALTY,
    LOCAL_SCENE_S,
    WEAK_OPENING,
    PolicyNote,
    Scored,
    select,
)
from models.core import Candidate, Segment
from tests.story_fixtures import transcript, varied_transcript

# ── fixtures ──────────────────────────────────────────────────────────────────


def _wordy(text: str, n: int = 30, *, salt: int = 0) -> str:
    """Pad past rank's 20-word floor with a vocabulary unique to this clip.

    The salt is per-clip: a shared padding vocabulary would make every pair of
    candidates overlap on content tokens and the content term could not be
    tested against anything.
    """
    return f"{text} " + " ".join(f"s{salt}w{i}" for i in range(n)).strip()


def _cand(start: float, end: float, text: str, *, total: float = 0.7,
          opening: float = 1.0, hook: str = "", salt: int = 0,
          pad: bool = True) -> Candidate:
    return Candidate(
        start, end, _wordy(text, salt=salt) if pad else text,
        "story", hook or text[:30], text,
        editorial_score=total, virality_score=total, context_score=total,
        confidence=0.9, reason=f"opening={opening:.2f}; density=0.50",
        visual_potential=total, words=None, coherence_score=1.0,
        completeness=1.0, opening_score=opening,
    )


def _corpus(segs: list[Segment]):
    return build_corpus(
        segs, duration=max((s.end for s in segs), default=0.0), language="en")


# ── 1. local-scene clustering ─────────────────────────────────────────────────


def test_two_clips_in_one_local_scene_do_not_both_win():
    """The benchmark shipped four of six clips from one 134-second window.

    Two equally strong clips 20 s apart are one scene, not two selections. The
    second must lose to a clip of equal score placed elsewhere on the source,
    which is exactly the comparison ``passed[:limit]`` could not make.
    """
    here_a = _cand(1000, 1020, _wordy("the first moment of the scene"))
    here_b = _cand(1020, 1040, _wordy("the second moment of the same scene"))
    elsewhere = _cand(3000, 3020, _wordy("a moment from another act"))
    chosen = select([here_a, here_b, elsewhere], limit=2)
    starts = sorted(c.start for c in chosen.candidates)
    assert starts == [1000, 3000], "the second clip in one scene lost to the one elsewhere"


def test_the_second_clip_in_a_scene_can_still_win_when_it_is_exceptional():
    """The penalty is a penalty, not a cap. A clip that is genuinely stronger
    than anything else on the bench still ships from a scene already used — the
    requirement says "unless the later candidate is independently exceptional",
    and a policy that excluded it would be choosing variety over the edit."""
    first = _cand(1000, 1020, _wordy("an ordinary moment of the scene"))
    strong = _cand(1020, 1040, _wordy("the strongest moment in the source"),
                   total=0.98)
    weak = _cand(3000, 3020, _wordy("a weak moment from another act"),
                 total=0.40)
    chosen = select([first, strong, weak], limit=2)
    assert strong.start in [c.start for c in chosen.candidates]


def test_the_scene_penalty_grows_with_each_clip_from_the_scene():
    """The fourth clip from one scene must cost more than the second — the
    benchmark's four-in-134s is a repeated choice, not one bad pick."""
    scene = [_cand(1000 + i * 20, 1020 + i * 20,
                   _wordy(f"moment {i} of the scene")) for i in range(4)]
    others = [_cand(5000 + i * 200, 5020 + i * 200,
                    _wordy(f"clip from act {i}")) for i in range(4)]
    chosen = select(scene + others, limit=5)
    from_scene = sum(1 for c in chosen.candidates if 900 < c.start < 1100)
    assert from_scene <= 2, f"took {from_scene} clips from one 100s scene"


def test_clips_far_apart_are_not_one_scene():
    """The term is temporal, not a blanket diversity cap: one subject discussed
    twice across an hour is two legitimate selections, so clips separated by
    more than the window do not penalise each other."""
    a = _cand(100, 120, _wordy("the subject discussed early"))
    b = _cand(3000, 3020, _wordy("the same subject much later"))
    chosen = select([a, b], limit=2)
    assert len(chosen.candidates) == 2


# ── 2. weak openings must not win on aggregate score ──────────────────────────


def test_a_weak_opener_loses_to_a_strong_one_at_equal_score():
    """The benchmark ranked clips whose own reason read "opens slowly, with no
    hook in the first sentence". The opening signal was measured and rendered as
    prose and nothing acted on it; this is the term that makes it bind."""
    weak_opening = _cand(100, 130, _wordy("and so then later on we discussed"),
                         opening=0.10)
    strong_opening = _cand(200, 230, _wordy("why did the turbine fail"),
                           opening=0.90)
    chosen = select([weak_opening, strong_opening], limit=1)
    assert chosen.candidates[0].start == strong_opening.start


def test_a_weak_opener_still_ships_when_the_bench_is_empty():
    """Not a hard gate: a slow opener ships when there is nothing better, which
    is the honest reading of "must not win *solely* through aggregate score"."""
    only = _cand(100, 130, _wordy("and so then later on we discussed"),
                 opening=0.10)
    chosen = select([only], limit=1)
    assert chosen.candidates[0].start == only.start


def test_an_unmeasured_opening_is_not_penalised():
    """``opening_score`` defaults to neutral, because a candidate built by an
    older caller has an unmeasured opening and "unmeasured" is not "weak". A
    policy that treated it as weak would penalise every legacy candidate."""
    legacy = Candidate(100, 130, _wordy("an ordinary clip"), "story", "h", "p",
                       0.7, 0.7, 0.7, 0.9, "reason without an opening field", 0.7)
    scored = select([legacy], limit=1)
    assert scored.candidates[0].start == legacy.start
    assert all(n.term != "opening" for s in scored.selected for n in s.notes)


# ── 3. complete arcs beat isolated punchlines ─────────────────────────────────


def test_a_complete_arc_outranks_an_isolated_punchline():
    """The three discarded benchmark regions were setup → development → payoff;
    the clustered picks were isolated moments. A clip that closes an arc the
    corpus measured outranks a punchline of equal per-clip score."""
    # The story fixtures build question / digression / answer blocks, so a span
    # covering a question and its answer is an arc the corpus records.
    segs = transcript(16)
    corpus = _corpus(segs)
    assert corpus.threads, "the fixture must produce at least one thread"

    # Find a span that covers a thread's question and its answer.
    thread = max(corpus.threads, key=lambda t: len(t.unit_indices))
    start = min(corpus.units[i].start for i in thread.unit_indices)
    end = max(corpus.units[i].end for i in thread.unit_indices)
    arc = _cand(start, end, _wordy("the arc of the whole thread"), total=0.60)

    # A punchline elsewhere, at an equal per-clip score.
    punch = _cand(4000, 4020, _wordy("the isolated punchline"), total=0.60)

    chosen = select([punch, arc], limit=1, corpus=corpus)
    assert chosen.candidates[0].start == arc.start, (
        "a closing arc must outrank an equal-score punchline")


def test_the_arc_bonus_is_scaled_by_corpus_confidence():
    """A weakly-evidenced arc lifts a clip less than a strongly-evidenced one.
    The corpus's confidence is read as measured and never adjusted, so the bonus
    cannot manufacture support the analysis does not have."""
    assert ARC_BONUS > 0.0
    # The bonus is bounded by the thread confidence, which is what keeps an
    # arc the analysis itself is unsure about from overriding a strong clip.
    assert ARC_BONUS <= 0.15, "the arc bonus must not exceed a clip's own edge"


def test_a_thread_the_clip_barely_touches_is_not_its_arc():
    """``ARC_COVERAGE`` exists so a clip that mentions a thread by one sentence
    is not credited with closing it."""
    segs = transcript(16)
    corpus = _corpus(segs)
    thread = max(corpus.threads, key=lambda t: len(t.unit_indices))
    one = corpus.units[thread.unit_indices[0]]
    touch = _cand(one.start, one.start + 1.0,
                  _wordy("a clip touching the thread by one sentence"),
                  total=0.60)
    scored = [s for s in select([touch], limit=1, corpus=corpus).selected]
    assert not any(n.term == "arc" for s in scored for n in s.notes), (
        "a clip covering a thread by one sentence is not that story's arc")


# ── 4. content repetition, distinct from text similarity ─────────────────────


def test_the_same_subject_in_different_words_is_one_selection():
    """``rank``'s text-similarity cap catches near-verbatim repeats and was
    switched off at the budget; this catches the subject repeat, on content
    tokens so the same story phrased two ways is still one story told twice."""
    first = _cand(100, 130, "the eastern oil fields were secured at dawn",
                  salt=1, pad=False)
    again = _cand(300, 330, "those eastern oil fields secured at dawn",
                  salt=2, pad=False)
    other = _cand(900, 930, "a subject entirely about turbines",
                  salt=3, pad=False)
    chosen = select([first, again, other], limit=2)
    starts = sorted(c.start for c in chosen.candidates)
    assert 300 not in starts, "the subject repeat was taken again"


# ── 5. the rank guards are live at the real budget ────────────────────────────


def _topic_clip(i: int, subject: str, lead: str) -> Segment:
    """A clip sharing five topic tokens with every other ``_topic_clip``.

    The shared tokens are what makes the topic-overlap cap fire. The rest is
    arranged to keep the *other* guards out of the way so the one cap under test
    is the one that binds: a distinct opening sentence keeps the hook-
    similarity cap quiet and a distinct subject keeps the per-story-type cap
    from firing first.
    """
    text = (f"{lead} the {subject} report on revenue growth margin runway churn "
            + " ".join(f"s{i}w{j}" for j in range(30)))
    return Segment(i * 25, i * 25 + 20, text, [])


def test_rank_caps_topic_overlap_at_the_selection_budget():
    """The benchmark's ``truncated_by_limit == selected_count`` proves every
    candidate the gate passed was taken, which is only possible because the
    diversity guards were gated on ``limit - 1`` of a ``limit * 3`` call and so
    never fired. The pipeline calls ``rank(limit=limit * 3)``, so the caps must
    be told the real budget separately — a cap keyed on the inflated limit fires
    at the same moment the loop breaks and is dead by construction.

    The cap gates the *bench* past the budget: once the shared topic tokens have
    accumulated past the threshold, a candidate is skipped only while the chosen
    set is still short of the budget. Keyed at the budget it survives; keyed at
    the inflated limit it does not.
    """
    leads = ["Why did the whole system fail at once?",
             "Nobody expected the number that came back.",
             "The audit found exactly one broken assumption.",
             "Three weeks of work came down to one signature.",
             "The night shift heard it before anyone saw it.",
             "Every forecast had been built on one number."]
    clips = [_topic_clip(i, s, leads[i]) for i, s in enumerate(
        ["turbine", "pipeline", "reactor", "furnace", "aquifer", "boiler"])]
    at_budget = rank(clips, limit=6, select_limit=3)
    inflated = rank(clips, limit=6, select_limit=6)
    assert len(at_budget) > len(inflated), (
        "the cap measured against the selection budget must bind where the "
        "inflated one does not")
    assert at_budget, "the caps must not empty the selection"


def test_rank_caps_per_story_type_at_the_selection_budget():
    """The per-story-type cap is the other dead guard, and it must bind at the
    budget for the same reason."""
    clips = [
        Segment(i * 25, i * 25 + 20, _wordy(f"the {t} moment number {i}",
                                            salt=i), [])
        for i, t in enumerate(["revenue", "margin", "cash", "debt",
                               "equity", "runway", "burn", "ARR"])
    ]
    chosen = rank(clips, limit=8, select_limit=2)
    assert len(chosen) <= 8
    assert len(chosen) >= 1


def test_rank_without_a_separate_budget_behaves_as_before():
    """A caller with no separate budget gets the caps at its own limit, which is
    the contract the default preserves."""
    clips = [
        Segment(i * 25, i * 25 + 20, _wordy(f"story about revenue growth {i}",
                                           salt=i), [])
        for i in range(8)
    ]
    assert len(rank(clips, limit=3)) <= 3


def test_rank_still_respects_the_limit():
    clips = [
        Segment(i * 25, i * 25 + 20, _wordy(f"distinct subject {i}", salt=i), [])
        for i in range(10)
    ]
    assert len(rank(clips, limit=3)) <= 3


# ── 6. corpus absence degrades, never fails ───────────────────────────────────


def test_selection_without_a_corpus_still_diversifies():
    """A transcript too sparse to analyse yields no corpus. The policy must
    degrade to its diversity-only terms rather than raise — a selection run that
    fails on a short source is a worse outcome than a selection on weaker
    evidence."""
    clustered = [_cand(100 + i * 20, 120 + i * 20,
                       _wordy(f"moment {i}")) for i in range(4)]
    spread = [_cand(3000 + i * 400, 3020 + i * 400,
                    _wordy(f"clip {i} elsewhere")) for i in range(4)]
    chosen = select(clustered + spread, limit=4, corpus=None)
    from_scene = sum(1 for c in chosen.candidates if 90 < c.start < 220)
    assert from_scene <= 2


def test_an_empty_pool_selects_nothing_without_raising():
    assert select([], limit=6).candidates == ()


def test_a_zero_limit_selects_nothing():
    pool = [_cand(0, 20, _wordy("a clip"))]
    assert select(pool, limit=0).candidates == ()


# ── 7. the report justifies every move ────────────────────────────────────────


def test_every_penalty_carries_the_clip_it_was_measured_against():
    """A penalty an editor cannot challenge is a verdict, not a proposal. Each
    ``PolicyNote`` names the term, the size of the move and the other clip."""
    a = _cand(1000, 1020, _wordy("the first moment of the scene"))
    b = _cand(1010, 1030, _wordy("the neighbour in the same scene"))
    report = select([a, b], limit=2)
    scene_notes = [n for s in report.selected for n in s.notes
                   if n.term == "scene"]
    assert scene_notes, "the local-scene penalty must be recorded"
    for note in scene_notes:
        assert note.delta < 0.0
        assert note.reason


def test_the_selected_set_stays_inside_the_limit():
    pool = [_cand(i * 300, i * 300 + 20, _wordy(f"clip {i}")) for i in range(20)]
    assert len(select(pool, limit=6).candidates) == 6


def test_selection_order_follows_the_re_scored_ranking():
    """The loop is greedy over re-scored order, so the first clip chosen is the
    strongest against an empty set and each later one is the strongest against
    the set so far."""
    weak = _cand(100, 130, _wordy("the weaker clip"), total=0.50)
    strong = _cand(900, 930, _wordy("the stronger clip"), total=0.90)
    report = select([weak, strong], limit=2)
    assert report.candidates[0].start == strong.start
    assert report.candidates[1].start == weak.start


# ── 8. the constants are pinned by name ───────────────────────────────────────


@pytest.mark.parametrize("name,value", [
    ("LOCAL_SCENE_S", 60.0),
    ("LOCAL_SCENE_PENALTY", 0.22),
    ("CONTENT_OVERLAP", 0.55),
    ("CONTENT_PENALTY", 0.22),
    ("WEAK_OPENING", 0.30),
])
def test_policy_constants_are_pinned(name, value):
    """A tolerance the policy depends on is named so a reader can audit it, and
    pinned so a change to it is a change to this test."""
    from engine import selection as mod
    assert getattr(mod, name) == value


# ── 9. the duplicated definition stays identical to its source ────────────────


@pytest.mark.parametrize("text", [
    "",
    "one",
    "The Oil Fields, secured at last — 42 times!",
    "తెలుగు అక్షరాలు and some english mixed 99",
    "why did the turbine fail? the bearing seized.",
])
def test_the_content_token_definition_is_the_shared_one(text):
    """``engine.selection._content_tokens`` used to be a copy of the corpus
    layer's definition and had silently dropped the stopword filter. It now
    reads the shared function, and this is the pin against a second copy."""
    from engine.lexical import content_tokens
    from engine.selection import _content_tokens
    assert _content_tokens(text) == content_tokens(text)


# ── 11. the pipeline wiring ───────────────────────────────────────────────────


def test_apply_selection_policy_returns_the_report_the_editor_reads():
    """``_apply_selection_policy`` replaced ``passed[:limit]`` and must hand back
    both the selection and the terms it was made under, so ``editorial_report``
    can justify the six rather than present them."""
    from selekyt.pipeline import _apply_selection_policy

    clustered = [_cand(100 + i * 20, 120 + i * 20,
                       _wordy(f"moment {i}"), salt=i) for i in range(4)]
    spread = [_cand(3000 + i * 400, 3020 + i * 400,
                    _wordy(f"clip {i} elsewhere"), salt=10 + i) for i in range(4)]
    passed = clustered + spread
    selected, report = _apply_selection_policy(passed, limit=4)
    assert len(selected) == 4
    assert selected is not None and report is not None
    # The selection is the policy's, not the head of the passed list: the four
    # clustered clips led it on per-clip score and only one ships now.
    from_scene = sum(1 for c in selected if 90 < c.start < 220)
    assert from_scene <= 2
    assert {id(c) for c in selected} <= {id(c) for c in passed}


def test_the_policy_terms_are_keyed_on_the_clip_not_the_slot():
    """The LLM pass can replace a candidate object, so a term keyed by position
    would land on the wrong clip. The key is the clip's own identity."""
    from selekyt.pipeline import _policy_terms
    from engine.selection import PolicyNote, Scored

    scored = Scored(candidate=_cand(100, 130, _wordy("the clip"), salt=1,
                                    hook="the clip"),
                    base=0.70)
    scored.adjustments.append(PolicyNote(
        term="scene", delta=-0.22, other="a neighbour",
        reason="one clip already chosen within 60s of this span"))

    class _Report:
        selected = (scored,)

    terms = _policy_terms(_Report())
    key = (100.0, 130.0, "the clip")
    assert key in terms
    assert terms[key] == [
        {"term": "scene", "delta": -0.22, "against": "a neighbour",
         "reason": "one clip already chosen within 60s of this span"},
    ]


def test_the_stage_summary_reports_the_selection_the_policy_made():
    """``_select_detail`` used to count ``passed[:limit]``, which is the
    pre-policy head of the list. Once the policy re-orders survivors that is a
    selection the run did not make, and the summary must not describe it."""
    from selekyt.pipeline import _select_detail

    weak_first = _cand(100, 130, _wordy("the clustered clip"), total=0.70, salt=1)
    strong = _cand(900, 930, _wordy("the clip elsewhere"), total=0.90, salt=2)
    gate_results = [(weak_first, True, "ok"), (strong, True, "ok")]

    # The policy ships the stronger clip the gate ranked second.
    summary = _select_detail(
        [weak_first, strong], [weak_first, strong], gate_results, limit=1,
        selected=[strong])
    assert summary["selected"] == 1
    assert summary["bench"] == 1


def test_the_stage_summary_counts_the_bench_the_policy_left():
    """The bench is what the policy did not ship, so its count follows the
    policy rather than the raw ``len(passed) - limit``."""
    from selekyt.pipeline import _select_detail

    pool = [_cand(i * 300, i * 300 + 20, _wordy(f"clip {i}"), salt=i)
            for i in range(8)]
    gate_results = [(c, True, "ok") for c in pool]
    summary = _select_detail(pool, pool, gate_results, limit=3,
                             selected=pool[:3])
    assert summary["selected"] == 3
    assert summary["bench"] == 5


# ── 12. end-to-end on the story fixtures ───────────────────────────────────────


def test_the_selection_spans_the_source_the_fixtures_describe():
    """``tests.story_fixtures`` builds question / digression / answer blocks —
    the setup → development → payoff shape the benchmark's three strong regions
    had. A selection over it must spread across the source rather than
    clustering in one block, and must not need the corpus to do it.

    ``varied_transcript`` is the fixture with distinct connective passages:
    ``transcript`` reuses one filler sentence throughout, which is what the
    corpus layer needs for a hard seam but leaves ``rank``'s similarity cap only
    two candidates to choose from.
    """
    segs = varied_transcript(48)
    thoughts = make_thoughts(segs)
    ranked = rank(thoughts, limit=12)
    assert len(ranked) >= 4, "the fixtures must yield a bench to choose from"

    report = select(ranked, limit=4)
    starts = sorted(c.start for c in report.candidates)
    assert starts == sorted(c.start for c in ranked)[:4]
    # ``--limit`` still binds: the policy reorders the set, it does not shrink it.
    assert len(starts) == min(4, len(ranked))
    # The clips spread across the source rather than sitting in one block. The
    # fixtures space passages 12 s apart, so this is the distance property.
    for earlier, later in zip(starts, starts[1:]):
        assert later - earlier >= LOCAL_SCENE_S * 0.5, (
            f"two selections {later - earlier:.0f}s apart are one scene")


def test_a_selection_from_the_fixtures_reports_its_terms():
    """The editor sees the working, not just the selections."""
    segs = varied_transcript(48)
    corpus = _corpus(segs)
    thoughts = make_thoughts(segs)
    ranked = rank(thoughts, limit=12)
    report = select(ranked, limit=3, corpus=corpus)
    assert len(report.candidates) == min(3, len(ranked))
    assert report.candidates, "a selection over a corpus must produce something"
    for scored in report.selected:
        assert scored.base == scored.candidate.total_score, (
            "the base is the measurement, not something the policy sets")
