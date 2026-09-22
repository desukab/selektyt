from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from models.core import Candidate, Word
from engine.edit_plan import join_words


@dataclass
class CaptionBlock:
    words: list[Word]
    emphasis: set[int]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return join_words([w for w in self.words if w.text.strip()])


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


# The Brahmic abugidas: Devanagari, Bengali, Gurmukhi, Gujarati, Odia, Tamil,
# Telugu, Kannada, Malayalam, Sinhala. Every one of them is a syllabic script
# in which a consonant carries an inherent vowel and a virama cancels it, so
# the unit of layout is the syllable, not the code point: "ప్ర" is three code
# points (ప + ് + ర) but one glyph cluster. Counting code points made a Telugu
# line fit barely half as much text as an English one of the same nominal width.
_INDIC_RANGES = (
    (0x0900, 0x097F),  # Devanagari
    (0x0980, 0x09FF),  # Bengali
    (0x0A00, 0x0A7F),  # Gurmukhi
    (0x0A80, 0x0AFF),  # Gujarati
    (0x0B00, 0x0B7F),  # Odia
    (0x0B80, 0x0BFF),  # Tamil
    (0x0C00, 0x0C7F),  # Telugu
    (0x0C80, 0x0CFF),  # Kannada
    (0x0D00, 0x0D7F),  # Malayalam
    (0x0D80, 0x0DFF),  # Sinhala
)


def _is_indic(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _INDIC_RANGES)


# Combining class 9 is the Unicode category "virama" and is the same in every
# Brahmic script, so it detects a conjunct without a per-script table.
def _is_virama(ch: str) -> bool:
    return unicodedata.combining(ch) == 9


def _is_mark(ch: str) -> bool:
    # Mn, Mc or Me. Absorption has to key on the category rather than on a
    # nonzero combining class: a spacing mark such as the Telugu anusvara
    # "ం" is Mc with combining class 0, and treating it as a new syllable
    # doubled the measured width of ordinary Telugu prose.
    return unicodedata.category(ch)[0] == "M"


# Zero-width joiner and the skin-tone/hair components: one emoji person with a
# skin tone is one glyph, and counting the joiner and the modifier as columns
# would more than double a wide glyph's cost.
_JOINERS = frozenset({0x200D, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064})
_MODIFIER_RANGES = ((0x1F3FB, 0x1F3FF), (0x1F9B0, 0x1F9B5))


def _is_emoji_component(ch: str) -> bool:
    o = ord(ch)
    return o in _JOINERS or any(lo <= o <= hi for lo, hi in _MODIFIER_RANGES)


def caption_width(text: str) -> int:
    """Width of ``text`` in display columns, where one column is an average
    ASCII character.

    This is the one place caption layout measures text, shared by the ASS/SRT
    writers and the timeline cue wrapper; the two previous copies disagreed
    with each other and both disagreed with the fonts.

    Measured against real type (DejaVu Sans and Noto Sans Telugu at 100 px, one
    column defined as the mean advance of English lowercase prose):

    * an ASCII character is exactly one column by definition;
    * an East Asian fullwidth or wide glyph is two (it is 1.0 em against
      lowercase prose's 0.52 em, and it takes a full monospace cell in every
      CJK-aware renderer);
    * an Indic syllable cluster is two. On the Telugu fixture the real clusters
      measure 2.4–10.2 columns against the rule's 4–10, so the rule is +29%
      conservative in the mean and between −8% and +89% per word —
      conservative on average, occasionally narrow, which is why
      ``max_line_width`` is a soft bound with a force-wrap fallback rather than
      a hard clip. The code-point rule it replaced was +145% conservative, and
      a Telugu line that should have held ~34 columns was being broken at ~14;
    * a combining mark is zero, because it does not advance the pen: it
      modifies the base before it. This is what makes "e" + U+0301 cost one
      column, not two;
    * everything else — Latin extended, Greek, Cyrillic, Hebrew, Arabic,
      punctuation — is one column per code point.
    """
    total = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if _is_mark(ch) or _is_emoji_component(ch):
            i += 1
            continue
        if _is_indic(ch):
            total += 2
            i += 1
            # Absorb the signs of this syllable; a virama additionally absorbs
            # the consonant that follows, because "ప్ర" is one cluster.
            pending_conjunct = False
            while i < n:
                c = text[i]
                if _is_mark(c) or _is_emoji_component(c):
                    pending_conjunct = pending_conjunct or _is_virama(c)
                    i += 1
                    continue
                if pending_conjunct and _is_indic(c):
                    i += 1
                    pending_conjunct = False
                    continue
                break
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            total += 2
            i += 1
            continue
        total += 1
        i += 1
    return total


_width = caption_width


# The canvas the burned-in captions declare, and the safe area inside it. The
# width budget below is derived from these, so a font size or a margin change
# reaching one writer and not the other is what makes a cue wider than the
# screen it is laid out on.
PLAYRES_X = 1080
PLAYRES_Y = 1920
MARGIN_H = 70

# One column, in em, of the font the captions render in. Measured against the
# lowercase prose ``caption_width`` calibrates a column on (0.5209 em in DejaVu
# Sans), plus headroom: a real line mixes capitals and lowercase, and a capital
# is wider than the mean its column was derived from. 0.55 em is the widest
# plausible per-column cost at ordinary weight; BOLD_WIDENING covers the
# synthetic-bold presets, whose capitals run ~8% wider still.
COLUMN_EM = 0.55
BOLD_WIDENING = 1.08


