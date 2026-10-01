"""The render cache must key on the caption style, because the captions are
burned into the MP4 rather than composited by the player.

The defect this pins: two runs differing only in ``SELEKYT_CAPTION_STYLE``
produce the same source fingerprint and the same selection digest. Without the
style in the cache key, the second run reused the first's MP4 while rewriting
the ``.ass`` beside it — a deliverable whose burned-in captions disagreed with
its own sidecar, detected by nothing, on a setting the user had deliberately
changed. All four presets emit distinct ASS bytes, so the disagreement was
material, not cosmetic.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.captions import _digest, ass_content
from engine.caption_director import style_name
from selekyt.pipeline import _program_digest, _selection_digest
from tests.test_pipeline_resume import _fake_candidate, _real_render_pair

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not available")


def _scaffold(tmp_path: Path, monkeypatch, cached_style: str | None):
    """A project whose state names a cached render written under ``cached_style``.

    ``None`` stands for a record written before the style was part of the key,
    which is what every existing ``pipeline_state.json`` in the corpus looks
    like; it must read as a miss, not as a match against ``minimal``.

    A cached record implies the previous run left the ``.ass`` on disk, so the
    scaffold writes the one the recorded style would have produced — with the
    digest the previous run would have recorded. Scaffolding under the same
    style a later run uses is what makes the cache-hit test a real hit.
    """
    media = tmp_path / "video.mp4"
    media.write_bytes(b"x" * 100)
    project = tmp_path / "project"
    for d in ("", "facebook", "shorts", "captions", "content_pack"):
        (project / d).mkdir(parents=True, exist_ok=True)

    cand = _fake_candidate()
    if cached_style is not None:
        monkeypatch.setenv("SELEKYT_CAPTION_STYLE", cached_style)
    fp = "test-fingerprint"
    digest = _selection_digest([cand])
    record = {"fingerprint": fp, "selection_digest": digest,
              "program_digest": _program_digest([cand]),
              "facebook": str(project / "facebook" / "01.mp4"),
              "shorts": str(project / "shorts" / "01.mp4")}
    if cached_style is not None:
        record["caption_style"] = cached_style
        ass = ass_content(cand)
        record["ass_digest"] = _digest(ass)
        (project / "captions" / "01.ass").write_text(ass, encoding="utf-8")
    state = {"source": "https://example.com/v", "media_path": str(media),
             "media_info": {"title": "t"}, "fingerprint": fp,
             "renders": {"01": record}}
    (project / "pipeline_state.json").write_text(json.dumps(state), encoding="utf-8")
    (project / "transcript.json").write_text(json.dumps({
        "language": "en",
        "segments": [{"start": 10.0, "end": 20.0, "text": "hello world",
                       "words": [{"text": "hello", "start": 0.0, "end": 0.5,
                                  "confidence": 1.0}]}],
    }), encoding="utf-8")
    _real_render_pair(project)
    return project, fp


def _run(tmp_path, fp):
    """Run with the expensive parts stubbed, returning the render mock so a
    test can ask whether the cache was taken or missed. Caption generation is
    left real: the candidate carries word timings, and the documents it writes
    are what the cache key now digests — stubbing them would test nothing."""
    with patch("selekyt.pipeline.download"), \
         patch("selekyt.pipeline.render_clip") as mock_render, \
         patch("selekyt.pipeline.write_report"), \
         patch("selekyt.pipeline.make_thoughts",
               return_value=[__import__("models.core", fromlist=["Segment"]).Segment(
                   10, 20, "hello", [])]), \
         patch("selekyt.pipeline.rank", return_value=[_fake_candidate()]), \
         patch("selekyt.pipeline.validate", return_value=(True, "ok")), \
         patch("selekyt.pipeline.caption_metadata", return_value={}), \
         patch("selekyt.pipeline.build_content_pack", return_value={"pack": True}), \
         patch("selekyt.pipeline.attach_metadata", side_effect=lambda c, *a, **kw: c), \
         patch("selekyt.pipeline.aggregate_pacing_report", return_value={}):
        import selekyt.pipeline as p
        with patch.object(p, "_fingerprint", return_value=fp):
            p.run("https://example.com/v", tmp_path, model="small")
    return mock_render


def test_all_caption_presets_emit_distinct_ass():
    """The premise of the fix: a style change really does change the captions,
    so keying the cache on it is guarding a material difference."""
    from engine.captions import ass_for_candidate, caption_presets

    cand = _fake_candidate()
    hashes = set()
    for name in caption_presets():
        import hashlib
        import tempfile

        d = tempfile.mkdtemp()
        path = Path(d) / "t.ass"
        with patch.dict("os.environ", {"SELEKYT_CAPTION_STYLE": name}):
            ass_for_candidate(cand, path)
        hashes.add(hashlib.sha256(path.read_bytes()).hexdigest())
    assert len(hashes) == len(caption_presets())


def test_a_style_change_invalidates_the_render_cache(tmp_path, monkeypatch):
    """The regression. A cached render under one style must not be reused under
    another — the MP4 carries the old style burned in."""
    project, fp = _scaffold(tmp_path, monkeypatch, cached_style="minimal")
    monkeypatch.setenv("SELEKYT_CAPTION_STYLE", "punch")
    assert style_name() == "punch"
    mock_render = _run(tmp_path, fp)
    mock_render.assert_called(), (
        "a render cached under 'minimal' must be re-rendered under 'punch', or "
        "the shipped MP4 keeps the old style while its sidecar is rewritten")


def test_the_same_style_reuses_the_render_cache(tmp_path, monkeypatch):
    """The fix must not cost a re-render when nothing changed."""
    project, fp = _scaffold(tmp_path, monkeypatch, cached_style="minimal")
    assert style_name() == "minimal"
    mock_render = _run(tmp_path, fp)
    mock_render.assert_not_called()


def test_a_record_without_a_style_is_a_miss_not_a_match(tmp_path, monkeypatch):
    """Every ``pipeline_state.json`` written before this fix has no
    ``caption_style`` key. Treating its absence as ``minimal`` would keep the
    whole corpus on a cache key the fix never validated, so absence reads as
    unknown and the clip re-renders once."""
    project, fp = _scaffold(tmp_path, monkeypatch, cached_style=None)
    monkeypatch.setenv("SELEKYT_CAPTION_STYLE", "minimal")
    mock_render = _run(tmp_path, fp)
    mock_render.assert_called()


def test_an_edited_ass_invalidates_the_render_cache(tmp_path, monkeypatch):
    """The other half of the round trip. An editor's cue move must reach the
    render, not just survive on disk: the shipped MP4 carries the captions
    burned in, so a cached render made under the old document is a render of
    the wrong captions."""
    project, fp = _scaffold(tmp_path, monkeypatch, cached_style="minimal")
    assert style_name() == "minimal"
    ass = project / "captions" / "01.ass"
    kept = ass.read_text(encoding="utf-8")
    edited = kept.replace("Dialogue: 0,", "Dialogue: 1,")
    assert edited != kept, "the edit must actually change the document"
    ass.write_text(edited, encoding="utf-8")

    mock_render = _run(tmp_path, fp)
    mock_render.assert_called(), (
        "a render cached under the pre-edit ASS must be re-rendered after the "
        "edit, or the shipped MP4 keeps the old cue burned in while the "
        "document beside it describes the new one")


def test_the_cache_record_carries_the_style_that_was_burned_in(tmp_path, monkeypatch):
    """So a later run can tell what it is looking at."""
    project, fp = _scaffold(tmp_path, monkeypatch, cached_style=None)
    monkeypatch.setenv("SELEKYT_CAPTION_STYLE", "noir")
    _run(tmp_path, fp)
    record = json.loads((project / "pipeline_state.json").read_text())["renders"]["01"]
    assert record["caption_style"] == "noir"
    assert record["ass_digest"] == _digest(ass_content(_fake_candidate())), (
        "the record must name the ASS that was actually burned into the MP4, "
        "or a later edit to that document cannot be detected")
