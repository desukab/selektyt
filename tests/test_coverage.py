"""Tests for the transcript coverage check (engine.coverage).

The check exists because one corpus source shipped as an OK deliverable with a
transcript covering 4.5% of a 339.6 s file that is 73% speech-energy audio, and
nothing in the pipeline noticed. These tests pin three properties that the
measurement across the corpus forced on the design:

1. it fires on the measured collapse;
2. it does *not* fire on sources where the energy VAD is the broken instrument
   rather than the transcript — the two measured cases are a quiet Hungarian
   source (0% VAD against 87% ASR) and a long quiet briefing (0.4% against 82%);
3. it never reports a pass it could not compute.

The media here is synthesised rather than read from the corpus, because the
point is the arithmetic on the two fractions, and a synthetic file makes the
boundary conditions directly controllable instead of hoped for.
"""
from __future__ import annotations

import json
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from engine.coverage import (COLLAPSE_RATIO, MIN_VAD_FRACTION, check, measure)


def _write_wav(path: Path, seconds: float, tone_hz: float = 220.0,
               amplitude: float = 0.3, rate: int = 16000) -> None:
    """A real WAV with a real amplitude, so the energy VAD scores it rather
    than seeing digital silence. ``amplitude`` is the dial that stands in for
    a quiet recording: 0.3 is a loud source, 0.004 is the mean-RMS regime of
    the Hungarian corpus source (−43 dBFS)."""
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        import math
        val = int(amplitude * 32767 * math.sin(2 * math.pi * tone_hz * i / rate))
        frames += struct.pack("<h", val)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(bytes(frames))


def _segments(starts: list[float], length: float = 2.0) -> list[dict]:
    return [{"start": s, "end": s + length, "text": "x"} for s in starts]


@pytest.fixture
def loud_source(tmp_path):
    """A 100 s loud source that is mostly speech-energy: the stand-in for
    ``telugu-tutorial``'s 73%."""
    audio = tmp_path / "audio.wav"
    _write_wav(audio, 100.0, amplitude=0.3)
    return audio


def test_the_measured_collapse_fires_loudly(loud_source):
    """4.5 s of transcript against 72 s of speech energy — the corpus's
    shipped failure, reproduced at fixture scale."""
    report = check(loud_source, _segments([300.0], length=4.5))
    assert report.verdict == "coverage_collapse"
    assert report.coverage_ratio is not None
    assert report.coverage_ratio < COLLAPSE_RATIO
    # The detail must carry the numbers a user would need to judge it.
    assert "4.5" in report.detail or "speech" in report.detail


def test_a_transcript_covering_the_speech_passes(loud_source):
    """The lowest measured good ratio in the corpus was 1.302. A synthetic tone
    scores as 100% speech energy, so the fixture ratio tops out at 1.0; what
    matters is that it sits far above the collapse threshold, not that it
    exceeds 1."""
    report = check(loud_source, _segments([i * 10.0 for i in range(9)], length=9.0))
    assert report.verdict == "ok"
    assert report.coverage_ratio is not None
    assert report.coverage_ratio > COLLAPSE_RATIO


def test_a_quiet_source_with_a_good_transcript_is_not_failed(tmp_path):
    """The Hungarian corpus source: mean −43.3 dBFS, energy VAD 0%, ASR 87%.
    The VAD is blind, not the transcript. A symmetric check would fail this;
    the check must decline to compute instead, and must say so."""
    audio = tmp_path / "quiet.wav"
    _write_wav(audio, 100.0, amplitude=0.004)
    report = check(audio, _segments([i * 1.05 for i in range(90)], length=1.0))
    assert report.verdict == "not_computable"
    assert report.asr_speech_frac > 0.8
    assert "trustworthy denominator" in report.detail


def test_a_source_with_a_blind_vad_floor_is_recorded_not_passed(tmp_path):
    """Below ``MIN_VAD_FRACTION`` the ratio is meaningless, and reporting
    ``ok`` would be exactly the green-without-measuring failure the check
    exists to remove."""
    audio = tmp_path / "barely.wav"
    _write_wav(audio, 100.0, amplitude=0.004)
    report = check(audio, _segments([10.0], length=5.0))
    assert report.verdict == "not_computable"
    assert report.vad_speech_frac < MIN_VAD_FRACTION


def test_missing_audio_is_not_measured_rather_than_passed(tmp_path):
    """An absent extraction must not read as coverage fine."""
    report = check(tmp_path / "does-not-exist.wav", _segments([1.0]))
    assert report.verdict == "not_measured"
    assert "could not be measured" in report.detail


def test_an_empty_transcript_is_the_empty_guards_case_not_this_one(loud_source):
    """``pipeline.py`` raises its own no-speech error before this runs; a
    zero-coverage transcript here would otherwise duplicate that message with
    worse numbers."""
    report = check(loud_source, [])
    assert report.verdict == "ok"
    assert report.asr_speech_s == 0.0


