from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import types

import pytest

from models.core import Segment, Word
from providers import cloud_transcribe


def _fake_media(monkeypatch, tmp_path: Path, duration: float):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"source identity")

    def extract(_media, audio):
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"normalized audio" * 1000)

    def ffmpeg_chunk(args, **kwargs):
        Path(args[-1]).write_bytes(b"wav chunk" * 100)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cloud_transcribe, "_extract_audio", extract)
    monkeypatch.setattr(cloud_transcribe, "_duration", lambda _path: duration)
    monkeypatch.setattr(cloud_transcribe.subprocess, "run", ffmpeg_chunk)
    return media


def test_groq_long_audio_chunks_preserve_offsets_and_cache(monkeypatch, tmp_path):
    media = _fake_media(monkeypatch, tmp_path, 305.0)
    calls = []

    def groq(path, _key, _language):
        calls.append(path)
        if len(calls) == 1:
            return {
                "language": "en",
                "segments": [
                    {"start": 10.0, "end": 11.0, "text": "Opening.",
                     "words": [{"word": "Opening.", "start": 10, "end": 11,
                                "probability": 0.9}]},
                    # This utterance's midpoint belongs to the next core chunk.
                    {"start": 298.0, "end": 303.0, "text": "Boundary phrase.",
                     "words": [{"word": "Boundary", "start": 298, "end": 300},
                               {"word": "phrase.", "start": 300, "end": 303}]},
                ],
            }
        return {
            "language": "en",
            "segments": [
                {"start": 6.0, "end": 11.0, "text": "Boundary phrase.",
                 "words": [{"word": "Boundary", "start": 6, "end": 8},
                           {"word": "phrase.", "start": 8, "end": 11}]},
                {"start": 12.0, "end": 13.0, "text": "After.",
                 "words": [{"word": "After.", "start": 12, "end": 13}]},
            ],
        }

    monkeypatch.setattr(cloud_transcribe, "_groq_request", groq)
    segments, language, provenance = cloud_transcribe.transcribe(
        media, "groq", "owned-secret", checkpoint_dir=tmp_path / "transcription",
    )

    assert language == "en"
    assert [s.text for s in segments] == ["Opening.", "Boundary phrase.", "After."]
    assert segments[1].start == pytest.approx(298.0)
    assert segments[1].end == pytest.approx(303.0)
    assert segments[0].words[0].confidence == pytest.approx(0.9)
    assert provenance["provider"] == "groq"
    assert provenance["model"] == "whisper-large-v3"
    assert provenance["word_timestamps"] == "per-word"

    again, _, _ = cloud_transcribe.transcribe(
        media, "groq", "owned-secret", checkpoint_dir=tmp_path / "transcription",
    )
    assert len(calls) == 2
    assert [s.text for s in again] == [s.text for s in segments]

    cloud_transcribe.transcribe(
        media, "groq", "owned-secret", checkpoint_dir=tmp_path / "transcription",
        chunk_minutes=4,
    )
    assert len(calls) == 4, "changing the chunk window must invalidate cached results"


def test_gemini_word_annotations_normalize_to_canonical_speaker_segments():
    interaction = SimpleNamespace(
        output_text="Hello there.",
        steps=[SimpleNamespace(content=[SimpleNamespace(annotations=[
            SimpleNamespace(type="word_info", text="Hello", start_offset="0.1s",
                            end_offset="0.4s", speaker="spk_1"),
            SimpleNamespace(type="word_info", text="there.", start_offset="0.5s",
                            end_offset="0.8s", speaker="spk_1"),
        ])])],
    )
    segments = cloud_transcribe._gemini_segments(interaction, 20.0, 0.0, 100.0)
    assert len(segments) == 1
    segment = segments[0]
    assert isinstance(segment, Segment)
    assert segment.text == "Hello there."
    assert segment.speaker == "spk_1"
    assert segment.start == pytest.approx(20.1)
    assert segment.end == pytest.approx(20.8)
    assert [word.text for word in segment.words] == ["Hello", "there."]
    assert segment.words[0].start == pytest.approx(20.1)


