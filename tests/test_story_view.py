"""Tests for ``engine.story_view`` — the presentation layer of the story memory.

The view is a *report*, not a memory: the corpus is the memory, and the view is
re-derived from it every time. These tests pin that discipline — determinism,
purity, refusal to re-analyse — along with the shape the directive asks an
exported memory to have: every moment answers what was said, where, by whom, in
what story, in what role, why it is here, what it connects to, what it
establishes, what it depends on and how confident the measurement is.

The corpus for every test is built the way the pipeline builds one. The values
these tests depend on were recorded from the analyser, not assumed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from engine import corpus as corpus_mod
from engine.corpus import build_corpus
from engine.corpus_io import read_corpus
from engine.story_view import (
    VIEW_SCHEMA,
    StoryMemoryFiles,
    hms,
    read_story_view,
    render_story_md,
    story_view,
    verify_story_view,
    write_story_memory,
)
from models.core import Segment, Word


def _seg(a: float, b: float, text: str, confidence: float = 0.9) -> Segment:
    """A timed segment like a transcript carries one, word for word."""
    words = text.split()
    span = (b - a) / max(1, len(words))
    return Segment(
        a, b, text,
        [Word(w, a + i * span, a + (i + 1) * span, confidence)
         for i, w in enumerate(words)],
    )


# One story across three distant moments — thread 0 = [0, 2, 3] with a
# question_answer edge (0 → 2) and a causal edge (2 → 3). Units 1 and 4 are
# other, single-unit stories. Recorded from the analyser, not assumed.
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


def _view(segs: list[Segment] | None = None) -> dict:
    segs = _story_segs() if segs is None else segs
    return story_view(build_corpus(segs, duration=65.0), segs)


# ── timecodes: the corpus holds seconds, not frames ──────────────────────────


def test_hms_formats_minutes_and_hours():
    assert hms(0.0) == "00:00"
    assert hms(51.0) == "00:51"
    assert hms(20.91) == "00:21"  # nearest second, never a frame count
    assert hms(3661.5) == "1:01:02"


def test_hms_does_not_go_negative():
    assert hms(-12.0) == "00:00"


# ── the view is a pure function of the corpus ────────────────────────────────


def test_the_view_is_deterministic():
    segs = _story_segs()
    c = build_corpus(segs, duration=65.0)
    assert story_view(c, segs) == story_view(c, segs)


def test_the_view_does_not_change_the_graph():
    """A report of the memory is not a mutation of it."""
    segs = _story_segs()
    c = build_corpus(segs, duration=65.0)
    before = c.to_dict()
    story_view(c, segs)
    assert c.to_dict() == before


def test_the_view_does_not_re_run_the_analyser(monkeypatch):
    """If the view re-analysed the source, an exported memory would report
    whatever the current analyser produces rather than what the file holds."""
    def _boom(*a, **kw):
        raise AssertionError("story_view re-ran the analyser")

    monkeypatch.setattr(corpus_mod, "build_corpus", _boom)
    c = build_corpus(_story_segs(), duration=65.0)
    v = story_view(c)
    assert v["provenance"]["unit_count"] == len(c.units)


def test_provenance_names_the_source_and_the_graph():
    c = build_corpus(_story_segs(), duration=65.0, source="interview.mov")
    v = story_view(c, _story_segs())
    p = v["provenance"]
    assert p["source"] == "interview.mov"
    assert p["duration"] == 65.0
    assert p["duration_read"] == "01:05"
    assert p["corpus_fingerprint"] == c.build.fingerprint
    assert p["corpus_schema"] == c.build.schema
    assert p["unit_count"] == len(c.units)
    assert p["languages"] == ["english"]
    assert v["timing_note"]


# ── every moment answers the questions the memory owes its reader ────────────


def test_the_view_names_its_own_schema_and_kind():
    v = _view()
    assert v["schema"] == VIEW_SCHEMA
    assert v["kind"] == "selektyt.story_view"


def test_every_moment_answers_the_eight_questions():
    v = _view()
    for m in v["moments"]:
        for key in ("unit", "start", "end", "timecode", "text", "story_ids",
                    "stories", "speaker", "speaker_basis", "visual", "edges",
                    "establishes", "depends_on", "chain_mates", "topic",
                    "redundancy", "gate", "confidence", "confidence_basis"):
            assert key in m, f"moment {m['unit']} is missing {key}"
        assert m["start"] < m["end"]
        assert m["timecode"] == hms(m["start"])
        assert m["unit"] == round(m["unit"])


def test_the_opening_moment_carries_its_story_and_its_edge():
    """Unit 0 sets up thread 0 by asking the question thread 0 answers."""
    v = _view()
    m0 = v["moments"][0]
    assert m0["story_ids"] == [0]
    assert m0["stories"][0]["story_id"] == 0
    assert m0["stories"][0]["role"] == "tension"
    assert any("sets up 1 later moment(s)" in w for w in m0["stories"][0]["why"])
    assert [e["unit"] for e in m0["edges"] if e["direction"] == "sets_up"] == [2]
    assert m0["establishes"] == [2]
    assert m0["depends_on"] == []


def test_an_edge_carries_its_kind_its_strength_and_its_evidence():
    v = _view()
    edge = v["moments"][0]["edges"][0]
    assert edge["kind"] == "question_answer"
    assert edge["direction"] == "sets_up"
    assert 0.0 < edge["strength"] <= 1.0
    assert 0.0 < edge["confidence"] <= 1.0
    assert edge["reason"]
    assert edge["evidence"]
    assert edge["story_ids"] == [0]
    assert edge["timecode"] == hms(24.0)


def test_the_edge_direction_is_read_from_the_other_end():
    """The same relation is a setup from its first end and a payoff from its last."""
    v = _view()
    incoming = [e for e in v["moments"][2]["edges"]
                if e["direction"] == "pays_off"]
    assert [e["unit"] for e in incoming] == [0]
    # a payoff is what a story lands on, so it depends on what it pays off
    assert v["moments"][2]["depends_on"] == [0]


def test_a_standalone_passage_is_its_own_one_moment_story():
    """Unit 1 relates to nothing, so the analyser leaves it as a story of one
    rather than adopting it into another story's chain."""
    v = _view()
    m1 = v["moments"][1]
    assert m1["story_ids"] == [1]
    assert [s["story_id"] for s in m1["stories"]] == [1]
    assert m1["stories"][0]["role"] == "continuation"
    story = v["stories"][m1["story_ids"][0]]
    assert story["unit_indices"] == [1]
    assert story["links"] == []
    # it takes no part in any other story's argument
    assert m1["edges"] == []
    assert m1["establishes"] == [] and m1["depends_on"] == []
    assert m1["chain_mates"] == []


