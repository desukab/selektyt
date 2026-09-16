"""Deterministic cut/interval algebra tests.
Covers all edge cases: zero-length, overlapping, nested, adjacent,
out-of-bounds, pathological inputs. Every function is pure and testable."""
import pytest
from engine.cut_algebra import (
    merge_intervals, subtract_intervals, pad_intervals, trim_intervals,
    split_intervals, word_safe_filter, is_adjacent, is_nested,
    source_to_output_time, concat_kept_ranges, enforce_duration,
    remove_overlaps, tile_cutlist,
)


# ── merge_intervals ──

def test_merge_empty():
    assert merge_intervals([]) == []


def test_merge_single():
    assert merge_intervals([(1.0, 3.0)]) == [(1.0, 3.0)]


def test_merge_overlapping():
    assert merge_intervals([(1.0, 3.0), (2.0, 4.0)]) == [(1.0, 4.0)]


def test_merge_adjacent():
    assert merge_intervals([(1.0, 2.0), (2.0, 3.0)]) == [(1.0, 3.0)]


def test_merge_adjacent_with_gap_tolerance():
    assert merge_intervals([(1.0, 2.0), (2.0005, 3.0)], gap=0.001) == [(1.0, 3.0)]


def test_merge_nested():
    assert merge_intervals([(1.0, 5.0), (2.0, 3.0)]) == [(1.0, 5.0)]


def test_merge_unsorted():
    result = merge_intervals([(3.0, 4.0), (1.0, 2.5), (2.0, 3.5)])
    assert result == [(1.0, 4.0)]


def test_merge_no_touch():
    assert merge_intervals([(1.0, 2.0), (3.0, 4.0)]) == [(1.0, 2.0), (3.0, 4.0)]


def test_merge_gap_bigger_than_tolerance():
    assert merge_intervals([(1.0, 2.0), (2.5, 3.5)], gap=0.1) == [(1.0, 2.0), (2.5, 3.5)]


# ── subtract_intervals ──

def test_subtract_empty_base():
    assert subtract_intervals([], [(1.0, 2.0)]) == []


def test_subtract_empty_remove():
    assert subtract_intervals([(1.0, 3.0)], []) == [(1.0, 3.0)]


def test_subtract_no_overlap():
    assert subtract_intervals([(1.0, 3.0)], [(5.0, 7.0)]) == [(1.0, 3.0)]


def test_subtract_complete_containment():
    assert subtract_intervals([(1.0, 5.0)], [(2.0, 3.0)]) == [(1.0, 2.0), (3.0, 5.0)]


def test_subtract_left_edge():
    assert subtract_intervals([(1.0, 5.0)], [(0.0, 2.0)]) == [(2.0, 5.0)]


def test_subtract_right_edge():
    assert subtract_intervals([(1.0, 5.0)], [(4.0, 6.0)]) == [(1.0, 4.0)]


def test_subtract_exact_match():
    assert subtract_intervals([(1.0, 3.0)], [(1.0, 3.0)]) == []


def test_subtract_partial_left():
    assert subtract_intervals([(1.0, 5.0)], [(0.0, 3.0)]) == [(3.0, 5.0)]


def test_subtract_partial_right():
    assert subtract_intervals([(1.0, 5.0)], [(3.0, 7.0)]) == [(1.0, 3.0)]


def test_subtract_multiple_remove():
    base = [(0.0, 10.0)]
    remove = [(1.0, 2.0), (5.0, 6.0), (8.0, 9.0)]
    result = subtract_intervals(base, remove)
    assert result == [(0.0, 1.0), (2.0, 5.0), (6.0, 8.0), (9.0, 10.0)]


def test_subtract_overlapping_removes():
    base = [(0.0, 10.0)]
    remove = [(1.0, 3.0), (2.5, 5.0)]
    result = subtract_intervals(base, remove)
    assert result == [(0.0, 1.0), (5.0, 10.0)]


# ── pad_intervals ──

def test_pad_zero():
    assert pad_intervals([(1.0, 3.0)], 0.0) == [(1.0, 3.0)]


def test_pad_positive():
    assert pad_intervals([(1.0, 3.0)], 0.5) == [(0.5, 3.5)]


def test_pad_negative():
    assert pad_intervals([(1.0, 3.0)], -0.5) == [(1.5, 2.5)]


