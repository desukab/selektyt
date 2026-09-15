from __future__ import annotations
import json
from pathlib import Path
from engine.editorial import make_thoughts, rank
from engine.captions import ass_for_candidate
from engine.render import render_clip
from providers.youtube import download
from providers.whisper import transcribe


def run(source: str, out_root: Path, model: str = "small", language: str | None = None, limit: int = 6) -> Path:
    project = out_root / "project"
    project.mkdir(parents=True, exist_ok=True)
    media, info = download(source, project / "source") if source.startswith(("http://", "https://")) else (Path(source), {})
    transcript, detected = transcribe(media, model_name=model, language=language)
    thoughts = make_thoughts(transcript)
    selected = rank(thoughts, limit=limit)
    (project / "transcript.json").write_text(json.dumps({"language": detected, "segments": [s.__dict__ for s in transcript]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (project / "selections.json").write_text(json.dumps([c.to_dict() for c in selected], ensure_ascii=False, indent=2), encoding="utf-8")
    for idx, c in enumerate(selected, 1):
        caption = ass_for_candidate(c, project / "captions" / f"{idx:02d}.ass")
        render_clip(media, c, project / "facebook" / f"{idx:02d}.mp4", caption, vertical=False)
        render_clip(media, c, project / "shorts" / f"{idx:02d}.mp4", caption, vertical=True)
    report = {
        "source": source,
        "title": info.get("title"),
        "language": detected,
        "candidate_count": len(thoughts),
        "selected_count": len(selected),
        "outputs": {"facebook": str(project / "facebook"), "shorts": str(project / "shorts")},
        "policy": "retention-aware editorial heuristics; no fabricated claims",
    }
    (project / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return project
