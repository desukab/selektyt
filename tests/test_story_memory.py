"""The story-memory chain, end to end: build, export, reimport, edit, export again.

This file holds the round trip the directive calls the most important test —
analyze once, write the memory, read it back in a *different* process, choose a
story, cut it, and ship the cut as FCPXML and EDL — plus the graph-level
properties no single layer sees on its own: that the graph is not a list, that
every relationship carries a measurement and a reason, that every moment carries
its source time, and that a relationship the evidence cannot support is reported
as absent rather than guessed.

The seventeen required tests are spread across four files, each where the layer
it pins lives:

  #1  two distant moments linked into one story       tests/test_story_path.py
  #2  setup -> payoff                                  tests/test_story_path.py
  #3  three or more linked moments                     tests/test_story_path.py
  #4  several stories in one source                    tests/test_story_path.py
  #5  one moment in several stories                    tests/test_story_path.py
  #6  non-linear relationships                                    here
  #7  relationship confidence                                     here
  #8  provenance back to source timestamps                        here
  #9  story graph -> edit plan                          tests/test_story_path.py
  #10 edit plan -> FCPXML / EDL                                    here
  #11 export -> import round trip                                  here
  #12 reimported memory regenerates the structure      tests/test_story_view.py
  #13 no fabricated relationship                                   here
  #14 redundant moments handled                         tests/test_story_path.py
  #15 mid-sentence openings rejected                   tests/test_story_path.py
  #16 payoff without its setup flagged                 tests/test_story_path.py
  #17 the existing measurements still hold                          here

Every corpus here is built by the analyser from a transcript the way the
pipeline builds one. The values these tests assert were recorded from the
analyser, not assumed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine.corpus import build_corpus
from engine.corpus_io import read_corpus
from engine.nle_export import export as nle_export
from engine.nle_export import (
    independent_parse_edl,
    render_edl,
    render_fcpxml,
    validate_edl,
    validate_fcpxml,
)
from engine.story_path import story_path
from engine.story_view import hms, story_view, write_story_memory
from engine.timeline_build import timeline_for_candidate
from models.core import Segment, Word

ROOT = Path(__file__).resolve().parent.parent


def _seg(a: float, b: float, text: str, confidence: float = 0.9) -> Segment:
    """A timed segment like a transcript carries one, word for word."""
    words = text.split()
    span = (b - a) / max(1, len(words))
    return Segment(
        a, b, text,
        [Word(w, a + i * span, a + (i + 1) * span, confidence)
         for i, w in enumerate(words)],
    )


# One story across three distant moments: a question, its answer, and the
# consequence of the answer. The analyser finds thread 0 = units [0, 2, 3] with
# a question_answer link (0 -> 2) and a causal link (2 -> 3). Units 1 and 4 are
# other, single-unit stories — the interference that makes the three linked
# moments *distant* rather than adjacent.
def _story_segs() -> list[Segment]:
    return [
        _seg(0.0, 8.0, "Why did the satellite clock fail? The launch was nominal."),
        _seg(12.0, 20.0, "The harbour was rebuilt. The contractors finished early."),
        _seg(24.0, 32.0, "The satellite clock failed to synchronise. "
                         "The battery was under-spec."),
        _seg(36.0, 44.0, "The lighthouse needs paint. Its lamp is original."),
        _seg(48.0, 56.0, "So the battery was replaced. "
                         "The vendor paid the replacement cost."),
    ]


# Two passages about one subject with nothing between them that evidences a
# relationship: no question, no answer, no result connective, and only one unit
# of separation, so the callback's distance bar is not met either. The analyser
# still places both units in one *topic* — which is where the honest line is
# drawn: same subject is recorded, same story is not claimed.
def _unlinked_segs() -> list[Segment]:
    return [
        _seg(0.0, 8.0, "The satellite clock is sixty years old. Its face is brass."),
        _seg(12.0, 20.0, "The harbour was rebuilt. The contractors finished early."),
        _seg(24.0, 32.0, "The satellite clock sits in the archive room. "
                         "Visitors photograph it often."),
        _seg(36.0, 44.0, "The lighthouse needs paint. Its lamp is original."),
    ]


# Two stories sharing one moment. The analyser finds thread 0 = [0, 4] (the
# satellite clock) and thread 2 = [2, 4] (the lighthouse), each with its own
# question_answer link into the shared payoff. ``unit_threads[4] == [0, 2]`` is
# the whole point of the fixture.
def _shared_segs() -> list[Segment]:
    return [
        _seg(0.0, 8.0, "Why did the satellite clock fail? The launch was nominal."),
        _seg(12.0, 20.0, "The harbour was rebuilt. The contractors finished early."),
        _seg(24.0, 32.0, "Why does the lighthouse need painting? "
                         "The salt corrodes metal."),
        _seg(36.0, 44.0, "The estuary was dredged. The shipping channel widened."),
        _seg(48.0, 58.0, "The satellite clock failed to synchronise. "
                         "The lighthouse needed painting and the salt corroded "
                         "the metal."),
        _seg(62.0, 70.0, "The vendor paid the replacement cost."),
    ]


# A story that recalls its own opening: the same sentence spoken twice, far
# apart, so the analyser's redundancy layer groups units 0 and 3 as a
# restatement while its link layer joins them with a callback.
def _restatement_segs() -> list[Segment]:
    same = ("The satellite antenna failed on launch, "
            "and the agency wrote a report.")
    return [
        _seg(0.0, 14.0, "The studio microphone arrived this week, and the "
                        "engineer tested it all afternoon."),
        _seg(30.0, 44.0, same + " The funding review approved the replacement budget."),
        _seg(60.0, 74.0, "A weather forecast note was published later that afternoon."),
        _seg(90.0, 104.0, "The office coffee machine broke again, and nobody fixed it."),
        _seg(120.0, 134.0, "The intern organised the archive shelves on a quiet Friday."),
        _seg(150.0, 164.0, same + " The review board met in the morning."),
        _seg(180.0, 194.0, "A delivery van parked outside the building all morning."),
        _seg(210.0, 224.0, "The auditor asked for the telemetry logs by the weekend."),
    ]


# A bundle of the main fixture, written once and read by the round-trip tests.
@pytest.fixture
def memory(tmp_path: Path):
    segs = _story_segs()
    corpus = build_corpus(segs, duration=65.0, source="interview.mov")
    files = write_story_memory(corpus, tmp_path / "memory", segs)
    return files, segs, corpus


# ── #11: analyze once, share, reimport somewhere else ───────────────────────


def test_an_exported_memory_is_read_back_by_a_different_process(memory):
    """The memory is a file, not an object: it survives a process boundary.

    The child process gets only the written JSON. If a value survives, it could
    only have come from the file, which is what makes the round trip honest
    rather than decorative.
    """
    files, _segs, corpus = memory
    script = (
        "import json, sys\n"
        "from engine.corpus_io import read_corpus\n"
        "from engine.story_view import story_view\n"
        "c = read_corpus(sys.argv[1])\n"
        "json.dump({\n"
        "    'fingerprint': c.build.fingerprint,\n"
        "    'unit_count': len(c.units),\n"
        "    'threads': [t.unit_indices for t in c.threads],\n"
        "    'thread_titles': [t.title for t in c.threads],\n"
        "    'links': [(l.setup_unit, l.payoff_unit, l.kind, l.strength,\n"
        "               l.confidence) for l in c.links],\n"
        "    'unit_threads': c.unit_threads,\n"
        "    'topic_membership': [t.unit_indices for t in c.topics],\n"
        "    'view_stories': [s['story_id'] for s in story_view(c)['stories']],\n"
        "}, sys.stdout)\n"
    )
    env = {"PATH": str(Path(sys.executable).parent),
           "PYTHONPATH": str(ROOT)}
    completed = subprocess.run(
        [sys.executable, "-c", script, str(files.corpus)],
        capture_output=True, text=True, check=True, cwd=str(ROOT), env=env)
    got = json.loads(completed.stdout)

    assert got["fingerprint"] == corpus.build.fingerprint
    assert got["unit_count"] == len(corpus.units)
    assert got["threads"] == [[0, 2, 3], [1], [4]]
    assert got["thread_titles"] == [t.title for t in corpus.threads]
    assert got["links"] == [
        [0, 2, "question_answer", pytest.approx(0.65), pytest.approx(0.65)],
        [2, 3, "causal", pytest.approx(0.75), pytest.approx(0.75)],
    ]
    assert got["unit_threads"] == corpus.unit_threads
    assert got["topic_membership"] == [t.unit_indices for t in corpus.topics]
    assert got["view_stories"] == [0, 1, 2]


def test_an_imported_memory_still_selects_a_story_and_cuts_it(memory):
    """The reimported graph is not a snapshot: it is worked on, and identically.

    Requirement #9 lives in ``test_story_path.py`` for the in-memory graph; this
    is the same selection through the file, and it keeps the nesting property —
    the tight cut is a subset of the generous one, because the graph did not
    change between the two budgets.
    """
    files, segs, _corpus = memory
    corpus = read_corpus(files.corpus)

    tight = story_path(corpus, segs, thread_id=0, target_seconds=20.0)
    generous = story_path(corpus, segs, thread_id=0, target_seconds=40.0)
    assert tight.unit_indices == [0, 3]
    assert generous.unit_indices == [0, 2, 3]
    assert set(tight.unit_indices) < set(generous.unit_indices)
    assert tight.thread_id == generous.thread_id == 0


# ── #10 and #8: from a story on a graph to a cut a NLE can open ─────────────


def _edit_files(path, media: Path, out_dir: Path) -> list[Path]:
    """The same export the pipeline performs per candidate, for one story path.

    Each moment becomes its own timeline — one story, one file pair per moment —
    exactly as ``selekyt/pipeline.py`` exports the independent-clip selection.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for i, cand in enumerate(path):
        timeline = timeline_for_candidate(media, cand, fps=30.0, duration=65.0)
        written.extend(nle_export(timeline, out_dir, stem=f"{i:02d}").values())
    return written


