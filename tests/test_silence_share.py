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

from tools.editorial_audit import (  # noqa: E402
    QUIET_DBFS,
    SILENCE_SHARE_MAX,
    check_silence_share,
    measure_audio,
    word_free_finding,
)


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


def test_share_is_bounded_so_no_out_of_range_value_can_be_read_as_a_bug():
    """The docstring used to say a negative share 'means word spans run outside
    the intervals, itself a bug'. That signal is unreachable: each overlap term
    is clamped against its interval, so ``spoken`` cannot exceed ``kept`` and
    share is always in [0, 1]. A span outside every interval reads as 1.0.

    Pinning the range matters because an unclamped computation — summing raw
    word durations instead of the clamped overlap — would make the metric
    negative on exactly that case, and a negative share passes the 0.30 bar
    while looking like a diagnostic. That is a wrong PASS dressed as a signal.
    """
    cases = [
        ("span entirely after the interval", [(10.0, 20.0)], [(0.0, 1.0)]),
        ("span straddling the interval end", [(0.5, 5.0)], [(0.0, 1.0)]),
        ("interval inside a single span", [(0.0, 10.0)], [(2.0, 3.0)]),
        ("span straddling the interval start", [(0.0, 5.0)], [(3.0, 4.0)]),
    ]
    for name, words, intervals in cases:
        ok, share = check_silence_share(_cand("w " * len(words), words), intervals)
        assert 0.0 <= share <= 1.0, f"{name}: share {share} left [0, 1]"
    # The fully-outside case is the one a raw-duration sum would make negative.
    ok, share = check_silence_share(_cand("w", [(10.0, 20.0)]), [(0.0, 1.0)])
    assert share == 1.0 and not ok, (
        f"a span outside every interval should read as all word-free, got {share}"
    )


def test_a_failure_with_no_render_to_decode_reports_unmeasured_not_a_cause():
    """The word "silence" in this metric's name is the defect, not the reading.

    On nasa360-ep5 clip 05 a 40.8% figure was explained first as an editorial
    pause and then as dead air to be trimmed. Both were invented from the word
    timings; measuring the window showed a loud music bed (mean −31.4 dB,
    0.00 s of true silence; `docs/METRICS.md` A2.4). The audit's own output line
    must not assert a cause it has not measured, because that is how the second
    explanation nearly became a change that cut music out of a shipped clip.
    """
    # The label is composed in word_free_finding, not in the check itself, so
    # this calls it the same way the report loop does, with no measurement.
    cand = _cand("w w w", [(0.0, 2.0), (2.1, 4.0), (9.0, 11.0)])
    ok, share = check_silence_share(cand, [(0.0, 11.0)])
    assert not ok
    state, line = word_free_finding(share, None)
    assert state == "FAIL", "an unmeasurable window must stay a failure"
    assert "dead air" not in line
    assert "unmeasured" in line
    # No figures are quoted, because none exist: a dB number on this line would
    # have to come from a decode that never happened.
    assert "-60" not in line and "dB" not in line


# --- acoustic measurement of the shipped render ----------------------------


def _pcm(seconds: float, amplitude: float, rate: int = 16000) -> list[int]:
    """A deterministic buffer: a full-scale square-ish tone scaled by amplitude.

    Synthesised rather than decoded so the measurement is tested against known
    numbers instead of against a fixture nobody can re-derive.
    """
    import math

    n = int(seconds * rate)
    return [
        int(32767 * amplitude * (1.0 if (i // 200) % 2 else -1.0))
        for i in range(n)
    ]


def test_a_silent_buffer_reads_as_silent_and_corroborates_dead_air():
    m = measure_audio(_pcm(2.0, 10 ** (-70 / 20)), 16000)
    assert m is not None
    assert m["dur"] == pytest.approx(2.0, abs=1e-3)
    assert m["quiet_share"] == pytest.approx(1.0)
    assert m["rms_db"] < QUIET_DBFS
    state, line = word_free_finding(0.55, m)
    assert state == "FAIL"
    assert "dead air corroborated" in line


def test_a_loud_buffer_reads_as_content_and_downgrades_to_review():
    m = measure_audio(_pcm(2.0, 10 ** (-20 / 20)), 16000)
    assert m["quiet_share"] == pytest.approx(0.0, abs=1e-6)
    assert m["rms_db"] == pytest.approx(-20.0, abs=0.5)
    state, line = word_free_finding(0.55, m)
    assert state == "REVIEW"
    # The figures quoted are the ones measured, so an editor can check them.
    assert f"= {m['quiet_share']:.1%} below" in line
    assert f"RMS {m['rms_db']:.1f} dB" in line
    assert "dead air corroborated" not in line


def test_a_window_at_exactly_the_bar_stays_review():
    """The classification bar is the same 30% as the word-coverage bar, so a
    window sitting on it is REVIEW rather than a pass by rounding."""
    m = measure_audio(_pcm(2.0, 10 ** (-20 / 20)), 16000)
    m["quiet_s"] = SILENCE_SHARE_MAX * m["dur"]
    m["quiet_share"] = SILENCE_SHARE_MAX
    state, _line = word_free_finding(0.40, m)
    assert state == "REVIEW"


def test_an_empty_buffer_is_no_measurement_not_a_silence_reading():
    """Zero samples cannot support a claim either way, so it returns None and
    the finding falls back to unmeasured rather than to a clean reading."""
    assert measure_audio([], 16000) is None
    assert measure_audio(_pcm(1.0, 0.001), 0) is None
    state, line = word_free_finding(0.55, None)
    assert state == "FAIL" and "unmeasured" in line


def test_decoding_is_skipped_when_ffmpeg_is_absent(tmp_path, monkeypatch):
    """A machine without ffmpeg must report the window as unmeasured, not raise
    and not silently pass it."""
    from tools.editorial_audit import _decode_render_audio, measure_clip_render

    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert _decode_render_audio(tmp_path / "nope.mp4") is None
    assert measure_clip_render(tmp_path, 1) is None


def test_a_render_that_does_not_exist_is_no_measurement(tmp_path):
    from tools.editorial_audit import measure_clip_render

    assert measure_clip_render(tmp_path, 1) is None


def test_a_real_render_decodes_to_measurable_pcm(tmp_path):
    """End to end against ffmpeg, guarded so it skips where ffmpeg is missing
    rather than failing the suite on a machine that does not have it."""
    import shutil
    import subprocess

    pytest.importorskip("wave")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")

    # A one-second 1 kHz tone at -20 dBFS, muxed as the audit's renders are.
    raw = tmp_path / "tone.wav"
    samples = _pcm(1.0, 10 ** (-20 / 20))
    import wave

    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(s.to_bytes(2, "little", signed=True) for s in samples))
    mp4 = tmp_path / "facebook" / "01.mp4"
    mp4.parent.mkdir()
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-c:a", "aac",
         "-b:a", "128k", str(mp4)],
        capture_output=True, check=True, timeout=180,
    )
    from tools.editorial_audit import measure_clip_render

    m = measure_clip_render(tmp_path, 1)
    assert m is not None, "ffmpeg is installed but the decode failed"
    # AAC round-trips the level closely, so this is a tolerance on a codec, not
    # on the measurement: a silent or full-scale buffer would miss by 20+ dB.
    assert m["dur"] == pytest.approx(1.0, abs=0.05)
    assert m["rms_db"] == pytest.approx(-20.0, abs=3.0)
    assert m["quiet_share"] == pytest.approx(0.0, abs=0.05)


