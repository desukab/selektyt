"""Per-module tests for the corpus editorial layer.

`tests/test_corpus.py` pins the data contract; this file pins the *editorial
decisions* of each analysis module independently, with fixtures designed to make
one judgement at a time and assert the reasoning, not just the shape.
"""
from __future__ import annotations

from engine.corpus import build_corpus
from engine.lexical import containment, content_tokens, signature_terms
from engine.redundancy import group_redundancy
from engine.story_units import Sentence
from engine.threads import build_threads, mark_broll
from models.core import Segment, Word


def timed_words(text: str, start: float, wps: float = 2.4) -> list[Word]:
    toks = text.split()
    step = 1.0 / wps
    return [Word(t, start + i * step, start + (i + 1) * step)
            for i, t in enumerate(toks)]


def seg(start: float, text: str, wps: float = 2.4, words: bool = True) -> Segment:
    end = start + (len(text.split()) / wps if text else 0.4)
    return Segment(
        start=start, end=end, text=text,
        words=timed_words(text, start, wps) if words and text else [],
    )


def unit_inputs(segments: list[Segment]):
    """Build a corpus and hand back the parallel arrays each module consumes."""
    c = build_corpus(segments, duration=max(s.end for s in segments) + 1.0)
    from engine.corpus import _build_units
    units, sents, starts, ends, seg_lists, _ = _build_units(segments, has_words=True)
    return c, [u.text for u in units], sents, starts, ends, seg_lists


# ── topics: the editorial judgements ───────────────────────────────────────


def test_topics_recurrence_survives_an_interruption():
    """The signature corpus-level fact: a subject that goes away and returns."""
    segs = [
        seg(0.0, "The satellite clock failed. The telemetry shows the offset."),
        seg(12.0, "The studio microphone arrived. The shock mount is well built."),
        seg(24.0, "The microphone sounds excellent. Nothing else to report."),
        seg(36.0, "Back to the satellite clock. The same failure appeared again."),
    ]
    c = build_corpus(segs, duration=50.0)
    spine = [t for t in c.topics if "clock" in t.label or "satellite" in t.label]
    assert spine, [t.label for t in c.topics]
    assert spine[0].runs >= 2
    assert spine[0].recurring
    assert "interruption" in spine[0].reason


def test_topics_single_unit_is_a_passing_mention_not_a_subject():
    segs = [
        seg(0.0, "The harbour was rebuilt. The contractors finished early."),
        seg(12.0, "The lighthouse needs paint. Its lamp is original."),
    ]
    c = build_corpus(segs, duration=30.0)
    for t in c.topics:
        assert t.confidence <= 0.35
        assert "passing mention" in t.reason


def test_topics_label_is_verbatim_signature_terms():
    """No label is ever a composed phrase the speaker did not say."""
    segs = [
        seg(0.0, "The satellite clock failed. The telemetry shows the offset."),
        seg(12.0, "The satellite clock failed again on the second generation."),
    ]
    c = build_corpus(segs, duration=25.0)
    t = c.topics[0]
    assert t.label
    for term in t.terms:
        # every signature term appears verbatim in some member passage
        assert any(term in u.text for u in c.units if u.index in t.unit_indices)


def test_topics_neighbouring_subjects_are_not_merged_on_function_words():
    """Two passages sharing only 'the'/'and' are not one subject."""
    segs = [
        seg(0.0, "The committee met and discussed the budget and the schedule."),
        seg(12.0, "The gardener planted the roses and the hedge and the lawn."),
    ]
    c = build_corpus(segs, duration=30.0)
    # No topic should claim both units: the subjects are committee and gardener.
    for t in c.topics:
        assert len(t.unit_indices) == 1


# ── threads: setup to payoff ───────────────────────────────────────────────


def test_threads_question_is_answered_not_linked_to_another_question():
    """The link module is tested on units, not segments, so the fixture is built
    from explicit unit text rather than a transcript."""
    texts = [
        "Why did the satellite clock fail? The launch was nominal.",
        "The satellite clock never synchronised. The ground station saw the drift.",
        "How much did the delay cost? Who is responsible here?",
    ]
    sents = [[Sentence(t, float(i) * 10, float(i) * 10 + 8)] for i, t in enumerate(texts)]
    starts = [float(i) * 10 for i in range(3)]
    ends = [s + 8 for s in starts]
    res = build_threads(texts, sents, starts, ends, duration=35.0,
                        topic_of_unit=[-1] * 3)
    qa = [l for l in res.links if l.kind == "question_answer"]
    assert qa, "a question followed by a same-subject answer must link"
    # The third unit is itself a question, so it cannot be an answer to the first
    assert not any(l.payoff_unit == 2 for l in qa)


