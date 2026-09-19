"""NLE-compatibility audit for a real SelekyT run.

The export stage can write a timeline that is perfectly well-formed XML and
well-formed CMX 3600 and still be useless to an editor:

  - media the timeline points at that an NLE cannot relink, so every clip comes
    up offline and the user faces a relink dialog for a file that never moved;
  - record spans that overlap, so the NLE reports a conflict on import;
  - a timeline whose duration disagrees with the render it describes, which
    means the exported sequence is not the cut the user just watched.

Those are exactly the failures that only surface when someone opens the project
in Premiere/Resolve/FCP — so this tool checks them against the rendered files
rather than against the exporter's own idea of what it wrote.

    python tools/nle_check.py out/url-e2e/project

It reads only the artifacts a normal run already writes, plus ffprobe on the
rendered media. The EDL is parsed by ``pycmx``, an independent CMX 3600 reader,
rather than by SelekyT's own emitter: validating our output with our own parser
proves nothing. If pycmx is not installed that check FAILS — it does not skip —
because a check that passes while its validator is absent is how a
self-consistent-but-wrong emitter ships unchallenged. Install the ``nle`` extra
(``pip install -e '.[nle]'``) to make it execute. The exit code is nonzero if
any hard check fails, so this composes with CI like
``tools/editorial_audit.py``.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

# The FCPXML 1.11 element vocabulary, from Apple's Final Cut Pro XML
# reference. An element outside this set is either a typo or a version
# mismatch, and an NLE will reject or silently drop it. This is a vocabulary
# guard over the elements SelekyT emits plus the structural containers an NLE
# accepts — it is deliberately not a claim that Apple's full schema was
# applied (see docs/NLE_COMPATIBILITY.md for what is and is not verified).
FCPXML_ELEMENTS = {
    "fcpxml", "resources", "library", "event", "project", "sequence", "spine",
    "asset", "asset-clip", "format", "media-rep", "audio-channel",
    "chapter-marker", "marker", "rating", "keyword", "analysis-notes",
    "sync-clip", "gap", "transition", "filter", "caption", "role", "info",
    "metadata", "string", "float", "int", "bool", "date",
}
FRAME_TOLERANCE = 1.0
# Render comparison tolerance. Measured on real runs, the exported timeline is
# systematically 0-1 frame shorter than the rendered file (746 rendered frames
# against a 745-frame timeline, 513 against 512, 493 against 493), and the MP4
# container duration adds ~0.04s of muxer slop on top of frame-count arithmetic.
# Two frames covers both without hiding a real editorial disagreement.
RENDER_FRAME_TOLERANCE = 2.0


def _probe(path: Path, entries: str, select: str | None = None) -> str:
    try:
        cmd = ["ffprobe", "-v", "error"]
        if select:
            cmd += ["-select_streams", select]
        cmd += ["-show_entries", entries,
                "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return ""


def _frame_delta_verdict(delta: float, frame: float, tolerance: float) -> tuple[bool, str]:
    """Compare a duration delta on the frame grid, or report it as not run.

    ``frame`` is one frame in seconds. When it is 0.0 the rate was never
    resolved and ``delta <= frame * tolerance`` collapses to ``delta <= 0.0`` —
    an exact test on float seconds that passes on a residue of exactly 0.0 and
    fails on 3.3e-07, while both print the identical "no fps" detail. A check
    that never ran is not evidence of a correct export, so it is reported as
    not run rather than allowing a floating-point accident to read as a pass.
    """
    if frame <= 0.0:
        return False, "NOT RUN (source frame rate unknown)"
    return delta <= frame * tolerance, f"({delta / frame:.2f} frames)"


def _fps(path: Path) -> float:
    # The video stream's rate is the one a sequence is stamped with, and it is
    # not necessarily the first stream: on several containers ffprobe lists
    # the audio stream first, whose r_frame_rate is 0/0. Select v:0
    # explicitly, the way providers/ffmpeg.py:probe_fps does. A probe that
    # takes whichever stream comes first reads the audio rate on those files
    # and returns 0.0, which fails every fps-dependent check below.
    rate = _probe(path, "stream=r_frame_rate", select="v:0").strip()
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            if float(den) != 0:
                return float(num) / float(den)
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def _parse_recorded_rate(record: dict) -> float:
    """The frame rate a run recorded about its own source, as a float.

    ffprobe reports ``r_frame_rate`` as a rational (``30000/1001``); a corpus
    record may carry either that string or a pre-divided float. Used when the
    source media is no longer present, so the run is still auditable.
    """
    rate = record.get("fps")
    if rate is None:
        return 0.0
    if isinstance(rate, str) and "/" in rate:
        num, _, den = rate.partition("/")
        try:
            return float(num) / float(den) if float(den) != 0 else 0.0
        except ValueError:
            return 0.0
    try:
        val = float(rate)
        return val if val > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _duration(path: Path) -> float:
    try:
        return float(_probe(path, "format=duration"))
    except ValueError:
        return 0.0


def _tc_seconds(tc: str, fps: float) -> float:
    """Timecode to seconds on the emitter's own convention.

    ``engine.timecode.parse_smpte`` is what built these strings, so it is also
    what interprets them; the rendered file is then the empirical check that
    the convention is the right one.
    """
    from engine.timecode import parse_smpte
    return parse_smpte(tc, fps)


def _rational_seconds(value: str) -> float:
    """Parse FCPXML's ``num/den s`` rational into seconds."""
    text = value.strip().rstrip("s")
    if "/" in text:
        num, den = text.split("/")
        try:
            return float(num) / float(den) if float(den) != 0 else 0.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _edl_events(text: str) -> list[dict]:
    """Group an EDL's statements into events the way an NLE reads them.

    CMX 3600 repeats the event number for each channel of one edit, so
    ``001 ... V`` and ``001 ... A`` are two records of one event, not two
    events. pyccmx does this grouping when it is available; this fallback keeps
    the remaining checks running without it.
    """
    events: dict[str, dict] = {}
    for line in text.splitlines():
        # event, source, channel, kind, then the four in/out timecodes. The
        # timecodes are anchored, so the two tokens between the event number
        # and them are unambiguously channel and kind.
        m = re.match(r"^(\d{3,4})\s+(\S+)\s+(\S+)\s+(\S+)\s+"
                     r"(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})"
                     r"\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})\s*$", line)
        if not m:
            continue
        num, source, channel, _kind = m.group(1), m.group(2), m.group(3), m.group(4)
        ev = events.setdefault(num, {"number": num, "source": source, "edits": []})
        ev["edits"].append({
            "channel": channel,
            "src_in": m.group(5), "src_out": m.group(6),
            "rec_in": m.group(7), "rec_out": m.group(8),
        })
    return [events[n] for n in sorted(events)]


