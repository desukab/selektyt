from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from selekyt.pipeline import run
from selekyt.server import serve_stdio as serve_python


def _doctor() -> int:
    print("SelekyT doctor")
    checks = [
        ("python", True, shutil.which("python") is not None),
        ("ffmpeg", True, shutil.which("ffmpeg") is not None),
        ("ffprobe", True, shutil.which("ffprobe") is not None),
        ("yt-dlp", True, shutil.which("yt-dlp") is not None),
    ]
    try:
        import faster_whisper  # noqa: F401
        whisper_ok = True
    except ImportError:
        whisper_ok = False
    checks.append(("faster-whisper", True, whisper_ok))
    ok = True
    for name, required, present in checks:
        status = "OK" if present else ("MISSING" if required else "OPTIONAL")
        print(f"  {name:18} {status}")
        ok = ok and present
    print("Ready." if ok else "Install the missing REQUIRED tools before running.")
    return 0 if ok else 1


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
        choices=["minimal", "punch", "spotlight", "noir"],
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
        "--keep-pauses", action="store_true", default=True,
        help="Preserve intentional and breathing pauses (default: True)",
    )
    p.add_argument(
        "--remove-filler", action="store_true", default=True,
        help="Remove hesitation/filler pauses in audio analysis (default: True)",
    )
    p.add_argument(
        "--export-fcpxml", action="store_true",
        help="Export Final Cut Pro XML (FCPXML 1.11) alongside cut list",
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
    )
    if args.json:
        report_path = project / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"SelekyT complete: {project}")


if __name__ == "__main__":
    main()
