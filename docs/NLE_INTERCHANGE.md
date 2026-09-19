# NLE Interchange — which format actually opens where

All facts below were retrieved 2026-09-19 from the vendors' own current
documentation (linked at the end). Nothing here is inherited from an earlier
report or assumed from the format's reputation.

## The assumption this corrects

It is tempting to treat FCPXML as the universal interchange format, because it
is the richest one SelekyT emits and the one Final Cut Pro natively reads.
**It is not universal.** Premiere Pro's own documentation states it "does not
recognize" `.fcpxml`, and Avid Media Composer's 1317-page editing guide
contains no FCPXML import path at all. Shipping only FCPXML would reach two of
the four editors SelekyT needs to reach and silently exclude the other two.

## Import coverage

| Format | DaVinci Resolve 21 | Premiere Pro 2026 | Final Cut Pro 11 | Avid Media Composer 2025 |
|---|---|---|---|---|
| **EDL (CMX 3600)** | **yes** | **yes** | no | **yes** |
| **FCPXML** | **yes** | no | **yes** | no |
| FCP7 XML (xmeml) | yes | yes | no | no |
| AAF | yes | yes | no | yes |
| OTIO | yes | no | no | yes (new in 2025.6) |
| OMF | not documented | no | no | yes |

EDL is the best single target, and FCPXML is the one that reaches the editor
EDL misses. SelekyT's existing dual export is therefore the correct design
rather than a belt-and-braces habit: **EDL covers Resolve, Premiere and Avid;
FCPXML covers Final Cut Pro and Resolve. Together all four open the cut
natively, with no third-party converter in the path.**

EDL's well-known limits cost SelekyT nothing specifically because its
timelines are a single video clip per cut list: the "one video track" rule is
not a constraint, and there are no transitions to degrade into cross
dissolves.

## Two operational constraints the research turned up

1. **Reel names must be ≤ 8 characters.** 8 is the CMX 3600 standard;
   Premiere's 32-character option is a non-default extension and will not
   round-trip cleanly. Resolve's conform additionally "requires reel names and
   accurate timecode" — an EDL with a placeholder reel name fails the
   editor's conform at import, after SelekyT has already reported success.
2. **EDL carries no caption data at all.** FCPXML is the only format in this
   set that round-trips captions natively. Caption fidelity is a second,
   independent argument for keeping the FCPXML export beside the EDL rather
   than collapsing to EDL alone.

## What is still not verified

**No NLE has opened a SelekyT artifact.** This is the central gap in
SelekyT's product evidence and it is stated plainly rather than papered over.
This host has no NLE installed and no display server, so "timeline opens,
media links, edit points land" has been verified only by the mechanical audit
in `tools/nle_check.py`, which checks that the artifact is well-formed and
self-consistent — not that a real NLE accepts it. The audit passing is
necessary and is not sufficient.

The specific things still untested, in the order an editor would hit them:

- DaVinci Resolve importing the EDL and relinking against the real source,
  with the conform landing on the same frames the audit reports.
- Whether the 8-character reel name actually survives import on each NLE.
- FCPXML importing into Final Cut Pro with captions attached and in sync.
- Resolve's FCPXML import, which is a second independent reader for that
  format — SelekyT's only independent readers today are `pycmx` (EDL) and
   the XML well-formedness check.

## Format details SelekyT depends on

- SelekyT writes FCPXML 1.11. Apple supports "the current version, or one of
  two previous versions"; 1.10+ is required for the object tracking and
  Cinematic features of FCP 10.6. SelekyT's 1.11 sits comfortably inside that
  window.
- Resolve can import "Final Cut Pro 7 or Final Cut Pro X" XML and can convert
  between them, so SelekyT's FCPXML is readable by both of the NLEs in that
  column.
- CMX 3600 declares no frame rate of its own, so an EDL is only meaningful on
  the grid it was written on. SelekyT validates its own EDL against the
  probed source rate for this reason; a reader that assumes 30 fps will
  misread a 24 fps cut.

## Sources

Retrieved 2026-09-19. Adobe's helpx returns 403 to this host, so those pages
were read via the Wayback Machine; the FCPXML-negative and EDL statements are
explicit and unambiguous in the archived text.

- DaVinci Resolve 21 Reference Manual — https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_21_Reference_Manual.pdf
- Avid Media Composer v2025.x Editing Guide — https://resources.avid.com/SupportFiles/attach/Media_Composer/Media_Composer_v2025.x_Editing_Guide.pdf
- Avid Media Composer v2025.6 What's New — https://resources.avid.com/SupportFiles/attach/Media_Composer/Media_Composer_v2025.6_What's_New.pdf
- Apple FCP User Guide, Use XML to transfer projects — https://support.apple.com/guide/final-cut-pro/use-xml-to-transfer-projects-verdbd66ae/mac
- Apple FCP User Guide, Supported export formats — https://support.apple.com/guide/final-cut-pro/supported-export-formats-vere9bbb1cc4/mac
- Adobe helpx (archived): Supported file formats; Export a project as an EDL
  file; Importing XML files from Final Cut Pro 7 and Final Cut Pro X;
  Importing AAF project files from Avid Media Composer; Export a project as a
  Final Cut Pro XML file.
