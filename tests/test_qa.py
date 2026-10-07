"""QA validation tests — silent tail, black frames, diversity, coverage, pacing."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from engine.qa import (
    probe,
    validate_output,
    validate_outputs,
    audio_condition,
    decode_sample,
    detect_silent_tail,
    black_frame_pct,
    visual_diversity_score,
    validate_outputs_expanded,
    source_coverage,
    write_report,
)

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")


def _lavfi_video(out: Path, color: str = "blue", duration: float = 3.0,
                 size: str = "640x360", tone: float | None = 440.0,
                 silent_tail: float = 0.0) -> None:
    """Render lavfi test video. tone=None → no audio; silent_tail>0 → silence at end."""
    out.parent.mkdir(parents=True, exist_ok=True)
    filters = ["-f", "lavfi", "-i", f"color=c={color}:s={size}:d={duration}"]
    if tone is not None:
        tone_dur = max(0.0, duration - silent_tail)
        filters += [
            "-f", "lavfi", "-i", f"sine=frequency={tone}:duration={tone_dur}",
        ]
        if silent_tail > 0:
            filters += [
                "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={silent_tail}",
                "-filter_complex", f"[1:a][2:a]concat=n=2:v=0:a=1[aout]",
                "-map", "0:v", "-map", "[aout]",
            ]
        else:
            filters += ["-map", "0:v", "-map", "1:a", "-shortest"]
    else:
        filters += ["-map", "0:v"]
    cmd = ["ffmpeg", "-y", *filters,
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
           "-c:a", "aac", "-b:a", "64k", "-t", str(duration), str(out)]
    subprocess.run(cmd, check=True, capture_output=True)


# ── validate_output / validate_outputs ──

def test_validate_good_output():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "good.mp4"
        _lavfi_video(mp4)
        ok, errors = validate_output(mp4, vertical=False)
        assert ok and errors == []


def test_validate_orientation():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "vert.mp4"
        _lavfi_video(mp4, size="360x640")
        ok, _ = validate_output(mp4, vertical=True)
        assert ok


def _anamorphic_vertical(out: Path) -> Path:
    """A vertical frame whose pixels are wider than they are tall: 1080x1706 at
    SAR 8:9 displays as exactly 9:16, 1920 lines tall, in fewer pixels.

    This is the real geometry an NTSC-derived source delivers through
    ``vertical_scale_filter``, and it is the case a pixel-height floor gets
    wrong: 1706 < 1920 is true and the frame is still the canvas.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=blue:s=1080x1706:d=1.0",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
         "-vf", "setsar=8/9",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
         "-shortest", str(out)],
        check=True, capture_output=True)
    return out


