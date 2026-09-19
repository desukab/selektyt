from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from selekyt.cookies import convert


def require(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"{name} is required and must be on PATH")


def _cookie_args(cookies: str | None) -> list[str]:
    """Return yt-dlp auth args, converting a browser header file when needed."""
    if cookies:
        path = Path(cookies).expanduser()
        if not path.is_file():
            raise RuntimeError(f"YouTube cookies file not found: {path}")
        # Accept either an existing Netscape file or a raw browser Cookie header.
        first = path.read_text(encoding="utf-8", errors="replace")[:256]
        if "# Netscape HTTP Cookie File" in first:
            return ["--cookies", str(path)]
        generated = path.with_name(path.stem + ".cookies.txt")
        if not generated.exists() or generated.stat().st_mtime_ns < path.stat().st_mtime_ns:
            convert(path, generated)
        return ["--cookies", str(generated)]

    header = os.environ.get("SELEKYT_YOUTUBE_COOKIE")
    return ["--add-header", f"Cookie: {header}"] if header else []


def _runtime_args() -> list[str]:
    runtime = os.environ.get("SELEKYT_JS_RUNTIME")
    return ["--js-runtimes", runtime] if runtime else []


def download(url: str, out_dir: Path, cookies: str | None = None) -> tuple[Path, dict]:
    require("yt-dlp")
    out_dir.mkdir(parents=True, exist_ok=True)
    auth = [*_cookie_args(cookies), *_runtime_args()]
    info_cmd = ["yt-dlp", *auth, "--dump-single-json", "--no-download", "--no-playlist", url]
    try:
        proc = subprocess.run(info_cmd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        info = json.loads(proc.stdout)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or "").strip()
        if "Sign in to confirm" in err or "not a bot" in err.lower():
            raise RuntimeError("YouTube rejected automated traffic. Use --cookies with a fresh local cookie/header file or a valid SELEKYT_YOUTUBE_COOKIE. Do not commit credentials.") from exc
        raise RuntimeError(f"yt-dlp metadata lookup failed:\n{err}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp returned invalid metadata JSON; check yt-dlp/EJS configuration.") from exc

    vid = info.get("id", "video")
    target = out_dir / f"{vid}.%(ext)s"
    cmd = ["yt-dlp", *auth, "-f", "bv*+ba/b", "--merge-output-format", "mp4", "-o", str(target), "--no-playlist", url]
    try:
        # yt-dlp's progress must not reach this process's stdout. The Python
        # sidecar serves a one-JSON-object-per-line protocol on fd 1, and
        # inherited child output arrives on the real stdout, bypassing the
        # sidecar's own sys.stdout swap (see server._progress_to_stderr).
        # Pointing the child at fd 2 keeps progress visible in the CLI and off
        # the wire, whatever Python-level swaps are in force.
        subprocess.run(cmd, check=True, stdout=2, stderr=2)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("yt-dlp failed while downloading the YouTube video. Try a fresh cookie/header file or local download.") from exc
    files = sorted(out_dir.glob(f"{vid}.*"))
    # ``--merge-output-format mp4`` only applies when yt-dlp actually merges
    # streams. A source whose best muxed format is already MPEG-PS (.mpg) or a
    # transport stream (.ts) is written as-is with its own extension, so a
    # fixed allowlist here would reject a download that succeeded. Any file
    # ffprobe can read as a container is usable; the pipeline decodes through
    # ffmpeg regardless of the wrapper.
    media = next((p for p in files if p.is_file() and p.suffix.lower() not in {".json", ".part", ".temp"}), None)
    if not media:
        raise RuntimeError("yt-dlp completed but no video file was found")
    return media, info
