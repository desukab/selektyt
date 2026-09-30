"""Progress-plumbing tests: the numbers the panel shows are measured, not made.

The UI is a presentation layer over an event stream, and the stream's honesty is
a property of the plumbing rather than of the renderer: a chunk counter that
advances without a chunk decoding, or a part fraction that moves without a part
rendering, is a fake percentage no amount of rendering discipline can undo.
These exercise the providers and the render loop directly, with the heavy parts
stubbed, because that is where the counts originate.
"""
from __future__ import annotations

import io
import sys
import types
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _write_minimal_wav(path: Path, seconds: float = 0.6) -> None:
    frames = b"\x00\x00" * int(16000 * seconds)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(frames)


def _install_fake_faster_whisper(model_names: list[str]) -> None:
    mod = types.ModuleType("faster_whisper")

    class Factory:
        def __init__(self, name, **_kw):
            model_names.append(name)
            self.transcribe = self._transcribe

        def _transcribe(self, *args, **kwargs):
            seg = MagicMock()
            seg.start, seg.end, seg.text, seg.words = 0.0, 2.0, "test", []
            info = MagicMock()
            info.language = "en"
            return ([seg], info)

    mod.WhisperModel = Factory
    sys.modules["faster_whisper"] = mod


def _whisper_events(tmp_path: Path, duration: float, chunk_minutes: int) -> list[dict]:
    model_names: list[str] = []
    _install_fake_faster_whisper(model_names)
    events: list[dict] = []
    saved = sys.stdout
    sys.stdout = io.StringIO()

    def fake_ffmpeg(cmd, *_a, **_kw):
        target = Path(cmd[-1])
        src = next(Path(a) for a in cmd if str(a).endswith("audio.wav"))
        target.write_bytes(src.read_bytes())

    try:
        with patch("providers.whisper._duration", return_value=duration), \
                patch("providers.whisper._extract_audio") as extract, \
                patch("providers.whisper.subprocess.run", side_effect=fake_ffmpeg), \
                patch("providers.whisper._require"):
            extract.side_effect = lambda media, out: _write_minimal_wav(out)
            from providers.whisper import transcribe

            audio = tmp_path / "fake.wav"
            audio.write_bytes(b"x")
            transcribe(audio, model_name="small", chunk_minutes=chunk_minutes,
                       progress=events.append)
    finally:
        sys.stdout = saved
        sys.modules.pop("faster_whisper", None)
    return events


def test_whisper_reports_one_event_per_decoded_chunk(tmp_path):
    """One event per chunk, and the count each event carries is the chunk count
    the provider itself computed for this audio — not a count the display chose
    and not a tick of a clock."""
    events = _whisper_events(tmp_path, duration=300.0, chunk_minutes=1)
    count = events[0]["count"]
    assert [e["index"] for e in events] == list(range(1, count + 1))
    assert all(e["count"] == count for e in events)


def test_whisper_counts_cached_chunks_as_progress(tmp_path):
    """A resumed run skips a decode, but the chunk counter still moves past it:
    standing still would read as a stall on a resume."""
    events = _whisper_events(tmp_path, duration=180.0, chunk_minutes=1)
    count = events[0]["count"]
    assert [e["index"] for e in events] == list(range(1, count + 1))

    second: list[dict] = []
    saved = sys.stdout
    sys.stdout = io.StringIO()
    try:
        with patch("providers.whisper._duration", return_value=180.0), \
                patch("providers.whisper._extract_audio") as extract, \
                patch("providers.whisper.subprocess.run") as run, \
                patch("providers.whisper._require"):
            extract.side_effect = lambda media, out: _write_minimal_wav(out)
            from providers.whisper import transcribe

            transcribe(tmp_path / "fake.wav", model_name="small", chunk_minutes=1,
                       progress=second.append)
    finally:
        sys.stdout = saved
        sys.modules.pop("faster_whisper", None)
    assert [e["index"] for e in second] == list(range(1, count + 1))
    assert run.call_count == 0, "a cached chunk must not re-decode"


def test_whisper_eta_is_absent_until_a_running_average_exists(tmp_path):
    """The ETA needs at least three decoded chunks; before that it is None
    rather than a guess presented as a measurement."""
    events = _whisper_events(tmp_path, duration=120.0, chunk_minutes=1)
    assert len(events) >= 1
    assert all(e.get("eta") is None for e in events)


