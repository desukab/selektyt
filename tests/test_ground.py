"""GROUND: retrieved intervals decoded into source frames.

What these tests are for
------------------------

``engine.ground`` is the bridge between the temporal answer retrieval produces
and the pixels a later stage would reason about. The contract under test is
narrow and it is the whole point of the module:

- the intervals handed in decide exactly which frames come out — nothing wider;
- every frame carries the source time it was requested at;
- a frame is a decoded observation, never an interpretation, and the claim says
  so in words;
- a failure to decode is reported, never returned as an empty success.

Most assertions are checked against the *pixels* rather than the strings. The
fixture is a grey ramp whose brightness encodes the frame index, so a test can
convert a decoded frame's mean back to a frame number and confirm the seek
landed where the timestamp says it did. Trusting the module's own bookkeeping
here would test nothing: the field that has to be right is the one ffmpeg wrote.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.ground import (
    SCHEMA_VERSION,
    GroundResult,
    MAX_FRAMES_DEFAULT,
    MIN_SAMPLE_GAP_S,
    fit_to_stream,
    ground,
    sample_times,
)
from engine.memory.evidence import Evidence, Modality, Source
from engine.memory.intervals import Interval, IntervalSet

FPS = 10.0
FRAMES = 60
BASE_BRIGHTNESS = 20
BRIGHTNESS_STEP = 4


def _ramp_video(out: Path, frames: int = FRAMES, size: int = 160) -> Path:
    """A grey ramp: frame ``i`` is uniform ``BASE_BRIGHTNESS + i * STEP``.

    Brightness is the frame index, written in pixels. Decoding a frame and
    reading its mean recovers which frame ffmpeg selected, which is how a test
    verifies a timestamp without trusting the module's arithmetic.
    """
    import cv2
    import numpy as np

    out.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(out), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (size, size)
    )
    try:
        for i in range(frames):
            level = min(255, BASE_BRIGHTNESS + i * BRIGHTNESS_STEP)
            writer.write(np.full((size, size, 3), level, dtype=np.uint8))
    finally:
        writer.release()
    return out


def _frame_index(path: str) -> float:
    """Which source frame a decoded JPEG shows, recovered from its brightness."""
    import cv2

    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    assert image is not None, f"frame {path} is not readable as an image"
    return (float(image.mean()) - BASE_BRIGHTNESS) / BRIGHTNESS_STEP


def _frame_time(path: str) -> float:
    """The source time of a decoded frame, recovered from its pixels."""
    return _frame_index(path) / FPS


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("ground-media") / "ramp.mp4"
    return _ramp_video(out)


@pytest.fixture
def frames_dir(tmp_path: Path) -> Path:
    return tmp_path / "ground" / "frames"


# ── 1. A valid interval produces visual evidence ──────────────────────────────


def test_a_valid_interval_produces_visual_evidence(video: Path, frames_dir: Path):
    result = ground([(1.0, 3.0)], video, out_dir=frames_dir, duration=6.0)

    assert not result.is_empty, "a valid interval must ground something"
    assert len(result.evidence) == 1
    assert not result.failures, result.report

    item = result.evidence[0]
    assert item.modality is Modality.VISUAL
    assert item.source == Source(
        module="engine.ground",
        method="sample_window",
        params_digest=Source.of(
            "engine.ground",
            "sample_window",
            max_frames=MAX_FRAMES_DEFAULT,
            min_gap_s=MIN_SAMPLE_GAP_S,
            jpeg_quality=3,
        ).params_digest,
    )
    assert item.grounded is True, "a decoded frame is directly observed"
    assert item.confidence == 1.0
    assert item.intervals == IntervalSet.spanning(1.0, 3.0)
    assert all(Path(a).is_file() and Path(a).stat().st_size > 0 for a in item.artifacts)


def test_the_claim_is_an_observation_not_an_interpretation(video: Path, frames_dir: Path):
    """The one semantic rule of this module, pinned on its own words."""
    result = ground([(1.0, 3.0)], video, out_dir=frames_dir, duration=6.0)
    claim = result.evidence[0].claim
    rationale = result.evidence[0].rationale

    assert "sampled visual observation" in claim
    assert "1.000s" in claim and "3.000s" in claim
    for banned in ("person", "face", "shows", "depicts", "contains"):
        assert banned not in claim.lower(), f"a ground claim must not say {banned!r}"
    assert "not an interpretation" in rationale
    assert "no vision model has evaluated" in rationale


# ── 2. Multiple intervals keep their provenance ───────────────────────────────


def test_disjoint_intervals_stay_disjoint(video: Path, frames_dir: Path):
    result = ground([(1.0, 2.0), (4.0, 5.0)], video, out_dir=frames_dir, duration=6.0)

    assert len(result.intervals) == 2
    assert [(g.start, g.end) for g in result.intervals] == [(1.0, 2.0), (4.0, 5.0)]
    assert len(result.evidence) == 2
    # Provenance is per interval: each item names its own window and only its
    # own frames.
    for item, span in zip(result.evidence, result.intervals):
        assert item.intervals == IntervalSet.spanning(span.start, span.end)
        assert len(item.artifacts) == len(span.frames)
        assert all(str(frames_dir) in a for a in item.artifacts)


def test_overlapping_intervals_merge_into_one_decode(video: Path, frames_dir: Path):
    """Two windows that touch are one decode, not two evidence items for one
    observation."""
    result = ground([(1.0, 2.5), (2.0, 4.0)], video, out_dir=frames_dir, duration=6.0)

    assert len(result.intervals) == 1
    grounded = result.intervals[0]
    assert (grounded.start, grounded.end) == (1.0, 4.0)
    assert len(result.evidence) == 1
    assert result.metrics.decoded_frames == 3
    # Every frame landed inside the merged window.
    for frame in grounded.frames:
        assert grounded.start <= frame.time <= grounded.end


# ── 3. Out-of-range intervals are clamped or rejected, deterministically ──────


def test_an_interval_overhanging_the_end_is_clamped(video: Path, frames_dir: Path):
    result = ground([(4.0, 9.0)], video, out_dir=frames_dir, duration=6.0)

    grounded = result.intervals[0]
    assert (grounded.start, grounded.end) == (4.0, 6.0)
    assert grounded.clamped is True
    assert grounded.requested == (4.0, 9.0), "the original ask must survive the clamp"
    assert "clamped" in grounded.note
    assert not result.failures, result.report
    assert all(f.time <= 6.0 for f in grounded.frames)


def test_an_interval_entirely_past_the_end_is_rejected(video: Path, frames_dir: Path):
    result = ground([(7.0, 9.0)], video, out_dir=frames_dir, duration=6.0)

    assert result.is_empty
    assert result.intervals == ()
    assert len(result.failures) == 1
    assert "past the end" in result.failures[0]


def test_clamping_is_deterministic(video: Path, frames_dir: Path):
    first = ground([(4.0, 9.0)], video, out_dir=frames_dir, duration=6.0)
    second = ground([(4.0, 9.0)], video, out_dir=frames_dir, duration=6.0)
    assert [f.time for g in first.intervals for f in g.frames] == [
        f.time for g in second.intervals for f in g.frames
    ]


# ── 4. Malformed intervals are rejected, not repaired ─────────────────────────


@pytest.mark.parametrize(
    "pair, reason",
    [
        ((float("nan"), 2.0), "not finite"),
        ((1.0, float("nan")), "not finite"),
        ((float("inf"), 2.0), "not finite"),
        ((-1.0, 2.0), "before the start of the source"),
        ((3.0, 1.0), "zero or negative length"),
        ((2.0, 2.0), "zero or negative length"),
    ],
)
def test_malformed_intervals_are_rejected_by_name(
    video: Path, frames_dir: Path, pair, reason
):
    result = ground([pair], video, out_dir=frames_dir, duration=6.0)
    assert result.is_empty
    assert len(result.failures) == 1
    assert reason in result.failures[0]


def test_malformed_input_does_not_silently_become_empty(video: Path, frames_dir: Path):
    """A reject-and-continue call reports what it dropped and grounds what is left."""
    result = ground(
        [(-1.0, 2.0), (1.0, 2.0), (3.0, 1.0)], video, out_dir=frames_dir, duration=6.0
    )
    assert len(result.failures) == 2
    assert len(result.evidence) == 1
    assert result.evidence[0].intervals == IntervalSet.spanning(1.0, 2.0)


def test_unreadable_input_shapes_are_reported(video: Path, frames_dir: Path):
    result = ground(["not-an-interval"], video, out_dir=frames_dir, duration=6.0)
    assert result.is_empty
    assert "not an interval" in result.failures[0]


# ── 5. Very short intervals do not produce duplicate frames ──────────────────


@pytest.mark.parametrize("duration", [0.01, 0.02])
def test_a_sub_frame_interval_samples_once(video: Path, frames_dir: Path, duration):
    """Three candidates inside one frame period are one frame, not three writes
    of the identical frame."""
    result = ground([(1.0, 1.0 + duration)], video, out_dir=frames_dir, duration=6.0)
    grounded = result.intervals[0]
    assert len(grounded.frames) == 1
    assert "dropped" in grounded.note


def test_a_near_frame_interval_never_repeats_a_frame(video: Path, frames_dir: Path):
    """The dedup bound is the frame period, not 'one sample per interval'."""
    result = ground([(1.0, 1.05)], video, out_dir=frames_dir, duration=6.0)
    times = [f.time for f in result.intervals[0].frames]
    assert len(times) == 2
    assert all(b - a >= MIN_SAMPLE_GAP_S for a, b in zip(times, times[1:]))


def test_adjacent_frames_are_not_deduplicated(video: Path, frames_dir: Path):
    """The dedup is a sub-frame-period rule, not a 'one frame per interval' rule."""
    result = ground([(1.0, 1.3)], video, out_dir=frames_dir, duration=6.0)
    times = [f.time for f in result.intervals[0].frames]
    assert len(times) == 3
    assert all(b - a >= MIN_SAMPLE_GAP_S for a, b in zip(times, times[1:]))


# ── 6. Timestamps are accurate — verified against the pixels ──────────────────


def test_every_frame_lands_on_the_frame_at_or_after_its_timestamp(
    video: Path, frames_dir: Path
):
    """The timestamp is the *requested* time and the pixels confirm ffmpeg chose
    the frame at or after it — which is the documented seek semantics. The check
    is against the recovered frame *index* (an integer), which is exact; the
    time derived from it carries the lossy JPEG's noise, so the tolerance below
    is a fraction of the frame period rather than zero."""
    result = ground([(1.0, 4.0)], video, out_dir=frames_dir, duration=6.0)
    grounded = result.intervals[0]
    for frame in grounded.frames:
        index = _frame_index(frame.path)
        # The frame ffmpeg chose is the one at or after the requested instant:
        # the index is the requested time in frames, rounded up.
        assert index >= frame.time * FPS - 0.5, (
            f"claimed {frame.time:.3f}s but the pixels show frame {index:.2f}"
        )
        assert index < (frame.time + 1.0 / FPS) * FPS + 0.5


def test_recovered_frame_times_agree_with_the_recorded_timestamps(
    video: Path, frames_dir: Path
):
    """The pixel-derived time matches the recorded timestamp to within one frame
    period — a redundant but independent check that the timestamp on the object
    is the time that was actually sought."""
    result = ground([(1.0, 4.0)], video, out_dir=frames_dir, duration=6.0)
    for frame in result.intervals[0].frames:
        assert abs(_frame_time(frame.path) - frame.time) < 1.0 / FPS


def test_the_sampled_times_are_beginning_end_and_middle(video: Path, frames_dir: Path):
    result = ground([(1.0, 4.0)], video, out_dir=frames_dir, duration=6.0)
    assert sorted(f.time for f in result.intervals[0].frames) == pytest.approx(
        [1.0, 2.5, 4.0]
    )


# ── 7. No frame comes from outside the requested interval ─────────────────────


def test_no_frame_outside_the_interval(video: Path, frames_dir: Path):
    result = ground([(2.0, 5.0)], video, out_dir=frames_dir, duration=6.0)
    grounded = result.intervals[0]
    for frame in grounded.frames:
        assert grounded.start <= frame.time <= grounded.end
        # And the pixels agree the frame came from inside the window, to within
        # the frame period the lossy JPEG's noise admits.
        assert _frame_time(frame.path) > grounded.start - 1.0 / FPS
        assert _frame_time(frame.path) < grounded.end + 1.0 / FPS


def test_ground_does_not_widen_the_answer(video: Path, frames_dir: Path):
    """The union GROUND receives is the union it grounds: no pad, no margin."""
    result = ground([(2.0, 3.0)], video, out_dir=frames_dir, duration=6.0)
    (grounded,) = result.intervals
    assert (grounded.start, grounded.end) == (2.0, 3.0)
    assert result.metrics.requested_duration_s == pytest.approx(1.0)


# ── 8. Failures are reported, never returned as empty success ─────────────────


def test_a_missing_decoder_is_reported(video: Path, frames_dir: Path, monkeypatch):
    monkeypatch.setattr(
        "engine.ground.require_ffmpeg",
        lambda: (_ for _ in ()).throw(RuntimeError("ffmpeg not found")),
    )
    result = ground([(1.0, 2.0)], video, out_dir=frames_dir, duration=6.0)

    assert result.is_empty
    assert all("decoder unavailable" in f for f in result.failures)
    assert "decoder unavailable" in result.report
    # And no frame files were written for a decode that never ran.
    assert not list(frames_dir.glob("*.jpg"))


def test_a_failed_decode_is_reported_per_frame(
    video: Path, frames_dir: Path, monkeypatch
):
    """A decode that yields nothing is a reported failure — it is not the same
    answer as 'nothing visual happened here'."""
    monkeypatch.setattr("engine.ground.decode_frame", lambda *a, **k: None)
    result = ground([(1.0, 2.0)], video, out_dir=frames_dir, duration=6.0)

    assert result.is_empty, "no frames decoded means no evidence"
    assert len(result.failures) == 3
    assert all("no decodable frame" in f for f in result.failures)
    assert len(result.intervals) == 1, "the interval is still grounded — empty"
    assert result.intervals[0].is_empty


def test_missing_media_produces_no_fabricated_evidence(frames_dir: Path, tmp_path):
    result = ground([(1.0, 2.0)], tmp_path / "does-not-exist.mp4", out_dir=frames_dir)

    assert result.is_empty
    assert result.evidence == ()
    assert "source unreadable" in result.report
    assert len(result.failures) >= 1
    assert not list(frames_dir.glob("*.jpg"))


def test_an_empty_request_is_an_empty_answer_not_an_error(video: Path, frames_dir: Path):
    result = ground([], video, out_dir=frames_dir, duration=6.0)
    assert result.is_empty
    assert not result.failures
    assert result.intervals == ()


# ── 9. The existing contracts still hold ──────────────────────────────────────


def test_evidence_round_trips_through_the_shared_contract(video: Path, frames_dir: Path):
    result = ground([(1.0, 3.0)], video, out_dir=frames_dir, duration=6.0)
    item = result.evidence[0]
    restored = Evidence.from_dict(item.to_dict())

    assert restored.claim == item.claim
    assert restored.intervals == item.intervals
    assert restored.modality is Modality.VISUAL
    assert restored.source == item.source
    assert restored.artifacts == item.artifacts
    assert restored.grounded is True


def test_the_result_is_serializable_and_reloadable(video: Path, frames_dir: Path, tmp_path):
    result = ground([(1.0, 3.0), (4.0, 5.5)], video, out_dir=frames_dir, duration=6.0)
    path = result.save(tmp_path / "ground.json")

    payload = json.loads(path.read_text())
    assert payload["schema"] == SCHEMA_VERSION

    back = GroundResult.load(path)
    assert len(back.evidence) == 2
    assert len(back.intervals) == 2
    assert back.n_frames == result.n_frames
    assert [g.start for g in back.intervals] == [1.0, 4.0]
    assert back.evidence[0].claim == result.evidence[0].claim
    assert back.evidence[0].modality is Modality.VISUAL
    assert back.metrics == result.metrics
    # The frame files survive serialization as paths, not as inlined blobs.
    assert all(Path(f["path"]).is_file() for g in back.intervals for f in
               [g.frames[0].to_dict()])


def test_evidence_records_into_the_shared_memory_store(video: Path, frames_dir: Path):
    """GROUND's output is shaped to be recorded, not converted."""
    from engine.memory.evidence import EvidenceSet
    from engine.memory.store import Level, SourceMeta, VideoMemory

    result = ground([(1.0, 3.0)], video, out_dir=frames_dir, duration=6.0)
    meta = SourceMeta(path=str(video), duration=6.0, fingerprint="ground-test")
    memory = VideoMemory.empty(meta).record(*result.evidence)

    assert len(memory.evidence()) == 1
    # The perception ladder already had a GROUND rung waiting for an instrument
    # at this level; the evidence lands on it by its own source, not by a
    # special case.
    assert memory.level_of(result.evidence[0]) is Level.GROUND
    assert not memory.at_level(Level.GROUND).is_empty
    assert EvidenceSet(items=result.evidence).intervals == IntervalSet.spanning(1.0, 3.0)


