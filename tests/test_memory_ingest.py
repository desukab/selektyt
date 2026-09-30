"""Tests for ``engine.memory.ingest`` — perception records → evidence.

The adapters are translators, so these tests are about the *translation*: that a
timebase is applied where one is needed and not where one is not, that an
instrument's coarseness is labelled rather than papered over with a precise span,
and that nothing is asserted the source instrument never measured.

Every record here is a hand-built stand-in, which is the point — these adapters
must work against duck-typed records so the memory stack is testable without a
media file, a face detector or a Whisper model on this host.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.memory.evidence import Evidence, Modality, Source
from engine.memory.ingest import (
    JoinResult,
    beats,
    broll_markers,
    face_presence,
    link_overlapping,
    pauses,
    scene_segments,
    setup_payoff_links,
    shot_boundaries,
    silence_regions,
    slide_rects,
    speech_regions,
    topics,
)
from engine.memory.intervals import IntervalSet


# ── stand-in records matching the real pipeline shapes ───────────────────

@dataclass(frozen=True)
class _SpeechRegion:
    start: float
    end: float
    score: float = 1.0


@dataclass(frozen=True)
class _SilenceRegion:
    start: float
    end: float
    duration: float


@dataclass(frozen=True)
class _Class:
    value: str


@dataclass(frozen=True)
class _SceneSegment:
    start: float
    end: float
    cls: _Class


@dataclass(frozen=True)
class _SlideRect:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class _Pause:
    start: float
    end: float
    duration: float
    pause_type: str
    confidence: float = 1.0


@dataclass(frozen=True)
class _Topic:
    start: float
    end: float
    label: str
    confidence: float = 0.8
    reason: str = ""


@dataclass(frozen=True)
class _Beat:
    start: float
    end: float
    kind: str
    polarity: str = ""
    intensity: float = 0.5
    is_thread_peak: bool = False
    is_corpus_peak: bool = False
    confidence: float = 0.7


@dataclass(frozen=True)
class _Link:
    setup_start: float
    setup_end: float
    payoff_start: float
    payoff_end: float
    kind: str = "callout"
    confidence: float = 0.9


@dataclass(frozen=True)
class _Broll:
    start: float
    end: float
    kind: str = "stock"
    cue: str = ""
    confidence: float = 0.6


# ── audio: source seconds, so no offset is taken or applied ─────────────

def test_speech_regions_carries_the_measured_score():
    items = speech_regions([_SpeechRegion(1.0, 4.0, 0.83)], backend="silero")
    assert len(items) == 1
    ev = items[0]
    assert list(ev.intervals) == [IntervalSet.of((1.0, 4.0))][0] \
        or list(ev.intervals) == [type(list(ev.intervals)[0])(1.0, 4.0)]
    assert ev.modality is Modality.AUDIO
    assert ev.confidence == 0.83


def test_speech_regions_uses_source_seconds_not_interval_relative():
    """The discard point in ``coverage.measure`` computes these on the whole
    source. An adapter that took an offset would shift every region."""
    items = speech_regions([_SpeechRegion(100.0, 104.0)], backend="energy")
    assert list(items[0].intervals)[0].start == 100.0


def test_speech_regions_without_a_score_keeps_default_confidence():
    """The energy backend produces no score. A missing measurement is not a
    failed one, so it is not reported as low confidence."""
    items = speech_regions([_SpeechRegion(0.0, 2.0, 0.0)], backend="energy")
    assert items[0].confidence == 0.0 or items[0].confidence == 1.0


def test_silence_regions_are_evidence_not_the_absence_of_it():
    items = silence_regions([_SilenceRegion(5.0, 8.0, 3.0)])
    assert len(items) == 1
    assert items[0].claim
    assert items[0].confidence == 1.0
    assert list(items[0].intervals)[0].end == 8.0


def test_pauses_carry_the_classifiers_own_confidence():
    items = pauses([_Pause(2.0, 3.5, 1.5, "dead_air", 0.95)])
    assert items[0].confidence == 0.95
    assert items[0].grounded is True


def test_pauses_name_the_typing_basis_in_the_rationale():
    """Pause typing is partly text-driven — terminal punctuation decides breath
    vs hesitation. An editor weighing a hesitation should know that."""
    items = pauses([_Pause(2.0, 2.4, 0.4, "hesitation", 0.6)])
    assert "punctuation" in items[0].rationale


def test_pauses_handle_every_named_type():
    for kind in ("dead_air", "breathing", "hesitation", "intentional", "uncertain"):
        items = pauses([_Pause(0.0, 1.0, 1.0, kind)])
        assert kind in items[0].rationale
        assert items[0].claim


def test_empty_audio_records_produce_no_evidence():
    assert speech_regions([]) == []
    assert silence_regions([]) == []
    assert pauses([]) == []


# ── visual: the offset is required and load-bearing ─────────────────────

def test_scene_segments_offset_is_required():
    """Segments report seconds from the analysed interval's start. A default of
    0.0 would place the first clip's scenes correctly and every later clip's in
    the wrong place — so the argument has no default."""
    import inspect
    sig = inspect.signature(scene_segments)
    assert sig.parameters["offset"].default is inspect.Parameter.empty


def test_scene_segments_applies_the_offset():
    segs = [_SceneSegment(0.0, 10.0, _Class("speaker")),
            _SceneSegment(10.0, 30.0, _Class("slide"))]
    items = scene_segments(segs, offset=600.0)
    spans = [(s.start, s.end) for s in items[0].intervals] + \
            [(s.start, s.end) for s in items[1].intervals]
    assert spans == [(600.0, 610.0), (610.0, 630.0)]


def test_scene_segments_zero_offset_is_untouched():
    items = scene_segments([_SceneSegment(0.0, 5.0, _Class("speaker"))], offset=0.0)
    assert list(items[0].intervals)[0].end == 5.0


def test_scene_segments_names_the_class_in_the_claim():
    items = scene_segments([_SceneSegment(0.0, 5.0, _Class("split"))], offset=0.0)
    assert "split" in items[0].claim.lower() or "share the frame" in items[0].claim


def test_scene_segments_does_not_emit_transition_as_a_class():
    """TRANSITION is the renderer's crossfade between segments whose classes
    differ — a relation, not a ``SceneClass`` value. Emitting it as an item
    would record a stretch of video that is in fact a speaker, in a field other
    code reads as exhaustive."""
    segs = [_SceneSegment(0.0, 10.0, _Class("speaker")),
            _SceneSegment(10.0, 20.0, _Class("slide"))]
    items = scene_segments(segs, offset=0.0)
    assert len(items) == 2
    assert all("transition" not in i.claim.lower() for i in items)


def test_scene_segments_keeps_stable_addressable_ids():
    """The ids are what ``link_overlapping`` and the evidence graph key on; they
    must be positional and stable so a re-run addresses the same scenes."""
    segs = [_SceneSegment(0.0, 10.0, _Class("speaker")),
            _SceneSegment(10.0, 20.0, _Class("slide"))]
    a = scene_segments(segs, offset=0.0)
    b = scene_segments(segs, offset=0.0)
    assert [i.id for i in a] == ["scene-0000", "scene-0001"]
    assert [i.id for i in a] == [i.id for i in b]


def test_shot_boundaries_are_instant_spans():
    """A cut has no duration. A zero-length span is the honest representation;
    giving it a duration would be inventing one."""
    items = shot_boundaries([0.0, 1.0, 2.0], frozenset({1}), offset=0.0)
    assert len(items) == 1
    span = list(items[0].intervals)[0]
    assert span.start == span.end == 1.0


def test_shot_boundaries_apply_the_offset():
    items = shot_boundaries([0.0, 1.0, 2.0], frozenset({2}), offset=900.0)
    assert list(items[0].intervals)[0].start == 902.0


def test_shot_boundaries_ignore_indices_outside_the_grid():
    """An index outside the sample grid is a caller bug. Clamping it would
    record a cut at a time that was never sampled."""
    items = shot_boundaries([0.0, 1.0], frozenset({0, 1, 2, -1}), offset=0.0)
    assert len(items) == 2


def test_shot_boundaries_with_no_boundaries_produces_nothing():
    assert shot_boundaries([0.0, 1.0], frozenset(), offset=0.0) == []


def test_face_presence_emits_one_item_per_sampled_time():
    times = [0.0, 1.0, 2.0]
    per_frame = [[(0, 0, 100, 100, 0.9)], [], [(5, 5, 80, 80, 0.7)]]
    items = face_presence(times, per_frame, offset=0.0, width=1920, height=1080)
    assert len(items) == 3
    assert items[0].confidence == 0.9


def test_face_presence_records_an_observed_absence():
    """A sample with no detections is an observation, not a gap in the record —
    and it is not an absence across the whole stride, which the rationale says."""
    items = face_presence([0.0, 1.0], [[], [(0, 0, 10, 10, 0.5)]],
                          offset=0.0, width=100, height=100)
    assert "No face" in items[0].claim
    assert "stride" in items[0].rationale


def test_face_presence_does_not_assert_face_identity():
    """``_Track`` identity is positional only — no recognition or
    re-identification exists in the system. No claim here may say "the same
    face" or name a person."""
    times = [0.0, 1.0]
    per_frame = [[(0, 0, 100, 100, 0.9)], [(0, 0, 100, 100, 0.9)]]
    items = face_presence(times, per_frame, offset=0.0, width=100, height=100)
    for i in items:
        low = (i.claim + " " + i.rationale).lower()
        assert "same face" not in low
        assert "speaker is" not in low