def test_whisper_progress_is_optional_and_never_changes_the_decode(tmp_path):
    """``progress=None`` is the path every non-CLI caller takes. Progress is
    purely observative: removing the sink leaves the segments, the language and
    the provider's own console output byte-for-byte the same, and raising a
    sink is not required for the decode to run."""
    from providers.whisper import transcribe

    def fake_ffmpeg(cmd, *_a, **_kw):
        target = Path(cmd[-1])
        src = next(Path(a) for a in cmd if str(a).endswith("audio.wav"))
        target.write_bytes(src.read_bytes())

    def run_with(sink, ckpt):
        _install_fake_faster_whisper([])
        captured = io.StringIO()
        saved = sys.stdout
        sys.stdout = captured
        try:
            with patch("providers.whisper._duration", return_value=120.0), \
                    patch("providers.whisper._extract_audio") as extract, \
                    patch("providers.whisper.subprocess.run",
                          side_effect=fake_ffmpeg), \
                    patch("providers.whisper._require"):
                extract.side_effect = lambda media, out: _write_minimal_wav(out)
                segments, lang, _prov = transcribe(
                    tmp_path / "fake.wav", model_name="small",
                    chunk_minutes=1, checkpoint_dir=ckpt, progress=sink)
        finally:
            sys.stdout = saved
            sys.modules.pop("faster_whisper", None)
        words = [(s.start, s.end, s.text) for s in segments]
        return words, lang, captured.getvalue()

    # With a sink attached, the chunk events flow and nothing is raised.
    sink_events: list[dict] = []
    words, lang, printed = run_with(sink_events.append, tmp_path / "ck1")
    assert sink_events, "the provider must emit progress when a sink is attached"

    # Without one the decode is the same decode: same words, same language, and
    # the same console output. Progress observes the work; it is not part of it.
    words2, lang2, printed2 = run_with(None, tmp_path / "ck2")
    assert (words2, lang2, printed2) == (words, lang, printed)


# ── sherpa ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_sherpa(monkeypatch):
    """A sherpa-onnx stand-in so the window loop runs without the binary."""
    stub = types.ModuleType("sherpa_onnx")

    class Recognizer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def create_recognizer(self, **kwargs):
            return MagicMock()

    stub.OfflineRecognizer = Recognizer
    monkeypatch.setitem(sys.modules, "sherpa_onnx", stub)
    return stub


def test_sherpa_reports_one_event_per_window(tmp_path, fake_sherpa, monkeypatch):
    """sherpa's window is fixed at 28 s, so the count follows the audio length
    and nothing else — the requested ``chunk_minutes`` is not a progress unit
    there."""
    import providers.sherpa as sherpa

    monkeypatch.setattr(sherpa, "_recognizer_for",
                        lambda *a, **k: (MagicMock(), "base", None))
    monkeypatch.setattr(sherpa, "_extract_audio",
                        lambda media, out: _write_minimal_wav(out, seconds=61.0))
    monkeypatch.setattr(sherpa, "_duration", lambda path: 61.0)

    class Reader:
        sample_rate = 16000

        def window(self, start, end):
            return [0] * (self.sample_rate * 5)

        def close(self):
            pass

    monkeypatch.setattr(sherpa, "_WindowedWav", lambda path: Reader())
    monkeypatch.setattr(sherpa, "_decode_chunk",
                        lambda recognizer, samples, sr, start, lang: ([], "en"))
    monkeypatch.setattr(sherpa, "_write_sherpa_checkpoint", lambda *a, **k: None)

    events: list[dict] = []
    saved = sys.stdout
    sys.stdout = io.StringIO()
    try:
        sherpa.transcribe(tmp_path / "fake.wav", model_name="base",
                          checkpoint_dir=tmp_path / "ckpt",
                          progress=events.append)
    finally:
        sys.stdout = saved
    # 61 s at a 28 s window is three windows.
    assert [e["index"] for e in events] == [1, 2, 3]
    assert all(e["count"] == 3 for e in events)


# ── render ──────────────────────────────────────────────────────────────────


_ARGS = ("story", "hook", "payoff", 1.0, 1.0, 1.0, 1.0, "reason")


def _candidate(start: float, end: float) -> "Candidate":
    from models.core import Candidate

    return Candidate(start, end, "text", *_ARGS)


