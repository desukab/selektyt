"""The source-quality policy: which stream SeleKyT asks yt-dlp for.

This is the fix for a measured defect, not a hypothetical one. The baseline
Termux run produced six vertical outputs at ~202x360 and ~110-140 kbps of video,
and the source it was built from was 640x360 at ~65 kbps — for a video that also
offers native 854x480 and 1280x720. The deliverable inherited that one upstream
decision: 360 source lines become a 202-pixel-wide vertical crop, and CRF 20 on
a frame that small simply emits ~120 kbps. No encoder setting was wrong, and no
reframing bug was needed, to produce the numbers that were reported.

``bv*+ba/b`` does not lose to a better stream by ranking below it. It matches
*nothing* — because ffmpeg is not on PATH to mux the two halves, or because a
degraded player response hid the adaptive streams — and selection then falls to
``/b``, the best stream carrying both video and audio, which on YouTube is itag
18 at 640x360. The download succeeds and the file looks complete until it is
measured, so nothing reported the loss.

The policy below names an explicit format id instead. These tests pin the
choice against format lists modelled on the real one, rather than the network,
because the failure is invisible at download time and a regression would be
silent again.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import providers.youtube as youtube_mod
from providers.youtube import (
    LEGACY_FORMAT_SPEC,
    codec_family,
    describe_source_choice,
    download,
    is_super_resolution,
    select_format_spec,
)

# Everything these tests assume the host can decode. The real list is queried at
# runtime; fixing it here keeps the policy under test rather than the machine.
_DECODABLE = {"h264", "hevc", "mpeg4", "vp9", "av1", "aac", "mp3", "opus", "vorbis"}


@pytest.fixture(autouse=True)
def _fixed_decodable_codecs(monkeypatch):
    """The decoder list is a property of the host, not of the policy.

    ``download`` also runs yt-dlp as a stubbed child elsewhere in this suite, and
    that stub intercepts ``subprocess.run`` globally — including the ffmpeg query
    this layer memoises. Pinning both keeps a test's answer from depending on
    which other test ran first.
    """
    monkeypatch.setattr(youtube_mod, "decodable_codecs", lambda: _DECODABLE)
    monkeypatch.setattr(youtube_mod, "_DECODABLE_CODECS", set(_DECODABLE))


def _video(fmt_id, height, *, width=None, vcodec="avc1.4d401f", acodec="none", tbr=0,
            note=None):
    return {
        "format_id": fmt_id,
        "height": height,
        "width": width or int(height * 16 / 9),
        "vcodec": vcodec,
        "acodec": acodec,
        "tbr": tbr,
        "format_note": note,
    }


def _audio(fmt_id="140", *, acodec="mp4a.40.2", tbr=48):
    return {"format_id": fmt_id, "vcodec": "none", "acodec": acodec, "tbr": tbr}


# The ladder the baseline video actually offered: 360p, 480p and 720p, all
# video-only H.264, plus the muxed 360p stream that won last time.
REAL_LADDER = [
    _video("134", 360, tbr=68),
    _video("135", 480, tbr=99),
    _video("136", 720, tbr=191),
    _audio(),
    _video("18", 360, acodec="mp4a.40.2", tbr=54),
]


def _spec(formats, **kw):
    kw.setdefault("decodable", _DECODABLE)
    return select_format_spec({"formats": formats}, **kw)


def _choice(formats, **kw):
    kw.setdefault("decodable", _DECODABLE)
    return describe_source_choice({"formats": formats}, **kw)


# ── the reported defect ─────────────────────────────────────────────────────


def test_a_360_480_720_ladder_does_not_stop_at_360p():
    """The exact ladder the baseline run saw must resolve to its top rung.

    This is the failure the whole module exists for: the three heights were all
    available and the download still arrived at 640x360.
    """
    spec = _spec(REAL_LADDER)
    assert spec.split("/")[0] == "136+140", (
        f"the ladder offers 720p H.264 with an AAC half; the policy chose "
        f"{spec!r} instead. A 360p deliverable traces to this one choice."
    )


def test_the_chosen_stream_is_the_720p_one_not_the_360p_muxed():
    """The recorded choice and the spec's first arm must name the same stream."""
    choice = _choice(REAL_LADDER)
    assert choice["chosen"]["format_id"] == "136"
    assert choice["chosen"]["height"] == 720
    assert choice["format_spec"].split("/")[0].startswith("136"), (
        "the record of what was chosen must not drift from the spec that was sent"
    )
    assert choice["reason"] == "native 720p, the best decodable stream at or under 1080p"


