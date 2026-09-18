"""Determinism of the editorial stages.

Two runs over identical inputs must produce byte-identical artifacts for the
deterministic stages: editorial report, pacing report, captions, cutlist and
FCPXML. Nothing in these stages may depend on wall-clock time, process
identity, hash order or filesystem state.

The per-stage checks run everywhere (no ffmpeg); the full-pipeline byte
comparison is gated on ffmpeg because it exercises the real writer and the
real renders.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.fixtures import editorial_fixtures as fx

_NEED_FFMPEG = fx.skip_no_ffmpeg

DETERMINISTIC_ARTIFACTS = (
    "transcript.json", "selections.json", "editorial_report.json",
    "pacing_report.json", "edit_plan.json", "content_pack.json",
)
# Renders embed encoder timestamps/metadata; QA records file sizes. Neither is
# claimed to be reproducible.
NON_DETERMINISTIC_ARTIFACTS = ("qa.json", "report.json", "pipeline_state.json")


def _stage_selection(transcript, limit=4):
    """Run the deterministic selection stages exactly once, from the same
    inputs the pipeline uses."""
    from engine.context_guard import validate
    from engine.editorial import make_thoughts, rank

    thoughts = make_thoughts(transcript)
    ranked = rank(thoughts, limit=max(limit * 3, limit))
    selected = [c for c in ranked if validate(c)[0]][:limit]
    assert selected, "the fixture must select at least one clip"
    return thoughts, ranked, selected


# ── fixture purity ──


def test_fixture_generation_is_independent_of_time_and_order():
    """The transcript builders are pure functions: repeated calls are
    identical, and the long-form builder never leaks randomness."""
    a = fx.english_transcript()
    b = fx.english_transcript()
    assert [(s.start, s.end, s.text) for s in a] == [(s.start, s.end, s.text) for s in b]

    long_a = fx.long_transcript(minutes=8)
    long_b = fx.long_transcript(minutes=8)
    assert [s.text for s in long_a] == [s.text for s in long_b]

    # an 8-minute fixture really is minutes-scale, not a scaled-down stub
    assert long_a[-1].end >= 7 * 60


# ── stage purity (no ffmpeg) ──


def test_thoughts_and_ranking_are_stable_across_calls():
    from engine.editorial import make_thoughts, rank

    transcript = fx.english_transcript()
    t1, t2 = make_thoughts(transcript), make_thoughts(transcript)
    assert [(s.start, s.end, s.text) for s in t1] == [(s.start, s.end, s.text) for s in t2]

    r1, r2 = rank(t1, limit=4), rank(t2, limit=4)
    assert [c.to_dict() for c in r1] == [c.to_dict() for c in r2]


def test_editorial_pacing_caption_cutlist_and_fcpxml_are_byte_identical(tmp_path):
    """Every deterministic stage, invoked twice from the same candidate set,
    writes byte-identical artifacts. Each stage is exercised through the same
    engine entry points the pipeline calls."""
    from engine.caption_director import caption_metadata
    from engine.captions import ass_for_candidate, srt_for_candidate
    from engine.content_pack import build_content_pack
    from engine.context_guard import validate
    from engine.edit_plan import (
        aggregate_pacing_report,
        pacing_report,
        retention_intervals,
    )
    from engine.fcpxml import export_fcpxml
    from engine.jumpcut import CutList, CutSegment, SegmentType

    transcript = fx.telugu_transcript()
    _, ranked, selected = _stage_selection(transcript, limit=3)
    # exactly the population the pipeline feeds the aggregator
    rejected = [c for c in ranked if not validate(c)[0]]

    def run_all(root: Path) -> dict[str, bytes]:
        artifacts: dict[str, bytes] = {}
        for i, c in enumerate(selected, 1):
            ass = ass_for_candidate(c, root / f"{i:02d}.ass")
            srt = srt_for_candidate(c, root / f"{i:02d}.srt")
            fcpxml = export_fcpxml(
                CutList(
                    segments=[
                        CutSegment(start=a, end=b, segment_type=SegmentType.KEEP)
                        for a, b in retention_intervals(c)
                    ],
                    original_duration=c.end - c.start,
                ),
                root / f"{i:02d}.fcpxml", fps=30.0,
            )
            artifacts[f"{i:02d}.ass"] = ass.read_bytes()
            artifacts[f"{i:02d}.srt"] = srt.read_bytes()
            artifacts[f"{i:02d}.fcpxml"] = fcpxml.read_bytes()
            # the JSON stages, serialised exactly as the pipeline serialises them
            artifacts[f"{i:02d}.caption.json"] = json.dumps(
                caption_metadata(c), ensure_ascii=False, indent=2).encode()
            pack = build_content_pack(c)
            pack["clip_index"] = i
            artifacts[f"{i:02d}.pack.json"] = json.dumps(
                pack, ensure_ascii=False, indent=2).encode()
            artifacts[f"{i:02d}.pacing.json"] = json.dumps(
                pacing_report(c), ensure_ascii=False, indent=2).encode()
        artifacts["aggregate_pacing.json"] = json.dumps(
            aggregate_pacing_report(
                selected, rejected, len(ranked) - len(selected)
            ), ensure_ascii=False, indent=2).encode()
        return artifacts

    first = run_all(tmp_path / "a")
    second = run_all(tmp_path / "b")
    assert first and set(first) == set(second)
    for name, content in first.items():
        assert content == second[name], f"stage artifact {name} is not reproducible"


def test_retention_intervals_and_cut_algebra_are_pure():
    """The cutlist a render consumes is a pure function of the candidate."""
    from engine.edit_plan import retention_intervals

    _, _, selected = _stage_selection(fx.english_transcript(), limit=3)
    for c in selected:
        once = retention_intervals(c)
        twice = retention_intervals(c)
        assert once == twice
        # pure across an equal-but-distinct candidate object
        from models.core import Candidate
        clone = Candidate(
            c.start, c.end, c.text, c.story_type, c.hook, c.payoff,
            c.editorial_score, c.virality_score, c.context_score, c.confidence,
            c.reason, c.visual_potential, list(c.words or []),
            c.coherence_score, c.completeness, c.llm_metadata,
        )
        assert retention_intervals(clone) == once


# ── full pipeline (ffmpeg) ──


@_NEED_FFMPEG
def test_two_full_runs_produce_byte_identical_deterministic_artifacts(tmp_path):
    """Two independent pipeline runs over identical fixture inputs agree on
    every deterministic artifact, byte for byte."""
    transcript = fx.english_transcript()
    media = fx.media_for(transcript, tmp_path / "source" / "clip.mp4")

    first = fx.run_fixture_pipeline(tmp_path / "run_a", transcript, media,
                                    language="en", limit=3)
    second = fx.run_fixture_pipeline(tmp_path / "run_b", transcript, media,
                                     language="en", limit=3)

    for name in DETERMINISTIC_ARTIFACTS:
        a = (first / name).read_bytes()
        b = (second / name).read_bytes()
        assert a == b, f"{name} differs between two runs over identical inputs"

    # captions: one file per clip, in both formats plus metadata
    for pattern in ("*.ass", "*.srt", "*.json"):
        for path in sorted((first / "captions").glob(pattern)):
            twin = second / "captions" / path.name
            assert twin.is_file(), f"missing {twin.name} in the second run"
            assert path.read_bytes() == twin.read_bytes(), \
                f"caption {path.name} is not reproducible"

    # per-clip content packs
    for path in sorted((first / "content_pack").glob("*.json")):
        assert path.read_bytes() == (second / "content_pack" / path.name).read_bytes()

    # the artifacts we explicitly do NOT claim determinism for still exist and
    # differ only in the ways they must (paths/sizes), never in the selection
    for name in NON_DETERMINISTIC_ARTIFACTS:
        assert (first / name).is_file() and (second / name).is_file()
    assert (json.loads((first / "report.json").read_text())["selected_count"]
            == json.loads((second / "report.json").read_text())["selected_count"])


def test_the_fcpxml_export_op_is_reproducible_across_processes(tmp_path):
    """FCPXML generated in two separate sidecar processes is byte-identical:
    no PID, timestamp or path-order leakage.

    The sidecar is launched through a tiny in-tmp launcher that applies the
    same editable-install remap as the suite's conftest, so the subprocess
    imports this worktree's modules rather than the installed checkout.
    """
    import subprocess
    import sys

    root = Path(__file__).resolve().parent.parent
    launcher = tmp_path / "launch_sidecar.py"
    launcher.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "for _f in list(sys.meta_path):\n"
        "    if type(_f).__name__ == '_EditableFinder' and hasattr(_f, 'MAPPING'):\n"
        "        for _k in list(_f.MAPPING):\n"
        "            _f.MAPPING[_k] = str(f'{root}/' + _k)\n"
        "from selekyt.server import serve_stdio\n"
        "raise SystemExit(serve_stdio())\n",
        encoding="utf-8",
    )

    request = json.dumps({
        "op": "render_fcpxml",
        "params": {
            "cutlist": {
                "source_duration": 20.0,
                "intervals": [
                    {"start": 0.0, "end": 4.0, "kind": "keep"},
                    {"start": 4.0, "end": 10.0, "kind": "remove"},
                    {"start": 10.0, "end": 14.0, "kind": "keep"},
                    {"start": 14.0, "end": 20.0, "kind": "remove"},
                ],
            },
            "fps": 30,
            "title": "determinism",
            "reference": {"name": "src", "duration": 20.0, "path": "/tmp/src.mp4"},
        },
    })
    outputs = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, str(launcher)],
            input=request + "\n", capture_output=True, text=True, timeout=60,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        assert len(lines) == 1, f"stdout must carry one JSON line, got {lines}"
        outputs.append(lines[0])
    assert outputs[0] == outputs[1], "FCPXML export differs across processes"
    assert "determinism" in json.loads(outputs[0])["data"]["xml"]
