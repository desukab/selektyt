"""Regression tests for the F2/F6/F7/F9/F13/F10/F11/F17 hardening pass.

Each test pins a behaviour the audit flagged as silently wrong:

F2  — hard render failures raise, advisory findings do not; a render that
      succeeded must stay cached instead of being re-encoded because a later
      clip's QA failed.
F6  — --keep-pauses and --remove-filler must actually switch the cut engine;
      they were accepted and ignored (and --keep-pauses could never be False).
F7  — bad --limit/--chunk-minutes/--beam-size must fail before any network or
      ASR work, not after download and transcription.
F9  — the report must separate "rejected by the editorial gate" from
      "truncated by --limit", because they mean different things to an editor.
F13 — a cached render is only reused when ffprobe confirms it is a real,
      correctly-oriented video; a stub or truncated file is re-rendered.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from models.core import Candidate, Segment, Word
from selekyt.pipeline import run as pipeline_run, _program_digest, _selection_digest
from engine.edit_plan import retention_intervals
from engine.captions import _digest, ass_content
from providers.transcribe import _ENGINE_INSTALLED_MESSAGE
from tests.speech_fixtures import write_speech_wav


# ── shared scaffolding ──

def _candidate(start: float, end: float, text: str = "hello world") -> Candidate:
    # The real editorial bar an injected candidate has to clear is 8 s of
    # duration and 20 words of density (``pipeline.py``'s ``_no_story_diagnosis``
    # floor). One word at the span's start passed before that bar existed and
    # now aborts the run before the memory stage is ever reached, which makes
    # every memory assertion vacuous rather than testing the capture.
    duration = end - start
    word_count = max(20, int(duration * 2))
    step = duration / word_count
    words = [
        Word(f"word{i}", start + i * step, start + (i + 1) * step)
        for i in range(word_count)
    ]
    return Candidate(
        start, end, text, "story", text[:12], text[-12:],
        editorial_score=0.9, virality_score=0.8, context_score=0.9,
        confidence=0.9, reason="test", visual_potential=0.8,
        words=words,
    )


_PACING = {
    "total_candidates": 1, "selected_count": 1, "rejected_count": 0,
    "context_guard_rejections": 0, "avg_duration": 10.0,
    "duration_distribution": {"<15s": 1, "15-30s": 0, "30-45s": 0, "45-55s": 0},
    "silence_pct": 0.0, "filler_pct": 0.0, "avg_words_per_second": 2.0,
    "avg_hook_latency": 0.0, "payoff_presence_rate": 1.0,
    "duplicate_overlap_rate": 0.0,
}


def _pipeline_patches(tmp_path, candidates, gate_results=None):
    """Patch the pipeline's heavy stages so a run is deterministic offline."""
    if gate_results is None:
        gate_results = [(True, "ok") for _ in candidates]
    return {
        "download": patch("selekyt.pipeline.download"),
        "render_clip": patch("selekyt.pipeline.render_clip"),
        "write_report": patch("selekyt.pipeline.write_report",
                              return_value=Path("qa.json")),
        "make_thoughts": patch("selekyt.pipeline.make_thoughts",
                               return_value=[Segment(10, 20, "hello", [])]),
        "score_all": patch("selekyt.pipeline.score_all", return_value=candidates),
        "validate": patch("selekyt.pipeline.validate", side_effect=gate_results),
        "caption_metadata": patch("selekyt.pipeline.caption_metadata",
                                  return_value={"style": "minimal"}),
        "build_content_pack": patch("selekyt.pipeline.build_content_pack",
                                    return_value={"pack": True}),
        "attach_metadata": patch("selekyt.pipeline.attach_metadata",
                                 side_effect=lambda c, *a, **kw: c),
        "aggregate_pacing_report": patch(
            "selekyt.pipeline.aggregate_pacing_report", return_value=_PACING),
    }


@contextlib.contextmanager
def _entered(patches: dict):
    """Enter a dict of patchers and always exit the patchers themselves.

    ``patcher.__enter__()`` returns the mock, and a mock's ``__exit__`` is
    itself a mock that restores nothing -- so collecting the return values and
    exiting those leaks every patch into the next test module. Callers that
    need a mock use the yielded dict; cleanup is on the patchers.
    """
    mocks: dict = {}
    try:
        for key, patcher in patches.items():
            mocks[key] = patcher.__enter__()
        yield mocks
    finally:
        for patcher in patches.values():
            try:
                patcher.__exit__(None, None, None)
            except RuntimeError:
                # Never entered (a failure partway through the loop above):
                # nothing of this patch is left to leak.
                pass


