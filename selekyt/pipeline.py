from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from providers.atomic import read_json, write_json_atomic
from providers.audio import peak_dbfs, AudioReadError

from engine.editorial import make_thoughts, score_all, score_candidate
from engine.captions import ship_captions
from engine.caption_director import caption_metadata, style_name
from engine.content_pack import build_content_pack
from engine.context_guard import validate
from engine.corpus import build_corpus
from engine.coverage import check as check_coverage
from engine.language_fitness import fitness as language_fitness
from engine.language_fitness import is_refusable as language_is_refusable
from engine.edit_plan import retention_intervals, pacing_report, aggregate_pacing_report
from engine.qa import write_report
from engine.report import ReportBuilder
from engine.render import concat_renders, render_clip
from engine.timeline_build import timeline_for_candidate
from engine.nle_export import (
    export as nle_export,
    independent_parse_edl,
    independent_parse_fcpxml,
    validate_edl,
    validate_fcpxml,
)
from engine.llm_editorial import attach_metadata
from engine.story_composer import compose_story, compose_story_variants
from engine.selection import Scored, SelectionReport
from engine.pipeline_audio import run_pipeline_audio
from providers.ffmpeg import FfmpegError, probe_duration, probe_fps
from providers.youtube import download
from providers.transcribe import (
    _ENGINE_INSTALLED_MESSAGE,
    select_engine,
    transcribe as transcribe_auto,
)
from selekyt.transcription_settings import (
    PROVIDERS as TRANSCRIPTION_PROVIDERS,
    default_provider as default_transcription_provider,
    require_api_key as require_transcription_api_key,
)
from models.core import Segment, Word


STATE_NAME = "pipeline_state.json"

# Below this peak the extracted audio is digital silence, which no container
# repair can help: a remux of a silent file is a silent file. The two corpus
# sources that are correctly rejected as speechless measure -inf and -30.5 dBFS
# against 16-bit full scale, so this floor separates "nothing was decoded" from
# "something loud was decoded into nothing".
SILENT_PEAK_DBFS = -50.0


# ── UI event channel ──────────────────────────────────────────────────────────
#
# ``run`` accepts an optional ``progress`` callable and emits structured events
# to it as stages begin and end. The CLI presentation layer renders from these;
# nobody else passes it, and the pipeline's own ``print`` calls stay exactly as
# they were so the sidecar's stdout-redirection shim and every tool that greps
# the stage prefixes keep working unchanged. Events are the only thing the UI
# may consume: parsing those print lines would couple the presentation layer to
# the wording of diagnostics, and the four classification strings below are
# read by tools/corpus_run.py.

ProgressFn = Callable[[dict[str, Any]], None]


def _notify(progress: ProgressFn | None, **event: Any) -> None:
    """Emit one UI event, or nothing when nobody is listening."""
    if progress is not None:
        progress(event)


@contextmanager
def _stage(progress: ProgressFn | None, name: str, detail: Any = None):
    """Bracket a pipeline stage for a listening UI.

    ``detail`` may be a mapping, or a zero-arg callable returning one, because
    most stage summaries are only known once the stage has run. A raised
    exception emits ``fail`` rather than ``done`` so the UI can attribute the
    failure to the stage that was actually running, then re-raises.
    """
    _notify(progress, type="stage", stage=name, state="start")
    started = time.monotonic()
    try:
        yield
    except BaseException as exc:
        # The class name is deliberately not in the payload: it is a Python
        # implementation detail, and the CLI shows this string to a user who
        # asked for an editorial package, not a traceback. The corpus runner
        # classifies on phrases inside the message itself, not the type.
        _notify(progress, type="fail", stage=name, reason=str(exc))
        raise
    _notify(
        progress, type="stage", stage=name, state="done",
        duration=time.monotonic() - started,
        detail=detail() if callable(detail) else (detail or {}),
    )



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
                    speaker=s.get("speaker"),
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


def _fingerprint(media: Path, source: str, model: str, language: str | None,
                 provider: str = "local") -> str:
    st = media.stat()
    raw = f"{source}|{media.resolve()}|{st.st_size}|{st.st_mtime_ns}|{provider}|{model}|{language or 'auto'}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cached_or_require_key(state: dict, source: str, model: str,
                           language: str | None, provider: str,
                           transcript_path: Path | None = None) -> str | None:
    """Return the API key, unless this run will reuse a transcript it already has.

    A cloud run whose transcript is already on disk for this exact source,
    model and language never calls the transcriber, so it needs no live
    credential. Anything else still requires one, and ``require_api_key``
    raises rather than letting an explicit cloud choice fall back to local.

    The reuse predicate here is the one the transcribe stage itself uses: the
    fingerprint must match **and** no explicit language may have been asked
    for, since a cached transcript plus ``--language x`` falls through and
    re-transcribes. Checking the fingerprint alone would hand a ``None`` key to
    a transcription that does run.

    ``None`` is the sentinel for "no key needed" rather than an empty string,
    because an empty key is what a misconfigured environment looks like and the
    distinction between "unused" and "missing" has to survive to the call site.
    """
    if language:
        return require_transcription_api_key(provider)
    media = Path(state.get("media_path") or source).expanduser()
    try:
        already = (_fingerprint(media, source, model, language, provider)
                   == state.get("fingerprint"))
    except OSError:
        # The source is a URL that has not been downloaded yet, so there is no
        # file to fingerprint and therefore no transcript to reuse.
        already = False
    if already and (transcript_path is None or transcript_path.is_file()):
        return None
    return require_transcription_api_key(provider)


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
        ok, _errors = validate_output(path, vertical, source=source, min_height=720)
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


def _program_digest(program_order: list) -> str:
    """Digest of the order the program plays the selected clips in.

    Distinct from the selection digest: the same clips in a different sequence
    are a different edit, and the render cache is keyed by index, so without
    this a reorder would reuse the previous run's MP4s in place and ship the
    wrong clip at each number.
    """
    raw = "|".join(f"{c.start:.3f},{c.end:.3f}" for c in program_order)
    return hashlib.sha256(raw.encode()).hexdigest()


def _corpus_detail(corpus_report: Any, memory_path: Any) -> dict[str, Any]:
    """Corpus/memory summary: what the analysis produced, not what it tried to.

    A corpus that failed is ``None``, which is shown as nothing rather than as
    a zero that could be mistaken for an analysis that ran and found no topics.
    """
    out: dict[str, Any] = {}
    if corpus_report is not None:
        topics = getattr(corpus_report, "topics", None)
        if topics is not None:
            out["topics"] = len(topics)
    if memory_path is not None and Path(str(memory_path)).is_file():
        out["memory"] = "recorded"
    return out


def _audio_detail(summary: dict) -> dict[str, Any]:
    """The audio stage's summary, from the provenance dict it returned.

    A compression ratio below 1.0 is not a compressed cut, so it is not shown as
    one — ``dead_air`` is the ratio that reads honestly on its own.
    """
    if not summary:
        return {}
    out: dict[str, Any] = {}
    if summary.get("vad_backend"):
        out["vad"] = summary["vad_backend"]
    speech = summary.get("speech_ratio")
    if isinstance(speech, (int, float)):
        out["speech"] = f"{speech * 100:.0f}%"
    dead = summary.get("dead_air_ratio")
    if isinstance(dead, (int, float)):
        out["dead air"] = f"{dead * 100:.0f}%"
    comp = summary.get("compression_ratio")
    if isinstance(comp, (int, float)) and comp > 1.0:
        out["compression"] = f"{comp:.2f}x"
    return out


def _assemble_detail(summary: dict) -> dict[str, Any]:
    """The assembly stage's summary: the shape of the edit as it was built.

    ``summary`` is only populated once the assembly landed, so an empty dict is
    the honest signal that the canonical edit is not available rather than a
    report of an edit with zero clips.
    """
    if not summary:
        return {"edit": "unavailable"}
    out: dict[str, Any] = {
        "clips": summary.get("clips", 0),
        "duration": summary.get("duration", 0.0),
        "order": summary.get("order"),
    }
    if summary.get("problems"):
        out["problems"] = len(summary["problems"])
    if summary.get("dangling"):
        out["dangling"] = summary["dangling"]
    if summary.get("skipped"):
        # A selection that produced no renderable span is named, not buried: the
        # policy chose it and the edit dropped it, and an editor comparing the
        # selection count against the shipped count needs to see why they differ.
        out["skipped"] = list(summary["skipped"])
    if summary.get("interchange"):
        # Reported here and not only as a note because a program EDL that fails
        # its own validator is a broken deliverable, and the detail is what the
        # report channel carries to a caller that never saw the note.
        out["interchange"] = dict(summary["interchange"])
    return out


def _render_detail(outputs: list, edit_report: list) -> dict[str, Any]:
    """The render stage's summary: files on disk, not files requested.

    ``outputs`` is appended only after a clip's MP4s land, so its length is the
    count of orientation-files actually written; the two-per-clip pairing is
    what turns that into a clip count.
    """
    out: dict[str, Any] = {"clips": len(edit_report)}
    if outputs:
        out["orientations"] = f"{len(outputs)} files"
    return out


