"""Unicode (Telugu) handling end to end.

A Telugu-script transcript must survive editorial scoring, context-guard
validation, caption generation and JSON round-tripping without mojibake. The
script is checked at every boundary: in-memory strings, JSON bytes on disk,
ASS/SRT caption text and the wire.
"""
from __future__ import annotations

import itertools
import json
import re

from tests.fixtures import editorial_fixtures as fx

_NEED_FFMPEG = fx.skip_no_ffmpeg
_TELUGU_RE = re.compile(r"[ఀ-౿]")
REPLACEMENT = "�"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _assert_script_clean(text: str) -> None:
    assert REPLACEMENT not in text, "mojibake: U+FFFD reached the output"
    assert _TELUGU_RE.search(text), "no Telugu script survived the stage"


# ── transcript layer ──


def test_transcript_json_round_trips_telugu_without_loss(tmp_path):
    transcript = fx.telugu_transcript()
    path = fx.write_transcript_json(tmp_path / "t.json", transcript, "te")

    # bytes on disk are valid UTF-8 and re-decode identically
    raw = path.read_bytes()
    raw.decode("utf-8", errors="strict")

    import selekyt.pipeline as pipeline_mod
    loaded, language = pipeline_mod._load_transcript(path)
    assert language == "te"
    assert len(loaded) == len(transcript)
    for original, back in zip(transcript, loaded):
        assert back.text == original.text
        assert [w.text for w in back.words] == [w.text for w in original.words]
        _assert_script_clean(back.text)


# ── editorial layer ──


def test_language_detection_identifies_telugu():
    from engine.context_guard import detect_languages

    transcript = fx.telugu_transcript()
    text = " ".join(s.text for s in transcript)
    assert "telugu" in detect_languages(text)
    assert detect_languages("only english words here") == ["english"]


def test_thoughts_preserve_the_script_and_yield_valid_candidates():
    from engine.context_guard import validate
    from engine.editorial import make_thoughts, rank

    transcript = fx.telugu_transcript()
    source_text = _norm(" ".join(s.text for s in transcript))

    thoughts = make_thoughts(transcript)
    assert thoughts, "Telugu must still segment into candidates"
    for t in thoughts[:25]:
        _assert_script_clean(t.text)
        # thoughts are contiguous runs of source sentences
        assert _norm(t.text) in source_text

    ranked = rank(thoughts, limit=4)
    assert ranked, "at least one Telugu clip must clear the editorial gate"
    for c in ranked:
        ok, why = validate(c)
        assert ok, f"a Telugu candidate the pipeline would ship failed the guard: {why}"
        _assert_script_clean(c.text)
        _assert_script_clean(c.hook)
        _assert_script_clean(c.payoff)
        assert 0.0 <= c.editorial_score <= 1.0
        assert c.story_type in {"fact", "question", "conflict", "revelation",
                                "story", "insight"}
        # scores are finite numbers, never NaN through a Unicode path
        assert all(isinstance(s, float) for s in
                   (c.editorial_score, c.virality_score, c.context_score,
                    c.coherence_score, c.total_score))


def test_scoring_reason_string_survives_telugu():
    from engine.editorial import score_candidate

    transcript = fx.telugu_transcript()
    c = score_candidate(transcript[0])
    _assert_script_clean(c.text)
    assert c.reason, "the editorial rationale must be recorded"
    assert REPLACEMENT not in c.reason


# ── a transcript with no terminal punctuation ──
#
# The Telugu fixture above is fully punctuated, so it proves the multilingual
# path only for transcripts that carry sentence marks. This section covers the
# other case: real ASR on Hindi can emit no Devanagari danda at all, which
# leaves the scan with no signal that a thought ended. The committed corpus
# shipped exactly that source producing zero candidates, and the user was told
# no story *passed the editorial gate* — a judgement that never ran, because
# nothing was ever constructed to be judged.