def _run_pipeline(tmp_path, candidates, *, limit=6, gate_results=None,
                  real_pacing=False, source="https://example.com/v",
                  media_info=None, asr=None, **kw):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"0" * 100)
    project = tmp_path / "project"
    fp = "test-fingerprint"
    for sub in ("facebook", "shorts", "captions", "content_pack"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    (project / "pipeline_state.json").write_text(
        json.dumps({"source": source, "media_path": str(media),
                    "media_info": {"title": "t"} if media_info is None else media_info,
                    "fingerprint": fp}),
        encoding="utf-8")
    if not (project / "transcript.json").exists():
        # A second call with the same project is a resume: the transcript on
        # disk is what that run reuses, so it is left alone rather than
        # rewritten without the provenance the first run recorded.
        #
        # The injected transcript is the one the editorial bar actually reads,
        # and the resume tests below run the *real* select stage on it (only
        # ``_fingerprint`` is patched on the second call), so it has to clear
        # the same bar a real transcript does: the 8 s / 20-word floor in
        # ``rank``, and ``context_guard.validate``'s confidence, boilerplate,
        # absolute-claim and standalone-context checks. One word, or twenty
        # unpunctuated tokens over a 10 s span, scores ``context`` at ~0.70 and
        # lands just under the 0.70 standalone threshold, aborting selection
        # before any later stage runs — which turns assertions about memory or
        # the report into vacuous passes. Two sentences with a finite verb,
        # spread over an 18 s span, clears it on the merits.
        _TEXT = (
            "The crew tested the new engine in the morning and recorded "
            "the results. The measurements showed a clear improvement over "
            "the earlier design."
        )
        _SPAN = (2.0, 20.0)
        _tokens = _TEXT.split()
        _step = (_SPAN[1] - _SPAN[0]) / len(_tokens)
        words = [
            {"text": t, "start": _SPAN[0] + i * _step,
             "end": _SPAN[0] + (i + 1) * _step, "confidence": 1.0}
            for i, t in enumerate(_tokens)
        ]
        (project / "transcript.json").write_text(json.dumps({
            "language": "en",
            **({"asr": asr} if asr is not None else {}),
            "segments": [{"start": _SPAN[0], "end": _SPAN[1], "text": _TEXT,
                          "words": words}],
        }), encoding="utf-8")
    patches = _pipeline_patches(tmp_path, candidates, gate_results)
    if real_pacing:
        # The pacing report carries the truncation distinction too, so some
        # tests need the real aggregate_pacing_report rather than the stub.
        patches.pop("aggregate_pacing_report")
    with _entered(patches), patch("selekyt.pipeline._fingerprint", return_value=fp):
        return pipeline_run(source, tmp_path, model="small", limit=limit, **kw)


# ── F9: rejection accounting separates gate rejections from --limit ──

def test_report_separates_gate_rejections_from_limit_truncation(tmp_path):
    """Four candidates, two rejected by the gate, --limit 1.

    Before F9 this reported `rejected_by_context_guard = 3`, counting the one
    clip that passed and was merely cut by --limit — an editor reading the
    report could not tell a judgment from a budget.
    """
    cands = [_candidate(10.0 + i, 20.0 + i) for i in range(4)]
    gate = [(i >= 2, "ok" if i >= 2 else "starts mid-conversation")
            for i, c in enumerate(cands)]
    project = _run_pipeline(tmp_path, cands, limit=1, gate_results=gate,
                            real_pacing=True)
    report = json.loads((project / "report.json").read_text())

    assert report["selected_count"] == 1
    assert report["rejected_by_context_guard"] == 2, \
        "only the two gate rejections may count"
    assert report["truncated_by_limit"] == 1, \
        "the clip that passed but lost to --limit is a separate count"
    assert report["rejection_reasons"] == {"starts mid-conversation": 2}

    pacing = json.loads((project / "pacing_report.json").read_text())
    assert pacing["context_guard_rejections"] == 2
    assert pacing["truncated_by_limit"] == 1


# ── the bench: the count the report prints, backed by the clips it names ──

def _bench_report(tmp_path, n: int, limit: int, asr: dict | None = None):
    cands = [_candidate(10.0 + i, 20.0 + i, f"clip number {i}") for i in range(n)]
    gate = [(True, "ok") for _ in cands]
    return _run_pipeline(tmp_path, cands, limit=limit, gate_results=gate,
                         real_pacing=True, asr=asr)


def test_the_bench_carries_the_clips_the_limit_dropped(tmp_path):
    """Four candidates pass every gate, --limit 1.

    The report said "3 more candidates passed" and named none of them, so the
    count was a claim with nothing behind it: an editor who wanted the third
    clip had to re-run the source to find it. The bench is those clips, and
    its length is the count the report already printed.
    """
    report = json.loads((_bench_report(tmp_path, 4, 1) / "report.json").read_text())

    assert report["selected_count"] == 1
    assert report["truncated_by_limit"] == 3
    assert len(report["bench"]) == report["truncated_by_limit"], \
        "the bench is the count; it must not be a claim the report cannot back"
    assert [c["text"] for c in report["bench"]] == \
        ["clip number 1", "clip number 2", "clip number 3"], \
        "the bench is the clips the limit dropped, in rank order"


def test_bench_entries_are_lean_but_scored_on_the_same_scale(tmp_path):
    """``--limit`` drops are read to decide whether to raise the budget, so a
    bench entry carries the same scores in the same places as a shipped clip
    and none of the word timings it does not need."""
    report = json.loads((_bench_report(tmp_path, 2, 1) / "report.json").read_text())
    bench = report["bench"]

    assert len(bench) == 1
    entry = bench[0]
    assert not (entry.keys() & {"words", "llm_metadata"}), \
        "word timings and LLM metadata are what a re-render needs, not a bench"
    for field in ("start", "end", "text", "story_type", "hook", "payoff",
                  "editorial_score", "virality_score", "context_score",
                  "confidence", "reason"):
        assert field in entry, f"a bench entry must answer {field}"

    # Same weighting as the shipped clips: 0.9/0.8/0.9 editorial/virality/
    # context with 0.8 visual, so 0.27+0.16*0.8 ... computed by Candidate.
    expected = Candidate(
        entry["start"], entry["end"], entry["text"], entry["story_type"],
        entry["hook"], entry["payoff"], entry["editorial_score"],
        entry["virality_score"], entry["context_score"], entry["confidence"],
        entry["reason"], visual_potential=entry["visual_potential"],
    ).total_score
    assert entry["total_score"] == round(expected, 4), \
        "a bench entry must rank on the same scale as the clips above it"


def test_no_bench_when_everything_ships(tmp_path):
    """A run that ships everything it found reports an empty bench rather than
    omitting the key: the absence of "bench" would read as "not computed"."""
    report = json.loads((_bench_report(tmp_path, 1, 6) / "report.json").read_text())
    assert report["truncated_by_limit"] == 0
    assert report["bench"] == []


# ── the report names the model that actually produced the words ──

_ASR = {
    "provider": "local",
    "engine": "whisper.cpp",
    "requested_model": "medium",
    "model": "small",
    "downgrade_reason": "medium is not available locally",
    "word_timestamps": "attention",
    "fallback_reason": None,
}


def test_report_repeats_the_provenance_fields_a_trust_decision_needs(tmp_path):
    """The model name sits in ``transcript.json`` under ``asr``. An editor
    deciding whether the word timing is trustworthy had to open a second file
    to find it; the report now carries the fields that decision turns on, and
    the downgrade is named rather than inferred from a mismatch. ``provider``
    is among them because local and cloud timing differ in kind, not only in
    name — an editor weighing a cloud transcript's word timing is weighing a
    different thing than a local one."""
    report = json.loads((_bench_report(tmp_path, 1, 6, asr=_ASR)
                         / "report.json").read_text())

    assert set(report["transcription"]) == set(_ASR)
    assert report["transcription"]["model"] == "small"
    assert report["transcription"]["requested_model"] == "medium"
    assert report["transcription"]["downgrade_reason"] == \
        "medium is not available locally", \
        "the model that ran is named, and so is the reason it was not the ask"
    assert report["transcription"]["word_timestamps"] == "attention"


def test_provenance_survives_a_resumed_run(tmp_path):
    """A resumed run reuses the transcript on disk and never calls the ASR
    engine. Reading provenance from a local that only the fresh path assigns
    would UnboundLocalError on the second run instead of naming the model."""
    project = _bench_report(tmp_path, 1, 6, asr=_ASR)
    # The state file is what makes the second run a resume: the fingerprint
    # matches, so transcription is skipped and the cached transcript is used.
    report = json.loads((project / "report.json").read_text())
    assert report["transcription"]["model"] == "small"

    rerun = _run_pipeline(tmp_path, [_candidate(10.0, 20.0)], limit=6,
                          real_pacing=True)
    rerun_report = json.loads((rerun / "report.json").read_text())
    assert rerun_report["transcription"]["model"] == "small", \
        "a resumed run still names the model that produced the cached words"


def test_provenance_is_absent_rather_than_fabricated_on_an_old_transcript(tmp_path):
    """A transcript written before the ``asr`` block existed is still usable.
    The report says None — "not recorded" — rather than inventing a model."""
    report = json.loads((_bench_report(tmp_path, 1, 6, asr=None)
                         / "report.json").read_text())
    assert report["transcription"]["model"] is None
    assert report["transcription"]["engine"] is None


def test_no_truncation_when_everything_fits(tmp_path):
    cands = [_candidate(10.0, 20.0)]
    project = _run_pipeline(tmp_path, cands, limit=6)
    report = json.loads((project / "report.json").read_text())
    assert report["rejected_by_context_guard"] == 0
    assert report["truncated_by_limit"] == 0


def test_local_source_title_is_the_filename_not_null(tmp_path):
    """``report.json`` opened with ``"title": null`` for every local file,
    because a local path supplies no metadata title and nothing fell back. The
    title is the first thing shown about the user's own media, and null is an
    absence where the product owed a name."""
    cands = [_candidate(10.0, 20.0)]
    media = tmp_path / "My Interview 2024.mp4"
    media.write_bytes(b"0" * 100)
    project = _run_pipeline(tmp_path, cands, limit=6, source=str(media),
                            media_info={})
    report = json.loads((project / "report.json").read_text())
    assert report["title"] == "My Interview 2024", \
        f"a local file's title must be its stem, got {report['title']!r}"


def test_a_metadata_title_is_preferred_over_the_filename(tmp_path):
    """The fallback must not displace a real title: a download that supplied one
    keeps it, so the two sources of a title stay in the right order.

    The channel matters here. A metadata title comes from the downloader's info
    dict, which a local path cannot supply — so this exercises the resume path,
    where a run that already fetched the source reads its persisted
    ``media_info`` back (line 333) rather than fetching again. That is also the
    path an editor hits on their second run on the same project."""
    cands = [_candidate(10.0, 20.0)]
    project = _run_pipeline(tmp_path, cands, limit=6,
                            source="https://example.com/episode-7",
                            media_info={"title": "Episode 7 — The Valve"})
    report = json.loads((project / "report.json").read_text())
    assert report["title"] == "Episode 7 — The Valve", \
        f"a supplied metadata title must win over the stem, got {report['title']!r}"


def test_corpus_analysis_is_written_and_reports_when_unavailable(tmp_path):
    """The corpus layer (topics, threads, redundancy groups) is a finished
    analysis that nothing in the pipeline consumed, so the product computed
    redundancy groups no editor could see. It now ships as
    ``editorial_corpus.json``; a failure in that optional analysis must report
    to stderr and not destroy a run whose clips are already selected."""
    cands = [_candidate(10.0, 20.0)]
    project = _run_pipeline(tmp_path, cands, limit=6)
    corpus_path = project / "editorial_corpus.json"
    assert corpus_path.exists(), "the corpus artifact must be written"

    corpus = json.loads(corpus_path.read_text())
    assert corpus["build"]["schema"]
    assert "units" in corpus and "topics" in corpus
    assert "redundancy_groups" in corpus and "threads" in corpus
    # Registered in the report next to the other artifact paths, the way every
    # other analysis file is.
    report = json.loads((project / "report.json").read_text())
    assert report["editorial_corpus"] == str(corpus_path)

    # The analysis is optional: a raised exception must be reported, not
    # raised, and the rest of the run must still complete.
    err = io.StringIO()
    second = tmp_path / "second"
    second.mkdir()
    with patch("selekyt.pipeline.build_corpus",
               side_effect=RuntimeError("corpus exploded")):
        with contextlib.redirect_stderr(err):
            project2 = _run_pipeline(second, cands, limit=6)
    assert "corpus exploded" in err.getvalue(), \
        "the failure must be surfaced on stderr rather than swallowed"
    assert (project2 / "report.json").exists(), \
        "a corpus failure must not abort the pipeline"
    assert not (project2 / "editorial_corpus.json").exists()
    # The report must not then claim the artifact it failed to write: a key
    # pointing at a missing file is how a UI shows an editor a broken run.
    report2 = json.loads((project2 / "report.json").read_text())
    assert report2.get("editorial_corpus") is None, \
        "the report must name the corpus only when it actually exists"


def _speech_audio_for(project: Path, seconds: float = 20.0) -> Path:
    """A real voiced WAV where the pipeline expects the extracted audio.

    The coverage gate and the memory capture both read this path, and a
    synthesised tone would not do: the VAD chain is entitled to score a pure
    tone as non-speech, which would leave the run with no speech regions and
    make every memory assertion vacuous. A resumed run does not re-extract it,
    so the fixture supplies it directly.
    """
    audio = project / "transcription" / "audio.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    write_speech_wav(audio, seconds)
    return audio


def _valid_render(out: Path, duration: float, *, vertical: bool) -> Path:
    """A real MP4 container of the right shape, in the test's encoder profile.

    Nothing in these tests depends on the pixels a real render would produce,
    but the run cannot reach the report unless the output QA probes pass, and
    those are checks on the file rather than on the plan. So the stand-in is an
    actual decoded container with the correct orientation, the plan's exact
    duration, and real audio — the profile ``tests/test_render.py`` settled on
    after the slow/crf18 default shipped.
    """
    size = "1080x1920" if vertical else "1920x1080"
    duration = max(1.0, round(duration, 3))
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c=blue:s={size}:rate=30:duration={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
         "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)],
        check=True, capture_output=True,
    )
    return out


def _resume_render(media, cand, out, caption, *, vertical=False, **kw):
    """A stand-in render: same shape as the plan, none of the decoding.

    The plan's edited duration is the sum of the retention intervals the real
    render would concatenate, so the stand-in is built to that length and the
    report's duration check reads the same figure the plan promises.
    """
    edited = sum(b - a for a, b in retention_intervals(cand))
    return _valid_render(Path(out), edited, vertical=vertical)


@contextlib.contextmanager
def _resume():
    """A second call over an existing project, with the analysis stages real.

    Only ``_fingerprint`` (so the cached transcript on disk is the one read)
    and the pixel-producing side of the render are patched. The injected media
    is 100 bytes, so no decoder can read it and a real render is impossible —
    and the pixels are not what these tests assert on. The select stage, the
    coverage gate, the memory capture and the output QA in the report must run
    for real: a resume that mocked any of those would make every memory
    assertion vacuous, which is exactly the failure mode the editorial-bar
    comment in ``_run_pipeline`` guards against. ``_resume_render`` substitutes
    a *real* container of the shape the plan promises, so the report's probes
    still run against something rather than being bypassed.
    """
    with patch("selekyt.pipeline._fingerprint",
               return_value="test-fingerprint"), \
         patch("selekyt.pipeline.render_clip", side_effect=_resume_render):
        yield


def test_video_memory_is_written_from_artifacts_the_run_already_held(tmp_path):
    """The VAD pass for the coverage gate and the corpus analysis were both
    computed and both discarded; the capture must cost no second listen. The
    regions in ``memory.json`` have to be the ones the gate measured, and the
    report must say whether the capture happened rather than leaving a reader
    to guess from an absent file."""
    cands = [_candidate(10.0, 20.0)]
    project = _run_pipeline(tmp_path, cands, limit=6)
    _speech_audio_for(project)

    # A second run over the same project reuses the transcript on disk, so the
    # audio above is the one the coverage gate now reads.
    with _resume():
        pipeline_run("https://example.com/v", tmp_path, model="small", limit=6)

    memory_path = project / "memory.json"
    assert memory_path.is_file(), "the capture must land on disk"

    memory = json.loads(memory_path.read_text())
    assert memory["schema"]
    assert memory["meta"]["duration"] == 20.0
    # The speech regions are the gate's own measurement, not a re-listen: there
    # is a span, and it is inside the source the meta describes.
    audio_items = [i for i in memory["items"] if i["modality"] == "audio"]
    assert any(i["claim"] == "Speech is present" for i in audio_items)
    assert any(i["claim"].startswith("Silence") for i in audio_items), \
        "the complement is derived, so the whole source is covered at level 1"
    for item in audio_items:
        for span in item["intervals"]["intervals"]:
            assert 0.0 <= span["start"] < span["end"] <= 20.0

    # Level 0 is recorded by construction and level 1 spans the source; a level
    # with no evidence is not recorded as examined.
    levels = {r["level"]: r for r in memory["levels"]}
    assert levels[0]["coverage"]["intervals"] == [
        {"start": 0.0, "end": 20.0}]

    report = json.loads((project / "report.json").read_text())
    assert report["memory"]["path"] == str(memory_path)
    assert report["memory"]["summary"]["items"] == len(memory["items"])
    assert report["memory"]["summary"]["errors"] == [], \
        "a clean capture reports no stage failures"


def test_video_memory_failure_reports_and_does_not_abort_the_run(tmp_path):
    """A memory that cannot be built is a missing report, not a missing
    deliverable: the clips are already selected and rendered by this point."""
    cands = [_candidate(10.0, 20.0)]
    project = _run_pipeline(tmp_path, cands, limit=6)
    _speech_audio_for(project)

    err = io.StringIO()
    # The pipeline imports ``engine.memory`` *function-locally* inside the
    # corpus stage, so nothing has imported it yet and ``patch`` cannot resolve
    # a target absent from ``sys.modules``. Importing it here makes the patch
    # target the module object the run will actually use.
    import engine.memory.populate  # noqa: F401 (import for the patch target)
    with patch("engine.memory.populate.build",
               side_effect=RuntimeError("memory exploded")), \
            _resume(), \
            contextlib.redirect_stderr(err):
        pipeline_run("https://example.com/v", tmp_path, model="small", limit=6)
    assert "memory exploded" in err.getvalue(), \
        "the failure must be surfaced rather than swallowed"

    assert not (project / "memory.json").exists(), \
        "a failed capture must not leave a partial record behind"
    report = json.loads((project / "report.json").read_text())
    assert report["memory"]["path"] is None, \
        "the report must not name a file it did not write"
    assert "memory exploded" in report["memory"]["summary"]["error"]


def test_an_unmeasured_source_skips_memory_without_lying(tmp_path):
    """No audio means no duration and no regions. Building a memory anyway
    would record a zero-length source and flag every span in it, which reads
    as an empty source rather than an unmeasured one."""
    cands = [_candidate(10.0, 20.0)]
    project = _run_pipeline(tmp_path, cands, limit=6)
    assert not (project / "transcription" / "audio.wav").exists()

    with _resume():
        pipeline_run("https://example.com/v", tmp_path, model="small", limit=6)

    assert not (project / "memory.json").exists()
    report = json.loads((project / "report.json").read_text())
    assert report["memory"]["path"] is None
    assert "skipped" in report["memory"]["summary"], \
        "the report must say the capture was skipped and why"


# ── F13: cached renders are validated, stubs are re-rendered ──

def _real_video(out: Path, size="640x360", duration=3.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:s={size}:d={duration}",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3.0",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
         "-c:a", "aac", "-b:a", "64k", "-shortest", str(out)],
        check=True, capture_output=True,
    )


