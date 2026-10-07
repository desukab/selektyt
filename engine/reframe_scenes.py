"""Scene-aware planning for coherent, single-crop vertical reframing.

``engine.reframe`` reframes a *subject*: it tracks a face, elects one, and keeps
it inside a full-height 9:16 window. That is the whole story for a talking head,
and it is provably incomplete for a lecture, where the frame holds a speaker and
a slide at the same time and the 9:16 window is too narrow for both. This module
perceives the second content class and records when both regions cannot fit a
single crop. The delivered frame remains vertical, but a SPLIT scene may deliberately compose two source regions so neither the speaker nor the content is silently discarded.

Four scene classes
------------------

``SPEAKER``  a face, no slide (or a slide that fits beside it). The ordinary
             tracked crop from ``engine.reframe``.

``SLIDE``    content and no speaker -- a screen share, a title card, a chart
             filling the frame. The crop is centred on the content region
             instead of on a face that is not there.

``SPLIT``    the detected speaker and content cannot both fit one legal 9:16
             crop. The renderer turns that classification into an explicit
             speaker+content two-band composition instead of silently discarding
             one region.

``TRANSITION``  not a layout -- the fade the renderer inserts between two
             segments of differing classes, instead of a hard switch. See
             :func:`transition_points` and :func:`scene_filter`, which builds
             the ``xfade`` cascade.

The decision that matters
-------------------------

Whether a speaker and a slide fit in one crop is *geometry*, not taste, and it
is settled exactly rather than heuristically. The crop is full-height, so the
only freedom is horizontal, and the constraint set is two intervals:

* the crop's left edge ``X`` must hold the face with a margin on both sides,
  giving ``face.x + face.w + margin - crop_w <= X <= face.x - margin``;
* at least half the slide's width must survive, which for a slide ``[a, b]``
  and a crop of width ``L`` is exactly
  ``a + need - L <= X <= b - need`` with ``need = 0.5 * slide.w``.

Both are intervals in ``X``, so the joint constraint is their intersection, and
"both fit" is "the intersection is non-empty". That is a closed form with no
search and no tuning, and it is the fact the previous pass was missing -- it
could choose a crop that serves neither region and had no way to notice.

Measured provenance of every threshold
---------------------------------------

The detector was built and calibrated against a composite source: the real
pentagon briefing speaker with a generated slide plate composited at a known
rectangle, so the ground truth is exact and the signal is real footage rather
than a synthetic stand-in. On that source (1280x720, plate 480x470 at x=740):

* slide cells read temporal-staticness 0.84-1.00; the rest of the frame reads
  p50 0.18 and p90 0.75, so ``_STATIC_MIN = 0.80`` keeps every slide cell and
  drops ~90% of the static background;
* edge density alone is *not* a slide signal -- the plate's blank white interior
  reads 0.00 Canny, so any product with edge density would silently lose most of
  a real slide. Brightness separates the plate (0.27-0.95) from the briefing's
  own dark backdrop, and *either* structure *or* brightness admits a cell;
  the conjunction ``static>=0.8 & (edge>=0.02 | bright>=0.35)`` measures
  precision 0.98 / recall 1.00 against ground truth;
* at component level the predicate yields a single connected region covering
  210 of 214 ground-truth cells, and the component is 23.7% of the frame area,
  so ``_MIN_SLIDE_AREA_FRAC = 0.06`` is a ~4x margin below the real thing.

Real footage does exercise this module, and it is where the classifier was
corrected. The two-angle Royal Society interview (``royal-society-cloke.webm``,
1920x1080 VP9) alternates between a tight head shot and a wide shot that holds a
speaker beside a large static region; the wide shot's face is 52-66 px wide and
its static region covers up to 98% of the frame. On the four real cut intervals
of that run the composition now keeps the speaker on 26 of 26 samples where the
pre-composition reframe kept it on 25 of 26 (``tools/reframe_benchmark.py``
scoring, YuNet oracle). That measurement caught a rule the geometry was already
able to replace: an earlier version classified on the slide's size alone and
returned ``SLIDE`` for any region covering 80% of the frame before it looked at
the face, which cropped to the slide's centre and dropped the speaker on 6 of
those 26 samples. See :func:`classify_scene`.

What is still not exercised on real footage: no corpus clip is a lecture whose
slide is captured alongside a *foreground* speaker at lecture scale, and the AV1
tutorial this corpus contains cannot be decoded through OpenCV at all, so the
slide detector's precision on that codec is unmeasured here. Those gaps are
recorded in the state document as known limitations rather than papered over.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

import cv2
import numpy as np

from engine.reframe import (
    _even,
    _perceive_window,
    _x_expression,
    plan_crop,
    vertical_crop_width,
)
from providers.ffmpeg import probe_sample_aspect


class UnreadableSource(RuntimeError):
    """The source yielded no decodable frame at all.

    Distinct from "no subject detected", which is a composition outcome the
    planner handles with a centred crop. This is an ingest failure: neither the
    OpenCV reader nor its ffmpeg fallback decoded a frame, so there is no
    geometry to compose and no filter to build. Raised rather than returned as
    a degenerate ``crop=1:0`` filter, which ffmpeg rejects while configuring the
    crop pad (exit 234, a zero-byte output file) and reports to the editor as an
    unexplained failure that points at the render rather than the ingest.
    """


# ---------------------------------------------------------------------------
# Perception: slides
# --------------------------------------------------------------------------- #

CELL = 32  # analysis cell, in source pixels. Coarse enough to average out
# letterform jitter over a whole word and fine enough that a 480x470 plate
# resolves to 15x14 cells rather than a handful.

_MOTION_FULL = 10.0
# A cell whose per-pixel temporal std reaches this is fully "moving". 10 is the
# value the composite scan was built with, and on it the plate reads ~0.97 and
# the speaker's head and hands read ~0.0; the constant lives here so the scale
# of _STATIC_MIN below is defined.

_STATIC_MIN = 0.80  # see the module docstring's measured table
_EDGE_MIN = 0.02
_BRIGHT_MIN = 0.35
_MIN_SLIDE_AREA_FRAC = 0.06  # of the frame area


@dataclass(frozen=True)
class SlideRect:
    """A detected content region in source pixels."""

    x: float
    y: float
    w: float
    h: float

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def cx(self) -> float:
        return self.x + 0.5 * self.w


@dataclass(frozen=True)
class FaceBox:
    """A speaker box in source pixels, kept distinct from a raw detection so the
    margin semantics live in one place."""

    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + 0.5 * self.w


def _cell_maps(frames: list[np.ndarray], w: int, h: int
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-cell staticness, edge density and brightness over the sample set.

    The three maps are computed on frames resampled to a multiple of the cell
    grid rather than on the source frames: the temporal std needs every sample
    at the *same* pixel grid, and INTER_AREA over an integer multiple is an
    exact box average, so the cells are averages of whole source pixels and not
    of a resampled approximation.
    """
    gw, gh = max(1, w // CELL), max(1, h // CELL)
    small = [cv2.resize(f, (gw * 8, gh * 8), interpolation=cv2.INTER_AREA)
             for f in frames if f is not None]
    if len(small) < 4:
        return np.zeros((gh, gw)), np.zeros((gh, gw)), np.zeros((gh, gw))
    stack = np.stack([s.astype(np.float32) for s in small])
    tstd = stack.std(axis=0).mean(axis=2)
    static = np.clip(1.0 - tstd / _MOTION_FULL, 0.0, 1.0)
    edge = np.mean([cv2.Canny(cv2.cvtColor(s, cv2.COLOR_BGR2GRAY), 60, 160)
                    .astype(np.float32) / 255.0 for s in small], axis=0)
    lum = np.mean(stack, axis=(0, 3)) / 255.0

    def blockify(m: np.ndarray) -> np.ndarray:
        ch, cw = m.shape[0] // gh, m.shape[1] // gw
        return m[: gh * ch, : gw * cw].reshape(gh, ch, gw, cw).mean(axis=(1, 3))

    return blockify(static), blockify(edge), blockify(lum)


def detect_slides(frames: list[np.ndarray | None], w: int, h: int
                  ) -> list[SlideRect]:
    """Content regions, largest first, or an empty list when there are none.

    A slide is *static over time*, *structured or bright*, and *large*. The
    temporal term is the discriminator and the other two are guards: static
    alone also matches a studio's locked-off backdrop, and structure alone
    matches a moving speaker's clothes, so neither suffices on its own.

    The result is a rectangle per surviving connected component rather than a
    per-cell mask, because the consumer needs a region to frame or compose, and
    the component bbox is the smallest rectangle the evidence actually supports.
    """
    good = [f for f in frames if f is not None]
    if len(good) < 4:
        return []
    static, edge, bright = _cell_maps(good, w, h)
    mask = ((static >= _STATIC_MIN)
            & ((edge >= _EDGE_MIN) | (bright >= _BRIGHT_MIN)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if n <= 1:
        return []
    frame_area = float(w * h)
    out: list[SlideRect] = []
    for i in range(1, n):
        x, y, cw, ch, cells = stats[i]
        area = float(cw) * CELL * float(ch) * CELL
        if area < _MIN_SLIDE_AREA_FRAC * frame_area:
            continue
        # Snap the component back to the source grid. Cells are CELL px, so the
        # rectangle is exact at the grid's resolution; clamping keeps a region
        # that runs to the frame edge from being reported as hanging outside it.
        x0 = min(float(x) * CELL, float(w) - 1.0)
        y0 = min(float(y) * CELL, float(h) - 1.0)
        x1 = min(float(x + cw) * CELL, float(w))
        y1 = min(float(y + ch) * CELL, float(h))
        rect = SlideRect(x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))
        # Drop a component that is a single thin strut of cells: a bright
        # horizontal band (a news ticker, a desk edge) is static and bright and
        # passes every test above, but it is not something to frame on its own.
        if rect.w / rect.h > 6.0 or rect.h / rect.w > 6.0:
            continue
        out.append(rect)
    out.sort(key=lambda r: r.area, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Geometry: can one crop hold both?
# --------------------------------------------------------------------------- #

_FACE_MARGIN = 0.35
# Fraction of the face's own width added on each side as breathing room. The
# value is the same one the subject-election regressions grade against
# (``MARGIN_FRACTION_OK`` in ``tests/test_reframe_regression.py``), so a "fits"
# verdict here means the same framing the regression suite already accepts.

_SLIDE_MIN_WIDTH_FRAC = 0.50
# The least amount of a slide that must survive the crop for it to count as
# "in frame". Half the text is the point at which a slide stops being legible
# and starts being a decorative band down the edge of a vertical clip.


def face_crop_window(face: FaceBox, crop_w: float, src_w: float
                     ) -> tuple[float, float]:
    """The closed interval of legal crop left-edges for this face.

    ``[lo, hi]``; ``lo > hi`` means no crop of this width can hold the face with
    the margin, which is a real outcome for a face wider than the crop and is
    reported rather than clamped away.
    """
    margin = face.w * _FACE_MARGIN
    lo = (face.x + face.w + margin) - crop_w
    hi = face.x - margin
    lo = max(0.0, lo)
    hi = min(float(src_w) - float(crop_w), hi)
    return lo, hi


def _slide_crop_window(slide: SlideRect, crop_w: float
                       ) -> tuple[float, float]:
    """The interval of crop left-edges keeping ``_SLIDE_MIN_WIDTH_FRAC`` of the
    slide in frame.

    The overlap of ``[X, X + L]`` with ``[a, b]`` is at least ``need`` iff
    ``a + need - L <= X <= b - need`` (given ``need <= b - a``). That identity
    is what turns "keep half the slide" from a search into an interval, and it
    is exact -- verified against an explicit endpoint scan before being kept.
    """
    a, b, L = slide.x, slide.x + slide.w, float(crop_w)
    need = slide.w * _SLIDE_MIN_WIDTH_FRAC
    if need > (b - a):
        return 1.0, 0.0
    return a + need - L, b - need


def joint_crop_x(face: FaceBox, slide: SlideRect, crop_w: float, src_w: float
                 ) -> float | None:
    """The crop left-edge that holds the face *and* the slide, or ``None``.

    When such an edge exists this returns the one nearest to centring the face:
    the face stays the primary subject, and the slide is what the crop concedes
    to. When it does not, ``None`` is the answer; the caller records a SPLIT
    classification and keeps the speaker as the vertical crop's subject.
    """
    flo, fhi = face_crop_window(face, crop_w, src_w)
    slo, shi = _slide_crop_window(slide, crop_w)
    lo, hi = max(flo, slo), min(fhi, shi)
    if lo > hi:
        return None
    ideal = face.cx - 0.5 * float(crop_w)
    return float(min(max(ideal, lo), hi))


def both_fit_in_crop(face: FaceBox, slide: SlideRect, crop_w: float,
                     src_w: float) -> bool:
    """Whether one legal 9:16 crop can carry the speaker and the slide."""
    return joint_crop_x(face, slide, crop_w, src_w) is not None


# ---------------------------------------------------------------------------
# Classification over time
# --------------------------------------------------------------------------- #


class SceneClass(Enum):
    SPEAKER = "speaker"
    SLIDE = "slide"
    SPLIT = "split"


@dataclass(frozen=True)
class SceneSegment:
    """One stretch of one scene class, on the segment's own timeline.

    ``start``/``end`` are seconds from the start of the analysed interval, which
    is the timeline ffmpeg's filtergraph sees when the render seeks into the
    source: the same convention as ``plan_crop``'s keypoints.

    ``slide`` is the content region the decision was made from, carried through
    so the layout does not have to re-detect it, and so a reader can answer
    *why* a clip was composed the way it was.

    ``joint`` marks a ``SPEAKER`` segment where a single crop holds the slide as
    well: the layout keeps its face tracking but clamps the crop to the joint
    window, so the content is not cut while the speaker moves.
    """

    start: float
    end: float
    cls: SceneClass
    slide: SlideRect | None = None
    joint: bool = False
    # The representative face that caused the SPLIT classification. Keeping it
    # with the segment lets the renderer compose the speaker and content rather
    # than throwing one side away.
    face: FaceBox | None = None


def classify_scene(face: FaceBox | None, slide: SlideRect | None,
                   crop_w: float, src_w: float, src_h: float) -> SceneClass:
    """The scene class for one sample.

    Pure on its inputs, so the mandated regressions (a slide appearing mid-clip,
    a speaker leaving, a slide too large to compose beside anything) are
    synthetic streams rather than a guess at what a detector would see.

    The speaker is consulted before the slide's size, and deliberately. A slide
    that fills the frame can still have a speaker standing in front of it, and a
    rule that classifies on the slide alone drops that speaker from the layout
    decision. That was measured, not imagined: on a real corpus cut the shot
    widened to a speaker whose face was 52 px wide in a 1920 px frame beside a
    static region covering 98% of the frame. The slide-only rule called it a
    SLIDE, the layout cropped to the slide's own centre, and the speaker left the
    deliverable on 6 of 26 samples while the pre-composition reframe kept it on
    25 of 26. The geometry below records whether the speaker and content can
    share one crop. If they cannot, the vertical render still prioritizes the
    speaker crop rather than composing separate source regions.
    """
    if face is not None and slide is not None:
        if both_fit_in_crop(face, slide, crop_w, src_w):
            return SceneClass.SPEAKER
        return SceneClass.SPLIT
    if slide is not None:
        return SceneClass.SLIDE
    return SceneClass.SPEAKER


_MIN_HOLD_S = 2.0
# A class must hold for this long before it can switch. Below this, an
# oscillation between classes is a detector artefact -- the speaker steps out of
# frame for a second, or a slide fades in -- and the plan should ride through it
# rather than cut the layout twice in two seconds.

_SNAP_TO_SHOT_S = 0.75
# When a class switch falls within this of a shot boundary, the switch moves to
# the boundary. A layout change on a hard cut reads as an edit; the same change
# a frame later reads as a mistake.


def _runs(classes: list[SceneClass]) -> list[list[int]]:
    """Maximal ``[first, last]`` index runs of one class."""
    out: list[list[int]] = []
    for i, cls in enumerate(classes):
        if out and classes[out[-1][0]] is cls:
            out[-1][1] = i
        else:
            out.append([i, i])
    return out


def plan_scenes(times: list[float], classes: list[SceneClass],
                boundaries: set[int] | None = None
                ) -> list[SceneSegment]:
    """Turn a per-sample classification into held, shot-aligned segments.

    Short runs are absorbed into their longer neighbour rather than dropped:
    a half-second slide blip inside a speaker segment is evidence the slide is
    there, but not enough to re-lay the whole clip for, and the neighbour's
    class is the one that already describes the surrounding seconds.

    The returned segments are *contiguous and cover the whole sampled span*:
    each hands off to the next at a single cut time, with no hole and no
    overlap. That is a contract the render depends on rather than a convenience
    -- the composed graph trims every segment to its own window and crossfades
    the overlap, so a gap would be dropped from the video while the audio,
    mapped over the interval unchanged, played on for the missing second. The
    desync would not surface as an error anywhere; the deliverable would simply
    drift from its own EDL by the width of the gap.
    """
    if not times:
        return []
    boundaries = boundaries or set()
    cls = list(classes)
    while True:
        runs = _runs(cls)
        if len(runs) <= 1:
            break
        # The shortest run is the one least likely to be a real change of scene.
        k = min(range(len(runs)),
                key=lambda j: times[runs[j][1]] - times[runs[j][0]])
        r = runs[k]
        if times[r[1]] - times[r[0]] >= _MIN_HOLD_S:
            break
        left = runs[k - 1] if k > 0 else None
        right = runs[k + 1] if k + 1 < len(runs) else None
        if left is None:
            target = right
        elif right is None or (times[left[1]] - times[left[0]]
              >= (times[right[1]] - times[right[0]])):
            target = left
        else:
            target = right
        adopted = cls[target[0]]
        for i in range(r[0], r[1] + 1):
            cls[i] = adopted

    runs = _runs(cls)
    if len(runs) == 1:
        # The whole interval is one scene. Reported as [times[0], times[-1]]
        # rather than as a degenerate single-point span, so a caller that lays
        # out from segment bounds covers the clip it asked about.
        return [SceneSegment(times[0], times[-1], cls[runs[0][0]])]

    # Where one class hands off to the next is *between* two samples: the last
    # sample of the outgoing run and the first of the incoming one each assert
    # their own class, and neither is the switch. The midpoint is the honest
    # place for it, and a shot boundary within reach is a better one -- a layout
    # change lands on a hard cut and reads as an edit, where the same change a
    # frame later reads as a mistake. Snapping the *time* rather than moving the
    # run indices keeps the segments contiguous by construction, which moving
    # the indices did not (it left a one-sample hole at every switch).
    cuts: list[float] = []
    for k in range(1, len(runs)):
        prev_end, next_start = runs[k - 1][1], runs[k][0]
        t = 0.5 * (times[prev_end] + times[next_start])
        cands = [times[i] for i in boundaries
                 if 0 < i < len(times) and abs(times[i] - t) <= _SNAP_TO_SHOT_S]
        if cands:
            t = min(cands, key=lambda x: abs(x - t))
        cuts.append(t)

    # Two consecutive cuts cannot cross. Every run still in the list outlasts
    # _MIN_HOLD_S (that is the loop above's exit condition once it has more than
    # one run to merge), so adjacent midpoints are at least that far apart and a
    # snap of at most _SNAP_TO_SHOT_S on each cannot reach the other one. Stated
    # here because the contiguity the render depends on follows from it, and a
    # silent clamp would turn a real regression into a zero-length segment.
    for prev, cur in zip(cuts, cuts[1:]):
        assert prev < cur, "scene-class cuts are not monotonic"

    segments: list[SceneSegment] = [SceneSegment(times[0], cuts[0],
                                                 cls[runs[0][0]])]
    for k in range(1, len(runs)):
        end = cuts[k] if k + 1 < len(runs) else times[-1]
        segments.append(SceneSegment(cuts[k - 1], end, cls[runs[k][0]]))
    return segments


def transition_points(segments: list[SceneSegment]
                      ) -> list[tuple[float, SceneClass, SceneClass]]:
    """Where the layout changes, and between which classes.

    Empty for a single-class clip, which is the common case and which renders
    with the ordinary tracked crop. Non-empty is what the render turns into an
    ``xfade`` cascade, so a change of scene class is a dissolve and not a cut
    inside a single delivered clip.
    """
    out: list[tuple[float, SceneClass, SceneClass]] = []
    for prev, cur in zip(segments, segments[1:]):
        if prev.cls is not cur.cls:
            out.append((cur.start, prev.cls, cur.cls))
    return out


# ---------------------------------------------------------------------------
# Layout: the ffmpeg chains
# --------------------------------------------------------------------------- #

_TRANSITION_S = 0.5
# Crossfade duration between two scene classes. Short enough to sit on a cut
# without dragging, long enough that a layout change is not a flash. Every
# segment must outlast twice this, which _MIN_HOLD_S guarantees.


def _band_geometry(src_w: int, src_h: int,
                   out_w: int, out_h: int, box: tuple[float, float, float, float]
                   ) -> tuple[int, int, float, float]:
    """The source window to crop for a band, as ``(w, h, x, y)``.

    The window has the band's *pixel* aspect exactly, so the scale onto the band
    is a pure resize with no squeeze; and it is the *smallest* such window
    containing ``box``, so nothing the editor needs is cropped to fit the shape.

    Both properties are worth stating because the obvious alternative -- crop
    the box and then scale it to the band's shape -- distorts it, and the other
    obvious one -- crop a fixed window -- cuts content.

    One consequence is easy to get backwards and is worth spelling out: the
    delivered band keeps the source's SAR (see :func:`_set_sar`), so the SAR
    appears on both sides of the equality and cancels. The no-squeeze condition
    is plain ``sw/sh == out_w/out_h`` -- pixel ratios, with no aspect factor.
    Carrying the factor anyway gives the shape the window would need if the band
    were *square-pixel*, which it is not; measured on a 720x486 SAR 8:9 source
    that squeezes the content about 11% sideways and a square in the frame comes
    out a tall rectangle (``tests/test_reframe.py``,
    ``test_an_anamorphic_slide_is_not_squeezed``).
    """
    bx, by, bw, bh = box
    r = out_w / out_h  # required source window aspect; the SAR cancels (above)
    sw = max(float(bw), float(bh) * r)
    sh = sw / r
    if sw > src_w or sh > src_h:
        # The box is larger than any exact-aspect window the source holds. Fit
        # the window to the source and accept cropping the box's edges -- this
        # is a full-frame slide in practice, and a SPLIT is not chosen for one.
        sw = float(min(sw, src_w))
        sh = sw / r
        if sh > src_h:
            sh = float(src_h)
            sw = sh * r
    sw_i, sh_i = _even(sw), _even(sh)
    x = min(max(bx + 0.5 * bw - 0.5 * sw_i, 0.0), float(src_w - sw_i))
    y = min(max(by + 0.5 * bh - 0.5 * sh_i, 0.0), float(src_h - sh_i))
    return sw_i, sh_i, x, y


def _scale_to(out_w: int, out_h: int) -> str:
    return f"scale={out_w}:{out_h}:flags=lanczos"


def _set_sar(sar_w: int, sar_h: int) -> str:
    """Pin the delivered frame's sample aspect to the source's.

    The frame size is derived the way :func:`engine.reframe.vertical_scale_filter`
    derives it -- a pixel count that displays as 9:16 *because* of the source's
    SAR -- so the SAR has to survive the composition. Writing ``setsar=1``
    instead throws it away and an anamorphic deliverable measures 308x486 at DAR
    1:1.27 rather than 9:16, which is the exact defect the anamorphic regression
    test exists to pin (measured). For a square-pixel source this emits
    ``setsar=1/1`` and changes nothing.
    """
    return f"setsar={sar_w}/{sar_h}"


def _layout_chains(seg: SceneSegment, src_w: int, src_h: int, sar_w: int,
                   sar_h: int, crop_w: int, out_w: int, out_h: int,
                   track_pts: list[tuple[float, float]],
                   in_label: str, out_label: str | None,
                   trim: tuple[float, float] | None = None) -> list[str]:
    """The ffmpeg chains implementing one segment's layout.

    ``in_label`` names the source chain this layout reads (``"0"`` for a
    single-segment graph, a ``split`` output for a multi-segment one); an empty
    label means the chain *is* the graph input, which is the ``-vf``-compatible
    single-chain form. ``out_label`` names what the last chain produces.
    ``None`` for the output label means the last chain *is* the graph's output,
    which is how a single-segment multi-chain layout is expressed.

    ``trim`` gives the window this segment is clipped to in clip-relative time.
    ``setpts=PTS-STARTPTS`` after it makes the rebased PTS the time every
    expression in this chain sees, so the caller's keypoints are already
    segment-local. It is applied to the first chain only, upstream of any
    any multi-chain filter -- both downstream segments then read the same local
    clock and the dissolve between layouts starts at the boundary.
    """
    tail = ""
    if out_label is not None:
        tail = f"[{out_label}]"
    src = f"[{in_label}]" if in_label else ""
    pre = ""
    if trim is not None:
        pre = f"trim={trim[0]:.3f}:{trim[1]:.3f},setpts=PTS-STARTPTS,"

    if seg.cls is SceneClass.SLIDE:
        box = (seg.slide.x, seg.slide.y, seg.slide.w, seg.slide.h)
        bw, bh, bx, by = _band_geometry(src_w, src_h,
                                        out_w, out_h, box)
        return [f"{src}{pre}crop={bw}:{bh}:x='{bx:.0f}':y='{by:.0f}',"
                f"{_scale_to(out_w, out_h)},{_set_sar(sar_w, sar_h)}{tail}"]

    if seg.cls is SceneClass.SPLIT:
        # A single 9:16 crop cannot legally preserve both regions. Do not throw
        # one away: compose a deliberate two-band frame, with the speaker given
        # the larger band and the slide kept readable below it. This is the same
        # visual decision an editor makes for a two-person/speaker+screen shot,
        # and it is materially better than centring on one side and silently
        # deleting the other.
        if seg.face is not None and seg.slide is not None:
            top_h = _even(out_h * 0.58)
            bottom_h = max(2, out_h - top_h)
            # Give the speaker enough surrounding context to avoid a claustrophobic
            # face-only crop while keeping the face near the centre of its band.
            face = seg.face
            speaker_box = (
                max(0.0, face.cx - max(2.0 * face.w, 0.22 * src_w)),
                max(0.0, face.y - 1.5 * face.h),
                min(float(src_w), max(4.0 * face.w, 0.44 * src_w)),
                min(float(src_h), max(3.0 * face.h, 0.55 * src_h)),
            )
            sbw, sbh, sbx, sby = _band_geometry(
                src_w, src_h, out_w, top_h, speaker_box
            )
            dbw, dbh, dbx, dby = _band_geometry(
                src_w, src_h, out_w, bottom_h,
                (seg.slide.x, seg.slide.y, seg.slide.w, seg.slide.h),
            )
            # The split is a small labelled subgraph inside the enclosing
            # composition. Its final pad must carry the caller's output label;
            # otherwise the outer xfade graph has nothing to consume.
            tail_label = f"[{out_label}]" if out_label is not None else ""
            return [
                f"{src}{pre}split=2[sp][sl];"
                f"[sp]crop={sbw}:{sbh}:x='{sbx:.0f}':y='{sby:.0f}',"
                f"{_scale_to(out_w, top_h)}[spv];"
                f"[sl]crop={dbw}:{dbh}:x='{dbx:.0f}':y='{dby:.0f}',"
                f"{_scale_to(out_w, bottom_h)}[slv];"
                f"[spv][slv]vstack=inputs=2,{_set_sar(sar_w, sar_h)}"
                f"{(',' + ','.join(tail)) if tail else ''}{tail_label}"
            ]
        # If perception loses either region, retain the old safe tracked crop.
        x_expr = _x_expression(src_w, crop_w, track_pts)
        return [f"{src}{pre}crop={crop_w}:{src_h}:x='{x_expr}':y=0,"
                f"{_scale_to(out_w, out_h)},{_set_sar(sar_w, sar_h)}{tail}"]

    # SPEAKER. The crop is the full height the subject tracker already planned
    # against; in the joint case the same tracking is clamped to the window that
    # also keeps the slide.
    if seg.joint and seg.slide is not None:
        # The clamp is the joint interval itself: any crop left-edge inside it
        # holds both regions by construction, and _x_expression centres the
        # tracked subject inside it. ``_slide_crop_window`` is the constraint
        # the face does not already impose, and it is the binding one here --
        # a joint segment exists precisely because the face's own window was
        # wide enough to leave the slide room.
        lo, hi = _slide_crop_window(seg.slide, crop_w)
        lo = max(0.0, lo)
        hi = min(float(src_w - crop_w), hi)
        if lo <= hi:
            x_expr = _x_expression(src_w, crop_w, track_pts, clamp=(lo, hi))
            return [f"{src}{pre}crop={crop_w}:{src_h}:x='{x_expr}':y=0,"
                    f"{_scale_to(out_w, out_h)},{_set_sar(sar_w, sar_h)}{tail}"]
    x_expr = _x_expression(src_w, crop_w, track_pts)
    return [f"{src}{pre}crop={crop_w}:{src_h}:x='{x_expr}':y=0,"
            f"{_scale_to(out_w, out_h)},{_set_sar(sar_w, sar_h)}{tail}"]


def _graph(segments: list[SceneSegment], src_w: int, src_h: int, sar_w: int,
           sar_h: int, crop_w: int, out_w: int, out_h: int,
           track_pts: list[list[tuple[float, float]]],
           tail: tuple[str, ...]) -> str:
    """Compose the layouts of every segment into one filtergraph.

    A single segment whose layout is a single chain (a plain SPEAKER or SLIDE
    segment) is returned as that bare chain: it runs under ``-vf`` unchanged.
    Several segments joined by transitions are returned as a labelled graph whose
    final chain produces ``[v]``, which needs ``-filter_complex`` plus an
    explicit ``-map`` (a simple ``-vf`` graph rejects a labelled multi-chain
    description: measured, "Error initializing a simple filtergraph"). The
    caller tells the two apart with :func:`is_composed`.

    For more than one segment the source is split once per segment and the
    per-segment layouts are joined by an ``xfade`` cascade, whose offsets are
    the segment start times -- so each dissolve begins exactly where the scene
    class changes, and every chain runs the clip's full duration, which is what
    ``xfade`` needs in order to hold input A up to its offset.
    """
    if len(segments) == 1:
        body = _layout_chains(segments[0], src_w, src_h, sar_w, sar_h,
                              crop_w, out_w, out_h, track_pts[0], "", None)[0]
        if tail:
            body = body + "," + ",".join(tail)
        return body

    # ``split`` without explicit output labels assigns its own generated names,
    # which are not the ``s{i}`` the per-segment chains refer to -- ffmpeg then
    # reads ``[s0]`` as a stream specifier and dies with "Invalid stream
    # specifier: s0" (measured). Naming them keeps the graph self-describing
    # instead of depending on a pad-naming convention no documentation guarantees.
    parts = [f"[0]split={len(segments)}" +
             "".join(f"[s{i}]" for i in range(len(segments)))]

    # Each segment needs its *own* clock. ``xfade`` is a concatenation filter,
    # not a same-timeline switcher: it starts input B's timeline at the offset
    # and reads it forward from there (measured -- output frame N past the
    # offset shows input B's frame N - offset*fps, at 43.5 dB against 6-23 dB
    # for its neighbours, and not B's frame N). A graph where every chain shares
    # the clip-local clock therefore holds each post-transition segment's
    # *picture* seconds in the past while its crop expressions -- which do run on
    # filter time -- pan to the position that is right for the frame being shown
    # a moment later. The two disagree, and the deliverable slides away from its
    # audio for as long as the clip runs past the first dissolve. Trimming each
    # chain to its window and rebasing its PTS puts picture and expression on one
    # local clock; ``setpts=PTS-STARTPTS`` is provably what a downstream ``crop``
    # then sees as ``t`` (measured: pixel-identical to the same expression
    # evaluated on a clock that starts at the trim point).
    half = _TRANSITION_S / 2.0
    windows: list[tuple[float, float]] = []
    for i, seg in enumerate(segments):
        # The window is the classified span plus half a transition on every side
        # that has a neighbour, which centres each dissolve on the class change
        # instead of running it afterwards: during the fade the outgoing layout
        # still describes content that belongs to it and the incoming one
        # already does. The extensions are also what pays for the overlap
        # ``xfade`` consumes -- each transition takes _TRANSITION_S, half from
        # each side -- so the crossfaded total comes back to exactly the
        # interval's duration and the deliverable still matches its EDL and its
        # audio frame for frame. No frame-count correction is needed in the
        # render for composing, which matters: that count is what the exported
        # timeline stamps every cut on.
        s = seg.start - (half if i else 0.0)
        e = seg.end + (half if i < len(segments) - 1 else 0.0)
        windows.append((s, e))

    for i, seg in enumerate(segments):
        # The tracker keypoints are clip-local and the trimmed chain's ``t`` is
        # segment-local, so the expressions have to be rebased by the same
        # offset the window was extended by, or the crop would sit one half
        # transition off the subject.
        base = windows[i][0]
        local_track = [(t - base, c) for t, c in track_pts[i]]
        chains = _layout_chains(seg, src_w, src_h, sar_w, sar_h, crop_w,
                                out_w, out_h, local_track, f"s{i}", f"p{i}",
                                trim=windows[i])
        parts.extend(chains)

    # ``xfade``'s offset is where input B's clock begins, and it requires input
    # A to run to offset + duration, so the only offset that dissolves the whole
    # tail of A is A's own duration minus the transition. Accumulating over the
    # extended windows lands each dissolve on its class change and makes the
    # total above come out as intended.
    cum = 0.0
    prev = "p0"
    for i in range(1, len(segments)):
        cum += windows[i - 1][1] - windows[i - 1][0]
        offset = cum - i * _TRANSITION_S
        last = i == len(segments) - 1
        nxt = "v" if last else f"q{i}"
        chain = (f"xfade=transition=fade:duration={_TRANSITION_S:.3f}"
                 f":offset={offset:.3f}")
        if last:
            chain += "," + _set_sar(sar_w, sar_h)
            if tail:
                chain += "," + ",".join(tail)
        parts.append(f"[{prev}][p{i}]{chain}[{nxt}]")
        prev = nxt
    return ";".join(parts)


def is_composed(graph: str) -> bool:
    """True when ``graph`` is a multi-chain description needing ``-filter_complex``.

    A chain never contains a bare ``;`` -- it is ffmpeg's chain separator, and
    no option value in any graph this module builds carries one -- so the
    presence of one is an unambiguous signal that the result is a labelled
    graph ending in ``[v]`` and cannot be passed to ``-vf``.
    """
    return ";" in graph


# ---------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _SceneCacheKey:
    video: str
    size: int
    mtime: int
    start: float
    end: float
    samples: int
    # The tail is part of the returned graph, not a caller-side decoration: the
    # shipped subtitles ride inside the composition's last chain. Two calls that
    # differ only in the tail get different graphs, so a key that ignored it
    # would hand the second caller the first caller's -- measured, it silently
    # dropped the subtitles from the captioned render when an uncaptioned render
    # of the same interval had already populated the cache
    # (tests/test_render.py, test_the_burned_captions_survive_the_vertical_crop).
    tail: tuple[str, ...] = ()

    def __hash__(self) -> int:
        return hash((self.video, self.size, self.mtime, self.start,
                     self.end, self.samples, self.tail))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _SceneCacheKey) and self.__dict__ == other.__dict__


_SCENE_CACHE: dict[_SceneCacheKey, str] = {}


def _frame_area_slides(frames: list[np.ndarray | None], w: int, h: int,
                       boundaries: set[int], kept: list[tuple[int, float, np.ndarray]]
                       ) -> tuple[dict[int, SlideRect | None], list[SlideRect]]:
    """Detect slides once per shot, and report every slide the interval holds.

    A slide's position does not move within a shot, so detecting per shot rather
    than per frame is the right granularity: it costs one pass per shot instead
    of one per sample, and it prevents a slide from flickering in and out of the
    plan when a single sample decodes poorly.
    """
    if not boundaries:
        boundaries = set()
    per_shot: dict[int, SlideRect | None] = {}
    all_slides: list[SlideRect] = []
    # Assign each kept sample to its shot. A boundary index refers to the
    # position in the decoded sample list, so it has to be matched against the
    # kept indices rather than against the shot's own counter.
    shots: dict[int, list[np.ndarray]] = {}
    order: list[int] = []
    cur = 0
    for idx, _t, frame in kept:
        if idx in boundaries and shots:
            cur += 1
        shots.setdefault(cur, []).append(frame)
        if cur not in order:
            order.append(cur)
    for shot in order:
        found = detect_slides(shots[shot], w, h)
        per_shot[shot] = found[0] if found else None
        if found:
            all_slides.extend(found)
    return per_shot, all_slides


def _carry_slides(segments: list[SceneSegment], times: list[float],
                  classes: list[SceneClass], shot_of: list[int],
                  per_shot: dict[int, SlideRect | None],
                  faces: list[FaceBox | None], crop_w: float,
                  src_w: float) -> list[SceneSegment]:
    """Carry the slide and the joint verdict into the segments.

    The slide comes from a sample *inside the segment* that the classifier saw
    as the segment's class, not from the first sample at or after the segment's
    start. ``plan_scenes`` absorbs a run shorter than ``_MIN_HOLD_S`` into its
    longer neighbour by rewriting that run's class to the neighbour's, so the
    samples at a segment's edge can carry a class the segment reports without
    ever having been classified as it -- and their shot can hold no slide at
    all. A slide segment uses this region to choose its crop; retaining it on a
    SPLIT segment keeps the classification evidence available to diagnostics.

    Choosing an originating sample is provably safe instead. A segment's class
    is always the *original* class of some sample inside it -- absorption only
    merges adjacent runs and adopts a class the neighbour already held -- and a
    sample classified ``SPLIT`` or ``SLIDE`` has a slide by construction, since
    ``classify_scene`` returns those only for a slide it was handed. The lookup
    therefore retains the content region associated with each classification.
    """
    out: list[SceneSegment] = []
    for seg in segments:
        i = next((j for j, t in enumerate(times)
                  if seg.start <= t <= seg.end and classes[j] is seg.cls), None)
        if i is None:
            # Cannot happen for the reason above; the fallback keeps a future
            # change to the absorption rules from turning into a crash here
            # rather than into an assertion where the cause is visible.
            i = next((j for j, t in enumerate(times) if t >= seg.start),
                     len(times) - 1)
        slide = per_shot.get(shot_of[i])
        joint = (seg.cls is SceneClass.SPEAKER and slide is not None
                 and faces and any(
                     f is not None and both_fit_in_crop(f, slide, crop_w, src_w)
                     for f in faces))
        out.append(SceneSegment(seg.start, seg.end, seg.cls, slide, joint, faces[i]))
    return out


def analyse_scene(video: Path, start: float, end: float, samples: int = 28
                  ) -> tuple[list[SceneSegment], list[SlideRect], int, int]:
    """Perceive and plan one interval.

    Returns the held scene segments, every slide the interval contains, and the
    source dimensions. Public so a caller can answer "why was this clip
    composed this way" without rebuilding the filter.
    """
    times, frames, w, h, per_frame, boundaries = _perceive_window(video,
                                                                 start, end,
                                                                 samples)
    if not times:
        # Zeros, not max(w, 1): a caller checks this to decide whether the
        # source was readable at all, and a clamped geometry used to become a
        # degenerate crop that ffmpeg rejects at configure time. The frames are
        # genuinely unknown, so the dimensions are too.
        return [], [], 0, 0
    sar_w, sar_h = probe_sample_aspect(video)
    crop_w = min(vertical_crop_width(h, sar_w, sar_h), w)

    kept = [(i, t, f) for i, (t, f) in enumerate(zip(times, frames))
            if f is not None]
    if not kept:
        # Times but no decodable frame: the source opened and its samples are
        # placed, but not one of them decoded. Reported as unreadable rather
        # than as an empty plan, for the same reason as the ``not times`` case
        # above -- an editor needs to know the file is the problem.
        return [], [], 0, 0

    boundaries = set(boundaries)
    per_shot, all_slides = _frame_area_slides(frames, w, h, boundaries, kept)

    # One representative face per sample: the detector's top-ranked box. The
    # election that turns a stream of boxes into a subject is ``plan_crop``'s
    # job, and this only needs a box good enough to place the speaker.
    faces: list[FaceBox | None] = []
    for idx, _t, _f in kept:
        boxes = per_frame[idx] if idx < len(per_frame) else []
        if not boxes:
            faces.append(None)
            continue
        x, y, fw, fh = boxes[0][:4]
        faces.append(FaceBox(float(x), float(y), float(fw), float(fh)))

    shot_of: list[int] = []
    cur = 0
    for idx, _t, _f in kept:
        if idx in boundaries and shot_of:
            cur += 1
        shot_of.append(cur)

    classes: list[SceneClass] = []
    for face, shot in zip(faces, shot_of):
        slide = per_shot.get(shot)
        classes.append(classify_scene(face, slide, crop_w, w, h))

    kept_times = [t for _, t, _ in kept]
    segments = plan_scenes(kept_times, classes,
                           {j for j, (i, _, _) in enumerate(kept) if i in boundaries})

    out = _carry_slides(segments, kept_times, classes, shot_of, per_shot,
                        faces, crop_w, w)

    # The samples are evidence *for* the interval, not its boundary: the grid
    # sample landing exactly on ``end`` seeks past the final frame and comes back
    # undecodable, so the last sample is dropped and the plan stops one interval
    # early. Measured on an 8s interval with 48 samples, the plan ended at 7.830
    # and a composed graph trimmed its last chain to that, delivering 94 frames
    # where the EDL and the audio the render maps alongside it expect 96. The
    # classes at each edge are the ones nearest it, so spanning the plan over the
    # interval the caller asked about makes a composed deliverable match its
    # audio and its EDL frame for frame. ``_graph`` relies on this: it times the
    # xfade off the segments and assumes the total comes back to the duration.
    if out:
        out[0] = replace(out[0], start=min(out[0].start, start))
        out[-1] = replace(out[-1], end=max(out[-1].end, end))
    return out, all_slides, w, h


def scene_filter(video: Path, start: float, end: float, tail: tuple[str, ...] = (),
                 samples: int = 28) -> str:
    """The complete 9:16 filter for one interval, scene-aware.

    ``tail`` holds filters that must land on the finished frame -- the shipped
    subtitles -- and is folded into the graph's last chain. The composition owns
    this because a multi-chain graph cannot be extended with a comma from the
    outside without knowing which chain is last, which is the reason this
    function takes the subtitles rather than letting the caller join them.

    Returns the ordinary single-chain crop when the interval holds no slide and
    one scene class: byte-identical to what ``engine.reframe`` shipped before
    this module existed, so a talking-head clip renders exactly as it did and
    the change is opt-in by evidence.
    """
    key = _SceneCacheKey(str(video), video.stat().st_size, video.stat().st_mtime_ns,
                         round(start, 3), round(end, 3), samples,
                         tuple(tail))
    hit = _SCENE_CACHE.get(key)
    if hit is not None:
        return hit

    segments, slides, w, h = analyse_scene(video, start, end, samples)
    if not w or not h:
        # Neither reader could open the source at all: not "no subject found",
        # but "no frame was ever decoded". The filter this would build is
        # ``crop=1:0:...``, which ffmpeg rejects while configuring the crop pad
        # and exits with no output file -- measured, exit 234 with a zero-byte
        # file. That surfaces to the editor as a raw ffmpeg failure with no hint
        # that the source is unreadable, so raise here instead. The distinction
        # matters because the two have different fixes: an untracked subject is a
        # composition question, and
        # an unreadable file is an ingest one.
        raise UnreadableSource(
            f"{video}: no frame decoded from {start:.3f}s-{end:.3f}s by either "
            f"the OpenCV reader or the ffmpeg fallback; the source is missing, "
            f"truncated past readability, or in a codec neither reader supports")
    sar_w, sar_h = probe_sample_aspect(video)
    crop_w = min(vertical_crop_width(h, sar_w, sar_h), w)

    plain_speaker = bool(segments) and all(
        seg.cls is SceneClass.SPEAKER for seg in segments) and not slides
    if not segments or plain_speaker:
        # Built inline rather than by calling back into
        # ``engine.reframe.vertical_reframe_filter``: that function delegates
        # here, so composing it would recurse. The chain it builds is the same
        # one, from the same cached trajectory, so the output is identical.
        from engine.reframe import (
            _probe_and_track,
            _x_expression,
            vertical_scale_filter,
        )
        _w, _h, points = _probe_and_track(video, start, end, samples)
        result = (f"crop={crop_w}:{_h}:x='{_x_expression(_w, crop_w, points)}':y=0,"
                  f"{vertical_scale_filter(crop_w, sar_w, sar_h)}")
        if tail:
            result = result + "," + ",".join(tail)
        _SCENE_CACHE[key] = result
        return result

    # Composed scenes must obey the same minimum 720-line delivery as
    # the plain speaker path. A low-resolution source can have a crop narrower
    # than 405 px, so preserving crop_w here would silently ship a 240p
    # vertical frame even though the caption burn claims a 720-line canvas.
    min_display_height = 720
    min_frame_w = min_display_height * 9 * sar_h / (16 * sar_w)
    out_w = _even(min(1080, max(crop_w, min_frame_w)))
    out_h = _even(out_w * sar_w * 16 / (9 * sar_h))

    # The crop trajectory uses the same cached detections as the scene planner;
    # re-running perception here would spend time to recover identical boxes.
    times, frames, _w, _h, per_frame, boundaries = _perceive_window(video,
                                                                   start, end,
                                                                   samples)
    kept = [(i, t, f) for i, (t, f) in enumerate(zip(times, frames))
            if f is not None]
    kept_times = [t for _, t, _ in kept]
    kept_dets = [per_frame[i] for i, _, _ in kept]
    kept_bounds = {j for j, (i, _, _) in enumerate(kept) if i in boundaries}

    track_pts: list[list[tuple[float, float]]] = []
    for seg in segments:
        # The samples spanning this segment, used to plan both of its
        # trajectories. A segment shorter than the sample spacing collapses to
        # two adjacent samples rather than to one, because a single sample
        # gives ``plan_crop`` no interval to smooth over.
        a = min(range(len(kept_times)),
                key=lambda i: abs(kept_times[i] - seg.start))
        b = min(range(len(kept_times)),
                key=lambda i: abs(kept_times[i] - seg.end))
        a, b = min(a, b), max(a, b)
        b = max(b, min(a + 1, len(kept_times) - 1))
        seg_times = kept_times[a:b + 1]
        seg_dets = kept_dets[a:b + 1]
        if not seg_times:
            track_pts.append([])
            continue
        seg_bounds = {j for j in kept_bounds if a <= j <= b}
        track_pts.append(plan_crop(seg_times, seg_dets, seg_bounds, w, h, crop_w))

    result = _graph(segments, w, h, sar_w, sar_h, crop_w, out_w, out_h,
                    track_pts, tail)
    _SCENE_CACHE[key] = result
    return result