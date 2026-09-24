"""The shipped ASS must be sliced for a multi-part render, not regenerated.

The defect this pins: a candidate keeping more than one retention interval
renders as a concatenation of parts, and each part is burned with its own
subtitle stream at part-local time. Until this fix the per-part ASS was
*regenerated* from a sub-candidate, which replaced whatever an editor had
retimed in the shipped document — so the MP4 disagreed with the file beside
it, and the disagreement was invisible because both files were valid ASS.

``engine.captions.slice_ass`` now slices the shipped document instead. The
window it slices on is the *edited* timeline the document itself keeps:
``ass_content`` emits cues through ``_source_to_edited``, with every removed
gap already closed, so part ``i`` of the render begins at the sum of the
durations before it — not at that interval's source time.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import engine.render as render_mod
from engine.captions import ass_content, slice_ass
from engine.edit_plan import retention_intervals
from models.core import Candidate, Word

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
_FFMPEG = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not available")


def _hhmmss(v: float) -> str:
    """Seconds to the ``H:MM:SS.cc`` timestamp engine.captions writes."""
    v = max(0.0, v)
    return (f"{int(v // 3600)}:{int(v % 3600 // 60):02d}:{int(v % 60):02d}"
            f".{min(99, int(round((v - int(v)) * 100))):02d}")


def _ass_sec(field: str) -> float:
    h, m, rest = field.strip().split(":", 2)
    s, _, cs = rest.partition(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0


# The document shape engine.captions emits, written by hand so a cue can carry
# text and tags the generator would never produce — which is what an editor's
# edit looks like, and what the regeneration path silently discarded.
_HEADER = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
    "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,"
    "SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
    "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
    "Alignment,MarginL,MarginR,MarginV,Encoding\n"
    "Style: Default,Noto Sans,54,&H00FFFFFF,&H00FFFFFF,&H99000000,"
    "&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,70,70,170,1\n\n[Events]\n"
    "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,"
    "Text\n"
)


def _ass(*cues: tuple[float, float, str]) -> str:
    """An ASS document carrying exactly the given ``(start, end, text)`` cues."""
    return _HEADER + "".join(
        f"Dialogue: 0,{_hhmmss(start)},{_hhmmss(end)},Default,,0,0,0,,{text}\n"
        for start, end, text in cues
    )


def _dialogues(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]


def _merged(spans) -> list[tuple[float, float]]:
    """Merge overlapping intervals, so a cue repeated across two parts counts once.

    One editor cue straddling a part boundary appears in both parts as two
    clipped copies that share an edge. Merging is what makes those two count as
    the original cue rather than as two cues.
    """
    out: list[tuple[float, float]] = []
    for st, en in sorted(spans):
        if out and st <= out[-1][1] + 0.005:
            out[-1] = (out[-1][0], max(out[-1][1], en))
        else:
            out.append((st, en))
    return out


def _two_interval_candidate() -> Candidate:
    """A candidate the planner cuts into two parts.

    A 1.0 s gap between ``bravo`` and ``charlie`` clears
    ``retention_intervals``'s 0.70 s minimum, and both halves clear its 0.45 s
    floor, so this is the smallest shape that reaches the multi-part render
    path. Its emitted ASS is a single cue spanning both parts, which is what
    makes the straddling cases below reachable with a hand edit rather than a
    contrived candidate.
    """
    return Candidate(
        0.0, 3.0, "alpha bravo charlie delta", "story", "alpha",
        "charlie delta", editorial_score=0.8, virality_score=0.7,
        context_score=0.9, confidence=0.9, reason="t", visual_potential=0.8,
        words=[
            Word("alpha", 0.0, 0.5),
            Word("bravo", 0.5, 1.0),
            Word("charlie", 2.0, 2.5),
            Word("delta", 2.5, 3.0),
        ],
    )


# ── unit: the slice itself ─────────────────────────────────────────────────────


def test_a_cue_is_shifted_onto_part_local_time():
    """A cue inside the window keeps its duration and loses only its offset."""
    sliced = slice_ass(_ass((1.0, 2.0, "hello")), 1.0, 3.0)
    assert _dialogues(sliced) == [
        "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello"]


def test_a_cue_wholly_outside_the_window_is_dropped():
    """Neither a cue already finished nor one not yet due is carried into a part.

    The part does not contain the speech either describes, so burning it would
    put words on screen at a moment the media does not hold them.
    """
    doc = _ass((0.1, 0.5, "too early"), (5.0, 5.5, "too late"))
    assert _dialogues(slice_ass(doc, 1.0, 3.0)) == []


def test_a_cue_straddling_an_edge_is_clipped_to_the_window():
    """A cue running past a cut appears only for as long as the part holds media."""
    doc = _ass((0.5, 2.5, "straddles the start"),
               (2.5, 4.0, "straddles the end"))
    # Window [1.0, 3.0): the first cue is clipped to [1.0, 2.5] and shifted to
    # part-local [0.0, 1.5]; the second is clipped to [2.5, 3.0] -> [1.5, 2.0].
    assert _dialogues(slice_ass(doc, 1.0, 3.0)) == [
        "Dialogue: 0,0:00:00.00,0:00:01.50,Default,,0,0,0,,straddles the start",
        "Dialogue: 0,0:00:01.50,0:00:02.00,Default,,0,0,0,,straddles the end"]


def test_the_editors_text_and_override_tags_survive_verbatim():
    """The point of slicing rather than regenerating: the editor's cues are kept.

    A tag like ``\\b1`` is not something the emitter writes on plain words, so
    its presence after the slice is direct evidence the document was carried
    rather than rebuilt from the candidate's transcript.
    """
    text = r"{\b1}EDITED{\b0} {\k25}cue"
    doc = _ass((0.0, 1.0, text))
    sliced = slice_ass(doc, 0.0, 1.0)
    assert len(_dialogues(sliced)) == 1
    # Everything after the two time fields is byte-identical.
    assert _dialogues(sliced)[0].split(",", 9)[2:] == \
        _dialogues(doc)[0].split(",", 9)[2:]


def test_non_dialogue_lines_pass_through_untouched():
    """The header and style block are what make the cues render at all."""
    doc = _ass((0.0, 1.0, "hello"))
    sliced = slice_ass(doc, 0.0, 1.0)
    assert sliced.split("Format: Layer")[0] == doc.split("Format: Layer")[0]
    assert "[V4+ Styles]" in sliced and "[Events]" in sliced


def test_the_two_windows_rebuild_the_edited_timeline():
    """Slicing every part's window and re-offsetting recovers the document's times.

    This is the invariant that makes the multi-part burn honest: whatever the
    shipped document says, the concatenation of parts shows each cue for exactly
    the edited time the document declares, and no cue is shown by a part whose
    window does not contain it.

    Clipping a cue that straddles a boundary puts an extra cue end into the
    slices, so the ends are not expected to match the document one for one.
    What must match is the span: the parts tile the edited timeline contiguously,
    so the union of what each part shows is exactly the union of what the
    document declares.
    """
    c = _two_interval_candidate()
    intervals = retention_intervals(c)
    assert len(intervals) == 2, "the fixture must reach the multi-part path"
    doc = ass_content(c)

    shown: list[tuple[float, float]] = []
    for i, (a, b) in enumerate(intervals):
        offset = sum(y - x for x, y in intervals[:i])
        for ln in _dialogues(slice_ass(doc, offset, offset + (b - a))):
            fields = ln.split(",", 9)
            st = _ass_sec(fields[1]) + offset
            en = _ass_sec(fields[2]) + offset
            # No part may claim time outside its own window.
            assert offset - 0.005 <= st and en <= offset + (b - a) + 0.005, (
                f"part {i} carries a cue outside its window")
            assert st < en, f"part {i} carries a zero-length cue"
            shown.append((st, en))

    assert _merged(shown) == _merged(
        (_ass_sec(ln.split(",", 9)[1]), _ass_sec(ln.split(",", 9)[2]))
        for ln in _dialogues(doc)), (
        "the parts between them do not cover exactly the cue spans the shipped "
        "document declares")


def test_a_cue_straddling_a_part_boundary_is_burned_in_both_parts():
    """One editor cue running across a removed gap appears in both parts.

    Part 0 holds the media up to the cut and part 1 picks it up afterwards, so
    the cue is clipped to each window rather than being assigned to one part
    and dropped from the other — the caption stays up across the jump cut for
    as long as the speech is actually heard.
    """
    c = _two_interval_candidate()
    (a0, b0), (a1, b1) = retention_intervals(c)
    offset = b0 - a0
    doc = _ass((0.80, offset + 0.40, "crosses the cut"))
    part0 = slice_ass(doc, 0.0, offset)
    part1 = slice_ass(doc, offset, offset + (b1 - a1))
    assert len(_dialogues(part0)) == 1 and len(_dialogues(part1)) == 1
    # Held to the end of part 0's window, and resumed at the start of part 1's.
    assert _ass_sec(_dialogues(part0)[0].split(",", 9)[2]) == \
        pytest.approx(offset, abs=0.01)
    assert _ass_sec(_dialogues(part1)[0].split(",", 9)[1]) == 0.0


# ── render: the burn really uses the sliced document ──────────────────────────


@_FFMPEG
def test_the_parts_carry_the_editors_document_not_a_regeneration(tmp_path,
                                                                 monkeypatch):
    """What reaches ffmpeg is the shipped document, sliced — not a rebuild.

    The old path called ``ass_for_candidate`` on a sub-candidate, so an
    editor's cue existed on disk and was never burned. Capturing the ASS each
    part is handed catches that directly: the editor's text has to appear in a
    part, and the transcript's words have to be absent.
    """
    from tests.test_render import _flat_source

    c = _two_interval_candidate()
    a0, b0 = retention_intervals(c)[0]
    offset = b0 - a0
    source = _flat_source(tmp_path / "source.mp4", dur=5.0)
    # Two cues no generation from this candidate could produce: they are in the
    # middle of words, they do not follow the word boundaries, and the second
    # one starts across the removed gap.
    ass = tmp_path / "edited.ass"
    ass.write_text(_ass(
        (0.10, 0.90, r"{\b1}EDITED{\b0} part zero"),
        (offset + 0.10, offset + 0.90, r"{\b1}EDITED{\b0} part one"),
    ), encoding="utf-8")

    # ``render_clip`` deletes its parts directory on the way out, so the ASS has
    # to be read while the part is still being rendered.
    handed: list[str] = []
    real = render_mod._render_one

    def spy(src, start, end, out, part_ass, vertical, fps):
        if part_ass is not None:
            handed.append(Path(part_ass).read_text(encoding="utf-8"))
        return real(src, start, end, out, part_ass, vertical, fps)

    monkeypatch.setattr(render_mod, "_render_one", spy)
    out = render_mod.render_clip(source, c, tmp_path / "clip.mp4", ass=ass)
    assert out.is_file(), "the multi-part render did not produce a deliverable"
    assert len(handed) == 2, f"expected two burned parts, got {len(handed)}"

    # The editor's distinctive cues are what reach the burn, one per part, and
    # the transcript the generator would have used is nowhere in either.
    assert r"{\b1}EDITED{\b0} part zero" in handed[0]
    assert r"{\b1}EDITED{\b0} part one" in handed[1]
    assert "alpha" not in handed[0] and "alpha" not in handed[1], (
        "the part ASS was regenerated from the transcript instead of sliced "
        "from the shipped document")
    # A multi-part burn must slice; the whole document cannot be handed to a
    # single part, because the other part's cues would burn at the wrong time.
    assert handed[0] != ass.read_text(encoding="utf-8")
    assert handed[1] != ass.read_text(encoding="utf-8")


@_FFMPEG
def test_each_part_burns_only_its_own_cue(tmp_path):
    """Each cue burns its own part for exactly its own duration, and nothing between.

    A flat source makes the uncaptioned render uniform, so any pixel that
    differs has to be the subtitle filter. The two cues are written at the same
    part-local window, so each must light a contiguous run inside its own part
    and the two runs must be the same length — the cue's duration in frames.
    The frames between them, across the concatenation seam, must stay clean:
    that seam is where a mis-sliced document would smear one part's cue into
    the other.

    The run length is measured rather than the absolute frame indices, because
    the indices depend on a timing detail that is deliberately not pinned here.
    Input seeking opens a part on the first source frame at or after the cut, so
    a part starting at 1.94 s on a 15 fps grid opens on the frame at 2.00 s and
    the container labels that frame 0.066 s rather than 0. libass still times the
    cue against the media the part actually holds — the burn lands on the frames
    carrying the speech the cue describes — so the *pixels* are right and only
    the container's own labels are a frame off. A viewer of the deliverable sees
    the subtitle with the speech; re-deriving the expected indices from the
    labels would assert the artifact instead of the behaviour.
    """
    from tests.test_render import _flat_source, _frames

    c = _two_interval_candidate()
    intervals = retention_intervals(c)
    (a0, b0), _ = intervals
    offset, fps = b0 - a0, 15.0
    source = _flat_source(tmp_path / "source.mp4", dur=5.0)
    ass = tmp_path / "edited.ass"
    ass.write_text(_ass(
        (0.10, 0.90, "EDITED PART ZERO"),
        (offset + 0.10, offset + 0.90, "EDITED PART ONE"),
    ), encoding="utf-8")

    burned, _ = _frames(
        render_mod.render_clip(source, c, tmp_path / "burn.mp4", ass=ass, fps=fps))
    control, _ = _frames(
        render_mod.render_clip(source, c, tmp_path / "ctrl.mp4", fps=fps))
    assert len(burned) == len(control), "the captioned and clean renders differ in length"

    # One ink flag per frame: the source is flat, so any differing pixel is the
    # subtitle filter, and asking which frames differ is asking which frames hold
    # a cue.
    per_frame = [bool((b.astype(">i2") - c_.astype(">i2")).max() > 20)
                 for b, c_ in zip(burned, control)]
    assert any(per_frame), "neither editor cue reached the pixels"

    # Where the concatenation seam sits: the first part is exactly this many
    # frames, by construction (``_frame_count`` is what the render is pinned to).
    seam = round((b0 - a0) * fps)

    def run(lo: int, hi: int) -> list[int]:
        return [i for i in range(lo, hi) if per_frame[i]]

    run0 = run(0, seam)
    run1 = run(seam, len(per_frame))
    assert run0, "part zero's cue never burned"
    assert run1, "part one's cue never burned"
    # Contiguous, and each wholly inside its own part.
    assert run0 == list(range(run0[0], run0[-1] + 1)), "part zero's cue is not contiguous"
    assert run1 == list(range(run1[0], run1[-1] + 1)), "part one's cue is not contiguous"
    assert run0[-1] < seam, "part zero's cue burned past the seam into part one"
    assert run1[0] >= seam, "part one's cue burned back across the seam into part zero"
    # Each cue lights exactly its own duration in frames, and no more.
    want = round((0.90 - 0.10) * fps)
    assert len(run0) == want, (
        f"part zero burned {len(run0)} frames for a {want}-frame cue")
    assert len(run1) == want, (
        f"part one burned {len(run1)} frames for a {want}-frame cue")
    # A cue clipped to its part cannot reach the last frame of the deliverable.
    assert run1[-1] < len(per_frame) - 1, (
        "the last frame carries ink, so cue one was not clipped to its part")
