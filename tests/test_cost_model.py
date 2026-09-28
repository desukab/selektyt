"""The cost model's printed figures are recomputable from the records they cite.

``docs/TIME_SAVINGS.md`` §2 is the most-quoted table in the repository: every
downstream cost figure, cloud number and speed ratio in ``docs/EVALUATION.md``,
``docs/TRACTION_DASHBOARD.md`` and the fundraising deck traces back to it. Until
this module existed nothing in the test suite read it, and in the two sessions
before it was written three of its figures were found wrong, all the same way --
a number printed in prose that no machine had recomputed from the record it
named: the render rows measured an encoder that had already been retired, the
speed range was derived from unpaired min/max bounds, and a speedup was computed
as ``1 - ratio`` rather than ``1/ratio - 1``. Each was found by a human
re-reading a record a test could have read.

This is the response, and it is deliberately the narrow one. It does not parse
the document's prose and it does not try to re-derive the total from every
stage. It finds the two render rows by their labels, extracts the figures they
print, and recomputes them from ``corpus/render/runs/pentagon-30s-ass.json``
using the table's own stated conventions. If the record changes, or the table's
arithmetic drifts from it, or the table is reformatted so a label no longer
matches, these fail loudly rather than letting the orphaned figure keep
circulating.

The constants the scaling depends on -- 99.254 s of delivered output per preset
and 1469.77 s of source -- are the reference job's own, stated in §2 and held
here as named values rather than re-derived, because re-deriving them would mean
re-running a job whose source has been deleted to free disk. What is checked is
that everything *downstream* of them follows, which is where the three errors
actually lived.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

RECORD = ROOT / "corpus" / "render" / "runs" / "pentagon-30s-ass.json"
DOC = ROOT / "docs" / "TIME_SAVINGS.md"

# The reference job's own figures, per docs/TIME_SAVINGS.md §2. The run shipped
# 6 clips x 2 presets = 99.254 s of video per preset; the source is 1469.77 s.
DELIVERED_SECONDS_PER_PRESET = 99.254
SOURCE_SECONDS = 1469.77

# The table's Wall column is the window's wall scaled to the job's own output
# length, so one render row's wall is RTF * DELIVERED_SECONDS_PER_PRESET. The
# RTF column is that scaled wall over the whole source. Both are the document's
# stated conventions, restated here so a change to them has to be made in two
# places or nowhere.

_ROW = re.compile(
    r"^\| Render (?P<orient>16:9|9:16)[^|]*\| \*\*(?P<wall>[\d.]+) s\*\* "
    r"\| (?P<rtf>[\d.]+) \|",
    re.MULTILINE,
)


@pytest.fixture(scope="module")
def record():
    if not RECORD.is_file():
        pytest.skip(f"{RECORD} is cited by docs/TIME_SAVINGS.md and is gone")
    return json.loads(RECORD.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def table(record):
    """The two render rows, as a label -> (orientation, wall, rtf) mapping."""
    text = DOC.read_text(encoding="utf-8")
    rows = {}
    for m in _ROW.finditer(text):
        rows[m.group("orient")] = (float(m.group("wall")), float(m.group("rtf")))
    if len(rows) != 2:
        pytest.fail(
            f"found {len(rows)} Render rows in {DOC}; the table's format has "
            f"changed and every downstream figure needs re-verifying by hand")
    return rows


def test_the_record_is_the_shipped_encoder_pair(record):
    """Both rows are scaled from one record, so the record has to be the shipped
    encoder. A record of a retired preset would make the whole table a fiction
    in the present tense, which is what happened with ``veryfast``/crf20."""
    assert record["kind"] == "ship", (
        f"the table cites {RECORD.name} for a shipped row, but its kind is "
        f"{record['kind']!r}")
    assert record["encoder"] == ["-preset", "slow", "-crf", "18",
                                 "-pix_fmt", "yuv420p"], (
        f"the table's rows say shipped slow/crf18; the record encodes with "
        f"{record['encoder']}")


@pytest.mark.parametrize("orientation", ["16:9", "9:16"])
def test_the_printed_rtf_is_recomputable_from_the_record(table, record,
                                                         orientation):
    """The record's own RTF (0.8221 at 16:9) is the wall over the *output*
    length of the window, and the table's RTF column (0.05552) is that scaled to
    the job and divided by the *source* length. Those are two different ratios
    with two different denominators, and conflating them is the exact mistake an
    earlier pass made when it divided a raw window wall by the source length and
    landed 1.8x wrong. Both are checked, in both directions.

    The record also carries ``median_rtf`` and ``worst_rtf``; the table is built
    on the best-window one, which is the convention worth stating because it is
    the one that flatters the pipeline, and a silent switch to median would move
    the whole cost model without any defect being reported.
    """
    wall, table_rtf = table[orientation]
    render = next(r for r in record["renders"] if r["orientation"] == orientation)
    # The record's RTF is internally consistent: its own wall over its own
    # output length, so a hand-edit of either field is caught at the source.
    recomputed = render["best_wall_seconds"] / render["content_seconds"]
    assert recomputed == pytest.approx(render["rtf"], abs=5e-4), (
        f"{orientation}: the record's rtf {render['rtf']} does not match its "
        f"own {render['best_wall_seconds']} s / {render['content_seconds']} s "
        f"= {recomputed:.4f}")
    # Then the table's two columns follow from it by the scaling stated above.
    scaled_wall = render["rtf"] * DELIVERED_SECONDS_PER_PRESET
    assert wall == pytest.approx(scaled_wall, abs=0.05), (
        f"{orientation}: the table prints {wall} s, but the record's RTF "
        f"{render['rtf']} x {DELIVERED_SECONDS_PER_PRESET} s gives "
        f"{scaled_wall:.2f} s")
    assert table_rtf == pytest.approx(scaled_wall / SOURCE_SECONDS, abs=1e-4), (
        f"{orientation}: the table's RTF column prints {table_rtf}, but "
        f"{scaled_wall:.2f} s / {SOURCE_SECONDS} s = "
        f"{scaled_wall / SOURCE_SECONDS:.5f}")


def test_the_render_share_the_changelog_is_about(table):
    """The headline finding of the 2026-09-28 correction is that render went
    from 5.5% of the machine budget to 24%, which is a property of the two rows
    above and the document's own total. It is recomputed here because it is the
    one number in the document someone is most likely to repeat in a meeting.

    The total is the A5 assumption-audit row ("Machine time for the reference
    source, 488.8 s"), which is the document's statement of it; the §2 table's
    own total line is a cost figure rather than seconds, so the audit row is
    what the regex anchors on."""
    render_wall = sum(wall for wall, _ in table.values())
    text = DOC.read_text(encoding="utf-8")
    total = re.search(r"(?<![\d.])(\d{3}\.\d) s \((\d+\.\d) min\)", text)
    if not total:
        pytest.fail("could not find the document's 'N s (M min)' machine-time total")
    share = render_wall / float(total.group(1))
    assert 0.20 <= share <= 0.28, (
        f"render is {share:.0%} of the document's {float(total.group(1))} s "
        f"total ({render_wall:.1f} s of render), but the document claims 24%")
