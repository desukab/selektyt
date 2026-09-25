from __future__ import annotations

import json
import os
import re
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

# ── source quality: choose an explicit native format ────────────────────────
#
# ``bv*+ba/b`` is yt-dlp's own default: the best video stream of any kind,
# merged with the best audio, else the best stream that already carries both.
# It picked 640x360 (~65 kbps) for a video that also offers native 854x480 and
# 1280x720, and the deliverable inherited that choice all the way to the
# render — 360 source lines become a 202x360 vertical frame, and at CRF 20 on
# a frame that small the encoder emits ~120 kbps of video. The whole quality
# complaint was one upstream decision.
#
# The clause that should have taken 720p does not fail by ranking badly. It
# fails by *matching nothing*, which drops selection to ``/b`` — the best
# stream that already carries both video and audio, which on YouTube is itag
# 18 at 640x360. Two conditions make it match nothing: yt-dlp cannot find
# ffmpeg to mux the video and audio halves, or the player response came back
# degraded. YouTube extraction now needs a JavaScript runtime, and without one
# yt-dlp warns that "some formats may be missing", which on a phone usually
# means every adaptive stream is. Neither failure is audible: the download
# succeeds, and the resulting file looks complete until it is measured.
#
# So instead of asking yt-dlp to rank formats implicitly, the metadata lookup
# that already runs is used to pick one explicitly: a native stream (not the
# AI-upscaled ones YouTube now labels) that this machine's ffmpeg can decode,
# at or under a height the renderer can use, with audio joined only when a
# merge is actually possible. Ordered fallbacks degrade to older muxed streams
# rather than to a hidden default, and the choice is recorded in the project
# state beside the probe that shows what actually arrived, so the next
# 640x360 is diagnosable instead of invisible.

# The vertical frame is never wider than 1080 pixels and the scale layer never
# enlarges, so source height above this buys deliverable detail only by
# widening a crop that is already at the ceiling, and costs bandwidth, storage
# and decode time on the phone the run is happening on.
DEFAULT_MAX_SOURCE_HEIGHT = 1080
_MAX_HEIGHT_ENV = "SELEKYT_MAX_SOURCE_HEIGHT"
_ALLOW_SUPER_RESOLUTION_ENV = "SELEKYT_ALLOW_SUPER_RESOLUTION"
# Below this, a "max height" is a refusal to download rather than a policy.
_MIN_SENSIBLE_HEIGHT = 144
# yt-dlp evaluates a ``/`` list in order and takes the first arm that matches,
# so this many explicit alternatives is as far as a single call should reach.
_MAX_FORMAT_SPECS = 6

LEGACY_FORMAT_SPEC = "bv*+ba/b"

# yt-dlp writes full codec ids ("avc1.64001f", "av01.0.12M.08", "vp9"), but
# ffmpeg's decoder list and every decision below want the family. YouTube's AAC
# audio arrives as "mp4a.40.2" — the container name, not the codec — so it needs
# its own alias or every adaptive audio stream looks undecodable and the policy
# quietly falls back to a low-resolution muxed stream.
_CODEC_ALIASES = {
    "avc": "h264", "avc1": "h264",
    "h265": "hevc", "hev1": "hevc", "hvc1": "hevc",
    "mp4v": "mpeg4", "vp09": "vp9", "av01": "av1",
    "mp4a": "aac",
}

# H.264 first: phones decode it in hardware and an H.264 video half merges
# into the MP4 container without a transcode. Every codec ffmpeg can decode is
# still accepted; this only orders streams of equal resolution.
_VIDEO_CODEC_PREFERENCE = {"h264": 2, "hevc": 1}
# Audio that muxes into MP4 without a transcode is preferred, then the codecs a
# phone is likeliest to handle.
_AUDIO_CODEC_PREFERENCE = {"aac": 3, "mp3": 2, "ac3": 1, "opus": 0, "vorbis": 0}