def test_establishes_and_depends_on_match_the_edges_exactly():
    v = _view()
    for m in v["moments"]:
        from_edges = {e["unit"] for e in m["edges"]}
        assert set(m["establishes"]) | set(m["depends_on"]) == from_edges
        assert m["establishes"] == sorted(set(m["establishes"]))
        assert m["depends_on"] == sorted(set(m["depends_on"]))


# ── confidence: measured, defaulted, or absent — never invented ──────────────


def test_confidence_is_the_mean_per_token_probability_when_measured():
    v = _view()
    m0 = v["moments"][0]
    assert m0["confidence"] == pytest.approx(0.9, abs=1e-3)
    assert "mean per-token transcription probability over" in m0["confidence_basis"]


def test_confidence_says_when_every_word_carries_the_default():
    """A provider that computed no probabilities returns 1.0 per word. A perfect
    score is a signature of that, not of a perfect transcript."""
    segs = [
        _seg(0.0, 8.0, "Why did the satellite clock fail? The launch was nominal.", 1.0),
        _seg(12.0, 20.0, "The satellite clock never synchronised. The drift was real.", 1.0),
    ]
    c = build_corpus(segs, duration=25.0)
    v = story_view(c, segs)
    for m in v["moments"]:
        assert m["confidence"] == 1.0
        assert "unmeasured, not as certainty" in m["confidence_basis"]


def test_confidence_is_null_and_says_so_when_there_are_no_words():
    c = build_corpus(_story_segs(), duration=65.0)
    v = story_view(c)  # no transcript → no words → no measurement
    assert all(m["confidence"] is None for m in v["moments"])
    assert all(m["confidence_basis"].startswith("no word timing is recorded")
               for m in v["moments"])
    assert any("no speaker labels" in n for n in v["notes"])


