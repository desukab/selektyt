"""Re-emit a project's EDL/FCPXML from its persisted selections, without a re-run.

``selekyt/pipeline.py`` builds the interchange for each clip through
``timeline_for_candidate(media, candidate, fps, duration)`` followed by
``nle_export``. Every input to that pair is recoverable from the project's own
artifacts — ``selections.json`` holds the candidate, ``source_probe.json`` holds
the frame rate and duration the run was cut on, and the media path is in the
probe — so re-emitting produces the same timeline a fresh run would have
written, byte for byte, for those files. That is what makes an interchange fix
confirmable without re-transcoding every source in the corpus.

What this does NOT do, and the distinction matters:

* It touches **no render, no caption, no transcript and no selection**. A defect
  in any of those still needs a real re-run.
* It applies **no upstream fix**. ``engine/edit_plan.py::_word_aligned`` moves
  the selection bounds themselves, so it runs *before* ``selections.json`` is
  written; re-emitting from a pre-fix selections.json reproduces pre-fix bounds
  exactly. Only an emitter-side defect (the FROM CLIP path base) is recoverable
  this way. ``tools/regenerate_captions.py`` has the same limit for the same
  reason, and both tools say so rather than quietly standing in for a re-run.
* It **refuses to run without the source media**. The entire point of the path
  fix is that the EDL must name a file an NLE can open, so writing one that
  points at nothing would recreate the defect it exists to repair. Pass a
  project whose source is retained (``corpus_run.py --keep-media``).

Usage:
    .venv/bin/python tools/regenerate_timelines.py corpus/<id>_run/project
"""
from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.nle_export import export  # noqa: E402
from engine.timeline_build import timeline_for_candidate  # noqa: E402
from models.core import Candidate, Word  # noqa: E402


def _rate(record: dict) -> float:
    """ffprobe reports ``r_frame_rate`` as a rational (``30000/1001``); a
    project may carry either that string or a pre-divided float."""
    rate = record.get("fps")
    if rate is None:
        return 0.0
    if isinstance(rate, str) and "/" in rate:
        num, _, den = rate.partition("/")
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        val = float(rate)
        return val if val > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _candidate(d: dict) -> Candidate:
    words = [Word(w["text"], w["start"], w["end"]) for w in (d.get("words") or [])]
    known = {f.name for f in fields(Candidate)}
    kwargs = {k: v for k, v in d.items() if k in known and k != "total_score"}
    kwargs["words"] = words or None
    return Candidate(**kwargs)


def main(project_dir: str) -> int:
    project = Path(project_dir)
    probe_path = project / "source_probe.json"
    sel_path = project / "selections.json"
    for path, what in ((probe_path, "the recorded source probe (run corpus_run.py "
                                    "--keep-media, or probe the source by hand)"),
                       (sel_path, "the persisted selections")):
        if not path.is_file():
            print(f"missing {path} — {what}", file=sys.stderr)
            return 2

    record = json.loads(probe_path.read_text(encoding="utf-8"))
    media = Path(record.get("source") or "")
    if not media.is_file():
        print(f"{media} is not present. The EDL must name a file an NLE can open, "
              f"so there is nothing legitimate to write here. Retain the source "
              f"(corpus_run.py --keep-media) and retry.", file=sys.stderr)
        return 2
    fps = _rate(record)
    if fps <= 0.0:
        print(f"{probe_path} records no frame rate; the timelines would be written "
              f"on an unknown grid and would audit as a retime", file=sys.stderr)
        return 2
    duration = record.get("duration")

    out_dir = project / "timeline"
    changed = 0
    raws = json.loads(sel_path.read_text(encoding="utf-8"))
    # Numbered by position in the reel, so the order must come from the canonical
    # edit when there is one: ``01.edl`` is the program's opening, and
    # regenerating in pick order would write the opening's timeline under 02.
    from engine.assembly import program_order_of

    order = program_order_of(project)
    if order is not None:
        by_span = {(round(float(r["start"]), 3), round(float(r["end"]), 3)): r
                   for r in raws}
        keyed = [k for k in ((round(s, 3), round(e, 3)) for s, e in order)
                 if k in by_span]
        raws = [by_span[k] for k in keyed]
    for idx, raw in enumerate(raws, 1):
        c = _candidate(raw)
        tl = timeline_for_candidate(media, c, fps=fps, duration=duration)
        before = {f: f.read_text(encoding="utf-8") if f.is_file() else None
                  for f in (out_dir / f"{idx:02d}.edl", out_dir / f"{idx:02d}.fcpxml")}
        export(tl, out_dir, stem=f"{idx:02d}")
        after = {f: f.read_text(encoding="utf-8") for f in before}
        # A rewrite that no file can account for means the run never emitted a
        # timeline for this clip, which is itself a result worth saying out loud
        # rather than silently counting as a change.
        diffs = [f.name for f in before if before[f] != after[f]]
        print(f"clip {idx:02d}: {'unchanged' if not diffs else 'REWRITTEN ' + ','.join(diffs)}")
        changed += 1 if diffs else 0

    print(f"{changed} of {idx} timeline(s) changed")
    print("run tools/nle_check.py over this directory to confirm the export")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
