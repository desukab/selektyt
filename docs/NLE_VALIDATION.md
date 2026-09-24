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
EDL↔FCPXML cross-check. On `nasa360-ep5`, all six clips agree to **within 0.63
frames** against both presets — five of the six match the render exactly, and
the worst case is clip 02 at 0.63 frames of a 29.97 fps frame. Full transcript:
`docs/nle-audit-output.txt`, regenerable with the command above.

Across the seven audited projects the current count is **435 findings passing,
34 deferred, none failing** (regenerable with the loop in §6; the earlier figure
of twelve deferred checks on this page was stale — the corpus grew). Deferral is
not silence: a deferred finding is one whose *evidence* is absent rather than one
that passed. `corpus_run.py` deletes the source media unless `--keep-media` is
passed, so the four runs that did not retain theirs cannot have their relink
checked from the committed tree, and the three that did retain it carry absolute
paths that resolve only on the machine that generated them.

The telugu run is why that distinction is written down rather than assumed. Its
media was retained long after the run, and the moment it appeared on disk the
EDL's relink finding stopped deferring and **failed**: the shipped EDL carried
`FROM CLIP: corpus/media/telugu-tutorial.webm`, a path relative to the pipeline's
working directory, which an NLE opening the EDL from its own timeline directory
resolves to a file that is not there — every clip would have imported offline on
a machine that had the media right there. The exporter had already been fixed
(`engine/nle_export.py::_from_clip_path` makes the path absolute and refuses to
ship one it cannot resolve); the committed artifact predated the fix, and no
audit could see it while the media it needed was missing. Re-emitting with
`tools/regenerate_timelines.py` changed exactly one line, the path base, and the
finding now passes. The four remaining relative-path EDLs are the same defect at
a lower confidence: they are stale emitters' output too, and re-emitting them
needs their sources (1.3 GB across four hosts), so they stay deferred rather
than being rewritten to point at files this repo does not have.

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
3. **A second FCPXML reader** — would raise confidence in the file format but
   does not cross the library→NLE line, and both halves already have one
   independent reader each. Listed only so the distinction is explicit.

---

## 5. Claims this repository does and does not make

Said, because it is verifiable in the repo:

- SelekyT emits CMX 3600 EDL and FCPXML 1.11 whose timelines agree with the
  rendered output to within `RENDER_FRAME_TOLERANCE` — asserted on every corpus
  project by `tools/nle_check.py`. **Both halves are read by software that does
  not share this codebase, and neither reader is a mirror of the emitter:** the
  EDL by `pycmx`, a third-party CMX 3600 reader; the FCPXML by the
  OpenTimelineIO FCPXML adapter, vendored under `third_party/otio_fcpxml_adapter/`
  (Apache-2.0, origin and commit recorded in `third_party/PROVENANCE.md`). The
  FCPXML half was self-checked until that reader was found — parsed by
  SelekyT's own `ElementTree` reader against a vocabulary list SelekyT wrote,
  which is a self-consistency check and not an independent one — and the gap
  was recorded here as a gap rather than averaged away. The reader that closed
  it was chosen for one specific property: it resolves an asset's media through
  `<media-rep kind="original-media">`, which is where SelekyT writes `src`. A
  reader that reads `src` off the `<asset>` element instead parses the whole
  spine and returns every clip unlinked, which is a false positive in the exact
  shape a validator must not produce. So the check is not "it parsed": a
  document that comes back with no clips fails, and so does any clip whose
  media reference is not an `ExternalReference` with a non-empty target URL —
  because that is the failure mode a media-rep-blind reader produces, and
  "parsed by a library while every cut lands offline" is not a validation.
  See `docs/NLE_COMPATIBILITY.md` for the readers that were tried and rejected.
- Every one of the 27 committed EDL/FCPXML pairs is swept end to end by
  `tests/test_corpus_interchange.py`: `pycmx` accepts each EDL, the vendored
  FCPXML reader reads each FCPXML back as clips whose media resolves, the
  emitter's own validators are clean on each pair, and **each** `<asset-clip>`
  carries the same offset/start/duration as its EDL event to within 1.5 frames
  (worst measured deviation across the whole corpus: exactly 1.000 frame, the
  expected grid-snapping artefact).
- Each format is also compared against the ground truth of what the render
  actually kept, interval by interval and in order, by `tools/nle_check.py` —
  all **44 retained intervals across the 27 clips**, in both formats, with the
  worst endpoint off by exactly 1.00 frame against the `edit_plan.json` record.
  This is the third leg and it is not the same claim as the one above: the two
  formats could agree with each other while both mis-describing the cut, and
  on a multi-interval clip they would still agree on the total duration while
  doing it. Every multi-interval clip in the corpus keeps its extra interval in
  the middle, which is where a totals comparison is blind. The audit also
  rejects a hole between neighbouring spine clips, which "clips in
  non-decreasing order" admits.
- Frame-rate handling is correct on the grid the timelines were written on,
  including the case where the source media is gone (see §1 of
  `tools/nle_check.py` for the recorded-probe fallback).

**Not said, because it has not been shown:**

- That DaVinci Resolve, Premiere Pro, Final Cut Pro, or any other NLE imports,
  relinks, or plays these timelines. Nothing in this repository claims an NLE
  has ever opened a SelekyT artifact. Where earlier docs implied otherwise, they
  have been corrected. A third-party reader that resolves every clip's media is
  *not* an NLE import, and the two are not exchanged for each other anywhere on
  this page: the reader proves the document is machine-readable by code that
  does not share this codebase and that its media references point at real
  files. It says nothing about an NLE's import path, its relink dialog, or its
  playback.
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

# The count above, over every audited project (435 pass / 34 deferred / 0 fail)
for d in corpus/*_run/project; do
  [ -d "$d/timeline" ] || continue
  PYTHONPATH=/tmp/nlelibs:tools .venv/bin/python tools/nle_check.py "$d"
done | grep -c ' PASS '

# Re-emitting a stale timeline after an emitter-side fix, without a re-run.
# The media must be present — the tool refuses to write an EDL pointing at
# nothing, because that would recreate the defect it is repairing.
.venv/bin/python tools/regenerate_timelines.py corpus/telugu-tutorial_run/project
```
