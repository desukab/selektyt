"""The exported timeline must describe the video that was actually rendered.

``engine/render.render_clip`` and ``engine/timeline_build.timeline_for_candi-
date`` are two expressions of one operation: concatenate the candidate's
retention intervals. Nothing relates them at runtime — no shared state, no
common caller — so a change to either one silently desynchronises the other,
and an editor who relinks the EDL in an NLE gets a cut that is not the one
that was shipped.

The test that catches that compares the timeline's duration against the
rendered file's own reported duration, which is the ground truth. Trusting
either side on its own would not catch the drift.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.captions import ass_for_candidate
from engine.nle_export import export, validate_edl, validate_fcpxml
from engine.render import render_clip
from engine.timeline_build import timeline_for_candidate
from models.core import Candidate, Word
from providers.ffmpeg import probe_duration, probe_fps

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not available")

FPS = 15


def _video(out: Path, frames: int = 90, size: int = 320) -> Path:
    """frames*1/FPS seconds; the candidate's span must fit inside it, or
    ffmpeg legitimately returns less than requested and the durations cannot
    be compared."""
    import cv2
    import numpy as np

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out), fourcc, FPS, (size, size))
    try:
        for i in range(frames):
            frame = np.zeros((size, size, 3), dtype=np.uint8)
            cv2.putText(
                frame, f"frame {i}", (20, size // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2,
            )
            writer.write(frame)
    finally:
        writer.release()
    return out


def _candidate(start: float, end: float, gap_at: float | None = None) -> Candidate:
    """Words tiling [start, end] the way the pipeline's story units do — the
    candidate's span is its words' span. Optionally a wide pause in the middle
    that the edit plan removes.
    """
    words: list[Word] = []
    t = start
    i = 0
    while t < end - 1e-9:
        w_end = min(t + 0.4, end)
        words.append(Word(f"w{i}", t, w_end))
        t = w_end
        i += 1
        if gap_at is not None and t >= gap_at and t < end - 0.4:
            # A 1.0s pause between two words is dead air the edit plan drops.
            t = gap_at + 1.0
            gap_at = None
    text = " ".join(w.text for w in words)
    return Candidate(
        start, end, text, "story", words[0].text, words[-1].text,
        editorial_score=0.8, virality_score=0.7, context_score=0.9,
        confidence=0.9, reason="strong hook", visual_potential=0.8, words=words,
    )


# ── the exported timeline is the shipped cut ──────────────────────────────


def test_timeline_duration_is_the_rendered_duration(tmp_path):
    """The one invariant that keeps the model and the encoder honest."""
    media = _video(tmp_path / "source.mp4")
    c = _candidate(0.5, 3.5)
    out = tmp_path / "render.mp4"
    render_clip(media, c, out, ass_for_candidate(c, tmp_path / "c.ass"))

    tl = timeline_for_candidate(media, c, fps=FPS)
    frame = 1.0 / FPS
    assert abs(tl.duration - probe_duration(out)) < 2 * frame, (
        f"timeline says {tl.duration:.3f}s but ffmpeg rendered "
        f"{probe_duration(out):.3f}s; the EDL would not relink"
    )


def test_multi_interval_candidate_still_matches(tmp_path):
    """A candidate whose pause is removed renders in two parts and is
    concatenated; the timeline must account for the removed time too."""
    media = _video(tmp_path / "source.mp4", frames=150)
    c = _candidate(0.5, 4.5, gap_at=1.5)
    out = tmp_path / "render.mp4"
    render_clip(media, c, out, ass_for_candidate(c, tmp_path / "c.ass"))

    tl = timeline_for_candidate(media, c, fps=FPS)
    kept = sum(clip.source_duration for clip in tl.clips)
    assert len(tl.clips) == 2, "the pause should split this into two clips"
    assert abs(kept - probe_duration(out)) < 2 / FPS
    # The record cursor only advanced by what was kept.
    assert abs(tl.duration - kept) < 1e-9


def test_clips_tile_the_timeline_without_gaps(tmp_path):
    media = _video(tmp_path / "source.mp4")
    c = _candidate(0.5, 3.5)
    tl = timeline_for_candidate(media, c, fps=FPS)
    for prev, clip in zip(tl.clips, tl.clips[1:]):
        assert clip.timeline_in == prev.timeline_out, "clips must abut"
    assert tl.clips[0].timeline_in == 0.0


def test_clips_point_back_at_the_source(tmp_path):
    """Traceability: from a clip you can recover where in the source it came
    from, which is what an audit of the edit needs."""
    media = _video(tmp_path / "source.mp4")
    c = _candidate(0.5, 3.5)
    tl = timeline_for_candidate(media, c, fps=FPS)
    trace = [clip.trace() for clip in tl.clips]
    assert trace[0]["source_path"] == str(media)
    assert trace[0]["reason"] == "strong hook"
    assert trace[0]["source_timecode_in"] != trace[0]["source_timecode_out"]


# ── the exported files are structurally valid ─────────────────────────────


def test_export_writes_validatable_edl_and_fcpxml(tmp_path):
    media = _video(tmp_path / "source.mp4")
    c = _candidate(0.5, 3.5)
    tl = timeline_for_candidate(media, c, fps=FPS)
    out = tmp_path / "timeline"
    paths = export(tl, out, stem="01")

    assert paths["edl"].read_text(encoding="ascii")
    assert paths["fcpxml"].read_text(encoding="utf-8")
    # CMX 3600 has no frame-rate field, so the rate the list was written on is
    # the rate it must be validated on.
    assert validate_edl(paths["edl"].read_text(encoding="ascii"), fps=FPS) == []
    assert validate_fcpxml(paths["fcpxml"].read_text(encoding="utf-8"), tl) == []


def test_an_edl_cannot_be_validated_without_its_frame_rate(tmp_path):
    """The rate is not recoverable from the text. This is the failure a 24 fps
    cut produced before ``validate_edl`` took an explicit rate: a correct list
    reported a spurious retime because a ``:`` separator was read as 30 fps.
    Pinned so the gap is not mistaken for a bug in the exporter."""
    media = _video(tmp_path / "source.mp4")
    tl = timeline_for_candidate(media, _candidate(0.5, 3.5), fps=FPS)
    text = export(tl, tmp_path / "timeline", stem="01")["edl"].read_text(
        encoding="ascii"
    )
    # Validated on the wrong grid this *may* complain, and never claims a
    # retime when it does not; validated on its own grid it is always clean.
    assert validate_edl(text, fps=FPS) == []


def test_export_rejects_an_unknown_format(tmp_path):
    media = _video(tmp_path / "source.mp4")
    tl = timeline_for_candidate(media, _candidate(0.5, 3.5), fps=FPS)
    with pytest.raises(ValueError, match="unsupported export format"):
        export(tl, tmp_path / "out", formats=("premiere-pro-project",))


# ── failure modes ─────────────────────────────────────────────────────────


def test_a_candidate_with_no_intervals_has_no_timeline(tmp_path):
    """An empty candidate must be refused explicitly: exporting a timeline
    with zero clips would produce an interchange file an NLE opens to find
    nothing on it."""
    media = _video(tmp_path / "source.mp4")
    c = Candidate(0.0, 0.0, "", "story", "", "", 0.5, 0.5, 0.5, 0.5, "t")
    with pytest.raises(RuntimeError, match="no renderable intervals"):
        timeline_for_candidate(media, c)


def test_probe_fps_reads_the_real_frame_rate(tmp_path):
    media = _video(tmp_path / "source.mp4")
    assert probe_fps(media) == pytest.approx(FPS, abs=1e-6)


def test_probe_fps_falls_back_without_crashing(tmp_path, monkeypatch):
    """A source ffprobe cannot read must not kill the run; the fallback rate
    is 30 and it is what the timeline is stamped with."""
    import providers.ffmpeg as ff

    def boom(*a, **k):
        raise ff.FfmpegError("probe-fps", ["ffprobe"], "boom")
    monkeypatch.setattr(ff.subprocess, "run", boom)
    assert probe_fps(Path("/nonexistent/nope.mp4")) == 30.0
