"""Failure-first tests for ``engine.retrieval`` — INDEX and RETRIEVE.

Each test names the hazard it guards against in its docstring, following the
convention in ``tests/test_memory_intervals.py`` and ``tests/test_memory_evidence.py``.
The test surface itself is the one ``docs/retrieval-design.md`` §7 commits to, so
a test that goes missing here is a design contract that stopped being checked.
"""

from __future__ import annotations

import json
import time

import pytest

from engine.language_fitness import FitnessReport
from engine.memory.intervals import IntervalSet
from engine.retrieval import (
    SCHEMA_VERSION,
    RetrievalIndex,
    StaleIndexError,
    build_index,
    retrieve,
    source_fingerprint,
)
from models.core import Segment, Word

_STEP = 0.25


def _seg(start: float, text: str, confs=None, conf: float = 1.0) -> Segment:
    """One utterance whose words are evenly spaced, so timings are predictable."""
    words = text.split()
    return Segment(
        start, start + _STEP * len(words), text,
        [Word(w, start + _STEP * i, start + _STEP * i + 0.2,
              confs[i] if confs else conf)
         for i, w in enumerate(words)])


def _usable() -> FitnessReport:
    return FitnessReport("usable", "en", "small", wer=0.0503,
                         cer=0.0311, clip_count=40)


def _poor() -> FitnessReport:
    return FitnessReport("poor", "te", "medium", wer=0.9154,
                         cer=0.6924, clip_count=50)


def _index(segments, **over):
    params = dict(language="en", source_fingerprint="fp-1", fitness=_usable())
    params.update(over)
    return build_index(segments, **params)


# ── index build ──────────────────────────────────────────────────────────────


def test_retrieval_index_is_byte_identical_across_builds(tmp_path):
    """hazard: a nondeterministic index makes "stale" untestable and caching
    unsound, because two builds of one transcript could differ and neither would
    be the wrong one."""
    segments = [_seg(0.0, "the coolant pump failed at the reactor"),
                _seg(60.0, "the harvest silo opened for the season")]
    first = _index(segments).save(tmp_path / "first.json")
    second = _index(segments).save(tmp_path / "second.json")
    assert first.read_bytes() == second.read_bytes()


def test_retrieval_index_round_trip_loses_nothing(tmp_path):
    """hazard: a serialisation that drops the side data silently degrades every
    confidence figure read back from disk."""
    segments = [_seg(0.0, "coolant pump failed"),
                _seg(20.0, "harvest silo opened")]
    index = _index(segments)
    path = index.save(tmp_path / "index.json")
    back = RetrievalIndex.load(path)
    assert back.schema == SCHEMA_VERSION
    assert back.source_fingerprint == index.source_fingerprint
    assert back.postings == index.postings
    assert back.segments == index.segments
    assert back.word_confidence == index.word_confidence
    assert back.word_terms == index.word_terms
    assert back.n_terms == index.n_terms


def test_retrieval_index_is_refused_when_the_source_fingerprint_differs(tmp_path):
    """hazard: an index built for one transcript is served for another, and
    retrieval answers a question about the wrong video."""
    index = _index([_seg(0.0, "coolant pump failed")])
    path = index.save(tmp_path / "index.json")
    with pytest.raises(StaleIndexError) as excinfo:
        RetrievalIndex.load(path, source_fingerprint="a-different-source")
    assert "was built for source" in str(excinfo.value)


def test_retrieval_index_loads_when_the_fingerprint_matches(tmp_path):
    path = _index([_seg(0.0, "coolant pump failed")]).save(tmp_path / "i.json")
    assert RetrievalIndex.load(path, source_fingerprint="fp-1").n_terms > 0


def test_retrieval_index_survives_a_near_empty_transcript():
    """hazard: the 2-words-per-minute ``telugu-tutorial`` case crashes or loops
    on a nine-word index, or invents a span to avoid returning nothing."""
    sparse = [_seg(0.0, "one two three"),
              _seg(120.0, "four five six")]
    index = _index(sparse, language="te")
    assert index.is_empty is False
    empty = _index([], language="te")
    assert empty.is_empty is True
    # A query against a near-empty index answers emptiness, never an error.
    result = retrieve(index, ["coolant"])
    assert result.is_empty
    assert result.evidence == ()


