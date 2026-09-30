"""Failure-first tests for ``engine.memory.evidence``.

The hazards here are different from the interval algebra's. Intervals break by
being numerically wrong; evidence breaks by being *convincingly* wrong — a claim
that carries confidence 1.0 over zero spans, a modality that silently flips on a
round trip, a link that points at an id nobody ever assigned. Every case below is
one of those, and the assertions pin behaviour the editorial layer will rely on.
"""
import json

import pytest

from engine.memory.evidence import (
    SCHEMA_VERSION,
    Evidence,
    EvidenceLink,
    EvidenceSet,
    Modality,
    Source,
)
from engine.memory.intervals import ALLEN_RELATIONS, Interval, IntervalSet


def _ev(**over) -> Evidence:
    base = dict(
        claim="The second speaker is on screen",
        intervals=IntervalSet.of((12.0, 31.0)),
        modality=Modality.VISUAL,
        source=Source.of("engine.reframe", "track_subject_center", stride=28),
    )
    base.update(over)
    return Evidence(**base)


# ── Modality ────────────────────────────────────────────────────────────────

def test_modality_serialises_as_plain_name():
    """``str`` mixin so JSON needs no custom encoder — a consumer reading the
    persisted record must see a name, not ``<Modality.VISUAL: 'visual'>``."""
    assert json.dumps({"m": Modality.VISUAL}) == '{"m": "visual"}'


def test_modality_round_trips_by_name():
    assert Modality("visual") is Modality.VISUAL
    assert Modality("editorial") is Modality.EDITORIAL


def test_modality_unknown_name_raises():
    """An unknown modality in a persisted record is a schema break, not a
    default to text — defaulting would silently recategorise evidence."""
    with pytest.raises(ValueError):
        Modality("haptic")


def test_modality_order_is_not_a_quality_ranking():
    """The enum order is the order the pipeline produces these channels. It must
    not be read as trustworthiness; TEXT from a 91%-WER transcript is weaker
    than VISUAL from a reliable detector."""
    assert list(Modality) == [
        Modality.TEXT, Modality.AUDIO, Modality.VISUAL, Modality.EDITORIAL,
    ]


# ── Source and the params digest ────────────────────────────────────────────

def test_source_bare_has_empty_digest():
    s = Source("engine.reframe", "track_subject_center")
    assert s.params_digest == ""


def test_source_digest_is_key_order_independent():
    """A re-run with a differently-ordered kwargs dict is the same run; the
    digest must not claim the instrument changed."""
    a = Source.of("m", "f", stride=28, threshold=0.6)
    b = Source.of("m", "f", threshold=0.6, stride=28)
    assert a.params_digest == b.params_digest
    assert a == b


def test_source_digest_distinguishes_parameters():
    """A memory built at 28 samples per window is not the same memory as one
    built at 56 — the store checks this rather than trusting filenames."""
    a = Source.of("m", "f", stride=28)
    b = Source.of("m", "f", stride=56)
    assert a.params_digest != b.params_digest


def test_source_digest_is_short_and_stable():
    a = Source.of("m", "f", stride=28)
    again = Source.of("m", "f", stride=28)
    assert len(a.params_digest) <= 12
    assert a.params_digest == again.params_digest


def test_source_round_trip():
    s = Source.of("m", "f", stride=28)
    assert Source.from_dict(s.to_dict()) == s


# ── Evidence construction and confidence ────────────────────────────────────

def test_evidence_defaults():
    ev = _ev()
    assert ev.confidence == 1.0
    assert ev.grounded is True
    assert ev.rationale == ""
    assert ev.artifacts == ()
    assert ev.links == ()
    assert ev.id == ""


def test_evidence_over_high_confidence_is_clamped():
    """Pinned as current behaviour: an instrument reporting 1.4 is clamped, not
    raised. ``__post_init__`` clamping is a deliberate repair — but note it is
    silent, so a caller passing 1.4 and reading 1.0 cannot tell it happened."""
    ev = _ev(confidence=1.4)
    assert ev.confidence == 1.0


def test_evidence_negative_confidence_is_clamped_to_zero():
    ev = _ev(confidence=-0.5)
    assert ev.confidence == 0.0


