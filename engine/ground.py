"""GROUND: turn retrieved temporal intervals into decoded source frames.

Why this module exists
----------------------

``engine.retrieval`` answers a query with a union of temporal intervals, and
that is where it deliberately stops: it reads the transcript, it never looks at
a pixel, and its ``Evidence`` is TEXT. ``docs/retrieval-design.md`` §6.1 makes
the handoff explicit — retrieval hands over the union and GROUND grounds exactly
that union, no more, because decode is the cost.

The VISUAL adapters in ``engine.memory.ingest`` look like they already do this,
and they do not. They are *translators*: they turn the per-window numbers that
``engine.reframe`` already produced into ``Evidence``, and every one of them
takes a mandatory ``offset`` because reframe reports seconds relative to the
window it analysed. Nothing in the repo decodes an *arbitrary* interval a caller
asked for. That is the layer this module is.

What GROUND does
----------------

1. takes the intervals it is given — a ``RetrievalResult``, an ``IntervalSet``,
   an iterable of ``Interval``, or raw ``(start, end)`` pairs;
2. validates each one and reports the ones it will not ground by name, rather
   than silently repairing them (an inverted span is a caller bug and a span
   past the end of the source is a stale index);
3. clamps what survives to the media duration and merges overlaps, so two
   retrieval windows that touch become one decode;
4. decodes only those windows — one ffmpeg invocation per frame, input-side
   seek, so a full pass over the source never happens;
5. emits ``Modality.VISUAL`` ``Evidence`` through the contracts the rest of the
   repo already uses, with ``artifacts`` pointing at the frame files.

What GROUND does *not* do
-------------------------

A decoded frame is not an interpretation. Nothing here says a person is on
screen, that a slide changed, or that a shot is interesting; the claims assert
*existence and time* only — "3 sampled visual observations exist in 1.000-3.000s,
at 1.000s, 1.500s and 2.000s" — and the rationale says in so many words that no
interpretation was performed. Semantic reading is a later stage (VERIFY) that
consumes these frames, and labelling frames as "observations" now is what keeps
that stage honest: it cannot inherit a meaning this stage never established.

Purity and determinism
----------------------

Sampling is a pure function of the interval bounds: beginning, end and middle,
in that priority, deduplicated by a minimum gap (two sample times inside one
frame period are the same frame and are sampled once), capped by ``max_frames``.
The same intervals and the same media always yield the same times, so a test
can pin them.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.memory.evidence import Evidence, Modality, Source
from engine.memory.intervals import MERGE_EPS, Interval, IntervalSet
from providers.ffmpeg import (
    FfmpegError,
    probe_duration,
    probe_fps,
    require_ffmpeg,
    run_capture,
)

# ── Sampling policy ───────────────────────────────────────────────────────────

#: Default frame budget per interval: beginning, end and middle.
MAX_FRAMES_DEFAULT = 3

#: Two sample times closer than this are the same frame and are deduplicated.
#: ~40 ms, the frame period at 25 fps, so a sub-frame interval collapses to one
#: sample instead of three writes of one identical frame.
MIN_SAMPLE_GAP_S = 0.04

#: ffmpeg quality for the extracted JPEGs. High but lossy: a VERIFY stage that
#: needs pixel fidelity will re-decode from the source rather than trust these.
JPEG_QUALITY = 3

#: Per-frame decode budget. Input-side seek makes this cheap; a window that
#: cannot produce a frame in this time is a reported failure, not a wait.
DECODE_TIMEOUT_S = 60.0

#: Schema of the persisted ``GroundResult``. Bumped only when the on-disk
#: shape changes in a way an old reader cannot honour.
SCHEMA_VERSION = "1.0"


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SampledFrame:
    """One decoded frame and the exact source time it was requested at.

    ``time`` is the time GROUND asked ffmpeg for, in source seconds — the
    deterministic quantity derived from the interval bounds, not a
    decode-dependent one. ffmpeg's accurate input-side seek lands on the frame
    at or after that instant, so the frame in ``path`` may be a frame period
    later than ``time``; recording the requested time keeps the value
    reproducible, and the caveat is named in every rationale this module writes.
    """

    time: float
    width: int
    height: int
    path: str

    def to_dict(self) -> dict:
        return {
            "time": self.time,
            "width": self.width,
            "height": self.height,
            "path": self.path,
        }

    @staticmethod
    def from_dict(d: dict) -> SampledFrame:
        return SampledFrame(
            time=float(d["time"]),
            width=int(d.get("width", 0)),
            height=int(d.get("height", 0)),
            path=str(d.get("path", "")),
        )


@dataclass(frozen=True)
class GroundedInterval:
    """One interval after it has been physically grounded into the source.

    ``requested`` is what the caller asked for and ``start``/``end`` are what
    was actually decoded, so a clamp is visible on the object rather than
    discoverable only by diffing against the input. ``failures`` holds the
    per-frame decode errors, named per frame, because an interval that decoded
    two of three frames is neither a success nor a total failure.
    """

    start: float
    end: float
    requested: tuple[float, float]
    frames: tuple[SampledFrame, ...] = ()
    clamped: bool = False
    note: str = ""
    failures: tuple[str, ...] = ()

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def is_empty(self) -> bool:
        return not self.frames

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "requested": list(self.requested),
            "clamped": self.clamped,
            "note": self.note,
            "frames": [f.to_dict() for f in self.frames],
            "failures": list(self.failures),
        }

    @staticmethod
    def from_dict(d: dict) -> GroundedInterval:
        return GroundedInterval(
            start=float(d["start"]),
            end=float(d["end"]),
            requested=(float(d["requested"][0]), float(d["requested"][1])),
            frames=tuple(SampledFrame.from_dict(f) for f in d.get("frames", [])),
            clamped=bool(d.get("clamped", False)),
            note=str(d.get("note", "")),
            failures=tuple(str(x) for x in d.get("failures", [])),
        )


@dataclass(frozen=True)
class GroundMetrics:
    """What grounding actually cost, measured rather than estimated.

    ``requested_duration_s`` is the sum of the *merged* interval durations — the
    decode budget actually spent — while ``source_duration_s`` is the whole
    media. Their ratio is the claim targeted decode makes: GROUND reads the
    fraction, never the whole.
    """

    source_duration_s: float
    requested_duration_s: float
    decoded_frames: int
    elapsed_s: float
    decoder: str = "ffmpeg"

    def to_dict(self) -> dict:
        return {
            "source_duration_s": self.source_duration_s,
            "requested_duration_s": self.requested_duration_s,
            "decoded_frames": self.decoded_frames,
            "elapsed_s": self.elapsed_s,
            "decoder": self.decoder,
        }


@dataclass(frozen=True)
class GroundResult:
    """The answer to one ``ground`` call: evidence plus its audit trail.

    ``failures`` is a list of human-readable lines naming the interval and the
    reason, and ``metrics`` is the measured cost. An empty ``evidence`` with an
    empty ``failures`` is a genuine "nothing was asked for" (or an empty
    retrieval answer); an empty ``evidence`` with entries in ``failures`` is a
    failed decode and the two must never be conflated.
    """

    evidence: tuple[Evidence, ...] = ()
    intervals: tuple[GroundedInterval, ...] = ()
    failures: tuple[str, ...] = ()
    metrics: GroundMetrics | None = None
    report: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.evidence

    @property
    def n_frames(self) -> int:
        return sum(len(g.frames) for g in self.intervals)

    def as_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "evidence": [e.to_dict() for e in self.evidence],
            "intervals": [g.to_dict() for g in self.intervals],
            "failures": list(self.failures),
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "report": self.report,
        }

    def save(self, path: Path | str) -> Path:
        """Write the full result as JSON, so a run artifact can be reloaded."""
        import json

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(self.as_dict(), indent=2, sort_keys=True))
        tmp.replace(out)
        return out

    @staticmethod
    def from_dict(d: dict) -> GroundResult:
        return GroundResult(
            evidence=tuple(Evidence.from_dict(e) for e in d.get("evidence", [])),
            intervals=tuple(
                GroundedInterval.from_dict(g) for g in d.get("intervals", [])
            ),
            failures=tuple(str(x) for x in d.get("failures", [])),
            metrics=(
                GroundMetrics(**d["metrics"]) if d.get("metrics") else None
            ),
            report=str(d.get("report", "")),
        )

    @staticmethod
    def load(path: Path | str) -> GroundResult:
        import json

        raw = json.loads(Path(path).read_text())
        schema = raw.get("schema")
        if schema != SCHEMA_VERSION:
            raise ValueError(
                f"ground result at {path} is schema {schema!r}, expected "
                f"{SCHEMA_VERSION!r}; refusing to read it as a compatible result"
            )
        return GroundResult.from_dict(raw)


# ── Sampling policy ───────────────────────────────────────────────────────────


def sample_times(start: float, end: float, *, max_frames: int = MAX_FRAMES_DEFAULT) -> tuple[
    tuple[float, ...], str
]:
    """The source times to sample inside ``[start, end]``, and why.

    Candidates are the beginning, the end and the middle, considered in that
    order: the two endpoints bound the interval and the middle is the extra, so
    a caller who asks for two frames gets the extent rather than an opening and
    a midpoint. Candidates closer than ``MIN_SAMPLE_GAP_S`` to one already kept
    are the same frame and are dropped — the deduplication that keeps a very
    short interval from producing three writes of one frame.

    Returns the kept times in temporal order, plus a note naming the policy
    decision made, so ``GroundedInterval.note`` records the reasoning rather
    than just the outcome.
    """
    if max_frames < 1:
        raise ValueError(
            f"max_frames must be at least 1 (got {max_frames}); a ground call "
            "that samples nothing is a caller bug"
        )
    if end <= start:
        return (start,), "zero-length interval sampled once, at its start"
    candidates = (
        (start, "beginning"),
        (end, "end"),
        (0.5 * (start + end), "middle"),
    )
    kept: list[float] = []
    kept_names: list[str] = []
    dropped: list[str] = []
    capped = False
    for when, name in candidates:
        if kept and min(abs(when - k) for k in kept) < MIN_SAMPLE_GAP_S:
            dropped.append(name)
            continue
        kept.append(when)
        kept_names.append(name)
        if len(kept) >= max_frames:
            capped = True
            break
    kept.sort()
    why = f"sampled {', '.join(kept_names)}"
    if dropped:
        why += (
            f"; dropped {', '.join(dropped)} within {MIN_SAMPLE_GAP_S:g}s of a "
            "kept sample"
        )
    if capped and len(kept) < 3:
        why += f"; capped at the {max_frames}-frame budget"
    return tuple(kept), why


def fit_to_stream(
    times: tuple[float, ...], total: float, fps: float
) -> tuple[tuple[float, ...], str]:
    """Pull sample times back inside the decodable range of the stream.

    ``sample_times`` is pure in the interval bounds, which is what makes it
    testable; it does not know anything about the media. The last frame of a
    stream is at ``total - 1/fps``, so a sample requested at exactly ``total``
    — the common case for an interval clamped to the source end — asks ffmpeg
    for a frame *after* the last one and decodes nothing. Guarding by half a
    frame period is not enough on a slow source; a frame and a half always lands
    on the final frame, since ``ceil((total - 1.5/fps) * fps) == n_frames - 1``.

    Interior samples are untouched — a time well inside the stream always has a
    frame at or after it. Returns the bounded times and a note when any moved.
    """
    if not (math.isfinite(total) and total > 0.0) or not (math.isfinite(fps) and fps > 0.0):
        return times, ""
    last = total - 1.5 / fps
    if last <= 0.0:
        return times, ""
    moved: list[float] = []
    out = []
    for when in times:
        bounded = min(max(when, 0.0), last)
        if bounded != when:
            moved.append(when)
        out.append(bounded)
    note = (
        f"pulled {', '.join(f'{m:.3f}s' for m in moved)} inside the last "
        f"decodable frame ({last:.3f}s)"
        if moved
        else ""
    )
    return tuple(out), note


# ── Interval validation and normalisation ─────────────────────────────────────

def _pairs(requested: Any) -> tuple[list[tuple[float, float]], list[str]]:
    """Pull ``(start, end)`` pairs out of whatever shape the caller passed.

    Accepts a ``RetrievalResult`` (its ``intervals`` are the answer of record),
    an ``IntervalSet``, an iterable of ``Interval``, or raw pairs. Returns the
    pairs plus rejection reasons — a shape this module cannot read is reported
    rather than coerced, because an empty result read from a mis-typed argument
    would look like "nothing matched".
    """
    if hasattr(requested, "intervals"):
        items = list(requested.intervals)
    elif isinstance(requested, IntervalSet):
        items = list(requested)
    elif isinstance(requested, Interval):
        items = [requested]
    elif isinstance(requested, (tuple, list)) and requested and isinstance(
        requested[0], (int, float)
    ):
        # A single bare pair, which is ambiguous with an interval list only if
        # the caller passes numbers; take it as one interval.
        items = [requested]
    else:
        items = list(requested) if requested is not None else []

    pairs: list[tuple[float, float]] = []
    rejected: list[str] = []
    for item in items:
        if isinstance(item, Interval):
            a, b = float(item.start), float(item.end)
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            try:
                a, b = float(item[0]), float(item[1])
            except (TypeError, ValueError):
                rejected.append(f"interval {item!r} rejected: not a pair of numbers")
                continue
        else:
            rejected.append(
                f"interval {item!r} rejected: not an interval or a (start, end) pair"
            )
            continue
        if not (math.isfinite(a) and math.isfinite(b)):
            rejected.append(f"interval {a}..{b}s rejected: not finite")
        elif a < 0.0:
            rejected.append(
                f"interval {a:g}..{b:g}s rejected: starts before the start of the source"
            )
        elif b <= a:
            rejected.append(f"interval {a:g}..{b:g}s rejected: zero or negative length")
        else:
            pairs.append((a, b))
    return pairs, rejected


def _clamp(pair: tuple[float, float], total: float) -> tuple[
    tuple[float, float] | None, bool, str
]:
    """Fit one interval inside ``[0, total]``.

    Returns the clamped pair (or ``None`` to reject), whether it was shortened,
    and a reason. An interval that starts past the end is rejected outright —
    it is the signature of a stale index over a re-trimmed source — while one
    that merely runs over is shortened and flagged, because the part that exists
    is still groundable.
    """
    start, end = pair
    if not (math.isfinite(total) and total > 0.0):
        return pair, False, ""
    if start >= total:
        return None, False, f"starts at {start:g}s, at or past the end of a {total:g}s source"
    if end > total:
        return (start, total), True, f"clamped end {end:g}s to the {total:g}s source duration"
    return pair, False, ""


# ── Decoding ──────────────────────────────────────────────────────────────────


def _jpeg_size(path: Path) -> tuple[int, int]:
    """Read width and height out of a JPEG header.

    Rather than decoding the image (or paying for a second process), this walks
    the frame markers to the first SOF segment. The frames exist to be opened
    by an editor or a later stage; GROUND only needs their dimensions for its
    own provenance, and reading 30 bytes of header is enough.
    """
    data = path.read_bytes()
    if data[:2] != b"\xff\xd8":
        raise ValueError(f"{path} is not a JPEG")
    i = 2
    while i + 9 <= len(data):
        if data[i] != 0xFF:
            # Not at a marker: resync one byte at a time.
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xD9:
            break
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[i + 5 : i + 7], "big")
            width = int.from_bytes(data[i + 7 : i + 9], "big")
            return width, height
        if length < 2:
            break
        i += 2 + length
    raise ValueError(f"no SOF marker found in {path}; cannot read its dimensions")


def decode_frame(video: Path, when: float, out_path: Path) -> tuple[int, int] | None:
    """Decode the single frame at ``when`` in ``video`` into ``out_path``.

    One invocation, one frame, input-side seek. ffmpeg lands on the keyframe at
    or before the target and decodes forward to it, so only that short run of
    frames is read — the rest of the source is never touched, which is the cost
    property §4 of ``docs/ground-design.md`` measures. Returns ``(width,
    height)`` on success or ``None`` if no frame was produced — the caller
    reports the miss rather than treating it as an absence of visual content.

    ``when`` is formatted to microseconds so repeated calls reproduce the same
    seek instead of drifting on float formatting.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        f"{when:.6f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-q:v",
        str(JPEG_QUALITY),
        str(out_path),
    ]
    try:
        run_capture(
            command,
            stage="ground",
            source=str(video),
            target=str(out_path),
            timeout=DECODE_TIMEOUT_S,
        )
    except (FfmpegError, OSError, RuntimeError):
        return None
    if not out_path.is_file() or out_path.stat().st_size == 0:
        return None
    try:
        return _jpeg_size(out_path)
    except ValueError:
        return None