def test_retrieval_index_reports_absent_optional_channels():
    """hazard: VAD absent is silently treated as VAD-empty, so two runs on the
    same source answer in different characters."""
    segments = [_seg(0.0, "coolant pump failed")]
    absent = _index(segments)
    assert absent.optional["vad"].loaded is False
    assert absent.optional["editorial_corpus"].loaded is False
    # The note is what distinguishes "never ran" from "ran and found none".
    assert "not supplied" in absent.optional["vad"].note

    present = _index(segments, speech_regions=[(0.0, 1.0)],
                     editorial_units=[(0.0, 5.0)])
    assert present.optional["vad"].loaded is True
    assert present.optional["vad"].count == 1
    assert present.optional["vad"].note == ""
    assert present.optional["editorial_corpus"].count == 1


def test_retrieval_postings_are_sorted_by_start():
    """hazard: an unsorted postings list makes ``bisect`` silently return wrong
    windows rather than erroring."""
    segments = [_seg(0.0, "coolant pump coolant valve coolant housing")]
    index = _index(segments)
    starts = [hit[0] for hit in index.postings["coolant"]]
    assert starts == sorted(starts)
    word_ids = [hit[2] for hit in index.postings["coolant"]]
    assert word_ids == sorted(word_ids)


def test_retrieval_index_segment_array_covers_every_word():
    """hazard: a word outside every stored utterance makes containment return
    None and the window sweep drops a real match."""
    segments = [_seg(0.0, "coolant pump failed"),
                _seg(10.0, "harvest silo opened")]
    index = _index(segments)
    assert index.n_words == 6
    for word_idx in range(index.n_words):
        assert index.segment_index_of_word(word_idx) is not None
    assert index.segment_index_of_word(-1) is None


def test_retrieval_index_carries_the_fitness_verdict():
    """hazard: an index built on a ``poor`` transcript is read as authoritative
    by a consumer that never asked about language fitness."""
    index = _index([_seg(0.0, "coolant pump")], fitness=_poor())
    assert index.fitness["verdict"] == "poor"
    assert index.fitness["wer"] == pytest.approx(0.9154)


# ── retrieve ─────────────────────────────────────────────────────────────────


def test_retrieve_returns_a_set_of_intervals_not_one_span():
    """hazard: a consumer assumes a single span and takes the first of four
    matches, wrong three times in four."""
    segments = [
        _seg(0.0, "the coolant pump failed at the reactor"),
        _seg(300.0, "a completely different subject about silage"),
        _seg(600.0, "the coolant pump failed a second time here"),
        _seg(900.0, "the coolant pump failed a third time today"),
    ]
    index = _index(segments)
    result = retrieve(index, ["coolant", "pump"], k=10, nms_s=1.0)
    assert isinstance(result.intervals, IntervalSet)
    assert result.intervals.cardinality == 3
    assert result.intervals.cardinality == len(result.evidence)


def test_retrieve_returns_intervals_in_ascending_start_order():
    segments = [
        _seg(900.0, "the coolant pump failed late"),
        _seg(0.0, "the coolant pump failed early"),
    ]
    index = _index(segments)
    result = retrieve(index, ["coolant"], k=10, nms_s=1.0)
    starts = [iv.start for iv in result.intervals.intervals]
    assert starts == sorted(starts)


def test_retrieve_phrase_query_does_not_match_terms_in_reverse_order():
    """hazard: "adjacent" collapses to "both present" and the answer widens to
    unrelated passages."""
    segments = [
        # "coolant pump" in forward order, twice.
        _seg(0.0, "the coolant pump failed"),
        # The same two words, reversed and far apart, must not count.
        _seg(500.0, "we saw the pump and later the coolant"),
    ]
    index = _index(segments)
    assert retrieve(index, [("coolant", "pump")], k=10,
                    nms_s=1.0).intervals.cardinality == 1
    assert retrieve(index, [("pump", "coolant")], k=10,
                    nms_s=1.0).intervals.cardinality == 0


def test_retrieve_phrase_query_rejects_terms_too_far_apart():
    """hazard: two words ten minutes apart are reported as a phrase."""
    segments = [_seg(0.0, "coolant is mentioned")]
    far = [_seg(0.0, "the pump is here"),
           _seg(600.0, "and the coolant over there")]
    index = _index(far)
    assert retrieve(index, [("pump", "coolant")]).is_empty


def test_retrieve_reports_emptiness_when_nothing_matches():
    """hazard: a zero-hit query returns a nearest span and the editor clips
    something that was never said."""
    index = _index([_seg(0.0, "the coolant pump failed")])
    result = retrieve(index, ["asparagus"])
    assert result.is_empty
    assert result.evidence == ()
    assert result.intervals.duration == 0.0
    # And it says so, rather than returning a silent null.
    assert "nothing in this source matches" in result.report


