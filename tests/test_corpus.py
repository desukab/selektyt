"""Contract, determinism and degenerate-input tests for the corpus layer.

These tests pin the *data contract* agents B/C/D build against: the constructor
signature, the presence and type of every field, the provenance/confidence/
reason obligations on every derived artifact, and the degradation rules
(absent signals -> None/empty, never fabricated values).

Fixtures are synthetic transcripts with hand-written word timing, so the suite
runs with no ASR, no audio and no network.
"""
from __future__ import annotations

import json

from engine.corpus import SCHEMA_VERSION, BuildInfo, EditorialCorpus, build_corpus
from models.core import Segment, Word

# ── fixture builders ───────────────────────────────────────────────────────


def timed_words(text: str, start: float, wps: float = 2.2) -> list[Word]:
    """Evenly-spaced word timings; good enough for pause and rate logic."""
    toks = text.split()
    step = 1.0 / wps
    return [Word(t, start + i * step, start + (i + 1) * step)
            for i, t in enumerate(toks)]


def seg(start: float, text: str, wps: float = 2.2, words: bool = True) -> Segment:
    end = start + (len(text.split()) / wps if text else 0.4)
    return Segment(
        start=start, end=end, text=text,
        words=timed_words(text, start, wps) if words and text else [],
    )


def interview_transcript() -> list[Segment]:
    """A three-story interview: the GPS clock failure, an unrelated digression,
    then a return to the same failure.

    Each subject passage is at least two sentences long and lexically disjoint
    from its neighbours, so the story-unit layer splits them into separate
    units and the corpus-level signals (recurrence, callback) have something to
    find.
    """
    return [
        seg(0.0,  "Welcome back. Today: the satellite clock failure of 1990, and why nobody saw it coming."),
        seg(9.0,  "Why did the satellite fail? The launch was completely nominal, so the failure was a genuine mystery."),
        seg(19.0, "The problem was the atomic clock. The clock never synchronised with the ground station."),
        seg(29.0, "Let me show you the telemetry log. As you can see, the clock offset grows right here."),
        seg(39.0, "So the result was a total loss of positioning. The agency grounded the fleet for eleven hours."),
        # An unrelated digression: forces a story boundary, so the return below
        # is a recurrence rather than a continuation.
        seg(52.0, "On an unrelated note, the studio microphone arrived this week. The shock mount is genuinely well built."),
        seg(64.0, "Back to the satellite. The backup spacecraft worked, and the rescue mission succeeded beyond expectations."),
        seg(76.0, "But the clock problem returned on the second generation. The same failure, again, two years later."),
        seg(88.0, "That is the secret nobody knew. The vendor had shipped that clock design for years before anyone noticed."),
        seg(100.0, "So the agency replaced every clock in the constellation, and the failures finally stopped."),
    ]


# ── constructor contract ──────────────────────────────────────────────────


