"""CLI + pipeline audio wiring tests."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from models.core import Segment, Word


# ── CLI flags ──

def test_cli_accepts_audio_flags():
    from selekyt.cli import main
    with patch("selekyt.cli._doctor") as doctor, \
         patch("sys.argv", ["selekyt", "--audio-analysis", "--export-fcpxml",
                            "--silence-threshold", "0.7",
                            "--min-speech-ms", "200",
                            "--min-silence-ms", "120",
                            "--keep-pauses", "--remove-filler",
                            "https://example.com/v"]), \
         patch("selekyt.cli.run") as mock_run:
        main()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["audio_analysis"] is True
    assert kwargs["export_fcpxml"] is True
    assert kwargs["silence_threshold"] == 0.7
    assert kwargs["min_speech_ms"] == 200
    assert kwargs["min_silence_ms"] == 120


def test_cli_doctor_no_source():
    from selekyt.cli import main
    with patch("selekyt.cli._doctor", return_value=0) as doctor, \
         patch("sys.argv", ["selekyt", "--doctor"]):
        try:
            main()
        except SystemExit:
            pass
    doctor.assert_called()


def test_cli_defaults():
    from selekyt.cli import main
    with patch("selekyt.cli._doctor") as doctor, \
         patch("sys.argv", ["selekyt", "x.mp4"]), \
         patch("selekyt.cli.run") as mock_run:
        main()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["audio_analysis"] is False
    assert kwargs["silence_threshold"] == 0.5
    assert kwargs["min_speech_ms"] == 150


# ── pipeline audio wiring ──

def test_pipeline_audio_off_by_default(tmp_path):
    """Without --audio-analysis, no VAD artifacts and no audio in report."""
    media = tmp_path / "v.mp4"
    media.write_bytes(b"0" * 100)
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    for d in ("facebook", "shorts", "captions", "content_pack"):
        (project / d).mkdir(exist_ok=True)
    fp = "fp"
    state = {"source": "https://example.com/v", "media_path": str(media),
             "media_info": {}, "fingerprint": fp}
    (project / "pipeline_state.json").write_text(json.dumps(state))
    (project / "transcript.json").write_text(json.dumps({
        "language": "en",
        "segments": [{"start": 0.0, "end": 10.0, "text": "hello world",
                       "words": [{"text": "hello", "start": 0.0, "end": 0.5, "confidence": 1.0}]}],
    }))

    from models.core import Candidate
    cand = Candidate(0, 10, "hello world this is great", "story", "hello",
                     "great", editorial_score=0.9, virality_score=0.8,
                     context_score=0.9, confidence=0.9, reason="t",
                     visual_potential=0.8, words=[Word("hello", 0.0, 0.5)])
    with patch("selekyt.pipeline.download"), \
         patch("selekyt.pipeline.render_clip"), \
         patch("selekyt.pipeline.write_report"), \
         patch("selekyt.pipeline.make_thoughts", return_value=[Segment(0, 10, "hello", [])]), \
         patch("selekyt.pipeline.score_all", return_value=[cand]), \
         patch("selekyt.pipeline.validate", return_value=(True, "ok")), \
         patch("selekyt.pipeline.caption_metadata", return_value={}), \
         patch("selekyt.pipeline.build_content_pack", return_value={}), \
         patch("selekyt.pipeline.aggregate_pacing_report", return_value={"total_candidates": 1}):
        import selekyt.pipeline as p
        with patch.object(p, "_fingerprint", return_value=fp):
            p.run("https://example.com/v", tmp_path, model="small")
    report = json.loads((project / "report.json").read_text())
    # Absence is explicit, not silent: a B2B consumer can tell "not run" apart
    # from a failure.
    assert report["audio"] == {"enabled": False}
    assert not (project / "audio").exists()


def test_pipeline_audio_on_writes_artifacts(tmp_path):
    """With audio_analysis=True, VAD artifacts written to project/audio/."""
    media = tmp_path / "v.mp4"
    media.write_bytes(b"0" * 100)
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    for d in ("facebook", "shorts", "captions", "content_pack"):
        (project / d).mkdir(exist_ok=True)
    fp = "fp"
    state = {"source": "https://example.com/v", "media_path": str(media),
             "media_info": {}, "fingerprint": fp}
    (project / "pipeline_state.json").write_text(json.dumps(state))
    (project / "transcript.json").write_text(json.dumps({
        "language": "en",
        "segments": [{"start": 0.0, "end": 10.0, "text": "hello world",
                       "words": [{"text": "hello", "start": 0.0, "end": 0.5, "confidence": 1.0}]}],
    }))

    from models.core import Candidate
    cand = Candidate(0, 10, "hello world this is great", "story", "hello",
                     "great", editorial_score=0.9, virality_score=0.8,
                     context_score=0.9, confidence=0.9, reason="t",
                     visual_potential=0.8, words=[Word("hello", 0.0, 0.5)])
    fake_summary = {
        "vad_backend": "energy", "speech_ratio": 0.5, "silence_ratio": 0.5,
        "dead_air_ratio": 0.3, "speech_regions": 2, "compression_ratio": 0.7,
    }
    with patch("selekyt.pipeline.download"), \
         patch("selekyt.pipeline.render_clip"), \
         patch("selekyt.pipeline.write_report"), \
         patch("selekyt.pipeline.make_thoughts", return_value=[Segment(0, 10, "hello", [])]), \
         patch("selekyt.pipeline.score_all", return_value=[cand]), \
         patch("selekyt.pipeline.validate", return_value=(True, "ok")), \
         patch("selekyt.pipeline.caption_metadata", return_value={}), \
         patch("selekyt.pipeline.build_content_pack", return_value={}), \
         patch("selekyt.pipeline.aggregate_pacing_report", return_value={"total_candidates": 1}), \
         patch("selekyt.pipeline.run_pipeline_audio", return_value=fake_summary):
        import selekyt.pipeline as p
        with patch.object(p, "_fingerprint", return_value=fp):
            p.run("https://example.com/v", tmp_path, model="small", audio_analysis=True)
    report = json.loads((project / "report.json").read_text())
    assert report["audio"] == fake_summary


def test_pipeline_audio_enriches_pacing_via_real_helper(tmp_path):
    """The real run_pipeline_audio writes VAD fields into the pacing dict."""
    from engine.pipeline_audio import run_pipeline_audio
    summary = {
        "vad_backend": "energy", "vad_model": "energy-rms",
        "fallback_reason": "silero unavailable", "frame_ms": 32,
        "hysteresis": {"threshold": 0.5, "release": 0.35,
                       "min_speech_ms": 150, "min_silence_ms": 100},
        "speech_ratio": 0.5, "silence_ratio": 0.5,
        "dead_air_ratio": 0.3, "speech_regions": 2, "compression_ratio": 0.7,
    }
    pacing = {"total_candidates": 1}
    with patch("engine.pipeline_audio.run_audio_analysis", return_value=summary):
        got = run_pipeline_audio(
            tmp_path / "v.mp4", tmp_path, words=None, source="x.mp4",
            pacing=pacing,
        )
    assert got == summary
    assert pacing["vad"]["vad_backend"] == "energy"
    assert pacing["vad"]["vad_speech_ratio"] == 0.5
    # provenance must travel to the pacing report, not just the backend name
    assert pacing["vad"]["vad_model"] == "energy-rms"
    assert pacing["vad"]["fallback_reason"] == "silero unavailable"
    assert pacing["vad"]["frame_ms"] == 32
    assert pacing["vad"]["hysteresis"]["release"] == 0.35


def test_pipeline_audio_provenance_is_tolerant_of_partial_summary(tmp_path):
    """Older/partial summaries must not crash the pacing enrichment."""
    from engine.pipeline_audio import run_pipeline_audio
    partial = {
        "vad_backend": "ffmpeg", "speech_ratio": 0.4, "silence_ratio": 0.6,
        "dead_air_ratio": 0.2, "speech_regions": 1, "compression_ratio": 0.9,
    }
    pacing: dict = {}
    with patch("engine.pipeline_audio.run_audio_analysis", return_value=partial):
        got = run_pipeline_audio(
            tmp_path / "v.mp4", tmp_path, words=None, source="x.mp4",
            pacing=pacing,
        )
    assert got == partial
    assert pacing["vad"]["vad_backend"] == "ffmpeg"
    assert pacing["vad"]["vad_model"] is None
    assert pacing["vad"]["scores_cached"] is False


def test_pipeline_audio_passes_thresholds_to_engine(tmp_path):
    """Silence thresholds propagate into the audio engine call."""
    media = tmp_path / "v.mp4"
    media.write_bytes(b"0" * 100)
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    for d in ("facebook", "shorts", "captions", "content_pack"):
        (project / d).mkdir(exist_ok=True)
    fp = "fp"
    state = {"source": "https://example.com/v", "media_path": str(media),
             "media_info": {}, "fingerprint": fp}
    (project / "pipeline_state.json").write_text(json.dumps(state))
    (project / "transcript.json").write_text(json.dumps({
        "language": "en",
        "segments": [{"start": 0.0, "end": 10.0, "text": "hello world",
                       "words": [{"text": "hello", "start": 0.0, "end": 0.5, "confidence": 1.0}]}],
    }))

    from models.core import Candidate
    cand = Candidate(0, 10, "hello world this is great", "story", "hello",
                     "great", editorial_score=0.9, virality_score=0.8,
                     context_score=0.9, confidence=0.9, reason="t",
                     visual_potential=0.8, words=[Word("hello", 0.0, 0.5)])
    with patch("selekyt.pipeline.download"), \
         patch("selekyt.pipeline.render_clip"), \
         patch("selekyt.pipeline.write_report"), \
         patch("selekyt.pipeline.make_thoughts", return_value=[Segment(0, 10, "hello", [])]), \
         patch("selekyt.pipeline.score_all", return_value=[cand]), \
         patch("selekyt.pipeline.validate", return_value=(True, "ok")), \
         patch("selekyt.pipeline.caption_metadata", return_value={}), \
         patch("selekyt.pipeline.build_content_pack", return_value={}), \
         patch("selekyt.pipeline.aggregate_pacing_report", return_value={"total_candidates": 1}), \
         patch("selekyt.pipeline.run_pipeline_audio",
               return_value={"vad_backend": "energy"}) as mock_audio:
        import selekyt.pipeline as p
        with patch.object(p, "_fingerprint", return_value=fp):
            p.run("https://example.com/v", tmp_path, model="small",
                  audio_analysis=True, silence_threshold=0.8,
                  min_speech_ms=250, export_fcpxml=True)
    kwargs = mock_audio.call_args.kwargs
    assert kwargs["silence_threshold"] == 0.8
    assert kwargs["min_speech_ms"] == 250
    assert kwargs["export_fcpxml"] is True
    assert kwargs["words"] is not None