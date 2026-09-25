from __future__ import annotations
import cv2
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path

from providers.ffmpeg import probe_sample_aspect
from engine.reframe_perception import get_detector, find_shot_boundaries


# --------------------------------------------------------------------------- #
# Tuning. Every value here is either measured on the corpus or is a deliberate
# editorial choice; none is a placeholder. See the notes on each one.
# --------------------------------------------------------------------------- #

# A detection can continue a track only if its centre moved less than this many
# face-widths since the track's last sighting. Measured on short04: the two
# speakers stand ~600 px apart with faces ~100 px wide, so 2.5 widths (250 px)
# never merges two people, while a real speaker's drift between 1.1 s samples is
# a fraction of a face width.
_MAX_JUMP_WIDTHS = 2.5

# After a shot boundary a track only re-binds this tightly. A pan trips the
# boundary detector too, and the same speaker usually returns near where they
# left; one face width is the "same person, same place" test, and anything
# looser would let a new shot's first speaker inherit the previous shot's
# subject.
_MAX_JUMP_AFTER_CUT_WIDTHS = 1.0

# A challenger has to beat the incumbent by this factor before the crop moves
# to a new subject. 1.4 is deliberately not 1.0: a coin-flip between two
# equally-confident detections would otherwise oscillate the crop between two
# people for the whole clip, which is the visible failure this layer exists to
# prevent.
_SWITCH_MARGIN = 1.4

# Once a subject is chosen the crop holds them for at least this long, even if a
# challenger looks better. Below ~3 s a switch reads as a mistake rather than an
# editorial decision; 4 s is comfortably inside a typical short-form shot.
_MIN_HOLD_S = 4.0

# Evidence is gathered over this window either side of each sample, so a subject
# who appears or leaves partway through a clip is scored on when they were
# actually there rather than on a clip-wide average.
_EVIDENCE_WINDOW_S = 3.0

# The crop may travel at most this fraction of the source width per second. A
# full traverse of a 1280 px source takes ~4.5 s, which makes any subject switch
# a visible deliberate reframe rather than a twitch, and is slow enough that a
# static subject produces a static crop because the target stops moving first.
_MAX_SPEED_PER_S = 0.22

# ...except briefly after a shot boundary, where the cap is lifted. A cut is
# instant in the source, so a crop that re-establishes at the normal rate pans
# for ~2 s across the edit point; an operator would snap and be done. The lifted
# rate is still finite because a whip pan also trips the boundary detector and
# must not become a hard jump.
_MAX_SPEED_AFTER_CUT_PER_S = 1.2
_SETTLE_S = 0.75

# Lead room given in the direction the subject is travelling, in face widths.
# Stationary subject -> zero lead -> face centred, which is the boring safe
# default; a moving subject gets room to walk into. 0.6 widths is a subtle
# offset, well short of the third-of-frame lead a human operator would leave.
_LEAD_WIDTHS = 0.6
_LEAD_MAX_FRAC = 0.35  # never let lead alone push the subject past a third off-centre

# Two consecutive crop targets closer than this, in pixels, are the same target:
# expressing both makes ffmpeg interpolate a move that no editor would make.
_TARGET_EPS_PX = 6

# Keep this much of the crop between the face's near edge and the crop boundary,
# so a subject at the edge of the source is not half-out of the deliverable.
_FACE_MARGIN_PX = 24

# Half-width of the low pass applied to the elected subject position. One
# second: enough to damp a subject's own fidgeting, short enough that a real
# traverse still arrives. See _smooth_elected.
_SMOOTH_HALF_S = 1.0


def _fallback_center(frame: np.ndarray, gray: np.ndarray | None = None) -> float:
    """The no-detector heuristic: brightest structured column region.

    The previous version took the first moment of the edge profile — the
    centroid of edge density. On a two-shot that is precisely wrong: two
    speakers give a bimodal profile whose centroid is the empty middle between
    them, so the "safe" fallback systematically reframed onto nobody. On short04
    it returned 0.431-0.453 against speakers at 0.25 and 0.71 of the width.

    The fix is to take the peak instead of the mean, then re-centre locally
    around it: the strongest cluster of structure is where the subject is, and
    the local centroid removes single-column spikes from a bright background
    edge. This is still a heuristic of last resort — no detector means no
    identity, no confidence and no notion of "active" — but it no longer
    prefers the gap between two people.
    """
    if gray is None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    profile = edges.mean(axis=0).astype(np.float32)
    kernel = np.ones(81, np.float32) / 81.0
    profile = np.convolve(profile, kernel, mode="same")
    total = float(profile.sum())
    if total <= 1e-6:
        return 0.5
    peak = int(np.argmax(profile))
    half = max(4, int(len(profile) * 0.08))
    lo, hi = max(0, peak - half), min(len(profile), peak + half)
    region = profile[lo:hi]
    xs = np.arange(lo, hi, dtype=np.float32)
    if float(region.sum()) <= 1e-6:
        return float(peak) / len(profile)
    centre = float((xs * region).sum() / region.sum())
    return float(np.clip(centre / len(profile), 0.05, 0.95))


