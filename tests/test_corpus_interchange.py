"""Every committed interchange pair, read end to end by two independent readers.

`tools/nle_check.py` audits one project at a time and compares only aggregate
spans: the min rec_in to the max rec_out against the render, the min/max source
in/out against the edit plan. A single mis-timed clip in the middle of a
multi-event cut is invisible to an aggregate, so this file sweeps the whole
committed corpus and compares *each* clip.

Three things are asserted per pair, in ascending order of what they would mean
if false:

  1. `pycmx` accepts the EDL. A third-party CMX 3600 reader is the only
     independent opinion in the repo about whether the file is a well-formed
     EDL; the emitter's own parser proving the emitter's own output is
     circular.
  2. The repo's own `validate_edl` / `validate_fcpxml` are clean. These are
     the checks that run on emission, so a failure here means a committed
     artifact regressed rather than that it was never checked.
  3. Each FCPXML `<asset-clip>` carries the same offset / start / duration as
     its EDL event. Two formats written from one model must describe one cut;
     a disagreement would surface in an NLE as a clip playing the wrong
     material, and no existing test would have caught it.

The frame rate is read from the sibling FCPXML's `<format frameDuration>`
rather than from the source media, because `corpus_run.py` deletes the media by
default and seven of the eight projects have none left. `validate_edl` with no
rate reads a ``:`` separator as 30 fps, which reports spurious retime errors on
the 25 and 39 fps lists — the rate has to come from somewhere, and the
interchange file itself is the one place that always has it.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from engine.nle_export import validate_edl, validate_fcpxml
from tools.nle_check import _edl_events, _rational_seconds, _tc_seconds

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# The inventory is spelled out rather than globbed blind: a sweep that finds no
# files passes vacuously, and a corpus restructure would then silently delete
# every assertion in this file. Add a project here when a new run is committed.
EXPECTED_TIMELINE_PAIRS = {
    "emwcon-panel-2018_run": 5,
    "hadhazy-protest_run": 3,
    "nasa360-ep5_run": 6,
    "ooi-vertical_run": 3,
    "pentagon-esper-briefing_run": 5,
    "royal-society-cloke_run": 4,
    "telugu-tutorial_run": 1,
}

# One clip's values are each snapped to the frame grid independently, so a
# sum accumulated on the grid can sit one frame off the snapped total. The
# largest deviation measured across the whole committed corpus is exactly
# 1.000 frames; this covers it with margin and would fail on a real
# half-second clip slip.
CLIP_FRAME_TOLERANCE = 1.5

_RATIONAL = re.compile(r"(-?\d+(?:\.\d+)?)(?:/(\d+))?")


def _rate_of(root: ET.Element) -> float:
    """The frame rate a sequence declares, as a float.

    ``1001/30000s`` is 29.97: the rational is frame *duration*, so it divides
    through. Falls back to 30 only when no format element exists at all.
    """
    fmt = root.find(".//format")
    raw = (fmt.get("frameDuration") if fmt is not None else "") or ""
    match = _RATIONAL.fullmatch(raw.strip().removesuffix("s"))
    if match:
        num, den = float(match.group(1)), float(match.group(2) or 1)
        if den:
            return den / num
    return 30.0


def _timeline_pairs() -> list[tuple[Path, Path, str]]:
    """Every committed ``NN.edl`` next to its ``NN.fcpxml``."""
    pairs: list[tuple[Path, Path, str]] = []
    for project, expected in EXPECTED_TIMELINE_PAIRS.items():
        found = sorted((ROOT / "corpus" / project / "project" / "timeline").glob("*.edl"))
        assert len(found) == expected, (
            f"{project}: {EXPECTED_TIMELINE_PAIRS[project]} timeline pairs were "
            f"committed, this file now finds {len(found)}. Update "
            f"EXPECTED_TIMELINE_PAIRS so the sweep cannot shrink silently."
        )
        for edl in found:
            fcpxml = edl.with_suffix(".fcpxml")
            assert fcpxml.is_file(), f"{edl} has no sibling .fcpxml"
            pairs.append((edl, fcpxml, f"{project}-{edl.stem}"))
    return pairs


PAIRS = _timeline_pairs()


@pytest.mark.parametrize("edl, fcpxml, pair_id", PAIRS, ids=[p[2] for p in PAIRS])
def test_pycmx_accepts_every_committed_edl(edl, fcpxml, pair_id):
    """A third-party CMX 3600 reader must parse the file we committed."""
    pycmx = pytest.importorskip(
        "pycmx", reason="install the `nle` extra (pip install -e '.[nle]')"
    )
    events = list(pycmx.parse_cmx3600(io.StringIO(edl.read_text())).events)
    assert events, "pycmx parsed the EDL but found no edit events"
    assert sum(len(e.edits) for e in events) > 0


@pytest.mark.parametrize("edl, fcpxml, pair_id", PAIRS, ids=[p[2] for p in PAIRS])
def test_the_emitters_own_validators_are_clean(edl, fcpxml, pair_id):
    """The checks that run on emission must still pass on the artifact."""
    fps = _rate_of(ET.fromstring(fcpxml.read_bytes()))
    problems = validate_edl(edl.read_text(), fps=fps) + validate_fcpxml(fcpxml.read_text())
    assert not problems, f"{pair_id}: {problems}"


@pytest.mark.parametrize("edl, fcpxml, pair_id", PAIRS, ids=[p[2] for p in PAIRS])
def test_every_fcpxml_clip_matches_its_edl_event(edl, fcpxml, pair_id):
    """The two formats must describe one cut, clip by clip.

    The aggregate span can agree while an interior clip is wrong, which is the
    failure mode this sweep exists to catch. Clips and events are written in
    spine order, so the pairing is positional; a count mismatch is reported
    first because a zip would otherwise truncate the comparison and pass.
    """
    root = ET.fromstring(fcpxml.read_bytes())
    fps = _rate_of(root)
    events = _edl_events(edl.read_text())
    clips = [c for c in root.iter("asset-clip")]

    assert len(clips) == len(events), (
        f"{pair_id}: {len(clips)} FCPXML asset-clip(s) vs {len(events)} EDL "
        f"event(s); the positional pairing below would be wrong"
    )

    mismatches = []
    for event, clip in zip(events, clips):
        offset = _rational_seconds(clip.get("offset") or "0s")
        start = _rational_seconds(clip.get("start") or "0s")
        duration = _rational_seconds(clip.get("duration") or "0s")
        edits = event["edits"]
        rec_in = min(_tc_seconds(e["rec_in"], fps) for e in edits)
        src_in = min(_tc_seconds(e["src_in"], fps) for e in edits)
        src_out = max(_tc_seconds(e["src_out"], fps) for e in edits)

        for field, got, want in (
            ("offset", offset, rec_in),
            ("start", start, src_in),
            ("duration", duration, src_out - src_in),
        ):
            delta_frames = abs(got - want) * fps
            if delta_frames > CLIP_FRAME_TOLERANCE:
                mismatches.append(
                    f"event {event['number']} {field}: fcpxml {got:.3f}s vs edl "
                    f"{want:.3f}s ({delta_frames:.2f} frames)"
                )
    assert not mismatches, f"{pair_id} at {fps:.4f} fps: " + "; ".join(mismatches)


# ---------------------------------------------------------------------------
# The honesty of the headline claim, pinned.
#
# The sweep above measures agreement between two formats that SelekyT itself
# wrote. Only one of the two is ever read by software that does not share this
# codebase, and for a while the docs said "two independent parsers accept" --
# with the very next bullet naming `pycmx` and "the emitter's own validators",
# which is one independent reader and one mirror. The correction below is not
# cosmetic: an EDL↔FCPXML agreement figure means something different when both
# halves are read by the same hand than when one is read by a third party, and
# the number should not be able to travel without that qualification.
# ---------------------------------------------------------------------------
def test_the_independence_of_the_two_readers_is_stated_asymmetrically():
    """One half has a third-party reader, the other does not, and the docs must
    say so rather than averaging the two into 'two independent parsers'."""
    validation = (DOCS / "NLE_VALIDATION.md").read_text(encoding="utf-8")
    compatibility = (DOCS / "NLE_COMPATIBILITY.md").read_text(encoding="utf-8")
    header = (ROOT / "engine" / "fcpxml.py").read_text(encoding="utf-8")

    # The specific overstatement, and its close variants. Each was present
    # before the correction; a gate that could not see them is decorative.
    for phrase in (
        "two independent parsers",
        "two independent readers",
        "independent CMX 3600/FCPXML reader",
    ):
        assert phrase not in validation, (
            f"NLE_VALIDATION.md claims {phrase!r}, which averages one "
            f"independent reader and one of SelekyT's own into a plural"
        )
        assert phrase not in header, (
            f"engine/fcpxml.py claims {phrase!r} in the header that callers "
            f"and readers trust for the verification status"
        )

    # The counterweight has to be present too, or removing the overstatement
    # above could leave the FCPXML half described as independently read by
    # omission rather than by correction.
    assert "The independence is asymmetric" in validation, (
        "NLE_VALIDATION.md no longer carries the asymmetric-independence "
        "qualification next to the EDL/FCPXML claim"
    )
    assert "The FCPXML half is not validated by an independent reader" in compatibility, (
        "NLE_COMPATIBILITY.md no longer states that the FCPXML half has no "
        "independent reader"
    )
