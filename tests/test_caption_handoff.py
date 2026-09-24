"""The shipped ASS must survive a re-run, and the SRT beside it must follow it.

The defect this pins had three parts, and fixing only the third would have
fixed nothing:

1. ``selekyt.pipeline`` wrote the ``.ass`` unconditionally on every run, so an
   editor's moved cue was overwritten before the render cache was even
   consulted.
2. The per-clip render cache key omitted the ASS contents, so even a preserved
   edit would have shipped the cached MP4 with the old cue burned in.
3. ``srt_for_candidate`` regenerated the sidecar from the candidate, so a
   preserved edit would have left the package carrying two caption documents
   that disagreed.

``ship_captions`` is the guard for 1 and 3; the ``ass_digest`` in the render
record is the guard for 2 (see ``tests/test_caption_style_cache.py``).
"""
from __future__ import annotations

from pathlib import Path

from engine.captions import (
    _digest,
    ass_content,
    ship_captions,
    srt_for_candidate,
    srt_from_ass,
)
from models.core import Candidate, Word
from tests.test_captions import _candidate, _parse_srt, _srt_sec, _timed_words


def _ass_cue_count(path: Path) -> int:
    return sum(1 for ln in path.read_text(encoding="utf-8").splitlines()
               if ln.startswith("Dialogue:"))


def test_an_unedited_run_is_stable(tmp_path):
    """The guard must not churn a project nobody touched."""
    c = _candidate(_timed_words())
    ass, srt = tmp_path / "01.ass", tmp_path / "01.srt"
    before = ship_captions(c, ass, srt)[1]
    after = ship_captions(c, ass, srt, before)[1]
    assert before == after
    assert _ass_cue_count(ass) > 0


def test_an_editors_hand_on_the_ass_is_kept(tmp_path):
    """A changed document that is not our own last write is preserved verbatim.

    The discriminator is the recorded digest: the file differs from what we
    would generate *and* from what we left there last time, so the change is
    someone else's.
    """
    c = _candidate(_timed_words())
    ass, srt = tmp_path / "01.ass", tmp_path / "01.srt"
    prev = ship_captions(c, ass, srt)[1]

    kept = ass.read_text(encoding="utf-8")
    edited = kept.replace("Dialogue: 0,", "Dialogue: 1,")
    assert edited != kept, "the edit must actually change the document"
    ass.write_text(edited, encoding="utf-8")

    path, digest = ship_captions(c, ass, srt, prev)
    assert path == ass
    assert ass.read_text(encoding="utf-8") == edited, "the hand edit must survive"
    assert digest == _digest(edited)


def test_a_kept_edit_reprojects_the_srt_from_it(tmp_path):
    """The ASS is the master document; the SRT is derived, never independently
    regenerated — otherwise the package ships two captions that disagree."""
    c = _candidate(_timed_words())
    ass, srt = tmp_path / "01.ass", tmp_path / "01.srt"
    prev = ship_captions(c, ass, srt)[1]

    kept = ass.read_text(encoding="utf-8")
    # A retime an editor would actually do: drop the last cue by moving the
    # preceding one's end time back.
    dialogues = [ln for ln in kept.splitlines() if ln.startswith("Dialogue:")]
    trimmed = kept.replace(dialogues[-1], "")
    ass.write_text(trimmed, encoding="utf-8")

    before = _parse_srt(srt)
    assert len(before) == len(dialogues)
    ship_captions(c, ass, srt, prev)

    after = _parse_srt(srt)
    assert len(after) == len(dialogues) - 1, (
        "the SRT must lose the cue the editor removed from the ASS")
    assert all(a["text"].strip() for a in after)


def test_a_changed_clip_regenerates_the_documents(tmp_path):
    """A file that differs from the fresh generation but still matches the
    recorded digest was not edited by an editor — the *clip* changed under it,
    so the captions must be regenerated to match the clip's new words."""
    c = _candidate(_timed_words())
    ass, srt = tmp_path / "01.ass", tmp_path / "01.srt"
    prev = ship_captions(c, ass, srt)[1]

    # The clip's words move: the old ASS is now wrong for them, but it is still
    # what we wrote, which is what the recorded digest says.
    moved = Candidate(c.start, c.end, c.text, c.story_type, c.hook, c.payoff,
                      editorial_score=c.editorial_score,
                      virality_score=c.virality_score,
                      context_score=c.context_score, confidence=c.confidence,
                      reason=c.reason, visual_potential=c.visual_potential,
                      words=[Word(w.text, w.start + 1.0, w.end + 1.0)
                             for w in c.words])
    path, digest = ship_captions(moved, ass, srt, prev)
    assert path == ass
    assert digest == _digest(ass_content(moved))
    assert digest != prev


