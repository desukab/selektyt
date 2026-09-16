from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from engine.editorial import make_thoughts, rank
from engine.captions import ass_for_candidate
from engine.context_guard import validate
from engine.edit_plan import retention_intervals
from engine.qa import write_report
from engine.render import render_clip
from providers.youtube import download
from providers.whisper import transcribe
from models.core import Segment, Word


def _load_transcript(path: Path) -> tuple[list[Segment], str] | None:
    if not path.exists(): return None
    try:
        data = json.loads(path.read_text(encoding="utf-8")); out=[]
        for s in data.get("segments", []):
            words=[Word(**w) for w in s.get("words", [])]
            out.append(Segment(float(s["start"]), float(s["end"]), s["text"], words))
        return out, str(data.get("language") or "unknown")
    except (OSError, ValueError, TypeError, KeyError):
        return None


def run(source: str, out_root: Path, model: str = "small", language: str | None = None, limit: int = 6, cookies: str | None = None) -> Path:
    project = out_root / "project"; project.mkdir(parents=True, exist_ok=True)
    is_url = source.startswith(("http://", "https://"))
    if is_url:
        media, info = download(source, project / "source", cookies=cookies)
    else:
        media, info = Path(source).expanduser(), {}
        if not media.is_file(): raise RuntimeError(f"Local video not found: {media}")

    transcript_path = project / "transcript.json"
    cached = _load_transcript(transcript_path) if transcript_path.exists() else None
    if cached and not language:
        transcript, detected = cached
    else:
        transcript, detected = transcribe(media, model_name=model, language=language)
        transcript_path.write_text(json.dumps({"language": detected, "segments": [{"start":s.start,"end":s.end,"text":s.text,"words":[asdict(w) for w in s.words]} for s in transcript]}, ensure_ascii=False, indent=2), encoding="utf-8")

    thoughts = make_thoughts(transcript)
    ranked = rank(thoughts, limit=max(limit * 3, limit))
    selected = [c for c in ranked if validate(c)[0]][:limit]
    if not selected: raise RuntimeError("No complete standalone stories passed the editorial/context gate.")
    (project / "selections.json").write_text(json.dumps([c.to_dict() for c in selected], ensure_ascii=False, indent=2), encoding="utf-8")

    outputs: list[Path] = []
    edit_report = []
    for idx, c in enumerate(selected, 1):
        intervals = retention_intervals(c)
        caption = ass_for_candidate(c, project / "captions" / f"{idx:02d}.ass")
        fb = project / "facebook" / f"{idx:02d}.mp4"; sh = project / "shorts" / f"{idx:02d}.mp4"
        render_clip(media, c, fb, caption, vertical=False); render_clip(media, c, sh, caption, vertical=True)
        outputs += [fb, sh]
        edit_report.append({"index": idx, "source_start": c.start, "source_end": c.end, "source_duration": round(c.end-c.start,3), "intervals": [[round(a,3),round(b,3)] for a,b in intervals], "edited_duration": round(sum(b-a for a,b in intervals),3), "removed_seconds": round((c.end-c.start)-sum(b-a for a,b in intervals),3)})
    (project / "edit_plan.json").write_text(json.dumps(edit_report, indent=2), encoding="utf-8")
    qa_path = write_report(project, outputs)
    report = {"source": source, "title": info.get("title"), "language": detected, "candidate_count": len(thoughts), "selected_count": len(selected), "rejected_by_context_guard": len(ranked)-len(selected), "outputs":{"facebook":str(project/"facebook"),"shorts":str(project/"shorts")}, "qa":str(qa_path), "policy":"retention-aware editorial heuristics; source speech is not rewritten; no fabricated claims"}
    (project / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return project
