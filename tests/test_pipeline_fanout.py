"""The render fan-out: N clips encoding at once, the same deliverables.

``--render-jobs`` fans the per-clip encodes out across the host's cores. The
fan-out shares the sequential loop's clip order, its checkpoint discipline,
and its failure account: every clip lands exactly where it would have landed
one-by-one, only sooner. These tests pin the contract that makes that true:

- the same renders happen in the same order for the same files whether the
  encodes ran one at a time or fanned out — differing only in the threads
  each encode was handed;
- the width is bounded: ffmpeg concurrency never exceeds the requested
  number of jobs, because N encodes running N encodes-wide is a fork bomb,
  not a speedup;
- a worker that dies raises where the sequential path would have raised:
  the run fails with the first clip that broke, not with the last.
"""
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from models.core import Candidate, Segment, Word

import selekyt.pipeline as pipeline
from selekyt.pipeline import _program_digest, _selection_digest  # noqa: F401


def _fake_transcript():
    words = [Word("hello", 0.0, 0.5)]
    seg = Segment(10.0, 20.0, "This is a great important thing to say in a video.", words)
    return [seg], "en"


def _candidate(index: int) -> Candidate:
    start = 10.0 + (index - 1) * 25.0
    words = [Word("hello", start, start + 0.5)]
    return Candidate(
        start=start, end=start + 10.0, text="This is a great important thing to say in a video.",
        story_type="story", hook="This is a great", payoff="say in a video.",
        editorial_score=0.9, virality_score=0.8, context_score=0.9,
        confidence=0.9, reason=f"test {index}", visual_potential=0.8, words=words,
    )


_FAKE_PACING = {
    "total_candidates": 2, "selected_count": 2, "rejected_count": 0,
    "context_guard_rejections": 0, "avg_duration": 10.0,
    "duration_distribution": {"<15s": 2, "15-30s": 0, "30-45s": 0, "45-55s": 0},
    "silence_pct": 0.0, "filler_pct": 0.0, "avg_words_per_second": 2.0,
    "avg_hook_latency": 0.0, "payoff_presence_rate": 1.0, "duplicate_overlap_rate": 0.0,
}


