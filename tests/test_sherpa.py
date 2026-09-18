"""Sherpa-onnx transcription backend: repetition handling and model lookup.

The repetition merge and the model-file lookup are the two parts of this
backend that fail silently — a missed merge puts a duplicated sentence in
the transcript, and a mis-resolved model quietly downgrades "small" to
"base" without telling anyone. Both are pinned here, along with the error
surface a host sees when the optional sherpa-onnx wheel or its model is
absent: that must name what to install rather than leaking a bare
ModuleNotFoundError out of a transcription run.
"""
import shutil
import sys
from pathlib import Path

import pytest

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


def test_recognizer_errors_when_library_missing(tmp_path, monkeypatch):
    # sherpa-onnx is an optional extra, so a host can legitimately lack the
    # wheel. That must surface as an actionable error naming the extra to
    # install, not a bare ModuleNotFoundError from inside a transcription run.
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)
    models = tmp_path / "models"
    models.mkdir()
    with pytest.raises(RuntimeError, match="sherpa-onnx") as excinfo:
        _recognizer_for("small", "en", models)
    # The ImportError is chained, not swallowed: the traceback still shows
    # what the interpreter failed to import.
    assert isinstance(excinfo.value.__cause__, ImportError)
    assert "sherpa_onnx" in str(excinfo.value.__cause__)


# ── F4: requested threads must reach the recognizer ──


class _FakeRecognizer:
    """Stands in for sherpa_onnx.OfflineRecognizer.

    sherpa-onnx is an optional extra and not installed on the CI host, but the
    thread-count contract is on our side of the boundary: it must survive
    ``_recognizer_for`` and land in the recognizer's kwargs.
    """

    instances: list["_FakeRecognizer"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeRecognizer.instances.append(self)


@pytest.fixture
def fake_sherpa(monkeypatch):
    """Install the stub wheel, keep the module name the backend imports."""
    import types

    stub = types.ModuleType("sherpa_onnx")
    _FakeRecognizer.instances.clear()

    class _Offline:
        @staticmethod
        def from_whisper(**kwargs):
            return _FakeRecognizer(**kwargs)

    stub.OfflineRecognizer = _Offline
    monkeypatch.setitem(sys.modules, "sherpa_onnx", stub)
    return stub


def test_requested_cpu_threads_reach_the_recognizer(tmp_path, fake_sherpa):
    """F4: the backend used to accept --cpu-threads and drop it on the floor."""
    models = tmp_path / "models"
    models.mkdir()
    for name in ("base-encoder.int8.onnx", "base-decoder.int8.onnx", "base-tokens.txt"):
        (models / name).write_bytes(b"")

    _recognizer_for("base", "en", models, num_threads=3)
    assert _FakeRecognizer.instances, "the recognizer was never constructed"
    assert _FakeRecognizer.instances[-1].kwargs["num_threads"] == 3


def test_unspecified_threads_get_a_bounded_default(tmp_path, fake_sherpa):
    """No thread count requested: fall back to a value capped well below the
    whole machine, never to None (which sherpa reads as 'use everything')."""
    models = tmp_path / "models"
    models.mkdir()
    for name in ("base-encoder.int8.onnx", "base-decoder.int8.onnx", "base-tokens.txt"):
        (models / name).write_bytes(b"")

    from providers.sherpa import _default_threads

    _recognizer_for("base", "en", models)
    used = _FakeRecognizer.instances[-1].kwargs["num_threads"]
    assert used == _default_threads()
    assert 1 <= used <= 4


# ── F4b: a flag that cannot apply must be declared, not silently accepted ──


def _pcm_wav(path: Path, seconds: float, rate: int = 16000) -> Path:
    """Silent 16 kHz mono PCM: enough windowing to span two whisper windows.

    ``transcribe`` reads this back through the windowed reader, so no audio
    decode happens and no model is needed.
    """
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"\x00\x00" * frames)
    return path


_HAVE_FFPROBE = shutil.which("ffprobe") is not None


@pytest.mark.skipif(not _HAVE_FFPROBE, reason="ffprobe needed for audio duration")
@pytest.mark.parametrize("chunk_minutes", [1, 5, 99])
def test_chunk_minutes_is_declared_not_applicable_not_silently_ignored(
        tmp_path, monkeypatch, chunk_minutes):
    """F4: sherpa's whisper window is fixed at 28 s, so --chunk-minutes cannot
    select a chunk size. Before the fix it was accepted anyway and the run
    looked as though it had honoured it. Now the provenance must say so."""
    import providers.sherpa as sh

    # Two 28 s windows plus change, so the chunk count is a real decision.
    media = tmp_path / "source.mp4"
    media.write_bytes(b"not real media; extraction is bypassed below")
    ckpt = tmp_path / "transcription"
    _pcm_wav(ckpt / "audio.wav", 60.0)

    monkeypatch.setattr(sh, "_recognizer_for",
                        lambda *a, **kw: (object(), "base", None))
    monkeypatch.setattr(
        sh, "_decode_chunk",
        lambda recognizer, samples, sr, offset, lang: (
            [Segment(round(offset, 3), round(offset + 5.0, 3),
                     f"window at {offset:.0f}", [])],
            lang,
        ),
    )

    segments, lang, provenance = sh.transcribe(
        media, model_name="base", language="en", checkpoint_dir=ckpt,
        chunk_minutes=chunk_minutes, cpu_threads=2,
    )

    assert lang == "en"
    assert provenance["num_threads"] == 2, "cpu_threads must reach provenance"
    assert provenance["model"] == "base"
    assert provenance["downgrade_reason"] is None
    # the requested value is named, and the window is what actually ran
    assert f"chunk_minutes={chunk_minutes}" in provenance["chunking"]
    assert "28s" in provenance["chunking"]
    # ceil(60 s / 28 s) windows, and that count is a function of the fixed
    # whisper window alone -- it must not move with chunk_minutes
    assert len(segments) == 3
    assert [s.start for s in segments] == [0.0, 28.0, 56.0]


@pytest.mark.skipif(not _HAVE_FFPROBE, reason="ffprobe needed for audio duration")
def test_requested_threads_appear_in_the_run_log(tmp_path, monkeypatch, capsys):
    """The operator's --cpu-threads is echoed on stdout next to the chunk plan,
    so a mis-set value is visible before anyone reads the provenance."""
    import providers.sherpa as sh

    media = tmp_path / "source.mp4"
    media.write_bytes(b"bypassed")
    ckpt = tmp_path / "transcription"
    _pcm_wav(ckpt / "audio.wav", 60.0)
    monkeypatch.setattr(sh, "_recognizer_for",
                        lambda *a, **kw: (object(), "base", None))
    monkeypatch.setattr(sh, "_decode_chunk",
                        lambda *a, **kw: ([], "en"))

    sh.transcribe(media, model_name="base", checkpoint_dir=ckpt, cpu_threads=3)
    assert "threads=3" in capsys.readouterr().out

