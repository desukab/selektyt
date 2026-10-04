"""Project lifecycle integration.

Given a local media file and a transcript, ``selekyt.pipeline.run`` writes an
artifact set under ``out/project/``. These tests run the real pipeline body
(only the download and transcription boundaries are stubbed with fixture
inputs) and assert the artifact set is *complete and internally consistent*:
every artifact exists, parses, and agrees with every other artifact about
clip indices and source timecode.

Render-dependent tests are gated on ffmpeg; the pure-editorial consistency
tests run everywhere.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.fixtures import editorial_fixtures as fx

_NEED_FFMPEG = fx.skip_no_ffmpeg


# ── shared fixture project (one real pipeline run) ───────────────────────────


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> Path:
    transcript = fx.english_transcript()
    return fx.media_for(transcript, tmp_path_factory.mktemp("media") / "source" / "clip.mp4")


@pytest.fixture(scope="module")
def project(media, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("run")
    return fx.run_fixture_pipeline(
        out, fx.english_transcript(), media, language="en", limit=4,
    )


def _load(project: Path, name: str):
    return json.loads((project / name).read_text(encoding="utf-8"))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ── completeness ──


@_NEED_FFMPEG
def test_run_writes_every_expected_artifact(project):
    """Every documented artifact exists, is non-empty and parses as JSON."""
    for name in (
        "transcript.json", "selections.json", "editorial_report.json",
        "pacing_report.json", "edit_plan.json", "content_pack.json",
        "report.json", "qa.json",
    ):
        path = project / name
        assert path.is_file(), f"missing artifact {name}"
        assert path.stat().st_size > 0, f"empty artifact {name}"
        json.loads(path.read_text(encoding="utf-8"))


@_NEED_FFMPEG
def test_run_writes_one_render_and_caption_set_per_clip(project):
    selections = _load(project, "selections.json")
    for idx in range(1, len(selections) + 1):
        for sub in ("captions", "facebook", "shorts", "content_pack"):
            assert (project / sub).is_dir(), f"missing {sub}/"
        for ext in ("ass", "srt", "json"):
            assert (project / "captions" / f"{idx:02d}.{ext}").is_file()
        for sub in ("facebook", "shorts"):
            out = project / sub / f"{idx:02d}.mp4"
            assert out.is_file() and out.stat().st_size > 0
        pack = project / "content_pack" / f"{idx:02d}.json"
        assert pack.is_file()


# ── internal consistency: indices and source timecode ──


@_NEED_FFMPEG
def test_clip_indices_agree_across_every_artifact(project):
    """selections / editorial_report agree in the policy's pick order, and
    edit_plan / content_pack / captions / renders agree in the order the program
    plays them; the two groups hold the same clips.

    These orders differ by design — the policy ranks, the assembly sequences —
    so joining them by position would assert that a reorder never happens, which
    is exactly what the assembly exists to do. The join is by source span, and
    what has to agree is the set of clips and each group's internal numbering.
    """
    selections = _load(project, "selections.json")
    editorial = _load(project, "editorial_report.json")
    edit_plan = _load(project, "edit_plan.json")
    packs = _load(project, "content_pack.json")
    report = _load(project, "report.json")

    n = report["selected_count"]
    assert n >= 3, "fixture must select several clips for a meaningful run"
    assert len(selections) == len(editorial) == n
    assert len(edit_plan) == len(packs) == report["program_count"]

    # The policy's own records agree with each other, position for position.
    for i in range(n):
        assert selections[i]["start"] == editorial[i]["start"]
        assert selections[i]["end"] == editorial[i]["end"]
        assert _norm(selections[i]["text"]) == _norm(editorial[i]["text"])

    # The deliverables agree with each other, position for position, and are
    # numbered from one with no gaps.
    for i, (plan, pack) in enumerate(zip(edit_plan, packs), 1):
        assert plan["index"] == i
        assert pack["clip_index"] == i
        assert (project / "content_pack" / f"{i:02d}.json").is_file()
        assert (project / "captions" / f"{i:02d}.json").is_file()

    # And the two groups hold the same clips, joined by span rather than slot.
    def spans(rows, s, e):
        return {(round(r[s], 3), round(r[e], 3)) for r in rows}

    assert spans(selections, "start", "end") == spans(edit_plan,
                                                      "source_start",
                                                      "source_end"), \
        "the policy selected a clip the program does not ship, or vice versa"


@_NEED_FFMPEG
def test_editorial_report_total_score_matches_the_selection_weights(project):
    """The report's total_score is the weighted sum the Candidate documents,
    recomputable from the fields selections.json itself carries."""
    selections = _load(project, "selections.json")
    editorial = _load(project, "editorial_report.json")
    assert len(selections) == len(editorial)
    for sel, row in zip(selections, editorial):
        expected = (
            0.30 * sel["editorial_score"]
            + 0.22 * sel["coherence_score"]
            + 0.20 * sel["virality_score"]
            + 0.16 * sel["context_score"]
            + 0.12 * sel["visual_potential"]
        )
        # both artifacts derive from one float; editorial rounds to 3 places,
        # selections to 4 (Candidate.to_dict)
        assert row["total_score"] == round(sel["total_score"], 3)
        assert abs(row["total_score"] - expected) < 5e-4
        assert 0.0 <= row["total_score"] <= 1.0
        for key in ("editorial_score", "virality_score", "context_score",
                    "visual_potential"):
            assert 0.0 <= row[key] <= 1.0


@_NEED_FFMPEG
def test_edit_plan_intervals_stay_inside_the_clip_and_account_for_time(project):
    """Retention intervals are conservative: they never leave the clip span,
    and edited_duration is exactly their sum."""
    edit_plan = _load(project, "edit_plan.json")
    assert edit_plan
    for row in edit_plan:
        start, end = row["source_start"], row["source_end"]
        assert end > start
        assert row["source_duration"] == round(end - start, 3)
        assert row["intervals"], "a selected clip must have renderable intervals"
        kept = 0.0
        for a, b in row["intervals"]:
            assert b > a
            # conservative: intervals may add small edge padding only
            assert a >= start - 0.5, "interval escapes the clip span"
            assert b <= end + 0.5, "interval escapes the clip span"
            kept += b - a
        assert row["edited_duration"] == round(kept, 3)
        assert row["removed_seconds"] == round((end - start) - kept, 3)
        assert 0.0 <= row["edited_duration"] <= row["source_duration"] + 1e-6


@_NEED_FFMPEG
def test_pacing_report_accounts_for_every_ranked_candidate(project):
    """Every ranked candidate is either selected or counted as a context-guard
    rejection — pacing's census and report.json's census agree."""
    pacing = _load(project, "pacing_report.json")
    report = _load(project, "report.json")
    assert pacing["total_candidates"] == (
        report["selected_count"] + report["rejected_by_context_guard"]
    )
    assert pacing["selected_count"] == report["selected_count"]
    assert pacing["context_guard_rejections"] == report["rejected_by_context_guard"]
    assert pacing["total_candidates"] > 0


# ── export manifest ──


@_NEED_FFMPEG
def test_every_path_in_the_report_points_at_a_real_nonempty_file(project):
    """report.json is the manifest a B2B consumer reads: every path it records
    must resolve, and every clip it implies must exist."""
    report = _load(project, "report.json")
    for key in ("content_pack", "editorial_report", "pacing_report", "qa"):
        path = Path(report[key])
        assert path.is_file(), f"report['{key}'] does not exist: {path}"
        assert path.stat().st_size > 0
        assert path.resolve().is_relative_to(project.resolve())
    captions = Path(report["captions"])
    assert captions.is_dir(), "report['captions'] must point at the caption set"
    assert list(captions.glob("*.ass")), "the caption set is empty"

    for sub in ("facebook", "shorts"):
        directory = Path(report["outputs"][sub])
        assert directory.is_dir()
        mp4s = sorted(directory.glob("*.mp4"))
        assert mp4s, f"{sub} directory is empty"
        assert all(p.stat().st_size > 0 for p in mp4s)
        # The assembled program reel lives in the same directory and spans every
        # clip, so it is not counted among them.
        clips = [p for p in mp4s if p.stem != "program"]
        assert len(clips) == report["selected_count"]

    # qa.json is itself a manifest: every file it audited must exist and the
    # size it recorded must match the file on disk.
    qa = _load(project, "qa.json")
    assert qa["ok"] is True, "QA must pass for a clean fixture run"
    assert qa["per_file"]
    for recorded_path, detail in qa["per_file"].items():
        path = Path(recorded_path)
        assert path.is_file(), f"qa.json records a missing output: {recorded_path}"
        assert path.stat().st_size > 0
        assert detail["size"] == path.stat().st_size


# ── captions vs source ──


@_NEED_FFMPEG
def test_captions_cover_the_clip_and_stay_inside_its_span(project):
    transcript_text = _norm(" ".join(s.text for s in fx.english_transcript()))
    edit_plan = _load(project, "edit_plan.json")
    for i, plan in enumerate(edit_plan, 1):
        meta = json.loads((project / "captions" / f"{i:02d}.json").read_text(encoding="utf-8"))
        assert meta["blocks"], f"clip {i} has no caption blocks"
        for a, b in zip(meta["blocks"], meta["blocks"][1:]):
            assert a["end"] <= b["start"], f"clip {i}'s caption blocks overlap"
        for block in meta["blocks"]:
            assert block["end"] > block["start"]
            # no caption word is ever unspoken source text
            for token in block["text"].split():
                assert token in transcript_text, \
                    f"caption token {token!r} is not spoken source text"


@_NEED_FFMPEG
def test_ass_and_srt_tracks_carry_the_same_cues(project):
    """The platform-neutral SRT track must track the styled ASS track
    cue for cue, with cue times relative to the clip start."""
    # Caption files are numbered in program order while selections.json is the
    # policy's pick order, so the clip a track belongs to is found by span.
    selections = _load(project, "selections.json")
    edit_plan = _load(project, "edit_plan.json")
    by_span = {(round(s["start"], 3), round(s["end"], 3)): s
               for s in selections}
    for i, plan in enumerate(edit_plan, 1):
        ass = (project / "captions" / f"{i:02d}.ass").read_text(encoding="utf-8")
        srt = (project / "captions" / f"{i:02d}.srt").read_text(encoding="utf-8")
        ass_cues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
        srt_blocks = [b for b in srt.strip().split("\n\n") if b.strip()]
        assert ass_cues and srt_blocks
        assert len(ass_cues) == len(srt_blocks)
        # every SRT timing line is well formed
        for block in srt_blocks:
            assert re.search(
                r"^\d+\n\d{1,2}:\d{2}:\d{2},\d{3} --> \d{1,2}:\d{2}:\d{2},\d{3}\n",
                block,
            )
        sel = by_span[(round(plan["source_start"], 3),
                       round(plan["source_end"], 3))]
        duration = sel["end"] - sel["start"]
        prev_end = 0.0
        for cue in ass_cues:
            m = re.match(
                r"Dialogue: 0,(\d+):(\d{2}):(\d{2})\.(\d{2}),(\d+):(\d{2}):(\d{2})\.(\d{2}),",
                cue,
            )
            assert m, f"malformed ASS dialogue line: {cue!r}"
            st = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 100
            en = int(m[5]) * 3600 + int(m[6]) * 60 + int(m[7]) + int(m[8]) / 100
            # rendered cues are clamped into the clip and never overlap
            assert 0.0 <= st < en <= duration + 0.15, \
                f"clip {i} cue {st:.3f}..{en:.3f} escapes duration {duration:.3f}"
            assert st >= prev_end - 1e-9
            prev_end = en


@_NEED_FFMPEG
def test_content_pack_titles_trace_to_spoken_words_only(project):
    """The pack's titles/caption are extractive — no fabricated claims."""
    # Packs are numbered in program order; selections.json is pick order. The
    # pack's own clip is the selection with the same source span.
    selections = _load(project, "selections.json")
    packs = _load(project, "content_pack.json")
    by_span = {(round(s["start"], 3), round(s["end"], 3)): s
               for s in selections}
    for pack in packs:
        sel = by_span[(round(pack["source_start"], 3),
                       round(pack["source_end"], 3))]
        clip_text = _norm(sel["text"])
        assert pack["source_based"] is True
        assert "fabricated" in pack["policy"]
        assert pack["story_type"] == sel["story_type"]
        assert _norm(pack["instagram_caption"]) == clip_text
        for title in pack["titles"]:
            assert title, "an empty title variant shipped"
            assert _norm(title).lower() in clip_text.lower(), \
                f"title {title!r} is not spoken source text"
        assert f"#{pack['story_type']}" in pack["hashtags"]


