"""Adapter tests: the junction where a provider's measurement becomes the
percentage the panel draws.

Two sides of the event stream are already pinned: ``test_progress_sources.py``
holds the providers to the payloads they emit, and ``test_cli_ui.py`` holds the
renderer to the events it consumes. Nothing between them was tested — and the
between is exactly where a fake percentage would be invented, because that is
where a count that came from a decode becomes a bar the user reads as work.

These drive the real ``selekyt.pipeline.run`` with the provider entry points and
``engine.render.render_clip`` monkeypatched to scripted measurements, so the two
adapters under test — ``_chunk_progress`` (provider index/count -> UI
current/total) and the render stage's per-orientation ``_sink`` (part/parts ->
the two sub-bar fractions) — are the real code, and every number on screen can
be traced back to a payload a provider actually sent.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from models.core import Segment, Word


# ── fixtures ─────────────────────────────────────────────────────────────────


_STORY_A1 = (
    "We shipped the feature on Tuesday morning. The metric moved "
    "forty percent overnight. The team celebrated the launch."
)
_STORY_A2 = (
    "The rollout completed ahead of schedule. Customers reported "
    "faster load times across the board. This outcome validated "
    "the quarter of hard work."
)
_SEPARATOR = "Episode 12. Recorded in March 2024."
# The second story shares a content word with itself and its last sentence
# opens with a discourse attachment: the coherence score reads three sentences
# with neither as two spliced stories, and would refuse a clip worth selecting.
_STORY_B = (
    "Hiring doubled the engineering team that year. Two interviewers "
    "joined every hiring panel. So the offer acceptance rate climbed "
    "to ninety percent."
)


def _words(text: str, start: float, length: float) -> list[Word]:
    """Timestamped words spaced evenly across a segment's length."""
    tokens = text.split()
    step = length / len(tokens)
    return [
        Word(tok, start + i * step, start + (i + 1) * step)
        for i, tok in enumerate(tokens)
    ]


def _segment(start: float, length: float, text: str) -> Segment:
    return Segment(start, start + length, text, _words(text, start, length))


def _two_stories() -> list[Segment]:
    """Two clips the editorial gate will pass, in the shape it needs.

    The first story occupies two segments so a span of it survives the word
    floor; the separator is one a boilerplate scan recognises and wide enough
    that rank's 4 s spacing floor keeps the two clips apart. These are the
    shapes ``tests/test_pipeline_e2e.py`` drives the same gate with.
    """
    return [
        _segment(0.0, 8.0, _STORY_A1),
        _segment(8.0, 8.0, _STORY_A2),
        # a separator the engine breaks spans on, not a sentence it absorbs
        _segment(16.0, 4.0, _SEPARATOR),
        _segment(20.0, 8.0, _STORY_B),
    ]


_PROVENANCE = {
    "engine": "test",
    "requested_model": "small",
    "model": "small",
    "downgrade_reason": "",
    "word_timestamps": "test",
    "fallback_reason": "",
}


def _write_qa(root: Path, outputs=None, source_path: str | None = None) -> Path:
    """Stand in for ``engine.qa.write_report``, which probes rendered MP4s.

    The renders here are scripted, not encoded, so QA has nothing to probe;
    the report stage still needs a path to record.
    """
    qa = root / "qa.json"
    qa.write_text("{}", encoding="utf-8")
    return qa


