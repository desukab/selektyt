# SelekyT

**AI-native editorial software for turning long-form video into stories, edits, and publishable social content.**

SelekyT is built around a simple idea:

> **Let AI do the first-pass editorial work without taking the editor out of the loop.**

Long-form video contains useful stories, but finding them, shaping them, removing filler, adapting the framing, captioning the result, and preparing an editable handoff can take hours.

SelekyT is being built to compress that first pass.

## What it does

**Long-form video → editorial understanding → first-pass edits → editable timelines → social outputs**

SelekyT can:

- analyze long-form video and transcription
- identify complete story moments rather than arbitrary time windows
- rank candidate moments using editorial signals
- apply context/truth safeguards
- shorten long pauses conservatively (a deeper measured dead-air and filler
  pass is opt-in: `--audio-analysis`)
- create 16:9 and 9:16 versions
- reframe subjects for vertical video
- generate word-timed captions
- produce structured editorial selections and reports
- export an editable **CMX 3600 EDL** timeline alongside every rendered cut
  (**FCPXML** too, with `--audio-analysis --export-fcpxml`)

The goal is not to replace the editor. The goal is to give the editor a strong, editable first pass.

## Why SelekyT

Most AI video tools focus on generating a finished clip.

SelekyT is being built around the **editorial decision layer**:

**What is the story?  
Which moments belong together?  
What should be removed?  
What context must be preserved?  
How should the edit be handed back to a human?**

The important output is therefore not only an MP4. SelekyT preserves the editorial decisions behind the cut so they can be inspected and refined.

## Who it is for

SelekyT is being developed for people and teams working with large amounts of long-form video:

- YouTubers and creators
- podcast teams
- professional editors
- agencies
- media companies and newsrooms
- corporate content teams
- education and publishing teams

## Current capabilities

The current product can run locally from a video file and provides a command-line workflow for processing footage into editorial selections, captions, rendered cuts, and timeline data.

It supports local transcription, story-unit selection, 16:9 and 9:16 outputs, vertical reframing, captions, provenance, and timeline export.

A lightweight local web interface is also included for driving the
video-to-social workflow. It is a source-checkout extra, started with
`node web/server.mjs`, and is not installed by the Python wheel.

### Timeline handoff

SelekyT currently emits a CMX 3600 EDL timeline with every rendered cut and
validates its structure. FCPXML 1.11 is optional and requires
`--audio-analysis --export-fcpxml`; only the EDL is produced on a default run.

**Important:** timeline generation has been validated mechanically; real-world import into a professional NLE is still an active validation step. We do not claim NLE compatibility has been proven until an editor imports and uses the files.

## Quick start

For a local video:

```bash
pip install -e '.[transcription]'   # Python 3.10+, ffmpeg on PATH
selekyt analyze ./my-video.mp4 --limit 6
```

The run writes a project directory in the current working directory (override
with `--out`): `out/run.log` holds the engine's own console output, and
`out/project/` holds the rendered 16:9 and 9:16 cuts, captions, timeline
exports, and `report.json`.

The `analyze` verb is optional — `selekyt ./my-video.mp4` does the same thing —
but the verb is the documented form.

Check the local environment:

```bash
selekyt --doctor
```

Control what the terminal shows:

```bash
selekyt ./my-video.mp4 --quiet          # nothing but the result and errors
selekyt ./my-video.mp4 --verbose        # the engine's own diagnostics, live
selekyt ./my-video.mp4 --json           # the final report as JSON on stdout
selekyt ./my-video.mp4 --no-color       # monochrome; also honours NO_COLOR
```

Run the measured audio pass, which the default run skips:

```bash
selekyt ./my-video.mp4 --audio-analysis \
    --export-fcpxml     # VAD dead-air removal, filler trimming, FCPXML cut list
```

`--audio-analysis` gates the whole stage: it measures speech with a VAD backend
(`--vad-backend`), removes silence (`--silence-threshold`, `--silence-padding`,
`--min-speech-ms`, `--min-silence-ms`), and trims hesitation pauses by default
(`--no-remove-filler` to keep them). `--keep-pauses` — the default — preserves
intentional and breathing pauses rather than cutting them as dead air.

The live panel is drawn on stderr so stdout stays a clean channel for `--json`.
Every figure it shows is measured — a chunk decoded, a render part finished —
and a stage the run never reached is not drawn at all.

Reclaim the disk a finished run no longer needs. Most of a project's bytes are
intermediates — the downloaded source and the extracted audio plus transcription
checkpoints — not the deliverables, and nothing deleted them:

```bash
selekyt --clean-project out                     # reports what it removed
selekyt --clean-project out --keep-source       # keep the download to re-render
```

Either the `--out` directory or the `project` directory inside it works — they
name the same run.

It refuses unless every render the edit plan called for is on disk, because
deleting those intermediates mid-run would break resume. `--keep-source`
preserves the downloaded media, which is what `tools/nle_check.py` probes and a
re-run renders from.

For URL sources, support depends on the source host and access conditions. Local files are the most reliable path today.

## Product direction

SelekyT is moving toward a broader **editorial intelligence layer**:

**Source video → understand → find stories → shape the first pass → human edit → publish → learn from feedback**

The longer-term objective is to make editorial work faster and more repeatable while keeping human judgment in control.

Planned areas include:

- deeper semantic editorial reasoning
- better person/object tracking and motion-aware reframing
- stronger NLE workflows
- channel-specific editorial preferences
- feedback loops from real editorial decisions and published performance
- automated title and hook experimentation without inventing claims

## What we are proving now

SelekyT is an early-stage product. The next important evidence is not more architecture — it is **real editorial use**.

We are focused on:

1. getting the product into the hands of real editors
2. measuring how much first-pass work it saves
3. validating the timeline workflow in real editing software
4. improving reliability across languages and difficult footage
5. turning repeated editorial feedback into a better product

We would rather publish what has actually been tested than inflate the product with unsupported claims.

## Building in public

Follow the build:

- **Website:** https://desukab.github.io/
- **X:** https://x.com/SeleKyT

## Project status

SelekyT is under active development.

This repository is **proprietary and source-available**. The code is available for authorized evaluation and development; commercial use, redistribution, competing hosted services, and derivative commercial products require permission.

See [LICENSE](./LICENSE) for the full terms.

---

**SELEKYT**

*AI proposes. The editor decides.*
