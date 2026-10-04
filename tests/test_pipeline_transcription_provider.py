from __future__ import annotations

import json

import pytest

from selekyt import pipeline


def test_explicit_cloud_provider_requires_key_before_downloading_or_running_local_asr(
        monkeypatch, tmp_path):
    def missing(provider):
        raise RuntimeError(f"{provider} key missing")

    monkeypatch.setattr(pipeline, "require_transcription_api_key", missing)
    monkeypatch.setattr(pipeline, "download", lambda *a, **k: pytest.fail("download ran"))
    monkeypatch.setattr(pipeline, "select_engine", lambda *a, **k: pytest.fail("local ASR was probed"))

    with pytest.raises(RuntimeError, match="groq key missing"):
        pipeline.run("https://example.test/video", tmp_path,
                     transcription_provider="groq")
    assert not (tmp_path / "project").exists()


def test_transcript_fingerprint_changes_with_provider(tmp_path):
    media = tmp_path / "input.mp4"
    media.write_bytes(b"fixture")
    local = pipeline._fingerprint(media, "input.mp4", "small", None, "local")
    groq = pipeline._fingerprint(media, "input.mp4", "small", None, "groq")
    gemini = pipeline._fingerprint(media, "input.mp4", "small", None, "gemini")
    assert len({local, groq, gemini}) == 3


def test_provider_default_remains_local(monkeypatch, tmp_path):
    from selekyt import transcription_settings

    monkeypatch.setattr(transcription_settings, "settings_path",
                        lambda: tmp_path / "no-settings.json")
    assert transcription_settings.default_provider() == "local"


def test_cached_canonical_transcript_preserves_provider_speaker_and_word_times(tmp_path):
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps({
        "language": "te",
        "asr": {"provider": "gemini", "model": "gemini-3.5-transcribe"},
        "segments": [{
            "start": 1.0, "end": 2.0, "text": "నమస్కారం",
            "speaker": "spk_1",
            "words": [{"text": "నమస్కారం", "start": 1.0, "end": 2.0,
                       "confidence": 1.0}],
        }],
    }), encoding="utf-8")
    segments, language = pipeline._load_transcript(path)
    assert language == "te"
    assert segments[0].speaker == "spk_1"
    assert segments[0].words[0].start == pytest.approx(1.0)
    assert pipeline._asr_provenance(path)["provider"] == "gemini"