def test_the_height_floor_is_judged_in_display_space():
    """The delivery floor is how tall the frame *displays*, not how many pixels
    it stores, because the burned-in cue scales with the displayed size."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        canvas = _anamorphic_vertical(td / "anamorphic.mp4")
        ok, errors = validate_output(canvas, vertical=True, min_height=1920)
        assert ok, (
            f"an anamorphic frame that displays 1920 lines failed the 1920-line "
            f"floor: {errors}")

        # The floor still rejects a frame that genuinely displays short: the
        # old 720-line delivery, which shipped cues at a sixth of their size.
        short = td / "short.mp4"
        _lavfi_video(short, size="406x720", tone=None)
        ok, errors = validate_output(short, vertical=True, min_height=1920)
        assert not ok and any("delivery height" in e for e in errors), errors


def test_validate_outputs_gathers_missing():
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "nope.mp4"
        errors = validate_outputs([missing])
        assert errors != []


# ── audio condition: the defect this whole layer exists for ──
#
# The shipped bug was a deliverable with no audio stream, produced by a render
# that exited 0 and reported nothing. That single symptom has two opposite
# causes, and the QA layer has to say which:
#
#   (a) the source has no audio at all  → the output is correct as-is
#   (b) the source has audio, the render lost it → broken render
#
# A test that only checks "validate_output fails on no audio" cannot tell them
# apart and neither could the old code. Each fixture below is built with real
# ffmpeg rather than a hand-crafted container, so the check is exercised
# against files that really have (or really lack) the property.


def _source_with_audio(out: Path, duration: float = 4.0) -> Path:
    """A real file with a genuine audio track, for the broken-render fixtures."""
    _lavfi_video(out, color="blue", duration=duration, size="640x360")
    return out


def _source_without_audio(out: Path, duration: float = 4.0) -> Path:
    """A real file with no audio track at all — tone=None suppresses the stream."""
    _lavfi_video(out, color="blue", duration=duration, size="640x360", tone=None)
    return out


def _video_only_of(source: Path, out: Path, duration: float = 4.0) -> Path:
    """Re-encode a source with ``-an``: a real video-only file, as a broken render."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-an", "-t", str(duration),
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_audio_condition_absent_broken_is_an_error():
    """(b) The source has audio, the output does not → broken render, hard error.

    This is the regression guard for the original defect. If someone later
    weakens the audio check to advisory, or re-adds a bare 'no audio stream'
    that a video-only source also trips, this test fails on the wrong side.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = _source_with_audio(td / "src.mp4")
        broken = _video_only_of(src, td / "facebook" / "broken.mp4")

        cond = audio_condition(broken, src)
        assert cond["state"] == "absent_broken", cond
        assert "NO AUDIO RETAINED" in cond["message"], cond["message"]
        # The message must name the distinguishing fact: the source *has* audio.
        assert "source has an audio stream" in cond["message"], cond["message"]

        ok, errors = validate_output(broken, vertical=False, source=src)
        assert ok is False
        assert any("NO AUDIO RETAINED" in e for e in errors), errors


def test_audio_condition_absent_expected_is_not_an_error():
    """(a) A video-only output of a video-only source is correct, not a defect.

    This is the deliberate semantics change and it needs its own test: the old
    code appended 'no audio stream' unconditionally, which would have failed
    this file. If that error is ever re-added, this assertion is what breaks.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = _source_without_audio(td / "silent_src.mp4")
        faithful = _video_only_of(src, td / "facebook" / "video_only.mp4")

        cond = audio_condition(faithful, src)
        assert cond["state"] == "absent_expected", cond
        assert "video-only deliverable by design" in cond["message"], cond["message"]

        ok, errors = validate_output(faithful, vertical=False, source=src)
        assert ok is True, f"a video-only source must not fail QA: {errors}"
        assert errors == [], errors


def test_audio_condition_declared_but_empty_is_the_silent_failure():
    """A declared audio stream carrying nothing is the shipped defect's shape.

    The container provably held audio packets while the program had no sound;
    a stream that exists on paper and decodes to nothing is that same class.
    Built with ffmpeg (``anullsrc`` at ~0 duration) so the check is tested
    against a real file that really has the defect, not a fabricated probe.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        out = td / "empty.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "color=c=red:s=640x360:d=4:rate=30",
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=0.001",
             "-map", "0:v", "-map", "1:a",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
             "-c:a", "aac", str(out)],
            check=True, capture_output=True,
        )
        # The audio stream is genuinely there in the container...
        probe_data = probe(out)
        assert any(s.get("codec_type") == "audio" for s in probe_data["streams"]), \
            "fixture premise: an audio stream must be declared"

        cond = audio_condition(out, None)
        assert cond["state"] == "declared_but_empty", cond
        assert "COULD NOT BE" in cond["message"], cond["message"]

        ok, errors = validate_output(out, vertical=False)
        assert ok is False
        assert any("COULD NOT BE" in e for e in errors), errors


def test_audio_condition_unprobeable_source_is_treated_as_broken():
    """A source that cannot be probed must not license a video-only output.

    If it did, pointing the pipeline at a path that does not exist would pass
    a broken render as a faithful video-only delivery. The conservative
    fall-through is the default when the evidence is missing.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src_with_audio = _source_with_audio(td / "src.mp4")
        broken = _video_only_of(src_with_audio, td / "facebook" / "broken.mp4")
        nowhere = td / "does_not_exist.mp4"

        cond = audio_condition(broken, nowhere)
        assert cond["state"] == "absent_broken", cond
        ok, errors = validate_output(broken, vertical=False, source=nowhere)
        assert ok is False, "an unprobeable source must not pass a video-only render"
        assert any("could not be probed" in e for e in errors), errors


