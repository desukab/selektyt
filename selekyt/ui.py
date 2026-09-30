"""Terminal presentation layer for the SeleKyT CLI.

Deliberately dependency-free. SeleKyT runs on hosts where wheels are missing
(Termux ARM64 has no ctranslate2), and a UI library would be the one package
that can fail to install and take the whole CLI with it; the ANSI subset this
needs is short, so it is written here rather than imported.

Everything in this module is presentation. It consumes the event stream
``selekyt.pipeline.run`` emits and renders it; it never touches the engine,
never decides an editorial value, and never invents a number. If the pipeline
has not reported a metric, the renderer shows nothing for it rather than a
placeholder that reads like one.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
from typing import Any, TextIO

# Neon lime, held back from full saturation so it stays readable on a black
# background instead of buzzing. This is the one accent; everything else is
# white, gray or a single restrained warning colour.
LIME = "\x1b[38;2;178;255;63m"
AMBER = "\x1b[38;2;214;158;64m"
RED = "\x1b[38;2;224;92;88m"
GRAY = "\x1b[38;2;150;154;160m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"

# The stage ids the pipeline emits, in the order the pipeline runs them.
# ``audio`` is conditional on --audio-analysis, so a stage that never starts
# never appears on the panel.
STAGE_LABELS: dict[str, str] = {
    "input": "Input",
    "transcribe": "Transcription",
    "verify": "Verification",
    "select": "Editorial",
    "corpus": "Corpus",
    "audio": "Audio analysis",
    "render": "Rendering",
    "report": "Package",
}


def _color_enabled(no_color: bool, force: bool | None, streams=(sys.stdout, sys.stderr)) -> bool:
    """Colour only when something is able to show it.

    ``NO_COLOR`` is the de-facto standard and wins over everything else, so a
    user who has asked for plain output globally gets it without per-command
    flags. ``--no-color`` is the per-command escape hatch. Otherwise colour
    needs a tty on the streams the UI writes to, because a redirected stream
    would embed escape codes in a log file or, worse, in the sidecar's wire.

    ``force`` is tri-state: an explicit True or False overrides the FORCE_COLOR
    lookup entirely (the caller has already decided), and None keeps the env
    behaviour.
    """
    if no_color or os.environ.get("NO_COLOR"):
        return False
    if force is not None:
        return bool(force)
    if os.environ.get("FORCE_COLOR"):
        return True
    return all(getattr(s, "isatty", lambda: False)() for s in streams)


def _hhmmss(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _wrap(text: str, width: int) -> str:
    if width < 8:
        return text
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if len(trial) > width and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return "\n  ".join(lines)


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


# The colour codes this module embeds in a row. They take no screen columns,
# so width accounting has to look through them.
_SGR = re.compile("\x1b\\[[0-9;]*m")


def _retain_terminal_fd(*streams: TextIO) -> int | None:
    """A descriptor naming the first real terminal among ``streams``.

    Dup'd rather than borrowed: the panel's lifetime outlasts the descriptors it
    was constructed on, because the CLI repoints fds 1 and 2 at the run log
    while the engine runs. None when no stream is a terminal — a test double or
    a redirected channel — in which case measurement falls back to the streams
    themselves.
    """
    for stream in streams:
        try:
            if not stream.isatty():
                continue
            fd = stream.fileno()
        except (AttributeError, ValueError, OSError):
            continue
        try:
            return os.dup(fd)
        except OSError:
            continue
    return None


def _as_number(value: Any) -> float | int | None:
    """A numeric value, or None for anything that is not one.

    Presentation degrades the way it already does for an unknown event type:
    a field of the wrong type is "not reported" rather than an exception, and
    a value that is not a number is never shown as one. ``bool`` is excluded
    on purpose — it is a flag wearing an int's clothes, not a measurement.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _clamp_visible(text: str, width: int) -> str:
    """Cut a row that carries colour codes to ``width`` screen columns.

    Colour passes through uncounted; a row that would wrap is cut at the edge
    instead, so one logical line is always exactly one screen row and
    ``lines_drawn`` never desyncs from what is on screen. Information is lost
    to the cut, but wrapping loses it too and desyncs every later repaint.
    """
    if len(_SGR.sub("", text)) <= width:
        return text
    out: list[str] = []
    used = 0
    pos = 0
    cut = False
    for m in _SGR.finditer(text):
        chunk = text[pos : m.start()]
        if chunk:
            room = width - used
            if room <= 0:
                cut = True
                break
            if len(chunk) <= room:
                out.append(chunk)
                used += len(chunk)
            else:
                out.append(_truncate(chunk, room))
                cut = True
                break
        out.append(m.group())
        pos = m.end()
    if not cut:
        chunk = text[pos:]
        room = width - used
        if room <= 0:
            cut = True
        elif len(chunk) > room:
            out.append(_truncate(chunk, room))
            cut = True
        else:
            out.append(chunk)
    if cut and _SGR.search("".join(out)):
        # The cut landed inside a coloured span; leave the terminal in its
        # default state rather than bleeding the colour into the next row.
        out.append(RESET)
    return "".join(out)