def test_the_retrieval_verdict_travels_onto_the_evidence(video: Path, frames_dir: Path):
    """retrieval-design.md §6.1: a grounded conclusion over a poor transcript
    must not be presented with the confidence of one over a usable transcript.

    GROUND's confidence is for the decode; the retrieval answer's own verdict —
    its report, which names the fitness and any channel it could not consult —
    rides along on the evidence so a consumer cannot misread the one as
    endorsement of the other.
    """
    from engine.retrieval import RetrievalResult

    verdict = (
        "kept 1 of 3 candidate windows after top-3 selection with NMS at 8s; "
        "transcript fitness poor (a verdict that must travel)"
    )
    result_set = RetrievalResult(
        intervals=IntervalSet.of(Interval(1.0, 2.0)),
        evidence=(),
        channels={"vad": False},
        report=verdict,
        query_terms=["hiring"],
    )
    grounded = ground(result_set, video, out_dir=frames_dir, duration=6.0)

    (item,) = grounded.evidence
    assert verdict in item.rationale, "the retrieval verdict must travel"
    assert "covers the decode only" in item.rationale
    # Confidence is unchanged: it describes the decode, which did succeed.
    assert item.confidence == 1.0


def test_no_verdict_when_the_input_has_no_report(video: Path, frames_dir: Path):
    """Raw pairs carry no verdict, and the rationale says nothing it cannot
    support."""
    grounded = ground([(1.0, 2.0)], video, out_dir=frames_dir, duration=6.0)
    assert "retrieval answer that selected these windows" not in grounded.evidence[0].rationale