def test_valid_cached_render_is_reused(tmp_path):
    """A real, ffprobe-valid pair of renders is not re-encoded."""
    cands = [_candidate(10.0, 20.0)]
    project_dir = tmp_path / "project"
    digest = _selection_digest(cands)
    media = tmp_path / "video.mp4"
    media.write_bytes(b"0" * 100)
    for sub in ("facebook", "shorts", "captions", "content_pack"):
        (project_dir / sub).mkdir(parents=True, exist_ok=True)
    # The cache guard's floor is the delivery canvas itself (1920x1080 /
    # 1080x1920): the burned-in ASS is laid out at that size, so a shorter
    # deliverable carries proportionally smaller text and is re-rendered rather
    # than shipped. A fixture below the floor is rejected as vigorously as a
    # stub, which makes this test indistinguishable from the stub case below.
    _real_video(project_dir / "facebook" / "01.mp4", "1920x1080")
    _real_video(project_dir / "shorts" / "01.mp4", "1080x1920")
    fp = "test-fingerprint"
    # The cached render implies the previous run burned in the ASS it also left
    # on disk and recorded the digest of — without those two the record is from
    # before the caption guard and would not be the render of today's captions.
    ass = ass_content(cands[0])
    (project_dir / "captions" / "01.ass").write_text(ass, encoding="utf-8")
    (project_dir / "pipeline_state.json").write_text(json.dumps({
        "source": "https://example.com/v", "media_path": str(media),
        "media_info": {"title": "t"}, "fingerprint": fp,
        "renders": {"01": {"fingerprint": fp, "selection_digest": digest,
                           "program_digest": _program_digest(cands),
                           "caption_style": "minimal",
                           "ass_digest": _digest(ass),
                           "facebook": str(project_dir / "facebook" / "01.mp4"),
                           "shorts": str(project_dir / "shorts" / "01.mp4")}},
    }), encoding="utf-8")
    (project_dir / "transcript.json").write_text(json.dumps({
        "language": "en", "segments": [{"start": 10.0, "end": 20.0, "text": "h",
                                        "words": []}]}), encoding="utf-8")

    patches = _pipeline_patches(tmp_path, cands)
    with _entered(patches) as mocks, \
         patch("selekyt.pipeline._fingerprint", return_value=fp):
        pipeline_run("https://example.com/v", tmp_path, model="small")
        assert mocks["render_clip"].call_count == 0, \
            "a valid cached render must not be re-encoded"


