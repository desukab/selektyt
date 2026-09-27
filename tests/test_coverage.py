"""Tests for the transcript coverage check (engine.coverage).

The check exists because one corpus source shipped as an OK deliverable with a
transcript covering 4.5% of a 339.6 s file that is 73% speech-energy audio, and
nothing in the pipeline noticed. These tests pin four properties:

1. it fires on the measured collapse;
2. it never reports a pass it could not compute — below the speech floor the
   verdict becomes ``not_computable`` and says so;
3. it does not fail a source where the VAD is the broken instrument rather than
   the transcript;
4. it takes its "audio truth" from a backend that actually listens, and refuses
   the one backend that would make the gate circular.

The media is synthesised rather than read from the corpus, because the point is
the arithmetic on the two fractions and a synthetic file makes the boundary
conditions directly controllable. It has to be a *voiced* excitation, not a
tone: the coverage gate now takes its audio truth from the same backend chain
the pipeline prefers, and a neural speech model is entitled to score a pure tone
as non-speech — which would leave the fixture with no speech fraction at all and
every collapse assertion vacuous. The shape that is heard by every backend in
the chain, and the level dial that separates a level-blind backend from a model
that listens, are measured and documented in ``tests/speech_fixtures.py``.
"""
from __future__ import annotations

import json
import shutil

import pytest

from engine.coverage import (
    COLLAPSE_RATIO,
    MIN_VAD_FRACTION,
    check,
    measure,
)
from engine.vad import FFmpegVADBackend, SileroVADBackend
from tests.speech_fixtures import (
    INAUDIBLE_AMP,
    LOUD_AMP,
    QUIET_AMP,
    write_speech_wav,
)

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
# The quiet-source case only resolves to `ok` on a host where the chain can
# select a backend that listens. Silero and ffmpeg are both level-aware enough
# to hear a -65 dBFS voiced source (measured 60.8% and 0.0%... see the fixture
# module for the table); energy is not, so on a host with neither, the verdict
# on that source is an honest `not_computable` and the stronger assertion would
# pin the machine instead of the contract.
_LISTENING_BACKEND_AVAILABLE = (
    SileroVADBackend().is_available() or FFmpegVADBackend().is_available()
)


def _segments(starts: list[float], length: float = 2.0) -> list[dict]:
    return [{"start": s, "end": s + length, "text": "x"} for s in starts]


@pytest.fixture
def speech_source(tmp_path):
    """A 100 s source ~50% of which every backend in the chain scores as speech:
    the stand-in for ``telugu-tutorial``'s 73%."""
    audio = tmp_path / "audio.wav"
    write_speech_wav(audio, 100.0, amp=LOUD_AMP)
    return audio


def test_the_measured_collapse_fires_loudly(speech_source):
    """4.5 s of transcript against ~50 s of scored speech — the corpus's shipped
    failure, reproduced at fixture scale."""
    report = check(speech_source, _segments([0.0], length=4.5))
    assert report.verdict == "coverage_collapse"
    assert report.coverage_ratio is not None
    assert report.coverage_ratio < COLLAPSE_RATIO
    # The detail must carry the numbers a user would need to judge it.
    assert "4.5" in report.detail or "speech" in report.detail


def test_a_transcript_covering_the_speech_passes(speech_source):
    """The lowest measured good ratio in the corpus was 1.302. This fixture
    scores ~50% speech, so a transcript covering 90% of it lands near 1.8; what
    matters is that it sits far above the collapse threshold."""
    report = check(speech_source, _segments([i * 10.0 for i in range(9)], length=9.0))
    assert report.verdict == "ok"
    assert report.coverage_ratio is not None
    assert report.coverage_ratio > COLLAPSE_RATIO


def test_a_quiet_source_with_a_good_transcript_is_never_failed(tmp_path):
    """The corpus's quiet sources — ``hadhazy-protest`` at mean -43.3 dBFS and
    ``pentagon-esper-briefing`` — had good transcripts and level-blind VAD reads
    (0.0% and 0.4%). Under the old hardcoded energy gate both came back
    ``not_computable``; under the chain both come back ``ok``.

    The property that holds on every host, regardless of which backends are
    installed, is the one worth pinning: a quiet source is never *failed*. A
    symmetric check would have shipped a wrong verdict on exactly the sources
    whose transcripts were fine."""
    audio = tmp_path / "quiet.wav"
    write_speech_wav(audio, 100.0, amp=QUIET_AMP)
    report = check(audio, _segments([i * 1.05 for i in range(90)], length=1.0))
    assert report.verdict != "coverage_collapse"
    assert report.asr_speech_frac > 0.8