def test_the_gate_reports_the_guards_own_verdict():
    """A moment the guard refuses is marked refused in the memory too."""
    segs = _story_segs()
    segs[0] = _seg(0.0, 8.0, "Which is why the satellite clock failed. "
                             "The launch was nominal.")
    c = build_corpus(segs, duration=65.0)
    v = story_view(c, segs)
    gate = v["moments"][0]["gate"]
    assert gate["ok"] is False
    # the guard's own wording, verbatim — the view passes it through, it does
    # not compose a sentence of its own
    assert gate["verdict"] == (
        "refused — starts mid-conversation (which) — depends on prior context")
    assert all(m["gate"]["ok"] is True for m in v["moments"][1:])


def test_the_gate_of_a_passing_moment_says_so():
    v = _view()
    assert v["moments"][2]["gate"] == {
        "ok": True,
        "verdict": "ok — clears the editorial gate",
        "clip_confidence": pytest.approx(0.9, abs=1e-3),
    }


def test_clip_confidence_is_null_for_a_wordless_moment():
    """The gate's clip confidence is only a measurement when there are words."""
    c = build_corpus(_story_segs(), duration=65.0)
    v = story_view(c)
    assert all(m["gate"]["clip_confidence"] is None for m in v["moments"])


# ── speakers: named when the source names them, never guessed ────────────────


def test_no_speaker_labels_means_unattributed_with_a_basis():
    v = _view()
    for m in v["moments"]:
        assert m["speaker"] == "unattributed"
        assert "no speaker labels" in m["speaker_basis"]


def test_a_speaker_label_reaches_the_moment():
    """``Segment`` carries no speaker field, so the label is attached the way
    ``derive_speaker_blocks`` reads it: by attribute on the segment object."""
    segs = _story_segs()
    segs[0].speaker = "Host"
    c = build_corpus(segs, duration=65.0)
    v = story_view(c, segs)
    named = [m for m in v["moments"] if m["speaker"] != "unattributed"]
    assert named
    for m in named:
        assert m["speaker"] == "Host"
        assert m["speaker_basis"] == "speaker label from the source"


def test_labels_that_resolve_to_no_moment_say_that_instead():
    c = build_corpus(_story_segs(), duration=65.0)
    c.has_speaker_labels = True
    v = story_view(c, _story_segs())
    for m in v["moments"]:
        assert m["speaker"] == "unattributed"
        assert "none resolves to this moment" in m["speaker_basis"]


# ── topics and redundancy, as the corpus found them ─────────────────────────


def test_a_moment_carries_its_topic_label():
    v = _view()
    assert v["moments"][0]["topic"]["label"]
    # no unit of this corpus restates another, so no moment belongs to a group
    for m in v["moments"]:
        assert m["redundancy"] is None


def test_a_redundant_moment_says_which_passage_is_representative():
    """Required test #14's memory half: a moment that restates another reports
    the group, its relation and the passage the corpus keeps."""
    same = "The satellite antenna failed on launch, and the agency wrote a report."
    segs = [
        _seg(0.0, 20.0, same + " The telemetry was clear from the start."),
        _seg(24.0, 44.0, "The studio microphone arrived this week, "
                         "quite unexpectedly."),
        _seg(48.0, 68.0, same + " Nobody on the team expected the failure."),
        _seg(72.0, 92.0, "A completely unrelated note about the weather "
                         "forecast today."),
        _seg(96.0, 112.0, same + " The review board met in the morning."),
    ]
    c = build_corpus(segs, duration=120.0)
    assert c.redundancy_groups, "the fixture must yield a redundancy group"
    v = story_view(c, segs)
    group = c.redundancy_groups[0]
    grouped = [m for m in v["moments"] if m["redundancy"] is not None]
    assert {m["unit"] for m in grouped} == set(group.members)
    for m in grouped:
        r = m["redundancy"]
        assert r["group_id"] == group.group_id
        assert r["relation"] == group.relation
        assert r["representative"] == group.representative
        assert r["is_the_representative"] == (m["unit"] == group.representative)
        assert r["reason"]
    # the corpus keeps one passage and points every restatement at it
    reps = [m for m in grouped if m["redundancy"]["is_the_representative"]]
    assert [m["unit"] for m in reps] == [group.representative]


# ── the story blocks: the graph, not an ordering ────────────────────────────