def test_stub_cached_render_is_re_rendered(tmp_path):
    """A stub or garbage file in the render slot is NOT accepted as finished.

    This is the F13 fix: before it, any non-empty file passed the cache check,
    so a 5-byte leftover from a killed run was shipped as a finished clip.
    """
    cands = [_candidate(10.0, 20.0)]
    project_dir = tmp_path / "project"
    digest = _selection_digest(cands)
    media = tmp_path / "video.mp4"
    media.write_bytes(b"0" * 100)
    for sub in ("facebook", "shorts", "captions", "content_pack"):
        (project_dir / sub).mkdir(parents=True, exist_ok=True)
    # Deliberately not real videos.
    (project_dir / "facebook" / "01.mp4").write_bytes(b"clip")
    (project_dir / "shorts" / "01.mp4").write_bytes(b"clip")
    fp = "test-fingerprint"
    (project_dir / "pipeline_state.json").write_text(json.dumps({
        "source": "https://example.com/v", "media_path": str(media),
        "media_info": {"title": "t"}, "fingerprint": fp,
        "renders": {"01": {"fingerprint": fp, "selection_digest": digest,
                           "caption_style": "minimal",
                           "facebook": str(project_dir / "facebook" / "01.mp4"),
                           "shorts": str(project_dir / "shorts" / "01.mp4")}},
    }), encoding="utf-8")
    (project_dir / "transcript.json").write_text(json.dumps({
        "language": "en", "segments": [{"start": 10.0, "end": 20.0, "text": "h",
                                        "words": []}]}), encoding="utf-8")

    patches = _pipeline_patches(tmp_path, cands)
    with _entered(patches) as mocks, \
         patch("selekyt.pipeline._fingerprint", return_value=fp):
        pipeline_run("https://example.com/v", tmp_path, model="small")
        assert mocks["render_clip"].call_count == 2, \
            "both slots must be re-rendered when the cached files are not real videos"


