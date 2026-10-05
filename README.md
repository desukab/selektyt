# SeleKyT

**AI-native editorial software for turning long-form video into stories, edits, and publishable social content.**

> **AI proposes. The editor decides.**

SeleKyT compresses the first-pass editorial workflow: understand long-form video, find coherent story moments, rank them, protect context, remove unnecessary dead air, reframe for social formats, caption the result, and hand the edit back to a human through editable timelines.

## What it does

**Long-form video → editorial understanding → first-pass edits → editable timelines → social outputs**

SeleKyT can:

- analyze long-form video and transcription
- identify complete story moments instead of arbitrary time windows
- rank candidate moments using editorial signals
- apply context and truth safeguards
- perform measured dead-air/filler processing when enabled
- create 16:9 and 9:16 outputs with vertical reframing
- generate word-timed captions
- produce structured selections, provenance, and reports
- export **CMX 3600 EDL and FCPXML 1.11** timelines alongside rendered cuts

The goal is not to replace the editor. It is to give the editor a strong, editable first pass.

## Why SeleKyT

Most AI video tools focus on generating a finished clip.

SeleKyT focuses on the **editorial decision layer**:

- What is the story?
- Which moments belong together?
- What should be removed?
- What context must be preserved?
- How should the decision be handed back to a human?

The output is therefore more than an MP4: the editorial decisions behind the cut remain inspectable and editable.

## Quick start

Requirements: **Python 3.10+** and **ffmpeg** on your PATH.

```bash
pip install -e '.[transcription]'
selekyt analyze ./my-video.mp4 --limit 6
```

The same workflow can be started without the optional `analyze` verb:

```bash
selekyt ./my-video.mp4
```

Check the local environment:

```bash
selekyt --doctor
```

A run writes its project under `out/` by default, including rendered 16:9 and 9:16 cuts, captions, reports, and timeline exports. Use `--out` to choose another location.

## Transcription

**Local transcription is the default.** No API key is required.

Groq and Gemini are optional BYOK cloud providers:

```bash
pip install -e '.[cloud-transcription]'

selekyt configure-transcription groq
# or: selekyt configure-transcription gemini
```

A provider can also be selected for one run:

```bash
selekyt ./my-video.mp4 --transcription local
selekyt ./my-video.mp4 --transcription groq
selekyt ./my-video.mp4 --transcription gemini
```

When a cloud provider is selected, extracted audio is sent to that provider using your credentials. SeleKyT does not silently fall back to local transcription after an explicitly selected cloud provider fails.

## Audio analysis

The default workflow stays conservative. The measured audio pass is opt-in:

```bash
selekyt ./my-video.mp4 --audio-analysis --export-fcpxml
```

This enables VAD-based speech measurement, dead-air removal, filler trimming, and the additional audio-analysis FCPXML cut list.

## Timeline handoff

Every default rendered cut includes:

- a **CMX 3600 EDL**
- an **FCPXML 1.11** timeline

The timeline artifacts preserve the edit decisions so a human editor can inspect and continue the work.

**Timeline structure is mechanically validated. Real-world NLE import is editor validation, not something SeleKyT claims without evidence.** Test the generated timeline in the NLE you use.

## Current status

SeleKyT is an early-stage product with the core local video-to-editorial workflow implemented and exercised.

The next important evidence is **real editorial use**:

1. put the product in the hands of editors
2. measure first-pass time saved
3. validate timeline workflows in target editing software
4. improve reliability across languages and difficult footage
5. turn repeated editorial feedback into product improvements

These are product-validation iterations, not prerequisites for using the current workflow.

For URL sources, support depends on the source host and access conditions. Local files remain the most reliable path.

## Who it is for

SeleKyT is aimed at people and teams working with large amounts of long-form video:

- creators and YouTubers
- podcast teams
- professional editors
- agencies
- media companies and newsrooms
- corporate content teams
- education and publishing teams

## Building in public

- **Website:** https://desukab.github.io/
- **X:** https://x.com/SeleKyT

## License

SeleKyT is **source-available and free for non-commercial use** under the **SeleKyT Non-Commercial Source-Available License v1.0**.

Personal use, education, academic research, non-profit research, experimentation, evaluation, and other genuinely non-commercial use are permitted under the license.

Commercial use, commercial distribution, paid hosted services, and commercial products or derivatives require prior written permission.

See [LICENSE](./LICENSE) for the complete terms.

---

**SELEKYT**

*AI proposes. The editor decides.*