def test_audio_condition_present_on_a_good_render():
    """The happy path must still classify as present, with the evidence named."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        out = td / "facebook" / "good.mp4"
        _lavfi_video(out, color="blue", duration=3.0, size="640x360")
        cond = audio_condition(out, out)
        assert cond["state"] == "present", cond


# ── decode sampling: headers can lie ──


def test_decode_sample_decodes_real_frames_and_audio():
    """The sampler must return nonzero counts for a real file, not just exit 0."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "good.mp4"
        _lavfi_video(out, duration=3.0)
        sample = decode_sample(out, max_frames=8, audio_seconds=2.0)
        assert sample["video_error"] is None, sample
        assert sample["audio_error"] is None, sample
        assert sample["video_frames"] > 0, "frames must actually be decoded"
        assert sample["audio_samples"] > 0, "audio samples must actually be decoded"


def test_decode_sample_reports_missing_audio_stream():
    """A video-only file must report the audio half as an error, not zero samples.

    Zero samples and 'no stream to decode' are different conditions; the
    message has to say which, because one is a broken render and the other is
    a render of a silent source.
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "videonoly.mp4"
        _lavfi_video(out, duration=3.0, tone=None)
        sample = decode_sample(out)
        assert sample["audio_error"] is not None, sample
        assert "no audio stream to decode" in sample["audio_error"], sample
        assert sample["video_frames"] > 0, "the video half should still decode"


# ── frame rate: the deliverable must match the grid its EDL was cut on ──


def test_expected_fps_mismatch_is_an_error():
    """A 25 fps render claimed as 30 fps has desynced from its own timeline."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "fb.mp4"
        _lavfi_video(out, duration=3.0, size="640x360")
        ok, errors = validate_output(out, vertical=False, expected_fps=30.0)
        assert ok is False
        assert any("expected 30.000 fps" in e for e in errors), errors


def test_expected_fps_within_tolerance_passes():
    """29.97 against a claimed 30 is a 0.1% deviation, not a desync.

    The 2% tolerance is wide enough for the fractional rates real sources use
    (30000/1001, 24000/1001) and narrow enough that a genuine wrong-rate
    render — one that would drift a full frame every ~2 s against its EDL —
    is still caught.
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "fb.mp4"
        # Render at an exact 29.97 by lavfi rate, then claim 30: inside 2%.
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=3:rate=30000/1001",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-map", "0:v", "-map", "1:a", "-shortest",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
             "-c:a", "aac", str(out)],
            check=True, capture_output=True,
        )
        ok, errors = validate_output(out, vertical=False, expected_fps=30.0)
        assert ok is True, f"29.97 vs 30 is inside tolerance: {errors}"


def test_expected_fps_wrong_rate_is_caught_not_waved_through():
    """25 vs 30 is a 17% deviation and must fail despite a tolerant threshold.

    This is the discriminating counterpart to the test above: the 2% tolerance
    is a property of fractional-rate sources, not a licence to ship any rate.
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "fb.mp4"
        _lavfi_video(out, duration=3.0, size="640x360")
        ok, errors = validate_output(out, vertical=False, expected_fps=25.0)
        assert ok is True, "25 claimed as 25 is correct"
        ok, errors = validate_output(out, vertical=False, expected_fps=50.0)
        assert ok is False
        assert any("50.000 fps" in e for e in errors), errors


# ── silent tail ──

def test_detect_silent_tail_positive():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "tail.mp4"
        _lavfi_video(mp4, duration=6.0, silent_tail=2.0)
        has_tail, sec, error = detect_silent_tail(mp4, min_sec=1.0)
        assert error is None, f"a clean probe must not report an error: {error}"
        assert has_tail
        assert sec > 0


def test_detect_silent_tail_negative():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "notail.mp4"
        _lavfi_video(mp4, duration=6.0)
        has_tail, sec, error = detect_silent_tail(mp4, min_sec=1.0)
        assert error is None
        assert not has_tail
        assert sec == 0.0


def test_detect_silent_tail_short_video_noop():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "short.mp4"
        _lavfi_video(mp4, duration=1.0)
        has_tail, sec, error = detect_silent_tail(mp4)
        assert not has_tail and sec == 0.0 and error is None


def test_detect_silent_tail_reports_probe_failure_not_silence():
    """A probe that cannot run is not the same as "no silent tail".

    This is the failure mode F14 fixed: an ffmpeg error used to return
    (False, 0.0) through a bare except, so a genuinely broken render could
    pass QA as though it were clean. The third return value must carry the
    failure so the caller can fail the file.
    """
    with tempfile.TemporaryDirectory() as td:
        junk = Path(td) / "not-a-video.mp4"
        junk.write_bytes(b"definitely not a media container")
        has_tail, sec, error = detect_silent_tail(junk)
        # Either ffprobe refuses it (error set) or the probe reports failure;
        # either way it must not read as a verified-clean file.
        assert error is not None, "an unprobeable file must not look clean"
        assert has_tail is False
        assert sec == 0.0


