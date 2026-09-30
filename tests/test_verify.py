"""Failure-first tests for ``engine.verify``.

This module's whole purpose is catching the system's own errors, so the tests
are adversarial: every one feeds the loop something broken — or something that
looks broken to a sloppy check but is not — and pins what it must report.

The hazards these tests pin, in the order an editor would meet them:

- a cut that lands inside a word, and the ulp-straddling bound that must *not*
  be reported as one, because the check and ``_word_aligned`` must agree or the
  repair moves a bound the check passed;
- a cut that drops a word from a sentence, versus one that only tightens a
  pause — every mid-sentence finding the corpus audit ever produced was the
  pause case, so the distinction is what keeps the check honest;
- an edge the transcript cannot classify, which must be REVIEW rather than a
  guess, because a guess at a sentence end is an editorial decision;
- a span the truth does not cover, and the ladder rung that was used to decide
  it, because a degraded rung reported as a full rung is a check claiming
  backing it does not have;
- confidence that must be trimmed away and reported, never inflated;
- a duplicate sentence trimmed from the lower-ranked clip and escalated from
  the higher-ranked one, because re-scoring either is the forbidden move;
- artifacts that disagree with the plan, at the tolerances the corpus measured;
- and the loop's own failure modes: a re-derivation that does not clear its
  fault, a repair that moves nothing, and the pass cap — each of which must
  terminate and be reported rather than hang or pass.
"""
import pytest

from engine.edit_plan import _BOUND_EPS, _word_aligned
from engine.memory.evidence import Evidence, EvidenceSet, Modality, Source
from engine.memory.intervals import IntervalSet
from engine.verify import (
    CAPTION_TOLERANCE_S,
    CONTAINER_DURATION_TOLERANCE,
    DUPLICATE_MIN_CHARS,
    Fault,
    INTERCHANGE_FRAME_TOLERANCE,
    Repair,
    SiblingClip,
    Verdict,
    VerifyContext,
    VerifyReport,
    apply_repair,
    check_duplicate_sentence,
    check_interchange_drift,
    check_low_confidence_only,
    check_mid_sentence_cuts,
    check_mid_word_cuts,
    check_render_decodable,
    check_render_drift,
    check_shaved_tail_word,
    check_unsupported_spans,
    diagnose,
    measure,
    run_loop,
    verify,
)
from models.core import Segment, Word

# ── the transcript every test cuts from ───────────────────────────────────────
# Words are contiguous except the one pause at 1.5-2.3 s, so a plan can be
# built that is clean by construction and each test breaks exactly one thing.
# The first utterance closes on ``syria.`` — terminal punctuation is what makes
# an edge decidable, and the pentagon clip that opened on `` Syria.`` is the
# shipped defect this loop exists to catch.

_WORDS = [
    Word(text="you", start=0.00, end=0.40),
    Word(text="mentioned", start=0.40, end=0.80),
    Word(text="the", start=0.80, end=1.00),
    Word(text="oil", start=1.00, end=1.30),
    Word(text="fields", start=1.30, end=1.50),
    Word(text="in", start=2.30, end=2.60),
    Word(text="the", start=2.60, end=2.90),
    Word(text="east", start=2.90, end=3.10),
    Word(text="of", start=3.10, end=3.30),
    Word(text="syria.", start=3.30, end=3.40),
    Word(text="and", start=3.40, end=3.60),
    Word(text="securing", start=3.60, end=4.00),
    Word(text="the", start=4.00, end=4.20),
    Word(text="oil", start=4.20, end=4.60),
    Word(text="fields", start=4.60, end=5.00),
    Word(text="too.", start=5.00, end=5.40),
]

_UTTERANCES = (
    Segment(start=0.00, end=3.40,
            text="You mentioned the oil fields in the east of Syria.",
            words=_WORDS[0:10]),
    Segment(start=3.40, end=5.40, text="And securing the oil fields too.",
            words=_WORDS[10:16]),
)


def _w(text: str, start: float, end: float) -> Word:
    return Word(text=text, start=start, end=end)


def _speech(spans=((0.00, 5.40),), confidence=0.9) -> EvidenceSet:
    """Speech regions as the G3 truth, so a clean plan is clean by default."""
    return EvidenceSet.of(Evidence(
        claim="speech regions from VAD",
        intervals=IntervalSet.of(*spans),
        modality=Modality.AUDIO,
        source=Source.of("engine.coverage", "vad", min_duration=0.25),
        confidence=confidence,
        rationale="VAD speech regions measured on the source audio",
    ))


def _ctx(**over) -> VerifyContext:
    base = dict(
        source_duration=60.0,
        fps=30.0,
        words=tuple(_WORDS),
        utterances=_UTTERANCES,
        evidence=_speech(),
        confidence_floor=0.8,
    )
    base.update(over)
    return VerifyContext(**base)