# ── Evidence construction ─────────────────────────────────────────────────────


def _observation_claim(times: tuple[float, ...], start: float, end: float) -> str:
    """The claim GROUND is willing to make about a decoded window.

    Existence and time, nothing else. The word "observation" is deliberate: a
    claim that a frame *exists* is checkable by opening the artifact, while a
    claim about what the frame *shows* would need a vision model this stage
    does not have.
    """
    n = len(times)
    if n == 0:
        return "no frames were sampled from this interval"
    stamp = ", ".join(f"{t:.3f}s" for t in times)
    return (
        f"{n} sampled visual observation{'s' if n != 1 else ''} exist in "
        f"{start:.3f}-{end:.3f}s, at {stamp}"
    )


def _observation_rationale(
    grounded: GroundedInterval,
    media: Path,
    params_digest: str,
    verdict: str = "",
) -> str:
    """Why the observation evidence is what it is, and what it is *not*."""
    frames = "; ".join(
        f"{f.time:.3f}s -> {f.width}x{f.height} {Path(f.path).name}" for f in grounded.frames
    )
    why = (
        f"Decoded {len(grounded.frames)} frame(s) from {media} by input-side ffmpeg "
        f"seek only (params digest {params_digest}); requested window "
        f"{grounded.requested[0]:.3f}-{grounded.requested[1]:.3f}s, grounded as "
        f"{grounded.start:.3f}-{grounded.end:.3f}s"
        f"{' (clamped)' if grounded.clamped else ''}; {grounded.note}. "
        f"This is a sampled visual observation, not an interpretation: no vision "
        f"model has evaluated these frames, so nothing about their content is "
        f"asserted. ffmpeg's accurate seek lands on the frame at or after the "
        f"requested instant, so each frame may be up to one frame period later "
        f"than the timestamp recorded."
    )
    if verdict:
        # The confidence on this evidence is for the decode, not for the
        # retrieval answer that selected the window. That answer's own verdict —
        # the transcript's fitness, the channels it could not consult — travels
        # here so a consumer cannot read a confident decode as endorsement of a
        # window chosen over a poor transcript (retrieval-design.md §6.1).
        sentence = verdict.rstrip()
        if sentence and sentence[-1] not in ".!?":
            sentence += "."
        why += (
            f" The confidence on this evidence covers the decode only; the "
            f"retrieval answer that selected these windows reports: {sentence}"
        )
    return why + f" Frames: {frames}"