def test_h264_is_preferred_over_vp9_and_av1_at_equal_resolution():
    """Phones decode H.264 in hardware and it muxes into MP4 untouched; at equal
    resolution that is worth more than a codec with a higher nominal bitrate."""
    formats = [
        _video("247", 720, vcodec="vp09.00.31.08", tbr=210),
        _video("398", 720, vcodec="av01.0.12M.08", tbr=207),
        _video("136", 720, tbr=191),
        _audio(),
    ]
    assert _spec(formats).split("/")[0] == "136+140"


def test_a_largest_native_stream_above_the_ceiling_is_taken_rather_than_refused():
    """The ceiling is a preference, not a refusal: a 1440p-only source still
    downloads, at the best height it has."""
    formats = [_video("264", 1440, tbr=800), _audio()]
    spec = _spec(formats, max_height=1080)
    assert spec.split("/")[0] == "264+140"
    assert "1440p" in _choice(formats, max_height=1080)["reason"]


def test_the_ceiling_is_honoured_when_a_native_stream_is_under_it():
    formats = [_video("136", 720, tbr=191), _video("137", 1080, tbr=400), _audio()]
    assert _spec(formats, max_height=720).split("/")[0] == "136+140"


# ── low-resolution fallback ──────────────────────────────────────────────────


def test_a_source_with_only_360p_takes_360p():
    """When the best there is *is* 360p, that is a property of the source, not a
    policy failure. The point is never to stop below the best available."""
    formats = [_video("134", 360, tbr=68), _audio(), _video("18", 360, acodec="mp4a.40.2", tbr=54)]
    spec = _spec(formats)
    assert spec.split("/")[0] == "134+140", (
        "a video-only 360p stream with a decodable audio half should still be "
        "merged rather than falling back to the lower-bitrate muxed one"
    )


def test_with_no_separate_audio_half_the_muxed_stream_is_taken():
    """A source with no audio-only stream cannot be improved by merging. The
    stream that already carries audio is the honest pick, and the policy must
    not offer a merge that would arrive silent."""
    formats = [_video("134", 360, tbr=68), _video("18", 360, acodec="mp4a.40.2", tbr=54)]
    assert _spec(formats).split("/")[0] == "18"


def test_a_source_with_only_a_low_muxed_stream_takes_it():
    """No adaptive streams at all: the muxed stream is the source, and joining
    is not needed."""
    formats = [_video("18", 360, acodec="mp4a.40.2", tbr=54)]
    assert _spec(formats).split("/")[0] == "18"


def test_a_source_with_no_usable_stream_defers_to_yt_dlp():
    """Unrecognisable metadata must not abort a run whose only problem is the
    format list. yt-dlp's own ranking is the floor, not an error."""
    assert _spec([{"format_id": "weird"}]) == LEGACY_FORMAT_SPEC
    assert _spec([]) == LEGACY_FORMAT_SPEC
    assert select_format_spec({}) == LEGACY_FORMAT_SPEC


def test_the_deferral_says_why_rather_than_failing_opaquely():
    """A 640x360 that arrives despite this policy has to be diagnosable from the
    state file alone."""
    choice = _choice([{"format_id": "weird", "height": 144}])
    assert choice["chosen"] is None
    assert choice["format_spec"] == LEGACY_FORMAT_SPEC
    assert "yt-dlp" in choice["reason"]


# ── AI-upscaled streams ─────────────────────────────────────────────────────


def _sr_ladder():
    return [
        _video("136", 720, tbr=191),
        _video("137-sr", 1080, tbr=400, note="AI-upscaled 1080p"),
        _audio(),
    ]


def test_an_ai_upscaled_stream_is_not_taken_by_default():
    """YouTube labels the streams it upscaled, and the label is machine-readable.
    An upscaled 1080p carries no more real detail than the 720p it was made
    from, and costs more bandwidth to prove it."""
    spec = _spec(_sr_ladder())
    assert spec.split("/")[0] == "136+140"
    assert "137-sr" not in spec, "an AI-upscaled stream must not appear in the spec"


def test_an_ai_upscaled_stream_is_taken_when_explicitly_allowed():
    """A source with no native alternative should still be reachable, but only by
    asking for it."""
    formats = [_video("137-sr", 1080, tbr=400, note="AI-upscaled 1080p"), _audio()]
    spec = _spec(formats, allow_super_resolution=True)
    assert spec.split("/")[0] == "137-sr+140"
    assert "AI-upscaled" in _choice(formats, allow_super_resolution=True)["reason"]


