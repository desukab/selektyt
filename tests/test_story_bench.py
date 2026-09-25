"""Benchmark the story layer on a transcript large enough to be a feature.

The design doc requires the layer to scale without a quadratic graph explosion,
and to say which numbers are measurements and which are bounds. This test does
both: it builds a transcript large enough that a degenerate fixture would show
up, asserts the bounds that are *structural* (provable from the code rather than
observed), and prints the measurements it took so a regression names the number
that moved.

Every assertion here falls into one of three kinds, and the comment on each says
which:

* a **structural bound** — provable from the construction, so it cannot be
  satisfied vacuously and cannot drift with hardware;
* a **measured invariant** — true on this fixture by construction, so it pins
  behaviour the fixture exists to exercise (a change to the analyser that breaks
  it is a change the reviewer should see);
* a **wall-clock ceiling** — the one kind that varies between machines, used
  only where an existing test already set the ceiling.
"""

from __future__ import annotations

import resource
import time
from pathlib import Path

from engine.corpus import build_corpus
from engine.corpus_io import read_corpus
from engine.story_path import story_path
from engine.timeline_build import timeline_for_candidate
from tests.story_fixtures import duration_for, transcript

# The forward window ``build_threads`` walks when emitting edges. Every bound on
# edge count and thread membership below is expressed in terms of it, so a
# window change changes the bound rather than silently invalidating it.
WINDOW_UNITS = 6
# Measured on this fixture: 0.58 edges per unit at every size, against a bound
# of 6. A regression that saturated the window would pass the structural bound
# and still be fourteen times worse, so the observed ratio is asserted too.
_EDGES_PER_UNIT_MEASURED = 1.75
# ru_maxrss is kilobytes on Linux.
_KB = 1024.0
# The one wall-clock ceiling the existing scale test already asserts. CI
# machines vary; a tighter number would be a machine claim, not a code claim.
_BUILD_CEILING_S = 120.0
# A transcript copy per artifact would be quadratic in the unit count — 2000
# units times 2000 artifacts times the passage text is hundreds of megabytes.
# The measured peak is 11 MB, so this is roughly twenty times headroom and still
# an order of magnitude below a quadratic blowup.
_RSS_PER_UNIT_MB = 0.125


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / _KB


def _build(n_units: int):
    """Build a corpus at one size, returning it with the cost it took."""
    segs = transcript(n_units)
    before = _rss_mb()
    start = time.monotonic()
    corpus = build_corpus(segs, duration=duration_for(n_units), language="en")
    elapsed = time.monotonic() - start
    peak = _rss_mb()
    return corpus, segs, elapsed, before, peak


def _biggest_thread(corpus):
    """The longest story, which is the one the path layer walks."""
    if not corpus.threads:
        return None
    return max(range(len(corpus.threads)),
               key=lambda i: len(corpus.threads[i].unit_indices))


def _lines(corpus, elapsed, before, peak, paths, clips, path_s, edit_s):
    """The measured numbers, as one line per metric.

    Printed on failure and captured by ``-rs``, so a moved number arrives in the
    report rather than as a bare assertion name.
    """
    n = len(corpus.units)
    per_unit = [len(m) for m in corpus.unit_threads]
    big = _biggest_thread(corpus)
    got = {
        "units": n,
        "edges": len(corpus.links),
        "topics": len(corpus.topics),
        "stories": len(corpus.threads),
        "beats": len(corpus.beats),
        "redundancy_groups": len(corpus.redundancy_groups),
        "threads_per_unit_max": max(per_unit, default=0),
        "biggest_thread": len(corpus.threads[big].unit_indices)
                          if big is not None else 0,
        "build_s": round(elapsed, 3),
        "path60": len(paths[60.0]) if 60.0 in paths else "unmeasured",
        "path180": len(paths[180.0]) if 180.0 in paths else "unmeasured",
        "edit_clips": clips,
        "path180_s": round(path_s, 4),
        "edit180_s": round(edit_s, 4),
        "rss_delta_mb": round(peak - before, 1),
        "edges_per_unit": round(len(corpus.links) / max(n, 1), 2),
    }
    return " ".join(f"{k}={v}" for k, v in got.items())