class Terminal:
    """Capability detection plus the small write primitives the renderers use."""

    def __init__(
        self,
        *,
        no_color: bool = False,
        quiet: bool = False,
        verbose: bool = False,
        json_mode: bool = False,
        force: bool | None = None,
        out: TextIO | None = None,
        err: TextIO | None = None,
    ) -> None:
        self.quiet = quiet
        self.verbose = verbose
        self.json_mode = json_mode
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        # ``force`` is the programmatic override of the FORCE_COLOR env lookup:
        # set it to enable or disable colour whatever the environment says, and
        # leave it None to keep the env behaviour. ``NO_COLOR`` stays the
        # unconditional winner either way, per _color_enabled's contract.
        self.color = _color_enabled(no_color, force, streams=(self.out, self.err))
        # The live panel is written to stderr so stdout stays a clean channel for
        # --json and for piping, and so the engine's own stdout never has to be
        # interleaved with it. It needs a tty on both, since a redirected stdout
        # is what the sidecar and CI see.
        self.interactive = (
            not quiet and not json_mode
            and getattr(self.err, "isatty", lambda: False)()
            and getattr(self.out, "isatty", lambda: False)()
        )
        self._size_fd = _retain_terminal_fd(self.err, self.out)
        self.width = self._width()
        self.height = self._height()

    def refresh_size(self) -> None:
        """Re-measure the terminal, so a resize mid-run takes effect on the next
        frame instead of laying the panel out for a geometry that no longer
        exists."""
        self.width = self._width()
        self.height = self._height()

    def _size(self) -> os.terminal_size:
        """Measure the terminal this panel was built on.

        ``shutil.get_terminal_size`` probes fd 1, which the CLI repoints at the
        run log while the engine runs — that redirect is how ffmpeg's banner and
        onnxruntime's C++ warnings stay off the terminal — so a re-measure
        mid-run would read a regular file, fail, and fall back to 80 columns.
        The panel would then lay itself out for a terminal it is not on while
        the user's is 40 wide. The descriptor dup'd at construction still names
        the terminal (winsize is a property of the pty, so a resize is visible
        through it), which is also the descriptor the panel writes are already
        routed to.
        """
        if self._size_fd is not None:
            try:
                return os.get_terminal_size(self._size_fd)
            except OSError:
                # The terminal closed under the run. Fall through to the stream
                # probe rather than pinning a size for a device that is gone.
                self._size_fd = None
        try:
            return shutil.get_terminal_size((80, 24))
        except (ValueError, OSError):
            return os.terminal_size((80, 24))

    def _width(self) -> int:
        cols = self._size().columns
        # A genuinely narrow terminal gets a narrowed layout rather than a
        # wrapped one; wrapping breaks the progress bars' alignment.
        return max(40, min(cols, 100))

    def _height(self) -> int:
        rows = self._size().lines
        # The live panel is clamped to this many rows per frame, so the bound is
        # a real one in both directions: a terminal that misreports height
        # cannot make the panel vanish or fill the scrollback.
        return max(8, min(rows, 60))

    def close(self) -> None:
        """Release the terminal descriptor this measures against."""
        fd, self._size_fd = self._size_fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def s(self, code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if self.color else text

    def bar(self, fraction: float | None, width: int) -> str:
        value = _as_number(fraction)
        if value is None:
            # Nothing was measured, so no bar is drawn at all: a row of empty
            # track reads as 0%, and the percentage text is suppressed for the
            # same reason.
            return ""
        filled = int(round(max(0.0, min(1.0, value)) * width))
        return self.s(LIME, "█" * filled + "░" * (width - filled))


class ProgressRenderer:
    """Turns the pipeline's event stream into a live terminal panel.

    Call :meth:`event` with every event the pipeline emits and :meth:`close`
    when the run is over. Between events a one-second ticker updates the elapsed
    clock, which is the only thing on the panel that moves without new
    information from the pipeline — clock time is real work time, not simulated
    progress.
    """

    def __init__(self, term: Terminal) -> None:
        self.term = term
        self.started = time.time()
        self.stages: list[str] = []
        self.detail: dict[str, dict[str, Any]] = {}
        self.durations: dict[str, float] = {}
        self.active: str | None = None
        self.progress: dict[str, Any] = {}
        self.notes: list[dict[str, Any]] = []
        # Filled by a ``fail`` event and read by the caller after the exception
        # has propagated out of the pipeline.
        self.failed_stage: str | None = None
        self.failed_reason: str | None = None
        self.lines_drawn = 0
        self._static_done: set[str] = set()
        self._static_started: set[str] = set()
        self._ticker: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        if term.interactive:
            self._ticker = threading.Thread(target=self._tick, daemon=True)
            self._ticker.start()

    # ── event protocol ──────────────────────────────────────────────────────

    def event(self, evt: dict[str, Any]) -> None:
        """Apply one pipeline event and redraw.

        Unknown event types are ignored rather than raised: the pipeline is the
        one that evolves, and a renderer that crashed on a field it had not seen
        would turn a cosmetic layer into a reliability problem.
        """
        kind = evt.get("type")
        with self._lock:
            if kind == "stage":
                self._stage(evt)
            elif kind == "progress":
                stage = evt.get("stage") or self.active
                if stage:
                    self.progress[stage] = evt
            elif kind == "note":
                self.notes.append(evt)
                if not self.term.interactive and not self.term.quiet:
                    # The static stream cannot repaint, so a note is written
                    # when it lands rather than when a stage is summarized.
                    row = self._note_row(evt)
                    if row is not None:
                        self._write(row + "\n")
            elif kind == "fail":
                # Remembered rather than rendered here: the exception is still
                # unwinding, and the screen that reports it is drawn by the
                # caller once the pipeline has actually let go.
                self.failed_stage = evt.get("stage")
                self.failed_reason = evt.get("reason")
            elif kind == "done":
                self.active = None
            self._render()

    def _stage(self, evt: dict[str, Any]) -> None:
        stage = evt.get("stage")
        if not stage:
            return
        state = evt.get("state")
        if stage not in self.stages:
            self.stages.append(stage)
        if state == "start":
            self.active = stage
            self.progress.pop(stage, None)
        elif state == "done":
            duration = _as_number(evt.get("duration"))
            self.durations[stage] = duration if duration is not None else 0.0
            self.detail[stage] = evt.get("detail") or {}
            self.progress.pop(stage, None)
            if self.active == stage:
                self.active = None

    # ── rendering ────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        while not self._stop.wait(1.0):
            with self._lock:
                self._render()

    def _write(self, text: str) -> None:
        self.term.err.write(text)
        self.term.err.flush()

    def _render(self) -> None:
        # Quiet is asked to print nothing at all, so even the append-only static
        # line is suppressed; it is not a tty-only feature that quiet opts into.
        if self.term.quiet:
            return
        # Non-interactive output is append-only: cursor control would land in a
        # log file as literal escape sequences, so a static line per stage is
        # what CI and piping get.
        if not self.term.interactive:
            self._render_static()
            return
        # The terminal is re-measured on every frame: a user who resizes the
        # window mid-run gets the new geometry on the next frame rather than
        # bars and margins sized for a width that no longer exists. This is one
        # shutil.get_terminal_size call per event, already inside the per-event
        # lock below; it is a cached ioctl-sized lookup next to disk writes and
        # a repaint, so its cost is not what the frame is waiting on. The clamp
        # in _width/_height keeps a torn or misreported read from changing the
        # layout to something unusable.
        self.term.refresh_size()
        blocks: list[list[str]] = []
        for stage in self.stages:
            blocks.append(self._stage_lines(stage))
        notes = self._note_lines()
        if notes:
            blocks.append(notes)
        # The panel is bounded by the screen: a frame taller than the terminal
        # scrolls on every repaint, and close()'s erase can no longer reach the
        # top of it. Every row is also clamped to the width, so the row count
        # below is the screen-row count and the erase stays exact.
        lines = [_clamp_visible(ln, self.term.width) for ln in self._fit_to_screen(blocks, self.term.height)]
        if self.lines_drawn:
            # Repaint from the top of the previous frame and clear to the end of
            # the screen, so a shorter frame never leaves the tail of a longer
            # one visible below it.
            self._write(f"\x1b[{self.lines_drawn}A\x1b[J")
        if not lines:
            # Nothing has been reported yet. A bare newline here would scroll
            # the screen once a second from construction until the first stage
            # event, and nothing would ever erase it.
            self.lines_drawn = 0
            return
        self.lines_drawn = len(lines)
        self._write("\r" + "\n".join(lines) + "\n")

    @staticmethod
    def _fit_to_screen(blocks: list[list[str]], height: int) -> list[str]:
        """Keep the blocks that fit, whole, preferring the tail.

        Each block is one stage's summary (or the notes block). The stage that
        is running is at the bottom, so when a long run outgrows the screen it
        is the finished stages at the top that scroll away — the honest
        alternative to a panel that writes past the top of the terminal.
        """
        kept: list[str] = []
        for block in reversed(blocks):
            if len(block) > height:
                if kept:
                    break
                # One stage that alone outgrows the screen keeps its own tail;
                # an empty panel would hide the stage that is actually running.
                kept = list(block[-height:])
                continue
            if len(kept) + len(block) > height:
                break
            kept = block + kept
        return kept

    def _stage_lines(self, stage: str) -> list[str]:
        t = self.term
        label = STAGE_LABELS.get(stage, stage)
        if stage in self.durations:
            mark = t.s(LIME, "✓")
            right = t.s(GRAY, _hhmmss(self.durations[stage]))
        elif stage == self.active:
            mark = t.s(LIME, "●")
            right = self._active_right(stage)
        else:
            mark = t.s(GRAY, "·")
            right = ""
        lines = [f"  {mark}  {label:<15}{right}"]
        if stage == self.active:
            lines.extend(self._progress_lines(stage))
        elif stage in self.durations:
            lines.extend(self._detail_lines(stage))
        return lines

    def _active_right(self, stage: str) -> str:
        t = self.term
        prog = self.progress.get(stage)
        if not prog:
            return t.s(GRAY, f"{_elapsed(self.started):>6}")
        pct = _as_number(prog.get("percent"))
        if pct is not None:
            return f"{pct:>5.0f}%"
        cur, total = prog.get("current"), prog.get("total")
        if cur is not None and total:
            return f"{cur}/{total}"
        return t.s(GRAY, f"{_elapsed(self.started):>6}")

    def _progress_lines(self, stage: str) -> list[str]:
        t = self.term
        prog = self.progress.get(stage)
        if not isinstance(prog, dict):
            prog = {}
        units: list[tuple[str, float | None]] = []
        sub = prog.get("subprogress")
        if sub and isinstance(sub, (list, tuple)):
            # An entry that is not a mapping is skipped rather than raised on,
            # the way an unknown event type is ignored.
            for unit in sub:
                if not isinstance(unit, dict):
                    continue
                units.append((str(unit.get("label", "")), _as_number(unit.get("fraction"))))
        else:
            percent = _as_number(prog.get("percent"))
            if percent is not None:
                # ``percent`` is 0-100, so the bar needs the fraction; the number
                # printed beside it is the percentage the event carried.
                units.append((str(prog.get("label") or ""), percent / 100.0))
        if not units:
            note = prog.get("note")
            return [t.s(GRAY, f"       {note}")] if note else []
        bar_width = max(8, t.width - 30)
        out: list[str] = []
        note = prog.get("note")
        if note:
            out.append(t.s(GRAY, f"       {note}"))
        for label, fraction in units:
            if fraction is None:
                # No fraction was reported for this unit, so the bar and its
                # percentage are both omitted: empty track would read as 0%.
                out.append(f"       {label}")
                continue
            bar = t.bar(fraction, bar_width)
            pct = f"  {fraction * 100:>3.0f}%"
            out.append(f"       {label:<8}{bar}{pct}")
        return out

    def _detail_lines(self, stage: str) -> list[str]:
        t = self.term
        detail = self.detail.get(stage)
        # ``detail or {}`` in the event handler lets any truthy non-mapping
        # through, so the type is checked here: a payload of the wrong shape
        # renders no detail rows rather than raising.
        if not isinstance(detail, dict) or not detail:
            return []
        rows = [
            f"       {t.s(GRAY, '├─')} {k:<13}{self._fmt(v)}" for k, v in detail.items()
        ]
        if rows:
            rows[-1] = rows[-1].replace("├─", "└─", 1)
        return rows

    @staticmethod
    def _fmt(value: Any) -> str:
        if value is None:
            # A key the pipeline did not populate prints nothing: the word
            # "None" reads as a value, and there is none to show.
            return ""
        if isinstance(value, float):
            return f"{value:g}"
        return str(value)

    def _note_row(self, evt: Any) -> str | None:
        """One row for a note event, or None when it carries no text.

        Warnings take the restrained amber the palette already reserves for
        them; a note without a level is still shown, in gray, because it is a
        real notice the pipeline asked the user to see.
        """
        if not isinstance(evt, dict):
            return None
        text = evt.get("text")
        if not text:
            return None
        t = self.term
        mark = t.s(AMBER, "!") if evt.get("level") == "warn" else t.s(GRAY, "·")
        return f"  {mark}  {text}"

    def _note_lines(self) -> list[str]:
        return [row for row in map(self._note_row, self.notes) if row is not None]

    def _render_static(self) -> None:
        """Append-only rendering for a non-interactive stream."""
        t = self.term
        for stage in self.stages:
            label = STAGE_LABELS.get(stage, stage)
            if stage == self.active and stage not in self._static_started:
                self._static_started.add(stage)
                self._write(f"  {t.s(GRAY, '·')}  {label}…\n")
            if stage in self.durations and stage not in self._static_done:
                self._static_done.add(stage)
                detail = self.detail.get(stage)
                extra = ""
                if isinstance(detail, dict) and detail:
                    parts = [f"{k}={self._fmt(v)}" for k, v in detail.items()]
                    extra = "  " + t.s(GRAY, " · ".join(parts))
                self._write(f"  {t.s(LIME, '✓')}  {label}{extra}\n")

    def close(self) -> None:
        self._stop.set()
        if self._ticker is not None:
            self._ticker.join(timeout=2.0)
            self._ticker = None
        if self.term.interactive and self.lines_drawn:
            self._write(f"\x1b[{self.lines_drawn}A\x1b[J")
            self.lines_drawn = 0


def _elapsed(started: float) -> str:
    return _hhmmss(time.time() - started)


# ── static screens ────────────────────────────────────────────────────────────


def start_screen(
    term: Terminal,
    *,
    source: str,
    model: str,
    language: str | None,
    out: str,
    width: int | None,
    height: int | None,
) -> None:
    """The identity block shown once, before the pipeline starts.

    Drawn with a rule rather than a full box: the box has to be reflowed at
    every terminal width and its right edge is what ends up crooked, while the
    wordmark is the part that has to be legible.
    """
    if term.quiet or term.json_mode:
        return
    t = term
    rule = "━" * min(t.width, 68)
    dims = f"{width}x{height}" if width and height else None
    bits = [source]
    if dims:
        bits.append(dims)
    bits += [f"model {model}", language or "auto-detect"]
    lines = [
        "",
        t.s(LIME + BOLD, "S E L E K Y T"),
        t.s(GRAY, _truncate("editorial intelligence for long-form video", t.width)),
        "",
        _truncate("  ·  ".join(bits), t.width),
        t.s(GRAY, _truncate(f"package → {out}", t.width)),
        "",
        rule,
    ]
    for line in lines:
        t.err.write(line + "\n")
    t.err.flush()


def complete_screen(
    term: Terminal,
    *,
    report: dict[str, Any],
    project: str,
    runtime: float,
    artifacts: int,
) -> None:
    """The final screen, built only from keys the report actually carries.

    Every row is read from the report rather than from a value the UI
    remembers, so a field the pipeline does not populate is a field that is not
    shown.
    """
    if term.json_mode or term.quiet:
        return
    t = term
    rule = "━" * min(t.width, 68)
    rows = [
        "",
        t.s(LIME + BOLD, "SELEKYT COMPLETE"),
        t.s(GRAY, "your editorial package is ready"),
        "",
        f"  {t.s(GRAY, 'INPUT')}",
        f"    {_truncate(_input_line(report), t.width - 4)}",
        "",
        f"  {t.s(GRAY, 'OUTPUT')}",
        *_output_rows(report, t),
        "",
        f"  {t.s(GRAY, 'PACKAGE')}",
        f"    {t.s(BOLD, project)}",
        "",
        f"  {t.s(GRAY, 'Runtime')}     {_hhmmss(runtime)}"
        f"   {t.s(GRAY, 'Artifacts')}  {artifacts}",
        "",
        rule,
        t.s(GRAY, "long video in · editorial decisions out"),
        "",
    ]
    for line in rows:
        t.out.write(line + "\n")
    t.out.flush()


def _input_line(report: dict[str, Any]) -> str:
    bits: list[str] = []
    title = report.get("title")
    if title:
        bits.append(str(title))
    coverage = report.get("coverage") or {}
    duration = coverage.get("duration_s")
    if duration:
        bits.append(_hhmmss(float(duration)))
    prov = report.get("provenance") or {}
    source_path = prov.get("source_path")
    if source_path and not title:
        bits.append(str(source_path))
    return "  ·  ".join(bits) if bits else "source"


def _output_rows(report: dict[str, Any], t: Terminal) -> list[str]:
    """One row per artifact family the run actually produced.

    ``report.json`` names directories rather than enumerating files, so presence
    is checked against the paths the report itself points at — a family the run
    did not write is shown as absent rather than omitted, which is the honest
    middle ground between hiding it and implying it succeeded.
    """
    from pathlib import Path

    rows: list[str] = []
    families = [
        ("16:9 cuts", (report.get("outputs") or {}).get("facebook")),
        ("9:16 cuts", (report.get("outputs") or {}).get("shorts")),
        ("captions", report.get("captions")),
        ("timeline", report.get("timeline")),
        ("content pack", report.get("content_pack")),
        ("editorial report", report.get("editorial_report")),
        ("story corpus", report.get("editorial_corpus")),
        ("video memory", (report.get("memory") or {}).get("path")),
    ]
    for label, path in families:
        if path is None:
            continue
        ok = Path(str(path)).exists()
        mark = t.s(LIME, "✓") if ok else t.s(AMBER, "absent")
        rows.append(f"    {label:<18}{mark}")
    selected = report.get("selected_count")
    if selected is not None:
        count = f"{selected} selected"
        candidates = report.get("candidate_count")
        if candidates:
            count += f"  ·  {candidates} candidates scored"
        rows.append(f"    {'stories':<18}{count}")
    return rows


def fail_screen(
    term: Terminal,
    *,
    stage: str | None,
    reason: str,
    remediation: str | None,
    log_path: str | None,
    out: TextIO | None = None,
) -> None:
    """Render a failure as an actionable block, never as a traceback.

    The exception's own message is echoed verbatim because ``tools/corpus_run.py``
    classifies runs by grepping it out of the CLI's stderr; paraphrasing it here
    would break that classification silently.
    """
    t = term
    stream = out if out is not None else t.err
    rule = "━" * min(t.width, 68)
    label = STAGE_LABELS.get(stage, "Run") if stage else "Run"
    lines = [
        "",
        t.s(RED + BOLD, f"✕ {label.upper()} FAILED"),
        "",
        t.s(GRAY, "Reason"),
        # Unwrapped on purpose: ``tools/corpus_run.py`` classifies a failed
        # corpus run by grepping phrases ("no audible spoken content", "digital
        # silence") out of this stream, and wrapping inserts a newline inside
        # the phrase at narrow widths, which silently turns a classified
        # failure into an unclassified one. The terminal wraps it visually
        # either way, so nothing is lost on screen.
        f"  {reason.strip()}",
        "",
    ]
    if remediation:
        lines += [t.s(GRAY, "Next"), f"  {_wrap(remediation, t.width - 2)}", ""]
    if log_path:
        lines += [t.s(GRAY, "Details"), f"  {log_path}", ""]
    lines += [rule, ""]
    for line in lines:
        stream.write(line + "\n")
    stream.flush()


__all__ = [
    "ProgressRenderer",
    "Terminal",
    "complete_screen",
    "fail_screen",
    "start_screen",
]
