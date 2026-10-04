from __future__ import annotations

import subprocess

from engine.render import concat_renders


def _av_part(path, frequency: int) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         "testsrc=size=320x180:rate=25:duration=1.0", "-f", "lavfi", "-i",
         f"sine=frequency={frequency}:sample_rate=48000:duration=1.0",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def _stream(path, selector: str) -> tuple[float, float]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", selector,
         "-show_entries", "stream=start_time,duration", "-of", "csv=p=0",
         str(path)],
        check=True, capture_output=True, text=True,
    )
    start, duration = result.stdout.strip().split(",")
    return float(start), float(duration)


def test_program_concat_trims_each_encoded_part_to_its_frame_duration(tmp_path):
    first, second = tmp_path / "01.mp4", tmp_path / "02.mp4"
    _av_part(first, 440)
    _av_part(second, 660)

    reel = concat_renders(
        [first, second], tmp_path / "program.mp4", part_durations=[1.0, 1.0]
    )

    video_start, video_duration = _stream(reel, "v:0")
    audio_start, audio_duration = _stream(reel, "a:0")
    assert abs(video_duration - 2.0) <= 1 / 25
    assert abs(audio_duration - 2.0) <= 1 / 25
    assert abs((video_start + video_duration) - (audio_start + audio_duration)) <= 1 / 25