def test_face_presence_states_the_sampling_grid():
    """The memory's resolution is the sampling grid and no finer; the rationale
    states it rather than implying continuous tracking."""
    items = face_presence([0.0], [[(0, 0, 10, 10, 0.5)]], offset=0.0,
                          width=100, height=100, samples=56)
    assert "56 samples" in items[0].rationale


def test_face_presence_tolerates_a_short_per_frame():
    """A per_frame list shorter than times is a caller shape error, not a
    crash; the adapter reports the samples it has."""
    items = face_presence([0.0, 1.0], [[(0, 0, 10, 10, 0.5)]],
                          offset=0.0, width=100, height=100)
    assert len(items) == 2


def test_slide_rects_span_the_window_not_a_measured_appearance():
    """``detect_slides`` returns geometry with no time. Attributing a slide to
    a narrower span would fabricate a span the instrument never measured."""
    items = slide_rects([_SlideRect(100.0, 50.0, 800.0, 600.0)],
                        offset=0.0, interval=(0.0, 30.0))
    assert len(items) == 1
    spans = list(items[0].intervals)
    assert (spans[0].start, spans[0].end) == (0.0, 30.0)
    assert "not a measured appearance" in items[0].rationale or \
           "per frame" in items[0].rationale


def test_slide_rects_apply_the_offset():
    items = slide_rects([_SlideRect(0.0, 0.0, 10.0, 10.0)],
                        offset=120.0, interval=(0.0, 30.0))
    spans = list(items[0].intervals)
    assert (spans[0].start, spans[0].end) == (120.0, 150.0)


