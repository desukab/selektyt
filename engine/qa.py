from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path


def probe(path: Path) -> dict:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe is required and must be on PATH")
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]
    return json.loads(subprocess.check_output(cmd, text=True))


def validate_outputs(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for f in files:
        if not f.exists() or f.stat().st_size < 10000:
            errors.append(f"missing or suspiciously small output: {f}")
            continue
        try:
            probe(f)
        except Exception as exc:
            errors.append(f"ffprobe failed for {f}: {exc}")
    return errors
