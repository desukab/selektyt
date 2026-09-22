"""Captions for the FINAL EDITED timeline.

``engine/captions.py`` captions a *candidate* — a single keep from the
source. This module captions the *edit*: it takes an ``engine.timeline.Timeline``
plus the transcript words of its source media, maps those words onto the
edited timeline (so a caption appears at the moment the audience hears it,
not at the moment it was spoken in the source) and writes SRT + ASS + a
traceability sidecar.

Where the timing comes from, in priority order:

1. Transcript words with real timestamps. A word survives into the timeline
   when its span overlaps a clip of the media it belongs to; its span is
   clamped to the clip and shifted by the clip's placement. This is exact.
2. Transcript text without word timestamps. The text is distributed across
   the clips that read from that media, in proportion to how much of the
   media each clip uses, and evenly inside each clip. This is an
   **approximation** and it is never silent: every cue it produces carries
   ``"approximate": true`` and the sidecar records it, so no downstream
   consumer can mistake even spacing for measured speech timing.

Editorial constraints applied while grouping cues (each one is a deliberate
choice, and each is tested):

* Sentence structure — a cue closes at a sentence terminator
  (``. ! ? । ॥``) rather than running two sentences together.
* Speech boundaries — a cue starts at its first word, not at the clip's
  in-point, and ends a short tail past its last word, never wandering into
  silence on its own.
* Natural reading rhythm — a cue is held at least as long as its text takes
  to read at a configurable reading speed, but never past the next cue.
* Maximum line length and two lines per cue, measured in *display* width so
  Telugu, Devanagari and other wide scripts are not overflowed.
* Clip boundaries — a cue never bridges a hard cut; words on either side of
  a cut belong to different cues.
* Safe area — ASS margins keep the text inside the platform safe zone.

Multilingual text is handled by never splitting a word and by measuring
width per character; no script-specific segmentation is attempted, and
combining marks stay attached to their base because only whitespace and
terminators are treated as break points.

Note on styling: the visual presets and the emphasis picker are shared with
``engine/captions.py`` via ``engine/caption_director`` so the edited-timeline
captions and the per-candidate captions look like one product.
"""

from __future__ import annotations

import bisect
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from engine import timecode

# Shared with engine/captions.py so both caption writers agree on emphasis and
# on how wide a Telugu/Devanagari glyph is.
from engine.captions import caption_geometry, caption_presets, preset_for
from engine.caption_director import (_emphasis, _width, max_line_width_for,
                                     style_name)
from engine.timeline import Clip, Timeline, format_smpte
from models.core import Word

SENTENCE_END = ".!?।॥"
BREAK_CHARS = set(SENTENCE_END)


@dataclass(frozen=True)
class CaptionConfig:
    max_line_width: int | None = None
    """Display columns per line (a wide glyph counts as 2).

    ``None`` derives the budget from the active preset's font size and the
    canvas below, which is what makes it a width rather than a number: a fixed
    34 was wrong for the two bold presets, where a 34-column cue renders as
    four lines because 58 px bold does not fit that many columns into the safe
    area. Set explicitly only to lay out on a canvas of a different size — see
    ``write_ass``, which does that for a timeline whose render is narrower than
    1080. The number is a width only relative to the presets' font sizes and
    the safe area — ``tests/test_caption_safe_area.py`` burns the widest line
    this admits and measures where its pixels land."""
    max_lines: int = 2
    max_cue_seconds: float = 6.0
    min_cue_seconds: float = 0.9
    reading_cps: float = 17.0
    """Display columns per second, used to compute a cue's minimum hold."""
    tail_seconds: float = 0.12
    """How long a cue outlives its last spoken word."""
    max_gap_seconds: float = 0.7
    """A silence longer than this closes a cue even mid-sentence."""
    inter_cue_gap: float = 0.04
    """Enforced hole between consecutive cues so players never stack them."""
    # Defaults come from the one geometry the burned-in captions declare, so a
    # canvas change reaches both writers rather than only the one that made it.
    playres_x: int = caption_geometry()["playres_x"]
    playres_y: int = caption_geometry()["playres_y"]
    margin_h: int = caption_geometry()["margin_h"]
    """Safe-area horizontal margin, in script pixels."""
    font: str = ""
    """Overrides the detected font when non-empty."""