def test_gemini_never_fabricates_timestamps_when_annotations_are_missing():
    with pytest.raises(RuntimeError, match="without the requested word timestamps"):
        cloud_transcribe._gemini_segments(
            SimpleNamespace(output_text="un-timed transcript", steps=[]),
            0.0, 0.0, 30.0,
        )


def test_gemini_word_joining_keeps_cjk_text_readable():
    interaction = SimpleNamespace(
        output_text="你好。",
        steps=[SimpleNamespace(content=[SimpleNamespace(annotations=[
            SimpleNamespace(type="word_info", text="你", start_offset="0s",
                            end_offset="0.2s", speaker=None),
            SimpleNamespace(type="word_info", text="好。", start_offset="0.2s",
                            end_offset="0.5s", speaker=None),
        ])])],
    )
    segments = cloud_transcribe._gemini_segments(interaction, 0.0, 0.0, 1.0)
    assert segments[0].text == "你好。"


def test_provider_language_names_normalize_to_iso_codes():
    assert cloud_transcribe._language_code("Telugu") == "te"
    assert cloud_transcribe._language_code("English") == "en"
    assert cloud_transcribe._language_code("pt-BR") == "pt-BR"


def test_gemini_silent_chunk_is_allowed_without_timing_annotations():
    assert cloud_transcribe._gemini_segments(
        SimpleNamespace(output_text="", steps=[]), 0.0, 0.0, 30.0,
    ) == []


def test_cloud_failure_redacts_the_user_key_and_never_substitutes_local(monkeypatch, tmp_path):
    media = _fake_media(monkeypatch, tmp_path, 10.0)

    def fail(_path, key, _language):
        raise RuntimeError(f"request failed for {key}")

    monkeypatch.setattr(cloud_transcribe, "_groq_request", fail)
    with pytest.raises(RuntimeError, match=r"Groq transcription failed: request failed for \[redacted\]") as error:
        cloud_transcribe.transcribe(
            media, "groq", "sensitive-key-value", checkpoint_dir=tmp_path / "transcription",
        )
    assert "sensitive-key-value" not in str(error.value)
    assert "No local transcript was substituted" in str(error.value)


def test_groq_adapter_uses_multilingual_large_v3_timestamp_endpoint(monkeypatch, tmp_path):
    media = tmp_path / "chunk.wav"
    media.write_bytes(b"wav")
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            seen["request"] = kwargs
            return {"text": "ok"}

    groq = types.ModuleType("groq")
    groq.Groq = Client
    monkeypatch.setitem(sys.modules, "groq", groq)
    assert cloud_transcribe._groq_request(media, "my-key", "te")["text"] == "ok"
    assert seen["client"] == {"api_key": "my-key"}
    request = seen["request"]
    assert request["model"] == "whisper-large-v3"
    assert request["language"] == "te"
    assert request["response_format"] == "verbose_json"
    assert request["timestamp_granularities"] == ["segment", "word"]


def test_gemini_adapter_uses_dedicated_verbatim_transcription_and_deletes_upload(
        monkeypatch, tmp_path):
    media = tmp_path / "chunk.wav"
    media.write_bytes(b"wav")
    seen = {}

    class Files:
        def upload(self, **kwargs):
            seen["upload"] = kwargs
            return SimpleNamespace(uri="files/1", mime_type="audio/wav", name="files/1")

        def delete(self, **kwargs):
            seen["delete"] = kwargs

    class Interactions:
        def create(self, **kwargs):
            seen["request"] = kwargs
            return SimpleNamespace(output_text="ok", steps=[])

    class Client:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.files = Files()
            self.interactions = Interactions()

    genai = types.ModuleType("google.genai")
    genai.Client = Client
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    assert cloud_transcribe._gemini_request(media, "my-key", "te").output_text == "ok"
    assert seen["client"] == {"api_key": "my-key"}
    assert seen["upload"] == {"file": str(media)}
    request = seen["request"]
    assert request["model"] == "gemini-3.5-transcribe"
    assert request["input"] == [{"type": "audio", "uri": "files/1", "mime_type": "audio/wav"}]
    config = request["generation_config"]["transcription_config"]
    assert config["language_codes"] == ["te-IN"]
    assert config["mode"] == {"type": "verbatim", "timestamp_granularities": ["word"]}
    assert seen["delete"] == {"name": "files/1"}
