from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from providers.atomic import read_json, write_json_atomic

from engine.editorial import make_thoughts, rank
from engine.captions import ass_for_candidate, srt_for_candidate
from engine.caption_director import caption_metadata
from engine.content_pack import build_content_pack
from engine.context_guard import validate
from engine.coverage import check as check_coverage
from engine.edit_plan import retention_intervals, pacing_report, aggregate_pacing_report
from engine.qa import write_report
from engine.render import render_clip
from engine.timeline_build import timeline_for_candidate
from engine.nle_export import export as nle_export, validate_edl, validate_fcpxml
from engine.llm_editorial import attach_metadata
from engine.pipeline_audio import run_pipeline_audio
from providers.ffmpeg import FfmpegError, probe_duration, probe_fps
from providers.youtube import download
from providers.transcribe import transcribe as transcribe_auto
from models.core import Segment, Word


STATE_NAME = "pipeline_state.json"


def _load_state(state_path: Path) -> dict:
    """Load pipeline state, recovering from corruption.

    ``pipeline_state.json`` is the file that makes a long run resumable: it
    holds the download cache, the transcript fingerprint and the render cache.
    Losing it to a truncated write means re-downloading and re-transcribing
    hours of audio, so it is worth recovering.

    A file that cannot be parsed is quarantined beside the state directory
    (never deleted — an operator may want it) and the last *known-good*
    backup is used. Only when neither reads does the run start fresh; the
    downloaded source media is still on disk, so a fresh state re-downloads
    rather than silently claiming a partial state is complete.
    """
    backup = state_path.with_name(state_path.name + ".bak")
    data = read_json(state_path)
    if isinstance(data, dict):
        return data
    if state_path.is_file():
        # Quarantine under a name that cannot collide with a later good state
        # and never silently overwrite an earlier quarantine.
        quarantine = state_path.with_name(state_path.name + ".corrupt.json")
        idx = 1
        while quarantine.exists():
            quarantine = state_path.with_name(f"{state_path.name}.corrupt.{idx}.json")
            idx += 1
        try:
            shutil.move(str(state_path), str(quarantine))
            print(
                f"[state] pipeline state was unreadable; quarantined as {quarantine.name}",
                flush=True,
            )
        except OSError as exc:
            print(f"[state] pipeline state unreadable and could not be quarantined: {exc}", flush=True)
    data = read_json(backup)
    if isinstance(data, dict):
        print("[state] recovered state from the last good backup", flush=True)
        # Restore it to the primary path so a subsequent corruption does not
        # cost the same recovery twice.
        try:
            shutil.copy2(str(backup), str(state_path))
        except OSError:
            pass
        return data
    return {}


def _save_state(state_path: Path, state: dict) -> None:
    """Write state atomically, keeping a backup of the previous good version.

    ``os.replace`` makes the visible file either the old contents or the new
    ones, so a kill mid-write can never leave a truncated state. The backup
    is only rotated from a version that parses, so the backup is always one
    the recovery path can trust.
    """
    if state_path.is_file():
        if isinstance(read_json(state_path), dict):
            try:
                shutil.copy2(str(state_path), state_path.with_name(state_path.name + ".bak"))
            except OSError:
                pass
    write_json_atomic(state_path, state)


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


def _render_is_valid(path: Path, *, vertical: bool, source: Path | None = None) -> bool:
    """Confirm a cached render is a complete, correctly-oriented video.

    A non-empty file is not evidence of a finished render: a run killed
    mid-encode leaves a truncated MP4 that is non-empty and unplayable, and
    ffprobe is the cheapest reliable check. Returns False for anything that
    cannot be probed, so the caller re-renders rather than shipping it.

    ``source`` is passed so the audio check can tell a video-only source from a
    render that dropped its audio. Without it a cached render is only rejected
    for missing audio, never diagnosed, which is the right default for a cache
    guard but would re-render a genuinely video-only source on every run.
    """
    from engine.qa import validate_output

    if not path.is_file() or path.stat().st_size < 1000:
        return False
    try:
        ok, _errors = validate_output(path, vertical, source=source)
    except Exception:
        return False
    return ok


