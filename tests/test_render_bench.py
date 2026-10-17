"""Tests for the arithmetic behind the render throughput figures.

`tools/render_bench.py` regenerates the render real-time factor the cost model is
built on, and the encoder ladder that justifies the x264 settings the pipeline
ships. It is a *benchmark*, not a test: it prints a table and asserts nothing,
because a hard RTF gate is flaky on a shared host. Nothing here renders a frame,
spawns ffmpeg, or runs `ship`/`ladder` end to end.

What these pin instead is the arithmetic and the structure the table rests on —
the ladder itself, the parsers, the sweep aggregation, the disk ceiling — by
feeding the helpers known inputs, and the committed run records by reading them.
A benchmark whose own bookkeeping is wrong measures nothing a doc can quote.
"""
import inspect
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import render_bench
from tools.render_bench import (
    LADDER,
    _aggregate_sweep,
    _deltas,
    _emitted_duration,
    _fmt_range,
    _median,
    _mm,
    _parse_entries,
    _parse_floats,
    _shipped_pair,
    _worst_case_bytes,
)

ROOT = Path(__file__).resolve().parent.parent
RUNS = render_bench.RUNS


# ── the ladder, and the shipped marker on it ──────────────────────────────────

def _docstring_ladder():
    """Read the encoder ladder out of ``_encoder_args``' docstring, as pairs.

    ``LADDER`` claims to be that table in the order it is listed, so the
    expectation is *derived* from the docstring rather than restated: an edit
    that drops or reorders an entry has to move ``LADDER`` with it, or the bench
    silently scores a different ladder from the one the docs defend.

    The docstring carries one table per orientation the pipeline ships, each
    under a header naming the frame size. The ladder is the same set of entries
    in both — it is an encoder decision, not an orientation one — so the
    vertical table is read and the landscape one is checked to agree with it
    rather than parsed twice.
    """
    from engine.render import _encoder_args

    lines = (_encoder_args.__doc__ or "").splitlines()
    header = None
    for i, line in enumerate(lines):
        if line.split() == ["entry", "dB", "vs", "shipped", "bitrate", "ratio", "RTF", "ratio"]:
            header = i
            break
    assert header is not None, \
        "the ladder table is no longer in _encoder_args' docstring, so LADDER " \
        "has nothing to agree with"
    pairs: list[tuple[str, int]] = []
    for line in lines[header + 1:]:
        fields = line.split()
        # The table ends at the blank line after it; a prose line after that has
        # a different shape, and reaching it means the parse has run off the end.
        # An entry row is "preset/crfNN .. .. .. ..".
        if not fields or "/" not in fields[0]:
            break
        name, _, crf = fields[0].partition("/")
        if not crf.startswith("crf") or not crf[3:].isdigit():
            break
        pairs.append((name, int(crf[3:])))
    return pairs


def test_the_docstring_ladder_tables_agree_with_each_other():
    """Both orientation tables list the same ladder. They are the same encoder
    decision measured over two pixel counts, so an entry present in one and
    missing from the other is a transcription slip in a table that a decision is
    then made from — and the shipped row has to appear in both, since the pair
    the decision rests on is orientation-independent."""
    from engine.render import _encoder_args

    lines = (_encoder_args.__doc__ or "").splitlines()
    # The header row of each table, matched token-for-token rather than on its
    # indentation: the table body drops any literal-indented version of this —
    # CPython 3.13 strips a docstring's common indent at compile time, and an
    # indented anchor no longer matches there while the same table is intact.
    # Token equality keeps the anchor off the entry rows, which also contain
    # "dB" and "shipped": no entry row is a re-ordering of exactly these eight
    # tokens.
    header = ["entry", "dB", "vs", "shipped", "bitrate", "ratio", "RTF", "ratio"]
    starts = [i + 1 for i, line in enumerate(lines) if line.split() == header]
    assert len(starts) == 2, \
        f"expected one table per orientation, found {len(starts)}"
    seen = []
    for start in starts:
        rows = []
        for line in lines[start:]:
            fields = line.split()
            # The table ends at the blank line after it, the same boundary
            # ``_docstring_ladder`` reads: prose beyond it is not table state.
            if not fields or "/" not in fields[0]:
                break
            if fields[0].split("/")[1].startswith("crf"):
                name, _, crf = fields[0].partition("/")
                if crf[3:].isdigit():
                    rows.append((name, int(crf[3:])))
        seen.append(rows)
    assert seen[0] == seen[1], (
        "the two orientation tables in _encoder_args' docstring disagree: "
        f"{seen[0]} vs {seen[1]}")
    assert ("slow", 18) in seen[0], "the shipped pair is missing from a table"