def _faults(report) -> set[Fault]:
    # Both spellings reach this helper: a bare tuple of findings from a single
    # ``check_*`` function, and a whole ``VerifyReport`` from ``verify``. The
    # report exposes its findings as a field, so unwrap it; a tuple is already
    # the iterable the caller meant.
    findings = report.findings if isinstance(report, VerifyReport) else report
    return {f.fault for f in findings}


# ── the closed sets ───────────────────────────────────────────────────────────


def test_fault_enum_is_the_designs_closed_set():
    """Fourteen faults, exactly the set §4.3 names. A fault outside this set is
    one the loop has no diagnosis for, so the enum is the contract."""
    assert {f.value for f in Fault} == {
        "mid_word_cut", "mid_sentence_cut", "shaved_tail_word",
        "unsupported_span", "low_confidence_only", "duplicate_sentence",
        "caption_text_drift", "caption_span_drift", "interchange_drift",
        "record_overlap", "render_drift", "render_undecodable",
        "media_unresolvable", "undecidable",
    }


def test_repair_enum_is_the_permitted_closure():
    """Five repairs and no others: the closure is the safety argument."""
    assert {r.value for r in Repair} == {
        "snap", "trim", "reemit", "remux", "escalate",
    }


# ── MID_WORD_CUT ──────────────────────────────────────────────────────────────


def test_mid_word_cut_detected():
    """A boundary inside a word is reported with the word, the interval and the
    edge — the three things a repair needs in order to move it."""
    plan = [(0.00, 1.15)]
    findings = check_mid_word_cuts(plan, _ctx())
    assert [f.fault for f in findings] == [Fault.MID_WORD_CUT]
    f = findings[0]
    assert (f.interval_index, f.edge) == (0, "end")
    assert "oil" in f.detail
    assert f.verdict == Verdict.FAIL


def test_boundary_on_a_word_edge_is_clean():
    """A boundary exactly on a word's start or end is between words, not inside
    one."""
    assert check_mid_word_cuts([(0.00, 1.30), (2.30, 3.40)], _ctx()) == ()


def test_ulp_straddling_bound_is_not_a_mid_word_cut():
    """A bound within ``_BOUND_EPS`` of a word's end is a boundary. The check
    and the snap must agree: if the check flagged it, the repair would move a
    bound that was already aligned — the shipped pentagon case, where a
    sentence bound at ``364.84`` met a word ending at ``364.84000000000003``."""
    t = 3.40 - 1e-7  # inside ``too.`` by a hundred nanometres
    assert t > 3.30 and t < 5.00 + 0
    findings = check_mid_word_cuts([(0.00, t)], _ctx())
    assert findings == ()
    # And the repair agrees it is already on the boundary.
    assert _word_aligned(t, _WORDS) == t


def test_mid_word_repair_snaps_and_reverifies_clean():
    """The loop repairs and re-runs the whole suite, and the plan it returns is
    one the exporters recognise: word-aligned and in order."""
    result = run_loop([(0.00, 3.45)], _ctx())
    assert result.verdict == Verdict.CLEAN
    assert result.plan == ((0.00, 3.40),)
    assert result.passes == 2
    assert len(result.repairs) == 1
    rec = result.repairs[0]
    assert (rec.repair, rec.fault) == (Repair.SNAP, Fault.MID_WORD_CUT)
    assert rec.before == (0.00, 3.45)
    assert rec.after == (0.00, 3.40)
    assert rec.changed_plan is True
    # Nothing is escalated and the caller has nothing to do.
    assert result.escalated == ()
    assert result.caller_actions == ()


# ── MID_SENTENCE_CUT ──────────────────────────────────────────────────────────


def test_word_loss_inside_a_sentence_is_a_sentence_cut():
    """A boundary that drops a word from the middle of a sentence leaves a hole
    in the speech the viewer hears; the finding names the two words it
    separated."""
    plan = [(0.00, 1.00), (1.70, 3.40)]  # drops ``oil`` (1.00-1.30)
    findings = check_mid_sentence_cuts(plan, _ctx())
    assert Fault.MID_SENTENCE_CUT in _faults(findings)
    f = next(f for f in findings if f.fault == Fault.MID_SENTENCE_CUT)
    assert (f.interval_index, f.edge) == (1, "start")
    assert "oil" in f.detail


def test_pause_tightening_is_not_a_sentence_cut():
    """A boundary that only shortens a pause between two kept words loses no
    word, so the sentence is intact. Every mid-sentence finding the corpus
    audit produced was this case, so a check that flags it manufactures the
    entire defect class."""
    plan = [(0.00, 1.50), (2.30, 3.40)]  # tightens the 1.5-2.3 s pause
    findings = check_mid_sentence_cuts(plan, _ctx())
    assert Fault.MID_SENTENCE_CUT not in _faults(findings)


