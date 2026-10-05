"""The frame-level agreement the NLE docs argue from, recomputed from the
committed corpus.

``docs/METRICS.md`` A4.2/A4.3 and ``docs/NLE_COMPATIBILITY.md`` make specific
numerical claims about how well the exported timeline agrees with the render:
that the EDL and the FCPXML agree to 0.00 frames, and that the worst
timeline-vs-render gap is 0.69 frames. Those numbers were once stale — an
earlier METRICS.md reported a worst case of 1.63 frames over six clips with a
per-clip table (24.858/24.892, 17.084/17.138, ...) that matches no committed
timeline at all, and ``tools/nle_check.py`` cited frame counts (746 vs 745,
513 vs 512, 493 vs 493) that match no committed render. This module recomputes
the real figures so they cannot drift again.

One asymmetry matters and is not hidden: the EDLs and FCPXMLs are committed
(27 of each), but the rendered MP4s are not — they are gitignored. A4.3 is
therefore reproducible from a clean checkout and is asserted unconditionally;
A4.2 is only measurable where the renders exist, and is skipped with a stated
reason rather than passed vacuously when they do not.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.nle_check import check_project  # noqa: E402

# The committed corpus: every project that carries a timeline/ directory.
# 27 clips have both an EDL and an FCPXML committed.
PROJECTS = sorted(p for p in (ROOT / "corpus").glob("*_run/project")
                  if (p / "timeline").is_dir())

_FRAME_RE = re.compile(r"\(([\d.]+) frames\)")


def _findings() -> list[tuple[str, bool, str, str]]:
    """Every nle_check finding over the committed corpus, with its project."""
    out = []
    for project in PROJECTS:
        for name, ok, detail in check_project(project)[1]:
            out.append((name, ok, detail, project.parent.name))
    return out


def _frames(detail: str) -> float | None:
    m = _FRAME_RE.search(detail)
    return float(m.group(1)) if m else None


# --- A4.3: EDL vs FCPXML, reproducible from committed artifacts ------------


def test_the_corpus_is_present():
    """The whole point of these numbers is that they are measured, not chosen.
    A corpus that went missing would make every test below vacuous, so the
    count is asserted up front."""
    assert PROJECTS, "no corpus projects with a timeline/ directory were found"
    edls = list((ROOT / "corpus").glob("*_run/project/timeline/*.edl"))
    # 30 = the 27 per-clip EDLs the corpus has always carried, plus a program
    # reel each for the three media-holding runs, added by the 2026-10-05
    # re-run. The reel is one EDL for a whole concatenation, so it counts as one
    # here even though it describes every clip.
    assert len(edls) == 30, (
        f"30 clips have committed EDLs; found {len(edls)}. The counts in the "
        f"docs are stated per clip and move with this number"
    )


def test_edl_and_fcpxml_agree_exactly_on_every_clip():
    """A4.3. Two emitters on separate code paths describing the same cut.
    This is reproducible from committed artifacts alone and is asserted
    unconditionally."""
    deltas = []
    for name, ok, detail, _project in _findings():
        if "edl == fcpxml" not in name:
            continue
        assert ok, f"an EDL/FCPXML pair disagrees: {detail}"
        delta = _frames(detail)
        if delta is not None:
            deltas.append(delta)
    assert deltas, "no edl==fcpxml findings were produced at all"
    worst = max(deltas)
    assert worst == pytest.approx(0.0, abs=1e-9), (
        f"the docs claim 0.00 frames on all clips; the worst measured is "
        f"{worst:.2f}"
    )


# --- A4.2: timeline vs render, measurable only where renders exist ---------


def _render_findings() -> list[tuple[float, str]]:
    """(delta, detail) for every clip/render pair, or [] if no renders exist."""
    pairs = []
    for name, ok, detail, _project in _findings():
        if "timeline ==" not in name or "SKIP" in detail:
            continue
        delta = _frames(detail)
        if delta is not None:
            pairs.append((delta, detail))
    return pairs


def test_renders_present_are_measured_or_skipped_out_loud():
    """The skip is visible. A test that passes without measuring anything is
    the failure mode this suite exists to prevent, so the absence of renders is
    reported rather than silently counted as agreement."""
    pairs = _render_findings()
    if not pairs:
        pytest.skip(
            "no rendered MP4s in the corpus (they are gitignored); A4.2 is "
            "measured on this machine only and is not reproducible from a "
            "clean checkout"
        )


@pytest.mark.skipif(
    not _render_findings(),
    reason="no rendered MP4s in the corpus; A4.2 needs the gitignored renders",
)
def test_timeline_vs_render_worst_case_is_what_the_docs_claim():
    """A4.2. The docs cite 0.69 frames as the worst case; anything materially
    different means either the renders or the docs moved and the other was not
    updated."""
    pairs = _render_findings()
    worst = max(d for d, _ in pairs)
    assert worst == pytest.approx(0.69, abs=0.02), (
        f"the docs cite a worst case of 0.69 frames; measured {worst:.2f} over "
        f"{len(pairs)} clip/render pairs"
    )


@pytest.mark.skipif(
    not _render_findings(),
    reason="no rendered MP4s in the corpus; A4.2 needs the gitignored renders",
)
def test_every_timeline_render_pair_passes_the_documented_tolerance():
    """The 2-frame tolerance is the contract that makes the measured spread
    safe: it must cover the worst case with room, not sit on top of it."""
    pairs = _render_findings()
    worst = max(d for d, _ in pairs)
    assert worst <= 2.0
    # 2.0 / 0.69 = 2.9x headroom, which is what the docs claim. The bar is set
    # at 2.5x rather than 3.0 because the measured value is 2.9x and the
    # statement is "~3x"; if the gap ever closes on 2.5x the justification for
    # 2.0 frames is gone and the tolerance should be re-measured rather than
    # left to look deliberate.
    assert worst < 2.0 / 2.5, (
        f"the worst gap is {worst:.2f} frames against a 2.0-frame tolerance, "
        f"a ratio of {2.0 / worst:.1f}x; the '~3x headroom' claim no longer "
        f"holds"
    )


@pytest.mark.skipif(
    not _render_findings(),
    reason="no rendered MP4s in the corpus; A4.2 needs the gitignored renders",
)
def test_the_documented_band_set_is_the_measured_one():
    """The docs say the non-exact pairs fall into narrow bands. That is a
    specific claim about the shape of the discrepancy, so it is pinned: a new
    band appearing is a change worth noticing, not noise.

    The 2026-10-05 re-run widened the set from three bands to six. That is not a
    wider spread — the largest gap moved 0.69 -> 0.70 frames — but the three
    media-holding sources were re-rendered and their per-clip durations moved by
    fractions of a second, so the pairs that were 0.63/0.69 now also read
    0.64/0.65/0.70 on the clips whose length changed. Every one is the same
    container-overhead shortfall the section has always described (~21 ms), just
    measured against a different whole-frame count.
    """
    bands = sorted({round(d, 2) for d, _ in _render_findings()})
    assert bands == [0.0, 0.53, 0.63, 0.64, 0.65, 0.69, 0.7], (
        f"the documented bands are 0.00/0.53/0.63/0.64/0.65/0.69/0.70; "
        f"measured {bands}"
    )


# --- the docs cite these figures, so the docs are checked against them -------
#
# The defect this whole module was written for was not a wrong measurement: it
# was docs citing figures that matched no committed artifact (1.63 frames over
# six clips, 746 vs 745 frames). Pinning the artifacts alone does not stop that
# — the artifacts were always fine. So the cited numbers are read back out of
# the prose and compared with what the corpus measures.

DOC_FILES = [
    ROOT / "docs" / "METRICS.md",
    ROOT / "docs" / "NLE_COMPATIBILITY.md",
]


def _doc_text(path: Path) -> str:
    """One document's prose, with any `stale-begin`/`stale-end` block removed.

    A correction note has to be able to quote the wrong figure it is correcting;
    without this exclusion the gate would reject the very sentence that fixes
    the defect. The markers are explicit rather than inferred from a blockquote
    so that a note which does not carry them is still checked in full.
    """
    out = []
    keeping = True
    for line in path.read_text(encoding="utf-8").splitlines():
        if "stale-begin" in line:
            keeping = False
        if keeping:
            out.append(line)
        if "stale-end" in line:
            keeping = True
    return "\n".join(out)


# Each cited figure is pinned to the document that states it, not to the pair.
# Both files cite both figures, so a gate that searched their concatenation was
# blind to the one failure mode it existed for: a figure drifting in one
# document while the other still carried it, the gate staying green the whole
# time. That is how the 1.63-frame figure survived for as long as it did.
#
# The four gates below have each been confirmed to fire by mutation — changing
# the cited figure in one file only, and reverting. A gate that has never been
# watched fail is an assumption, not a check.
DOC_CLAIMS = [
    (
        ROOT / "docs" / "METRICS.md",
        "0.70 frames",
        "A4.2's worst-case timeline-vs-render gap",
    ),
    (
        ROOT / "docs" / "NLE_COMPATIBILITY.md",
        "0.70 frames",
        "the worst-case gap the 2-frame tolerance is justified by",
    ),
    (
        ROOT / "docs" / "METRICS.md",
        "0.00 frames on all 30",
        "A4.3's EDL-vs-FCPXML agreement, stated over all 30 timelines",
    ),
    (
        ROOT / "docs" / "NLE_COMPATIBILITY.md",
        "0.00 frames on all 30",
        "the same A4.3 figure, restated in the NLE summary",
    ),
]

# The superseded figures, each already replaced by a measured value. Checked
# per document for the same reason: a correction undone in one file is a defect
# whether or not the other file still avoids the phrase.
#
# The two paired durations are banned as the *table row*, not as bare numbers:
# `24.858` is no longer unreproducible — it is the committed nasa360-ep5 clip-01
# timeline span and the duration of its render, which the pin test below reads
# straight out of the audit. What was fabricated was the *pairing*
# (24.858 s of timeline against 24.892 s of render, a 1.00-frame disagreement)
# and the 17.084/17.138 pairing beside it; neither pair matches any committed
# artifact. Banning the bare number would forbid the page from quoting a value
# the corpus now genuinely measures.
STALE_FIGURES = (
    "1.63 frames", "746 rendered", "745-frame", "513 vs 512",
    "493 frames", "~0.04 s", "24.858/24.892", "17.084",
)


def test_no_document_repeats_the_unreproducible_figures():
    """The correction has to hold in both files, independently."""
    for path in DOC_FILES:
        text = _doc_text(path)
        for stale in STALE_FIGURES:
            assert stale not in text, (
                f"{stale!r} is back in {path.name}; it matched no committed "
                f"artifact and was replaced with a measured value"
            )


def test_each_document_states_the_figure_it_claims():
    """Per-document. A figure that has drifted in one file is a defect in that
    file, whether or not its neighbour still carries the right number."""
    for path, figure, what in DOC_CLAIMS:
        assert path.is_file(), f"{path} moved or was renamed; DOC_CLAIMS is stale"
        assert figure in _doc_text(path), (
            f"{path.name} no longer states {figure!r} ({what}). Either the "
            f"measurement moved and the doc is now right — in which case the "
            f"measurement tests above have to move with it — or the doc "
            f"drifted, and those tests are what is still holding the real value."
        )


def test_the_da_vinci_experiment_quotes_the_committed_clip_01_span():
    """The closing experiment names a duration a reader will check against a
    render. It has to be the span the committed timeline measures, not a figure
    carried over from the superseded per-clip table — which quoted 24.858 s
    against a 24.892 s render that matches nothing, and is why the bare number
    was banned in the first place. The audit is the source here, not the doc:
    pinning the doc to the artifact is what makes the number reproducible."""
    nasa = ROOT / "corpus" / "nasa360-ep5_run" / "project"
    assert (nasa / "timeline" / "01.edl").is_file(), (
        "the nasa360-ep5 clip-01 timeline the experiment points at is gone; "
        "the experiment and this test both need a committed target"
    )
    spans = {d for name, _ok, d in check_project(nasa)[1]
             if name == "01 edl == fcpxml duration"}
    assert len(spans) == 1, (
        f"expected one clip-01 duration finding, got {sorted(spans)}"
    )
    m = re.search(r"edl ([\d.]+)s", spans.pop())
    assert m, "the edl==fcpxml finding no longer quotes the span in seconds"
    text = _doc_text(ROOT / "docs" / "NLE_COMPATIBILITY.md")
    assert m.group(1) in text, (
        f"NLE_COMPATIBILITY.md quotes a clip-01 duration other than the "
        f"{m.group(1)} s the committed timeline measures; the experiment's "
        f"figure has to come from the artifact it names"
    )
