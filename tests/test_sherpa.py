"""Sherpa-onnx transcription backend: repetition handling and model lookup.

The repetition merge and the model-file lookup are the two parts of this
backend that fail silently — a missed merge puts a duplicated sentence in
the transcript, and a mis-resolved model quietly downgrades "small" to
"base" without telling anyone. Both are pinned here.
"""
from pathlib import Path

from models.core import Segment

from providers.sherpa import (
    _is_section_marker,
    _is_whisper_repeat,
    _merge_segments,
    _model_files,
    _recognizer_for,
    _SIZE_TO_MODEL,
)


def _seg(start, end, text):
    return Segment(start, end, text, [])


def test_repeat_detection_identical():
    assert _is_whisper_repeat("The system shipped on time.", "The system shipped on time.")


def test_repeat_detection_prefix_variant():
    # The decoder re-emits with a varied tail; token overlap still catches it.
    assert _is_whisper_repeat(
        "The system shipped on time, Part 1.",
        "The system shipped on time.",
    )


def test_repeat_detection_ignores_unrelated():
    assert not _is_whisper_repeat(
        "The satellite network reached global coverage.",
        "Baking sourdough requires patience and warmth.",
    )


def test_merge_drops_back_to_back_repeat():
    segs = [
        _seg(0, 5, "The system shipped on time."),
        _seg(5, 10, "The system shipped on time."),
        _seg(10, 15, "Then the team celebrated."),
    ]
    merged = _merge_segments(segs)
    assert len(merged) == 2
    assert merged[0].end == 10.0


def test_merge_keeps_longer_wording():
    segs = [
        _seg(0, 5, "The system shipped on time."),
        _seg(5, 10, "The system shipped on time, as scheduled."),
    ]
    merged = _merge_segments(segs)
    assert len(merged) == 1
    assert "as scheduled" in merged[0].text


def test_merge_reunites_repeat_split_by_a_marker():
    # Whisper re-emits the chunk-boundary segment after a bare "Part 1.",
    # so the two copies are not adjacent. The merge must look past the marker.
    segs = [
        _seg(0, 12, "The satellite network reached global coverage in 1995."),
        _seg(12, 14, "Part 1."),
        _seg(14, 21, "The satellite network reached global coverage in 1995."),
        _seg(21, 28, "Coverage then expanded across the continent."),
    ]
    merged = _merge_segments(segs)
    texts = [s.text for s in merged]
    assert sum(1 for t in texts if t.startswith("The satellite network")) == 1
    assert merged[0].start == 0.0
    assert merged[0].end == 21.0


def test_merge_does_not_swallow_real_repetition():
    # A speaker genuinely repeating themselves later in the piece, separated
    # by other content, is not the artifact and must survive.
    segs = [
        _seg(0, 5, "The system shipped on time."),
        _seg(5, 30, "Many months passed and the team grew. New features landed."),
        _seg(30, 35, "The system shipped on time."),
    ]
    assert len(_merge_segments(segs)) == 3


def test_section_marker_detection():
    assert _is_section_marker("Part 2.")
    assert _is_section_marker("Chapter 10")
    assert _is_section_marker("భాగం 2")
    assert not _is_section_marker("The satellite network reached global coverage in 1995.")
    assert not _is_section_marker("Part of the system failed on Tuesday morning.")


def test_model_resolution_prefers_requested_size(tmp_path):
    # A tarball extracted without its wrapper leaves the files loose in the
    # models directory; the resolver must still find "small" there instead
    # of silently downgrading to base.
    models = tmp_path / "models"
    models.mkdir()
    for name in ("small-encoder.int8.onnx", "small-decoder.int8.onnx", "small-tokens.txt"):
        (models / name).write_bytes(b"")
    files = _model_files("small", models)
    assert files is not None
    assert all(f.parent == models for f in files)


def test_model_resolution_finds_subdirectory_layout(tmp_path):
    models = tmp_path / "models"
    sub = models / _SIZE_TO_MODEL["base"]
    sub.mkdir(parents=True)
    for name in ("base-encoder.int8.onnx", "base-decoder.int8.onnx", "base-tokens.txt"):
        (sub / name).write_bytes(b"")
    files = _model_files("base", models)
    assert files is not None
    assert all(f.parent == sub for f in files)


def test_model_resolution_falls_back_to_base(tmp_path):
    # "small" is absent, so the documented fallback to base kicks in.
    models = tmp_path / "models"
    sub = models / _SIZE_TO_MODEL["base"]
    sub.mkdir(parents=True)
    for name in ("base-encoder.int8.onnx", "base-decoder.int8.onnx", "base-tokens.txt"):
        (sub / name).write_bytes(b"")
    assert _model_files("small", models) is None
    assert _model_files("base", models) is not None


def test_recognizer_errors_when_nothing_exists(tmp_path):
    models = tmp_path / "empty"
    models.mkdir()
    try:
        _recognizer_for("small", "en", models)
    except RuntimeError as exc:
        assert "sherpa-onnx" in str(exc)
    else:
        raise AssertionError("a missing model must raise, not silently downgrade")