def test_threads_causal_requires_a_result_connective():
    """Causation is asserted by the speaker, not inferred from sequence."""

    def units_of(*texts):
        sents = [[Sentence(t, float(i) * 10, float(i) * 10 + 8)]
                 for i, t in enumerate(texts)]
        starts = [float(i) * 10 for i in range(len(texts))]
        return list(texts), sents, starts, [s + 8 for s in starts]

    t1, s1, a1, e1 = units_of(
        "The satellite clock broke.",
        "The satellite clock was lost entirely.",
    )
    t2, s2, a2, e2 = units_of(
        "The satellite clock broke.",
        "So the satellite clock was lost entirely. The agency reported it.",
    )
    r1 = build_threads(t1, s1, a1, e1, duration=25.0)
    r2 = build_threads(t2, s2, a2, e2, duration=25.0)
    assert not any(l.kind == "causal" for l in r1.links), \
        "temporal order alone must not license a causal claim"
    assert any(l.kind == "causal" for l in r2.links), \
        "an explicit result connective on the same subject must link causally"


def test_threads_links_are_forward_in_time_and_contain_their_evidence():
    texts = [
        "Why did the satellite clock fail? The launch was nominal.",
        "The satellite clock never synchronised. The station saw the drift.",
    ]
    sents = [[Sentence(t, float(i) * 10, float(i) * 10 + 8)] for i, t in enumerate(texts)]
    starts = [float(i) * 10 for i in range(2)]
    res = build_threads(texts, sents, starts, [s + 8 for s in starts], duration=25.0)
    for l in res.links:
        assert l.payoff_start >= l.setup_start
        assert len(l.evidence) == 2
        assert all(q for q in l.evidence)


def test_threads_arc_describes_the_chain_shape():
    texts = [
        "Why did the satellite clock fail? The launch was nominal.",
        "The satellite clock never synchronised. The station saw the drift.",
        "So the satellite clock was lost. The agency grounded the fleet.",
    ]
    sents = [[Sentence(t, float(i) * 10, float(i) * 10 + 8)] for i, t in enumerate(texts)]
    starts = [float(i) * 10 for i in range(3)]
    res = build_threads(texts, sents, starts, [s + 8 for s in starts], duration=35.0)
    assert any(t.links for t in res.threads)
    assert "→" in res.threads[0].arc


# ── beats: intensity is honest, polarity is not guessed ────────────────────


def test_beats_revelation_outranks_plain_problem():
    segs = [seg(0.0, "The clock failed. The clock never synchronised.")]
    c = build_corpus(segs, duration=15.0)
    assert c.beats[0].kind in ("tension", "revelation")


def test_beats_neutral_polarity_says_so_in_the_reason():
    segs = [seg(0.0, "The report arrived. The meeting was scheduled.")]
    c = build_corpus(segs, duration=15.0)
    b = c.beats[0]
    assert b.polarity == "neutral"
    assert "not guessed" in b.reason


def test_beats_corpus_peak_is_marked_once():
    """The single strongest beat in the corpus is flagged exactly once.

    Built on explicit units, since a transcript with short same-subject passages
    gets merged by the story-unit layer and cannot place beats per passage.
    """
    texts = [
        "The harbour was rebuilt and the contractors finished the job.",
        "That is the secret nobody knew. The vendor had shipped the design for years.",
        "The lighthouse was painted and the lamp was replaced.",
    ]
    sents = [[Sentence(t, float(i) * 10, float(i) * 10 + 8)] for i, t in enumerate(texts)]
    starts = [float(i) * 10 for i in range(3)]
    from engine.threads import build_beats
    beats = build_beats(texts, sents, starts, [s + 8 for s in starts])
    assert sum(1 for b in beats if b.is_corpus_peak) == 1
    assert beats[1].is_corpus_peak
    assert beats[1].kind == "revelation"


# ── redundancy: which passage to keep, and why ─────────────────────────────


def test_redundancy_representative_is_complete_over_truncated():
    """A full-stop version beats a truncated one even when slightly shorter."""
    texts = [
        "The clock failed on launch, and the agency wrote a report.",
        "The clock failed on launch, and the agency wrote",
    ]
    groups = group_redundancy(
        texts, [0.0, 5.0], [4.0, 8.0], [0.9, 0.9], min_similarity=0.5,
    ).groups
    assert groups
    g = groups[0]
    assert g.representative == 0
    assert "closes on a full stop" in g.reason or "strongest" in g.reason


def test_redundancy_elaboration_is_labelled_not_cut():
    """A later passage with materially more substance is a revision."""
    texts = [
        "The clock failed on launch.",
        (
            "The clock failed on launch, because the oscillator drifted and the "
            "ground station never corrected the offset before the burn."
        ),
    ]
    groups = group_redundancy(
        texts, [0.0, 5.0], [3.0, 20.0], [0.9, 0.9], min_similarity=0.4,
    ).groups
    assert groups
    assert groups[0].relation == "elaboration"
    assert "revision" in groups[0].reason


def test_redundancy_never_groups_a_single_passage():
    texts = ["The clock failed.", "The harbour was rebuilt.", "The lamp is original."]
    res = group_redundancy(texts, [0.0, 4.0, 8.0], [3.0, 7.0, 11.0], [0.9, 0.9, 0.9])
    assert res.groups == []


# ── B-roll: only where the transcript licenses it ──────────────────────────