def _report_detail(report: dict, project: Any) -> dict[str, Any]:
    """The packaging stage's summary: which deliverables the run can point at.

    ``report.json`` is the artifact this stage writes, so a report dict that has
    not been built yet means the stage is still mid-flight; the count of keys
    that name a real path is what an editor can actually go and open.
    """
    if not report:
        return {}
    named = [
        key
        for key in ("captions", "timeline", "content_pack", "editorial_report",
                    "editorial_corpus", "story_discovery", "qa")
        if report.get(key) and Path(str(report[key])).exists()
    ]
    return {"artifacts": len(named)}


def _story_discovery_report(corpus) -> dict[str, Any] | None:
    """Expose the story graph independently of the render budget.

    ``EditorialCorpus.threads`` is the source's discovered story inventory.
    This report is deliberately derived from the already-built graph: it does
    not invent a second story detector and it is not capped by ``--limit``.
    A thread can contain several moments and can overlap another thread; that
    is a property of the source, not a reason to collapse it to one clip.
    """
    if corpus is None:
        return None
    stories = []
    for thread in corpus.threads:
        stories.append({
            "story_id": thread.thread_id,
            "title": thread.title,
            "terms": list(thread.terms),
            "unit_indices": list(thread.unit_indices),
            "start": round(thread.start, 3),
            "end": round(thread.end, 3),
            "duration": round(max(0.0, thread.end - thread.start), 3),
            "arc": thread.arc,
            "span_share": round(thread.span_share, 3),
            "confidence": round(thread.confidence, 3),
            "reason": thread.reason,
            "evidence": list(thread.evidence),
            "links": [
                {
                    "setup_unit": link.setup_unit,
                    "payoff_unit": link.payoff_unit,
                    "kind": link.kind,
                    "strength": round(link.strength, 3),
                    "confidence": round(link.confidence, 3),
                    "reason": link.reason,
                    "evidence": list(link.evidence),
                }
                for link in thread.links
            ],
        })
    return {
        "story_count": len(stories),
        "stories": stories,
        "selection_note": (
            "Story discovery is independent of --limit. --limit controls the "
            "number of final candidate cuts rendered; it does not cap this "
            "story inventory."
        ),
    }

def _apply_selection_policy(passed: list, *, limit: int, corpus=None) -> tuple:
    """Choose the set, in the context of the set being chosen.

    ``passed[:limit]`` — which this replaces — is a sort and a slice: every
    candidate scored independently, the top N taken. Nothing in it knows the
    selection it is building, so two strong clips 20 s apart in one scene both
    win, and on the 53-minute benchmark four of six came from a single
    134-second window while three complete arcs elsewhere were discarded.

    ``engine.selection.select`` re-scores each survivor against the ones already
    chosen and reports every move it makes. It invents no signal: it reads the
    per-clip scores ``editorial`` measured and the corpus records the selector
    never saw, and it is the corpus that is allowed to be absent — a transcript
    too sparse to analyse degrades to the diversity-only terms rather than
    failing the run.

    Returns ``(selected, policy_report)`` so the editorial report can carry the
    terms each selection was made under rather than presenting the six as a
    verdict the editor cannot see the working for.
    """
    from engine.selection import select

    report = select(passed, limit=limit, corpus=corpus)
    return list(report.candidates), report


def _policy_terms(report) -> dict[tuple, list[dict]]:
    """The policy's notes, keyed so the editorial report can attach them.

    The key is the clip's own identity — start, end, hook — rather than its slot
    in ``selected``: the LLM pass below can replace a candidate object, and the
    report must attribute each term to the clip the policy scored rather than to
    whichever clip ends up in that position. A clip nothing fired for is simply
    absent, which is itself information: it won on its own score.
    """
    return {
        (round(s.candidate.start, 3), round(s.candidate.end, 3),
         s.candidate.hook): [
            {"term": n.term, "delta": round(n.delta, 4),
             "against": n.other, "reason": n.reason}
            for n in s.notes
        ]
        for s in report.selected
    }


def _select_detail(thoughts: list, ranked: list, gate_results: list[tuple],
                   limit: int, selected: list | None = None) -> dict[str, Any]:
    """Editorial stage summary: the funnel the gate and the policy produced.

    Every number here is a count of a list the stage already built; none is
    derived for display. ``selected`` is the selection the policy actually made,
    which is not the same list as ``passed[:limit]`` once the policy re-orders
    survivors against each other — reporting the pre-policy head of the list
    would describe a selection the run did not make.
    """
    passed = [c for c, ok, _r in gate_results if ok]
    kept = passed[:limit] if selected is None else selected
    out: dict[str, Any] = {
        "candidates": len(thoughts),
        "selected": len(kept),
    }
    if len(gate_results) - len(passed):
        out["rejected"] = len(gate_results) - len(passed)
    bench = len(passed) - len(kept)
    if bench > 0:
        out["bench"] = bench
    return out


def _verify_detail(fit: Any, coverage: Any) -> dict[str, Any]:
    """The language/coverage audit's summary, from the verdicts it returned.

    ``coverage`` is absent on the language-refusal path, which raises before it
    runs; the UI then shows the fitness verdict alone rather than a coverage
    figure that was never measured.
    """
    out: dict[str, Any] = {}
    if getattr(fit, "verdict", None):
        out["language"] = str(fit.verdict)
    if coverage is not None:
        ratio = getattr(coverage, "coverage_ratio", None)
        if ratio is not None:
            out["coverage"] = f"{ratio * 100:.0f}%"
        if getattr(coverage, "vad_backend", None):
            out["vad"] = coverage.vad_backend
    return out


def _chunk_progress(progress: ProgressFn | None, stage: str) -> ProgressFn | None:
    """Adapt a UI progress sink to the transcription providers' callback.

    The providers report per-chunk facts they already compute for their own
    print lines (index, count, ETA); this only forwards them, so the percent on
    screen is the chunk position the decode actually reached.
    """
    if progress is None:
        return None

    def _send(payload: dict[str, Any]) -> None:
        event: dict[str, Any] = {
            "type": "progress",
            "stage": stage,
            "current": payload.get("index"),
            "total": payload.get("count"),
        }
        if payload.get("eta") is not None:
            event["eta"] = payload["eta"]
        progress(event)

    return _send


def _transcribe_detail(
    transcript: list, detected: str | None, provenance: dict, reused: bool
) -> dict[str, Any]:
    """Transcription stage summary from the provenance the run already keeps."""
    out: dict[str, Any] = {
        "segments": len(transcript),
        "language": detected or "unknown",
        "model": provenance.get("model"),
    }
    if reused:
        out["cache"] = "reused"
    elif provenance.get("downgrade_reason"):
        out["model"] = f"{provenance.get('model')} (downgraded)"
    return out


def _input_detail(info: dict) -> dict[str, Any]:
    """The input stage's summary, restricted to what the media reported.

    A local file carries no yt-dlp metadata, so ``title`` and ``resolution`` are
    absent there by construction; the UI shows what exists rather than a
    placeholder word where a value is missing.
    """
    out: dict[str, Any] = {}
    if info.get("title"):
        out["title"] = str(info["title"])[:60]
    width, height = int(info.get("width") or 0), int(info.get("height") or 0)
    if width and height:
        out["resolution"] = f"{width}x{height}"
    return out


def _no_speech_diagnosis(peak: float | None, unreadable: str | None = None) -> str:
    """The message for an empty transcript, chosen by what the audio showed.

    Every empty transcript used to get the same answer — the source has no
    audible speech — which was true for a silent film and false for a source
    whose container had lost its audio. The peak level of the extraction is
    what separates them, and it is reported back to the user because a number
    is what makes the diagnosis checkable rather than merely confident.

    ``unreadable`` covers the third case, which peak level cannot express: the
    extraction exists but will not parse. That is not silence and it is not a
    loud-but-undecodable file, so it gets its own wording. ``None`` for the
    peak still means the file is absent.
    """
    if unreadable:
        return (
            f"the extracted audio could not be read ({unreadable}), so its "
            "level is unknown rather than low — the extraction itself is "
            "damaged. Try re-muxing or re-encoding the source by hand."
        )
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


def _coverage_corroboration(coverage, *, bypassed: bool) -> str:
    """What the coverage gate already measured, when it measured the same defect.

    The two guards are independent by construction: one compares the transcript
    against the audio, the other scores the words the transcript contains. When
    both refuse the same run for the same underlying reason — a collapsed
    transcript is collapsed whether you measure it in audio covered or in
    per-word probability — the editor should hear it once, as one diagnosis
    backed by two measurements, rather than as two refusals that do not
    reference each other. ``coverage.detail`` is reused verbatim so the numbers
    in this sentence cannot drift from the gate's own.
    """
    if coverage is None or coverage.verdict != "coverage_collapse":
        return ""
    # The detail is a full sentence; only its wording is wanted here, not its
    # terminal period — the sentence this clause is spliced into supplies that.
    own = coverage.detail.rstrip()
    if own.endswith("."):
        own = own[:-1]
    clause = f"The coverage gate measured this independently: {own}."
    if bypassed:
        clause += (
            " --skip-coverage-check was passed, so the run continued past that "
            "gate to reach this one."
        )
    return " " + clause