# ── 10. A retrieval answer flows through GROUND end to end ────────────────────


def test_a_retrieval_result_is_accepted_directly(video: Path, frames_dir: Path):
    """GROUND takes the answer of record without the caller unpacking it."""
    from engine.retrieval import RetrievalResult

    result_set = RetrievalResult(
        intervals=IntervalSet.of(Interval(1.0, 2.0), Interval(4.0, 5.0)),
        evidence=(),
        channels={},
        report="",
        query_terms=["x"],
    )
    grounded = ground(result_set, video, out_dir=frames_dir, duration=6.0)

    assert [(g.start, g.end) for g in grounded.intervals] == [(1.0, 2.0), (4.0, 5.0)]
    assert all(e.modality is Modality.VISUAL for e in grounded.evidence)
    assert grounded.metrics.decoded_frames == 6


# ── 11. The cost of grounding is measured, not asserted ───────────────────────


def test_the_metrics_report_measures_the_real_cost(video: Path, frames_dir: Path):
    result = ground([(1.0, 3.0), (4.0, 5.0)], video, out_dir=frames_dir, duration=6.0)
    metrics = result.metrics

    assert metrics is not None
    assert metrics.source_duration_s == pytest.approx(6.0)
    assert metrics.requested_duration_s == pytest.approx(3.0)
    assert metrics.decoded_frames == 6
    assert metrics.elapsed_s > 0.0
    # Targeted decode: the requested window is a fraction of the source.
    assert metrics.requested_duration_s < metrics.source_duration_s
    assert "in " in result.report
    assert str(metrics.decoded_frames) in result.report