def test_a_story_block_carries_its_shape_not_a_timeline():
    v = _view()
    s = v["stories"][0]
    assert s["story_id"] == 0
    assert s["title"]
    assert s["arc"] == "tension → resolution"
    assert s["confidence"] > 0.0
    assert s["reason"]
    assert s["evidence"]
    assert s["unit_indices"] == [0, 2, 3]
    assert s["roles"] == {0: "tension", 2: "tension", 3: "resolution"}
    assert s["moments_in_source_order"] == [0, 2, 3]
    # the span is the story's extent in the source; the view says at the top
    # level that these are source timestamps rather than SMPTE frames
    assert s["timecode"] == "00:00–00:51"
    assert "not SMPTE" in v["timing_note"]


def test_a_story_names_its_peak_beat():
    v = _view()
    peak = v["stories"][0]["peak"]
    assert peak["unit"] == 0
    assert peak["kind"] == "tension"
    assert peak["is_thread_peak"] is True


def test_a_single_moment_story_is_reported_as_one():
    """A passing remark the analyser could not relate to anything is still a
    story of one, and the view says so without inventing a chain for it."""
    v = _view()
    lone = [s for s in v["stories"] if len(s["unit_indices"]) == 1]
    assert len(lone) >= 2
    for s in lone:
        assert s["links"] == []
        assert s["peak"] is None or s["peak"]["unit"] == s["unit_indices"][0]


# ── one moment in two stories ───────────────────────────────────────────────


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


def test_a_shared_moment_carries_two_stories_two_roles_two_edges():
    """Required test #5: a moment may belong to several stories at once."""
    c = build_corpus(_shared_segs(), duration=75.0)
    v = story_view(c, _shared_segs())
    m4 = v["moments"][4]
    assert m4["story_ids"] == [0, 2]
    assert [s["story_id"] for s in m4["stories"]] == [0, 2]
    assert [e["unit"] for e in m4["edges"]] == [0, 2]
    assert m4["establishes"] == [] and m4["depends_on"] == [0, 2]
    for s in m4["stories"]:
        assert s["role"] == "tension"
        # the role is read from the story that claims the moment, so the
        # justification is story-scoped rather than stated once for both
        assert s["why"]


# ── the taxonomy: what this memory claims, and what it refuses to claim ─────


def test_the_taxonomy_lists_every_kind_the_directive_names():
    v = _view()
    rel = v["relationships"]
    assert rel["kinds_inferred_from_evidence"] == [
        "question_answer", "problem_solution", "causal", "callback",
        "contrast_turn"]
    assert rel["roles_derived_from_edge_position"] == ["sets_up", "pays_off"]
    covered = {r["kind"] for r in rel["recorded_under_another_analysis"]}
    assert covered == {"repeats", "expands", "develops"}
    unsupported = {r["kind"] for r in rel["unsupported"]}
    assert unsupported == {"supports", "explains", "provides_context",
                           "foreshadows", "creates_conflict", "escalates",
                           "qualifies", "depends_on"}


def test_an_unsupported_relationship_never_appears_as_an_edge():
    """The eight unsupported kinds are refused by construction, so no moment may
    carry one: listing them in the taxonomy would be a promise otherwise."""
    v = _view()
    forbidden = {r["kind"] for r in v["relationships"]["unsupported"]}
    for m in v["moments"]:
        for e in m["edges"]:
            assert e["kind"] not in forbidden
        for c in m["chain_mates"]:
            assert c["reason"]


def test_chain_mates_report_co_membership_never_inference():
    """Units 0 and 3 of thread 0 share a story but no typed edge: the chain
    holds both, and that is what the entry says."""
    v = _view()
    mates = {c["unit"]: c for c in v["moments"][0]["chain_mates"]}
    assert 3 in mates
    assert mates[3]["story_id"] == 0
    assert "connected by the chain" in mates[3]["reason"]
    assert "not by an inferred relationship" in mates[3]["reason"]
    # a pair the typed edges already cover is never reported twice
    for m in v["moments"]:
        assert all(c["unit"] != 2 for c in m["chain_mates"])


def test_chain_mates_are_symmetric_and_never_self_referential():
    v = _view()
    for m in v["moments"]:
        for c in m["chain_mates"]:
            assert c["unit"] != m["unit"]
            reciprocal = any(x["unit"] == m["unit"] for x in v["moments"][c["unit"]]["chain_mates"])
            assert reciprocal, f"unit {m['unit']} claims unit {c['unit']} one-way"