_CODECS_IN_LINE = re.compile(r"\(codec ([a-z0-9]+)\)")
# What to assume when ffmpeg cannot be asked. H.264 and MPEG-4 are the formats
# a phone is most likely to decode, and the caller reports that it guessed.
_CONSERVATIVE_CODECS = {"h264", "mpeg4", "aac", "mp3"}

_DECODABLE_CODECS: set[str] | None = None


def require(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"{name} is required and must be on PATH")


def _max_source_height() -> int:
    """The tallest source SeleKyT asks for, from ``SELEKYT_MAX_SOURCE_HEIGHT``.

    Overridable because a network that bills by the byte or a device short on
    storage has a different answer than the default, and the override has to be
    a real ceiling to mean anything.
    """
    raw = os.environ.get(_MAX_HEIGHT_ENV, "").strip()
    try:
        requested = int(raw)
    except ValueError:
        return DEFAULT_MAX_SOURCE_HEIGHT
    return requested if requested >= _MIN_SENSIBLE_HEIGHT else DEFAULT_MAX_SOURCE_HEIGHT


def _allow_super_resolution() -> bool:
    """Whether the AI-upscaled streams YouTube labels may be taken.

    Off by default: an upscaled 1080p stream carries no more real detail than
    the 720p it was made from, costs more bandwidth to prove it, and the
    render would then treat an interpolation as though it were captured
    resolution. ``SELEKYT_ALLOW_SUPER_RESOLUTION=1`` opts in for the case where
    a source genuinely has no native alternative.
    """
    return os.environ.get(_ALLOW_SUPER_RESOLUTION_ENV, "").strip().lower() in ("1", "true", "yes")


def codec_family(codec: str) -> str | None:
    """The decoder family a codec id belongs to, or ``None`` for ``none``.

    ffmpeg names decoders by family — ``h264``, ``av1`` — and this is the
    spelling compared against that list.
    """
    if not codec or codec == "none":
        return None
    base = codec.split(".")[0].lower()
    return _CODEC_ALIASES.get(base, base)


def is_super_resolution(fmt: dict) -> bool:
    """Whether YouTube marked a stream as AI-upscaled.

    The extractor appends ``sr`` to such a stream's format id and the words
    ``AI-upscaled`` to its note, both derived from the ``xtags=sr`` marker on
    the stream URL. Either is sufficient on its own; checking both keeps this
    working if the note is localised away or the id scheme changes.
    """
    if str(fmt.get("format_id") or "").endswith("-sr"):
        return True
    return "AI-upscaled" in (fmt.get("format_note") or "")


def decodable_codecs() -> set[str]:
    """The codec families the local ffmpeg can decode.

    A stream the host cannot decode is not a usable source: yt-dlp downloads
    it happily and ffmpeg then fails on the first frame, after the bandwidth
    is spent. ``ffmpeg -decoders`` names each entry with the family in
    ``(codec av1)`` form, which is the spelling ``codec_family`` produces.
    Without ffmpeg there is no answer to give, so the conservative set is
    returned and the caller reports that it guessed rather than checked.
    """
    global _DECODABLE_CODECS
    if _DECODABLE_CODECS is not None:
        return _DECODABLE_CODECS
    found: set[str] = set()
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-decoders"], capture_output=True,
            text=True, timeout=30)
        found = {fam for line in proc.stdout.splitlines()
                 for fam in _CODECS_IN_LINE.findall(line)}
    except (OSError, subprocess.SubprocessError):
        found = set()
    _DECODABLE_CODECS = found or set(_CONSERVATIVE_CODECS)
    return _DECODABLE_CODECS