def test_broll_demo_and_deictic_are_placed_on_the_cue_sentence():
    segs = [
        seg(0.0, "Let me show you the telemetry. As you can see the offset grows."),
        seg(12.0, "The report was filed. Nothing further was discussed."),
    ]
    _, _texts, sents, _starts, _ends, _ = unit_inputs(segs)
    markers = mark_broll(sents, [], rhythm=None)
    kinds = {m.kind for m in markers}
    assert "demonstration" in kinds
    assert "deictic" in kinds
    for m in markers:
        assert m.start < m.end
        assert m.cue in segs[0].text


def test_broll_no_cues_means_no_markers_and_no_invention():
    segs = [seg(0.0, "The clock failed. The agency wrote a report. The end.")]
    _, _texts, sents, _starts, _ends, _ = unit_inputs(segs)
    assert mark_broll(sents, [], rhythm=None) == []


# ── rhythm: cadence from word timing, honestly ────────────────────────────


def test_rhythm_rush_flag_tracks_a_speaker_speeding_up():
    """A unit spoken much faster than the corpus median is flagged as a rush."""
    fast = seg(20.0, "The tropical storm arrived without any warning whatsoever today.", wps=5.0)
    slow = seg(0.0, "The harbour. Was rebuilt. Very slowly. Over decades.", wps=1.2)
    c = build_corpus([slow, fast], duration=40.0)
    assert len(c.units) >= 2, "the fixture must yield separate units"
    rushing = [u for u in c.rhythm.per_unit if u.rush > 0.3]
    assert rushing, "a passage well above the median rate must be flagged"
    assert any(u.unit_index > 0 for u in rushing)


def test_rhythm_false_start_is_detected_from_a_repeated_stem():
    segs = [seg(0.0, "I think the clock, I think the clock failed on launch.")]
    c = build_corpus(segs, duration=15.0)
    assert c.rhythm.false_start_count >= 1
    assert any(u.false_start_count >= 1 for u in c.rhythm.per_unit)


def test_rhythm_incomplete_thought_is_detected():
    segs = [Segment(0.0, 4.0, "The clock failed and the", [])]
    c = build_corpus(segs, duration=10.0)
    assert c.rhythm.incomplete_count >= 1


def test_rhythm_filler_count_uses_the_cue_vocabulary():
    segs = [seg(0.0, "So, um, the clock, you know, it failed on the launch.")]
    c = build_corpus(segs, duration=12.0)
    assert c.rhythm.filler_count >= 2


# ── lexical helpers ────────────────────────────────────────────────────────


def test_content_tokens_drop_function_words_and_digits():
    toks = content_tokens("The 1990 system was the best system of all 7 systems.")
    assert "system" in toks
    assert "the" not in toks
    assert not any(t.isdigit() for t in toks)


def test_signature_terms_are_verbatim_and_bounded():
    texts = ["The satellite clock failed.",
             "The satellite clock failed again on the second generation."]
    terms = signature_terms([content_tokens(t) for t in texts],
                            {"satellite": 1.0, "clock": 1.0, "failed": 1.0,
                             "second": 0.5, "generation": 0.5}, limit=3)
    assert len(terms) <= 3
    for t in terms:
        assert any(t in text for text in texts)


def test_containment_is_relative_to_the_smaller_bag():
    a = content_tokens("The clock failed.")
    b = content_tokens("The clock failed on launch.")
    c = content_tokens("The harbour was rebuilt.")
    assert containment(a, b) == 1.0
    assert containment(c, b) == 0.0


# ── unicode: every supported script produces real structure ──────────────


def test_unicode_threads_and_beats_in_every_supported_script():
    cases = {
        "te": ("ఉపగ్రహం ఎందుకు విఫలమైంది?", "సమస్య గడియతో ఉంది. అది ఎప్పుడూ సరిపడలేదు."),
        "hi": ("सैटेलाइट क्यों विफल हुआ?", "समस्या यांत्रिक थी. यह कभी सही नहीं हुआ।"),
        "ta": ("செயற்கைக்கோள் ஏன் தோல்வியடைந்தது?", "சிக்கல் இயந்திரத்தில் இருந்தது. அது ஒருபோதும் சரியாகவில்லை."),
        "bn": ("স্যাটেলাইট কেন ব্যর্থ হলো?", "সমস্যাটা যান্ত্রিক ছিল। এটা কখনোই ঠিক হয়নি।"),
    }
    for lang, (q, a) in cases.items():
        segs = [seg(0.0, q), seg(6.0, a)]
        c = build_corpus(segs, duration=15.0, language=lang)
        assert len(c.units) >= 1
        assert c.beats, f"{lang} must produce beats"
        assert all(0.0 <= b.intensity <= 1.0 for b in c.beats)
        assert c.build.fingerprint
        # the question must be recognised as tension by the cue vocabulary
        assert any(b.kind == "tension" for b in c.beats), \
            f"{lang} interrogative must register as a tension beat"


def test_mixed_script_transcript_detects_both_scripts():
    segs = [seg(0.0, "The satellite failed. समस्या यांत्रिक थी और कोई रहस्य नहीं था।")]
    c = build_corpus(segs, duration=15.0)
    assert "hindi" in c.detected_languages
    assert "english" in c.detected_languages