def test_a_state_older_than_the_guard_regenerates(tmp_path):
    """No recorded digest means the difference cannot be attributed — an
    existing ``pipeline_state.json`` from before this guard — so the documents
    are regenerated rather than risk shipping captions that do not match the
    clip's words."""
    c = _candidate(_timed_words())
    ass, srt = tmp_path / "01.ass", tmp_path / "01.srt"
    ass.write_text("hand-written by an earlier build\n", encoding="utf-8")

    path, digest = ship_captions(c, ass, srt, None)
    assert path == ass
    assert digest == _digest(ass_content(c))
    assert _ass_cue_count(ass) > 0


def test_the_projection_tracks_the_generator_cue_for_cue(tmp_path):
    """``srt_from_ass`` must follow ``srt_for_candidate`` cue for cue for the
    documents this module writes, or deriving the sidecar would change the cues
    an unedited package ships.

    Byte equality is *not* the invariant, and asserting it would be wrong: an
    ASS timestamp is centiseconds and a SubRip one is milliseconds, so the
    projection is lossy by up to 5 ms in the last digit. What must hold is one
    cue per cue, the same text, and times that agree to the ASS's own
    resolution.
    """
    c = _candidate(_timed_words())
    gen_path = srt_for_candidate(c, tmp_path / "gen.srt")
    expected = _parse_srt(gen_path)
    got = _parse_srt(Path(_write(tmp_path / "proj.srt", srt_from_ass(ass_content(c)))))

    assert len(got) == len(expected)
    for e, g in zip(expected, got):
        assert e["text"] == g["text"], "the cue body must not change"
        assert e["idx"] == g["idx"]
        for a, b in ((_srt_sec(e["st"]), _srt_sec(g["st"])),
                     (_srt_sec(e["en"]), _srt_sec(g["en"]))):
            assert abs(a - b) <= 0.01, (
                f"{a:.3f}s vs {b:.3f}s is past the centisecond the ASS keeps")


def test_the_projection_strips_override_tags(tmp_path):
    """An edited ASS still carries karaoke and emphasis tags; the SubRip track
    must not."""
    c = _candidate(_timed_words())
    ass = ass_content(c, words_per_line=3)
    projected = srt_from_ass(ass)
    assert "{" not in projected
    assert "\\k" not in projected
    # A cue body survives the strip and keeps every spoken word.
    for w in c.words:
        assert w.text in projected


def test_the_projection_renumbers_cues_after_a_deletion(tmp_path):
    """Sequential numbering over the cues actually written, so a hand edit that
    removes a cue cannot leave a gap in the SubRip index."""
    c = _candidate(_timed_words())
    ass = ass_content(c)
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    trimmed = ass.replace(dialogues[0], "")
    cues = _parse_srt(Path(_write(tmp_path / "t.srt", srt_from_ass(trimmed))))
    assert [int(x["idx"]) for x in cues] == list(range(1, len(cues) + 1))


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_a_preserved_document_with_no_cues_projects_to_an_empty_srt(tmp_path):
    """An editor who stripped every cue gets an empty sidecar, not a crash and
    not a silently-regenerated track."""
    c = _candidate(_timed_words())
    ass, srt = tmp_path / "01.ass", tmp_path / "01.srt"
    prev = ship_captions(c, ass, srt)[1]
    kept = ass.read_text(encoding="utf-8")
    stripped = "\n".join(ln for ln in kept.splitlines()
                         if not ln.startswith("Dialogue:"))
    ass.write_text(stripped + "\n", encoding="utf-8")
    path, digest = ship_captions(c, ass, srt, prev)
    assert path == ass
    assert srt.read_text(encoding="utf-8") == ""
    assert digest == _digest(stripped + "\n")