def _video_streams(formats: list[dict], *, allow_super_resolution: bool,
                   decodable: set[str]) -> list[dict]:
    """Streams worth downloading, best first.

    ``decodable`` is a hard filter rather than a preference: a stream the host
    cannot play is wasted bandwidth and a render that fails late. Super
    resolution is filtered separately so the caller can try every native
    option first and fall back to it, rather than treating it as simply worse.
    """
    usable: list[dict] = []
    for fmt in formats:
        if not fmt.get("height") or not fmt.get("width"):
            continue
        family = codec_family(fmt.get("vcodec") or "")
        if family is None or family not in decodable:
            continue
        if fmt.get("has_drm"):
            continue
        if is_super_resolution(fmt) and not allow_super_resolution:
            continue
        usable.append(fmt)
    return sorted(usable, key=_video_rank_key, reverse=True)


def _video_rank_key(fmt: dict) -> tuple[int, int, float]:
    """Resolution, then codec, then bitrate — the order a human would pick in."""
    family = codec_family(fmt.get("vcodec") or "")
    return (
        int(fmt.get("height") or 0),
        _VIDEO_CODEC_PREFERENCE.get(family or "", 0),
        float(fmt.get("tbr") or fmt.get("vbr") or 0),
    )


def _best_audio_id(formats: list[dict], decodable: set[str]) -> str | None:
    """The audio-only stream to mux in, preferring one that needs no transcode.

    Joining an Opus half into an MP4 container means re-encoding audio, while
    an AAC half copies. Picking the audio half is a real decision, not a
    detail: a video-only source without one ships silent, and the render would
    describe that as success.
    """
    usable = []
    for fmt in formats:
        if (fmt.get("vcodec") or "none") != "none":
            continue
        family = codec_family(fmt.get("acodec") or "")
        if family is None or family not in decodable:
            continue
        usable.append(fmt)
    if not usable:
        return None
    best = max(usable, key=lambda f: (
        _AUDIO_CODEC_PREFERENCE.get(codec_family(f.get("acodec") or "") or "", -1),
        float(f.get("tbr") or f.get("abr") or 0),
    ))
    return str(best.get("format_id") or "") or None


def _format_spec(video: dict, audio_id: str | None, can_merge: bool) -> str | None:
    """The ``-f`` arm for one video stream, or ``None`` if it cannot be used.

    A stream that already carries audio is a single download and needs no
    muxer, which is what keeps a source usable on a host without ffmpeg. A
    video-only stream is only offered when its audio half is known and a merge
    is possible; otherwise it is skipped rather than fetched silent.
    """
    fmt_id = str(video.get("format_id") or "")
    if not fmt_id:
        return None
    if (video.get("acodec") or "none") == "none":
        if not can_merge or not audio_id:
            return None
        return f"{fmt_id}+{audio_id}"
    return fmt_id