def test_native_still_wins_when_super_resolution_is_allowed():
    formats = _sr_ladder()
    assert _spec(formats, allow_super_resolution=True).split("/")[0] == "136+140"


@pytest.mark.parametrize("fmt,expected", [
    ({"format_id": "137-sr"}, True),
    ({"format_id": "137"}, False),
    ({"format_note": "AI-upscaled 1080p"}, True),
    ({"format_note": "1080p"}, False),
    ({}, False),
    ({"format_id": None, "format_note": None}, False),
])
def test_super_resolution_is_detected_from_either_marker(fmt, expected):
    """Either marker alone is enough, so a localised note or a changed id scheme
    does not silently re-admit upscaled streams as native resolution."""
    assert is_super_resolution(fmt) is expected


# ── decodability and merging ────────────────────────────────────────────────


def test_a_codec_the_host_cannot_decode_is_not_downloaded():
    """AV1 that this ffmpeg cannot decode is wasted bandwidth and a render that
    fails late, so it is a hard filter rather than a low preference."""
    formats = [
        _video("398", 720, vcodec="av01.0.12M.08", tbr=207),
        _video("136", 720, tbr=191),
        _audio(),
    ]
    spec = _spec(formats, decodable={"h264", "aac"})
    assert spec.split("/")[0] == "136+140"
    assert "398" not in spec


def test_audio_the_host_cannot_decode_falls_back_to_a_muxed_stream():
    """Without a usable audio half a video-only stream would arrive silent, which
    the render would describe as success. The muxed stream is the honest pick."""
    formats = [
        _video("136", 720, tbr=191),
        _audio(acodec="mp4a.40.2"),
        _video("18", 360, acodec="mp4a.40.2", tbr=54),
    ]
    spec = _spec(formats, decodable={"h264"})
    assert spec.split("/")[0] == "18"


def test_without_ffmpeg_the_halves_are_not_joined():
    """``bv*+ba/b``'s silent fallthrough begins when there is no muxer. Without
    ffmpeg only streams that already carry audio are offered, so the failure is
    a lower resolution, stated in the record, rather than a silent mismatch.

    Both lookup rungs are closed: ``shutil.which`` is the system one, and
    ``_static_ffmpeg_bin`` is the wheel's. A machine that has run an install
    once has the platform zip already unpacked, and leaving that rung open makes
    this test report "ffmpeg present" on a host where the format chooser would
    in fact have merged the halves."""
    import shutil

    import providers.ffmpeg as ffmpeg_mod

    formats = [
        _video("136", 720, tbr=191),
        _video("18", 360, acodec="mp4a.40.2", tbr=54),
    ]
    real_which = shutil.which
    real_bundled = ffmpeg_mod._static_ffmpeg_bin
    shutil.which = lambda name: None
    ffmpeg_mod._static_ffmpeg_bin = lambda name: None
    try:
        spec = _spec(formats)
        reason = _choice(formats)["reason"]
    finally:
        shutil.which = real_which
        ffmpeg_mod._static_ffmpeg_bin = real_bundled
    assert spec.split("/")[0] == "18", (
        "without a muxer a video-only stream must not be offered as a merge"
    )
    assert "ffmpeg is not on PATH" in reason


def test_with_ffmpeg_the_halves_are_joined():
    import shutil

    real_which = shutil.which

    class _Present:
        def __call__(self, name):
            return "/usr/bin/ffmpeg" if name == "ffmpeg" else real_which(name)

    shutil.which = _Present()
    try:
        assert _spec(REAL_LADDER).split("/")[0] == "136+140"
    finally:
        shutil.which = real_which


@pytest.mark.parametrize("codec,expected", [
    ("avc1.64001f", "h264"),
    ("avc1", "h264"),
    ("av01.0.12M.08", "av1"),
    ("vp09.00.31.08", "vp9"),
    ("mp4a.40.2", "aac"),
    ("hvc1.1.6.L120", "hevc"),
    ("none", None),
    ("", None),
])
def test_codec_ids_map_to_decoder_families(codec, expected):
    """ffmpeg names decoders by family; this is the spelling compared against its
    list. YouTube's AAC arrives as ``mp4a`` — the container, not the codec."""
    assert codec_family(codec) == expected


# ── resolved by yt-dlp's own engine, not by a model of it ──────────────────