def _smooth_centers(
    centers: list[tuple[float, float]], window: int = 5
) -> list[tuple[float, float]]:
    """Symmetric moving average over the tracked center column.

    Retained for the fallback path, where there is no identity layer to do the
    job properly and a moving average is still better than raw per-sample
    edge-centres. The detector path does not use this: averaging across a shot
    cut blends two different compositions into one, and it cannot express a
    deliberate hold-then-move.
    """
    if len(centers) < 3 or window < 2:
        return list(centers)
    half = window // 2
    out: list[tuple[float, float]] = []
    for i, (t, _c) in enumerate(centers):
        lo = max(0, i - half)
        hi = min(len(centers), i + half + 1)
        mean = float(np.mean([c for _, c in centers[lo:hi]]))
        out.append((t, mean))
    return out


# --------------------------------------------------------------------------- #
# Layer 2: identity-persistent tracking
# --------------------------------------------------------------------------- #

@dataclass
class _Track:
    """One subject's sightings across the interval.

    ``samples`` are ``(t, x, y, w, h, score)`` in source pixels with ``t``
    relative to the interval start. Identity is positional only — nearest
    centre within a face-size-relative radius — which is enough to separate two
    static speakers and cheap enough to need no model. A real re-identification
    embedding (``cv2.FaceRecognizerSF``) would handle people who cross each
    other; that is the one case positional identity loses, and it is left out
    deliberately rather than assumed.
    """
    samples: list[tuple[float, float, float, float, float, float]] = field(default_factory=list)

    def center(self, t: float) -> tuple[float, float] | None:
        """Interpolated face centre at ``t``, or None outside the sighting span."""
        if not self.samples:
            return None
        ts = [s[0] for s in self.samples]
        if t < ts[0] or t > ts[-1]:
            return None
        if len(self.samples) == 1:
            _t, x, y, w, h, _s = self.samples[0]
            return x + w / 2, y + h / 2
        for (t0, x0, y0, w0, h0, _), (t1, x1, y1, w1, h1, _) in zip(
                self.samples, self.samples[1:]):
            if t0 <= t <= t1:
                f = (t - t0) / max(1e-6, t1 - t0)
                return (x0 + w0 / 2) * (1 - f) + (x1 + w1 / 2) * f, \
                    (y0 + h0 / 2) * (1 - f) + (y1 + h1 / 2) * f
        _t, x, y, w, h, _s = self.samples[-1]
        return x + w / 2, y + h / 2

    def mean_face_w(self) -> float:
        return float(np.mean([s[3] for s in self.samples])) if self.samples else 0.0

    def evidence(self, t: float, window: float = _EVIDENCE_WINDOW_S) -> float:
        """How strongly this track claims to be the subject at time ``t``.

        Combines the three things a detector actually tells us -- confidence,
        size (a closer speaker is a bigger face), and how often the track was
        seen nearby in time -- and deliberately nothing else. Gaze, audio
        activity and speaker identity would all refine this and are not
        available offline without new models; the combination below is what
        separates the two speakers on short04 by a wide margin and is honest
        about what it does not know.
        """
        near = [s for s in self.samples if abs(s[0] - t) <= window]
        if not near:
            return 0.0
        score = float(np.mean([s[5] for s in near]))
        # Root area rather than area: a face 4x larger is a stronger subject
        # claim, but letting size dominate would make the crop chase whoever
        # leaned closest to the camera.
        size = min(1.0, float(np.sqrt(np.mean([s[3] * s[4] for s in near]))) / 200.0)
        # Presence is how many of the samples in the window this track actually
        # won, so a speaker who is there for half the window scores 0.5 and a
        # challenger has to overcome that rather than a clip-wide average that
        # ignores when each person was actually on screen.
        spacing = float(np.mean(np.diff([s[0] for s in near]))) if len(near) > 1 else window
        presence = min(1.0, len(near) * spacing / window)
        return score * size * presence