def _drive(
    monkeypatch,
    tmp_path: Path,
    events: list[dict],
    *,
    transcript: list[Segment] | None = None,
    payloads: tuple[dict, ...] = (),
    parts: tuple[int, int] = (3, 2),
    limit: int = 2,
    decode: bool = True,
    trust_render_cache: bool = False,
):
    """Run the real pipeline against scripted provider measurements.

    Every heavy boundary is stubbed to a scripted payload — the ASR entry
    point, the render entry point, the coverage gate's audio read, the fps
    probe and the QA probe — and nothing else. The two adapters under test run
    for real, and the returned logs are the provider's side of the
    conversation, so a test can hold the UI's events up against what was
    actually sent rather than against the pipeline's own arithmetic.

    Returns ``(payload_log, render_calls, decodes)``: every payload the fake
    providers emitted, one record per ``render_clip`` invocation, and one
    entry per chunk the fake ASR actually decoded.
    """
    import selekyt.pipeline as p

    media = tmp_path / "source" / "clip.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    # Written only when absent: the state's fingerprints key on the media's
    # mtime, so rewriting an identical file would bust the transcript and
    # render caches of a resumed run.
    if not media.is_file():
        media.write_bytes(b"selektyt progress fixture")

    payload_log: list[dict] = []
    render_calls: list[dict] = []
    decodes: list[int] = []

    monkeypatch.setattr(
        p, "download",
        lambda source, dest, cookies=None: (media, {"title": "scripted"}),
    )
    # The source is a placeholder file, so both probes are stubbed rather than
    # allowed to spawn ffmpeg against media that cannot answer.
    monkeypatch.setattr(p, "probe_fps", lambda path: 30.0)
    monkeypatch.setattr(p, "probe_duration", lambda path: None)
    # The extracted audio does not exist in this fixture (there is no decode),
    # so the level measurement the empty-transcript diagnosis reads is stubbed
    # to "silent" rather than allowed to fail on a missing file.
    monkeypatch.setattr(p, "peak_dbfs", lambda path: None)

    def fake_transcribe(_media, **kw) -> tuple[list[Segment], str, dict]:
        sink = kw.get("progress")
        for payload in payloads:
            if decode:
                decodes.append(payload["index"])
            if sink is not None:
                sink(dict(payload))
        return list(transcript or []), "en", dict(_PROVENANCE)

    monkeypatch.setattr(p, "transcribe_auto", fake_transcribe)

    coverage = types.SimpleNamespace(
        verdict="not_measured",
        detail="no audio was measured",
        speech_regions=(),
        duration_s=0.0,
        vad_backend=None,
        coverage_ratio=None,
        as_dict=lambda: {"verdict": "not_measured"},
    )
    monkeypatch.setattr(p, "check_coverage", lambda *a, **k: coverage)

    def fake_render_clip(source, candidate, out, ass=None, vertical=False,
                         fps=30.0, progress=None, threads=None):
        n = parts[0] if not vertical else parts[1]
        render_calls.append({"out": out, "vertical": vertical, "parts": n})
        # The real loop reports a part before and after rendering it; the
        # fraction the UI divides by is the count of parts this loop performs.
        for i in range(n):
            if progress is not None:
                payload_log.append({"clip": out, "vertical": vertical,
                                    "part": i, "parts": n})
                progress({"part": i, "parts": n, "vertical": vertical})
            # a real part would be encoded here
            if progress is not None:
                payload_log.append({"clip": out, "vertical": vertical,
                                    "part": i + 1, "parts": n})
                progress({"part": i + 1, "parts": n, "vertical": vertical})
        return out

    monkeypatch.setattr(p, "render_clip", fake_render_clip)
    monkeypatch.setattr(p, "write_report", _write_qa)
    if trust_render_cache:
        # A resumed run's renders are verified by ffprobe in the real pipeline;
        # here they are scripted, so the verification is trusted instead.
        monkeypatch.setattr(p, "_render_is_valid", lambda *a, **k: True)

    p.run("https://example.com/v", tmp_path, model="small", limit=limit,
          progress=events.append)
    return payload_log, render_calls, decodes


def _chunk_events(events: list[dict]) -> list[dict]:
    return [e for e in events
            if e["type"] == "progress" and e["stage"] == "transcribe"]


def _render_events(events: list[dict]) -> list[dict]:
    return [e for e in events
            if e["type"] == "progress" and e["stage"] == "render"]


def _frac(event: dict, label: str) -> float:
    for unit in event["subprogress"]:
        if unit["label"] == label:
            return float(unit["fraction"])
    raise AssertionError(f"no sub-bar labelled {label!r} in {event!r}")


def _has_bars(event: dict) -> bool:
    """The loop-tail counter event carries no bar fractions — it closes the
    clip count after the clip is exported — so it is not a bar event."""
    return "subprogress" in event


# ── transcription: provider index/count -> UI current/total ──────────────────


