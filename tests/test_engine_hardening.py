"""Regression tests for the F6/F10/F11/F15/F17 findings.

F6  — --remove-filler must actually reach the cut engine. It was accepted by
      run_audio_analysis and then dropped on the floor.
F10 — FFmpeg VAD must close a silence run that is still open at EOF, or a
      silent tail scores as speech to the very end of the file.
F11 — SRT cue numbers must be sequential over the cues actually written, with
      no gaps where an empty cue was skipped.
F15 — a stereo or non-16kHz WAV must be handled, not refused.
F17 — serve() must count the requests that failed rather than returning a
      counter it never touches.
"""
from __future__ import annotations

import io
import json
import shutil
import wave
from pathlib import Path

import pytest

from models.core import Segment, Word
from engine.vad import VADResult


_HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _write_wav(path: Path, samples: list[float], rate: int = 16000,
               channels: int = 1, sampwidth: int = 2) -> Path:
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32)
    if channels > 1:
        arr = np.repeat(arr[:, None], channels, axis=1).reshape(-1)
    raw = (np.clip(arr, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(sampwidth)
        f.setframerate(rate)
        f.writeframes(raw)
    return path


def _tone(seconds: float, rate: int = 16000, freq: float = 440.0) -> list[float]:
    import numpy as np

    n = int(rate * seconds)
    t = np.arange(n) / rate
    return (0.5 * np.sin(2 * np.pi * freq * t)).tolist()


# ── F6: remove_filler reaches the cut engine ──

def test_remove_filler_actually_switches_the_engine():
    """--no-remove-filler must change the cut list, not just be accepted.

    Before F6 run_audio_analysis took the parameter and never passed it on, so
    the flag had no effect at all.
    """
    from engine.audio_fusion import AudioTruth, FusedWord
    from engine.jumpcut import JumpCutEngine
    from models.core import Word
    from engine.pause_classifier import ClassifiedPause, PauseType
    from engine.vad import SpeechRegion, SilenceRegion

    # A speech region, then a long hesitation pause, then more speech.
    speech = [SpeechRegion(0.0, 5.0), SpeechRegion(6.5, 10.0)]
    silence = [SilenceRegion(5.0, 6.5, 1.5)]
    pauses = [ClassifiedPause(5.0, 6.5, 1.5, PauseType.HESITATION, 0.9)]

    def truth() -> AudioTruth:
        return AudioTruth(
            whisper_words=[Word("hello", 0.1, 0.5)],
            vad_speech=speech,
            vad_silence=silence,
            pauses=pauses,
            fused_words=[FusedWord(
                Word("hello", 0.1, 0.5), vad_speech=True,
                leading_silence=0.1, trailing_silence=4.4,
            )],
            fused_regions=[],
            duration=10.0,
            vad_backend="energy",
            vad_score_file="",
            speech_ratio=0.8,
            silence_ratio=0.2,
            dead_air_ratio=0.0,
            hallucinated_count=0,
        )

    removed = JumpCutEngine(remove_filler=True).generate_cutlist(truth())
    kept = JumpCutEngine(remove_filler=False).generate_cutlist(truth())
    assert removed.segments_removed >= 1
    assert kept.segments_removed < removed.segments_removed, \
        "remove_filler=False must keep the hesitation pause the engine would cut"
    assert any(s.segment_type.value == "remove_filler" for s in removed.segments), \
        "the filler removal branch must actually fire"


def test_removed_hesitation_is_labelled_filler_not_silence():
    """The gap's reason must name the decision that made it.

    Gaps are re-derived from the final keep tiling, which forgets *why* a pause
    was dropped. Classified by duration alone, a removed hesitation is reported
    as dead air — the cut is right but the editor's traceability is wrong, and
    REMOVE_FILLER is unreachable: any pause long enough to count as filler
    (>= filler_threshold) already exceeds silence_threshold, so duration never
    selects it.
    """
    from engine.audio_fusion import AudioTruth
    from engine.jumpcut import JumpCutEngine, SegmentType
    from engine.pause_classifier import ClassifiedPause, PauseType
    from engine.vad import SpeechRegion, SilenceRegion

    speech = [SpeechRegion(0.0, 5.0), SpeechRegion(6.5, 10.0)]
    silence = [SilenceRegion(5.0, 6.5, 1.5)]
    pauses = [ClassifiedPause(5.0, 6.5, 1.5, PauseType.HESITATION, 0.9)]

    truth = AudioTruth(
        whisper_words=[], vad_speech=speech, vad_silence=silence, pauses=pauses,
        fused_words=[], fused_regions=[], duration=10.0, vad_backend="energy",
    )
    cl = JumpCutEngine(remove_filler=True).generate_cutlist(truth)

    gap = next(s for s in cl.segments if s.segment_type != SegmentType.KEEP)
    assert (gap.start, gap.end) == (5.0, 6.5)
    assert gap.segment_type is SegmentType.REMOVE_FILLER, \
        f"a removed hesitation must be labelled filler, got {gap.segment_type.value}"
    assert gap.reason == "hesitation"

    # Dead air is a different editorial decision and must stay distinguishable.
    dead = [ClassifiedPause(5.0, 6.5, 1.5, PauseType.DEAD_AIR, 0.9)]
    truth_dead = AudioTruth(
        whisper_words=[], vad_speech=speech, vad_silence=silence, pauses=dead,
        fused_words=[], fused_regions=[], duration=10.0, vad_backend="energy",
    )
    gap_dead = next(
        s for s in JumpCutEngine().generate_cutlist(truth_dead).segments
        if s.segment_type != SegmentType.KEEP
    )
    assert gap_dead.segment_type is SegmentType.REMOVE_SILENCE
    assert gap_dead.reason == "dead_air"


def test_remove_filler_reaches_run_audio_analysis(tmp_path, monkeypatch):
    """The parameter must be threaded through, not dropped at the boundary."""
    from engine import audio_analysis

    # A real (empty) media file: the fingerprint stats it before any stage runs.
    media = tmp_path / "media.wav"
    media.write_bytes(b"")

    seen: dict = {}

    class _FakeEngine:
        def __init__(self, **kw):
            seen.update(kw)

        def generate_cutlist(self, truth):
            return type("Cut", (), {
                "segments": [], "total_keep_duration": 0.0,
                "total_remove_duration": 0.0, "original_duration": 10.0,
                "compression_ratio": 1.0, "segments_removed": 0,
                "words_removed": 0,
            })()

    class _FakeBackend:
        name = "fake"

        def analyze(self, wav_path):
            return VADResult(scores=[], frame_ms=20, sample_rate=16000,
                             backend="fake", duration=1.0)

    monkeypatch.setattr(audio_analysis, "JumpCutEngine", _FakeEngine)
    monkeypatch.setattr(
        audio_analysis, "get_vad_backend", lambda *a, **k: _FakeBackend(),
    )
    monkeypatch.setattr(audio_analysis, "extract_pcm_wav", lambda *a, **k: Path("x"))
    monkeypatch.setattr(audio_analysis, "segment_with_meta",
                        lambda *a, **k: ([], {"hysteresis": {}}))
    monkeypatch.setattr(audio_analysis, "silence_from_speech",
                        lambda *a, **k: [])
    monkeypatch.setattr(audio_analysis, "PauseClassifier",
                        lambda *a, **k: type("P", (), {"classify": lambda *a: []})())
    monkeypatch.setattr(audio_analysis, "fuse_audio_truth",
                        lambda *a, **k: type("T", (), {
                            "duration": 1.0, "speech_ratio": 1.0,
                            "silence_ratio": 0.0, "dead_air_ratio": 0.0,
                            "hallucinated_count": 0, "fused_words": [],
                            "fused_regions": [], "vad_speech": [],
                            "vad_silence": [], "pauses": [],
                            "vad_backend": "fake", "vad_score_file": "",
                            "whisper_words": [],
                        })())
    monkeypatch.setattr(audio_analysis, "load_scores", lambda p: None)
    monkeypatch.setattr(audio_analysis, "save_scores", lambda *a, **k: None)
    monkeypatch.setattr(audio_analysis, "cut_algebra", type("C", (), {
        "pad_intervals": lambda *a, **k: [],
    }))

    audio_analysis.run_audio_analysis(
        media, tmp_path / "project", keep_pauses=False, remove_filler=False,
    )
    assert seen.get("remove_filler") is False
    assert seen.get("keep_intentional") is False
    assert seen.get("keep_breathing") is False


# ── F10: FFmpeg VAD trailing silence ──

@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_ffmpeg_vad_scores_trailing_silence_as_silence(tmp_path):
    """A file whose tail is silent must not score as speech to the end.

    Before F10 the silence run open at EOF had no silence_end event, so it was
    never closed and the tail scored 1.0 (speech) — the dead air at the end of
    a clip was invisible to the whole audio stage.
    """
    import numpy as np

    from engine.vad import FFmpegVADBackend

    rate = 16000
    tone = (0.5 * np.sin(2 * np.pi * 440 * np.arange(int(rate * 3)) / rate))
    tail = np.zeros(int(rate * 2))
    full = np.concatenate([tone, tail])
    wav = _write_wav(tmp_path / "tail.wav", full.tolist(), rate=rate)

    result = FFmpegVADBackend().analyze(wav)
    frame = result.frame_ms / 1000.0
    n = len(result.scores)
    # The final two seconds must be silent.
    tail_start = int(3.0 / frame)
    assert all(s == 0.0 for s in result.scores[tail_start:n]), \
        f"trailing silence scored as speech: {result.scores[tail_start:tail_start + 5]}"
    assert any(s == 1.0 for s in result.scores[:int(2.5 / frame)]), \
        "the opening tone must score as speech"


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_ffmpeg_vad_all_speech_file(tmp_path):
    """No silence events at all: everything scores speech, no phantom silence."""
    from engine.vad import FFmpegVADBackend

    wav = _write_wav(tmp_path / "tone.wav", _tone(2.0), rate=16000)
    result = FFmpegVADBackend().analyze(wav)
    assert all(s == 1.0 for s in result.scores)
    assert result.scores


# ── F11: sequential SRT cue numbering ──

def test_srt_cue_numbers_are_sequential_with_no_gaps(tmp_path):
    """Cue indices must be 1..N over the cues written.

    The old code enumerated the block list and skipped empty blocks, leaving
    gaps in the numbering wherever a block had no text — an invalid SubRip
    index that breaks renumbering and diffing.
    """
    from engine.captions import srt_for_candidate

    words = [Word(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(12)]
    c = Segment(0.0, 12.0, " ".join(w.text for w in words), words)
    out = srt_for_candidate(c, tmp_path / "c.srt")
    text = out.read_text(encoding="utf-8")
    indices = [int(block.splitlines()[0]) for block in text.split("\n\n") if block.strip()]
    assert indices == list(range(1, len(indices) + 1)), \
        f"SRT cue numbers are not sequential: {indices}"


def test_srt_cues_match_ass_cues(tmp_path):
    """SRT and ASS must carry the same cues at the same times."""
    from engine.captions import ass_for_candidate, srt_for_candidate

    words = [Word(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(6)]
    c = Segment(0.0, 6.0, " ".join(w.text for w in words), words)
    srt = srt_for_candidate(c, tmp_path / "c.srt").read_text(encoding="utf-8")
    ass = ass_for_candidate(c, tmp_path / "c.ass").read_text(encoding="utf-8")
    srt_cues = [b for b in srt.split("\n\n") if b.strip()]
    dialogue = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(srt_cues) == len(dialogue), \
        f"SRT has {len(srt_cues)} cues but ASS has {len(dialogue)}"


# ── F15: WAV channel and sample-rate handling ──

@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_energy_vad_accepts_stereo_wav(tmp_path):
    """A stereo file is downmixed, not refused with an exception.

    Before F15 the reader raised "expected mono 16kHz WAV" on any other shape,
    which failed the whole analysis on a perfectly good recording.
    """
    from engine.vad import EnergyVAD

    wav = _write_wav(tmp_path / "stereo.wav", _tone(1.0), rate=16000, channels=2)
    result = EnergyVAD().analyze(wav)
    assert result.scores
    assert result.sample_rate == 16000


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_energy_vad_accepts_44100hz_wav(tmp_path):
    from engine.vad import EnergyVAD

    wav = _write_wav(tmp_path / "44k.wav", _tone(1.0), rate=44100)
    result = EnergyVAD().analyze(wav)
    assert result.scores
    assert result.sample_rate == 16000  # converted to the scorer's own grid


def test_windowed_wav_reads_only_the_requested_window(tmp_path):
    """F8: the sherpa reader must not load the whole file into memory."""
    import numpy as np

    from providers.sherpa import _WindowedWav

    # A chirp, not a steady tone: two windows one second apart must differ at
    # every sample, so a reader that ignored the seek is caught unambiguously.
    rate = 16000
    t = np.arange(rate * 10) / rate
    sig = (0.5 * np.sin(2 * np.pi * (100.0 + 50.0 * t) * t)).tolist()

    wav = _write_wav(tmp_path / "long.wav", sig, rate=rate)
    reader = _WindowedWav(wav)
    try:
        assert reader.sample_rate == rate
        first = reader.window(0.0, 1.0)
        later = reader.window(5.0, 6.0)
        assert len(first) == rate
        assert len(later) == rate
        # Seeking must actually move: the two windows differ.
        assert not np.array_equal(first, later), \
            "window() ignored its start time and read the same samples twice"
        # A window past EOF is clamped, not an error.
        end = reader.window(9.5, 12.0)
        assert 0 < len(end) <= int(rate * 0.5) + 1
    finally:
        reader.close()


def test_windowed_wav_downmixes_stereo(tmp_path):
    from providers.sherpa import _WindowedWav

    wav = _write_wav(tmp_path / "stereo.wav", _tone(1.0), rate=16000, channels=2)
    reader = _WindowedWav(wav)
    try:
        mono = reader.window(0.0, 0.5)
        assert len(mono) == 8000  # frames, not frames*channels
    finally:
        reader.close()


# ── F17: serve() counts failures ──

def test_serve_counts_failed_requests():
    """The errors counter the loop returns must reflect reality.

    F17: it was initialized to 0 and never touched, so a caller piping a batch
    through the sidecar could not distinguish a clean run from a half-failed one.
    """
    from selekyt.server import serve

    out = io.StringIO()
    req = '{"op": "ping", "params": {}}\n{"op": "bogus", "params": {}}\n'
    errors = serve(io.StringIO(req), out)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert lines[0]["ok"] is True
    assert lines[1]["ok"] is False
    assert errors == 1, f"one failed request must be counted, got {errors}"


def test_serve_clean_run_is_zero_errors():
    from selekyt.server import serve

    out = io.StringIO()
    errors = serve(io.StringIO('{"op": "ping", "params": {}}\n'), out)
    assert errors == 0


def test_serve_rejects_directory_as_media(tmp_path):
    """A directory handed in as media is a clear error, not a deep ffmpeg crash.

    Sidecar path scoping: the entrypoint validates it is a regular file before
    any engine stage sees it.
    """
    from selekyt.server import process_line

    d = tmp_path / "adir"
    d.mkdir()
    resp = json.loads(process_line(json.dumps({
        "op": "audio_analysis", "params": {"media": str(d)},
    })))
    assert resp["ok"] is False
    assert "media" in resp["error"]


def test_serve_rejects_nonexistent_media(tmp_path):
    from selekyt.server import process_line

    resp = json.loads(process_line(json.dumps({
        "op": "vad_analyze",
        "params": {"path": str(tmp_path / "nope.wav")},
    })))
    assert resp["ok"] is False
    assert "path" in resp["error"]