def test_the_default_ladder_is_the_one_the_docstring_defends():
    assert LADDER == _docstring_ladder(), (
        "LADDER no longer matches the ladder in engine/render._encoder_args' "
        "docstring; one of the two has to move")


def test_the_retired_pair_stays_in_the_ladder():
    """``("veryfast", 20)`` is the pair the stale cost figure was measured
    against. The tool's own docstring keeps it in the default set because the
    distance between it and the shipped entry *is* the evidence for the encoder
    decision — dropping it would leave a table that can no longer explain its own
    conclusion, and this holds the reason in place."""
    assert ("veryfast", 20) in LADDER


def test_the_shipped_marker_is_read_off_the_live_encoder_flags():
    """The shipped entry is parsed out of the encoder the pipeline actually
    imports, not hardcoded: the defect this tool exists for was a figure that
    disagreed with that function, and a bench that hardcoded the answer would
    disagree the same way."""
    from engine.render import _encoder_args

    flags = _encoder_args()
    derived = (flags[flags.index("-preset") + 1],
               flags[flags.index("-crf") + 1])
    assert _shipped_pair() == derived
    assert derived == ("slow", "18")


# ── the two comma-list parsers ────────────────────────────────────────────────

def test_parse_entries_reads_preset_crf_pairs():
    assert _parse_entries("slow:18,slow:20") == [("slow", 18), ("slow", 20)]
    # Whitespace around an entry is tolerated, and a lone int is a crf of a
    # preset named by what precedes the colon — not a parse error.
    assert _parse_entries(" slow:18 , veryfast:20 ") == [("slow", 18),
                                                          ("veryfast", 20)]


@pytest.mark.parametrize("bad", ["slow", "slow:abc", "slow:18.5", ":"])
def test_parse_entries_rejects_a_malformed_entry_with_an_exit_code(bad):
    """A typo on the command line is a wrong argument; the tool says so rather
    than scoring an entry against a preset it never named."""
    with pytest.raises(SystemExit):
        _parse_entries(bad)


def test_parse_floats_reads_a_comma_list_of_start_offsets():
    assert _parse_floats("0,300,600") == [0.0, 300.0, 600.0]


@pytest.mark.parametrize("bad", ["0,abc", "x"])
def test_parse_floats_rejects_a_malformed_list_with_an_exit_code(bad):
    with pytest.raises(SystemExit):
        _parse_floats(bad)


def test_an_all_empty_window_list_is_no_windows_not_an_error():
    # Blank segments are dropped, so a spec of commas alone asks for no windows
    # rather than crashing — cmd_ladder is what rejects the empty result.
    assert _parse_floats(",,") == []


# ── range helpers: every field of a sweep is a [min, max] pair ────────────────

def test_mm_returns_min_then_max_in_that_order():
    # The printer reads lo, hi, so the order is a contract: a swapped pair would
    # print "3.00-1.00" and look like a wider spread than the sweep had.
    assert _mm([3, 1, 2]) == [1, 3]


def test_fmt_range_collapses_a_pair_that_did_not_move():
    assert _fmt_range([4, 4], ".2f") == "4.00"


def test_fmt_range_spans_a_pair_that_moved():
    assert _fmt_range([1, 3], ".2f") == "1.00-3.00"


# ── quality vs cost: the two numbers the encoder decision rests on ────────────