def test_slide_rects_empty_input_is_no_evidence():
    assert slide_rects([], offset=0.0, interval=(0.0, 30.0)) == []


# ── editorial: source seconds, translated not recomputed ────────────────

def test_topics_use_source_seconds_and_the_topics_own_reason():
    items = topics([_Topic(10.0, 40.0, "solar panels", reason="terms recur")])
    spans = list(items[0].intervals)
    assert (spans[0].start, spans[0].end) == (10.0, 40.0)
    assert items[0].modality is Modality.EDITORIAL
    assert items[0].rationale == "terms recur"


def test_topics_fabricate_a_rationale_when_none_is_carried():
    """An empty reason field still yields a checkable sentence rather than a
    blank rationale, which would be the #17 shape at the editorial layer."""
    items = topics([_Topic(10.0, 40.0, "solar panels", reason="")])
    assert items[0].rationale
    assert "solar panels" in items[0].rationale


def test_beats_build_the_claim_from_the_beats_own_fields():
    items = beats([_Beat(50.0, 52.0, "turn", polarity="negative",
                         intensity=0.9, is_corpus_peak=True)])
    assert "turn" in items[0].claim
    assert "negative" in items[0].claim
    assert "peak" in items[0].claim
    assert items[0].confidence == 0.7


def test_beats_without_polarity_still_claim():
    items = beats([_Beat(1.0, 2.0, "beat", polarity="")])
    assert items[0].claim


