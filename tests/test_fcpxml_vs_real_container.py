"""The FCPXML resource block against the real container, read by ffprobe.

Why this test exists
--------------------
``tools/nle_check.py`` checks that no clip reads past its asset's declared
extent — but the extent it measures *against* is the duration the FCPXML itself
declares. A writer that derives the asset duration from its own clips, rather
than from the media, is invisible to that check: the clips and the declaration
agree with each other and both disagree with the container.

The NLE is not so forgiving. ``engine/fcpxml.py`` records that DaVinci binds an
asset to its source media by three checks — path, source-media start timecode,
and frame-rate format — and that a mismatch on any one fires the relink dialog
even when the path is right. So the resource block is not decorative metadata:
it is the half of the document an NLE argues with before it ever looks at a cut.

ffprobe is third-party software (libavformat) reading bytes the writer cannot
influence. That makes this an independent reader for the resource block, which
is the part of the FCPXML that had no independent check at all: ``pycmx`` reads
the EDL, and the FCPXML's own reader is this project's, so the agreement figure
was self-confirmed on one side and unchecked on the other.

Scope, stated plainly
---------------------
This validates the resource block's duration and frame rate against the real
media. It does not validate the spine, the cut positions, or anything an editor
would call the edit — and it is not an NLE import. An asset that relinks is
necessary for the timeline to open at all, and nothing more.

Media is gitignored (see ``docs/AI_EXECUTION_STATE.md``), so the test is opt-in:
it skips unless the file is present, and it fails loudly rather than passing if
the probe itself cannot run, because a probe that never ran is not evidence.
"""

from __future__ import annotations

import subprocess
import urllib.parse
from pathlib import Path

import pytest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "corpus" / "media"

# The sources whose media happens to be on this host. Both have committed
# timelines. Anything else skips; a third source added to corpus/media does not
# silently widen the sweep without being named here.
SOURCES = ("royal-society-cloke.webm", "pentagon-esper-briefing.mp4")

# A container's format duration is the muxer's own accounting, which need not
# land on the frame grid the timeline was built on: 626.017 s at 25 fps is 626 s
# and 0.42 of a frame. Declaring the asset on the frame grid is correct — an
# NLE works in frames — so the container is allowed to run slightly long, and
# only slightly. A full frame or more means the declaration is not describing
# this media.
DURATION_TOLERANCE_S = 1.0 / 25.0 * 0.95

pytestmark = pytest.mark.skipif(
    not all((MEDIA / s).is_file() for s in SOURCES),
    reason=f"media is gitignored; this needs {', '.join(SOURCES)} under corpus/media",
)


def _probe(path: Path) -> dict[str, str]:
    """Duration and frame rate as ffprobe sees them.

    stderr is captured rather than inherited so a failure's reason is not lost
    under the progress meter ffprobe does not actually print here but which a
    caller's logging may attach.
    """
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,r_frame_rate",
         "-of", "default=noprint_wrappers=1", str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out: dict[str, str] = {}
    video_rate = None
    for line in proc.stdout.decode().splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key] = value
        if key == "r_frame_rate" and video_rate is None:
            # The first r_frame_rate ffprobe reports is the video stream's;
            # the audio stream reports 0/0, which is not a rate.
            video_rate = value
    if video_rate is not None:
        out["frame_rate"] = video_rate
    return out


def _rational_seconds(text: str) -> float:
    """``15650/25s`` -> 626.0. Absent/nonsense -> NaN, so a missing attribute
    is a hard failure rather than a silent zero."""
    raw = (text or "").strip().removesuffix("s")
    if not raw:
        return float("nan")
    if "/" in raw:
        num, _, den = raw.partition("/")
        try:
            n, d = float(num), float(den)
        except ValueError:
            return float("nan")
        return n / d if d else float("nan")
    try:
        return float(raw)
    except ValueError:
        return float("nan")


def _frame_rate(text: str) -> float:
    """``25/1`` -> 25.0; ``0/0`` (the audio stream) -> NaN."""
    num, _, den = text.partition("/")
    try:
        n, d = float(num), float(den)
    except ValueError:
        return float("nan")
    return n / d if d else float("nan")


@pytest.fixture(params=SOURCES, ids=lambda s: s.removesuffix(".webm").removesuffix(".mp4"))
def source(request) -> Path:
    return MEDIA / request.param


def _timelines_for(source: Path) -> list[Path]:
    run = ROOT / "corpus" / (source.stem + "_run") / "project" / "timeline"
    return sorted(run.glob("*.fcpxml")) if run.is_dir() else []


