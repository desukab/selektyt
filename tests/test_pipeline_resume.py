"""Test per-stage resumability in the pipeline."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from models.core import Candidate, Segment, Word


from selekyt.pipeline import _selection_digest


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


_FAKE_PACING = {
    "total_candidates": 1, "selected_count": 1, "rejected_count": 0,
    "context_guard_rejections": 0, "avg_duration": 10.0,
    "duration_distribution": {"<15s": 1, "15-30s": 0, "30-45s": 0, "45-55s": 0},
    "silence_pct": 0.0, "filler_pct": 0.0, "avg_words_per_second": 2.0,
    "avg_hook_latency": 0.0, "payoff_presence_rate": 1.0, "duplicate_overlap_rate": 0.0,
}


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
         patch("selekyt.pipeline.build_content_pack", return_value={"pack": True}), \
         patch("selekyt.pipeline.attach_metadata", side_effect=lambda c, *a, **kw: c), \
         patch("selekyt.pipeline.aggregate_pacing_report", return_value=_FAKE_PACING):
        import selekyt.pipeline as p
        with patch.object(p, "_fingerprint", return_value=fp):
            p.run("https://example.com/v", tmp_path, model="small")
    mock_download.assert_not_called()


def _real_render_pair(project: Path, vertical_size: str = "360x640") -> None:
    """Write a genuine, ffprobe-valid pair of renders into a project.

    F13: the render cache now validates a cached clip with ffprobe before
    reusing it, so a 5-byte stub is no longer accepted as a finished render.
    Tests of the cache path must hand it a real video.
    """
    import subprocess

    for kind, size in (("facebook", "640x360"), ("shorts", vertical_size)):
        out = project / kind / "01.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:s={size}:d=3.0",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=3.0",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
             "-c:a", "aac", "-b:a", "64k", "-shortest", str(out)],
            check=True, capture_output=True,
        )


def test_pipeline_skips_render_when_clips_exist(tmp_path):
    """Rendering is skipped when valid fb/sh clips exist for this selection."""
    media = tmp_path / "video.mp4"
    media.write_bytes(b"x" * 100)
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "facebook").mkdir(exist_ok=True)
    (project / "shorts").mkdir(exist_ok=True)
    (project / "captions").mkdir(exist_ok=True)
    (project / "content_pack").mkdir(exist_ok=True)

    fp = "test-fingerprint"
    cand = _fake_candidate()
    digest = _selection_digest([cand])
    state = {"source": "https://example.com/v", "media_path": str(media),
             "media_info": {"title": "t"}, "fingerprint": fp,
             "render_fingerprint": fp,
             "render_selection_digest": digest,
             "renders": {"01": {"fingerprint": fp, "selection_digest": digest,
                                "caption_style": "minimal",
                                "facebook": str(project / "facebook" / "01.mp4"),
                                "shorts": str(project / "shorts" / "01.mp4")}}}
    (project / "pipeline_state.json").write_text(json.dumps(state), encoding="utf-8")
    (project / "transcript.json").write_text(json.dumps({
        "language": "en",
        "segments": [{"start": 10.0, "end": 20.0, "text": "hello world",
                       "words": [{"text": "hello", "start": 0.0, "end": 0.5, "confidence": 1.0}]}],
    }), encoding="utf-8")
    _real_render_pair(project)

    with patch("selekyt.pipeline.download") as mock_download, \
         patch("selekyt.pipeline.render_clip") as mock_render, \
         patch("selekyt.pipeline.write_report"), \
         patch("selekyt.pipeline.make_thoughts", return_value=[Segment(10, 20, "hello", [])]), \
         patch("selekyt.pipeline.rank", return_value=[_fake_candidate()]), \
         patch("selekyt.pipeline.validate", return_value=(True, "ok")), \
         patch("selekyt.pipeline.ass_for_candidate"), \
         patch("selekyt.pipeline.caption_metadata", return_value={"style": "minimal"}), \
         patch("selekyt.pipeline.build_content_pack", return_value={"pack": True}), \
         patch("selekyt.pipeline.attach_metadata", side_effect=lambda c, *a, **kw: c), \
         patch("selekyt.pipeline.aggregate_pacing_report", return_value=_FAKE_PACING):
        import selekyt.pipeline as p
        with patch.object(p, "_fingerprint", return_value=fp):
            p.run("https://example.com/v", tmp_path, model="small")
    mock_render.assert_not_called()


def test_pipeline_writes_editorial_and_pacing_reports(tmp_path):
    """Pipeline writes editorial_report.json and pacing_report.json."""
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

    with patch("selekyt.pipeline.download"), \
         patch("selekyt.pipeline.render_clip"), \
         patch("selekyt.pipeline.write_report"), \
         patch("selekyt.pipeline.make_thoughts", return_value=[Segment(10, 20, "hello", [])]), \
         patch("selekyt.pipeline.rank", return_value=[_fake_candidate()]), \
         patch("selekyt.pipeline.validate", return_value=(True, "ok")), \
         patch("selekyt.pipeline.ass_for_candidate"), \
         patch("selekyt.pipeline.caption_metadata", return_value={"style": "minimal"}), \
         patch("selekyt.pipeline.build_content_pack", return_value={"pack": True}), \
         patch("selekyt.pipeline.attach_metadata", side_effect=lambda c, *a, **kw: c), \
         patch("selekyt.pipeline.aggregate_pacing_report", return_value=_FAKE_PACING):
        import selekyt.pipeline as p
        with patch.object(p, "_fingerprint", return_value=fp):
            p.run("https://example.com/v", tmp_path, model="small")

    editorial = json.loads((project / "editorial_report.json").read_text())
    pacing = json.loads((project / "pacing_report.json").read_text())
    assert len(editorial) == 1
    assert editorial[0]["story_type"] == "story"
    assert pacing["total_candidates"] == 1
    assert "avg_duration" in pacing
