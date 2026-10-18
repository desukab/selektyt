"""The multimodal layer: measured eyes on the preselection tier.

These pins are about the *shape* of the reads, not graded thresholds: the
blend weights, the frozen floor and the tier budget say what the measured
read is, and a change to any of them is a change to what selection is
allowed to judge on.
"""
from pathlib import Path

from engine.multimodal import (
    blend_visual, profile_candidates, profile_clip, read_spans,
)


def test_blend_keeps_the_predictive_score_when_nothing_was_measured():
    # No profile -> the text-based prediction is the whole visual score;
    # the layer must not invent evidence it did not measure.
    assert blend_visual(None, 0.42) == 0.42
    assert blend_visual({}, 0.42) == 0.42
    assert blend_visual(None, -3.0) == 0.0
    assert blend_visual(None, 5.0) == 1.0


def test_a_lively_profile_outscores_a_flat_one():
    live = {"motion_max_mean_px": 20.0, "hard_cut_share": 0.3,
            "face_presence_fraction": 0.8, "brightness_spread": 30.0}
    flat = {"motion_max_mean_px": 0.5, "hard_cut_share": 0.0,
            "face_presence_fraction": 0.2, "brightness_spread": 4.0}
    assert blend_visual(live, 0.3) > blend_visual(flat, 0.3)
    # ... and the measured read can beat a large predictive edge outright:
    # footage that lives loses to no transcript promise
    assert blend_visual(live, 0.2) > blend_visual(flat, 0.9)


def test_a_frozen_read_floors_the_measured_visual():
    frozen = {"motion_max_mean_px": 0.0, "hard_cut_share": 0.0,
              "face_presence_fraction": 0.0, "brightness_spread": 1.0}
    # the transcript promising a visual story cannot keep the borrow
    assert blend_visual(frozen, 0.9) < 0.35


def _tiny_media(tmp_path: Path) -> Path:
    import cv2
    import numpy as np
    out = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"),
                             10, (160, 120))
    try:
        for i in range(60):
            frame = np.zeros((120, 160, 3), dtype=np.uint8)
            cv2.putText(frame, str(i // 10), (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            writer.write(frame)
    finally:
        writer.release()
    return out


def test_a_profile_names_what_it_measured(tmp_path: Path):
    media = _tiny_media(tmp_path)
    p = profile_clip(media, 0.0, 6.0)
    assert p is not None
    assert p["samples"] > 0
    assert p["provenance"]["frame_reader"] == "engine.reframe._sample_frames"
    assert p["provenance"]["face_detector"] == "engine.reframe_perception.FaceDetector"
    for key in ("motion_max_mean_px", "hard_cut_share",
                "face_presence_fraction", "brightness_mean"):
        assert key in p
    # a changing counter-text means the frames move
    assert p["motion_max_mean_px"] > 0.0


def test_a_fully_blank_read_is_unmeasurable(tmp_path: Path):
    media = _tiny_media(tmp_path)
    span = (100.0, 104.0)
    p = profile_clip(media, span[0], span[1])
    assert p is None, "frames past the end of a media read as nothing"


def test_the_preselection_quota_binds(tmp_path: Path):
    class Fake:
        def __init__(self, i: int):
            self.start, self.end = float(i), float(i) + 2.0
            self.visual_potential = 0.1

    media = _tiny_media(tmp_path)
    pairs = profile_candidates(media, [Fake(i) for i in range(30)],
                               quota=12)
    assert len(pairs) == 12, "the tier is the discovery budget, not the pool"


def test_the_reader_runs_in_a_child_and_preserves_unmeasurable_spans(
    tmp_path: Path,
):
    # The reader is native code (OpenCV decode + DNN); a crash there took a
    # whole editorial run down without a traceback once. The contract: the
    # pipeline only ever talks to a child process, and a span the child cannot
    # read comes back as None rather than poisoning the batch.
    media = _tiny_media(tmp_path)
    profiles = read_spans(media, [(0.0, 6.0), (1_000_000.0, 1_000_003.0)])
    assert isinstance(profiles, list) and len(profiles) == 2
    assert profiles[0] is not None and profiles[0]["samples"] > 0
    assert profiles[1] is None