def _row(preset, crf, kbps, ssim, psnr, rtf, shipped=False):
    return {"preset": preset, "crf": crf, "shipped": shipped, "ssim": ssim,
            "psnr_db": psnr, "kbps": kbps, "rtf": rtf,
            "output": {"width": 406, "height": 722}}


def test_deltas_score_every_entry_against_the_shipped_one():
    rows = [
        _row("slow", 18, kbps=1212, ssim=0.9995, psnr=43.18, rtf=0.35,
             shipped=True),
        _row("veryfast", 20, kbps=838, ssim=0.9991, psnr=40.54, rtf=0.10),
    ]
    shipped = rows[0]
    deltas = _deltas(rows, shipped)

    # The shipped entry is its own baseline, so its gaps are exactly zero.
    own = deltas["slow/crf18"]
    assert own["psnr_db_vs_shipped"] == 0.0
    assert own["ssim_vs_shipped"] == 0.0
    assert own["kbps_ratio_vs_shipped"] == 1.0
    assert own["rtf_ratio_vs_shipped"] == 1.0

    # Hand-computed: 40.54 - 43.18 dB, 838/1212 bitrate, 0.10/0.35 wall clock.
    moved = deltas["veryfast/crf20"]
    assert moved["psnr_db_vs_shipped"] == pytest.approx(-2.64, abs=1e-3)
    assert moved["ssim_vs_shipped"] == pytest.approx(-0.0004, abs=1e-6)
    assert moved["kbps_ratio_vs_shipped"] == pytest.approx(838 / 1212, abs=1e-3)
    assert moved["rtf_ratio_vs_shipped"] == pytest.approx(0.10 / 0.35, abs=1e-3)


def test_deltas_reports_a_shipped_entry_the_ladder_omits_instead_of_raising():
    # Running a custom ladder without the shipped pair is a user choice, not a
    # crash: the record says the table cannot be scored against what it ships.
    rows = [_row("slow", 20, kbps=900, ssim=0.9994, psnr=41.9, rtf=0.2)]
    deltas = _deltas(rows, None)
    assert deltas == {"note": "the shipped encoder is not in the ladder that was run"}


# ── sweep aggregation: one row per entry carrying its range ───────────────────

def _window(rows):
    """A single-window ladder record in the shape `_measure_ladder` writes."""
    return {
        "filter": "crop=406:722:0:0,scale=406:722",
        "frames": 240,
        "content_seconds": 8.0,
        "orientation": "9:16",
        "reference_encoder": ["libx264", "preset veryfast", "crf 0 (lossless)"],
        "shipped_pair": ["slow", "18"],
        "rows": rows,
    }


def test_aggregate_sweep_keeps_entry_order_and_carries_min_max():
    window_a = _window([
        _row("veryfast", 20, kbps=800, ssim=0.9990, psnr=40.0, rtf=0.10),
        _row("slow", 18, kbps=1200, ssim=0.9995, psnr=43.0, rtf=0.35,
             shipped=True),
    ])
    window_b = _window([
        _row("veryfast", 20, kbps=900, ssim=0.9992, psnr=41.5, rtf=0.12),
        _row("slow", 18, kbps=1300, ssim=0.9996, psnr=44.0, rtf=0.38,
             shipped=True),
    ])

    agg = _aggregate_sweep([window_a, window_b])

    # Entries appear in the order the windows listed them, not sorted or
    # reordered, so the printed table reads top-to-bottom as the ladder does.
    assert [(r["preset"], r["crf"]) for r in agg["rows"]] == [
        ("veryfast", 20), ("slow", 18)]

    first = agg["rows"][0]
    assert first["kbps"] == [800, 900]
    assert first["ssim"] == [0.9990, 0.9992]
    assert first["psnr_db"] == [40.0, 41.5]
    assert first["rtf"] == [0.10, 0.12]
    assert first["windows"] == 2

    # The shipped marker propagates: it is true in either window, and a sweep
    # that lost it would print a table with no entry marked as what ships.
    second = agg["rows"][1]
    assert second["shipped"] is True
    assert second["kbps"] == [1200, 1300]

    quality = agg["quality_vs_cost"]
    # Per window: 40.0-43.0 and 41.5-44.0 dB against that window's own shipped row.
    assert quality["veryfast/crf20"]["psnr_db_vs_shipped"] == [-3.0, -2.5]
    assert quality["veryfast/crf20"]["kbps_ratio_vs_shipped"] == [
        round(800 / 1200, 3), round(900 / 1300, 3)]
    assert quality["slow/crf18"]["psnr_db_vs_shipped"] == [0.0, 0.0]
    assert quality["slow/crf18"]["kbps_ratio_vs_shipped"] == [1.0, 1.0]