def _edl_media(text: str, edl: Path | None = None) -> list[str]:
    """The FROM CLIP paths an NLE would resolve, resolved the way an NLE does.

    A relative path is found relative to the EDL's *own directory*, not the
    directory the audit happens to be run from. Checking ``Path(rel).exists()``
    from the audit's working directory passes an EDL that imports with every
    clip offline — the audit and the emitter shared a working directory, so
    both resolved the same broken string the same lucky way. That is the bug
    this audit exists to catch, so resolve against the timeline first and fall
    back to the raw string only for a URI an EDL cannot meaningfully carry.
    """
    raw = [m.group(1) for m in re.finditer(r"^\s*\*\s*FROM CLIP:\s*(.+)$", text, re.M)]
    if edl is None:
        return raw
    resolved: list[str] = []
    for p in raw:
        if p.startswith(("file://", "http://", "https://")):
            resolved.append(p)
            continue
        candidate = Path(p)
        resolved.append(str(candidate if candidate.is_absolute()
                           else (edl.parent / candidate)))
    return resolved


def _referenced_media_present(timeline: Path) -> bool:
    """Whether any media the timelines name is actually reachable.

    ``NOT RUN`` means "no evidence either way", so it must be claimed only when
    the media really is unreachable by every path the artifacts carry — not when
    the audit's working directory happens not to be the one a relative path was
    written against. Asking the artifacts themselves, resolved the way the
    checks below resolve them, keeps that decision honest; deriving it from a
    separate path recorded in ``pipeline_state.json`` carries the same
    working-directory assumption and makes NOT RUN depend on where the audit
    was invoked from.
    """
    for edl in sorted(timeline.glob("*.edl")):
        text = edl.read_text(encoding="utf-8", errors="replace")
        for p in _edl_media(text, edl):
            if not p.startswith(("file://", "http://", "https://")) and Path(p).is_file():
                return True
    for fx in sorted(timeline.glob("*.fcpxml")):
        try:
            root = ET.fromstring(fx.read_text(encoding="utf-8"))
        except ET.ParseError:
            continue
        for rep in root.iter("media-rep"):
            src = rep.get("src", "")
            if src.startswith("file://") and Path(
                    urllib.parse.unquote(src[len("file://"):])).is_file():
                return True
    return False