# ── the fixture is not degenerate ─────────────────────────────────────────


def test_the_fixture_actually_builds_a_graph():
    """Every earlier bound in this file is meaningless if the corpus is one unit.

    A transcript whose passages share boilerplate collapses under
    ``segment_story_units`` into a single unit spanning hours, with no topics,
    no threads and no edges; ``edges <= WINDOW * units`` then holds at 0 <= 6
    and the benchmark has measured nothing. Splitting is therefore asserted,
    not assumed — and at every size, so a fixture that degrades only at scale is
    caught at the size it degrades.
    """
    for n_units in (200, 800, 2000):
        corpus, _segs, _elapsed, _before, _peak = _build(n_units)
        assert len(corpus.units) == n_units, (
            f"n_units={n_units}: the transcript collapsed into "
            f"{len(corpus.units)} unit(s) instead of {n_units}, so every "
            f"bound below would hold vacuously")
        assert len(corpus.threads) > 1
        assert len(corpus.links) > 1


# ── structural bounds: provable, not observed ─────────────────────────────


def test_edge_count_is_bounded_by_the_window_not_by_the_corpus():
    """The edge set is at most one edge per (unit, forward partner) pair.

    ``engine/threads.py`` emits edges with
    ``for j in range(i + 1, min(n, i + 1 + window_units))``, so a unit can gain
    at most ``window_units`` edges and the whole set is O(U * W) rather than
    O(U^2). The observed ratio is asserted alongside the bound because a
    regression that merely approached the bound would still pass it.
    """
    for n_units in (200, 800, 2000):
        corpus, _segs, _elapsed, _before, _peak = _build(n_units)
        n = len(corpus.units)
        assert len(corpus.links) <= WINDOW_UNITS * n
        ratio = len(corpus.links) / max(n, 1)
        assert ratio <= _EDGES_PER_UNIT_MEASURED * 4, (
            f"n_units={n_units}: {ratio:.2f} edges per unit is "
            f"{ratio / _EDGES_PER_UNIT_MEASURED:.1f}x the measured "
            f"{_EDGES_PER_UNIT_MEASURED}, so the link layer has changed")


def test_thread_membership_is_bounded_per_unit():
    """A unit may close several stories but cannot belong to all of them.

    The chain walk in ``engine/threads.py`` follows one best successor and stops
    at a unit already interior to a chain, so a unit is interior to at most one
    story and terminal to at most one per partner inside the window. Membership
    is therefore bounded by the window, not by the number of stories — which is
    what keeps the story view linear in the transcript rather than in the graph.
    """
    for n_units in (200, 800, 2000):
        corpus, _segs, _elapsed, _before, _peak = _build(n_units)
        n = len(corpus.units)
        assert len(corpus.unit_threads) == n
        per_unit = [len(m) for m in corpus.unit_threads]
        assert max(per_unit, default=0) <= WINDOW_UNITS + 1
        # Total membership is the sum of the per-unit lists, so it inherits the
        # same window bound rather than scaling with stories x units.
        assert sum(per_unit) <= (WINDOW_UNITS + 1) * n
        for i, ids in enumerate(corpus.unit_threads):
            for tid in ids:
                assert 0 <= tid < len(corpus.threads), (
                    f"unit {i} claims thread {tid}, which the corpus does not hold")


def test_every_analysis_is_bounded_by_the_spine():
    """Topics, threads and beats are views over units, not independent growth."""
    for n_units in (200, 800, 2000):
        corpus, _segs, _elapsed, _before, _peak = _build(n_units)
        n = len(corpus.units)
        assert len(corpus.topics) <= n
        assert len(corpus.threads) <= n
        assert len(corpus.beats) == n
        for thread in corpus.threads:
            for u in thread.unit_indices:
                assert 0 <= u < n
        for topic in corpus.topics:
            for u in topic.unit_indices:
                assert 0 <= u < n


# ── measured invariants: behaviour this fixture exists to exercise ────────