def test_grounding_reads_a_fraction_of_the_source_on_real_media(tmp_path):
    """The performance claim this module exists to make, measured on the
    committed corpus media rather than on a synthetic fixture."""
    media = Path("corpus/media/pentagon-esper-briefing.mp4")
    if not media.is_file():
        pytest.skip("the corpus media is not present in this checkout")

    result = ground(
        [(120.0, 135.0), (400.0, 420.0), (700.0, 730.0)],
        media,
        out_dir=tmp_path / "real-frames",
    )
    metrics = result.metrics
    assert metrics is not None

    requested = metrics.requested_duration_s
    total = metrics.source_duration_s
    print(
        f"\n[ground] {total:.1f}s source; grounded {requested:.1f}s "
        f"({100 * requested / total:.1f}% of the timeline) into "
        f"{metrics.decoded_frames} frames in {metrics.elapsed_s:.3f}s"
    )
    assert metrics.decoded_frames == 9
    # The whole point of targeted decode: 65s of a 1014s source is decoded,
    # never the rest.
    assert requested / total < 0.07
    assert not result.failures, result.report
    assert all(Path(a).is_file() for e in result.evidence for a in e.artifacts)


# ── 12. The sampling policy is pure and pinneable ─────────────────────────────


def test_the_policy_is_a_pure_function_of_the_bounds():
    assert sample_times(1.0, 4.0) == ((1.0, 2.5, 4.0), "sampled beginning, end, middle")
    # Priority is beginning, end, middle, so a two-frame budget gets the extent.
    assert sample_times(1.0, 4.0, max_frames=2)[0] == (1.0, 4.0)
    assert sample_times(1.0, 4.0, max_frames=1)[0] == (1.0,)
    # A zero-length interval is a single sample at its start, not an error.
    assert sample_times(2.0, 2.0)[0] == (2.0,)


