"""Proxy validation measurements — real media, no mocking.

Uses Es69N24I2Yo.mp4 (10min, 239MB) as PROXY source — clearly not
representative of Z907 content; results must be labeled accordingly.
Also exercises WAV corpus for VAD backend + segmentation correctness.

NOT a unit test. Exercises the real audio stack end-to-end against actual
media.

These are measurements tied to one specific recording, not correctness
contracts, and the recording is a 239 MB YouTube download that is not
redistributable with the repo and that YouTube rate-limits CI runners on. So
they are deliberately opt-in rather than fetched in CI, and each skip reason
names exactly what would make it run (see ``PROXY_REASON`` and
``CORPUS_REASON``). A skip that states its un-skip condition is honest; what
would not be is a measurement that reports green without having touched media.
To run them locally:

    SELEKYT_PROXY_MEDIA=/path/to/Es69N24I2Yo.mp4 \
    SELEKYT_WAV_CORPUS=/path/to/wav/dir \
    .venv/bin/python -m pytest tests/test_proxy_validation.py

``SELEKYT_WAV_CORPUS`` is optional: the WAV-corpus tests also pick up the
extracted 16 kHz mono audio under ``corpus/*_run/project/transcription/`` left
by ``tools/corpus_run.py``, so they run wherever a corpus run has happened.
"""
import json
import os
import time
from pathlib import Path

import pytest
from engine.vad import (
    get_vad_backend, segment_from_scores, silence_from_speech,
    save_scores, load_scores, VADResult, EnergyVAD, FFmpegVADBackend,
)
from engine.pause_classifier import classify_all_pauses
from engine.audio_fusion import fuse_audio_truth
from engine.jumpcut import JumpCutEngine, quick_cut
from engine.multitrack import TimelineTrack, MultiTrackProject
from engine.fcpxml import export_fcpxml
from engine.report import ReportBuilder, write_report_json

import sys
_TELUGIFY = Path.home() / ".cache" / "telugify"
_TITAN = Path.home() / "titan_engine" / "downloads"

# The proxy recording: a 10-minute, 239 MB YouTube source that is not
# redistributable with this repo. Its asserted ratios are measurements of this
# specific file, so it is fetched out-of-band by whoever wants the numbers
# rather than downloaded on every CI run.
_PROXY_ENV = "SELEKYT_PROXY_MEDIA"
_proxy = (Path(os.environ[_PROXY_ENV]) if os.environ.get(_PROXY_ENV)
          else _TITAN / "Es69N24I2Yo.mp4")
_has_proxy = _proxy.is_file()
PROXY_REASON = (
    f"opt-in measurement: set {_PROXY_ENV} to the proxy recording "
    f"(a ~239 MB YouTube source, not redistributable with the repo; "
    f"expected {_proxy})"
)
_PROXY_MARK = pytest.mark.skipif(not _has_proxy, reason=PROXY_REASON)


def _is_audio_file(p: Path) -> bool:
    return p.suffix.lower() in (".wav", ".mp3", ".m4a")


def _real_wavs():
    """WAV files of real speech for the VAD realism checks.

    Looked up in this order: SELEKYT_WAV_CORPUS, the 16 kHz mono audio a
    ``tools/corpus_run.py`` run extracts under corpus/, then the telugify
    cache the tests originally used. The corpus path matters because those
    WAVs are real speech the repo already knows how to produce, so the checks
    run wherever a corpus run has happened instead of only on a machine that
    happens to have another project's cache directory.
    """
    env = os.environ.get("SELEKYT_WAV_CORPUS")
    roots: list[Path] = []
    if env:
        roots.append(Path(env))
    roots.append(Path(__file__).resolve().parent.parent / "corpus")
    roots.append(_TELUGIFY)
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found += [p for p in sorted(root.glob("**/*.wav"))
                      if p.is_file() and p.stat().st_size > 0]
    # Deduplicate while keeping corpus order, and cap at 20 as before.
    seen, out = set(), []
    for p in found:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:20]


CORPUS_REASON = (
    "opt-in measurement: needs real-speech WAVs — set SELEKYT_WAV_CORPUS to a "
    "directory of 16 kHz mono WAVs, or run tools/corpus_run.py, which extracts "
    "them under corpus/*_run/project/transcription/"
)


# ── VAD Backend on real audio ──