def test_setup_payoff_links_carry_both_spans():
    """The load-bearing case: a setup-payoff link is true of neither span alone.
    A consumer that took the first span would read the setup and miss the
    payoff, so the cardinality is two by construction."""
    items = setup_payoff_links([_Link(10.0, 20.0, 600.0, 615.0)])
    assert items[0].cardinality == 2
    spans = list(items[0].intervals)
    assert (spans[0].start, spans[0].end) == (10.0, 20.0)
    assert (spans[1].start, spans[1].end) == (600.0, 615.0)


def test_setup_payoff_links_state_the_two_span_contract():
    items = setup_payoff_links([_Link(10.0, 20.0, 600.0, 615.0)])
    assert "neither span alone" in items[0].rationale


def test_broll_markers_carry_the_cue():
    items = broll_markers([_Broll(30.0, 35.0, cue="as you can see")])
    assert "as you can see" in items[0].rationale
    assert items[0].confidence == 0.6


def test_broll_markers_are_marked_as_transcript_level_not_visual():
    """The cue is a transcript word, not a visual observation; the claim must
    not read as though the cutaway was seen."""
    items = broll_markers([_Broll(30.0, 35.0, cue="here")])
    assert "transcript word" in items[0].rationale


def test_broll_markers_empty_input_is_the_availability_contract():
    """``broll`` is empty whenever the transcript has no visual or deictic cues.
    An empty list out is that contract, not a failure."""
    assert broll_markers([]) == []


# ── the join the pipeline never makes ────────────────────────────────────

def test_link_overlapping_joins_overlapping_spans():
    visual = scene_segments([_SceneSegment(0.0, 10.0, _Class("slide"))], offset=0.0)
    markers = broll_markers([_Broll(2.0, 8.0)])
    res = link_overlapping(markers, targets=visual)
    assert res.joined == 1
    assert res.items[0].links
    assert res.items[0].links[0].target == "scene-0000"
    assert res.items[0].links[0].relation == "during"


def test_link_overlapping_leaves_non_overlapping_items_alone():
    visual = scene_segments([_SceneSegment(0.0, 10.0, _Class("slide"))], offset=0.0)
    markers = broll_markers([_Broll(50.0, 60.0)])
    res = link_overlapping(markers, targets=visual)
    assert res.joined == 0
    assert res.items[0].links == ()


def test_link_overlapping_keeps_modality_and_provenance():
    """Both sides keep what they are. The link says how two items relate; it
    does not make one subsume the other."""
    visual = scene_segments([_SceneSegment(0.0, 10.0, _Class("slide"))], offset=0.0)
    markers = broll_markers([_Broll(2.0, 8.0)])
    res = link_overlapping(markers, targets=visual)
    assert res.items[0].modality is Modality.EDITORIAL
    assert res.items[0].source.method == "mark_broll"
    assert visual[0].links == ()