def test_retrieve_does_not_silently_loosen_a_failing_query():
    """hazard: automatic relaxation turns "not found" into "found something
    unrelated" with no trace of the change."""
    index = _index([_seg(0.0, "the coolant pump failed"),
                    _seg(100.0, "the harvest silo opened")])
    result = retrieve(index, ["coolant", "asparagus"])
    assert result.is_empty
    # The surviving term was reachable; the query as a whole was not loosened to
    # reach it.
    assert "not loosened" in result.report
    assert retrieve(index, ["coolant"]).intervals.cardinality == 1


def test_retrieve_nms_suppresses_repeats_within_60s():
    """hazard: one passage spoken twice fills all K slots and three distinct
    chapters are never returned."""
    segments = [
        # A repeat 20 s later — the same passage in the same chapter. NMS must
        # keep one of the pair, not both.
        _seg(0.0, "alpha bravo the coolant pump failed at the reactor"),
        _seg(20.0, "alpha bravo the coolant pump failed at the reactor again"),
        # Three genuinely distinct chapters, each more than 60 s from the others.
        _seg(400.0, "alpha bravo the harvest silo opened for the season"),
        _seg(800.0, "alpha bravo the turbine blade fractured at the root"),
        _seg(1200.0, "alpha bravo the pipeline valve leaked at the refinery"),
    ]
    index = _index(segments)
    # "alpha bravo" anchors every chapter, so a window for it exists near each
    # of the five occurrences above.
    result = retrieve(index, ["alpha", "bravo"], k=3, nms_s=60.0)
    starts = [iv.start for iv in result.intervals.intervals]
    assert len(starts) == 3
    # The suppressed repeat is gone: no two kept windows open within 60 s.
    for i, kept in enumerate(starts):
        for other in starts[i + 1:]:
            assert abs(kept - other) >= 60.0


def test_retrieve_nms_keeps_repeats_that_are_far_apart():
    """hazard: NMS is so aggressive that a phrase genuinely recurring across
    chapters is collapsed to one answer."""
    segments = [
        _seg(0.0, "the coolant pump failed at the reactor"),
        _seg(600.0, "the coolant pump failed at the refinery"),
        _seg(1200.0, "the coolant pump failed at the depot"),
    ]
    index = _index(segments)
    result = retrieve(index, ["coolant", "pump"], k=3, nms_s=60.0)
    assert result.intervals.cardinality == 3


def test_retrieve_respects_k_and_does_not_return_extras():
    """hazard: K is advisory and downstream code assumes exactly three windows."""
    segments = [_seg(i * 400.0, "the coolant pump failed again") for i in range(6)]
    index = _index(segments)
    for k in (1, 2, 3, 5):
        result = retrieve(index, ["coolant"], k=k, nms_s=1.0)
        assert result.intervals.cardinality == k
        assert len(result.evidence) == k


def test_retrieve_carries_the_language_verdict_in_every_result():
    """hazard: a ``poor`` transcript's retrieval results are presented with the
    confidence of a ``usable`` one's, laundering 91.54% WER into authority."""
    segments = [_seg(0.0, "the coolant pump failed"),
                _seg(400.0, "the coolant pump failed again")]
    index = _index(segments, language="te", fitness=_poor())
    result = retrieve(index, ["coolant", "pump"], k=5, nms_s=1.0)
    assert result.evidence
    for evidence in result.evidence:
        assert "verdict is poor (WER 91.5%)" in evidence.rationale


def test_retrieve_carries_the_verdict_when_fitness_was_never_measured():
    segments = [_seg(0.0, "the coolant pump failed")]
    index = _index(segments, fitness=None)
    result = retrieve(index, ["coolant"])
    assert "never measured" in result.evidence[0].rationale


def test_retrieve_low_confidence_words_lower_window_confidence():
    """hazard: the ASR itself said these words are doubtful and retrieval
    presents them as solid."""
    low = [_seg(0.0, "the coolant pump failed", confs=[1.0, 1.0, 0.1, 1.0, 1.0])]
    high = [_seg(0.0, "the coolant pump failed", confs=[1.0, 1.0, 1.0, 1.0, 1.0])]
    query = ["coolant", "pump"]
    low_conf = retrieve(_index(low), query).evidence[0].confidence
    high_conf = retrieve(_index(high), query).evidence[0].confidence
    assert low_conf < high_conf
    assert 0.0 <= low_conf <= 1.0


