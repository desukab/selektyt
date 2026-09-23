"""Tests for the NLE-compatibility audit.

The audit exists because two real defect classes are invisible until someone
opens the export in Premiere/Resolve/FCP: media the timeline points at that an
NLE cannot relink, and record spans that overlap on import. These tests build a
minimal but real project — a genuine media file, genuine EDL/FCPXML, a genuine
render — and confirm the audit passes it, then fails it on exactly those two
defect classes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
ROOT = Path(__file__).resolve().parent.parent


def _write_media(path: Path, seconds: float = 1.0) -> None:
    """A real file so ffprobe reports a true frame rate and duration.

    Video only, and the duration bound by the lavfi source itself rather than
    by an ffmpeg ``-t``: an ``-t`` placed before the inputs is an *input*
    option, and with an infinite source (``anullsrc``) the encode never
    terminates — a real run hung here for six minutes at 111% CPU. The
    audit never inspects audio, so there is no need for a second input.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=black:s=64x64:d={seconds}:r=25",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
        timeout=60,
    )


_EDL = """TITLE:   test cut
FCM: NON-DROP FRAME
001  MOVIE V     C        {src_in} {src_out} 00:00:00:00 00:00:01:00
     M2   movie.mov
* FROM CLIP: {media}
001  MOVIE A     C        {src_in} {src_out} 00:00:00:00 00:00:01:00
"""

_FCPXML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.11">
  <resources>
    <format id="f1" name="FFVideoFormat" frameDuration="1/25s" />
    <asset id="r1" name="movie.mov" start="0s" duration="25/25s" hasVideo="1" videoSources="1" hasAudio="1" audioSources="1" audioChannels="2">
      <media-rep kind="original-media" src="{media_uri}"/>
    </asset>
  </resources>
  <library>
    <event name="test">
      <project name="test">
        <sequence format="f1" tcFormat="NDF">
          <spine>
            <asset-clip ref="r1" offset="0s" start="0s" duration="25/25s" />
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""


def _build_project(root: Path, intervals: list[tuple[float, float]] | None = None) -> Path:
    """A minimal project: source, one EDL, one FCPXML, one render, a selection.

    ``intervals`` writes the ``edit_plan.json`` the interval checks compare
    against. Without it those checks SKIP rather than fail, so a test that
    means to exercise them has to supply one.
    """
    media = root / "source" / "movie.mov"
    media.parent.mkdir(parents=True)
    _write_media(media)
    root.joinpath("timeline").mkdir()
    root.joinpath("timeline", "01.edl").write_text(
        _EDL.format(media=media, src_in="00:00:00:00", src_out="00:00:01:00")
    )
    root.joinpath("timeline", "01.fcpxml").write_text(
        _FCPXML.format(media_uri="file://" + str(media.resolve()))
    )
    # The renders the timeline must describe: one real second of media, in both
    # aspect ratios the pipeline ships.
    for preset in ("facebook", "shorts"):
        root.joinpath(preset).mkdir()
        _write_media(root / preset / "01.mp4")
    root.joinpath("selections.json").write_text(
        '[{"start": 0.0, "end": 1.0, "text": "a", "hook": "a", "payoff": "a"}]'
    )
    if intervals is not None:
        root.joinpath("edit_plan.json").write_text(
            json.dumps([{"index": 1, "source_start": intervals[0][0],
                         "source_end": intervals[-1][1],
                         "intervals": [list(iv) for iv in intervals],
                         "edited_duration": sum(b - a for a, b in intervals),
                         "removed_seconds": 0.0}])
        )
    return root


def _audit(project: Path):
    sys.path.insert(0, str(TOOLS))
    try:
        import importlib
        nle_check = importlib.import_module("nle_check")
        return importlib.reload(nle_check).check_project(project)
    finally:
        sys.path.remove(str(TOOLS))
        sys.modules.pop("nle_check", None)


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"]).returncode != 0,
                    reason="ffmpeg is needed to build the test media")
def test_a_clean_project_passes(tmp_path):
    ok, findings = _audit(_build_project(tmp_path / "project"))
    failed = [f"{name}: {detail}" for name, cok, detail in findings if not cok]
    assert ok, f"the audit failed a clean project: {failed}"


def test_an_offline_media_reference_is_caught(tmp_path):
    """A relative asset src resolves against the timeline file's own directory,
    so the sequence imports with every clip offline. The audit must catch it."""
    project = _build_project(tmp_path / "project")
    broken = (project / "timeline" / "01.fcpxml").read_text()
    (project / "timeline" / "01.fcpxml").write_text(
        broken.replace("file://", "file://relative/")
    )
    ok, findings = _audit(project)
    assert not ok
    assert any("non-absolute src" in d for _n, _o, d in findings)