@pytest.mark.skipif(not _LISTENING_BACKEND_AVAILABLE,
                    reason="needs silero or ffmpeg so the chain can select a "
                           "backend that hears a quiet source")
def test_a_quiet_source_is_ok_where_the_chain_can_hear_it(tmp_path):
    """The measured effect of routing the gate through the chain: this source
    scores 0.0% speech on energy and 60.8% on silero, so the verdict moves from
    ``not_computable`` to ``ok``. That is the two corpus verdict changes, at
    fixture scale, and it is the reason the gate no longer hardcodes energy."""
    audio = tmp_path / "quiet.wav"
    write_speech_wav(audio, 100.0, amp=QUIET_AMP)
    report = check(audio, _segments([i * 1.05 for i in range(90)], length=1.0))
    assert report.verdict == "ok"


def test_a_source_no_backend_can_hear_is_recorded_not_passed(tmp_path):
    """Below ``MIN_VAD_FRACTION`` the ratio is meaningless, and reporting ``ok``
    would be exactly the green-without-measuring failure the check exists to
    remove. This level is blind to every backend the chain can select, so the
    verdict is host-independent here — unlike the quiet case above."""
    audio = tmp_path / "inaudible.wav"
    write_speech_wav(audio, 100.0, amp=INAUDIBLE_AMP)
    report = check(audio, _segments([10.0], length=5.0))
    assert report.verdict == "not_computable"
    assert report.vad_speech_frac < MIN_VAD_FRACTION
    assert "trustworthy denominator" in report.detail
    # The detail names the backend that ran, so a reader can tell a broken
    # instrument from a silent source.
    assert report.detail.startswith("the ")
    assert " VAD found" in report.detail


def test_the_measure_names_which_backend_supplied_the_audio_truth(speech_source):
    """The gate's verdict rests on one backend's notion of speech, so the number
    has to carry that backend's name or a reader cannot tell what was measured."""
    m = measure(speech_source, _segments([1.0]))
    assert m["vad_backend"] in ("silero", "ffmpeg", "energy"), m["vad_backend"]
    assert m["vad_speech_frac"] > MIN_VAD_FRACTION


def test_the_gate_uses_the_chain_not_a_hardcoded_backend(speech_source, monkeypatch):
    """Regression: the gate used to construct ``EnergyVAD`` directly, so the
    pipeline's only audio-vs-transcript guard was computed by the chain's
    last-choice, level-blind backend. It consults the chain now.

    Making every backend report unavailable still yields energy — it is the
    documented last resort, available wherever numpy is — but the *order* in
    which the chain probes them is the property, and energy is probed last,
    never first."""
    from engine import coverage

    probed = []
    for _name, cls in coverage._COVERAGE_BACKENDS:
        monkeypatch.setattr(
            cls, "is_available",
            lambda self, name=_name: probed.append(name) or False,
        )
    m = measure(speech_source, _segments([1.0]))
    assert probed == [name for name, _cls in coverage._COVERAGE_BACKENDS], (
        "the chain did not probe its backends in the pipeline's preference order"
    )
    assert probed[0] != "energy", "energy is the chain's last choice, not its first"
    assert m["vad_backend"] == "energy"


def test_whisper_is_refused_as_audio_truth(speech_source):
    """Whisper infers speech from transcript word timings, so the gate would
    compare the transcript against itself and a collapse would sit at ratio 1.0
    by construction. Refusing it is load-bearing, not a nicety."""
    with pytest.raises(RuntimeError, match="compare the transcript against itself"):
        check(speech_source, _segments([1.0]), vad_backend="whisper")


def test_an_unavailable_forced_backend_is_refused_not_silently_substituted(
        speech_source, monkeypatch):
    """A forced backend that cannot run has to say so. Silently falling back
    would make ``--vad-backend`` agree to a measurement taken with something
    else, which is worse than an error the user can act on."""
    from engine.vad import SileroVADBackend
    monkeypatch.setattr(SileroVADBackend, "is_available", lambda self: False)
    with pytest.raises(RuntimeError, match="unavailable"):
        check(speech_source, _segments([1.0]), vad_backend="silero")


