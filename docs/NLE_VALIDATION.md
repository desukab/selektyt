# NLE Validation — what has actually been opened, and what is blocked

> **Status:** MECHANICAL SELF-CONSISTENCY ESTABLISHED. **NO NLE HAS OPENED AN
> ARTIFACT.** This document exists to say that plainly, to record the routes
> that were *tested and closed* so they are not retried, and to name the
> cheapest legitimate way to get real NLE evidence.

The mission is explicit that only a real NLE opening an artifact counts as
external evidence, and equally explicit that validation must not be fabricated.
Everything below is either a command that was actually run on this host, or a
statement that something was not done.

---

## 1. What is actually established

Two independent, unrelated parsers accept every timeline SelekyT emits:

| Reader | What it is | Result |
|---|---|---|
| `pycmx` 1.5.1 | Third-party CMX 3600 reader, run deliberately **outside** the project venv (`/tmp/nlelibs`) so SelekyT's own dependencies cannot influence it | Accepts all EDLs. On `royal-society-cloke`: `01.edl` → 3 events / 5 channel records, `02.edl` → 2/3, `03.edl` → 2/3 |
| `engine.nle_export` (this repo) | SelekyT's own emitter and reader | Round-trips the same files |

Both agree with the ffmpeg render to within `RENDER_FRAME_TOLERANCE` — asserted
by `tools/nle_check.py`, which compares timeline spans against the rendered
`.mp4` durations on the frame grid the timelines were stamped on, plus an
EDL↔FCPXML cross-check. On `nasa360-ep5`, all six clips agree to **0.00 frames**
against both presets.

**What this is worth, honestly:** a library that parses a format is not an NLE.
`pycmx` accepting the EDL proves the file is a well-formed CMX 3600 document
that a real EDL reader can be expected to accept. It does not prove Resolve or
Premiere relink the media, honour the timecode, or play the intended cut. That
proof requires an NLE, and it has not been obtained.

---

## 2. Routes that were tested and are closed

Each of these was run on this host, not assumed. They are recorded so the next
attempt starts from a known-closed door.

### Blender 4.0.2 — no CMX 3600 import at all

Blender is installed (`/usr/bin/blender`, 4.0.2) and its Video Sequence Editor
runs headless with `--background`, which made it look like the cheapest free
NLE that could open a timeline. It cannot:

```
$ blender --background --factory-startup --python-expr "
  import bpy
  print([o for o in dir(bpy.ops.sequencer) if 'import' in o or 'edl' in o])"
SEQUENCER_IMPORT_OPS: []
ALL_EDL: []
```

`bpy.ops.sequencer` in 4.0.2 exposes **no** import operator and no EDL operator
of any kind. The EDL import that older Blender releases carried as an add-on is
gone. FCPXML is not supported either. **Negative result, do not retry on
Blender 4.x.**

### OpenTimelineIO 0.18.1 (PyPI wheel) — EDL/FCPXML adapters are not in the wheel

OpenTimelineIO (Pixar/ASWF) is the obvious second *independent* CMX 3600
reader, and a maintained production interchange toolkit rather than a hobby
parser. Installed in isolation, again outside the project venv:

```
$ pip install --target /tmp/otiolibs --no-deps opentimelineio   # 0.18.1
$ PYTHONPATH=/tmp/otiolibs python -c "import opentimelineio as otio; \
    print(sorted(otio.adapters.available_adapter_names()))"
['otio_json', 'otiod', 'otioz']
```

The PyPI wheel ships only the JSON adapters. The CMX 3600 and FCPXML adapters
are C++ plugins and are absent, so `read_from_file` on a `.edl` raises
`NoKnownAdapterForExtensionError`. Building them from source needs the C++
plugin toolchain and a source build; that was **not** attempted, because it
would not yield NLE evidence anyway — a library is still a library.

### melt (MLT 7.22) — no EDL or FCPXML producer

`melt` is the one headless NLE *core* on this host (`/usr/bin/melt`, MLT
7.22.0) — the engine Kdenlive and Shotcut are built on — so it is the nearest
thing to a real NLE that can be driven without a display. It cannot read either
format:

```
$ melt -query producers | grep -o '"[^"]*"' | head
"xml" "xml-string" "xml-nogl" "kdenlivetitle" ...
$ melt corpus/royal-society-cloke_run/project/timeline/01.edl
Failed to load "…/01.edl"
```

Its producers are MLT/Westley XML and Kdenlive titles only; there is no CMX 3600
or FCPXML entry point, and the same holds for the committed FCPXML. Worth
recording because it is the route someone will reach for first: it looks like a
headless NLE and it is one, it just has no importer for either interchange
format. (The mirror image of this — *writing* MLT XML and letting melt play the
cut — is item 3 of §5.)

