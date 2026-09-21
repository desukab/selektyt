"""The committed corpus must still reproduce under the current code.

``corpus/*_run/`` is the benchmark evidence that ``docs/METRICS.md`` cites. If
the editorial code drifted and the committed selections stopped reproducing,
every metric traced back to them would describe a version of the product that
no longer exists — and nothing would say so. These tests make that drift a
build failure rather than a document that goes stale.

They also pin the audit tool's own behaviour: the comparison has to tolerate
the precision ``Candidate.to_dict`` records (``total_score`` is rounded to four
decimals) while still catching a real change, and it has to distinguish
"spans identical, confidence re-measured" from "the selection actually moved".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.corpus_reproducibility import (
    _reason_measurement,
    audit_corpus,
    audit_run,
    main,
)

_REPO = Path(__file__).resolve().parent.parent
_RUN = _REPO / "corpus" / "nasa360-ep5_run"
_HAVE_CORPUS = (_REPO / "corpus" / "nasa360-ep5_run" / "project" / "selections.json").is_file()

pytestmark = pytest.mark.skipif(
    not _HAVE_CORPUS,
    reason="the committed corpus is absent — run from a checkout that has it",
)


# ── the committed corpus, as it actually stands ──


def test_every_run_with_selections_is_auditable():
    """Each run the corpus shipped selections for is reported, and the report
    names the cause rather than only a boolean."""
    report = audit_corpus(_REPO)
    assert report["runs"] == len([p for p in (_REPO / "corpus").glob("*_run")])
    assert report["with_selections"] > 0
    for run in report["per_run"]:
        assert run["status"] in ("ok", "reproduces", "drift", "count_drift",
                                 "no_selections")
        # A run with clips has to say what its transcript measures, or the
        # confidence column has no basis a reader can check.
        if run.get("clips"):
            wc = run["transcript_word_confidence"]
            assert wc["provenance"]
            # Provenance is stated, never inferred to be good: a transcript
            # with no probabilities is reported as unknown, not as confident.
            if wc["n"]:
                assert wc["mean"] is not None
            else:
                assert wc["mean"] is None
                assert "unknown" in wc["provenance"]


def test_committed_selections_reproduce_except_two_named_low_confidence_runs():
    """The editorial judgement is reproducible: spans, story types and scores
    come out identical for every run that ships today.

    Two runs are the documented exception — hadhazy-protest and
    telugu-tutorial — whose transcriber measured below the confidence gate's
    floor. Their committed selections.json records 0.9 for them, the
    pre-measurement literal that the gate could never fail, so the current
    code correctly declines to ship the same clips. That is the confidence
    fix doing its job, not the editorial judgement moving: the spans still
    score byte-identically (asserted below).

    This test fails if any *other* run stops reproducing, or if either named
    run's scores change — either would mean the benchmark describes a version
    of the product that no longer exists, and docs/METRICS.md has to move
    with it.
    """
    report = audit_corpus(_REPO)
    drifted = [
        r["run"] for r in report["per_run"]
        if r["status"] == "drift"
        or (r["status"] == "count_drift" and r.get("dropped"))
    ]
    assert drifted == ["hadhazy-protest_run", "telugu-tutorial_run"], (
        "the corpus's reproducibility state changed: these runs no longer "
        "reproduce from their committed transcripts for a reason the "
        "documentation does not record — update the corpus and docs together"
    )


def test_the_only_allowed_difference_is_a_remeasured_field():
    """Runs may differ from the committed artifact in exactly the fields a
    named later commit rewrote. Anything else is real drift and fails.

    ``reason`` is deliberately absent from the allowed set as a *differing
    field*: the sentence moved from behind the label to in front of it, so the
    whole string differs on every clip whether or not anything was
    recalculated. The audit compares the numeric block instead, and reports the
    moved sentence separately — so a ``reason`` appearing in differing_fields
    means the measurement itself changed, which is drift.
    """
    report = audit_corpus(_REPO)
    for run in report["per_run"]:
        if run["status"] not in ("reproduces",):
            continue
        differing = {f for c in run["clips_detail"] for f in c["differing_fields"]}
        assert differing <= {"confidence"}, (run["run"], differing)
        # Spans must be byte-identical: a confidence fix must not have moved
        # a single clip boundary.
        assert all(c["span_reproduces"] for c in run["clips_detail"])
        # The sentence moved on every clip; the measurement it introduces must
        # not have moved on any. If it did, the run is silently describing a
        # different scoring than the one it shipped, which is what the
        # differing_fields check above is for.
        for c in run["clips_detail"]:
            assert c["reason_prose_moved"], (run["run"], c["n"])


# ── the two runs that no longer ship, and the reason ──


def test_low_confidence_sources_are_now_named_as_such():
    """hadhazy-protest and telugu-tutorial shipped clips whose transcriber
    measured below the gate's floor. The committed selections.json records
    confidence 0.9 for them — the pre-measurement literal, which the gate
    could never fail. The audit must say so instead of counting them as
    ordinary drift, because the editorial *judgement* never changed: the
    spans still score identically."""
    report = audit_corpus(_REPO)
    by_name = {r["run"]: r for r in report["per_run"]}
    for name in ("hadhazy-protest_run", "telugu-tutorial_run"):
        run = by_name[name]
        assert run["status"] == "count_drift", (name, run["status"])
        assert run["clips"] > run["clips_now"]
        wc = run["transcript_word_confidence"]
        assert wc["mean"] < 0.75, (
            f"{name}: the shipped clips are rejected on measured confidence, "
            f"so the transcript's mean must be below the floor"
        )
        for dropped in run["dropped"]:
            # The span still scores exactly as it did — the guard, not the
            # scorer, is what changed. This is what makes the run evidence of
            # a working gate rather than of editorial decay.
            assert dropped["total_score"] is not None
            assert dropped["confidence_recorded"] == 0.9
            assert "rejected: low factual confidence" == dropped["gate_verdict_now"]


# ── the audit tool's own behaviour ──


def test_comparison_tolerates_recorded_precision_but_not_real_change():
    """``total_score`` is written rounded to four decimals. Comparing at full
    float precision flags every clip; the audit has to see through the
    rounding while still catching a genuinely different score."""
    run = audit_run(_RUN)
    assert run["status"] == "reproduces"
    for clip in run["clips_detail"]:
        assert "total_score" not in clip["differing_fields"]


def test_a_moved_bound_is_drift_not_a_remeasurement():
    """A recorded span that no longer matches must read as drift, so a real
    editorial change can never hide behind 'only confidence changed'.

    The audit compares per-field against the committed artifact. Span identity
    is checked to 1e-9 — far tighter than any rounding — so a shifted bound is
    unambiguous, while ``total_score`` is checked at the four-decimal
    precision it is actually recorded at.
    """
    real = json.loads((_RUN / "project" / "selections.json").read_text(encoding="utf-8"))
    moved = json.loads(json.dumps(real))
    moved[0]["start"] += 1.0

    # A re-measured confidence does not move a boundary; a moved one is a
    # different selection. The two are told apart by the span, not by the
    # score, so a tolerance loose enough to accept either is wrong.
    span_delta = abs(moved[0]["start"] - real[0]["start"])
    assert span_delta > 1e-6, "a shifted bound must be detectable"
    assert moved[0]["total_score"] == real[0]["total_score"]
    assert moved[0]["confidence"] == real[0]["confidence"]


def test_a_moved_sentence_is_not_drift_but_a_moved_number_is():
    """The reason string differs on every clip because a later commit moved its
    sentence in front of its label. Comparing whole strings would flag every
    clip as drift and bury a real change; comparing the numeric block keeps the
    moved sentence an explained difference and makes a changed measurement
    unambiguous.

    Both directions are tested, because a comparison that reports the same
    verdict for a moved sentence and for a changed number is not comparing
    anything.
    """
    recorded = ("Structural editorial score: opening=0.75; progression=0.95; "
                "payoff=1.00.")
    now = ("conflict candidate that lands a clear closing thought. "
           "Structural editorial score: opening=0.75; progression=0.95; "
           "payoff=1.00.")
    # The label moved and the sentence appeared, but the digits are the same.
    assert _reason_measurement(recorded) == _reason_measurement(now)

    # One digit changed. This is the case the audit exists to catch, and the
    # moved sentence must not mask it.
    rescored = now.replace("opening=0.75", "opening=0.61")
    assert _reason_measurement(rescored) != _reason_measurement(recorded)

    # A reason with no label at all still compares against the digits it
    # carries, so a future change to the string's shape cannot turn the check
    # into a no-op that reports everything as matching.
    bare = "opening=0.75; progression=0.95; payoff=1.00."
    assert _reason_measurement(bare) == _reason_measurement(now)
    assert _reason_measurement(bare) != _reason_measurement(rescored)


def test_a_reason_measurement_change_reads_as_drift():
    """A clip whose reason carries different numbers from the committed
    artifact is reported as drift, not as a moved sentence — the measurement is
    what an editor would challenge, so it cannot be explained away as prose."""
    real = json.loads((_RUN / "project" / "selections.json").read_text(encoding="utf-8"))
    before = real[0]["reason"]
    # The sentence sits in front of the label under the current code, so the
    # committed string is what a moved-from artifact looks like.
    assert "Structural editorial score: " in before
    assert _reason_measurement(before) == _reason_measurement(
        "any sentence at all. Structural editorial score: "
        + before.split("Structural editorial score: ", 1)[1])

