"""The VAD gate can empty a whole chunk and record it as success.

``providers/whisper.transcribe`` runs faster-whisper with ``vad_filter=True``,
and the gate is inside the decode path: when it judges a chunk to be non-speech
the decoder returns zero segments and raises nothing. That outcome was written
as ``status: "ok"`` with an empty ``segments`` list, which is exactly what a
genuinely silent chunk produces, so the two were indistinguishable on disk and
a suppressed chunk hardened permanently into the transcript.

This is how three of the eleven committed sources came to have no transcript at
all. Measured on the committed corpus, 2026-09-20:

    alice-1903            peak -120.0 dB   0 segments   -> silence, correct
    telugu-launch         peak   -1.8 dB   0 segments   -> suppressed, wrong
    roman-portrait-trailer peak    0.0 dB  0 segments   -> low-frequency bed

Decoding telugu-launch without the VAD recovers 16 segments / 105.9 s of the
173.7 s, with the same model, language and beam width.

The fix separates the two by measuring the audio the chunk was decoded from,
then decoding again without the gate when a loud chunk comes back empty. These
tests pin the separation — the measurement, the retry decision, and the fact
that the outcome is recorded rather than silent.
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from providers.whisper import (  # noqa: E402
    EMPTY_RETRY_PEAK_DB,
    _load_chunk_checkpoint,
    _peak_db,
    _vad_retry_needed,
    _write_chunk_checkpoint,
)


# --- the retry policy, including its boundary ------------------------------


def test_silence_does_not_trigger_a_retry():
    """alice-1903 measures at the silence floor. Re-decoding it costs minutes
    and cannot recover anything, so an empty result there is final."""
    assert not _vad_retry_needed(-120.0)


def test_audible_audio_does_trigger_a_retry():
    """telugu-launch peaks at −1.8 dB and the committed run lost all 105.9 s of
    its speech. The empty result over that level is not to be trusted."""
    assert _vad_retry_needed(-1.8)
    assert _vad_retry_needed(0.0)


def test_the_bar_is_the_boundary():
    """A dB either side of the bar decides it, so the threshold cannot drift
    without a test moving with it."""
    assert not _vad_retry_needed(EMPTY_RETRY_PEAK_DB)
    assert not _vad_retry_needed(EMPTY_RETRY_PEAK_DB - 0.1)
    assert _vad_retry_needed(EMPTY_RETRY_PEAK_DB + 0.1)


def _wav(path: Path, samples: list[int], rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(s.to_bytes(2, "little", signed=True) for s in samples))
    return path


def _tone(freq: float, seconds: float, rate: int = 16000, amplitude: float = 0.5) -> list[int]:
    import math

    n = int(seconds * rate)
    return [int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]


# --- the measurement separates silence from suppressed speech --------------


def test_digital_silence_reads_far_below_the_bar(tmp_path):
    """alice-1903 is exactly zero. An empty result for it is correct."""
    p = _wav(tmp_path / "silent.wav", [0] * 16000)
    assert _peak_db(p) < EMPTY_RETRY_PEAK_DB


def test_a_full_scale_tone_reads_above_the_bar(tmp_path):
    p = _wav(tmp_path / "tone.wav", _tone(440, 0.5, amplitude=1.0))
    assert _peak_db(p) > EMPTY_RETRY_PEAK_DB


def test_the_bar_is_between_the_two_measured_corpus_peaks():
    """-45 dB sits between alice-1903's -180 dB and telugu-launch's -8.6 dB.
    Both corpus values are re-read from the committed chunk audio below, so a
    change to the bar has to confront the numbers it moves."""
    assert -180.0 < EMPTY_RETRY_PEAK_DB < -8.6


def test_a_quiet_but_real_signal_still_reads_above_the_bar(tmp_path):
    """-30 dB is well inside the 'this is audible' side; a whisper-quiet but
    genuine recording must not be classed as silence."""
    p = _wav(tmp_path / "quiet.wav", _tone(440, 0.5, amplitude=10 ** (-30 / 20)))
    assert _peak_db(p) == pytest.approx(-30.0, abs=0.6)


def test_unreadable_audio_reads_as_silence_rather_than_blocking(tmp_path):
    """A file that cannot be parsed must not raise and must not manufacture a
    retry; it falls on the silence side and the empty result stands."""
    bad = tmp_path / "not-a-wav.wav"
    bad.write_bytes(b"not audio at all")
    assert _peak_db(bad) < EMPTY_RETRY_PEAK_DB


def test_a_missing_file_reads_as_silence_so_the_bar_must_be_measured_before_deletion(tmp_path):
    """The chunk WAV is deleted once its outcome is written, and the measurement
    has to happen first.

    This is not hypothetical: the first version of the retry measured the audio
    after ``wav.unlink()`` had already run, so every chunk hit this fallback and
    read as silent. The retry compiled, passed its unit tests and never fired
    once; an end-to-end run on telugu-launch reported 'peak -46.0 dB' for audio
    that measures −1.8 dB. A missing path falling on the silence side is the
    safe default for a real unreadable file and the silent killer of this
    feature, so the ordering is pinned here.
    """
    gone = tmp_path / "deleted.wav"
    assert not gone.exists()
    assert _peak_db(gone) < EMPTY_RETRY_PEAK_DB
    # The loud file that would have been measured: the two must not be equal,
    # or deletion and silence are indistinguishable from the caller's side.
    present = _wav(tmp_path / "tone.wav", _tone(440, 0.5, amplitude=1.0))
    assert _peak_db(present) > EMPTY_RETRY_PEAK_DB
    assert _peak_db(gone) != _peak_db(present)


# --- the committed corpus values the bar is built on -----------------------


CORPUS_AUDIO = [
    # (project, sample peak dB from the retained audio.wav, correct reading)
    # The committed chunk WAVs are deleted after decode, but the 16 kHz mono
    # extraction of the whole source is kept, and it is what the peak is
    # measured on. The committed run produced zero segments for all three.
    ("alice-1903", -120.0, "silence — an empty result is correct"),
    ("roman-portrait-trailer", 0.0, "loud, low-frequency bed"),
    ("telugu-launch", -1.8, "loud, speech-shaped — the gate lost real audio"),
]


@pytest.mark.parametrize("project, recorded_peak, reading", CORPUS_AUDIO)
def test_the_committed_audio_measures_where_the_bar_needs_it(project, recorded_peak, reading):
    """The three committed sources that produced no segments, re-measured.

    Reading the retained audio directly keeps the bar tied to measured audio
    rather than to a constant that could drift: moving it has to confront the
    peaks it would reclassify, and the note under each case says what the
    correct reading of an empty transcript for that source actually is.
    """
    audio = ROOT / "corpus" / f"{project}_run" / "project" / "transcription" / "audio.wav"
    if not audio.is_file():
        pytest.skip(f"retained audio not present: {audio}")
    measured = _peak_db(audio)
    assert measured == pytest.approx(recorded_peak, abs=1.5), (
        f"{project} measures {measured:.1f} dB but the bar was set from "
        f"{recorded_peak:.1f} dB ({reading})"
    )
    # The bar has to separate them: silence below, the two loud sources above.
    if recorded_peak <= -120:
        assert measured < EMPTY_RETRY_PEAK_DB
    else:
        assert measured > EMPTY_RETRY_PEAK_DB


# --- the outcome is recorded, not silent -----------------------------------


def test_a_recovered_chunk_records_its_note(tmp_path):
    """The whole point: a loud chunk that came back empty must not be written
    down as though it were silence. The note is what separates them on disk."""
    _write_chunk_checkpoint(
        tmp_path / "chunk_0000.json",
        index=0, start=0.0, end=300.0, decode_start=0.0,
        language="te", segments=[],
        status="ok",
        note="vad suppressed the whole chunk over -8.6 dB of audio; decoded "
             "again without the VAD and recovered 16 segments",
    )
    _seg, _lang, usable, note = _load_chunk_checkpoint(tmp_path / "chunk_0000.json")
    assert usable, "a recovered chunk is a completed decode and must stay cached"
    assert note and "vad suppressed" in note


def test_a_silently_empty_chunk_records_that_instead(tmp_path):
    """Silence gets a note too, so the distinction is visible both ways."""
    _write_chunk_checkpoint(
        tmp_path / "chunk_0000.json",
        index=0, start=0.0, end=300.0, decode_start=0.0,
        language="en", segments=[],
        status="ok", note="no segments; chunk is silent (peak -180.0 dB)",
    )
    _seg, _lang, usable, note = _load_chunk_checkpoint(tmp_path / "chunk_0000.json")
    assert usable
    assert note and "silent" in note


def test_a_normal_chunk_has_no_note(tmp_path):
    """Nothing is written for the ordinary case, so the provenance stays clean
    for the 8 of 11 sources that transcribed normally."""
    from models.core import Segment

    _write_chunk_checkpoint(
        tmp_path / "chunk_0000.json",
        index=0, start=0.0, end=300.0, decode_start=0.0,
        language="en", segments=[Segment(1.0, 2.0, "hello", [])],
        status="ok", note=None,
    )
    _seg, _lang, usable, note = _load_chunk_checkpoint(tmp_path / "chunk_0000.json")
    assert usable
    assert note is None


def test_a_failed_chunk_still_fails_to_load_and_still_records_why(tmp_path):
    """The failure path is unchanged: a failed decode is not a result, and it
    is re-decoded on the next run rather than standing in for an empty chunk."""
    _write_chunk_checkpoint(
        tmp_path / "chunk_0000.json",
        index=0, start=0.0, end=300.0, decode_start=0.0,
        language="unknown", segments=[],
        status="failed", error="RuntimeError: oops",
    )
    _seg, _lang, usable, note = _load_chunk_checkpoint(tmp_path / "chunk_0000.json")
    assert not usable
    assert note is None
