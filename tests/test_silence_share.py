"""Threshold and semantics of `check_silence_share` (criterion P5).

The audit's silence bar used to be 0.55 while the criterion it exists to
enforce says no clip may exceed 0.30. Every clip in the committed corpus
passed the old threshold, including two that fail the criterion outright, so
the check could not fire on the one condition it was written for. These tests
pin the corrected bar and pin the two known corpus instances, so a future
loosening has to delete a measured number rather than adjust a constant.

They also pin what the metric *is*: a word-free share computed from transcript
timings, never from decoded audio. A window with no transcript words can be a
loud music bed, so a FAIL is a request for a human to listen, not a verdict
that the cut is wrong — and that is why these tests assert the number, not
that the pipeline should have removed the material.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

ROOT = Path(__file__).resolve().parent.parent

from tools.editorial_audit import SILENCE_SHARE_MAX, check_silence_share  # noqa: E402


def _cand(text: str, words: list[tuple[float, float]]) -> dict:
    """A candidate whose word spans tile exactly the seconds given.

    Each tuple is a (start, end) word span; the gap between consecutive words
    is what the metric counts as word-free.
    """
    return {
        "text": text,
        "start": words[0][0],
        "end": words[-1][1],
        "words": [{"text": "w", "start": s, "end": e} for s, e in words],
    }


def test_the_threshold_is_the_criterion_p5_states():
    """0.30 is P5's hard bar, not a tuning constant."""
    assert SILENCE_SHARE_MAX == 0.30


def test_a_clip_of_consecutive_words_is_not_flagged():
    words = [(i * 0.4, i * 0.4 + 0.35) for i in range(10)]
    ok, share = check_silence_share(_cand("w " * 10, words), [(0.0, 4.0)])
    assert ok
    assert share == pytest.approx(0.125, abs=1e-3)


def test_the_bar_fires_on_the_gap_it_was_written_for():
    """A 10 s window with 4 s of speech and 6 s of nothing is 60% word-free."""
    words = [(0.0, 2.0), (2.1, 4.0), (9.0, 11.0)]
    ok, share = check_silence_share(_cand("w w w", words), [(0.0, 11.0)])
    assert not ok
    assert share > 0.30


def test_just_under_the_bar_still_passes():
    """19% word-free is inside the criterion; the check must not fire."""
    words = [(0.0, 3.6), (5.0, 7.2)]
    ok, share = check_silence_share(_cand("w w", words), [(0.0, 7.2)])
    assert ok
    assert share == pytest.approx(1 - 5.8 / 7.2, abs=1e-3)


def test_nothing_to_audit_reports_zero_without_passing_by_accident():
    """Empty inputs short-circuit to (True, 0.0) — there is no material to
    measure, which is not the same as a clip that is 0% word-free."""
    ok, share = check_silence_share({"text": "", "words": []}, [(0.0, 5.0)])
    assert ok and share == 0.0
    ok, share = check_silence_share(_cand("w", [(0.0, 1.0)]), [])
    assert ok and share == 0.0


def test_intervals_with_zero_width_fail_rather_than_read_as_clean():
    """``kept <= 0`` is a degenerate plan, not a silent clip."""
    ok, share = check_silence_share(_cand("w", [(0.0, 1.0)]), [(5.0, 5.0)])
    assert not ok


# The two committed clips that exceed the criterion. Recorded here because a
# threshold change should have to confront the measured numbers it moves.
KNOWN_FAILURES = [
    ("nasa360-ep5_run", 4, 0.408),
    ("telugu-tutorial_run", 0, 0.463),
]