def test_orphaned_opening_edge_is_detected():
    """The clip that opens on `` Syria.`` after dropping the sentence it
    finished. The boundary has no kept word on its other side, so the inward
    check cannot see it; only looking outward at the dropped words finds it."""
    plan = [(3.30, 5.40)]
    findings = check_mid_sentence_cuts(plan, _ctx())
    assert Fault.MID_SENTENCE_CUT in _faults(findings)
    assert any("opens mid-sentence" in f.detail
               for f in findings if f.fault == Fault.MID_SENTENCE_CUT)


def test_sentence_cut_repair_snaps_to_the_utterance_edge():
    """The repair snaps to the utterance edge rather than guessing a sentence
    end inside it."""
    plan = [(0.00, 1.00), (1.70, 3.40)]
    result = run_loop(plan, _ctx())
    assert result.verdict == Verdict.CLEAN
    assert result.plan == ((0.00, 3.40),)
    assert all(r.repair == Repair.SNAP for r in result.repairs)


def test_unpunctuated_utterance_is_undecidable_not_clean():
    """An utterance with no terminal punctuation anywhere makes every edge in
    it consistent with mid-sentence. That is a property of the ASR output, and
    the loop says REVIEW rather than guessing where a sentence ends — 34-47%
    of English segments on this corpus are like this."""
    words = tuple(w for w in _WORDS if w.text != "syria.")
    words = words + (Word(text="syria", start=3.30, end=3.40),)
    ut = (Segment(start=0.00, end=3.40, text="you mentioned the oil fields "
            "in the east of syria", words=list(words[:10])),)
    ctx = _ctx(words=words, utterances=ut)
    findings = check_mid_sentence_cuts([(3.30, 5.40)], ctx)
    undecidable = [f for f in findings if f.fault == Fault.UNDECIDABLE]
    assert len(undecidable) == 1
    assert undecidable[0].verdict == Verdict.REVIEW

    result = run_loop([(3.30, 5.40)], ctx)
    # A guess would have moved the bound; the loop records and stops instead.
    assert result.verdict == Verdict.REVIEW
    assert result.plan == ((3.30, 5.40),)
    assert any(r.repair == Repair.ESCALATE for r in result.repairs)


def test_missing_utterance_spans_are_undecidable():
    """Without the transcript's own utterance spans the sentence structure
    cannot be derived from the plan's text without inheriting its joins, so
    the check reports unmeasured rather than passing."""
    findings = check_mid_sentence_cuts([(0.00, 3.40)], _ctx(utterances=()))
    assert len(findings) == 1
    assert findings[0].fault == Fault.UNDECIDABLE
    assert findings[0].verdict == Verdict.REVIEW


# ── SHAVED_TAIL_WORD ──────────────────────────────────────────────────────────


def test_shaved_tail_word_detected_and_repaired():
    """The interval ends before the last word does, so the render truncates it
    mid-phoneme; the repair extends the interval to the word's end."""
    findings = check_shaved_tail_word([(0.00, 3.35)], _ctx())
    assert [f.fault for f in findings] == [Fault.SHAVED_TAIL_WORD]
    result = run_loop([(0.00, 3.35)], _ctx())
    assert result.verdict == Verdict.CLEAN
    assert result.plan == ((0.00, 3.40),)


def test_shaved_tail_word_is_clamped_to_the_source():
    """A word whose end runs past the source cannot extend the interval past
    the source — ``cut_algebra.normalize_keeps`` clamps the same way, and a
    plan spanning past the source is not a plan at all."""
    result = run_loop([(0.00, 5.35)], _ctx(source_duration=5.38))
    assert result.plan == ((0.00, 5.38),)
    assert all(iv[1] <= 5.38 for iv in result.plan)


def test_short_last_word_is_not_shaved():
    """Word duration alone is not the signal: unstressed function words
    genuinely run 90-150 ms. A 150 ms final word fully inside the interval is
    natural speech, and thresholding on duration would flag all of it."""
    words = (Word(text="well", start=0.00, end=0.90),
             Word(text="i", start=0.90, end=1.05))
    ctx = _ctx(words=words, utterances=(),
               evidence=_speech(((0.00, 1.05),)))
    assert check_shaved_tail_word([(0.00, 1.05)], ctx) == ()


# ── UNSUPPORTED_SPAN and the ground-truth ladder ──────────────────────────────


def test_unsupported_span_found_by_evidence():
    """Part of a kept interval sits in an evidence gap; the finding names the
    precise unsupported seconds, not a thinness score."""
    ctx = _ctx(evidence=_speech(((0.00, 2.00),)))
    findings = check_unsupported_spans([(0.00, 3.40)], ctx)
    assert [f.fault for f in findings] == [Fault.UNSUPPORTED_SPAN]
    f = findings[0]
    assert (round(f.span.start, 3), round(f.span.end, 3)) == (2.00, 3.40)
    assert "G3 evidence" in f.instrument