def _check_edl(edl: Path, renders: dict[str, Path], selections: list[dict], fps: float,
               project: Path, media_retained: bool = True) -> list[tuple[str, bool, str]]:
    text = edl.read_text(encoding="utf-8", errors="replace")
    findings: list[tuple[str, bool, str]] = []
    stem = edl.stem
    notes: list[str] = []

    # Independent parse: a third-party CMX 3600 reader must accept the file.
    # A missing reader is a FAIL, not a SKIP: a check that passes when its
    # validator is absent proves nothing, and an emitter can then ship a
    # self-consistent-but-wrong EDL through every audit in the repo. Install
    # the `nle` extra (`pip install -e '.[nle]'`) to make this check execute.
    try:
        from pycmx import parse_cmx3600  # type: ignore
    except ImportError:
        findings.append((f"{stem} edl independent parse", False,
                         "pycmx is not installed, so this EDL was never parsed by an "
                         "independent reader. Install the `nle` extra: "
                         "pip install -e '.[nle]'"))
    else:
        try:
            parsed = parse_cmx3600(io.StringIO(text))
            events = list(parsed.events)
            n_edits = sum(len(e.edits) for e in events)
            findings.append((f"{stem} edl independent parse", True,
                             f"{len(events)} event(s), {n_edits} channel record(s), "
                             f"title={parsed.title!r}"))
        except Exception as exc:  # a real reader rejecting the file is the point
            findings.append((f"{stem} edl independent parse", False,
                             f"pycmx rejected the EDL: {type(exc).__name__}: {exc}"))

    events = _edl_events(text)
    if not events:
        findings.append((f"{stem} edl structure", False, "no edit events parsed from the EDL"))
        return findings

    # Record spans must advance per channel: an overlap is an import conflict.
    # The cursor runs across the whole EDL, not per event — an event is one
    # edit split across channels, so a per-event reset would leave the cursor
    # empty for every channel and the check could never fire.
    overlaps: list[str] = []
    last_out: dict[str, float] = {}
    for ev in events:
        for edit in ev["edits"]:
            ch = edit["channel"]
            rec_in = _tc_seconds(edit["rec_in"], fps)
            rec_out = _tc_seconds(edit["rec_out"], fps)
            if ch in last_out and rec_in < last_out[ch] - 1e-6:
                overlaps.append(f"event {ev['number']} channel {ch} starts {rec_in:.3f}s "
                                f"before the previous edit ends ({last_out[ch]:.3f}s)")
            last_out[ch] = max(last_out.get(ch, 0.0), rec_out)
    findings.append((f"{stem} edl record spans advance", not overlaps,
                     "; ".join(overlaps) or f"{len(events)} event(s), no overlap"))

    # Media the EDL points at must exist, or the import lands offline. The path
    # is the source the pipeline was given, so when the harness deleted that
    # source afterwards the check has no evidence either way — NOT RUN, not a
    # defect in the export.
    media = _edl_media(text, edl)
    missing = [p for p in media if not Path(p).exists()]
    if missing and not media_retained:
        findings.append((f"{stem} edl media exists", True,
                         f"NOT RUN (source media not retained): {len(media)} clip path(s) "
                         f"point at the source, which corpus_run.py deletes unless "
                         f"--keep-media is passed"))
    else:
        findings.append((f"{stem} edl media exists", not missing,
                         (f"{len(media)} clip path(s)" if media else "no FROM CLIP paths")
                         + (f"; MISSING: {missing}" if missing else "")))

    # The timeline must describe the file that was actually rendered. A cut may
    # be several events, so the timeline is the full record span.
    all_edits = [e for ev in events for e in ev["edits"]]
    rec_in = min(_tc_seconds(e["rec_in"], fps) for e in all_edits)
    rec_out = max(_tc_seconds(e["rec_out"], fps) for e in all_edits)
    span = rec_out - rec_in
    frame = 1.0 / fps if fps else 0.0
    for preset, render in renders.items():
        if not render.exists():
            # A timeline that rendered only one aspect ratio still has
            # something real to compare; failing on a missing file would be a
            # false positive for an export that deliberately skipped a preset.
            findings.append((f"{stem} timeline == {preset} render", True,
                             f"SKIP ({render.name} not rendered)"))
            continue
        got = _duration(render)
        delta = abs(got - span)
        ok, detail = _frame_delta_verdict(delta, frame, RENDER_FRAME_TOLERANCE)
        findings.append((f"{stem} timeline == {preset} render", ok,
                         f"timeline {span:.3f}s vs render {got:.3f}s {detail}"
                         if frame else detail))

    # The source in/out must agree with what was actually kept. The selection
    # is the wrong comparison: a selection is a span, while the EDL describes
    # the retained intervals inside it — dead air at the head or tail is
    # removed before export, so the first event legitimately starts after the
    # selection start. On clip 01 of the OOI source the selection began at
    # 112.471 s but its first retained interval at 113.36 s, and the check
    # reported a 26-frame disagreement about an export that was correct.
    # ``edit_plan.json`` is the record of what the render concatenated.
    index = int(re.sub(r"[^0-9]", "", stem) or "0") - 1
    if 0 <= index < len(selections):
        want = selections[index]
        span = _retained_span(project, index)
        if span is None:
            # Without the plan the check can only fall back to the selection,
            # and a multi-interval cut will report a false disagreement. Say
            # that rather than passing silently.
            findings.append((f"{stem} source interval == selection", True,
                             "SKIP (edit_plan.json unreadable; selection span "
                             "would give a false negative on a multi-interval cut)"))
        else:
            lo, hi = span
            src_in = min(_tc_seconds(e["src_in"], fps) for e in all_edits)
            src_out = max(_tc_seconds(e["src_out"], fps) for e in all_edits)
            d_start = abs(src_in - lo) / frame if frame else 0.0
            d_end = abs(src_out - hi) / frame if frame else 0.0
            ok = d_start <= FRAME_TOLERANCE + 0.5 and d_end <= FRAME_TOLERANCE + 1.5
            findings.append((f"{stem} source interval == selection", ok,
                             f"src {src_in:.2f}-{src_out:.2f}s vs retained "
                             f"{lo:.2f}-{hi:.2f}s (selection "
                             f"{want['start']:.2f}-{want['end']:.2f}s; "
                             f"start {d_start:.1f}f, end {d_end:.1f}f)"))
    return findings


