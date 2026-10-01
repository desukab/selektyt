"""Tests for the canonical edit's interchange export.

The assembly produces one ``Timeline`` for the whole program; this pins that
the edit is *deliverable* — that the EDL and FCPXML written from it validate,
and that the same emitters the per-clip path uses cannot silently drift into a
different grammar from the program-level one.

No media is needed: the emitters read ``SourceMedia`` metadata, not the file,
so a source path that does not exist is exactly the case being tested — an
export must be structurally sound regardless of whether the media is present on
this host, because that is the condition under which it is handed to an NLE.
"""

from __future__ import annotations

import pytest

from engine.assembly import AssemblyConfig, assemble
from engine.nle_export import (
    export,
    independent_parse_edl,
    independent_parse_fcpxml,
    render_edl,
    validate_edl,
    validate_fcpxml,
)


class _Word:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class _Cand:
    def __init__(self, start, end, *, hook="hook", words=None, opening_score=0.5):
        self.start = start
        self.end = end
        self.hook = hook
        self.text = hook
        self.reason = "a reason"
        self.story_type = "story"
        self.total_score = 0.5
        self.opening_score = opening_score
        self.words = words or _synth(start, end)


def _synth(start, end, n=6):
    span = max(0.0, end - start)
    step = span / n
    return [_Word(start + i * step, start + (i + 1) * step * 0.9)
            for i in range(n)]


@pytest.fixture
def assembly(tmp_path):
    """A three-clip edit. Spans are disjoint in source time so the reel is
    visibly not source order, and no corpus is given so the assembly
    degenerates to the documented unroled, ungrouped state."""
    return assemble(
        [_Cand(60.0, 90.0, hook="second"),
         _Cand(0.0, 30.0, hook="first"),
         _Cand(300.0, 330.0, hook="third")],
        media=str(tmp_path / "source.mp4"),
        source_duration=1000.0,
        title="the program",
        corpus=None,
        config=AssemblyConfig(order="source"),
    )


class TestExportShape:
    def test_both_formats_are_written(self, assembly, tmp_path):
        out = export(assembly.timeline, tmp_path / "out", stem="program")
        assert set(out) == {"edl", "fcpxml"}
        assert out["edl"].read_text(encoding="ascii")
        assert out["fcpxml"].read_text(encoding="utf-8")

    def test_the_stem_defaults_to_the_timeline_title(self, assembly, tmp_path):
        out = export(assembly.timeline, tmp_path / "out")
        assert out["edl"].name == "the program.edl"

    def test_one_reel_one_source(self, assembly, tmp_path):
        out = export(assembly.timeline, tmp_path / "out", stem="p")
        edl = out["edl"].read_text(encoding="ascii")
        # every event reads from the same source
        from_clips = [ln for ln in edl.splitlines() if ln.startswith("* FROM CLIP")]
        assert len(from_clips) == len(assembly.timeline.clips)
        assert {ln.split()[-1] for ln in from_clips} == {
            str((tmp_path / "source.mp4"))}

    def test_unsupported_format_is_refused(self, assembly, tmp_path):
        with pytest.raises(ValueError, match="unsupported export format"):
            export(assembly.timeline, tmp_path / "out", formats=("xml",))


class TestValidation:
    def test_edl_validates_on_the_grid_it_was_written_on(self, assembly, tmp_path):
        out = export(assembly.timeline, tmp_path / "out", stem="p")
        text = out["edl"].read_text(encoding="ascii")
        # CMX 3600 declares no frame rate, so the validator is given the rate
        # the timeline was written on — exactly as the pipeline does.
        assert validate_edl(text, fps=assembly.timeline.fps) == []

    def test_fcpxml_validates_back_against_the_timeline(self, assembly, tmp_path):
        out = export(assembly.timeline, tmp_path / "out", stem="p")
        text = out["fcpxml"].read_text(encoding="utf-8")
        # validate_fcpxml cross-checks the document against the Timeline that
        # produced it, which is the one-way check G10 named as the limit.
        assert validate_fcpxml(text, assembly.timeline) == []

    def test_the_edl_is_accepted_by_an_independent_reader(self, assembly, tmp_path):
        out = export(assembly.timeline, tmp_path / "out", stem="p")
        text = out["edl"].read_text(encoding="ascii")
        ran, problems = independent_parse_edl(text)
        assert ran, "pycmx is not installed, so this assertion is vacuous"
        assert problems == []

    def test_the_fcpxml_is_accepted_by_an_independent_reader(self, assembly, tmp_path):
        out = export(assembly.timeline, tmp_path / "out", stem="p")
        ran, problems = independent_parse_fcpxml(
            out["fcpxml"].read_text(encoding="utf-8"))
        assert ran, "the FCPXML reader is not installed; this is advisory only"
        assert problems == []

    def test_a_corrupted_edl_is_caught(self, assembly, tmp_path):
        out = export(assembly.timeline, tmp_path / "out", stem="p")
        broken = out["edl"].read_text(encoding="ascii")
        # a record that is not a valid event
        broken = broken.replace("001", "0ZZ", 1)
        assert validate_edl(broken, fps=assembly.timeline.fps)


class TestAgreementWithTheEdit:
    def test_every_clip_in_the_edit_has_an_event(self, assembly, tmp_path):
        out = export(assembly.timeline, tmp_path / "out", stem="p")
        edl = out["edl"].read_text(encoding="ascii")
        n_events = len([ln for ln in edl.splitlines()
                        if ln[:3].isdigit() and " V " in ln])
        assert n_events == len(assembly.timeline.clips)

    def test_the_export_duration_matches_the_assembled_edit(self, assembly, tmp_path):
        out = export(assembly.timeline, tmp_path / "out", stem="p")
        edl = out["edl"].read_text(encoding="ascii")
        # the last event's record-out is the program's end on the timeline
        last = [ln for ln in edl.splitlines()
                if ln[:3].isdigit() and " V " in ln][-1]
        record_out = last.split()[-1]
        from engine.timecode import parse_smpte
        assert parse_smpte(record_out, fps=assembly.timeline.fps) == \
            pytest.approx(assembly.duration, abs=1 / assembly.timeline.fps)

    def test_source_and_timeline_spans_are_distinct_columns(self, assembly, tmp_path):
        # In source order the second clip starts at 60 s in the source and at
        # 30 s on the timeline. An export that conflated the two columns would
        # be unusable in an NLE, and this is the clip where they differ.
        out = export(assembly.timeline, tmp_path / "out", stem="p")
        events = [ln for ln in out["edl"].read_text(encoding="ascii").splitlines()
                  if ln[:3].isdigit() and " V " in ln]
        assert len(events) >= 2
        from engine.timecode import parse_smpte
        cols = events[1].split()
        fps = assembly.timeline.fps
        # 001 REEL V C  <src in> <src out> <timeline in> <timeline out>
        assert cols[3] == "C"
        assert parse_smpte(cols[4], fps=fps) == pytest.approx(60.0, abs=1 / fps)
        assert parse_smpte(cols[6], fps=fps) == pytest.approx(30.0, abs=1 / fps)
        # and the first clip really does start at the origin in both, so the
        # second clip's offset is the first clip's duration, not a coincidence
        first = events[0].split()
        assert parse_smpte(first[4], fps=fps) == pytest.approx(0.0, abs=1 / fps)
        assert parse_smpte(first[6], fps=fps) == pytest.approx(0.0, abs=1 / fps)
        assert parse_smpte(first[7], fps=fps) == pytest.approx(30.0, abs=1 / fps)
