"""Multi-track model tests."""
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