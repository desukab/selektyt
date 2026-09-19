# RED TEAM FINDINGS — SelekyT

Agent 8 (red team), 2026-09-19. Mission: try to disprove SelekyT's product
claims. Every verdict below is backed by a command I actually ran or a file I
actually read on this host. Nothing here is speculative, and nothing is
fabricated.

Environment facts confirmed first: `.venv/bin/python` is the only interpreter
(bare `python` does not exist); `ffprobe`/`ffmpeg` at `/usr/bin`; 4 cores, no
GPU, `DISPLAY` is empty and no X server or Xvfb binary exists on this host.

---

## Working notes (incremental)

### Claim 1 — rendered outputs on disk

All 30 rendered deliverables on disk are real, playable MP4s. ffprobe on every
file under `corpus/*_run/project/{facebook,shorts}/`:

- Container `mov,mp4,m4a,...`, video h264, audio AAC stereo, non-zero frame
  counts, non-zero size. No corrupt/unparseable files.
- Both presets present per source: `facebook/` = landscape, `shorts/` = 9:16
  (e.g. royal-society-cloke 1920x1080 vs 608x1080; ooi 1920x1080 vs 1080x1920).
- Audio stream present on every clip, with non-trivial RMS measured
  (astats on OOI clips gives -23 to -43 dB RMS, i.e. real speech, not digital
  silence). The OOI audio-drop defect described in CORPUS_RESULTS.md is not
  present in the outputs on disk.

Anomalies found (not claimed in the docs):
- `nasa360-ep5` clips carry a third stream `codec_type=data, codec_name=unknown,
  nb_frames=1` (a metadata/caption stream carried through from source). Harmless
  but worth knowing — it is not a clean video+audio-only export.
- `ooi-vertical` facebook/02 and /03: audio stream duration (12.35 s / 10.45 s)
  is shorter than video duration (13.98 s / 11.11 s) by 0.66–1.63 s. The audio
  is present and loud, so this is a trailing-offset/length mismatch, not a drop.
  Nothing in the docs quantifies or admits this.

### Claim 2 — transcript noise

Confirmed verbatim from `corpus/results.jsonl` and the on-disk
`transcript.json` files:

- telugu-tutorial: 4 segments, 83 chars, head
  `రిన్త్న న్స్హ్లుయిసి . నినాన్త్డందాన ంపెర్లిస్లిని...` — phonetic noise.
- hadhazy-protest: 38 segments of individually-plausible Hungarian fragments.
- hindi-baiga-interview: 3 segments, 317 chars of Devanagari noise;
  NO_STORIES_PASSED.

The `asr` block in every `transcript.json` records
`{"requested_model": "small", "model": "small", "downgrade_reason": null,
"fallback_reason": null}` — so CORPUS_RESULTS.md's attribution of the failure
to the `small` model is supported at the artifact level. NOTE GAP:
`corpus/results.jsonl` has an `asr_model` key that is `null` for all 11
sources, so the per-run record alone does NOT prove which model ran; you must
open each project's `transcript.json` to confirm it.