def _no_story_diagnosis(
    thoughts, ranked, gate_results, rejection_reasons,
    coverage=None, coverage_bypassed: bool = False,
) -> str:
    """The message when nothing was selected, chosen by where the chain stopped.

    "No complete standalone stories passed the editorial/context gate" was
    written for one case — candidates existed and the gate judged them — and it
    was returned for every other case too, including ones where no candidate
    was ever constructed. Those are not the same claim, and a user cannot act
    on a difference the message hides: a transcript too short to hold a
    complete thought is a length problem, a low-confidence transcript is a
    transcription problem an editor can re-run with a bigger model, and a gate
    rejection is an editorial judgement an editor can challenge. Naming which
    one happened is what makes the refusal checkable instead of merely
    confident, the same standard ``_no_speech_diagnosis`` already holds.
    """
    # The scan could not build a single span. With the punctuation fallback in
    # make_thoughts this needs a transcript with no sentences at all, so it is
    # now the rare case rather than the common one — but it still must not be
    # reported as a judgement the engine made.
    if not thoughts:
        return (
            "no candidate spans could be built from the transcript at all, so "
            "nothing was searched and nothing was rejected: selection saw no "
            "material it could evaluate. A transcript this short, or this "
            "degenerate, has no complete thought to extract."
        )
    # Spans existed but not one survived to a scored candidate. Rank drops a
    # span in one of two places that mean different things, and only the
    # survivors of the first are worth reporting a length for: its 8 s / 20 word
    # floor removes spans that cannot stand alone, and its own gate call removes
    # ones that can. The gate counts are all zero here, so a message about the
    # gate would be describing a step that did not happen for these spans.
    if not ranked:
        eligible = [
            t for t in thoughts
            if 8 <= t.duration <= 55
            and len(re.findall(r"\w+", t.text, flags=re.UNICODE)) >= 20
        ]
        if not eligible:
            shortest = min(t.duration for t in thoughts)
            densest = max(len(re.findall(r"\w+", t.text, flags=re.UNICODE)) for t in thoughts)
            return (
                f"{len(thoughts)} candidate span(s) were found, but none was long "
                f"enough and word-dense enough to stand alone as a clip (shortest "
                f"{shortest:.1f} s against an 8 s floor, most words {densest} "
                f"against 20). The source has speech, but no complete thought in "
                f"it is long enough to carry one."
            )
        return _refused_while_ranking(
            eligible, coverage=coverage, coverage_bypassed=coverage_bypassed,
        )
    # The case the old message was written for: candidates survived ranking and
    # were then refused at the final gate. Rank already passed these through
    # validate once, so reaching here means the two passes disagreed — the gate
    # is deterministic given a candidate, so in practice this is a patched or
    # non-deterministic gate, not a transcript property. The dominant reason is
    # named because it is the one an editor can act on, and a low-confidence
    # refusal carries the measured number for the same reason a peak level does.
    dominant = max(rejection_reasons, key=rejection_reasons.get) if rejection_reasons else "unknown"
    n = len(gate_results)
    if dominant == "low factual confidence":
        confs = [c.confidence for c, ok, _r in gate_results if not ok and c.confidence is not None]
        measured = f" measured {min(confs):.2f}" if confs else ""
        return (
            f"{n} candidate(s) were scored and every one was refused by the "
            f"editorial/context gate, most often for low factual confidence "
            f"(mean per-word probability{measured}, against a 0.75 floor). The "
            f"transcription is probably wrong rather than the stories being "
            f"weak — re-run with a larger model, or with --language set if the "
            f"detected language is not the one spoken."
            + _coverage_corroboration(coverage, bypassed=coverage_bypassed)
        )
    return (
        f"{n} candidate(s) were scored and every one was refused by the "
        f"editorial/context gate, most often for: {dominant}."
    )


