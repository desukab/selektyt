"""A source with no detectable speech must fail with the cause named.

A real URL run (archive.org's ``SVS-1335``) delivered a video whose audio track
is digital silence — ``volumedetect`` reports a -91 dB mean. Transcription
correctly produced zero segments, and then the run spent its remaining time
ranking an empty transcript only to die with
"No complete standalone stories passed the editorial/context gate", which tells
a user nothing they can act on: nothing names the missing speech, and the file
is indistinguishable from a genuine editorial rejection.

The pipeline now fails as soon as the empty transcript is in hand. The failure
is not editorial, so the message must not read like one.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import selekyt.pipeline as pipeline_mod


# The shape transcribe_auto really returns; the pipeline reads several keys
# off it for its provenance print, so a minimal stub KeyErrors there.
_PROVENANCE = {
    "engine": "faster-whisper", "requested_model": "small", "model": "small",
    "word_timestamps": "per-token", "fallback_reason": None,
    "downgrade_reason": None, "chunks": 1, "chunking": "single",
    "num_threads": 4,
}


def _media(tmp_path: Path) -> Path:
    media = tmp_path / "silent.mp4"
    media.write_bytes(b"0" * 1024)
    return media


def test_a_speechless_source_fails_before_ranking(tmp_path):
    """``rank`` must never be called on an empty transcript."""
    media = _media(tmp_path)
    with patch("selekyt.pipeline.transcribe_auto",
               return_value=([], "en", _PROVENANCE)), \
         patch("selekyt.pipeline.make_thoughts") as thoughts, \
         patch("selekyt.pipeline.rank") as rank:
        with pytest.raises(RuntimeError) as exc:
            pipeline_mod.run(str(media), tmp_path / "out", limit=3)
    assert "no speech" in str(exc.value).lower(), str(exc.value)
    # The expensive downstream stages were never reached.
    thoughts.assert_not_called()
    rank.assert_not_called()


def test_the_message_names_the_actual_cause(tmp_path):
    """The error must distinguish a speech-free source from an editorial
    rejection, and must not claim the gate judged anything."""
    media = _media(tmp_path)
    with patch("selekyt.pipeline.transcribe_auto",
               return_value=([], "en", _PROVENANCE)):
        with pytest.raises(RuntimeError) as exc:
            pipeline_mod.run(str(media), tmp_path / "out", limit=3)
    message = str(exc.value).lower()
    assert "speech" in message
    assert "editorial" not in message, (
        "a speech-free source is not an editorial rejection; the wording must "
        "not imply the gate judged the content"
    )
    assert "audio track" in message, (
        "the actionable hint is the missing audio track"
    )


def test_a_speaking_source_is_unaffected(tmp_path):
    """The guard must not fire on a transcript with real content."""
    from models.core import Segment, Word
    media = _media(tmp_path)
    seg = Segment(0.0, 5.0, "welcome to the show", [Word("welcome", 0.0, 0.4)])
    with patch("selekyt.pipeline.transcribe_auto",
               return_value=([seg], "en", _PROVENANCE)), \
         patch("selekyt.pipeline.make_thoughts", return_value=[seg]), \
         patch("selekyt.pipeline.rank", return_value=[]):
        with pytest.raises(RuntimeError) as exc:
            pipeline_mod.run(str(media), tmp_path / "out", limit=3)
    # Reaching the gate means the speech-free guard let the transcript through.
    assert "no complete standalone stories" in str(exc.value).lower(), \
        str(exc.value)
