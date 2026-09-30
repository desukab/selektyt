"""Presentation-layer tests: what the CLI shows, and that it never invents a number.

The contract these hold is the one the product depends on rather than the one
that is convenient to test: every metric on screen is read from a value the
pipeline actually produced, no progress fraction moves without work behind it,
and a stream that is not a terminal gets static text instead of escape codes.

The pipeline itself is stubbed here — these are the rendering contracts, not
the engine's, and a real end-to-end run belongs to tests/test_pipeline_e2e.py.
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

from selekyt import cli as cli_mod
from selekyt.ui import (
    AMBER,
    STAGE_LABELS,
    ProgressRenderer,
    Terminal,
    complete_screen,
    fail_screen,
    start_screen,
)

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class FakeTTY(io.StringIO):
    """A buffer that claims to be a terminal, to exercise the live path."""

    def isatty(self) -> bool:
        return True


class FakePipe(io.StringIO):
    """A buffer that is honestly not a terminal, as a redirected stream is."""

    def isatty(self) -> bool:
        return False


def _plain(text: str) -> str:
    return ANSI.sub("", text)


def _frame(renderer: ProgressRenderer) -> str:
    """The last complete frame the renderer wrote, with colour stripped."""
    err = renderer.term.err
    parts = re.split(r"\x1b\[\d*[AJ]\x1b\[J", err.getvalue())
    return _plain(parts[-1])


# ── capability detection ─────────────────────────────────────────────────────


def test_color_is_off_when_the_stream_is_not_a_terminal():
    term = Terminal(out=FakePipe(), err=FakePipe())
    assert term.color is False
    assert term.interactive is False


def test_no_color_flag_drops_the_palette_but_keeps_the_panel():
    """--no-color is about colour, so a terminal still gets its live panel."""
    term = Terminal(no_color=True, out=FakeTTY(), err=FakeTTY())
    assert term.color is False
    assert term.interactive is True


def test_json_mode_never_becomes_interactive():
    """--json is the machine-readable channel; the panel would corrupt it."""
    term = Terminal(json_mode=True, out=FakeTTY(), err=FakeTTY())
    assert term.interactive is False


def test_quiet_mode_never_becomes_interactive():
    term = Terminal(quiet=True, out=FakeTTY(), err=FakeTTY())
    assert term.interactive is False


def test_color_is_used_on_both_a_terminal_and_with_force(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    term = Terminal(out=FakePipe(), err=FakePipe())
    assert term.color is True
    assert term.interactive is False  # forcing colour does not force animation


def test_no_color_env_var_wins_over_force_color(monkeypatch):
    """NO_COLOR is the de-facto standard and takes precedence."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    assert term.color is False