def test_retrieve_confidence_never_exceeds_one():
    noisy = [_seg(0.0, "the coolant pump failed now",
                  confs=[5.0, -3.0, 2.0, 1.0, 0.5])]
    index = _index(noisy)
    assert retrieve(index, ["coolant"]).evidence[0].confidence <= 1.0
    # A negative or overflowing ASR confidence is recorded as zero doubt-free
    # quality, not as trust above the scale.
    assert all(0.0 <= c <= 1.0 for c in index.word_confidence)


def test_retrieve_source_digest_changes_with_k_or_nms():
    """hazard: a conclusion from K = 3 is inherited by a K = 10 run and treated
    as the same measurement."""
    index = _index([_seg(0.0, "the coolant pump failed")])
    a = retrieve(index, ["coolant"], k=3, nms_s=60.0).evidence[0].source
    b = retrieve(index, ["coolant"], k=10, nms_s=60.0).evidence[0].source
    c = retrieve(index, ["coolant"], k=3, nms_s=30.0).evidence[0].source
    assert a.params_digest != b.params_digest
    assert a.params_digest != c.params_digest
    assert b.params_digest != c.params_digest
    # Same parameters reproduce the same digest, so the digest is not noise.
    again = retrieve(index, ["coolant"], k=3, nms_s=60.0).evidence[0].source
    assert again.params_digest == a.params_digest


def test_retrieve_stopwords_are_scored_low_not_deleted():
    """hazard: a deleted stopword makes every phrase containing one unmatchable,
    and the term set silently depends on an English stoplist in a Telugu corpus."""
    segments = [
        _seg(0.0, "the coolant pump failed at the reactor"),
        _seg(60.0, "the harvest silo opened for the season"),
        _seg(120.0, "the turbine blade fractured at the root"),
    ]
    index = _index(segments)
    # The stopword is still indexed, so a phrase containing it can match.
    assert "the" in index.postings
    assert retrieve(index, [("coolant", "pump", "failed", "at", "the")],
                    k=5, nms_s=1.0).intervals.cardinality == 1
    # And it carries a lower idf weight than the content word, because df counts
    # it in every utterance rather than removing it from the vocabulary.
    idf = index.idf()
    assert idf["the"] < idf["coolant"]


def test_retrieve_ties_break_toward_the_tighter_span():
    """hazard: at equal term overlap a 40 s window outranks a 3 s one and the
    editor is handed a chapter instead of the sentence."""
    segments = [
        _seg(0.0, "coolant pump"),
        _seg(400.0, "coolant pump and then a very long digression about "
                    "nothing in particular that runs on and on and on"),
    ]
    index = _index(segments)
    result = retrieve(index, ["coolant", "pump"], k=2, nms_s=1.0)
    first = result.evidence[0]
    assert first.intervals.duration < result.evidence[1].intervals.duration


def test_retrieve_boosts_a_window_inside_measured_speech():
    """hazard: a speech-region boost is folded into the score silently, so two
    runs of the same query rank differently for reasons no rationale names.

    The window's words carry a sub-1.0 ASR confidence on purpose: a window that
    contains every query term scores 1.0 before the boost, so a perfect ASR
    confidence would clamp the boosted product back to 1.0 and hide the boost.
    """
    segments = [_seg(0.0, "the coolant pump failed at the reactor",
                     confs=[0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5])]
    plain = _index(segments)
    boosted = _index(segments, speech_regions=[(0.0, 2.0)])
    query = ["coolant", "pump"]
    plain_score = retrieve(plain, query).evidence[0].confidence
    boosted_score = retrieve(boosted, query).evidence[0].confidence
    assert boosted_score > plain_score
    rationale = retrieve(boosted, query).evidence[0].rationale
    assert "speech_region re-ranking applied" in rationale
    # And a window outside every measured region is not boosted.
    elsewhere = _index(segments, speech_regions=[(50.0, 60.0)])
    assert retrieve(elsewhere, query).evidence[0].confidence == plain_score


def test_retrieve_boosts_a_window_inside_an_editorial_unit():
    segments = [_seg(0.0, "the coolant pump failed at the reactor",
                     confs=[0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5])]
    plain = _index(segments)
    boosted = _index(segments, editorial_units=[(0.0, 5.0)])
    query = ["coolant", "pump"]
    assert (retrieve(boosted, query).evidence[0].confidence
            > retrieve(plain, query).evidence[0].confidence)
    evidence = retrieve(boosted, query).evidence[0]
    assert "editorial_unit re-ranking applied" in evidence.rationale
    # The modality moves to EDITORIAL where the story-structure channel decided.
    assert evidence.modality.value == "editorial"