def test_chunk_events_carry_the_providers_index_and_count_verbatim(
        monkeypatch, tmp_path):
    """The UI's current/total is the provider's index/count forwarded, not a
    number the display chose. The count here is 7 while only two chunks
    report, and the indexes skip — an adapter that counted events, or that
    assumed one unit per tick of a clock, would show neither."""
    events: list[dict] = []
    _drive(monkeypatch, tmp_path, events,
           transcript=_two_stories(),
           payloads=({"index": 1, "count": 7, "eta": None},
                     {"index": 3, "count": 7, "eta": None}))
    chunk = _chunk_events(events)
    assert [e["current"] for e in chunk] == [1, 3], \
        "current must be the provider's index, not a running count"
    assert all(e["total"] == 7 for e in chunk), \
        "total must be the provider's count, not the number of events received"


def test_chunk_events_advance_the_count_by_exactly_one_per_decoded_chunk(
        monkeypatch, tmp_path):
    """A contiguous chunk stream advances the UI count by exactly one per
    event, and the pair ends where the provider's own count said it ends."""
    events: list[dict] = []
    _drive(monkeypatch, tmp_path, events,
           transcript=_two_stories(),
           payloads=tuple({"index": i, "count": 4, "eta": None}
                          for i in (1, 2, 3, 4)))
    currents = [e["current"] for e in _chunk_events(events)]
    assert currents == [1, 2, 3, 4]
    diffs = [b - a for a, b in zip(currents, currents[1:])]
    assert set(diffs) == {1}, f"each decoded chunk advances the count by one, got {diffs}"


def test_a_cached_chunk_advances_the_count_without_decoding(
        monkeypatch, tmp_path):
    """A resumed run skips the decode but still reports the chunk, so the bar
    moves past it. Here the fake ASR decodes nothing at all — every chunk is a
    cache hit — and the count still walks 1..4, because the adapter forwards a
    payload, not a unit of work it performed."""
    events: list[dict] = []
    _, _, decodes = _drive(
        monkeypatch, tmp_path, events,
        transcript=_two_stories(),
        payloads=tuple({"index": i, "count": 4, "eta": None}
                       for i in (1, 2, 3, 4)),
        decode=False)
    assert decodes == [], "the fixture decoded nothing"
    assert [e["current"] for e in _chunk_events(events)] == [1, 2, 3, 4], \
        "a chunk reported without a decode must still advance the count"


def test_eta_is_forwarded_only_when_the_provider_measured_one(
        monkeypatch, tmp_path):
    """The ETA is the one derived number on the chunk event: it is forwarded
    when the provider has a running average and is absent — never a guess —
    when it does not."""
    events: list[dict] = []
    _drive(monkeypatch, tmp_path, events,
           transcript=_two_stories(),
           payloads=({"index": 1, "count": 3, "eta": None},
                     {"index": 2, "count": 3, "eta": 42.0},
                     {"index": 3, "count": 3, "eta": 37.5}))
    chunk = _chunk_events(events)
    assert "eta" not in chunk[0], "no measurement means no ETA field at all"
    assert chunk[1]["eta"] == 42.0
    assert chunk[2]["eta"] == 37.5


# ── render: part/parts -> the two orientation sub-bars ───────────────────────