def _update_tracks(tracks: list[_Track], dets: list[tuple[float, float, float, float, float]],
                   t: float, after_cut: bool) -> None:
    """Bind detections to tracks by nearest centre, creating tracks as needed.

    Mutates ``tracks`` in place. Detections that match nothing start a new
    track; a track that matches nothing simply has no sighting at ``t``, which
    is how temporary occlusion and detection loss are represented — the track
    survives and the crop holds its last known position.
    """
    limit_widths = _MAX_JUMP_AFTER_CUT_WIDTHS if after_cut else _MAX_JUMP_WIDTHS
    for x, y, w, h, _score in dets:
        cx, cy = x + w / 2, y + h / 2
        best, best_d = None, float("inf")
        for tr in tracks:
            prev = tr.center(t) or tr.center(tr.samples[-1][0])
            if prev is None:
                continue
            d = ((prev[0] - cx) ** 2 + (prev[1] - cy) ** 2) ** 0.5
            if d < best_d:
                best, best_d = tr, d
        # The radius is face-relative so a wide shot's small faces get a short
        # leash in pixels and a close-up's large faces a long one, keeping the
        # "same person?" question scale-free.
        radius = max(w, h) * limit_widths
        if best is not None and best_d <= radius:
            best.samples.append((t, x, y, w, h, _score))
        else:
            tracks.append(_Track([(t, x, y, w, h, _score)]))


# --------------------------------------------------------------------------- #
# Layers 3+4: active-subject election and crop optimisation
# --------------------------------------------------------------------------- #

def _elect_centers(tracks: list[_Track], times: list[float]) -> list[float]:
    """Per-sample face centre of the elected subject, or NaN where none.

    A forward pass with hysteresis: the incumbent is held for at least
    ``_MIN_HOLD_S`` and then displaced only by a challenger whose evidence
    exceeds it by ``_SWITCH_MARGIN``. Without this the crop follows whichever
    detection is momentarily larger, and on a two-shot that is a coin flip
    every sample — the A/B/A/B oscillation the brief calls out specifically.
    """
    out: list[float] = []
    incumbent: _Track | None = None
    hold_until = -float("inf")
    for t in times:
        live = [tr for tr in tracks if tr.evidence(t) > 0]
        if not live:
            out.append(float("nan"))
            continue
        if incumbent is None or incumbent.evidence(t) <= 0:
            incumbent = max(live, key=lambda tr: tr.evidence(t))
            hold_until = t + _MIN_HOLD_S
        elif t >= hold_until:
            best = max(live, key=lambda tr: tr.evidence(t))
            if best is not incumbent and \
                    best.evidence(t) > _SWITCH_MARGIN * incumbent.evidence(t):
                incumbent = best
                hold_until = t + _MIN_HOLD_S
        c = incumbent.center(t)
        out.append(c[0] if c else float("nan"))
    return out


def _lead_offset(track: _Track, t: float) -> float:
    """Room in front of a moving subject, in source pixels.

    Derived from the subject's own motion over the preceding samples rather
    than from a gaze model, which is the honest signal available offline: a
    speaker walking right is given room on the right, a stationary speaker is
    centred. Zero when the subject is still or the motion is noise.
    """
    past = [s for s in track.samples if s[0] <= t][-4:]
    if len(past) < 3:
        return 0.0
    xs = np.array([s[0] + s[3] / 2 for s in past], dtype=np.float32)
    ts = np.array([s[0] for s in past], dtype=np.float32)
    velocity = float(np.polyfit(ts, xs, 1)[0])  # px per second
    face_w = float(np.mean([s[3] for s in past]))
    # Only a sustained drift gets lead room; a subject shifting between two
    # stances would otherwise get a room assignment pointing at their last twitch.
    if abs(velocity) * (ts[-1] - ts[0]) < 0.5 * face_w:
        return 0.0
    return float(np.clip(velocity * 0.6, -_LEAD_MAX_FRAC * face_w, _LEAD_MAX_FRAC * face_w))


def _hold_through_gaps(times: list[float], centers: list[float],
                       max_gap_s: float = 5.0) -> list[float]:
    """Carry the last known subject across short detection losses.

    An occluded or momentarily profile-on speaker stops being detected for a
    second or two; dropping straight to the no-face behaviour in that gap makes
    the crop flinch to centre and back. Carrying the position forward, but only
    for a bounded time, treats brief loss as occlusion and long loss as "the
    subject really is gone".
    """
    out = list(centers)
    last_t, last_c = None, None
    for i, (t, c) in enumerate(zip(times, centers)):
        if not np.isnan(c):
            last_t, last_c = t, c
        elif last_c is not None and t - last_t <= max_gap_s:
            out[i] = last_c
    return out