def _stub_render(monkeypatch, intervals):
    """Make ``render_clip`` run its part loop without touching ffmpeg.

    Returns the list of ``_render_one`` calls so a test can tie the progress
    stream to actual work rather than to the code's own arithmetic.
    """
    from engine import render as render_mod

    calls: list[tuple] = []
    monkeypatch.setattr(render_mod, "require_ffmpeg", lambda: None)

    def fake_render_one(source, start, end, part, *_a, **_kw):
        calls.append((source, start, end))
        Path(part).write_bytes(b"")

    monkeypatch.setattr(render_mod, "_render_one", fake_render_one)
    monkeypatch.setattr(render_mod, "_has_audio", lambda path: False)
    monkeypatch.setattr(render_mod, "retention_intervals",
                        lambda c: intervals)
    # A multi-interval clip still concats through ffmpeg; the part loop is what
    # these tests measure, so the concat is stubbed to a plain file copy.
    monkeypatch.setattr(render_mod, "subprocess", _FakeConcat())
    return calls


class _FakeConcat:
    """``subprocess`` stand-in whose only job is to join the part files."""

    def run(self, cmd, *_, **__):
        # ``-i <concat> ... <out>`` — out is the last argument.
        out = Path(cmd[-1])
        for line in Path(cmd[cmd.index("-i") + 1]).read_text().splitlines():
            part = Path(line.split("'", 1)[1].rstrip("'"))
            with open(out, "ab") as dst, open(part, "rb") as src:
                dst.write(src.read())
        return MagicMock()


def test_render_reports_one_event_per_part_and_never_exceeds_one(tmp_path, monkeypatch):
    """A multi-interval clip reports its part count; a single-interval clip
    reports one part, not a fraction of nothing. An event arrives before and
    after each part, so the count the bar divides by is the count of renders
    the loop actually performed."""
    calls = _stub_render(monkeypatch, [(0.0, 5.0), (10.0, 15.0), (20.0, 22.0)])
    from engine import render as render_mod

    events: list[dict] = []
    render_mod.render_clip(tmp_path / "src.mp4", _candidate(0.0, 30.0),
                           tmp_path / "out.mp4", progress=events.append)
    # Before and after each of three parts.
    assert [e["part"] for e in events] == [0, 1, 1, 2, 2, 3]
    assert all(e["parts"] == 3 for e in events)
    # Three parts emitted, three renders performed: the counter never moved
    # without work behind it.
    assert len(calls) == 3 == events[0]["parts"]


def test_render_progress_moves_only_after_a_part_renders(tmp_path, monkeypatch):
    """The fraction reaching one is a claim that a part finished. The claim is
    checked against ``_render_one`` rather than against ``part/parts``, which
    is in range by construction and cannot fail."""
    calls = _stub_render(monkeypatch, [(0.0, 5.0), (10.0, 15.0)])
    from engine import render as render_mod

    events: list[dict] = []
    render_mod.render_clip(tmp_path / "src.mp4", _candidate(0.0, 30.0),
                           tmp_path / "out.mp4", progress=events.append)
    # ``part`` counts completed parts, so it can only exceed the number of
    # renders finished so far if a number was emitted without a render.
    finished = 0
    for evt in events:
        finished = max(finished, evt["part"])
        assert finished <= len(calls)
        assert 0 <= evt["part"] <= evt["parts"]
    assert len(calls) == 2


def test_render_reports_the_orientation_it_is_rendering(tmp_path, monkeypatch):
    """The pipeline keys its two sub-bars on the ``vertical`` flag, so a bar
    mislabelled here would silently attribute a 9:16 render to 16:9."""
    _stub_render(monkeypatch, [(0.0, 5.0)])
    from engine import render as render_mod

    events: list[dict] = []
    render_mod.render_clip(tmp_path / "src.mp4", _candidate(0.0, 30.0),
                           tmp_path / "out.mp4", vertical=True,
                           progress=events.append)
    assert all(e["vertical"] is True for e in events)


def test_render_without_a_sink_is_unchanged(tmp_path, monkeypatch):
    """``progress=None`` is the path every library caller takes: the parts are
    rendered and the output returned, and nothing is emitted anywhere."""
    calls = _stub_render(monkeypatch, [(0.0, 5.0)])
    from engine import render as render_mod

    out = tmp_path / "out.mp4"
    result = render_mod.render_clip(tmp_path / "src.mp4",
                                    _candidate(0.0, 30.0), out)
    assert result == out
    assert len(calls) == 1
