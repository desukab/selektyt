from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Callable

from engine.captions import slice_ass
from engine.edit_plan import retention_intervals
from engine.reframe import (LANDSCAPE_DELIVERY, VERTICAL_DELIVERY, landscape_filter,
                            vertical_reframe_filter)
from engine.reframe_scenes import is_composed
from models.core import Candidate
from providers.ffmpeg import ffmpeg_bin, ffprobe_bin


def require_ffmpeg() -> None:
    """Delegate to the shared resolver so a wheel with bundled ffmpeg works."""
    ffmpeg_bin()


def _ffmpeg() -> str:
    return ffmpeg_bin()


def _ffprobe() -> str:
    return ffprobe_bin()


# ── script coverage: the burn must not draw placeholder boxes ─────────────
#
# libass resolves the ASS's declared font through fontconfig and falls back to
# the ``.notdef`` glyph for every codepoint no installed font carries. That
# fallback still puts ink in the frame — a row of identical hollow rectangles,
# one per codepoint, combining marks included — so every measurement that asks
# "is there caption ink?" passes on a deliverable nobody can read. This was not
# hypothetical: the shipped Telugu corpus render was produced on a host with no
# Telugu font, and its cue band is 13 even-pitched boxes (verified against a
# burn of the same text with a deliberately-missing font, byte-identical).
#
# The only fix is at the source: refuse to burn text the host cannot draw.
_COVERED: set[int] | None = None
_TAGS = re.compile(r"\{[^}]*\}")
_SCRIPT_WORD = re.compile(r"^([A-Z][A-Za-z'-]+) ")