@pytest.mark.parametrize("project, index, expected", KNOWN_FAILURES)
def test_the_two_committed_clips_that_fail_criterion_p5(project, index, expected):
    """Both clips fail P5's 30% bar, and both are above the audit's old 0.55?

    No — 0.463 and 0.408 are both *below* 0.55, which is exactly why the old
    threshold never fired. They are recomputed here from the committed
    `selections.json` so the numbers stay tied to the artifacts rather than to
    a doc that could drift.
    """
    selections = json.loads(
        (ROOT / "corpus" / project / "project" / "selections.json").read_text()
    )
    from engine.edit_plan import retention_intervals
    from models.core import Candidate, Word

    src = selections[index]
    cands = [
        Candidate(
            start=src["start"], end=src["end"], text=src.get("text", ""),
            words=[Word(w["text"], w["start"], w["end"], w.get("confidence", 1.0))
                   for w in src.get("words", [])],
            story_type=src.get("story_type", ""), hook=src.get("hook", ""),
            payoff=src.get("payoff", ""),
            editorial_score=src.get("editorial_score", 0.0),
            virality_score=src.get("virality_score", 0.0),
            context_score=src.get("context_score", 0.0),
            confidence=src.get("confidence", 0.0),
            reason=src.get("reason", ""),
        )
    ]
    ok, share = check_silence_share(src, retention_intervals(cands[0]))
    assert share == pytest.approx(expected, abs=2e-3)
    assert not ok, (
        f"{project} clip {index + 1} measures {share:.1%} word-free, over P5's "
        f"30% bar. The audit must flag it for human review."
    )


def test_the_rest_of_the_corpus_meets_the_bar():
    """Every other committed clip is inside the criterion, so the two failures
    are isolated rather than a whole-corpus property."""
    from engine.edit_plan import retention_intervals
    from models.core import Candidate, Word

    failing = {(p, i) for p, i, _ in KNOWN_FAILURES}
    over = []
    total = 0
    for project_dir in sorted((ROOT / "corpus").glob("*_run/project")):
        path = project_dir / "selections.json"
        if not path.is_file():
            continue
        name = project_dir.parent.name
        for index, src in enumerate(json.loads(path.read_text())):
            if (name, index) in failing:
                continue
            total += 1
            cand = Candidate(
                start=src["start"], end=src["end"], text=src.get("text", ""),
                words=[Word(w["text"], w["start"], w["end"], w.get("confidence", 1.0))
                       for w in src.get("words", [])],
                story_type=src.get("story_type", ""), hook=src.get("hook", ""),
                payoff=src.get("payoff", ""),
                editorial_score=src.get("editorial_score", 0.0),
                virality_score=src.get("virality_score", 0.0),
                context_score=src.get("context_score", 0.0),
                confidence=src.get("confidence", 0.0),
                reason=src.get("reason", ""),
            )
            ok, share = check_silence_share(src, retention_intervals(cand))
            if not ok:
                over.append(f"{name} clip {index + 1}: {share:.1%}")
    assert total >= 20, f"expected at least 20 other committed clips, found {total}"
    assert not over, "clips outside the two known failures also exceed 30%: " + "; ".join(over)


def test_a_failure_is_reported_as_unmeasured_not_as_dead_air():
    """The word "silence" in this metric's name is the defect, not the reading.

    On nasa360-ep5 clip 05 a 40.8% figure was explained first as an editorial
    pause and then as dead air to be trimmed. Both were invented from the word
    timings; measuring the window showed a loud music bed (mean −31.4 dB,
    0.00 s of true silence; `docs/METRICS.md` A2.4). The audit's own output line
    must not assert a cause it has not measured, because that is how the second
    explanation nearly became a change that cut music out of a shipped clip.
    """
    # The label is composed in the report loop, not in the check itself, so this
    # builds the string the same way that loop does.
    cand = _cand("w w w", [(0.0, 2.0), (2.1, 4.0), (9.0, 11.0)])
    intervals = [(0.0, 11.0)]
    ok, share = check_silence_share(cand, intervals)
    assert not ok
    label = "" if ok else " (word-free; cause unmeasured)"
    line = f"silence share {share:.1%}{label}"
    assert "dead air" not in line
    assert "unmeasured" in line
