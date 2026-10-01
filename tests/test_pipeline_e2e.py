"""End-to-end pipeline orchestration on synthetic media.

Patches only the two network boundaries (download + transcribe) and runs the
real run() body: transcript cache, thoughts/rank/context-gate, captions,
content packs, real ffmpeg renders, QA report, and the resume/cache branches.
This is the coverage backbone for selekyt/pipeline.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from models.core import Segment, Word

_REPO = Path(__file__).resolve().parent.parent
_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not available")


def _synthetic_video(out: Path, frames: int = 320, size: int = 320, fps: int = 10) -> Path:
    """16:9 mp4 with a silent audio track: the QA gate requires landscape
    video plus an audio stream on every output."""
    import cv2
    import numpy as np
    import subprocess

    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.parent / f"{out.stem}_silent.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(raw), fourcc, fps, (size, size))
    try:
        for i in range(frames):
            frame = np.zeros((size, size, 3), dtype=np.uint8)
            cv2.putText(
                frame, f"frame {i}", (20, size // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2,
            )
            writer.write(frame)
    finally:
        writer.release()
    # pad to 16:9 and add a continuous tone so the silent-tail QA check
    # passes (a fully silent track reads as one 16s "silent tail")
    tone = out.parent / f"{out.stem}_tone.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=36", str(tone),
        ],
        check=True, capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(raw), "-i", str(tone),
            "-filter:v", "pad=iw*16/9:ih:(ow-iw)/2:0:black",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-shortest", "-movflags", "+faststart",
            str(out),
        ],
        check=True, capture_output=True,
    )
    return out


def _transcript() -> list[Segment]:
    """Two 8s thoughts with sentence boundaries, separated by a section marker.

    make_thoughts needs >= 6s spans ending in terminal punctuation, and the
    context guard rejects clips that start with a connective or anaphora, so
    the first thought starts with a subject and each sentence ends cleanly.

    Only the first story lives here: the marker and the second story have
    their own constructors and their own reasons for being shaped as they
    are.
    """

    def w(text: str, start: float, end: float) -> Word:
        return Word(text, start, end)

    seg1 = Segment(
        0.0, 8.0,
        "We shipped the feature on Tuesday morning. The metric moved "
        "forty percent overnight. The team celebrated the launch.",
        [
            w("We", 0.0, 0.2), w("shipped", 0.2, 0.5), w("the", 0.5, 0.7),
            w("feature", 0.7, 1.1), w("on", 1.1, 1.3), w("Tuesday", 1.3, 1.7),
            w("morning", 1.7, 2.1), w("The", 2.1, 2.3), w("metric", 2.3, 2.7),
            w("moved", 2.7, 3.1), w("forty", 3.1, 3.5), w("percent", 3.5, 3.9),
            w("overnight", 3.9, 4.4), w("The", 4.4, 4.6), w("team", 4.6, 5.0),
            w("celebrated", 5.0, 5.5), w("the", 5.5, 5.7), w("launch", 5.7, 8.0),
        ],
    )
    seg2 = Segment(
        8.0, 16.0,
        "The rollout completed ahead of schedule. Customers reported "
        "faster load times across the board. This outcome validated "
        "the quarter of hard work.",
        [
            w("The", 8.0, 8.2), w("rollout", 8.2, 8.6), w("completed", 8.6, 9.1),
            w("ahead", 9.1, 9.4), w("of", 9.4, 9.6), w("schedule", 9.6, 10.1),
            w("Customers", 10.1, 10.6), w("reported", 10.6, 11.1),
            w("faster", 11.1, 11.5), w("load", 11.5, 11.8), w("times", 11.8, 12.1),
            w("across", 12.1, 12.5), w("the", 12.5, 12.7), w("board", 12.7, 13.1),
            w("This", 13.1, 13.4), w("outcome", 13.4, 13.9),
            w("validated", 13.9, 14.4), w("the", 14.4, 14.6),
            w("quarter", 14.6, 15.0), w("of", 15.0, 15.2), w("hard", 15.2, 15.5),
            w("work", 15.5, 16.0),
        ],
    )
    return [seg1, seg2]


def _separator() -> Segment:
    """A section marker between the two stories.

    The run ships at most --limit clips, so a fixture with only one story in
    it cannot leave anything on the bench: every truncation assertion then
    holds vacuously at zero. Two stories need a separator, and it has to be
    one the engine actually recognises — ``segment_story_units`` only breaks
    a span at a boilerplate sentence, so a plain sentence here would be
    absorbed into a single candidate spanning both stories, which is the
    opposite of what the fixture is for. It also has to be wide: ``rank``
    keeps two clips only when they are more than four seconds apart, so a
    two-second marker still collapses the pair into one.
    """

    def w(text: str, start: float, end: float) -> Word:
        return Word(text, start, end)

    return Segment(
        16.0, 20.0,
        "Episode 12. Recorded in March 2024.",
        [
            w("Episode", 16.0, 16.9), w("12.", 16.9, 17.4),
            w("Recorded", 17.4, 18.4), w("in", 18.4, 18.7),
            w("March", 18.7, 19.3), w("2024.", 19.3, 20.0),
        ],
    )


def _second_story() -> Segment:
    """An 8s story on an unrelated subject, 4s after the first.

    See ``_separator`` for why the gap is what it is. Its sentences share a
    content word and its last opens with a discourse attachment, because the
    coherence score reads three sentences with no lexical continuity and no
    attachment as two spliced stories — which is correct behaviour, and
    would keep this story off the bench for a reason the fixture is not
    trying to exercise.
    """

    def w(text: str, start: float, end: float) -> Word:
        return Word(text, start, end)

    return Segment(
        20.0, 28.0,
        "Hiring doubled the engineering team that year. Two interviewers "
        "joined every hiring panel. So the offer acceptance rate climbed "
        "to ninety percent.",
        [
            w("Hiring", 20.0, 20.4), w("doubled", 20.4, 20.9),
            w("the", 20.9, 21.1), w("engineering", 21.1, 21.8),
            w("team", 21.8, 22.2), w("that", 22.2, 22.4), w("year", 22.4, 22.8),
            w("Two", 22.8, 23.0), w("interviewers", 23.0, 23.7),
            w("joined", 23.7, 24.2), w("every", 24.2, 24.6),
            w("hiring", 24.6, 25.1), w("panel", 25.1, 25.6),
            w("So", 25.6, 25.8), w("the", 25.8, 26.0),
            w("offer", 26.0, 26.3), w("acceptance", 26.3, 26.7),
            w("rate", 26.7, 27.0), w("climbed", 27.0, 27.3),
            w("to", 27.3, 27.5), w("ninety", 27.5, 27.7),
            w("percent", 27.7, 28.0),
        ],
    )


@pytest.fixture
def media(tmp_path: Path) -> Path:
    return _synthetic_video(tmp_path / "source" / "clip.mp4")


def _ui_contract_against(project: Path) -> None:
    """Run the desktop UI's artifact contract against a tree the engine wrote.

    ``node tests/ui_contract.mjs`` with no argument runs only the
    project-independent checks — which is how CI calls it — so every
    artifact-shape assertion there is unexercised unless something passes a
    real project in. This does, and fails on the report the run produced
    rather than on a fixture.
    """
    node = shutil.which("node")
    if node is None:
        return
    proc = subprocess.run(
        [node, str(_REPO / "tests" / "ui_contract.mjs"), str(project)],
        capture_output=True, text=True, cwd=_REPO,
    )
    assert proc.returncode == 0, (
        "the UI contract failed against a tree this run produced:\n"
        + proc.stdout + proc.stderr)
    assert "PASS" in proc.stdout


@pytest.fixture
def patched_pipeline(monkeypatch, media):
    import selekyt.pipeline as p

    monkeypatch.setattr(
        p, "download", lambda source, dest, cookies=None: (media, {"title": "synthetic clip"}),
    )
    monkeypatch.setattr(
        p, "transcribe_auto",
        lambda media, **kw: (
            [*_transcript(), _separator(), _second_story()],
            "en",
            # The full six-field provenance, including a downgrade: the report
            # repeats these and the UI warns on a downgrade, so a fixture that
            # only carried an engine name exercised neither.
            {"engine": "test", "requested_model": "large", "model": "small",
             "downgrade_reason": "large is unavailable in this environment",
             "word_timestamps": "test", "fallback_reason": ""},
        ),
    )
    return p


def test_run_produces_the_full_project_tree(patched_pipeline, tmp_path, media):
    project = patched_pipeline.run(
        "https://example.com/v", tmp_path, model="small", limit=2,
    )
    assert project == tmp_path / "project"
    for name in (
        "pipeline_state.json", "transcript.json", "selections.json",
        "editorial_report.json", "editorial_corpus.json",
        "pacing_report.json", "edit_plan.json",
        "content_pack.json", "report.json",
    ):
        assert (project / name).is_file(), f"missing {name}"
    for sub in ("captions", "facebook", "shorts", "content_pack"):
        assert (project / sub).is_dir(), f"missing {sub}/"

    report = json.loads((project / "report.json").read_text())
    assert report["source"] == "https://example.com/v"
    assert report["title"] == "synthetic clip"
    assert report["language"] == "en"
    assert report["selected_count"] >= 1
    assert report["audio"] == {"enabled": False}, "audio stage is explicitly off"
    assert "fabricated" in report["policy"]
    # The corpus layer used to be reachable only by importing engine.corpus
    # directly; the report must now name it, and the path it names must exist.
    corpus = json.loads((project / "editorial_corpus.json").read_text())
    assert corpus["build"]["schema"]
    assert report["editorial_corpus"] == str(project / "editorial_corpus.json")

    # captions for every selected clip, both formats
    packs = json.loads((project / "content_pack.json").read_text())
    assert len(packs) == report["selected_count"]
    for pack in packs:
        assert pack["source_based"] is True
        assert pack["titles"], "every clip ships title variants"
    idx = "01"
    assert (project / "captions" / f"{idx}.ass").is_file()
    assert (project / "captions" / f"{idx}.srt").is_file()
    assert (project / "facebook" / f"{idx}.mp4").stat().st_size > 0
    assert (project / "shorts" / f"{idx}.mp4").stat().st_size > 0

    # The bench and the model the summary strip renders as chips. Both are read
    # straight from report.json, so an editor who raises --limit pulls from the
    # entries the report names rather than re-running the source.
    assert report["truncated_by_limit"] == len(report["bench"]), \
        "the bench is the count the report prints; it must not be a bare number"
    for entry in report["bench"]:
        assert not (entry.keys() & {"words", "llm_metadata"}), \
            "the bench is a ranking view, not a re-render"
        assert isinstance(entry["total_score"], float)
    assert report["transcription"]["model"] == "small"
    assert report["transcription"]["requested_model"] == "large"
    assert report["transcription"]["downgrade_reason"] == \
        "large is unavailable in this environment", \
        "the model that ran is named, and so is why it was not the ask"
    assert report["transcription"] == {
        k: v for k, v in
        json.loads((project / "transcript.json").read_text())["asr"].items()
        if k in report["transcription"]}

    # The deliverable has to state what made it: an editor handed this tree
    # cannot reproduce it without the build and the parameters. These fields
    # have no other home in the report, and engine/report.py shipped for
    # several commits claiming to produce them while nothing called it.
    state = json.loads((project / "pipeline_state.json").read_text())
    prov = report["provenance"]
    assert prov["source_path"] == str(media)
    assert prov["source_fingerprint"] == state["fingerprint"]
    assert prov["started_at"] and prov["finished_at"]
    assert prov["duration_seconds"] > 0.0
    assert prov["platform"] and prov["python"]
    # The report names the build that made the tree. In an exported tarball with
    # no .git the field is honestly empty; in a checkout it has to be the SHA.
    expected_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=_REPO, capture_output=True, text=True,
    ).stdout.strip()
    assert prov["git_sha"] == expected_sha, \
        f"report git_sha {prov['git_sha']!r} != {expected_sha!r}"
    assert prov["cache_status"] == "transcript-fresh"
    assert report["config"]["model"] == "small"
    assert report["config"]["limit"] == 2
    assert report["config"]["audio_analysis"] is False
    assert report["config"]["cookies"] is None, "a credentials path is never echoed"
    # The model and the language stay where the report already had them: a
    # second copy under provenance is how the two drift apart.
    assert prov["whisper_model"] == "" and prov["whisper_language"] == ""

    # The UI's contract with this tree: the formatters, the SRT grammar, the
    # selections/edit-plan arithmetic and the summary fields, all checked
    # against artifacts this run actually wrote.
    _ui_contract_against(project)


def test_a_clip_on_the_bench_can_be_read_without_rerunning(patched_pipeline, tmp_path, media):
    """--limit 1 against a source with two distinct stories.

    The report used to print "1 more candidate passed" and name none of them,
    so an editor who wanted that clip had to re-run the source to find it. The
    bench is the clip itself, and it is the one the shipped selection is
    ranked above — the same fields, on the same scale, without the word
    timings a re-render would need.
    """
    project = patched_pipeline.run(
        "https://example.com/v", tmp_path, model="small", limit=1,
    )
    report = json.loads((project / "report.json").read_text())
    selections = json.loads((project / "selections.json").read_text())

    assert report["selected_count"] == 1
    assert report["truncated_by_limit"] == 1
    assert len(report["bench"]) == 1
    shipped, benched = selections[0], report["bench"][0]
    assert benched["start"] != shipped["start"], \
        "the bench holds a different clip, not a copy of the shipped one"
    for key in ("start", "end", "text", "story_type", "hook", "payoff",
                "editorial_score", "virality_score", "context_score",
                "confidence", "reason", "visual_potential", "total_score"):
        assert key in benched, f"the bench entry is missing {key}"
    assert not (benched.keys() & {"words", "llm_metadata"}), \
        "word timings and LLM metadata belong to a re-render, not a ranking view"
    # Ranked on the same scale as the clip above it, so an editor comparing the
    # two is comparing like with like.
    assert benched["total_score"] < shipped["total_score"], \
        "the bench is the clip the shipped selection outranked"

    # And the UI contract on a tree whose bench is non-empty: the chip renders
    # "+1 on the bench", so the contract's per-entry shape checks run against
    # a real entry rather than its empty-bench skip.
    _ui_contract_against(project)


def test_audio_stage_reports_provenance_when_enabled(patched_pipeline, tmp_path, media):
    project = patched_pipeline.run(
        "https://example.com/v", tmp_path, model="small", limit=1,
        audio_analysis=True, vad_backend="energy",
    )
    report = json.loads((project / "report.json").read_text())
    assert report["audio"]["vad_backend"] == "energy"
    assert report["audio"]["vad_model"] == "energy-rms"
    assert report["audio"]["frame_ms"] == 32
    assert (project / "audio").is_dir()


def test_resume_reuses_download_transcript_and_renders(patched_pipeline, tmp_path, media, capsys):
    args = ("https://example.com/v", tmp_path, "small", None, 2)
    patched_pipeline.run(*args)
    capsys.readouterr()  # discard first-run output
    project = patched_pipeline.run(*args)
    second = capsys.readouterr().out

    # all three caches hit on the second run
    assert "reusing downloaded source" in second, "download cache must hit on resume"
    assert "using unchanged transcript" in second, "transcript cache must hit on resume"
    assert "reusing rendered clip" in second, "render cache must hit on resume"
    assert (project / "report.json").is_file()


def test_local_source_path_is_used_directly(patched_pipeline, tmp_path, media):
    project = patched_pipeline.run(str(media), tmp_path, model="small", limit=1)
    state = json.loads((project / "pipeline_state.json").read_text())
    assert "media_path" not in state, "local files are not cached as downloads"
    assert (project / "report.json").is_file()


def test_missing_local_source_raises(monkeypatch, tmp_path):
    import selekyt.pipeline as p
    with pytest.raises(RuntimeError, match="Local video not found"):
        p.run(str(tmp_path / "nope.mp4"), tmp_path, model="small")


def test_transcript_cache_is_invalidated_on_model_change(patched_pipeline, tmp_path, media, capsys):
    patched_pipeline.run("https://example.com/v", tmp_path, model="small", limit=1)
    capsys.readouterr()
    patched_pipeline.run("https://example.com/v", tmp_path, model="medium", limit=1)
    out = capsys.readouterr().out
    assert "[transcribe]" in out, "a model change must bust the transcript cache"


def _independent_nle_audit_available() -> None:
    """``tools/nle_check.py`` parses the EDL SelekyT emitted with ``pycmx``, a
    reader this repo did not write — that independence is the whole point, so
    in the tool a missing reader is a FAIL rather than a SKIP. A checkout
    without the ``nle`` extra skips here instead of reporting a verified export
    the validator never saw. CI installs the extra."""
    import importlib.util

    if importlib.util.find_spec("pycmx") is None:
        pytest.skip("pycmx is not installed; pip install -e '.[nle]'")


def _audit_for_nle(project: Path):
    """Run the NLE-compatibility audit over a tree this pipeline just wrote."""
    import importlib
    import sys

    tools = _REPO / "tools"
    sys.path.insert(0, str(tools))
    try:
        nle_check = importlib.import_module("nle_check")
        return importlib.reload(nle_check).check_project(project)
    finally:
        sys.path.remove(str(tools))
        sys.modules.pop("nle_check", None)


def test_the_independent_nle_audit_passes_a_tree_the_pipeline_just_wrote(
        patched_pipeline, tmp_path, media, monkeypatch):
    """``tools/nle_check.py`` caught the classes of bug that only appear when
    someone opens the export in an NLE, and nothing scheduled it: pycmx was a
    declared dependency and installed in CI, but no code path or CI step ran
    the tool, so a change that broke the render/interchange agreement was only
    caught by a human auditing a directory by hand.

    The corpus-wide version of this test in ``tests/test_nle_check.py`` pins
    the audit against committed artifacts. This one pins the *emitter*: the
    EDL and FCPXML here did not exist until ``nle_export`` wrote them from the
    same retention intervals the render just concatenated, and the renders did
    not exist until ``render_clip`` cut them, so an emitter that drifts out of
    agreement with its own render fails here rather than in a user's NLE.

    What this cannot verify is what no test can: that an NLE actually opens the
    result. The audit checks the properties an NLE would complain about, not
    the import itself.
    """
    _independent_nle_audit_available()
    # The shared fixture's download stub returns the media where the fixture
    # built it; the real one writes into its out_dir and returns that path. The
    # audit resolves the source from project/source, so mirror the real
    # contract here or the audit reads "could not probe the source" about a run
    # whose media it could have found.
    def download(source, out_dir, cookies=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / media.name
        shutil.copy(media, dest)
        return dest, {"title": "synthetic clip"}

    patched_pipeline.download = download
    project = patched_pipeline.run(
        "https://example.com/v", tmp_path, model="small", limit=2,
    )

    ok, findings = _audit_for_nle(project)
    failures = [f"{name}: {detail}" for name, cok, detail in findings
                if not cok and not detail.startswith(("SKIP", "NOT RUN"))]
    assert not failures, "the audit failed a tree this pipeline just wrote:\n" \
        + "\n".join(failures)
    assert ok, "the audit reported checks it could not run on a tree whose " \
        "source is present"

    # The independent parse is the finding that an emitter's own grammar check
    # cannot substitute for, so it has to have actually run here.
    parsed = [d for n, _o, d in findings if n.endswith("edl independent parse")]
    assert parsed, "no EDL was parsed by an independent reader"
    assert "pycmx" not in parsed[0], parsed[0]


def _editorial_audit(project: Path):
    """``tools/editorial_audit.py`` over a tree this pipeline just wrote."""
    import importlib
    import sys

    tools = _REPO / "tools"
    sys.path.insert(0, str(tools))
    try:
        return importlib.import_module("editorial_audit").audit(project)
    finally:
        sys.path.remove(str(tools))
        sys.modules.pop("editorial_audit", None)


def test_the_editorial_audit_passes_a_tree_the_pipeline_just_wrote(
        patched_pipeline, tmp_path, media):
    """``tools/editorial_audit.py`` scores the shipped clips, and like the NLE
    audit above it was only ever run by hand or by
    ``tools/corpus_run.py``. Scheduling it here pins the *editorial engine*
    rather than committed artifacts: the selections and their retention
    intervals in this tree did not exist until the selection stage scored and
    chose them, so a change to scoring, interval construction or rendering that
    ships a clip ending mid-sentence, shaving a word or carrying a caption the
    transcript does not contain fails here.

    What this cannot verify is what no test can: that a clip is worth watching.
    The audit measures falsifiable properties of the render, not editorial
    judgement, and its silence-share bar deliberately fails a window the
    measurement says is not dead air, because that still needs a person to
    listen.
    """
    project = patched_pipeline.run(
        "https://example.com/v", tmp_path, model="small", limit=2,
    )
    ok, report = _editorial_audit(project)

    findings = report.get("findings") or []
    failures = [f"{name}: {detail}" for name, cok, detail in findings if not cok]
    assert findings, "the audit produced no findings for a tree with two selected clips"
    assert not failures, (
        "the editorial audit failed clips this pipeline just selected and "
        "rendered:\n" + "\n".join(failures))
    assert ok, "the audit reported a hard failure but no failing finding"

    # The clip checks are the ones that pin the engine; confirm they ran rather
    # than the audit accepting the tree on a technicality.
    clips = [name for name, _o, _d in findings if name.startswith("clip")]
    assert len(clips) == 2, f"expected the two selected clips to be audited, got {clips}"


def test_regenerating_captions_is_byte_identical_to_a_fresh_run(
        patched_pipeline, tmp_path):
    """``tools/regenerate_captions.py`` re-earns caption evidence without a
    re-transcode, and its byte-identity claim is against *this*.

    The committed corpus captions are a snapshot three days older than the
    caption emitter (79 of 81 files differ on re-emit; pinned in
    ``tests/test_corpus_reproducibility.py``), so the corpus cannot test this
    claim — only a tree the pipeline just wrote can. Regeneration must be
    byte-exact against it, which also pins ``srt_for_candidate`` and
    ``ass_for_candidate`` as pure functions of the candidate: a render or a
    transcript read here would be a latent nondeterminism in a deliverable.

    Mutation-verified: wiring ``time.time()`` into the emitted cue text fails
    here at ``0 of 2 caption track(s) changed``. A mutation to the cue tail's
    0.08 s floor passes, because both sides of this comparison share the same
    code — this test pins agreement between two runs, not agreement with the
    committed corpus, which is what the corpus freshness test is for.
    """
    project = patched_pipeline.run(
        "https://example.com/v", tmp_path, model="small", limit=2,
    )
    captions = project / "captions"
    before = {f.name: f.read_bytes() for f in sorted(captions.iterdir())}
    assert before, "the run wrote no caption files"

    proc = subprocess.run(
        [str(_REPO / ".venv" / "bin" / "python"),
         str(_REPO / "tools" / "regenerate_captions.py"), str(project)],
        capture_output=True, text=True)
    assert proc.returncode == 0, f"regeneration failed: {proc.stderr}"
    assert "0 of 2 caption track(s) changed" in proc.stdout, (
        f"regeneration changed captions a fresh run just wrote:\n{proc.stdout}")

    after = {f.name: f.read_bytes() for f in sorted(captions.iterdir())}
    assert before == after, (
        "regeneration is not byte-identical to a fresh run: "
        + ", ".join(sorted(set(before) | set(after))))


def test_the_progress_events_name_every_stage_and_real_counts(
    patched_pipeline, tmp_path, media,
):
    """The UI's event channel, run against the real pipeline.

    The renderer is presentation-only, so what has to be tested here is the
    contract it reads: every stage the panel can label is started and finished
    in order, each ``done`` carries a duration, and the render stage's counts
    are the counts the tree actually contains. A stage that emitted no event is
    a stage the panel silently omits, which is how a missing stage would ship
    as a working one.
    """
    events: list[dict] = []
    project = patched_pipeline.run(
        "https://example.com/v", tmp_path, model="small", limit=1,
        progress=events.append,
    )
    by_type = {}
    for evt in events:
        by_type.setdefault(evt["type"], []).append(evt)

    started = [e["stage"] for e in by_type["stage"] if e["state"] == "start"]
    finished = [e["stage"] for e in by_type["stage"] if e["state"] == "done"]
    # A stage that is not started is not finished either, so the two orders
    # agree and none is dropped: ``audio`` is absent here because the run did
    # not ask for it, and it must stay absent rather than appear empty.
    assert started == finished == ["input", "transcribe", "verify", "select",
                                   "corpus", "assemble", "render", "report"]

    details = {e["stage"]: e["detail"]
               for e in by_type["stage"] if e["state"] == "done"}
    for stage, detail in details.items():
        assert isinstance(detail, dict), f"{stage} carried no detail dict"
        assert detail, f"{stage} carried an empty detail; show nothing instead"
    # A detail that names a number the tree does not hold is a fabricated one.
    assert details["render"]["clips"] == len(
        list((project / "facebook").glob("*.mp4")))
    assert details["report"]["artifacts"] > 0


def test_audio_stage_appears_only_when_it_ran(patched_pipeline, tmp_path, media):
    events: list[dict] = []
    patched_pipeline.run("https://example.com/v", tmp_path, model="small", limit=1,
                         audio_analysis=True, vad_backend="energy",
                         progress=events.append)
    stages = [e["stage"] for e in events
              if e["type"] == "stage" and e["state"] == "start"]
    assert stages == ["input", "transcribe", "verify", "select", "corpus",
                      "audio", "assemble", "render", "report"]


def test_the_canonical_edit_is_written_and_valid(patched_pipeline, tmp_path,
                                                media, capfd):
    """The assemble stage must land an edit, not merely not crash.

    The stage catches its own exceptions and reports them, which is the right
    behaviour for a stage whose failure costs a report rather than a
    deliverable — but it means a run whose assembly raised looks identical to a
    successful one from the stage list alone. This is the assertion that closes
    that gap: the edit is on disk, it describes every clip the reel placed, its
    decisions do not dangle, and its interchange validates on both readers.
    """
    events: list[dict] = []
    project = patched_pipeline.run("https://example.com/v", tmp_path,
                                   model="small", limit=2,
                                   progress=events.append)
    assert project == tmp_path / "project"
    assert "canonical edit unavailable" not in capfd.readouterr().err, \
        "the assemble stage raised and reported instead of writing an edit"

    edit = json.loads((project / "edit.json").read_text(encoding="utf-8"))
    # Decisions, not just a header: every placement the reel made is a decision
    # the graph holds, and a decision anchoring on no clip is a move that moved
    # the clip without moving its reasoning.
    assert edit["decisions"], "the graph recorded no decisions at all"
    assert edit["placements"], "the reel placed no clips"
    assert len(edit["placements"]) == len(edit["decisions"])
    assert edit["dangling"] == 0

    # The program-level interchange is the artifact an NLE opens to see the edit
    # as assembled. It is validated inside the stage against the real timeline —
    # a stronger check than a reconstruction from this JSON, which records no
    # timeline positions — and the result is carried in the stage detail.
    edl = project / "timeline" / "program.edl"
    fcpxml = project / "timeline" / "program.fcpxml"
    assert edl.is_file() and fcpxml.is_file()

    detail = next(e["detail"] for e in events
                  if e["type"] == "stage" and e["stage"] == "assemble"
                  and e["state"] == "done")
    assert detail.get("edit") != "unavailable"
    interchange = detail["interchange"]
    assert interchange["problems"] == [], \
        f"the program interchange failed validation: {interchange['problems']}"
    assert interchange["edl_independently_parsed"], \
        "pycmx is not installed, so the independent EDL parse is vacuous"
    assert Path(interchange["edl"]) == edl
    assert Path(interchange["fcpxml"]) == fcpxml


def test_a_failed_stage_reports_itself_before_raising(
    patched_pipeline, tmp_path, media, monkeypatch,
):
    """The fail event is what lets the CLI attribute a failure to a stage.

    The verify stage refuses a language the fitness table marks unusable, which
    is the cheapest refusal the pipeline can raise; the attribution is what the
    failure screen names, so a stage that raised without emitting would be
    reported as a nameless "Run" failure.
    """
    import selekyt.pipeline as p

    monkeypatch.setattr(p, "language_is_refusable", lambda fit: True)
    events: list[dict] = []
    with pytest.raises(RuntimeError, match="not usable on this language"):
        p.run("https://example.com/v", tmp_path, model="small", limit=1,
              progress=events.append)
    fails = [e for e in events if e["type"] == "fail"]
    assert len(fails) == 1
    assert fails[0]["stage"] == "verify"
    assert "not usable on this language" in fails[0]["reason"]
