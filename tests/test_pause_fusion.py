"""Pause classification + audio/truth fusion tests."""
from models.core import Word
from engine.vad import SpeechRegion, SilenceRegion
from engine.pause_classifier import (
    PauseClassifier, ClassifiedPause, PauseType, classify_all_pauses,
)
from engine.audio_fusion import (
    AudioTruth, FusedWord, fuse_audio_truth,
)


def _speech(start, end):
    return SpeechRegion(start, end, 1.0)


def _silence(start, end):
    return SilenceRegion(start, end, round(end - start, 3))


def _word(start, end, text="hello"):
    return Word(text, start, end, 0.95)


# ── Pause Classifier (text-based boundaries) ──

def test_dead_air():
    """2.5s silence between regions → DEAD_AIR regardless of boundary."""
    silences = [_silence(2.0, 4.5)]
    speech = [_speech(0.0, 1.8), _speech(5.0, 6.0)]
    words = [_word(1.0, 1.7, "world.")]
    result = classify_all_pauses(silences, speech, 10.0, words)
    assert result[0].pause_type == PauseType.DEAD_AIR
    assert result[0].duration >= 1.5


def test_breathing_after_sentence_terminal():
    """0.3s gap immediately after word ending with '.' → BREATHING."""
    silences = [_silence(1.8, 2.1)]
    speech = [_speech(0.0, 1.8), _speech(2.5, 5.0)]
    words = [_word(1.0, 1.75, "world.")]
    result = classify_all_pauses(silences, speech, 8.0, words)
    assert result[0].pause_type == PauseType.BREATHING


def test_breathing_before_sentence_start():
    """0.3s gap between punct-terminated word and next sentence → BREATHING."""
    silences = [_silence(1.8, 2.1)]
    speech = [_speech(0.0, 1.8), _speech(2.5, 5.0)]
    words = [_word(1.0, 1.75, "end."), _word(2.55, 4.0, "So")]
    result = classify_all_pauses(silences, speech, 8.0, words)
    assert result[0].pause_type == PauseType.BREATHING


def test_hesitation_mid_sentence():
    """0.4s gap, NO punctuation on either side → HESITATION."""
    silences = [_silence(1.8, 2.2)]
    speech = [_speech(0.0, 1.8), _speech(2.5, 5.0)]
    words = [_word(1.0, 1.7, "hello"), _word(2.6, 4.0, "there")]
    result = classify_all_pauses(silences, speech, 10.0, words)
    assert result[0].pause_type == PauseType.HESITATION


def test_intentional_pause():
    """0.8s gap, no punctuation → INTENTIONAL."""
    silences = [_silence(1.8, 2.6)]
    speech = [_speech(0.0, 1.8), _speech(3.0, 5.0)]
    words = [_word(1.0, 1.7, "hello"), _word(3.1, 4.0, "there")]
    result = classify_all_pauses(silences, speech, 10.0, words)
    assert result[0].pause_type == PauseType.INTENTIONAL


def test_uncertain_short_silence():
    """50ms gap, no boundary → UNCERTAIN (too short for any named type)."""
    silences = [_silence(1.8, 1.85)]
    speech = [_speech(0.0, 1.8), _speech(2.5, 5.0)]
    words = [_word(1.0, 1.7, "hello")]
    result = classify_all_pauses(silences, speech, 10.0, words)
    assert result[0].pause_type == PauseType.UNCERTAIN


def test_empty_silences():
    assert classify_all_pauses([], [_speech(0.0, 5.0)], 6.0) == []


def test_confidence_in_range():
    silences = [_silence(1.8, 2.1)]
    speech = [_speech(0.0, 1.8), _speech(3.0, 6.0)]
    words = [_word(1.0, 1.7, "word.")]
    result = classify_all_pauses(silences, speech, 10.0, words)
    assert 0 < result[0].confidence <= 1.0


def test_fallback_without_words():
    """Without words, uses region-duration heuristic (regions > 1.5s → boundary)."""
    silences = [_silence(1.8, 2.1)]
    speech = [_speech(0.0, 1.8), _speech(3.0, 6.0)]
    result = classify_all_pauses(silences, speech, 10.0)
    assert len(result) == 1
    assert result[0].pause_type in (PauseType.BREATHING, PauseType.UNCERTAIN)