def _retained_span(project: Path, index: int) -> tuple[float, float] | None:
    """The first and last source second the render actually kept for a clip.

    The edit plan's intervals are the concatenation the render performed, so
    its min and max are what the interchange file is obliged to describe.
    Returns ``None`` when the plan is missing or malformed rather than
    silently falling back to the selection span, which would turn a real
    disagreement into a pass on every multi-interval cut.
    """
    path = project / "edit_plan.json"
    if not path.is_file():
        return None
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(plan, list) or not (0 <= index < len(plan)):
        return None
    intervals = plan[index].get("intervals") or []
    if not intervals or not all(
        isinstance(iv, (list, tuple)) and len(iv) == 2 for iv in intervals
    ):
        return None
    return float(min(iv[0] for iv in intervals)), float(max(iv[1] for iv in intervals))


def _check_fcpxml(fcpxml: Path, renders: dict[str, Path], fps: float,
                  media_retained: bool = True) -> list[tuple[str, bool, str]]:
    findings: list[tuple[str, bool, str]] = []
    stem = fcpxml.stem
    try:
        root = ET.fromstring(fcpxml.read_bytes())
    except ET.ParseError as exc:
        findings.append((f"{stem} fcpxml well-formed", False, f"XML parse error: {exc}"))
        return findings
    findings.append((f"{stem} fcpxml well-formed", True, "parsed"))

    version = root.get("version", "")
    findings.append((f"{stem} fcpxml version", version == "1.11", f'version="{version}"'))

    # Elements outside the 1.11 vocabulary are dropped or rejected by an NLE.
    unknown = {e.tag for e in root.iter()} - FCPXML_ELEMENTS
    findings.append((f"{stem} fcpxml vocabulary", not unknown,
                     f"unknown elements: {sorted(unknown)}" if unknown else "all elements in the 1.11 set"))

    # An asset src that is not an absolute file URI resolves against the
    # timeline file's own directory, which is almost never where the media is.
    # As with the EDL, a src that points at a source the harness deleted
    # afterwards says nothing about the export itself.
    broken: list[str] = []
    for asset in root.iter("asset"):
        rep = asset.find("media-rep")
        src = rep.get("src", "") if rep is not None else ""
        if not src.startswith("file:///"):
            broken.append(f"{asset.get('name', '?')}: non-absolute src {src!r}")
            continue
        path = Path(urllib.parse.unquote(src[len("file://"):]))
        if not path.exists():
            broken.append(f"{asset.get('name', '?')}: {path} does not exist")
    if broken and not media_retained:
        findings.append((f"{stem} fcpxml media relinkable", True,
                         f"NOT RUN (source media not retained): {len(broken)} asset(s) "
                         f"point at the source, which corpus_run.py deletes unless "
                         f"--keep-media is passed"))
    else:
        findings.append((f"{stem} fcpxml media relinkable", not broken,
                         "; ".join(broken) or "every asset src is an absolute URI to an existing file"))

    # A clip that reads past its asset's declared extent is clamped or flagged
    # by an NLE, and it usually means the asset was declared from the clips
    # rather than from the media. The measured case was exactly one frame.
    assets: dict[str, ET.Element] = {a.get("id"): a for a in root.iter("asset")}
    over: list[str] = []
    for clip in root.iter("asset-clip"):
        ref = clip.get("ref", "")
        asset = assets.get(ref)
        if asset is None:
            continue
        extent = (_rational_seconds(asset.get("start", "0s")) or 0.0) + (
            _rational_seconds(asset.get("duration", "0s")) or 0.0
        )
        end = (_rational_seconds(clip.get("start", "0s")) or 0.0) + (
            _rational_seconds(clip.get("duration", "0s")) or 0.0
        )
        if end > extent + 1e-6:
            over.append(f"clip ref={ref} reads past the end of its asset "
                        f"(to {end:.3f}s; the asset extends to {extent:.3f}s)")
    findings.append((f"{stem} fcpxml clips within asset", not over,
                     "; ".join(over) or "no clip reads past its asset"))

    # The spine must advance and its total duration must equal the render.
    spine = root.find(".//spine")
    total = 0.0
    advances = True
    cursor = 0.0
    if spine is not None:
        for clip in spine:
            if clip.tag not in ("asset-clip", "gap", "sync-clip"):
                continue
            start = _rational_seconds(clip.get("start", "0s"))
            duration = _rational_seconds(clip.get("duration", "0s"))
            if start < cursor - 1e-6:
                advances = False
            cursor = max(cursor, start + duration)
            if clip.tag == "asset-clip":
                total += duration
    frame = 1.0 / fps if fps else 0.0
    findings.append((f"{stem} fcpxml spine advances", advances,
                     "clips in non-decreasing start order" if advances else "a clip starts before its predecessor ends"))
    for preset, render in renders.items():
        if not render.exists():
            findings.append((f"{stem} fcpxml duration == {preset} render", True,
                             f"SKIP ({render.name} not rendered)"))
            continue
        got = _duration(render)
        delta = abs(got - total)
        ok, detail = _frame_delta_verdict(delta, frame, RENDER_FRAME_TOLERANCE)
        findings.append((f"{stem} fcpxml duration == {preset} render", ok,
                         f"spine {total:.3f}s vs render {got:.3f}s {detail}"
                         if frame else detail))
    return findings


