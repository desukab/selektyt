"""Test per-stage resumability in the pipeline."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from models.core import Candidate, Segment, Word


def _fake_transcript():
    words = [Word("hello", 0.0, 0.5)]
    seg = Segment(10.0, 20.0, "This is a great important thing to say in a video.", words)
    return [seg], "en"


def _fake_candidate():
    words = [Word("hello", 10.0, 10.5)]
    return Candidate(
        start=10.0, end=20.0, text="This is a great important thing to say in a video.",
        story_type="story", hook="This is a great", payoff="say in a video.",
        editorial_score=0.9, virality_score=0.8, context_score=0.9,
        confidence=0.9, reason="test", visual_potential=0.8, words=words,
    )


def test_pipeline_reuses_downloaded_media(tmp_path):
    """A second run must not call download() again for the same source."""
    media = tmp_path / "video.mp4"
    media.write_bytes(b"0" * 100)
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "facebook").mkdir(exist_ok=True)
    (project / "shorts").mkdir(exist_ok=True)
    (project / "captions").mkdir(exist_ok=True)
    (project / "content_pack").mkdir(exist_ok=True)

    fp = "test-fingerprint"
    state = {"source": "https://example.com/v", "media_path": str(media),
             "media_info": {"title": "t"}, "fingerprint": fp}
    (project / "pipeline_state.json").write_text(json.dumps(state), encoding="utf-8")
    (project / "transcript.json").write_text(json.dumps({
        "language": "en",
        "segments": [{"start": 10.0, "end": 20.0, "text": "hello world",
                       "words": [{"text": "hello", "start": 0.0, "end": 0.5, "confidence": 1.0}]}],
    }), encoding="utf-8")

    with patch("selekyt.pipeline.download") as mock_download, \
         patch("selekyt.pipeline.render_clip"), \
         patch("selekyt.pipeline.write_report"), \
         patch("selekyt.pipeline.make_thoughts", return_value=[Segment(10, 20, "hello", [])]), \
         patch("selekyt.pipeline.rank", return_value=[_fake_candidate()]), \
         patch("selekyt.pipeline.validate", return_value=(True, "ok")), \
         patch("selekyt.pipeline.ass_for_candidate"), \
         patch("selekyt.pipeline.caption_metadata", return_value={"style": "minimal"}), \
         patch("selekyt.pipeline.build_content_pack", return_value={"pack": True}):
        import selekyt.pipeline as p
        with patch.object(p, "_fingerprint", return_value=fp):
            p.run("https://example.com/v", tmp_path, model="small")
    mock_download.assert_not_called()


def test_pipeline_skips_render_when_clips_exist(tmp_path):
    """Rendering is skipped when fb/sh clips already exist for this fingerprint."""
    media = tmp_path / "video.mp4"
    media.write_bytes(b"x" * 100)
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "facebook").mkdir(exist_ok=True)
    (project / "shorts").mkdir(exist_ok=True)
    (project / "captions").mkdir(exist_ok=True)
    (project / "content_pack").mkdir(exist_ok=True)

    fp = "test-fingerprint"
    state = {"source": "https://example.com/v", "media_path": str(media),
             "media_info": {"title": "t"}, "fingerprint": fp,
             "render_fingerprint": fp}
    (project / "pipeline_state.json").write_text(json.dumps(state), encoding="utf-8")
    (project / "transcript.json").write_text(json.dumps({
        "language": "en",
        "segments": [{"start": 10.0, "end": 20.0, "text": "hello world",
                       "words": [{"text": "hello", "start": 0.0, "end": 0.5, "confidence": 1.0}]}],
    }), encoding="utf-8")
    (project / "facebook" / "01.mp4").write_bytes(b"clip")
    (project / "shorts" / "01.mp4").write_bytes(b"clip")

    with patch("selekyt.pipeline.download") as mock_download, \
         patch("selekyt.pipeline.render_clip") as mock_render, \
         patch("selekyt.pipeline.write_report"), \
         patch("selekyt.pipeline.make_thoughts", return_value=[Segment(10, 20, "hello", [])]), \
         patch("selekyt.pipeline.rank", return_value=[_fake_candidate()]), \
         patch("selekyt.pipeline.validate", return_value=(True, "ok")), \
         patch("selekyt.pipeline.ass_for_candidate"), \
         patch("selekyt.pipeline.caption_metadata", return_value={"style": "minimal"}), \
         patch("selekyt.pipeline.build_content_pack", return_value={"pack": True}):
        import selekyt.pipeline as p
        with patch.object(p, "_fingerprint", return_value=fp):
            p.run("https://example.com/v", tmp_path, model="small")
    mock_render.assert_not_called()
