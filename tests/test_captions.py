"""Native SRT export + ASS/SRT cue parity.

`srt_for_candidate` must track `ass_for_candidate` cue-for-cue (same grouping
and timing) so the platform-neutral fallback never diverges from the styled
track. Word-timed candidates and text-only candidates (no word timestamps) are
both covered.
"""
from __future__ import annotations

import re
from pathlib import Path

from engine.captions import ass_for_candidate, srt_for_candidate
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

    def srt_sec(ts: str) -> float:
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    def ass_sec(ts: str) -> float:
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    for cue, line in zip(srt_cues, ass_lines):
        fields = line.split(",", 9)
        assert abs(srt_sec(cue["st"]) - ass_sec(fields[1])) < 0.02, (
            f"ASS and SRT disagree on cue {cue['idx']} start"
        )
        assert abs(srt_sec(cue["en"]) - ass_sec(fields[2])) < 0.02, (
            f"ASS and SRT disagree on cue {cue['idx']} end"
        )