_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def test_the_unpunctuated_fixture_really_has_no_terminal_mark():
    transcript = fx.hindi_unpunctuated_transcript()
    assert transcript, "fixture must produce segments"
    for s in transcript:
        assert not re.search(r"[.!?।॥？！]$", s.text.strip()), (
            f"segment {s.start} carries a terminal mark; the fixture would no "
            f"longer exercise the unpunctuated path: {s.text[-12:]!r}"
        )
        assert _DEVANAGARI_RE.search(s.text), "the script must be Devanagari"
    # the whole source is shorter than the scan's max_sec - 2 relief valve, so
    # the valve cannot close a span either — nothing but the fallback can
    total = transcript[-1].end
    assert total < 55.0 - 2.0, (
        f"fixture runs {total:.1f} s and would reach the relief valve, so it "
        f"would stop testing the punctuation fallback"
    )


def test_an_unpunctuated_transcript_still_yields_rankable_candidates():
    """The regression: a transcript with no terminal mark must still be
    searched, and its thoughts must still be ranked and shipped, rather than
    collapsing to an empty candidate set."""
    from engine.context_guard import validate
    from engine.editorial import make_thoughts, rank

    transcript = fx.hindi_unpunctuated_transcript()
    source_text = _norm(" ".join(s.text for s in transcript))

    thoughts = make_thoughts(transcript)
    assert thoughts, (
        "an unpunctuated transcript produced no candidate spans at all — the "
        "scan searched nothing, and a refusal message about an editorial gate "
        "would be describing a step that never ran"
    )
    for t in thoughts:
        assert REPLACEMENT not in t.text
        assert _DEVANAGARI_RE.search(t.text), "Devanagari did not survive the scan"
        assert _norm(t.text) in source_text

    ranked = rank(thoughts, limit=4)
    assert ranked, "an eligible unpunctuated source must still produce clips"
    for c in ranked:
        ok, why = validate(c)
        assert ok, f"a clip the pipeline would ship failed the guard: {why}"
        assert _DEVANAGARI_RE.search(c.text)
        assert REPLACEMENT not in c.hook and REPLACEMENT not in c.payoff


def test_punctuation_is_not_needed_but_is_never_regretted():
    """The fallback is strictly additive: giving the same transcript terminal
    marks cannot lose a span the unpunctuated scan already found, so a
    punctuated source — the common case — is scanned exactly as before."""
    from engine.editorial import make_thoughts

    unpunctuated = make_thoughts(fx.hindi_unpunctuated_transcript())
    punctuated = make_thoughts(
        fx.build_transcript([s + "।" for s in fx.HINDI_STORIES])
    )
    assert unpunctuated and punctuated

    by_span = lambda spans: {(round(x.start, 2), round(x.end, 2)) for x in spans}
    assert by_span(punctuated) <= by_span(unpunctuated), (
        "punctuating the transcript lost a span the unpunctuated scan had "
        "found; the fallback must only ever add, never remove"
    )


# ── captions ──


def test_telugu_captions_carry_the_script_and_track_cue_for_cue(tmp_path):
    from engine.caption_director import caption_metadata
    from engine.captions import ass_for_candidate, srt_for_candidate
    from engine.editorial import make_thoughts, rank

    ranked = rank(make_thoughts(fx.telugu_transcript()), limit=1)
    assert ranked
    c = ranked[0]

    ass_path = tmp_path / "01.ass"
    srt_path = tmp_path / "01.srt"
    ass_for_candidate(c, ass_path)
    srt_for_candidate(c, srt_path)

    ass = ass_path.read_bytes().decode("utf-8", errors="strict")
    srt = srt_path.read_bytes().decode("utf-8", errors="strict")
    _assert_script_clean(ass)
    _assert_script_clean(srt)

    meta = caption_metadata(c)
    assert meta["blocks"]
    # every caption word is real spoken source text — no fabricated tokens
    transcript_text = _norm(" ".join(s.text for s in fx.telugu_transcript()))
    for block in meta["blocks"]:
        assert block["text"].strip()
        for token in block["text"].split():
            assert token in transcript_text, f"caption token {token!r} is unspoken"
    # the caption track is an ordered, non-overlapping cue sequence
    for a, b in zip(meta["blocks"], meta["blocks"][1:]):
        assert a["end"] <= b["start"], "caption metadata blocks overlap"

    # ASS and SRT carry one cue per block
    ass_cues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    srt_cues = [b for b in srt.strip().split("\n\n") if b.strip()]
    assert len(ass_cues) == len(srt_cues) == len(meta["blocks"])

    # cue times are relative to the clip and never overlap the next cue
    duration = c.end - c.start
    bounds = []
    for cue in ass_cues:
        m = re.match(
            r"Dialogue: 0,(\d+):(\d{2}):(\d{2})\.(\d{2}),(\d+):(\d{2}):(\d{2})\.(\d{2}),",
            cue,
        )
        assert m, f"malformed ASS cue: {cue!r}"
        st = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 100
        en = int(m[5]) * 3600 + int(m[6]) * 60 + int(m[7]) + int(m[8]) / 100
        assert 0.0 <= st < en <= duration + 0.5
        bounds.append((st, en))
    for (a_start, a_end), (b_start, _) in itertools.pairwise(bounds):
        assert a_end <= b_start + 1e-9, "ASS cues must not overlap"


