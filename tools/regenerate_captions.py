"""Regenerate a project's caption tracks without re-transcribing.

``selekyt/pipeline.py`` writes ``captions/NN.srt``, ``NN.ass`` and ``NN.json``
from the selection set through three pure functions in ``engine/captions.py``.
When a caption defect is fixed, re-earning the evidence should not cost a full
re-transcode of every source: the caption track is a function of
``selections.json`` alone, so re-emitting it from the persisted selections is
byte-identical to what a fresh run would write for those three files.

What this does NOT do: it touches no render, no EDL, no FCPXML and no transcript.
A defect in any of those still needs a real re-run.

Usage:
    .venv/bin/python tools/regenerate_captions.py corpus/<id>_run/project
"""
from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.captions import ass_for_candidate, srt_for_candidate  # noqa: E402
from engine.caption_director import caption_metadata  # noqa: E402
from models.core import Candidate, Word  # noqa: E402


def _candidate(d: dict) -> Candidate:
    words = [Word(w["text"], w["start"], w["end"]) for w in (d.get("words") or [])]
    known = {f.name for f in fields(Candidate)}
    kwargs = {k: v for k, v in d.items() if k in known and k != "total_score"}
    kwargs["words"] = words or None
    return Candidate(**kwargs)


def main(project_dir: str) -> int:
    project = Path(project_dir)
    selections = project / "selections.json"
    if not selections.is_file():
        print(f"missing {selections}", file=sys.stderr)
        return 2

    out_dir = project / "captions"
    out_dir.mkdir(parents=True, exist_ok=True)
    changed = 0
    for idx, raw in enumerate(json.loads(selections.read_text(encoding="utf-8")), 1):
        c = _candidate(raw)
        srt = out_dir / f"{idx:02d}.srt"
        ass = out_dir / f"{idx:02d}.ass"
        meta = out_dir / f"{idx:02d}.json"
        before = {p: p.read_text(encoding="utf-8") if p.is_file() else None
                  for p in (srt, ass, meta)}

        ass_for_candidate(c, ass)
        srt_for_candidate(c, srt)
        meta.write_text(json.dumps(caption_metadata(c), ensure_ascii=False, indent=2),
                        encoding="utf-8")

        same = all(before[p] == p.read_text(encoding="utf-8") for p in (srt, ass, meta))
        print(f"clip {idx:02d}: {'unchanged' if same else 'REWRITTEN'}")
        changed += 0 if same else 1

    print(f"{changed} of {idx} caption track(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
