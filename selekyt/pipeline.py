from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from engine.editorial import make_thoughts, rank
from engine.captions import ass_for_candidate
from engine.caption_director import caption_metadata
from engine.content_pack import build_content_pack
from engine.context_guard import validate
from engine.edit_plan import retention_intervals
from engine.qa import write_report
from engine.render import render_clip
from providers.youtube import download
from providers.whisper import transcribe
from models.core import Segment, Word


def _load_transcript(path: Path) -> tuple[list[Segment], str] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        out = []
        for s in data.get("segments", []):
            out.append(
                Segment(
                    float(s["start"]),
                    float(s["end"]),
                    s["text"],
                    [Word(**w) for w in s.get("words", [])],
                )
            )
        return out, str(data.get("language") or "unknown")
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _fingerprint(media: Path, source: str, model: str, language: str | None) -> str:
    st = media.stat()
    raw = f"{source}|{media.resolve()}|{st.st_size}|{st.st_mtime_ns}|{model}|{language or 'auto'}"
    return hashlib.sha256(raw.encode()).hexdigest()


def run(
    source: str,
    out_root: Path,
    model: str = "small",
    language: str | None = None,
    limit: int = 6,
    cookies: str | None = None,
    chunk_minutes: int = 10,
) -> Path:
    project = out_root / "project"
    project.mkdir(parents=True, exist_ok=True)
    if source.startswith(("http://", "https://")):
        media, info = download(source, project / "source", cookies=cookies)
    else:
        media, info = Path(source).expanduser(), {}
        if not media.is_file():
            raise RuntimeError(f"Local video not found: {media}")

    fingerprint = _fingerprint(media, source, model, language)
    state_path = project / "pipeline_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    transcript_path = project / "transcript.json"
    cached = _load_transcript(transcript_path) if state.get("fingerprint") == fingerprint else None
    if cached and not language:
        transcript, detected = cached
        print("[cache] using unchanged transcript", flush=True)
    else:
        print(
            f"[transcribe] model={model}; language={language or 'auto'}; "
            f"chunk={chunk_minutes}min",
            flush=True,
        )
        transcript, detected = transcribe(
            media,
            model_name=model,
            language=language,
            checkpoint_dir=project / "transcription",
            chunk_minutes=chunk_minutes,
        )
        transcript_path.write_text(
            json.dumps(
                {
                    "language": detected,
                    "segments": [
                        {
                            "start": s.start,
                            "end": s.end,
                            "text": s.text,
                            "words": [asdict(w) for w in s.words],
                        }
                        for s in transcript
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        state = {"fingerprint": fingerprint, "model": model, "language": detected}
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    thoughts = make_thoughts(transcript)
    ranked = rank(thoughts, limit=max(limit * 3, limit))
    selected = [c for c in ranked if validate(c)[0]][:limit]
    if not selected:
        raise RuntimeError("No complete standalone stories passed the editorial/context gate.")
    (project / "selections.json").write_text(
        json.dumps([c.to_dict() for c in selected], ensure_ascii=False, indent=2)
    )

    outputs = []
    edit_report = []
    content_packs = []
    for idx, c in enumerate(selected, 1):
        intervals = retention_intervals(c)
        caption = ass_for_candidate(c, project / "captions" / f"{idx:02d}.ass")
        (project / "captions" / f"{idx:02d}.json").write_text(
            json.dumps(caption_metadata(c), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        pack = build_content_pack(c)
        pack["clip_index"] = idx
        (project / "content_pack" / f"{idx:02d}.json").parent.mkdir(parents=True, exist_ok=True)
        (project / "content_pack" / f"{idx:02d}.json").write_text(
            json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        content_packs.append(pack)

        fb = project / "facebook" / f"{idx:02d}.mp4"
        sh = project / "shorts" / f"{idx:02d}.mp4"
        render_clip(media, c, fb, caption, vertical=False)
        render_clip(media, c, sh, caption, vertical=True)
        outputs += [fb, sh]
        edited = sum(b - a for a, b in intervals)
        edit_report.append(
            {
                "index": idx,
                "source_start": c.start,
                "source_end": c.end,
                "source_duration": round(c.end - c.start, 3),
                "intervals": [[round(a, 3), round(b, 3)] for a, b in intervals],
                "edited_duration": round(edited, 3),
                "removed_seconds": round((c.end - c.start) - edited, 3),
                "story_type": c.story_type,
            }
        )
    (project / "edit_plan.json").write_text(json.dumps(edit_report, indent=2))
    (project / "content_pack.json").write_text(
        json.dumps(content_packs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    qa_path = write_report(project, outputs)
    report = {
        "source": source,
        "title": info.get("title"),
        "language": detected,
        "candidate_count": len(thoughts),
        "selected_count": len(selected),
        "rejected_by_context_guard": len(ranked) - len(selected),
        "outputs": {"facebook": str(project / "facebook"), "shorts": str(project / "shorts")},
        "captions": str(project / "captions"),
        "content_pack": str(project / "content_pack.json"),
        "qa": str(qa_path),
        "policy": "retention-aware editorial heuristics; source speech is not rewritten; no fabricated claims",
    }
    (project / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    state.update({"fingerprint": fingerprint, "selected_count": len(selected), "qa": str(qa_path), "complete": True})
    state_path.write_text(json.dumps(state, indent=2))
    return project