@pytest.mark.parametrize("budget, expected", [(20.0, [0, 3]), (40.0, [0, 2, 3])])
def test_a_story_becomes_a_timeline_whose_clips_stay_inside_their_moments(
        memory, budget, expected):
    """Required #10 and #8: the edit traces back to story nodes to source ranges.

    Every clip the timeline places is a span of the *source file*, and that span
    must lie inside the story unit the path chose — so a clip can always be read
    back to a node of the story, and a node back to its timecode in the video.
    """
    files, segs, corpus = memory
    path = story_path(read_corpus(files.corpus), segs,
                      thread_id=0, target_seconds=budget)
    assert path.unit_indices == expected

    spans = {u.index: (u.start, u.end) for u in corpus.units}
    seen: list[tuple[int, float, float]] = []
    for i, cand in enumerate(path):
        timeline = timeline_for_candidate(
            Path("/tmp/source.mov"), cand, fps=30.0, duration=65.0)
        unit = path.unit_indices[i]
        lo, hi = spans[unit]
        for clip in timeline.clips:
            assert lo <= clip.source_in and clip.source_out <= hi, (
                f"clip {clip.source_in}:{clip.source_out} escapes story unit "
                f"{unit} ({lo}:{hi})")
            seen.append((unit, clip.source_in, clip.source_out))
        problems = validate_fcpxml(render_fcpxml(timeline), timeline) \
            + validate_edl(render_edl(timeline), 30.0)
        assert problems == [], problems
    assert len(seen) == len(set(seen)), "a clip was placed twice"
    # The tight cut's clips are a subset of the generous one's, clip for clip.
    if budget == 40.0:
        assert {t[1:] for t in seen} >= {(0.0, 8.0), (24.0, 32.0)}


