from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

from engine.captions import caption_presets
from selekyt.pipeline import run
from selekyt.server import serve_stdio as serve_python
from selekyt.runtime import bundled_mech_path, bundled_whisper_model, ping_mech
from selekyt.transcription_settings import (
    PROVIDERS as TRANSCRIPTION_PROVIDERS,
    TranscriptionSettingsError,
    configure_interactive as configure_transcription_interactive,
    default_provider as default_transcription_provider,
    api_key as transcription_api_key,
)
from selekyt.ui import (
    ProgressRenderer,
    Terminal,
    complete_screen,
    fail_screen,
    start_screen,
)


def _doctor() -> int:
    """Check only the tools required by the user's saved/default workflow."""
    print("SeleKyT doctor")
    if sys.platform == "win32":
        primary, alternate = "python", "python3"
    else:
        primary, alternate = "python3", "python"
    checks = [
        (primary, True, shutil.which(primary) is not None),
        (alternate, False, shutil.which(alternate) is not None),
        ("ffmpeg", True, shutil.which("ffmpeg") is not None),
        ("ffprobe", True, shutil.which("ffprobe") is not None),
        ("yt-dlp", False, shutil.which("yt-dlp") is not None),
    ]
    try:
        import onnxruntime  # noqa: F401
        ort_ok = True
    except ImportError:
        ort_ok = False
    checks.append(("onnxruntime", True, ort_ok))
    try:
        from engine.vad import SileroVADBackend
        silero_ok = SileroVADBackend().is_available()
    except Exception:
        silero_ok = False
    checks.append(("silero-vad model", True, silero_ok))
    try:
        import pycmx  # noqa: F401
        pycmx_ok = True
    except ImportError:
        pycmx_ok = False
    checks.append(("pycmx", True, pycmx_ok))
    try:
        import opentimelineio  # noqa: F401
        otio_ok = True
    except ImportError:
        otio_ok = False
    checks.append(("opentimelineio", True, otio_ok))
    try:
        import static_ffmpeg  # noqa: F401
        static_ffmpeg_ok = True
    except ImportError:
        static_ffmpeg_ok = False
    checks.append(("static-ffmpeg", True, static_ffmpeg_ok))
    mech = bundled_mech_path()
    if mech is not None:
        try:
            ping_mech()
            mech_ok = True
        except Exception:
            mech_ok = False
    else:
        mech_ok = False
    checks.append(("rust mechanical core", True, mech_ok))
    whisper_model = bundled_whisper_model()
    checks.append(("bundled whisper model", True, whisper_model is not None))
    provider = default_transcription_provider()
    print(f"  transcription     {provider}")
    if provider == "local":
        try:
            import faster_whisper  # noqa: F401
            asr_ok = True
        except ImportError:
            asr_ok = False
        checks.append(("faster-whisper", True, asr_ok))
    elif provider == "groq":
        try:
            import groq  # noqa: F401
            asr_ok = True
        except ImportError:
            asr_ok = False
        checks.append(("groq", True, asr_ok))
        checks.append(("groq key", True, bool(transcription_api_key("groq"))))
    else:
        try:
            import google.genai  # noqa: F401
            asr_ok = True
        except ImportError:
            asr_ok = False
        checks.append(("google-genai", True, asr_ok))
        checks.append(("gemini key", True, bool(transcription_api_key("gemini"))))
    missing = [name for name, required, present in checks if required and not present]
    for name, required, present in checks:
        status = "OK" if present else ("MISSING" if required else "OPTIONAL")
        print(f"  {name:18} {status}")
    if not missing:
        print("Ready.")
        return 0
    extra = {"local": "[transcription]", "groq": "[cloud-transcription]",
             "gemini": "[cloud-transcription]"}[provider]
    if any(name.endswith(" key") for name in missing):
        print(f"Configure the selected cloud provider with: selekyt setup --provider {provider}")
    else:
        print(f"Install the missing REQUIRED tools with: pip install 'selekyt{extra}'")
    return 1


