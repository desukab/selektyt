from __future__ import annotations

import argparse
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
        "--chunk-minutes",
        type=int,
        default=10,
        help="Whisper chunk size for long videos (default: 10)",
    )
    p.add_argument(
        "--cookies",
        default=None,
        help="Netscape cookies.txt OR browser Cookie-header file; credentials stay local",
    )
    p.add_argument("--doctor", action="store_true", help="Check the local processing environment")
    args = p.parse_args()
    if args.doctor or not args.source:
        raise SystemExit(_doctor())
    project = run(
        args.source,
        Path(args.out),
        args.model,
        args.language,
        args.limit,
        args.cookies,
        args.chunk_minutes,
    )
    print(f"SelekyT complete: {project}")


if __name__ == "__main__":
    main()
