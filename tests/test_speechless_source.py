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

import re
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


# ── a source that speaks, but yields no clip ─────────────────────────────────
#
# The speech-free guard only covers one way a run ends with nothing. Another run
# can reach the same refusal with a transcript full of speech, and the old
# sentence — "No complete standalone stories passed the editorial/context gate" —
# described only one of those outcomes while being returned for all of them. The
# tests below pin which of the several distinct causes a given transcript leads
# to, because a length problem, a transcription problem and an editorial
# judgement are acted on differently and a user cannot tell them apart from a
# message that blurs them.


def _wordy_transcript():
    """A ~12 s, 20-word span that clears rank's length and word floors."""
    from models.core import Segment, Word

    text = " ".join(f"topic{i}" for i in range(24))
    words = [Word(t, i * 0.5, i * 0.5 + 0.35, 1.0) for i, t in enumerate(text.split())]
    return Segment(0.0, 12.0, text, words)


def test_a_transcript_the_scan_could_not_search_says_so(tmp_path):
    """Nothing was constructed, so nothing was judged: the message must not
    imply an editorial gate ran on material the scan never built."""
    media = _media(tmp_path)
    seg = _wordy_transcript()
    with patch("selekyt.pipeline.transcribe_auto",
               return_value=([seg], "en", _PROVENANCE)), \
         patch("selekyt.pipeline.make_thoughts", return_value=[]), \
         patch("selekyt.pipeline.rank", return_value=[]):
        with pytest.raises(RuntimeError) as exc:
            pipeline_mod.run(str(media), tmp_path / "out", limit=3)
    message = str(exc.value)
    assert "no candidate spans could be built" in message, message
    assert "editorial/context gate" not in message, (
        "the gate never saw a candidate; the message must not claim it judged one"
    )


def test_a_transcript_with_no_stand_alone_span_names_the_floor(tmp_path):
    """Thoughts existed but none was long or dense enough to be a clip, so the
    report carries the measured length and word count rather than a verdict."""
    from models.core import Segment, Word

    media = _media(tmp_path)
    text = " ".join(f"word{i}" for i in range(8))  # 8 words in 5 s: below both floors
    words = [Word(t, i * 0.6, i * 0.6 + 0.3) for i, t in enumerate(text.split())]
    short = Segment(0.0, 5.0, text, words)
    with patch("selekyt.pipeline.transcribe_auto",
               return_value=([short], "en", _PROVENANCE)), \
         patch("selekyt.pipeline.make_thoughts", return_value=[short]), \
         patch("selekyt.pipeline.rank", return_value=[]):
        with pytest.raises(RuntimeError) as exc:
            pipeline_mod.run(str(media), tmp_path / "out", limit=3)
    message = str(exc.value)
    assert "5.0 s" in message, message
    assert "8 s floor" in message, message
    assert "against 20" in message, message
    assert "editorial/context gate" not in message, (
        "no candidate reached the gate; the floors are why nothing was selected"
    )


def test_a_low_confidence_transcript_is_reported_as_a_transcription_problem(tmp_path):
    """The case the committed Hindi corpus actually hits: the scan finds spans,
    they are long enough and dense enough, and the gate refuses every one for low
    measured confidence. That is a transcription problem, not a weak story, and
    the message must name the number and the fix.

    Nothing here is stubbed below the transcription boundary: ``make_thoughts``,
    ``rank`` and ``validate`` are the real ones, so this is the refusal path a
    user would actually get.
    """
    from tests.fixtures import editorial_fixtures as fx

    transcript = fx.build_transcript(
        ["The district library reopened after eighteen months of renovation work. "
         "Volunteers sorted nine thousand donated books onto fresh wooden "
         "shelves. The reading room gained better lighting and a quiet corner."]
    )
    for seg_ in transcript:
        for w in seg_.words:
            w.confidence = 0.5
    ranked = pipeline_mod.rank(transcript)
    assert not ranked, "the fixture must really yield no clip, or it tests nothing"

    media = _media(tmp_path)
    with patch("selekyt.pipeline.transcribe_auto",
               return_value=(transcript, "en", _PROVENANCE)):
        with pytest.raises(RuntimeError) as exc:
            pipeline_mod.run(str(media), tmp_path / "out", limit=3)
    message = str(exc.value)
    assert "low factual confidence" in message, message
    assert "0.75 floor" in message, message
    assert re.search(r"\b0\.5[0-9]\b", message), (
        "the measured confidence, not a generic label, is what makes this "
        f"checkable: {message}"
    )
    assert "re-run with a larger model" in message, message


def test_a_gate_refusal_that_is_not_confidence_names_its_actual_reason(tmp_path):
    """The dominant-reason branch must not collapse every refusal onto
    confidence; a span refused for another rule is reported with that rule."""
    media = _media(tmp_path)
    seg = _wordy_transcript()
    refusal = (False, "starts mid-conversation")

    with patch("selekyt.pipeline.transcribe_auto",
               return_value=([seg], "en", _PROVENANCE)), \
         patch("selekyt.pipeline.make_thoughts", return_value=[seg]), \
         patch("selekyt.pipeline.rank", return_value=[]), \
         patch("selekyt.pipeline.validate", return_value=refusal):
        with pytest.raises(RuntimeError) as exc:
            pipeline_mod.run(str(media), tmp_path / "out", limit=3)
    message = str(exc.value)
    assert "starts mid-conversation" in message, message
    assert "confidence" not in message, message