def test_aggregate_sweep_pairs_rtf_within_a_window_not_across_the_range():
    """The wall-clock ratio is the figure a reader reaches for first, and it is
    the one the unpaired derivation gets wrong invisibly.

    Each entry's ``rtf`` is an independent min/max over the sweep, so dividing
    one entry's fastest window by the other's slowest states a speedup that no
    window produced. The window below is the proof shape: medium's slowest
    window is also slow's slowest, so the paired ratios are close to each other
    while the unpaired ones are not — an unpaired derivation would widen the
    reported range on both ends with figures the machine never measured.
    """
    window_a = _window([
        _row("medium", 18, kbps=900, ssim=0.9990, psnr=42.0, rtf=0.26),
        _row("slow", 18, kbps=880, ssim=0.9995, psnr=42.1, rtf=0.35,
             shipped=True),
    ])
    window_b = _window([
        _row("medium", 18, kbps=950, ssim=0.9992, psnr=43.5, rtf=0.22),
        _row("slow", 18, kbps=930, ssim=0.9996, psnr=43.6, rtf=0.30,
             shipped=True),
    ])

    quality = _aggregate_sweep([window_a, window_b])["quality_vs_cost"]
    # 0.26/0.35 and 0.22/0.30, window by window, then ordered min-first.
    assert quality["medium/crf18"]["rtf_ratio_vs_shipped"] == [
        round(0.22 / 0.30, 3), round(0.26 / 0.35, 3)]
    # The unpaired derivation this replaces would have returned 0.22/0.35 and
    # 0.26/0.30 — a range of 0.63-0.87 against the real 0.73-0.74, whose
    # endpoints are two different windows' numbers crossed. A regression to it
    # fails here rather than in a published figure.
    paired = quality["medium/crf18"]["rtf_ratio_vs_shipped"]
    assert paired[0] > round(0.22 / 0.35, 3) + 0.05
    assert paired[1] < round(0.26 / 0.30, 3) - 0.05


def test_aggregate_sweep_scores_each_window_against_that_windows_shipped_row():
    """The gap to the shipped entry is recomputed per window, not over the
    sweep's pooled min and max — a pooled gap would mix a window's PSNR with
    another window's shipped PSNR and report a range the data never contained."""
    window_a = _window([
        _row("veryfast", 20, kbps=800, ssim=0.9990, psnr=40.0, rtf=0.10),
        _row("slow", 18, kbps=1200, ssim=0.9995, psnr=43.0, rtf=0.35,
             shipped=True),
    ])
    # A window whose custom ladder does not include the shipped entry at all.
    window_c = _window([
        _row("veryfast", 20, kbps=810, ssim=0.9991, psnr=40.5, rtf=0.11),
        _row("medium", 20, kbps=850, ssim=0.9993, psnr=41.0, rtf=0.15),
    ])

    quality = _aggregate_sweep([window_a, window_c])["quality_vs_cost"]

    # Only window A names a shipped row, so both ends of the range come from it.
    assert quality["veryfast/crf20"]["psnr_db_vs_shipped"] == [-3.0, -3.0]
    assert quality["veryfast/crf20"]["kbps_ratio_vs_shipped"] == [
        round(800 / 1200, 3)] * 2
    # medium appears only in the window with no shipped row to score against, so
    # it contributes no gap rather than one fabricated from the other window.
    assert "medium/crf20" not in quality


