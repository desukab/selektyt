# Vendored third-party provenance

Every entry here is a copy of an external project at a recorded commit, with the
upstream licence text committed next to the code. This file is the record that
lets a later reader re-verify origin and licence without archaeology, and it is
the place to look before redistributing any of it.

## `otio_fcpxml_adapter/` — an independent FCPXML 1.0–1.14 reader

**What it is.** `third_party/otio_fcpxml_adapter/fcpxml.py` is the
OpenTimelineIO FCPXML adapter: it turns FCPXML into OpenTimelineIO objects. Only
the read half is used by SelekyT, via `FcpxXml(text).to_otio()`.

**Why it is here.** Until this was vendored, every FCPXML SelekyT emitted was
validated by SelekyT's own `xml.etree.ElementTree` code — an emitter auditing
itself. The EDL half of the interchange pair had already escaped that with
`pycmx`, a third-party CMX 3600 reader; the FCPXML half had not, and the gap was
recorded as such in `docs/NLE_COMPATIBILITY.md`. This reader is the FCPXML
equivalent of `pycmx`, and it is the specific property that matters: it resolves
`<asset>` media through `<media-rep kind="original-media">` rather than reading
`src` off the `<asset>` element directly. SelekyT writes `src` on the
`media-rep`, so a reader that only looks at the `<asset>` attribute parses the
spine successfully and reports every clip as unlinked — "parsed by a library"
while dropping every media reference. That is the exact false positive this
reader exists to avoid, and the corpus sweep below is the evidence that it does.

**Origin.** `https://github.com/latenitefilms/otio-fcpx-xml-adapter`, branch
`fcpxml-v1-14-support`, commit
`20d9fa54141149c09d976f3520a8c29d8e5ad124` (2026-03-14). That branch is upstream
PR #7, "Add support for FCPXML v1.0 to v1.14", by Chris Hocking (LateNite
Films), who maintains the adapter — this is the project's own line of
development, not an arbitrary fork. The identical branch and commit also exist
under `peter-targett/otio-fcpx-xml-adapter`.

**Licence.** Apache-2.0. The full text is committed alongside the code as
`third_party/otio_fcpxml_adapter/LICENSE` (sha256
`cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`, the
canonical Apache-2.0 text). Apache-2.0 is permissive and compatible with this
project's distribution model; it requires preserving the copyright and licence
notices when redistributing, which is why the file ships next to the code.

**Not modified.** The vendored `fcpxml.py` is byte-identical to the file at the
commit above: sha256
`fbaa6f40907fdcea5de6483c804632c0cf5880e2f7c9672049a2d9cbd7f7075c`, 2524 lines.
To re-verify, fetch
`https://raw.githubusercontent.com/latenitefilms/otio-fcpx-xml-adapter/20d9fa54141149c09d976f3520a8c29d8e5ad124/src/otio_fcpxml_adapter/fcpxml.py`
and compare. Only the reader is exercised; the adapter's OTIO-to-FCPXML writer
(`FcpxOtio`) ships unused because splitting it out of the single upstream file
would be a modification, and an unmodified copy is what keeps this record
checkable.

**Not on PyPI.** The upstream package `otio-fcpxml-adapter` has no released
wheel; the branch is the only source. Vendoring rather than declaring a git
dependency keeps `pip install -e '.[nle]'` offline-installable, which is the
same local-first constraint the vendored YuNet weights exist for.

**One upstream packaging defect, not patched around here.** The upstream
`pyproject.toml` declares `license = { file="LICENSE" }` but the repository
ships the file as `LICENSE.md`, so `pip install otio-fcpxml-adapter @
git+https://github.com/latenitefilms/otio-fcpx-xml-adapter@20d9fa5...` fails
under hatchling with `OSError: License file does not exist: LICENSE`. Vendoring
makes the defect irrelevant to SelekyT. It is recorded because it is a real bug
in a project SelekyT now depends on, and it is worth reporting upstream.

**Runtime requirement.** The module imports `opentimelineio`, so the FCPXML
independent check needs it. It is declared in the `nle` optional extra
alongside `pycmx`; the base install is unchanged, and a build without the extra
reports the reader as unavailable rather than silently skipping.
