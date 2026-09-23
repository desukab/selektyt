"""Report system tests."""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from engine import report as report_module
from engine.report import (
    ReportBuilder, write_report_json, terminal_summary, write_terminal_summary,
)

_REPO = Path(report_module.__file__).resolve().parent.parent


def test_report_all_sections():
    b = ReportBuilder()
    b.set_provenance(source_path="a.mp4", whisper_model="small")
    b.set_config({"silence_threshold": 0.5})
    b.set_analysis(speech_ratio=0.6)
    b.set_candidates(count=5)
    b.set_editing(edited_duration=120.0)
    b.set_output(shorts="/tmp/x")
    b.set_qa(status="pass")
    d = b.to_dict()
    assert d["provenance"]["source_path"] == "a.mp4"
    assert d["config"]["silence_threshold"] == 0.5
    assert d["analysis"]["speech_ratio"] == 0.6
    assert d["candidates"]["count"] == 5
    assert d["editing"]["edited_duration"] == 120.0
    assert d["output"]["shorts"] == "/tmp/x"
    assert d["qa"]["status"] == "pass"


def test_report_provenance_autofill():
    b = ReportBuilder()
    d = b.to_dict()
    p = d["provenance"]
    assert p["started_at"]
    assert p["platform"]
    assert p["python"]
    assert p["software_version"]
    assert isinstance(p["duration_seconds"], float) or p["duration_seconds"] == 0.0


def test_finalize_records_duration():
    b = ReportBuilder(started_at=time.time() - 5)
    b.finalize(finished_at=time.time())
    d = b.to_dict()
    assert abs(d["provenance"]["duration_seconds"] - 5.0) < 2.0


def test_write_report_json(tmp_path):
    b = ReportBuilder()
    b.set_provenance(source_path="a.mp4")
    b.set_editing(edited_duration=10)
    out = write_report_json(b, tmp_path / "report.json")
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["provenance"]["source_path"] == "a.mp4"
    assert data["editing"]["edited_duration"] == 10


def test_write_report_creates_parent(tmp_path):
    b = ReportBuilder()
    out = write_report_json(b, tmp_path / "a" / "b" / "report.json")
    assert out.exists()


def test_terminal_summary_contains_key_fields():
    b = ReportBuilder()
    b.set_provenance(source_path="x.mp4", whisper_model="tiny")
    b.set_candidates(count=3)
    b.set_editing(edited_duration=50.0, compression_ratio=0.5)
    s = terminal_summary(b)
    assert "x.mp4" in s
    assert "tiny" in s
    assert "compression 0.5" in s


def test_write_terminal_summary(tmp_path):
    b = ReportBuilder()
    b.set_provenance(source_path="x.mp4")
    out = write_terminal_summary(b, tmp_path / "summary.txt")
    assert out.exists()
    assert "x.mp4" in out.read_text()


def test_unknown_setter_ignored():
    b = ReportBuilder()
    b.set_provenance(not_a_real_field=1)
    d = b.to_dict()
    assert "not_a_real_field" not in d["provenance"]


# ── the build identity follows the code, not the launch directory ─────────────


def _repo_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(_REPO), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def test_git_sha_is_resolved_from_the_package_not_the_cwd(tmp_path, monkeypatch):
    """``git_sha`` names the build that produced the tree, so it has to follow
    the code that ran. The pipeline is a CLI and can be launched from anywhere;
    resolving it with plain ``git rev-parse`` made the field silently empty for
    anyone not standing inside the repo, and an empty SHA reads to whoever finds
    the report as "this came from an unreleased build".

    Regression-pinned: this test fails at the equality below when ``_git_sha``
    runs ``git rev-parse`` without ``-C``, because the cwd here is deliberately
    outside the repo.
    """
    monkeypatch.chdir(tmp_path)
    assert _repo_sha(), "the checkout the tests run in has no SHA to compare with"
    assert report_module.ReportBuilder._git_sha() == _repo_sha()


def test_git_sha_is_empty_when_no_checkout_is_reachable(tmp_path, monkeypatch):
    """A tarball export has no ``.git`` anywhere above the package, and there the
    honest value is an empty string.

    The package is relocated under ``tmp_path`` and given a ``.git`` directory
    with no commits, which is what an unpacked-but-not-initialised tree looks
    like to the walk: present enough to stop it, empty enough to answer nothing.
    """
    for ancestor in [tmp_path, *tmp_path.parents]:
        if (ancestor / ".git").exists():
            pytest.skip(
                f"the tmp tree sits inside a checkout ({ancestor}); the empty "
                "case cannot be isolated there")
    package = tmp_path / "checkout" / "engine"
    package.mkdir(parents=True)
    (package.parent / ".git").mkdir()
    monkeypatch.setattr(report_module, "__file__", str(package / "report.py"))
    monkeypatch.chdir(tmp_path)
    assert report_module.ReportBuilder._git_sha() == ""


def test_git_sha_ignores_a_checkout_the_package_was_vendored_into(
        tmp_path, monkeypatch):
    """A checkout above the package that does not track the running file is not
    this code's history.

    This is the pip-install-into-someone-else's-repo shape: the package is
    dropped into a project it has nothing to do with, and that project has a
    perfectly good HEAD. Reporting that SHA would attribute the deliverable to a
    build that never produced it, which is a harder error to catch later than an
    empty field, because it looks like a real answer.

    Mutation-verified: dropping the ``ls-files --error-unmatch`` guard fails here
    with the unrelated checkout's SHA where the assertion expects an empty
    string.
    """
    unrelated = tmp_path / "unrelated"
    package = unrelated / "engine"
    package.mkdir(parents=True)
    # A real checkout with a real commit, tracking nothing of ours.
    env = {k: v for k, v in os.environ.items()
           if k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_")) is False}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    for args in (
        ["git", "init", "-q", str(unrelated)],
        ["git", "-C", str(unrelated), "commit", "-q", "--allow-empty", "-m", "x"],
    ):
        proc = subprocess.run(args, capture_output=True, text=True, env=env)
        assert proc.returncode == 0, proc.stderr or proc.stdout
    unrelated_sha = subprocess.run(
        ["git", "-C", str(unrelated), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, encoding="utf-8").stdout.strip()
    assert unrelated_sha, "the fixture checkout has no commit to misattribute"

    monkeypatch.setattr(report_module, "__file__", str(package / "report.py"))
    monkeypatch.chdir(tmp_path)
    assert report_module.ReportBuilder._git_sha() == "", (
        f"the report attributed a vendored copy to an unrelated checkout "
        f"({unrelated_sha}) that does not track it")