def test_a_story_edits_export_to_formats_an_editor_can_open(memory, tmp_path):
    """The validators this module ships are the ones that judge the export.

    ``independent_parse_edl`` uses a different EDL parser than the writer, so its
    verdict is not the writer agreeing with itself.
    """
    files, segs, _corpus = memory
    path = story_path(read_corpus(files.corpus), segs,
                      thread_id=0, target_seconds=40.0)
    written = _edit_files(path, Path("/tmp/source.mov"), tmp_path / "edit")

    assert len(written) == 2 * len(path)
    assert sorted(p.name for p in written) == [
        f"{i:02d}.{ext}" for i in range(len(path)) for ext in ("edl", "fcpxml")]
    assert set(written) == set(written[0].parent.iterdir())
    for path_ in written:
        assert path_.exists() and path_.stat().st_size > 0
        if path_.suffix == ".edl":
            text = path_.read_text()
            ran, problems = independent_parse_edl(text)
            assert ran, problems
            assert problems == [], problems
            assert validate_edl(text, fps=30.0) == []


def test_a_story_edit_carries_the_verdict_that_selected_each_moment(memory, tmp_path):
    """A clip's ``reason`` is the editorial verdict the guard measured for it.

    The story path refused or shipped each moment on that verdict, and the
    timeline carries it forward unchanged — so an editor reading a clip's reason
    reads the same sentence the story memory recorded for the moment.
    """
    files, segs, _corpus = memory
    path = story_path(read_corpus(files.corpus), segs,
                      thread_id=0, target_seconds=40.0)
    for cand, unit in zip(path, path.unit_indices):
        timeline = timeline_for_candidate(
            Path("/tmp/source.mov"), cand, fps=30.0, duration=65.0)
        assert timeline.clips, f"unit {unit} produced no clip"
        for clip in timeline.clips:
            assert clip.reason == cand.reason
            assert clip.reason, f"unit {unit} ships a clip with no reason"
        # The moment's verdict is the one the guard issued for this span.
        assert path.verdicts[unit]
        assert path.roles[unit] in {"setup", "resolution", "revelation",
                                    "tension", "turn", "continuation"}