def _select_source(
    formats: list[dict],
    *,
    cap: int,
    allow_super_resolution: bool,
    decodable: set[str],
) -> tuple[list[str], dict | None, str]:
    """The ordered ``-f`` arms and the stream the first arm names.

    Shared by :func:`select_format_spec` and :func:`describe_source_choice` so the
    record of what was chosen cannot drift from the spec that was actually sent.
    Returns ``(specs, chosen, reason)``; ``specs`` is empty when nothing here is
    usable, and the caller defers to yt-dlp's own ranking in that case.
    """
    # Joining halves needs a muxer; without one only streams that already carry
    # audio are offered, which on YouTube means older and lower-resolution ones.
    can_merge = shutil.which("ffmpeg") is not None
    native = _video_streams(formats, allow_super_resolution=False, decodable=decodable)
    audio_id = _best_audio_id(formats, decodable)

    # Each layer is a strictly weaker policy, and the first that yields a
    # usable stream wins: native under the ceiling, then native at any height
    # (the ceiling is a preference, not a refusal), then the same two with the
    # AI-upscaled streams admitted.
    if not allow_super_resolution:
        layers = (
            [f for f in native if int(f["height"]) <= cap],
            native,
        )
    else:
        super_res = _video_streams(formats, allow_super_resolution=True, decodable=decodable)
        layers = (
            [f for f in native if int(f["height"]) <= cap],
            native,
            [f for f in super_res if int(f["height"]) <= cap],
            super_res,
        )

    specs: list[str] = []
    chosen: dict | None = None
    # Video-only streams skipped for want of a muxer. Counted so the record can
    # say that a higher resolution existed and was unreachable, which is the
    # difference between "the source is low-resolution" and "this host could not
    # take what the source offered".
    unmergeable = 0
    for pool in layers:
        if not pool:
            continue
        for video in pool:
            if not can_merge and (video.get("acodec") or "none") == "none":
                unmergeable += 1
                continue
            spec = _format_spec(video, audio_id, can_merge)
            if not spec or spec in specs:
                continue
            specs.append(spec)
            chosen = chosen or video
            if len(specs) >= _MAX_FORMAT_SPECS:
                break
        if specs:
            break

    if not specs:
        if unmergeable:
            reason = ("ffmpeg is not on PATH, so the video and audio halves "
                      "cannot be joined and no stream that already carries "
                      "audio is usable; deferred to yt-dlp's own format ranking")
        else:
            reason = ("no decodable video stream in the metadata; deferred to "
                      "yt-dlp's own format ranking")
        return [], None, reason

    height = int(chosen["height"])
    if allow_super_resolution and is_super_resolution(chosen):
        reason = (f"no native stream available; took the AI-upscaled {height}p "
                  "stream because super resolution was explicitly allowed")
    elif height > cap:
        reason = (f"no native stream at or under {cap}p; took the best there is "
                  f"at {height}p rather than refusing the source")
    else:
        reason = f"native {height}p, the best decodable stream at or under {cap}p"
    if unmergeable:
        reason += (f"; ffmpeg is not on PATH, so {unmergeable} higher-resolution "
                   "video-only stream(s) could not be joined with an audio half "
                   "and were skipped")
    return specs, chosen, reason


def select_format_spec(
    info: dict,
    *,
    max_height: int | None = None,
    allow_super_resolution: bool | None = None,
    decodable: set[str] | None = None,
) -> str:
    """Build the yt-dlp ``-f`` string for the best source SeleKyT can use.

    Prefers a native, locally-decodable stream at or under ``max_height``,
    falling back in order: the same streams at any height (the ceiling is a
    preference, not a refusal), then the AI-upscaled ones, then yt-dlp's own
    default. The arms are explicit format ids, so a degraded player response
    that hides the adaptive streams matches nothing here and the failure is
    the empty match, not a silent reranking into 640x360.

    The result is ordered best-first with ``/`` so a single yt-dlp call tries
    each alternative, and it always ends in :data:`LEGACY_FORMAT_SPEC`: if
    every format here is unrecognisable the download still proceeds under
    yt-dlp's own rules rather than aborting a run whose metadata is the only
    thing wrong with it.
    """
    formats = info.get("formats") if isinstance(info, dict) else None
    if not formats:
        return LEGACY_FORMAT_SPEC
    cap = _max_source_height() if max_height is None else max_height
    allow_sr = _allow_super_resolution() if allow_super_resolution is None else allow_super_resolution
    decodable = decodable_codecs() if decodable is None else decodable
    specs, _chosen, _reason = _select_source(
        formats, cap=cap, allow_super_resolution=allow_sr, decodable=decodable)
    if not specs:
        # Nothing here is usable as offered. Defer to yt-dlp's own ranking
        # rather than fail a run whose only problem is a format list this code
        # does not understand.
        return LEGACY_FORMAT_SPEC
    return "/".join([*specs, LEGACY_FORMAT_SPEC])