def test_evidence_nan_confidence_does_not_propagate():
    """NaN is the same class of poison as a NaN interval: any comparison
    involving it is False. This test caught a real defect — the range clamp
    used to map NaN to 1.0, because ``min(1.0, nan)`` returns 1.0, so a sensor
    that divided by zero was recorded as maximally trustworthy."""
    ev = _ev(confidence=float("nan"))
    assert ev.confidence == 0.0


def test_evidence_infinite_confidence_is_not_superlative():
    """Infinity is a division-by-zero symptom, not a confidence above all
    others. ``min(1.0, inf)`` returns 1.0, so the range clamp alone would have
    promoted a broken sensor reading to maximum trust too."""
    assert _ev(confidence=float("inf")).confidence == 0.0
    assert _ev(confidence=float("-inf")).confidence == 0.0


def test_evidence_confidence_at_bounds_is_kept():
    assert _ev(confidence=0.0).confidence == 0.0
    assert _ev(confidence=1.0).confidence == 1.0


def test_evidence_is_immutable():
    """Frozen: an evidence object in flight must not be mutated out from under
    a query that already returned it."""
    ev = _ev()
    with pytest.raises(Exception):
        ev.confidence = 0.5  # type: ignore[misc]


def test_evidence_cardinality_and_duration_come_from_intervals():
    ev = _ev(intervals=IntervalSet.of((1.0, 3.0), (10.0, 14.0)))
    assert ev.cardinality == 2
    assert ev.duration == pytest.approx(6.0)


def test_evidence_supporting_zero_spans_is_representable():
    """The empty-interval case is the one to be careful with: a claim with
    confidence 1.0 and cardinality 0 asserts support nowhere. It is legal data
    (an instrument that found nothing), but a consumer that reads confidence
    without reading cardinality will over-trust it."""
    ev = _ev(intervals=IntervalSet.empty(), confidence=1.0)
    assert ev.cardinality == 0
    assert ev.intervals.is_empty
    assert ev.duration == 0.0


# ── Evidence links: the seed of the evidence graph ──────────────────────────

def test_link_rejects_non_allen_relation():
    """A typo in a relation becomes a loud error, not an unmatchable string the
    graph later silently fails to join."""
    with pytest.raises(ValueError):
        EvidenceLink("e2", "precedes")


def test_link_accepts_every_allen_name():
    for name in ALLEN_RELATIONS:
        assert EvidenceLink("e2", name).relation == name


def test_evidence_link_appends_and_validates():
    ev = _ev(links=(EvidenceLink("e2", "before"),))
    linked = ev.link("e3", "meets")
    assert [(l.target, l.relation) for l in linked.links] == [
        ("e2", "before"), ("e3", "meets"),
    ]
    # The original is untouched — link returns a copy.
    assert [(l.target, l.relation) for l in ev.links] == [("e2", "before")]


def test_two_links_keep_their_own_labels():
    """The defect this pins: a relation label shared across a tuple of targets
    is overwritten by the last ``link`` call, so an editor tracing the chain
    read the wrong relation for the first edge. One record per edge is what
    makes both labels survive."""
    linked = _ev().link("B", "before").link("C", "after")
    assert [(l.target, l.relation) for l in linked.links] == [
        ("B", "before"), ("C", "after"),
    ]


def test_link_rejects_bad_relation():
    with pytest.raises(ValueError):
        _ev().link("e2", "after_a_while")


def test_link_preserves_everything_else():
    ev = _ev(id="e1", rationale="face track stable", confidence=0.8)
    linked = ev.link("e2", "overlaps")
    assert linked.id == "e1"
    assert linked.rationale == "face track stable"
    assert linked.confidence == 0.8
    assert linked.intervals == ev.intervals
    assert linked.modality is ev.modality


def test_link_converse_names_the_reverse_edge():
    """The graph must never carry two spellings of one relation; the reverse
    edge is derived, not hand-written."""
    fwd = EvidenceLink("e2", "before")
    assert fwd.converse().relation == "after"
    assert fwd.converse().target == "e2"


def test_evidence_with_id_returns_copy():
    ev = _ev()
    named = ev.with_id("e7")
    assert named.id == "e7"
    assert ev.id == ""
    assert named.claim == ev.claim
    assert named.intervals == ev.intervals


def test_evidence_with_no_link_fields_round_trip():
    ev = _ev()
    back = Evidence.from_dict(ev.to_dict())
    assert back == ev
    assert back.links == ()


