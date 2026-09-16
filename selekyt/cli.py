from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from selekyt.pipeline import run


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
    args = p.parse_args()
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
    )
    print(f"SelekyT complete: {project}")


if __name__ == "__main__":
    main()