# ── black frame percentage ──

def test_black_frame_pct_black_video():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "black.mp4"
        _lavfi_video(mp4, color="black")
        pct = black_frame_pct(mp4)
        assert pct > 0.8


def test_black_frame_pct_bright_video():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "blue.mp4"
        _lavfi_video(mp4, color="blue")
        pct = black_frame_pct(mp4)
        assert pct < 0.2


def test_black_frame_pct_ranges():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "wht.mp4"
        _lavfi_video(mp4, color="white")
        pct = black_frame_pct(mp4)
        assert 0.0 <= pct <= 1.0


# ── visual diversity ──

def test_visual_diversity_identical_clips_low():
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td) / "a.mp4", Path(td) / "b.mp4"
        _lavfi_video(a, color="blue", duration=2.0)
        _lavfi_video(b, color="blue", duration=2.0)
        score = visual_diversity_score([a, b])
        assert score < 0.1


def test_visual_diversity_different_clips_higher():
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a.mp4"
        b = Path(td) / "b.mp4"
        _lavfi_video(a, color="blue", duration=2.0)
        _lavfi_video(b, color="red", duration=2.0)
        score = visual_diversity_score([a, b])
        assert score > 0.1


def test_visual_diversity_single_clip_defaults_1():
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a.mp4"
        _lavfi_video(a)
        score = visual_diversity_score([a])
        assert score == 1.0


# ── expanded validation ──

def test_validate_outputs_expanded_shape():
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "facebook").mkdir()
        (Path(td) / "shorts").mkdir()
        fb = Path(td) / "facebook" / "01.mp4"
        sh = Path(td) / "shorts" / "01.mp4"
        _lavfi_video(fb, duration=3.0, size="640x360")
        _lavfi_video(sh, duration=3.0, size="360x640")
        report = validate_outputs_expanded([fb, sh])
        assert report["ok"] is True
        assert report["errors"] == []
        assert report["warnings"] == []
        assert report["diversity"] == 1.0  # facebook has only one clip
        for f in (fb, sh):
            fff = report["per_file"][str(f)]
            assert fff["black_frame_pct"] < 0.2
            assert fff["silent_tail"] is False
            assert fff["size"] > 0


# ── source coverage ──

def test_source_coverage_from_selections():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        out = td / "facebook" / "01.mp4"
        _lavfi_video(out, duration=3.0, size="640x360")
        (td / "selections.json").write_text(json.dumps([
            {"start": 0.0, "end": 4.0},
            {"start": 5.0, "end": 6.0},
            {"start": 5.5, "end": 6.5},  # overlaps previous
        ]))
        cov = source_coverage([out], td / "selections.json", source_duration=10.0)
        assert cov["coverage_pct"] == 55.0  # 0-4 + 5-6.5 = 5.5s / 10s
        assert cov["overlap_count"] == 1
        assert cov["total_covered_sec"] == 5.5


def test_source_coverage_no_file():
    with tempfile.TemporaryDirectory() as td:
        cov = source_coverage([], Path(td) / "missing.json")
        assert cov["coverage_pct"] == 0
        assert cov["overlap_count"] == 0


def test_source_coverage_falls_back_to_probe():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        out = td / "out.mp4"
        _lavfi_video(out, duration=2.0)
        (td / "selections.json").write_text(json.dumps([{"start": 0, "end": 1}]))
        cov = source_coverage([out], td / "selections.json")
        assert cov["coverage_pct"] > 0


# ── write_report end-to-end ──

def test_write_report_full():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "facebook").mkdir()
        (td / "shorts").mkdir()
        fb = td / "facebook" / "01.mp4"
        sh = td / "shorts" / "01.mp4"
        _lavfi_video(fb, duration=3.0, size="640x360")
        _lavfi_video(sh, duration=3.0, size="360x640")
        (td / "selections.json").write_text(json.dumps([{"start": 1, "end": 3}]))
        src = td / "src.mp4"
        _lavfi_video(src, duration=30.0, size="640x360")
        (td / "pacing_report.json").write_text(json.dumps({"mean_length_s": 2.0}))
        qa_path = write_report(td, [fb, sh], source_path=str(src))
        data = json.loads(qa_path.read_text())
        assert data["ok"] is True
        assert "source_coverage" in data
        assert data["source_coverage"]["coverage_pct"] > 0
        assert "pacing" in data
        assert data["pacing"] == {"mean_length_s": 2.0}
        assert "diversity" in data
        assert "per_file" in data