def test_the_force_argument_enables_colour_without_the_environment(monkeypatch):
    """``force`` is the programmatic override of the FORCE_COLOR lookup."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    term = Terminal(out=FakePipe(), err=FakePipe(), force=True)
    assert term.color is True
    # Forcing colour still does not force animation: a pipe is a pipe.
    assert term.interactive is False


def test_the_force_argument_can_also_disable_colour_the_env_set(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    term = Terminal(out=FakeTTY(), err=FakeTTY(), force=False)
    assert term.color is False


def test_no_color_still_wins_over_an_explicit_force_argument():
    term = Terminal(no_color=True, out=FakeTTY(), err=FakeTTY(), force=True)
    assert term.color is False


def test_width_is_clamped_to_a_readable_range(monkeypatch):
    import shutil
    from collections import namedtuple

    monkeypatch.delenv("COLUMNS", raising=False)
    Size = namedtuple("Size", "columns lines")
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a, **k: Size(20, 24))
    assert Terminal(out=FakeTTY(), err=FakeTTY()).width == 40
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a, **k: Size(400, 24))
    assert Terminal(out=FakeTTY(), err=FakeTTY()).width == 100


def test_the_panel_measures_the_terminal_it_was_built_on(tmp_path):
    """``cli.main`` repoints fds 1 and 2 at the run log while the engine runs,
    because that is how ffmpeg's banner and onnxruntime's C++ warnings stay off
    the terminal. ``shutil.get_terminal_size`` reads fd 1, so a panel that
    re-measured mid-run would read a regular file, fail, and fall back to 80
    columns — laying itself out for a terminal it is not on while the user's is
    44 wide. Measurement has to answer to the terminal the panel draws to."""
    import fcntl
    import os
    import pty
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 22, 44, 0, 0))
    stream = os.fdopen(slave, "w", closefd=False)
    try:
        term = Terminal(out=stream, err=stream)
        assert term.width == 44 and term.height == 22
        # fd 1 becomes the run log — the redirect the CLI applies for the whole
        # engine run. The panel's own geometry must not move with it.
        saved = os.dup(1)
        try:
            log = open(tmp_path / "run.log", "w")
            os.dup2(log.fileno(), 1)
            term.refresh_size()
            assert term.width == 44, "the panel fell back to the 80-column default"
            # A resize still lands, because winsize is a property of the pty and
            # the retained descriptor still names it.
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 22, 70, 0, 0))
            term.refresh_size()
            assert term.width == 70
        finally:
            os.dup2(saved, 1)
            os.close(saved)
            log.close()
            term.close()
    finally:
        stream.close()
        os.close(master)


def test_a_stream_that_is_not_a_terminal_is_measured_from_the_streams():
    """A redirected channel or a test double has no terminal to retain, so
    measurement falls back to ``shutil`` exactly as it did before the retained
    descriptor existed."""
    import io
    import os

    term = Terminal(out=io.StringIO(), err=io.StringIO())
    assert term._size_fd is None
    assert term.width in range(40, 101)
    term.close()


# ── non-interactive output: no escape codes, no animation ────────────────────


def test_non_interactive_output_carries_no_escape_codes():
    err = FakePipe()
    term = Terminal(out=FakePipe(), err=err)
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "transcribe", "state": "start"})
    renderer.event({"type": "progress", "stage": "transcribe", "current": 3, "total": 10})
    renderer.event({"type": "stage", "stage": "transcribe", "state": "done",
                    "duration": 12.5, "detail": {"segments": 40}})
    renderer.close()
    assert "\x1b" not in err.getvalue()
    assert "Transcription" in err.getvalue()
    assert "segments" in err.getvalue()


def test_non_interactive_output_is_append_only():
    """A redirected stream gets one line per stage, never a repaint."""
    err = FakePipe()
    term = Terminal(out=FakePipe(), err=err)
    renderer = ProgressRenderer(term)
    for i in range(5):
        renderer.event({"type": "stage", "stage": "transcribe", "state": "start"})
        renderer.event({"type": "progress", "stage": "transcribe", "current": i, "total": 5})
    lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
    # The start line once; no cursor control means no repaint of any stage.
    assert sum("Transcription\u2026" in ln for ln in lines) == 1


def test_quiet_mode_prints_no_stage_panel():
    err = FakePipe()
    term = Terminal(quiet=True, out=FakePipe(), err=err)
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "input", "state": "start"})
    renderer.close()
    assert err.getvalue() == ""


# ── live panel ───────────────────────────────────────────────────────────────


def test_active_stage_shows_measured_counts_not_a_fake_percent():
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "transcribe", "state": "start"})
    renderer.event({"type": "progress", "stage": "transcribe", "current": 3, "total": 10})
    assert "3/10" in _frame(renderer)


def test_a_stage_with_no_progress_shows_only_the_clock():
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "input", "state": "start"})
    # No progress event, so the only thing that can move is elapsed time. A
    # percentage here would be a number invented for the display.
    frame = _frame(renderer)
    assert "%" not in frame
    assert "/" not in frame


def test_done_stage_reports_its_detail_and_duration():
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "select", "state": "start"})
    renderer.event({"type": "stage", "stage": "select", "state": "done",
                    "duration": 4.0,
                    "detail": {"candidates": 38, "selected": 6}})
    frame = _frame(renderer)
    assert "✓" in frame
    assert "candidates" in frame and "38" in frame
    assert "selected" in frame and "6" in frame


def test_a_stage_that_never_ran_is_never_shown():
    """``audio`` is opt-in, so a run without it has no audio row at all."""
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "input", "state": "done",
                    "duration": 1.0, "detail": {}})
    assert "Audio" not in _frame(renderer)


def test_unknown_event_does_not_break_the_renderer():
    """The pipeline evolves; a cosmetic layer must not crash on a new field."""
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "something-not-yet-invented", "stage": "input"})
    renderer.close()  # reaches close without raising


def test_subprogress_bars_are_drawn_per_orientation():
    err = FakeTTY()
    term = Terminal(out=FakeTTY(), err=err)
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "render", "state": "start"})
    renderer.event({"type": "progress", "stage": "render", "current": 1, "total": 6,
                    "subprogress": [{"label": "16:9", "fraction": 1.0},
                                    {"label": "9:16", "fraction": 0.5}]})
    frame = _frame(renderer)
    assert "16:9" in frame and "9:16" in frame
    assert "100%" in frame and "50%" in frame


def test_render_part_fraction_below_one_still_draws_a_partial_bar():
    err = FakeTTY()
    term = Terminal(out=FakeTTY(), err=err)
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "render", "state": "start"})
    renderer.event({"type": "progress", "stage": "render", "current": 0, "total": 4,
                    "subprogress": [{"label": "16:9", "fraction": 1 / 3},
                                    {"label": "9:16", "fraction": 0.0}]})
    frame = _frame(renderer)
    # A third of the bar is filled, not rounded away to empty and not inflated.
    assert "33%" in frame
    assert " 0%" in frame


def test_close_erases_the_live_panel():
    err = FakeTTY()
    term = Terminal(out=FakeTTY(), err=err)
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "input", "state": "start"})
    renderer.close()
    # The final frame after close is empty: nothing is left above the cursor.
    assert _frame(renderer) == ""


def test_fail_event_is_remembered_for_the_failure_screen():
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "transcribe", "state": "start"})
    try:
        raise RuntimeError("the source has no audible spoken content")
    except RuntimeError as exc:
        renderer.event({"type": "fail", "stage": "transcribe", "reason": str(exc)})
    assert renderer.failed_stage == "transcribe"
    assert "no audible spoken content" in (renderer.failed_reason or "")


# ── malformed payloads: degrade, never raise ────────────────────────────────


def test_a_non_mapping_detail_is_dropped_not_raised():
    """``detail or {}`` in the event handler lets any truthy non-mapping
    through, so the renderer must treat a wrong-shaped detail as no detail
    rather than crash — the same tolerance it already gives an unknown event
    type."""
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "transcribe", "state": "done",
                    "duration": 1.0, "detail": "a truthy non-mapping"})
    frame = _frame(renderer)
    assert "✓" in frame and "Transcription" in frame
    assert "non-mapping" not in frame
    renderer.close()


def test_a_non_mapping_detail_is_dropped_on_the_static_path_too():
    err = FakePipe()
    term = Terminal(out=FakePipe(), err=err)
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "transcribe", "state": "done",
                    "duration": 1.0, "detail": ["not", "a", "mapping"]})
    renderer.close()
    assert "Transcription" in err.getvalue()
    assert "mapping" not in err.getvalue()


def test_a_non_mapping_subprogress_entry_is_skipped_not_raised():
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "render", "state": "start"})
    renderer.event({"type": "progress", "stage": "render", "current": 1, "total": 6,
                    "subprogress": ["16:9", {"label": "9:16", "fraction": 0.5}]})
    frame = _frame(renderer)
    # The well-formed unit still renders; the malformed one renders nothing.
    assert "9:16" in frame and "50%" in frame
    assert "16:9" not in frame
    renderer.close()


def test_a_non_numeric_percent_is_not_rendered_as_one():
    """percent: "50" is not a percentage; the measured count still shows."""
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "transcribe", "state": "start"})
    renderer.event({"type": "progress", "stage": "transcribe", "current": 3, "total": 10,
                    "percent": "50"})
    frame = _frame(renderer)
    assert "3/10" in frame
    assert "50%" not in frame
    renderer.close()


def test_a_non_numeric_percent_draws_no_bar_either():
    """A percent unit whose number is unusable renders no bar and no
    percentage; the stage shows its clock instead of a placeholder."""
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "transcribe", "state": "start"})
    renderer.event({"type": "progress", "stage": "transcribe", "percent": "75",
                    "label": "chunks"})
    frame = _frame(renderer)
    assert "Transcription" in frame
    assert "░" not in frame and "█" not in frame
    assert "%" not in frame
    renderer.close()


def test_a_non_numeric_fraction_draws_no_bar_and_no_percentage():
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "render", "state": "start"})
    renderer.event({"type": "progress", "stage": "render", "current": 1, "total": 6,
                    "subprogress": [{"label": "16:9", "fraction": "0.5"},
                                    {"label": "9:16", "fraction": 0.5}]})
    frame = _frame(renderer)
    assert "16:9" in frame and "9:16" in frame
    assert frame.count("50%") == 1
    renderer.close()


def test_an_unmeasured_subprogress_unit_draws_no_bar():
    """``fraction: null`` means no measurement exists. The percentage text was
    already suppressed, but a row of empty track is an implied 0% — the number
    the pipeline did not report."""
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "render", "state": "start"})
    renderer.event({"type": "progress", "stage": "render", "current": 1, "total": 6,
                    "subprogress": [{"label": "16:9", "fraction": None}]})
    frame = _frame(renderer)
    assert "16:9" in frame
    assert "░" not in frame and "█" not in frame
    assert "%" not in frame
    renderer.close()


def test_a_none_detail_value_prints_nothing_not_the_word_none():
    """Reachable on a resumed run whose cached transcript provenance carries an
    ``asr`` without a ``model`` key."""
    for cls in (FakeTTY, FakePipe):
        err = cls()
        term = Terminal(out=cls(), err=err)
        renderer = ProgressRenderer(term)
        renderer.event({"type": "stage", "stage": "transcribe", "state": "done",
                        "duration": 1.0, "detail": {"model": None, "segments": 40}})
        renderer.close()
        plain = _plain(err.getvalue())
        assert "None" not in plain, cls
        assert "segments" in plain and "40" in plain, cls


def test_events_before_the_first_stage_write_nothing():
    """A progress event with no stage attached must render no bare newline:
    that scrolled one blank line per second from construction until the first
    stage event, and nothing ever erased it."""
    err = FakeTTY()
    term = Terminal(out=FakeTTY(), err=err)
    renderer = ProgressRenderer(term)
    renderer.event({"type": "progress", "current": 1, "total": 2})
    renderer.event({"type": "progress", "current": 2, "total": 2})
    renderer.close()
    assert err.getvalue() == ""


def test_a_non_numeric_duration_does_not_break_the_done_row():
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "input", "state": "done",
                    "duration": "not-a-number", "detail": {}})
    assert "Input" in _frame(renderer)
    renderer.close()


# ── the panel is bounded by the screen ───────────────────────────────────────


def test_every_panel_row_fits_the_terminal_width(monkeypatch):
    """A row wider than the terminal wraps, so the logical-line count desyncs
    from the screen rows and every later repaint erases too few of them."""
    import shutil
    from collections import namedtuple

    monkeypatch.delenv("COLUMNS", raising=False)
    Size = namedtuple("Size", "columns lines")
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a, **k: Size(40, 24))
    err = FakeTTY()
    term = Terminal(out=FakeTTY(), err=err)
    renderer = ProgressRenderer(term)
    long_stage = "a-stage-id-no-label-knows-and-quite-long"
    renderer.event({"type": "stage", "stage": "input", "state": "done", "duration": 1.0,
                    "detail": {"source": "a-detail-value-wider-than-the-whole-panel.mp4"}})
    renderer.event({"type": "stage", "stage": "transcribe", "state": "start"})
    renderer.event({"type": "stage", "stage": long_stage, "state": "start"})
    renderer.event({"type": "progress", "stage": long_stage, "current": 1, "total": 4,
                    "note": "a note far longer than forty columns is cut, not wrapped",
                    "subprogress": [{"label": "a-label-longer-than-the-label-column",
                                     "fraction": 0.5}]})
    frame = _frame(renderer)
    rows = [ln for ln in frame.splitlines() if ln.strip()]
    assert rows
    assert all(len(row) <= term.width for row in rows), rows
    # The erase count is the screen-row count, so no stale rows survive a repaint.
    assert renderer.lines_drawn == len(rows)
    renderer.close()


def test_the_panel_never_exceeds_the_terminal_height(monkeypatch):
    """A full run is eight stages of up to five rows; once that exceeded the
    screen, repaints scrolled the terminal and close() could not erase the
    top of the frame."""
    import shutil
    from collections import namedtuple

    monkeypatch.delenv("COLUMNS", raising=False)
    Size = namedtuple("Size", "columns lines")
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a, **k: Size(80, 8))
    err = FakeTTY()
    term = Terminal(out=FakeTTY(), err=err)
    renderer = ProgressRenderer(term)
    assert term.height == 8
    for name in ["input", "transcribe", "verify", "select", "corpus", "audio",
                 "render", "report"]:
        renderer.event({"type": "stage", "stage": name, "state": "done",
                        "duration": 1.0, "detail": {"a": 1, "b": 2, "c": 3}})
    frame = _frame(renderer)
    rows = [ln for ln in frame.splitlines() if ln.strip()]
    assert len(rows) == renderer.lines_drawn <= term.height
    # The whole-stage blocks nearest the cursor are kept — the run's own tail —
    # and the finished stages at the top are the ones that scroll away.
    assert "Package" in frame
    assert "Input" not in frame
    renderer.close()


def test_a_resize_mid_run_re_lays_out_the_next_frame(monkeypatch):
    """``Terminal._width`` was measured once at construction, so a user who
    resized the window mid-run got bars sized for a geometry that no longer
    existed. The next frame has to pick up the new width, clamped the same way
    ``_width`` clamps it."""
    import shutil
    from collections import namedtuple

    monkeypatch.delenv("COLUMNS", raising=False)
    Size = namedtuple("Size", "columns lines")
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a, **k: Size(40, 24))
    err = FakeTTY()
    term = Terminal(out=FakeTTY(), err=err)
    renderer = ProgressRenderer(term)
    assert term.width == 40
    renderer.event({"type": "stage", "stage": "render", "state": "start"})
    renderer.event({"type": "progress", "stage": "render", "current": 1, "total": 6,
                    "subprogress": [{"label": "16:9", "fraction": 0.5}]})
    narrow = _frame(renderer)
    narrow_bar = [ln for ln in narrow.splitlines() if "16:9" in ln][0]
    narrow_run = sum(ch == "█" or ch == "░" for ch in narrow_bar)

    # Same renderer, same Terminal: only the window moved.
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a, **k: Size(100, 24))
    renderer.event({"type": "progress", "stage": "render", "current": 2, "total": 6,
                    "subprogress": [{"label": "16:9", "fraction": 0.5}]})
    wide = _frame(renderer)
    wide_bar = [ln for ln in wide.splitlines() if "16:9" in ln][0]
    wide_run = sum(ch == "█" or ch == "░" for ch in wide_bar)

    assert term.width == 100  # refreshed, not the construction-time 40
    assert wide_run > narrow_run
    # The clamp still applies in both directions: a 12-column resize cannot make
    # the layout narrower than the floor, nor a 400-column one uncapped.
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a, **k: Size(12, 24))
    renderer._render()
    assert term.width == 40
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a, **k: Size(400, 24))
    renderer._render()
    assert term.width == 100
    renderer.close()


# ── notes: real warnings, actually shown ─────────────────────────────────────


def test_warn_notes_appear_on_the_live_panel():
    err = FakeTTY()
    term = Terminal(out=FakeTTY(), err=err)
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "corpus", "state": "start"})
    renderer.event({"type": "note", "level": "warn",
                    "text": "corpus analysis unavailable: no decoder"})
    frame = _frame(renderer)
    assert "corpus analysis unavailable" in frame
    assert AMBER in err.getvalue()  # a warning, in the palette's warning colour
    # A note the pipeline never sent is not invented.
    assert "video memory unavailable" not in frame
    renderer.close()


def test_an_unlevelled_note_is_still_shown():
    """The clip-reused notice carries no level, and it is a real notice."""
    term = Terminal(out=FakeTTY(), err=FakeTTY())
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "render", "state": "start"})
    renderer.event({"type": "note", "text": "clip 01 reused from cache"})
    assert "clip 01 reused from cache" in _frame(renderer)
    renderer.close()


def test_notes_appear_in_the_static_stream_without_escape_codes():
    err = FakePipe()
    term = Terminal(out=FakePipe(), err=err)
    renderer = ProgressRenderer(term)
    renderer.event({"type": "stage", "stage": "render", "state": "start"})
    renderer.event({"type": "note", "level": "warn", "text": "clip 01 reused from cache"})
    renderer.close()
    assert "clip 01 reused from cache" in err.getvalue()
    assert "\x1b" not in err.getvalue()


def test_quiet_mode_prints_no_notes():
    err = FakePipe()
    term = Terminal(quiet=True, out=FakePipe(), err=err)
    renderer = ProgressRenderer(term)
    renderer.event({"type": "note", "level": "warn", "text": "corpus analysis unavailable"})
    renderer.close()
    assert err.getvalue() == ""


# ── static screens ───────────────────────────────────────────────────────────


def test_start_screen_names_the_source_and_the_package():
    err = FakeTTY()
    term = Terminal(out=FakeTTY(), err=err)
    start_screen(term, source="video.mp4", model="small", language=None,
                 out="out/pkg", width=1920, height=1080)
    plain = _plain(err.getvalue())
    assert "S E L E K Y T" in plain
    assert "video.mp4" in plain
    assert "1920x1080" in plain
    assert "out/pkg" in plain


def test_start_screen_truncates_a_long_source_at_narrow_widths(monkeypatch):
    import shutil
    from collections import namedtuple

    monkeypatch.delenv("COLUMNS", raising=False)
    Size = namedtuple("Size", "columns lines")
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a, **k: Size(40, 24))
    err = FakeTTY()
    term = Terminal(out=FakeTTY(), err=err)
    long = "a-source-name-far-longer-than-the-terminal-is-wide.mp4"
    start_screen(term, source=long, model="small", language="te",
                 out="out/pkg", width=None, height=None)
    for line in err.getvalue().splitlines():
        assert len(_plain(line)) <= term.width or _plain(line).strip() == ""


def test_start_screen_is_suppressed_in_quiet_and_json_modes():
    for flags in ({"quiet": True}, {"json_mode": True}):
        err = FakeTTY()
        term = Terminal(out=FakeTTY(), err=err, **flags)
        start_screen(term, source="v.mp4", model="small", language=None,
                     out="out", width=None, height=None)
        assert err.getvalue() == "", flags


def test_complete_screen_reports_only_artifacts_that_exist(tmp_path):
    out = FakeTTY()
    term = Terminal(out=out, err=FakePipe())
    (tmp_path / "facebook").mkdir()
    (tmp_path / "facebook" / "01.mp4").write_bytes(b"x")
    report = {
        "title": "How a rolex is made",
        "coverage": {"duration_s": 600.0},
        "outputs": {"facebook": str(tmp_path / "facebook"), "shorts": str(tmp_path / "shorts")},
        "captions": str(tmp_path / "captions"),
        "selected_count": 6,
        "candidate_count": 38,
    }
    complete_screen(term, report=report, project=str(tmp_path),
                    runtime=90.0, artifacts=1)
    plain = _plain(out.getvalue())
    assert "SELEKYT COMPLETE" in plain
    assert "How a rolex is made" in plain
    assert "6 selected" in plain and "38 candidates scored" in plain
    # A family the run produced and one it did not are both named honestly.
    assert "✓" in plain
    assert "absent" in plain


def test_complete_screen_reports_an_unknown_duration_as_unknown():
    out = FakeTTY()
    term = Terminal(out=out, err=FakePipe())
    complete_screen(term, report={"coverage": {}}, project="out",
                    runtime=5.0, artifacts=0)
    plain = _plain(out.getvalue())
    assert "0:05" in plain
    # No duration was measured, so no duration is printed as one.
    assert "source" in plain


def test_fail_screen_names_the_stage_the_reason_and_the_log(tmp_path):
    err = FakeTTY()
    term = Terminal(out=FakePipe(), err=err)
    log = tmp_path / "run.log"
    log.write_text("detail", encoding="utf-8")
    fail_screen(term, stage="transcribe",
                reason="the source has no audible spoken content",
                remediation="re-mux the file by hand",
                log_path=str(log))
    plain = _plain(err.getvalue())
    assert "✕ TRANSCRIPTION FAILED" in plain
    assert "the source has no audible spoken content" in plain
    assert "re-mux the file by hand" in plain
    assert str(log) in plain


def test_fail_screen_never_wraps_the_reason():
    """tools/corpus_run.py greps phrases out of this line to classify a run.

    Wrapping inserts a newline inside the phrase at narrow widths, which turns
    a classified failure into an unclassified one; the terminal wraps visually
    either way, so nothing is lost on screen.
    """
    err = FakeTTY()
    term = Terminal(out=FakePipe(), err=err)
    reason = (
        "Transcription produced no speech: the source has no audible spoken "
        "content. the extracted audio track is digital silence, so a silent "
        "or music-only source has nothing to select."
    )
    fail_screen(term, stage="transcribe", reason=reason,
                remediation=None, log_path=None)
    plain = _plain(err.getvalue())
    line = next(ln for ln in plain.splitlines() if "audible" in ln)
    assert "no audible spoken content" in line
    assert "digital silence" in line


def test_fail_screen_works_without_remediation_or_log():
    err = FakeTTY()
    term = Terminal(out=FakePipe(), err=err)
    fail_screen(term, stage=None, reason="something broke",
                remediation=None, log_path=None)
    plain = _plain(err.getvalue())
    assert "✕ RUN FAILED" in plain
    assert "Next" not in plain
    assert "Details" not in plain


# ── CLI end to end, with the pipeline stubbed ────────────────────────────────


@pytest.fixture
def stubbed(monkeypatch):
    """Patch the pipeline with a fake that can emit a scripted event stream."""
    events: list[dict] = []
    project: list[Path] = []

    def fake_run(*args, **kwargs):
        sink = kwargs.get("progress")
        for evt in events:
            if sink is not None:
                sink(evt)
        return project[0] if project else None

    monkeypatch.setattr(cli_mod, "run", fake_run)
    return {"events": events, "project": project}


def _run_cli(monkeypatch, tmp_path, argv):
    monkeypatch.setattr(sys, "argv", ["selekyt", *argv])
    return monkeypatch


def test_a_successful_run_renders_the_start_and_complete_screens(
    monkeypatch, tmp_path, stubbed,
):
    stubbed["project"].append(tmp_path)
    (tmp_path / "facebook").mkdir()
    (tmp_path / "report.json").write_text(json.dumps({
        "title": "How a rolex is made",
        "coverage": {"duration_s": 600.0},
        "outputs": {"facebook": str(tmp_path / "facebook")},
        "selected_count": 4,
    }), encoding="utf-8")
    out, err = FakeTTY(), FakeTTY()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _run_cli(monkeypatch, tmp_path, [str(tmp_path / "src.mp4"), "--out", str(tmp_path)])
    cli_mod.main()
    plain_out = _plain(out.getvalue())
    assert "S E L E K Y T" in _plain(err.getvalue())
    assert "SELEKYT COMPLETE" in plain_out
    assert str(tmp_path) in plain_out
    assert "4 selected" in plain_out


def test_json_mode_prints_only_the_report_on_stdout(
    monkeypatch, tmp_path, stubbed,
):
    stubbed["project"].append(tmp_path)
    report = {"selected_count": 4, "audio": {"enabled": False}}
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    out, err = FakePipe(), FakePipe()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _run_cli(monkeypatch, tmp_path, [str(tmp_path / "src.mp4"), "--out", str(tmp_path), "--json"])
    cli_mod.main()
    printed = json.loads(out.getvalue())
    assert printed == report
    assert "\x1b" not in out.getvalue()


def test_engine_stdout_is_logged_and_stays_off_the_report_channel(
    monkeypatch, tmp_path, stubbed,
):
    """The engine's own prints go to run.log, never to --json's stdout."""
    stubbed["project"].append(tmp_path)
    (tmp_path / "report.json").write_text(json.dumps({"selected_count": 1}), encoding="utf-8")

    def fake_run(*args, **kwargs):
        print("[transcribe] chunk 1/2: engine chatter that must not reach stdout")
        result = cli_mod.run.__wrapped__ if hasattr(cli_mod.run, "__wrapped__") else None
        sink = kwargs.get("progress")
        if sink is not None:
            sink({"type": "stage", "stage": "transcribe", "state": "start"})
        return tmp_path

    monkeypatch.setattr(cli_mod, "run", fake_run)
    out = FakePipe()
    monkeypatch.setattr(sys, "stdout", out)
    _run_cli(monkeypatch, tmp_path, [str(tmp_path / "src.mp4"), "--out", str(tmp_path), "--json"])
    cli_mod.main()
    assert "engine chatter" not in out.getvalue()
    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "engine chatter" in log
    assert "--- selekyt run" in log


def test_a_failed_run_renders_the_failure_screen_and_exits_nonzero(
    monkeypatch, tmp_path,
):
    def boom(*args, **kwargs):
        sink = kwargs.get("progress")
        if sink is not None:
            sink({"type": "stage", "stage": "transcribe", "state": "start"})
            sink({"type": "fail", "stage": "transcribe",
                  "reason": "the source has no audible spoken content"})
        raise RuntimeError("the source has no audible spoken content")

    monkeypatch.setattr(cli_mod, "run", boom)
    out, err = FakeTTY(), FakeTTY()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _run_cli(monkeypatch, tmp_path, [str(tmp_path / "src.mp4"), "--out", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    plain = _plain(err.getvalue())
    assert "✕ TRANSCRIPTION FAILED" in plain
    assert "no audible spoken content" in plain


def test_no_color_flag_suppresses_every_colour_code(monkeypatch, tmp_path, stubbed):
    """--no-color drops the palette; the live panel itself is not the thing it
    disables, so a terminal still animates — monochrome."""

    class FakeTTYNoColor(FakeTTY):
        pass

    stubbed["project"].append(tmp_path)
    (tmp_path / "report.json").write_text(json.dumps({"selected_count": 1}), encoding="utf-8")
    out, err = FakeTTYNoColor(), FakeTTYNoColor()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setenv("FORCE_COLOR", "1")
    _run_cli(monkeypatch, tmp_path, [
        str(tmp_path / "src.mp4"), "--out", str(tmp_path), "--no-color"])
    cli_mod.main()
    sgr = re.compile(r"\x1b\[[0-9;]*m")
    assert not sgr.search(out.getvalue())
    assert not sgr.search(err.getvalue())


def test_quiet_mode_prints_no_screens(monkeypatch, tmp_path, stubbed):
    stubbed["project"].append(tmp_path)
    (tmp_path / "report.json").write_text(json.dumps({"selected_count": 1}), encoding="utf-8")
    out, err = FakeTTY(), FakeTTY()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _run_cli(monkeypatch, tmp_path, [str(tmp_path / "src.mp4"), "--out", str(tmp_path), "--quiet"])
    cli_mod.main()
    assert err.getvalue() == ""
    assert _plain(out.getvalue()) == ""


def test_the_output_path_is_reported(monkeypatch, tmp_path, stubbed):
    stubbed["project"].append(tmp_path)
    (tmp_path / "report.json").write_text(json.dumps({"selected_count": 1}), encoding="utf-8")
    out = FakeTTY()
    monkeypatch.setattr(sys, "stdout", out)
    _run_cli(monkeypatch, tmp_path, [str(tmp_path / "src.mp4"), "--out", str(tmp_path)])
    cli_mod.main()
    assert str(tmp_path) in _plain(out.getvalue())


def test_verbose_mode_does_not_redirect_engine_stdout(monkeypatch, tmp_path):
    """--verbose puts the engine's own output on the terminal, so no run.log."""
    def fake_run(*args, **kwargs):
        print("[transcribe] chunk 1/2: diagnostics the user asked to see")
        return tmp_path

    monkeypatch.setattr(cli_mod, "run", fake_run)
    out, err = FakeTTY(), FakeTTY()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _run_cli(monkeypatch, tmp_path, [str(tmp_path / "src.mp4"), "--out", str(tmp_path), "--verbose"])
    cli_mod.main()
    assert "diagnostics the user asked to see" in out.getvalue()
    assert (tmp_path / "run.log").is_file() is False