def test_unsupported_span_degrades_to_word_coverage_and_says_so():
    """The evidence store is specified but unpopulated. The check does not pass
    — it falls back to transcript word coverage and the finding carries the
    degraded rung, so a reader of the record sees which truth was used."""
    findings = check_unsupported_spans([(0.00, 3.40)], _ctx(evidence=None))
    assert [f.fault for f in findings] == [Fault.UNSUPPORTED_SPAN]
    assert "G2 word coverage" in findings[0].instrument
    assert "degraded" in findings[0].instrument


def test_unsupported_span_trim_reverifies_clean():
    """The trim removes exactly the uncovered seconds and the re-verify against
    the same truth passes."""
    ctx = _ctx(evidence=_speech(((0.00, 2.00),)))
    result = run_loop([(0.00, 3.40)], ctx)
    assert result.verdict == Verdict.CLEAN
    assert result.plan == ((0.00, 2.00),)


def test_a_plan_with_no_truth_at_all_is_not_clean():
    """No evidence and no transcript words: there is no rung to stand on, and
    every kept span is unsupported rather than verified."""
    ctx = _ctx(evidence=None, words=())
    report = verify([(0.00, 3.40)], ctx)
    assert Fault.UNSUPPORTED_SPAN in _faults(report)
    assert report.verdict == Verdict.FAIL


# ── LOW_CONFIDENCE_ONLY ───────────────────────────────────────────────────────


def test_low_confidence_only_names_what_was_discarded():
    """Support exists but only below the floor. The finding reports the
    discarded claims, per the memory layer's own rule that filters report
    rather than repair. Each unsupported interval is named separately, so a
    plan of two below-floor spans carries two findings, not one."""
    ctx = _ctx(evidence=_speech(((0.00, 1.70), (2.30, 3.40)), confidence=0.5))
    findings = check_low_confidence_only([(0.00, 1.70), (2.30, 3.40)], ctx)
    assert [f.fault for f in findings] == [Fault.LOW_CONFIDENCE_ONLY] * 2
    assert "speech regions from VAD" in findings[0].detail
    assert "speech regions from VAD" in findings[1].detail


def test_confidence_is_trimmed_never_inflated():
    """Below the floor everywhere, the trim has nowhere above-floor to keep, so
    no interval is deleted and nothing is inflated: each span escalates, the
    plan stands, and the loop reports FAIL — the confidence is a property of the
    media the loop cannot repair, and calling that REVIEW would understate it."""
    ctx = _ctx(evidence=_speech(((0.00, 1.70), (2.30, 3.40)), confidence=0.5))
    before = measure([(0.00, 1.70), (2.30, 3.40)], ctx)
    result = run_loop([(0.00, 1.70), (2.30, 3.40)], ctx)
    assert result.verdict == Verdict.FAIL
    assert result.plan == ((0.00, 1.70), (2.30, 3.40))
    assert all(r.repair == Repair.ESCALATE for r in result.repairs)
    assert result.escalated
    # Nothing was invented: the measure is unchanged and no confidence moved.
    assert measure(list(result.plan), ctx) == before
    # The fault the editor must see is named, not buried in a generic escalate.
    assert any(f.fault == Fault.LOW_CONFIDENCE_ONLY for f in result.findings)


def test_a_partly_above_floor_span_trims_to_what_it_can_keep():
    """The honest middle case: one interval is above the floor and one is
    below. The repair keeps the above-floor span and then escalates, because
    the speech that would complete the sentence sits below the floor and
    cutting to the floor would cut the thought in half."""
    from engine.memory.evidence import Evidence
    from engine.memory.intervals import IntervalSet

    evidence = EvidenceSet.of(
        Evidence(claim="speech regions from VAD",
                 intervals=IntervalSet.of((0.00, 1.70)),
                 modality=Modality.AUDIO,
                 source=Source.of("engine.coverage", "vad", min_duration=0.25),
                 confidence=0.9,
                 rationale="VAD speech regions measured on the source audio"),
        Evidence(claim="speech regions from VAD",
                 intervals=IntervalSet.of((1.70, 3.40)),
                 modality=Modality.AUDIO,
                 source=Source.of("engine.coverage", "vad", min_duration=0.25),
                 confidence=0.5,
                 rationale="VAD speech regions measured on the source audio"),
    )
    ctx = _ctx(evidence=evidence)
    result = run_loop([(0.00, 3.40)], ctx)
    # Trimmed to the above-floor span...
    assert result.plan == ((0.00, 1.70),)
    # ...and then escalated rather than shipped. Extending the clip back to the
    # sentence's end would re-enter below-floor evidence, so the snap is refused
    # and the mid-sentence fault is converted to UNDECIDABLE carrying the reason
    # (I2/I4): the editor is told the thought is open *and* why this loop cannot
    # close it, rather than being handed a clip the evidence does not back.
    assert result.verdict == Verdict.REVIEW
    assert result.escalated
    assert any("mid-sentence" in e.detail for e in result.escalated), (
        [e.detail for e in result.escalated])
    # The escalation's record names the evidence as the reason the snap was
    # refused, so the editor can see the floor is what stands in the way.
    assert any("outside the ground truth" in (r.note or "") for r in result.repairs), (
        [r.note for r in result.repairs])


