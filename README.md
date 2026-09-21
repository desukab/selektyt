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
- remove conservative dead air and redundant pauses
- create 16:9 and 9:16 versions
- reframe subjects for vertical video
- generate word-timed captions
- produce structured editorial selections and reports
- export editable **FCPXML** and **CMX 3600 EDL** timelines alongside rendered cuts

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

A lightweight local web interface is also included for driving the video-to-social workflow.

### Timeline handoff

SelekyT currently emits FCPXML 1.11 and CMX 3600 EDL timelines and validates their structure.

**Important:** timeline generation has been validated mechanically; real-world import into a professional NLE is still an active validation step. We do not claim NLE compatibility has been proven until an editor imports and uses the files.

## Quick start

For a local video:

```bash
pip install -e '.[transcription]'
selekyt ./my-video.mp4 --limit 6
```

Check the local environment:

```bash
selekyt --doctor
```

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