def test_remediiation_is_attached_to_the_failure_it_names():
    remediate = cli_mod._remediation
    no_speech = "the source has no audible spoken content"
    assert remediate(no_speech) is not None
    assert "speech track" in remediate(no_speech)
    assert remediate("Local video not found: x.mp4") is not None
    assert remediate("an unprecedented failure mode") is None


def test_the_cli_drives_the_panel_from_the_pipeline_event_stream(
    monkeypatch, tmp_path, stubbed,
):
    """``progress=renderer.event`` is the binding under test. The stubbed run's
    events have to reach the live panel and land the counts the pipeline
    measured, not the ones the display chose."""
    stubbed["project"].append(tmp_path)
    (tmp_path / "report.json").write_text(json.dumps({"selected_count": 1}), encoding="utf-8")
    stubbed["events"].extend([
        {"type": "stage", "stage": "input", "state": "start"},
        {"type": "stage", "stage": "input", "state": "done",
         "duration": 1.0, "detail": {"source": "src.mp4"}},
        {"type": "stage", "stage": "transcribe", "state": "start"},
        {"type": "progress", "stage": "transcribe", "current": 3, "total": 10},
    ])
    out, err = FakeTTY(), FakeTTY()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _run_cli(monkeypatch, tmp_path, [str(tmp_path / "src.mp4"), "--out", str(tmp_path)])
    cli_mod.main()
    plain_err = _plain(err.getvalue())
    # close() erases the panel from the screen, but the frames it drew are still
    # in the buffer — which is what proves they were drawn at all.
    assert "3/10" in plain_err
    assert "src.mp4" in plain_err


def test_stage_labels_cover_every_stage_the_pipeline_emits():
    """The emitted set is derived from the pipeline's own ``_stage`` call sites,
    so a stage added there trips this test unless a label is written for it —
    no longer just unless a human remembers to edit a hardcoded list."""
    import ast
    import inspect

    from selekyt import pipeline as pipeline_mod
    from selekyt.pipeline import _stage  # noqa: F401 - import proves the wiring

    source = Path(inspect.getsourcefile(pipeline_mod)).read_text(encoding="utf-8")
    emitted: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_stage"):
            continue
        for arg in node.args[1:2]:  # (progress, name, detail=...)
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                emitted.add(arg.value)
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                emitted.add(kw.value)
    assert emitted, "no _stage call site was found — this derivation is stale"
    assert emitted <= set(STAGE_LABELS), emitted - set(STAGE_LABELS)
    # Every label is a human phrase, never a module name a user would see in a
    # traceback.
    for label in STAGE_LABELS.values():
        assert "selekyt" not in label.lower()
        assert "pipeline" not in label.lower()
