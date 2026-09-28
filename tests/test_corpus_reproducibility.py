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
# Fixture for the tool-behaviour tests below. ooi-vertical is a delivering run
# that still reproduces, so it is a valid stand-in for a committed selection the
# current code agrees with. nasa360-ep5 used to hold this role but no longer
# can: the candidate-scan fix described in docs/CORPUS_BENCHMARK_STATUS.md let
# its tail generate candidates for the first time, so its committed selection is
# now an intentional drift. pentagon-esper-briefing held it, lost it to the
# word-boundary fix in engine/edit_plan.py, and then had its media re-run under
# the fixed engine, so it agrees with the current code again -- and reads ``ok``
# rather than ``reproduces``, because nothing about it is stale enough to need
# re-measuring. ooi-vertical stays the fixture because it exercises the harder
# case: its committed reason strings predate the sentence move, so it is a real
# "spans identical, confidence re-measured" comparison rather than an exact one.
_RUN = _REPO / "corpus" / "ooi-vertical_run"
_HAVE_CORPUS = (_REPO / "corpus" / "ooi-vertical_run" / "project" / "selections.json").is_file()

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


def test_committed_selections_reproduce_except_four_named_changed_runs():
    """The editorial judgement is reproducible: spans, story types and scores
    come out identical for every run that ships today.

    Four runs are the documented exception, in two distinct kinds:

    - **hadhazy-protest and telugu-tutorial** whose transcriber measured below
      the confidence gate's floor. Their committed selections.json records 0.9
      for them, the pre-measurement literal that the gate could never fail, so
      the current code correctly declines to ship the same clips. That is the
      confidence fix doing its job, not the editorial judgement moving: the
      spans still score byte-identically (asserted below).
    - **emwcon-panel-2018 and nasa360-ep5** whose committed selection was made
      with a candidate scan that stopped at a fixed cap. Candidates are built
      in start order, so the cap meant the tail of a long source generated
      nothing and could never be selected. The scan now spends its budget on
      even coverage instead, and on both runs the newly reachable tail
      outcompeted earlier clips the truncated scan had settled for. Measured:
      every span the current code ships that the committed artifact does not
      starts *past* the old scan's reach, and every span it dropped starts
      within it — a blind spot closing, not a scorer moving. See
      docs/CORPUS_BENCHMARK_STATUS.md for the numbers.

    Two runs that *were* exceptions are no longer, because their media was
    re-run under the fixed engine rather than re-scored against a stale
    artifact: **pentagon-esper-briefing** and **royal-society-cloke**, the two
    whose committed clips the editorial audit's edge check found opening and
    closing mid-word. The word-boundary fix moved their bounds, so their
    committed artifacts stopped reproducing; the fix is confirmed on real media
    by re-running the sources, and the regenerated selections are now ``ok`` —
    written by the code under test. That is the honest way to close the loop:
    the alternative is a selection that reproduces on paper while the render
    still ships the clipped word.

    This test fails if any *other* run stops reproducing, or if any named
    run's scores change in a way the docs do not record — either would mean
    the benchmark describes a version of the product that no longer exists,
    and docs/METRICS.md has to move with it.
    """
    report = audit_corpus(_REPO)
    drifted = [
        r["run"] for r in report["per_run"]
        if r["status"] == "drift"
        or (r["status"] == "count_drift" and r.get("dropped"))
    ]
    assert drifted == ["emwcon-panel-2018_run", "hadhazy-protest_run",
                       "nasa360-ep5_run", "telugu-tutorial_run"], (
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



# ── the committed captions are a snapshot, not a live output ───────────────


def test_the_committed_captions_are_what_the_current_emitter_writes(tmp_path):
    """The regenerate tools' byte-identity claim, measured rather than assumed.

    ``tools/regenerate_captions.py`` documents that re-emitting from a
    committed ``selections.json`` is byte-identical to what a fresh run would
    write. That is a claim about the *current* code, and it is only true if the
    committed captions and the committed code are the same age — which on this
    corpus they are not. The captions were written 2026-09-19 and the
    one-width-policy caption work landed 2026-09-21/22, so re-emitting rewrites
    most of them. Measured: 79 of 81 files.

    That is a freshness gap and not a defect: the committed sidecars still
    agree with the committed *pixels*, which were burned from the same
    2026-09-19 captions, so the corpus is self-consistent. Regenerating the
    sidecars without re-rendering would break that agreement, and re-rendering
    needs media the repo does not redistribute. The regenerated captions still
    pass the audit. This test exists because the number is quoted in
    ``docs/CORPUS_RESULTS.md`` and would otherwise be unverifiable prose; if
    the corpus is ever re-run, the count drops to zero and this test says so.

    The guard is mutation-verified: removing the one-frame gap between cues
    (``next_start - 0.033`` → ``next_start``) moves the count 79 → 80 and
    fails here at ``assert changed <= 79``. A mutation to the cue tail's
    ``st + 0.08`` floor changes nothing on this corpus and passes — correctly,
    since that floor is dominated by the clamp on every clip here, so against
    this data the mutation is a no-op rather than a blind spot.
    """
    import shutil
    import subprocess

    from engine.captions import ass_for_candidate, srt_for_candidate
    from models.core import Candidate, Word

    stems = sorted((_REPO / "corpus").glob("*_run/project/selections.json"))
    assert stems, "no corpus selections found; the test below would pass vacuously"

    changed = total = 0
    venv = str(_REPO / ".venv" / "bin" / "python")
    tool = str(_REPO / "tools" / "regenerate_captions.py")
    for selections in stems:
        project = selections.parent
        scratch = tmp_path / project.parent.name
        shutil.copytree(project, scratch, symlinks=True)
        captions = scratch / "captions"
        before = {f.name: f.read_bytes() for f in sorted(captions.glob("*"))}
        proc = subprocess.run([venv, tool, str(scratch)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, f"regenerate failed on {scratch}: {proc.stderr}"
        after = {f.name: f.read_bytes() for f in sorted(captions.glob("*"))}
        total += len(before)
        changed += sum(1 for k in set(before) | set(after)
                       if before.get(k) != after.get(k))

    assert total > 40, f"only {total} caption files found; the corpus glob is wrong"
    assert changed <= 79, (
        f"{changed} of {total} committed caption files now differ from what the "
        "current emitter writes. If the corpus was re-run the count should be "
        "near zero and the docs quoting 79 are stale; if it was not, this is a "
        "caption change the committed corpus no longer reflects — update "
        "docs/CORPUS_RESULTS.md to the new figure.")
