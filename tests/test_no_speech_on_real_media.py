"""The silent-track diagnosis, validated on real media rather than a stub.

Outstanding-work item 7 says the no-speech diagnosis is fixed in code but has
only ever been shown on synthetic files. This module closes the half of that
claim that is closable from this host and records plainly that the other half
is not closed.

What is measurable here: ``corpus/media/royal-society-cloke.webm`` is a real
recording with real speech. Muxing its video against a genuine digital-silence
track produces a file that goes through the same ffmpeg extraction and the same
decoder as any source a user hands in, and whose measured peak is ``-inf``.
That is the condition the diagnosis must attribute to a silent track, and
watching it happen on the real path is stronger than asserting it from a
mocked ``transcribe_auto`` — which is what ``test_speechless_source.py``
rightly does for the control-flow question and cannot do for the measurement
one.

What is NOT measurable here, and is recorded rather than papered over: the
*damaged-container* branch — real audio level, zero transcript, a stream-copy
remux that repairs it. Three corruption strategies were tried against the same
real excerpt (zeroing 1 MiB of the muxed body, truncating to 45%, corrupting
the region after the EBML head). **None reproduced the condition.** WebM/Opus
decodes around mid-body damage: the three produced 10, 7 and 12 segments
against the control's 12, with byte-identical peak. The historical case that
motivated the recovery path was a structurally damaged ``.ogv`` — a different
and more fragile muxer — and reproducing it needs either that file or a
targeted corruption this host has not found. So the recovery branch remains
validated on synthetic files only, and ``docs/AI_EXECUTION_STATE.md`` item 7
must keep saying so.

The tests are opt-in: the media is not redistributable with the repo, so they
skip with a stated reason where it is absent rather than passing vacuously.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from providers.audio import peak_dbfs  # noqa: E402
from providers.transcribe import transcribe as transcribe_auto  # noqa: E402
from selekyt.pipeline import SILENT_PEAK_DBFS, _no_speech_diagnosis  # noqa: E402

REAL_MEDIA = ROOT / "corpus" / "media" / "royal-society-cloke.webm"

pytestmark = pytest.mark.skipif(
    not REAL_MEDIA.is_file(),
    reason=f"{REAL_MEDIA} is not redistributable with the repo; this check "
           "runs only where the real media is present",
)


def _transcribe(media: Path, tmp_path: Path):
    """Run the production transcribe path and measure what it extracted."""
    ckpt = tmp_path / "transcription"
    transcript, _detected, _prov = transcribe_auto(
        media, model_name="small", language="en",
        checkpoint_dir=ckpt, chunk_minutes=5, beam_size=1, cpu_threads=4,
    )
    return transcript, peak_dbfs(ckpt / "audio.wav")


def _ffmpeg(args: list[str], out: Path) -> Path:
    # stderr is captured rather than inherited so a test failure's report is not
    # buried under ffmpeg's progress meter.
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-hide_banner", "-nostats",
         *args, str(out)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    return out


def _excerpt(tmp_path: Path) -> Path:
    """70 s of the real recording, so the run stays fast on a 4-core host."""
    return _ffmpeg(["-i", str(REAL_MEDIA), "-t", "70", "-c", "copy"],
                   tmp_path / "real.webm")


def _silent_track(source: Path, out: Path) -> Path:
    """The same video against a track of genuine digital silence.

    ``-map`` is required: without it ffmpeg refuses to combine a copied video
    stream with a lavfi input.
    """
    return _ffmpeg(
        ["-i", str(source),
         "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=48000",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "libopus", "-shortest"],
        out)


def test_the_control_media_actually_carries_speech(tmp_path):
    """The guard against a vacuous pass. If the source cannot be decoded at all,
    the silent-track test below would pass for the wrong reason."""
    transcript, peak = _transcribe(_excerpt(tmp_path), tmp_path / "control")
    assert transcript, (
        "the control excerpt produced no transcript, so the real media is "
        "unusable here and the diagnosis test below would be vacuous"
    )
    assert peak is not None and peak > SILENT_PEAK_DBFS, (
        f"the control excerpt measures peak {peak}, not speech level; the "
        f"media is not what this test assumes"
    )


def test_a_real_silent_track_is_diagnosed_as_silence_not_damage(tmp_path):
    """Real video, real decoder, a genuinely silent audio track. The diagnosis
    must name the missing audio and must not blame the container."""
    silent = _silent_track(_excerpt(tmp_path), tmp_path / "silent_track.webm")
    transcript, peak = _transcribe(silent, tmp_path / "silent")

    assert not transcript, "a silent track transcribed; the silence was not real"
    # -inf is what an all-zero extraction measures; a finite peak here means the
    # silence leaked a real sample somewhere.
    assert peak is not None and peak <= SILENT_PEAK_DBFS, (
        f"a digital-silence track measured peak {peak}; the extraction is not "
        f"silent, so the silent-track branch is being reached for the wrong "
        f"reason"
    )
    message = _no_speech_diagnosis(peak)
    assert "digital silence" in message, (
        f"a silent track must be told it has no audio; got {message!r}"
    )
    assert "damaged" not in message, (
        "a silent track is the user's content, not a broken file; blaming the "
        "container sends a user hunting for a problem their file does not have"
    )
