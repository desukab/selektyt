"""One caption width policy, shared by every caption writer.

The repository had three copies of "how wide is a glyph" that disagreed:
``engine/captions.py`` used ``east_asian_width`` (so a Telugu glyph was one
column and an em dash was two), ``engine/caption_director.py`` used
``ord(ch) >= 0x0900`` (so every Telugu code point was two, and a virama that
draws nothing cost as much as a consonant), and the timeline test carried a
third copy. Two of them could not both be right.

These tests pin the policy that replaced all three, and — where a font is
actually installed — check it against the font's real advance widths rather
than against another guess.
"""
from __future__ import annotations

import shutil

import pytest

from engine.caption_director import caption_width
from engine.captions import _width as captions_width
from engine.timeline_captions import _width as timeline_width

_TELUGU_FONT = "/tmp/dasha_fonts/NotoSansTelugu-Regular.ttf"
_LATIN_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _have_fonts() -> bool:
    try:
        from PIL import ImageFont  # noqa: F401
    except ImportError:
        return False
    return shutil.which("fc-list") is not None and all(
        __import__("os").path.isfile(p) for p in (_TELUGU_FONT, _LATIN_FONT)
    )


# ── the policy itself ──


def test_ascii_is_one_column_per_character():
    assert caption_width("") == 0
    assert caption_width("cut") == 3
    # spaces are characters too: the caller decides whether to count them
    assert caption_width("the cut") == 7


def test_latin_extended_and_cyrillic_are_one_column():
    for text in ("café", "résumé", "naïve", "Århus", "grüße", "Привет"):
        assert caption_width(text) == len(text), text


def test_a_combining_mark_costs_nothing():
    # "é" precomposed and "e" + U+0301 must be the same width: the diacritic
    # modifies the base, it does not advance the pen.
    assert caption_width("café") == caption_width("café") == 4
    # a mark with no base is still zero, not negative
    assert caption_width("́") == 0


def test_east_asian_glyphs_are_two_columns():
    assert caption_width("日本語") == 6
    assert caption_width("テスト") == 6
    # fullwidth digits and Latin, not just Han
    assert caption_width("ＡＢ") == 4
    # mixed lines mix rates
    assert caption_width("HD画質") == 6


def test_an_indic_syllable_is_two_columns_not_two_per_code_point():
    # ప్ర is ప + ് + ర: three code points, one glyph cluster. The code-point
    # rule charged 6 for it and broke Telugu lines at a third of their capacity.
    assert caption_width("ప్ర") == 2
    assert caption_width("ప్రారంభం") == 6
    # a vowel sign does not add a column either
    assert caption_width("క") == caption_width("కా") - 0
    assert caption_width("కా") == 2
    # the spacing anusvara "ం" is Mc with combining class 0 — it must still be
    # absorbed, or ordinary Telugu prose doubles in width
    assert caption_width("మనం") == 4


def test_devanagari_conjuncts_are_absorbed():
    assert caption_width("क्ष") == 2          # क + ् + ष
    assert caption_width("विद्या") == 4


def test_an_emoji_with_a_skin_tone_modifier_is_one_glyph():
    # 👍🏽 is 👍 + ZWJ + 🏽. Charging for the joiner and the modifier would make
    # a single wide glyph cost four columns.
    assert caption_width("👍🏽") == 2
    assert caption_width("👍") == 2


def test_punctuation_is_one_column():
    # the old east_asian_width copy charged two for an em dash; in the fonts we
    # ship it measures ~1.2 columns, so one is the closer and safer rate
    assert caption_width("— and …") == 7
    assert caption_width("Hello, World!") == 13


def test_all_three_caption_writers_share_one_policy():
    """A disagreement here means the cue wrapper and the ASS/SRT writer can
    disagree about whether the same line fits."""
    probes = ["the cut", "café résumé", "ప్రారంభం చిన్న", "日本語テスト", "👍🏽 ok"]
    for text in probes:
        assert captions_width(text) == timeline_width(text) == caption_width(text)


# ── checked against the fonts themselves, where they exist ──


@pytest.mark.skipif(not _have_fonts(), reason="no Telugu/Latin fonts installed")
def test_the_estimate_is_conservative_for_real_telugu():
    """The point of a width estimate is that a caption stays inside the safe
    area. Measuring the font's actual advance widths bounds the error instead
    of asserting the heuristic is exact."""
    from PIL import ImageFont

    telugu = ImageFont.truetype(_TELUGU_FONT, 100)
    latin = ImageFont.truetype(_LATIN_FONT, 100)
    # one column = the mean advance of ordinary English lowercase prose, in the
    # font the captions actually render in
    prose = "the quick brown fox jumps over lazy dogs and the team celebrated"
    column_px = latin.getlength(prose) / len(prose)

    words = ["ఇది", "ఒక", "చిన్న", "పరీక్ష।", "మనం", "కలిసి", "నేర్చుకుందాం।"]
    total_real = total_estimate = 0.0
    for word in words:
        real = telugu.getlength(word) / column_px
        estimate = caption_width(word)
        # a single word must not read as narrower than the font draws it by
        # more than a glyph's worth — the force-wrap fallback exists for the
        # rare word that does not fit, it is not a licence to be arbitrarily wrong
        assert estimate >= real - 1.0, (word, real, estimate)
        total_real += real
        total_estimate += estimate
    # conservative in the mean: a line of these words is broken no earlier than
    # the truth, so it never runs past the safe area
    assert total_estimate >= total_real
    # and it is not so conservative that Telugu lines hold a third of what
    # they could: the old rule scored +145% here
    assert total_estimate / total_real < 1.8


@pytest.mark.skipif(not _have_fonts(), reason="no Telugu/Latin fonts installed")
def test_telugu_captions_now_use_far_more_of_the_line():
    """The defect this fixed, stated as a capacity number: with the old
    code-point rule a 34-column line admitted only ~13 columns of real Telugu
    text; the new policy admits close to the full width."""
    from PIL import ImageFont

    telugu = ImageFont.truetype(_TELUGU_FONT, 100)
    latin = ImageFont.truetype(_LATIN_FONT, 100)
    prose = "the quick brown fox jumps over lazy dogs and the team celebrated"
    column_px = latin.getlength(prose) / len(prose)

    text = "ప్రారంభం చిన్న పరీక్ష నేర్చుకుందాం కలిసి మనం"
    real = telugu.getlength(text.replace(" ", "")) / column_px

    def old_rule(s: str) -> int:
        return sum(2 if ord(c) >= 0x0900 else 1 for c in s)

    max_width = 34
    # how many columns of real Telugu text each rule lets onto a 34-column line
    new_capacity = max_width * real / caption_width(text)
    old_capacity = max_width * real / old_rule(text)
    assert new_capacity > 2.0 * old_capacity, (new_capacity, old_capacity)
    # 26.8 of the 34 nominal columns on the measured fixture: the line is used
    # for ~79% of its width, where the old rule used ~37%
    assert new_capacity > 24, new_capacity
    assert old_capacity < 16, old_capacity
