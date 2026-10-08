# SeleKyT

**AI-native editorial software for turning long-form video into stories, edits, and publishable social content.**

> **AI proposes. The editor decides.**

SeleKyT compresses the first-pass editorial workflow: understand long-form video, find coherent story moments, rank them, protect context, remove unnecessary dead air, reframe for social formats, caption the result, and hand the edit back to a human through editable timelines.

## What it does

**Give SeleKyT a long video. Tell it what you want. It finds the story, makes the cut, and gives you something you can actually use.**

**Long-form video → understand → choose the story → edit → publish**

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

## Why consumers choose SeleKyT

You don't have to learn video editing before you can get something good out of a long recording.

SeleKyT is built around the things a person actually wants to say:

- **Make me a Short** — find one coherent, publishable short-form story instead of stitching together random highlights.
- **Make me a Story** — turn a long recording into the strongest coherent narrative, with a beginning, development, and payoff.
- **Find the Highlights** — return multiple strong moments when you want options.
- **Keep the context** — don't sacrifice the meaning of a moment just because an isolated sentence scores well.
- **Give me something editable** — you get the rendered videos *and* the underlying edit decisions, captions, provenance, and timelines.

The hard part happens underneath: SeleKyT tries to understand the whole recording before deciding what belongs in the final cut.

## Quick start

Requirements: **Python 3.10+ on Linux x86_64** for the production PyPI wheel. The PyPI distribution brings the runtime dependencies, bundled local Whisper model, Silero VAD weights, Rust mechanical runtime, and FFmpeg/ffprobe runtime it needs; no system FFmpeg or Rust install is required.

The intended install is a pip install — no checkout of this repository needed:

```bash
python -m venv .venv
source .venv/bin/activate      # on Windows: .venv\Scripts\activate
pip install selekyt
```

A plain `pip install selekyt` is the production path. Local transcription is
included with a bundled Whisper model, Silero VAD and its model are included,
independent EDL/FCPXML readers are included, the Rust mechanical runtime is
included for Linux x86_64, and the FFmpeg/ffprobe runtime is provisioned
automatically. The PyPI release intentionally publishes a platform wheel rather
than pretending the bundled native runtime is universal.
The `[transcription]` extra remains as a compatibility alias for older install
commands.

> **Release gate:** every PyPI release must pass the clean-install acceptance
> workflow before publication. The package is source-available and proprietary
> (see [LICENSE](./LICENSE)).

Then:

```bash
selekyt --doctor              # check the local processing environment
selekyt ./my-video.mp4        # find multiple strong highlights
selekyt ./my-video.mp4 --make short   # make the strongest short-form story
selekyt ./my-video.mp4 --make story   # make the strongest complete story
```

You can start with a simple goal instead of learning editorial settings. SeleKyT uses the same underlying evidence and story understanding, then decides how much material the result actually needs. **Short** describes the format, not a fixed duration.

The same workflow can be started without the optional `analyze` verb:

```bash
selekyt ./my-video.mp4
```

`--limit` controls the maximum number of output cuts. It defaults to 6 and
accepts any positive count — it is a budget you choose, not a product cap:

```bash
selekyt ./my-video.mp4 --limit 1     # one cut
selekyt ./my-video.mp4 --limit 10    # ten
selekyt ./my-video.mp4               # the default
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
pip install 'selekyt[cloud-transcription]'

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