def test_render_state_is_checkpointed_per_clip(tmp_path):
    """F2: an interrupted run resumes from the last finished clip, not clip 1."""
    cands = [_candidate(10.0 + i * 10, 20.0 + i * 10) for i in range(3)]
    project = _run_pipeline(tmp_path, cands, limit=3)
    state = json.loads((project / "pipeline_state.json").read_text())
    assert set(state["renders"]) == {"01", "02", "03"}, \
        "every finished clip must be checkpointed individually"
    for entry in state["renders"].values():
        assert entry["selection_digest"] == _selection_digest(cands)


# ── F7: bad flags fail before network/ASR work ──

def test_pipeline_rejects_bad_limit_before_download(tmp_path):
    with patch("selekyt.pipeline.download") as dl, \
         patch("selekyt.pipeline.transcribe_auto") as asr:
        with pytest.raises(ValueError, match="limit must be >= 1"):
            pipeline_run("https://example.com/v", tmp_path, limit=0)
        dl.assert_not_called()
        asr.assert_not_called()


def test_pipeline_rejects_bad_chunk_minutes_before_download(tmp_path):
    with patch("selekyt.pipeline.download") as dl, \
         patch("selekyt.pipeline.transcribe_auto") as asr:
        with pytest.raises(ValueError, match="chunk_minutes must be >= 1"):
            pipeline_run("https://example.com/v", tmp_path, chunk_minutes=0)
        dl.assert_not_called()
        asr.assert_not_called()