# The format ladder the baseline run reported for the source video, with the
# fields yt-dlp needs to actually resolve a spec against it.
_LADDER_DEFS = [
    ("134", 360, 640, "avc1.4d401e", "none", 68),
    ("135", 480, 854, "avc1.4d401f", "none", 99),
    ("136", 720, 1280, "avc1.4d401f", "none", 191),
    ("247", 720, 1280, "vp09.00.31.08", "none", 210),
    ("398", 720, 1280, "av01.0.12M.08", "none", 207),
    ("137-sr", 1080, 1920, "avc1.640028", "none", 400),
    ("140", None, None, "none", "mp4a.40.2", 48),
    ("18", 360, 640, "avc1.42001e", "mp4a.40.2", 54),
]


def _resolvable_formats():
    return [
        {"format_id": fid, "height": h, "width": w, "vcodec": v, "acodec": a,
         "tbr": t, "ext": "mp4", "url": f"https://rr.example.com/{fid}",
         "protocol": "https"}
        for fid, h, w, v, a, t in _LADDER_DEFS
    ]


def _resolved_by_yt_dlp(spec):
    """What yt-dlp's own format selector picks for ``spec`` against the ladder.

    This is the strongest check available without the network: the spec is
    parsed and resolved by the same code that will resolve it at download time,
    so a spec this module generates that yt-dlp cannot read fails here rather
    than producing a silent fallthrough on a real run.
    """
    ytdlp = pytest.importorskip("yt_dlp")
    with ytdlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
        selector = ydl.build_format_selector(spec)
        ctx = {"formats": _resolvable_formats(),
               "has_merged_format": True, "incomplete_formats": False}
        return list(selector(ctx))


def test_the_legacy_spec_reproduces_the_reported_defect():
    """``bv*+ba/b`` on this ladder resolves to itag 18 at 640x360 through
    yt-dlp's own engine. This is the measured baseline, reproduced offline — the
    defect this module exists to fix, not an assumption about it."""
    picked = _resolved_by_yt_dlp(LEGACY_FORMAT_SPEC)
    assert [f["format_id"] for f in picked] == ["18"], (
        "the legacy spec no longer falls through to itag 18 on this ladder; if "
        "that changed, the defect's reproduction moved and this test must move "
        "with it"
    )
    assert max(f["height"] for f in picked) == 360


def test_the_new_spec_resolves_to_720p_through_yt_dlp():
    """The spec the policy emits must resolve, through yt-dlp itself, to the
    720p stream the ladder actually offers."""
    spec = _spec([{"format_id": f[0], "height": f[1], "width": f[2], "vcodec": f[3],
                   "acodec": f[4], "tbr": f[5]} for f in _LADDER_DEFS])
    picked = _resolved_by_yt_dlp(spec)
    ids = [f["format_id"] for f in picked]
    assert ids == ["136+140"], f"yt-dlp resolved {spec!r} to {ids}"
    assert max(f["height"] for f in picked) == 720


# ── the height override ─────────────────────────────────────────────────────


def test_the_height_ceiling_is_configurable(monkeypatch):
    monkeypatch.setenv("SELEKYT_MAX_SOURCE_HEIGHT", "480")
    assert _spec(REAL_LADDER).split("/")[0] == "135+140"


def test_an_absurd_height_override_is_ignored(monkeypatch):
    """A ceiling below any real resolution is a refusal to download, not a
    policy, so it falls back to the default rather than being honoured."""
    for bad in ("0", "-720", "seven", "143"):
        monkeypatch.setenv("SELEKYT_MAX_SOURCE_HEIGHT", bad)
        assert _spec(REAL_LADDER).split("/")[0] == "136+140", (
            f"{bad!r} must not become a max height"
        )


def test_super_resolution_opt_in_is_env_configurable(monkeypatch):
    monkeypatch.setenv("SELEKYT_ALLOW_SUPER_RESOLUTION", "1")
    formats = [_video("137-sr", 1080, tbr=400, note="AI-upscaled 1080p"), _audio()]
    assert _spec(formats).split("/")[0] == "137-sr+140"


def test_super_resolution_opt_in_rejects_garbage(monkeypatch):
    """Only an explicit yes opts in; anything ambiguous stays on the safe side."""
    for raw in ("no", "maybe", "", "0"):
        monkeypatch.setenv("SELEKYT_ALLOW_SUPER_RESOLUTION", raw)
        formats = [_video("137-sr", 1080, tbr=400, note="AI-upscaled 1080p"), _audio()]
        assert _spec(formats) == LEGACY_FORMAT_SPEC, (
            f"{raw!r} must not admit an AI-upscaled stream"
        )


# ── wiring into download ────────────────────────────────────────────────────


