"""Multi-track model tests."""
import pytest
from engine.multitrack import (
    TimelineTrack, TrackInterval, MultiTrackProject,
    output_time_for_source, source_to_output_map,
)


def _track(name="main", lane=0, offset=0.0):
    return TimelineTrack(name=name, lane=lane, timecode_offset=offset)


def test_single_track_auto_positions():
    t = _track()
    t.add_interval(0.0, 1.0)
    t.add_interval(2.0, 3.0)
    assert len(t.intervals) == 2
    assert t.intervals[0].output_start == 0.0
    assert t.intervals[0].output_end == 1.0
    assert t.intervals[1].output_start == 1.0
    assert t.intervals[1].output_end == 2.0
    assert t.total_output_duration == 2.0


def test_track_offset():
    t = _track(offset=5.0)
    t.add_interval(0.0, 1.0)
    assert t.intervals[0].output_start == 5.0
    assert t.intervals[0].output_end == 6.0


def test_explicit_output_positions():
    t = _track()
    t.add_interval(0.0, 1.0, output_start=10.0, output_end=11.0)
    t.add_interval(1.0, 2.0, output_start=20.0, output_end=21.0)
    assert t.intervals[0].output_start == 10.0
    assert t.intervals[1].output_start == 20.0


def test_multi_track_timecode_alignment():
    t1 = _track("camera_a", lane=0, offset=0.0)
    t2 = _track("camera_b", lane=1, offset=0.0)
    t1.add_interval(0.0, 1.0)
    t2.add_interval(0.0, 1.0)
    project = MultiTrackProject([t1, t2], source_duration=10.0)
    assert project.output_duration == 1.0
    assert project.lane_count == 2


def test_validation_source_exceeded():
    t = _track()
    t.add_interval(0.0, 12.0)
    project = MultiTrackProject([t], source_duration=10.0)
    issues = project.validate()
    assert any("source duration" in msg for msg in issues)


def test_validation_overlapping_lanes():
    t1 = _track("a", lane=0)
    t2 = _track("b", lane=0)
    project = MultiTrackProject([t1, t2], source_duration=10.0)
    issues = project.validate()
    assert any("lane 0" in msg for msg in issues)


def test_validation_out_of_order():
    t = _track()
    t.add_interval(0.0, 1.0, output_start=5.0, output_end=6.0)
    t.add_interval(1.0, 2.0, output_start=0.0, output_end=1.0)
    project = MultiTrackProject([t], source_duration=10.0)
    issues = project.validate()
    assert any("out of order" in msg for msg in issues)


def test_validation_negative_start():
    t = _track()
    t.add_interval(-1.0, 1.0)
    project = MultiTrackProject([t], source_duration=10.0)
    issues = project.validate()
    assert any("before 0" in msg for msg in issues)


def test_empty_project():
    project = MultiTrackProject([], source_duration=10.0)
    assert project.output_duration == 0.0
    assert project.lane_count == 0
    assert project.validate() == []


def test_output_time_mapping():
    t = _track()
    t.add_interval(0.0, 1.0, output_start=0.0, output_end=1.0)
    t.add_interval(2.0, 3.0, output_start=1.0, output_end=2.0)
    assert output_time_for_source(t, 0.5) == 0.5
    assert output_time_for_source(t, 1.5) == 1.0   # removed gap → clamp to prev output end
    assert output_time_for_source(t, 2.5) == 1.5


def test_source_to_output_map():
    t = _track()
    t.add_interval(0.0, 1.0, output_start=0.0, output_end=1.0)
    t.add_interval(2.0, 3.0, output_start=1.0, output_end=2.0)
    mapping = source_to_output_map(t)
    assert mapping[0.0] == 0.0
    assert mapping[1.0] == 1.0
    assert mapping[2.0] == 1.0
    assert mapping[3.0] == 2.0


# ── FCPXML export conversion ──

