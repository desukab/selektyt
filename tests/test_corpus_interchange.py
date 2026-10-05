"""Every committed interchange pair, read end to end by two independent readers.

`tools/nle_check.py` audits one project at a time and compares only aggregate
spans: the min rec_in to the max rec_out against the render, the min/max source
in/out against the edit plan. A single mis-timed clip in the middle of a
multi-event cut is invisible to an aggregate, so this file sweeps the whole
committed corpus and compares *each* clip.

Three things are asserted per pair, in ascending order of what they would mean
if false:

  1. `pycmx` accepts the EDL, and the vendored OpenTimelineIO adapter reads the
     FCPXML back as clips whose media resolves. Third-party readers are the
     only independent opinion in the repo about whether these files are what
     they claim to be; the emitter's own parser proving the emitter's own
     output is circular. The FCPXML reader has to clear a bar higher than
     "it parsed" — see the test below for why.
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
#
# The three media-holding runs (ooi, pentagon, royal-society) are one pair above
# their neighbours: the re-run on 2026-10-05 emitted a ``program.edl`` /
# ``program.fcpxml`` pair the 2026-09-19 artifacts did not have, because the
# program reel landed after they were first run. The extra pair is the reel, and
# it is held to the same per-clip assertions as the per-clip pairs — measured on
# it directly, worst offset deviation 0.000 frames on all three.
EXPECTED_TIMELINE_PAIRS = {
    "emwcon-panel-2018_run": 5,
    "hadhazy-protest_run": 3,
    "nasa360-ep5_run": 6,
    "ooi-vertical_run": 4,
    "pentagon-esper-briefing_run": 6,
    "royal-society-cloke_run": 5,
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
def test_the_independent_fcpxml_reader_resolves_every_clip(edl, fcpxml, pair_id):
    """A third-party FCPXML reader must read the document back as linked clips.

    The reader is the OpenTimelineIO FCPXML adapter, vendored verbatim under
    ``third_party/otio_fcpxml_adapter/`` (Apache-2.0; origin, commit and licence
    in ``third_party/PROVENANCE.md``). It is not the obvious candidate: the
    OpenTimelineIO project's own copy of this adapter reads an asset's ``src``
    off the ``<asset>`` element, and SelekyT writes ``src`` on the
    ``<media-rep kind="original-media">`` child, as FCPXML 1.9+ requires. That
    reader parses the spine, resolves no media, and returns every clip as a
    ``MissingReference`` -- a document that reads back as a clean timeline in
    which every cut is offline. Had it been accepted on the strength of a
    successful parse, this whole claim would be a false positive, so the two
    failure modes a media-rep-blind reader produces are what this test forbids:
    a parse that yields no clips at all, and any clip whose media reference is
    not an ``ExternalReference`` carrying a target URL.

    A document that fails here has not been "read by a library"; the thing an
    editor actually needs -- the file each cut points at -- was dropped on the
    way through.
    """
    otio = pytest.importorskip(
        "opentimelineio",
        reason="install the `nle` extra (pip install -e '.[nle]') for the "
               "independent FCPXML reader, which ships compiled extensions",
    )
    pytest.importorskip(
        "third_party.otio_fcpxml_adapter.fcpxml",
        reason="the vendored FCPXML reader is missing from third_party/",
    )
    from third_party.otio_fcpxml_adapter.fcpxml import FcpxXml

    obj = FcpxXml(fcpxml.read_text(encoding="utf-8")).to_otio()
    clips = list(obj.find_children(otio.schema.Clip))
    assert clips, (
        f"{pair_id}: the reader parsed the document but found no clips, so no "
        f"cut in it was read back by an independent parser"
    )

    unlinked = [
        repr(clip.name)
        for clip in clips
        if not (
            isinstance(clip.media_reference, otio.schema.ExternalReference)
            and clip.media_reference.target_url
        )
    ]
    assert not unlinked, (
        f"{pair_id}: {len(unlinked)} of {len(clips)} clip(s) did not resolve to "
        f"a media reference with a target URL: {unlinked}. The reader parsed the "
        f"spine but the media the cuts point at did not come back, so the export "
        f"would land offline in an NLE"
    )


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
# wrote, and agreement between two halves written by the same hand means less
# than agreement between halves read by readers that do not share that hand.
# Both halves now have an independent reader, but that was not always so: for a
# while the FCPXML half was parsed only by SelekyT's own `ElementTree` code
# while the docs averaged the two into "two independent parsers", and the test
# below exists because that wording was able to travel without its
# qualification. The correction it pins now runs the other way too -- the
# independence claim must name the readers and where they live, so it cannot
# survive a reader being dropped or swapped for a mirror, and it must stay
# beside the counterweight that an independent reader is not an NLE import.
# ---------------------------------------------------------------------------
def test_both_readers_are_named_and_neither_is_called_an_nle():
    """The independence claim must travel with its specifics, and must not
    drift into claiming an NLE has ever opened anything."""
    validation = (DOCS / "NLE_VALIDATION.md").read_text(encoding="utf-8")
    compatibility = (DOCS / "NLE_COMPATIBILITY.md").read_text(encoding="utf-8")

    # Both readers named where the claim is made, so the claim cannot outlive
    # either of them. The FCPXML reader is vendored rather than pip-installed,
    # so the doc has to point at the tree and at the provenance record.
    for doc, name in ((validation, "NLE_VALIDATION.md"), (compatibility, "NLE_COMPATIBILITY.md")):
        assert "pycmx" in doc, f"{name} claims independence without naming the EDL reader"
        assert "otio_fcpxml_adapter" in doc, (
            f"{name} claims independence without naming the FCPXML reader"
        )
        assert "media-rep" in doc, (
            f"{name} does not say why this reader rather than another: the "
            f"media-rep resolution is the property that makes it a validator "
            f"instead of a false positive"
        )
    assert "third_party/PROVENANCE.md" in validation, (
        "NLE_VALIDATION.md names a vendored reader without pointing at the "
        "provenance record that lets a reader re-verify its origin and licence"
    )

    # The counterweight. An independent reader resolving every clip's media
    # proves the document is machine-readable and points at real files; it says
    # nothing about an NLE's import path, relink dialog, or playback, and the
    # two must never be exchanged for each other on these pages.
    assert "has ever opened a SelekyT artifact" in validation, (
        "NLE_VALIDATION.md no longer states that no NLE has ever opened a "
        "SelekyT artifact"
    )
    assert "never opened in an NLE" in compatibility, (
        "NLE_COMPATIBILITY.md no longer carries the never-opened-in-an-NLE "
        "qualification beside its independence claim"
    )

    # The specific overstatement that the independence claim must not collapse
    # back into. Both halves are now genuinely independently read, so the
    # plural is not currently false -- but the word "independent" is only
    # load-bearing in these docs because it is defined against SelekyT's own
    # reader, and the docs have to keep making that contrast explicitly rather
    # than letting "two independent readers" become a slogan whose two halves
    # nobody checks.
    assert "self-consistency check" in validation, (
        "NLE_VALIDATION.md no longer records that SelekyT's own reader is a "
        "self-consistency check, which is the contrast that makes "
        "'independent' mean anything on this page"
    )


def test_the_vendored_reader_is_what_provenance_says_it_is():
    """The provenance record's verbatim claim must hold against the tree."""
    provenance = (ROOT / "third_party" / "PROVENANCE.md").read_text(encoding="utf-8")
    reader = ROOT / "third_party" / "otio_fcpxml_adapter" / "fcpxml.py"
    licence = ROOT / "third_party" / "otio_fcpxml_adapter" / "LICENSE"

    assert reader.is_file(), "the vendored FCPXML reader is missing from the tree"
    assert licence.is_file(), (
        "the vendored reader's licence is missing; Apache-2.0 requires the "
        "notice to travel with the redistribution"
    )

    # The record commits to a byte-identical file and gives the reader a way to
    # check it, so the claim is falsifiable from the tree alone.
    assert "Not modified" in provenance
    assert "fbaa6f40907fdcea5de6483c804632c0cf5880e2f7c9672049a2d9cbd7f7075c" in provenance, (
        "PROVENANCE.md no longer carries the sha256 it claims for fcpxml.py"
    )
    import hashlib

    digest = hashlib.sha256(reader.read_bytes()).hexdigest()
    assert digest == "fbaa6f40907fdcea5de6483c804632c0cf5880e2f7c9672049a2d9cbd7f7075c", (
        f"fcpxml.py in the tree is sha256 {digest}, but PROVENANCE.md claims "
        "fbaa6f40907fdcea5de6483c804632c0cf5880e2f7c9672049a2d9cbd7f7075c. "
        "Either the record is stale or the file has been modified; the "
        "not-modified claim cannot stand either way"
    )
    assert hashlib.sha256(licence.read_bytes()).hexdigest() == (
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
    ), "the vendored LICENSE is not the canonical Apache-2.0 text"

    # Nothing under third_party/ is SelekyT code, and the reader is used as a
    # validator from outside the emit path -- that is where its value comes
    # from, and the package docstring is what keeps a later refactor from
    # quietly moving it into the pipeline.
    init = (ROOT / "third_party" / "__init__.py").read_text(encoding="utf-8")
    assert "Nothing under this tree is SelekyT code" in init