# ── the denominator: a rate over time the file does not contain is a lie ──────

def test_the_median_averages_the_two_middle_samples_of_an_even_run():
    """``--repeat 4`` has no single middle sample; reporting one index as the
    median is a small mislabel that only surfaces when someone picks an even
    repeat, and the number feeds a cost model rather than a label."""
    assert _median([3.0, 1.0, 2.0]) == 2.0       # odd: the middle
    assert _median([4.0, 1.0, 3.0, 2.0]) == 2.5  # even: the two middles
    assert _median([2.5]) == 2.5


def test_an_emit_at_a_mismatched_fps_is_scored_over_its_real_duration(tmp_path):
    """``-frames:v`` without ``-r`` inherits the source's frame rate, so a file
    asked for at ``--fps 25`` still plays at the source's fps and the requested
    span is not its duration. Verified on the real corpus source: 240 frames at
    --fps 30, 29.97 and 25 all emit 8.008 s, while ``frames / fps`` says 8.000,
    8.008 and 9.600. The last would understate every RTF and bitrate in the
    record by 17% — a systematic error that reads as the encoder having gotten
    cheaper."""
    import subprocess

    source = ROOT / "corpus" / "media" / "pentagon-esper-briefing.mp4"
    if not source.is_file():
        pytest.skip("the corpus source is not checked in")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required")

    out = tmp_path / "mismatched.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-ss", "0.000", "-i", str(source),
         "-frames:v", "240", "-t", f"{240 / 25:.6f}",
         "-vf", "crop=406:720:x=0:y=0,scale=406:722:flags=lanczos",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-an", str(out)],
        check=True)

    # The requested span is 9.6 s. The file plays 8.008 s. The probe is what
    # stands between the record and a 17% error, so it has to return the latter.
    emitted = _emitted_duration(out, 240)
    assert abs(emitted - 240 / 29.97002997002997) < 0.02, (
        f"emitted duration {emitted:.3f}s is not the ~8.008s the file plays at "
        f"the source's fps; the bench would be dividing by {240 / 25:.3f}s")
    assert emitted < 240 / 25, (
        "the emitted duration is not shorter than the mismatched requested span; "
        "the premise of the probe is wrong")