---

## 3. The blocker, stated plainly

Every free NLE that imports CMX 3600 EDL or FCPXML 1.11 is a GUI application,
and this host has **no display and no GPU**:

- **DaVinci Resolve** (free tier) — the realistic target, and the one real NLE
  most likely to accept both formats. No headless/scripted EDL-import path on
  Linux; the free Linux build is not officially supported, and 4 GB of free
  disk against a ~3 GB install plus media does not leave room to try.
- **Kdenlive / Shotcut / Flowblade** — accept EDL or FCPXML only through their
  GUIs. Their headless entry points consume MLT XML or their own project
  format, not CMX 3600.
- **Pitivi / GStreamer Editing Services** — `ges-launch-1.0 --load` reads the
  GES format only.

So the constraint is not effort, it is environment: **there is no free,
headless, no-display path from a CMX 3600 EDL or an FCPXML 1.11 to a real NLE
on this machine.** No amount of work inside this repository changes that.

---

## 4. The cheapest legitimate way to get real NLE evidence

In ascending order of cost. None of these has been done.

1. **A human editor with an NLE already installed** opens the EDL and the
   FCPXML from a `--keep-media` corpus run and answers three questions: did it
   import at all, did the media relink offline, and does the played cut match
   the rendered `.mp4`. This is by far the cheapest — minutes of someone's
   time, no install, no spend — and it is exactly the evidence class the
   evidence hierarchy puts above automated tests. **PENDING EXTERNAL
   VALIDATION.** No participant has been contacted; the mission forbids
   contacting anyone without explicit founder approval.
2. **DaVinci Resolve on a display-capable host.** Same three questions, run by
   us. Needs a machine with a display and ~5 GB of spare disk, and the free
   tier. No spend.
3. **Building OpenTimelineIO's C++ adapters** — would add a *second independent
   reader*, which raises confidence in the file format but does not cross the
   library→NLE line. Listed only so the distinction is explicit.

---

## 5. Claims this repository does and does not make

Said, because it is verifiable in the repo:

- SelekyT emits CMX 3600 EDL and FCPXML 1.11 that two independent parsers
  accept, and whose timelines agree with the rendered output to within
  `RENDER_FRAME_TOLERANCE` — asserted on every corpus project by
  `tools/nle_check.py`.
- Every one of the 27 committed EDL/FCPXML pairs is swept end to end by
  `tests/test_corpus_interchange.py`: `pycmx` accepts each EDL, the emitter's
  own validators are clean on each pair, and **each** `<asset-clip>` carries
  the same offset/start/duration as its EDL event to within 1.5 frames (worst
  measured deviation across the whole corpus: exactly 1.000 frame, the expected
  grid-snapping artefact). This is stronger than `nle_check`, which compares
  only aggregate spans and would miss a mis-timed clip in the middle of a
  multi-event cut.
- Frame-rate handling is correct on the grid the timelines were written on,
  including the case where the source media is gone (see §1 of
  `tools/nle_check.py` for the recorded-probe fallback).

**Not said, because it has not been shown:**

- That DaVinci Resolve, Premiere Pro, Final Cut Pro, or any other NLE imports,
  relinks, or plays these timelines. Nothing in this repository claims an NLE
  has ever opened a SelekyT artifact. Where earlier docs implied otherwise, they
  have been corrected.
- That the exported cut matches what an editor intended — that is a human
  editorial question, not an interchange question, and it belongs in
  `docs/HUMAN_EDITORIAL_EVALUATION.md`.

---

## 6. Reproducing the negative results

```bash
# Blender: confirm there is no EDL import operator
blender --background --factory-startup --python-expr \
  "import bpy; print([o for o in dir(bpy.ops.sequencer) if 'import' in o or 'edl' in o])"

# OpenTimelineIO: confirm the wheel has no EDL adapter
pip install --target /tmp/otiolibs --no-deps opentimelineio
PYTHONPATH=/tmp/otiolibs python -c \
  "import opentimelineio as otio; print(sorted(otio.adapters.available_adapter_names()))"

# What IS established: pycmx, outside the venv, parsing real timelines
PYTHONPATH=/tmp/nlelibs:tools .venv/bin/python tools/nle_check.py \
  corpus/royal-society-cloke_run/project

# The full corpus audit, with media retained so relink checks actually run
.venv/bin/python tools/corpus_run.py corpus/manifest.json --only royal-society-cloke --keep-media
PYTHONPATH=/tmp/nlelibs:tools .venv/bin/python tools/nle_check.py \
  corpus/royal-society-cloke_run/project
```