def _crop_targets(times: list[float], centers: list[float], tracks: list[_Track],
                  crop_w: int, src_w: int) -> list[tuple[float, float]]:
    """Turn elected subject centres into crop-centre targets in pixels.

    Target = subject centre + lead room, then constrained two ways: the crop
    must stay inside the source, and the subject's whole face must stay inside
    the crop with margin. The second is what stops a clamp from bisecting a
    speaker who is standing near the edge of a wide shot -- centring the crop
    on such a subject is legal, and wrong.
    """
    out: list[tuple[float, float]] = []
    incumbent = None
    for t, c in zip(times, centers):
        if np.isnan(c):
            continue
        # The incumbent is the track whose sighting brackets t and whose centre
        # matches the elected one; the lead room comes from its own motion.
        for tr in tracks:
            tc = tr.center(t)
            if tc is not None and abs(tc[0] - c) < 2.0:
                incumbent = tr
                break
        target = c + (_lead_offset(incumbent, t) if incumbent is not None else 0.0)
        lo, hi = crop_w / 2.0, src_w - crop_w / 2.0
        if incumbent is not None:
            # The face's own half-width plus margin must fit inside the crop, so
            # near-edge subjects pull the crop toward them rather than being cut.
            fw = incumbent.mean_face_w()
            lo = max(lo, _FACE_MARGIN_PX + fw / 2.0)
            hi = min(hi, src_w - _FACE_MARGIN_PX - fw / 2.0)
            if lo > hi:  # a face wider than the crop: centre it, least bad
                lo = hi = (lo + hi) / 2.0
        out.append((t, float(np.clip(target, lo, hi))))
    return out


def _smooth_elected(times: list[float], centers: list[float],
                    boundaries: set[int], half_window: float = _SMOOTH_HALF_S
                    ) -> list[float]:
    """Robustly low-pass the elected subject position, but never across a cut.

    The subject fidgets, shifts stance, and turns; a camera operator does not
    replicate any of that. Without a low pass the crop inherits the subject's
    own high-frequency motion sample for sample, and at a 406px crop a 2%
    fidget is a visible 68px hop on the delivered 1080-wide frame.

    The reduction is a *median*, not a mean, over the window. A mean of a
    window containing one bad sample still moves a fifth of the way toward it;
    a median drops it entirely. And for a genuinely monotonic traverse the
    median of the window is the centre sample's own value, so it costs no lag
    where the subject really is moving -- the one thing a mean cannot buy.

    The window is short -- one second either side -- because the low pass must
    not cost responsiveness: it is applied to the *elected* subject, so a
    handover already pays a minimum-hold delay, and a genuine traverse must
    still arrive. But one second is only meaningful if the samples are denser
    than that, and they are not: 28 samples over a 30 s clip puts them ~1.2 s
    apart, so a one-second window contains the sample alone and the filter
    becomes a no-op. The window is therefore widened, in *samples*, until it
    spans at least three of them -- the minimum at which a median is a filter
    rather than an identity -- and never narrowed below it. It also stops at
    every shot boundary, since combining across a cut would blend two
    different frames' subjects and smear the reframe into the pan it replaced.
    NaN (subject not elected) breaks the window into pieces rather than pulling
    a real position across a gap.
    """
    spacing = (float(np.median(np.diff(times))) if len(times) > 1 else 1.0)
    half = max(half_window, 2.0 * spacing)
    out = list(centers)
    for i, (t, c) in enumerate(zip(times, centers)):
        if not np.isfinite(c):
            continue
        near = []
        for j in range(len(times)):
            if abs(times[j] - t) > half or not np.isfinite(centers[j]):
                continue
            # A boundary at either end of the pair, or between the two, means
            # different shots. Both endpoints matter: a boundary index starts a
            # new shot, so the sample at it and the one before it are also
            # different shots, and a median that combines them makes the last
            # frame of the old shot jump to the new subject -- a reframe that
            # arrives half a second *before* its cut.
            if i != j and boundaries and any(
                    k in boundaries for k in range(min(i, j), max(i, j) + 1)):
                continue
            near.append(centers[j])
        if near:
            # A window of one or two samples has no middle to take; the mean is
            # the same value, so the branch is not a second filter but a
            # short-input guard.
            out[i] = float(np.median(near) if len(near) > 2 else np.mean(near))
    return out


def _collapse_holds(points: list[tuple[float, float]],
                    eps: float) -> list[tuple[float, float]]:
    """Reduce a run of targets in the same place to its first and last sample.

    Both endpoints are kept, and that is the whole point. The ffmpeg expression
    interpolates linearly between consecutive keypoints, so a run of thirty
    identical targets is only a flat hold if a keypoint still exists at the run's
    *end*: drop that one and the crop starts sliding toward a speaker who has
    not begun talking yet. Re-stamping the held keypoint forward to the run's
    last sample is the mirror error -- it erases the keypoint at the start of the
    *next* run, so a transition that should take half a second is read as a
    twenty-second crawl. A hold needs a keypoint where it begins and one where it
    ends.
    """
    out: list[tuple[float, float]] = []
    i = 0
    while i < len(points):
        j = i
        while (j + 1 < len(points)
               and abs(points[j + 1][1] - points[i][1]) < eps):
            j += 1
        out.append(points[i])
        if j > i:
            out.append(points[j])
        i = j + 1
    return out