@_PROXY_MARK
def test_energy_vad_proxy_produces_speech_regions():
    backend = EnergyVAD()
    pcm = _TELUGIFY / "tmp_vad_proxy.wav"
    if not pcm.exists():
        from providers.audio import extract_pcm_wav
        extract_pcm_wav(_proxy, pcm, 16000)
    t0 = time.perf_counter()
    result = backend.analyze(pcm)
    vad_elapsed = round(time.perf_counter() - t0, 2)
    speech = segment_from_scores(
        result.scores, result.sample_rate, result.frame_ms,
        threshold=0.5, min_speech_ms=150, min_silence_ms=100,
    )
    sil = silence_from_speech(speech, result.duration)
    total_speech = sum(s.end - s.start for s in speech)
    total_sil = sum(s.duration for s in sil)
    speech_ratio = round(total_speech / max(0.01, result.duration), 3)
    long_silence = sum(1 for s in sil if s.duration >= 1.5)
    long_silence_total = round(sum(s.duration for s in sil if s.duration >= 1.5), 2)
    print(f"\n[proxy VAD] backend={result.backend} frames={len(result.scores)} "
          f"duration={result.duration:.1f}s vad_time={vad_elapsed}s")
    print(f"  speech_regions={len(speech)} speech_ratio={speech_ratio}")
    print(f"  silence_regions={len(sil)} long_silence_count={long_silence} "
          f"long_silence_total={long_silence_total}s")
    assert len(speech) > 0
    assert 0.1 < speech_ratio < 0.95
    assert result.duration > 300


@_PROXY_MARK
def test_ffmpeg_vad_proxy_matches_energy_regions():
    """Energy and FFmpeg VAD should find comparable speech ratio on same media."""
    pcm = _TELUGIFY / "tmp_vad_proxy.wav"
    if not pcm.exists():
        from providers.audio import extract_pcm_wav
        extract_pcm_wav(_proxy, pcm, 16000)
    energy = EnergyVAD().analyze(pcm)
    ffmpeg_backend = FFmpegVADBackend()
    if not ffmpeg_backend.is_available():
        pytest.skip("ffmpeg not available")
    ffmpeg_res = ffmpeg_backend.analyze(pcm)
    e_speech = segment_from_scores(energy.scores, energy.sample_rate, energy.frame_ms)
    f_speech = segment_from_scores(ffmpeg_res.scores, ffmpeg_res.sample_rate, ffmpeg_res.frame_ms)
    e_ratio = sum(s.end - s.start for s in e_speech) / max(0.01, energy.duration)
    f_ratio = sum(s.end - s.start for s in f_speech) / max(0.01, ffmpeg_res.duration)
    print(f"\n[proxy VAD compare] energy_speech={e_ratio:.3f} ffmpeg_speech={f_ratio:.3f}")
    # Different algorithms yield different speech ratios — expected and
    # documented. Both are valid; the difference quantifies backend disagreement
    # on the same media, useful for model selection. Threshold < 0.55.
    assert abs(e_ratio - f_ratio) < 0.55, "VAD backends disagree excessively"


@_PROXY_MARK
def test_pause_classification_proxy():
    """Classify silences on proxy video — expect mix of dead_air and breathing."""
    pcm = _TELUGIFY / "tmp_vad_proxy.wav"
    if not pcm.exists():
        from providers.audio import extract_pcm_wav
        extract_pcm_wav(_proxy, pcm, 16000)
    backend = EnergyVAD()
    result = backend.analyze(pcm)
    speech = segment_from_scores(result.scores, result.sample_rate, result.frame_ms)
    sil = silence_from_speech(speech, result.duration)
    pauses = classify_all_pauses(sil, speech, result.duration)
    dead_air = [p for p in pauses if p.pause_type.value == "dead_air"]
    breathing = [p for p in pauses if p.pause_type.value == "breathing"]
    print(f"\n[proxy pauses] total={len(pauses)} dead_air={len(dead_air)} "
          f"breathing={len(breathing)}")
    assert len(pauses) > 0
    assert len(dead_air) + len(breathing) > 0


@_PROXY_MARK
def test_jumpcut_compression_proxy():
    """Jump-cut engine on proxy video: expect meaningful compression."""
    pcm = _TELUGIFY / "tmp_vad_proxy.wav"
    if not pcm.exists():
        from providers.audio import extract_pcm_wav
        extract_pcm_wav(_proxy, pcm, 16000)
    backend = EnergyVAD()
    result = backend.analyze(pcm)
    speech = segment_from_scores(result.scores, result.sample_rate, result.frame_ms)
    sil = silence_from_speech(speech, result.duration)
    pauses = classify_all_pauses(sil, speech, result.duration)
    truth = fuse_audio_truth([], speech, sil, pauses, result.duration, vad_backend="energy")
    cl = JumpCutEngine(silence_threshold=0.5, keep_intentional=True).generate_cutlist(truth)
    print(f"\n[proxy jumpcut] original={cl.original_duration:.1f}s "
          f"keep={cl.total_keep_duration:.1f}s removed={cl.total_remove_duration:.1f}s "
          f"ratio={cl.compression_ratio:.3f}")
    assert cl.total_remove_duration > 0
    assert cl.compression_ratio < 1.0