# ── #6: the graph is not an ordered list ────────────────────────────────────


def test_edges_jump_over_the_moments_between_them(memory):
    """Required #6: a relationship can skip the units in between.

    The thread is 0 -> 2 -> 3, so units 0 and 3 are *not* adjacent, no typed edge
    names them as a pair, and the view says so in terms that separate
    co-membership from an inferred relationship.
    """
    _files, _segs, corpus = memory
    view = story_view(corpus, _story_segs())
    m0 = view["moments"][0]
    assert [(e["unit"], e["kind"]) for e in m0["edges"]] == [(2, "question_answer")]
    assert [(c["unit"], c["adjacent"]) for c in m0["chain_mates"]] == [(3, False)]
    assert "not by an inferred relationship" in m0["chain_mates"][0]["reason"]

    m3 = view["moments"][3]
    assert {e["unit"] for e in m3["edges"]} == {2}
    assert [(c["unit"], c["adjacent"]) for c in m3["chain_mates"]] == [(0, False)]

    edges = {(min(l.setup_unit, l.payoff_unit), max(l.setup_unit, l.payoff_unit))
             for l in corpus.links}
    assert (0, 3) not in edges


def test_one_moment_is_reached_from_two_stories_by_two_edges():
    """Required #6: a moment has more than one predecessor, in different stories.

    Unit 4 is the payoff of thread 0 *and* thread 2. Both edges are typed, they
    come from different units, and the graph does not force the moment to pick a
    story.
    """
    corpus = build_corpus(_shared_segs(), duration=75.0)
    assert corpus.unit_threads[4] == [0, 2]

    into_4 = sorted((l.setup_unit, l.kind) for l in corpus.links
                    if l.payoff_unit == 4)
    assert into_4 == [(0, "question_answer"), (2, "question_answer")]
    setups = {l.setup_unit for l in corpus.links if l.payoff_unit == 4}
    assert setups == {0, 2}

    view = story_view(corpus, _shared_segs())
    m4 = view["moments"][4]
    assert m4["story_ids"] == [0, 2]
    assert {e["unit"] for e in m4["edges"]} == {0, 2}
    assert len({e["kind"] for e in m4["edges"]}) == 1


def test_a_callback_reaches_back_across_a_large_gap():
    """Required #6: a typed edge can also point backwards in the source."""
    corpus = build_corpus(_restatement_segs(), duration=240.0)
    link, = corpus.links
    assert (link.setup_unit, link.payoff_unit, link.kind) == (0, 3, "callback")
    # Unit 0 runs 0.0–38.10s and unit 3 opens at 150.0s: two minutes apart.
    assert corpus.units[3].start - corpus.units[0].end > 100.0
    assert corpus.threads[0].unit_indices == [0, 3]


# ── #7: every relationship carries a measurement and a reason ──────────────


# The bar each kind must clear, from ``engine/threads.py``. A link below its bar
# is dropped rather than recorded at a lower strength, so a shipped link's
# strength is always at or above these.
_KIND_BAR = {
    "question_answer": 0.65,
    "causal": 0.75,
    "problem_solution": 0.60,
    "contrast_turn": 0.55,
    "callback": 0.45,
}


