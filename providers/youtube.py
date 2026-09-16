from __future__ import annotations
import json
import os
import shutil
import subprocess
from pathlib import Path


def require(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"{name} is required and must be on PATH")


def _cookie_args(cookies: str | None) -> list[str]:
    if cookies:
        path = Path(cookies).expanduser()
        if not path.is_file(): raise RuntimeError(f"YouTube cookies file not found: {path}")
        return ["--cookies", str(path)]
    header = os.environ.get("SELEKYT_YOUTUBE_COOKIE")
    return ["--add-header", f"Cookie: {header}"] if header else []


def _runtime_args() -> list[str]:
    runtime = os.environ.get("SELEKYT_JS_RUNTIME")
    return ["--js-runtimes", runtime] if runtime else []


def download(url: str, out_dir: Path, cookies: str | None = None) -> tuple[Path, dict]:
    require("yt-dlp"); out_dir.mkdir(parents=True, exist_ok=True)
    auth = [*_cookie_args(cookies), *_runtime_args()]
    info_cmd = ["yt-dlp", *auth, "--dump-single-json", "--no-download", "--no-playlist", url]
    try:
        proc = subprocess.run(info_cmd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        info = json.loads(proc.stdout)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or "").strip()
        if "Sign in to confirm" in err or "not a bot" in err.lower():
            raise RuntimeError("YouTube rejected automated traffic. Use --cookies with a fresh local cookies.txt or a valid SELEKYT_YOUTUBE_COOKIE. Do not commit credentials.") from exc
        raise RuntimeError(f"yt-dlp metadata lookup failed:\n{err}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp returned invalid metadata JSON; check yt-dlp/EJS configuration.") from exc

    vid = info.get("id", "video")
    target = out_dir / f"{vid}.%(ext)s"
    cmd = ["yt-dlp", *auth, "-f", "bv*+ba/b", "--merge-output-format", "mp4", "-o", str(target), "--no-playlist", url]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("yt-dlp failed while downloading the YouTube video. Try a fresh cookie file or local download.") from exc
    files = sorted(out_dir.glob(f"{vid}.*"))
    media = next((p for p in files if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}), None)
    if not media: raise RuntimeError("yt-dlp completed but no video file was found")
    return media, info