def test_retrieve_names_the_absent_channel_in_the_rationale():
    """hazard: a channel that never loaded reads as one that loaded and found
    nothing, so its absence is never investigated."""
    index = _index([_seg(0.0, "the coolant pump failed")])
    rationale = retrieve(index, ["coolant"]).evidence[0].rationale
    assert "was not loaded" in rationale


def test_retrieve_result_reports_the_queryable_channels_on_empty():
    index = _index([_seg(0.0, "the coolant pump failed")],
                   speech_regions=[(0.0, 1.0)])
    result = retrieve(index, ["asparagus"])
    assert result.channels["vad"] is True
    assert result.channels["editorial_corpus"] is False
    assert "queryable non-lexical channels" in result.report


# ── high-WER degrade path ────────────────────────────────────────────────────


def test_retrieve_character_ngram_fallback_is_marked_ungrounded():
    """hazard: a probable-but-not-observed match is presented as an observed
    one."""
    # Same-script corruption of a word the ASR half-heard: spoken "pampu",
    # transcribed "pammpu".
    segments = [_seg(0.0, "the pammpu failed at the reactor"),
                _seg(400.0, "the harvest silo opened")]
    index = _index(segments, language="te", fitness=_poor())
    result = retrieve(index, ["pampu"])
    assert result.intervals.cardinality == 1
    evidence = result.evidence[0]
    assert evidence.grounded is False
    assert "character n-gram index" in evidence.rationale
    assert "hypothesis" in evidence.rationale
    assert evidence.confidence <= 0.5


def test_retrieve_ngram_fallback_only_runs_when_word_index_is_empty():
    """hazard: the lossy index shadows the exact one on sources where the exact
    one is correct."""
    segments = [_seg(0.0, "the pammpu failed at the reactor"),
                _seg(400.0, "the harvest silo opened")]
    index = _index(segments, language="te", fitness=_poor())
    exact = retrieve(index, ["pammpu"], k=5, nms_s=1.0)
    assert exact.intervals.cardinality == 1
    assert exact.evidence[0].grounded is True
    assert exact.evidence[0].confidence > 0.5
    # A poor verdict is what makes the fallback available at all.
    usable = _index(segments, language="te", fitness=_usable())
    assert not usable.ngram_postings
    assert retrieve(usable, ["pampu"]).is_empty


def test_retrieve_reports_channel_instead_of_fabricating_on_empty_lexical_hit():
    """hazard: lexical failure is papered over with a span instead of handed to
    a channel that does not depend on transcription accuracy."""
    index = _index([_seg(0.0, "the coolant pump failed")],
                   speech_regions=[(0.0, 2.0)], fitness=_usable())
    result = retrieve(index, ["asparagus"])
    assert result.is_empty
    assert result.evidence == ()
    # No span was invented to avoid the empty answer.
    assert result.intervals.cardinality == 0
    # And the report names the channel that would answer a differently-shaped
    # query, rather than saying nothing.
    assert "vad (1 spans)" in result.report


# ── cost ───────────────────────────────────────────────────────-─────────────


def _large_transcript(minutes: int = 75, words_per_minute: int = 160):
    """A deterministic long transcript with real repetition and a real
    vocabulary, so the benchmark measures the index and not the fixture.

    Every third utterance draws on the shared subject vocabularies — which is
    what gives "coolant" and "pump" posting lists long enough to be the worst
    case the design names — and the rest are drawn from a rotating filler pool
    with a per-utterance index, so the term count is a function of the source
    length rather than of five fixed sentences.
    """
    subjects = [
        "coolant pump reactor vessel grid load flow pressure valve",
        "harvest silo conveyor grain mill batch season yield",
        "turbine blade gearbox nacelle tower yaw drive brake",
        "pipeline compressor refinery storage meter station flare",
        "furnace kiln refractory smelter ingot cast heat",
    ]
    fillers = [
        "and then we also noted that the system was checked again today",
        "the inspector recorded the reading and filed the maintenance note",
        "a follow-up review confirmed the earlier measurement was accurate",
        "operations reported the schedule slipped by a working day last week",
        "the technician replaced the gasket and retested the joint seal",
        "records show the unit was commissioned eight years ago this month",
        "the shift lead signed the logbook and countersigned the waiver",
        "a second sensor agreed with the first within the stated tolerance",
    ]
    segments = []
    t = 0.0
    period = 0.25
    total_words = int(minutes * words_per_minute)
    words_done = 0
    line = 0
    while words_done < total_words:
        if line % 3 == 0:
            text = subjects[line % len(subjects)]
        else:
            text = fillers[line % len(fillers)]
        toks = text.split()
        segments.append(_seg(t, text))
        t += len(toks) * period + 0.6
        words_done += len(toks)
        line += 1
    return segments