# ── pure-editorial consistency (runs without ffmpeg) ──


def test_fixture_transcript_timings_are_monotonic_and_contained():
    """Every word lies inside its segment and time never runs backwards —
    the invariant caption and pacing stages rely on."""
    for transcript in (fx.english_transcript(), fx.telugu_transcript()):
        assert transcript
        prev_end = 0.0
        for seg in transcript:
            assert seg.end > seg.start
            assert seg.start >= prev_end - 1e-9
            prev_end = seg.end
            words = sorted(seg.words, key=lambda w: (w.start, w.end))
            for w in words:
                assert w.start >= seg.start - 1e-9, "word starts before its segment"
                assert w.end <= seg.end + 1e-9, "word ends after its segment"
                assert w.end > w.start


def test_selection_respects_the_rank_and_guard_invariants():
    """The long-form fixture must segment non-trivially and still select only
    clips that are guard-valid, in rank's duration/word window, and pairwise
    separated in source time."""
    from engine.context_guard import validate
    from engine.editorial import make_thoughts, rank
    from engine.story_units import segment_story_units

    transcript = fx.long_transcript(minutes=25)
    assert transcript[-1].end >= 20 * 60, "the long fixture must be 20+ minutes"
    units = segment_story_units(transcript)
    assert len(units) > 1, "a long-form transcript must split into story units"

    thoughts = make_thoughts(transcript)
    assert len(thoughts) > 100, "segmentation must produce many candidates"
    ranked = rank(thoughts, limit=8)
    assert ranked, "at least one clip must clear the editorial gate"

    for c in ranked:
        assert 8 <= (c.end - c.start) <= 55
        assert len(re.findall(r"\w+", c.text, re.UNICODE)) >= 20
        assert validate(c)[0], f"a ranked clip failed the context guard: {validate(c)[1]}"

    for i, a in enumerate(ranked):
        for b in ranked[i + 1:]:
            assert a.end <= b.start - 4 or a.start >= b.end + 4, \
                "rank must not ship overlapping clips"
