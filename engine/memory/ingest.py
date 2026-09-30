"""Ingest adapters — perception that already exists, translated into evidence.

Why this module exists
----------------------

``docs/memory-perception-inventory.md`` established the central fact this module
rests on: the pipeline *already perceives* face tracks, shot boundaries, scene
classes, speech regions, pause structure and story structure, and then discards
every one of them except the editorial corpus, reducing multimodal perception to
either an ffmpeg filtergraph string or an aggregate ratio. Memory is therefore a
capture problem, not a recompute problem, and these adapters are the capture.

Each adapter is a *translator*, not a producer. It takes records the pipeline
has already built and returns ``Evidence``; it never decodes a frame, never
reads an audio buffer, never calls a model. Three consequences follow, and all
three are deliberate:

- **No heavy imports.** The adapters accept duck-typed records, so importing
  ``engine.memory.ingest`` does not pull OpenCV, NumPy or Whisper into the
  memory package. A reasoning layer that only needs ``Evidence`` should not pay
  for a decoder it will not use, and on this host that is not a stylistic point.
- **Testable with stand-ins.** Every adapter can be exercised against a
  hand-built record without a media file, which is what makes failure-first
  testing of the memory stack possible at all here.
- **Capture is still the caller's job.** These functions do not wire themselves
  into ``engine.coverage.measure`` or ``engine.render.render_clip``. The
  inventory names the exact intercept points and the two-line size of the
  change; wiring is a separate edit to those modules and is not smuggled in
  here.

Timebases — the one thing this module must get right
----------------------------------------------------

Two conventions meet here, and conflating them puts every visual timestamp in
the wrong place without any error:

- Signals from ``engine.reframe`` and ``engine.reframe_scenes`` — face tracks,
  shot boundaries, scene segments — report seconds **relative to the start of
  the analysed interval**, because that is the timeline ffmpeg's filtergraph
  sees when it seeks into the source.
- Signals from ``engine.vad``, ``engine.pause_classifier``, ``engine.audio_fusion``
  and ``engine.corpus`` report **source seconds**.

So every visual adapter takes an explicit ``offset`` — the interval's start in
source seconds — and adds it, and every audio and editorial adapter does not.
The offset is a required argument with no default on the visual side, because a
default of ``0.0`` is exactly the silent-wrong-answer case: it works perfectly
on the first clip of the source and drifts on every clip after it. Nothing in
this module guesses which convention a record uses.

What the adapters do not claim
------------------------------

``detect_slides`` returns ``SlideRect``s with no time at all — geometry, no
per-frame attribution. An adapter that emitted a slide as evidence of a precise
span would be inventing the span, so the slide adapter records the analysed
window as the interval and says so in the rationale: the support is coarse
because the instrument is coarse, and a coarse interval honestly labelled is
worth more than a precise one silently fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.memory.evidence import Evidence, Modality, Source
from engine.memory.intervals import IntervalSet

SCHEMA_VERSION = "1.1"

_SCENE_PROSE = {
    "speaker": "A speaker is on screen",
    "slide": "A slide is on screen",
    "split": "Speaker and slide share the frame (split layout)",
}
_PAUSE_PROSE = {
    "dead_air": "Dead air: no speech and no visible structural reason for it",
    "breathing": "A breath pause between sentences",
    "hesitation": "A hesitation pause inside a sentence",
    "intentional": "An intentional pause used for emphasis",
    "uncertain": "A pause the classifier could not confidently type",
}


# ── audio: source seconds, no offset ─────────────────────────────────────


def speech_regions(
    regions,
    *,
    backend: str = "",
    duration: float = 0.0,
) -> list[Evidence]:
    """VAD speech regions → ``AUDIO`` evidence, one per region.

    ``score`` is the region's own speech probability as the backend measured it
    and is carried through unchanged — see ``evidence.py``: confidence is
    recorded, never adjusted here. A region with no score keeps the field's
    default of 1.0 rather than being marked untrusted, because the VAD chain's
    energy backend produces no score at all and a missing measurement is not a
    failed one.
    """
    src = Source.of(
        "engine.vad", "segment_with_meta",
        backend=backend or "unknown", threshold=0.5,
    )
    out: list[Evidence] = []
    for r in regions:
        start, end = float(r.start), float(r.end)
        out.append(Evidence(
            claim="Speech is present",
            intervals=IntervalSet.of((start, end)),
            modality=Modality.AUDIO,
            source=src,
            confidence=float(getattr(r, "score", 1.0)),
            rationale=(
                f"VAD backend {backend or 'unknown'} reported speech across "
                f"{start:g}..{end:g}s"
            ),
        ))
    return out


def silence_regions(silences, *, duration: float = 0.0) -> list[Evidence]:
    """The complement of the speech regions → ``AUDIO`` evidence.

    Silence is evidence in its own right, not the absence of evidence: it is
    what ``pause_classifier`` consumes, and an editor asking "where does the
    audio drop out" is asking for these spans. Confidence is 1.0 by
    construction — silence here is the backend's positive determination that
    no speech was present, not a low-scored guess.
    """
    src = Source.of("engine.vad", "silence_from_speech")
    out: list[Evidence] = []
    for s in silences:
        start, end = float(s.start), float(s.end)
        out.append(Evidence(
            claim="Silence: no speech detected",
            intervals=IntervalSet.of((start, end)),
            modality=Modality.AUDIO,
            source=src,
            confidence=1.0,
            rationale=(
                f"no VAD speech inside {start:g}..{end:g}s "
                f"({float(s.duration):g}s)"
            ),
        ))
    return out


def pauses(pauses_) -> list[Evidence]:
    """Classified pauses → ``AUDIO`` evidence, one per pause.

    The classifier is rules-based and deterministic, and it assigns its own
    per-type confidence; both are carried through. ``grounded=True`` because a
    pause is observed — it is a measured silence with a typed reading of it,
    not an inference about content.

    Note that the *typing* is partly text-driven: terminal punctuation decides
    whether a silence is a breath or a hesitation. The rationale records which
    basis was used where the record carries it, because an editor weighing a
    hesitation pause should know it rests on punctuation, not on acoustics.
    """
    src = Source.of("engine.pause_classifier", "classify_all_pauses")
    out: list[Evidence] = []
    for p in pauses_:
        kind = str(getattr(p.pause_type, "value", p.pause_type))
        start, end = float(p.start), float(p.end)
        out.append(Evidence(
            claim=_PAUSE_PROSE.get(kind, f"A {kind} pause"),
            intervals=IntervalSet.of((start, end)),
            modality=Modality.AUDIO,
            source=src,
            confidence=float(getattr(p, "confidence", 1.0)),
            rationale=(
                f"{kind} pause of {float(p.duration):g}s at "
                f"{start:g}..{end:g}s; typed from terminal punctuation where "
                f"word timings were available, else from speech-region length"
            ),
        ))
    return out


# ── visual: seconds relative to the analysed interval; offset required ───


def scene_segments(
    segments,
    *,
    offset: float,
    samples: int = 28,
    detector: str = "yunet",
) -> list[Evidence]:
    """``SceneSegment``s → ``VISUAL`` evidence, with class changes as links.

    ``offset`` is the analysed interval's start in source seconds and is
    **required**: the segments report seconds from the interval start, so an
    adapter that did not take an offset would place the first clip's scenes in
    the right place and every later clip's in the wrong one, silently.

    TRANSITION is deliberately *not* emitted as an evidence item. The inventory
    corrects a widespread belief here: it is not a ``SceneClass`` value but the
    crossfade the renderer inserts between segments whose classes differ. An
    adapter that modelled it as a class would record a transition as a stretch
    of video that is in fact a speaker, and would do so in a field other code
    reads as exhaustive. Class changes are recorded as ``meets`` links between
    the adjacent items instead — a relation between two scenes, which is what
    a transition is.
    """
    src = Source.of(
        "engine.reframe_scenes", "analyse_scene",
        samples=samples, detector=detector,
    )
    items: list[Evidence] = []
    for n, seg in enumerate(segments):
        kind = str(getattr(seg.cls, "value", seg.cls))
        items.append(Evidence(
            claim=_SCENE_PROSE.get(kind, f"Scene class {kind}"),
            intervals=IntervalSet.of((
                float(seg.start) + offset,
                float(seg.end) + offset,
            )),
            modality=Modality.VISUAL,
            source=src,
            confidence=1.0,
            rationale=(
                f"{kind} across {float(seg.start):g}..{float(seg.end):g}s of the "
                f"analysed interval (source {float(seg.start) + offset:g}.."
                f"{float(seg.end) + offset:g}s), from face and slide geometry"
            ),
            id=f"scene-{n:04d}",
        ))
    return items


def shot_boundaries(
    times,
    boundary_indices,
    *,
    offset: float,
    samples: int = 28,
    detector: str = "yunet",
) -> list[Evidence]:
    """Shot cuts → ``VISUAL`` evidence, one instant per cut.

    A cut is instantaneous, so the interval is a zero-length span — a closed
    interval whose endpoints coincide, which ``IntervalSet`` represents
    faithfully and which ``covers`` and ``at`` answer correctly. An adapter
    that gave a cut a duration would be inventing one.

    ``times`` are the sampled times *relative to the interval start*, in the
    same grid as ``_perceive_window`` returns; ``boundary_indices`` are the
    positions into it. Both are required together because the boundary set on
    its own carries no clock at all.
    """
    src = Source.of(
        "engine.reframe_perception", "find_shot_boundaries",
        samples=samples, threshold=22.0,
    )
    out: list[Evidence] = []
    for i in sorted(boundary_indices):
        if i < 0 or i >= len(times):
            # An index outside the sample grid is a caller bug, not a cut; it is
            # skipped rather than clamped, because clamping would record a cut
            # at a time that was never sampled.
            continue
        t = float(times[i]) + offset
        out.append(Evidence(
            claim="A shot cut occurs here",
            intervals=IntervalSet.of((t, t)),
            modality=Modality.VISUAL,
            source=src,
            confidence=1.0,
            rationale=(
                f"frame difference crossed the cut threshold at sampled index "
                f"{i}, source time {t:g}s; isolated crossings only — a run of "
                f"crossings is a pan, not a cut"
            ),
        ))
    return out


def face_presence(
    times,
    per_frame,
    *,
    offset: float,
    width: int,
    height: int,
    detector: str = "yunet",
    samples: int = 28,
) -> list[Evidence]:
    """Per-sample face detections → ``VISUAL`` evidence, one per sampled time.

    This is the signal the vertical reframer runs on, and the one the inventory
    calls the largest single perception expenditure in the pipeline: it decodes
    a window and runs a detector over every sampled frame, then keeps only the
    resulting crop filter. Each sampled time becomes one evidence item, so the
    resolution of the memory is the resolution of the sampling grid and no
    finer — the rationale states the grid rather than implying continuous
    tracking.

    ``per_frame`` is a list of detection lists, ``(x, y, w, h, score)`` in
    source pixels; a frame with no detections still yields an item, an observed
    absence with a rationale that says so. Face identity is *not* asserted
    anywhere here: the inventory records that ``_Track`` identity is positional
    only, with no recognition or re-identification in the system, so a claim
    like "the same speaker as before" is not one this adapter can make.
    """
    src = Source.of(
        "engine.reframe", "_perceive_window",
        samples=samples, detector=detector,
    )
    out: list[Evidence] = []
    for i, t_rel in enumerate(times):
        t = float(t_rel) + offset
        dets = per_frame[i] if i < len(per_frame) else []
        if dets:
            best = max(dets, key=lambda d: d[4] if len(d) > 4 else 0.0)
            n_faces = len(dets)
            claim = f"{n_faces} face{'s' if n_faces != 1 else ''} detected"
            rationale = (
                f"detector {detector} reported {n_faces} "
                f"face{'s' if n_faces != 1 else ''} at sampled time {t:g}s "
                f"(grid {samples} samples over the interval); best box "
                f"({float(best[0]):g},{float(best[1]):g}) "
                f"{float(best[2]):g}x{float(best[3]):g}px score "
                f"{float(best[4]) if len(best) > 4 else 0.0:g}"
            )
            confidence = float(best[4]) if len(best) > 4 else 1.0
        else:
            claim = "No face detected at this sample"
            rationale = (
                f"detector {detector} reported no face at sampled time {t:g}s "
                f"(grid {samples} samples over the interval); an absence at one "
                f"sample is not an absence across the sample's stride"
            )
            confidence = 1.0
        out.append(Evidence(
            claim=claim,
            intervals=IntervalSet.of((t, t)),
            modality=Modality.VISUAL,
            source=src,
            confidence=confidence,
            rationale=rationale,
        ))
    return out


def slide_rects(
    rects,
    *,
    offset: float,
    interval: tuple[float, float],
    samples: int = 28,
    detector: str = "yunet",
) -> list[Evidence]:
    """``SlideRect`` geometry → ``VISUAL`` evidence over the analysed window.

    ``detect_slides`` returns rectangles with **no time**: it scans a window's
    frames and reports the slide's geometry, not the frames it appeared in. So
    the interval here is the analysed window itself and the rationale says so
    plainly. This is the coarse-but-honest choice described in the module
    docstring — the alternative, attributing the slide to some narrower span,
    would be a span the instrument never measured.
    """
    src = Source.of(
        "engine.reframe_scenes", "detect_slides",
        samples=samples, detector=detector,
    )
    start = float(interval[0])
    end = float(interval[1])
    out: list[Evidence] = []
    for n, r in enumerate(rects):
        x, y = float(r.x), float(r.y)
        w, h = float(r.w), float(r.h)
        out.append(Evidence(
            claim="A slide region is present in the analysed window",
            intervals=IntervalSet.of((start + offset, end + offset)),
            modality=Modality.VISUAL,
            source=src,
            confidence=1.0,
            rationale=(
                f"static, edge-dense, bright region at ({x:g},{y:g}) "
                f"{w:g}x{h:g}px found somewhere in {start:g}..{end:g}s of the "
                f"interval; detect_slides reports geometry per window, not "
                f"per frame, so the span is the window and not a measured "
                f"appearance"
            ),
            id=f"slide-{n:04d}",
        ))
    return out


# ── editorial: source seconds, no offset ─────────────────────────────────


def topics(topics_, *, language: str = "unknown") -> list[Evidence]:
    """``Topic``s → ``EDITORIAL`` evidence, one per topic.

    A topic's ``start``/``end`` are source seconds, so no offset. The claim
    names the topic's label and its span is the span it covers; the rationale
    carries the topic's own ``reason`` verbatim where it has one, because that
    is the sentence that makes the topic checkable and it already exists.
    """
    src = Source.of("engine.topics", "cluster_topics", language=language)
    out: list[Evidence] = []
    for n, t in enumerate(topics_):
        start, end = float(t.start), float(t.end)
        label = str(getattr(t, "label", f"topic {n}"))
        reason = str(getattr(t, "reason", "") or "")
        out.append(Evidence(
            claim=f"The topic “{label}” is discussed",
            intervals=IntervalSet.of((start, end)),
            modality=Modality.EDITORIAL,
            source=src,
            confidence=float(getattr(t, "confidence", 1.0)),
            rationale=reason or (
                f"topic {label} spans {start:g}..{end:g}s of the corpus"
            ),
        ))
    return out


def beats(beats_, *, language: str = "unknown") -> list[Evidence]:
    """``DramaticBeat``s → ``EDITORIAL`` evidence.

    A beat is a point of change, and its ``start``/``end`` are source seconds.
    Intensity and polarity are carried into the claim, since "the turn of the
    story happens here, sharply negative" is the sentence an editor acts on and
    it costs nothing to build it from fields the beat already has.
    """
    src = Source.of("engine.threads", "build_beats", language=language)
    out: list[Evidence] = []
    for n, b in enumerate(beats_):
        start, end = float(b.start), float(b.end)
        kind = str(getattr(getattr(b, "kind", None), "value", getattr(b, "kind", "beat")))
        polarity = str(getattr(b, "polarity", "") or "")
        intensity = float(getattr(b, "intensity", 0.0) or 0.0)
        peak = bool(getattr(b, "is_thread_peak", False) or getattr(b, "is_corpus_peak", False))
        claim = f"A {kind} beat occurs"
        if polarity:
            claim = f"A {polarity} {kind} beat occurs"
        if peak:
            claim += ", and it is a peak"
        rationale = (
            f"{kind} beat at {start:g}..{end:g}s, intensity {intensity:g}"
            + (f", {polarity}" if polarity else "")
            + (", corpus peak" if getattr(b, "is_corpus_peak", False) else "")
        )
        out.append(Evidence(
            claim=claim,
            intervals=IntervalSet.of((start, end)),
            modality=Modality.EDITORIAL,
            source=src,
            confidence=float(getattr(b, "confidence", 1.0)),
            rationale=rationale,
        ))
    return out


def setup_payoff_links(links_, *, language: str = "unknown") -> list[Evidence]:
    """``SetupPayoffLink``s → ``EDITORIAL`` evidence.

    A setup-payoff link is the clearest case of evidence being *about two
    places at once*: the setup is laid down early and pays off much later, and
    the claim is not true of either span alone. The interval set therefore
    carries both, and its cardinality of two is load-bearing — a consumer that
    took the first span would read "this is set up here" and miss the payoff
    entirely.
    """
    src = Source.of("engine.threads", "link_setups_to_payoffs", language=language)
    out: list[Evidence] = []
    for n, link in enumerate(links_):
        s0, s1 = float(link.setup_start), float(link.setup_end)
        p0, p1 = float(link.payoff_start), float(link.payoff_end)
        kind = str(getattr(getattr(link, "kind", None), "value", getattr(link, "kind", "link")))
        out.append(Evidence(
            claim=f"A {kind} setup pays off",
            intervals=IntervalSet.of((s0, s1), (p0, p1)),
            modality=Modality.EDITORIAL,
            source=src,
            confidence=float(getattr(link, "confidence", 1.0)),
            rationale=(
                f"setup at {s0:g}..{s1:g}s pays off at {p0:g}..{p1:g}s; "
                f"the claim is true of neither span alone"
            ),
        ))
    return out


def broll_markers(markers, *, language: str = "unknown") -> list[Evidence]:
    """``BrollMarker``s → ``EDITORIAL`` evidence.

    ``broll`` is empty whenever the transcript carries no visual or deictic cues
    — the availability contract in the inventory is explicit that these may not
    exist, and this adapter returning an empty list for an empty input is that
    contract, not a failure. The marker's ``cue`` goes into the rationale
    because it is the word in the transcript that suggested the cutaway, and an
    editor checking the suggestion needs that word.
    """
    src = Source.of("engine.threads", "mark_broll", language=language)
    out: list[Evidence] = []
    for n, m in enumerate(markers):
        start, end = float(m.start), float(m.end)
        kind = str(getattr(getattr(m, "kind", None), "value", getattr(m, "kind", "b-roll")))
        cue = str(getattr(m, "cue", "") or "")
        out.append(Evidence(
            claim=f"A {kind} cutaway is cued here",
            intervals=IntervalSet.of((start, end)),
            modality=Modality.EDITORIAL,
            source=src,
            confidence=float(getattr(m, "confidence", 1.0)),
            rationale=(
                f"{kind} cutaway suggested for {start:g}..{end:g}s"
                + (f" on the cue “{cue}”" if cue else "")
                + "; the cue is a transcript word, not a visual observation"
            ),
        ))
    return out


# ── the join the pipeline never makes ────────────────────────────────────


@dataclass(frozen=True)
class JoinResult:
    """The output of ``link_overlapping``: joined items and the join count.

    ``joined`` reports how many items gained a link rather than returning only
    the new items, because "12 b-roll markers linked to on-screen evidence" and
    "12 b-roll markers, 0 linked" are different results and a caller who wants
    the second has no other way to compute it.
    """

    items: tuple[Evidence, ...]
    joined: int


def link_overlapping(
    items,
    *,
    targets,
    relation: str = "during",
) -> JoinResult:
    """Link each item to every target whose span it overlaps, by Allen relation.

    The join the pipeline never makes, and the one the inventory names as the
    first an editor would want: a b-roll marker is a transcript-level cue, the
    scene evidence is pixel-level observation, and "the cutaway is cued exactly
    where the slide is on screen" is a conclusion neither carries alone. Both
    sides keep their own modality and provenance — the link says *how* they
    relate, not that one subsumes the other.

    Items without an id are linked to by position-derived name (``item-N``) so
    the join works before the store has assigned ids; with ids, those are used
    as-is. Overlap is symmetric, so the caller links both directions by calling
    twice with the arguments swapped rather than this function silently
    building converse edges — the graph's convention, from ``EvidenceLink``,
    that a relation has one spelling per direction.
    """
    out: list[Evidence] = []
    joined = 0
    for n, item in enumerate(items):
        item_id = item.id or f"item-{n}"
        links = []
        for m, target in enumerate(targets):
            if item.intervals.overlaps(target.intervals):
                links.append((target.id or f"item-{m}", relation))
        if links:
            joined += 1
            current = item
            for target_id, rel in links:
                current = current.link(target_id, rel)
            out.append(current)
        else:
            out.append(item)
    return JoinResult(tuple(out), joined)
