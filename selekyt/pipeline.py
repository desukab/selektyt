from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from providers.atomic import read_json, write_json_atomic
from providers.audio import peak_dbfs

from engine.editorial import make_thoughts, rank
from engine.captions import ass_for_candidate, srt_for_candidate
from engine.caption_director import caption_metadata, style_name
from engine.content_pack import build_content_pack
from engine.context_guard import validate
from engine.corpus import build_corpus
from engine.coverage import check as check_coverage
from engine.edit_plan import retention_intervals, pacing_report, aggregate_pacing_report
from engine.qa import write_report
from engine.report import ReportBuilder
from engine.render import render_clip
from engine.timeline_build import timeline_for_candidate
from engine.nle_export import export as nle_export, validate_edl, validate_fcpxml
from engine.llm_editorial import attach_metadata
from engine.pipeline_audio import run_pipeline_audio
from providers.ffmpeg import FfmpegError, probe_duration, probe_fps
from providers.youtube import download
from providers.transcribe import (
    _ENGINE_INSTALLED_MESSAGE,
    select_engine,
    transcribe as transcribe_auto,
)
from models.core import Segment, Word


STATE_NAME = "pipeline_state.json"

# Below this peak the extracted audio is digital silence, which no container
# repair can help: a remux of a silent file is a silent file. The two corpus
# sources that are correctly rejected as speechless measure -inf and -30.5 dBFS
# against 16-bit full scale, so this floor separates "nothing was decoded" from
# "something loud was decoded into nothing".
SILENT_PEAK_DBFS = -50.0


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


# Keys a yt-dlp info dict can carry that hold the user's own credentials.
# ``http_headers`` is the live one: yt-dlp puts the request headers it used,
# including a ``Cookie`` line, there when a cookies file was supplied, and the
# info dict is persisted verbatim into ``pipeline_state.json`` — a file the web
# shell serves to any unauthenticated localhost caller. The rest are the other
# places yt-dlp has kept cookie material across versions; the denylist is
# deliberately explicit rather than "drop anything that looks like a header",
# because a fuzzy rule would also drop the format metadata the QA reports read.
_CREDENTIAL_KEYS = frozenset(
    {"http_headers", "cookies", "cookie_manager", "cookiejar", "_cookies", "cookies_str"}
)


def _scrub_credentials(info, depth: int = 8):
    """Return a copy of a yt-dlp info dict without credential-bearing fields.

    Nothing downstream reads these keys: the pipeline reads duration, title
    and format ids, and the captions/QA stages read none of it. Dropping them
    costs no functionality and is what stops a cookies file from becoming a
    file the product itself serves to anyone who can reach the port.
    """
    if depth <= 0:
        return info
    if isinstance(info, dict):
        return {
            k: _scrub_credentials(v, depth - 1)
            for k, v in info.items()
            if k not in _CREDENTIAL_KEYS
        }
    if isinstance(info, list):
        return [_scrub_credentials(v, depth - 1) for v in info]
    return info


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


def _asr_provenance(path: Path) -> dict | None:
    """The ``asr`` block of a transcript already on disk, or None.

    A resumed run reuses the cached transcript and never calls the ASR engine,
    so the model that produced those words has to be read back from the file
    the run wrote earlier. The report repeats six fields of it; without this,
    that path points at a name that was never assigned on a resume.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("asr") or None
    except (OSError, ValueError, TypeError):
        return None


def _recovery_note(path: Path) -> str | None:
    """The ``container_recovery`` marker on a transcript already on disk.

    The marker says the words did not come from the file the user handed in:
    the container was damaged and a remux of it transcribed where the original
    did not. That is a reason to check the deliverable against the source, and
    a resumed run has to be able to state it again — the recovery branch is
    not taken when the transcript is cached.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("container_recovery")
    except (OSError, ValueError, TypeError):
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


def _no_speech_diagnosis(peak: float | None) -> str:
    """The message for an empty transcript, chosen by what the audio showed.

    Every empty transcript used to get the same answer — the source has no
    audible speech — which was true for a silent film and false for a source
    whose container had lost its audio. The peak level of the extraction is
    what separates them, and it is reported back to the user because a number
    is what makes the diagnosis checkable rather than merely confident.
    """
    if peak is not None and math.isfinite(peak) and peak > SILENT_PEAK_DBFS:
        return (
            f"the extracted audio is not silent (peak {peak:.1f} dBFS) but no "
            "transcript could be decoded, and a stream-copy remux did not "
            "help — the container or codec is likely damaged. Try re-muxing "
            "or re-encoding the source by hand."
        )
    return (
        "the extracted audio track is digital silence, so a silent or "
        "music-only source has nothing to select; check that the file has an "
        "audio track at all."
    )