def test_a_missing_probed_duration_falls_back_to_the_frame_count(tmp_path):
    """A container that reports no duration still has to produce a denominator
    rather than a ZeroDivisionError in every RTF the record carries. A raw H.264
    elementary stream is the real case: it probes with ``duration=N/A``, and
    ``float("N/A")`` raises, so the fallback to ``frames / fps`` is what every
    RTF for such a file would rest on."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required")
    raw = tmp_path / "elementary.h264"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=30:duration=2", "-c:v", "libx264",
         "-preset", "veryfast", "-f", "h264", str(raw)],
        check=True)

    emitted = _emitted_duration(raw, 60)
    # The stream reports duration=N/A, so the answer comes from the fallback:
    # frames over the probed fps. That fps is ffprobe's *stream* rate, which on
    # an elementary stream is inferred from packet spacing and reads 60/1 here
    # rather than the 30 the fixture was built at — 1.0 s, not 2.0 s. The point
    # of the assertion is not that the fallback is accurate on a container with
    # no clock (it cannot be) but that it yields a finite, positive denominator
    # instead of propagating an exception into every RTF the record carries.
    assert emitted > 0, (
        f"a stream with duration=N/A produced {emitted}; a rate needs a "
        f"finite positive denominator, not a propagated probe failure")
    assert emitted != 60 / 30.0, (
        "the fallback is reading the container's clock, not the stream's "
        "inferred rate; the premise of this test is wrong")


# ── disk: the lossless reference is the one genuinely large artifact ──────────

def test_the_disk_ceiling_is_scaled_on_the_outputs_dims_not_the_sources():
    """The ceiling is 1.5 bytes per output pixel per frame, for the lossless
    reference plus one entry. 240 frames at the tracked 406x722 crop is exactly
    that, hand-multiplied."""
    per_frame = 406 * 722 * 3 // 2
    assert _worst_case_bytes(240, 406, 722, 5) == 240 * per_frame * 2

    # The reference is resident for the whole window but the entries are not:
    # each is unlinked as soon as it has been scored. The peak is therefore the
    # reference plus one entry regardless of how many entries the ladder has, and
    # sizing it on the entry count is the over-conservative mistake that made the
    # gate refuse measurements that fit — 5 entries used to cost 3x the room
    # 1 entry did, for a peak that is the same either way.
    one = _worst_case_bytes(240, 406, 722, 1)
    assert _worst_case_bytes(240, 406, 722, 5) == one
    assert _worst_case_bytes(240, 406, 722, 20) == one

    # The source is 1280x720, and sizing this ceiling on the source instead of
    # the output would have refused a run needing a third of the space — the
    # failure mode the docstring records. A source dimension has no business
    # being an argument here, so the signature is gated rather than the value.
    assert _worst_case_bytes(240, 1280, 720, 5) > 3 * _worst_case_bytes(
        240, 406, 722, 5)
    params = list(inspect.signature(_worst_case_bytes).parameters)
    assert not any("src" in p or "source" in p for p in params), (
        f"the ceiling takes the output's dimensions, not the source's "
        f"({params}); a source parameter would scale it by the source and "
        f"refuse measurements that need a third of the room")


# ── paths: a bench whose source can be cleaned up is a bench nobody reruns ────

def test_the_default_source_is_the_checked_in_corpus_clip():
    assert render_bench.DEFAULT_SOURCE == (ROOT / "corpus" / "media"
                                           / "pentagon-esper-briefing.mp4")
    assert render_bench.SCRATCH == ROOT / "corpus" / "render"
    assert RUNS == ROOT / "corpus" / "render" / "runs"
    if not render_bench.DEFAULT_SOURCE.is_file():
        # ``.gitignore`` excludes ``corpus/media/`` wholesale — the sources are
        # hundreds of megabytes — so a clean checkout never carries one, and
        # every other test in the suite that needs one skips for that reason
        # (see test_render.py, test_fcpxml_vs_real_container.py). The two
        # assertions above are the part that is checkable everywhere: the bench
        # must point at the corpus, not at a scratch file, or a quoted figure
        # is re-derivable nowhere. Where the media is present, this asserts it.
        pytest.skip("needs corpus/media/pentagon-esper-briefing.mp4 "
                    "(gitignored media)")


def test_the_run_records_are_evidence_and_the_scratch_is_not():
    """``.gitignore`` excludes the rendered MP4s, which is right — they are
    regenerable and large. It must not exclude the run records alongside them:
    those are the evidence a quoted RTF is audited from, and a rule broad enough
    to cover them would let a number be quoted that no checkout can verify. The
    mirror failure is the other half: scratch that *is* ignored becoming
    committable, which would put rendered deliverables in the repo."""
    if not (RUNS / "pentagon-30s.json").is_file():
        pytest.skip("the render run records are not present")

    def ignored(rel):
        # A nonzero exit means git does not ignore the path. --quiet is used
        # rather than --no-index because the question is what a checkout would
        # actually track, not what git has never seen.
        proc = subprocess.run(["git", "-C", str(ROOT), "check-ignore",
                               "--quiet", rel])
        return proc.returncode == 0

    evidence = [RUNS / "pentagon-30s.json", RUNS / "pentagon-240f.json",
                RUNS / "pentagon-240f-sweep.json"]
    were_ignored = [str(p.relative_to(ROOT)) for p in evidence
                    if ignored(p.relative_to(ROOT))]
    assert not were_ignored, (
        "these run records are gitignored, so a figure quoted from them cannot "
        "be audited from a checkout: " + ", ".join(were_ignored))

    assert ignored("corpus/render/scratch/x.mp4"), (
        "corpus/render/scratch is not gitignored; the regenerable renders would "
        "be committable alongside the records that are the evidence")


# ── drift gate: the files a bench output obliges the reader to update ─────────

@pytest.mark.parametrize("entry", [*render_bench.REGENERATES_SHIP,
                                   *render_bench.REGENERATES_LADDER])
def test_every_regenerated_target_still_exists(entry):
    """Each entry names a file that a stale figure lives in and that the bench
    output therefore obliges the reader to re-check. A path that moved or was
    deleted is a stale pointer: the tool would name a file that no longer exists
    as the place to look, and the drift it reports would go unfixed.

    Read from the module rather than restated, so a path that moves has to move
    in both places. ``tests/test_render.py:134`` carries a line suffix, which is
    checked to be inside the file as well as on it.
    """
    token = entry.split()[0]
    rel, _, line = token.partition(":")
    path = ROOT / rel
    assert path.is_file(), f"{rel} is named as a regenerated target but does not exist"
    if line:
        # A line citation past the end of the file points at nothing, which reads
        # as a citation and is not one.
        n = int(line)
        total = len(path.read_text(encoding="utf-8").splitlines())
        assert 1 <= n <= total, f"{rel}:{n} is outside the file ({total} lines)"


def test_both_regenerates_lists_are_populated():
    # An emptied list is the quiet way to stop pointing at the docs a bench
    # output makes stale, and the citation check above would pass vacuously.
    assert render_bench.REGENERATES_SHIP, "the ship output names nothing to update"
    assert render_bench.REGENERATES_LADDER, "the ladder output names nothing to update"


# ── the committed run records are what the docs quote from ────────────────────

# The records are the evidence; these pin the figures so a doc quoting them can
# be caught drifting without re-rendering. Every one is skipped if its record is
# absent, because `corpus/render/runs` is regenerated by the tool.

SINGLE = RUNS / "pentagon-240f.json"
SWEEP = RUNS / "pentagon-240f-sweep.json"
SHIP = RUNS / "pentagon-30s.json"


@pytest.mark.parametrize("preset, crf, kbps, psnr", [
    # 240 frames of the pentagon briefing at t=0 through the tracked 406x722
    # crop, scored against a -crf 0 reference of the same chain. The shipped
    # entry is the last row; if any of these moves, the ladder table in
    # `_encoder_args`' docstring and the figures in `tests/test_render.py` move
    # with it.
    ("veryfast", 20, 646, 44.402),
    ("medium", 20, 749, 45.661),
    ("slow", 20, 712, 45.678),
    ("slow", 18, 932, 46.825),
    ("slow", 16, 1222, 47.992),
])
def test_the_committed_ladder_run_pins_each_entry(preset, crf, kbps, psnr):
    if not SINGLE.is_file():
        pytest.skip("pentagon-240f run not present")
    rec = json.loads(SINGLE.read_text(encoding="utf-8"))
    row = next((r for r in rec["rows"]
                if r["preset"] == preset and r["crf"] == crf), None)
    assert row is not None, f"{preset}/crf{crf} is not in the run record"
    assert row["kbps"] == kbps
    assert row["psnr_db"] == psnr


def test_the_committed_ladder_run_marks_the_shipped_entry():
    if not SINGLE.is_file():
        pytest.skip("pentagon-240f run not present")
    rec = json.loads(SINGLE.read_text(encoding="utf-8"))
    marked = [(r["preset"], r["crf"]) for r in rec["rows"] if r["shipped"]]
    assert marked == [("slow", 18)], (
        "the record no longer marks slow/crf18 as shipped, so it disagrees with "
        "the encoder the pipeline imports")
    assert rec["shipped_pair"] == list(_shipped_pair())


def test_the_committed_ladder_records_deltas_are_recomputed_from_its_own_rows():
    """The stored `quality_vs_cost` has to be what `_deltas` produces from the
    stored rows: a record whose summary disagreed with its own table would be
    evidence that contradicts itself."""
    if not SINGLE.is_file():
        pytest.skip("pentagon-240f run not present")
    rec = json.loads(SINGLE.read_text(encoding="utf-8"))
    shipped = next((r for r in rec["rows"] if r["shipped"]), None)
    assert _deltas(rec["rows"], shipped) == rec["quality_vs_cost"]


def test_the_committed_sweep_spans_the_ranges_the_tool_quotes():
    """The tool's docstring cites the shipped entry moving 932 -> 1345 kbps and
    46.83 -> 43.74 dB across the sweep; the record has to carry exactly that, or
    the docstring's own worked example is unsourced."""
    if not SWEEP.is_file():
        pytest.skip("pentagon-240f sweep not present")
    rec = json.loads(SWEEP.read_text(encoding="utf-8"))
    assert rec["starts"] == [0.0, 300.0, 600.0]
    assert rec["kind"] == "ladder_sweep"
    shipped = next((r for r in rec["rows"] if r["shipped"]), None)
    assert shipped is not None
    # `_mm` reports [min, max], so the dB pair is stored low before high even
    # though the docstring quotes the windows' order.
    assert shipped["kbps"] == [932, 1345]
    assert shipped["psnr_db"] == [43.737, 46.825]
    assert shipped["windows"] == 3