def describe_source_choice(
    info: dict,
    *,
    max_height: int | None = None,
    allow_super_resolution: bool | None = None,
    decodable: set[str] | None = None,
) -> dict:
    """A record of which source the policy picked and what it rejected it for.

    Written into the project state beside the probe that reports what actually
    arrived, so a run that lands at 640x360 can be read afterwards: the spec
    asked for, the stream it named, and whether a merge was possible.
    """
    formats = info.get("formats") if isinstance(info, dict) else None
    cap = _max_source_height() if max_height is None else max_height
    allow_sr = _allow_super_resolution() if allow_super_resolution is None else allow_super_resolution
    decodable = decodable_codecs() if decodable is None else decodable
    specs, chosen, reason = _select_source(
        formats or [], cap=cap, allow_super_resolution=allow_sr, decodable=decodable)
    native = _video_streams(formats or [], allow_super_resolution=False, decodable=decodable)
    if not specs:
        return {
            "format_spec": LEGACY_FORMAT_SPEC,
            "chosen": None,
            "reason": reason,
            "max_source_height": cap,
            "super_resolution_allowed": allow_sr,
            "candidates": len(native),
        }
    return {
        "format_spec": "/".join([*specs, LEGACY_FORMAT_SPEC]),
        "chosen": {
            "format_id": chosen.get("format_id"),
            "height": chosen.get("height"),
            "width": chosen.get("width"),
            "vcodec": chosen.get("vcodec"),
            "tbr": chosen.get("tbr") or chosen.get("vbr"),
            "format_note": chosen.get("format_note") or None,
        },
        "reason": reason,
        "max_source_height": cap,
        "super_resolution_allowed": allow_sr,
        "candidates": len(native),
    }


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


def _run_metadata(base_cmd: list[str], clients: list[str]) -> tuple[dict, str, str]:
    """Look the video up once per client until one is served.

    Returns the metadata, the client that served it, and that attempt's stderr,
    so the download can try that client first and the caller can read warnings
    yt-dlp emitted on the way to a usable-looking answer.
    """
    last_err = ""
    for client in clients:
        cmd = [*base_cmd, *_player_client_args([client])]
        try:
            proc = subprocess.run(
                cmd, check=True, text=True, encoding="utf-8",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            return json.loads(proc.stdout), client, proc.stderr or ""
        except subprocess.CalledProcessError as exc:
            last_err = (exc.stderr or exc.stdout or "").strip()
            if not _is_bot_block(last_err):
                raise RuntimeError(f"yt-dlp metadata lookup failed:\n{last_err}") from exc
    raise _bot_error(clients)


_JS_RUNTIME_WARNING = "No supported JavaScript runtime could be found"


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
        info, served_by, warnings = _run_metadata(
            ["yt-dlp", *auth, "--dump-single-json", "--no-download", "--no-playlist", url],
            clients,
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp returned invalid metadata JSON; check yt-dlp/EJS configuration.") from exc

    vid = info.get("id", "video")
    target = out_dir / f"{vid}.%(ext)s"
    # Choose an explicit native format from the metadata rather than letting
    # ``bv*+ba/b`` fall through to a 640x360 muxed stream. The spec ends in the
    # legacy default, so a metadata list this code cannot read still downloads.
    # The choice is stashed on the metadata the caller already persists, so a
    # run that lands at 640x360 can be read back afterwards.
    choice = describe_source_choice(info)
    if choice["chosen"] is None and _JS_RUNTIME_WARNING in (warnings or ""):
        # A degraded player response is the usual reason a format list has
        # nothing usable in it, and it is fixable — install a JS runtime yt-dlp
        # accepts — so say so rather than reporting the empty list as though it
        # were a property of the video.
        choice["reason"] = (
            "yt-dlp reported no supported JavaScript runtime, so YouTube served "
            "a degraded player response and the format list has no usable "
            "adaptive stream; deferred to yt-dlp's own ranking. Installing a "
            "runtime it accepts (deno, or node >= 22) is likely to restore the "
            "higher resolutions"
        )
    info["_selekyt_source_choice"] = choice
    # Prefer the client that served the metadata: the wall is per-video, so the
    # client that answered the lookup is the one most likely to answer the
    # download. The remaining clients stay as fallbacks.
    download_clients = [served_by, *[c for c in clients if c != served_by]]
    _run_download(
        ["yt-dlp", *auth, "-f", choice["format_spec"], "--merge-output-format", "mp4", "-o", str(target), "--no-playlist", url],
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
