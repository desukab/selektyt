"""The corpus reader is the import half of the story-memory round trip.

``build_corpus`` writes; ``read_corpus`` reads; and the two share no code path,
which is what makes the round trip honest — a byte that survives the trip can
only have come from the file. These tests pin that discipline along with every
refusal in the reader's contract, because the story-memory tests read every
corpus through this module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine import corpus as corpus_mod
from engine.corpus import SCHEMA_VERSION, build_corpus
from engine.corpus_io import CorpusReadError, read_corpus
from models.core import Segment, Word


def _seg(a: float, b: float, text: str) -> Segment:
    words = text.split()
    span = (b - a) / max(1, len(words))
    return Segment(
        a, b, text,
        [Word(w, a + i * span, a + (i + 1) * span, 0.9) for i, w in enumerate(words)],
    )


def _corpus() -> corpus_mod.EditorialCorpus:
    """A corpus built the way the pipeline builds one — the only writer."""
    return build_corpus(
        [
            _seg(0.0, 8.0, "The satellite clock failed on the third orbit. "
                           "The station saw the drift."),
            _seg(10.0, 18.0, "Why did the satellite clock fail? "
                             "The battery was under-spec."),
            _seg(20.0, 28.0, "So the satellite clock was lost entirely. "
                             "The agency grounded the fleet."),
            _seg(30.0, 38.0, "Why did the battery fail? The vendor cut the "
                             "specification."),
            _seg(40.0, 48.0, "So the battery was replaced. The vendor paid "
                             "the replacement cost."),
        ],
        duration=55.0,
    )


# ── the round trip ──────────────────────────────────────────────────────────


def test_build_then_read_recovers_every_field():
    c = _corpus()
    back = read_corpus(c.to_dict())
    assert back.build.schema == SCHEMA_VERSION
    assert back.build.unit_count == len(c.units) == len(back.units)
    # The spine is position-addressable, so index and position must agree.
    assert [u.index for u in back.units] == list(range(len(back.units)))
    assert [u.start for u in back.units] == [u.start for u in c.units]
    # The analyses survive with their evidence, not just their counts.
    assert len(back.links) == len(c.links)
    assert all(len(lk.evidence) == 2 for lk in back.links)
    assert [t.unit_indices for t in back.threads] == \
        [t.unit_indices for t in c.threads]
    assert back.topics == c.topics
    assert back.redundancy_groups == c.redundancy_groups
    assert back.rhythm == c.rhythm
    assert back.source == c.source


def test_thread_membership_survives_the_round_trip():
    """A unit may belong to several stories; that is a list, and it must persist."""
    c = _corpus()
    back = read_corpus(c.to_dict())
    assert back.unit_threads == c.unit_threads
    assert len(back.unit_threads) == len(back.units)
    for ids in back.unit_threads:
        for tid in ids:
            assert 0 <= tid < len(back.threads)
    # The single primary thread stays consistent with the membership lists.
    for u, ids in enumerate(back.unit_threads):
        primary = back.units[u].thread_id
        if ids:
            assert primary is not None
            assert primary in ids
        else:
            assert primary is None


def test_read_accepts_a_path_a_string_and_a_dict(tmp_path: Path):
    c = _corpus()
    path = tmp_path / "editorial_corpus.json"
    path.write_text(json.dumps(c.to_dict()), encoding="utf-8")

    from_path = read_corpus(path)
    from_str_file = read_corpus(str(path))
    from_json = read_corpus(json.dumps(c.to_dict()))
    from_dict = read_corpus(c.to_dict())

    for got in (from_path, from_str_file, from_json, from_dict):
        assert got.unit_threads == c.unit_threads
        assert [t.unit_indices for t in got.threads] == \
            [t.unit_indices for t in c.threads]


def test_reader_does_not_call_the_builder(monkeypatch):
    """The reader must not re-analyse the source; if it did, the round trip
    would report whatever the current analyser produces rather than the file."""
    def _boom(*a, **kw):
        raise AssertionError("read_corpus re-ran the analyser")

    monkeypatch.setattr(corpus_mod, "build_corpus", _boom)
    c = _corpus()
    back = read_corpus(json.dumps(c.to_dict()))
    assert back.unit_threads == c.unit_threads


# ── schema 1.0 upgrade ──────────────────────────────────────────────────────


def _v10(c: corpus_mod.EditorialCorpus) -> dict:
    """A file as schema 1.0 wrote it: no per-unit thread membership key."""
    d = c.to_dict()
    d["build"]["schema"] = "1.0"
    del d["unit_threads"]
    return d


def test_schema_1_0_is_upgraded_by_deriving_membership():
    c = _corpus()
    d = _v10(c)
    back = read_corpus(d)
    # Membership is derived from the threads' own ``unit_indices``, which are
    # the definition of a story, not from the unit's single primary thread.
    derived: list[list[int]] = [[] for _ in back.units]
    for t in back.threads:
        for u in t.unit_indices:
            derived[u].append(t.thread_id)
    assert back.unit_threads == derived
    assert back.build.schema == SCHEMA_VERSION
    assert any("derived from thread.unit_indices" in n for n in back.notes)


def test_schema_1_0_with_disagreeing_halves_resolves_to_the_threads():
    """A file whose unit field and thread membership disagree is resolved in
    favour of the stories it actually declares."""
    c = _corpus()
    d = _v10(c)
    d["units"][0]["thread_id"] = 999  # impossible under any reading of 1.0
    with pytest.raises(CorpusReadError):
        read_corpus(d)


# ── refusals: the file is not what it claims to be ──────────────────────────


@pytest.mark.parametrize("name", ["not_json.json", "empty.json"])
def test_non_json_is_refused(tmp_path: Path, name: str):
    path = tmp_path / name
    path.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(CorpusReadError, match="not valid JSON"):
        read_corpus(path)


def test_a_json_array_is_not_a_corpus():
    with pytest.raises(CorpusReadError, match="a corpus is a JSON object"):
        read_corpus(json.dumps([1, 2, 3]))


def test_missing_schema_is_refused():
    d = _corpus().to_dict()
    del d["build"]["schema"]
    with pytest.raises(CorpusReadError, match="no schema version"):
        read_corpus(d)


def test_unknown_schema_is_refused():
    d = _corpus().to_dict()
    d["build"]["schema"] = "9.9"
    with pytest.raises(CorpusReadError, match="will not guess"):
        read_corpus(d)


def test_unreadable_file_is_reported(tmp_path: Path):
    with pytest.raises(CorpusReadError, match="cannot read"):
        read_corpus(tmp_path / "no_such_file.json")


def test_build_unit_count_must_match_the_spine():
    d = _corpus().to_dict()
    d["build"]["unit_count"] = 999
    with pytest.raises(CorpusReadError, match="claims 999"):
        read_corpus(d)


def test_an_index_that_resolves_to_nothing_is_refused():
    d = _corpus().to_dict()
    d["units"][0]["topic_id"] = 500
    with pytest.raises(CorpusReadError, match=r"unit\[0\].topic_id"):
        read_corpus(d)


def test_a_thread_link_must_precede_its_payoff():
    d = _corpus().to_dict()
    d["links"] = [dict(d["links"][0])] if d.get("links") else [
        {"setup_unit": 3, "payoff_unit": 1, "kind": "causal", "strength": 0.5,
         "setup_start": 30.0, "setup_end": 38.0, "payoff_start": 10.0,
         "payoff_end": 18.0, "confidence": 0.5, "reason": "x", "evidence": []}]
    lk = d["links"][0]
    lk["setup_unit"], lk["payoff_unit"] = lk["payoff_unit"], lk["setup_unit"]
    with pytest.raises(CorpusReadError, match="must precede its payoff"):
        read_corpus(d)


def test_a_span_that_ends_before_it_starts_is_refused():
    d = _corpus().to_dict()
    d["units"][0]["end"] = d["units"][0]["start"] - 5.0
    with pytest.raises(CorpusReadError, match=r"unit\[0\]"):
        read_corpus(d)


def test_a_nan_timecode_is_refused_not_read_as_zero():
    """``NaN`` compares equal to nothing, itself included.

    A reader that only checked ``end >= start`` would accept it, and the
    resulting span would silently poison every timecode derived from it —
    including the source timestamps a story memory exports — so it is refused
    by name rather than coerced.
    """
    d = _corpus().to_dict()
    d["units"][0]["end"] = float("nan")
    with pytest.raises(CorpusReadError, match="is not a finite number"):
        read_corpus(d)


def test_a_redundancy_representative_must_be_a_member():
    c = _corpus()
    d = c.to_dict()
    if not d["redundancy_groups"]:
        pytest.skip("the fixture produced no redundancy groups")
    g = d["redundancy_groups"][0]
    g["representative"] = 4242
    with pytest.raises(CorpusReadError, match="not one of the group's members"):
        read_corpus(d)


def test_an_empty_corpus_must_not_declare_stories():
    d = _corpus().to_dict()
    d["units"] = []
    d["build"]["unit_count"] = 0
    with pytest.raises(CorpusReadError, match="declares no units but holds"):
        read_corpus(d)


def test_a_required_field_the_file_omits_is_refused():
    d = _corpus().to_dict()
    del d["source"]
    with pytest.raises(CorpusReadError, match="no 'source'"):
        read_corpus(d)


def test_a_field_the_file_carries_that_this_version_lacks_is_dropped():
    """A file written by a newer SeleKyT stays readable for everything this
    version understands: unknown fields are dropped, not fatal."""
    d = _corpus().to_dict()
    d["units"][0]["future_field"] = {"anything": True}
    d["future_analysis"] = [1, 2, 3]
    back = read_corpus(d)
    assert not hasattr(back, "future_analysis")
    assert back.unit_threads == _corpus().unit_threads


def test_a_scalar_of_the_wrong_kind_is_refused():
    d = _corpus().to_dict()
    d["build"]["unit_count"] = "five"
    with pytest.raises(CorpusReadError, match="expected an integer"):
        read_corpus(d)


def test_a_boolean_is_not_an_integer():
    d = _corpus().to_dict()
    d["units"][0]["index"] = True
    with pytest.raises(CorpusReadError, match="expected an integer, got boolean"):
        read_corpus(d)


def test_membership_indexed_out_of_range_is_refused():
    d = _corpus().to_dict()
    d["unit_threads"][0] = [4242]
    with pytest.raises(CorpusReadError, match=r"unit_threads\[0\]"):
        read_corpus(d)


def test_rhythm_length_must_match_the_spine():
    d = _corpus().to_dict()
    if d["rhythm"].get("per_unit"):
        d["rhythm"]["per_unit"] = d["rhythm"]["per_unit"][:-1]
        with pytest.raises(CorpusReadError, match="rhythm reports"):
            read_corpus(d)
