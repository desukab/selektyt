# NLE Compatibility — Verification Status

**Date of record:** 2026-09-19
**Source of evidence:** the shipped run on `out/web/pipeline/project` — a real
rights-permitted 24:29 NASA 360 source (`NASA_360_program5_HD.mov`, 29.97 fps,
1469.77 s) cut into six clips with 16:9 and 9:16 renders.

---

## The headline

**No NLE has ever opened a SelekyT EDL or FCPXML.** That is the single most
important fact on this page. Nothing below is a substitute for it, and nothing
below should be quoted to a customer or an investor as "it works in Resolve."

What this page does record is the strongest evidence obtainable *without* an
NLE, and the exact experiment that closes the gap. It also records four
defects found this way — three of which an actual import would have surfaced
as a broken timeline, and one (the relative asset URI) that would have
surfaced as every clip coming up offline.

---

## What is verified today

Every line below is reproduced by a repeatable tool, not asserted by the code
that wrote the files:

```
python tools/nle_check.py out/web/pipeline/project
```

The tool deliberately does **not** use SelekyT's own parsers to validate
SelekyT's own output — proving our file with our reader proves nothing. The
EDL is parsed by `pycmx`, an independent CMX 3600 reader, and the renders are
measured by `ffprobe`.

| Check | Result |
|---|---|
| `pycmx` (independent CMX 3600 reader) accepts every EDL | **PASS** — 3/3, events and channel records counted |
| EDL is well-formed CMX 3600 with advancing record spans | **PASS** |
| FCPXML is well-formed XML, version 1.11, elements inside the 1.11 vocabulary | **PASS** — 3/3 |
| Every asset `src` is an absolute `file:///` URI to an existing file | **PASS** — 3/3 |
| No asset-clip reads past its asset's declared extent | **PASS** — 3/3 |
| FCPXML spine advances; no clip precedes its predecessor | **PASS** |
| EDL total span == FCPXML spine total | **PASS** — 0.00 frames on all three |
| Timeline duration == rendered MP4 duration (both presets) | **PASS** — 0.00–1.63 frames |
| EDL source in/out == the selection the cut was made from | **PASS** — 0.0–0.7 frames |

Full output is in `docs/nle-audit-output.txt`.

### The three formats agree with each other and with the render

For each cut the EDL's total record span, the FCPXML's spine total, and both
rendered MP4s agree to within 1.63 frames. This matters more than it sounds:
the EDL and the FCPXML are emitted by separate code paths, and the render by a
third (ffmpeg filter chains). Their agreement is the only evidence available
here that all three describe the same cut.

A systematic 0–1 frame shortfall remains: the export declares 493 frames where
the render contains 493 frames plus ~0.04 s of container muxer slop. The
tolerance is 2 frames, set by measuring this run rather than by taste — it
covers both effects without hiding a real editorial disagreement. It is a
known imprecision, not a bug, and it is recorded rather than tuned away.

---

## Four defects this work found

All four are now fixed and pinned by tests. Each is worth recording because
each is a class, not an instance.

**1. A relative asset URI — every clip offline.** The exporter wrapped the
timeline's media path in `file://` without ever touching the filesystem, so a
CLI invocation with a relative source path produced `file://out/...` — which
an NLE resolves against the timeline file's *own* directory, so the sequence
imports with every clip offline and the user faces a relink dialog for a file
that never moved. Auditing a freshly regenerated run found it on all six
timelines, so this was the live path, not a stale artifact. The same latent
bug existed in `engine/fcpxml.py` (the multitrack exporter) as a silent
fallback and was fixed first; the live one in `engine/nle_export.py` only
surfaced once the audit was pointed at a real run. Both now resolve the path
and fail loudly with the named path when they cannot, and the audit fails any
non-absolute `src`.

**2. The asset declared the clips' extent, not the media's.** Cut 03's asset
declared 671.371 s while its clip read to 671.404 s — one frame past the end
of the asset hosting it. The timeline never carried the probed media length,
so the exporter inferred it from the furthest clip and rounded down. The
pipeline now probes the source duration once and passes it through; the audit
gains a check that is not frame-tolerant, because one frame over is still
over.

**3. The audit's own overlap check could never fire.** The record-span cursor
was reset per event, and an event is one edit split across channels — so the
cursor was empty for every channel on every event. Caught by the audit's own
test suite, which is the argument for testing the validator as well as the
product.

**4. A missing render was a hard failure.** An export that deliberately
rendered only one aspect ratio failed the audit. Now a SKIP.

---

## What is NOT verified

Stated plainly, because these are the things that would bite in a customer
trial:

1. **No NLE has imported a SelekyT timeline.** Not Final Cut Pro, not DaVinci
   Resolve, not Premiere Pro, not Avid. There is no NLE installed in this
   environment. This is the open gap G2 in `docs/product-gap-map.md` and the
   single biggest credibility risk in the whole venture — see
   `docs/CUSTOMER_DISCOVERY.md`.
2. **The FCPXML is not schema-validated against Apple's XSD.** Apple's
   published schema could not be retrieved (candidate URLs returned 404/403;
   web search returned nothing). The vocabulary check is a *guard over the
   elements SelekyT emits plus the structural containers an NLE accepts* — it
   is deliberately not a claim that Apple's full schema was applied. A
   document can pass this check and still be rejected by FCP.
3. **Interchange is one direction and one shape.** SelekyT writes EDL and
   FCPXML; it does not read either. Multi-cam, roles, captions-in-FCPXML,
   transitions and effects are not emitted; a cut with no video is not
   represented. Nothing here is tested against Avid's interchange (AAF/OMF),
   which SelekyT does not emit at all.
4. **Frame-rate and NTSC coverage is thin.** Everything measured is 29.97 fps
   NTSC non-drop. 23.976, 24, 25, 50 and 59.94, and drop-frame timecode, are
   exercised in unit tests but never by a real encode-and-import cycle.
5. **Relinking is untested against real NLE behaviour.** DaVinci binds an
   asset by three checks — path, source-media start timecode, and frame-rate
   format — and the emitter handles the timecode offset for exactly that
   reason. But that handling has never been observed by Resolve.

---

## Per-NLE expectations — RESEARCHED, not tested

Read this section as *what to look for*, not as a result. Each line is from
vendor documentation, not from an import we performed. Treat every one as
unverified until this page carries a real result.

- **Final Cut Pro.** FCPXML 1.11 is the native interchange. FCP is the most
  likely to accept these files as written. Risk: the strictest schema
  validation of the three, so gap 2 above bites hardest here.
- **DaVinci Resolve.** Reads FCPXML and CMX 3600 EDL. The known trap is
  relinking: Resolve binds an asset by path *and* source-media start timecode
  *and* frame-rate format; a mismatch on any one fires the relink dialog even
  when the path is right. SelekyT already stamps the asset's start TC and
  shifts clips by the same offset, and marks `tcFormat="DF"` for NTSC
  drop-frame. Untested. Resolve's free tier is the cheapest way to close G2.
- **Premiere Pro.** Reads FCPXML via its XML import and CMX 3600 EDL. Its
  import is more permissive than FCP's and more likely to retime silently
  rather than reject.
- **Avid Media Composer.** Not supported. Avid's interchange is AAF; SelekyT
  emits neither AAF nor OMF. Do not claim Avid compatibility.

---

## The experiment that closes this

The cheapest high-value experiment available to SelekyT, and the one that
gates the entire customer thesis (`docs/CUSTOMER_DISCOVERY.md` calls this out
as Step 0):

> Install DaVinci Resolve (free tier) on a machine with a display. Import
> `out/web/pipeline/project/timeline/01.edl` and `01.fcpxml`. Record, for
> each: does the project open; are the clips online or offline; does the
> timeline duration equal the rendered `facebook/01.mp4` (24.892 s); do the
> source in-points match the selection (414.30 s); is there a relink dialog.

Cost: zero capital, one machine with a GUI. Resolve on Linux needs an
unsupported workaround or a VM, so a macOS or Windows machine is preferred.

The pass criterion is specific: **the sequence opens with all clips online
and its duration matches the render to within two frames.** Anything less is
a defect to fix, not a result to report. Repeat for Premiere Pro and Final
Cut Pro. Record the NLE, the exact version, and the result for each in this
file, and move the line from "RESEARCHED" to "VERIFIED" — or delete the claim.

Until then, every external statement about NLE compatibility should say what
this page says: structurally validated by an independent reader, duration
verified against the render, **never opened in an NLE**.

---

## Reproducing

```sh
# The audit. pycmx lives outside the project venv so SelekyT's own
# dependencies cannot influence it; without it that check reports SKIP and
# the rest still run.
PYTHONPATH=/tmp/nlelibs:tools .venv/bin/python tools/nle_check.py \
    out/web/pipeline/project

# The tests, including three that fail the audit on purpose.
.venv/bin/python -m pytest tests/test_nle_check.py tests/test_timeline_build.py -q
```

Exit code is nonzero if any hard check fails, so this composes with CI the
same way `tools/editorial_audit.py` already does.

Three tests deliberately break the audit rather than the product — a relative
asset URI, an overlapping record span, and a clip one frame past its asset.
A validator that cannot be failed proves nothing.