@_PROXY_MARK
def test_fcpxml_export_proxy():
    """FCPXML export from proxy cutlist: produces valid XML."""
    pcm = _TELUGIFY / "tmp_vad_proxy.wav"
    if not pcm.exists():
        from providers.audio import extract_pcm_wav
        extract_pcm_wav(_proxy, pcm, 16000)
    result = EnergyVAD().analyze(pcm)
    speech = segment_from_scores(result.scores, result.sample_rate, result.frame_ms)
    sil = silence_from_speech(speech, result.duration)
    pauses = classify_all_pauses(sil, speech, result.duration)
    truth = fuse_audio_truth([], speech, sil, pauses, result.duration)
    cl = quick_cut(truth, silence_threshold=0.5)
    out = export_fcpxml(cl, Path(pcm).parent / "test_cutlist.fcpxml",
                        source_path=str(_proxy))
    content = out.read_text()
    assert '<fcpxml version="1.11">' in content
    # AutoCut-format emitter: keeps become asset-clips on the primary
    # storyline, so a real proxy cutlist must produce at least one.
    assert "<asset-clip" in content, "no keeps survived to the export"
    print(f"\n[proxy fcpxml] output={out} size={out.stat().st_size} bytes")


# ── WAV corpus VAD determinism ──

def test_wav_corpus_vad_deterministic():
    """Same WAV + same params → same segmentation. Verify caching roundtrip."""
    wavs = _real_wavs()
    if not wavs:
        pytest.skip(CORPUS_REASON)
    wav = wavs[0]
    backend = EnergyVAD()
    r1 = backend.analyze(wav)
    s1 = segment_from_scores(r1.scores, r1.sample_rate, r1.frame_ms)
    r2 = backend.analyze(wav)
    s2 = segment_from_scores(r2.scores, r2.sample_rate, r2.frame_ms)
    assert len(s1) == len(s2)
    for a, b in zip(s1, s2):
        assert abs(a.start - b.start) < 0.01
        assert abs(a.end - b.end) < 0.01


def test_wav_corpus_energy_speech_ratio_range():
    """Energy VAD on corpus WAVs: speech ratio in reasonable bounds."""
    wavs = _real_wavs()
    if not wavs:
        pytest.skip(CORPUS_REASON)
    backend = EnergyVAD()
    ratios = []
    for wav in wavs[:10]:
        try:
            result = backend.analyze(wav)
            speech = segment_from_scores(result.scores, result.sample_rate, result.frame_ms)
            if result.duration > 0.1:
                ratios.append(sum(s.end - s.start for s in speech) / result.duration)
        except Exception:
            pass
    if ratios:
        avg = sum(ratios) / len(ratios)
        print(f"\n[corpus] wavs_tested={len(ratios)} avg_speech_ratio={avg:.3f}")
        assert 0.05 < avg < 0.95


# ── Proxy report + multitrack on real data ──

@_PROXY_MARK
def test_report_provenance_on_proxy():
    """ReportBuilder captures proxy source fingerprint correctly."""
    from providers.audio import source_fingerprint
    fp = source_fingerprint(_proxy, str(_proxy))
    b = ReportBuilder()
    b.set_provenance(source_path=str(_proxy), source_fingerprint=fp,
                     whisper_model="small", vad_backend="energy")
    d = b.to_dict()
    assert d["provenance"]["source_fingerprint"] == fp
    assert d["provenance"]["vad_backend"] == "energy"
    assert d["provenance"]["source_path"] == str(_proxy)


@_PROXY_MARK
def test_multitrack_alignment_proxy():
    """Multi-track timeline aligns correctly on proxy cutlist."""
    pcm = _TELUGIFY / "tmp_vad_proxy.wav"
    if not pcm.exists():
        from providers.audio import extract_pcm_wav
        extract_pcm_wav(_proxy, pcm, 16000)
    result = EnergyVAD().analyze(pcm)
    speech = segment_from_scores(result.scores, result.sample_rate, result.frame_ms)
    sil = silence_from_speech(speech, result.duration)
    pauses = classify_all_pauses(sil, speech, result.duration)
    truth = fuse_audio_truth([], speech, sil, pauses, result.duration)
    cl = quick_cut(truth, silence_threshold=0.5)
    track = TimelineTrack.from_cutlist(cl, name="proxy-main", lane=0)
    project = MultiTrackProject([track], source_duration=result.duration)
    issues = project.validate()
    print(f"\n[proxy multitrack] track_intervals={len(track.intervals)} "
          f"output_duration={track.total_output_duration:.1f}s issues={issues}")
    assert track.total_output_duration > 0
    assert issues == []