# --- the two committed clips, measured against their shipped renders --------


@pytest.mark.parametrize("project, index, expected", [
    ("nasa360-ep5_run", 4, 0.408),
    ("telugu-tutorial_run", 0, 0.463),
])
def test_the_two_over_bar_clips_are_reviewed_with_measured_figures(
    project, index, expected,
):
    """Both clips fail P5's 30% word-free bar, and both renders decode to
    content rather than to silence — which is exactly the finding the audit
    used to assert without measuring.

    This is a real integration test against the committed renders, not a
    restatement of the doc: the dB figures are produced here by decoding the
    shipped mp4. ``rms_max`` is loose because the point is the sign of the
    conclusion (content, not digital quiet), not the level.
    """
    import shutil

    from engine.edit_plan import retention_intervals
    from models.core import Candidate, Word

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed; the render cannot be decoded")

    project_dir = ROOT / "corpus" / project / "project"
    selections = json.loads((project_dir / "selections.json").read_text())
    src = selections[index]
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
    assert not ok
    assert share == pytest.approx(expected, abs=2e-3)

    from tools.editorial_audit import measure_clip_render

    measured = measure_clip_render(project_dir, index + 1)
    assert measured is not None, "the committed render failed to decode"
    assert measured["quiet_share"] < SILENCE_SHARE_MAX, (
        f"{project} clip {index + 1}: {measured['quiet_s']:.2f}s below "
        f"{QUIET_DBFS:.0f} dBFS of a {measured['dur']:.2f}s render — the window "
        f"is dead air, not content, and the REVIEW classification is wrong"
    )
    assert measured["rms_db"] > QUIET_DBFS, (
        f"{project} clip {index + 1}: render RMS {measured['rms_db']:.1f} dB is "
        f"under {QUIET_DBFS:.0f} dBFS, which contradicts a window of content"
    )
    state, line = word_free_finding(share, measured)
    assert state == "REVIEW"
    assert "unmeasured" not in line
    assert "dead air corroborated" not in line


@pytest.mark.parametrize("project", ["nasa360-ep5_run", "telugu-tutorial_run"])
def test_review_does_not_clear_the_exit_code(project):
    """A REVIEW is still a nonzero exit: the clip is over the bar and a human
    has not listened to it yet. Softening this would turn a measurement into a
    silent pass, which is the defect the state was added to remove."""
    import shutil

    from tools.editorial_audit import audit

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")

    project_dir = ROOT / "corpus" / project / "project"
    ok, rep = audit(project_dir)
    assert not ok, "an over-bar clip exited clean"
    # The telugu REVIEW is the acoustic-decode branch of ``word_free_finding``,
    # which has to measure the shipped render to contradict the dead air. A
    # clean checkout has no renders — .gitignore excludes them — and the clip
    # then reads as FAIL, the *other* nonzero state, which still clears nothing.
    # The exit-code assertion above is the part that holds everywhere; the
    # REVIEW content needs the media (see test_render.py for the same guard).
    if not any(project_dir.glob(f"{kind}/*.mp4") for kind in ("facebook", "shorts")):
        pytest.skip(
            "the REVIEW findings this asserts need the shipped renders, which "
            ".gitignore excludes; run the corpus to produce them")
    assert rep["review"], "an over-bar clip produced no REVIEW finding"
    for name in rep["review"]:
        assert name.startswith("clip "), f"{name} is not a clip finding"