def _limit_speed(points: list[tuple[float, float]], src_w: int,
                 settle: list[tuple[float, float]] | None = None
                 ) -> list[tuple[float, float]]:
    """Cap the crop's travel rate, then reduce holds to their endpoints.

    Applied to the *targets*, not to the subject: the subject can move as fast
    as it likes, and the crop simply falls behind and catches up rather than
    replicating the subject's every motion. That is what makes the result read
    as a camera operator with a movement budget rather than as a detector
    dragging a window.

    A shot boundary lifts the cap for a short window afterwards: a cut is
    instantaneous in the source, so the crop re-establishing over two seconds
    reads as a slow drift *across* the cut, which is worse than the snap an
    operator would make. The lifted rate is still finite so a whip pan -- which
    also trips the boundary detector -- reframes quickly instead of jumping.
    """
    if len(points) < 2:
        return list(points)
    out = [points[0]]
    for t, x in points[1:]:
        pt, px = out[-1]
        dt = max(1e-3, t - pt)
        rate = _MAX_SPEED_PER_S
        if settle and any(lo <= t <= hi for lo, hi in settle):
            rate = _MAX_SPEED_AFTER_CUT_PER_S
        max_step = rate * src_w * dt
        out.append((t, float(np.clip(x, px - max_step, px + max_step))))
    return _collapse_holds(out, _TARGET_EPS_PX)


# --------------------------------------------------------------------------- #
# Layer 5: sampling the source, and the ffmpeg trajectory itself
# --------------------------------------------------------------------------- #

@dataclass
class _ReframeCacheKey:
    video: str
    size: int
    mtime: int
    start: float
    end: float
    samples: int

    def __hash__(self) -> int:
        return hash((self.video, self.size, self.mtime, self.start, self.end, self.samples))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _ReframeCacheKey) and self.__dict__ == other.__dict__


_REFAME_CACHE: dict[_ReframeCacheKey, tuple[int, int, list[tuple[float, float]]]] = {}


def _sample_frames(video: Path, start: float, end: float, samples: int
                   ) -> tuple[list[float], list[np.ndarray | None], int, int]:
    """Open the source once and pull evenly spaced frames.

    Seeking by timestamp and then reading back the capture's real position, so
    a container whose keyframes do not cooperate still yields frames from the
    right neighbourhood instead of silently decoding from the last keyframe.

    Dimensions are read from the same open: the crop width is derived from the
    frame size, so a second open for geometry would decode the seek-heavy
    metadata pass twice per vertical clip.
    """
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return [], [], 0, 0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    out_t: list[float] = []
    out_f: list[np.ndarray | None] = []
    for t_abs in np.linspace(start, end, max(2, samples)):
        cap.set(cv2.CAP_PROP_POS_MSEC, float(t_abs * 1000.0))
        ok, frame = cap.read()
        out_t.append(float(t_abs - start))
        out_f.append(frame if ok else None)
    cap.release()
    return out_t, out_f, w, h


def plan_crop(times: list[float],
              detections: list[list[tuple[float, float, float, float, float]]],
              boundaries: set[int], w: int, h: int, crop_w: int
              ) -> list[tuple[float, float]]:
    """Layers 2-5 over a supplied detection stream: tracking -> election ->
    crop targets -> speed-limited trajectory.

    Exists apart from ``_reframe_track`` so the editorial behaviour is provable
    without a video file: the mandated regressions (two speakers, a cut, an
    occlusion, a false detection) are synthetic *detection streams*, which keeps
    them deterministic and keeps them from silently passing because a detector
    happened not to see the synthetic marker. The perception layer has its own
    tests against real footage; conflating the two is how a broken election
    hides behind a working detector.

    ``detections[i]`` are ``(x, y, w, h, score)`` boxes in source pixels for
    ``times[i]``; ``boundaries`` holds indices that start a new shot. Returns
    ``(t, centre_fraction)`` keypoints.
    """
    tracks: list[_Track] = []
    for i, t in enumerate(times):
        _update_tracks(tracks, detections[i], t, after_cut=(i in boundaries))
    elected = _elect_centers(tracks, times)
    held = _hold_through_gaps(times, elected)
    # The low pass goes after the hold and before the targets: it smooths the
    # elected subject position, and the target layer must still clamp the face
    # into the crop window from the un-smoothed geometry it can look up in
    # ``tracks``.
    held = _smooth_elected(times, held, boundaries)
    targets = _crop_targets(times, held, tracks, crop_w, w)
    settle = [(times[i], times[i] + _SETTLE_S)
              for i in sorted(boundaries) if i < len(times)]
    targets = _limit_speed(targets, w, settle)

    pts: list[tuple[float, float]] = []
    for t, x in targets:
        frac = float(np.clip(x / w, 0.0, 1.0))
        pts.append((t, frac))
    # Converting to fractions can merge targets that were distinct in pixels --
    # anything pushed against the 0 or 1 clamp -- so the holds are collapsed
    # again here, on fractions. See _collapse_holds for why a run needs both
    # its endpoints to remain a flat hold.
    return _collapse_holds(pts, 1e-4)