def _is_os_stderr(stream: object) -> bool:
    """Whether ``stream`` is the process's real stderr, i.e. fd 2.

    A dup2 on the descriptor cannot reach an in-memory stream, and such a
    stream never shared the descriptor, so it needs no re-plumbing below: a
    test double keeps receiving the panel exactly as it did before. Anything
    without a fileno, or whose fileno is not 2, falls on the same side.
    """
    try:
        return stream.fileno() == 2  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        # io.UnsupportedOperation subclasses both ValueError and OSError, so a
        # StringIO-based double is covered without naming it.
        return False


@contextmanager
def _engine_stdout(log_path: Path, term: Terminal):
    """Send the engine's own stdout — and every child's stdio — to the run log.

    Every provider prints its own diagnostics — chunk position, RT factor, ETA
    — to stdout, which is also the channel ``--json`` reports on. Without this,
    ``selekyt URL --json | jq`` carries transcription chatter above the JSON.
    The live panel is on stderr and is unaffected, so this is what makes the
    two channels separable without touching a single print inside the engine.

    Appends rather than truncating: a run that was killed and resumed is one
    history, and the earlier diagnostics are what diagnose the interruption.

    Subprocesses and native libraries inherit OS-level *file descriptors*, not
    Python's ``sys.stdout`` object, so the object swap alone does not silence
    them: ffmpeg's banner, yt-dlp's progress and onnxruntime's C++ warnings all
    write straight to fds 1 and 2 and land on the user's terminal, interleaved
    with the live panel. Redirecting the descriptors themselves — one dup2 pair
    here, inside the context the whole pipeline already runs in — covers every
    spawn site at once, including any an audit has not mapped, without changing
    a single test-pinned ``subprocess.run`` call shape. It is also the only
    approach that can reach a native library at all: onnxruntime and ctranslate2
    write to fd 2 from C++, through no ``subprocess`` call to add a kwarg to.

    Chosen over per-call ``stderr=`` plumbing for that coverage, and because it
    keeps the diagnostic instead of discarding it: ffmpeg's stderr is the only
    post-mortem a failed render has, and the CLI points a failed run at
    "Details saved to: ./<out>/run.log", so the banner and the encoder's error
    report belong there rather than in DEVNULL. Calls that deliberately pipe a
    child's output — ``stderr=subprocess.PIPE`` in providers/ffmpeg.py and
    engine/qa.py, yt-dlp's ``stdout=2`` — still win over the redirect, which is
    exactly right: those are the ones whose captured output becomes the error
    message the user reads.

    The live panel writes to ``term.err``, which by then is the process's real
    stderr object and so fd 2 — just pointed at the log by the dup2 above. It
    is handed the pre-redirect copy of the descriptor so the panel keeps
    drawing on the terminal while the engine's children write to the log.

    ``--verbose`` skips this entire context (see main): that flag asks for the
    engine's diagnostics — child output included — on the terminal, and that
    is what it still gets, byte for byte.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    real_stdout = sys.stdout
    handle = open(log_path, "a", encoding="utf-8")
    handle.write(f"\n--- selekyt run {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    handle.flush()
    sys.stdout = handle
    saved_out: int | None = None
    saved_err: int | None = None
    panel: TextIO | None = None
    real_term_err = term.err
    try:
        try:
            saved_out = os.dup(1)
            saved_err = os.dup(2)
            os.dup2(handle.fileno(), 1)
            os.dup2(handle.fileno(), 2)
        except OSError:
            # Whatever of the redirect did take hold goes back before
            # continuing: leaving fd 1 on the log would silently send the rest
            # of the CLI's own output there.
            for saved, fd in ((saved_out, 1), (saved_err, 2)):
                if saved is None:
                    continue
                try:
                    os.dup2(saved, fd)
                except OSError:
                    pass
                try:
                    os.close(saved)
                except OSError:
                    pass
            saved_out = saved_err = None
        else:
            # closefd=False: the descriptor is restored by hand in the finally
            # below, and a wrapper that owned it would close the terminal it
            # still aliases.
            panel = os.fdopen(
                saved_err, "w", encoding="utf-8", errors="replace", closefd=False
            )
            if _is_os_stderr(real_term_err):
                term.err = panel
        yield
    finally:
        # Restore in the order that keeps every still-running writer valid: the
        # ticker thread can render between these steps, and while the
        # descriptors are redirected both ``panel`` (the pre-redirect copy) and
        # the reverted ``term.err`` (fd 2) write to the terminal, so the panel
        # never blinks onto the log and never writes a closed descriptor.
        for stream in (panel, handle):
            if stream is None:
                continue
            try:
                stream.flush()
            except OSError:
                pass
        if saved_out is not None and saved_err is not None:
            os.dup2(saved_out, 1)
            os.dup2(saved_err, 2)
            term.err = real_term_err
            os.close(saved_out)
            os.close(saved_err)
        sys.stdout = real_stdout
        handle.close()


def _remediation(reason: str) -> str | None:
    """The action the message's own diagnosis implies, or None when there is none.

    Every clause matches a wording the pipeline itself raises, so the advice is
    attached to the failure it names rather than emitted unconditionally. An
    unknown failure gets no invented remedy — the run log is the honest thing
    to point at instead.
    """
    reason = reason or ""
    if "no audible spoken content" in reason:
        return (
            "Check that the source has an audible speech track. If its container "
            "is damaged, re-mux or re-encode it by hand and re-run."
        )
    if "no audio stream, or a container" in reason:
        return (
            "Check that the file plays and has an audio track. If the container "
            "is damaged, re-mux it — `ffmpeg -i in.mp4 -c copy out.mp4` — and "
            "re-run."
        )
    if "not usable on this language" in reason:
        return (
            "Re-run with a model this language is measured on, or pass "
            "--skip-language-check once you have verified the transcript by hand."
        )
    if "covers far less audio" in reason or "coverage" in reason.lower():
        return (
            "Verify the transcript against the source by hand, then re-run with "
            "--skip-coverage-check if it really is complete."
        )
    if "passed the editorial/context gate" in reason:
        return (
            "Nothing in this source stood on its own at the current gates. Raise "
            "--limit for more attempts, or try a source with clearer standalone "
            "passages; report.json names every rejection reason."
        )
    if "Local video not found" in reason:
        return "Check the path — a local source must be a readable media file."
    if "Install transcription support" in reason or "engine" in reason.lower():
        return "Run `selekyt --doctor` to see which tools and models are installed."
    return None


def _plain_reason(reason: str, exc: BaseException, stage: str | None) -> str:
    """A subprocess failure in plain words, or the reason unchanged.

    ``CalledProcessError`` reports the command it ran and the exit status, and
    nothing about why: the why is on the tool's own stderr, which the run log
    already captured. By the time this runs the pipeline has already turned the
    exception into a ``fail`` event carrying ``str(exc)``, so the command repr
    is looked for in the reason string as well as on the exception — the same
    message reaches the screen either way.

    The stage says which ffmpeg step failed, and the wording keeps to what that
    distinguishes: a decode that fails before transcription has anything to work
    on is most often a source with no audio stream, while an encode failure
    during rendering is reported without that guess. Both point at run.log
    rather than inventing a diagnosis, and any failure whose program is not
    ffmpeg/ffprobe/yt-dlp keeps the verbatim message.
    """
    import re
    from subprocess import CalledProcessError

    cmd = exc.cmd if isinstance(exc, CalledProcessError) else None
    code = getattr(exc, "returncode", None)
    if cmd is None:
        m = re.match(r"Command '\['(.+?)'", reason)
        if not m:
            return reason
        cmd = m.group(1)
        code = re.search(r"exit status (\d+)", reason)
        code = int(code.group(1)) if code else None
    args = cmd if isinstance(cmd, list) else [cmd]
    name = str(args[0]).split("/")[-1]
    if name not in ("ffmpeg", "ffprobe", "yt-dlp"):
        return reason
    where = f" in run.log"
    if stage == "transcribe":
        return (
            f"could not decode this source's audio: {name} exited {code}. The "
            "file likely has no audio stream, or a container this build of "
            f"{name} cannot open — what {name} printed when it failed is"
            + where
        )
    return (
        f"{name} failed while processing this source (exit {code}); what it "
        "printed when it failed is" + where
    )


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Fail fast on unusable numeric flags, before any network or ASR work.
    A bad --limit used to survive all the way through download and
    transcription and then die as a confusing "no stories passed" error; a
    bad --chunk-minutes died mid-transcription after extracting audio.
    argparse's own ``type=int`` already rejects non-integers, so what is left
    to check here is the value range.
    """
    problems: list[str] = []
    if args.limit is not None and args.limit < 1:
        problems.append(f"--limit must be >= 1 (got {args.limit})")
    if args.chunk_minutes is not None and args.chunk_minutes < 1:
        problems.append(f"--chunk-minutes must be >= 1 (got {args.chunk_minutes})")
    if args.beam_size is not None and args.beam_size < 1:
        problems.append(f"--beam-size must be >= 1 (got {args.beam_size})")
    if args.cpu_threads is not None and args.cpu_threads < 1:
        problems.append(f"--cpu-threads must be >= 1 (got {args.cpu_threads})")
    if args.silence_threshold is not None and args.silence_threshold < 0:
        problems.append(f"--silence-threshold must be >= 0 (got {args.silence_threshold})")
    if args.silence_padding is not None and args.silence_padding < 0:
        problems.append(f"--silence-padding must be >= 0 (got {args.silence_padding})")
    if args.min_speech_ms is not None and args.min_speech_ms < 0:
        problems.append(f"--min-speech-ms must be >= 0 (got {args.min_speech_ms})")
    if args.min_silence_ms is not None and args.min_silence_ms < 0:
        problems.append(f"--min-silence-ms must be >= 0 (got {args.min_silence_ms})")
    if problems:
        parser.error("; ".join(problems))