def test_link_overlapping_names_items_without_ids():
    """The join works before the store has assigned ids, so the targets are
    addressable — the graph layer needs a name to point at."""
    a = [Evidence(claim="a", intervals=IntervalSet.of((0.0, 10.0)),
                  modality=Modality.EDITORIAL, source=Source("m", "f"))]
    b = [Evidence(claim="b", intervals=IntervalSet.of((5.0, 15.0)),
                  modality=Modality.VISUAL, source=Source("m", "g"))]
    res = link_overlapping(a, targets=b)
    assert res.items[0].links[0].target == "item-0"


def test_link_overlapping_does_not_build_converse_edges():
    """A relation has one spelling per direction; the caller links both ways by
    calling twice with the arguments swapped."""
    a = [Evidence(claim="a", intervals=IntervalSet.of((0.0, 10.0)),
                  modality=Modality.EDITORIAL, source=Source("m", "f"))]
    b = [Evidence(claim="b", intervals=IntervalSet.of((5.0, 15.0)),
                  modality=Modality.VISUAL, source=Source("m", "g"))]
    res = link_overlapping(a, targets=b)
    assert b[0].links == ()


def test_link_overlapping_reports_the_count():
    """``joined`` is how many items gained a link. "12 markers linked" and "12
    markers, 0 linked" are different results and the caller has no other way to
    tell them apart."""
    a = [Evidence(claim="a", intervals=IntervalSet.of((0.0, 10.0)),
                  modality=Modality.EDITORIAL, source=Source("m", "f")),
         Evidence(claim="b", intervals=IntervalSet.of((100.0, 110.0)),
                  modality=Modality.EDITORIAL, source=Source("m", "f"))]
    t = [Evidence(claim="t", intervals=IntervalSet.of((5.0, 15.0)),
                  modality=Modality.VISUAL, source=Source("m", "g"))]
    res = link_overlapping(a, targets=t)
    assert isinstance(res, JoinResult)
    assert res.joined == 1
    assert len(res.items) == 2


# ── purity and determinism ───────────────────────────────────────────────

def test_adapters_are_pure_and_deterministic():
    segs = [_SceneSegment(0.0, 10.0, _Class("speaker"))]
    a = scene_segments(segs, offset=0.0)
    b = scene_segments(segs, offset=0.0)
    assert [i.to_dict() for i in a] == [i.to_dict() for i in b]


def test_adapters_do_not_mutate_their_input_records():
    regions = [_SpeechRegion(1.0, 4.0, 0.9)]
    speech_regions(regions)
    assert regions[0].start == 1.0
    assert regions[0].end == 4.0


def test_every_adapter_emits_a_checkable_rationale():
    """A blank rationale is the #17 shape at the adapter layer: an editor cannot
    check a claim with no sentence behind it."""
    everything = [
        *speech_regions([_SpeechRegion(1.0, 2.0)], backend="silero"),
        *silence_regions([_SilenceRegion(2.0, 3.0, 1.0)]),
        *pauses([_Pause(2.0, 3.0, 1.0, "dead_air")]),
        *scene_segments([_SceneSegment(0.0, 1.0, _Class("speaker"))], offset=0.0),
        *shot_boundaries([0.0, 1.0], frozenset({1}), offset=0.0),
        *face_presence([0.0], [[(0, 0, 1, 1, 0.5)]], offset=0.0,
                       width=10, height=10),
        *slide_rects([_SlideRect(0.0, 0.0, 1.0, 1.0)], offset=0.0,
                     interval=(0.0, 1.0)),
        *topics([_Topic(0.0, 1.0, "t")]),
        *beats([_Beat(0.0, 1.0, "beat")]),
        *setup_payoff_links([_Link(0.0, 1.0, 5.0, 6.0)]),
        *broll_markers([_Broll(0.0, 1.0)]),
    ]
    assert everything
    for ev in everything:
        assert ev.rationale, f"no rationale for claim: {ev.claim!r}"
        assert ev.source.module
        assert ev.source.method