def test_write_report_silent_tail_is_advisory_not_fatal():
    """A trailing silence run is an editorial warning, not a broken render.

    F2 split the two tiers: the clip is a real, playable video with a quiet
    tail, so QA records it in ``warnings`` and stays ``ok`` — the render is
    reusable and an editor decides. Hard failures (below) still raise.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "facebook").mkdir()
        (td / "shorts").mkdir()
        fb = td / "facebook" / "01.mp4"
        sh = td / "shorts" / "01.mp4"
        _lavfi_video(fb, duration=6.0, size="640x360", silent_tail=2.0)
        _lavfi_video(sh, duration=6.0, size="360x640", silent_tail=2.0)
        (td / "selections.json").write_text(json.dumps([{"start": 1, "end": 3}]))
        src = td / "src.mp4"
        _lavfi_video(src, duration=30.0)
        qa_path = write_report(td, [fb, sh], source_path=str(src))
        data = json.loads(qa_path.read_text())
        assert data["ok"] is True, "a silent tail must not fail the run"
        assert data["errors"] == []
        assert any("silent tail" in w for w in data["warnings"]), \
            "the tail must still be reported as a warning"
        for f in (fb, sh):
            assert data["per_file"][str(f)]["silent_tail"] is True


def test_write_report_raises_on_hard_render_failure():
    """A file that is not a valid render is a hard failure and must raise.

    This is the other half of F2: weakening QA for advisory findings must not
    weaken detection of genuinely broken output.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "facebook").mkdir()
        (td / "shorts").mkdir()
        fb = td / "facebook" / "01.mp4"
        sh = td / "shorts" / "01.mp4"
        _lavfi_video(fb, duration=3.0, size="640x360")
        # Vertical slot holds a landscape video: wrong orientation is a hard
        # structural error, not an advisory quality note.
        _lavfi_video(sh, duration=3.0, size="640x360")
        (td / "selections.json").write_text(json.dumps([{"start": 1, "end": 3}]))
        src = td / "src.mp4"
        _lavfi_video(src, duration=30.0)
        with pytest.raises(RuntimeError):
            write_report(td, [fb, sh], source_path=str(src))


# ── duration against the edit plan ──

def test_validate_output_accepts_duration_matching_plan():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "good.mp4"
        _lavfi_video(mp4, duration=3.0)
        ok, errors = validate_output(mp4, vertical=False, expected_duration=3.0)
        assert ok, errors


def test_validate_output_tolerates_small_duration_drift():
    """ffmpeg clocks its own container duration, so exact equality is hopeless.

    The 1% band is roughly seven times the worst drift measured on the corpus
    (0.025 s in 17.08 s); anything inside it is the same cut measured twice.
    """
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "good.mp4"
        _lavfi_video(mp4, duration=3.0)
        ok, errors = validate_output(mp4, vertical=False, expected_duration=3.02)
        assert ok, errors


def test_validate_output_rejects_a_dropped_interval():
    """The one failure no other check can see: a cleanly encoded wrong cut.

    A render that silently dropped a retention interval is a playable video of
    the wrong program — it decodes, has audio, sits at the right aspect and
    frame rate, and passes every structural check. Only the plan's own sum
    notices that a piece of the program is missing.
    """
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "short.mp4"
        _lavfi_video(mp4, duration=3.0)
        ok, errors = validate_output(mp4, vertical=False, expected_duration=10.0)
        assert not ok
        assert any("edit plan expected" in e for e in errors), errors
        assert any("dropped or duplicated" in e for e in errors), errors


def test_validate_output_rejects_an_overlong_render():
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "long.mp4"
        _lavfi_video(mp4, duration=10.0)
        ok, errors = validate_output(mp4, vertical=False, expected_duration=3.0)
        assert not ok
        assert any("edit plan expected" in e for e in errors), errors


