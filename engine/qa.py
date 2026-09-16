from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path


def probe(path: Path) -> dict:
    if shutil.which("ffprobe") is None: raise RuntimeError("ffprobe is required and must be on PATH")
    p = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], check=True, text=True, stdout=subprocess.PIPE)
    return json.loads(p.stdout)


def validate_output(path: Path, vertical: bool) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not path.is_file() or path.stat().st_size < 100_000: return False, ["missing or empty output"]
    try: data = probe(path)
    except Exception as exc: return False, [f"ffprobe failed: {exc}"]
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video: errors.append("no video stream")
    if not audio: errors.append("no audio stream")
    if video:
        w, h = int(video.get("width", 0)), int(video.get("height", 0))
        if vertical and not h > w: errors.append(f"expected vertical video, got {w}x{h}")
        if not vertical and not w > h: errors.append(f"expected landscape video, got {w}x{h}")
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    if duration < 1.0: errors.append("output duration is too short")
    return not errors, errors


def validate_outputs(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for f in files:
        ok, detail = validate_output(f, "shorts" in f.parts)
        if not ok: errors.extend(f"{f}: {x}" for x in detail)
    return errors


def write_report(root: Path, outputs: list[Path]) -> Path:
    errors = validate_outputs(outputs)
    report = root / "qa.json"
    report.write_text(json.dumps({"ok": not errors, "errors": errors, "outputs": [str(p) for p in outputs]}, indent=2), encoding="utf-8")
    if errors: raise RuntimeError("output QA failed: " + "; ".join(errors))
    return report
