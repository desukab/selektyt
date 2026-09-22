"""Tests for the no-speech diagnosis and container recovery.

The pipeline used to answer every empty transcript the same way: tell the user
their source "has no audible spoken content". That was correct for a silent
film and wrong for a source whose container was structurally damaged — one
corpus source at 95.5% speech energy was rejected on that message, and a clean
remux of the same file transcribed to 11 segments.

These tests pin the separation the diagnosis now makes:

* an extraction that *is* silent gets the silence message, and no remux is
  attempted, because a remux of a silent file costs minutes and cannot help;
* an extraction with real level and no transcript reaches for the container,
  and a remux that decodes recovers the run and switches the media the rest
  of the pipeline seeks;
* a remux that changes nothing leaves the diagnosis naming the container as
  the suspect, rather than the user's content.
"""
from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from models.core import Segment, Word
from providers.audio import peak_dbfs
from providers.ffmpeg import FfmpegError, remux
from selekyt.pipeline import (SILENT_PEAK_DBFS, _no_speech_diagnosis,
                              _recover_from_a_damaged_container)

_REPO = Path(__file__).resolve().parent.parent


def _ui_contract_against(project: Path) -> None:
    """Run the desktop UI's artifact contract against the tree this run wrote.

    A recovered run is the shape the ``container_recovery`` chip renders from,
    so the contract's recovery checks need one to run against; without it they
    are unexercised, exactly as the bench checks were before the e2e suite
    passed a project in.
    """
    node = shutil.which("node")
    if node is None:
        return
    proc = subprocess.run(
        [node, str(_REPO / "tests" / "ui_contract.mjs"), str(project)],
        capture_output=True, text=True, cwd=_REPO,
    )
    assert proc.returncode == 0, (
        "the UI contract failed against a recovered tree this run produced:\n"
        + proc.stdout + proc.stderr)
    assert "PASS" in proc.stdout

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not available")