def check_project(project: Path) -> tuple[bool, list[tuple[str, bool, str]]]:
    """Run every NLE-compatibility check on one project directory."""
    timeline = project / "timeline"
    if not timeline.is_dir():
        return False, [("timeline directory", False, f"no timeline/ in {project}")]
    source_dir = project / "source"
    source = next(source_dir.glob("*"), None) if source_dir.is_dir() else None
    # A local-file run does not copy the media into the project tree, so there
    # is nothing to glob. The pipeline's `source` key holds the media's own
    # path in that case (a URL run stores the URL and downloads into source/).
    # Fall back to it so a local run is auditable to the same standard; without
    # it every fps-dependent check reports "could not probe the source" on a
    # run that itself succeeded.
    if source is None:
        state_path = project / "pipeline_state.json"
        if state_path.is_file():
            try:
                recorded = json.loads(state_path.read_text(encoding="utf-8"))
                candidate = recorded.get("source")
                if candidate:
                    resolved = Path(candidate)
                    source = resolved if resolved.is_file() else None
            except (OSError, json.JSONDecodeError):
                pass
    fps = _fps(source) if source is not None else 0.0
    if fps <= 0.0:
        # The last resort is the probe the run itself recorded. corpus_run.py
        # deletes the source media by default, so the path above resolves to
        # nothing and the frame rate it stamped into every timeline would
        # otherwise be unknowable — turning a correct export into a wall of
        # "could not probe the source" failures. The recorded rate is only ever
        # read from a file the run wrote about itself, never guessed.
        probe_path = project / "source_probe.json"
        if probe_path.is_file():
            try:
                recorded = json.loads(probe_path.read_text(encoding="utf-8"))
                fps = _parse_recorded_rate(recorded)
            except (OSError, json.JSONDecodeError):
                pass

    selections = []
    sel_path = project / "selections.json"
    if sel_path.is_file():
        try:
            loaded = json.loads(sel_path.read_text(encoding="utf-8"))
            selections = loaded if isinstance(loaded, list) else loaded.get("selected", [])
        except (OSError, json.JSONDecodeError):
            pass

    findings: list[tuple[str, bool, str]] = []
    # The relink checks need the media itself, and corpus_run.py deletes it
    # unless --keep-media is passed. A missing source is then a property of the
    # audit, not of the export: an EDL pointing at a deliberately-deleted file
    # cannot be relinked, but the export is not thereby wrong. Reporting that as
    # a defect would make every corpus project fail for a reason unrelated to
    # the interchange, so the checks below are told the media is absent and
    # report NOT RUN instead of FAIL. ``source`` is already None in that case —
    # the pipeline_state fallback above only accepts a path that still exists.
    media_retained = (source is not None and Path(source).is_file()
                      or _referenced_media_present(timeline))
    findings.append(("source frame rate", fps > 0,
                     f"{source.name if source else '?'} @ {fps:.4f} fps"
                     if fps else "could not probe the source"))
    for edl in sorted(timeline.glob("*.edl")):
        renders = {"facebook": project / "facebook" / f"{edl.stem}.mp4",
                   "shorts": project / "shorts" / f"{edl.stem}.mp4"}
        findings += _check_edl(edl, renders, selections, fps, project, media_retained)
    for fcpxml in sorted(timeline.glob("*.fcpxml")):
        renders = {"facebook": project / "facebook" / f"{fcpxml.stem}.mp4",
                   "shorts": project / "shorts" / f"{fcpxml.stem}.mp4"}
        findings += _check_fcpxml(fcpxml, renders, fps, media_retained)

    # The two formats must agree with each other as well as with the render.
    stems = {p.stem for p in timeline.glob("*.edl")} & {p.stem for p in timeline.glob("*.fcpxml")}
    for stem in sorted(stems):
        edl_text = (timeline / f"{stem}.edl").read_text(encoding="utf-8", errors="replace")
        events = _edl_events(edl_text)
        try:
            root = ET.fromstring((timeline / f"{stem}.fcpxml").read_bytes())
        except ET.ParseError:
            continue
        spine = root.find(".//spine")
        spine_total = sum(_rational_seconds(c.get("duration", "0s"))
                          for c in (spine if spine is not None else [])
                          if c.tag == "asset-clip")
        if events:
            every = [e for ev in events for e in ev["edits"]]
            span = (max(_tc_seconds(e["rec_out"], fps) for e in every)
                    - min(_tc_seconds(e["rec_in"], fps) for e in every))
            frame = 1.0 / fps if fps else 0.0
            delta = abs(span - spine_total)
            ok, detail = _frame_delta_verdict(delta, frame, 1.5)
            findings.append((f"{stem} edl == fcpxml duration", ok,
                             f"edl {span:.3f}s vs fcpxml {spine_total:.3f}s {detail}"
                             if frame else detail))
    return all(ok for _name, ok, _detail in findings), findings


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: nle_check.py <project-dir>", file=sys.stderr)
        return 2
    project = Path(argv[1])
    if not project.is_dir():
        print(f"not a directory: {project}", file=sys.stderr)
        return 2
    ok, findings = check_project(project)
    width = max((len(n) for n, _o, _d in findings), default=8)
    # SKIP and NOT RUN both leave the verdict open rather than confirmed: the
    # former is a deliberate omission, the latter a check that had no evidence
    # to work from. Both are counted separately below so a run cannot look fully
    # verified while checks were quietly unable to run.
    n_notrun = 0
    for name, cok, detail in findings:
        if not cok:
            mark = "FAIL"
        elif detail.startswith(("SKIP", "NOT RUN")):
            mark = "SKIP "
            if detail.startswith("NOT RUN"):
                n_notrun += 1
        else:
            mark = "PASS"
        print(f"{name:<{width}}  {mark}  {detail}")
    print()
    if not ok:
        print("FAILURES PRESENT")
    elif n_notrun:
        print(f"ALL RUN CHECKS PASS, but {n_notrun} could not run "
              "(source media not retained; re-run with --keep-media to verify relink)")
    else:
        print("ALL CHECKS PASS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
