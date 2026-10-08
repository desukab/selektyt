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
    for name in ("python", "ffmpeg", "ffprobe", "yt-dlp", "onnxruntime", "faster-whisper"):
        assert name in out



def test_doctor_checks_groq_instead_of_local_whisper(monkeypatch, capsys):
    import types
    monkeypatch.setattr(cli_mod, "default_transcription_provider", lambda: "groq")
    monkeypatch.setitem(sys.modules, "groq", types.ModuleType("groq"))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    assert cli_mod._doctor() == 0
    out = capsys.readouterr().out
    assert "transcription     groq" in out
    assert "groq" in out
    assert "faster-whisper" not in out


def test_doctor_names_the_groq_extra_when_sdk_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(cli_mod, "default_transcription_provider", lambda: "groq")
    monkeypatch.setitem(sys.modules, "groq", None)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    assert cli_mod._doctor() == 1
    out = capsys.readouterr().out
    assert "groq             MISSING" in out
    assert "pip install 'selekyt[cloud-transcription]'" in out


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


def test_the_analyze_verb_is_an_optional_prefix(monkeypatch, tmp_path):
    """``selekyt analyze video.mp4`` is the documented invocation. The verb is
    dropped before argparse sees it, and the bare-path form keeps working — one
    verb, two spellings, no second code path."""
    seen = {}

    def record(source, *rest, **kw):
        seen["source"] = source
        return tmp_path

    monkeypatch.setattr(sys, "argv",
                        ["selekyt", "analyze", "video.mp4", "--out", str(tmp_path)])
    monkeypatch.setattr("selekyt.cli.run", record)
    cli_mod.main()
    assert seen["source"] == "video.mp4"

    monkeypatch.setattr(sys, "argv", ["selekyt", "video.mp4", "--out", str(tmp_path)])
    cli_mod.main()
    assert seen["source"] == "video.mp4"


def test_the_analyze_verb_alone_runs_doctor(monkeypatch, capsys):
    """A verb with no source is the same no-source case as a bare ``selekyt``,
    not a missing-file error."""
    monkeypatch.setattr(sys, "argv", ["selekyt", "analyze"])
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
        "--transcription", "groq",
        "--chunk-minutes", "2", "--beam-size", "2", "--cpu-threads", "4",
        "--caption-style", "punch", "--cookies", "c.txt",
        "--ollama-url", "http://localhost:11434",
        "--audio-analysis", "--vad-backend", "energy",
        "--silence-threshold", "0.6", "--silence-padding", "0.1",
        "--min-speech-ms", "200", "--min-silence-ms", "150",
        "--export-fcpxml",
    ])
    monkeypatch.setattr("selekyt.cli.run", fake_run)
    # ``main()`` writes the chosen style into os.environ itself — the render
    # cache key reads it from there. conftest's autouse fixture now owns the
    # whole ``SELEKYT_*`` namespace, which is what stops this write from
    # leaking into every later test in the process: without it, this test left
    # ``punch`` behind and ``test_valid_cached_render_is_reused`` then saw a
    # cache built under ``minimal``, decided the render was stale and
    # re-encoded it, failing an assertion about exactly that.
    cli_mod.main()
    assert seen["source"] == "https://example.com/v"
    assert seen["model"] == "small"
    assert seen["language"] == "te"
    assert seen["kw"]["transcription_provider"] == "groq"
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


# ── failure reporting: a normal user error is a diagnosis, not a repr ────────


def _ffmpeg_exc() -> Exception:
    from subprocess import CalledProcessError

    return CalledProcessError(
        234,
        ["ffmpeg", "-y", "-i", "in.mp4", "-vn", "-f", "wav", "out.wav"],
    )


def test_a_subprocess_failure_is_reported_in_plain_words():
    """``CalledProcessError``'s message is its command line and an exit code,
    which is an implementation detail the spec says a user must not see. The
    failure screen shows a diagnosis instead, and the exit status survives it
    because the number is what the user pastes into a bug report."""
    out = cli_mod._plain_reason(str(_ffmpeg_exc()), _ffmpeg_exc(), "transcribe")
    assert "Command [" not in out
    assert "ffmpeg exited 234" in out
    assert "run.log" in out


def test_a_command_repr_that_arrived_as_a_reason_string_is_translated():
    """The pipeline converts the exception into a ``fail`` event carrying
    ``str(exc)`` before the exception itself reaches the handler, so the
    command repr has to be recognised in the reason string, not only on the
    exception."""
    from subprocess import CalledProcessError

    # An unrelated exception type: the only carrier of the shape is the text.
    # The stage is the one a real extraction failure carries, so the wording is
    # the decode diagnosis rather than the generic one.
    out = cli_mod._plain_reason(str(_ffmpeg_exc()), RuntimeError("unrelated"),
                                "transcribe")
    assert "ffmpeg exited 234" in out
    # The same text with no stage to interpret it falls back to the generic
    # wording, which still names the program and the status and hides the repr.
    generic = cli_mod._plain_reason(str(_ffmpeg_exc()),
                                    RuntimeError("unrelated"), None)
    assert "ffmpeg" in generic and "234" in generic
    assert "Command [" not in generic
    # An unrelated reason, untouched.
    assert cli_mod._plain_reason("no audible spoken content",
                                 RuntimeError("no audio"), "select") == \
        "no audible spoken content"