# ── the human document ──────────────────────────────────────────────────────


def test_story_md_names_the_source_the_graph_and_every_story():
    c = build_corpus(_story_segs(), duration=65.0, source="interview.mov")
    md = render_story_md(c, _story_segs())
    lines = md.splitlines()
    assert any("source: interview.mov" in l for l in lines)
    assert any("corpus fingerprint" in l for l in lines)
    assert any("## STORY 01" in l for l in lines)
    assert any("WHY THESE CONNECT" in l for l in lines)
    assert any("HOW A STORY BECOMES AN EDIT" in l for l in lines)
    assert any("RELATIONSHIPS THIS MEMORY DOES NOT CLAIM" in l for l in lines)


def test_story_md_quotes_the_speaker_and_cites_the_evidence():
    c = build_corpus(_story_segs(), duration=65.0)
    md = render_story_md(c, _story_segs())
    lines = md.splitlines()
    assert any("00:00" in l and "satellite clock" in l for l in lines)
    # the evidence block quotes both ends of an edge, not a paraphrase
    assert any("question_answer" in l for l in lines)


def test_story_md_reports_a_refused_moment_under_its_story():
    segs = _story_segs()
    segs[0] = _seg(0.0, 8.0, "Which is why the satellite clock failed. "
                             "The launch was nominal.")
    md = render_story_md(build_corpus(segs, duration=65.0), segs)
    lines = md.splitlines()
    assert any("REFUSED BY THE EDITORIAL GATE" in l for l in lines)
    assert any("mid-conversation" in l for l in lines)


def test_story_md_labels_each_moment_by_its_role_in_that_story():
    c = build_corpus(_shared_segs(), duration=75.0)
    md = render_story_md(c, _shared_segs())
    lines = md.splitlines()
    # split the document at its story headings so a count is scoped to one story
    starts = [i for i, l in enumerate(lines) if l.startswith("## STORY ")]
    starts.append(len(lines))
    blocks = {lines[s].split(" — ", 1)[0][len("## STORY "):]:
              lines[starts[k] + 1:starts[k + 1]] for k, s in enumerate(starts[:-1])}
    assert len(blocks) >= 3
    # a moment-list line is indented by two spaces and carries the role label;
    # the evidence quotes below it are indented deeper and are not labels
    def labels(block):
        return [l for l in block
                if re.match(r"^  [A-Z]+ +\d\d:\d\d ", l) and "synchronise" in l]

    # unit 4 closes story 01 and story 03, and each labels it exactly once
    for sid in ("01", "03"):
        assert len(labels(blocks[sid])) == 1, \
            f"story {sid} labels unit 4 {len(labels(blocks[sid]))}x"
    # a story that does not hold unit 4 must not list it
    for sid, block in blocks.items():
        if sid in ("01", "03"):
            continue
        assert not labels(block), f"story {sid} lists a moment it does not hold"


# ── the bundle: three files, one memory ──────────────────────────────────────


def test_write_story_memory_writes_three_files(tmp_path: Path):
    c = build_corpus(_story_segs(), duration=65.0)
    files = write_story_memory(c, tmp_path, _story_segs())
    assert isinstance(files, StoryMemoryFiles)
    for p in (files.corpus, files.view, files.story_md):
        assert p.exists()
        assert p.read_text(encoding="utf-8")
    assert files.corpus.name == "editorial_corpus.json"
    assert files.view.name == "story_view.json"
    assert files.story_md.name == "story.md"


def test_the_corpus_file_is_byte_identical_to_the_canonical_writer(tmp_path: Path):
    """The memory's canonical half is the corpus itself, written by the corpus's
    own serialiser — the view does not get a second opinion on what the graph is."""
    c = build_corpus(_story_segs(), duration=65.0)
    files = write_story_memory(c, tmp_path, _story_segs())
    assert files.corpus.read_text(encoding="utf-8") == \
        json.dumps(c.to_dict(), indent=2, ensure_ascii=False) + "\n"