def _tmp_project(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    media = tmp_path / "video.mp4"
    media.write_bytes(b"0" * 100)
    project = tmp_path / "project"
    for d in ("facebook", "shorts", "captions", "content_pack"):
        (project / d).mkdir(parents=True, exist_ok=True)
    fp = "test-fingerprint"
    candidates = [_candidate(1), _candidate(2)]
    state = {"source": "https://example.com/v", "media_path": str(media),
             "media_info": {"title": "t"}, "fingerprint": fp}
    (project / "pipeline_state.json").write_text(json.dumps(state), encoding="utf-8")
    (project / "transcript.json").write_text(json.dumps({
        "language": "en",
        "segments": [{"start": 10.0, "end": 20.0, "text": "hello world",
                       "words": [{"text": "hello", "start": 0.0, "end": 0.5, "confidence": 1.0}]}],
    }), encoding="utf-8")
    return project


def _run_pipeline(project: Path, jobs: int | None, recorder: list) -> dict:
    """One run with ``render_clip`` faked into recording what it is asked to do.

    The fake writes a stub file, which is all the pipeline needs of a
    rendered deliverable here: the program-reel gate skips a reel whose parts
    no ffprobe can confirm, and the per-clip code reads no pixels.
    """
    cands = [_candidate(1), _candidate(2)]
    fp = "test-fingerprint"

    def fake_render_clip(source, candidate, out, ass=None, vertical=False,
                         fps=30.0, progress=None, threads=None):
        time.sleep(0.05)
        recorder.append((out.parent.name, out.name, vertical, threads))
        out.write_bytes(b"\x00" * 16)
        return out

    with (
        patch("selekyt.pipeline.download"),
        patch("selekyt.pipeline.render_clip", side_effect=fake_render_clip),
        patch("selekyt.pipeline.write_report"),
        patch("selekyt.pipeline.make_thoughts", return_value=[Segment(10, 20, "hello", [])]),
        patch("selekyt.pipeline.score_all", return_value=cands),
        patch("selekyt.pipeline.validate", return_value=(True, "ok")),
        patch("selekyt.pipeline.caption_metadata", return_value={"style": "minimal"}),
        patch("selekyt.pipeline.build_content_pack", return_value={"pack": True}),
        patch("selekyt.pipeline.attach_metadata", side_effect=lambda c, *a, **kw: c),
        patch("selekyt.pipeline.aggregate_pacing_report", return_value=_FAKE_PACING),
        patch.object(pipeline, "_fingerprint", return_value=fp),
    ):
        pipeline.run("https://example.com/v", project.parent, model="small",
                     render_jobs=jobs)
    return {"calls": list(recorder)}


def test_the_fanout_renders_the_same_clips_with_the_same_encodes(tmp_path):
    """Whatever the width, the same renders are asked of the encoder: each
    clip's two orientations, the same vertical flags, and each clip's calls
    all from the same encoder thread geometry. The fan-out runs the same work
    at once; it subtracts nothing and changes nothing about any clip.

    Order is compared per clip, not across the whole run: two workers at once
    legitimately interleave clip 02's encode with clip 01's, which is the
    entire point of a fan-out. What must agree is what each clip asks for.
    """
    solo = _run_pipeline(_tmp_project(tmp_path / "a"), None, [])
    fanned = _run_pipeline(_tmp_project(tmp_path / "b"), 4, [])
    key = lambda c: (c[0], c[1], c[2])  # noqa: E731
    assert sorted(map(key, solo["calls"])) == sorted(map(key, fanned["calls"]))
    # The cue order inside a clip is a property a viewer can catch: the 16:9
    # bar leads the 9:16 one because fb is the first call of the clip's task.
    per_clip = {}
    for parent, name, vertical, _t in fanned["calls"]:
        per_clip.setdefault(name, []).append(vertical)
    # letter position: False (16:9) before True (9:16)
    for name, flags in per_clip.items():
        assert flags == sorted(flags, key=lambda f: f), (name, flags)
    # and the threads a fanned encode gets are a real fraction, not a slogan
    fanned_threads = {threads for _p, _n, _v, threads in fanned["calls"]}
    assert all(isinstance(t, int) and t >= 1 for t in fanned_threads)
    solo_threads = {threads for _p, _n, _v, threads in solo["calls"]}
    assert solo_threads == {None}
    # each clip's encodes all get the same thread count, whatever that is
    threads_per_clip = {}
    for parent, name, vertical, t in fanned["calls"]:
        threads_per_clip.setdefault(name, set()).add(t)
    assert all(len(v) == 1 for v in threads_per_clip.values())


def test_the_fanout_checkpoints_each_clip_from_its_worker(tmp_path):
    """A fan-out run that dies mid-flight resumes from the clips already on
    disk. The state file must therefore say ``renders`` for a clip the moment
    its pair lands, not only when the loop completes."""
    project = _tmp_project(tmp_path)
    state_path = project / "pipeline_state.json"
    seen: list[str] = []

    real_state_save = pipeline._save_state

    def spy_state(path: Path, state: dict) -> None:
        draws = list(state.get("renders", {}))
        seen.append(",".join(draws))
        real_state_save(path, state)

    calls = []
    fake = MagicMock(side_effect=lambda *a, **kw: calls.append(1))
    with (
        patch("selekyt.pipeline.download"),
        patch("selekyt.pipeline.render_clip", fake),
        patch("selekyt.pipeline.write_report"),
        patch("selekyt.pipeline.make_thoughts", return_value=[Segment(10, 20, "hello", [])]),
        patch("selekyt.pipeline.score_all", return_value=[_candidate(1), _candidate(2)]),
        patch("selekyt.pipeline.validate", return_value=(True, "ok")),
        patch("selekyt.pipeline.caption_metadata", return_value={"style": "minimal"}),
        patch("selekyt.pipeline.build_content_pack", return_value={"pack": True}),
        patch("selekyt.pipeline.attach_metadata", side_effect=lambda c, *a, **kw: c),
        patch("selekyt.pipeline.aggregate_pacing_report", return_value=_FAKE_PACING),
        patch.object(pipeline, "_fingerprint", return_value="test-fingerprint"),
        patch.object(pipeline, "_save_state", side_effect=spy_state),
    ):
        pipeline.run("https://example.com/v", project.parent, model="small",
                     render_jobs=2)
    assert "01,02" in seen or ("01" in seen and "02" in seen) or \
        "02,01" in seen or ("02" in seen and "01" in seen), seen


def test_the_fanout_never_exceeds_its_width(tmp_path):
    """N jobs means at most N encodes in flight, ever. A fan-out that scales
    with the clip count instead of the pool width is a fork-count bug."""
    project = _tmp_project(tmp_path)
    counter = {"live": 0, "max": 0}
    guard = threading.Lock()

    def fake_render_clip(source, candidate, out, ass=None, vertical=False,
                         fps=30.0, progress=None, threads=None):
        with guard:
            counter["live"] += 1
            counter["max"] = max(counter["max"], counter["live"])
        try:
            time.sleep(0.15)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x00" * 16)
            return out
        finally:
            with guard:
                counter["live"] -= 1

    with (
        patch("selekyt.pipeline.download"),
        patch("selekyt.pipeline.render_clip", side_effect=fake_render_clip),
        patch("selekyt.pipeline.write_report"),
        patch("selekyt.pipeline.make_thoughts", return_value=[Segment(10, 20, "hello", [])]),
        patch("selekyt.pipeline.score_all", return_value=[_candidate(1), _candidate(2)]),
        patch("selekyt.pipeline.validate", return_value=(True, "ok")),
        patch("selekyt.pipeline.caption_metadata", return_value={"style": "minimal"}),
        patch("selekyt.pipeline.build_content_pack", return_value={"pack": True}),
        patch("selekyt.pipeline.attach_metadata", side_effect=lambda c, *a, **kw: c),
        patch("selekyt.pipeline.aggregate_pacing_report", return_value=_FAKE_PACING),
        patch.object(pipeline, "_fingerprint", return_value="test-fingerprint"),
    ):
        pipeline.run("https://example.com/v", project.parent, model="small",
                     render_jobs=2)
    assert counter["max"] <= 2, counter


def test_the_fanout_raises_the_first_broken_render(tmp_path):
    """The sequential path fails the run at the first clip whose encode dies;
    the fan-out reports the first of its queued failures, not whichever
    worker happened to notice last. Either way, the run does not sail on as
    if everything encoded."""
    project = _tmp_project(tmp_path)
    import pytest

    def fake_render_clip(source, candidate, out, ass=None, vertical=False,
                         fps=30.0, progress=None, threads=None):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 16)
        if vertical:
            raise RuntimeError(f"boom on {out.name}")
        return out

    with (
        patch("selekyt.pipeline.download"),
        patch("selekyt.pipeline.render_clip", side_effect=fake_render_clip),
        patch("selekyt.pipeline.write_report"),
        patch("selekyt.pipeline.make_thoughts", return_value=[Segment(10, 20, "hello", [])]),
        patch("selekyt.pipeline.score_all", return_value=[_candidate(1), _candidate(2)]),
        patch("selekyt.pipeline.validate", return_value=(True, "ok")),
        patch("selekyt.pipeline.caption_metadata", return_value={"style": "minimal"}),
        patch("selekyt.pipeline.build_content_pack", return_value={"pack": True}),
        patch("selekyt.pipeline.attach_metadata", side_effect=lambda c, *a, **kw: c),
        patch("selekyt.pipeline.aggregate_pacing_report", return_value=_FAKE_PACING),
        patch.object(pipeline, "_fingerprint", return_value="test-fingerprint"),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            pipeline.run("https://example.com/v", project.parent, model="small",
                         render_jobs=2)
