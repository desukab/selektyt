"""Captions for the final edited timeline.

The thing under test is not "captions exist" but that they are *synchronized
to the edit*: a word spoken at source second S must be captioned at the
timeline second the edit actually plays it, and every cue must be walkable
back to source timecode.
"""

from __future__ import annotations

import json
import re

import pytest

from engine import timecode
from engine.caption_director import caption_width
from engine.timeline import Timeline
from engine.timeline_captions import (
    CaptionConfig,
    Transcript,
    build_cues,
    captions_metadata,
    srt_for_timeline,
    timeline_words,
    write_captions,
)
from models.core import Word
from tests.timeline_fixtures import (
    ENGLISH_WORDS,
    TELUGU_TEXT,
    TELUGU_WORDS,
    english_timeline,
    telugu_timeline,
    two_clip_timeline,
)


def _transcript_for(timeline: Timeline, words, text="") -> Transcript:
    media = timeline.clips[0].source
    return Transcript(source_key=media.key, words=tuple(words), text=text)


def _parse_srt(path):
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    cues = []
    for block in re.split(r"\n\s*\n", raw):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        m = re.fullmatch(
            r"(?P<st>\d{1,2}:\d{2}:\d{2},\d{3}) --> (?P<en>\d{1,2}:\d{2}:\d{2},\d{3})",
            lines[1],
        )
        cues.append(
            {
                "idx": int(lines[0]),
                "st": m["st"] if m else "",
                "en": m["en"] if m else "",
                "text": "\n".join(lines[2:]),
            }
        )
    return cues


