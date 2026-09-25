"""The corpus-model doc's measured table, recomputed from the committed corpus.

``docs/corpus-model.md`` ships a per-source table of what ``build_corpus``
produces, and quotes a judgement ("keep unit 85") with verbatim evidence. Both
are the kind of figure that has gone stale in this repo before — the NLE
frame-delta cluster cited frame counts that matched no committed artifact, and
the VAD retry's justification was arithmetically wrong. This module recomputes
the table from the transcripts the docs name, so a doc edit that drifts off the
measurement fails here rather than shipping.

The layer was long unwired into the pipeline, and the doc said so; it is now read
by the story-memory layer (`engine/story_path.py`, `engine/story_view.py`), so
this module checks both numbers. It is not testing product behaviour — it is
testing that the doc's numbers are the numbers the corpus gives, and that the
doc's claim about consumption matches the consumption that actually exists. Both
are the weaker but still useful claim, and both were wrong last time.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.corpus import build_corpus  # noqa: E402
from models.core import Segment, Word  # noqa: E402

_PROJECTS = sorted(p for p in (ROOT / "corpus").glob("*_run/project")
                   if (p / "transcript.json").is_file())

DOC = ROOT / "docs" / "corpus-model.md"


def _segments(project: Path) -> list[Segment]:
    import json

    raw = json.loads((project / "transcript.json").read_text())["segments"]
    return [
        Segment(s["start"], s["end"], s["text"], [Word(**w) for w in s.get("words", [])])
        for s in raw
    ]


def _measured() -> dict[str, tuple[int, int, int, int, int]]:
    """name -> (segments, units, topics, threads, redundancy groups)."""
    out = {}
    for project in _PROJECTS:
        name = project.parent.name.replace("_run", "")
        segs = _segments(project)
        if not segs:
            out[name] = (0, 0, 0, 0, 0)
            continue
        corpus = build_corpus(segs, duration=max(s.end for s in segs))
        out[name] = (
            len(segs), len(corpus.units), len(corpus.topics),
            len(corpus.threads), len(corpus.redundancy_groups),
        )
    return out


MEASURED = _measured()


def test_the_corpus_the_doc_names_is_present():
    """A missing transcript would make the table below vacuous."""
    assert len(_PROJECTS) >= 9, (
        f"the doc names 9 transcripts with units; found {len(_PROJECTS)} projects"
    )


@pytest.mark.parametrize("name,expected", sorted(MEASURED.items()))
def test_the_doc_table_row_matches_the_corpus(name, expected):
    """Each row the doc prints must be what ``build_corpus`` measures now."""
    pytest.importorskip("numpy")
    text = DOC.read_text(encoding="utf-8")
    segs, units, topics, threads, redund = expected
    # The three empty sources share one row in the doc, so a row is matched by
    # its source name appearing beside its counts rather than by a full line.
    label = name.replace("_run", "")
    if segs == 0:
        # Empty sources are listed by name in the doc's merged row.
        assert label in text, (
            f"{label} has an empty transcript; the doc's merged empty row must "
            f"name it"
        )
        return
    row = f"| {label} | {segs} | {units} | {topics} | {threads} | {redund} |"
    assert row in text, (
        f"the doc's table no longer carries {label} as {row.strip()!r}; the "
        f"corpus measures it there. Either the doc drifted, or build_corpus "
        f"changed and the doc must be re-run."
    )


def test_the_doc_states_the_totals_the_corpus_measures():
    """The totals line is the one a reader would quote; it must be right."""
    text = DOC.read_text(encoding="utf-8")
    segs = sum(v[0] for v in MEASURED.values())
    assert f"1,771 segments" in text, (
        f"the doc's headline says 1,771 segments; the corpus has {segs}"
    )


def test_the_quoted_judgement_names_a_real_unit():
    """The doc quotes "keep unit 85" with verbatim evidence. The unit and the
    evidence must exist in the real build, or the example is decoration."""
    text = DOC.read_text(encoding="utf-8")
    assert "keep unit 85" in text, "the doc's worked example has moved"
    project = ROOT / "corpus" / "nasa360-ep5_run" / "project"
    if not (project / "transcript.json").is_file():
        pytest.skip("nasa360-ep5 transcript not present")
    corpus = build_corpus(_segments(project),
                          duration=max(s.end for s in _segments(project)))
    assert len(corpus.units) > 85, (
        "the doc quotes unit 85 but the build produced fewer than 86 units"
    )
    # The evidence quoted in the doc must be a real unit's text, not a
    # paraphrase. Both quoted strings appear in the build's units.
    quoted = ["I'm Jennifer Pulley", "We'll catch you next time"]
    units_text = "\n".join(u.text for u in corpus.units)
    for q in quoted:
        assert q in units_text, (
            f"the doc quotes {q!r} as evidence but no unit carries that text; "
            f"the example must be verbatim from a real build"
        )


def test_the_doc_states_who_consumes_the_layer_and_does_not_overclaim():
    """The doc must name what reads this layer, and must not convert that wiring
    into a quality claim.

    The layer was once consumed by nothing, and the previous version of this test
    pinned that plainly: overclaiming consumption was the failure mode this whole
    module exists to prevent. The story-memory layer changed the premise —
    ``engine/story_path.py`` and ``engine/story_view.py`` now read the corpus back
    through ``engine/corpus_io.py`` — so the honest assertion flipped with it. What
    did *not* flip is the second half: reaching the pipeline is not the same as
    improving an edit, and the doc must keep saying so.
    """
    text = DOC.read_text(encoding="utf-8")
    assert "Something consumes this layer now" in text, (
        "the doc must state plainly that the layer is now read, and name what "
        "reads it"
    )
    assert "engine/story_path.py" in text and "engine/corpus_io.py" in text, (
        "the doc must name the consuming modules, not just assert consumption"
    )
    # The claim the doc is still not allowed to make: that wiring improved an
    # edit. This is the sentence a reader would quote, and it must stay hedged.
    assert "does **not** establish" in text, (
        "the doc must still say the table does not establish that acting on it "
        "improves an edit; wiring is not evidence"
    )
    assert "has not been done" in text, (
        "the doc must name the blind A/B comparison as the missing evidence "
        "rather than leaving the hedge vague"
    )