def _reframe_track(video: Path, start: float, end: float, samples: int = 28
                   ) -> tuple[int, int, list[tuple[float, float]]]:
    """The full perception->tracking->election->crop->trajectory pass.

    Returns source dimensions and the ``(t, centre)`` keypoints the ffmpeg
    expression is built from, in fractions of the source width. Falls back to
    the edge-density heuristic when no detector is available, so a host with no
    model file still reframes rather than failing the render.

    The same fallback runs when a detector *is* available but found no face in
    the whole interval: a landscape shot, an animated title, or a synthetic
    clip. Producing zero keypoints there would silently leave the render on a
    static centre, which is worse than the heuristic -- the heuristic at least
    tracks a subject's position. Detections being empty and detections being
    impossible are the same problem from the crop's point of view, so they share
    one path.

    Known limitation -- slides. The crop is subject-centred, and there is no
    notion in this pass of a second region of interest such as a slide or a
    chart. For the common lecture layout (speaker left, slides right, each in
    its own half of a 1280-wide frame) the window is simply too narrow to hold
    both, and this is geometry rather than tuning: the crop at 720-tall source
    is 406 px, the speaker and the slide are ~560 px apart centre-to-centre, and
    enumerating every legal crop centre on such a frame found *zero* positions
    that keep the speaker's face and at least half the slide's text in frame at
    once. The pass does not know it is choosing between them, and what it picks
    is whichever side carries more structure -- the face detector when one is
    found, otherwise the edge-dense half, which for dense slide text is the
    slide and not the speaker. So on such a clip the deliverable can ship with
    the speaker cropped out, the slide half-cut, or both, and nothing here
    reports it. Extending the election to a second content class (text/slide
    detection) and a two-region objective is the real fix; that is a perception
    change, not a parameter one, and it is deliberately not papered over with a
    width or a bias here -- a wider crop would break the 9:16 deliverable, and a
    bias toward one side would be a guess that is wrong as often as it is right.
    """
    key = _ReframeCacheKey(str(video), video.stat().st_size, video.stat().st_mtime_ns,
                           round(start, 3), round(end, 3), samples)
    hit = _REFAME_CACHE.get(key)
    if hit is not None:
        return hit

    times, frames, w, h = _sample_frames(video, start, end, samples)
    if not times:
        result = (max(w, 1), max(h, 1), [])
        _REFAME_CACHE[key] = result
        return result

    detector = get_detector()
    per_frame: list[list[tuple[float, float, float, float, float]]] = []
    if detector.available:
        for frame in frames:
            per_frame.append(detector.detect(frame) if frame is not None else [])
    if not per_frame or not any(per_frame):
        # No usable detections anywhere: the heuristic path, on either host state.
        centers: list[tuple[float, float]] = []
        for t, frame in zip(times, frames):
            if frame is None:
                continue
            centers.append((t, _fallback_center(frame)))
        pts = _smooth_centers(centers)
        result = (w, h, [(t, c) for t, c in pts])
        _REFAME_CACHE[key] = result
        return result

    # The crop width the render will actually use, so the election optimises
    # the geometry that gets delivered rather than an abstract subject score.
    sar_w, sar_h = probe_sample_aspect(video)
    crop_w = min(vertical_crop_width(h, sar_w, sar_h), w)

    boundaries = set(find_shot_boundaries(frames))
    # Frames that failed to decode contribute neither detections nor boundaries;
    # they are dropped here rather than threaded through as empty detections,
    # which would let a track time its evidence against a frame that has none.
    kept = [(i, t, frame) for i, (t, frame) in enumerate(zip(times, frames))
            if frame is not None]
    pts = plan_crop([t for _, t, _ in kept],
                    [per_frame[i] for i, _, _ in kept],
                    {j for j, (i, _, _) in enumerate(kept) if i in boundaries},
                    w, h, crop_w)

    result = (w, h, pts)
    _REFAME_CACHE[key] = result
    return result