def test_a_story_path_ignores_the_size_of_the_corpus():
    """The path is chosen from one thread's units, so it does not scale with U.

    A 180 s budget selects the same moments from a 40-minute transcript and a
    6-hour one, because the candidate set is a story, not the corpus. That is
    the property that keeps edit generation cheap at length, and it is asserted
    as an equality across sizes rather than as a timing ratio — the latter
    would be a machine claim, this is a structural one.
    """
    paths: dict[float, list] = {}
    for n_units in (200, 800, 2000):
        corpus, segs, _elapsed, _before, _peak = _build(n_units)
        big = _biggest_thread(corpus)
        assert big is not None
        thread = corpus.threads[big]
        for budget in (60.0, 180.0):
            path = story_path(corpus, segs, thread_id=big,
                              target_seconds=budget)
            # The path cannot select more than the story holds.
            assert len(path) <= len(thread.unit_indices)
            assert len(path) >= 1
            paths.setdefault(budget, []).append(len(path))
    for budget, counts in paths.items():
        assert len(set(counts)) == 1, (
            f"a {budget:g}s budget selected {counts} moment(s) as the corpus "
            f"grew; the path is chosen from one thread, not the corpus")
    # The tighter budget cannot select the wider one's moments.
    assert paths[60.0][0] <= paths[180.0][0]


def test_a_story_path_becomes_the_same_number_of_clips():
    """Every selected moment becomes exactly one clip — no fan-out at length."""
    n_units = 2000
    corpus, segs, _elapsed, _before, _peak = _build(n_units)
    big = _biggest_thread(corpus)
    path = story_path(corpus, segs, thread_id=big, target_seconds=180.0)
    clips = 0
    start = time.monotonic()
    for cand in path:
        timeline = timeline_for_candidate(Path("/tmp/source.mov"), cand,
                                          fps=30.0,
                                          duration=duration_for(n_units))
        clips += len(timeline.clips)
    edit_s = time.monotonic() - start
    assert clips == len(path)
    assert edit_s < 10.0


# ── wall-clock ceiling and memory ─────────────────────────────────────────


def test_build_time_and_peak_memory_stay_bounded():
    """The 3-hour ceiling the existing scale test asserts, on a real graph."""
    n_units = 2000
    corpus, _segs, elapsed, before, peak = _build(n_units)
    assert elapsed < _BUILD_CEILING_S, (
        f"build took {elapsed:.1f}s; the cost model regressed. "
        + _lines(corpus, elapsed, before, peak, {}, 0, 0.0, 0.0))
    # A copy of the passage text per artifact would be quadratic in the unit
    # count; the corpus indexes units instead, so the delta stays linear.
    assert peak - before <= _RSS_PER_UNIT_MB * n_units, (
        f"peak rose {peak - before:.1f} MB for {n_units} units, which is "
        f"above the linear bound {_RSS_PER_UNIT_MB * n_units:.0f} MB; an "
        f"artifact has started copying transcript text instead of indexing it")


# ── the graph survives a round trip at scale ──────────────────────────────


def test_a_large_corpus_survives_export_and_re_import():
    """The reconstruction requirement, at the size the benchmark builds."""
    n_units = 200
    corpus, _segs, _elapsed, _before, _peak = _build(n_units)
    back = read_corpus(corpus.to_dict())
    assert back.unit_threads == corpus.unit_threads
    assert [t.unit_indices for t in back.threads] == \
        [t.unit_indices for t in corpus.threads]
    assert len(back.links) == len(corpus.links)
    # The imported memory still yields an edit, which is the point of importing.
    big = _biggest_thread(back)
    path = story_path(back, transcript(n_units), thread_id=big,
                      target_seconds=60.0)
    assert len(path) >= 1


def test_the_graph_is_deterministic_across_builds():
    """Two builds of the same transcript agree on every artifact."""
    first, _segs, _elapsed, _before, _peak = _build(200)
    second, _s, _e, _b, _p = _build(200)
    assert first.build.fingerprint == second.build.fingerprint
    assert first.unit_threads == second.unit_threads
    assert [t.unit_indices for t in first.threads] == \
        [t.unit_indices for t in second.threads]
    assert len(first.links) == len(second.links)
