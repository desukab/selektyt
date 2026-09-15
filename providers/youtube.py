from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path


def require(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"{name} is required and must be on PATH")


def _cookie_args(cookies: str | None) -> list[str]:
    if not cookies:
        return []
    path = Path(cookies).expanduser()
    if not path.is_file():
        raise RuntimeError(f"YouTube cookies file not found: {path}")
    return ["--cookies", str(path)]


def download(url: str, out_dir: Path, cookies: str | None = None) -> tuple[Path, dict]:
    require("yt-dlp")
    out_dir.mkdir(parents=True, exist_ok=True)
    cookie_args = _cookie_args(cookies)

    info_cmd = ["yt-dlp", *cookie_args, "--dump-single-json", "--no-download", "--no-playlist", url]
    try:
        info = json.loads(subprocess.check_output(info_cmd, text=True, stderr=subprocess.STDOUT))
    except subprocess.CalledProcessError as exc:
        output = exc.output or ""
        if "Sign in to confirm you're not a bot" in output:
            raise RuntimeError(
                "YouTube blocked this Codespace request as automated traffic. "
                "Export fresh YouTube cookies from a browser where you are signed in, "
                "upload the cookies.txt file to the Codespace, then rerun with "
                "--cookies /path/to/cookies.txt. Do not commit the cookies file."
            ) from exc
        raise RuntimeError(f"yt-dlp could not read the YouTube video metadata:\n{output.strip()}") from exc

    vid = info.get("id", "video")
    target = out_dir / f"{vid}.%(ext)s"
    cmd = [
        "yt-dlp",
        *cookie_args,
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", str(target),
        "--no-playlist",
        url,
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("yt-dlp failed while downloading the YouTube video") from exc

    files = sorted(out_dir.glob(f"{vid}.*"))
    media = next((p for p in files if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}), None)
    if not media:
        raise RuntimeError("yt-dlp completed but no video file was found")
    return media, info