@dataclass(frozen=True)
class TimelineWord:
    """A transcript word placed onto the edited timeline."""

    text: str
    start: float
    end: float
    source_key: str
    source_start: float
    source_end: float
    clip: str
    approximate: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class Transcript:
    """Transcript material for one piece of source media.

    Provide ``words`` for measured, word-timed captions, or ``text`` alone for
    the documented approximation path. At least one of the two is required.
    """

    source_key: str
    words: tuple[Word, ...] = ()
    text: str = ""

    def __post_init__(self) -> None:
        if not self.words and not self.text.strip():
            raise ValueError(
                f"transcript for {self.source_key!r} has neither words nor text"
            )


@dataclass
class CaptionCue:
    index: int
    start: float
    end: float
    lines: list[str] = field(default_factory=list)
    words: list[TimelineWord] = field(default_factory=list)
    approximate: bool = False
    source: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "lines": list(self.lines),
            "words": [
                {
                    "text": w.text,
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "source_start": round(w.source_start, 3),
                    "source_end": round(w.source_end, 3),
                }
                for w in self.words
            ],
            "approximate": self.approximate,
            "source": self.source,
        }


# ------------------------------------------------------------------ placement


def _clip_words(clip: Clip, words: tuple[Word, ...]) -> list[TimelineWord]:
    """The words a clip covers, clamped to it and shifted onto the timeline.

    A word is kept when its span overlaps the clip's source span by any
    amount; its timing is clamped so nothing is claimed outside the clip.
    """
    out: list[TimelineWord] = []
    for w in words:
        if w.end <= clip.source_in or w.start >= clip.source_out:
            continue
        s = max(w.start, clip.source_in)
        e = min(w.end, clip.source_out)
        if e - s <= 0.0:
            continue
        out.append(
            TimelineWord(
                text=w.text,
                start=clip.timeline_in + (s - clip.source_in),
                end=clip.timeline_in + (e - clip.source_in),
                source_key=clip.source.key,
                source_start=s,
                source_end=e,
                clip=clip.name,
            )
        )
    return out


def _approximate_words(clip: Clip, text: str, config: CaptionConfig) -> list[TimelineWord]:
    """Evenly spaced pseudo-words across this clip, from plain text.

    Deliberately approximate and marked as such: without word timestamps there
    is no way to know where inside the clip any given word is actually spoken.
    Tokens are kept whole and broken only at whitespace, so multilingual text
    is never mangled mid-word.
    """
    tokens = [t for t in text.split() if t]
    if not tokens or clip.duration <= 0.0:
        return []
    step = clip.duration / len(tokens)
    return [
        TimelineWord(
            text=tok,
            start=clip.timeline_in + i * step,
            end=clip.timeline_in + min((i + 1) * step, clip.duration),
            source_key=clip.source.key,
            source_start=clip.source_in
            + (i * step / max(clip.duration, 1e-9)) * clip.source_duration,
            source_end=clip.source_in
            + (min((i + 1) * step, clip.duration) / max(clip.duration, 1e-9))
            * clip.source_duration,
            clip=clip.name,
            approximate=True,
        )
        for i, tok in enumerate(tokens)
    ]


def _approximate_allocation(
    clips: list[Clip], text: str
) -> list[tuple[Clip, list[str]]]:
    """Split a text-only transcript across a media's clips.

    Without word timings there is no way to know which words the edit actually
    keeps, so the text is spread across the clips in proportion to the edited
    screen time each one receives — the allocation that best matches what an
    audience hears. A clip too short for a fair share of a word gets none
    rather than being handed a token it cannot display.
    """
    tokens = text.split()
    if not tokens or not clips:
        return []
    total = sum(c.duration for c in clips)
    if total <= 0.0:
        return []
    # Cumulative screen-time boundaries; token i lands in the clip whose share
    # of the timeline contains the token's position in the text.
    bounds: list[float] = []
    acc = 0.0
    for clip in clips:
        acc += clip.duration
        bounds.append(acc / total)
    by_clip: dict[str, list[str]] = {c.name: [] for c in clips}
    for i, tok in enumerate(tokens):
        idx = bisect.bisect_left(bounds, (i + 0.5) / len(tokens))
        idx = min(idx, len(clips) - 1)
        by_clip[clips[idx].name].append(tok)
    return [(c, by_clip[c.name]) for c in clips if by_clip[c.name]]