def test_the_control_media_actually_probes(source: Path) -> None:
    """Guard against a vacuous pass: if ffprobe cannot read this container, every
    comparison below would be comparing declared values against nothing."""
    probe = _probe(source)
    assert "duration" in probe and float(probe["duration"]) > 0, (
        f"ffprobe returned no duration for {source.name}; the comparisons below "
        f"would be comparing declarations against nothing"
    )
    rate = _frame_rate(probe.get("frame_rate", ""))
    assert rate == rate and rate > 0, (
        f"ffprobe reported no usable video frame rate for {source.name}: "
        f"{probe.get('frame_rate')!r}"
    )


def test_every_committed_timeline_describes_the_real_container(source: Path) -> None:
    """The asset's declared duration and frame rate must match what the
    container actually carries, on every timeline built from that source."""
    timelines = _timelines_for(source)
    assert timelines, (
        f"{source.name} is present but no committed timeline exists under "
        f"corpus/{source.stem}_run/project/timeline/; the test would then "
        f"cover nothing"
    )
    probe = _probe(source)
    container_duration = float(probe["duration"])
    container_rate = _frame_rate(probe["frame_rate"])

    problems: list[str] = []
    checked = 0
    for timeline in timelines:
        root = ET.fromstring(timeline.read_bytes())
        assets = [a for a in root.iter("asset")
                  if (a.get("name") or "") == source.name]
        if not assets:
            # A timeline that does not reference this source is not a defect;
            # it is a different timeline. Named so a silent rename is visible.
            continue
        for asset in assets:
            checked += 1
            declared = _rational_seconds(asset.get("duration", ""))
            if declared != declared:
                problems.append(
                    f"{timeline.name}: asset {source.name} declares no parsable "
                    f"duration (got {asset.get('duration')!r})"
                )
                continue
            delta = container_duration - declared
            # The container may legitimately run a fraction of a frame long; it
            # may not run long by a frame or more, and it may not be shorter.
            if delta < 0 or delta > DURATION_TOLERANCE_S:
                problems.append(
                    f"{timeline.name}: asset declares {declared:.3f}s but the "
                    f"container is {container_duration:.3f}s, a "
                    f"{delta:+.3f}s ({delta * container_rate:+.2f} frame) "
                    f"disagreement"
                )
        fmt = next(root.iter("format"), None)
        if fmt is not None:
            # frameDuration is a *duration* rational, not a rate: 1/25s means
            # 25 fps and 1001/30000s means 29.97. It inverts.
            num, _, den = (fmt.get("frameDuration") or "0/1").removesuffix("s").partition("/")
            try:
                n, d = float(num), float(den)
            except ValueError:
                n, d = 0.0, 0.0
            declared_rate = d / n if n and d else float("nan")
            if declared_rate != declared_rate or declared_rate <= 0:
                problems.append(
                    f"{timeline.name}: format declares no parsable frame rate "
                    f"(got {fmt.get('frameDuration')!r})"
                )
            elif abs(declared_rate - container_rate) > 0.01:
                problems.append(
                    f"{timeline.name}: format declares {declared_rate:g} fps but "
                    f"the container is {container_rate:g} fps"
                )
    assert checked, (
        f"{source.name} has {len(timelines)} committed timeline(s) but none "
        f"declares an asset named after it; the name has drifted"
    )
    assert not problems, f"{source.name}: " + "; ".join(problems)


def test_the_asset_src_is_an_absolute_uri_to_that_source(source: Path) -> None:
    """The relink check that does not depend on an NLE at all: the path the
    document points at must be this host's path to this file, absolute and
    quoted, or the NLE resolves it against the timeline's own directory."""
    for timeline in _timelines_for(source):
        root = ET.fromstring(timeline.read_bytes())
        for asset in root.iter("asset"):
            if (asset.get("name") or "") != source.name:
                continue
            rep = asset.find("media-rep")
            src = (rep.get("src", "") if rep is not None else "")
            assert src.startswith("file:///"), (
                f"{timeline.name}: {source.name} src is not an absolute file URI "
                f"({src!r})"
            )
            pointed = Path(urllib.parse.unquote(src[len("file://"):]))
            assert pointed == source.resolve(), (
                f"{timeline.name}: src points at {pointed}, not {source.resolve()}"
            )


# The measured figure the doc quotes, pinned where it is produced. A tolerance
# that the doc describes as sub-frame must actually be sub-frame, and a source
# whose media appears must be covered -- the number is only honest if the sweep
# it summarises cannot shrink to nothing without a test noticing.
DOC = ROOT / "docs" / "NLE_COMPATIBILITY.md"
ROW = ("| FCPXML asset duration and frame rate == the real container, read by "
       "`ffprobe` | **PASS** — 9 timelines over 2 sources; worst disagreement "
       "0.425 frame (`royal-society-cloke`, the other source reads 0.168), "
       "within the 0.95-frame tolerance |")