def test_low_confidence_check_is_silent_without_an_evidence_store():
    """A confidence floor with no confidence to read would fabricate the
    finding, so the rung that does not exist reports nothing — the G2
    degradation belongs to the support check."""
    ctx = _ctx(evidence=None, confidence_floor=0.8)
    assert check_low_confidence_only([(0.00, 3.40)], ctx) == ()


def test_trimming_a_plan_to_empty_escalates():
    """A trim that would leave the plan shipping nothing is not applied: an
    empty plan is a clip with no footage, and the editor decides that."""
    ctx = _ctx(evidence=_speech(((0.00, 3.40),), confidence=0.5))
    result = run_loop([(0.00, 3.40)], ctx)
    assert result.plan == ((0.00, 3.40),)
    assert result.verdict == Verdict.REVIEW
    assert any(r.repair == Repair.ESCALATE for r in result.repairs)
    assert result.escalated


# ── DUPLICATE_SENTENCE ────────────────────────────────────────────────────────


def _sibling(text: str, rank: float) -> SiblingClip:
    return SiblingClip(text=text, rank=rank)


_DUPLICATE = ("You mentioned the oil fields in the east of Syria.")


def test_duplicate_sentence_trimmed_from_the_lower_ranked_clip():
    """The same sentence ships twice; the lower-ranked clip gives it up. The
    ranking is the candidate's own — re-scoring to make a check pass is the
    forbidden move."""
    ctx = _ctx(siblings=(_sibling(_DUPLICATE, 0.9),), rank=0.5)
    plan = [(0.00, 3.40), (3.60, 5.40)]
    findings = check_duplicate_sentence(plan, ctx)
    assert [f.fault for f in findings] == [Fault.DUPLICATE_SENTENCE]
    assert findings[0].interval_index == 0

    result = run_loop(plan, ctx)
    assert result.verdict == Verdict.CLEAN
    # Interval 1 opened at 3.60 s, inside the second utterance, so the loop
    # snaps it back to that utterance's own opening: the plan the duplicate
    # trim leaves behind is still a plan with an edge to repair.
    assert result.plan == ((3.40, 5.40),)


def test_duplicate_sentence_escalated_from_the_higher_ranked_clip():
    """This clip outranks the other one, so the duplicate is the other clip's
    to give up. Trimming the better clip to satisfy a check is exactly the
    editorial decision the loop is forbidden to make, so no interval is named
    and the fault escalates."""
    ctx = _ctx(siblings=(_sibling(_DUPLICATE, 0.5),), rank=0.9)
    result = run_loop([(0.00, 3.40), (3.60, 5.40)], ctx)
    assert result.plan == ((0.00, 3.40), (3.60, 5.40))
    assert result.verdict == Verdict.REVIEW
    assert result.escalated
    assert result.escalated[0].fault == Fault.UNDECIDABLE


def test_a_sentence_too_short_to_be_one_is_not_a_duplicate():
    """The 24-character floor keeps stock phrases from counting as duplicates
    of themselves."""
    ctx = _ctx(siblings=(_sibling("the oil fields too.", 0.9),), rank=0.5)
    assert check_duplicate_sentence([(0.00, 3.40)], ctx) == ()


# ── captions ──────────────────────────────────────────────────────────────────


def test_caption_text_drift_detected():
    """A cue word the plan's own transcript does not carry. The plan is never
    touched: the captions are re-emitted from ``join_words``, never patched."""
    from engine.verify import Cue

    ctx = _ctx(cues=(Cue(text="elephant mentioned the oil fields", start=0.0,
                         end=1.5),))
    report = verify([(0.00, 3.40)], ctx)
    assert Fault.CAPTION_TEXT_DRIFT in _faults(report)

    result = run_loop([(0.00, 3.40)], ctx)
    assert result.plan == ((0.00, 3.40),)
    assert [r.repair for r in result.caller_actions] == [Repair.REEMIT]
    assert result.caller_actions[0].instrument == "engine.edit_plan.join_words"
    # The cue text did not change inside this call, so the re-derive did not
    # clear the fault and it escalates rather than looping.
    assert result.verdict == Verdict.REVIEW
    assert any("re-derivation did not clear" in e.detail
               for e in result.escalated)