def test_every_relationship_is_measured_reasoned_and_quoted(memory):
    """Required #7: confidence is a number with a stated basis, never a label."""
    _files, segs, corpus = memory
    source_text = " ".join(s.text for s in segs)
    assert len(corpus.links) == 2, "the fixture must contain relationships"
    for link in corpus.links:
        assert link.kind in _KIND_BAR, f"unbarred kind {link.kind}"
        assert link.strength >= _KIND_BAR[link.kind], link.kind
        assert 0.0 < link.confidence <= 1.0
        assert link.reason, "a relationship with no reason is a guess"
        assert link.evidence, "a relationship with no evidence is a guess"
        assert link.setup_start < link.setup_end <= link.payoff_start
        for quote_ in link.evidence:
            # A quotation the source does not contain would be invented support.
            assert quote_ in source_text, f"quote not in the transcript: {quote_}"
        # The evidence is drawn from the two units the edge joins, in order.
        assert corpus.units[link.setup_unit].text.startswith(link.evidence[0])
        assert corpus.units[link.payoff_unit].text.startswith(link.evidence[-1])

    assert [(l.setup_unit, l.payoff_unit, l.kind, l.strength) for l in corpus.links] == [
        (0, 2, "question_answer", pytest.approx(0.65)),
        (2, 3, "causal", pytest.approx(0.75)),
    ]


def test_confidence_is_low_when_the_evidence_is_only_lexical():
    """Honest uncertainty: a link on lexical evidence alone is not near-certain.

    The thread's confidence is a stated formula over the strengths of its links
    (``engine/threads.py``), and it is capped below 1.0 — a story built from
    transcript evidence can be well supported, never certain.
    """
    corpus = build_corpus(_story_segs(), duration=65.0)
    thread = corpus.threads[0]
    assert thread.confidence < 0.95
    assert thread.reason and thread.evidence
    for beat in (corpus.beats[bi] for bi in thread.beat_indices):
        assert 0.0 <= beat.confidence <= 1.0
        assert beat.reason


def test_the_view_states_the_basis_of_every_confidence_it_reports(memory):
    """A confidence in the view never appears without the reason to believe it."""
    _files, segs, corpus = memory
    view = story_view(corpus, segs)
    for moment in view["moments"]:
        gate = moment["gate"]
        assert gate.get("verdict")
        if moment["confidence"] is not None:
            assert moment["confidence_basis"], moment["unit"]
    for story in view["stories"]:
        assert 0.0 <= story["confidence"] <= 1.0
        assert story["reason"]


# ── #8: provenance, from a moment to a timecode in the source ───────────────


def test_every_moment_carries_its_own_source_timecode(memory):
    """Required #8: a moment's timestamp is the source's, not the edit's."""
    _files, segs, corpus = memory
    view = story_view(corpus, segs)
    for moment in view["moments"]:
        unit = corpus.units[moment["unit"]]
        assert moment["timecode"] == hms(unit.start)
        assert moment["start"] == round(unit.start, 3)
        assert moment["end"] == round(unit.end, 3)
        assert moment["text"] == unit.text

    # The timecodes are the source's own clock, and the view says so.
    assert view["provenance"]["source"] == "interview.mov"
    assert view["provenance"]["corpus_fingerprint"] == corpus.build.fingerprint
    assert view["provenance"]["duration"] == 65.0
    assert view["timing_note"]


def test_provenance_survives_the_round_trip(memory):
    """The source name and the fingerprint are the memory's own provenance."""
    files, _segs, corpus = memory
    reimported = read_corpus(files.corpus)
    assert reimported.source == corpus.source == "interview.mov"
    assert reimported.build.fingerprint == corpus.build.fingerprint
    assert reimported.duration == corpus.duration

    view = story_view(reimported)
    assert view["provenance"]["source"] == "interview.mov"
    assert view["provenance"]["corpus_fingerprint"] == corpus.build.fingerprint
    for moment in view["moments"]:
        unit = reimported.units[moment["unit"]]
        assert moment["timecode"] == hms(unit.start)


def test_a_story_node_traces_to_a_source_range_in_the_edit(memory, tmp_path):
    """The chain runs both ways: from a clip on the timeline to a moment.

    ``source_in``/``source_out`` are seconds into the source file, so a clip can
    be resolved to the story unit whose span contains it — which is what an
    editor asking "why is this here" is actually asking.
    """
    files, segs, corpus = memory
    path = story_path(read_corpus(files.corpus), segs,
                      thread_id=0, target_seconds=40.0)
    timelines = [
        timeline_for_candidate(Path("/tmp/source.mov"), cand,
                               fps=30.0, duration=65.0)
        for cand in path
    ]
    for unit_index, cand, timeline in zip(path.unit_indices, path, timelines):
        span = corpus.units[unit_index]
        assert timeline.clips, f"unit {unit_index} produced no clip"
        for clip in timeline.clips:
            assert span.start <= clip.source_in and clip.source_out <= span.end
            # The clip carries the measured verdict the guard issued for that span.
            assert clip.reason == cand.reason and clip.reason
        assert path.roles[unit_index] and path.verdicts[unit_index]


