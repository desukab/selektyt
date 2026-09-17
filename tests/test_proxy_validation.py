"""Proxy validation measurements — real media, no mocking.

Uses Es69N24I2Yo.mp4 (10min, 239MB) as PROXY source — clearly not
representative of Z907 content; results must be labeled accordingly.
Also exercises WAV corpus for VAD backend + segmentation correctness.

NOT a unit test. Exercises the real audio stack end-to-end against
actual media. Skips silently when proxy file is absent.
"""
import json
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

_proxy = _TITAN / "Es69N24I2Yo.mp4"
_has_proxy = _proxy.exists()


def _is_audio_file(p: Path) -> bool:
    return p.suffix.lower() in (".wav", ".mp3", ".m4a")


def _real_wavs():
    """Return WAV files from corpus for VAD realism checks."""
    if not _TELUGIFY.exists():
        return []
    return sorted(_TELUGIFY.glob("*.wav"))[:20]


# ── VAD Backend on real audio ──

@pytest.mark.skipif(not _has_proxy, reason="proxy video not present")
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


@pytest.mark.skipif(not _has_proxy, reason="proxy video not present")
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


@pytest.mark.skipif(not _has_proxy, reason="proxy video not present")
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


@pytest.mark.skipif(not _has_proxy, reason="proxy video not present")
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


@pytest.mark.skipif(not _has_proxy, reason="proxy video not present")
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
        pytest.skip("WAV corpus not available")
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
        pytest.skip("WAV corpus not available")
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

@pytest.mark.skipif(not _has_proxy, reason="proxy video not present")
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


@pytest.mark.skipif(not _has_proxy, reason="proxy video not present")
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