def test_pipeline_rejects_bad_beam_size_before_download(tmp_path):
    with patch("selekyt.pipeline.download") as dl, \
         patch("selekyt.pipeline.transcribe_auto") as asr:
        with pytest.raises(ValueError, match="beam_size must be >= 1"):
            pipeline_run("https://example.com/v", tmp_path, beam_size=0)
        dl.assert_not_called()
        asr.assert_not_called()


def test_pipeline_rejects_bad_cpu_threads_before_download(tmp_path):
    with patch("selekyt.pipeline.download") as dl, \
         patch("selekyt.pipeline.transcribe_auto") as asr:
        with pytest.raises(ValueError, match="cpu_threads must be >= 1"):
            pipeline_run("https://example.com/v", tmp_path, cpu_threads=0)
        dl.assert_not_called()
        asr.assert_not_called()


# ── F7b: a default install fails before it downloads ──

def test_pipeline_reports_a_missing_transcription_engine_before_download(tmp_path):
    """faster-whisper is an extra, not a base dependency — deliberately, so the
    package installs without an ASR engine and the selector is what says which
    one will run. Before this check the failure surfaced at transcription, i.e.
    after a URL source had been downloaded and remuxed, so a user asking for a
    clip fetched a whole video to be told to run pip.

    ``select_engine`` is patched rather than uninstalled because the host this
    suite runs on has an engine; the pipeline reads the same function the real
    transcription path does, so the patched value is what the run would have
    concluded on its own.
    """
    with patch("selekyt.pipeline.download") as dl, \
         patch("selekyt.pipeline.transcribe_auto") as asr, \
         patch("selekyt.pipeline.select_engine", return_value=None):
        with pytest.raises(RuntimeError) as excinfo:
            pipeline_run("https://example.com/v", tmp_path)
        dl.assert_not_called()
        asr.assert_not_called()
    message = str(excinfo.value)
    # The message has to be the one that names the fix, and both extras have to
    # be in it: a user who cannot install ctranslate2 needs the sherpa path.
    # The install advice is checked for the form that works for a user who
    # installed from PyPI rather than from a checkout: ``pip install -e ...``
    # only resolves from the source tree, so it points a shipped install at a
    # path it does not have.
    assert "pip install 'selekyt[transcription]'" in message
    assert "pip install 'selekyt[sherpa]'" in message
    assert "pip install -e" not in message, (
        "the hint advises an editable install, which only resolves inside a "
        "source checkout; a user who pip-installed SeleKyT has no such tree")