def test_caption_text_from_the_plan_is_clean():
    from engine.verify import Cue

    ctx = _ctx(cues=(Cue(text="you mentioned the oil fields", start=0.0,
                         end=1.5),))
    assert Fault.CAPTION_TEXT_DRIFT not in _faults(verify([(0.00, 1.50)], ctx))


def test_caption_span_drift_detected():
    """A cue ending past the render is a cue the viewer never sees."""
    from engine.verify import Cue, RenderArtifact

    over = RenderArtifact(duration=10.0)
    ctx = _ctx(render=over,
               cues=(Cue(text="you mentioned", start=0.0, end=11.0),))
    findings = verify([(0.00, 1.50)], ctx).findings
    assert Fault.CAPTION_SPAN_DRIFT in _faults(findings)


def test_caption_within_tolerance_is_clean():
    """A cue ending inside ``CAPTION_TOLERANCE_S`` of the render's end is on
    the end, not past it — ffprobe rounds, and an exact comparison would fail
    every cue."""
    from engine.verify import Cue, RenderArtifact

    ctx = _ctx(render=RenderArtifact(duration=10.0),
               cues=(Cue(text="you mentioned", start=0.0,
                         end=10.0 + CAPTION_TOLERANCE_S / 2),))
    assert Fault.CAPTION_SPAN_DRIFT not in _faults(verify([(0.00, 1.50)], ctx))


def test_caption_fit_is_undecidable_without_a_render():
    """Cues exist but no render duration was supplied: the fit cannot be
    decided, and that is REVIEW, not clean."""
    from engine.verify import Cue

    ctx = _ctx(cues=(Cue(text="you mentioned", start=0.0, end=11.0),))
    findings = verify([(0.00, 3.40)], ctx).findings
    assert Fault.UNDECIDABLE in _faults(findings)
    assert verify([(0.00, 3.40)], ctx).verdict == Verdict.REVIEW


# ── interchange and record grid ───────────────────────────────────────────────


def test_interchange_interval_count_mismatch():
    """An export that dropped a middle interval reads the same total duration,
    so only a count comparison notices — every multi-interval cut in the
    corpus has its extra interval in the middle."""
    from engine.verify import InterchangeArtifact

    ctx = _ctx(interchange=InterchangeArtifact(
        kind="edl", intervals=((0.00, 3.40), (3.60, 5.40))))
    findings = check_interchange_drift([(0.00, 1.50), (2.30, 3.40), (3.60, 5.40)],
                                       ctx)
    assert [f.fault for f in findings] == [Fault.INTERCHANGE_DRIFT]


def test_interchange_endpoint_drift_beyond_tolerance():
    """An interval off the plan by more than ``INTERCHANGE_FRAME_TOLERANCE``
    frames is a moved cut, not a rounding of one."""
    from engine.verify import InterchangeArtifact

    ctx = _ctx(interchange=InterchangeArtifact(
        kind="fcpxml", intervals=((0.00, 3.40), (3.70, 5.40))))
    findings = check_interchange_drift([(0.00, 3.40), (3.60, 5.40)], ctx)
    assert [f.fault for f in findings] == [Fault.INTERCHANGE_DRIFT]
    # 0.1 s at 30 fps is 3.0 frames, twice the tolerance.
    assert "3.00 frames" in findings[0].detail


def test_interchange_within_frame_tolerance_is_clean():
    """The export snaps each interval to the frame grid independently, so an
    interval a frame off the plan is the same cut. The corpus's worst
    deviation is exactly 1.00 frame and the tolerance leaves half a frame."""
    from engine.verify import InterchangeArtifact

    ctx = _ctx(interchange=InterchangeArtifact(
        kind="fcpxml", intervals=((0.00, 3.40), (3.6333, 5.40))))
    findings = check_interchange_drift([(0.00, 3.40), (3.60, 5.40)], ctx)
    assert findings == ()


def test_record_overlap_detected():
    """The record grid must advance per channel; a correct plan cannot produce
    an overlap because the cursor accumulates over kept durations only."""
    from engine.verify import InterchangeArtifact, check_record_overlap

    ctx = _ctx(interchange=InterchangeArtifact(
        kind="edl", intervals=((0.00, 1.50), (2.30, 3.40)),
        record_spans=((0.00, 1.50), (1.40, 2.50))))
    findings = check_record_overlap([(0.00, 1.50), (2.30, 3.40)], ctx)
    assert [f.fault for f in findings] == [Fault.RECORD_OVERLAP]


# ── render and media ──────────────────────────────────────────────────────────


def test_render_drift_detected():
    """The container is off the plan by more than 1%: an interval was dropped
    or duplicated. This is the only check that sees a wrong cut rather than a
    broken file."""
    from engine.verify import RenderArtifact

    ctx = _ctx(render=RenderArtifact(duration=3.00))
    findings = check_render_drift([(0.00, 3.40)], ctx)
    assert [f.fault for f in findings] == [Fault.RENDER_DRIFT]
    assert "11.8%" in findings[0].detail


