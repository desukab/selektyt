"""Pacing analysis tests: per-candidate pacing report and aggregate stats."""
from models.core import Segment, Word, Candidate
from engine.edit_plan import pacing_report, aggregate_pacing_report, retention_intervals


def _w(start, end, text):
    return Word(text, start, end, 1.0)


def _make_candidate(start, end, text, words=None):
    return Candidate(start, end, text, "insight", text[:60], text[-60:], 0.6, 0.5, 0.7, 0.9, "test", 0.5, words, None)


def test_pacing_report_dense_speech():
    words = [_w(i * 0.3, i * 0.3 + 0.25, f"w{i}") for i in range(30)]
    c = _make_candidate(0, 10, " ".join(f"w{i}" for i in range(30)), words)
    report = pacing_report(c)
    assert report["words_per_second"] >= 2.5
    assert report["total_words"] == 30
    assert report["silence_fraction"] < 0.1
    assert report["natural_rhythm"] is True


def test_pacing_report_natural_rhythm():
    words = [_w(i * 0.4, i * 0.4 + 0.3, f"w{i}") for i in range(20)]
    c = _make_candidate(0, 10, " ".join(f"w{i}" for i in range(20)), words)
    report = pacing_report(c)
    assert 1.5 <= report["words_per_second"] <= 3.5
    assert report["natural_rhythm"] is True


def test_pacing_report_detects_filler():
    words = [
        _w(0.0, 0.3, "um"), _w(0.4, 0.7, "uh"),
        _w(0.8, 1.0, "hmm"), _w(1.2, 1.5, "the"),
        _w(1.6, 1.9, "system"), _w(2.0, 2.3, "changed"),
    ]
    c = _make_candidate(0, 5, "um uh hmm the system changed", words)
    report = pacing_report(c)
    assert report["filler_pct"] > 0


def test_pacing_report_long_pauses():
    words = [_w(0.0, 0.5, "hello"), _w(3.0, 3.5, "world")]
    c = _make_candidate(0, 5, "hello world", words)
    report = pacing_report(c)
    assert report["long_pauses"] >= 1
    assert report["silence_fraction"] > 0.3


def test_pacing_report_payoff_presence():
    text = "The system changed everything in the world because nobody expected it to happen this way at all."
    words = [_w(0.0, 0.3, w) for w in text.split()]
    c = _make_candidate(0, 10, text, words)
    report = pacing_report(c)
    assert report["payoff_presence"] is True


def test_pacing_report_no_payoff():
    c = _make_candidate(0, 10, "The system changed everything because nobody expected it")
    report = pacing_report(c)
    assert report["payoff_presence"] is False


def test_aggregate_empty():
    result = aggregate_pacing_report([], [], 0)
    assert result["total_candidates"] == 0


def test_aggregate_distribution():
    selected = [_make_candidate(0, 35, "a " * 50)]
    rejected = [
        _make_candidate(40, 50, "b " * 30),
        _make_candidate(55, 65, "c " * 40),
    ]
    result = aggregate_pacing_report(selected, rejected, 2)
    assert result["total_candidates"] == 3
    assert result["selected_count"] == 1
    assert result["rejected_count"] == 2
    assert result["context_guard_rejections"] == 2
    assert "duration_distribution" in result


def test_aggregate_hook_latency():
    words = [_w(0.0, 0.3, "why"), _w(0.4, 0.7, "the"), _w(0.8, 1.1, "system")]
    c = _make_candidate(0, 5, "why the system changed", words)
    result = aggregate_pacing_report([c], [], 0)
    assert result["avg_hook_latency"] is not None
    assert result["avg_hook_latency"] >= 0


def test_retention_intervals_basic():
    words = [_w(0.0, 0.3, "hello"), _w(2.0, 2.3, "world")]
    c = _make_candidate(0, 3, "hello world", words)
    intervals = retention_intervals(c)
    assert len(intervals) >= 1
    assert all(b > a for a, b in intervals)
