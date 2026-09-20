from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from engine.captions import caption_presets
from selekyt.pipeline import run
from selekyt.server import serve_stdio as serve_python


def _doctor() -> int:
    print("SelekyT doctor")
    # `python` is what the docs tell people to type, but a plain Debian/Ubuntu
    # install ships only `python3`, and every distro that has `python` also has
    # `python3`. Requiring the unversioned name reported a hard MISSING while
    # SelekyT was running under a perfectly good interpreter, sending users to
    # install a package they did not need.
    # The mirror-image gap is Windows: a python.org install puts `python.exe`
    # on PATH and usually no `python3` at all, so the versioned check fails
    # there for the same reason. Require whichever name the platform actually
    # uses and keep the other advisory.
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
        import faster_whisper  # noqa: F401
        whisper_ok = True
    except ImportError:
        whisper_ok = False
    checks.append(("faster-whisper", True, whisper_ok))
    missing = [name for name, required, present in checks if required and not present]
    for name, required, present in checks:
        status = "OK" if present else ("MISSING" if required else "OPTIONAL")
        print(f"  {name:18} {status}")
    if not missing:
        print("Ready.")
        return 0
    print(
        "Install the missing REQUIRED tools before running: "
        + ", ".join(missing)
        + ". (OPTIONAL tools are only needed for some sources.)"
    )
    return 1


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


def main() -> None:
    p = argparse.ArgumentParser(
        prog="selekyt",
        description="Turn long-form video into retention-aware social cuts.",
    )
    p.add_argument("source", nargs="?", help="YouTube URL or local video path")
    p.add_argument("--out", default="out", help="Output directory")
    p.add_argument("--model", default="small", help="faster-whisper model size")
    p.add_argument("--language", default=None, help="Force transcription language, e.g. te, hi, ta")
    p.add_argument("--limit", type=int, default=6, help="Number of selected stories")
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
        help="Force VAD backend (default: auto-select best available)",
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
        "--json", action="store_true",
        help="Print the final machine-readable report as JSON on stdout",
    )
    p.add_argument(
        "--server", action="store_true",
        help="Run the Python JSON-RPC sidecar loop instead of the pipeline",
    )
    args = p.parse_args()
    if args.server:
        raise SystemExit(serve_python())
    if args.source:
        _validate_args(p, args)
    if args.doctor or not args.source:
        raise SystemExit(_doctor())
    os.environ["SELEKYT_CAPTION_STYLE"] = args.caption_style
    project = run(
        args.source,
        Path(args.out),
        args.model,
        args.language,
        args.limit,
        args.cookies,
        args.chunk_minutes,
        args.beam_size,
        args.cpu_threads,
        args.ollama_url,
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
    )
    if args.json:
        report_path = project / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"SelekyT complete: {project}")


if __name__ == "__main__":
    main()