def test_pad_clamp_to_zero():
    result = pad_intervals([(0.2, 3.0)], 0.5, bounds=(0.0, 10.0))
    assert result[0][0] == 0.0


def test_pad_clamp_to_max():
    result = pad_intervals([(8.0, 9.5)], 0.5, bounds=(0.0, 10.0))
    assert result[0][1] == 10.0


def test_pad_with_bounds():
    result = pad_intervals([(0.1, 9.9)], 1.0, bounds=(0.0, 10.0))
    assert result == [(0.0, 10.0)]


def test_pad_multiple():
    result = pad_intervals([(1.0, 2.0), (5.0, 6.0)], 0.3)
    assert result == [(0.7, 2.3), (4.7, 6.3)]


# ── trim_intervals ──

def test_trim_positive():
    assert trim_intervals([(1.0, 5.0)], 0.5) == [(1.5, 4.5)]


def test_trim_negative():
    assert trim_intervals([(1.0, 5.0)], -0.5) == [(0.5, 5.5)]


def test_trim_exceeds_duration():
    result = trim_intervals([(1.0, 1.5)], 0.5)
    assert result == [] or result[0][1] <= result[0][0]


def test_trim_multiple():
    result = trim_intervals([(1.0, 3.0), (5.0, 8.0)], 0.25)
    assert result == [(1.25, 2.75), (5.25, 7.75)]


# ── split_intervals ──

def test_split_at_point():
    result = split_intervals([(1.0, 5.0)], [3.0])
    assert (1.0, 3.0) in result and (3.0, 5.0) in result


def test_split_at_multiple_points():
    result = split_intervals([(0.0, 10.0)], [2.0, 5.0, 8.0])
    assert len(result) == 4


def test_split_outside():
    result = split_intervals([(2.0, 4.0)], [0.0, 1.0])
    assert result == [(2.0, 4.0)]


def test_split_empty_points():
    assert split_intervals([(1.0, 5.0)], []) == [(1.0, 5.0)]


def test_split_at_boundary():
    result = split_intervals([(1.0, 5.0)], [1.0])
    assert len(result) == 1


def test_split_at_end():
    result = split_intervals([(1.0, 5.0)], [5.0])
    assert len(result) == 1


# ── word_safe_filter ──

def test_word_safe_filter_basic():
    words = [(0.0, 0.5, "hello"), (0.6, 1.0, "world"), (2.0, 2.5, "foo")]
    result = word_safe_filter([(0.3, 2.3)], words, margin=0.05)
    assert len(result) == 1


def test_word_safe_filter_extends_to_full_word():
    words = [(0.0, 0.5, "hello")]
    result = word_safe_filter([(0.1, 0.3)], words, margin=0.2)
    assert result == [(0.0, 0.5)]


def test_word_safe_filter_snaps_to_word_boundaries():
    words = [(1.0, 2.0, "hello"), (2.5, 3.5, "world")]
    result = word_safe_filter([(1.1, 3.4)], words, margin=0.5)
    assert result == [(1.0, 3.5)]


def test_word_safe_filter_no_snap_when_far():
    words = [(1.0, 2.0, "hello"), (5.0, 6.0, "world")]
    result = word_safe_filter([(1.3, 3.4)], words, margin=0.05)
    assert result == [(1.3, 3.4)]


def test_word_safe_filter_empty_words():
    result = word_safe_filter([(1.0, 3.0)], [], margin=0.05)
    assert result == [(1.0, 3.0)]


# ── is_adjacent ──

def test_adjacent_true():
    assert is_adjacent((1.0, 2.0), (2.0, 3.0))


def test_adjacent_with_tolerance():
    assert is_adjacent((1.0, 2.0), (2.0005, 3.0), tolerance=0.001)


def test_adjacent_false_gap():
    assert not is_adjacent((1.0, 2.0), (2.5, 3.5))


def test_adjacent_false_overlap():
    assert not is_adjacent((1.0, 2.5), (2.0, 3.0))


def test_adjacent_reversed():
    assert is_adjacent((2.0, 3.0), (1.0, 2.0))


# ── is_nested ──

def test_nested_true():
    assert is_nested((2.0, 3.0), (1.0, 5.0))