def _probe_and_track(
    video: Path, start: float, end: float, samples: int = 28
) -> tuple[int, int, list[tuple[float, float]]]:
    """Open the source once, read its dimensions and track the subject.

    Kept as the single entry point for dimensions and track together: the crop
    width depends on the frame size and the subject is centred against that same
    width, so doing them in two ``VideoCapture`` opens not only decoded the
    seek-heavy metadata pass twice per vertical clip but left two places to get
    one geometry wrong.
    """
    return _reframe_track(video, start, end, samples)


def track_subject_center(video: Path, start: float, end: float, samples: int = 28) -> list[tuple[float, float]]:
    _, _, centers = _probe_and_track(video, start, end, samples)
    return centers


def vertical_crop_width(height: int, sar_w: int, sar_h: int) -> int:
    """Pixel width of a 9:16 crop of ``height`` pixels at this sample aspect.

    Display width is ``w * sar_w`` and display height ``h * sar_h`` in the same
    units, so the pixel width that *displays* as 9:16 is
    ``h * 9/16 * sar_h / sar_w`` -- wider in pixels than the square-pixel
    answer when the pixels themselves are wide. Rounding to even keeps the
    scaler and libx264 happy.

    The rounding is NOT where the delivered ratio is settled: an integer pixel
    grid cannot hold 9:16 exactly (2160 px is 1215 wide at 9:16, which has to
    become the even 1216, and 1216x2160 is 0.08% off). That residue is absorbed
    later by :func:`vertical_scale_filter`, which scales the crop onto a frame
    whose *display* ratio is 9:16 by construction, so the crop staying a
    fraction wide does not reach the deliverable.
    """
    width = height * 9 * sar_h / (16 * sar_w)
    return max(2, int(width / 2 + 0.5) * 2)


def _x_expression(src_w: int, crop_w: float,
                  points: list[tuple[float, float]]) -> str:
    """Centre the tracked subject in a ``crop_w``-wide window, keyed to time.

    The keypoint centres are fractions of the *source width*, so the window's
    left edge is ``src_w * c - crop_w / 2`` -- not ``(src_w - crop_w) * c - crop_w / 2``.
    The two differ by ``crop_w * c``, which is a whole crop width for a subject
    at the far side of a wide shot: the window lands on empty frame and the
    delivered output loses the speaker while every consumer that scores the
    trajectory (the benchmark, the regression tests) still sees him centred.
    The clamp to ``[0, max_x]`` is what makes a subject at the edge of the
    source legal at all.

    With no track the window sits at the middle of the available range. The
    expression interpolates between sampled centres, so it is built from the
    last sample backwards: each earlier sample becomes an ``if(lt(t,...))``
    that hands off to the segment after it.
    """
    max_x = max(0.0, float(src_w) - float(crop_w))
    if not points:
        return str(int(max_x / 2))

    def _left(frac: float) -> float:
        return max(0.0, min(max_x, float(src_w) * frac - float(crop_w) / 2.0))

    expr = str(_left(points[-1][1]))
    for i in range(len(points) - 2, -1, -1):
        t0, c0 = points[i]
        t1, c1 = points[i + 1]
        x0 = _left(c0)
        x1 = _left(c1)
        expr = f"if(lt(t,{t1:.3f}),{x0:.1f}+({x1:.1f}-{x0:.1f})*(t-{t0:.3f})/{max(0.001,t1-t0):.3f},{expr})"
    return expr


def _probe_dims(video: Path) -> tuple[int, int]:
    """Pixel dimensions, or the 1080p default when ffprobe says nothing."""
    w, h, _points = _probe_and_track(video, 0.0, 0.0)
    if not w or not h:
        return 1920, 1080
    return w, h


def _even(n: float) -> int:
    return max(2, int(n / 2 + 0.5) * 2)