def _parse_ass(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return {
        "header": [ln for ln in lines if ln.startswith("PlayResX") or ln.startswith("ScriptType")],
        "style": [ln for ln in lines if ln.startswith("Style:")],
        "dialogue": [ln for ln in lines if ln.startswith("Dialogue:")],
    }


# ---------------------------------------------------------------- placement


def test_words_shift_onto_the_edited_timeline():
    """A word at source 1.0s inside clip 1 (source 0-6 -> timeline 0-6) stays
    put; the same word inside a clip placed later moves with the clip."""
    tl = telugu_timeline()
    placed = timeline_words(tl, [_transcript_for(tl, TELUGU_WORDS)])
    starts = {w.text: w.start for w in placed}
    # Clip 1 is an identity mapping of the source span.
    assert starts["ఇది"] == pytest.approx(1.0)
    # Words outside every clip (6-20s gap) are dropped, not invented.
    assert all(w.start < 6.0 or w.source_start >= 20.0 for w in placed)


def test_words_from_the_second_clip_land_after_the_cut():
    tl = two_clip_timeline()
    media = tl.clips[0].source
    words = (Word("first", 12.0, 12.5), Word("second", 42.0, 42.5))
    placed = timeline_words(tl, [Transcript(source_key=media.key, words=words)])
    # source 12s is in clip 1 (10-20 -> 0-10) at timeline 2.0
    assert placed[0].start == pytest.approx(2.0)
    # source 42s is in clip 2 (40-45 -> 10-15) at timeline 12.0
    assert placed[1].start == pytest.approx(12.0)


def test_words_spanning_a_cut_boundary_are_clamped_not_moved():
    tl = two_clip_timeline()
    media = tl.clips[0].source
    # A word from 18s to 22s straddles the cut at source 20s.
    placed = timeline_words(
        tl, [Transcript(source_key=media.key, words=(Word("bridge", 18.0, 22.0),))]
    )
    assert len(placed) == 1
    w = placed[0]
    assert w.source_start == pytest.approx(18.0)
    assert w.source_end == pytest.approx(20.0)  # clamped to the clip
    assert w.end - w.start == pytest.approx(2.0)


def test_words_with_no_overlap_are_dropped():
    tl = telugu_timeline()
    media = tl.clips[0].source
    placed = timeline_words(
        tl, [Transcript(source_key=media.key, words=(Word("gap", 10.0, 15.0),))]
    )
    assert placed == []


# ------------------------------------------------------------------ grouping


def test_cues_split_on_sentence_terminators():
    tl = telugu_timeline()
    cues = build_cues(
        timeline_words(tl, [_transcript_for(tl, TELUGU_WORDS)]), tl
    )
    texts = [c.text.replace("\n", " ") for c in cues]
    # The danda closes the first sentence, so the two sentences do not share
    # a cue.
    assert any("పరీక్ష।" in t and "మనం" not in t for t in texts)
    assert any("నేర్చుకుందాం।" in t for t in texts)


def test_cues_never_bridge_a_hard_cut():
    tl = two_clip_timeline()
    media = tl.clips[0].source
    words = tuple(Word(f"w{i}", 11.0 + i * 0.5, 11.5 + i * 0.5) for i in range(20))
    cues = build_cues(
        timeline_words(tl, [Transcript(source_key=media.key, words=words)]), tl
    )
    for cue in cues:
        clips = {w.clip for w in cue.words}
        assert len(clips) == 1, "a cue must not span two clips"


def test_cues_respect_max_lines_and_width():
    tl = english_timeline()
    config = CaptionConfig(max_line_width=20, max_lines=2)
    cues = build_cues(timeline_words(tl, [_transcript_for(tl, ENGLISH_WORDS)]), tl, config)
    for cue in cues:
        assert len(cue.lines) <= config.max_lines
        for line in cue.lines:
            # Whitespace between words is not drawn, so measure per glyph.
            # The shared policy, not a local copy: the wrapper and the ASS/SRT
            # writers must agree on how wide a glyph is.
            assert caption_width(line.replace(" ", "")) <= config.max_line_width, (
                line, caption_width(line)
            )


def test_cues_do_not_overlap_and_stay_inside_the_timeline():
    tl = english_timeline()
    cues = build_cues(timeline_words(tl, [_transcript_for(tl, ENGLISH_WORDS)]), tl)
    assert cues
    for a, b in zip(cues, cues[1:]):
        assert b.start >= a.end, "cues must not overlap"
    for cue in cues:
        assert cue.start >= 0.0
        assert cue.end <= tl.duration + 1e-6


def test_cues_hold_long_enough_to_read():
    """Each cue is held for its reading-speed minimum, or as long as it can
    be without running into the next cue or past its clip — whichever binds
    first. Asserting the rule, not a magic number."""
    tl = english_timeline()
    config = CaptionConfig(reading_cps=17.0)
    cues = build_cues(timeline_words(tl, [_transcript_for(tl, ENGLISH_WORDS)]), tl, config)
    for i, cue in enumerate(cues):
        text = cue.text.replace("\n", " ")
        width = sum(2 if ord(ch) >= 0x0900 else 1 for ch in text if not text.isspace())
        want = min(
            config.max_cue_seconds, max(config.min_cue_seconds, width / config.reading_cps)
        )
        clip_end = max(
            (c.timeline_out for c in tl.clips if c.name == cue.words[0].clip),
            default=cue.end,
        )
        available = clip_end - cue.start
        if i + 1 < len(cues):
            available = min(available, cues[i + 1].start - cue.start - config.inter_cue_gap)
        got = cue.end - cue.start
        assert got >= min(want, max(available, 0.08)) - 1e-6, (cue.text, got, want, available)


# --------------------------------------------------- approximation is visible


def test_text_only_transcript_is_marked_approximate_everywhere():
    tl = telugu_timeline()
    tr = Transcript(source_key=tl.clips[0].source.key, text=TELUGU_TEXT)
    words = timeline_words(tl, [tr])
    assert words
    assert all(w.approximate for w in words)
    meta = captions_metadata(tl, [tr])
    assert meta["approximate"] is True
    assert "మొదటి భాగం" in meta["approximate_reason"]
    assert all(c["approximate"] for c in meta["cues"])


def test_word_timed_transcript_is_not_marked_approximate():
    tl = telugu_timeline()
    meta = captions_metadata(tl, [_transcript_for(tl, TELUGU_WORDS)])
    assert meta["approximate"] is False
    assert meta["approximate_reason"] == ""
    assert all(not c["approximate"] for c in meta["cues"])


def test_approximate_text_is_split_by_edited_screen_time():
    """A text-only transcript is allocated across a media's clips in proportion
    to the screen time the *edit* gives each clip — not to how much of the
    source file it reads, which would hand a 6s keep of a 2-minute source a
    single word."""
    tl = telugu_timeline()  # keeps 6s then 4s of a 120s source
    tr = Transcript(source_key=tl.clips[0].source.key, text=TELUGU_TEXT)
    placed = timeline_words(tl, [tr])
    by_clip: dict[str, list[str]] = {}
    for w in placed:
        by_clip.setdefault(w.clip, []).append(w.text)
    # Every token survives somewhere...
    all_tokens = [t for toks in by_clip.values() for t in toks]
    assert all_tokens == TELUGU_TEXT.split()
    # ...and the longer clip carries the larger share of the text.
    assert len(by_clip[tl.clips[0].name]) > len(by_clip[tl.clips[1].name])


def test_approximate_cues_stay_inside_their_clip():
    tl = telugu_timeline()
    tr = Transcript(source_key=tl.clips[0].source.key, text=TELUGU_TEXT)
    for w in timeline_words(tl, [tr]):
        clip = next(c for c in tl.clips if c.name == w.clip)
        assert clip.timeline_in <= w.start and w.end <= clip.timeline_out + 1e-6


def test_ass_omits_karaoke_tags_on_approximate_timings():
    """Karaoke on invented timings would claim to know where speech is; the
    real assertion is in ``test_ass_karaoke_is_absent_for_approximate_cues``."""
    tl = telugu_timeline()
    tr = Transcript(source_key=tl.clips[0].source.key, text=TELUGU_TEXT)
    placed = timeline_words(tl, [tr])
    assert all(w.approximate for w in placed)


def test_transcript_requires_words_or_text():
    with pytest.raises(ValueError, match="neither words nor text"):
        Transcript(source_key="x")


# ------------------------------------------------------------------- writers


def test_srt_and_ass_are_cue_for_cue_identical(tmp_path):
    tl = english_timeline()
    tr = [_transcript_for(tl, ENGLISH_WORDS)]
    from engine.timeline_captions import ass_for_timeline

    srt = srt_for_timeline(tl, tr, tmp_path / "e.srt")
    ass = ass_for_timeline(tl, tr, tmp_path / "e.ass")
    cues = _parse_srt(srt)
    dialogues = _parse_ass(ass)["dialogue"]
    assert len(cues) == len(dialogues)
    for cue, line in zip(cues, dialogues):
        fields = line.split(",", 9)  # Layer,Start,End,Style,...,Text
        assert len(fields) == 10
        # Same cue window, to the centisecond (ASS writes CS, SRT writes MS).
        assert abs(_cs_seconds(fields[1]) - _to_seconds(cue["st"])) < 0.011
        assert abs(_cs_seconds(fields[2]) - _to_seconds(cue["en"])) < 0.011
        # Same text once the ASS override tags are stripped. ``\N`` is the ASS
        # line break, so it is normalised like the SRT's newline: the burned
        # track must break where this writer measured the break, not where
        # libass re-wraps a single long line.
        srt_text = cue["text"].replace("\n", " ")
        ass_text = re.sub(r"\{[^}]*\}", "", fields[9]).strip().replace(r"\N", " ")
        assert srt_text.split() == ass_text.split()


def test_srt_is_well_formed_and_ordered(tmp_path):
    tl = english_timeline()
    out = srt_for_timeline(tl, [_transcript_for(tl, ENGLISH_WORDS)], tmp_path / "e.srt")
    cues = _parse_srt(out)
    assert cues
    assert [c["idx"] for c in cues] == list(range(1, len(cues) + 1))
    assert all(re.fullmatch(r"\d{1,2}:\d{2}:\d{2},\d{3}", c["st"]) for c in cues)
    assert all(re.fullmatch(r"\d{1,2}:\d{2}:\d{2},\d{3}", c["en"]) for c in cues)
    assert all(c["text"].strip() for c in cues)
    starts = [_to_seconds(c["st"]) for c in cues]
    assert starts == sorted(starts)


def _to_seconds(tc: str) -> float:
    h, m, rest = tc.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _cs_seconds(tc: str) -> float:
    """ASS centisecond timestamp → seconds."""
    h, m, rest = tc.split(":")
    s, cs = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100


def test_srt_carries_every_surviving_word(tmp_path):
    tl = telugu_timeline()
    out = srt_for_timeline(tl, [_transcript_for(tl, TELUGU_WORDS)], tmp_path / "t.srt")
    body = out.read_text(encoding="utf-8")
    for w in TELUGU_WORDS:
        assert w.text in body


def test_ass_header_style_and_safe_area(tmp_path):
    tl = english_timeline()
    from engine.timeline_captions import ass_for_timeline

    path = ass_for_timeline(tl, [_transcript_for(tl, ENGLISH_WORDS)], tmp_path / "e.ass")
    parsed = _parse_ass(path)
    assert any("ScriptType: v4.00+" in ln for ln in parsed["header"])
    assert any(ln.startswith("Style: Default,") for ln in parsed["style"])
    assert parsed["dialogue"]
    # Karaoke timings are present for measured words.
    assert any(r"{\k" in ln for ln in parsed["dialogue"])


def test_ass_karaoke_is_absent_for_approximate_cues(tmp_path):
    from engine.timeline_captions import ass_for_timeline

    tl = telugu_timeline()
    tr = Transcript(source_key=tl.clips[0].source.key, text=TELUGU_TEXT)
    path = ass_for_timeline(tl, [tr], tmp_path / "t.ass")
    parsed = _parse_ass(path)
    assert parsed["dialogue"]
    assert not any(r"{\k" in ln for ln in parsed["dialogue"])


def _glued_transcript(timeline: Timeline) -> Transcript:
    """Words as faster-whisper emits them: a number and a hyphenation split
    across raw tokens whose leading space is the only glue.

    ``'1'`` + ``',024'`` must read ``1,024`` and ``'short'`` + ``'-lived'``
    must read ``short-lived``. The emitters used to strip each token and
    space-join, which re-split both and shipped ``the 1 ,024 pixels were
    short -lived`` -- the exact corruption ``engine.edit_plan.join_words``
    exists to prevent, in the writer that never called it.
    """
    words = [
        Word("the", 0.00, 0.20),
        Word(" 1", 0.20, 0.40),
        Word(",024", 0.40, 0.60),
        Word(" pixels", 0.60, 0.80),
        Word(" were", 0.80, 1.00),
        Word("short", 1.00, 1.20),
        Word("-lived", 1.20, 1.40),
        Word(" but", 1.40, 1.60),
        Word(" bright", 1.60, 1.80),
    ]
    return Transcript(source_key=timeline.clips[0].source.key,
                      words=tuple(words))


def test_timeline_captions_glue_split_numbers_and_hyphens(tmp_path):
    """Both deliverables of the same cue must read ``1,024`` and
    ``short-lived`` -- the SRT and the burned ASS agree with each other and
    with the transcript."""
    from engine.timeline_captions import ass_for_timeline

    tl = english_timeline()
    tr = [_glued_transcript(tl)]

    srt = srt_for_timeline(tl, tr, tmp_path / "g.srt")
    ass = ass_for_timeline(tl, tr, tmp_path / "g.ass")

    srt_body = srt.read_text(encoding="utf-8")
    ass_visible = re.sub(r"\{[^}]*\}", "",
                         ass.read_text(encoding="utf-8")).replace(r"\N", " ")

    for needle in ("1,024", "short-lived"):
        assert needle in srt_body, f"SRT lost {needle!r}: {srt_body!r}"
        assert needle in ass_visible, f"ASS lost {needle!r}: {ass_visible!r}"
    for corruption in ("1 ,024", "short -lived"):
        assert corruption not in srt_body, f"SRT re-split: {corruption!r}"
        assert corruption not in ass_visible, f"ASS re-split: {corruption!r}"


def test_ass_burns_the_measured_line_breaks(tmp_path):
    """The burned track breaks where this writer measured the break.

    ``_wrap`` returns the lines; the ASS used to ignore them and hand libass
    one long line, so the burned cue re-wrapped to libass's own metrics and
    could break a cue this writer had judged unbreakable. The ``\\N`` in the
    dialogue is the measured layout being shipped instead.
    """
    from engine.timeline_captions import (CaptionConfig, _force_wrap, _wrap,
                                          ass_for_timeline)

    tl = english_timeline()
    tr = [_transcript_for(tl, ENGLISH_WORDS)]
    config = CaptionConfig()
    words = timeline_words(tl, tr, config)
    cues = build_cues(words, tl, config)
    multi = [c for c in cues if len(c.lines) > 1]
    if not multi:
        pytest.skip("no cue wraps on this fixture; the check needs one")
    path = ass_for_timeline(tl, tr, tmp_path / "w.ass")
    body = path.read_text(encoding="utf-8")
    # Every cue that wrapped carries its line break into the dialogue.
    for cue in multi:
        assert any(r"\N" in ln for ln in body.splitlines()
                   if f",{_ass_ts_of(cue)}," in ln), (
            f"cue {cue.index} wrapped to {cue.lines!r} but burned as one line")

    # And the wrap functions themselves never split a glued token, even on a
    # budget that forces breaks everywhere.
    tight = CaptionConfig(max_line_width=8)
    for words_for_cue in ([w for w in cue.words] for cue in cues):
        wrapped = _wrap(words_for_cue, tight) or _force_wrap(words_for_cue, tight)
        assert wrapped, "a cue that does not fit must be force-wrapped, not dropped"
        joined = " ".join(wrapped)
        assert " ,0" not in joined and " -" not in joined, joined


def _ass_ts_of(cue) -> str:
    from engine.timeline_captions import _ass_ts
    return f"{_ass_ts(cue.start)},{_ass_ts(cue.end)}"


def test_write_captions_emits_three_files(tmp_path):
    tl = english_timeline()
    paths = write_captions(tl, [_transcript_for(tl, ENGLISH_WORDS)], tmp_path / "caps")
    assert all(p.is_file() for p in paths.values())
    meta = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert meta["cue_count"] == len(_parse_srt(paths["srt"]))
    assert meta["title"] == tl.title
    assert meta["source_media"][0]["path"] == tl.clips[0].source.path


def test_empty_transcript_yields_empty_captions_without_crashing(tmp_path):
    tl = telugu_timeline()
    out = tmp_path / "empty.srt"
    srt_for_timeline(tl, [], out)
    assert out.read_text(encoding="utf-8") == ""
    meta = captions_metadata(tl, [])
    assert meta["cue_count"] == 0


# -------------------------------------------------------------- traceability


def test_every_cue_traces_to_source_timecode():
    tl = two_clip_timeline()
    media = tl.clips[0].source
    words = tuple(Word(f"w{i}", 11.0 + i, 11.5 + i) for i in range(4))
    cues = build_cues(
        timeline_words(tl, [Transcript(source_key=media.key, words=words)]), tl
    )
    assert cues
    for cue in cues:
        assert cue.source["spans"], "every cue must carry a source span"
        for span in cue.source["spans"]:
            assert span["source_path"] == media.path
            assert re.fullmatch(r"\d{2}:\d{2}:\d{2}[;:]\d{2}", span["source_timecode_in"])
            assert span["source_timecode_out"] != span["source_timecode_in"]
            assert span["timeline_in"] >= 0.0
            # The cue's timeline window is inside the span it claims.
            assert cue.start >= span["timeline_in"] - 1e-6
            assert cue.end <= span["timeline_out"] + 1.0  # tail allowance


def test_metadata_cue_source_recomputes_to_the_same_frames():
    tl = telugu_timeline()
    meta = captions_metadata(tl, [_transcript_for(tl, TELUGU_WORDS)])
    rate = tl.clips[0].rate
    for cue in meta["cues"]:
        for span in cue["source"]["spans"]:
            a = timecode.parse_smpte(span["source_timecode_in"], tl.clips[0].source.fps)
            b = timecode.parse_smpte(span["source_timecode_out"], tl.clips[0].source.fps)
            # The reported span is a whole number of frames at the media rate.
            frames = (b - a) / rate.frame_seconds()
            assert abs(frames - round(frames)) < 1e-6