def test_a_run_with_an_engine_available_is_not_blocked_by_that_check(tmp_path):
    """The check must be a probe, not an unconditional gate. With an engine
    reported available the run proceeds past it — otherwise the previous test
    would pass against a check that always raises. The run still fails here,
    but elsewhere and for an unrelated reason (yt-dlp is not on PATH in this
    sandbox), and that is what proves the pre-flight let it through."""
    with patch("selekyt.pipeline.select_engine", return_value="faster-whisper"):
        with pytest.raises(Exception) as excinfo:
            pipeline_run("https://example.com/v", tmp_path)
    assert str(excinfo.value) != _ENGINE_INSTALLED_MESSAGE, \
        "the pre-flight raised the engine error on a host that does have an engine"


_HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# ── credential hygiene in the persisted download state ──

def test_scrub_credentials_removes_every_credential_field():
    """A yt-dlp info dict can carry the request headers it used, and when a
    cookies file was supplied that includes a ``Cookie`` line. The dict is
    persisted into ``pipeline_state.json``, which the web shell serves to any
    unauthenticated localhost caller, so the credential material has to be
    gone before the write, not after the fact."""
    from selekyt.pipeline import _scrub_credentials

    info = {
        "title": "a video",
        "duration": 213.0,
        "http_headers": {"Cookie": "SESSDATA=secret; VISITOR_INFO=also-secret",
                          "User-Agent": "test"},
        "cookies": [{"name": "SESSDATA", "value": "secret"}],
        "_cookies": {"youtube.com": {"SESSDATA": {"value": "secret"}}},
        "formats": [
            {"format_id": "18", "http_headers": {"Cookie": "secret"}},
            {"format_id": "22"},
        ],
        "thumbnails": [{"url": "https://t/1.jpg"}],
    }
    scrubbed = _scrub_credentials(info)

    assert scrubbed["title"] == "a video"
    assert scrubbed["duration"] == 213.0
    assert scrubbed["thumbnails"] == info["thumbnails"]
    for key in ("http_headers", "cookies", "_cookies"):
        assert key not in scrubbed, key
        # Nested occurrences go too: the formats list carries per-request headers.
        assert key not in scrubbed["formats"][0], key
    assert scrubbed["formats"][0]["format_id"] == "18"
    assert scrubbed["formats"][1] == {"format_id": "22"}
    # The caller's dict is not mutated: nothing downstream should see the
    # headers either, and a function that edits in place would be dropping
    # them from the very object yt-dlp handed the pipeline.
    assert "http_headers" in info