def test_multiple_silences():
    silences = [_silence(2.0, 4.5), _silence(5.0, 5.3)]
    speech = [_speech(0.0, 2.0), _speech(6.0, 8.0)]
    words = [_word(1.0, 1.9, "end.")]
    result = classify_all_pauses(silences, speech, 10.0, words)
    assert len(result) == 2
    types = [r.pause_type for r in result]
    assert PauseType.DEAD_AIR in types


# ── Audio Fusion ──

def test_fusion_preserves_whisper_words():
    words = [_word(0.0, 0.5), _word(1.0, 1.5), _word(3.0, 3.5)]
    speech = [_speech(0.0, 2.0), _speech(2.8, 4.0)]
    silences = [_silence(2.0, 2.8)]
    pauses = [ClassifiedPause(2.0, 2.8, 0.8, PauseType.BREATHING)]
    truth = fuse_audio_truth(words, speech, silences, pauses, 5.0)
    assert truth.whisper_words == words
    assert len(truth.fused_words) == 3


def test_fusion_marks_vad_speech():
    words = [_word(0.0, 0.5), _word(5.0, 5.5)]
    speech = [_speech(0.0, 1.0)]
    silences = [_silence(1.0, 6.0)]
    pauses = [ClassifiedPause(1.0, 5.0, 4.0, PauseType.DEAD_AIR)]
    truth = fuse_audio_truth(words, speech, silences, pauses, 6.0)
    assert truth.fused_words[0].vad_speech is True
    assert truth.fused_words[1].vad_speech is False


def test_fusion_detects_hallucination():
    words = [_word(0.0, 0.5), _word(2.0, 2.5), _word(5.0, 5.5)]
    speech = [_speech(0.0, 0.8)]
    silences = [_silence(0.8, 6.0)]
    pauses = [ClassifiedPause(0.8, 6.0, 5.2, PauseType.DEAD_AIR)]
    truth = fuse_audio_truth(words, speech, silences, pauses, 6.0)
    assert truth.hallucinated_count == 2
    assert truth.speech_ratio < 0.3


def test_fusion_speech_silence_ratios():
    words = [_word(0.0, 0.5)]
    speech = [_speech(0.0, 3.0)]
    silences = [_silence(3.0, 6.0)]
    pauses = [ClassifiedPause(3.0, 6.0, 3.0, PauseType.DEAD_AIR)]
    truth = fuse_audio_truth(words, speech, silences, pauses, 6.0)
    assert abs(truth.speech_ratio - 0.5) < 0.05
    assert abs(truth.silence_ratio - 0.5) < 0.05


def test_fusion_dead_air_ratio():
    words = [_word(0.0, 0.5)]
    speech = [_speech(0.0, 1.0)]
    silences = [_silence(1.0, 6.0)]
    pauses = [ClassifiedPause(1.0, 6.0, 5.0, PauseType.DEAD_AIR)]
    truth = fuse_audio_truth(words, speech, silences, pauses, 6.0)
    assert truth.dead_air_ratio > 0.8


def test_fusion_fused_regions():
    words = [_word(0.0, 0.5, "a"), _word(1.0, 1.5, "b")]
    speech = [_speech(0.0, 2.0)]
    silences = [_silence(2.0, 3.0)]
    pauses = [ClassifiedPause(2.0, 3.0, 1.0, PauseType.DEAD_AIR)]
    truth = fuse_audio_truth(words, speech, silences, pauses, 4.0)
    assert len(truth.fused_regions) == 1
    assert truth.fused_regions[0].word_count == 2


def test_fusion_empty_words():
    speech = [_speech(0.0, 3.0)]
    silences = [_silence(3.0, 6.0)]
    pauses = [ClassifiedPause(3.0, 6.0, 3.0, PauseType.DEAD_AIR)]
    truth = fuse_audio_truth([], speech, silences, pauses, 6.0)
    assert truth.hallucinated_count == 0
    assert len(truth.fused_words) == 0
    assert truth.speech_ratio > 0.4
