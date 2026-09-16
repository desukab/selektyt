from __future__ import annotations
import argparse
from pathlib import Path
from selekyt.pipeline import run


def main() -> None:
    p = argparse.ArgumentParser(prog="selekyt", description="Turn long-form video into retention-aware social cuts.")
    p.add_argument("source", help="YouTube URL or local video path")
    p.add_argument("--out", default="out", help="Output directory")
    p.add_argument("--model", default="small", help="faster-whisper model size")
    p.add_argument("--language", default=None, help="Force transcription language, e.g. te, hi, ta")
    p.add_argument("--limit", type=int, default=6, help="Number of selected stories")
    p.add_argument("--cookies", default=None, help="Netscape cookies.txt path; credentials stay local")
    args = p.parse_args()
    project = run(args.source, Path(args.out), args.model, args.language, args.limit, args.cookies)
    print(f"SelekyT complete: {project}")

if __name__ == "__main__":
    main()