def test_part_events_reach_the_sub_bar_of_the_orientation_rendering(
        monkeypatch, tmp_path):
    """A part event lands on the bar for the orientation whose render produced
    it and nowhere else. The two orientations are given different part counts
    (16:9 in three parts, 9:16 in two) so a part misrouted to the other bar
    would show up as a fraction the other orientation cannot reach: while 16:9
    renders, 9:16 must read 0.0, and once 16:9 is done, 9:16's events must
    hold 16:9 at 1.0 rather than dropping it back."""
    events: list[dict] = []
    payload_log, _, _ = _drive(monkeypatch, tmp_path, events,
                               transcript=_two_stories(), parts=(3, 2))
    # Bar events carrying a note are the pipeline's own announcements — the
    # 16:9-completion event between a clip's two renders — not part payloads,
    # so the payload-derived events are exactly the noteless ones.
    bars = [e for e in _render_events(events)
            if "note" not in e and _has_bars(e)]
    assert len(bars) == len(payload_log), \
        "one UI bar event per payload the provider sent, no more"
    for evt, payload in zip(bars, payload_log):
        if payload["vertical"]:
            assert _frac(evt, "16:9") == 1.0, \
                "a completed orientation is held at 1.0, not reset"
            assert 0.0 <= _frac(evt, "9:16") <= 1.0
        else:
            assert _frac(evt, "9:16") == 0.0, \
                "a 16:9 part must not move the 9:16 bar"
            assert 0.0 <= _frac(evt, "16:9") <= 1.0
    # The announcement itself: one per clip, and it reports the state the
    # payloads established rather than inventing a new one — 16:9 full, 9:16
    # not started — so it is a state change, not an extra progress tick.
    announcements = [e for e in _render_events(events)
                     if "note" in e and _has_bars(e)
                     and _frac(e, "16:9") == 1.0 and _frac(e, "9:16") == 0.0]
    n_clips = max(e["total"] for e in bars)
    assert len(announcements) == n_clips
    assert all(e["note"].endswith("16:9 rendered") for e in announcements)


def test_both_bars_start_at_their_true_zero_and_never_exceed_one(
        monkeypatch, tmp_path):
    """Each clip opens both bars at 0.0 — including a clip after one that
    finished at 1.0, so a completed clip cannot leak a full bar into the next
    — and no payload, however odd, pushes a fraction past 1.0."""
    events: list[dict] = []
    _drive(monkeypatch, tmp_path, events, transcript=_two_stories())
    render = _render_events(events)
    assert render, "the render stage emitted progress"
    bars = [e for e in render if _has_bars(e)]
    assert bars, "the render stage emitted bar events"
    # A clip opening is the event where both bars read zero; the pipeline's
    # mid-clip announcements (16:9 rendered) are excluded by that definition.
    openings = [e for e in bars
                if _frac(e, "16:9") == 0.0 and _frac(e, "9:16") == 0.0]
    assert len(openings) >= 2, "each clip opens with its own zero event"
    for evt in openings:
        assert _frac(evt, "16:9") == 0.0 and _frac(evt, "9:16") == 0.0, \
            f"a clip's bars start at zero, not at the previous clip's {evt}"
    for evt in bars:
        for label in ("16:9", "9:16"):
            assert 0.0 <= _frac(evt, label) <= 1.0, \
                f"{label} left the unit interval in {evt}"


def test_a_bar_reaches_one_only_when_its_own_render_finished(
        monkeypatch, tmp_path):
    """A fraction of 1.0 is the claim that every part of that orientation's
    render finished. The claim is checked against the payloads the fake render
    actually sent, not against part/parts — which is in range by construction
    and so cannot fail."""
    events: list[dict] = []
    payload_log, _, _ = _drive(monkeypatch, tmp_path, events,
                               transcript=_two_stories(), parts=(3, 2))
    # Noteless bar events are the payload-derived ones; the pipeline's own
    # 16:9-completion announcement carries a note and is excluded below.
    bars = [e for e in _render_events(events)
            if "note" not in e and _has_bars(e)]
    assert len(bars) == len(payload_log), \
        "one bar event per payload, so the zip below is a true pairing"
    pairs = list(zip(bars, payload_log))
    for label, vertical in (("16:9", False), ("9:16", True)):
        reached = [i for i, (evt, _p) in enumerate(pairs)
                   if _frac(evt, label) == 1.0]
        assert reached, f"{label} never reached 1.0"
        first = reached[0]
        evt, payload = pairs[first]
        assert payload["vertical"] is vertical, \
            f"{label} reached 1.0 on a payload from the other orientation: {payload}"
        assert payload["part"] == payload["parts"], \
            f"{label} reached 1.0 before its last part: {payload}"
        # And it is the last payload that orientation sent for this clip: no
        # later payload of the same orientation contradicts a finished bar.
        clip = payload["clip"]
        later = [p for p in payload_log[first + 1:]
                 if p["clip"] == clip and p["vertical"] is vertical]
        assert not later, \
            f"{label} hit 1.0 with parts of its own render still outstanding"


