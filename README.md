# SelekyT

Local-first long-form → social video editorial engine.

SelekyT accepts a YouTube URL (or local video), finds the strongest complete stories, and renders publishable Facebook cuts plus correctly reframed 9:16 Shorts/Reels with minimal subtitles.

## Example

```bash
pip install -e .
selekyt "https://www.youtube.com/watch?v=VIDEO_ID"
```

The pipeline is: download → probe → word-timestamp transcription → thought segmentation → candidate stories → editorial/virality scoring → context guard → ranking/deduplication → retention edit plan → dynamic reframing/captions → FFmpeg render → QA.

Default dependencies are local/open-source: yt-dlp, FFmpeg, faster-whisper, OpenCV, and optional Ollama. Use only videos you own, have permission to edit, or are otherwise legally entitled to process and republish.