def _recover_from_a_damaged_container(
    media: Path, dest_dir: Path, transcribe,
) -> tuple[Path, list, str, dict, str] | None:
    """Re-transcribe from a clean remux, if a remux produces anything.

    ``transcribe`` is the pipeline's own transcription call, so the recovered
    transcript comes from the same decode path the original would have — not
    from a probe, and not from a second model.

    Returns ``None`` when the recovery is not applicable, which is the common
    case and must stay quiet: a speechless source should hear the honest
    no-speech diagnosis, not a paragraph about a container that was fine.

    One strategy, one attempt. The evidence for this fix is a single corpus
    source whose `.ogv` was structurally damaged and which transcribed to 11
    segments from a clean copy; that is what a remux repairs, and a second
    invented strategy would be speculation presented as a fallback. A retry
    loop here is also how a genuinely speechless source burns minutes on
    copies that cannot help it.

    The remux is a *stream copy*, never a re-encode. Repairing a container
    must not become an excuse to alter the content, and a copy that changes
    nothing is the only recovery whose transcript can be compared to the
    original's at all. The recovered file is kept, because downstream renders
    and timelines seek it; deleting it would fix the transcript and break the
    cut.
    """
    from providers.ffmpeg import FfmpegError, remux

    dest_dir.mkdir(parents=True, exist_ok=True)
    candidate = dest_dir / f"{media.stem}.remux.mkv"
    try:
        remux(media, candidate)
    except FfmpegError as exc:
        tail = exc.stderr_tail.strip().splitlines()
        print(f"[transcribe] remux of {media.name} rejected by ffmpeg: "
              f"{tail[-1] if tail else exc}", flush=True)
        return None
    if not candidate.is_file() or candidate.stat().st_size < 10000:
        return None
    try:
        transcript, detected, provenance = transcribe(candidate)
    except Exception as exc:  # a recovery that raises must not kill the run
        print(f"[transcribe] the remux of {media.name} would not transcribe: "
              f"{type(exc).__name__}: {exc}", flush=True)
        return None
    if not transcript:
        return None
    return candidate, transcript, detected, provenance, "a stream-copy remux"


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
    # Same reason, larger cost: on a default install (neither ASR extra), the
    # transcription backend is missing, and before this check that only became
    # apparent after download and audio extraction — a whole video fetched to
    # reach an error about pip. The probe reports which engine would run, so
    # it also fails on a default install that has faster-whisper but is asking
    # for a model the fallback cannot supply. Sherpa availability depends on
    # the requested model, hence the model name rather than a bare boolean.
    if select_engine(model) is None:
        raise RuntimeError(_ENGINE_INSTALLED_MESSAGE)
    project = out_root / "project"
    project.mkdir(parents=True, exist_ok=True)
    # Resolved before anything is recorded: report.json quotes these paths to
    # readers that are not standing where the CLI was invoked — the desktop
    # app's sidecar among them, whose working directory is the app's own. A
    # relative path points somewhere else for them, silently.
    project = project.resolve()
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
            state.update(
                {
                    "source": source,
                    "media_path": str(media),
                    "media_info": _scrub_credentials(info),
                }
            )
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
    # The cache is only actually reused when no explicit language was asked
    # for: a cached transcript plus `--language x` falls through and
    # re-transcribes, so `cached` alone is not the reuse signal.
    transcript_reused = bool(cached and not language)
    if transcript_reused:
        transcript, detected = cached
        # Provenance is read back rather than carried in a local: this branch
        # skips transcription entirely, and the report still has to name which
        # model produced the words a resumed run is reusing. The recovery
        # marker goes with it for the same reason.
        asr_provenance = _asr_provenance(transcript_path) or {}
        recovery_note = _recovery_note(transcript_path)
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
        recovery_note = None
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
        # An empty transcript means one of two unrelated things, and only one
        # of them is the user's problem: the source genuinely has no speech,
        # or its audio is there and the decode lost it. For a long time the
        # error below blamed the user's content for both — a corpus source at
        # 95.5% speech energy was rejected as "no audible spoken content"
        # purely because its container was broken, and a clean remux of the
        # same file transcribed to 11 segments.
        #
        # The extraction is the thing to ask first, because it is cheap and it
        # settles it: digital silence cannot be repaired by any container fix,
        # while real level with no transcript points at the decode.
        peak = peak_dbfs(project / "transcription" / "audio.wav")
        if peak is not None and math.isfinite(peak) and peak > SILENT_PEAK_DBFS:
            recovered = _recover_from_a_damaged_container(
                media, project / "source",
                transcribe=lambda m: transcribe_auto(
                    m, model_name=model, language=language,
                    checkpoint_dir=project / "transcription",
                    chunk_minutes=chunk_minutes, beam_size=beam_size,
                    cpu_threads=cpu_threads,
                ),
            )
            if recovered is not None:
                remuxed, transcript, detected, asr_provenance, note = recovered
                # Downstream renders and timelines seek this media; leaving
                # them on the damaged original would reintroduce the decode
                # loss the recovery just fixed, one stage later and silently.
                media = remuxed
                state.update({"media_path": str(media)})
                _save_state(state_path, state)
                write_json_atomic(
                    transcript_path,
                    {
                        "language": detected,
                        "asr": asr_provenance,
                        "segments": [
                            {"start": s.start, "end": s.end, "text": s.text,
                             "words": [asdict(w) for w in s.words]}
                            for s in transcript
                        ],
                        # Named so a reader can tell this transcript came from
                        # a repaired container, not the file they handed in.
                        "container_recovery": note,
                },
            )
                recovery_note = note
                print(f"[transcribe] recovered {len(transcript)} segment(s) via {note}",
                      flush=True)

    if not transcript:
        # A source with no detectable speech has no stories to select, and
        # every candidate would be rejected for having too few words. Failing
        # here names the actual cause instead of letting it surface forty
        # minutes later as "no complete standalone stories passed the gate",
        # which tells a user nothing they can act on. Hit on a real URL whose
        # audio track was digital silence.
        raise RuntimeError(
            "Transcription produced no speech: the source has no audible "
            f"spoken content. {_no_speech_diagnosis(peak_dbfs(project / 'transcription' / 'audio.wav'))}"
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
    # The bench: candidates that passed every editorial gate and lost only to
    # the --limit budget. Recording just the count left an editor staring at
    # "5 more clips passed" with no way to see them, so the report now carries
    # the candidates themselves and the count is a claim the artifact backs.
    bench = [c.to_bench_dict() for c in passed[limit:]]
    if not selected:
        raise RuntimeError("No complete standalone stories passed the editorial/context gate.")
    if ollama_url:
        selected = [attach_metadata(c, ollama_url) for c in selected]
    (project / "selections.json").write_text(
        json.dumps([c.to_dict() for c in selected], ensure_ascii=False, indent=2),
        encoding="utf-8",
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

    # The corpus layer (topics, threads, redundancy groups, rhythm) is a
    # finished, tested analysis that nothing in the pipeline consumed: it was
    # reachable only by importing engine.corpus directly, so an editor running
    # the product never saw the redundancy groups it had already computed and
    # never saw a topic spine at all. It is deterministic, pure and 0.2 s on an
    # 8.9-minute source, so writing it alongside the other reports costs
    # nothing and it degrades to a valid corpus with explanatory notes on a
    # transcript too short or too sparse to analyse. A failure here must not
    # take down a run whose clips are already selected and rendered, so the
    # write reports rather than raises. The report then names the path only if
    # the file landed: a key pointing at a missing artifact is worse than an
    # absent key, because a UI resolving it fails loudly and an editor reads
    # that as a broken run.
    corpus_path = project / "editorial_corpus.json"
    try:
        corpus_report = build_corpus(
            transcript,
            duration=max((s.end for s in transcript), default=0.0),
            language=detected or "unknown",
            source=source,
        )
        corpus_path.write_text(
            json.dumps(corpus_report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        print(f"[selektyt] corpus analysis unavailable: {exc}", file=sys.stderr)
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
    # The captions are burned into the render, not composited by the player, so
    # a style change changes the MP4 itself. The cache key has to carry it: two
    # runs differing only in SELEKYT_CAPTION_STYLE produce the same fingerprint
    # and the same selection digest, and without this entry the second one
    # reuses the first's MP4 while rewriting the .ass beside it — a deliverable
    # whose burned-in captions disagree with its own sidecar, with no warning.
    caption_style = style_name()
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
            and cached.get("caption_style") == caption_style
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
                "caption_style": caption_style,
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
    (project / "edit_plan.json").write_text(json.dumps(edit_report, indent=2), encoding="utf-8")
    (project / "content_pack.json").write_text(
        json.dumps(content_packs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    qa_path = write_report(project, outputs, source_path=str(media))
    # A local file carries no metadata title, so the report used to read
    # ``"title": null`` for every local source — the first thing shown about
    # the user's own media was an absence. yt-dlp's title is preferred when a
    # download supplied one; otherwise the stem of the file is the title the
    # user already knows it by.
    title = info.get("title") or media.stem
    report = {
        "source": source,
        "title": title,
        "language": detected,
        "candidate_count": len(thoughts),
        "selected_count": len(selected),
        "rejected_by_context_guard": context_guard_rejections,
        # Separate from the above: these passed every editorial gate and were
        # dropped only because --limit capped the output. A budget, not a
        # judgment — and the bench below is what it counts.
        "truncated_by_limit": truncated_by_limit,
        # The bench: these passed every gate and differ from the shipped clips
        # only in rank, so an editor raising --limit reads from here. The
        # entries are visible and comparable; promoting one without re-running
        # the source is not possible, because rank() has no include hook —
        # see docs/PRODUCT_WORKFLOW_AUDIT.md J2.
        "bench": bench,
        "rejection_reasons": dict(sorted(rejection_reasons.items(), key=lambda kv: -kv[1])),
        "outputs": {"facebook": str(project / "facebook"), "shorts": str(project / "shorts")},
        "captions": str(project / "captions"),
        "timeline": str(project / "timeline"),
        "content_pack": str(project / "content_pack.json"),
        "editorial_report": str(project / "editorial_report.json"),
        "editorial_corpus": str(corpus_path) if corpus_path.is_file() else None,
        "pacing_report": str(project / "pacing_report.json"),
        "qa": str(qa_path),
        "policy": "retention-aware editorial heuristics; source speech is not rewritten; no fabricated claims",
        # Which model actually produced the words, and at what timing
        # fidelity. The full provenance is in transcript.json under ``asr``;
        # repeating the six fields a trust judgement needs keeps an editor
        # from hunting for them in a different file to decide whether the word
        # timing is attention-based or character-proportional. A null
        # downgrade_reason reads as "the requested model ran all the way
        # through" — it is recorded on every run, not only when it fires.
        "transcription": {
            k: asr_provenance.get(k)
            for k in ("engine", "requested_model", "model", "downgrade_reason",
                      "word_timestamps", "fallback_reason")
        },
        # None means the transcript came from the file the user handed in. A
        # value means it did not: the container was damaged, a remux
        # transcribed where the original did not, and the clips were cut from
        # the repair. Timings in a repaired file can shift relative to the
        # original, so this is the thing to check before trusting a frame, and
        # it lived only in transcript.json until now — the same gap as the
        # model name above.
        "container_recovery": recovery_note,
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
    # What made this deliverable, and on what: a clip handed to a reviewer is
    # reproducible only if the report says which build and which parameters
    # produced it. Every value below is read from the place the pipeline
    # already computed it rather than recomputed here, so each fact keeps the
    # one home it had.
    report_builder = ReportBuilder()
    report_builder.set_provenance(
        source_path=str(media),
        source_fingerprint=fingerprint,
        # The transcript is what a resumed run reuses, so the cache state is a
        # property of it rather than of the render.
        cache_status="transcript-reused" if transcript_reused else "transcript-fresh",
    )
    # Deliberately not populated: the model, the language, the ASR engine and
    # the VAD backend all already have one home in this report (``transcription``,
    # ``language``, ``audio``), and a second copy of a fact under a second key is
    # how the two drift apart. Neither the ASR nor the VAD reports a version
    # string either, so the version fields stay empty rather than carrying a
    # model or engine name that would read as one.
    report_builder.set_config({
        "model": model,
        "language": language,
        "limit": limit,
        "chunk_minutes": chunk_minutes,
        "beam_size": beam_size,
        "cpu_threads": cpu_threads,
        "audio_analysis": audio_analysis,
        "vad_backend": vad_backend,
        "silence_threshold": silence_threshold,
        "silence_padding": silence_padding,
        "min_speech_ms": min_speech_ms,
        "min_silence_ms": min_silence_ms,
        "keep_pauses": keep_pauses,
        "remove_filler": remove_filler,
        "export_fcpxml": export_fcpxml,
        "skip_coverage_check": skip_coverage_check,
        "caption_style": caption_style,
        "ollama_url": ollama_url,
        "cookies": "<set>" if cookies else None,
    })
    report["provenance"] = report_builder.finalize().provenance_dict()
    report["config"] = report_builder.to_dict()["config"]
    (project / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # No "complete" flag is written here. Nothing in the repo, the web shell or
    # the tests ever reads one: resume keys off ``renders`` and the caller learns
    # the run finished from this function returning. A write-only flag is worse
    # than no signal, because a future reader of pipeline_state.json would take
    # "complete" as a guarantee that a later, failing step had succeeded.
    state.update({"fingerprint": fingerprint, "render_fingerprint": fingerprint, "render_selection_digest": sel_digest, "selected_count": len(selected), "qa": str(qa_path)})
    _save_state(state_path, state)
    return project
