from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path


def require(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"{name} is required and must be on PATH")


def download(url: str, out_dir: Path) -> tuple[Path, dict]:
    require("yt-dlp")
    out_dir.mkdir(parents=True, exist_ok=True)
    info_cmd = ["yt-dlp", "--dump-single-json", "--no-download", url]
    info = json.loads(subprocess.check_output(info_cmd, text=True))
    vid = info.get("id", "video")
    target = out_dir / f"{vid}.%(ext)s"
    cmd = ["yt-dlp", "-f", "bv*+ba/b", "--merge-output-format", "mp4", "-o", str(target), "--no-playlist", url]
    subprocess.run(cmd, check=True)
    files = sorted(out_dir.glob(f"{vid}.*"))
    media = next((p for p in files if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}), None)
    if not media:
        raise RuntimeError("yt-dlp completed but no video file was found")
    return media, info