def _cutlist():
    from engine.jumpcut import cutlist_from_wire

    return cutlist_from_wire(
        [
            {"start": 0.0, "end": 2.0, "kind": "keep"},
            {"start": 2.0, "end": 4.0, "kind": "remove"},
            {"start": 4.0, "end": 6.0, "kind": "keep"},
        ],
        10.0,
    )


def test_to_fcpxml_params_makes_the_first_track_the_reference():
    from engine.multitrack import multitrack_from_cutlist

    reference = _track("camera_a", lane=0, offset=0.0)
    b_roll = _track("camera_b", lane=1, offset=0.0)
    project = multitrack_from_cutlist(_cutlist(), [reference, b_roll])

    params = project.to_fcpxml_params(fps=25.0, title="shoot")
    assert params.reference.name == "camera_a"
    assert [t.name for t in params.linked] == ["camera_b"]
    # The reference defines project time, so it sits at 0.
    assert params.reference.offset == 0.0
    # Its keeps came from the cutlist.
    assert params.reference.duration > 0


def test_to_fcpxml_params_maps_timecode_offset_onto_export_offset():
    """A track that rolled 3s after the reference has timecode_offset 3.0,
    which the FCPXML emitter turns into the asset-clip offset (where its media
    begins in project time)."""
    from engine.multitrack import multitrack_from_cutlist

    project = multitrack_from_cutlist(
        _cutlist(),
        [_track("camera_a"), _track("lav", lane=-1, offset=3.0)],
    )
    params = project.to_fcpxml_params(fps=25.0)
    assert params.linked[0].offset == 3.0


def test_to_fcpxml_params_carries_media_metadata():
    from engine.multitrack import multitrack_from_cutlist

    project = multitrack_from_cutlist(_cutlist(), [_track("camera_a")])
    project.tracks[0].source_path = "/tmp/cam.mp4"
    project.tracks[0].start_timecode = "01:00:00;00"
    params = project.to_fcpxml_params(fps=29.97)
    assert params.reference.path == "/tmp/cam.mp4"
    assert params.reference.start_timecode == "01:00:00;00"


def test_to_fcpxml_params_refuses_a_nonzero_reference_offset():
    """The reference track's offset defines the timeline, not a placement, so
    a nonzero value is a modelling error rather than something to compensate."""
    from engine.multitrack import MultiTrackProject

    project = MultiTrackProject(
        [_track("camera_a", offset=5.0), _track("camera_b", lane=1, offset=0.0)],
        source_duration=10.0,
    )
    try:
        project.to_fcpxml_params(fps=25.0)
    except ValueError as exc:
        assert "reference" in str(exc).lower()
    else:
        raise AssertionError("a nonzero reference offset must be rejected")


def test_to_fcpxml_params_needs_at_least_one_track():
    from engine.multitrack import MultiTrackProject

    with pytest.raises(ValueError, match="no tracks"):
        MultiTrackProject([], source_duration=10.0).to_fcpxml_params(fps=25.0)


def test_multitrack_from_cutlist_reuses_existing_intervals():
    """A linked track that already carries intervals keeps them; the reference
    is seeded from the cutlist's keeps when empty."""
    from engine.multitrack import multitrack_from_cutlist

    reference = _track("camera_a")
    b_roll = _track("camera_b", lane=1)
    b_roll.add_interval(0.0, 2.0, output_start=0.0, output_end=2.0)
    project = multitrack_from_cutlist(_cutlist(), [reference, b_roll])
    assert len(project.tracks[0].intervals) == 2, "reference seeded from cutlist keeps"
    assert len(project.tracks[1].intervals) == 1, "b-roll intervals preserved"
    assert project.source_duration == 10.0


def test_media_extent_falls_back_to_the_furthest_read():
    t = _track("camera_a")
    t.add_interval(1.0, 4.0)
    t.add_interval(6.0, 8.5)
    assert t.media_extent == 8.5
    t.media_duration = 12.0
    assert t.media_extent == 12.0, "explicit metadata wins"