def test_render_drift_within_tolerance_is_clean():
    from engine.verify import RenderArtifact

    ctx = _ctx(render=RenderArtifact(
        duration=3.40 * (1 + CONTAINER_DURATION_TOLERANCE / 2)))
    assert check_render_drift([(0.00, 3.40)], ctx) == ()


def test_render_undecodable_detected():
    """A declared stream that decodes to zero frames is a broken render, and
    the repair is a stream-copy remux the caller performs."""
    from engine.verify import RenderArtifact, check_render_decodable

    ctx = _ctx(render=RenderArtifact(duration=3.40, decoded_frames=0))
    findings = check_render_decodable([(0.00, 3.40)], ctx)
    assert [f.fault for f in findings] == [Fault.RENDER_UNDECODABLE]

    result = run_loop([(0.00, 3.40)], ctx)
    assert [r.repair for r in result.caller_actions] == [Repair.REMUX]
    assert all("stream copy" in r.instrument for r in result.caller_actions)


def test_render_not_sampled_is_review_not_clean():
    """A decode sample the caller did not run is unmeasured. A check that
    passes while its validator is absent is how a self-consistent-but-wrong
    emitter ships unchallenged."""
    from engine.verify import RenderArtifact

    ctx = _ctx(render=RenderArtifact(duration=3.40))
    findings = check_render_decodable([(0.00, 3.40)], ctx)
    assert [f.fault for f in findings] == [Fault.UNDECIDABLE]
    assert findings[0].verdict == Verdict.REVIEW


def test_silent_render_is_not_undecodable():
    """A stream that decodes to frames is fine; silence is data, and a remux of
    a silent file is a silent file."""
    from engine.verify import RenderArtifact

    ctx = _ctx(render=RenderArtifact(duration=3.40, decoded_frames=240))
    assert check_render_decodable([(0.00, 3.40)], ctx) == ()


def test_media_unresolvable_detected():
    """An asset the NLE cannot relink puts every clip offline; the repair is a
    re-resolution off the artifact's own FROM CLIP and a re-emit."""
    from engine.verify import InterchangeArtifact, check_media_resolvable

    ctx = _ctx(interchange=InterchangeArtifact(
        kind="edl", intervals=((0.00, 3.40),),
        media_src="moved/source.mp4", media_resolvable=False))
    findings = check_media_resolvable([(0.00, 3.40)], ctx)
    assert [f.fault for f in findings] == [Fault.MEDIA_UNRESOLVABLE]
    assert "moved/source.mp4" in findings[0].detail


# ── DIAGNOSE ──────────────────────────────────────────────────────────────────


def test_diagnose_names_the_channel_and_the_permitted_repairs():
    """A diagnosis says which channel failed and which repairs are allowed, and
    the permitted set never contains an operation outside the closure."""
    ctx = _ctx()
    f = check_mid_word_cuts([(0.00, 1.15)], ctx)[0]
    d = diagnose(f, ctx)
    assert d.modality == Modality.TEXT
    assert d.permitted == (Repair.SNAP,)
    assert Fault.UNDECIDABLE not in d.permitted


def test_diagnose_reads_the_channel_of_the_missing_evidence():
    """``UNSUPPORTED_SPAN`` on the audio channel and on the visual channel are
    different defects even though the fault class is the same, so the diagnosis
    reads the modality off the evidence that did not cover the span."""
    visual = EvidenceSet.of(Evidence(
        claim="face present", intervals=IntervalSet.of((0.00, 2.00)),
        modality=Modality.VISUAL, source=Source.of("engine.reframe", "track"),
        confidence=0.9))
    ctx = _ctx(evidence=visual)
    f = check_unsupported_spans([(0.00, 3.40)], ctx)[0]
    assert diagnose(f, ctx).modality == Modality.VISUAL


# ── the loop's own failure modes ──────────────────────────────────────────────


def test_clean_plan_verifies_clean_with_no_repairs():
    """A plan aligned to word and utterance edges, fully backed by evidence:
    one pass, no repairs, nothing escalated."""
    result = run_loop([(0.00, 3.40)], _ctx())
    assert result.verdict == Verdict.CLEAN
    assert result.repairs == ()
    assert result.passes == 1
    assert result.capped is False


