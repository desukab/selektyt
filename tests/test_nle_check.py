"""Tests for the NLE-compatibility audit.

The audit exists because two real defect classes are invisible until someone
opens the export in Premiere/Resolve/FCP: media the timeline points at that an
NLE cannot relink, and record spans that overlap on import. These tests build a
minimal but real project — a genuine media file, genuine EDL/FCPXML, a genuine
render — and confirm the audit passes it, then fails it on exactly those two
defect classes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _write_media(path: Path) -> None:
    """A real 1-second file so ffprobe reports a true frame rate and duration.

    Video only, and the duration bound by the lavfi source itself rather than
    by an ffmpeg ``-t``: an ``-t`` placed before the inputs is an *input*
    option, and with an infinite source (``anullsrc``) the encode never
    terminates — a real run hung here for six minutes at 111% CPU. The
    audit never inspects audio, so there is no need for a second input.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=black:s=64x64:d=1:r=25",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
        timeout=60,
    )


_EDL = """TITLE:   test cut
FCM: NON-DROP FRAME
001  MOVIE V     C        {src_in} {src_out} 00:00:00:00 00:00:01:00
     M2   movie.mov
* FROM CLIP: {media}
001  MOVIE A     C        {src_in} {src_out} 00:00:00:00 00:00:01:00
"""

_FCPXML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.11">
  <resources>
    <format id="f1" name="FFVideoFormat" frameDuration="1/25s" />
    <asset id="r1" name="movie.mov" start="0s" duration="25/25s" hasVideo="1" videoSources="1" hasAudio="1" audioSources="1" audioChannels="2">
      <media-rep kind="original-media" src="{media_uri}"/>
    </asset>
  </resources>
  <library>
    <event name="test">
      <project name="test">
        <sequence format="f1" tcFormat="NDF">
          <spine>
            <asset-clip ref="r1" offset="0s" start="0s" duration="25/25s" />
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""


def _build_project(root: Path) -> Path:
    """A minimal project: source, one EDL, one FCPXML, one render, a selection."""
    media = root / "source" / "movie.mov"
    media.parent.mkdir(parents=True)
    _write_media(media)
    root.joinpath("timeline").mkdir()
    root.joinpath("timeline", "01.edl").write_text(
        _EDL.format(media=media, src_in="00:00:00:00", src_out="00:00:01:00")
    )
    root.joinpath("timeline", "01.fcpxml").write_text(
        _FCPXML.format(media_uri="file://" + str(media.resolve()))
    )
    # The renders the timeline must describe: one real second of media, in both
    # aspect ratios the pipeline ships.
    for preset in ("facebook", "shorts"):
        root.joinpath(preset).mkdir()
        _write_media(root / preset / "01.mp4")
    root.joinpath("selections.json").write_text(
        '[{"start": 0.0, "end": 1.0, "text": "a", "hook": "a", "payoff": "a"}]'
    )
    return root


def _audit(project: Path):
    sys.path.insert(0, str(TOOLS))
    try:
        import importlib
        nle_check = importlib.import_module("nle_check")
        return importlib.reload(nle_check).check_project(project)
    finally:
        sys.path.remove(str(TOOLS))
        sys.modules.pop("nle_check", None)


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"]).returncode != 0,
                    reason="ffmpeg is needed to build the test media")
def test_a_clean_project_passes(tmp_path):
    ok, findings = _audit(_build_project(tmp_path / "project"))
    failed = [f"{name}: {detail}" for name, cok, detail in findings if not cok]
    assert ok, f"the audit failed a clean project: {failed}"


def test_an_offline_media_reference_is_caught(tmp_path):
    """A relative asset src resolves against the timeline file's own directory,
    so the sequence imports with every clip offline. The audit must catch it."""
    project = _build_project(tmp_path / "project")
    broken = (project / "timeline" / "01.fcpxml").read_text()
    (project / "timeline" / "01.fcpxml").write_text(
        broken.replace("file://", "file://relative/")
    )
    ok, findings = _audit(project)
    assert not ok
    assert any("non-absolute src" in d for _n, _o, d in findings)


def test_an_overlapping_record_span_is_caught(tmp_path):
    """Two edits on the *same* channel whose record spans overlap is an import
    conflict; separate channels are separate tracks, so those may coincide."""
    project = _build_project(tmp_path / "project")
    edl = (project / "timeline" / "01.edl").read_text()
    # A second event whose video record starts before event 1's video ends.
    edl += (
        "002  MOVIE V     C        00:00:01:00 00:00:02:00 00:00:00:20 00:00:01:00\n"
        "     M2   movie.mov\n"
        "* FROM CLIP: " + str(project / "source" / "movie.mov") + "\n"
        "002  MOVIE A     C        00:00:01:00 00:00:02:00 00:00:00:20 00:00:01:00\n"
    )
    (project / "timeline" / "01.edl").write_text(edl)
    ok, findings = _audit(project)
    assert not ok
    assert any("before the previous edit ends" in d for _n, _o, d in findings)