def _refused_while_ranking(eligible, coverage=None, *, coverage_bypassed: bool = False) -> str:
    """Why nothing survived ranking, for spans that were otherwise eligible.

    Rank discards the gate's reason (it only needs the boolean), so it is
    re-derived here on scored candidates — the same ``score_candidate`` rank
    itself uses, so the confidence and context numbers in the message are the
    ones the gate actually saw. This is a third pass over the gate, but it runs
    only on a path that is already a failure and only over the eligible spans;
    the alternative is a message that says a refusal happened and cannot say
    what it was, which is the defect this whole helper exists to close.
    """
    reasons: dict[str, int] = {}
    confs: list[float] = []
    n_refused = 0
    for t in eligible:
        candidate = score_candidate(t)
        outcome = validate(candidate)
        ok = bool(outcome[0])
        reason = str(outcome[1]) if len(outcome) > 1 else "rejected"
        if ok:
            continue
        n_refused += 1
        reasons[reason] = reasons.get(reason, 0) + 1
        if reason == "low factual confidence":
            confs.append(float(candidate.confidence))
    if not reasons:
        # Every eligible span clears the gate but none survived ranking's own
        # diversity limits — too similar to an already-chosen clip, or one more
        # of a story type rank has already taken two of. That is a budget
        # constraint on variety, not a judgement on any single span, and saying
        # otherwise would misdescribe the only reason a clip is missing here.
        return (
            f"{len(eligible)} span(s) were long enough, word-dense enough and "
            f"clear the editorial/context gate, but ranking set every one aside "
            f"to keep the selection varied — none was refused on its merits. "
            f"Raise --limit to see more of them."
        )
    dominant = max(reasons, key=reasons.get)
    measured = f" (best measured {max(confs):.2f}, against a 0.75 floor)" if confs else ""
    remedy = ""
    if dominant == "low factual confidence":
        # The confidence number alone is not actionable: an editor reading 0.50
        # has no way to tell a transcription defect from weak material, so the
        # remedy travels with the diagnosis for the same reason it does on the
        # post-ranking refusal above.
        remedy = (
            " The transcription is probably wrong rather than the stories being "
            "weak — re-run with a larger model, or with --language set if the "
            "detected language is not the one spoken."
            + _coverage_corroboration(coverage, bypassed=coverage_bypassed)
        )
    return (
        f"{n_refused} eligible span(s) were scored, but every one was refused "
        f"by the editorial/context gate while ranking, most often for: "
        f"{dominant}{measured}.{remedy} No clip was lost to a budget — every one "
        f"was refused on its merits."
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
    skip_language_check: bool = False,
    transcription_provider: str | None = None,
    progress: ProgressFn | None = None,
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
    provider = transcription_provider or default_transcription_provider()
    if provider not in TRANSCRIPTION_PROVIDERS:
        raise ValueError(
            f"transcription provider must be one of: {', '.join(TRANSCRIPTION_PROVIDERS)}"
        )
    run_started = time.time()
    project = out_root / "project"
    state_path = project / STATE_NAME
    # Read before the directory is created: the transcript cache decides
    # whether a cloud run needs a credential at all, and that decision has to
    # be made before anything is written, so a run that cannot authenticate
    # leaves no project behind. Reading a state file that does not exist yet
    # yields an empty state rather than creating the directory.
    state = _load_state(state_path)
    cloud_key = None
    if provider != "local":
        # Credentials are resolved only when a transcription will actually run.
        # An already-completed transcript for this source and model is reused
        # without a live key, which is what the cache is for: an expired or
        # absent key must not block re-running a fixed pipeline over a
        # transcript that was already paid for. An explicit cloud choice still
        # never falls back to local — when the transcript is not cached the key
        # is required and the run fails rather than switching engines.
        cloud_key = _cached_or_require_key(
            state, source, model, language, provider,
            transcript_path=project / "transcript.json")
    if provider == "local" and select_engine(model) is None:
        raise RuntimeError(_ENGINE_INSTALLED_MESSAGE)
    project.mkdir(parents=True, exist_ok=True)
    # Resolved before anything is recorded: report.json quotes these paths to
    # readers that are not standing where the CLI was invoked — the desktop
    # app's sidecar among them, whose working directory is the app's own. A
    # relative path points somewhere else for them, silently.
    project = project.resolve()
    state_path = project / STATE_NAME
    state = _load_state(state_path)
    with _stage(progress, "input", detail=lambda: _input_detail(info)):
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
            # the media's own path rather than a URL. tools/nle_check.py reads
            # that key to find the media and derive the frame rate the timelines
            # were stamped with; without it, every fps-dependent check reports
            # "could not probe the source" on a local-file run that otherwise
            # succeeded.
            if state.get("source") != source:
                state.update({"source": source, "media_info": {}})
                _save_state(state_path, state)

    fingerprint = _fingerprint(media, source, model, language, provider)
    transcript_path = project / "transcript.json"

    def transcribe_current(target: Path):
        if provider == "local":
            segments, lang, provenance = transcribe_auto(
                target, model_name=model, language=language,
                checkpoint_dir=project / "transcription",
                chunk_minutes=chunk_minutes, beam_size=beam_size,
                cpu_threads=cpu_threads,
                progress=_chunk_progress(progress, "transcribe"),
            )
            provenance["provider"] = "local"
            return segments, lang, provenance
        from providers.cloud_transcribe import transcribe as transcribe_cloud
        return transcribe_cloud(
            target, provider, cloud_key or "", language=language,
            checkpoint_dir=project / "transcription",
            chunk_minutes=chunk_minutes,
            progress=_chunk_progress(progress, "transcribe"),
        )

    cached = _load_transcript(transcript_path) if state.get("fingerprint") == fingerprint else None
    # The cache is only actually reused when no explicit language was asked
    # for: a cached transcript plus `--language x` falls through and
    # re-transcribes, so `cached` alone is not the reuse signal.
    transcript_reused = bool(cached and not language)
    with _stage(progress, "transcribe", detail=lambda: _transcribe_detail(
        transcript, detected, asr_provenance, transcript_reused)):
        if transcript_reused:
            transcript, detected = cached
            # Provenance is read back rather than carried in a local: this branch
            # skips transcription entirely, and the report still has to name
            # which model produced the words a resumed run is reusing. The
            # recovery marker goes with it for the same reason.
            asr_provenance = _asr_provenance(transcript_path) or {}
            recovery_note = _recovery_note(transcript_path)
            print("[cache] using unchanged transcript", flush=True)
        else:
            selected_model = model if provider == "local" else (
                "whisper-large-v3" if provider == "groq" else "gemini-3.5-transcribe"
            )
            print(
                f"[transcribe] provider={provider}; model={selected_model}; "
                f"language={language or 'auto'}; chunk={chunk_minutes}min",
                flush=True,
            )
            transcript, detected, asr_provenance = transcribe_current(media)
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
                            "speaker": s.speaker,
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
            audio_path = project / "transcription" / "audio.wav"
            try:
                peak = peak_dbfs(audio_path)
            except AudioReadError as exc:
                # Not measurable is not the same as silent. A file that is there
                # and cannot be parsed is a decode that went wrong, which is the
                # damaged-container case below — reporting it as silence would send
                # a user looking for an audio track their file does have.
                peak = None
                unreadable = str(exc)
            else:
                unreadable = None
            if unreadable or (peak is not None and math.isfinite(peak)
                              and peak > SILENT_PEAK_DBFS):
                recovered = _recover_from_a_damaged_container(
                    media, project / "source",
                    transcribe=transcribe_current,
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
                                 "words": [asdict(w) for w in s.words],
                                 "speaker": s.speaker}
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
            # Re-measured on purpose: the recovery above may have remuxed the
            # source and re-extracted the audio, in which case the peak taken
            # before it describes a file that no longer exists. Measured here
            # instead of hoisted, because before this point the peak was a
            # decision and here it is a report, and only the report needs to
            # survive an extraction that still will not parse.
            try:
                peak = peak_dbfs(project / "transcription" / "audio.wav")
                unreadable = None
            except AudioReadError as exc:
                peak, unreadable = None, str(exc)
            raise RuntimeError(
                "Transcription produced no speech: the source has no audible "
                f"spoken content. {_no_speech_diagnosis(peak, unreadable)}"
            )

    # Before comparing the transcript against the audio, ask a cheaper and more
    # specific question of it: can the model that just ran transcribe this
    # language at all? The coverage gate below catches a transcript that
    # describes too little of the audio, but a dense source in a language the
    # model cannot handle has high coverage, well-formed text and a wrong one —
    # the corpus's Telugu source measures 151% WER on this model, so the output
    # carries more error words than the reference has words. Every other audit
    # reads the transcript's text and cannot tell a fluent hallucination from a
    # faithful one, so this is the one check that consults a table measured
    # offline instead of asking the suspect.
    # The model that *ran* is the one to score, not the one requested: a silent
    # downgrade to a smaller model can move a language across the usable line
    # and the verdict has to follow the model that produced the words.
    fit = language_fitness(detected or language, asr_provenance.get("model") or model)
    # ``seen`` carries the coverage object out to the stage's detail callback;
    # it is assigned inside the block and read once, after the block closes.
    seen: dict[str, Any] = {}
    with _stage(progress, "verify",
                detail=lambda: _verify_detail(fit, seen.get("coverage"))):
        if language_is_refusable(fit) and not skip_language_check:
            raise RuntimeError(
                f"Transcription is not usable on this language: {fit.detail} "
                "Re-run with a model this language is measured on, or pass "
                "--skip-language-check once you have verified the transcript by "
                "hand — but verify it, because the captions and the cut points "
                "are all derived from it."
            )
    # A `poor` verdict does not stop the run, and not for the comforting reason:
    # the poor band (above the bar, at or below 100% WER) has one measured
    # member — Telugu on medium, 91.54% — and its only measured alternative is
    # worse, so a refusal would name a fix the corpus does not have. The rest of
    # that band is unmeasured, and refusing inside it would be guessing where
    # the data is absent. A `not_measured` verdict never stops it either —
    # refusing on absence would block every language the table has not covered,
    # which is most of them. Both are recorded below so neither can look like a
    # pass.

    # The near-empty case: a transcript that is well-formed, non-empty and in
    # the right language yet describes a small fraction of audio that is
    # mostly speech. Nothing else in the pipeline sees this, because every
    # other audit compares the transcript against itself or against the
    # selection, never against the audio — the corpus shipped exactly this as
    # an OK deliverable (4.5% of a 73%-speech-energy source). Runs before
    # selection and render, which are the parts that cost time.
    # The coverage gate needs a backend that reads the audio, so the pipeline's
    # own `vad_backend` is forwarded to it: the same instrument that decided what
    # counts as speech for the selection should supply the gate's audio truth.
    # Whisper is the one choice that cannot be forwarded — it infers speech from
    # transcript word timings, so the gate would compare the transcript against
    # itself and a collapse would sit at ratio 1.0 by construction
    # (`engine.coverage._select_backend` refuses it for that reason). Falling
    # back to the gate's own chain here keeps a `--vad-backend whisper` selection
    # run working while the audit still comes from an instrument that listened;
    # the backend that actually ran is recorded in `coverage` below.
        gate_backend = None if vad_backend == "whisper" else vad_backend
        coverage = check_coverage(project / "transcription" / "audio.wav",
                                  [{"start": s.start, "end": s.end} for s in transcript],
                                  vad_backend=gate_backend)
        seen["coverage"] = coverage
        if coverage.verdict == "coverage_collapse" and not skip_coverage_check:
            raise RuntimeError(
                f"Transcription coverage collapse: {coverage.detail}. Re-run with a "
                "larger model if the language is a low-resource one, or pass "
                "--skip-coverage-check once you have verified the transcript by "
                "hand — but verify it, because this check exists to stop a wrong "
                "deliverable shipping as a success."
            )

    # The corpus is built *before* selection, not after. For the whole beta it
    # ran at the end of this function — computed, written to JSON and discarded
    # for the one purpose that would have used it, because the selector never
    # saw it. On the 53-minute benchmark the corpus's threads held all three
    # strong regions the final six ignored, and the selector picked four of six
    # clips out of one 134-second window with no access to any of that evidence.
    # Building it here costs 0.15 s at that length and is the change that makes
    # the corpus evidence load-bearing in the edit rather than in a report.
    #
    # A failure to build is reported, not fatal: the selection layer reads a
    # ``None`` corpus as absent and degrades to its diversity-only terms, so a
    # transcript too sparse to analyse still selects — on weaker evidence, which
    # the report says. ``None`` is also the signal ``engine.memory.populate.
    # build`` reads as "the corpus stage failed upstream".
    try:
        corpus_report = build_corpus(
            transcript,
            duration=max((s.end for s in transcript), default=0.0),
            language=detected or "unknown",
            source=source,
        )
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        print(f"[selektyt] corpus analysis unavailable: {exc}", file=sys.stderr)
        _notify(progress, type="note", level="warn",
                text=f"corpus analysis unavailable: {exc}")
        corpus_report = None

    # Persist the complete story inventory before any output-budget selection.
    # This is the evidence that ``6 selected`` does not mean ``6 stories found``.
    story_discovery = _story_discovery_report(corpus_report)
    if story_discovery is not None:
        (project / "story_discovery.json").write_text(
            json.dumps(story_discovery, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    thoughts = make_thoughts(transcript)
    # ``--limit`` is an output budget, not a discovery budget. Score the
    # complete eligible candidate pool before the gate and set-selection layers;
    # the old ``rank(limit * 3)`` call silently reduced discovery to 18 candidates
    # on the default six-output run.
    ranked = score_all(thoughts)
    # Validate once, not twice: the previous form called validate() again when
    # building the rejected list, so a candidate's gate reason was computed two
    # expensive times and the two lists could disagree on a non-deterministic
    # gate. Every reason below comes from this single pass.
    #
    # ``validate`` historically returned a 2-tuple; some callers/tests still
    # patch it that way, so both shapes are accepted rather than forcing a
    # breaking change on the contract.
    gate_results: list[tuple] = []
    with _stage(progress, "select", detail=lambda: _select_detail(
        thoughts, ranked, gate_results, limit, selected)):
        for i, c in enumerate(ranked, 1):
            outcome = validate(c)
            ok = bool(outcome[0])
            reason = str(outcome[1]) if len(outcome) > 1 else "rejected"
            gate_results.append((c, ok, reason))
            # A per-candidate tick is real progress: the gate is the work this
            # stage does, and every candidate through it is a unit of it.
            _notify(progress, type="progress", stage="select",
                    current=i, total=len(ranked))
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
        selection = _apply_selection_policy(passed, limit=limit,
                                            corpus=corpus_report)
        selected, policy_report = selection

        # The selection policy chooses strong material; the story composer now
        # chooses the *telling*.  It searches the full gate-passed pool rather
        # than composing only the six clips the policy happened to rank first.
        # This is the important distinction between "six good clips" and "one
        # coherent story made from six good moments".
        story_source_seconds = max((c.end for c in transcript), default=0.0)
        story_target_seconds = max(120.0, min(600.0, story_source_seconds * 0.12))
        story_max_moments = max(10, min(24, math.ceil(story_target_seconds / 20.0)))
        story_variants = compose_story_variants(
            passed,
            corpus=corpus_report,
            max_moments=story_max_moments,
            target_seconds=story_target_seconds,
        )
        story_plan = story_variants[0] if story_variants else compose_story(
            passed,
            corpus=corpus_report,
            limit=limit,
            max_moments=story_max_moments,
            target_seconds=story_target_seconds,
        )
        (project / "story_variants.json").write_text(
            json.dumps([
                {
                    "style": p.style,
                    "score": round(p.score, 4),
                    "duration": round(sum(max(0.0, c.end - c.start) for c in p.candidates), 3),
                    "moments": len(p.candidates),
                    "source_spans": [
                        {"start": round(c.start, 3), "end": round(c.end, 3)}
                        for c in p.candidates
                    ],
                }
                for p in story_variants
            ], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if story_plan.candidates:
            selected_ids = {id(c) for c in story_plan.candidates}
            by_id = {
                id(s.candidate): s
                for s in policy_report.selected + policy_report.deferred
            }
            composed_scored = tuple(
                by_id.get(id(c), Scored(candidate=c, base=c.total_score))
                for c in story_plan.candidates
            )
            composed_deferred = tuple(
                by_id.get(id(c), Scored(candidate=c, base=c.total_score))
                for c in passed if id(c) not in selected_ids
            )
            policy_report = SelectionReport(
                selected=composed_scored,
                deferred=composed_deferred,
                notes={
                    **policy_report.notes,
                    "story_composer": list(story_plan.reasons) + [
                        f"chosen construction: {story_plan.style}",
                        f"whole-story score: {story_plan.score:.3f}",
                    ],
                },
            )
            selected = list(story_plan.candidates)
        # The bench: candidates that passed every editorial gate and lost only to
        # the --limit budget — or to a selection-policy term, which is a different
        # loss and is reported separately below. Recording just the count left an
        # editor staring at "5 more clips passed" with no way to see them, so the
        # report now carries the candidates themselves and the count is a claim
        # the artifact backs.
        shipped = {id(c) for c in selected}
        bench = [c.to_bench_dict() for c in passed if id(c) not in shipped]
        if not selected:
            raise RuntimeError(
                "No complete standalone stories were selected. "
                + _no_story_diagnosis(
                    thoughts, ranked, gate_results, rejection_reasons,
                    coverage=coverage, coverage_bypassed=skip_coverage_check,
                )
            )
        # The policy's terms, keyed on the clip's stable identity rather than its
        # position in ``selected``: ``attach_metadata`` runs below and an
        # editor's report must attribute each term to the clip the policy
        # actually scored, not to whichever clip happens to sit in that slot.
        policy_terms = _policy_terms(policy_report)

        if ollama_url:
            selected = [attach_metadata(c, ollama_url) for c in selected]
        (project / "selections.json").write_text(
            json.dumps([c.to_dict() for c in selected], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    editorial_report = []
    for c in selected:
        entry = {
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
        }
        # Why the policy kept this clip, in its own terms: the scene it shared
        # with another selection, the opening it was held back for, the arc it
        # closes. Absent when the clip won on its own score and nothing fired,
        # which is also worth seeing.
        terms = policy_terms.get(
            (round(c.start, 3), round(c.end, 3), c.hook))
        if terms:
            entry["policy"] = terms
        editorial_report.append(entry)
    (project / "editorial_report.json").write_text(
        json.dumps(editorial_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # The corpus layer (topics, threads, redundancy groups, rhythm) is the
    # story-memory layer's input. It was originally a finished, tested analysis
    # that nothing consumed — reachable only by importing engine.corpus, so an
    # editor never saw the redundancy groups already computed and never saw a
    # topic spine at all. This file is what changed that: engine.story_path and
    # engine.story_view read it back through engine.corpus_io, so a run writes
    # the canonical graph the story view is derived from. It is deterministic,
    # pure and 0.2 s on an 8.9-minute source, so writing it alongside the other
    # reports costs nothing and it degrades to a valid corpus with explanatory
    # notes on a transcript too short or too sparse to analyse. A failure here
    # must not take down a run whose clips are already selected and rendered, so
    # the write reports rather than raises. The report then names the path only
    # if the file landed: a key pointing at a missing artifact is worse than an
    # absent key, because a UI resolving it fails loudly and an editor reads
    # that as a broken run.
    corpus_path = project / "editorial_corpus.json"
    with _stage(progress, "corpus", detail=lambda: _corpus_detail(
            corpus_report, memory_path)):
        # The corpus was built above, before selection, so the selection policy
        # could read it; this stage writes the record the memory layer and the
        # story view read. ``corpus_report`` is ``None`` when the build failed
        # above, and that is the signal ``engine.memory.populate.build`` reads
        # as "the corpus stage failed upstream" — the memory then records no
        # topic-level evidence at all rather than half a corpus, and the summary
        # below does not duplicate a diagnosis the except already printed.
        if corpus_report is not None:
            try:
                corpus_path.write_text(
                    json.dumps(corpus_report.to_dict(), ensure_ascii=False,
                               indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                print(f"[selektyt] corpus write unavailable: {exc}", file=sys.stderr)
                _notify(progress, type="note", level="warn",
                        text=f"corpus write unavailable: {exc}")

        # Video memory: the speech regions and the corpus analysis above were both
        # already computed and both were about to be discarded. ``coverage`` holds
        # the regions the VAD measured for the gate — the only pass over the whole
        # audio a default run makes — and ``corpus_report`` holds the topics,
        # beats, setup-payoff links and b-roll markers. Capturing both here costs
        # no decode, no listen and no inference, and it is what makes "what was on
        # screen while this was said" answerable later. A failure here is
        # reported exactly like the corpus failure above and for the same reason:
        # the run's clips are already selected, and a memory that could not be
        # written is a missing report, not a missing deliverable.
        memory_summary: dict = {}
        memory_path = project / "memory.json"
        if coverage.verdict == "not_measured":
            # No duration and no regions, so Level 0 would be a source of zero
            # length and every recorded span an out-of-bounds warning. Writing
            # that would make a source the memory cannot measure look like one it
            # measured and found empty, which is the same confusion as a green
            # coverage verdict on an unmeasured run — the reason ``coverage`` is
            # recorded on every run above.
            memory_summary = {"skipped": coverage.detail}
        else:
            try:
                from engine.memory import SourceMeta, populate
                media_st = media.stat()
                memory, summary = populate.build(
                    SourceMeta(
                        path=str(media),
                        duration=coverage.duration_s,
                        # The media digest excludes the ASR parameters, because a
                        # re-transcribe changes the words and not the pixels; the
                        # transcript digest below includes them for exactly that
                        # reason.
                        fingerprint=hashlib.sha256(
                            f"{source}|{media.resolve()}|{media_st.st_size}"
                            f"|{media_st.st_mtime_ns}".encode()
                        ).hexdigest(),
                        transcript_fingerprint=fingerprint,
                        width=int(info.get("width", 0) or 0),
                        height=int(info.get("height", 0) or 0),
                    ),
                    speech_regions=tuple(coverage.speech_regions),
                    vad_backend=coverage.vad_backend,
                    corpus=corpus_report,
                    language=detected or "unknown",
                )
                memory.save(memory_path)
                memory_summary = summary.as_dict()
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                print(f"[selektyt] video memory unavailable: {exc}", file=sys.stderr)
                memory_summary = {"error": f"{type(exc).__name__}: {exc}"}
                _notify(progress, type="note", level="warn",
                        text=f"video memory unavailable: {exc}")

        # Retrieval index: the transcript was already built into thoughts, ranked
        # and selected on, and the index is what makes it *queryable* after the
        # run — the difference between a source whose evidence this edit consumed
        # and one a later query can still search. It re-uses the fitness verdict
        # already measured at the top of this function, the speech regions the
        # coverage gate already measured, and the topic spans the corpus above
        # already found, so it costs no decode, no listen and no inference. Its
        # fingerprint is the run digest above — which includes the model and
        # the language rather than the media bytes alone, for exactly the
        # reason ``SourceMeta``'s comment gives — so a re-transcribe
        # invalidates a stored index rather than leaving it silently re-usable
        # against words that no longer match it.
        retrieval_summary: dict = {}
        retrieval_path = project / "retrieval-index.json"
        if not any(getattr(s, "words", None) for s in transcript):
            # Without word timestamps there are no postings to build: every term
            # needs a time to answer an interval query, and an index whose terms
            # carry none is a vocabulary list. Reporting the absence rather than
            # writing an empty index keeps "no index" distinguishable from "an
            # index over nothing", the same distinction the memory block above
            # draws for an unmeasured source.
            retrieval_summary = {"skipped": "transcript has no word timestamps"}
        else:
            try:
                from engine.retrieval import build_index
                index = build_index(
                    transcript,
                    language=detected or "unknown",
                    source_fingerprint=fingerprint,
                    fitness=fit,
                    speech_regions=[
                        (float(r.start), float(r.end))
                        for r in coverage.speech_regions
                    ],
                    editorial_units=[
                        (float(t.start), float(t.end))
                        for t in (corpus_report.topics if corpus_report else [])
                    ],
                )
                index.save(retrieval_path)
                retrieval_summary = {
                    "schema": index.schema,
                    "terms": index.n_terms,
                    "words": index.n_words,
                    "segments": len(index.segments),
                    "ngram_fallback": bool(index.ngram_postings),
                    "channels": {
                        name: status.to_dict()
                        for name, status in index.optional.items()
                    },
                }
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                print(f"[selektyt] retrieval index unavailable: {exc}",
                      file=sys.stderr)
                retrieval_summary = {"error": f"{type(exc).__name__}: {exc}"}
                _notify(progress, type="note", level="warn",
                        text=f"retrieval index unavailable: {exc}")

    pacing = aggregate_pacing_report(
        selected, rejected, context_guard_rejections,
        truncated_by_limit=truncated_by_limit,
    )

    audio_summary: dict = {}
    if audio_analysis:
        # The only stage that is opt-in, so it never appears on the panel for a
        # default run — a stage that did not run is not shown as one that did.
        with _stage(progress, "audio",
                    detail=lambda: _audio_detail(audio_summary)):
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

    # The canonical edit: one ordered timeline plus the decisions behind it.
    # ``selected`` leaves the policy in greedy pick order, so until this stage
    # nothing describes the edit as a whole — the render loop numbers the
    # deliverables off that order, making ``01.mp4`` "the clip the policy scored
    # highest" rather than "the opening". The assembly sequences the set into a
    # program and records what it dropped and why, which is the gap G1/G3 the
    # reconnaissance named. It is pure and reads no media, so a failure here is
    # a missing report rather than a missing deliverable and is reported rather
    # than raised, exactly as the corpus and memory writes above are.
    edit_summary: dict = {}
    edit_path = project / "edit.json"
    # Held outside the stage so the render loop below can follow the program the
    # assembly sequenced. It stays None when the assembly could not be built, and
    # every reader of it degrades to the policy's pick order in that case — the
    # deliverables are the selection either way, only their numbering differs.
    assembly: Any = None
    with _stage(progress, "assemble",
                detail=lambda: _assemble_detail(edit_summary)):
        try:
            from engine.assembly import AssemblyConfig, assemble
            assembly = assemble(
                selected,
                media=media,
                fps=source_fps,
                source_duration=source_duration,
                title=f"{source} edit",
                corpus=corpus_report,
                scored=list(policy_report.selected),
                # ``deferred`` holds ``Scored`` records, each wrapping the
                # candidate it scored; the assembly's contract is candidates,
                # so they are unwrapped here rather than making the assembly
                # learn the policy's record type.
                deferred=[s.candidate for s in policy_report.deferred],
                config=AssemblyConfig(order="story"),
            )
            edit_path.write_text(
                json.dumps(assembly.as_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            problems = assembly.timeline.validate()
            edit_summary = dict(assembly.report)
            edit_summary["clips"] = len(assembly.timeline.clips)
            edit_summary["duration"] = round(assembly.duration, 3)
            edit_summary["problems"] = problems
            if assembly.skipped:
                edit_summary["skipped"] = list(assembly.skipped)

            # The canonical edit's own interchange: one EDL and one FCPXML for
            # the whole program, where the per-clip exports below are six
            # separate one-track files. This is the artifact an NLE opens to see
            # the edit as assembled rather than as a bin of clips. The emitters
            # and validators are the same ones the per-clip path already uses,
            # so the two halves cannot drift into different grammars, and the
            # independent EDL reader is run on every export for the same reason
            # it is below: our validator is our own grammar, so a document it
            # accepts is not thereby a document an NLE can read.
            exported = nle_export(assembly.timeline, project / "timeline",
                                  stem="program")
            edl_text = exported["edl"].read_text(encoding="ascii")
            export_problems = validate_edl(edl_text, fps=source_fps)
            ran_reader, indep_problems = independent_parse_edl(edl_text)
            if ran_reader:
                export_problems += indep_problems
            fcpxml_problems = validate_fcpxml(
                exported["fcpxml"].read_text(encoding="utf-8"),
                assembly.timeline,
            )
            edit_summary["interchange"] = {
                "edl": str(exported["edl"]),
                "fcpxml": str(exported["fcpxml"]),
                "problems": export_problems + fcpxml_problems,
                "edl_independently_parsed": ran_reader,
            }
            if export_problems or fcpxml_problems:
                _notify(progress, type="note", level="warn",
                        text=f"the canonical edit's interchange reports "
                             f"{len(export_problems) + len(fcpxml_problems)} "
                             f"problem(s)")
            # Dangling decisions mean the assembly moved a clip without moving
            # the reasoning attached to it — a bug to surface, not a warning to
            # bury, so it is reported at warn and not folded into the summary.
            if assembly.graph.dangling():
                _notify(progress, type="note", level="warn",
                        text=f"{len(assembly.graph.dangling())} decision(s) "
                             f"anchor on no clip in the assembled edit")
            if problems:
                _notify(progress, type="note", level="warn",
                        text=f"the assembled timeline reports "
                             f"{len(problems)} problem(s)")
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            print(f"[selektyt] canonical edit unavailable: {exc}", file=sys.stderr)
            _notify(progress, type="note", level="warn",
                    text=f"canonical edit unavailable: {exc}")

    # The deliverables are numbered in the order the program plays them, not the
    # order the policy picked them. Those agree when the assembly has no reason
    # to reorder — no threads binding a group, no opener strong enough to lead —
    # and visibly disagree otherwise, which is the point of having an assembly
    # at all. ``01.mp4`` is therefore the program's opening rather than the clip
    # the policy scored highest, and a reel that led with its second-strongest
    # clip ships that way in every derivative. The placement order is
    # deterministic for a given selection, so this is stable across re-runs.
    program = (list(assembly.placements) if assembly is not None else [])
    program_order = [p.candidate for p in sorted(program, key=lambda p: p.sequence)] \
        if program else selected

    # A digest of the program's order, for the same reason the selection digest
    # exists: the render cache is keyed by index, so a reel that kept every clip
    # but reordered them would otherwise find a valid MP4 at 01.mp4 — rendered
    # from whichever clip led before — and ship the wrong clip as the opening.
    # The selection digest alone does not catch this, because the selection is
    # unchanged; only the sequence moved.
    program_digest = _program_digest(program_order)

    # Per-clip render cache. The previous single render_fingerprint was only
    # written after the whole loop completed, so a run killed during clip 3 of
    # 6 re-rendered all six even though 1 and 2 were already on disk.
    renders = state.get("renders", {})
    # The burned-in caption digest of each clip, in program order. The assembled
    # reel is a function of these clips, so its cache key is keyed on them: any
    # clip the loop re-rendered or re-captioned appears here, and a reel
    # assembled from the old parts is not reused.
    program_ass_digests: list[str] = []
    with _stage(progress, "render",
                detail=lambda: _render_detail(outputs, edit_report)):
        for idx, c in enumerate(program_order, 1):
            intervals = retention_intervals(c)
            # The ASS is a render input, not a by-product: it is the document burned
            # into the MP4, so it must be written through the guard that keeps an
            # editor's hand on it. Writing it unconditionally here would overwrite a
            # moved cue before the cache below is even consulted, and the moved cue
            # would never reach the render.
            caption, ass_digest = ship_captions(
                c,
                project / "captions" / f"{idx:02d}.ass",
                project / "captions" / f"{idx:02d}.srt",
                renders.get(f"{idx:02d}", {}).get("ass_digest"),
            )
            program_ass_digests.append(ass_digest)
            (project / "captions" / f"{idx:02d}.json").write_text(
                json.dumps(caption_metadata(c), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            pack = build_content_pack(c)
            pack["clip_index"] = idx
            # The span this pack describes, so a reader can join the pack to its
            # clip without trusting position: selections.json is the policy's
            # pick order and the packs are numbered in program order, and a
            # reorder would otherwise leave no way to tell which clip a pack
            # belongs to short of reading its caption text.
            pack["source_start"] = round(c.start, 3)
            pack["source_end"] = round(c.end, 3)
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
            # The ASS digest is in the key for the same reason the selection digest
            # is: the burned-in captions are part of what the cached MP4 *is*, so a
            # changed cue must invalidate the render even when nothing else about
            # the clip moved. It also makes a hand edit self-invalidating — a
            # preserved ASS arrives with a digest that no cached render carries.
            cached = renders.get(f"{idx:02d}")
            # Two bars, one per deliverable orientation, both driven by the part
            # count the render itself reports — no bar moves without a part
            # landing. A clip that is reused from cache shows both bars full,
            # which is what it is: the file was verified on disk, not rendered.
            _notify(progress, type="progress", stage="render",
                    current=idx - 1, total=len(program_order),
                    subprogress=[{"label": "16:9", "fraction": 0.0},
                                 {"label": "9:16", "fraction": 0.0}],
                    note=f"clip {idx:02d}/{len(program_order)}")
            if (
                cached
                and cached.get("fingerprint") == fingerprint
                and cached.get("selection_digest") == sel_digest
                and cached.get("program_digest") == program_digest
                and cached.get("caption_style") == caption_style
                and cached.get("ass_digest") == ass_digest
                and _render_is_valid(fb, vertical=False, source=media)
                and _render_is_valid(sh, vertical=True, source=media)
            ):
                print(f"[cache] reusing rendered clip {idx:02d}", flush=True)
                _notify(progress, type="progress", stage="render",
                        current=idx - 1, total=len(program_order),
                        subprogress=[{"label": "16:9", "fraction": 1.0},
                                     {"label": "9:16", "fraction": 1.0}],
                        note=f"clip {idx:02d} reused from cache")
                _notify(progress, type="note",
                        text=f"clip {idx:02d} reused from cache")
            else:
                # One sink per orientation: the bar for the orientation not
                # currently rendering holds the fraction the completed one
                # reached, so the pair reads as "16:9 done, 9:16 at part 2/3".
                done = {"16:9": 0.0, "9:16": 0.0}

                def _sink(label: str, *, done=done, idx=idx) -> None:
                    def _send(payload: dict) -> None:
                        total = payload.get("parts") or 1
                        done[label] = (payload.get("part") or 0) / total
                        _notify(progress, type="progress", stage="render",
                                current=idx - 1, total=len(program_order),
                                subprogress=[
                                    {"label": "16:9", "fraction": done["16:9"]},
                                    {"label": "9:16", "fraction": done["9:16"]},
                                ])

                    return _send

                render_clip(media, c, fb, caption, vertical=False,
                            fps=source_fps, progress=_sink("16:9"))
                # The 16:9 render has finished and its bar should say so at this
                # instant, not at the 9:16 render's first part event — otherwise
                # the pair reads as "both in progress" through a render that is
                # already half done. A note marks it so the UI can tell this
                # announcement from a part payload, which is also what makes it
                # read as a state change rather than a progress tick.
                _notify(progress, type="progress", stage="render",
                        current=idx - 1, total=len(program_order),
                        subprogress=[
                            {"label": "16:9", "fraction": 1.0},
                            {"label": "9:16", "fraction": 0.0},
                        ], note=f"clip {idx:02d} 16:9 rendered")
                done["16:9"] = 1.0
                render_clip(media, c, sh, caption, vertical=True,
                            fps=source_fps, progress=_sink("9:16"))
                renders[f"{idx:02d}"] = {
                    "fingerprint": fingerprint,
                    "selection_digest": sel_digest,
                    "program_digest": program_digest,
                    "caption_style": caption_style,
                    "ass_digest": ass_digest,
                    "facebook": str(fb),
                    "shorts": str(sh),
                }
                # Checkpoint after each clip, not once at the end: this is what
                # makes an interrupted run resume from the last finished clip.
                state["renders"] = renders
                _save_state(state_path, state)
            # The editable interchange for this clip. The timeline is built from
            # the same retention_intervals the render just concatenated, so an NLE
            # relinking these clips gets the cut that was shipped — but note that
            # this agreement is measured in two legs, not one direct comparison:
            # validate_edl/validate_fcpxml below check the timeline against the
            # plan on the frame grid it was written on, and write_report later
            # probes the rendered MP4s and compares their durations against the
            # same plan's edited_duration at 1%. The two legs meet at the plan, so
            # a render that dropped an interval is caught — outside the sub-1%
            # slop a container muxer can introduce, which is what
            # tools/nle_check.py's direct render-vs-timeline comparison is for at
            # its two-frame tolerance. That tool is not invoked here; run it over
            # the project directory, or tools/corpus_run.py does, or the suite
            # does — tests/test_nle_check.py runs it over the committed corpus and
            # tests/test_pipeline_e2e.py over a tree this code just wrote, which
            # is what catches an emitter drifting past its own self-consistent
            # validation. The independent EDL parse below is the half of that audit
            # that needs no media, so it runs on every export.
            tl = timeline_for_candidate(media, c, fps=source_fps, duration=source_duration)
            exported = nle_export(tl, project / "timeline", stem=f"{idx:02d}")
            # CMX 3600 declares no frame rate, so the list is validated on the grid
            # it was written on. Without the real rate a 24 fps cut is read as 30
            # and correct spans report a spurious few-frame retime.
            edl_text = exported["edl"].read_text(encoding="ascii")
            edl_problems = validate_edl(edl_text, fps=source_fps)
            # A reader that is not ours must accept the same text: the regex above
            # is the emitter's own grammar, so an EDL it accepts is not thereby an
            # EDL an NLE can read. Absent pycmx this stays advisory rather than
            # fatal — see independent_parse_edl for why the two differ.
            ran, indep_problems = independent_parse_edl(edl_text)
            if ran:
                if indep_problems:
                    edl_problems += indep_problems
            else:
                print(
                    "[export] pycmx is not installed, so the EDL was not parsed by an "
                    "independent reader; install the 'nle' extra to verify it",
                    flush=True,
                )
                _notify(progress, type="note", level="warn",
                        text="pycmx absent: the EDL was not independently verified")
            fcpxml_problems = validate_fcpxml(
                exported["fcpxml"].read_text(encoding="utf-8"), tl
            )
            # The same standard applied to the other half: our FCPXML grammar is
            # the emitter's own, so a document it accepts is not thereby a document
            # an NLE can read. The vendored OTIO adapter resolves each clip's media
            # through <media-rep>, where we write it; a parse that comes back with
            # unlinked clips is reported as a problem rather than a pass. Absent the
            # reader this stays advisory, exactly as the EDL half does above.
            fcpxml_text = exported["fcpxml"].read_text(encoding="utf-8")
            ran_fcpxml, indep_fcpxml_problems = independent_parse_fcpxml(fcpxml_text)
            if ran_fcpxml:
                fcpxml_problems += indep_fcpxml_problems
            else:
                print(
                    "[export] the FCPXML reader is not installed, so the FCPXML was "
                    "not parsed by an independent reader; install the 'nle' extra "
                    "to verify it",
                    flush=True,
                )
                _notify(progress, type="note", level="warn",
                        text="the FCPXML reader is absent; the export is unverified")
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
            # The counter inside the loop reports the clip it is about to render,
            # so without this the last clip would leave the stage reading N-1 of
            # N while every clip is on disk, checkpointed and exported. idx here
            # *is* finished — cached or rendered by either branch above — so N/N
            # is what the bar should read.
            _notify(progress, type="progress", stage="render",
                    current=idx, total=len(program_order))

        # The assembled program as one watchable reel. ``program.edl`` sequences
        # these same clips in this same order on this same source, so the reel
        # *is* the per-clip renders joined end to end — concatenating them
        # costs no encode and cannot diverge from the clips the loop just QA'd,
        # where a second render of the same material would be a third opinion
        # on every cut. The captions are already burned in, including any an
        # editor retimed through the shipped ASS, so the reel inherits them.
        # A user without an NLE can now watch the story the assembly found
        # instead of being handed loose cuts plus an interchange file.
        fb_program = project / "facebook" / "program.mp4"
        sh_program = project / "shorts" / "program.mp4"
        parts_fb = [project / "facebook" / f"{i:02d}.mp4"
                    for i in range(1, len(program_order) + 1)]
        parts_sh = [project / "shorts" / f"{i:02d}.mp4"
                    for i in range(1, len(program_order) + 1)]
        # What actually distinguishes one reel from another: the order the
        # assembly chose, plus the burned-in captions of every clip in it. A
        # re-rendered clip or a moved cue changes an entry here and invalidates
        # the cached reel, exactly as the per-clip key does for a clip. Source
        # and style are in the key for the same reasons they are per clip.
        parts_digest = hashlib.sha256(
            "".join(program_ass_digests).encode()
        ).hexdigest()
        cached_program = renders.get("program")
        if (
            cached_program
            and cached_program.get("fingerprint") == fingerprint
            and cached_program.get("program_digest") == program_digest
            and cached_program.get("caption_style") == caption_style
            and cached_program.get("parts_digest") == parts_digest
            and _render_is_valid(fb_program, vertical=False, source=media)
            and _render_is_valid(sh_program, vertical=True, source=media)
        ):
            print("[cache] reusing assembled program reel", flush=True)
            _notify(progress, type="note",
                    text="program reel reused from cache")
        elif program_order and all(p.is_file() for p in parts_fb + parts_sh) \
                and all(_render_is_valid(p, vertical=False, source=media) for p in parts_fb) \
                and all(_render_is_valid(p, vertical=True, source=media) for p in parts_sh):
            _notify(progress, type="note", text="assembling the program reel")
            # Each timeline clip is snapped independently to the sequence
            # frame grid. Match that same summed frame duration here; raw float
            # ``assembly.duration`` can land between frames, while unconstrained
            # stream copy accumulates mux padding from every source cut.
            program_part_durations = [
                sum(
                    max(1, round((end - start) * source_fps)) / source_fps
                    for start, end in retention_intervals(candidate)
                )
                for candidate in program_order
            ]
            concat_renders(parts_fb, fb_program,
                           part_durations=program_part_durations)
            concat_renders(parts_sh, sh_program,
                           part_durations=program_part_durations)
            # A reel the muxer could not produce is a missing deliverable, not a
            # silent absence: the run's per-clip QA below would still pass, so
            # without this check a broken program would ship as a clean run.
            if not _render_is_valid(fb_program, vertical=False, source=media):
                raise RuntimeError(
                    "the assembled 16:9 program reel failed its own validation"
                )
            if not _render_is_valid(sh_program, vertical=True, source=media):
                raise RuntimeError(
                    "the assembled 9:16 program reel failed its own validation"
                )
            renders["program"] = {
                "fingerprint": fingerprint,
                "program_digest": program_digest,
                "caption_style": caption_style,
                "parts_digest": parts_digest,
                "facebook": str(fb_program),
                "shorts": str(sh_program),
            }
            # Same checkpoint discipline as a clip: a run killed after the reel
            # is assembled must not reassemble it.
            state["renders"] = renders
            _save_state(state_path, state)
            _notify(progress, type="note",
                    text=f"program reel assembled ({len(program_order)} clips)")
        else:
            # The clips are the reel's only input, so an unusable one means the
            # reel cannot be built. This does not fail the run: the per-clip
            # deliverables are still good and the QA stage below is what gates
            # them, while a reel that cannot honestly be made is reported as
            # absent rather than faked. ``tools/nle_check.py`` reports the
            # missing reel as SKIP for the same reason.
            print(
                "[render] skipping the program reel: a per-clip render it "
                "needs is missing or not a valid video",
                flush=True,
            )
            _notify(progress, type="note", level="warn",
                    text="the program reel could not be assembled")
        if fb_program.is_file() and sh_program.is_file():
            outputs += [fb_program, sh_program]
    with _stage(progress, "report",
                detail=lambda: _report_detail(report, project)):
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
            # The count the render loop actually targeted, which is lower than
            # selected_count when the assembly dropped a candidate with no
            # renderable span. Stated separately rather than as a delta because
            # both numbers are facts an editor reads independently: how many the
            # policy chose, and how many the program shipped.
            "program_count": len(program_order),
            "rejected_by_context_guard": context_guard_rejections,
            # Separate from the above: these passed every editorial gate and were
            # dropped only because --limit capped the output. A budget, not a
            # judgment — and the bench below is what it counts.
            "truncated_by_limit": truncated_by_limit,
            "story_composition": {
                "style": story_plan.style,
                "score": round(story_plan.score, 4),
                "reasons": list(story_plan.reasons),
            },
            # The bench: these passed every gate and differ from the shipped clips
            # only in rank, so an editor raising --limit reads from here. The
            # entries are visible and comparable; promoting one without re-running
            # the source is not possible, because rank() has no include hook —
            # see docs/PRODUCT_WORKFLOW_AUDIT.md J2.
            "bench": bench,
            "rejection_reasons": dict(sorted(rejection_reasons.items(), key=lambda kv: -kv[1])),
            "outputs": {"facebook": str(project / "facebook"), "shorts": str(project / "shorts")},
            # The assembled reel, named by file rather than directory: it is one
            # deliverable per aspect, not a bucket of them, and a reader who
            # wants the story whole rather than clip by clip goes straight here.
            # Absent when the reel could not be assembled, which the CLI reports
            # as absent rather than as a path that does not resolve.
            "program": ({"facebook": str(fb_program), "shorts": str(sh_program)}
                        if fb_program.is_file() else None),
            "captions": str(project / "captions"),
            "timeline": str(project / "timeline"),
            "content_pack": str(project / "content_pack.json"),
            "editorial_report": str(project / "editorial_report.json"),
            "editorial_corpus": str(corpus_path) if corpus_path.is_file() else None,
            "story_discovery": str(project / "story_discovery.json") if (project / "story_discovery.json").is_file() else None,\n            "story_variants": str(project / "story_variants.json") if (project / "story_variants.json").is_file() else None,
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
                for k in ("provider", "engine", "requested_model", "model", "downgrade_reason",
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
            # Whether the model that ran is one that can transcribe the language it
            # transcribed. Recorded on every run for the same reason the coverage
            # verdict is: a `not_measured` is a statement that this language-model
            # pair has never been scored against a reference corpus, and recording
            # only failures would make the unmeasured case look like a pass.
            # `excluded` names the WER records the loader refused, because a table
            # that quietly drops the records that disagree with it can support any
            # conclusion.
            "language_fitness": fit.as_dict(),
            # Video memory is a *different file*, not a section of this report: the
            # queries it answers ("where is this discussed", "what was on screen
            # while it was said") return intervals, not prose, and they belong in a
            # record a retrieval layer reads. What belongs here is whether the
            # capture happened and which stages failed — an editor who asks the
            # memory a question it cannot answer needs to be told the evidence was
            # never recorded, and a missing memory.json otherwise reads as an empty
            # source rather than a failed capture.
            "memory": {
                "path": str(memory_path) if memory_path.is_file() else None,
                "summary": memory_summary,
            },
            # A third artifact file, and for the same reason: the retrieval index
            # answers "where in this source is X discussed" and that answer is an
            # interval set, not prose. What belongs here is whether the index was
            # written, how large it is, and which optional channels it re-ranked
            # against — an editor who queries a stale index gets answers for a
            # transcript that no longer exists, and a missing file otherwise reads
            # as a source with nothing in it.
            "retrieval": {
                "path": str(retrieval_path) if retrieval_path.is_file() else None,
                "summary": retrieval_summary,
            },
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
        report_builder = ReportBuilder(started_at=run_started)
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
            "transcription_provider": provider,
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
        state.update({"fingerprint": fingerprint, "render_fingerprint": fingerprint, "render_selection_digest": sel_digest, "selected_count": len(selected), "program_count": len(program_order), "qa": str(qa_path)})
        _save_state(state_path, state)
    return project


# ── retention: the bytes a finished run does not need to keep ────────────────

# Directories whose contents the pipeline regenerates on a re-run. ``source`` is
# the downloaded media (a local source is never copied in, so it is absent on a
# local-file run) and ``transcription`` is the extracted 16 k mono audio plus
# the per-chunk ASR checkpoints.
#
# ``transcript.json`` is deliberately *not* on this list: a re-run reuses it, so
# removing it would force a re-transcription on every later invocation. The
# deliverables (``facebook/``, ``shorts/``, ``timeline/``, ``captions/``,
# ``content_pack/``) and every report are likewise kept — they are the output.
REGENERABLE_DIRS = ("source", "transcription")


def _rendered_files(project: Path, renders: dict) -> list[Path]:
    """Every MP4 the state claims to have rendered, by where renders are written.

    Completeness is judged on the conventional location rather than the path
    recorded in the state, because that path's shape is not stable: the current
    run records an absolute path (``project`` is resolved before the render
    loop), but corpus states predating that fix record paths relative to the
    invocation's cwd. Reading the recorded string would then look for a file
    relative to this process's cwd and find or miss it depending on where the
    caller stands — the same ambiguity ``report.json``'s own paths had. The
    renders are always written to ``<project>/facebook/<idx>.mp4`` and
    ``<project>/shorts/<idx>.mp4``, so that is what is checked."""
    out = []
    for idx in renders:
        for kind in ("facebook", "shorts"):
            out.append(project / kind / f"{idx}.mp4")
    return out


def cleanable(project: Path) -> tuple[bool, str]:
    """Whether it is safe to reclaim this project's intermediates.

    A run is cleanable only when every render the edit plan called for is on
    disk. Deleting the extracted audio or the downloaded source partway through
    would break resume: a resumed run needs the source to re-render and the
    checkpoints to avoid re-transcribing the parts it had finished. Once the
    renders exist, none of that is needed again unless the run is re-run, and a
    re-run regenerates both from the source URL or the local path — at the cost
    of a download, which is the trade the caller is opting into.
    """
    project = project.resolve()
    state_path = project / STATE_NAME
    if not state_path.is_file():
        return False, f"{state_path} does not exist; this is not a pipeline project directory"
    state = _load_state(state_path)
    renders = state.get("renders") or {}
    if not renders:
        return False, "no renders are recorded in pipeline_state.json, so the run has not finished"
    for path in _rendered_files(project, renders):
        if not path.is_file():
            return False, f"{path.name} is recorded as rendered but is missing; the run is incomplete"
    selected_count = state.get("program_count", state.get("selected_count"))
    if selected_count is not None and len(renders) < int(selected_count):
        return False, (
            f"{len(renders)} of {selected_count} clips are rendered; "
            "the rest are still outstanding"
        )
    return True, ""


def reclaim_intermediates(project: Path, keep_source: bool = False) -> dict:
    """Remove the intermediates a finished run no longer needs.

    Returns a description of what was removed and how many bytes it held, so
    the caller can report it rather than silently deleting a user's download.
    Refuses (returns ``removed: []`` with a ``reason``) if the run is not
    complete — see :func:`cleanable` for why.

    ``keep_source`` preserves the downloaded media. Deleting it is where almost
    all the bytes are, but it is also what ``tools/nle_check.py`` probes to
    cross-check the render against the timeline, and what a re-render needs. A
    user who wants to re-run with a different style or limit keeps it; one who
    only wants the deliverables does not.
    """
    project = project.resolve()
    ok, reason = cleanable(project)
    if not ok:
        return {"ok": False, "reason": reason, "removed": [], "bytes": 0}

    names = [d for d in REGENERABLE_DIRS if not (keep_source and d == "source")]
    removed = []
    total = 0
    for name in names:
        root = project / name
        if not root.is_dir():
            continue
        size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
        # The state's media_path still points into source/ when it is deleted
        # here; leaving it stale would make a resumed run trust a path that no
        # longer exists instead of re-downloading. media_info is scrubbed
        # already, so it carries no credentials either way.
        shutil.rmtree(root, ignore_errors=False)
        removed.append({"dir": name, "files": "all", "bytes": size})
        total += size

    state_path = project / STATE_NAME
    state = _load_state(state_path)
    media_path = state.get("media_path")
    dropped_media = media_path and not Path(media_path).is_file()
    if dropped_media:
        state["media_path"] = None
        _save_state(state_path, state)

    return {
        "ok": True,
        "removed": removed,
        "bytes": total,
        # Named so a caller reading the report knows a re-run will fetch again
        # rather than discovering it from a cache miss.
        "re_download_on_rerun": dropped_media or None,
    }