def timeline_words(
    timeline: Timeline,
    transcripts: list[Transcript] | dict[str, Transcript],
    config: CaptionConfig | None = None,
) -> list[TimelineWord]:
    """Place every transcript word that survives into the edit, in timeline
    order. Words whose media has no clip, or that fall in cut-away regions,
    are dropped — they have no honest position on this timeline."""
    config = config or CaptionConfig()
    by_key: dict[str, Transcript] = (
        transcripts if isinstance(transcripts, dict) else {t.source_key: t for t in transcripts}
    )
    placed: list[TimelineWord] = []
    for clip in timeline.clips:
        tr = by_key.get(clip.source.key)
        if tr is None:
            continue
        if tr.words:
            placed.extend(_clip_words(clip, tr.words))

    # Text-only transcripts are allocated per media across that media's clips
    # in one pass, since the split depends on all of the clips together.
    for tr in by_key.values():
        if tr.words:
            continue
        clips = [c for c in timeline.clips if c.source.key == tr.source_key]
        for clip, tokens in _approximate_allocation(clips, tr.text):
            placed.extend(_approximate_words(clip, " ".join(tokens), config))
    return sorted(placed, key=lambda w: (w.start, w.end))


# ------------------------------------------------------------------ grouping


def _wrap(words: list[TimelineWord], config: CaptionConfig) -> list[str] | None:
    """Wrap words into at most ``max_lines`` lines of at most the config's
    display columns.

    Returns None when the words do not fit — the caller must then split the
    cue. Whitespace between words is not counted (it does not draw), which is
    why the measured width is per glyph.
    """
    # The budget is a width on this canvas for this preset, not a constant: a
    # timeline rendered narrower than 1080 has proportionally less safe area,
    # and a cue sized for the reference canvas would wrap on it.
    width = config.max_line_width or max_line_width_for(
        preset_for(), config.playres_x, config.margin_h)
    lines: list[str] = []
    current: list[str] = []
    used = 0
    for w in words:
        token = w.text.strip()
        if not token:
            continue
        add = _width(token) + (1 if current else 0)
        if used + add > width:
            if len(lines) + 1 >= config.max_lines:
                return None
            lines.append(" ".join(current))
            current = [token]
            used = _width(token)
        else:
            current.append(token)
            used += add
    if current:
        lines.append(" ".join(current))
    return lines or None


def _ends_sentence(word: TimelineWord) -> bool:
    return bool(word.text) and word.text.strip()[-1] in BREAK_CHARS


def _cue_source(words: list[TimelineWord], timeline: Timeline) -> dict:
    """Traceability for a cue: the source span it was drawn from, in both
    seconds and SMPTE, plus where it lands on the edited timeline."""
    if not words:
        return {}
    by_clip: dict[str, list[TimelineWord]] = {}
    for w in words:
        by_clip.setdefault(w.clip, []).append(w)
    spans = []
    for clip_name, ws in by_clip.items():
        clip = next((c for c in timeline.clips if c.name == clip_name), None)
        if clip is None:
            continue
        rate = clip.rate
        spans.append(
            {
                "clip": clip.name,
                "source_path": clip.source.path,
                "source_in": round(min(w.source_start for w in ws), 3),
                "source_out": round(max(w.source_end for w in ws), 3),
                "source_timecode_in": format_smpte(
                    _clip_media_zero(clip) + min(w.source_start for w in ws), rate
                ),
                "source_timecode_out": format_smpte(
                    _clip_media_zero(clip) + max(w.source_end for w in ws), rate
                ),
                "timeline_in": round(min(w.start for w in ws), 3),
                "timeline_out": round(max(w.end for w in ws), 3),
            }
        )
    return {"spans": spans}


def _clip_media_zero(clip: Clip) -> float:
    return timecode.parse_smpte(clip.source.start_timecode, clip.source.fps)


def build_cues(
    words: list[TimelineWord], timeline: Timeline, config: CaptionConfig | None = None
) -> list[CaptionCue]:
    """Group timeline words into displayable cues.

    Pure: same words + same config always give the same cues.
    """
    config = config or CaptionConfig()
    cues: list[CaptionCue] = []
    current: list[TimelineWord] = []

    def flush() -> None:
        if not current:
            return
        lines = _wrap(current, config)
        if lines is None:
            # Does not fit even alone: force-split at the line boundary.
            lines = _force_wrap(current, config)
        cues.append(
            CaptionCue(
                index=0,  # numbered at the end
                start=current[0].start,
                end=current[-1].end,
                lines=lines,
                words=list(current),
                approximate=any(w.approximate for w in current),
                source=_cue_source(current, timeline),
            )
        )
        current.clear()

    for w in words:
        trial = current + [w]
        lines = _wrap(trial, config)
        close = lines is None
        if not close and current:
            prev = current[-1]
            gap = w.start - prev.end
            close = (
                _ends_sentence(prev)
                or gap > config.max_gap_seconds
                or w.clip != prev.clip
            )
        if close and current:
            flush()
        current.append(w)
        # A sentence terminator always closes the cue after this word.
        if _ends_sentence(w) or w.end - current[0].start > config.max_cue_seconds:
            flush()
    flush()

    # Timing pass: tail, reading-speed minimum hold, clamps to the clip and to
    # the following cue. Cues are already non-overlapping by construction
    # (words are), the hold only consumes the gap to the next cue.
    for i, cue in enumerate(cues):
        words_ = cue.words
        clip_end = max(
            (c.timeline_out for c in timeline.clips if c.name == words_[0].clip),
            default=cue.end,
        )
        end = min(words_[-1].end + config.tail_seconds, clip_end)
        min_hold = max(
            config.min_cue_seconds,
            _width(cue.text) / config.reading_cps,
        )
        min_hold = min(min_hold, config.max_cue_seconds)
        end = max(end, min(words_[0].start + min_hold, clip_end))
        if i + 1 < len(cues):
            end = min(end, cues[i + 1].start - config.inter_cue_gap)
        cue.end = max(end, cue.start + 0.08)

    for i, cue in enumerate(cues, 1):
        cue.index = i
    return cues