def _selection_digest(selected: list) -> str:
    """Digest of the chosen clips.

    The render cache is keyed on the source and settings, but two different
    selections of the same source are different videos: without this digest
    a re-run that picked new clips would silently re-render nothing and ship
    the previous selection's files under the new indices.
    """
    raw = "|".join(f"{c.start:.3f},{c.end:.3f},{c.text}" for c in selected)
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
    skip_coverage_check: bool = False,
) -> Path:
    # Reject unusable parameters before touching the network or the disk: a
    # bad --limit used to survive download + transcription and then surface as
    # a misleading "no stories passed the gate" error, and a bad
    # --chunk-minutes died mid-transcription after the audio extraction.
    if limit is None or limit < 1:
        raise ValueError(f"limit must be >= 1 (got {limit})")
    if chunk_minutes < 1:
        raise ValueError(f"chunk_minutes must be >= 1 (got {chunk_minutes})")
    if beam_size < 1:
        raise ValueError(f"beam_size must be >= 1 (got {beam_size})")
    if cpu_threads is not None and cpu_threads < 1:
        raise ValueError(f"cpu_threads must be >= 1 (got {cpu_threads})")
    if silence_threshold < 0 or silence_padding < 0:
        raise ValueError("silence_threshold and silence_padding must be >= 0")
    if min_speech_ms < 0 or min_silence_ms < 0:
        raise ValueError("min_speech_ms and min_silence_ms must be >= 0")
    project = out_root / "project"
    project.mkdir(parents=True, exist_ok=True)
    state_path = project / STATE_NAME
    state = _load_state(state_path)
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
            _save_state(state_path, state)
    else:
        media, info = Path(source).expanduser(), {}
        if not media.is_file():
            raise RuntimeError(f"Local video not found: {media}")
        # A local source is not copied into the project tree, so `source` is
        # the media's own path rather than a URL. tools/nle_check.py reads that
        # key to find the media and derive the frame rate the timelines were
        # stamped with; without it, every fps-dependent check reports "could
        # not probe the source" on a local-file run that otherwise succeeded.
        if state.get("source") != source:
            state.update({"source": source, "media_info": {}})
            _save_state(state_path, state)

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
        transcript, detected, asr_provenance = transcribe_auto(
            media,
            model_name=model,
            language=language,
            checkpoint_dir=project / "transcription",
            chunk_minutes=chunk_minutes,
            beam_size=beam_size,
            cpu_threads=cpu_threads,
        )
        print(
            f"[transcribe] engine={asr_provenance['engine']} "
            f"word_timestamps={asr_provenance['word_timestamps']}"
            + (f" ({asr_provenance['fallback_reason']})" if asr_provenance["fallback_reason"] else ""),
            flush=True,
        )
        # Provenance must name the model that actually ran, not the one that
        # was asked for. A silent small→base downgrade is invisible without
        # this line, and an editor challenging the transcript cannot tell why
        # the word timing fidelity changed.
        used = asr_provenance.get("model")
        downgrade = asr_provenance.get("downgrade_reason")
        if downgrade:
            print(
                f"[transcribe] MODEL DOWNGRADE: requested "
                f"{asr_provenance.get('requested_model')}, used {used}: {downgrade}",
                flush=True,
            )
        write_json_atomic(
            transcript_path,
            {
                "language": detected,
                "asr": asr_provenance,
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
        )
        # merge, not replace: the download cache (source/media_path/media_info)
        # must survive transcription so a resumed run reuses the local file
        state.update({"fingerprint": fingerprint, "model": model, "language": detected})
        _save_state(state_path, state)

    if not transcript:
        # A source with no detectable speech has no stories to select, and
        # every candidate would be rejected for having too few words. Failing
        # here names the actual cause instead of letting it surface forty
        # minutes later as "no complete standalone stories passed the gate",
        # which tells a user nothing they can act on. Hit on a real URL whose
        # audio track was digital silence.
        raise RuntimeError(
            "Transcription produced no speech: the source has no audible "
            "spoken content. Check that the file has an audio track and that "
            "it contains speech (a silent or music-only source has nothing "
            "to select)."
        )

    # The near-empty case: a transcript that is well-formed, non-empty and in
    # the right language yet describes a small fraction of audio that is
    # mostly speech. Nothing else in the pipeline sees this, because every
    # other audit compares the transcript against itself or against the
    # selection, never against the audio — the corpus shipped exactly this as
    # an OK deliverable (4.5% of a 73%-speech-energy source). Runs before
    # selection and render, which are the parts that cost time.
    coverage = check_coverage(project / "transcription" / "audio.wav",
                              [{"start": s.start, "end": s.end} for s in transcript])
    if coverage.verdict == "coverage_collapse" and not skip_coverage_check:
        raise RuntimeError(
            f"Transcription coverage collapse: {coverage.detail}. Re-run with a "
            "larger model if the language is a low-resource one, or pass "
            "--skip-coverage-check once you have verified the transcript by "
            "hand — but verify it, because this check exists to stop a wrong "
            "deliverable shipping as a success."
        )

    thoughts = make_thoughts(transcript)
    ranked = rank(thoughts, limit=max(limit * 3, limit))
    # Validate once, not twice: the previous form called validate() again when
    # building the rejected list, so a candidate's gate reason was computed two
    # expensive times and the two lists could disagree on a non-deterministic
    # gate. Every reason below comes from this single pass.
    #
    # ``validate`` historically returned a 2-tuple; some callers/tests still
    # patch it that way, so both shapes are accepted rather than forcing a
    # breaking change on the contract.
    gate_results: list[tuple] = []
    for c in ranked:
        outcome = validate(c)
        ok = bool(outcome[0])
        reason = str(outcome[1]) if len(outcome) > 1 else "rejected"
        gate_results.append((c, ok, reason))
    passed = [c for c, ok, _r in gate_results if ok]
    rejected = [c for c, ok, _r in gate_results if not ok]
    # The two ways a candidate disappears are editorially different and the
    # report must not conflate them:
    #   * the gate rejected it — an editorial judgment an editor can challenge;
    #   * it passed but ranked below --limit — a budget constraint, not a
    #     judgment, and the clip is still available in the ranked set.
    context_guard_rejections = len(rejected)
    truncated_by_limit = max(0, len(passed) - limit)
    rejection_reasons: dict[str, int] = {}
    for _c, ok, reason in gate_results:
        if not ok:
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
    selected = passed[:limit]
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

    pacing = aggregate_pacing_report(
        selected, rejected, context_guard_rejections,
        truncated_by_limit=truncated_by_limit,
    )

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
    sel_digest = _selection_digest(selected)
    # Probed once and reused: the whole run cuts one source, and the frame
    # rate is what makes an EDL's timecodes land on whole frames.
    source_fps = probe_fps(media)
    # The exported asset's declared extent: the whole file, not the last clip
    # that reads from it, so an NLE's media browser shows the real length and
    # no clip can read past the end of the asset that hosts it. Like the frame
    # rate this is metadata for the interchange rather than an input to the
    # render, so a media file ffprobe cannot read falls back to the exporter's
    # own extent instead of aborting a run that would otherwise have succeeded.
    try:
        source_duration = probe_duration(media)
    except FfmpegError:
        source_duration = None
    # Per-clip render cache. The previous single render_fingerprint was only
    # written after the whole loop completed, so a run killed during clip 3 of
    # 6 re-rendered all six even though 1 and 2 were already on disk.
    renders = state.get("renders", {})
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
        # A cached render is only reusable when it really is the render for
        # this selection of this source AND ffprobe can confirm it is a
        # complete, correctly-oriented video. A truncated file from a killed
        # run, or a 5-byte stub, would otherwise be shipped as a finished clip.
        cached = renders.get(f"{idx:02d}")
        if (
            cached
            and cached.get("fingerprint") == fingerprint
            and cached.get("selection_digest") == sel_digest
            and _render_is_valid(fb, vertical=False, source=media)
            and _render_is_valid(sh, vertical=True, source=media)
        ):
            print(f"[cache] reusing rendered clip {idx:02d}", flush=True)
        else:
            render_clip(media, c, fb, caption, vertical=False, fps=source_fps)
            render_clip(media, c, sh, caption, vertical=True, fps=source_fps)
            renders[f"{idx:02d}"] = {
                "fingerprint": fingerprint,
                "selection_digest": sel_digest,
                "facebook": str(fb),
                "shorts": str(sh),
            }
            # Checkpoint after each clip, not once at the end: this is what
            # makes an interrupted run resume from the last finished clip.
            state["renders"] = renders
            _save_state(state_path, state)
        # The editable interchange for this clip. The timeline is built from
        # the same retention_intervals the render just concatenated and stamped
        # on the same frame grid, so an NLE relinking these clips gets the cut
        # that was shipped. tools/nle_check.py is the check that verifies this
        # agreement to within RENDER_FRAME_TOLERANCE; it is not invoked here —
        # run it over the project directory, or tools/corpus_run.py does.
        tl = timeline_for_candidate(media, c, fps=source_fps, duration=source_duration)
        exported = nle_export(tl, project / "timeline", stem=f"{idx:02d}")
        # CMX 3600 declares no frame rate, so the list is validated on the grid
        # it was written on. Without the real rate a 24 fps cut is read as 30
        # and correct spans report a spurious few-frame retime.
        edl_problems = validate_edl(
            exported["edl"].read_text(encoding="ascii"), fps=source_fps
        )
        fcpxml_problems = validate_fcpxml(
            exported["fcpxml"].read_text(encoding="utf-8"), tl
        )
        if edl_problems or fcpxml_problems:
            raise RuntimeError(
                f"clip {idx:02d} exported an interchange file that failed its own "
                f"validation: {edl_problems + fcpxml_problems}"
            )
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
                # No file paths here: edit_plan.json is a deterministic artifact
                # (two runs over the same input agree byte for byte), and a
                # run-specific output path is exactly what breaks that. Paths
                # live in report.json, which is not deterministic by design.
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
        # Separate from the above: these passed every editorial gate and were
        # dropped only because --limit capped the output. An editor reading
        # this report can challenge the rejections and pull from this bench.
        "truncated_by_limit": truncated_by_limit,
        "rejection_reasons": dict(sorted(rejection_reasons.items(), key=lambda kv: -kv[1])),
        "outputs": {"facebook": str(project / "facebook"), "shorts": str(project / "shorts")},
        "captions": str(project / "captions"),
        "timeline": str(project / "timeline"),
        "content_pack": str(project / "content_pack.json"),
        "editorial_report": str(project / "editorial_report.json"),
        "pacing_report": str(project / "pacing_report.json"),
        "qa": str(qa_path),
        "policy": "retention-aware editorial heuristics; source speech is not rewritten; no fabricated claims",
        # The completeness measurement behind the coverage guard. Recorded on
        # every run, not only on failures: a `not_computable` verdict is a
        # statement that the check could not measure anything, and recording
        # only the failures would make the silent case look like a pass.
        "coverage": coverage.as_dict(),
    }
    # The audio section is always present: either the full provenance summary,
    # or an explicit `enabled: False` so a B2B consumer can distinguish
    # "not run" from "failed silently".
    report["audio"] = audio_summary if audio_summary else {"enabled": False}
    (project / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    state.update({"fingerprint": fingerprint, "render_fingerprint": fingerprint, "render_selection_digest": sel_digest, "selected_count": len(selected), "qa": str(qa_path), "complete": True})
    _save_state(state_path, state)
    return project