def test_an_impossible_frame_budget_is_a_caller_bug():
    with pytest.raises(ValueError, match="at least 1"):
        sample_times(1.0, 4.0, max_frames=0)
    with pytest.raises(ValueError, match="at least 1"):
        ground([(1.0, 2.0)], "unused.mp4", out_dir="unused", max_frames=0)


def test_fit_to_stream_pulls_an_end_sample_onto_the_last_frame():
    # A 10 fps source of 60 frames: the last decodable instant is 5.9s, so a
    # sample at the container duration must come back inside it.
    times, note = fit_to_stream((5.0, 6.0, 5.999), total=6.0, fps=10.0)
    assert times == (5.0, 5.85, 5.85)
    assert "pulled" in note


def test_fit_to_stream_leaves_interior_samples_alone():
    times, note = fit_to_stream((1.0, 2.5, 4.0), total=6.0, fps=10.0)
    assert times == (1.0, 2.5, 4.0)
    assert note == ""


def test_fit_to_stream_is_a_no_op_without_a_frame_rate():
    assert fit_to_stream((5.0, 6.0), total=6.0, fps=0.0) == ((5.0, 6.0), "")
    assert fit_to_stream((5.0, 6.0), total=0.0, fps=10.0) == ((5.0, 6.0), "")


# ── 13. The frame files are the artifact contract ─────────────────────────────