def test_the_committed_sweep_reaggregates_from_its_own_windows():
    if not SWEEP.is_file():
        pytest.skip("pentagon-240f sweep not present")
    rec = json.loads(SWEEP.read_text(encoding="utf-8"))
    agg = _aggregate_sweep(rec["per_window"])
    # The summary is reproducible from the windows it aggregates: the ranges and
    # the per-window gaps are a function of the record, not of the run. The one
    # exception is rtf_ratio_vs_shipped, which the committed sweep predates —
    # re-aggregating adds it, so it is compared apart from the rest rather than
    # deleted from the expectation.
    for entry, gap in rec["quality_vs_cost"].items():
        for key, expected in gap.items():
            assert agg["quality_vs_cost"][entry][key] == expected, (
                f"{entry}.{key} does not re-derive from the record's own windows")
        assert "rtf_ratio_vs_shipped" in agg["quality_vs_cost"][entry], (
            f"{entry}: re-aggregation produced no rtf ratio, so the pairing the "
            f"docstring's speed range rests on is no longer reproducible")
    by_key = {(r["preset"], r["crf"]): r for r in rec["rows"]}
    for row in agg["rows"]:
        pinned = by_key[(row["preset"], row["crf"])]
        assert row["kbps"] == pinned["kbps"]
        assert row["psnr_db"] == pinned["psnr_db"]
        assert row["shipped"] == pinned["shipped"]
        assert row["windows"] == pinned["windows"]


