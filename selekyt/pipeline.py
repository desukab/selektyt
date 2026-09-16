from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from engine.editorial import make_thoughts, rank
from engine.captions import ass_for_candidate, srt_for_candidate
from engine.caption_director import caption_metadata
from engine.content_pack import build_content_pack
from engine.context_guard import validate
from engine.edit_plan import retention_intervals, pacing_report, aggregate_pacing_report
from engine.qa import write_report
from engine.render import render_clip
from engine.llm_editorial import attach_metadata
from engine.pipeline_audio import run_pipeline_audio
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
    chunk_minutes: int = 5,
    beam_size: int = 1,
    cpu_threads: int | None = None,
    ollama_url: str | None = None,
    audio_analysis: bool = False,
    vad_backend: str | None = None,
    silence_threshold: float = 0.5,
    silence_padding: float = 0.0,
    min_speech_ms: int = 150,
    min_silence_ms: int = 100,
    keep_pauses: bool = True,
    remove_filler: bool = True,
    export_fcpxml: bool = False,
) -> Path:
    project = out_root / "project"
    project.mkdir(parents=True, exist_ok=True)
    state_path = project / "pipeline_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if source.startswith(("http://", "https://")):
        media_path = state.get("media_path")
        if (
            media_path
            and Path(media_path).is_file()
            and state.get("source") == source
        ):
            media, info = Path(media_path), state.get("media_info", {})
            print(f"[cache] reusing downloaded source: {media}", flush=True)
        else:
            media, info = download(source, project / "source", cookies=cookies)
            state.update({"source": source, "media_path": str(media), "media_info": info})
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    else:
        media, info = Path(source).expanduser(), {}
        if not media.is_file():
            raise RuntimeError(f"Local video not found: {media}")

    fingerprint = _fingerprint(media, source, model, language)
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
            beam_size=beam_size,
            cpu_threads=cpu_threads,
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
        # merge, not replace: the download cache (source/media_path/media_info)
        # must survive transcription so a resumed run reuses the local file
        state.update({"fingerprint": fingerprint, "model": model, "language": detected})
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    thoughts = make_thoughts(transcript)
    ranked = rank(thoughts, limit=max(limit * 3, limit))
    selected = [c for c in ranked if validate(c)[0]][:limit]
    context_guard_rejections = len(ranked) - len(selected)
    if not selected:
        raise RuntimeError("No complete standalone stories passed the editorial/context gate.")
    if ollama_url:
        selected = [attach_metadata(c, ollama_url) for c in selected]
    (project / "selections.json").write_text(
        json.dumps([c.to_dict() for c in selected], ensure_ascii=False, indent=2)
    )

    editorial_report = []
    for c in selected:
        editorial_report.append({
            "start": round(c.start, 3),
            "end": round(c.end, 3),
            "text": c.text,
            "story_type": c.story_type,
            "hook": c.hook,
            "payoff": c.payoff,
            "editorial_score": round(c.editorial_score, 3),
            "virality_score": round(c.virality_score, 3),
            "context_score": round(c.context_score, 3),
            "confidence": round(c.confidence, 3),
            "visual_potential": round(c.visual_potential, 3),
            "total_score": round(c.total_score, 3),
            "reason": c.reason,
            "llm_metadata": c.llm_metadata,
        })
    (project / "editorial_report.json").write_text(
        json.dumps(editorial_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    rejected = [c for c in ranked if not validate(c)[0]]
    pacing = aggregate_pacing_report(selected, rejected, context_guard_rejections)

    audio_summary: dict = {}
    if audio_analysis:
        all_words: list[Word] = []
        for s in transcript:
            all_words.extend(s.words or [])
        audio_summary = run_pipeline_audio(
            media,
            project,
            words=all_words or None,
            source=source,
            pacing=pacing,
            min_speech_ms=min_speech_ms,
            min_silence_ms=min_silence_ms,
            silence_threshold=silence_threshold,
            silence_padding=silence_padding,
            keep_pauses=keep_pauses,
            remove_filler=remove_filler,
            export_fcpxml=export_fcpxml,
            vad_backend=vad_backend,
            force=False,
        )
        (project / "audio").mkdir(exist_ok=True)
    (project / "pacing_report.json").write_text(
        json.dumps(pacing, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    outputs = []
    edit_report = []
    content_packs = []
    for idx, c in enumerate(selected, 1):
        intervals = retention_intervals(c)
        caption = ass_for_candidate(c, project / "captions" / f"{idx:02d}.ass")
        srt_for_candidate(c, project / "captions" / f"{idx:02d}.srt")
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
        if (
            state.get("render_fingerprint") == fingerprint
            and fb.is_file()
            and sh.is_file()
            and fb.stat().st_size > 0
            and sh.stat().st_size > 0
        ):
            print(f"[cache] reusing rendered clip {idx:02d}", flush=True)
        else:
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
    qa_path = write_report(project, outputs, source_path=str(media))
    report = {
        "source": source,
        "title": info.get("title"),
        "language": detected,
        "candidate_count": len(thoughts),
        "selected_count": len(selected),
        "rejected_by_context_guard": context_guard_rejections,
        "outputs": {"facebook": str(project / "facebook"), "shorts": str(project / "shorts")},
        "captions": str(project / "captions"),
        "content_pack": str(project / "content_pack.json"),
        "editorial_report": str(project / "editorial_report.json"),
        "pacing_report": str(project / "pacing_report.json"),
        "qa": str(qa_path),
        "policy": "retention-aware editorial heuristics; source speech is not rewritten; no fabricated claims",
    }
    # The audio section is always present: either the full provenance summary,
    # or an explicit `enabled: False` so a B2B consumer can distinguish
    # "not run" from "failed silently".
    report["audio"] = audio_summary if audio_summary else {"enabled": False}
    (project / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    state.update({"fingerprint": fingerprint, "render_fingerprint": fingerprint, "selected_count": len(selected), "qa": str(qa_path), "complete": True})
    state_path.write_text(json.dumps(state, indent=2))
    return project
