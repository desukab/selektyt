from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from engine.editorial import make_thoughts, rank
from engine.captions import ass_for_candidate
from engine.context_guard import validate
from engine.render import render_clip
from providers.youtube import download
from providers.whisper import transcribe


def run(source: str, out_root: Path, model: str = "small", language: str | None = None, limit: int = 6) -> Path:
    project = out_root / "project"
    project.mkdir(parents=True, exist_ok=True)
    media, info = download(source, project / "source") if source.startswith(("http://", "https://")) else (Path(source), {})
    transcript, detected = transcribe(media, model_name=model, language=language)
    thoughts = make_thoughts(transcript)
    ranked = rank(thoughts, limit=max(limit * 2, limit))
    selected = [c for c in ranked if validate(c)[0]][:limit]

    transcript_json = {"language": detected, "segments": []}
    for s in transcript:
        transcript_json["segments"].append({"start": s.start, "end": s.end, "text": s.text, "words": [asdict(w) for w in s.words]})
    (project / "transcript.json").write_text(json.dumps(transcript_json, ensure_ascii=False, indent=2), encoding="utf-8")
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
        "rejected_by_context_guard": len(ranked) - len(selected),
        "outputs": {"facebook": str(project / "facebook"), "shorts": str(project / "shorts")},
        "policy": "retention-aware editorial heuristics; no fabricated claims",
    }
    (project / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return project
