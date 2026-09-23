# Reframe perception — provenance, licences & references

The vertical (9:16) reframe now uses a real face detector. This document is the
record of what was copied, what was only studied, and what was rejected, so that
the perception layer can be replaced or re-licensed later without archaeology.

## What ships: YuNet face detector

`engine/models/face_detection_yunet.onnx` — 232,589 bytes, sha256
`8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.

- **Source:** [`opencv/opencv_zoo`](https://github.com/opencv/opencv_zoo),
  `models/face_detection_yunet`.
- **Licence:** Apache License 2.0. The full text is committed alongside the
  weights as `engine/models/face_detection_yunet.LICENSE`. Apache-2.0 is
  permissive and compatible with this project; the notice is preserved as the
  licence requires.
- **Provenance:** the weights were obtained from the opencv_zoo repository, not
  regenerated, and are unmodified — the sha256 above is the check against
  tampering or accidental truncation.
- **Runtime:** inference is via OpenCV's own `cv2.FaceDetectorYN`, so the only
  added weight is the 232 KB ONNX file. No Ultralytics, no Torch, no MediaPipe
  runtime ships with SelekyT.

### Why YuNet

The previous face path was `cv2.CascadeClassifier`. OpenCV 5.0 removed it, along
with `cv2.HOGDescriptor` and `cv2.legacy`, and `cv2.data.haarcascades` ships no
XML — so on a clean install of the pinned `opencv-python-headless>=4.9` the face
path was dead code and every vertical render silently degraded to the Canny
edge-density fallback. `cv2.FaceDetectorYN` is the OpenCV-5-surviving entry
point, it takes the zoo's YuNet model directly, and the model is small enough to
commit rather than download at runtime — which is what makes the Android/Termux
and offline paths work without a network fetch. The failure that motivated the
swap was a *detection* failure (no detector), not an accuracy failure, so a
detector of modest accuracy is the right cost/quality point: 232 KB versus
several hundred MB of a general object detector.

### Optionality and fallback

The detector is loaded lazily and its absence is not an error. If the weights
file is missing, or `onnxruntime`/`cv2.FaceDetectorYN` is unavailable on the
platform, `engine/reframe_perception.py` reports unavailable and `engine/reframe.py`
falls back to the edge-density active-region path that was previously the only
path. The output degrades to the previous behaviour; it does not fail the render.
This is exercised by the tests that cover the no-face and low-confidence cases.

## What was studied as a technical reference (not copied)

**[MediaPipe AutoFlip](https://github.com/google/mediapipe)** (Apache-2.0) was
read as a reference for the *architecture*, and its influence is in the layering
of `engine/reframe.py`, not in its code: the separation of a perception stage
from a crop-region decision stage from a camera-motion/shot-boundary stage, and
the idea that the crop is the output of an optimisation over candidate windows
rather than the byproduct of a detection's centroid. No AutoFlip code is in this
repository. Its default crop *behaviour* was deliberately not adopted: AutoFlip
can reframe aggressively on salient-but-non-human content, which is the opposite
of the editorial decision SelekyT needs (hold the speaker; treat a cut as a cut).

**[PySceneDetect](https://github.com/Breakthrough/PySceneDetect)**
(BSD-3-Clause) was evaluated for shot-boundary detection and **not adopted**.
The reframe already needs per-frame samples for tracking, and the boundary
signal it needs is "did the frame content change discontinuously," which is a
cheap delta between already-decoded adjacent samples. Adding a decode pipeline
and a dependency to produce that one boolean was not justified by a measured
improvement, so the boundary detector stays an internal one and PySceneDetect
remains a reference, not a requirement.

## What was rejected, and on what evidence

**[Ultralytics YOLO](https://github.com/ultralytics/ultralytics)** — AGPL-3.0.
This was benchmarked conceptually and rejected as a production dependency on two
grounds, either of which alone would be sufficient:

1. **Licence.** AGPL-3.0 is a strong copyleft network licence. SelekyT is
   distributed as a desktop application; the interaction between AGPL's
   network-use clause and a shipped application that a user runs on their own
   machine is genuinely uncertain, and resolving it would need counsel. YuNet is
   Apache-2.0 and needs no such resolution.
2. **Footprint versus value.** The failure being fixed was not an accuracy
   failure. A 232 KB detector that opens in OpenCV already takes the rendered
   face-presence measurement from 0.053 to 1.000 on the worst corpus clip. The
   several-hundred-MB YOLO runtime buys person/body/pose boxes that the crop
   decision does not currently consume, because for a talking-head briefing the
   face *is* the subject. If SelekyT later needs to reframe a scene where the
   speaker is not the most salient object — a wide two-shot, a walk-and-talk —
   that is the point at which a bigger detector would be worth benchmarking
   again, and it would have to win on measured output, not on capability lists.

**pyannote.audio** (speaker diarisation) and **SyncNet** (audio-visual sync) were
evaluated conceptually and not added. Both would let the crop pick the *speaking*
person rather than the most confident face, which is a real improvement over
"hold the most confident face." Neither was added because the corpus's
multi-speaker failure was solved at the tracking layer — the crop holds the
speaker it has and switches once, deliberately — and adding a GPU-oriented
diarisation stack to solve a problem already solved by 40 lines of hysteresis
would be a large dependency with no measured output gain. The case for revisiting
is specific and recorded: if real footage appears where two people are both
detectable and the wrong one is talking for long stretches, diarisation is the
right tool and the hysteresis is not.

## Licensing position of this layer

Nothing in the reframe stack is viral. The perception weights are Apache-2.0, the
inference is OpenCV's, and the planner, tracker, scorer and trajectory code is
original to this repository. If the perception layer is ever swapped for a
stronger detector, `engine/reframe_perception.py` is the only file that has to
change — the planner consumes a detection list and does not know which detector
produced it.