def test_measure_reports_the_union_not_the_sum(loud_source):
    """Adjacent overlapping segments must not be counted twice."""
    segs = [{"start": 0.0, "end": 10.0}, {"start": 5.0, "end": 15.0}]
    m = measure(loud_source, segs)
    assert m["asr_speech_s"] == pytest.approx(15.0, abs=0.05)
    assert m["asr_speech_frac"] == pytest.approx(0.15, abs=0.001)


def test_a_wrong_sample_rate_is_rejected_not_silently_miscounted(tmp_path):
    """The fractions are computed on a frame grid derived from 16 kHz; a file
    on a different grid would yield confidently wrong fractions."""
    audio = tmp_path / "44k.wav"
    _write_wav(audio, 10.0, amplitude=0.3, rate=44100)
    with pytest.raises(ValueError, match="wrong grid"):
        measure(audio, _segments([1.0]))


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"]).returncode != 0,
                    reason="ffmpeg is needed to build the test media")
def test_the_pipeline_aborts_on_a_collapse_before_selecting_or_rendering(tmp_path, monkeypatch):
    """The guard has to sit before selection and render — the expensive parts
    — and a collapsed run must not reach them. Exercised through ``run`` so
    the wiring is what is tested, not just the module.

    The patched transcriber writes a real loud extraction, which is what the
    unpatched one does; the corpus case it reproduces is a 4.5 s transcript
    against a mostly-speech source.
    """
    import selekyt.pipeline as p
    from tests.test_pipeline_e2e import _synthetic_video, _transcript

    media = _synthetic_video(tmp_path / "source" / "clip.mp4")
    monkeypatch.setattr(p, "download",
                        lambda source, dest, cookies=None: (media, {"title": "t"}))

    def transcribe(media, **kw):
        checkpoint = kw.get("checkpoint_dir")
        if checkpoint is not None:
            (checkpoint / "audio.wav").parent.mkdir(parents=True, exist_ok=True)
            # A loud 100 s extraction, against a transcript covering only its
            # first 16 s — 16% coverage of a source the VAD scores as mostly
            # speech, which is the corpus failure at fixture scale.
            _write_wav(checkpoint / "audio.wav", 100.0, amplitude=0.3)
        return _transcript(), "en", {"engine": "test", "model": "test",
                                     "word_timestamps": "test",
                                     "fallback_reason": ""}

    monkeypatch.setattr(p, "transcribe_auto", transcribe)

    with pytest.raises(RuntimeError, match="coverage collapse"):
        p.run("https://example.com/v", tmp_path, model="small", limit=2)

    # The expensive stages must not have run: no selection, no renders.
    assert not (tmp_path / "project" / "selections.json").exists()
    assert not (tmp_path / "project" / "facebook").exists()


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"]).returncode != 0,
                    reason="ffmpeg is needed to build the test media")
def test_the_check_is_skippable_and_records_the_measurement(tmp_path, monkeypatch):
    """The false-positive class is real — an energy VAD cannot tell speech from
    music — so the check has to be overridable, and the override has to leave
    the measurement in the report rather than deleting the evidence."""
    import selekyt.pipeline as p
    from tests.test_pipeline_e2e import _synthetic_video, _transcript

    media = _synthetic_video(tmp_path / "source" / "clip.mp4")
    monkeypatch.setattr(p, "download",
                        lambda source, dest, cookies=None: (media, {"title": "t"}))

    def transcribe(media, **kw):
        checkpoint = kw.get("checkpoint_dir")
        if checkpoint is not None:
            (checkpoint / "audio.wav").parent.mkdir(parents=True, exist_ok=True)
            _write_wav(checkpoint / "audio.wav", 100.0, amplitude=0.3)
        return _transcript(), "en", {"engine": "test", "model": "test",
                                     "word_timestamps": "test",
                                     "fallback_reason": ""}

    monkeypatch.setattr(p, "transcribe_auto", transcribe)
    project = p.run("https://example.com/v", tmp_path, model="small", limit=2,
                    skip_coverage_check=True)
    report = json.loads((project / "report.json").read_text(encoding="utf-8"))
    assert report["coverage"]["verdict"] == "coverage_collapse"
    assert report["coverage"]["coverage_ratio"] < COLLAPSE_RATIO


def test_a_run_whose_extraction_is_absent_degrades_and_continues(tmp_path, monkeypatch):
    """A resumed or partially-patched run may not have the extraction on disk.
    That must read as ``not_measured`` in the report — not as a pass, and not
    as a hard failure that breaks a run which would otherwise have worked."""
    import selekyt.pipeline as p
    from tests.test_pipeline_e2e import _synthetic_video, _transcript

    media = _synthetic_video(tmp_path / "source" / "clip.mp4")
    monkeypatch.setattr(p, "download",
                        lambda source, dest, cookies=None: (media, {"title": "t"}))
    monkeypatch.setattr(p, "transcribe_auto",
                        lambda media, **kw: (_transcript(), "en",
                                             {"engine": "test", "model": "test",
                                              "word_timestamps": "test",
                                              "fallback_reason": ""}))
    project = p.run("https://example.com/v", tmp_path, model="small", limit=2)
    report = json.loads((project / "report.json").read_text(encoding="utf-8"))
    assert report["coverage"]["verdict"] == "not_measured"