# ── Evidence serialisation ──────────────────────────────────────────────────

def test_evidence_round_trip_preserves_every_field():
    ev = _ev(
        confidence=0.72,
        rationale="YuNet track held across 19 frames",
        grounded=False,
        artifacts=("runs/x/frame_0042.png",),
        links=(EvidenceLink("e2", "during"), EvidenceLink("e3", "meets")),
        id="e1",
    )
    back = Evidence.from_dict(json.loads(json.dumps(ev.to_dict())))
    assert back == ev
    assert back.intervals == ev.intervals
    assert back.modality is ev.modality
    assert back.artifacts == ("runs/x/frame_0042.png",)
    assert [(l.target, l.relation) for l in back.links] == [
        ("e2", "during"), ("e3", "meets"),
    ]
    assert back.id == "e1"
    assert back.grounded is False


def test_evidence_round_trip_preserves_multi_span_intervals():
    """Cardinality is the field an eval harness compares; it must survive JSON
    without collapsing to one span."""
    ev = _ev(intervals=IntervalSet.of((1.0, 3.0), (10.0, 14.0), (20.0, 21.0)))
    back = Evidence.from_dict(json.loads(json.dumps(ev.to_dict())))
    assert back.cardinality == 3
    assert list(back.intervals) == list(ev.intervals)


def test_evidence_round_trip_preserves_instant_intervals():
    ev = _ev(intervals=IntervalSet.of(Interval(4.0, 4.0)))
    back = Evidence.from_dict(json.loads(json.dumps(ev.to_dict())))
    assert back.cardinality == 1
    assert list(back.intervals)[0].is_instant


def test_evidence_dict_carries_schema():
    assert _ev().to_dict()["schema"] == SCHEMA_VERSION


def test_evidence_from_dict_requires_a_claim():
    """A record without a claim is not evidence of anything; a KeyError here is
    correct, and a default to "" would fabricate an empty assertion."""
    with pytest.raises(KeyError):
        Evidence.from_dict({"intervals": {"intervals": []}})


def test_evidence_from_dict_defaults_missing_optional_fields():
    """Tolerant of a record written by an earlier version of the schema."""
    back = Evidence.from_dict({
        "claim": "x",
        "intervals": {"intervals": [{"start": 0.0, "end": 1.0}]},
    })
    assert back.modality is Modality.TEXT
    assert back.confidence == 1.0
    assert back.grounded is True
    assert back.source == Source("", "")


def test_evidence_from_dict_rejects_unknown_modality():
    with pytest.raises(ValueError):
        Evidence.from_dict({
            "claim": "x",
            "intervals": {"intervals": []},
            "modality": "haptic",
        })


def test_evidence_from_dict_migrates_the_legacy_link_schema():
    """Schema 1.0 carried a tuple of target ids under one shared relation label.
    A record written then must still load, as one edge per target — and a record
    with targets but no label cannot be recovered as typed edges, so its links
    are dropped rather than guessed at."""
    back = Evidence.from_dict({
        "schema": "1.0",
        "claim": "x",
        "intervals": {"intervals": [{"start": 0.0, "end": 1.0}]},
        "relates_to": ["e2", "e3"],
        "relation": "during",
    })
    assert [(l.target, l.relation) for l in back.links] == [
        ("e2", "during"), ("e3", "during"),
    ]

    unlabelled = Evidence.from_dict({
        "claim": "x",
        "intervals": {"intervals": []},
        "relates_to": ["e2"],
    })
    assert unlabelled.links == ()


def test_evidence_from_dict_tolerates_null_optional_fields():
    """A record corrupted on disk — or written by a serializer that emitted nulls
    for absent values — must load rather than raise, because the alternative is
    losing the whole record over one null field."""
    back = Evidence.from_dict({
        "claim": "x",
        "intervals": None,
        "source": None,
        "rationale": None,
        "artifacts": None,
        "links": None,
        "id": None,
    })
    assert back.claim == "x"
    assert back.intervals.is_empty
    assert back.source == Source("", "")
    assert back.rationale == ""
    assert back.artifacts == ()
    assert back.links == ()
    assert back.id == ""