def _capturing_stub(monkeypatch, formats, *, stderr=""):
    """Serve real metadata, capture the ``-f`` argument, write a dummy file."""
    captured: dict = {}

    def fake_run(cmd, *args, **kw):
        if "--dump-single-json" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"id": "v", "formats": formats}),
                stderr=stderr)
        captured["args"] = cmd
        target = next((a for a in cmd if "%(ext)s" in str(a)), None)
        if target is not None:
            Path(str(target).replace("%(ext)s", "mp4")).write_bytes(b"0" * 1024)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(youtube_mod, "_cookie_args", lambda c: [])
    monkeypatch.setattr(youtube_mod, "_runtime_args", lambda: [])
    monkeypatch.setattr(youtube_mod, "require", lambda name: None)
    return captured


def test_download_sends_the_chosen_spec_not_the_legacy_default(tmp_path, monkeypatch):
    """The policy has to reach the actual ``-f`` argument or it is commentary."""
    captured = _capturing_stub(monkeypatch, REAL_LADDER)
    media, info = download("https://youtu.be/fxbCHn6gE3U", tmp_path / "src")

    assert media.suffix == ".mp4"
    f = captured["args"][captured["args"].index("-f") + 1]
    assert f.split("/")[0] == "136+140", (
        f"download asked for {f!r}; the 720p stream was available"
    )
    assert f.endswith(LEGACY_FORMAT_SPEC), (
        "the spec must end in yt-dlp's own default so unrecognisable metadata "
        "still downloads"
    )


def test_download_records_the_choice_on_the_metadata_it_returns(tmp_path, monkeypatch):
    """The choice is written to the project state inside ``media_info``, so the
    next 640x360 is diagnosable instead of invisible."""
    _capturing_stub(monkeypatch, REAL_LADDER)
    _media, info = download("https://youtu.be/fxbCHn6gE3U", tmp_path / "src")

    choice = info["_selekyt_source_choice"]
    assert choice["chosen"]["height"] == 720
    assert choice["max_source_height"] == 1080
    assert choice["super_resolution_allowed"] is False
    # The record carries no credential material of its own.
    assert "cookie" not in json.dumps(choice).lower()
    # And it survives the scrub the pipeline applies before persisting, which
    # drops keys by name rather than copying an allowlist.
    from selekyt.pipeline import _scrub_credentials

    assert "_selekyt_source_choice" in _scrub_credentials(info)


def test_an_unusable_format_list_plus_a_missing_js_runtime_is_diagnosed(
        tmp_path, monkeypatch):
    """The Termux baseline's most likely cause is a degraded player response.

    Without a JavaScript runtime yt-dlp cannot decipher the player response, and
    YouTube then serves a ladder with no adaptive streams at all — which is
    exactly the shape that lets itag 18 win. The download still succeeds, so the
    state file is the only place the cause can be read from. It has to name the
    fix (install a runtime) rather than reporting the empty list as though it
    were a property of the video.
    """
    warning = ("WARNING: No supported JavaScript runtime could be found in "
               "$PATH, and no bundled build is available. Please install deno, "
               "or node.js >= 22")
    _capturing_stub(monkeypatch, [{"format_id": "weird"}], stderr=warning)
    _media, info = download("https://youtu.be/fxbCHn6gE3U", tmp_path / "src")

    choice = info["_selekyt_source_choice"]
    assert choice["chosen"] is None, (
        "a list with no decodable stream must not claim to have chosen one"
    )
    assert choice["format_spec"] == LEGACY_FORMAT_SPEC
    assert "JavaScript runtime" in choice["reason"], (
        "the record must say the metadata was degraded, not that the video is "
        f"low-resolution; got {choice['reason']!r}"
    )
    assert "deno" in choice["reason"]
    assert "node >= 22" in choice["reason"], (
        "the reason must name the fix, not just the symptom"
    )


def test_a_missing_js_runtime_is_not_blamed_when_a_stream_was_chosen(
        tmp_path, monkeypatch):
    """The diagnostic must not overwrite an honest success. A ladder with a real
    720p stream is chosen for what it is even when the runtime warning was
    emitted alongside it."""
    warning = "WARNING: No supported JavaScript runtime could be found in $PATH"
    _capturing_stub(monkeypatch, REAL_LADDER, stderr=warning)
    _media, info = download("https://youtu.be/fxbCHn6gE3U", tmp_path / "src")

    choice = info["_selekyt_source_choice"]
    assert choice["chosen"]["height"] == 720
    assert "JavaScript runtime" not in choice["reason"], (
        "a successful choice must keep its own reason; the runtime warning is "
        "only the explanation for a list with nothing in it"
    )