def _wav(path: Path, seconds: float, amplitude: float, rate: int = 16000) -> Path:
    n = int(seconds * rate)
    frames = b"".join(
        struct.pack("<h", int(amplitude * 32767 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(n)
    )
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(frames)
    return path


def _media(path: Path, seconds: float = 6.0) -> Path:
    """A real container with a real audio stream, for the remux to copy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=%d" % seconds,
         "-c:a", "aac", str(path)],
        check=True, timeout=120,
    )
    return path


# ── peak_dbfs: the measurement the diagnosis turns on ──

def test_a_silent_extraction_measures_minus_infinity(tmp_path):
    assert peak_dbfs(_wav(tmp_path / "s.wav", 2.0, amplitude=0.0)) == -math.inf


def test_a_loud_extraction_measures_near_full_scale(tmp_path):
    peak = peak_dbfs(_wav(tmp_path / "l.wav", 2.0, amplitude=0.5))
    # amplitude 0.5 is -6.02 dB by construction; the tolerance is for the
    # quantisation of the sine's peak sample, which does not land exactly on 0.5.
    assert peak is not None and -6.5 <= peak <= -5.5


def test_an_absent_extraction_measures_nothing(tmp_path):
    assert peak_dbfs(tmp_path / "nothing.wav") is None


# ── the diagnosis itself ──

def test_silence_is_reported_as_silence():
    msg = _no_speech_diagnosis(-math.inf)
    assert "digital silence" in msg
    assert "damaged" not in msg


def test_level_with_no_transcript_points_at_the_container():
    """The measured corpus case: -0.8 dBFS peak, 4.5% coverage, no transcript
    — and a message that used to blame the content."""
    msg = _no_speech_diagnosis(-0.8)
    assert "-0.8 dBFS" in msg
    assert "container or codec is likely damaged" in msg


def test_a_quiet_but_real_source_is_not_called_silent():
    """-43 dBFS is quiet, not silent, and the floor must not reclassify it:
    the Hungarian corpus source sits there and its transcript is fine."""
    assert -43.3 > SILENT_PEAK_DBFS
    assert "damaged" in _no_speech_diagnosis(-43.3)


# ── the recovery ──

def test_a_remux_that_decodes_recovers_and_switches_the_media(tmp_path):
    """The recovered file has to become the media downstream seeks, so the
    return carries the path rather than only the transcript."""
    media = _media(tmp_path / "src" / "in.m4a")
    seen = []

    def transcribe(path):
        seen.append(path)
        return ([object()], "en", {"engine": "t"})

    recovered = _recover_from_a_damaged_container(
        media, tmp_path / "src", transcribe,
    )
    assert recovered is not None
    remuxed, transcript, detected, provenance, note = recovered
    assert transcript and detected == "en"
    assert remuxed.is_file(), "downstream seeks this; it must survive"
    assert remuxed != media
    assert seen == [remuxed], "the transcription must come from the remux"
    assert note == "a stream-copy remux"


def test_a_remux_that_decodes_nothing_recovers_nothing(tmp_path):
    """The corpus's speechless sources land here: the remux is fine and the
    transcript is still empty, which is the honest no-speech case."""
    media = _media(tmp_path / "src" / "in.m4a")
    assert _recover_from_a_damaged_container(
        media, tmp_path / "src", lambda path: ([], "en", {}),
    ) is None


def test_a_source_ffmpeg_cannot_copy_recovers_nothing_without_raising(tmp_path):
    """Not a media file at all: ffmpeg rejects the copy, and the run must fall
    through to the diagnosis rather than die in the recovery."""
    junk = tmp_path / "src" / "in.bin"
    junk.parent.mkdir(parents=True, exist_ok=True)
    junk.write_bytes(b"not a container")
    assert _recover_from_a_damaged_container(
        junk, tmp_path / "src", lambda path: ([object()], "en", {}),
    ) is None


def test_a_recovery_whose_transcribe_raises_does_not_kill_the_run(tmp_path):
    """A broken decode path inside the retry is a tooling problem; it must not
    convert a diagnosable no-speech run into an unrelated traceback."""
    media = _media(tmp_path / "src" / "in.m4a")

    def boom(path):
        raise RuntimeError("decoder exploded")

    assert _recover_from_a_damaged_container(
        media, tmp_path / "src", boom,
    ) is None


def test_the_remux_is_a_stream_copy_not_a_re_encode(tmp_path):
    """A repair must not become an excuse to alter the content; a copy that
    changes nothing is also the only recovery comparable to the original."""
    media = _media(tmp_path / "src" / "in.m4a")
    dest = tmp_path / "copy.m4a"
    remux(media, dest)
    assert dest.is_file()
    # Stream copy preserves the codec: a re-encode would report a different one.
    def codec(path):
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True,
        )
        return proc.stdout.strip()

    assert codec(dest) == codec(media) == "aac"


def test_a_remux_of_an_unreadable_file_raises(tmp_path):
    """So the caller can distinguish "recovery attempted and failed" from
    "recovery was never possible"."""
    junk = tmp_path / "j.bin"
    junk.write_bytes(b"garbage")
    with pytest.raises(FfmpegError):
        remux(junk, tmp_path / "out.m4a")


# ── the recovery as the report the editor reads records it ──

def _video(path: Path, seconds: float = 12.0) -> Path:
    """A real container with video and audio, for the remux to stream-copy.

    Audio-only media would answer the recovery question just as well, but the
    run renders from the file the recovery hands it, and a source with no
    video stream has no landscape render to validate.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:rate=10:size=320x180",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(path)],
        check=True, timeout=180,
    )
    return path


def _recovered_segment() -> Segment:
    """One story the remux yields and the original did not.

    Wide enough and wordy enough to clear rank()'s floor (8 s, 20 tokens) and
    to stand alone, so the run reaches the report rather than stopping at an
    empty selection and the recovery is recorded where an editor would read it.
    """
    words = [
        ("The", 1.0, 1.2), ("launch", 1.2, 1.6), ("slipped", 1.6, 2.1),
        ("twice", 2.1, 2.5), ("because", 2.5, 2.8), ("the", 2.8, 2.9),
        ("integration", 2.9, 3.5), ("tests", 3.5, 3.9), ("kept", 3.9, 4.2),
        ("failing", 4.2, 4.7), ("on", 4.7, 4.8), ("the", 4.8, 4.9),
        ("staging", 4.9, 5.4), ("cluster", 5.4, 5.9), ("nobody", 5.9, 6.3),
        ("on", 6.3, 6.4), ("the", 6.4, 6.5), ("team", 6.5, 6.9),
        ("had", 6.9, 7.1), ("seen", 7.1, 7.5), ("that", 7.5, 7.7),
        ("failure", 7.7, 8.2), ("before", 8.2, 8.6), ("the", 8.6, 8.7),
        ("fix", 8.7, 9.1), ("landed", 9.1, 9.6), ("overnight", 9.6, 10.2),
    ]
    return Segment(
        1.0, 10.2,
        "The launch slipped twice because the integration tests kept failing "
        "on the staging cluster nobody on the team had seen that failure "
        "before the fix landed overnight.",
        [Word(t, s, e) for t, s, e in words],
    )


_PROVENANCE = {"engine": "test", "requested_model": "small", "model": "small",
               "downgrade_reason": None, "word_timestamps": "attention",
               "fallback_reason": ""}


def _recovering_pipeline(monkeypatch, tmp_path):
    """A run whose original decode yields nothing and whose remux yields a story.

    The two boundaries the pipeline crosses are patched (download, transcribe);
    the remux, the selection, the captions and the renders all run for real, so
    the test reads the report a damaged source actually produces.
    """
    import selekyt.pipeline as p

    media = _video(tmp_path / "source" / "clip.mp4")
    monkeypatch.setattr(
        p, "download", lambda source, dest, cookies=None: (media, {"title": "t"}),
    )
    calls = []

    def transcribe(source_media, **kw):
        calls.append(source_media)
        checkpoint = kw["checkpoint_dir"]
        (checkpoint / "audio.wav").parent.mkdir(parents=True, exist_ok=True)
        # Loud on purpose: the diagnosis only reaches for the container when
        # the extraction has real level. A silent wav here would skip the
        # recovery and the test would exercise nothing.
        _wav(checkpoint / "audio.wav", 6.0, amplitude=0.3)
        if source_media.suffix == ".mkv":
            return [_recovered_segment()], "en", dict(_PROVENANCE)
        # The damaged original: decodes far enough to have level, no words.
        return [], "en", dict(_PROVENANCE)

    monkeypatch.setattr(p, "transcribe_auto", transcribe)
    # A constant fingerprint makes the second call a resume: the transcript the
    # first run wrote (including its recovery marker) is what it reuses.
    monkeypatch.setattr(p, "_fingerprint", lambda *a, **k: "damaged-source")
    return p, calls


def test_a_run_that_recovered_names_the_repair_in_the_report(tmp_path, monkeypatch):
    """The recovery was recorded only in transcript.json, which nothing read.

    An editor whose source was repaired needs to know the clips were cut from
    the repair, because a remux can shift timing relative to the file they
    handed in — and the report is the file the desktop app opens."""
    p, calls = _recovering_pipeline(monkeypatch, tmp_path)
    project = p.run("https://example.com/v", tmp_path, model="small", limit=1)

    assert [c.suffix for c in calls] == [".mp4", ".mkv"], \
        "the original failed and the remux is what produced the words"
    report = json.loads((project / "report.json").read_text())
    assert report["container_recovery"] == "a stream-copy remux", \
        "the report must say the transcript came from a repair, not the source"
    transcript = json.loads((project / "transcript.json").read_text())
    assert transcript["container_recovery"] == report["container_recovery"], \
        "the report repeats the marker rather than paraphrasing it"
    _ui_contract_against(project)


def test_the_recovered_file_is_the_one_the_run_renders_from(tmp_path, monkeypatch):
    """The report points at outputs; the state file is what a resume renders
    from. Both have to name the remux, or a re-run cuts from the damaged file
    the recovery just repaired."""
    p, _ = _recovering_pipeline(monkeypatch, tmp_path)
    project = p.run("https://example.com/v", tmp_path, model="small", limit=1)
    state = json.loads((project / "pipeline_state.json").read_text())
    assert state["media_path"].endswith(".remux.mkv")
    assert Path(state["media_path"]).is_file(), \
        "the remux is kept because downstream seeks it"


def test_a_recovered_run_still_names_the_repair_when_it_is_resumed(
        tmp_path, monkeypatch):
    """The resume path never enters the recovery branch: it reuses the cached
    transcript and the marker has to be read back from the file, exactly like
    the ASR provenance. Without the read-back the second report would silently
    claim the words came from the file the user handed in."""
    p, _ = _recovering_pipeline(monkeypatch, tmp_path)
    project = p.run("https://example.com/v", tmp_path, model="small", limit=1)

    resumed = p.run("https://example.com/v", tmp_path, model="small", limit=1)
    report = json.loads((resumed / "report.json").read_text())
    assert report["container_recovery"] == "a stream-copy remux", \
        "a resumed run must not forget that its transcript came from a repair"
    # The cached transcript is reused, so the run must not have transcribed again.
    assert report["transcription"]["model"] == "small"


def test_a_run_that_never_recovered_says_so_explicitly(tmp_path, monkeypatch):
    """``null`` is a statement — the transcript came from the source as given —
    not a missing key the UI would render as a broken chip."""
    import selekyt.pipeline as p

    media = _video(tmp_path / "source" / "clip.mp4")
    monkeypatch.setattr(
        p, "download", lambda source, dest, cookies=None: (media, {"title": "t"}),
    )
    monkeypatch.setattr(
        p, "transcribe_auto",
        # An undamaged source: the first decode already yields the story, so
        # the recovery branch is never entered.
        lambda media, **kw: ([_recovered_segment()], "en", dict(_PROVENANCE)),
    )
    monkeypatch.setattr(p, "_fingerprint", lambda *a, **k: "clean-source")
    project = p.run("https://example.com/v", tmp_path, model="small", limit=1)
    report = json.loads((project / "report.json").read_text())
    assert report["container_recovery"] is None
    assert "container_recovery" in report, \
        "the key is present on every run so a reader distinguishes " \
        "\"not recovered\" from \"not recorded\""


def _patched_run(monkeypatch, tmp_path, extraction_amplitude: float):
    """A run whose transcriber writes the given extraction and returns nothing.

    The extraction is what the real transcriber would have produced; the peak
    of it is what decides whether the run reaches for the container at all.
    """
    import selekyt.pipeline as p

    media = _media(tmp_path / "source" / "clip.m4a")
    monkeypatch.setattr(
        p, "download", lambda source, dest, cookies=None: (media, {"title": "t"}),
    )

    def transcribe(media, **kw):
        checkpoint = kw.get("checkpoint_dir")
        if checkpoint is not None:
            (checkpoint / "audio.wav").parent.mkdir(parents=True, exist_ok=True)
            _wav(checkpoint / "audio.wav", 6.0, amplitude=extraction_amplitude)
        return [], "en", {"engine": "t", "model": "t",
                          "word_timestamps": "t", "fallback_reason": ""}

    monkeypatch.setattr(p, "transcribe_auto", transcribe)
    return p


def test_a_silent_run_is_diagnosed_without_attempting_a_remux(tmp_path, monkeypatch):
    """The expensive mistake this prevents: transcribing a silent source twice.
    The recovery must never be entered when the extraction is silent."""
    import selekyt.pipeline as p

    patched = _patched_run(monkeypatch, tmp_path, extraction_amplitude=0.0)
    entered = []

    def should_not_run(*a, **k):
        entered.append(True)
        raise AssertionError("a silent source must not reach the remux path")

    monkeypatch.setattr(patched, "_recover_from_a_damaged_container", should_not_run)
    with pytest.raises(RuntimeError, match="digital silence"):
        patched.run("https://example.com/v", tmp_path, model="small", limit=2)
    assert not entered, "a silent source must not pay for a second transcription"


def test_a_loud_empty_run_points_at_the_container_and_not_the_content(tmp_path, monkeypatch):
    """Level with no transcript: the recovery is entered, it cannot help, and
    the message the user reads names the container rather than their media."""
    import selekyt.pipeline as p

    patched = _patched_run(monkeypatch, tmp_path, extraction_amplitude=0.3)
    attempted = {"media": None}

    def recover(media, dest_dir, transcribe):
        attempted["media"] = media
        return None

    monkeypatch.setattr(patched, "_recover_from_a_damaged_container", recover)
    with pytest.raises(RuntimeError) as exc:
        patched.run("https://example.com/v", tmp_path, model="small", limit=2)
    assert attempted["media"] is not None, "the recovery must be reached"
    assert "dBFS" in str(exc.value)
    assert "container or codec is likely damaged" in str(exc.value)

