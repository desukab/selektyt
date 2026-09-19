"""The corpus harness must survive a re-run without losing its own evidence.

``tools/corpus_run.py`` records the source's frame rate into the project as
``source_probe.json`` because it deletes the media by default and
``tools/nle_check.py`` needs that rate afterwards to judge timelines on the grid
they were stamped on. ``run_one`` also ``rmtree``s the project directory so a
re-run starts clean — and for a long time those two facts combined to destroy
the probe on every re-run, because the write was guarded on the directory
already existing. It had just been deleted.

That left a project whose frame rate was unknowable once the media was gone, so
every fps-dependent audit of it reported "could not probe the source" on an
export that was correct when it was made. Detected on ``royal-society-cloke``,
the one corpus source that had actually been re-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.corpus_run import run_one  # noqa: E402

_ENTRY = {
    "id": "probe-regression",
    "axis": "test",
    "url": "unused: fetch is stubbed",
    "limit": 1,
    "model": "small",
}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """``run_one`` with the network and the pipeline itself stubbed out.

    The probe write happens before the CLI is invoked, so neither stub affects
    the behaviour under test; they only stop the test from doing real work.
    """
    media = tmp_path / "media" / "source.webm"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"stub")

    monkeypatch.setattr("tools.corpus_run.fetch", lambda entry, d: (media, None))
    monkeypatch.setattr("tools.corpus_run.probe", lambda p: {"fps": "25/1", "duration": 10.0})

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "tools.corpus_run.subprocess.run", lambda *a, **kw: _Result()
    )
    return tmp_path


def _probe(workdir: Path) -> Path:
    return workdir / "probe-regression_run" / "project" / "source_probe.json"


def test_a_first_run_records_the_source_probe(harness):
    rec = run_one(dict(_ENTRY), harness, keep_media=True)
    assert rec["status"] == "OK"
    assert _probe(harness).is_file()


def test_a_re_run_still_records_the_source_probe(harness):
    """Regression: the write was guarded on the probe directory existing, and
    ``run_one`` had just deleted it. Every re-run silently dropped the frame
    rate the audits depend on, and once the media was deleted there was no
    recovering it from anywhere else in the project."""
    run_one(dict(_ENTRY), harness, keep_media=True)
    first = _probe(harness).read_text(encoding="utf-8")

    run_one(dict(_ENTRY), harness, keep_media=True)
    assert _probe(harness).is_file(), "a re-run must not lose the source probe"
    assert _probe(harness).read_text(encoding="utf-8") == first


def test_the_probe_outlives_the_media(harness):
    """``keep_media=False`` is the default for a corpus run, and the probe is
    the only place the frame rate survives it."""
    run_one(dict(_ENTRY), harness, keep_media=False)
    media = harness / "media" / "source.webm"
    assert not media.exists()
    assert _probe(harness).is_file()


_SILENT = (
    '  File "selekyt/pipeline.py", line 468, in run\n'
    "    raise RuntimeError(...)\n"
    "RuntimeError: Transcription produced no speech: the source has no audible "
    "spoken content. the extracted audio track is digital silence, so a silent "
    "or music-only source has nothing to select; check that the file has an "
    "audio track at all.\n"
)

_DAMAGED = (
    '  File "selekyt/pipeline.py", line 468, in run\n'
    "    raise RuntimeError(...)\n"
    "RuntimeError: Transcription produced no speech: the source has no audible "
    "spoken content. the extracted audio is not silent (peak -22.4 dBFS) but no "
    "transcript could be decoded, and a stream-copy remux did not help — the "
    "container or codec is likely damaged. Try re-muxing or re-encoding the "
    "source by hand.\n"
)


@pytest.mark.parametrize(
    "stderr, expected",
    [(_SILENT, "silent_track"), (_DAMAGED, "damaged_container")],
    ids=["silent track", "damaged container"],
)
def test_a_no_speech_failure_records_which_of_the_two_causes_it_was(
        harness, monkeypatch, stderr, expected):
    """An empty transcript has two unrelated causes — a genuinely silent track,
    and audio that is there but would not decode — and the pipeline's own
    message already separates them. The record has to carry that separation,
    because ``run_one`` deletes the media by default: once it is gone the peak
    measurement that decided the case is not recoverable, and a bare
    ``NO_SPEECH_DETECTED`` row cannot be classified later. The three real rows
    in ``corpus/results.jsonl`` predate this field and are unclassifiable for
    exactly that reason."""
    class _Result:
        returncode = 1
        stdout = ""
        # A class-body annotation cannot see the enclosing parameter, so the
        # attribute is set after the class exists.
        stderr = ""

    _Result.stderr = stderr

    monkeypatch.setattr(
        "tools.corpus_run.subprocess.run", lambda *a, **kw: _Result()
    )
    rec = run_one(dict(_ENTRY), harness, keep_media=True)
    assert rec["status"] == "NO_SPEECH_DETECTED"
    assert rec["no_speech_cause"] == expected


def test_an_unrecognised_no_speech_message_is_labelled_not_guessed(harness, monkeypatch):
    """If the message text drifts, say ``unknown`` rather than silently filing
    the failure under whichever cause the classifier happened to match — a wrong
    classification here is worse than an admitted gap, because it is what a
    later reader will trust."""
    class _Result:
        returncode = 1
        stdout = ""
        stderr = (
            "RuntimeError: Transcription produced no speech: the source has no "
            "audible spoken content. [a message shape this harness has not met]\n"
        )

    monkeypatch.setattr(
        "tools.corpus_run.subprocess.run", lambda *a, **kw: _Result()
    )
    rec = run_one(dict(_ENTRY), harness, keep_media=True)
    assert rec["status"] == "NO_SPEECH_DETECTED"
    assert rec["no_speech_cause"] == "unknown"