def test_a_clip_reading_past_its_asset_is_caught(tmp_path):
    """A clip whose extent exceeds the asset's declared duration is clamped or
    flagged by an NLE. The measured case was exactly one frame, so the
    assertion must not be frame-tolerant: one frame over is still over."""
    project = _build_project(tmp_path / "project")
    fcpxml = (project / "timeline" / "01.fcpxml").read_text()
    # The clip reads 0:00:01:00 to 0:00:02:00 but the asset is one second.
    broken = fcpxml.replace('duration="25/25s" />', 'duration="50/25s" />')
    assert broken != fcpxml, "the template changed; pick a new clip duration"
    (project / "timeline" / "01.fcpxml").write_text(broken)
    ok, findings = _audit(project)
    assert not ok
    assert any("reads past the end of its asset" in d for _n, _o, d in findings)


def test_an_edl_media_path_is_resolved_against_the_timeline_not_the_audit(tmp_path, monkeypatch):
    """Regression: the audit checked ``Path(from_clip).exists()`` from *its own*
    working directory. The emitter writes the path from its working directory
    too, so when both were run from the same place — as they always are in this
    repo — a relative path resolved the same lucky way on both sides and the
    check passed an EDL that imports with every clip offline. An NLE resolves a
    relative FROM CLIP against the timeline's own directory, so that is the
    base the audit must use.

    Reproduced from the real corpus project: the EDL read
    ``corpus/media/x.webm`` while sitting in ``project/timeline/``.
    """
    project = _build_project(tmp_path / "project")
    media = project / "source" / "movie.mov"
    # The audit is run from the directory the path *does* resolve from. The
    # pre-fix check passed here precisely because it shared that directory.
    monkeypatch.chdir(project)
    edl = (project / "timeline" / "01.edl").read_text()
    (project / "timeline" / "01.edl").write_text(
        edl.replace(str(media), str(Path("source") / "movie.mov"))
    )
    ok, findings = _audit(project)
    assert not ok, "a FROM CLIP an NLE cannot resolve must fail the audit"
    assert any("edl media exists" in n and not cok for n, cok, _d in findings), (
        [f"{n}: {d}" for n, cok, d in findings]
    )
    assert media.is_file(), "the failure is the path base, not the media being gone"


def test_an_overlapping_record_span_is_caught(tmp_path):
    """Two edits on the *same* channel whose record spans overlap is an import
    conflict; separate channels are separate tracks, so those may coincide."""
    project = _build_project(tmp_path / "project")
    edl = (project / "timeline" / "01.edl").read_text()
    # A second event whose video record starts before event 1's video ends.
    edl += (
        "002  MOVIE V     C        00:00:01:00 00:00:02:00 00:00:00:20 00:00:01:00\n"
        "     M2   movie.mov\n"
        "* FROM CLIP: " + str(project / "source" / "movie.mov") + "\n"
        "002  MOVIE A     C        00:00:01:00 00:00:02:00 00:00:00:20 00:00:01:00\n"
    )
    (project / "timeline" / "01.edl").write_text(edl)
    ok, findings = _audit(project)
    assert not ok
    assert any("before the previous edit ends" in d for _n, _o, d in findings)


# A two-interval cut at 25 fps: 0.0-0.4 s and 0.6-1.0 s of the source, read as
# frames 0-10 and 15-25, concatenated into a 0.8 s record. The kept total is
# genuinely 0.8 s, so the renders are 0.8 s too — a cut that removes a middle
# interval is what the fixture is for, and the durations only line up if the
# renders describe the same cut.
_TWO_INTERVAL_EDL = """TITLE:   test cut
FCM: NON-DROP FRAME
001  MOVIE V     C        00:00:00:00 00:00:00:10 00:00:00:00 00:00:00:10
     M2   movie.mov
* FROM CLIP: {media}
001  MOVIE A     C        00:00:00:00 00:00:00:10 00:00:00:00 00:00:00:10
002  MOVIE V     C        00:00:00:15 00:00:01:00 00:00:00:10 00:00:00:20
     M2   movie.mov
* FROM CLIP: {media}
002  MOVIE A     C        00:00:00:15 00:00:01:00 00:00:00:10 00:00:00:20
"""