def test_scrub_credentials_passes_scalars_and_empty_containers():
    from selekyt.pipeline import _scrub_credentials

    assert _scrub_credentials(None) is None
    assert _scrub_credentials(3) == 3
    assert _scrub_credentials("s") == "s"
    assert _scrub_credentials({}) == {}
    assert _scrub_credentials([]) == []


def test_pipeline_persists_no_credential_fields_from_a_download(tmp_path):
    """End to end: what the download returns is what gets written to disk, so a
    run whose source needed a cookies file must not leave the cookies on disk
    where ``/api/file`` can hand them to any local caller."""
    info = {
        "title": "a video",
        "duration": 213.0,
        "http_headers": {"Cookie": "SESSDATA=secret"},
        "cookies": [{"name": "SESSDATA", "value": "secret"}],
    }
    media = tmp_path / "video.mp4"
    media.write_bytes(b"0" * 100)
    project = tmp_path / "project"
    fp = "test-fingerprint"
    # No media_path here: a cached download would short-circuit ``download()``
    # and the test would exercise nothing. The transcript cache stays, so the
    # run does not also need ASR.
    for sub in ("facebook", "shorts", "captions", "content_pack"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    (project / "pipeline_state.json").write_text(
        json.dumps({"source": "https://example.com/v", "fingerprint": fp}),
        encoding="utf-8")
    (project / "transcript.json").write_text(json.dumps({
        "language": "en",
        "segments": [{"start": 10.0, "end": 20.0, "text": "hello world",
                      "words": [{"text": "hello", "start": 0.0,
                                 "end": 0.5, "confidence": 1.0}]}],
    }), encoding="utf-8")
    with _entered(_pipeline_patches(tmp_path, [_candidate(10.0, 20.0)])) as mocks, \
         patch("selekyt.pipeline._fingerprint", return_value=fp):
        mocks["download"].return_value = (media, info)
        pipeline_run("https://example.com/v", tmp_path, model="small", limit=1)
    mocks["download"].assert_called_once()

    state = json.loads(
        (tmp_path / "project" / "pipeline_state.json").read_text(encoding="utf-8"))
    assert state["media_info"]["title"] == "a video"
    for key in ("http_headers", "cookies"):
        assert key not in state["media_info"], f"{key} persisted to disk"


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_cli_rejects_bad_flags_before_work(monkeypatch, tmp_path):
    """--limit 0 must exit 2 from argparse, before the pipeline is entered."""
    from selekyt import cli

    monkeypatch.setattr(sys, "argv", [
        "selekyt", "https://example.com/v", "--out", str(tmp_path), "--limit", "0",
    ])
    with patch("selekyt.cli.run") as run:
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2
        run.assert_not_called()


def test_cli_exposes_keep_pauses_off(monkeypatch, tmp_path):
    """--no-keep-pauses must reach the pipeline as False.

    F6: the flag was `action='store_true', default=True`, so it could never be
    turned off — the switch was decorative.
    """
    from selekyt import cli

    seen = {}
    monkeypatch.setattr(sys, "argv", [
        "selekyt", "https://example.com/v", "--out", str(tmp_path),
        "--no-keep-pauses", "--no-remove-filler",
    ])
    monkeypatch.setattr(
        "selekyt.cli.run",
        lambda *a, **kw: seen.update(kw=kw) or tmp_path,
    )
    cli.main()
    assert seen["kw"]["keep_pauses"] is False
    assert seen["kw"]["remove_filler"] is False


def test_cli_keep_pauses_default_still_true(monkeypatch, tmp_path):
    """Backwards compatibility: no flag means both stay on."""
    from selekyt import cli

    seen = {}
    monkeypatch.setattr(sys, "argv", ["selekyt", "URL", "--out", str(tmp_path)])
    monkeypatch.setattr(
        "selekyt.cli.run",
        lambda *a, **kw: seen.update(kw=kw) or tmp_path,
    )
    cli.main()
    assert seen["kw"]["keep_pauses"] is True
    assert seen["kw"]["remove_filler"] is True