def _covered_codepoints() -> set[int] | None:
    """Every codepoint any installed font can draw, or ``None`` if unknown.

    fontconfig is the same resolver libass uses, so its answer is the right
    one. Without it there is no answer to give and the caller must not guess:
    ``None`` means "unchecked", not "covered". The scan is a single subprocess
    over the installed set and is memoised for the process.
    """
    global _COVERED
    if _COVERED is not None:
        return _COVERED
    fc_list = shutil.which("fc-list")
    if fc_list is None:
        return None
    try:
        proc = subprocess.run(
            [fc_list, "--format", "%{charset}\n"], capture_output=True,
            text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    covered: set[int] = set()
    for line in proc.stdout.splitlines():
        for item in line.split():
            # Each item is a hex codepoint or a closed hex range ("0c00-0c7f").
            lo, _, hi = item.partition("-")
            try:
                a, b = int(lo, 16), int(hi or lo, 16)
            except ValueError:
                continue
            covered.update(range(a, b + 1))
    _COVERED = covered
    return covered


def _script_name(cp: int) -> str:
    """A readable name for the block a codepoint belongs to.

    ``unicodedata`` gives "TELUGU LETTER NA", and the leading word is the
    script — enough to name the missing font family in the error without a
    table that would have to track every block.
    """
    try:
        name = unicodedata.name(chr(cp))
    except ValueError:
        return f"U+{cp:04X}"
    m = _SCRIPT_WORD.match(name)
    return f"{m.group(1)} (U+{cp:04X})" if m else f"U+{cp:04X}"


def uncovered_script(text: str) -> list[str]:
    """Scripts in ``text`` that no installed font can render.

    Empty means the burn will draw real glyphs (or that fontconfig is absent
    and the question cannot be answered, which the caller reports as
    "unchecked" rather than as a pass).
    """
    covered = _covered_codepoints()
    if covered is None:
        return []
    missing = sorted({cp for cp in (ord(c) for c in text) if cp not in covered})
    return [_script_name(cp) for cp in missing]


def _check_caption_coverage(ass: Path) -> None:
    """Refuse to burn captions the host's fonts cannot draw.

    Called for every burn, so it has to stay cheap: one fontconfig scan per
    process and a set lookup per codepoint.
    """
    covered = _covered_codepoints()
    if covered is None:
        # No fontconfig (a Windows ffmpeg build resolves through DirectWrite
        # instead). The burn may still be tofu, but this code cannot tell, and
        # failing here would break every render on a platform it cannot
        # diagnose. The question stays open rather than being answered wrong.
        return
    text = ""
    for line in ass.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        text += _TAGS.sub("", line.split(",", 9)[-1]).replace("\\N", " ")
    missing = sorted({cp for cp in (ord(c) for c in text) if cp not in covered})
    if not missing:
        return
    by_script: dict[str, int] = {}
    for cp in missing:
        by_script[_script_name(cp)] = by_script.get(_script_name(cp), 0) + 1
    detail = "; ".join(f"{name}: {n} codepoint(s)" for name, n in
                       sorted(by_script.items(), key=lambda kv: -kv[1])[:4])
    raise RuntimeError(
        f"the captions in {ass.name} contain script no installed font can "
        f"render — libass would draw placeholder boxes into the deliverable "
        f"instead of text, and the result looks like a successful burn while "
        f"being unreadable ({detail}). Install a font covering the script "
        f"(for example the 'fonts-noto-core' package, which provides Noto "
        f"Sans Telugu and the other Indic families) and re-render. "
        f"Refusing to ship captions nobody can read.")


def _frame_count(start: float, end: float, fps: float) -> tuple[int, float]:
    """Resolve a [start, end) interval to a whole frame count on ``fps``.

    The exported timeline stamps every cut on this same grid, so the render has
    to produce exactly that many frames or the deliverable and the interchange
    file disagree. Asking ffmpeg for ``-t 8.367`` does not do this: it emits
    every frame whose timestamp falls at or below the requested time, so an
    interval that is not a whole number of frames is rounded *up* to the next
    frame. One concatenated part does this once; a clip of N parts does it N
    times, and the overshoot grows with the cut count while the EDL stays on
    the grid. Measured on a two-part clip, 2.63 frames long. Deriving the count
    explicitly instead keeps a 15-part supercut frame-accurate against its own
    EDL, and the returned duration is what ``-t`` is set to so the two bounds
    cannot argue about which one wins.
    """
    n = int(round((end - start) * fps))
    # A degenerate interval still has to render something, and a negative one
    # would ask for a negative frame count.
    return max(1, n), max(1, n) / fps


def _has_audio(path: Path) -> bool:
    """Whether a file carries an audio stream at all.

    Used to decide whether a part that came out video-only is a defect or the
    faithful rendering of a video-only source. The distinction is what keeps
    the retry below from looping on a source that never had audio.
    """
    try:
        out = subprocess.run(
            [_ffprobe(), "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of",
             "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True, encoding="utf-8",
        )
        return bool(out.stdout.strip())
    except (subprocess.CalledProcessError, OSError):
        return False


def _audio_start(path: Path) -> float | None:
    """The presentation time of the first audio packet, or ``None`` if unknown.

    Input seeking (``-ss`` before ``-i``) on some containers does not drop the
    audio stream outright but shifts it: the demuxer seeks the video to the
    keyframe and the audio to its own packet nearest the target, and the two
    land at different presentation times. The part then carries a track that
    ffprobe is happy to report and that begins seconds after the picture — a
    deliverable that looks complete and opens on silence. Measured on the
    OOI AV1/Opus source, a 12.220 s seek produced a 19.052 s part whose audio
    started at 2.946 s, so the retry below was never taken: ``_has_audio`` saw
    a stream and stopped looking.
    """
    try:
        out = subprocess.run(
            [_ffprobe(), "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=start_time",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True, encoding="utf-8",
        )
        return float(out.stdout.strip())
    except (ValueError, subprocess.CalledProcessError, OSError):
        return None


# Audio this far behind the picture is a seek artefact rather than an edit. The
# part's own video starts at 0.0 by construction, so any positive audio start is
# sound the viewer does not hear at the beginning of the clip; a small
# container-level rounding is tolerated to keep the retry for genuine cases.
_AUDIO_START_TOLERANCE = 0.25


def _audio_is_unusable(path: Path) -> bool:
    """Whether a rendered part's audio is missing or starts after the picture.

    This is the retry's actual trigger. A part with no audio stream at all is
    one half of it; a part whose stream exists but starts late is the other,
    and ``_has_audio`` cannot see it, because the stream is present and
    decodable and simply begins in the wrong place.
    """
    if not _has_audio(path):
        return True
    start = _audio_start(path)
    if start is None:
        return False
    return start > _AUDIO_START_TOLERANCE


def _encoder_args() -> list[str]:
    """The x264 settings the deliverable is mastered with.

    These were chosen by measurement, not by taste. The ladder was scored against
    a -crf 0 reference of the same filter chain, on real corpus footage (the
    1280x720, 29.97 fps pentagon briefing), 240 frames per entry, in three
    independent 8 s windows starting at 0, 300 and 600 s, for both orientations
    the pipeline ships: the tracked 406x722 vertical crop and the full-frame
    1280x720 landscape. ``corpus/render/runs/pentagon-240f-sweep.json`` and
    ``pentagon-240f-16x9-sweep.json`` are the records.

    Read the absolute columns as *window* properties, not encoder ones. The same
    veryfast/crf20 entry scores 44.402 dB in one 9:16 window and 40.901 in
    another -- a 3.5 dB swing from content alone, which is wider than the gap
    between most of the entries being chosen between. A single-window table
    presented as an encoder property is exactly what the paragraph this replaced
    did, and its figures matched no committed record at all. Only the gap to the
    shipped entry, and the ratio of bitrate and wall clock, are stable across
    windows. The two orientations have their own ranges, because they encode
    3.14x different pixel counts:

        9:16, 406x722 (0.29 Mpx)
        entry          dB vs shipped   bitrate ratio   RTF ratio
        veryfast/crf20   -3.18 .. -2.42   0.644 .. 0.700   0.34 .. 0.36
        medium/crf20     -1.43 .. -1.16   0.776 .. 0.804   0.69 .. 0.70
        slow/crf20       -1.35 .. -1.15   0.761 .. 0.765   0.90 .. 0.92
        slow/crf18            shipped          1.0              1.0
        slow/crf16       +1.17 .. +1.40   1.298 .. 1.311   1.08 .. 1.11

        16:9, 1280x720 (0.92 Mpx)
        entry          dB vs shipped   bitrate ratio   RTF ratio
        veryfast/crf20   -3.76 .. -2.91   0.685 .. 0.719   0.27 .. 0.29
        medium/crf20     -1.49 .. -1.32   0.818 .. 0.825   0.66 .. 0.72
        slow/crf20       -1.37 .. -1.27   0.786 .. 0.792   0.89 .. 0.93
        slow/crf18            shipped          1.0              1.0
        slow/crf16       +1.24 .. +1.34   1.256 .. 1.276   1.07 .. 1.10

    veryfast/crf20, the old setting, is the lowest-quality entry in both tables:
    2.4-3.2 dB behind slow/crf18 at 9:16 and 2.9-3.8 dB at 16:9, which is a real
    softness on a phone-held Short rather than a chart artefact. It is also the
    cheapest entry in both, so the move to slow/crf18 is a deliberate quality
    purchase -- roughly +2.8 dB for +40-45% bitrate, and +1.6-1.9 s of wall clock
    on 8 s of 9:16 footage, tolerable on the Termux/ARM host the pipeline also
    runs on. crf16 buys another 1.2-1.4 dB for another 25-30% bitrate, past the
    point where the extra is worth carrying.

    One entry in those tables is not like the others: medium/crf20 lands within
    0.13 dB of slow/crf20 while costing 0.74-0.78 of its wall clock, and at 2-5%
    *higher* bitrate -- so the preset itself is rate-distortion dominated at a
    fixed crf on this footage, and buys nothing but wall clock. That raised
    a question worth answering, and it has been answered: medium/crf18 was swept
    against the shipped pair on the same three windows per orientation
    (corpus/render/runs/pentagon-240f-medium18.json and
    pentagon-240f-medium18-16x9.json).

    It is 30-55% faster, and it is not free. Within 0.03-0.15 dB of the shipped
    quality in every window, but at 2-5% *higher* bitrate -- medium is strictly
    dominated on the rate-distortion curve at this crf, and what it sells is wall
    clock, not bytes. So the shipped pair stays on slow: the gap is small enough
    that a batch service that needs throughput can take the 30-55% deliberately,
    and small enough that a product whose deliverables go to phones on cellular
    should not pay 2-5% more bytes for a PSNR delta no viewer will see. Neither
    side of that is measurable here -- PSNR is a proxy, and no human has rated
    either encode -- so the choice is left where a measurement ends and a product
    judgement begins, and a per-run speed toggle remains the honest way to expose
    it rather than a silent swap of a deliberate decision. The 30-55% range is
    not symmetric across orientation either: 9:16 is where the win is (34-55%),
    16:9 stays 30-40%, which is worth knowing before quoting one number at a
    customer whose mix is not this one.

    The speed range above is the per-window one, not the ratio of the two ``rtf``
    columns in a record: those are independent min/max bounds, so dividing one
    entry's fastest window by the other's slowest states a speedup no window
    showed (0.567 at 9:16, against the 0.646 the slowest paired window actually
    shows -- a 76% speedup invented from bounds that never co-occurred).
    ``rtf_ratio_vs_shipped`` in a sweep's per-window ``quality_vs_cost`` carries
    the paired figure; the two records above carry it, and
    tests/test_render.py re-derives the range from it and cross-checks it
    against the rows. A speedup is also 1/ratio - 1 and not 1 - ratio -- 0.646
    of the wall clock is 55% faster, not 35% -- and the paragraph above stated
    the smaller number until 2026-09-28 for exactly that reason.

    The resolution is a delivery contract, not a lever: the frame has to be the
    canvas the burned-in captions are laid out on (1080x1920 vertical,
    1920x1080 landscape, ``engine.reframe.VERTICAL_DELIVERY``), because libass
    scales the ASS layout onto the frame it is burned on -- the same cue
    measures 492x42 px at 1080x1920 and 185x16 px at 406x720. An earlier
    revision deliberately kept the source-preserving crop and let the player do
    that upscale, measuring the trade: pre-upscaling a 406x722 crop to 1080x1920
    before the encoder was genuinely *better* at that display size on this
    footage (PSNR 50.101 vs 47.347 dB, both compared at 1080x1920 against the
    same lossless reference) at 4266 kbps against 932 kbps and 5.1x the wall
    clock, for +2.75 dB. That is a real cost and it is still paid; what changed
    is that the alternative is not "the player upscales for free" but "the
    deliverable's captions are 16 px tall". The delivery canvas is the floor
    because the captions are unusable below it, not because the pixels are free.
    ``corpus/render/runs/pentagon-240f-upscaled.json`` is the record of the
    cost side, and it stays accurate as the cost side.
    """
    # yuv420p is the only plan every player and every platform accepts. ffmpeg
    # picks it for most sources anyway, but a 4:2:2 or 4:4:4 master (common for
    # pro footage) would otherwise carry its chroma subsampling into an MP4
    # that half the decoders in the world refuse to open.
    return ["-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p"]


def _aligned_ass(ass: Path, canvas: tuple[int, int]) -> Path:
    """The ASS to burn, with its declared canvas aligned to the frame.

    libass fits the layout the ASS declares in ``PlayResX``/``PlayResY`` into the
    frame it is burned on, scaling uniformly to fit, so a document authored for a
    1080x1920 canvas and burned onto a 1920x1080 frame renders every cue at 9/16
    of its designed size -- the portrait layout is fitted into a landscape frame,
    and the cues come out 22 px tall where they were authored at 40 (measured).
    The same document serves both orientations because one clip is rendered both
    ways, so the burn adapts a copy's header instead of the package's document:
    cues, timings and tags are copied verbatim, and the shipped ASS stays the
    editor's hand-edited file.

    The canvas asked for is the frame's *display* canvas rather than its pixel
    buffer: an anamorphic frame is fewer pixels but still displays 9:16, and the
    uniform scale keeps the cues' relative positions and sizes as authored.
    """
    text = ass.read_text(encoding="utf-8")
    have = {}
    for line in text.splitlines():
        for key, name in (("x", "PlayResX"), ("y", "PlayResY")):
            if line.startswith(f"{name}:"):
                try:
                    have[key] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
    if have.get("x") == canvas[0] and have.get("y") == canvas[1]:
        return ass
    if have.get("x") and have.get("y"):
        # Rewriting a header the document does not declare would invent a canvas
        # it was never laid out against, so only an ASS that states one is
        # realigned.
        rewritten = []
        for line in text.splitlines(keepends=True):
            if line.startswith("PlayResX:"):
                line = f"PlayResX: {canvas[0]}\n"
            elif line.startswith("PlayResY:"):
                line = f"PlayResY: {canvas[1]}\n"
            rewritten.append(line)
        text = "".join(rewritten)
    out = ass.parent / f".{ass.stem}.canvas{canvas[0]}x{canvas[1]}.ass"
    out.write_text(text, encoding="utf-8")
    return out


def _subtitle_terms(ass: Path | None) -> list[str]:
    """The ffmpeg filter terms that burn an ASS into the chain, or none.

    The escaping is load-bearing and easy to get wrong, so it lives in one place
    and is shared by the render and by the benchmark that records the filter the
    render actually used: a record whose graph omits the subtitles a deliverable
    carries is not reproducible from the record. The caller passes the document
    to burn -- for the render that is the canvas-aligned copy from
    :func:`_aligned_ass`, so the record names the shipped ASS and the burn reads
    the same cues at the frame's own canvas.
    """
    if not ass:
        return []
    font_dir = Path(__file__).resolve().parent / "assets" / "fonts"
    font_files = (font_dir / "Fredoka-Regular.ttf", font_dir / "Fredoka-Bold.ttf")
    if any(not path.is_file() for path in font_files):
        raise RuntimeError(f"bundled Fredoka caption fonts are missing from {font_dir}")

    def filter_path(path: Path) -> str:
        safe = path.resolve().as_posix().replace("'", "\\'")
        # A Windows drive letter ("C:/...") puts a bare ":" in the
        # filtergraph, which ffmpeg parses as the option separator.
        return safe.replace(":", "\\:")

    safe = filter_path(ass)
    # A Windows drive letter ("C:/...") puts a bare ":" in the filtergraph,
    # which ffmpeg parses as the option separator and then fails with
    # "Invalid argument". Escaping it as "\:" is the documented way to pass
    # a literal colon inside a filter value.
    return [f"subtitles='{safe}':fontsdir='{filter_path(font_dir)}'"]


def _render_one(source: Path, start: float, end: float, out: Path, ass: Path | None, vertical: bool, fps: float, threads: int | None = None) -> None:
    vf: list[str] = []
    if not vertical:
        # Landscape fits the whole source into an exact 16:9 frame, padding
        # rather than cropping: a 4:3 source becomes a 640x486 square-pixel
        # picture inside an 864x486 frame. Without this the deliverable
        # inherits the source's own ratio and a 4:3 NTSC tape ships labelled
        # 16:9, and nothing in the pipeline would ever notice.
        vf.append(landscape_filter(source))
    frames, span = _frame_count(start, end, fps)
    # The burned ASS is laid out on the canvas the delivered frame displays as,
    # because libass scales that layout to the frame: a mismatched canvas ships
    # the cues at a fraction of their authored size. ``_aligned_ass`` copies the
    # document only when its declared canvas disagrees, so a matching ASS is
    # burned as shipped and nothing is left to clean up.
    canvas = VERTICAL_DELIVERY if vertical else LANDSCAPE_DELIVERY
    burn_ass = _aligned_ass(ass, canvas) if ass else None
    sub = _subtitle_terms(burn_ass)
    if vertical:
        # The complete 9:16 reframe: a tracked crop, then a scale onto a frame
        # whose display ratio is 9:16 by construction. Scaling by width with an
        # even height instead leaves the crop's integer-rounding residue in the
        # deliverable, and a 3840x2160 source shipped 1080x1918 that way --
        # 0.1% from the ratio the package advertises.
        #
        # The subtitles are passed as a *tail* rather than appended here: a
        # speaker+slide scene is composed from several chains joined by
        # xfade/vstack and has no single outside to append to, so the
        # composition owns the tail and folds it into its own final chain.
        graph = vertical_reframe_filter(source, start, end, tail=tuple(sub))
        if is_composed(graph):
            # A SPLIT, or several scenes joined by a transition, is a labelled
            # multi-chain description ending in [v]. ffmpeg rejects that under
            # -vf ("Error initializing a simple filtergraph", measured), so it
            # runs through -filter_complex -- which also means the default
            # stream mapping stops applying and audio has to be mapped
            # explicitly or the part ships silent.
            video_opt = ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?"]
        else:
            vf.append(graph)
            video_opt = ["-vf", ",".join(vf)]
    else:
        vf.extend(sub)
        video_opt = ["-vf", ",".join(vf)]
    common = ["-frames:v", str(frames), "-t", f"{span:.6f}",
              *video_opt, "-c:v", "libx264", *_encoder_args(),
              # A same-settings encode produces a same-quality deliverable
              # whatever the thread count, so a fan-out run that hands each
              # ffmpeg process a fraction of the cores changes only wall
              # clock. Only a fan-out passes this; the solo path keeps x264's
              # own default so today's runs stay byte-for-byte what they were.
              *([] if threads is None else ["-threads", str(threads)]),
              "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]

    # Input seeking (``-ss`` before ``-i``) is the fast path: ffmpeg seeks
    # straight to the keyframe instead of decoding up to it. On some containers
    # that seek loses the audio stream outright — the demuxer discards every
    # audio packet whose timestamp precedes the seek target and then has
    # nothing left to emit, so the part is written video-only and no error is
    # reported. Reproduced on an AV1/Opus WebM where seeking past ~15 s dropped
    # the audio on 7 of 8 sampled positions while the container provably holds
    # audio throughout. An output-side deliverable with no sound is not a
    # warning-grade problem, so fall back to output seeking, which decodes and
    # discards and therefore cannot drop a stream.
    try:
        subprocess.run([_ffmpeg(), "-y", "-ss", f"{start:.3f}", "-i", str(source), *common],
                       check=True)
        if _audio_is_unusable(out) and _has_audio(source):
            subprocess.run([_ffmpeg(), "-y", "-i", str(source), "-ss", f"{start:.3f}", *common],
                           check=True)
    finally:
        # The canvas-aligned copy is burn-local: the shipped ASS is the package's
        # document and stays beside the deliverable, so the copy goes once the
        # part that needed it exists -- including when the encode failed and the
        # part never did, or a retry would find a stale header beside the master.
        if burn_ass is not None and burn_ass != ass:
            burn_ass.unlink(missing_ok=True)


def concat_renders(
    parts: list[Path], out: Path, *, part_durations: list[float] | None = None
) -> Path:
    """Join already-rendered parts into one file without re-encoding.

    The parts come from the same encoder with the same parameters — every one
    of them was written by ``_render_one`` — so the copy demuxer can stitch
    them at container level: no decode, no encode, no generation loss, and the
    output cannot differ from the parts it was made of. That last property is
    the reason the assembled program is concatenated rather than rendered a
    second time: a reel built this way *is* the clips, byte for byte, so
    nothing the run QA'd per clip can drift in the reel.

    A single part is copied rather than muxed, which is the same operation the
    demuxer would perform and one fewer ffmpeg invocation.
    """
    require_ffmpeg()
    if not parts:
        raise RuntimeError("concat_renders needs at least one part")
    out.parent.mkdir(parents=True, exist_ok=True)
    if part_durations is not None:
        if len(part_durations) != len(parts) or any(d <= 0 for d in part_durations):
            raise ValueError("part_durations must contain one positive duration per part")
        audio = [_has_audio(p) for p in parts]
        if any(present != audio[0] for present in audio[1:]):
            raise RuntimeError("program reel parts do not agree on whether audio is present")
        cmd = [_ffmpeg(), "-y"]
        for part in parts:
            cmd += ["-i", str(part)]
        filters: list[str] = []
        inputs: list[str] = []
        for i, duration in enumerate(part_durations):
            filters.append(
                f"[{i}:v:0]setpts=PTS-STARTPTS,setsar=1,trim=duration={duration:.9f},"
                f"setpts=PTS-STARTPTS[v{i}]"
            )
            inputs.append(f"[v{i}]")
            if audio[i]:
                filters.append(
                    f"[{i}:a:0]asetpts=PTS-STARTPTS,atrim=duration={duration:.9f},"
                    f"asetpts=PTS-STARTPTS[a{i}]"
                )
                inputs.append(f"[a{i}]")
        filters.append(
            f"{''.join(inputs)}concat=n={len(parts)}:v=1:a={1 if audio[0] else 0}"
            "[vout]" + ("[aout]" if audio[0] else "")
        )
        cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
        if audio[0]:
            cmd += ["-map", "[aout]"]
        cmd += ["-c:v", "libx264", *_encoder_args()]
        if audio[0]:
            cmd += ["-c:a", "aac", "-b:a", "160k"]
        cmd += ["-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True)
        return out
    if len(parts) == 1:
        shutil.copyfile(str(parts[0]), str(out))
        return out
    # The concat demuxer resolves relative entries against the concat file's
    # own directory, not the process CWD — CWD-relative paths get doubled and
    # the whole render fails. Entries must be absolute.
    tmp = out.parent / f".{out.stem}_concat"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        concat = tmp / "concat.txt"
        concat.write_text(
            "\n".join(f"file '{p.resolve().as_posix()}'" for p in parts),
            encoding="utf-8",
        )
        cmd = [_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(concat)]
        cmd += ["-c", "copy", "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def render_clip(source: Path, candidate: Candidate, out: Path, ass: Path | None = None, vertical: bool = False, fps: float = 30.0, progress: Callable[[dict], None] | None = None, threads: int | None = None) -> Path:
    require_ffmpeg(); out.parent.mkdir(parents=True, exist_ok=True)
    intervals = retention_intervals(candidate)
    if not intervals: raise RuntimeError("candidate has no renderable intervals")
    if ass is not None:
        _check_caption_coverage(ass)
    tmp = out.parent / f".{out.stem}_parts"; tmp.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, (start, end) in enumerate(intervals):
        part_ass = None
        if ass:
            if len(intervals) == 1:
                part_ass = ass
            else:
                # The shipped document is on the edited timeline, where part i
                # begins at the sum of the durations before it — not at this
                # interval's source time. Slicing it keeps an editor's cues and
                # tags and retargets only the times; regenerating from the
                # sub-candidate would discard them, and the shipped MP4 would
                # then disagree with the document beside it.
                offset = sum(b - a for a, b in intervals[:i])
                part_ass = tmp / f"{i:03d}.ass"
                part_ass.write_text(
                    slice_ass(ass.read_text(encoding="utf-8"),
                              offset, offset + (end - start)),
                    encoding="utf-8")
        part = tmp / f"{i:03d}.mp4"
        # Part granularity is the finest honest measure here: ffmpeg reports
        # ``time=`` on stderr, but these parts are seconds long, so a fraction
        # within one would move too fast to be worth reading and would need the
        # process's stderr piped rather than inherited. The part count is what
        # the render itself can count.
        if progress is not None:
            progress({"part": i, "parts": len(intervals), "vertical": vertical})
        _render_one(source, start, end, part, part_ass, vertical, fps,
                    threads=threads)
        parts.append(part)
        if progress is not None:
            progress({"part": i + 1, "parts": len(intervals), "vertical": vertical})
    if len(parts) == 1:
        shutil.move(str(parts[0]), str(out)); shutil.rmtree(tmp, ignore_errors=True); return out
    concat_renders(parts, out)
    shutil.rmtree(tmp, ignore_errors=True)
    return out