def test_frame_files_are_named_by_interval_time_and_position(video: Path, frames_dir: Path):
    result = ground([(1.0, 4.0)], video, out_dir=frames_dir, duration=6.0)
    names = sorted(Path(f.path).name for f in result.intervals[0].frames)

    assert len(names) == 3
    # The name carries the interval index, the sample position and the time, so
    # a reader can order and locate a frame without opening it.
    assert all(n.startswith("ground_interval000_") for n in names)
    assert any("at_00002.500s" in n for n in names)
    assert all(n.endswith(".jpg") for n in names)


def test_the_dimensions_are_read_from_the_frame_not_assumed(
    video: Path, frames_dir: Path
):
    result = ground([(1.0, 2.0)], video, out_dir=frames_dir, duration=6.0)
    assert all((f.width, f.height) == (160, 160) for f in result.intervals[0].frames)


# ── 14. Existing retrieval behaviour is untouched ────────────────────────────


def test_a_real_retrieval_answer_flows_through_ground(tmp_path: Path):
    """The stage's reason for existing: a retrieval answer — temporal intervals
    plus TEXT evidence — goes in, and VISUAL evidence grounded in real frames
    comes out.

    Runs the real ``build_index`` / ``retrieve`` path over a transcript with
    word timestamps, so the ``RetrievalResult`` is the object the pipeline
    produces rather than one constructed for the test.
    """
    from models.core import Segment, Word
    from engine.retrieval import build_index, retrieve

    def w(text: str, start: float, end: float) -> Word:
        return Word(text, start, end)

    first = Segment(
        0.0,
        8.0,
        "We shipped the feature on Tuesday morning. The metric moved "
        "forty percent overnight. The team celebrated the launch.",
        [
            w("We", 0.0, 0.2), w("shipped", 0.2, 0.5), w("the", 0.5, 0.7),
            w("feature", 0.7, 1.1), w("on", 1.1, 1.3), w("Tuesday", 1.3, 1.7),
            w("morning", 1.7, 2.1), w("The", 2.1, 2.3), w("metric", 2.3, 2.7),
            w("moved", 2.7, 3.1),
        ],
    )
    second = Segment(
        20.0,
        28.0,
        "Hiring doubled the engineering team that year. Two interviewers "
        "joined every hiring panel.",
        [
            w("Hiring", 20.0, 20.4), w("doubled", 20.4, 20.9),
            w("the", 20.9, 21.1), w("engineering", 21.1, 21.8),
            w("team", 21.8, 22.2), w("that", 22.2, 22.4), w("year", 22.4, 22.8),
            w("Two", 22.8, 23.0), w("interviewers", 23.0, 23.7),
            w("joined", 23.7, 24.2), w("every", 24.2, 24.6),
            w("hiring", 24.6, 25.1), w("panel", 25.1, 28.0),
        ],
    )
    index = build_index(
        [first, second],
        language="en",
        source_fingerprint="ground-e2e",
    )
    result = retrieve(index, ["hiring"])
    assert not result.intervals.is_empty, "the transcript must answer 'hiring'"
    assert all(e.modality.name == "TEXT" for e in result.evidence)

    # The retrieval answer lives in the second story only.
    for span in result.intervals:
        assert span.start >= 20.0 - 0.5
        assert span.end <= 28.0 + 0.5

    media = _ramp_video(tmp_path / "source" / "story.mp4", frames=300)  # 30s
    grounded = ground(result, media, out_dir=tmp_path / "frames")

    assert not grounded.is_empty
    assert all(e.modality is Modality.VISUAL for e in grounded.evidence)
    assert grounded.metrics.decoded_frames > 0
    assert not grounded.failures, grounded.report

    # Every grounded window sits inside the answer retrieval returned, and
    # every frame sits inside its window — the chain is: source -> retrieved
    # interval -> grounded interval -> decoded frame.
    retrieved = result.intervals
    for span in grounded.intervals:
        assert retrieved.covers(Interval(span.start, span.end)), (
            f"grounded {span.start}-{span.end}s is outside the retrieved answer"
        )
        for frame in span.frames:
            assert span.start <= frame.time <= span.end
            assert Path(frame.path).is_file()


def test_retrieval_tests_still_answer_without_grounding():
    """GROUND imports nothing from retrieval at import time and changes nothing
    about how a query is answered."""
    from engine.retrieval import build_index, retrieve
    from models.core import Segment, Word

    segment = Segment(
        0.0,
        4.0,
        "The telescope observed the distant galaxy.",
        [Word("telescope", 0.0, 0.6), Word("observed", 0.6, 1.2),
         Word("distant", 1.2, 1.8), Word("galaxy", 1.8, 4.0)],
    )
    index = build_index([segment], language="en", source_fingerprint=" untouched")
    result = retrieve(index, ["telescope"])
    assert not result.intervals.is_empty
    assert all(e.modality.name == "TEXT" for e in result.evidence)