def test_reemit_that_does_not_clear_converts_to_undecidable():
    """The termination guard (§5, I2). An exporter whose intervals disagree
    with the plan is told to re-emit; inside one call the artifact facts cannot
    change, so the fault recurs and is converted to ``UNDECIDABLE`` and
    escalated — the loop terminates instead of re-deriving forever."""
    from engine.verify import InterchangeArtifact

    ctx = _ctx(interchange=InterchangeArtifact(
        kind="edl", intervals=((0.00, 3.40), (3.70, 5.40))))
    result = run_loop([(0.00, 3.40), (3.60, 5.40)], ctx)
    assert result.passes <= len(_WORDS) + 3
    assert result.verdict == Verdict.REVIEW
    assert any(r.repair == Repair.REEMIT for r in result.caller_actions)
    assert any("re-derivation did not clear" in e.detail
               for e in result.escalated)
    # And the plan was never touched by an artifact repair.
    assert result.plan == ((0.00, 3.40), (3.60, 5.40))


def test_a_repair_that_moves_nothing_escalates():
    """A finding whose span does not reach the interval it names is the symptom
    of a transcript/plan desync. Retrying it would loop forever, so the repair
    is reported as the no-op it is and the fault escalates (§5, I4)."""
    from engine.memory.intervals import Interval
    from engine.verify import Finding

    plan = [(0.00, 1.50)]
    finding = Finding(fault=Fault.UNSUPPORTED_SPAN, verdict=Verdict.FAIL,
                      stage="check_unsupported_spans", interval_index=0,
                      edge="whole", span=Interval(50.0, 51.0),
                      detail="desynced", instrument="IntervalSet.gaps")
    new_plan, record = apply_repair(plan, finding, _ctx())
    assert new_plan == plan
    assert record.changed_plan is False
    assert record.note.endswith("the repair is a no-op")


def test_hitting_the_cap_is_reported_as_a_failure():
    """The cap is a bound, and reaching it is the loop's own failure —
    observable, not a silent stop."""
    result = run_loop([(0.00, 1.15)], _ctx(), max_passes=0)
    assert result.capped is True
    assert result.verdict == Verdict.FAIL
    assert result.repairs == ()


def test_measure_decreases_under_every_plan_repair():
    """The termination argument (§5, I1) as a number: a snap drives the
    boundary distance to zero, a trim reduces kept time. Both strictly."""
    ctx = _ctx(evidence=_speech(((0.00, 2.00),)))
    snap = run_loop([(0.00, 3.45)], _ctx())
    assert measure(list(snap.plan), ctx)[1] < measure([(0.00, 3.45)], ctx)[1]
    trim = run_loop([(0.00, 3.40)], ctx)
    assert measure(list(trim.plan), ctx)[0] < measure([(0.00, 3.40)], ctx)[0]


def test_repair_never_invents_a_span():
    """SNAP and TRIM only remove or align: the repaired plan never covers time
    the editor did not select, save the utterance edge a snap moves a start
    back to."""
    from engine.memory.intervals import IntervalSet

    ctx = _ctx()
    initial = IntervalSet.of((0.00, 3.45))
    result = run_loop([(0.00, 3.45)], ctx)
    grown = IntervalSet.of(*result.plan).subtract(initial)
    assert grown.intervals == [] or all(
        iv.start >= 0.0 for iv in grown.intervals)


def test_repair_records_are_auditable():
    """Every record carries the fault, the check, the instrument and the
    before/after of the interval it touched — the ``repairs.jsonl`` contract."""
    result = run_loop([(0.00, 3.45)], _ctx())
    assert len(result.repairs) == 1
    rec = result.repairs[0]
    assert rec.fault == Fault.MID_WORD_CUT
    assert rec.stage == "check_mid_word_cuts"
    assert rec.instrument
    assert rec.changed_plan is True
    assert rec.before != rec.after


def test_the_loop_is_pure_and_deterministic():
    """Identical input, byte-identical output: two runs over the same plan and
    context agree on the plan, the findings, the records and the pass count.
    A loop that changed its verdict on identical input would be the
    non-determinism the project has committed to eliminate."""
    ctx = _ctx(evidence=_speech(((0.00, 2.00),)))
    args = ([(0.00, 3.40)], ctx)
    first = run_loop(*args)
    second = run_loop(*args)
    assert first == second
    assert verify([(0.00, 3.40)], ctx) == verify([(0.00, 3.40)], ctx)


def test_re_verify_cannot_pass_an_unchanged_problem():
    """The adversarial case the brief names directly: a re-verify that reports
    clean while the underlying problem is unchanged. Here the interchange still
    disagrees after its recorded re-emit, so the fault survives in the final
    findings as ``UNDECIDABLE`` — escalated, never absorbed into a pass."""
    from engine.verify import InterchangeArtifact

    ctx = _ctx(interchange=InterchangeArtifact(
        kind="edl", intervals=((0.00, 3.40), (3.70, 5.40))))
    result = run_loop([(0.00, 3.40), (3.60, 5.40)], ctx)
    assert result.verdict != Verdict.CLEAN
    assert Fault.INTERCHANGE_DRIFT not in {f.fault for f in result.findings}
    assert Fault.UNDECIDABLE in {f.fault for f in result.findings}