def test_the_view_file_is_valid_json_round_tripping_the_schema(tmp_path: Path):
    c = build_corpus(_story_segs(), duration=65.0)
    files = write_story_memory(c, tmp_path, _story_segs())
    back = json.loads(files.view.read_text(encoding="utf-8"))
    assert back["schema"] == VIEW_SCHEMA
    # JSON has no integer keys, so a round trip through it stringifies the int
    # keys a live dict carries (a story's ``roles``); compare both sides after
    # the same serialisation, and the file text itself is byte-stable.
    fresh = story_view(c, _story_segs())
    assert back == json.loads(json.dumps(fresh))
    assert files.view.read_text(encoding="utf-8") == \
        json.dumps(fresh, indent=2, ensure_ascii=False) + "\n"


# ── the reader: an exported view is loadable by a process that never built it


def test_read_story_view_accepts_a_path_a_string_a_dict_and_json(tmp_path: Path):
    c = build_corpus(_story_segs(), duration=65.0)
    files = write_story_memory(c, tmp_path, _story_segs())
    views = [read_story_view(files.view),
             read_story_view(str(files.view)),
             read_story_view(files.view.read_text(encoding="utf-8")),
             # a live dict holds int keys the file cannot; both sides go through
             # the same serialisation before they are compared
             read_story_view(json.loads(json.dumps(story_view(c, _story_segs()))))]
    for v in views:
        assert v["provenance"]["unit_count"] == 5
    assert views[0] == views[3]


def test_read_story_view_refuses_an_unknown_schema():
    v = _view()
    v["schema"] = "9.9"
    with pytest.raises(ValueError, match="will not guess"):
        read_story_view(v)


def test_read_story_view_refuses_the_right_shape_under_the_wrong_kind():
    v = _view()
    v["kind"] = "something.else"
    with pytest.raises(ValueError, match="not 'selektyt.story_view'"):
        read_story_view(v)


def test_read_story_view_refuses_a_json_array():
    with pytest.raises(ValueError, match="a story view is a JSON object"):
        read_story_view(json.dumps([1, 2, 3]))


# ── verification: the view agrees with the corpus, or says where it does not


def test_a_genuine_round_trip_verifies_clean(tmp_path: Path):
    c = build_corpus(_story_segs(), duration=65.0)
    files = write_story_memory(c, tmp_path, _story_segs())
    back = read_corpus(files.corpus)
    view = read_story_view(files.view)
    assert verify_story_view(view, back) == []


def test_verification_catches_a_view_that_disagrees():
    """Tampering with the view is reported, and the corpus is named as the
    memory: the check never concludes the corpus is the stale one."""
    c = build_corpus(_story_segs(), duration=65.0)
    v = story_view(c, _story_segs())
    v["moments"][0]["end"] = v["moments"][0]["end"] + 60.0
    problems = verify_story_view(v, c)
    assert problems
    assert any("unit 0" in p or "moment" in p for p in problems)


def test_verification_catches_a_story_that_lost_a_moment():
    c = build_corpus(_story_segs(), duration=65.0)
    v = story_view(c, _story_segs())
    v["stories"][0]["unit_indices"] = [0, 2]
    assert verify_story_view(v, c)


def test_verification_catches_a_moment_the_view_invented():
    c = build_corpus(_story_segs(), duration=65.0)
    v = story_view(c, _story_segs())
    v["moments"].append(dict(v["moments"][0], unit=99))
    assert verify_story_view(v, c)


# ── requirement #12: an imported memory regenerates the same structure ──────


def test_a_reimported_corpus_yields_a_byte_identical_view(tmp_path: Path):
    """The memory is re-derivable from its own file. If it were not, an import
    would be a promise rather than a reconstruction."""
    c = build_corpus(_story_segs(), duration=65.0)
    files = write_story_memory(c, tmp_path, _story_segs())
    back = read_corpus(files.corpus)
    assert story_view(back, _story_segs()) == story_view(c, _story_segs())


def test_a_reimported_corpus_yields_a_byte_identical_document(tmp_path: Path):
    c = build_corpus(_story_segs(), duration=65.0)
    files = write_story_memory(c, tmp_path, _story_segs())
    back = read_corpus(files.corpus)
    assert render_story_md(back, _story_segs()) == render_story_md(c, _story_segs())


def test_the_view_of_an_imported_memory_verifies_against_the_import(tmp_path: Path):
    c = build_corpus(_story_segs(), duration=65.0)
    files = write_story_memory(c, tmp_path, _story_segs())
    back = read_corpus(files.corpus)
    assert verify_story_view(read_story_view(files.view), back) == []