def _version() -> str:
    """The installed distribution's version, and nothing invented when absent.

    Sourced from the package metadata rather than a second literal so the two
    cannot drift; a checkout run without an install reports that instead of a
    number that may not be true. Failing closed here is better than a wrong
    version next to a release number a user would act on.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("selekyt")
        except PackageNotFoundError:
            return "unknown (not installed; run pip install -e .)"
    except ImportError:  # pragma: no cover - 3.10+ ships importlib.metadata
        return "unknown"


def main() -> None:
    # ``selekyt analyze video.mp4`` is the documented invocation and
    # ``selekyt video.mp4`` stays valid: the verb carries no state, it only
    # names what the default does. A file genuinely called ``analyze`` remains
    # reachable as ``./analyze``.
    argv = sys.argv[1:]
    # Setup is a real CLI command, not a video source. Handle it before
    # argparse's positional source path so an installed entry point cannot
    # accidentally send "setup" into the normal pipeline.
    if argv[:1] == ["setup"]:
        setup = argparse.ArgumentParser(
            prog="selekyt setup",
            description="Set up SeleKyT transcription for this machine.",
        )
        setup.add_argument(
            "--provider", choices=TRANSCRIPTION_PROVIDERS,
            help="Transcription provider (default: interactive choice)",
        )
        setup_args = setup.parse_args(argv[1:])
        print("S E L E K Y T")
        print("First-run setup")
        print()
        print("Local transcription is the default and needs no API key.")
        print("Groq and Gemini are optional cloud providers.")
        print()
        try:
            provider = configure_transcription_interactive(setup_args.provider)
        except (TranscriptionSettingsError, OSError, ValueError, EOFError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from None
        label = {
            "local": "private/offline",
            "groq": "cloud using your Groq key",
            "gemini": "cloud using your Gemini key",
        }[provider]
        print()
        print(f"Saved default transcription provider: {provider} ({label}).")
        print("Run `selekyt --doctor` to check the environment.")
        print("Then run `selekyt ./my-video.mp4` to start editing.")
        return
    if argv and argv[0] == "configure-transcription":
        setup = argparse.ArgumentParser(
            prog="selekyt configure-transcription",
            description="Set SeleKyT's saved transcription provider and BYOK key.",
        )
        setup.add_argument("provider", nargs="?", choices=TRANSCRIPTION_PROVIDERS)
        setup_args = setup.parse_args(argv[1:])
        try:
            provider = configure_transcription_interactive(setup_args.provider)
        except (TranscriptionSettingsError, OSError, ValueError, EOFError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from None
        label = {"local": "private/offline", "groq": "cloud using your Groq key",
                 "gemini": "cloud using your Gemini key"}[provider]
        print(f"Saved default transcription provider: {provider} ({label}).")
        return
    if argv and argv[0] == "analyze":
        argv = argv[1:]
    p = argparse.ArgumentParser(
        prog="selekyt",
        description="Turn long-form video into retention-aware social cuts.",
    )
    p.add_argument("--version", action="version", version=_version())
    p.add_argument("source", nargs="?", help="YouTube URL or local video path")
    p.add_argument("--out", default="out", help="Output directory")
    p.add_argument("--model", default="small", help="faster-whisper model size")
    p.add_argument("--language", default=None, help="Force transcription language, e.g. te, hi, ta")
    p.add_argument(
        "--transcription", choices=TRANSCRIPTION_PROVIDERS, default=None,
        help="Provider for this run only (default: saved preference, initially local)",
    )
    p.add_argument("--limit", type=int, default=6, help="Maximum number of output cuts")
    p.add_argument(
        "--make", choices=["short", "story", "highlights"], default="highlights",
        help="Tell SeleKyT what you want: a ~60s short, a ~3min story, or multiple highlights",
    )
    p.add_argument(
        "--chunk-minutes", type=int, default=5, help="Whisper chunk size for long videos"
    )
    p.add_argument(
        "--beam-size", type=int, default=1, help="Beam size for Whisper decoding (>=1)"
    )
    p.add_argument(
        "--cpu-threads",
        type=int,
        default=None,
        help="CPU threads for Whisper (default: all available)",
    )
    p.add_argument(
        "--caption-style",
        # Derived rather than duplicated: a second list here is what lets the
        # accepted names drift from the presets the caption writer implements,
        # and argparse would then accept a name that renders as minimal.
        choices=list(caption_presets()),
        default="minimal",
        help="Caption visual preset",
    )
    p.add_argument(
        "--cookies",
        default=None,
        help="Netscape cookies.txt OR browser Cookie-header file; credentials stay local",
    )
    p.add_argument("--doctor", action="store_true", help="Check the local processing environment")
    p.add_argument(
        "--ollama-url",
        default=None,
        help="Ollama server URL (e.g. http://localhost:11434) for optional LLM editorial metadata",
    )
    p.add_argument(
        "--audio-analysis", action="store_true",
        help="Run VAD analysis + jump-cut engine (silence removal, FCPXML export)",
    )
    p.add_argument(
        "--vad-backend", default=None,
        choices=["energy", "ffmpeg", "silero", "whisper"],
        help="Force VAD backend (default: auto-select best available). "
             "Auto-selection prefers silero, then ffmpeg, then energy, then "
             "whisper; forcing a backend is for comparison and for a host "
             "where the preferred one is absent.",
    )
    p.add_argument(
        "--silence-threshold", type=float, default=0.5,
        help="Seconds of silence to remove as dead air (default: 0.5)",
    )
    p.add_argument(
        "--silence-padding", type=float, default=0.0,
        help="Seconds to pad each speech region boundary (default: 0.0)",
    )
    p.add_argument(
        "--min-speech-ms", type=int, default=150,
        help="Minimum speech duration in ms to keep (default: 150)",
    )
    p.add_argument(
        "--min-silence-ms", type=int, default=100,
        help="Minimum silence duration in ms to merge into speech (default: 100)",
    )
    p.add_argument(
        "--keep-pauses",
        action=argparse.BooleanOptionalAction, default=True,
        help="Preserve intentional and breathing pauses (default: True; "
             "use --no-keep-pauses to remove them as dead air)",
    )
    p.add_argument(
        "--remove-filler",
        action=argparse.BooleanOptionalAction, default=True,
        help="Remove hesitation/filler pauses in audio analysis (default: True; "
             "use --no-remove-filler to keep them)",
    )
    p.add_argument(
        "--export-fcpxml", action="store_true",
        help="Export Final Cut Pro XML (FCPXML 1.11) alongside cut list",
    )
    p.add_argument(
        "--skip-coverage-check", action="store_true",
        help="Ship a run whose transcript covers far less audio than the audio "
             "contains speech. The check exists because that condition shipped "
             "wrong captions as a success; only pass this after verifying the "
             "transcript by hand.",
    )
    p.add_argument(
        "--skip-language-check", action="store_true",
        # argparse %-formats its help strings, so a literal percent has to be
        # doubled or `selekyt --help` dies with "unsupported format character".
        help="Ship a run whose language the transcription model measures as "
             "unusable (WER above 100%%, i.e. more error words than the source "
             "has words). Captions and cut points are all derived from that "
             "transcript, so the deliverable is worthless on it; only pass "
             "this after verifying the transcript by hand.",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Print the final machine-readable report as JSON on stdout",
    )
    p.add_argument(
        "--server", action="store_true",
        help="Run the Python JSON-RPC sidecar loop instead of the pipeline",
    )
    p.add_argument(
        "--clean-project", metavar="DIR",
        help="Reclaim the intermediates a finished run no longer needs "
             "(transcription checkpoints and the downloaded source). Refuses "
             "unless every render the edit plan called for is on disk. Use "
             "--keep-source to preserve the downloaded media.",
    )
    p.add_argument(
        "--keep-source", action="store_true",
        help="With --clean-project, keep the downloaded media (what "
             "tools/nle_check.py probes and a re-render needs)",
    )
    p.add_argument(
        "--no-color", action="store_true",
        help="Disable colour in the output (also set via NO_COLOR)",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Print nothing but the final result and errors",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Show the engine's own diagnostics (chunk timing, ffmpeg output) "
             "instead of logging them to run.log",
    )
    args = p.parse_args(argv)
    if args.server:
        raise SystemExit(serve_python())
    if args.clean_project:
        raise SystemExit(_report_cleanup(Path(args.clean_project), keep_source=args.keep_source))
    if args.source:
        _validate_args(p, args)
    if args.doctor or not args.source:
        raise SystemExit(_doctor())
    os.environ["SELEKYT_CAPTION_STYLE"] = args.caption_style
    out = Path(args.out)
    term = Terminal(
        no_color=args.no_color, quiet=args.quiet, verbose=args.verbose,
        json_mode=args.json,
    )
    started = time.monotonic()
    renderer = ProgressRenderer(term)
    effective_transcription = args.transcription or default_transcription_provider()
    start_screen(
        term, source=args.source, model=args.model, language=args.language,
        out=str(out), width=None, height=None,
        transcription_provider=effective_transcription,
    )
    log_path = out / "run.log"
    # Keyword-only extras the pipeline accepts beyond its positional core; kept
    # in one place so the verbose and logged branches cannot drift apart.
    extras = dict(
        audio_analysis=args.audio_analysis,
        vad_backend=args.vad_backend,
        silence_threshold=args.silence_threshold,
        silence_padding=args.silence_padding,
        min_speech_ms=args.min_speech_ms,
        min_silence_ms=args.min_silence_ms,
        keep_pauses=args.keep_pauses,
        remove_filler=args.remove_filler,
        export_fcpxml=args.export_fcpxml,
        skip_coverage_check=args.skip_coverage_check,
        skip_language_check=args.skip_language_check,
        transcription_provider=args.transcription,
        progress=renderer.event,
        make=args.make,
    )
    try:
        # ``--verbose`` wants the engine's own output on the terminal, so it
        # skips the redirect; the panel and the diagnostics then interleave on
        # the two streams, which is what that flag asked for. Every other run
        # sends the engine's prints and every child's stdio to run.log, keeping
        # only the live panel on the terminal (see _engine_stdout).
        if term.verbose:
            project = run(args.source, out, args.model, args.language, args.limit,
                          args.cookies, args.chunk_minutes, args.beam_size,
                          args.cpu_threads, args.ollama_url, **extras)
        else:
            with _engine_stdout(log_path, term):
                project = run(args.source, out, args.model, args.language, args.limit,
                              args.cookies, args.chunk_minutes, args.beam_size,
                              args.cpu_threads, args.ollama_url, **extras)
    except BaseException as exc:
        # A normal user error is reported as an action to take, not as a
        # traceback. The exception's own message is echoed verbatim because
        # tools/corpus_run.py classifies runs by grepping it out of stderr, but
        # its Python class name is dropped: it is an implementation detail the
        # spec says a normal user must never see. A subprocess failure is the
        # exception to the verbatim rule: a CalledProcessError's message is its
        # command line and an exit code, which tells a user nothing about the
        # source they handed in — and the verbatim form is already in run.log
        # beside the tool's own stderr, so nothing a post-mortem needs is lost.
        renderer.close()
        reason = _plain_reason(
            renderer.failed_reason or str(exc), exc, renderer.failed_stage
        )
        fail_screen(
            term,
            stage=renderer.failed_stage,
            reason=reason,
            remediation=_remediation(reason),
            log_path=str(log_path) if log_path.is_file() else None,
        )
        # The panel is gone and its screen is drawn; the terminal descriptor
        # measurement used to lay them out can go back now.
        term.close()
        raise SystemExit(1)
    renderer.close()
    runtime = time.monotonic() - started
    # The report is read rather than remembered because it is the record the run
    # just wrote: a field the pipeline does not populate is a field the screen
    # does not show. A caller that hands back something other than a path (a
    # test double, or a future entry that returns early) has no report to read,
    # and the screen renders what it can name without one.
    report: dict[str, object] = {}
    if isinstance(project, (Path, str)):
        report_path = Path(project) / "report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        artifacts = sum(1 for f in Path(project).rglob("*") if f.is_file())
    else:
        artifacts = 0
    if args.json:
        # The only thing on stdout in this mode: the report, after the redirect
        # above has been released.
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        complete_screen(
            term, report=report, project=str(project), runtime=runtime,
            artifacts=artifacts,
        )
    # Last use of term was the screen above; nothing renders after this point.
    term.close()


def _project_dir(arg: Path) -> Path:
    """The directory a run wrote its state into, given either spelling.

    ``--out`` names a directory that holds ``run.log`` alongside the ``project``
    subdirectory the pipeline wrote; ``--clean-project`` was documented against
    the same value, so both the out directory and the project inside it resolve
    here rather than making a user retype a path they already typed once. When
    neither holds state the argument is returned unchanged, and the engine's
    own refusal explains why."""
    for candidate in (arg, arg / "project"):
        if (candidate / "pipeline_state.json").is_file():
            return candidate
    return arg


def _report_cleanup(project: Path, *, keep_source: bool = False) -> int:
    """Print what ``--clean-project`` removed, or why it refused.

    The bytes are the point of the command, so they are reported in a form a
    person can read; the exit code is 1 on a refusal so a script can tell a
    no-op from a success."""
    from selekyt.pipeline import reclaim_intermediates
    resolved = _project_dir(project)
    result = reclaim_intermediates(resolved, keep_source=keep_source)
    if not result["ok"]:
        print(f"not cleaning {project}: {result['reason']}")
        return 1
    for row in result["removed"]:
        mb = row["bytes"] / (1024 * 1024)
        print(f"removed {row['dir']}/ ({mb:.1f} MB)")
    if not result["removed"]:
        print(f"{resolved} has no regenerable intermediates to reclaim")
    if result.get("re_download_on_rerun"):
        print(
            "the downloaded source is gone; a re-run will fetch it again "
            "(keep it with --keep-source if you intend to re-render)"
        )
    return 0


if __name__ == "__main__":
    main()