def test_evidence_determinism():
    """Identical input yields identical evidence — no wall clock, no network,
    no inference. Two independently built objects must compare equal including
    their digests."""
    a = _ev(confidence=0.5, rationale="r")
    b = _ev(confidence=0.5, rationale="r")
    assert a == b
    assert a.to_dict() == b.to_dict()


# ── EvidenceSet: the variable-cardinality answer object ─────────────────────

def _set() -> EvidenceSet:
    return EvidenceSet.of(
        _ev(claim="Speaker on screen", intervals=IntervalSet.of((0.0, 10.0)),
            modality=Modality.VISUAL, confidence=0.9),
        _ev(claim="Budget discussed", intervals=IntervalSet.of((5.0, 8.0), (40.0, 50.0)),
            modality=Modality.TEXT, confidence=0.4),
        _ev(claim="Slide shown", intervals=IntervalSet.of((60.0, 70.0)),
            modality=Modality.VISUAL, confidence=0.95),
    )


def test_set_empty_is_valid_answer():
    s = EvidenceSet.empty()
    assert s.is_empty
    assert len(s) == 0
    assert s.cardinality == 0
    assert s.intervals.is_empty
    assert list(s) == []


def test_set_cardinality_counts_spans_not_items():
    """The cost of acting on an answer is the number of spans, not the number
    of claims — this is the number that separates one thing to check from
    forty."""
    s = _set()
    assert len(s) == 3
    assert s.cardinality == 4


def test_set_intervals_unions_every_span():
    s = _set()
    assert list(s.intervals) == [
        Interval(0.0, 10.0), Interval(40.0, 50.0), Interval(60.0, 70.0),
    ]


def test_set_intervals_empty_when_no_items():
    assert EvidenceSet.empty().intervals.is_empty


def test_set_for_modality():
    s = _set()
    vis = s.for_modality(Modality.VISUAL)
    assert len(vis) == 2
    assert all(i.modality is Modality.VISUAL for i in vis)
    assert s.for_modality(Modality.AUDIO).is_empty


def test_set_for_modality_survives_round_trip():
    """Pinned because the filter compares against an enum whose ``str`` mixin
    means a deserialised modality compares equal by value: if the filter used
    identity instead, every modality filter would silently return empty and the
    grounding layer would conclude there is no visual evidence."""
    s = EvidenceSet([Evidence.from_dict(i.to_dict()) for i in _set()])
    assert len(s.for_modality(Modality.VISUAL)) == 2


def test_set_for_modality_accepts_a_plain_string():
    """``Modality`` mixes in ``str`` precisely so a caller holding the persisted
    value — a plain ``"visual"`` — does not have to know to re-wrap it. This is
    the call the store will make on every read."""
    s = _set()
    assert len(s.for_modality("visual")) == 2  # type: ignore[arg-type]
    assert s.for_modality("audio").is_empty  # type: ignore[arg-type]


def test_set_in_window_does_not_clip():
    """Deliberately: an editor needs to know the evidence extends past the
    window, which a clipped interval would hide."""
    s = _set()
    got = s.in_window(45.0, 65.0)
    assert [i.claim for i in got] == ["Budget discussed", "Slide shown"]
    assert got.with_id is not None


def test_set_in_window_boundary_inclusive():
    s = _set()
    assert len(s.in_window(0.0, 0.0)) == 1


def test_set_in_window_outside_all_spans_is_empty():
    assert _set().in_window(100.0, 200.0).is_empty


def test_set_above_confidence():
    s = _set()
    assert len(s.above_confidence(0.5)) == 2
    assert len(s.above_confidence(0.95)) == 1
    assert s.above_confidence(1.01).is_empty


def test_set_below_confidence_is_the_complement():
    """The question "what did I discard" is one the verify stage asks, and a
    single filter cannot answer both ways — so the complement is its own
    method."""
    s = _set()
    high = s.above_confidence(0.5)
    low = s.below_confidence(0.5)
    assert len(low) == 1
    assert [i.claim for i in low] == ["Budget discussed"]
    assert len(high) + len(low) == len(s)
    assert {i.claim for i in high} ^ {i.claim for i in low} == {
        i.claim for i in s
    }


def test_set_grounded_only():
    s = EvidenceSet.of(
        _ev(claim="observed", grounded=True),
        _ev(claim="inferred", grounded=False),
    )
    assert [i.claim for i in s.grounded_only()] == ["observed"]