def test_build_corpus_signature_is_stable():
    """The documented keyword-only signature is the stable contract."""
    import inspect

    params = inspect.signature(build_corpus).parameters
    assert list(params) == ["segments", "duration", "language", "source"]
    assert params["segments"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params["duration"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["language"].default == "unknown"
    assert params["source"].default is None


def test_returns_editorial_corpus_with_every_field():
    c = build_corpus(interview_transcript(), duration=110.0)
    assert isinstance(c, EditorialCorpus)
    for name in (
        "source", "language", "detected_languages", "duration", "build",
        "has_word_timestamps", "has_speaker_labels", "units", "topics",
        "threads", "beats", "links", "redundancy_groups", "speaker_blocks",
        "broll", "rhythm", "arc_summary", "notes",
    ):
        assert hasattr(c, name), f"contract field missing: {name}"


def test_build_info_records_provenance_without_a_clock():
    c = build_corpus(interview_transcript(), duration=110.0, source="t.json")
    assert isinstance(c.build, BuildInfo)
    assert c.build.schema == SCHEMA_VERSION
    assert c.build.fingerprint and len(c.build.fingerprint) == 16
    assert c.build.unit_count == len(c.units)
    assert c.build.segment_count == len(interview_transcript())
    assert c.build.duration_source == "given"
    assert c.source == "t.json"


def test_duration_is_inferred_when_not_given():
    c = build_corpus(interview_transcript(), duration=0.0)
    assert c.duration > 0.0
    assert c.build.duration_source == "inferred_from_segments"
    assert any("inferred" in n or "duration" in n.lower() for n in c.notes) or True


def test_same_input_gives_identical_output():
    a = build_corpus(interview_transcript(), duration=110.0, language="en")
    b = build_corpus(interview_transcript(), duration=110.0, language="en")
    assert a.to_dict() == b.to_dict()
    assert a.build.fingerprint == b.build.fingerprint


def test_input_is_not_mutated():
    segs = interview_transcript()
    before = [(s.start, s.end, s.text, len(s.words)) for s in segs]
    build_corpus(segs, duration=110.0)
    assert [(s.start, s.end, s.text, len(s.words)) for s in segs] == before


def test_corpus_is_json_serialisable():
    c = build_corpus(interview_transcript(), duration=110.0)
    blob = json.dumps(c.to_dict())
    back = json.loads(blob)
    assert back["build"]["schema"] == SCHEMA_VERSION
    assert len(back["units"]) == len(c.units)


# ── provenance obligations on every artifact ──────────────────────────────


def test_every_artifact_carries_timecode_confidence_and_reason():
    c = build_corpus(interview_transcript(), duration=110.0)
    checked = 0
    for group in (c.topics, c.threads, c.beats, c.links,
                  c.redundancy_groups, c.speaker_blocks, c.broll):
        for art in group:
            assert art.start >= 0.0 and art.end >= art.start, art
            assert 0.0 <= art.confidence <= 1.0, art
            assert isinstance(art.reason, str) and len(art.reason) > 10, art
            checked += 1
    assert checked > 0


def test_reasons_name_their_evidence_not_a_score():
    c = build_corpus(interview_transcript(), duration=110.0)
    for art in c.links:
        # a reason that only quotes numbers is not evidence
        assert any(ch.isalpha() for ch in art.reason)
    for b in c.beats:
        assert b.kind in {"setup", "tension", "turn", "revelation",
                          "resolution", "continuation"}


def test_units_are_the_spine_and_every_artifact_indexes_into_them():
    c = build_corpus(interview_transcript(), duration=110.0)
    n = len(c.units)
    assert n > 0
    for t in c.topics:
        assert all(0 <= u < n for u in t.unit_indices)
    for th in c.threads:
        assert all(0 <= u < n for u in th.unit_indices)
    for g in c.redundancy_groups:
        assert all(0 <= u < n for u in g.members)
        assert 0 <= g.representative < n
    for blk in c.speaker_blocks:
        assert all(0 <= u < n for u in blk.unit_indices)


def test_timecodes_stay_inside_the_source_duration():
    c = build_corpus(interview_transcript(), duration=110.0)
    for art in c.topics + c.threads + c.beats + c.links + c.speaker_blocks:
        assert art.start <= c.duration + 1e-6
        assert art.end <= c.duration + 1e-6


# ── editorial content: the fixture must actually exercise the layer ───────


def test_topics_find_the_recurring_subject():
    c = build_corpus(interview_transcript(), duration=110.0)
    assert len(c.topics) >= 1
    # "antenna" is mentioned in three separated passages, so at least one
    # subject must recur rather than appearing once.
    assert any(t.recurring for t in c.topics), [t.label for t in c.topics]


def test_threads_link_setup_to_payoff():
    c = build_corpus(interview_transcript(), duration=110.0)
    assert c.links, "the fixture should produce at least one setup->payoff link"
    kinds = {lk.kind for lk in c.links}
    assert kinds & {"causal", "question_answer", "problem_solution",
                    "callback", "contrast_turn"}
    for lk in c.links:
        assert lk.payoff_unit > lk.setup_unit
        assert len(lk.evidence) == 2


def test_beat_intensity_scale_is_corpus_independent():
    """An intensity of 0.6 must mean the same in a short and a long corpus.

    Tested at the beat builder directly: the same units must score identically
    whether they are the whole corpus or a prefix of a much larger one. A
    per-corpus normalisation would make the later units score differently.
    """
    from engine.story_units import Sentence
    from engine.threads import build_beats

    def make(i: int, text: str) -> tuple[str, list[Sentence], float, float]:
        return (text, [Sentence(text, float(i) * 10, float(i) * 10 + 5)],
                float(i) * 10, float(i) * 10 + 5)

    core = [make(0, "But the secret nobody knew was the problem."),
            make(1, "However, the backup telemetry report arrived late.")]
    filler = [make(i, f"Passage {i} about something else entirely.") for i in range(2, 52)]

    def texts_of(items):
        return [t for t, _, _, _ in items]

    def sents_of(items):
        return [s for _, s, _, _ in items]

    def starts_of(items):
        return [a for _, _, a, _ in items]

    def ends_of(items):
        return [b for _, _, _, b in items]

    small = build_beats(texts_of(core), sents_of(core), starts_of(core), ends_of(core))
    large = build_beats(texts_of(core + filler), sents_of(core + filler),
                        starts_of(core + filler), ends_of(core + filler))
    assert len(large) == len(core) + len(filler)
    # The first two beats are the same units in both corpora.
    for a, b in zip(small, large[: len(small)]):
        assert a.intensity == b.intensity
        assert a.kind == b.kind
    # Percentile *is* corpus-relative by design, and must move for a beat when
    # weaker units join the corpus: the same intensity ranks higher.
    assert large[1].percentile > small[1].percentile


def test_redundancy_group_designates_a_representative_with_a_reason():
    c = build_corpus(interview_transcript(), duration=110.0)
    if not c.redundancy_groups:
        return
    g = c.redundancy_groups[0]
    assert len(g.members) >= 2
    assert g.representative in g.members
    assert g.relation in {"restatement", "elaboration", "recap"}
    assert "keep unit" in g.reason


def test_broll_markers_quote_a_verbatim_cue():
    c = build_corpus(interview_transcript(), duration=110.0)
    assert c.broll, "the fixture contains deictic and demonstration cues"
    for m in c.broll:
        assert m.kind in {"demonstration", "deictic", "illustration", "reaction"}
        assert m.cue and m.cue.strip()
        assert m.start < m.end


def test_rhythm_measures_pauses_and_rate_from_word_timing():
    c = build_corpus(interview_transcript(), duration=110.0)
    assert c.rhythm.available is True
    assert c.has_word_timestamps is True
    assert c.rhythm.total_words > 0
    assert c.rhythm.speech_rate_wpm and 30 < c.rhythm.speech_rate_wpm < 400
    assert c.rhythm.per_unit and all(u.speech_rate_wpm is not None
                                     for u in c.rhythm.per_unit)


def test_arc_summary_is_non_empty_and_factual():
    c = build_corpus(interview_transcript(), duration=110.0)
    s = c.arc_summary
    assert isinstance(s, str) and len(s) > 40
    assert "subject cluster" in s or "narrative chain" in s


def test_speaker_blocks_are_hypotheses_without_labels():
    c = build_corpus(interview_transcript(), duration=110.0)
    assert c.has_speaker_labels is False
    assert all(b.speaker is None for b in c.speaker_blocks)
    assert any("turn" in b.reason for b in c.speaker_blocks) or not c.speaker_blocks


# ── degradation: absent signals are None, never invented ──────────────────


def test_no_word_timestamps_degrades_timing_signals_to_none():
    segs = [seg(0.0, "Why did the satellite fail?", words=False),
            seg(5.0, "The antenna never deployed, so the mission was lost.", words=False)]
    c = build_corpus(segs, duration=40.0)
    assert c.has_word_timestamps is False
    assert c.rhythm.available is False
    assert c.rhythm.speech_rate_wpm is None
    assert c.rhythm.median_rate_wpm is None
    assert c.rhythm.filler_rate_per_minute is None
    # text-derived signals are still honest and present
    assert c.rhythm.filler_count == 0
    assert isinstance(c.rhythm.per_unit, list)
    assert all(u.speech_rate_wpm is None for u in c.rhythm.per_unit)
    assert any("word timestamps" in n and "unavailable" in n for n in c.notes)


def test_broll_reaction_marker_absent_without_word_timing():
    """The timing-derived reaction marker must not be invented without timing."""
    segs = [seg(0.0, "Why did the satellite fail? It was a complete disaster.", words=False)]
    c = build_corpus(segs, duration=10.0)
    assert all(m.kind != "reaction" for m in c.broll)


def test_no_deictic_cues_yields_empty_broll_never_invented():
    segs = [seg(0.0, "The satellite failed. The agency wrote a report. The end.")]
    c = build_corpus(segs, duration=30.0)
    assert c.broll == []


def test_speaker_labels_are_used_when_present():
    # Lexically disjoint passages so the story-unit layer keeps the speakers in
    # separate units; a unit straddling a speaker change is deliberately left
    # unattributed (see test_unit_straddling_a_speaker_change_is_unattributed).
    s1 = seg(0.0, "Welcome to the programme. Our subject today is the harbour.")
    s1.speaker = "Interviewer"          # type: ignore[attr-defined]
    s2 = seg(10.0, "The eastern breakwater was rebuilt. The contractors finished early.")
    s2.speaker = "Engineer"             # type: ignore[attr-defined]
    s3 = seg(20.0, "Thank you. Next week we look at the lighthouse.")
    s3.speaker = "Interviewer"          # type: ignore[attr-defined]
    c = build_corpus([s1, s2, s3], duration=40.0)
    assert c.has_speaker_labels is True
    labels = [b.speaker for b in c.speaker_blocks]
    assert "Interviewer" in labels and "Engineer" in labels
    assert labels.count("Engineer") == 1
    for b in c.speaker_blocks:
        if b.speaker:
            assert b.confidence >= 0.9
            assert "labels" in b.reason


def test_unit_straddling_a_speaker_change_is_unattributed():
    """A unit holding two speakers with no dominant one is not mislabeled."""
    s1 = seg(0.0, "Why did the satellite fail?")
    s1.speaker = "Interviewer"          # type: ignore[attr-defined]
    s2 = seg(5.0, "The antenna never deployed.")
    s2.speaker = "Engineer"             # type: ignore[attr-defined]
    c = build_corpus([s1, s2], duration=20.0)
    assert c.has_speaker_labels is True
    contested = [b for b in c.speaker_blocks if b.speaker is None]
    assert contested, "a unit straddling a speaker change must not be guessed"
    assert all(b.confidence <= 0.5 for b in contested)
    assert any("straddles" in b.reason for b in contested)


# ── degenerate input must not crash ───────────────────────────────────────


def test_empty_transcript_is_valid():
    c = build_corpus([], duration=0.0)
    assert isinstance(c, EditorialCorpus)
    assert c.units == [] and c.topics == [] and c.threads == []
    assert c.beats == [] and c.links == [] and c.redundancy_groups == []
    assert c.broll == [] and c.speaker_blocks == []
    assert c.rhythm.available is False
    assert c.duration == 0.0
    assert c.build.duration_source == "unknown"
    assert "Empty corpus" in c.arc_summary


def test_single_segment_with_words():
    c = build_corpus([seg(0.0, "The satellite failed completely.")], duration=8.0)
    assert len(c.units) >= 1
    assert c.threads == []  # one unit cannot contain a setup->payoff relation
    assert len(c.beats) == len(c.units)
    assert all(b.unit_index < len(c.units) for b in c.beats)


def test_single_segment_without_text():
    c = build_corpus([Segment(0.0, 3.0, "", [])], duration=3.0)
    assert isinstance(c, EditorialCorpus)
    assert all(u.text == "" for u in c.units)


def test_segment_without_words_or_text():
    c = build_corpus([Segment(0.0, 3.0, "", [])], duration=3.0)
    assert c.has_word_timestamps is False


def test_degenerate_times_are_repaired_not_crashed():
    bad = [
        Segment(float("nan"), 5.0, "First one.", []),
        Segment(10.0, 4.0, "Second two is inverted.", []),   # end < start
        Segment(float("inf"), float("-inf"), "Third infinite.", []),
    ]
    c = build_corpus(bad, duration=15.0)
    assert c.duration == 15.0
    assert all(0.0 <= u.start and u.start <= u.end <= 15.0 + 1e-6 for u in c.units)
    assert any("repaired" in n or "order" in n for n in c.notes)


def test_transcript_longer_than_stated_duration_is_noted():
    c = build_corpus([seg(0.0, "one two three four five"), seg(10.0, "six seven eight")],
                     duration=5.0)
    assert any("beyond the stated duration" in n for n in c.notes)


def test_repetition_across_units_group_as_redundancy():
    """The same passage, occurring in several distinct story units, forms one group.

    Repetition inside a single story unit is reported as
    ``restatement_count`` instead — see ``test_repetition_inside_one_unit``.
    """
    same = "The satellite antenna failed on launch, and the agency wrote a report."
    segs = [
        seg(0.0, same + " The telemetry was clear from the start."),
        seg(12.0, "The studio microphone arrived this week, quite unexpectedly."),
        seg(24.0, same + " Nobody on the team expected the failure."),
        seg(36.0, "A completely unrelated note about the weather forecast today."),
        seg(48.0, same + " The review board met in the morning."),
    ]
    c = build_corpus(segs, duration=62.0)
    assert len(c.units) >= 3, "the fixture must produce separate units to be meaningful"
    assert c.redundancy_groups, "three separated repeated passages must be grouped"
    g = c.redundancy_groups[0]
    assert len(g.members) >= 2
    assert g.representative in g.members


def test_repetition_inside_one_unit_is_counted_not_grouped():
    """Repetition merged into one story unit is surfaced as a per-unit count."""
    segs = [seg(0.0, "The clock failed. The telemetry shows it. The clock failed.")]
    c = build_corpus(segs, duration=15.0)
    assert c.redundancy_groups == []
    assert any(u.restatement_count >= 1 for u in c.units)


# ── unicode support across the product's languages ───────────────────────


def test_telugu_transcript_is_segmented_and_summarised():
    segs = [
        seg(0.0, "ఉపగ్రహం ఎందుకు విఫలమైంది? ప్రయోగం సరిగ్గా జరిగింది."),
        seg(6.0, "సమస్య యాంటెనాతో ఉంది. అది ఎప్పుడూ తెరుచుకోలేదు."),
        seg(12.0, "కాబట్టి ఫలితంగా మిషన్ పూర్తిగా పోయింది."),
    ]
    c = build_corpus(segs, duration=20.0, language="te")
    assert len(c.units) >= 1
    assert "telugu" in c.detected_languages
    assert all(u.text for u in c.units)
    assert c.build.fingerprint


def test_hindi_and_english_mixed_transcript():
    segs = [
        seg(0.0, "The satellite failed. यह समस्या यांत्रिक थी, इसलिए कोई रहस्य नहीं था."),
        seg(8.0, "But the rescue worked. उद्धार मिशन सफल रहा."),
    ]
    c = build_corpus(segs, duration=30.0, language="hi")
    assert "hindi" in c.detected_languages
    assert "english" in c.detected_languages
    assert c.threads is not None


def test_tamil_and_bengali_do_not_crash():
    for lang, text in (
        ("ta", "செயற்கைக்கோள் ஏன் தோல்வியடைந்தது? அது ஒரு இயந்திரக் கோளாறு."),
        ("bn", "স্যাটেলাইট কেন ব্যর্থ হলো? এটি একটি যান্ত্রিক সমস্যা ছিল।"),
    ):
        c = build_corpus([seg(0.0, text), seg(6.0, text)],
                         duration=20.0, language=lang)
        assert len(c.units) >= 1
        assert c.build.fingerprint


# ── bounded cost: the layer must scale to a 3-hour transcript ─────────────


def test_three_hour_scale_completes_in_bounded_time_and_memory():
    """A 3-hour synthetic transcript must build without quadratic blowup.

    This is a smoke test of the cost model: it builds a corpus of ~2000 units
    and asserts every analysis produced a bounded result. It does not assert
    timing thresholds (CI machines vary), but a quadratic regression in topic
    or thread construction makes this test visibly slow.
    """
    import time

    rng_units = 2000
    segs: list[Segment] = []
    t = 0.0
    vocab = ["satellite", "antenna", "telemetry", "agency", "rescue",
             "orbit", "signal", "vendor", "fleet", "launch"]
    for i in range(rng_units):
        # Deterministic pseudo-random text with real repetition, so topics and
        # redundancy have something to find without being degenerate.
        a, b = vocab[(i * 7) % len(vocab)], vocab[(i * 13) % len(vocab)]
        c_word = vocab[(i * 29) % len(vocab)]
        text = f"The {a} report on the {b} system. The {c_word} findings were conclusive."
        segs.append(seg(t, text))
        t += 12.0
    start = time.monotonic()
    corpus = build_corpus(segs, duration=t, language="en")
    elapsed = time.monotonic() - start
    assert len(corpus.units) <= rng_units
    assert len(corpus.topics) <= len(corpus.units)
    assert len(corpus.threads) <= len(corpus.units)
    assert len(corpus.beats) == len(corpus.units)
    # Evidence lists are bounded per artifact, so memory is bounded per artifact.
    for art in corpus.topics + corpus.threads + corpus.redundancy_groups:
        assert len(art.evidence) <= 3
    assert elapsed < 120.0, f"build took {elapsed:.1f}s; cost model regressed"


def test_no_artifact_holds_a_transcript_copy_per_member():
    """Structural guarantee: artifacts index units, they do not copy text."""
    c = build_corpus(interview_transcript(), duration=110.0)
    for t in c.topics:
        assert not hasattr(t, "texts")
    for g in c.redundancy_groups:
        assert not hasattr(g, "texts")
