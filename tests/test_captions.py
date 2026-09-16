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