def test_telugu_content_pack_is_extractive_and_script_safe(tmp_path):
    from engine.content_pack import build_content_pack
    from engine.editorial import make_thoughts, rank

    ranked = rank(make_thoughts(fx.telugu_transcript()), limit=1)
    assert ranked
    c = ranked[0]
    pack = build_content_pack(c)

    _assert_script_clean(pack["instagram_caption"])
    assert _norm(pack["instagram_caption"]) == _norm(c.text)
    assert pack["source_based"] is True
    for title in pack["titles"]:
        assert title
        assert _norm(title) in _norm(c.text), "titles must be spoken source words"
    # a pack with Telugu text must not be tagged as an English-only pack
    assert "#reels" in pack["hashtags"]


# ── full pipeline ──


@_NEED_FFMPEG
def test_full_telugu_run_preserves_the_script_in_every_artifact(tmp_path):
    """The whole engine, from transcript to rendered captions, must carry
    Telugu without mangling it, and every artifact must be valid UTF-8."""
    transcript = fx.telugu_transcript()
    source_text = _norm(" ".join(s.text for s in transcript))
    media = fx.media_for(transcript, tmp_path / "source" / "clip.mp4")

    project = fx.run_fixture_pipeline(
        tmp_path / "out", transcript, media, language="te", limit=4,
    )

    report = json.loads((project / "report.json").read_text(encoding="utf-8"))
    assert report["language"] == "te"
    assert report["selected_count"] >= 1

    # every JSON artifact is strict-UTF-8 decodable and script-clean
    for path in project.rglob("*.json"):
        text = path.read_bytes().decode("utf-8", errors="strict")
        assert REPLACEMENT not in text, f"mojibake in {path.name}"
        json.loads(text)

    selections = json.loads((project / "selections.json").read_text(encoding="utf-8"))
    editorial = json.loads(
        (project / "editorial_report.json").read_text(encoding="utf-8")
    )
    assert len(selections) == len(editorial) == report["selected_count"]
    for sel, row in zip(selections, editorial):
        _assert_script_clean(sel["text"])
        assert _norm(sel["text"]) == _norm(row["text"])
        # the clip is a contiguous run of source sentences
        assert _norm(sel["text"]) in source_text

    # captions on disk carry the script, in both formats
    for i in range(1, report["selected_count"] + 1):
        for ext in ("ass", "srt"):
            body = (project / "captions" / f"{i:02d}.{ext}").read_bytes()
            _assert_script_clean(body.decode("utf-8", errors="strict"))

    # packs stay extractive in Telugu. Packs are numbered in program order while
    # selections is the policy's pick order, so the pack's own clip is the one
    # sharing its source span.
    packs = json.loads((project / "content_pack.json").read_text(encoding="utf-8"))
    by_span = {(round(s["start"], 3), round(s["end"], 3)): s
               for s in selections}
    for pack in packs:
        sel = by_span[(round(pack["source_start"], 3),
                       round(pack["source_end"], 3))]
        assert _norm(pack["instagram_caption"]) == _norm(sel["text"])

    assert (project / "facebook" / "01.mp4").is_file()
    assert (project / "shorts" / "01.mp4").is_file()