def test_set_about_matches_claim_and_rationale():
    s = EvidenceSet.of(
        _ev(claim="The budget is discussed", rationale=""),
        _ev(claim="Something else", rationale="budget figure on the slide"),
        _ev(claim="Unrelated", rationale="Unrelated"),
    )
    got = s.about("budget")
    assert {i.claim for i in got} == {"The budget is discussed", "Something else"}


def test_set_about_is_case_insensitive_and_crude():
    """Documented as a keyword match, not a semantic one. A matcher that
    implied understanding it does not have would be worse here."""
    assert len(_set().about("BUDGET")) == 1
    assert _set().about("xylophone").is_empty


def test_set_with_id_finds_and_returns_none():
    s = EvidenceSet.of(_ev(id="e1"), _ev(id="e2", claim="other"))
    assert s.with_id("e2").claim == "other"
    assert s.with_id("missing") is None


def test_set_with_id_on_unassigned_ids():
    """Nothing in the set has an id until a store assigns one; the lookup must
    report that rather than raising."""
    assert EvidenceSet.of(_ev()).with_id("anything") is None


def test_set_union_keeps_duplicates():
    """Union is concatenation, not a set-union: two channels may independently
    report the same claim, and collapsing them would lose that they came from
    two instruments."""
    a = EvidenceSet.of(_ev(id="e1"))
    b = EvidenceSet.of(_ev(id="e1"))
    u = a.union(b)
    assert len(u) == 2


def test_set_union_with_empty_is_identity():
    s = _set()
    assert list(s.union(EvidenceSet.empty())) == list(s)


def test_set_restrict_clips_and_drops_items_outside():
    s = _set()
    kept = s.restrict(IntervalSet.of((4.0, 6.0)))
    assert [i.claim for i in kept] == ["Speaker on screen", "Budget discussed"]
    first = kept.with_id is not None
    assert first


def test_set_restrict_clips_the_spans_not_the_claim():
    """The clip, not the source, is now the world — but the claim text is
    unchanged, because the claim was about the whole source span."""
    s = _set()
    kept = list(s.restrict(IntervalSet.of((4.0, 6.0))))
    spans = [list(i.intervals) for i in kept]
    assert spans == [[Interval(4.0, 6.0)], [Interval(5.0, 6.0)]]
    assert kept[1].claim == "Budget discussed"


def test_set_restrict_drops_items_that_lose_everything():
    s = _set()
    kept = s.restrict(IntervalSet.of((200.0, 300.0)))
    assert kept.is_empty


def test_set_restrict_preserves_provenance_and_links():
    ev = _ev(id="e1", confidence=0.6, rationale="r", grounded=False,
             links=(EvidenceLink("e9", "before"),),
             artifacts=("a.png",))
    kept = list(EvidenceSet.of(ev).restrict(IntervalSet.of((5.0, 20.0))))
    assert kept[0].id == "e1"
    assert kept[0].confidence == 0.6
    assert kept[0].rationale == "r"
    assert kept[0].grounded is False
    assert [(l.target, l.relation) for l in kept[0].links] == [("e9", "before")]
    assert kept[0].artifacts == ("a.png",)
    assert kept[0].source == ev.source


def test_set_restrict_does_not_mutate_original():
    s = _set()
    s.restrict(IntervalSet.of((5.0, 8.0)))
    assert [i.claim for i in s] == [
        "Speaker on screen", "Budget discussed", "Slide shown",
    ]
    assert s.cardinality == 4


def test_set_round_trip():
    s = _set()
    back = EvidenceSet.from_dict(json.loads(json.dumps(s.to_dict())))
    assert len(back) == len(s)
    assert back.cardinality == s.cardinality
    assert [i.claim for i in back] == [i.claim for i in s]
    assert list(back.intervals) == list(s.intervals)


def test_set_round_trip_empty():
    back = EvidenceSet.from_dict(EvidenceSet.empty().to_dict())
    assert back.is_empty


def test_set_dict_carries_counts():
    d = _set().to_dict()
    assert d["schema"] == SCHEMA_VERSION
    assert d["count"] == 3
    assert d["cardinality"] == 4


def test_set_iter_and_len_agree():
    s = _set()
    assert len(list(iter(s))) == len(s) == 3