def test_clip_count_is_independent_of_the_part_fractions(
        monkeypatch, tmp_path):
    """The clip counter and the part fractions are two different units: a
    clip's part events move its bars and nothing else, and the counter reaches
    one per clip rendered — never one per part."""
    events: list[dict] = []
    _, render_calls, _ = _drive(monkeypatch, tmp_path, events,
                                transcript=_two_stories(), parts=(3, 2))
    render = _render_events(events)
    totals = {e["total"] for e in render}
    assert totals == {2}, "the total is the number of clips, once per clip"
    # Every event a clip's parts produced still names that same clip.
    for idx in (0, 1):
        own = [e for e in render if e["current"] == idx]
        assert own, f"clip {idx + 1} emitted no progress at all"
        assert all(e["current"] == idx and e["total"] == 2 for e in own)
    # Two orientations per clip, and the counter closes at the clip it just
    # finished — one increment per clip, never one per part, and it reaches
    # the total rather than stopping one short with every clip on disk.
    assert len(render_calls) == 4
    assert max(e["current"] for e in render) == len(render_calls) // 2


def test_a_cached_render_shows_both_bars_full_without_rendering(
        monkeypatch, tmp_path):
    """A clip reused from cache is both bars at 1.0 and no payload at all: the
    file was verified on disk rather than rendered, and that is what full bars
    mean there. The second run reuses the transcript too, so nothing is
    decoded either — no payload anywhere, and no invented fraction."""
    first: list[dict] = []
    _drive(monkeypatch, tmp_path, first, transcript=_two_stories(),
           trust_render_cache=True)
    second: list[dict] = []
    payload_log, render_calls, decodes = _drive(
        monkeypatch, tmp_path, second, transcript=_two_stories(),
        trust_render_cache=True)
    assert not decodes, "a resumed run decodes nothing"
    assert not payload_log and not render_calls, \
        "a cached clip renders nothing, so no provider payload exists"

    cached = [e for e in _render_events(second)
              if "reused from cache" in e.get("note", "")]
    assert len(cached) == 2, "one cached-clip event per selected clip"
    for evt in cached:
        assert _frac(evt, "16:9") == 1.0 and _frac(evt, "9:16") == 1.0
    # The counter still advances one per clip on the cached path.
    assert [e["current"] for e in cached] == [0, 1]
    assert all(e["total"] == 2 for e in cached)


# ── stages that produce nothing ──────────────────────────────────────────────


def test_an_empty_transcript_invents_no_progress(monkeypatch, tmp_path):
    """A transcript with no speech stops the run in the transcription stage.
    No chunk was decoded, so no chunk event exists; no fraction is emitted
    anywhere, and the failure names the stage that actually stopped."""
    events: list[dict] = []
    with pytest.raises(RuntimeError, match="produced no speech"):
        _drive(monkeypatch, tmp_path, events, transcript=[], payloads=())
    assert not _chunk_events(events), \
        "a stage that decoded no chunk must report no chunk progress"
    assert not _render_events(events), \
        "a run that never reached selection must report no render progress"
    assert not any("subprogress" in e for e in events), \
        "no invented fraction may appear on a run that produced nothing"
    fails = [e for e in events if e["type"] == "fail"]
    assert len(fails) == 1 and fails[0]["stage"] == "transcribe"


def test_a_transcript_with_no_selectable_clip_invents_no_render_progress(
        monkeypatch, tmp_path):
    """Speech that yields no clip stops the run at selection. The select stage
    gate never ran over a candidate, so it emits no count, and the render stage
    is never reached: no bar, no fraction, no clip counter."""
    events: list[dict] = []
    degenerate = [Segment(0.0, 3.0, "hello world again",
                          _words("hello world again", 0.0, 3.0))]
    with pytest.raises(RuntimeError, match="No complete standalone stories"):
        _drive(monkeypatch, tmp_path, events, transcript=degenerate)
    assert not _render_events(events), \
        "no render progress may be reported for a run that rendered nothing"
    assert not any("subprogress" in e for e in events)
    assert not [e for e in events
                if e["type"] == "progress" and e["stage"] == "select"], \
        "the gate counted no candidates, so it reports no count"
    fails = [e for e in events if e["type"] == "fail"]
    assert len(fails) == 1 and fails[0]["stage"] == "select"
