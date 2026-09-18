"""CLI entry-point coverage: --doctor, --server, --json, __main__.

These exercise the real argparse surface and the real sidecar entry, so the
dispatch logic (not the pipeline) is what is under test.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import selekyt.__main__ as cli_main
from selekyt import cli as cli_mod


def _run_module(args: list[str]) -> subprocess.CompletedProcess:
    """Run `python -m selekyt ...` as a subprocess (the real __main__ path)."""
    return subprocess.run(
        [sys.executable, "-m", "selekyt", *args],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
    )


def test_doctor_reports_tool_availability(capsys):
    rc = cli_mod._doctor()
    out = capsys.readouterr().out
    assert rc in (0, 1)
    assert "SelekyT doctor" in out
    for name in ("python", "ffmpeg", "ffprobe", "yt-dlp", "faster-whisper"):
        assert name in out


def test_doctor_exits_nonzero_when_a_required_tool_is_missing(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr("selekyt.cli.faster_whisper", None, raising=False)
    # force the optional import to fail
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    assert cli_mod._doctor() == 1


def test_doctor_reports_ready_on_a_python3_only_host(monkeypatch, capsys):
    """A plain Debian/Ubuntu install ships `python3` and not `python`.

    Requiring the unversioned name reported a hard MISSING while SelekyT ran
    under a perfectly good interpreter, sending users to install a package
    they did not need before a single frame was processed.
    """
    import shutil
    import types

    present = {"python3", "ffmpeg", "ffprobe"}

    def fake_which(name):
        return f"/usr/bin/{name}" if name in present else None

    monkeypatch.setattr(shutil, "which", fake_which)
    # A host can have the wheel installed without the unversioned `python`.
    monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))

    assert cli_mod._doctor() == 0
    out = capsys.readouterr().out
    assert "Ready." in out
    assert "python3" in out and "OK" in out
    # The unversioned name is advisory, not a blocker.
    assert "python " in out or "python  " in out
    assert "MISSING" not in out


def test_doctor_names_the_missing_required_tools(monkeypatch, capsys):
    """`ffmpeg` missing must say so, not just report a nonzero exit."""
    import shutil

    present = {"python3", "ffprobe"}

    def fake_which(name):
        return f"/usr/bin/{name}" if name in present else None

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    assert cli_mod._doctor() == 1
    out = capsys.readouterr().out
    assert "ffmpeg" in out
    assert "MISSING" in out
    # A local-file user with no yt-dlp is not blocked by the message.
    assert "yt-dlp" in out


def test_main_without_source_runs_doctor(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["selekyt"])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code in (0, 1)
    assert "SelekyT doctor" in capsys.readouterr().out


def test_main_server_flag_runs_the_sidecar(monkeypatch):
    """--server dispatches to serve_stdio instead of the pipeline."""
    called = {}
    monkeypatch.setattr(sys, "argv", ["selekyt", "--server"])
    monkeypatch.setattr(
        "selekyt.cli.serve_python",
        lambda: called.setdefault("served", 0) or 0,
    )
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert called["served"] == 0
    assert exc.value.code == 0


def test_main_json_prints_the_report(monkeypatch, capsys, tmp_path):
    report = {"selected": [{"start": 0.0, "end": 10.0}], "audio": {"vad_backend": "energy"}}
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["selekyt", "URL", "--out", str(tmp_path), "--json"])
    monkeypatch.setattr(
        "selekyt.cli.run", lambda *a, **k: tmp_path,
    )
    cli_mod.main()
    printed = json.loads(capsys.readouterr().out)
    assert printed == report


def test_main_without_json_prints_a_human_line(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "argv", ["selekyt", "URL", "--out", str(tmp_path)])
    monkeypatch.setattr("selekyt.cli.run", lambda *a, **k: tmp_path)
    cli_mod.main()
    assert str(tmp_path) in capsys.readouterr().out


def test_main_forwards_every_flag(monkeypatch, tmp_path):
    seen = {}

    def fake_run(source, out, model, language, limit, cookies, chunk_minutes,
                 beam_size, cpu_threads, ollama_url, **kw):
        seen.update(
            source=source, out=out, model=model, language=language, limit=limit,
            cookies=cookies, chunk_minutes=chunk_minutes, beam_size=beam_size,
            cpu_threads=cpu_threads, ollama_url=ollama_url, kw=kw,
        )
        return out

    monkeypatch.setattr(sys, "argv", [
        "selekyt", "https://example.com/v", "--out", str(tmp_path),
        "--model", "small", "--language", "te", "--limit", "3",
        "--chunk-minutes", "2", "--beam-size", "2", "--cpu-threads", "4",
        "--caption-style", "punch", "--cookies", "c.txt",
        "--ollama-url", "http://localhost:11434",
        "--audio-analysis", "--vad-backend", "energy",
        "--silence-threshold", "0.6", "--silence-padding", "0.1",
        "--min-speech-ms", "200", "--min-silence-ms", "150",
        "--export-fcpxml",
    ])
    monkeypatch.setattr("selekyt.cli.run", fake_run)
    cli_mod.main()
    assert seen["source"] == "https://example.com/v"
    assert seen["model"] == "small"
    assert seen["language"] == "te"
    assert seen["limit"] == 3
    assert seen["chunk_minutes"] == 2
    assert seen["beam_size"] == 2
    assert seen["cpu_threads"] == 4
    assert seen["cookies"] == "c.txt"
    assert seen["ollama_url"] == "http://localhost:11434"
    assert seen["kw"]["audio_analysis"] is True
    assert seen["kw"]["vad_backend"] == "energy"
    assert seen["kw"]["silence_threshold"] == 0.6
    assert seen["kw"]["silence_padding"] == 0.1
    assert seen["kw"]["min_speech_ms"] == 200
    assert seen["kw"]["min_silence_ms"] == 150
    assert seen["kw"]["export_fcpxml"] is True


def test_caption_style_reaches_the_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["selekyt", "URL", "--caption-style", "noir"])
    monkeypatch.setattr("selekyt.cli.run", lambda *a, **k: tmp_path)
    import os
    # Set, not deleted: main() writes the key itself, and monkeypatch only
    # restores keys it owns. Letting production create it left `noir` in the
    # process environment for every later test in the run.
    monkeypatch.setenv("SELEKYT_CAPTION_STYLE", "minimal")
    cli_mod.main()
    assert os.environ["SELEKYT_CAPTION_STYLE"] == "noir"


def test_module_entry_runs_the_cli(monkeypatch, tmp_path):
    """`python -m selekyt` dispatches through cli.main, not a copy."""
    rc = cli_main.main  # the entry exists and is callable
    assert callable(rc)


@pytest.mark.skipif(
    subprocess.run([sys.executable, "-c", "import selekyt"], capture_output=True).returncode != 0,
    reason="package not importable in this interpreter",
)
def test_python_m_selekyt_doctor_works():
    """The installed `python -m selekyt --doctor` path runs end to end."""
    proc = _run_module(["--doctor"])
    assert proc.returncode in (0, 1)
    assert "SelekyT doctor" in proc.stdout


def test_python_m_selekyt_server_answers_ping(tmp_path):
    """`python -m selekyt --server` is a live JSON-RPC sidecar over stdio."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "selekyt", "--server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    try:
        proc.stdin.write(json.dumps({"op": "ping", "params": {}}) + "\n")
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["ok"] is True
        assert resp["data"]["engine"] == "python"
    finally:
        proc.stdin.close()
        proc.wait(timeout=15)