# ── The stage ─────────────────────────────────────────────────────────────────


def ground(
    requested: Any,
    media: Path | str,
    *,
    out_dir: Path | str,
    max_frames: int = MAX_FRAMES_DEFAULT,
    duration: float | None = None,
) -> GroundResult:
    """Ground retrieved intervals into decoded source frames.

    Parameters
    ----------
    requested:
        The intervals to ground: a ``RetrievalResult``, an ``IntervalSet``, an
        iterable of ``Interval``, or ``(start, end)`` pairs. GROUND grounds
        exactly this set — it never expands the answer, because widening a
        window is a retrieval decision that costs decode (§6.1).
    media:
        The source media to decode.
    out_dir:
        Where to write the frame JPEGs. Created if absent.
    max_frames:
        Frame budget per interval, applied after deduplication.
    duration:
        Media duration in seconds. Probed from ``media`` when omitted; passing
        it explicitly avoids a probe and pins the clamp bounds in a test.

    Returns
    -------
    GroundResult
        Evidence per grounded interval, the grounded intervals themselves, the
        named failures, and the measured cost.

    Raises
    ------
    ValueError
        If ``max_frames`` is less than 1, which is a caller bug.
    """
    started = time.perf_counter()
    if max_frames < 1:
        raise ValueError(
            f"max_frames must be at least 1 (got {max_frames}); a ground call "
            "that samples nothing is a caller bug"
        )
    video = Path(media)
    out_dir = Path(out_dir)

    # When the caller passes the retrieval answer itself, its report — the
    # transcript's fitness verdict and the channels it could not consult —
    # travels onto the evidence GROUND produces (retrieval-design.md §6.1).
    retrieval_report = getattr(requested, "report", "") or ""

    pairs, rejected = _pairs(requested)
    failures = list(rejected)

    # Duration and frame rate: probe them unless the caller pinned the
    # duration. A probe failure is the signature of a missing or unreadable
    # source and is reported once, here, rather than once per interval. The
    # frame rate is not an input the caller supplies — it is a property of the
    # stream, needed only to keep an end-of-source sample decodable.
    probe_failure = ""
    total = None
    fps = 0.0
    if duration is not None:
        total = float(duration)
    else:
        try:
            total = probe_duration(video)
        except (FfmpegError, OSError, RuntimeError) as exc:
            probe_failure = f"{type(exc).__name__}: {exc}"
    if total is not None:
        try:
            fps = probe_fps(video)
        except (FfmpegError, OSError, RuntimeError):
            # A source with no readable frame rate keeps the guard off; an end
            # sample that then fails to decode is reported per frame.
            fps = 0.0

    valid_pairs: list[tuple[float, float]] = []
    original_pairs: list[tuple[float, float]] = []
    clamped_pairs: list[tuple[float, float]] = []
    for pair in pairs:
        if probe_failure:
            # Without a duration nothing can be clamped, and the source is
            # unreadable anyway; report every interval against the probe error.
            failures.append(
                f"interval {pair[0]:g}..{pair[1]:g}s not grounded: source unreadable "
                f"({probe_failure})"
            )
            continue
        clamped, was_clamped, why = _clamp(pair, total or 0.0)
        if clamped is None:
            failures.append(f"interval {pair[0]:g}..{pair[1]:g}s rejected: {why}")
            continue
        valid_pairs.append(clamped)
        original_pairs.append(pair)
        if was_clamped:
            clamped_pairs.append(clamped)
    # Merge overlaps before decoding: two retrieval windows that touch are one
    # decode, and decoding the same span twice would produce two evidence items
    # for one observation.
    merged = IntervalSet.from_pairs(valid_pairs)
    n_merged_in = len(valid_pairs)

    def _asked_for(span: Interval) -> tuple[float, float]:
        """The caller's original ask behind a merged span.

        Clamping shortens an interval and merging unions several into one, so
        the span that gets decoded is not what was requested — and the
        difference is the whole point of the ``requested`` field. The ask is the
        extent over the pre-clamp intervals that fed into this span.
        """
        contributing = [
            o
            for c, o in zip(valid_pairs, original_pairs)
            if c[0] < span.end + MERGE_EPS and c[1] > span.start - MERGE_EPS
        ]
        if not contributing:
            return (float(span.start), float(span.end))
        return (min(a for a, _ in contributing), max(b for _, b in contributing))

    # The decoder is required for every decode; if it is absent the whole stage
    # fails rather than each interval, and the failure names the cause.
    decoder_failure = ""
    try:
        require_ffmpeg()
    except RuntimeError as exc:
        decoder_failure = str(exc)

    grounded: list[GroundedInterval] = []
    for index, span in enumerate(merged):
        asked_for = _asked_for(span)
        times, note = sample_times(span.start, span.end, max_frames=max_frames)
        times, fitted = fit_to_stream(times, total or 0.0, fps)
        if fitted:
            note = f"{note}; {fitted}"
        frames: list[SampledFrame] = []
        frame_failures: list[str] = []
        for position, when in enumerate(times):
            label = f"{index:03d}_{position:02d}_at_{when:09.3f}s"
            frame_path = out_dir / f"ground_interval{label}.jpg"
            if decoder_failure:
                frame_failures.append(f"{when:.3f}s: decoder unavailable ({decoder_failure})")
                continue
            size = decode_frame(video, when, frame_path)
            if size is None:
                frame_failures.append(
                    f"{when:.3f}s: ffmpeg produced no decodable frame"
                )
                continue
            frames.append(
                SampledFrame(
                    time=when,
                    width=size[0],
                    height=size[1],
                    path=str(frame_path),
                )
            )
        # A merged span is clamped if any interval that fed into it was cut
        # short at the source end — an overlap test on the pre-merge pairs,
        # because the merge can carry a clamped end forward under a longer one
        # and comparing ends alone would not see that.
        clamped = any(
            c[0] < float(span.end) + MERGE_EPS and c[1] > float(span.start) - MERGE_EPS
            for c in clamped_pairs
        )
        why = "clamped end to the source duration" if clamped else ""
        grounded.append(
            GroundedInterval(
                start=float(span.start),
                end=float(span.end),
                requested=asked_for,
                frames=tuple(frames),
                clamped=clamped,
                note=note + (f"; {why}" if why else ""),
                failures=tuple(frame_failures),
            )
        )
        failures.extend(
            f"interval {asked_for[0]:g}..{asked_for[1]:g}s, frame {f}"
            for f in frame_failures
        )

    elapsed = time.perf_counter() - started
    n_frames = sum(len(g.frames) for g in grounded)
    requested_duration = sum(g.duration for g in grounded)
    metrics = GroundMetrics(
        source_duration_s=float(total) if total is not None else 0.0,
        requested_duration_s=requested_duration,
        decoded_frames=n_frames,
        elapsed_s=elapsed,
    )

    params_digest = Source.of(
        "engine.ground",
        "sample_window",
        max_frames=max_frames,
        min_gap_s=MIN_SAMPLE_GAP_S,
        jpeg_quality=JPEG_QUALITY,
    ).params_digest

    evidence: list[Evidence] = []
    for grounded_interval in grounded:
        if grounded_interval.is_empty:
            continue
        times = tuple(f.time for f in grounded_interval.frames)
        evidence.append(
            Evidence(
                claim=_observation_claim(times, grounded_interval.start, grounded_interval.end),
                intervals=IntervalSet.spanning(grounded_interval.start, grounded_interval.end),
                modality=Modality.VISUAL,
                source=Source(
                    module="engine.ground",
                    method="sample_window",
                    params_digest=params_digest,
                ),
                confidence=1.0,
                rationale=_observation_rationale(
                    grounded_interval, video, params_digest, retrieval_report
                ),
                grounded=True,
                artifacts=tuple(f.path for f in grounded_interval.frames),
            )
        )

    parts = [
        f"grounded {len(grounded)} interval(s)"
        f" ({n_merged_in} requested, {len(rejected)} rejected"
        f"{', ' + str(len(clamped_pairs)) + ' clamped' if clamped_pairs else ''})"
        f" into {n_frames} frame(s) from {video}"
        f" ({metrics.source_duration_s:.2f}s source, "
        f"{metrics.requested_duration_s:.2f}s requested)"
        f" in {elapsed:.3f}s",
    ]
    if decoder_failure:
        parts.append(f"decoder unavailable: {decoder_failure}")
    if probe_failure:
        parts.append(f"source unreadable: {probe_failure}")
    parts.extend(failures)
    report = "\n".join(parts)

    return GroundResult(
        evidence=tuple(evidence),
        intervals=tuple(grounded),
        failures=tuple(failures),
        metrics=metrics,
        report=report,
    )