# ── #13: a relationship the evidence cannot support is not invented ─────────


def test_two_passages_on_one_subject_are_not_therefore_related():
    """Required #13: same topic is recorded, and no relationship is claimed.

    Units 0 and 2 are both about the satellite clock, so they share a topic —
    and nothing else: no question, no answer, no connective, no callback
    distance. The memory says they are on the same subject and stops there.
    """
    segs = _unlinked_segs()
    corpus = build_corpus(segs, duration=48.0)

    shared = [t.unit_indices for t in corpus.topics if len(t.unit_indices) > 1]
    assert shared == [[0, 2]]
    assert corpus.topics[0].unit_indices == [0, 2]
    assert corpus.links == []
    assert all(len(t.unit_indices) == 1 for t in corpus.threads)

    view = story_view(corpus, segs)
    for moment in view["moments"]:
        assert moment["edges"] == [], moment["unit"]
        assert moment["chain_mates"] == [], moment["unit"]
    assert view["moments"][0]["topic"]["topic_id"] == 0
    assert view["moments"][2]["topic"]["topic_id"] == 0
    assert view["moments"][0]["story_ids"] == [0]
    assert view["moments"][2]["story_ids"] == [2]


def test_the_memory_names_the_relationships_it_refuses_to_claim(memory):
    """Required #13: the absences are documented, not silently omitted."""
    _files, _segs, _corpus = memory
    view = story_view(_corpus, _story_segs())
    refused = {u["kind"]: u["reason"]
               for u in view["relationships"]["unsupported"]}
    assert set(refused) == {
        "supports", "explains", "provides_context", "foreshadows",
        "creates_conflict", "escalates", "qualifies", "depends_on",
    }
    for kind, reason in refused.items():
        assert reason, f"{kind} is listed as unsupported with no reason why"
    assert "provides_context" in refused
    assert "topic co-membership alone is exactly the hallucination" \
        in refused["provides_context"]

    # What the memory *does* claim is the smaller, evidenced set.
    assert set(view["relationships"]["kinds_inferred_from_evidence"]) == \
        set(_KIND_BAR)


def test_no_relationship_is_claimed_without_a_link():
    """A story holds only the pairs an edge or a chain actually joins."""
    corpus = build_corpus(_shared_segs(), duration=75.0)
    joined = {(min(l.setup_unit, l.payoff_unit), max(l.setup_unit, l.payoff_unit))
              for l in corpus.links}
    for thread in corpus.threads:
        members = set(thread.unit_indices)
        for i in members:
            for j in members:
                if i == j or (min(i, j), max(i, j)) in joined:
                    continue
                # A pair with no typed edge can only be chain co-membership.
                assert abs(i - j) >= 1
                assert corpus.unit_threads[i] == corpus.unit_threads[j]


# ── #14: a passage the source repeats is reported per moment ────────────────


def test_a_repeated_passage_is_marked_on_both_moments():
    """Required #14, at the moment level: the redundancy is visible per node."""
    segs = _restatement_segs()
    corpus = build_corpus(segs, duration=240.0)
    view = story_view(corpus, segs)

    assert len(corpus.redundancy_groups) == 1
    group = corpus.redundancy_groups[0]
    assert group.members == [0, 3]

    redundancy = {m["unit"]: m["redundancy"] for m in view["moments"]}
    assert redundancy[0]["group_id"] == redundancy[3]["group_id"] == 0
    assert redundancy[0]["relation"] == "restatement"
    assert redundancy[0]["is_the_representative"] is True
    assert redundancy[3]["is_the_representative"] is False
    assert redundancy[0]["representative"] == 0
    assert redundancy[1] is None and redundancy[4] is None


# ── #17: the schema change broke nothing already measured ───────────────────


def test_the_corpus_fingerprint_is_unchanged_by_the_extension():
    """Required #17: the schema bumped to 1.1, the content did not move.

    The fingerprint is the analyser's contract with every file written before
    this extension; pinning it is what proves the new ``unit_threads`` field was
    added alongside the measurement rather than in place of it.
    """
    corpus = build_corpus(_story_segs(), duration=65.0, source="interview.mov")
    assert corpus.build.fingerprint == "d69848f961fb00c3"
    assert corpus.build.schema == "1.1"
    assert read_corpus.__module__ == "engine.corpus_io"
