"""EDL record spans must advance, never overlap by a rounding frame.

Reproduces a real defect found when a multi-clip cut was first exported through
the web shell: three clips concatenated on a 29.97 timeline, each event's
``rec_in`` rounded independently from ``clip.timeline_in``. The accumulated sum
of the snapped spans drifted one frame past where the next clip's own
``timeline_in`` rounded to, so event 3 started at frame 622 while event 2 ended
at frame 623. The validator rejected the list with
"channel V: event 2 record span overlaps event 3", and the whole run failed at
timeline export — after renders and captions were already written.

The failure is in the arithmetic, not the content: it needs only consecutive
clips whose durations do not land on frame boundaries, which any real 29.97
source produces. These timelines pin that shape without depending on the
specific source that happened to trigger it.

The fix advances each event from the previous event's snapped out-point on its
own channel rather than from an independent rounding.
"""
from __future__ import annotations

import re

from engine import timecode
from engine.nle_export import export_edl, render_edl, validate_edl


def _several_clip_timeline(durations: list[float], offset: float = 0.0):
    """Concatenate clips whose durations are deliberately frame-awkward.

    A span like 6.6333s at 29.97 does not land on a frame boundary, so snapping
    it and then rounding the next clip's in-point can disagree by a frame —
    exactly the condition the accumulation must survive.
    """
    from engine.timeline import Clip, SourceMedia, Timeline, Track

    media = SourceMedia(
        path="/fixtures/talk.mp4", fps=29.97, duration=600.0,
        start_timecode="00:00:00;00",
    )
    clips, cursor, src = [], 0.0, 10.0
    for i, dur in enumerate(durations):
        clips.append(Clip(media, src, src + dur, cursor + offset, cursor + dur,
                          "V1", f"clip{i}", "hook"))
        cursor += dur
        src += dur + 7.0
    return Timeline(title="Concatenated", fps=29.97,
                    tracks=[Track("V1", "video")], clips=clips)


def test_record_spans_never_overlap_on_frame_awkward_durations(tmp_path):
    tl = _several_clip_timeline([6.6333, 11.3334, 8.5167])
    path = export_edl(tl, tmp_path / "edit.edl")
    problems = validate_edl(path.read_text(encoding="ascii"))
    assert problems == [], problems


def test_record_in_never_precedes_the_previous_record_out():
    """The concrete shape of the defect: event 3's in-point at or after event
    2's out-point, on every channel."""
    tl = _several_clip_timeline([6.6333, 11.3334, 8.5167])
    text = render_edl(tl)
    rate = timecode.detect_rate(tl.fps)

    def rec_frames(line: str) -> tuple[int, int]:
        fields = line.split()
        # "001 REEL V C src_in src_out rec_in rec_out"
        ri, ro = fields[-2], fields[-1]

        def f(ts: str) -> int:
            h, m, rest = ts.split(":")
            # Drop-frame separators are ';' and the last field is frames.
            s, fr = re.split(r"[;:]", rest)
            return (int(h) * 3600 + int(m) * 60 + int(s)) * rate.nominal_fps + int(fr)

        return f(ri), f(ro)

    events = [rec_frames(ln) for ln in text.splitlines()
              if re.match(r"^\d{3}\s+\S+\s+V\s+C\s", ln)]
    assert len(events) >= 2
    for (a_in, a_out), (b_in, _b_out) in zip(events, events[1:]):
        assert b_in >= a_out, (
            f"event starts at record frame {b_in} before the previous event "
            f"ends at {a_out}"
        )


def test_the_exact_reproduction_from_the_real_run():
    """The three durations that produced the failure in the real run: 6.63s,
    11.33s, 8.51s concatenated on a 29.97 primary track. Before the fix this
    emits an event 3 whose record in-point precedes event 2's record out-point
    — verified by reverting the fix and re-running this file, which fails with
    the original 'channel V: event 2 record span overlaps event 3'."""
    tl = _several_clip_timeline([6.63, 11.33, 8.51])
    problems = validate_edl(render_edl(tl))
    assert problems == [], problems


def test_audio_and_video_of_one_cut_share_a_record_in_point():
    """The V and A events of a single cut must land on the same record
    in-point; the per-channel accumulation must not push the audio channel
    ahead of the video one."""
    from engine.timeline import Clip, SourceMedia, Timeline, Track

    media = SourceMedia(path="/fixtures/talk.mp4", fps=29.97, duration=600.0,
                        start_timecode="00:00:00;00")
    tl = Timeline(
        title="Cut with audio", fps=29.97,
        tracks=[Track("V1", "video"), Track("A1", "audio")],
        clips=[
            Clip(media, 10.0, 20.0, 0.0, 10.0, "V1", "a", "h"),
            Clip(media, 40.0, 46.0, 10.0, 16.0, "V1", "b", "h"),
            Clip(media, 10.5, 20.5, 0.0, 10.0, "A1", "sound", "h"),
        ],
    )
    text = render_edl(tl)
    assert validate_edl(text) == []
    # The audio event sits at the same record in-point as the first video event.
    v_line = next(ln for ln in text.splitlines() if ln.startswith("001") and " V " in ln)
    a_line = next(ln for ln in text.splitlines() if ln.startswith("001") and " A " in ln)
    assert v_line.split()[-2] == a_line.split()[-2]