def test_an_unknown_program_keeps_its_verbatim_message():
    """Only ffmpeg/ffprobe/yt-dlp have a diagnosis here. Anything else is a
    failure this layer cannot interpret, so its own message is the honest thing
    to print — including for tools this does not know about yet."""
    from subprocess import CalledProcessError

    exc = CalledProcessError(2, ["curl", "-s", "https://example.com"])
    assert cli_mod._plain_reason(str(exc), exc, "input") == str(exc)


def test_a_render_time_ffmpeg_failure_is_not_blamed_on_the_source():
    """An encode that fails during rendering is not the source's audio track:
    the transcribe-stage diagnosis would be wrong there, so the render stage
    gets the version that reports the failure without naming a cause."""
    out = cli_mod._plain_reason(str(_ffmpeg_exc()), _ffmpeg_exc(), "render")
    assert "no audio stream" not in out
    assert "ffmpeg" in out and "run.log" in out


def test_a_failing_run_reports_no_command_repr_and_no_traceback(monkeypatch, capsys, tmp_path):
    """End to end: the pipeline raising a subprocess failure leaves the user
    with an exit code, a diagnosis and a log to point at — never the command it
    ran and never a Python traceback."""
    def boom(*a, **k):
        raise _ffmpeg_exc()

    monkeypatch.setattr(sys, "argv", ["selekyt", "in.mp4", "--out", str(tmp_path)])
    monkeypatch.setattr("selekyt.cli.run", boom)
    with pytest.raises(SystemExit) as info:
        cli_mod.main()
    assert info.value.code == 1
    err = capsys.readouterr().err
    assert "Command [" not in err
    assert "Traceback" not in err
    assert "run.log" in err


def _finished_out_dir(tmp_path: Path) -> Path:
    """An ``--out`` directory holding a finished run: the pipeline writes its
    state into ``out/project``, beside the run log. Only the pieces
    ``cleanable`` judges on are present — the recorded renders' files and the
    intermediates worth reclaiming."""
    import json as _json
    from selekyt.pipeline import STATE_NAME

    out = tmp_path / "run"
    project = out / "project"
    (project / "facebook").mkdir(parents=True)
    (project / "shorts").mkdir()
    (project / "transcription").mkdir()
    renders = {
        "01": {
            "facebook": str(project / "facebook" / "01.mp4"),
            "shorts": str(project / "shorts" / "01.mp4"),
        }
    }
    for path in renders["01"].values():
        Path(path).write_bytes(b"a real render")
    (project / "transcription" / "audio.wav").write_bytes(b"\x00" * 1_000_000)
    (out / "run.log").write_text("log", encoding="utf-8")
    (project / STATE_NAME).write_text(
        _json.dumps({"renders": renders, "selected_count": 1}), encoding="utf-8"
    )
    return out


def test_clean_project_accepts_the_out_directory_the_user_named(monkeypatch, capsys, tmp_path):
    """``--clean-project`` was documented against the same value ``--out``
    takes, but the pipeline wrote its state one level deeper, into
    ``out/project``. Refusing the outer directory sent a user to retype a path
    they had already typed — both spellings now resolve to the same run."""
    out = _finished_out_dir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["selekyt", "--clean-project", str(out)])
    with pytest.raises(SystemExit) as info:
        cli_mod.main()
    assert info.value.code == 0
    captured = capsys.readouterr()
    assert "removed transcription/" in captured.out
    assert not (out / "project" / "transcription").exists()
    # The deliverables survive, whichever spelling the argument used.
    assert (out / "project" / "facebook" / "01.mp4").is_file()


def test_clean_project_still_accepts_the_project_directory_itself(monkeypatch, capsys, tmp_path):
    """The spelling that names the project directory directly keeps working —
    the resolution only widens what is accepted, it does not move the target."""
    out = _finished_out_dir(tmp_path)
    project = out / "project"
    monkeypatch.setattr(sys, "argv", ["selekyt", "--clean-project", str(project)])
    with pytest.raises(SystemExit) as info:
        cli_mod.main()
    assert info.value.code == 0
    assert "removed transcription/" in capsys.readouterr().out


def test_clean_project_refuses_a_directory_that_is_neither(monkeypatch, capsys, tmp_path):
    """Neither spelling holds state: the engine's refusal names the path the
    user typed, and the exit code is still 1 so a script can tell."""
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    monkeypatch.setattr(sys, "argv", ["selekyt", "--clean-project", str(nowhere)])
    with pytest.raises(SystemExit) as info:
        cli_mod.main()
    assert info.value.code == 1
    out = capsys.readouterr().out
    assert str(nowhere) in out
    assert "not a pipeline project directory" in out


@pytest.mark.skipif(
    subprocess.run([sys.executable, "-c", "import selekyt"], capture_output=True).returncode != 0,
    reason="package not importable in this interpreter",
)
def test_python_m_selekyt_doctor_works():
    """The installed `python -m selekyt --doctor` path runs end to end."""
    proc = _run_module(["--doctor"])
    assert proc.returncode in (0, 1)
    assert "SelekyT doctor" in proc.stdout


@pytest.mark.skipif(
    subprocess.run([sys.executable, "-c", "import selekyt"], capture_output=True).returncode != 0,
    reason="package not importable in this interpreter",
)
def test_help_renders_every_flag():
    """`--help` reaches argparse's %-formatting, so a literal percent in any help
    string is a crash on the first thing a new user types.

    Nothing else in the suite renders help, which is how a broken escape here
    once shipped: the flag was unreachable from any test."""
    proc = _run_module(["--help"])
    assert proc.returncode == 0, proc.stderr
    assert "--skip-language-check" in proc.stdout
    assert "100%" in proc.stdout


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