def landscape_filter(video: Path, target: tuple[int, int] = (16, 9),
                     max_width: int = 1920) -> str:
    """The ffmpeg filter that fits a whole source into an exact ``target``
    display aspect, padding rather than cropping.

    Why pad: a 16:9 deliverable made from a 4:3 source has to lose content or
    add bars, and cropping is an editorial decision the pipeline is not
    entitled to make unsupervised -- on a talking-head source it takes heads
    off. Padding keeps every frame the source recorded and still delivers a
    frame that measures exactly 16:9, which is the contract the render
    promised. A wider-than-target source is letterboxed by the same rule.

    The content is scaled to fit and never enlarged: a 720x486 NTSC source
    becomes a 640x486 square-pixel picture inside an 864x486 frame, not an
    upscaled 1080p one. Square pixels are forced (``setsar=1``) so the aspect
    does not depend on the player honouring container metadata.
    """
    w, h = _probe_dims(video)
    sar_w, sar_h = probe_sample_aspect(video)
    tw, th = target

    # The largest frame of this aspect whose height does not exceed the
    # source's, capped at the delivery width. Height is derived from the
    # rounded width so the two are exactly proportional.
    frame_w = _even(min(h * tw / th, max_width))
    frame_h = _even(frame_w * th / tw)

    # Square-pixel extent of the source picture at its own pixel height.
    content_w = w * sar_w / sar_h
    scale = min(frame_w / content_w, frame_h / h, 1.0)
    cw, ch = _even(content_w * scale), _even(h * scale)

    return (
        f"scale={cw}:{ch}:flags=lanczos,"
        f"pad={frame_w}:{frame_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    )


def vertical_crop_filter(video: Path, start: float, end: float) -> str:
    """The ffmpeg ``crop`` filter for the 9:16 reframe of one interval.

    Returns the whole filter, width and tracked x together, because the two
    must agree: the width has to account for the source's sample aspect ratio
    while the subject is centred against that same width, and recomposing it
    at the call site leaves two places to get one geometry wrong. The width is
    clamped to the source width so a source already narrower than 9:16 is
    passed through at its own ratio instead of failing the filter.
    """
    w, h, points = _probe_and_track(video, start, end)
    if not w or not h:
        w, h = 1920, 1080
    sar_w, sar_h = probe_sample_aspect(video)
    crop_w = min(vertical_crop_width(h, sar_w, sar_h), w)
    return f"crop={crop_w}:{h}:x='{_x_expression(w, crop_w, points)}':y=0"


def vertical_scale_filter(crop_w: int, sar_w: int, sar_h: int,
                          max_width: int = 1080) -> str:
    """The ffmpeg ``scale`` that lands the 9:16 crop on a frame measuring 9:16.

    The defect this replaces: the crop is cut at the pixel width that *displays*
    as 9:16, but the integer grid forces it a fraction wide (2160 px is 1215 at
    9:16 and has to round to the even 1216). Scaling that crop by width with an
    even height — ``scale=min(1080,iw):-2`` — preserves the fraction instead of
    correcting it, and the residue is amplified by the scale: a 1216x2160 crop
    became a **1080x1918** deliverable, not 1080x1920. Found by rendering a
    3840x2160 source and measuring the short; it is off at every source height
    (a 1280x720 source ships 406x720, which is 0.25% from 9:16) and QA did not
    ring because its display-aspect tolerance is 1%.

    Deriving the height explicitly instead — as ``landscape_filter`` already
    does for 16:9 — puts the residue into a sub-0.1% squeeze the eye cannot see
    and delivers a frame that measures exactly 9:16, which is the contract the
    render advertises.

    The height is derived in *display* space through the source's sample aspect:
    a 9:16 pixel count is only 9:16 for square pixels, so an anamorphic source's
    deliverable keeps its SAR and the math accounts for it. Never enlarge: the
    width is capped at the crop's own width, not just at 1080.
    """
    frame_w = _even(min(crop_w, max_width))
    frame_h = _even(frame_w * sar_w * 16 / (9 * sar_h))
    return f"scale={frame_w}:{frame_h}:flags=lanczos"


def vertical_reframe_filter(video: Path, start: float, end: float) -> str:
    """The complete 9:16 reframe for one interval: crop, then the scale that
    lands it on an exact frame.

    Owns both stages for the same reason ``vertical_crop_filter`` owns the crop
    width and the tracked x together: the scale's target height is a function
    of the crop width and the source's sample aspect, and recomposing it at the
    call site is a second place to get one geometry wrong. Callers that need to
    compose their own chain (the benchmark, scoring an old trajectory against a
    new one) still have ``vertical_crop_filter`` and ``crop_x_expression``.
    """
    w, h, points = _probe_and_track(video, start, end)
    if not w or not h:
        w, h = 1920, 1080
    sar_w, sar_h = probe_sample_aspect(video)
    crop_w = min(vertical_crop_width(h, sar_w, sar_h), w)
    return (
        f"crop={crop_w}:{h}:x='{_x_expression(w, crop_w, points)}':y=0,"
        f"{vertical_scale_filter(crop_w, sar_w, sar_h)}"
    )


def crop_x_expression(video: Path, start: float, end: float) -> str:
    """The ``x`` component alone, for callers that compose their own filter."""
    w, h, points = _probe_and_track(video, start, end)
    if not w or not h:
        w, h = 1920, 1080
    sar_w, sar_h = probe_sample_aspect(video)
    crop_w = min(vertical_crop_width(h, sar_w, sar_h), w)
    return _x_expression(w, crop_w, points)
