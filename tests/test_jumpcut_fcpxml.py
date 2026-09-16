"""Jump-cut engine + FCPXML export tests."""
from pathlib import Path
from models.core import Word
from engine.vad import SpeechRegion, SilenceRegion
from engine.pause_classifier import ClassifiedPause, PauseType
from engine.audio_fusion import AudioTruth, FusedWord, fuse_audio_truth
from engine.jumpcut import (
    JumpCutEngine, CutList, CutSegment, SegmentType, quick_cut,
)
from engine.fcpxml import export_fcpxml, cutlist_to_simple_xml


def _make_truth(
    words, speech_regions, silences, pauses, duration,
    remove_hallucinations=True,
):
    return fuse_audio_truth(words, speech_regions, silences, pauses, duration)


def _word(s, e, t="hello"):
    return Word(t, s, e, 0.95)


def _speech(s, e):
    return SpeechRegion(s, e, 1.0)


def _silence(s, e):
    return SilenceRegion(s, e, round(e - s, 3))


def _pause(s, e, ptype=PauseType.DEAD_AIR):
    return ClassifiedPause(s, e, round(e - s, 3), ptype)


# ── Jump-Cut Engine ──

def test_removes_long_silence():
    words = [_word(0.0, 0.5), _word(3.0, 3.5)]
    speech = [_speech(0.0, 0.8), _speech(2.8, 4.0)]
    silences = [_silence(0.8, 2.8)]
    pauses = [_pause(0.8, 2.8, PauseType.DEAD_AIR)]
    truth = _make_truth(words, speech, silences, pauses, 5.0)
    cl = JumpCutEngine(silence_threshold=0.5).generate_cutlist(truth)
    assert cl.total_keep_duration > 0
    assert cl.total_remove_duration > 0
    assert cl.compression_ratio < 1.0


def test_keeps_intentional_pause():
    words = [_word(0.0, 0.5), _word(1.5, 2.0)]
    speech = [_speech(0.0, 0.8), _speech(1.2, 2.2)]
    silences = [_silence(0.8, 1.2), _silence(2.2, 3.0)]
    pauses = [_pause(0.8, 1.2, PauseType.INTENTIONAL)]
    truth = _make_truth(words, speech, silences, pauses, 3.0)
    cl = JumpCutEngine(silence_threshold=0.5, keep_intentional=True).generate_cutlist(truth)
    # The intentional pause (0.8-1.2) is kept — no REMOVE covering that range
    removed_ranges = [(s.start, s.end) for s in cl.segments if s.segment_type != SegmentType.KEEP]
    for rstart, rend in removed_ranges:
        assert not (rstart < 1.2 and rend > 0.8), "intentional pause was removed"


def test_removes_hesitation_above_threshold():
    words = [_word(0.0, 0.5), _word(1.5, 2.0)]
    speech = [_speech(0.0, 0.8), _speech(1.5, 2.2)]
    silences = [_silence(0.8, 1.5)]
    pauses = [_pause(0.8, 1.5, PauseType.HESITATION)]
    truth = _make_truth(words, speech, silences, pauses, 3.0)
    cl = JumpCutEngine(hesitation_threshold=0.5).generate_cutlist(truth)
    assert cl.total_remove_duration > 0


def test_removes_hallucinations():
    words = [_word(0.0, 0.5), _word(5.0, 5.5)]
    speech = [_speech(0.0, 1.0)]
    silences = [_silence(1.0, 6.0)]
    pauses = [_pause(1.0, 6.0, PauseType.DEAD_AIR)]
    truth = _make_truth(words, speech, silences, pauses, 7.0, remove_hallucinations=True)
    cl = JumpCutEngine(remove_hallucinations=True).generate_cutlist(truth)
    assert cl.words_removed >= 1


def test_keeps_breathing():
    words = [_word(0.0, 0.5), _word(1.2, 2.0)]
    speech = [_speech(0.0, 0.8), _speech(0.9, 2.2)]
    silences = [_silence(0.8, 0.9), _silence(2.2, 3.0)]
    pauses = [_pause(0.8, 0.9, PauseType.BREATHING)]
    truth = _make_truth(words, speech, silences, pauses, 3.0)
    cl = JumpCutEngine(keep_breathing=True, silence_threshold=0.5).generate_cutlist(truth)
    removed_ranges = [(s.start, s.end) for s in cl.segments if s.segment_type != SegmentType.KEEP]
    for rstart, rend in removed_ranges:
        assert not (rstart < 0.9 and rend > 0.8), "breathing pause was removed"


def test_quick_cut_convenience():
    words = [_word(0.0, 0.5), _word(3.0, 3.5)]
    speech = [_speech(0.0, 0.8), _speech(2.8, 4.0)]
    silences = [_silence(0.8, 2.8)]
    pauses = [_pause(0.8, 2.8, PauseType.DEAD_AIR)]
    truth = _make_truth(words, speech, silences, pauses, 5.0)
    cl = quick_cut(truth, silence_threshold=0.5)
    assert cl.total_remove_duration > 0


def test_cutlist_stats():
    words = [_word(0.0, 0.5), _word(3.0, 3.5)]
    speech = [_speech(0.0, 0.8), _speech(2.8, 4.0)]
    silences = [_silence(0.8, 2.8)]
    pauses = [_pause(0.8, 2.8, PauseType.DEAD_AIR)]
    truth = _make_truth(words, speech, silences, pauses, 5.0)
    cl = JumpCutEngine(silence_threshold=0.5).generate_cutlist(truth)
    assert cl.original_duration == 5.0
    assert cl.segments_removed > 0
    total = cl.total_keep_duration + cl.total_remove_duration
    assert abs(total - 5.0) < 0.2


def test_empty_truth():
    truth = _make_truth([], [], [], [], 0.0)
    cl = JumpCutEngine().generate_cutlist(truth)
    assert len(cl.segments) == 0


# ── FCPXML Export ──

def _sample_cutlist():
    return CutList(
        segments=[
            CutSegment(0.0, 1.5, SegmentType.KEEP, "speech"),
            CutSegment(1.5, 2.0, SegmentType.REMOVE_SILENCE, "dead_air"),
            CutSegment(2.0, 4.5, SegmentType.KEEP, "speech"),
        ],
        original_duration=5.0,
    )


def test_fcpxml_export(tmp_path):
    cl = _sample_cutlist()
    out = export_fcpxml(cl, tmp_path / "out.fcpxml", source_path="test.mp4")
    assert out.exists()
    content = out.read_text()
    assert "fcpxml" in content
    assert "keep_0.00" in content
    assert "remove_silence" in content


def test_simple_xml_export(tmp_path):
    cl = _sample_cutlist()
    out = cutlist_to_simple_xml(cl, tmp_path / "out.xml")
    assert out.exists()
    content = out.read_text()
    assert "compression_ratio" in content
    assert "remove_silence" in content


def test_fcpxml_creates_parent_dirs(tmp_path):
    cl = _sample_cutlist()
    out = export_fcpxml(cl, tmp_path / "sub" / "dir" / "out.fcpxml")
    assert out.exists()
