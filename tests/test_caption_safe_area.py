"""The width policy's one untested consequence: does a widest-legal caption
actually stay on the screen?

``caption_width`` and the 34-column limit were unified in d7a259b, and the
policy is checked against the fonts' advance widths in ``test_caption_width``.
What nothing checked was the relation that makes the policy mean anything: the
budget has to fit between the safe-area margins at the preset's font size, on
the canvas the ASS declares. A font size bumped by 4 px, or a margin halved,
leaves the width number untouched and every cue of a wide line wrapping to
twice as many lines as the layout assumes — and the suite would stay green,
because the arithmetic that produces the number never touches the pixels.

So this measures the pixels. The widest line the policy admits is burned onto a
canvas of the declared size with the real ffmpeg/libass path, and the rendered
lines are counted and compared against the cue's own ``max_lines``. It is the
only test in the suite that can catch a geometry change the width policy does
not know about.

Why the count and not the horizontal extent: libass wraps a line that does not
fit rather than clipping it, so a width-policy error never runs a caption off
the edge of the frame. It doubles the cue's height instead, which is worse in
one way and better in another — nothing is unreadable, but a cue the code
believes is two lines is four, and the reading-time hold computed from 34
columns is then shorter than the text a viewer actually has to read. Measuring
rendered line count catches that; measuring the right-hand pixel does not.

What it does *not* claim, and is stated because the gap is easy to overread:
the ASS declares its font by name and libass resolves it through fontconfig, so
the font that renders is whichever the host substitutes. On this build host
"Noto Sans" is not installed and DejaVu Sans is substituted — wider than Noto,
which makes the bound conservative, but it is a bound measured against a font
the file does not name. The resolved font is recorded in the test output, so a
host where the substitution goes the other way is visible rather than silent.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from engine.caption_director import (MAX_LINE_WIDTH, MAX_WORDS, _is_emoji_component,
                                     _is_indic, _is_mark, _is_virama, caption_width,
                                     max_line_width_for)
from engine.captions import (MARGIN_H, PLAYRES_X, caption_geometry,
                             caption_presets, preset_for)
from engine.timeline_captions import CaptionConfig

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
try:
    import PIL  # noqa: F401
except ImportError:
    _HAVE_PIL = False
else:
    _HAVE_PIL = True
pytestmark = [
    pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not available"),
    # ``_ink`` measures the rendered PNG to count visible caption lines, which
    # is what makes these assertions about line wrapping measurements rather
    # than inspections of the ASS text. Without the imaging library the check
    # cannot look at the render at all, and a skip names that rather than
    # reporting a measurement it never made.
    pytest.mark.skipif(not _HAVE_PIL, reason="Pillow is not installed"),
]


def _resolved_font(declared: str) -> str:
    """What fontconfig actually answers for the name the ASS declares.

    libass resolves through fontconfig, so this is the font that renders — and
    it is not always the one the file names.
    """
    fc = shutil.which("fc-match")
    if fc is None:
        return "unknown (no fc-match)"
    proc = subprocess.run(
        [fc, "--format=%{family} %{style}", declared],
        capture_output=True, text=True, timeout=15,
    )
    return proc.stdout.strip() or "unresolved"


_DECLARED = "Noto Sans"


def _script_is_supported(lang: str) -> bool:
    """Whether fontconfig can resolve this script at all on this host.

    libass falls back to ``.notdef`` placeholder boxes for a code point no
    installed font carries, and a box is full em wide — wider than most real
    glyphs and unrelated to the shapes a width policy is calibrated on. A burn
    on such a host measures boxes, not type, so this is the gate that keeps a
    render assertion from being evidence about a script it never drew.
    """
    fc = shutil.which("fc-list")
    if fc is None:
        # Without fontconfig the substitution is unknown either way, so the
        # burn is not trusted — the same conclusion, reached conservatively.
        return False
    try:
        proc = subprocess.run([fc, f":lang={lang}", "family"],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(proc.stdout.strip())


def _charges(text: str) -> tuple[int, int, int]:
    """How ``caption_width`` charges ``text``: (clusters, single columns, wide
    columns).

    The counter, not the width: a Telugu word is a sequence of clusters, and the
    policy charges two columns per cluster. The width is the sum of the parts —
    clusters at two, separators and punctuation at one, wide glyphs at two — and
    a width that exceeds its parts has regressed to charging per code point,
    which is the state the virama absorption exists to prevent.
    """
    import unicodedata

    clusters = singles = wide = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        i += 1
        if _is_mark(ch) or _is_emoji_component(ch):
            continue
        if _is_indic(ch):
            clusters += 1
            pending = False
            while i < n:
                c = text[i]
                if _is_mark(c) or _is_emoji_component(c):
                    pending = pending or _is_virama(c)
                    i += 1
                    continue
                if pending and _is_indic(c):
                    i += 1
                    pending = False
                    continue
                break
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            wide += 1
        else:
            singles += 1
    return clusters, singles, wide


@pytest.fixture(scope="module")
def resolved() -> str:
    """Resolved once and printed, so a host that substitutes a *narrower* font
    than the declared one is visible rather than silently making the bound look
    better."""
    which = _resolved_font(_DECLARED)
    print(f"\n  ASS declares {_DECLARED!r}; fontconfig resolves {which!r}")
    return which


def _ass_for_a_line(line: str, style: str) -> str:
    """The same ASS ``engine.captions`` writes, for one cue holding ``line``.

    Built by hand against ``preset_for`` and ``caption_geometry`` rather than by
    calling the writer, so a change to either is what fails the test — the
    writer's own formatting is not what is under measurement here.
    """
    p = preset_for(style)
    geo = caption_geometry()
    return (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {geo['playres_x']}\nPlayResY: {geo['playres_y']}\n"
        "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,"
        "Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,"
        "Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,"
        "Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{_DECLARED},{p['fontsize']},{p['primary']},"
        f"&H00FFFFFF,{p['outline']},{p['back']},{p['bold']},0,0,0,100,100,0,0,1,"
        f"{p['outline_w']},{p['shadow']},2,{geo['margin_h']},{geo['margin_h']},"
        f"{p['margin_v']},1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,"
        "MarginL,MarginR,MarginV,Effect,Text\n"
        f"Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,{line}\n"
    )


def _burn(ass: Path, png: Path) -> None:
    """Lay the cues out on a canvas of the declared size and rasterise one frame."""
    canvas = f"color=c=black:s={PLAYRES_X}x{caption_geometry()['playres_y']}:d=1:r=1"
    # A colon in a Windows drive letter is the filtergraph's option separator;
    # the ASS path is escaped the way engine/render.py escapes it.
    safe = ass.as_posix().replace("'", "\\'").replace(":", "\\:")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", canvas,
         "-vf", f"ass='{safe}'", "-frames:v", "1", str(png)],
        check=True, timeout=180,
    )


def _ink(png: Path, fontsize: int) -> tuple[int, tuple[int, int], tuple[int, int]]:
    """The rendered line count and the ink's bounding box.

    Lines are contiguous bands of lit rows. A single rendered line is one band;
    a cue libass wrapped is two. Two bands closer than the threshold are one
    line, because a single line of descender-heavy text ("gjpqy") has an empty
    band of rows between its descenders and its x-height — that gap is 4 px at
    54 px, and counting it as a line break would report one line as two.

    The threshold is derived from the font size rather than hard-coded, because
    the two gaps it has to separate both scale with it: the intra-line gap of a
    descender-only line is ~0.07 em and the inter-line gap libass leaves is
    ~0.15 em (the outline and shadow bleed into it, which is why it is smaller
    than the font's own leading). 0.11 em sits between the two on every preset.
    """
    from PIL import Image
    import numpy as np

    img = np.asarray(Image.open(str(png)).convert("L"))
    lit = img > 40
    if not lit.any():
        return 0, (-1, -1), (-1, -1)
    rowsum = lit.sum(axis=1)
    bands: list[list[int]] = []
    start = None
    merge_gap = fontsize * 0.11
    for i, v in enumerate(rowsum):
        if v and start is None:
            start = i
        elif not v and start is not None:
            _push(bands, start, i, merge_gap)
            start = None
    if start is not None:
        _push(bands, start, len(rowsum), merge_gap)
    ys, xs = np.where(lit)
    return len(bands), (int(xs.min()), int(xs.max())), (int(ys.min()), int(ys.max()))


def _push(bands: list[list[int]], lo: int, hi: int, merge_gap: float) -> None:
    """Merge a band into the previous one when the gap between them is inside
    one line — the space between a line's descenders and its x-height is not a
    line break."""
    if bands and lo - bands[-1][1] < merge_gap:
        bands[-1][1] = hi
    else:
        bands.append([lo, hi])


def _widest_line(words: list[str], budget: int) -> str:
    """Fill a line to the column budget, so the measurement is of the worst
    case the policy admits rather than of an average caption."""
    line, kept = "", []
    for w in words:
        trial = " ".join(kept + [w])
        if caption_width(trial) > budget:
            break
        kept.append(w)
        line = trial
    assert caption_width(line) <= budget
    # A single word longer than the budget is the case the force-wrap fallback
    # exists for; the line must still carry something to measure.
    assert line, "no word fit the budget — the probe is measuring nothing"
    return line


_TITLE_CASE = ("The Quick Brown Fox Jumps Over The Lazy Dog And The "
               "Team Celebrated Late That Night In The Old Town").split()
_TELUGU = "ప్రారంభం చిన్న పరీక్ష నేర్చుకుందాం కలిసి మనం ఇప్పుడు".split()


@pytest.mark.parametrize("style", list(caption_presets()))
def test_a_line_at_the_budget_renders_as_one_line(
        tmp_path, style, resolved, capsys):
    """The policy's central claim, measured: a line of the budget's width,
    filled with title case — the honest worst case, because the policy charges
    a capital one column while a capital draws wider than the lowercase mean a
    column was calibrated on — has to render as one line and stay inside the
    safe area.

    Before the budget was derived per preset this failed for ``punch`` and
    ``spotlight``: a 34-column cue rendered as four lines, because 58 px bold
    does not fit 34 columns into the safe area. libass wrapped it, so nothing
    was unreadable — the cue was simply twice as tall as the layout assumed,
    and its reading-time hold was computed for half the text.
    """
    budget = max_line_width_for(preset_for(style))
    line = _widest_line(_TITLE_CASE, budget)
    ass = tmp_path / "probe.ass"
    ass.write_text(_ass_for_a_line(line, style), encoding="utf-8")
    png = tmp_path / "probe.png"
    _burn(ass, png)

    lines, x_bounds, _ = _ink(png, preset_for(style)["fontsize"])
    geo = caption_geometry()
    slack = (geo["playres_x"] - geo["margin_h"]) - x_bounds[1]
    print(f"  {style:10s} budget={budget:2d} cols, line={caption_width(line):2d} "
          f"cols → {lines} rendered line(s), x={x_bounds[0]}..{x_bounds[1]}, "
          f"slack={slack}px")
    assert lines == 1, (
        f"{style}: a {caption_width(line)}-column line at the policy's own "
        f"budget of {budget} wrapped to {lines} lines. The budget is wider "
        f"than this preset's font size admits on this canvas.")
    right_edge = geo["playres_x"] - geo["margin_h"]
    assert x_bounds[0] >= geo["margin_h"], (
        f"{style}: the caption's left edge ({x_bounds[0]}) is inside the left "
        f"margin ({geo['margin_h']}px)")
    assert x_bounds[1] < right_edge, (
        f"{style}: the caption's right edge ({x_bounds[1]}px) runs past the "
        f"safe area's right edge ({right_edge}px). The width budget does not fit "
        f"the preset's font size ({preset_for(style)['fontsize']}px) at this "
        f"margin — one of the three has to move, and the width policy cannot "
        f"tell which.")


@pytest.mark.parametrize("style", list(caption_presets()))
def test_a_two_line_cue_at_the_budget_renders_as_two_lines(
        tmp_path, style, resolved):
    """``_wrap`` admits a cue of up to ``max_lines`` lines, so the cue's own
    worst case is both lines at the budget. This is the case that doubled to
    four lines under the bold presets — which is not a wrapping detail, it is
    a cue the code committed to one shape rendering as another."""
    budget = max_line_width_for(preset_for(style))
    first = _widest_line(_TITLE_CASE, budget)
    rest = [w for w in _TITLE_CASE if w not in first.split()] or _TITLE_CASE
    second = _widest_line(rest, budget)
    ass = tmp_path / "two.ass"
    ass.write_text(_ass_for_a_line(first + "\\N" + second, style), encoding="utf-8")
    png = tmp_path / "two.png"
    _burn(ass, png)

    lines, x_bounds, y_bounds = _ink(png, preset_for(style)["fontsize"])
    geo = caption_geometry()
    print(f"  {style:10s} two lines of {caption_width(first)}+"
          f"{caption_width(second)} cols → {lines} rendered, y={y_bounds}")
    assert lines == 2, (
        f"{style}: a two-line cue at the policy's budget rendered as {lines} "
        f"lines. The cue's height is what the reading-time hold is computed "
        f"against, so this is a cue laid out for one shape and shipped as another.")
    assert x_bounds[1] < geo["playres_x"] - geo["margin_h"], \
        f"{style}: a budget-width cue runs past the safe area"


@pytest.mark.parametrize("style", list(caption_presets()))
def test_the_widest_telugu_line_renders_as_one_line(tmp_path, style, resolved):
    """The script the width policy was written for. Telugu prose is the case
    where an incoherent policy is most visible: a column rule that overcharges
    a syllable cluster breaks these lines at a third of their width, and the
    defect reads as captions that never use the screen.

    What this can and cannot measure on a given host, stated because the gap is
    easy to overread as a pass: the render is only evidence about Telugu type
    if the host has a Telugu-capable font. If it does not, libass draws
    ``.notdef`` placeholder boxes, which were verified byte-identical to a
    deliberately-missing private-use glyph. A box is full em wide, far wider
    than a Telugu syllable, so a probe that wraps as boxes says nothing about
    the clusters the policy was calibrated on. On such a host the test
    measures the policy's arithmetic instead — that the widest Telugu line the
    policy admits really is at the budget, and is carried by whole syllable
    clusters, not by the code points the pre-cluster rule overcharged — and
    records that the pixel measurement is still outstanding.

    On a host that *does* carry a Telugu font the assertion is the real one;
    the branch probe below is what keeps the two honest, so no host is silently
    measuring the wrong thing. (This probe was written when the build host had
    no Telugu font; ``fonts-noto-core`` has since been installed and the pixel
    branch is the one taken here. The guard in ``engine/render`` now refuses a
    burn that would come out as boxes, so a host in that state fails loudly
    elsewhere instead of producing a measurement here.)
    """
    budget = max_line_width_for(preset_for(style))
    line = _widest_line(_TELUGU, budget)
    geo = caption_geometry()

    if not _script_is_supported("te"):
        clusters, singles, wide = _charges(line)
        # Not a skip: the policy half is still measured, and the render half is
        # what this prints instead of silently passing on a box measurement.
        print(f"  {style:10s} Telugu: no Telugu-capable font on this host "
              f"(fc-list :lang=te is empty); the burn draws .notdef boxes, so "
              f"the pixel measurement is NOT evidence about Telugu type. "
              f"Policy: {caption_width(line)} cols against a {budget}-column "
              f"budget, {len(line)} code points in {clusters} clusters "
              f"(+{singles} separators).")
        assert caption_width(line) <= budget, \
            f"{style}: the policy admits a Telugu line wider than its own budget"
        # Every column has to be accounted for: a cluster costs two, a separator
        # or a punctuation mark costs one, a wide glyph costs two. A width that
        # exceeds that sum is charging for code points inside a cluster, which is
        # the exact regression the virama absorption was written to prevent —
        # and the one that made Telugu lines break at a third of their width.
        assert caption_width(line) == 2 * clusters + singles + 2 * wide, (
            f"{style}: Telugu columns are not whole syllable clusters — "
            f"{caption_width(line)} cols from {clusters} clusters and {singles} "
            f"single-column characters means marks are being charged for")
        assert clusters > singles, \
            f"{style}: the widest Telugu line is not Telugu-dominant prose"
        return

    ass = tmp_path / "te.ass"
    ass.write_text(_ass_for_a_line(line, style), encoding="utf-8")
    png = tmp_path / "te.png"
    _burn(ass, png)

    lines, x_bounds, _ = _ink(png, preset_for(style)["fontsize"])
    print(f"  {style:10s} budget={budget}, Telugu line={caption_width(line)} "
          f"cols → {lines} line(s), x={x_bounds} (resolved font {resolved!r})")
    assert lines == 1, f"{style}: a Telugu line at the budget wrapped to {lines} lines"
    assert x_bounds[1] < geo["playres_x"] - geo["margin_h"], \
        f"{style}: a Telugu line at the budget runs past the safe area"


def test_the_derived_budget_is_under_the_measured_capacity(resolved):
    """The derivation is calibrated against the resolved font, which is wider
    on some hosts than others, so it is deliberately conservative: every
    derived budget must be below the widest line that host actually renders on
    one line. Asserting the derivation *and* the render keeps a calibration
    drift from looking like a pass."""
    for style, preset in caption_presets().items():
        budget = max_line_width_for(preset)
        # A line of the budget's width in title case must be admittable at all,
        # and must be narrower than the pre-derivation constant it replaced.
        probe = _widest_line(_TITLE_CASE, budget)
        assert caption_width(probe) <= budget
        print(f"  {style:10s} derived budget {budget} columns "
              f"(was {MAX_LINE_WIDTH} before derivation)")


def test_every_writer_lays_out_to_one_width_budget():
    """The budget is derived rather than copied, so a writer that widened its
    own line could not quietly outgrow the canvas the others measured."""
    from engine.caption_director import build_blocks
    from engine.captions import _group_words

    assert build_blocks.__defaults__[0] == MAX_WORDS
    assert _group_words.__defaults__[0] == MAX_WORDS
    # ``None`` is the sentinel that means "derive from the active preset", not
    # a budget of zero; a writer that defaulted to a number would silently keep
    # the fixed 34 the derivation replaced.
    assert build_blocks.__defaults__[1] is None, \
        "build_blocks no longer derives its width from the preset"
    assert _group_words.__defaults__[1] is None, \
        "_group_words no longer derives its width from the preset"
    # The word cap has to agree too: the policy breaks a line on either limit,
    # so a writer allowing more words lays out lines the other never makes.
    assert CaptionConfig().max_line_width is None, \
        "CaptionConfig no longer derives its width from the preset"


def test_the_geometry_the_width_is_derived_from_is_the_one_written():
    """``caption_width``'s column is meaningless without the canvas it is laid
    out on: the budget is a width only relative to a font size and a margin.
    The two ASS writers carry their own copies of those numbers, so this is the
    check that keeps them one copy."""
    geo = caption_geometry()
    config = CaptionConfig()
    assert config.playres_x == geo["playres_x"]
    assert config.playres_y == geo["playres_y"]
    assert config.margin_h == geo["margin_h"]
    assert geo["playres_x"] == PLAYRES_X and geo["margin_h"] == MARGIN_H


def test_the_two_ass_writers_lay_out_on_one_preset_table():
    """A second table would drift, and the drift would surface as one writer's
    cues no longer fitting the canvas the other measured."""
    from engine.timeline_captions import _ass_presets

    assert _ass_presets() == caption_presets()
    # And the table is complete: a style the selector accepts must be a style
    # the ASS writer can lay out, or the cue falls back to ``minimal`` silently.
    for name in caption_presets():
        for key in ("fontsize", "primary", "outline", "back", "bold",
                    "outline_w", "shadow", "margin_v"):
            assert key in preset_for(name), f"{name} is missing {key}"


def test_a_narrower_canvas_gets_a_proportionally_narrower_budget():
    """A timeline rendered narrower than 1080 has less safe area, and a cue
    sized for the reference canvas would wrap on it. The derivation takes the
    canvas, so the budget scales rather than the caption overflowing."""
    preset = preset_for("punch")
    full = max_line_width_for(preset, PLAYRES_X, MARGIN_H)
    half = max_line_width_for(preset, PLAYRES_X // 2, MARGIN_H)
    assert half < full, "a canvas half as wide must not admit the same number of columns"
    assert half <= full // 2 + 1