_TWO_INTERVAL_FCPXML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.11">
  <resources>
    <format id="f1" name="FFVideoFormat" frameDuration="1/25s" />
    <asset id="r1" name="movie.mov" start="0s" duration="25/25s" hasVideo="1" videoSources="1" hasAudio="1" audioSources="1" audioChannels="2">
      <media-rep kind="original-media" src="{media_uri}"/>
    </asset>
  </resources>
  <library>
    <event name="test">
      <project name="test">
        <sequence format="f1" tcFormat="NDF">
          <spine>
            <asset-clip ref="r1" offset="0s" start="0s" duration="10/25s" />
            <asset-clip ref="r1" offset="10/25s" start="15/25s" duration="10/25s" />
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""


def _drop_edl_interval(text: str) -> str:
    """Delete the second event, so one event describes a cut the plan records
    as two. The record span shrinks too, which is a second defect the audit
    reports independently."""
    events = [ln for ln in text.splitlines() if ln.startswith("002")]
    out = text
    for ln in events:
        out = out.replace(ln + "\n", "")
    return out


def _drop_fcpxml_interval(text: str) -> str:
    """Delete the second asset-clip from the spine."""
    return text.replace(
        '            <asset-clip ref="r1" offset="10/25s" start="15/25s" '
        'duration="10/25s" />\n',
        "",
    )


_DROP_EDL_INTERVAL = _drop_edl_interval
_DROP_FCPXML_INTERVAL = _drop_fcpxml_interval


def _two_interval_project(root: Path) -> Path:
    """A cut that keeps two intervals out of one second of source."""
    project = _build_project(root, intervals=[(0.0, 0.4), (0.6, 1.0)])
    media = project / "source" / "movie.mov"
    # The renders the timeline must describe: the 0.8 s the cut actually kept,
    # in both aspect ratios. Built after _build_project overwrote them with a
    # one-second file.
    for preset in ("facebook", "shorts"):
        _write_media(project / preset / "01.mp4", seconds=0.8)
    (project / "timeline" / "01.edl").write_text(
        _TWO_INTERVAL_EDL.format(media=media)
    )
    (project / "timeline" / "01.fcpxml").write_text(
        _TWO_INTERVAL_FCPXML.format(media_uri="file://" + str(media.resolve()))
    )
    return project


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"]).returncode != 0,
                    reason="ffmpeg is needed to build the test media")
def test_a_two_interval_cut_passes_interval_by_interval(tmp_path):
    """Both interchange formats must describe every retained interval, in
    order, not merely the clip's overall extent. A clean two-interval cut is
    the fixture this check was written against, so it has to pass first."""
    ok, findings = _audit(_two_interval_project(tmp_path / "project"))
    failed = [f"{n}: {d}" for n, cok, d in findings if not cok]
    assert ok, f"the audit failed a clean two-interval cut: {failed}"
    interval_findings = [d for n, _o, d in findings
                         if "source intervals == edit plan" in n]
    assert len(interval_findings) == 2, "one interval check per format"
    for detail in interval_findings:
        assert "2 interval(s)" in detail, detail


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"]).returncode != 0,
                    reason="ffmpeg is needed to build the test media")
def test_a_dropped_middle_interval_is_caught(tmp_path):
    """The failure this check exists for. A cut of two intervals exported as
    one — the middle interval silently lost — keeps the same total duration,
    so the render comparison passes and only an interval-level comparison
    notices. On the real corpus every multi-interval clip has its extra
    interval in the middle, which is where a totals check is blind."""
    for ext, drop in (("edl", _DROP_EDL_INTERVAL), ("fcpxml", _DROP_FCPXML_INTERVAL)):
        project = _two_interval_project(tmp_path / f"project-{ext}")
        text = (project / "timeline" / f"01.{ext}").read_text()
        broken = drop(text)
        assert broken != text, "the fixture no longer contains the interval"
        (project / "timeline" / f"01.{ext}").write_text(broken)
        ok, findings = _audit(project)
        assert not ok, f"{ext}: one interval describing a two-interval cut passed"
        named = [f"{n}: {d}" for n, cok, d in findings
                 if "source intervals == edit plan" in n and not cok]
        assert named, f"{ext}: the interval check did not fire"
        assert "describe a cut the edit plan records as 2 interval(s)" in named[0]


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"]).returncode != 0,
                    reason="ffmpeg is needed to build the test media")