def _force_wrap(words: list[TimelineWord], config: CaptionConfig) -> list[str]:
    """Last-resort wrap for a single unbreakable token longer than the line:
    emit one line per word so nothing is silently dropped."""
    lines: list[str] = []
    current: list[str] = []
    width = 0
    for w in words:
        token = w.text.strip()
        if not token:
            continue
        add = _width(token) + (1 if current else 0)
        if width + add > config.max_line_width and current:
            lines.append(" ".join(current))
            current = [token]
            width = _width(token)
        else:
            current.append(token)
            width += add
    if current:
        lines.append(" ".join(current))
    return lines or [" "]


# ------------------------------------------------------------------- writers


def _srt_ts(v: float) -> str:
    v = max(0.0, v)
    h = int(v // 3600)
    m = int(v % 3600 // 60)
    s = int(v % 60)
    ms = min(999, int(round((v - int(v)) * 1000)))
    return f"{h}:{m:02d}:{s:02d},{ms:03d}"


def _ass_ts(v: float) -> str:
    v = max(0.0, v)
    h = int(v // 3600)
    m = int(v % 3600 // 60)
    s = int(v % 60)
    cs = min(99, int(round((v - int(v)) * 100)))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\\", "")


def _ass_presets() -> dict:
    """The same presets the burned-in captions use.

    A second table here would drift from ``engine.captions`` the way the width
    functions used to, and the drift is invisible until a cue laid out by this
    writer no longer fits the canvas the other one measured.
    """
    return caption_presets()


def _ass_karaoke(cue: CaptionCue, config: CaptionConfig) -> str:
    """Word-timed karaoke text when timings are real, plain text when they
    are the documented approximation (karaoke on invented timings would be a
    lie about where speech is)."""
    if cue.approximate:
        return _escape_ass(cue.text)
    raw = [Word(w.text, w.source_start, w.source_end) for w in cue.words]
    emphasis = _emphasis(raw)
    pieces: list[str] = []
    words = [w for w in cue.words if w.text.strip()]
    for idx, w in enumerate(words):
        duration_cs = max(1, int(round(w.duration * 100)))
        text = _escape_ass(w.text.strip())
        if idx in emphasis:
            text = r"{\c&H00D7FF&\b1}" + text + r"{\c&HFFFFFF&\b0}"
        pieces.append(rf"{{\k{duration_cs}}}{text}")
    return " ".join(pieces) if pieces else _escape_ass(cue.text)


def srt_for_timeline(
    timeline: Timeline,
    transcripts: list[Transcript] | dict[str, Transcript],
    out: str | Path,
    config: CaptionConfig | None = None,
) -> Path:
    """Write a SubRip track for the edited timeline.

    Cue text is the plain (two-line-wrapped) string; styling lives in the ASS
    track. Both writers share one grouping pass, so they are cue-for-cue
    identical.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cues = build_cues(timeline_words(timeline, transcripts, config), timeline, config)
    blocks = [
        f"{c.index}\n{_srt_ts(c.start)} --> {_srt_ts(c.end)}\n{c.text}" for c in cues
    ]
    out.write_text(("\n\n".join(blocks) + "\n") if blocks else "", encoding="utf-8")
    return out


def ass_for_timeline(
    timeline: Timeline,
    transcripts: list[Transcript] | dict[str, Transcript],
    out: str | Path,
    config: CaptionConfig | None = None,
) -> Path:
    """Write an ASS track for the edited timeline.

    Resolution and safe-area margins come from ``CaptionConfig`` (or from the
    timeline's render manifest when it specifies one). Word-timed karaoke is
    used only for measured timings.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    config = config or CaptionConfig()
    if timeline.manifest.is_populated() and timeline.manifest.width:
        config = CaptionConfig(
            max_line_width=config.max_line_width,
            max_lines=config.max_lines,
            max_cue_seconds=config.max_cue_seconds,
            min_cue_seconds=config.min_cue_seconds,
            reading_cps=config.reading_cps,
            tail_seconds=config.tail_seconds,
            max_gap_seconds=config.max_gap_seconds,
            inter_cue_gap=config.inter_cue_gap,
            playres_x=timeline.manifest.width,
            playres_y=timeline.manifest.height,
            margin_h=config.margin_h,
            font=config.font,
        )

    style = style_name()
    presets = _ass_presets()
    p = presets.get(style, presets["minimal"])
    font = config.font or os.getenv("SELEKYT_FONT", "Noto Sans")

    cues = build_cues(timeline_words(timeline, transcripts, config), timeline, config)
    lines = [
        f"Dialogue: 0,{_ass_ts(c.start)},{_ass_ts(c.end)},Default,,0,0,0,,{_ass_karaoke(c, config)}"
        for c in cues
    ]

    content = (
        f"[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {config.playres_x}\nPlayResY: {config.playres_y}\n"
        f"ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,"
        "Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,"
        "MarginV,Encoding\n"
        f"Style: Default,{font},{p['fontsize']},{p['primary']},&H00FFFFFF,"
        f"{p['outline']},{p['back']},{p['bold']},0,0,0,100,100,0,0,1,"
        f"{p['outline_w']},{p['shadow']},2,{config.margin_h},{config.margin_h},"
        f"{p['margin_v']},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,"
        "Effect,Text\n"
        + "\n".join(lines)
        + "\n"
    )
    out.write_text(content, encoding="utf-8")
    return out


def captions_metadata(
    timeline: Timeline,
    transcripts: list[Transcript] | dict[str, Transcript],
    config: CaptionConfig | None = None,
) -> dict:
    """The traceability sidecar for a caption set.

    Records the generator, the style, whether any cue is approximate and why,
    and per-cue source spans in seconds and SMPTE, so every cue can be walked
    back to the footage it captions.
    """
    config = config or CaptionConfig()
    words = timeline_words(timeline, transcripts, config)
    cues = build_cues(words, timeline, config)
    approx_clips = sorted({w.clip for w in words if w.approximate})
    return {
        "generator": "engine.timeline_captions",
        "title": timeline.title,
        "fps": timeline.fps,
        "timeline_duration_seconds": round(timeline.duration, 3),
        "style": style_name(),
        "cue_count": len(cues),
        "approximate": bool(approx_clips),
        "approximate_reason": (
            "word timings unavailable for: " + ", ".join(approx_clips)
            if approx_clips
            else ""
        ),
        "config": {
            "max_line_width": config.max_line_width,
            "max_lines": config.max_lines,
            "max_cue_seconds": config.max_cue_seconds,
            "min_cue_seconds": config.min_cue_seconds,
            "reading_cps": config.reading_cps,
            "tail_seconds": config.tail_seconds,
            "max_gap_seconds": config.max_gap_seconds,
            "inter_cue_gap": config.inter_cue_gap,
        },
        "source_media": [
            {"path": m.path, "fps": m.fps, "start_timecode": m.start_timecode}
            for m in timeline.source_media()
        ],
        "cues": [c.to_dict() for c in cues],
    }


def write_captions(
    timeline: Timeline,
    transcripts: list[Transcript] | dict[str, Transcript],
    out_dir: str | Path,
    stem: str | None = None,
    config: CaptionConfig | None = None,
) -> dict[str, Path]:
    """Write SRT + ASS + JSON sidecar for an edited timeline in one call."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or timeline.title or "captions"
    srt = srt_for_timeline(timeline, transcripts, out_dir / f"{stem}.srt", config)
    ass = ass_for_timeline(timeline, transcripts, out_dir / f"{stem}.ass", config)
    meta = captions_metadata(timeline, transcripts, config)
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"srt": srt, "ass": ass, "json": json_path}


__all__ = [
    "CaptionConfig",
    "CaptionCue",
    "TimelineWord",
    "Transcript",
    "ass_for_timeline",
    "build_cues",
    "captions_metadata",
    "srt_for_timeline",
    "timeline_words",
    "write_captions",
]