def test_nested_equal():
    assert is_nested((1.0, 5.0), (1.0, 5.0))


def test_nested_false_partial():
    assert not is_nested((1.0, 3.0), (2.0, 4.0))


def test_nested_false_outside():
    assert not is_nested((1.0, 6.0), (2.0, 5.0))


# ── source_to_output_time ──

def test_source_to_output_basic():
    kept = [(0.0, 2.0), (5.0, 8.0)]
    assert source_to_output_time(0.0, kept) == 0.0
    assert source_to_output_time(2.0, kept) == 2.0
    assert source_to_output_time(5.0, kept) == 2.0
    assert source_to_output_time(8.0, kept) == 5.0


def test_source_to_output_in_gap():
    kept = [(0.0, 2.0), (5.0, 8.0)]
    assert source_to_output_time(3.0, kept) == 2.0


def test_source_to_output_empty():
    assert source_to_output_time(5.0, []) == 0.0


def test_source_to_output_before_first():
    kept = [(2.0, 5.0)]
    assert source_to_output_time(0.0, kept) == 0.0


# ── concat_kept_ranges ──

def test_concat_basic():
    assert concat_kept_ranges([(0.0, 2.0), (5.0, 8.0)]) == 5.0


def test_concat_empty():
    assert concat_kept_ranges([]) == 0.0


def test_concat_single():
    assert concat_kept_ranges([(1.0, 4.0)]) == 3.0


# ── enforce_duration ──

def test_enforce_min_duration():
    result = enforce_duration([(0.0, 2.0), (5.0, 6.0), (8.0, 11.0)], min_dur=3.0)
    assert all(b - a >= 3.0 for a, b in result)


def test_enforce_max_duration():
    result = enforce_duration([(0.0, 10.0)], max_dur=5.0)
    assert all(b - a <= 5.0 for a, b in result)


def test_enforce_min_drops_short():
    result = enforce_duration([(0.0, 1.0), (5.0, 10.0)], min_dur=3.0)
    assert len(result) == 1 and result[0] == (5.0, 10.0)


def test_enforce_both():
    result = enforce_duration([(0.0, 1.0), (5.0, 15.0), (20.0, 22.0)], min_dur=3.0, max_dur=8.0)
    for a, b in result:
        assert 3.0 <= b - a <= 8.0


# ── remove_overlaps ──

def test_remove_overlaps_none():
    assert remove_overlaps([(1.0, 2.0), (3.0, 4.0)]) == [(1.0, 2.0), (3.0, 4.0)]


def test_remove_overlaps_partial():
    result = remove_overlaps([(1.0, 3.0), (2.0, 4.0)])
    assert len(result) == 1


def test_remove_overlaps_full():
    result = remove_overlaps([(1.0, 5.0), (2.0, 3.0)])
    assert result == [(1.0, 5.0)]


# ── tile_cutlist ──

def test_tile_cutlist_basic():
    keeps = [(1.0, 3.0), (5.0, 8.0)]
    result = tile_cutlist(keeps, total_duration=10.0)
    assert any(r[0] == 0.0 and r[1] == 1.0 for r in result)
    assert any(r[0] == 3.0 and r[1] == 5.0 for r in result)


def test_tile_cutlist_no_gaps():
    keeps = [(0.0, 10.0)]
    result = tile_cutlist(keeps, total_duration=10.0)
    assert result == []


def test_tile_cutlist_empty():
    result = tile_cutlist([], total_duration=10.0)
    assert len(result) == 1 and result[0] == (0.0, 10.0)


# ── pathological ──

def test_merge_zero_length():
    assert merge_intervals([(1.0, 1.0)]) == [(1.0, 1.0)]


def test_subtract_inverted_interval():
    result = subtract_intervals([(5.0, 10.0)], [(15.0, 12.0)])
    assert result == [(5.0, 10.0)]


def test_pad_zero_length():
    assert pad_intervals([(1.0, 1.0)], 0.5) == [(0.5, 1.5)]


def test_enforce_duration_all_too_short():
    result = enforce_duration([(0.0, 1.0), (2.0, 3.0)], min_dur=5.0)
    assert result == []


def test_concat_nested_intervals():
    result = concat_kept_ranges([(1.0, 5.0), (2.0, 3.0)])
    assert result == 4.0