def test_retrieve_completes_under_50ms_on_a_75_minute_index():
    """hazard: retrieval quietly becomes the expensive stage and the pipeline's
    order stops paying for itself.

    The budget is ~500x the ~0.1 ms the design measured, so this is a regression
    alarm rather than a tight bound — it is here to catch a change that turns a
    bisect into a scan, not to be tuned against. The query is deliberately the
    *worst* shape retrieval sees: the two most frequent terms in the source,
    whose posting lists are the longest and whose window sweep therefore
    produces the most candidates. A rare content-word pair is a hundred times
    cheaper and measures nothing.
    """
    segments = _large_transcript()
    index = _index(segments)
    assert index.n_words > 8000
    by_df = sorted(index.df_table().items(), key=lambda kv: kv[1])
    # The two most frequent terms: the longest posting lists, and therefore the
    # most candidate windows the sweep has to score.
    worst = [term for term, _ in by_df[-2:]]
    assert index.df_table()[worst[0]] > 300, (
        "the fixture's most frequent term should have a long posting list, or "
        "the benchmark is not measuring the worst case")

    start = time.perf_counter()
    result = retrieve(index, worst, k=3, nms_s=60.0)
    elapsed = (time.perf_counter() - start) * 1000.0
    assert elapsed < 50.0, f"retrieve took {elapsed:.1f} ms on {worst}"
    assert result.intervals.cardinality >= 1

    # A rare pair, for contrast: the alarm has to be reachable, not trivially
    # satisfied by an empty query path.
    start = time.perf_counter()
    retrieve(index, ["coolant", "pump"], k=3, nms_s=60.0)
    assert (time.perf_counter() - start) * 1000.0 < 50.0


def test_retrieve_index_build_is_cheap_on_a_75_minute_transcript():
    """hazard: the index build is the one cost a run pays up front, and a
    quadratic build would price retrieval out of a default run."""
    start = time.perf_counter()
    index = _index(_large_transcript())
    elapsed = (time.perf_counter() - start) * 1000.0
    assert elapsed < 2000.0, f"build took {elapsed:.0f} ms"
    assert index.n_terms > 100


def test_retrieval_index_size_is_modest_for_a_long_source(tmp_path):
    """hazard: the index is written beside the transcript on every run, so a
    source-scale index would fill the disk the way the renders once did."""
    index = _index(_large_transcript())
    path = index.save(tmp_path / "index.json")
    kib = path.stat().st_size / 1024.0
    assert kib < 4096.0, f"index is {kib:.0f} KiB"
    # And it loads back.
    assert RetrievalIndex.load(path).n_terms == index.n_terms


# ── transcript loading ──────────────────────────────────────────────────────


def test_load_transcript_reads_the_pipeline_record(tmp_path):
    """hazard: the loader decodes transcript.json differently from the pipeline
    that wrote it, so a query and its own index disagree."""
    record = {
        "language": "en",
        "asr": {"engine": "faster-whisper"},
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "coolant pump",
             "words": [{"text": "coolant", "start": 0.0, "end": 0.5,
                        "confidence": 0.9},
                       {"text": "pump", "start": 0.5, "end": 1.0,
                        "confidence": 0.8}]},
        ],
    }
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    from engine.retrieval import load_transcript

    segments, language = load_transcript(path)
    assert language == "en"
    assert [w.text for w in segments[0].words] == ["coolant", "pump"]
    assert segments[0].words[0].confidence == pytest.approx(0.9)


def test_source_fingerprint_differs_across_inputs(tmp_path):
    """hazard: two sources resolve to one fingerprint and an index is shared
    across videos that are not the same video."""
    one = tmp_path / "one.mp4"
    two = tmp_path / "two.mp4"
    one.write_bytes(b"media-one")
    two.write_bytes(b"media-two")
    transcript = tmp_path / "t.json"
    transcript.write_text("{}", encoding="utf-8")
    assert (source_fingerprint(one, transcript)
            != source_fingerprint(two, transcript))
    assert source_fingerprint(one, transcript) == source_fingerprint(one, transcript)