def test_a_hole_in_the_spine_is_caught(tmp_path):
    """The render concatenates the kept intervals, so a gap between two
    neighbouring asset-clips is a cut the export does not describe. 'Clips in
    non-decreasing start order' admits a gap, and the duration check sees only
    the total — which a gap and an equal-length overlap cancel out of."""
    project = _two_interval_project(tmp_path / "project")
    text = (project / "timeline" / "01.fcpxml").read_text()
    # The second clip starts a frame late, opening a one-frame hole.
    broken = text.replace('offset="10/25s"', 'offset="11/25s"')
    assert broken != text, "the fixture has no second clip to shift"
    (project / "timeline" / "01.fcpxml").write_text(broken)
    ok, findings = _audit(project)
    assert not ok
    assert any("leaves a" in d and "hole" in d for _n, _o, d in findings), (
        [f"{n}: {d}" for n, _o, d in findings])


# --- a source the pipeline correctly declined ------------------------------


def _outcome(project: Path, **fields) -> Path:
    """Write the record corpus_run.py leaves in a finished project tree.

    Four committed corpus runs ship a transcript and no timeline: the source
    had no speech, or nothing passed the gate. Their outcome used to live only
    in ``corpus/results.jsonl`` — a sibling of the tree — so pointed at the
    project alone the audit could print "no timeline/" and nothing else, which
    reads as a broken export rather than a declined source.
    """
    rec = {"id": "some-source", "status": "NO_SPEECH_DETECTED", "exit_code": 1,
           "no_speech_cause": None, "clips": 0, "segments": 0,
           "elapsed_s": 12.3, "finished_utc": "2026-09-22T00:00:00Z"}
    rec.update(fields)
    path = project / "run_outcome.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def test_a_declined_source_names_its_cause_not_just_the_missing_dir(tmp_path):
    """A run that found no speech is a correct negative result, not a broken
    export. The finding has to say so, because that is how a reader tells the
    four committed failures from an interrupted run."""
    project = tmp_path / "project"
    project.mkdir()
    _outcome(project, status="NO_SPEECH_DETECTED",
             no_speech_cause="silent_track")
    ok, findings = _audit(project)
    assert not ok, "there is no timeline, so the check must still fail"
    [(_name, _cok, detail)] = findings
    assert "NO_SPEECH_DETECTED" in detail
    assert "exit 1" in detail
    assert "silent_track" in detail
    assert "did not export anything to check" in detail


def test_a_run_that_succeeded_but_never_exported_is_a_real_defect(tmp_path):
    """The other half of the distinction. An OK outcome with no timeline means
    the export stage is missing — a bug in the pipeline, not a property of the
    source — and the finding must not soften that into 'declined'."""
    project = tmp_path / "project"
    project.mkdir()
    _outcome(project, status="OK", exit_code=0)
    ok, findings = _audit(project)
    assert not ok
    [(_name, _cok, detail)] = findings
    assert "though the run records OK" in detail
    assert "real defect" in detail
    assert "did not export anything" not in detail


def test_an_unclassifiable_run_says_so_rather_than_guessing(tmp_path):
    """No outcome record and no timeline: the run cannot be classified, and the
    finding must not imply a cause it does not have."""
    project = tmp_path / "project"
    project.mkdir()
    ok, findings = _audit(project)
    assert not ok
    [(_name, _cok, detail)] = findings
    assert "cannot be classified" in detail
    assert "NO_SPEECH" not in detail