def test_write_report_checks_durations_against_the_plan():
    """The plan the pipeline writes is what the renders are measured against."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "facebook").mkdir()
        (td / "shorts").mkdir()
        fb = td / "facebook" / "01.mp4"
        sh = td / "shorts" / "01.mp4"
        _lavfi_video(fb, duration=3.0, size="640x360")
        _lavfi_video(sh, duration=3.0, size="360x640")
        (td / "selections.json").write_text(json.dumps([{"start": 1, "end": 3}]))
        (td / "edit_plan.json").write_text(json.dumps([
            {"index": 1, "edited_duration": 3.0, "intervals": [[0.0, 3.0]]},
        ]))
        src = td / "src.mp4"
        _lavfi_video(src, duration=30.0, size="640x360")
        data = json.loads(write_report(td, [fb, sh], source_path=str(src)).read_text())
        assert data["ok"] is True, data["errors"]


def test_write_report_fails_when_a_clip_is_shorter_than_the_plan():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "facebook").mkdir()
        (td / "shorts").mkdir()
        fb = td / "facebook" / "01.mp4"
        sh = td / "shorts" / "01.mp4"
        _lavfi_video(fb, duration=3.0, size="640x360")
        _lavfi_video(sh, duration=3.0, size="360x640")
        (td / "selections.json").write_text(json.dumps([{"start": 1, "end": 3}]))
        (td / "edit_plan.json").write_text(json.dumps([
            {"index": 1, "edited_duration": 9.0, "intervals": [[0.0, 9.0]]},
        ]))
        src = td / "src.mp4"
        _lavfi_video(src, duration=30.0, size="640x360")
        with pytest.raises(RuntimeError) as excinfo:
            write_report(td, [fb, sh], source_path=str(src))
        assert "edit plan expected" in str(excinfo.value)


def test_write_report_ignores_an_unreadable_plan():
    """A plan that cannot be parsed costs the plan comparison, not the run."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "facebook").mkdir()
        (td / "shorts").mkdir()
        fb = td / "facebook" / "01.mp4"
        sh = td / "shorts" / "01.mp4"
        _lavfi_video(fb, duration=3.0, size="640x360")
        _lavfi_video(sh, duration=3.0, size="360x640")
        (td / "selections.json").write_text(json.dumps([{"start": 1, "end": 3}]))
        (td / "edit_plan.json").write_text("{not json")
        src = td / "src.mp4"
        _lavfi_video(src, duration=30.0, size="640x360")
        data = json.loads(write_report(td, [fb, sh], source_path=str(src)).read_text())
        assert data["ok"] is True, data["errors"]


# ── unmeasurable advisory metrics ──

def test_black_frame_pct_reports_none_for_an_unreadable_file():
    """``0.0`` would read as "verified no black frames" on a file it never read."""
    with tempfile.TemporaryDirectory() as td:
        junk = Path(td) / "not_a_video.mp4"
        junk.write_bytes(b"not a video at all")
        assert black_frame_pct(junk) is None


def test_visual_diversity_reports_none_when_no_clip_decodes():
    """Two clips given and none readable is no measurement, not diversity 1.0."""
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a.mp4"
        b = Path(td) / "b.mp4"
        a.write_bytes(b"not a video")
        b.write_bytes(b"not a video either")
        assert visual_diversity_score([a, b]) is None


def test_unmeasurable_black_ratio_is_reported_not_read_as_clean(monkeypatch):
    """An unmeasurable metric must appear in qa.json as a gap, not a number.

    Mutation-checked by forcing the failure path directly: with the metric
    returning ``None`` the run stays ``ok`` (the video is still valid) but says
    so in warnings, instead of recording ``black_frame_pct: 0.0`` for a file
    whose frames were never read.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "facebook").mkdir()
        (td / "shorts").mkdir()
        fb = td / "facebook" / "01.mp4"
        sh = td / "shorts" / "01.mp4"
        _lavfi_video(fb, duration=3.0, size="640x360")
        _lavfi_video(sh, duration=3.0, size="360x640")
        monkeypatch.setattr("engine.qa.black_frame_pct", lambda path, **kw: None)
        data = json.loads(write_report(td, [fb, sh]).read_text())
        assert data["ok"] is True, "an unmeasured advisory metric is not a hard failure"
        assert data["per_file"][str(fb)]["black_frame_pct"] is None
        assert any("black-frame ratio could not be measured" in w for w in data["warnings"])