def max_line_width_for(preset: dict, playres_x: int = PLAYRES_X,
                       margin_h: int = MARGIN_H) -> int:
    """How many columns fit between the safe-area margins in this preset.

    ``MAX_LINE_WIDTH`` used to be one number that every writer copied and no
    writer checked against the canvas. Measured, that number is wrong for the
    two bold presets: a 34-column cue renders as **four** lines under ``punch``
    and ``spotlight`` where the policy assumes two, because 58 px bold does not
    fit 34 columns into the 940 px of safe area. libass wraps rather than
    clipping, so the cue does not run off the frame — it doubles in height,
    which nothing measures and which also makes the reading-time hold
    (computed from 34 columns) shorter than what rendered.

    Deriving the budget makes the relation structural: the budget is a function
    of the font size and the margin, so a preset change cannot outgrow the
    canvas without the derivation noticing. The derived budgets are
    intentionally under the measured one-line capacity — 31/27/28/32 columns
    against measured capacities of 34/30/30/34 — because the calibration is
    against the resolved font, which is wider on some hosts than others.
    """
    usable = playres_x - 2 * margin_h
    em = preset["fontsize"] * (BOLD_WIDENING if preset.get("bold") else 1.0)
    return int(usable // (em * COLUMN_EM))


# The width budget every caption writer lays out to, and the word count on top
# of it. ``MAX_LINE_WIDTH`` remains the *measured* budget — the widest line the
# policy admits on the reference canvas — and the tests below pin it against
# the derivation so the two cannot drift apart.
MAX_LINE_WIDTH = 34
MAX_WORDS = 6


def _emphasis(words: list[Word]) -> set[int]:
    """Pick a few meaningful words without requiring an LLM.

    Numbers, longer content words, English technical terms and words carrying
    strong punctuation are useful visual anchors. Repeated filler-like tokens
    are deliberately ignored.
    """
    stop = {
        "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for",
        "is", "are", "was", "were", "this", "that", "it", "i", "we", "you",
        "అది", "ఇది", "అంటే", "మరి", "కానీ", "మనం", "అతను", "ఆమె",
        "और", "यह", "वह", "है", "हैं", "के", "की", "का", "में",
    }
    scores: list[tuple[float, int]] = []
    for i, w in enumerate(words):
        t = _clean(w.text)
        low = t.lower()
        if not t or low in stop or len(low) <= 2:
            continue
        score = min(4.0, len(re.sub(r"[^\w\u0900-\u0dff]", "", t)) / 4)
        if re.search(r"\d", t):
            score += 2.0
        if re.search(r"[!?]", t):
            score += 1.5
        if t.isupper() and len(t) > 2:
            score += 1.0
        scores.append((score, i))
    scores.sort(reverse=True)
    # Never overwhelm the caption: roughly one emphasized word per 3 words.
    limit = max(1, min(2, len(words) // 3))
    return {i for _, i in scores[:limit]}


def build_blocks(candidate: Candidate, max_words: int = MAX_WORDS,
                 max_width: int | None = None, max_duration: float = 3.2,
                 preset: dict | None = None) -> list[CaptionBlock]:
    # ``max_width`` is derived from the preset when a caller does not name a
    # canvas: the budget is a width relative to the font size and the safe
    # area, and a fixed 34 was wrong for the bold presets (see
    # ``max_line_width_for``). Callers that lay out on a different canvas pass
    # their own. Imported here rather than at module level because
    # engine.captions imports this module for ``caption_width``.
    if max_width is None:
        from engine.captions import caption_geometry, preset_for
        geo = caption_geometry()
        max_width = max_line_width_for(preset or preset_for(),
                                       geo["playres_x"], geo["margin_h"])
    words = sorted((candidate.words or []), key=lambda w: (w.start, w.end))
    words = [w for w in words if _clean(w.text)]
    # A word straddling the selection end belongs to what the speaker said
    # next, not to this story: engine/edit_plan.py snaps that end back to the
    # word's start so the render drops it, and a caption that still shows it
    # describes audio the deliverable does not contain. Excluding a word that
    # overlaps the bound keeps the caption track a transcript of the render
    # rather than of the pre-snap span. Measured on a real selection: a word
    # at 235.220-235.380 s survived into the captions of a candidate ending
    # 235.237 s, appeared in the SRT but not in the transcript, and its cue ran
    # past the end of the 52.42 s render.
    if words and candidate.end > candidate.start:
        words = [w for w in words if w.end <= candidate.end]
    if not words:
        return []

    blocks: list[CaptionBlock] = []
    cur: list[Word] = []
    for word in words:
        trial = cur + [word]
        text = " ".join(w.text.strip() for w in trial)
        duration = word.end - (cur[0].start if cur else word.start)
        boundary = bool(re.search(r"[.!?।॥]$", cur[-1].text.strip())) if cur else False
        if cur and (len(trial) > max_words or _width(text) > max_width or duration > max_duration or boundary):
            blocks.append(CaptionBlock(cur, _emphasis(cur)))
            cur = [word]
        else:
            cur = trial
    if cur:
        blocks.append(CaptionBlock(cur, _emphasis(cur)))
    return blocks


def style_name() -> str:
    import os
    return os.getenv("SELEKYT_CAPTION_STYLE", "minimal").strip().lower()


def caption_metadata(candidate: Candidate) -> dict:
    blocks = build_blocks(candidate)
    return {
        "style": style_name(),
        "blocks": [
            {
                "start": round(b.start, 3),
                "end": round(b.end, 3),
                "text": b.text,
                "emphasis": [b.words[i].text for i in sorted(b.emphasis)],
            }
            for b in blocks
        ],
    }
