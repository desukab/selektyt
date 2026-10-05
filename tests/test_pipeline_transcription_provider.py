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


def test_a_completed_cloud_transcript_is_resumed_without_a_live_key(
        monkeypatch, tmp_path):
    """A cached transcript is reused, so resuming it needs no credential.

    The transcript cache exists so an engine fix can be re-run over a
    transcription that was already paid for. Requiring the key before checking
    the cache made an expired or absent key block that resume outright — on this
    host that is exactly the shape of the Tears of Steel acceptance run, which
    reuses a completed Groq transcript and has no live key to present.

    A run that would actually transcribe still requires the key
    (``test_explicit_cloud_provider_requires_key_...`` above), so this is a
    narrower check on the resume path, not a loosening of the cloud contract.
    """
    media = tmp_path / "source.mp4"
    media.write_bytes(b"fixture")
    project = tmp_path / "project"
    (project / "transcription").mkdir(parents=True)
    transcript = project / "transcript.json"
    transcript.write_text(json.dumps({
        "language": "en",
        "asr": {"provider": "groq", "model": "whisper-large-v3"},
        "segments": [],
    }), encoding="utf-8")

    def refuse(provider):
        raise AssertionError(f"{provider} key was demanded on a cached resume")
    monkeypatch.setattr(pipeline, "require_transcription_api_key", refuse)

    state = {
        "fingerprint": pipeline._fingerprint(media, str(media), "small", None, "groq"),
        "source": str(media),
    }
    key = pipeline._cached_or_require_key(
        state, str(media), "small", None, "groq",
        transcript_path=transcript)
    assert key is None, "a resumed transcript must not demand a live credential"


def test_a_cached_fingerprint_without_a_transcript_still_requires_the_key(
        monkeypatch, tmp_path):
    """A fingerprint match alone is not evidence the transcript exists.

    The cache guard compares the fingerprint, but a state file can outlive its
    transcript (a partial cleanup, a hand-edited project). Demanding the key in
    that case keeps the cloud contract intact: nothing is resumed, so nothing
    is free to skip authentication.
    """
    media = tmp_path / "source.mp4"
    media.write_bytes(b"fixture")

    def missing(provider):
        raise RuntimeError(f"{provider} key missing")
    monkeypatch.setattr(pipeline, "require_transcription_api_key", missing)

    state = {
        "fingerprint": pipeline._fingerprint(media, str(media), "small", None, "groq"),
    }
    with pytest.raises(RuntimeError, match="groq key missing"):
        pipeline._cached_or_require_key(
            state, str(media), "small", None, "groq",
            transcript_path=media.parent / "transcript.json")


def test_an_explicit_language_forces_transcription_and_the_key(monkeypatch, tmp_path):
    """``--language x`` makes a cached fingerprint unusable, so the key returns.

    The cache is only reused when no language was asked for; an explicit one
    falls through and re-transcribes. Checking the fingerprint without that
    condition would hand a ``None`` key to a transcription that actually runs —
    the helper would be complicit in an unauthenticated cloud call.
    """
    media = tmp_path / "source.mp4"
    media.write_bytes(b"fixture")
    transcript = tmp_path / "project" / "transcript.json"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"language": "en", "segments": []}),
                          encoding="utf-8")

    def missing(provider):
        raise RuntimeError(f"{provider} key missing")
    monkeypatch.setattr(pipeline, "require_transcription_api_key", missing)

    state = {
        "fingerprint": pipeline._fingerprint(media, str(media), "small", None, "groq"),
    }
    with pytest.raises(RuntimeError, match="groq key missing"):
        pipeline._cached_or_require_key(
            state, str(media), "small", "en", "groq",
            transcript_path=transcript)


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