__all__ = [
    "DECODE_TIMEOUT_S",
    "GroundMetrics",
    "GroundResult",
    "GroundedInterval",
    "JPEG_QUALITY",
    "MAX_FRAMES_DEFAULT",
    "MIN_SAMPLE_GAP_S",
    "SCHEMA_VERSION",
    "SampledFrame",
    "decode_frame",
    "fit_to_stream",
    "ground",
    "sample_times",
]


# Things deliberately not done here, kept where a reader will find them:
#
# - No vision model, no CLIP, no embedding. The first GROUND milestone is media
#   grounding; attaching a model would move the cost from decode (measured) to
#   inference (unmeasured) before the bridge itself is trusted.
# - No wiring into ``selekyt.pipeline``. The pipeline builds a retrieval index
#   but issues no query at run time, so there is no call site for GROUND yet;
#   inventing a query is a retrieval decision, not a grounding one. ``retrieve``
#   then ``ground`` is composed by the caller, which is where the query lives.
# - No re-decoding of frames GROUND has already written. ``artifacts`` are the
#   contract for a later stage; reading them back through this module would
#   duplicate the decode this module exists to bound.
#
# ``engine.memory.ingest`` remains the path for *whole-source* VISUAL memory:
# it translates reframe's per-window analysis of the entire media. GROUND is the
# path for *caller-chosen* windows, and the two are complementary rather than
# one superseding the other.