def test_missing_audio_is_not_measured_rather_than_passed(tmp_path):
    """An absent extraction must not read as coverage fine."""
    report = check(tmp_path / "does-not-exist.wav", _segments([1.0]))
    assert report.verdict == "not_measured"
    assert "could not be measured" in report.detail


def test_an_empty_transcript_is_the_empty_guards_case_not_this_one(speech_source):
    """``pipeline.py`` raises its own no-speech error before this runs; a
    zero-coverage transcript here would otherwise duplicate that message with
    worse numbers."""
    report = check(speech_source, [])
    assert report.verdict == "ok"
    assert report.asr_speech_s == 0.0


def test_measure_reports_the_union_not_the_sum(speech_source):
    """Adjacent overlapping segments must not be counted twice."""
    segs = [{"start": 0.0, "end": 10.0}, {"start": 5.0, "end": 15.0}]
    m = measure(speech_source, segs)
    assert m["asr_speech_s"] == pytest.approx(15.0, abs=0.05)
    assert m["asr_speech_frac"] == pytest.approx(0.15, abs=0.001)


def test_a_wrong_sample_rate_is_rejected_not_silently_miscounted(tmp_path):
    """The fractions are computed on a frame grid derived from 16 kHz; a file
    on a different grid would yield confidently wrong fractions."""
    audio = tmp_path / "44k.wav"
    write_speech_wav(audio, 10.0, amp=LOUD_AMP, rate=44100)
    with pytest.raises(ValueError, match="wrong grid"):
        measure(audio, _segments([1.0]))


@pytest.mark.skipif(not _HAVE_FFMPEG,
                    reason="ffmpeg is needed to build the test media")
def test_the_pipeline_aborts_on_a_collapse_before_selecting_or_rendering(
        tmp_path, monkeypatch):
    """The guard has to sit before selection and render — the expensive parts
    — and a collapsed run must not reach them. Exercised through ``run`` so
    the wiring is what is tested, not just the module.

    The patched transcriber writes a real voiced extraction, which is what the
    unpatched one does. It is 340 s long because that is the corpus case at its
    own scale — the shipped failure was 4.5 s of transcript against a 339.6 s
    file — and the fixture is only ~50% speech rather than 73%, so a shorter
    file would put a 16 s transcript above the collapse threshold and the
    guard would correctly not fire.
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
            # A 340 s extraction ~50% of which the chain scores as speech,
            # against a transcript covering only its first 16 s (4.7% of the
            # file, ratio ~0.09) — the corpus failure at its own scale.
            write_speech_wav(checkpoint / "audio.wav", 340.0, amp=LOUD_AMP)
        return _transcript(), "en", {"engine": "test", "model": "test",
                                     "word_timestamps": "test",
                                     "fallback_reason": ""}

    monkeypatch.setattr(p, "transcribe_auto", transcribe)

    with pytest.raises(RuntimeError, match="coverage collapse"):
        p.run("https://example.com/v", tmp_path, model="small", limit=2)

    # The expensive stages must not have run: no selection, no renders.
    assert not (tmp_path / "project" / "selections.json").exists()
    assert not (tmp_path / "project" / "facebook").exists()


@pytest.mark.skipif(not _HAVE_FFMPEG,
                    reason="ffmpeg is needed to build the test media")
def test_the_check_is_skippable_and_records_the_measurement(tmp_path, monkeypatch):
    """The false-positive class is real — an energy VAD cannot tell speech from
    music — so the check has to be overridable, and the override has to leave
    the measurement in the report rather than deleting the evidence. Same 340 s
    fixture as the abort test, so the two agree on what a collapse is."""
    import selekyt.pipeline as p
    from tests.test_pipeline_e2e import _synthetic_video, _transcript

    media = _synthetic_video(tmp_path / "source" / "clip.mp4")
    monkeypatch.setattr(p, "download",
                        lambda source, dest, cookies=None: (media, {"title": "t"}))

    def transcribe(media, **kw):
        checkpoint = kw.get("checkpoint_dir")
        if checkpoint is not None:
            (checkpoint / "audio.wav").parent.mkdir(parents=True, exist_ok=True)
            write_speech_wav(checkpoint / "audio.wav", 340.0, amp=LOUD_AMP)
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
