from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from selekyt.cookies import convert

# YouTube's sign-in wall is not a property of one client. Observed from a
# datacenter IP, it answers "Sign in to confirm you're not a bot" for *some*
# videos and serves others to the same unauthenticated client in the same burst
# (one music-video download completed at 640x360 while a lecture was refused),
# so a single hardcoded client is never reliable. Trying each candidate in turn
# converts a hard failure into a retry; cookies remain the reliable answer on a
# network where every candidate is refused.
DEFAULT_PLAYER_CLIENTS = ("android", "tv_embedded", "web_safari")

BOT_MARKERS = ("Sign in to confirm", "not a bot")


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


def _client_candidates() -> list[str]:
    """The player-client list to try, in order.

    Overridable with ``SELEKYT_YOUTUBE_PLAYER_CLIENT=android,tv_embedded`` for a
    network where the default list is entirely refused.
    """
    configured = os.environ.get("SELEKYT_YOUTUBE_PLAYER_CLIENT", "")
    candidates = [c.strip() for c in configured.split(",") if c.strip()]
    return candidates or list(DEFAULT_PLAYER_CLIENTS)


def _player_client_args(clients: Sequence[str]) -> list[str]:
    return ["--extractor-args", f"youtube:player_client={'|'.join(clients)}"]


def _is_bot_block(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(marker.lower() in low for marker in BOT_MARKERS)


def _bot_error(clients: list[str]) -> RuntimeError:
    return RuntimeError(
        "YouTube rejected automated traffic from this network for every player "
        f"client tried ({', '.join(clients)}). Use --cookies with a fresh local "
        "cookie/header file or a valid SELEKYT_YOUTUBE_COOKIE, or set "
        "SELEKYT_YOUTUBE_PLAYER_CLIENT to a client list your network accepts. "
        "Do not commit credentials."
    )


def _run_metadata(base_cmd: list[str], clients: list[str]) -> tuple[dict, str]:
    """Look the video up once per client until one is served.

    Returns the metadata and the client that served it, so the download can try
    that client first instead of restarting the list.
    """
    last_err = ""
    for client in clients:
        cmd = [*base_cmd, *_player_client_args([client])]
        try:
            proc = subprocess.run(
                cmd, check=True, text=True, encoding="utf-8",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            return json.loads(proc.stdout), client
        except subprocess.CalledProcessError as exc:
            last_err = (exc.stderr or exc.stdout or "").strip()
            if not _is_bot_block(last_err):
                raise RuntimeError(f"yt-dlp metadata lookup failed:\n{last_err}") from exc
    raise _bot_error(clients)


def _run_download(base_cmd: list[str], clients: list[str]) -> None:
    """Download once per client until one is served.

    yt-dlp's progress must not reach this process's stdout: the sidecar serves a
    one-JSON-object-per-line protocol on fd 1 and an inherited child writes to
    the real fd 1, bypassing any Python-level ``sys.stdout`` swap (see
    ``server._progress_to_stderr``). stderr is captured to classify the failure
    and replayed to the real stderr so the CLI diagnosis stays visible.
    """
    last_err = ""
    for client in clients:
        cmd = [*base_cmd, *_player_client_args([client])]
        try:
            subprocess.run(cmd, check=True, stdout=2, stderr=subprocess.PIPE, text=True,
                           encoding="utf-8", errors="replace")
            return
        except subprocess.CalledProcessError as exc:
            last_err = (exc.stderr or "").strip()
            if last_err:
                print(last_err, file=sys.stderr)
            if not _is_bot_block(last_err):
                raise RuntimeError(f"yt-dlp failed while downloading the video:\n{last_err}") from exc
    raise _bot_error(clients)


def download(url: str, out_dir: Path, cookies: str | None = None) -> tuple[Path, dict]:
    require("yt-dlp")
    out_dir.mkdir(parents=True, exist_ok=True)
    clients = _client_candidates()
    auth = [*_cookie_args(cookies), *_runtime_args()]
    try:
        info, served_by = _run_metadata(
            ["yt-dlp", *auth, "--dump-single-json", "--no-download", "--no-playlist", url],
            clients,
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp returned invalid metadata JSON; check yt-dlp/EJS configuration.") from exc

    vid = info.get("id", "video")
    target = out_dir / f"{vid}.%(ext)s"
    # Prefer the client that served the metadata: the wall is per-video, so the
    # client that answered the lookup is the one most likely to answer the
    # download. The remaining clients stay as fallbacks.
    download_clients = [served_by, *[c for c in clients if c != served_by]]
    _run_download(
        ["yt-dlp", *auth, "-f", "bv*+ba/b", "--merge-output-format", "mp4", "-o", str(target), "--no-playlist", url],
        download_clients,
    )
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
