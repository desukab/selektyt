"""Native SRT export + ASS/SRT cue parity.

`srt_for_candidate` must track `ass_for_candidate` cue-for-cue (same grouping
and timing) so the platform-neutral fallback never diverges from the styled
track. Word-timed candidates and text-only candidates (no word timestamps) are
both covered.
"""
from __future__ import annotations

import re
from pathlib import Path

from engine.captions import (
    ass_for_candidate,
    caption_presets,
    preset_for,
    srt_for_candidate,
)
from engine.edit_plan import retention_intervals
from models.core import Candidate, Word


def _candidate(words: list[Word], text: str | None = None) -> Candidate:
    start = words[0].start
    end = words[-1].end
    return Candidate(
        start, end, text if text is not None else " ".join(w.text for w in words),
        "story", "hook", "payoff",
        editorial_score=0.8, virality_score=0.7, context_score=0.9,
        confidence=0.9, reason="t", visual_potential=0.8, words=words,
    )


def _timed_words() -> list[Word]:
    return [Word(t, i * 0.4, i * 0.4 + 0.35) for i, t in enumerate(
        ["this", "is", "a", "test", "of", "the", "caption", "system"], start=1)]


def _parse_srt(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        idx, timing, text = lines[0], lines[1], " ".join(lines[2:])
        m = re.fullmatch(r"(?P<st>\d{1,2}:\d{2}:\d{2},\d{3}) --> (?P<en>\d{1,2}:\d{2}:\d{2},\d{3})", timing)
        cues.append({"idx": idx, "st": m["st"] if m else "", "en": m["en"] if m else "", "text": text})
    return cues


def _parse_ass_dialogues(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln for ln in lines if ln.startswith("Dialogue:")]


def _srt_sec(ts: str) -> float:
    """``HH:MM:SS,mmm`` → seconds, so a cue bound can be compared to a duration."""
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def test_srt_is_well_formed_and_ordered(tmp_path):
    out = tmp_path / "captions" / "01.srt"
    srt_for_candidate(_candidate(_timed_words()), out)
    assert out.is_file()
    cues = _parse_srt(out)
    assert cues, "SRT must emit at least one cue"
    assert [int(c["idx"]) for c in cues] == list(range(1, len(cues) + 1))
    assert all(re.fullmatch(r"\d{1,2}:\d{2}:\d{2},\d{3}", c["st"]) for c in cues)
    assert all(re.fullmatch(r"\d{1,2}:\d{2}:\d{2},\d{3}", c["en"]) for c in cues)
    assert all(c["text"].strip() for c in cues)


def test_srt_text_covers_every_word(tmp_path):
    words = _timed_words()
    out = tmp_path / "01.srt"
    srt_for_candidate(_candidate(words), out)
    body = out.read_text(encoding="utf-8")
    for w in words:
        assert w.text in body


def test_srt_and_ass_cues_match_one_for_one(tmp_path):
    c = _candidate(_timed_words())
    srt = srt_for_candidate(c, tmp_path / "01.srt")
    ass = ass_for_candidate(c, tmp_path / "01.ass")
    srt_cues = _parse_srt(srt)
    ass_lines = _parse_ass_dialogues(ass)
    assert len(srt_cues) == len(ass_lines)


def _number_and_hyphen_words() -> list[Word]:
    """Tokens as faster-whisper actually emits them, raw and unstripped.

    A four-digit number comes apart as ``'1'`` and ``',024'``, and a hyphenated
    word as ``'short'`` and ``'-lived'``. The leading space is load-bearing: it
    is what makes the pieces glue back into ``1,024`` and ``short-lived``, and
    the fragment tokens carry no space at all. ``providers/whisper.py`` stores
    ``w.word`` verbatim and nothing downstream strips it, so this is the shape
    the caption writers actually meet.

    These tokens were previously pre-stripped while the docstring claimed
    otherwise, which is why this suite passed on a defect the real run shipped:
    the karaoke emitter's separator test sees the leading space, the fixture
    had none, and the burned-in ASS read ``Thisisabout`` while the SRT beside
    it read ``This is about``.
    """
    return [
        Word("This", 0.0, 0.3),
        Word(" is", 0.3, 0.6),
        Word(" about", 0.6, 0.9),
        Word(" the", 0.9, 1.2),
        Word("1", 1.2, 1.5),
        Word(",024", 1.5, 1.8),
        Word(" pixels", 1.8, 2.1),
        Word("short", 2.1, 2.4),
        Word("-lived", 2.4, 2.7),
    ]


def test_captions_glue_split_numbers_and_hyphens_not_re_split_them(tmp_path):
    """The caption track must agree with the transcript it captions.

    ``align_span`` glues these tokens; the emitters used to ``.strip()`` and
    space-join, re-corrupting exactly what had been repaired, so the shipped
    SRT read ``"the 1 ,024"`` while the transcript read ``"1,024"``. A caption
    that disagrees with the text it captions is a wrong deliverable, and the
    failure looks entirely plausible — the cue is well formed, it just says
    the wrong thing.
    """
    words = _number_and_hyphen_words()
    c = _candidate(words)
    srt = srt_for_candidate(c, tmp_path / "01.srt")
    ass = ass_for_candidate(c, tmp_path / "01.ass")
    srt_body = srt.read_text(encoding="utf-8")
    ass_body = ass.read_text(encoding="utf-8")

    for needle in ("1,024", "short-lived"):
        assert needle in srt_body, f"SRT lost {needle!r}: {srt_body!r}"
    # The ASS dialogue line carries karaoke tags between tokens, so compare
    # visible text rather than raw bytes.
    ass_visible = re.sub(r"\{\\[^}]*\}", "", ass_body)
    for needle in ("1,024", "short-lived"):
        assert needle in ass_visible, f"ASS lost {needle!r}: {ass_visible!r}"
    # The specific corruption: a space inserted inside a number or a hyphenation.
    assert "1 ,024" not in srt_body
    assert "short -lived" not in srt_body
    assert "1 ,024" not in ass_visible
    assert "short -lived" not in ass_visible


def test_karaoke_keeps_glued_tokens_adjacent():
    """The ASS karaoke path tags each word separately, so it must still glue.

    Karaoke tags sit *between* tokens (``{\\k30}1{\\k30},024``), so the check
    strips them first: what is left must read ``1,024``, with no space inside
    the number.
    """
    from engine.captions import _karaoke_text

    text = _karaoke_text(_number_and_hyphen_words(), set())
    visible = re.sub(r"\{\\[^}]*\}", "", text)
    assert "1,024" in visible, visible
    assert "short-lived" in visible, visible
    assert "1 ,024" not in visible
    assert "short -lived" not in visible


def test_karaoke_and_srt_agree_on_the_same_words(tmp_path):
    """The burned-in track and the file beside it must read identically.

    The karaoke emitter strips its tokens and decided the separator on the raw
    text, so a whisper token's leading space failed ``re.match(r'\\w', raw)``,
    no separator was emitted, and the strip destroyed the gluing space the
    token already carried. The burned-in ASS read ``Thisisabout`` while the SRT
    for the same candidate read ``This is about`` -- two deliverables of one
    clip disagreeing with each other, and every existing test passed because
    the shared fixture had pre-stripped the tokens its docstring called raw.
    """
    from engine.captions import _karaoke_text, ass_for_candidate, srt_for_candidate
    from engine.edit_plan import join_words

    words = _number_and_hyphen_words()
    c = _candidate(words)

    srt = srt_for_candidate(c, tmp_path / "agree.srt")
    ass = ass_for_candidate(c, tmp_path / "agree.ass")

    srt_body = srt.read_text(encoding="utf-8")
    ass_visible = re.sub(r"\{\\[^}]*\}", "", ass.read_text(encoding="utf-8"))

    # The visible ASS text is the join of the same words the SRT carries, and
    # neither may re-split what ``align_span`` repaired.
    assert "This is about" in ass_visible, ass_visible
    assert "This is about" in srt_body, srt_body
    assert "1,024" in ass_visible and "1,024" in srt_body
    assert "short-lived" in ass_visible and "short-lived" in srt_body
    assert "Thisisabout" not in ass_visible, ass_visible
    assert "1 ,024" not in ass_visible and "1 ,024" not in srt_body

    # And the karaoke builder itself agrees with the canonical joiner.
    assert re.sub(r"\{[^}]*\}", "", _karaoke_text(words, set())) == join_words(words)


def test_srt_handles_missing_word_timestamps(tmp_path):
    c = Candidate(
        0.0, 4.0, "caption fallback works even without word timings",
        "story", "hook", "payoff",
        editorial_score=0.8, virality_score=0.7, context_score=0.9,
        confidence=0.9, reason="t", visual_potential=0.8, words=None,
    )
    out = tmp_path / "01.srt"
    srt_for_candidate(c, out)
    cues = _parse_srt(out)
    assert cues, "text-only candidates still produce cues"
    assert all(c["text"].strip() for c in cues)
    assert "without" in " ".join(c["text"] for c in cues)


def test_empty_candidate_writes_an_empty_file_not_a_crash(tmp_path):
    c = Candidate(0.0, 1.0, "", "story", "h", "p", 0.1, 0.1, 0.1, 0.1, "t")
    out = tmp_path / "01.srt"
    srt_for_candidate(c, out)
    assert out.read_text(encoding="utf-8") == ""


def test_a_word_straddling_the_selection_end_is_not_captioned(tmp_path):
    """Regression: a word overlapping the selection end is in ``candidate.words``
    but is cut from the render, because ``retention_intervals`` snaps that end
    back to the word's start. A caption showing it describes audio the
    deliverable does not contain, and its cue ran past the end of the render.

    Measured on a real selection: a word at 235.220-235.380 s survived into the
    captions of a candidate ending 235.237 s, appeared in the SRT but not in the
    transcript, and its cue ended 0.06 s past the 52.42 s render.
    """
    words = [Word(f"w{i}", 10.0 + i * 0.4, 10.0 + i * 0.4 + 0.35)
             for i in range(9)]
    straddle = words[-1]
    # A word whose span crosses the selection end.
    trailing = Word("straddled", straddle.end + 0.04, straddle.end + 0.30)
    words.append(trailing)
    # The selection ends inside that trailing word.
    end = trailing.start + 0.017
    c = Candidate(10.0, end, " ".join(w.text for w in words[:-1]), "story",
                  "h", "p", 0.8, 0.7, 0.9, 0.9, "t", 0.8, words)
    assert trailing.start < c.end < trailing.end  # really straddling

    out = tmp_path / "01.srt"
    srt_for_candidate(c, out)
    body = out.read_text(encoding="utf-8")
    assert "straddled" not in body
    # And no cue may run past what the render actually contains.
    rendered = sum(b - a for a, b in retention_intervals(c))
    for cue in _parse_srt(out):
        assert _srt_sec(cue["en"]) <= rendered + 0.15


def _candidate_with_a_removed_pause():
    """A candidate whose retention intervals drop a 1.0s pause in the middle.

    The render concatenates the intervals, so a caption for a word after the
    pause must not be timed as if the pause were still on screen.

    The span is long enough that the edit survives ``retention_intervals``'s
    safety guard (it falls back to the intact source when a cut would remove
    more than 28% of the candidate), so this really is a two-interval edit.
    """
    words = [Word(f"w{i}", 10.0 + i * 0.4, 10.0 + i * 0.4 + 0.35) for i in range(9)]
    # 1.0s of silence here is removed by retention_intervals.
    t = words[-1].end
    words += [Word(f"v{i}", t + 1.0 + i * 0.4, t + 1.0 + i * 0.4 + 0.35) for i in range(9)]
    return Candidate(10.0, words[-1].end, " ".join(w.text for w in words), "story",
                     "h", "p", 0.8, 0.7, 0.9, 0.9, "t", 0.8, words)


def test_cue_times_account_for_removed_pauses(tmp_path):
    """Regression: cues were emitted at ``word.start - candidate.start``, a flat
    source offset that ignores the gaps the render cuts out. Every cue after
    the first removed pause ran early by the size of that pause, and the last
    cue could land past the end of the rendered file."""
    from engine.captions import _blocks_for_candidate, _cue_bounds, _source_to_edited
    from engine.edit_plan import retention_intervals

    c = _candidate_with_a_removed_pause()
    intervals = retention_intervals(c)
    assert len(intervals) >= 2, "this candidate must actually be cut into parts"
    kept = sum(b - a for a, b in intervals)
    assert kept < c.end - c.start

    cut = intervals[0][1]
    blocks = _blocks_for_candidate(c, 6)
    # The first block spoken after the cut is the one that used to drift.
    after = next(b for b in blocks if b.start >= intervals[1][0])
    st, _en = _cue_bounds(c, after)
    want = _source_to_edited(c, after.start)
    flat = after.start - c.start
    assert flat - want > 0.5, "fixture must remove a pause the viewer would notice"
    assert abs(st - want) < 1e-6, (
        f"a post-pause cue must be retimed onto the edited timeline: got {st:.3f}s, "
        f"want {want:.3f}s; the flat source offset would have given {flat:.3f}s"
    )

    # And the shipped files must not run past the end of the rendered clip.
    out = tmp_path / "01.srt"
    srt_for_candidate(c, out)
    for cue in _parse_srt(out):
        h, m, rest = cue["en"].split(":")
        s, ms = rest.split(",")
        end = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
        assert end <= kept + 1e-6, (
            f"cue {cue['idx']} ends at {cue['en']}, past the {kept:.2f}s render"
        )


def test_ass_and_srt_still_agree_after_retiming(tmp_path):
    """Retiming must apply to both tracks identically, or the ASS burned into
    the render and the SRT shipped beside it disagree."""
    c = _candidate_with_a_removed_pause()
    srt = srt_for_candidate(c, tmp_path / "01.srt")
    ass = ass_for_candidate(c, tmp_path / "01.ass")
    srt_cues = _parse_srt(srt)
    ass_lines = _parse_ass_dialogues(ass)
    assert len(srt_cues) == len(ass_lines)

    def ass_sec(ts: str) -> float:
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    for cue, line in zip(srt_cues, ass_lines):
        fields = line.split(",", 9)
        assert abs(_srt_sec(cue["st"]) - ass_sec(fields[1])) < 0.02, (
            f"ASS and SRT disagree on cue {cue['idx']} start"
        )
        assert abs(_srt_sec(cue["en"]) - ass_sec(fields[2])) < 0.02, (
            f"ASS and SRT disagree on cue {cue['idx']} end"
        )


# ── the presets behind SELEKYT_CAPTION_STYLE ──────────────────────────────


def test_every_named_style_renders_a_style_line(tmp_path, monkeypatch):
    """A style the writer cannot render is a style the UI must not offer. Each
    one is also exercised through the env var the sidecar sets, so the path the
    web shell takes is the one covered here."""
    c = _candidate(_timed_words())
    for name in caption_presets():
        monkeypatch.setenv("SELEKYT_CAPTION_STYLE", name)
        ass = ass_for_candidate(c, tmp_path / f"{name}.ass")
        assert f"Style: Default," in ass.read_text(encoding="utf-8")


def test_an_unknown_style_falls_back_not_to_a_crash(tmp_path, monkeypatch):
    """The CLI takes arbitrary strings, so a style a newer build removed still
    renders captions rather than failing the run. The sidecar refuses unknown
    names before this point; this is why that check belongs there and not here."""
    # ``preset_for(None)`` reads the ambient style, and a run earlier in the
    # suite may legitimately have set one.
    monkeypatch.delenv("SELEKYT_CAPTION_STYLE", raising=False)
    assert preset_for("a-style-that-does-not-exist") == preset_for("minimal")
    assert preset_for(None) == preset_for("minimal")


def test_a_style_name_is_matched_case_and_whitespace_insentively():
    """``--caption-style Punch`` and the sidecar's normalised ``punch`` must
    resolve to the same preset, or the dropdown and the CLI would drift apart."""
    assert preset_for("  PUNCH  ") == preset_for("punch")