def test_the_committed_ship_record_describes_the_encoder_the_pipeline_ships():
    """The whole point of the tool is that a measured figure describes the
    encoder actually in use. The record has to name the live flags — if it
    records any other pair, the RTF rows in the docs describe a different
    encoder again, which is the defect the tool exists to close. The RTF itself
    is deliberately not pinned: it is load-dependent and a benchmark, not a
    gate."""
    if not SHIP.is_file():
        pytest.skip("pentagon-30s run not present")
    rec = json.loads(SHIP.read_text(encoding="utf-8"))
    assert rec["kind"] == "ship"
    assert rec["encoder"] == render_bench._encoder_flags(), (
        "the ship record was measured with an encoder the pipeline no longer "
        "ships; every doc quoting its RTF figures is stale")
    src = rec["source"]
    assert (src["width"], src["height"]) == (1280, 720)
    assert src["path"].endswith("pentagon-esper-briefing.mp4")
    for render in rec["renders"]:
        # A 30-second window at 30 fps is 900 frames; the orientations are both
        # present, so the record covers the two deliverables the cost model
        # multiplies. No RTF value is asserted here.
        assert render["orientation"] in ("16:9", "9:16")
        assert render["frames"] == 900
    assert sorted(r["orientation"] for r in rec["renders"]) == ["16:9", "9:16"]
