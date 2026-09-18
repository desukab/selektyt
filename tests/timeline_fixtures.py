"""Shared fixture timelines for the edited-timeline test suite.

Kept in one place so the timeline model, caption and NLE-export tests all
reason about the *same* edits — a disagreement between them is then a real
bug rather than a fixture drift.
"""

from __future__ import annotations

from models.core import Word

# Telugu sentence pair with word timings. The "।" (Devanagari danda) is used
# as a sentence terminator in Telugu typography, so these cues must split on
# it rather than running the two sentences together.
TELUGU_WORDS = [
    Word("ఇది", 1.00, 1.40),
    Word("ఒక", 1.40, 1.70),
    Word("చిన్న", 1.70, 2.20),
    Word("పరీక్ష।", 2.20, 2.90),
    Word("మనం", 3.10, 3.60),
    Word("కలిసి", 3.60, 4.10),
    Word("నేర్చుకుందాం।", 4.10, 5.00),
]

TELUGU_TEXT = " ".join(w.text for w in TELUGU_WORDS)

ENGLISH_WORDS = [
    Word("This", 0.50, 0.85),
    Word("is", 0.85, 1.00),
    Word("the", 1.00, 1.15),
    Word("first", 1.15, 1.55),
    Word("idea.", 1.55, 2.05),
    Word("Here", 2.40, 2.75),
    Word("is", 2.75, 2.90),
    Word("the", 2.90, 3.05),
    Word("second", 3.05, 3.50),
    Word("idea", 3.50, 3.90),
    Word("and", 3.90, 4.10),
    Word("it", 4.10, 4.25),
    Word("runs", 4.25, 4.55),
    Word("longer", 4.55, 5.05),
    Word("than", 5.05, 5.30),
    Word("one", 5.30, 5.50),
    Word("line", 5.50, 5.80),
    Word("can", 5.80, 5.95),
    Word("hold.", 5.95, 6.30),
]

ENGLISH_TEXT = " ".join(w.text for w in ENGLISH_WORDS)


def interview_media(**kw) -> SourceMedia:  # noqa: F821 (type defined by caller)
    from engine.timeline import SourceMedia

    base = dict(
        path="/fixtures/interview.mp4",
        fps=29.97,
        duration=300.0,
        start_timecode="01:00:00;00",
    )
    base.update(kw)
    return SourceMedia(**base)


def telugu_media(**kw):
    from engine.timeline import SourceMedia

    base = dict(
        path="/fixtures/telugu_talk.mp4",
        fps=25.0,
        duration=120.0,
        start_timecode="00:00:00:00",
    )
    base.update(kw)
    return SourceMedia(**base)


def two_clip_timeline():
    """A single-track 29.97 drop-frame edit: 10s of intro then 5s later.

    The clip source spans are deliberately *not* contiguous in the source
    (10-20s and 40-45s) so any code that confuses source time with timeline
    time is visible in the output.
    """
    from engine.timeline import Clip, Timeline, Track

    media = interview_media()
    return Timeline(
        title="Two Clip Edit",
        fps=29.97,
        tracks=[Track("V1", "video")],
        clips=[
            Clip(media, 10.0, 20.0, 0.0, 10.0, "V1", "intro", "hook"),
            Clip(media, 40.0, 45.0, 10.0, 15.0, "V1", "point", "payoff"),
        ],
    )


def gapped_timeline():
    """Primary track with a hole in the middle, which the FCPXML spine must
    fill with an explicit gap rather than leaving a hole."""
    from engine.timeline import Clip, Timeline, Track

    media = interview_media()
    return Timeline(
        title="Gapped Edit",
        fps=29.97,
        tracks=[Track("V1", "video")],
        clips=[
            Clip(media, 10.0, 14.0, 0.0, 4.0, "V1", "a"),
            Clip(media, 40.0, 46.0, 9.0, 15.0, "V1", "b"),
        ],
    )


def multitrack_timeline():
    """V1 primary + a V2 B-roll lane + an A1 external-audio lane, all reading
    from different files, so lane assignment and asset fan-out are exercised."""
    from engine.timeline import Clip, SourceMedia, Timeline, Track

    cam = interview_media()
    broll = SourceMedia(
        path="/fixtures/broll.mov", fps=23.976, duration=200.0, start_timecode="00:00:00:00"
    )
    recorder = SourceMedia(
        path="/fixtures/recorder.wav",
        fps=30.0,
        duration=300.0,
        start_timecode="01:00:00;00",
        has_video=False,
    )
    return Timeline(
        title="Multitrack Edit",
        fps=29.97,
        tracks=[Track("V1", "video"), Track("V2", "video"), Track("A1", "audio")],
        clips=[
            Clip(cam, 10.0, 20.0, 0.0, 10.0, "V1", "cam_a"),
            Clip(cam, 40.0, 45.0, 10.0, 15.0, "V1", "cam_b"),
            Clip(broll, 5.0, 12.0, 1.0, 8.0, "V2", "broll"),
            Clip(recorder, 10.5, 20.5, 0.0, 10.0, "A1", "sound"),
        ],
    )


def telugu_timeline():
    """A 25fps edit of Telugu speech with word timings, for the caption and
    multilingual tests."""
    from engine.timeline import Clip, Timeline, Track

    media = telugu_media()
    return Timeline(
        title="తెలుగు ఎడిట్",
        fps=25.0,
        tracks=[Track("V1", "video")],
        clips=[
            Clip(media, 0.0, 6.0, 0.0, 6.0, "V1", "మొదటి భాగం"),
            Clip(media, 20.0, 24.0, 6.0, 10.0, "V1", "రెండవ భాగం"),
        ],
    )


def english_timeline():
    """A 30fps edit of English speech with word timings, including a sentence
    long enough to wrap to two caption lines."""
    from engine.timeline import Clip, SourceMedia, Timeline, Track

    media = SourceMedia(
        path="/fixtures/english_talk.mp4",
        fps=30.0,
        duration=120.0,
        start_timecode="00:00:00:00",
    )
    return Timeline(
        title="English Edit",
        fps=30.0,
        tracks=[Track("V1", "video")],
        clips=[
            Clip(media, 0.0, 7.0, 0.0, 7.0, "V1", "part_one"),
            Clip(media, 30.0, 33.0, 7.0, 10.0, "V1", "part_two"),
        ],
    )
