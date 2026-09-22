"""Retention: reclaiming the intermediates a finished run no longer needs.

A project's cost is almost all intermediates — the downloaded source and the
extracted audio plus ASR checkpoints — while the deliverables are a few MB.
Nothing in the pipeline ever deleted either, so ``out/`` grew without bound and
a run at volume died in ``OSError`` from ``write_json_atomic`` after the
checkpoint had already landed. These tests pin the control that closes that:
what it removes, what it must not touch, and the completeness gate that keeps
it from breaking resume.
"""
from pathlib import Path

from selekyt.pipeline import cleanable, reclaim_intermediates, STATE_NAME


def _finished_project(tmp_path: Path, *, n_clips: int = 3) -> Path:
    """A project whose renders are all on disk: the state records one render per
    selected clip, and each conventional MP4 exists. ``reclaim_intermediates``
    judges completeness on that pair, so a fixture with renders but no files is
    an *incomplete* run and has to be built separately."""
    project = tmp_path / "project"
    (project / "transcription" / "chunks").mkdir(parents=True)
    (project / "source").mkdir(parents=True)
    renders = {}
    for i in range(1, n_clips + 1):
        idx = f"{i:02d}"
        for kind in ("facebook", "shorts"):
            d = project / kind
            d.mkdir(exist_ok=True)
            (d / f"{idx}.mp4").write_bytes(b"fakes but real files")
        renders[idx] = {
            "facebook": str(project / "facebook" / f"{idx}.mp4"),
            "shorts": str(project / "shorts" / f"{idx}.mp4"),
        }
    # The bytes a real run holds here: the extracted 16 k mono audio and the
    # per-chunk checkpoints. A zero-byte transcription would reclaim nothing,
    # and every assertion below about "the bytes are gone" would be vacuous.
    (project / "transcription" / "audio.wav").write_bytes(b"\x00" * 2_000_000)
    (project / "transcription" / "chunks" / "chunk_0001.json").write_bytes(b"{}" * 40_000)
    (project / "source" / "source.mov").write_bytes(b"\x00" * 3_000_000)
    # transcript.json is a deliverable of a sort: a re-run reuses it and skips
    # transcription entirely, so removing it would cost a re-transcribe.
    (project / "transcript.json").write_text('{"segments": []}', encoding="utf-8")
    (project / "report.json").write_text('{"selected_count": 3}', encoding="utf-8")
    (project / STATE_NAME).write_text(
        '{"source": "https://example/a", "media_path": "%s", '
        '"renders": %s, "selected_count": %d}'
        % ((project / "source" / "source.mov").as_posix(), _json(renders), n_clips),
        encoding="utf-8",
    )
    return project


def _json(obj) -> str:
    import json

    return json.dumps(obj)


def test_a_finished_project_reclaims_its_intermediates(tmp_path):
    project = _finished_project(tmp_path)


    def tree_bytes():
        return sum(f.stat().st_size for f in project.rglob("*") if f.is_file())

    before = tree_bytes()
    # Measured separately from the tree, because the cleanup also rewrites
    # pipeline_state.json (clearing the media path it just invalidated), and
    # letting that few hundred bytes into the ledger makes the equality below
    # about bookkeeping rather than about what was deleted.
    expected = sum(
        f.stat().st_size
        for root in ("source", "transcription")
        for f in (project / root).rglob("*")
        if f.is_file()
    )

    result = reclaim_intermediates(project)

    assert result["ok"] is True
    names = {row["dir"] for row in result["removed"]}
    assert names == {"source", "transcription"}, names
    assert result["bytes"] == expected
    # The claim that matters: the directories are gone, not merely reported.
    assert not (project / "transcription").exists()
    assert not (project / "source").exists()
    # And this is what the whole finding was about, as a fraction of the project.
    after = tree_bytes()
    assert after < before * 0.1, "most of a finished project is intermediates"


def test_the_deliverables_survive_the_cleanup(tmp_path):
    """Reclaiming intermediates is only safe if everything a user would call an
    output is still there afterwards. A cleanup that reclaimed the renders would
    be deleting the product."""
    project = _finished_project(tmp_path)
    kept = [
        project / "facebook" / "01.mp4",
        project / "shorts" / "01.mp4",
        project / "transcript.json",
        project / "report.json",
    ]
    reclaim_intermediates(project)
    for path in kept:
        assert path.is_file(), f"{path.name} was reclaimed with the intermediates"


def test_keep_source_preserves_the_download_a_rerun_needs(tmp_path):
    """Deleting ``source/`` is where the bytes are, but it is also what a
    re-render and ``tools/nle_check.py`` need, so it is opt-out rather than
    unconditional."""
    project = _finished_project(tmp_path)
    media = project / "source" / "source.mov"

    result = reclaim_intermediates(project, keep_source=True)

    assert result["ok"] is True
    assert media.is_file(), "--keep-source must not remove the downloaded media"
    assert not result.get("re_download_on_rerun")

    # The same project without the flag does remove it, and says so.
    result = reclaim_intermediates(project, keep_source=False)
    assert [row["dir"] for row in result["removed"]] == ["source"]
    assert not media.exists()
    assert result.get("re_download_on_rerun") is True
    import json

    state = json.loads((project / STATE_NAME).read_text(encoding="utf-8"))
    # A stale media_path would make a resumed run trust a path that no longer
    # exists instead of re-downloading, so it is cleared rather than left.
    assert state["media_path"] is None


def test_an_incomplete_run_is_refused_and_keeps_its_intermediates(tmp_path):
    """The completeness gate is the whole reason the control is opt-in and
    post-hoc: deleting the extracted audio mid-run would break resume, and
    deleting the source would break the renders still to come."""
    project = _finished_project(tmp_path, n_clips=3)
    (project / "shorts" / "02.mp4").unlink()

    result = reclaim_intermediates(project)

    assert result["ok"] is False
    assert result["bytes"] == 0 and result["removed"] == []
    assert "02.mp4" in result["reason"], "the reason must name what is missing"
    assert (project / "transcription").exists(), "a refusal must not delete anything"
    assert (project / "source").exists()


def test_a_run_with_unrendered_selections_is_refused(tmp_path):
    """A project that rendered fewer clips than it selected is mid-flight, and
    the state's own ``selected_count`` is what says so."""
    project = _finished_project(tmp_path, n_clips=2)
    import json

    state = json.loads((project / STATE_NAME).read_text(encoding="utf-8"))
    state["selected_count"] = 6
    (project / STATE_NAME).write_text(json.dumps(state), encoding="utf-8")

    ok, why = cleanable(project)
    assert ok is False
    assert "2 of 6" in why, why


def test_a_directory_that_is_not_a_project_is_refused(tmp_path):
    ok, why = cleanable(tmp_path / "nowhere")
    assert ok is False
    assert STATE_NAME in why


def test_the_cleanup_is_idempotent(tmp_path):
    """A user who runs it twice should get a clean no-op, not an error: the
    second call finds nothing to reclaim and says so."""
    project = _finished_project(tmp_path)
    first = reclaim_intermediates(project)
    second = reclaim_intermediates(project)
    assert first["ok"] and second["ok"]
    assert first["bytes"] > 0
    assert second["removed"] == [] and second["bytes"] == 0