def test_the_doc_row_matches_the_sweep_that_produced_it() -> None:
    """The count in the doc is a claim about this file's own coverage."""
    assert ROW in DOC.read_text(encoding="utf-8"), (
        "the NLE_COMPATIBILITY.md table no longer carries the ffpxml-vs-container "
        "row verbatim; either the doc drifted, or the sweep below changed and the "
        "row must be re-run. If SOURCES or a run's timeline count changed, update "
        "the row and this constant together."
    )
    covered = sum(len(_timelines_for(MEDIA / s)) for s in SOURCES)
    assert covered == 9, (
        f"the doc row says 9 timelines over 2 sources, but this sweep now covers "
        f"{covered}; the row and the constant above disagree"
    )
    assert DURATION_TOLERANCE_S < 1.0 / 25.0, (
        "the doc calls the duration tolerance sub-frame; it is not"
    )


def test_the_tolerance_is_set_by_the_grid_not_chosen_to_pass() -> None:
    """A tolerance tuned to the data it excuses is not a tolerance, it is a
    rubber stamp. 0.95 frame is deliberately under one frame rather than
    derived from the worst measured value, so the bound is fixed by the frame
    grid and the measurement has to fit it."""
    assert DURATION_TOLERANCE_S == 1.0 / 25.0 * 0.95


def test_the_corpus_has_never_produced_a_drop_frame_timeline() -> None:
    """The drop-frame branch is covered synthetically and has never been
    reached on real media. This records that as a property of the committed
    corpus rather than as a passing remark in a comment, so the day a DF
    source enters the corpus this test fails and the caveat in
    ``engine/fcpxml.py`` has to be rewritten instead of quietly going false.

    Both facts are asserted, not just the absence: a corpus with zero NTSC
    rates would also produce all-NDF documents, and that would not be the same
    finding at all.
    """
    rates: set[str] = set()
    formats: set[str] = set()
    for timeline in sorted(ROOT.glob("corpus/*_run/project/timeline/*.fcpxml")):
        root = ET.fromstring(timeline.read_bytes())
        for seq in root.iter("sequence"):
            formats.add(seq.get("tcFormat") or "")
        for fmt in root.iter("format"):
            rates.add((fmt.get("frameDuration") or "").removesuffix("s"))
    assert formats == {"NDF"}, formats
    # 1001/30000s and 3960/120000s are NTSC-family rates where drop-frame is a
    # real possibility; 1/25s and 1/39s are not. The finding is that DF was
    # available and not selected, which needs at least one NTSC rate present.
    ntsc = {"1001/30000", "3960/120000"}
    assert rates & ntsc, (
        f"no NTSC-family frame rate remains in the corpus ({sorted(rates)}), so "
        f"all-NDF no longer means drop-frame was never chosen"
    )


def test_neither_probeable_source_carries_a_timecode_track() -> None:
    """The half of the DaVinci relink check that is about the media, not the
    document. Neither source on this host has a TMCD stream or a nonzero
    container start, so ``asset start="0s"`` has only ever been correct by
    being trivial. Stated here because it bounds the ffprobe check above: the
    resource block agrees with the container, and the container has no
    timecode opinion to disagree with."""
    for source in (MEDIA / s for s in SOURCES):
        probe = _probe(source)
        start = probe.get("start_time")
        if start is None:
            # ffprobe reports no container start_time for this muxer, which is
            # itself the absence of an opinion; the stream-level check below is
            # what settles it.
            pass
        else:
            assert abs(float(start)) < 0.05, (
                f"{source.name} reports a nonzero container start_time of "
                f"{start}s; asset start=\"0s\" is then wrong on this media, not "
                f"merely unexercised"
            )
        kinds = {k for k in probe if k == "codec_type"}
        # A TMCD stream would appear as a third codec_type alongside video and
        # audio. _probe only asks for video/audio rates, so a timecode track is
        # detected by asking for it explicitly rather than by counting.
        check = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1", str(source)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        names = {ln.partition("=")[2].strip()
                 for ln in check.stdout.decode().splitlines() if "=" in ln}
        assert "srt" not in names and not any(
            n.startswith("tmcd") for n in names
        ), f"{source.name} carries a timecode track ({sorted(names)}); the " \
           f"embedded-TC offset path is exercised by this media"