def test_a_corrupt_outcome_is_handled_not_crashed(tmp_path):
    """A truncated run_outcome.json is a plausible on-disk state for an
    interrupted write; the audit degrades to 'cannot be classified' rather
    than raising on a reader who has no other copy of the record."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "run_outcome.json").write_text('{"status": "OK', encoding="utf-8")
    ok, findings = _audit(project)
    assert not ok
    assert any("cannot be classified" in d for _n, _o, d in findings)


def test_every_committed_corpus_project_is_self_describing():
    """A checkout must explain its own failures. Before run_outcome.json this
    required corpus/results.jsonl, which a tarball of one project does not
    include; now the reason travels with the artifacts."""
    roots = sorted((ROOT / "corpus").glob("*_run/project"))
    assert roots, "no corpus projects found; the fixture glob is wrong"
    missing = [str(p.parent.name) for p in roots
               if not (p / "run_outcome.json").is_file()]
    assert not missing, f"projects without run_outcome.json: {missing}"
    for p in roots:
        rec = json.loads((p / "run_outcome.json").read_text(encoding="utf-8"))
        assert rec["status"] in {
            "OK", "NO_SPEECH_DETECTED", "NO_STORIES_PASSED", "PIPELINE_FAILED",
            "FETCH_FAILED", "TIMEOUT", "HARNESS_ERROR",
        }, f"{p.parent.name}: unrecognised status {rec['status']!r}"
        # The status and the artifacts have to agree, or one of them is stale.
        has_timeline = (p / "timeline").is_dir()
        assert has_timeline == (rec["status"] == "OK"), (
            f"{p.parent.name}: status {rec['status']} but "
            f"{'a' if has_timeline else 'no'} timeline/")


# --- the audit is actually scheduled ----------------------------------------


def _require_independent_validator():
    """The independent EDL parse is the point of the audit — SelekyT's own
    emitter wrote the file and ``pycmx`` reads it — so in the tool that check
    FAILS rather than skipping when pycmx is absent. A checkout that did not
    install the ``nle`` extra skips here rather than reporting a green audit
    the validator never ran. CI installs the extra."""
    import importlib.util

    if importlib.util.find_spec("pycmx") is None:
        pytest.skip("pycmx is not installed; pip install -e '.[nle]'")
    if subprocess.run(["which", "ffprobe"],
                      capture_output=True).returncode != 0:
        pytest.skip("ffprobe is needed to measure the committed renders")


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"]).returncode != 0,
                    reason="ffmpeg is needed to build the test media")
def test_every_committed_corpus_project_passes_the_independent_audit():
    """The audit existed, its dependency was declared and installed in CI, and
    nothing ran it — so a change that broke the agreement between the render
    and the interchange formats was caught only when a human audited a project
    directory by hand. This schedules it, over the committed corpus rather than
    over a hand-built fixture.

    That matters because the fixtures above are written to be correct by
    construction: they exercise the audit's logic, but they do not exercise the
    *emitter*. These timelines are what ``engine.nle_export`` actually wrote on
    real sources, against the renders the pipeline actually produced, so an
    emitter that drifted into output its own reader would accept fails here.

    What this still cannot do is the relink half. ``corpus_run.py`` deletes the
    source media unless ``--keep-media`` is passed, so a finding that the media
    is absent is a property of the audit's evidence, not of the export, and it
    is accepted here as NOT RUN rather than as a failure. Worth knowing about
    the committed trees: the four runs whose media was discarded still reference
    it by a path that no longer exists anywhere, and the three whose media is
    retained reference absolute paths that resolve only on the machine that
    generated them — so the relink check passes here and reports NOT RUN
    everywhere else, CI included. Both are accepted; what is not accepted is a
    finding that ran and failed.

    The distinction is not academic, and the telugu run is how it was learned.
    Its media was retained long after the run, and the moment it appeared on
    disk this check turned from NOT RUN into a hard failure: the EDL it had
    shipped carried ``FROM CLIP: corpus/media/telugu-tutorial.webm``, a path
    relative to the pipeline's working directory, which an NLE opening the EDL
    from its own timeline directory resolves to a file that is not there. Every
    clip would have imported offline on a machine that had the media right
    there. The exporter had already been fixed (``engine/nle_export.py``
    ``_from_clip_path`` makes the path absolute and refuses to ship one it
    cannot resolve) — the committed artifact simply predated the fix, and no
    audit could see it while the media it needed was missing. Re-emitting the
    timeline with ``tools/regenerate_timelines.py`` changed exactly one line,
    the path base, and the finding now passes. That is the shape of defect this
    test catches by running at all, and the reason a NOT RUN is reported rather
    than silently counted clean.
    """
    _require_independent_validator()
    roots = sorted((ROOT / "corpus").glob("*_run/project"))
    audited = [p for p in roots if (p / "timeline").is_dir()]
    assert audited, "no corpus project with a timeline; the glob is wrong"
    # A project that declined its source has no timeline by design (see the
    # self-describing tests above), so it is not a missing audit target.

    failures = []
    deferred = 0
    passed = 0
    for project in audited:
        ok, findings = _audit(project)
        if not ok:
            failures.append(f"{project.parent.name}: a hard check failed")
        for name, cok, detail in findings:
            # A NOT RUN or SKIP finding can carry cok=True — the audit's own
            # emitter does this for the media checks, so counting them by the
            # ok flag alone would read a corpus where nothing could run as one
            # where everything passed. The detail prefix is the verdict.
            if detail.startswith(("SKIP", "NOT RUN")):
                deferred += 1
                continue
            if cok:
                passed += 1
            else:
                failures.append(f"{project.parent.name}: {name}: {detail}")

    assert not failures, "\n".join(failures)
    assert passed > 0, \
        "no finding actually ran; the audit accepted the corpus without checking it"
    print(f"nle_check: {len(audited)} projects, {passed} findings passed, "
          f"{deferred} deferred (media not retained, or a preset not rendered)")
