"""Editorial integration tests: audio-derived scoring + VAD-enhanced pacing."""
from models.core import Segment, Word, Candidate
from engine.editorial import score_candidate, _audio_quality_features
from engine.edit_plan import pacing_report, aggregate_pacing_report
from engine.vad import SpeechRegion, SilenceRegion
from engine.pause_classifier import ClassifiedPause, PauseType


def _segs(text, start, end, words):
    return Segment(start, end, text, words)


def _w(s, e, t="word"):
    return Word(t, s, e, 1.0)


class _FakeTruth:
    def __init__(self, speech, pauses, duration):
        self.vad_speech = speech
        self.vad_silence = []
        self.pauses = pauses
        self.duration = duration


def _truth(speech_ranges, pause_ranges, duration):
    speech = [SpeechRegion(a, b, 1.0) for a, b in speech_ranges]
    pauses = [ClassifiedPause(a, b, b - a, PauseType.DEAD_AIR) for a, b in pause_ranges]
    return _FakeTruth(speech, pauses, duration)


# ── _audio_quality_features ──

def test_audio_features_no_words():
    assert _audio_quality_features([], 10.0) is None
    assert _audio_quality_features([_w(0.0, 0.5)], 10.0) is None


def test_audio_features_low_silence():
    words = [_w(0.0, 0.5), _w(0.6, 1.0), _w(1.1, 1.5)]
    f = _audio_quality_features(words, 1.5)
    assert f is not None
    assert f["silence_ratio"] < 0.15
    assert f["long_gap_ratio"] == 0.0


def test_audio_features_high_silence():
    words = [_w(0.0, 0.5), _w(2.0, 2.5), _w(4.0, 4.5)]
    f = _audio_quality_features(words, 4.5)
    assert f["silence_ratio"] > 0.6
    assert f["long_gap_ratio"] == 1.0


# ── Audio-derived scoring ──

def test_score_penalizes_dead_air():
    good = _segs("The system changed overnight and the reason is clear for everyone to see.",
                 0, 8, [_w(0, 0.5), _w(0.6, 1.0), _w(1.1, 1.5), _w(1.6, 2.0),
                         _w(2.1, 2.5), _w(2.6, 3.0), _w(3.1, 3.5), _w(3.6, 4.0)])
    bad = _segs("The system changed overnight and the reason is clear for everyone to see.",
                0, 8, [_w(0, 0.5), _w(3.0, 3.5), _w(3.6, 4.0), _w(4.1, 4.5),
                       _w(4.6, 5.0), _w(5.1, 5.5), _w(5.6, 6.0), _w(6.1, 6.5)])
    c_good = score_candidate(good)
    c_bad = score_candidate(bad)
    assert c_good.editorial_score > c_bad.editorial_score


def test_score_no_audio_neutral():
    seg = _segs("The system changed overnight and the reason is clear.",
                0, 6, [])
    c = score_candidate(seg)
    assert "audio_pace" not in c.reason


def test_score_with_audio_reason_has_audio_pace():
    seg = _segs("The system changed overnight and the reason is clear.",
                0, 6, [_w(0, 0.5), _w(0.6, 1.0), _w(1.1, 1.5)])
    c = score_candidate(seg)
    assert "audio_pace" in c.reason


# ── VAD-enhanced pacing report ──

def test_pacing_report_without_vad():
    c = Candidate(0, 10, "word " * 20, "insight", "word", "word.",
                  editorial_score=0.5, virality_score=0.5, context_score=0.6,
                  confidence=0.9, reason="", visual_potential=0.5,
                  words=[_w(i * 0.1, i * 0.1 + 0.5) for i in range(5)])
    r = pacing_report(c)
    assert "words_per_second" in r
    assert "vad_speech_ratio" not in r


def test_pacing_report_with_vad():
    c = Candidate(0, 10, "word " * 20, "insight", "word", "word.",
                  editorial_score=0.5, virality_score=0.5, context_score=0.6,
                  confidence=0.9, reason="", visual_potential=0.5, words=[])
    truth = _truth([(0, 4), (5, 10)], [(4, 5)], 10.0)
    r = pacing_report(c, truth)
    assert r["vad_speech_ratio"] == 0.9
    assert r["vad_dead_air_ratio"] == 0.1
    assert r["vad_speech_regions"] == 2


def test_pacing_report_vad_no_dead_air():
    c = Candidate(0, 10, "word " * 20, "insight", "word", "word.",
                  editorial_score=0.5, virality_score=0.5, context_score=0.6,
                  confidence=0.9, reason="", visual_potential=0.5, words=[])
    truth = _truth([(0, 10)], [], 10.0)
    r = pacing_report(c, truth)
    assert r["vad_speech_ratio"] == 1.0
    assert r["vad_dead_air_ratio"] == 0.0


def test_aggregate_pacing_report_still_works():
    cs = [
        Candidate(i * 20, i * 20 + 10, "word " * 20, "insight", "w", "w.",
                  editorial_score=0.5, virality_score=0.5, context_score=0.6,
                  confidence=0.9, reason="", visual_potential=0.5,
                  words=[_w(0.0, 0.5)])
        for i in range(3)
    ]
    agg = aggregate_pacing_report(cs, [], 0)
    assert agg["total_candidates"] == 3
    assert "silence_pct" in agg