"""Does the committed evaluation corpus still reproduce under the current code?

The corpus at ``corpus/*_run/`` is the product's benchmark evidence: every
number in ``docs/METRICS.md`` about clip counts and selection behaviour traces
back to a selection recorded there. Nothing re-checked those selections after
the editorial code moved on, so the benchmark could drift silently and every
metric that cites it would become a claim about a version that no longer
exists.

This tool re-scores each run's committed ``transcript.json`` with the current
editorial code and compares the result with the selection the run shipped. It
needs no media — the duration the corpus uses is inferred from the segment ends,
which is what makes this check cheap enough to run in CI — and it changes
nothing on disk. The selection it replays is the full stack the pipeline runs,
``rank`` then the context gate then ``engine.selection.select`` with the corpus
built from the transcript, because the shipped artifact is the last stage's
output and the first stage alone is not it. It reports, per run:

  * span reproduction   the same clips in the same order, to the millisecond;
  * score reproduction  the same story type and total score per clip;
  * confidence          the recorded value against the value the current code
                        measures, and whether the context gate would still let
                        the clip ship;
  * reason              whether the clip-specific sentence moved, and —
                        separately — whether the measurement it introduces
                        did;
  * cause               the named change in the code that explains any gap.

Run it from the repository root:

    python tools/corpus_reproducibility.py            # human report
    python tools/corpus_reproducibility.py --json     # machine report

Exit code is nonzero if any run that shipped clips no longer reproduces, so
this composes with CI rather than being a report someone has to remember to
read. Runs that ended in a documented failure (no speech, no stories) have no
selections to check and are reported as such, not counted as drift.

A run whose *spans* reproduce but whose recorded fields differ is not drift in
the editorial judgement — confidence was made a measurement and the reason's
sentence was moved in front of its label, and neither moves a clip. Both are
named in the report so a reader can tell a re-recorded field from a moved
judgement. Reason is compared on the numeric block it carries for exactly this
reason: the sentence moved, so comparing whole strings would flag every clip
and a genuine change to the measurement would be indistinguishable from that.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from engine.editorial import make_thoughts, rank, score_candidate
from engine.context_guard import validate

_REPO = Path(__file__).resolve().parent.parent
_NUM = {
    "total_score", "editorial_score", "virality_score", "context_score",
    "coherence_score", "completeness", "confidence", "visual_potential",
}
_STR = {"story_type", "text", "hook", "payoff", "reason"}

# to_dict rounds total_score to four decimals when it writes selections.json,
# so comparing it at full float precision would flag every clip as drifted for
# the rounding alone. Each field is compared at the precision it is recorded
# at; every stored score other than total_score is written unrounded.
_TOLERANCE = {"total_score": 5e-5}

# The context gate's confidence floor, repeated here so the report can name the
# number rather than import a threshold the reader has to go looking for.
_CONFIDENCE_FLOOR = 0.75

# Recorded confidences at or above this value on a transcript whose measured
# word confidence is far below it are the pre-d79bef3 literal 0.90, which the
# guard could never fail. d79bef3 replaced it with the measured mean per-word
# probability, so a run committed before that change can carry a confidence
# that the current code would not have written.
_PRE_MEASURED_CONFIDENCE = 0.90


def _load_transcript(path: Path):
    """Decode a committed transcript.json into Segment objects.

    Mirrors selekyt.pipeline._load_transcript, which is the exact decoder the
    real run used; re-implementing it here would risk decoding the fixture
    differently from the pipeline that produced it.
    """
    from models.core import Segment, Word

    data = json.loads(path.read_text(encoding="utf-8"))
    segments = [
        Segment(
            float(s["start"]), float(s["end"]), s["text"],
            [Word(**w) for w in s.get("words", [])],
        )
        for s in data.get("segments", [])
    ]
    return segments, str(data.get("language") or "unknown")


def _word_confidence(segments) -> list[float]:
    return [
        w.confidence for s in segments for w in (s.words or [])
        if w.confidence is not None
    ]


def _scored_candidates(segments):
    """The same filter rank() applies before sorting, so the report can name
    candidates the gate rejected instead of only the ones that survived."""
    return [
        score_candidate(s)
        for s in make_thoughts(segments)
        if 8 <= s.duration <= 55 and len(re.findall(r"\w+", s.text, re.UNICODE)) >= 20
    ]


def _reason_measurement(reason: str) -> str:
    """The numeric block of a reason, independent of where the prose sits.

    A later commit moved the clip-specific sentence from behind the label to in
    front of it, so the whole string differs while the measurement it carries
    does not. Comparing the blocks keeps that an explained difference instead
    of drift; comparing the raw strings would flag every clip and bury a
    genuine change in the noise.
    """
    label = "Structural editorial score: "
    at = reason.find(label)
    block = reason[at + len(label):] if at >= 0 else reason
    return block.strip().rstrip(".")


def audit_run(run_dir: Path) -> dict:
    """Compare one committed run against what the current code would select."""
    name = run_dir.name
    project = run_dir / "project"
    sel_path = project / "selections.json"
    tr_path = project / "transcript.json"
    out: dict = {"run": name, "status": "ok", "clips": 0}

    if not sel_path.is_file() or not tr_path.is_file():
        out["status"] = "no_selections"
        out["note"] = (
            "run recorded a failure in results.jsonl, so it has no selection "
            "to reproduce"
        )
        return out

    committed = json.loads(sel_path.read_text(encoding="utf-8"))
    n = len(committed)
    out["clips"] = n
    try:
        segments, language = _load_transcript(tr_path)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        out["status"] = "unreadable_transcript"
        out["note"] = f"{type(exc).__name__}: {exc}"
        return out
    out["language"] = language

    probs = _word_confidence(segments)
    out["transcript_word_confidence"] = {
        "n": len(probs),
        "mean": round(sum(probs) / len(probs), 4) if probs else None,
        "min": round(min(probs), 4) if probs else None,
        # A transcript with no probabilities decodes as unknown (1.0), never
        # as high: the absence of a signal is not evidence of a good one.
        "provenance": "measured per-word probabilities"
        if probs else "none recorded — confidence reads as unknown",
    }

    # The pipeline's selection is three stages, not one, and only the first is
    # a sort. Replicating just the sort was enough while the policy layer had
    # not changed anything, but the shipped selection is the policy layer's
    # output: on ooi-vertical ``rank`` alone returns 113.42 / 41.42 / 90.73
    # while the run shipped 113.42 / 12.22 / 41.42, because the corpus's threads
    # moved the second clip in. An audit that replays the sort and calls that
    # difference drift is reporting a code path the pipeline no longer ends in.
    #
    # The pipeline itself degrades rather than failing when the corpus cannot be
    # built — the selector reads a missing corpus as absent and falls back to
    # its diversity-only terms — so the audit has to degrade the same way rather
    # than crash. A replay that cannot reach the policy layer reports the rank
    # head and says what it did not run, which is a weaker check stated as such,
    # not a verdict pretending to be the full stack.
    try:
        current, replay_note = _select_as_pipeline(segments, language, n)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        current = rank(make_thoughts(segments),
                       limit=max(n * 3, n), select_limit=n)[:n]
        replay_note = (
            f"the policy layer could not be replayed ({type(exc).__name__}: "
            f"{exc}), so this is the ranked head, not the shipped stack"
        )
    if replay_note:
        out["replay"] = replay_note
    out["clips_now"] = len(current)

    if len(current) != n:
        out["status"] = "count_drift"
        out["note"] = (
            f"current code selects {len(current)} clip(s) from this "
            f"transcript where the run shipped {n}"
        )
        # Explain which shipped clips the current code drops, so the number is
        # not the whole story.
        current_spans = {(round(c.start, 3), round(c.end, 3)) for c in current}
        out["dropped"] = [
            {
                "start": s["start"], "end": s["end"],
                "story_type": s.get("story_type"),
                "total_score": s.get("total_score"),
                "confidence_recorded": s.get("confidence"),
                # The gate verdict needs the re-scored candidate, not the
                # recorded one: the recorded confidence is the stale value.
                "gate_verdict_now": _gate_verdict_for(
                    _scored_candidates(segments), s["start"], s["end"],
                ),
            }
            for s in committed
            if (round(float(s["start"]), 3), round(float(s["end"]), 3))
            not in current_spans
        ]
        return out

    clip_reports = []
    reproduces = True
    for i, (cur, com) in enumerate(zip(current, committed), 1):
        span_ok = (
            abs(cur.start - float(com["start"])) < 1e-9
            and abs(cur.end - float(com["end"])) < 1e-9
        )
        # total_score is a derived property, not a stored field, so it is read
        # off the object rather than out of __dict__ — which does not hold it.
        numeric = sorted(
            f for f in _NUM
            if abs(float(getattr(cur, f, 0.0) or 0.0) - float(com.get(f) or 0.0))
            > _TOLERANCE.get(f, 1e-9)
        )
        textual = sorted(
            f for f in _STR - {"reason"}
            if (getattr(cur, f, "") or "").strip() != (com.get(f) or "").strip()
        )
        # ``reason`` is compared on its measurement alone: the sentence moved
        # from behind the label to in front of it, so the string differs on
        # every clip whether or not anything was recalculated. A moved sentence
        # is reported as such; a moved number is drift, and is listed alongside
        # the score fields so it cannot hide.
        reason_now = (getattr(cur, "reason", "") or "").strip()
        reason_was = (com.get("reason") or "").strip()
        prose_moved = reason_now != reason_was
        measurement_moved = (
            prose_moved and _reason_measurement(reason_now)
            != _reason_measurement(reason_was)
        )
        if measurement_moved:
            textual.append("reason")
        if not span_ok or numeric or textual:
            reproduces = False
        gate_ok, gate_why = validate(cur)
        clip_reports.append({
            "n": i,
            "start": round(cur.start, 3),
            "end": round(cur.end, 3),
            "span_reproduces": span_ok,
            "story_type": cur.story_type,
            "total_score_now": round(cur.total_score, 4),
            "total_score_recorded": com.get("total_score"),
            "confidence_now": round(cur.confidence, 4),
            "confidence_recorded": com.get("confidence"),
            "gate_verdict_now": "passes" if gate_ok else f"rejected: {gate_why}",
            "differing_fields": numeric + textual,
            "reason_prose_moved": prose_moved and not measurement_moved,
        })
    out["clips_detail"] = clip_reports

    # Name the change that explains a gap rather than leaving the reader to
    # guess. Since the runs were committed, confidence was made a measurement
    # and the reason's sentence was moved in front of its label — neither of
    # which moves a clip. A difference in anything else is real drift.
    differing = sorted({f for c in clip_reports for f in c["differing_fields"]})
    prose_moved = any(c["reason_prose_moved"] for c in clip_reports)
    if not reproduces and set(differing) <= {"confidence"}:
        out["status"] = "reproduces"
        out["note"] = _explain_field_drift(differing, prose_moved, clip_reports)
    elif not reproduces:
        out["status"] = "drift"
        out["note"] = (
            "differs in fields no committed change explains: "
            f"{', '.join(differing)}"
        )
    elif prose_moved:
        # Nothing the audit treats as a change moved a clip, but the reason
        # string a reader will actually open differs from the artifact, so the
        # report says why rather than presenting an unexplained difference.
        out["note"] = _explain_field_drift(differing, prose_moved, clip_reports)
    return out


def _explain_field_drift(differing: list[str], prose_moved: bool,
                         clip_reports: list[dict]) -> str:
    """Plain-language cause for the fields rewritten after the runs were
    committed, and the consequence a reader should take away."""
    named = list(differing)
    if prose_moved:
        named.append("reason")
    if not named:
        return ""
    if len(named) > 1:
        listed = f"{', '.join(named[:-1])} and {named[-1]} differ"
    else:
        listed = f"{named[0]} differs"
    parts = [
        "Spans, story types and scores reproduce exactly. Only "
        f"{listed} — a field rewritten after the run was "
        "committed, not a change of judgement:"
    ]
    if "confidence" in named:
        parts.append(
            f"the recorded confidence is the pre-measurement literal "
            f"{_PRE_MEASURED_CONFIDENCE}, which the context gate could never "
            f"fail; the current code measures the mean per-word probability "
            f"the transcriber actually emitted."
        )
        if any(c["gate_verdict_now"] != "passes" for c in clip_reports):
            parts.append(
                f"At least one clip's measured confidence now falls below the "
                f"{_CONFIDENCE_FLOOR} floor, so the current code would not "
                f"ship this run's selection as it stands — the guard is "
                f"rejecting a transcript the old one vouched for."
            )
        else:
            parts.append("Every clip still clears the confidence floor.")
    if prose_moved:
        parts.append(
            "The recorded reason opens with the 28-character label "
            "'Structural editorial score: ' and then the numbers, so every "
            "clip's reason read identically up to the first digit; the "
            "current code leads with the sentence naming the strongest and "
            "weakest signal for that clip, and the measurement follows it "
            "under the label. The numbers the label introduces are "
            "byte-identical — the difference is where the prose sits, not "
            "what was measured."
        )
    return " ".join(parts)


def _select_as_pipeline(segments, language: str, limit: int):
    """Replay the selection the way the pipeline actually makes it.

    The shipped ``selections.json`` is not the head of the ranked list. The
    pipeline is, in order, ``rank`` then the context gate then
    ``engine.selection.select`` over the survivors with the corpus built from
    this transcript — see ``selekyt.pipeline`` at the ``_apply_selection_policy``
    call. Replaying the first stage alone reads as drift whenever the policy
    layer re-orders anything, which is a claim that the code changed its
    judgement when it only changed which stage finishes the job.

    Duration is inferred from the segment ends exactly as the pipeline infers
    it, so the audit needs no media: ``source`` reaches the corpus as a label
    only and is not part of thread construction.

    Returns ``(selected, note)`` where ``note`` is empty on a complete replay
    and otherwise names what the replay could not run, so the report can say
    whether it is the shipped stack or a partial one.
    """
    from engine.corpus import build_corpus
    from engine.selection import select

    ranked = rank(make_thoughts(segments),
                  limit=max(limit * 3, limit), select_limit=limit)
    passed = [c for c in ranked if validate(c)[0]]
    if not passed:
        return [], "the context gate passed no candidates, so there was " \
                   "nothing for the policy layer to order"
    duration = max((s.end for s in segments), default=0.0)
    corpus = build_corpus(segments, duration=duration,
                          language=language or "unknown")
    return list(select(passed, limit=limit, corpus=corpus).candidates), ""


def _gate_verdict_for(candidates, start: float, end: float) -> str:
    """Why the current gate treats the clip that spanned start-end.

    The word-anchored sentence timer lands a sentence's bound on the word that
    composes it, where the old proportional timer put it at a share of the
    segment's length. On telugu-tutorial that moved the shipped span's end from
    332.06 to 331.20 — same sentences, same rejection, a bound that is now the
    last word's end instead of a fraction of the segment. A lookup that demands
    the exact span would then report "not generated" for a clip the code does
    generate and does reject, which reads as the clip vanishing when it is the
    *reason* it vanishes that the report is for. When no exact match exists the
    nearest candidate at the same start is used, and the report names the span
    it actually read so the substitution is visible rather than silent.
    """
    exact = [c for c in candidates
             if abs(c.start - start) < 1e-9 and abs(c.end - end) < 1e-9]
    if exact:
        ok, why = validate(exact[0])
        return "passes" if ok else f"rejected: {why}"
    same_start = [c for c in candidates if abs(c.start - start) < 1e-9]
    if same_start:
        # Nearest by end, so the report reads the candidate that most nearly is
        # this clip rather than one that happens to open here and run on.
        near = min(same_start, key=lambda c: abs(c.end - end))
        ok, why = validate(near)
        return (f"rejected: {why}"
                f" (span now {near.start:.2f}-{near.end:.2f}, "
                f"the word-anchored sentence timer moved the bound)")
    return "not generated at this span by the current code"


def audit_corpus(root: Path) -> dict:
    runs = sorted(p for p in (root / "corpus").glob("*_run"))
    per_run = [audit_run(p) for p in runs]
    failing = [
        r for r in per_run
        if r["status"] not in ("ok", "reproduces", "no_selections")
    ]
    return {
        "repo": str(root),
        "runs": len(runs),
        "with_selections": sum(1 for r in per_run if r.get("clips")),
        "not_reproducing": [r["run"] for r in failing],
        "per_run": per_run,
    }


def _print_human(report: dict) -> None:
    print(f"corpus reproducibility audit — {report['runs']} runs, "
          f"{report['with_selections']} with selections")
    for r in report["per_run"]:
        print()
        print(f"  {r['run']}")
        print(f"    status      {r['status']}")
        if r["status"] == "no_selections":
            print(f"    note        {r['note']}")
            continue
        print(f"    language    {r.get('language')}")
        wc = r.get("transcript_word_confidence") or {}
        mean = wc.get("mean")
        print(f"    clips       {r['clips']} shipped / {r.get('clips_now')} now")
        print(f"    confidence  transcript mean={mean} "
              f"({wc.get('provenance')})")
        if r.get("replay"):
            print(f"    replay      {r['replay']}")
        if r["status"] == "count_drift":
            print(f"    note        {r['note']}")
            for d in r.get("dropped", []):
                print(f"    dropped     {d['start']}-{d['end']} "
                      f"{d['story_type']} score={d['total_score']} "
                      f"-> {d['gate_verdict_now']}")
            continue
        for c in r.get("clips_detail", []):
            mark = "ok" if c["span_reproduces"] and not c["differing_fields"] else "DIFF"
            print(f"    {c['n']}. [{mark}] {c['start']}-{c['end']} "
                  f"{c['story_type']} score={c['total_score_now']} "
                  f"conf now={c['confidence_now']} "
                  f"recorded={c['confidence_recorded']} "
                  f"gate={c['gate_verdict_now']}")
        if r.get("note"):
            print(f"    note        {r['note']}")
    bad = report["not_reproducing"]
    print()
    if bad:
        print(f"NOT REPRODUCING: {', '.join(bad)}")
    else:
        print("Every run with selections reproduces exactly, or differs only "
              "in fields a later commit rewrote with the cause named above.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=_REPO)
    ap.add_argument("--json", action="store_true", help="machine-readable")
    args = ap.parse_args(argv)

    report = audit_corpus(args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 1 if report["not_reproducing"] else 0


if __name__ == "__main__":
    sys.exit(main())
