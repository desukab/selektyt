# SelekyT Technical Audit

Date: 2026-09-19 · Branch: `main` · Auditor: Agent 8 (Technical)

## Scope and method

Every claim below is labelled:

- **VERIFIED** — I reproduced it. I ran the code (the test suite, extracted
  functions in isolation, `ffprobe`/`du` on real artifacts) and observed the
  result.
- **INSPECTED** — I read the code and the claim follows from it, but I did not
  execute the path that would prove it.
- **SUSPECTED** — I believe it from reading, but the evidence is indirect or
  the path is not reachable from what I ran.

What I ran: the full suite twice (`824 passed, 10 skipped` in 63.96 s; first
run 822 passed / 10 skipped in 78.33 s — the docs cite 819, slightly stale),
`ffprobe` and `du` over `out/`, and the `guardedPath` function extracted from
`web/server.mjs` and executed against crafted inputs. I did not run the
pipeline and I wrote nothing outside this file.

The audit is informed by the repo's own honesty. `docs/AI_EXECUTION_STATE.md`
records that YouTube has been fetched **once** from this environment, with no
artifact kept (the blocking is per-video and intermittent, not a hard IP ban),
and that no NLE has ever opened an exported timeline. Several findings below
are the code-level consequence of that attested gap between "the tests pass"
and "a user can rely on the output".

---

## Executive summary

SelekyT is unusually well-engineered for its stage: atomic state writes,
per-clip checkpointing, a single timecode source of truth, SAR-aware
reframing, and two purpose-built falsifiable audit tools. The serious problems
are concentrated in three places, none of them in the algorithms:

1. **The web shell used to have no authentication, no origin policy, and a
   prefix-based path jail that was bypassable** (`web/server.mjs`). All three
   are fixed: a foreign `Origin` is refused before a mutating route parses its
   body, a non-JSON `Content-Type` never reaches `JSON.parse`, and the jail
   boundary includes the separator. The residual — no token, local clients
   trusted — is stated in row #1 and is a product-shape decision, not a defect.
2. **Credentials are persisted into state and reachable over that
   unauthenticated channel.** `pipeline_state.json` stores the full yt-dlp
   `media_info` blob, which embeds the `Cookie` header, and `/api/file` will
   serve it.
3. **The pipeline's QA cannot detect a wrong output.** `engine/qa.py` checks
   that a video exists and is roughly the right orientation; it never checks
   that the shipped MP4 matches the exported timeline, that captions are
   present, or that the caption style the user asked for is the one burned
   into the pixels. The repo already contains tools that *would* check these
   — `tools/nle_check.py`, `tools/editorial_audit.py` — and never wires them
   in.

The single most severe risk and the most embarrassing item are both in area
2, and are stated at the end of this document.

---

## 1. Architecture

### Layering is sound where the pipeline is concerned

The call graph is genuinely layered: `selekyt/cli.py` and `selekyt/server.py`
(transport) → `selekyt/pipeline.py` (orchestration) → `engine/*` (domain:
`edit_plan`, `editorial`, `context`, `reframe`, `captions`, `render`,
`timeline`, `nle_export`, `qa`) → `providers/*` (IO and third-party:
`youtube`, `whisper`, `sherpa`, `ffmpeg`, `atomic`). No `engine` module
imports `selekyt`, and no `provider` imports `engine`. That is a real
boundary, and it holds.

### Two FCPXML emitters — VERIFIED; the policy divergence is CLOSED

`engine/fcpxml.py` is a CutList-driven emitter; `engine/nle_export.py` is a
Timeline-driven emitter. Both are live, in different products:

- `selekyt/pipeline.py` imports `nle_export` and calls it — this is what writes
  the timelines a user actually receives.
- `selekyt/server.py`'s `op_render_fcpxml` implements the sidecar's
  `render_fcpxml` op with the **older** cutlist emitter, documented there as
  matching the Rust sidecar's op of the same name (asserted in
  `test_sidecar_parity`).

So the interactive web path and the batch pipeline do emit FCPXML through
different code. Both route their timecode math through `engine.timecode`, so
the *arithmetic* has one source of truth. **The *policy* now does too — this
is the part of the finding that has closed.** Both `_to_file_uri` functions
were recorded here as divergent, the cutlist emitter raising and the timeline
emitter "silently falling back"; that description is stale, and inspecting
both today shows the same guard on both: an unresolvable path raises a
`RuntimeError` naming the path, because a relative `src` imports with every
clip offline and a fallback would ship that under a URI that looks complete.
The alignment is pinned by
`tests/test_fcpxml_multitrack.py::test_an_unresolvable_asset_path_fails_instead_of_shipping_a_relative_uri`,
which asserts the raise on both emitters in one test and was
mutation-verified (a silent fallback reintroduced on the cutlist side fails
exactly that test).

**Still open, and now the whole of the finding:** the two emitters are
separate by design — the cutlist one speaks AutoCut's keeps/removes
vocabulary and the timeline one speaks an editor's clips, and each is
self-contained — but the consequence is two code paths that can drift in ways
the alignment test does not cover (element vocabulary, gap handling, lane
assignment), plus a second `Track` dataclass (`engine/fcpxml.py` alongside
`engine/timeline.py`) representing the same concept. Merging them would be a
rewrite of a working export surface for no user-visible gain, so it is not
done; the cost is accepted and recorded rather than pretended away.

### Duplicated geometry work in the reframe layer — INSPECTED

`engine/reframe.py` exposes `vertical_crop_filter` (`:153`) and
`crop_x_expression` (`:172`). Each independently calls `_probe_and_track`
(`:76`), which opens a `VideoCapture` and decodes 28 sampled frames. Called
together for one interval that is two full tracking passes; the filter
function exists precisely so the caller cannot get width and x out of sync
(`:155-161`), but the `crop_x_expression` public API invites exactly the
duplicated decode it was built to prevent.

### The CLI/web contract disagreement is real and deliberate — VERIFIED

`selekyt/cli.py` accepts any local source path and any `--out` directory with
no confinement. `web/server.mjs` `resolveMedia` confines a local source to the repo root
via `guardedPath`, and `route` hardcodes `out: join(OUT_WEB, "pipeline")`. The
web comment at `:299-300` documents that the cookies path is *deliberately*
not confined. So the two entry points disagree on three axes (source path,
output location, cookie path), and `docs/product-gap-map.md` confirms this is
a decision rather than an oversight. It is a defensible one, but it means
behaviour verified through the CLI is not what the web user gets, and the two
are tested separately rather than against a shared contract.

Web exposure is also narrower than the CLI: `selekyt/cli.py` exposes 16
tunable flags; `/api/run_pipeline` (`web/server.mjs` `assertSameOrigin`) forwards 9 of
them and omits every audio-analysis parameter, while `/api/export` forwards
the audio parameters that `/api/run_pipeline` omits. `vad_backend` is
reachable from neither.

### `providers/*` is a clean seam — INSPECTED

`providers/whisper.py`, `sherpa.py`, `youtube.py`, `ffmpeg.py`, `atomic.py`
are narrow and substitutable; the transcription fallback chain (faster-whisper
→ sherpa-onnx → silero) is selected behind the provider boundary, not
threaded through the engine. This is the best-factored part of the codebase.

---

## 2. Security

This is the area that would fail an enterprise review, and it fails not
because of a subtle bug but because several independent weaknesses combine.

### There is no authentication at any layer — VERIFIED, then FIXED at the boundary that mattered

`web/server.mjs` binds `SELEKYT_HOST || "127.0.0.1"` and still listens with no
bearer token and no session — and deliberately so, because a local single-user
tool that demands a token has to store it somewhere the page can read, which is
not an improvement. What it lacked, and what the finding is really about, is
**no origin policy at all**: every route in the manual router (`/api/run_pipeline`,
`/api/export`, `/api/vad_analyze`, `/api/segment`, `/api/cutlist_from_segments`)
accepted a POST from any origin, and the Python sidecar's own comment at
`selekyt/server.py:108-121` is explicit that it is not an authorization
boundary — a correct local design decision that the HTTP layer in front of it
used to make meaningless.

Because `getJson` never inspected the request `Content-Type` (VERIFIED at the
time: `Content-Type` appeared only on *outgoing* responses), a cross-origin
`text/plain` HTML form POST carrying valid JSON reached `JSON.parse` with no
CORS preflight. That is the classic JSON CSRF primitive, and it needed no
script and no cooperation from the user beyond loading a page. `127.0.0.1`
binding did not stop it — the browser is on the same host.

**Fixed**, in the two halves that made the primitive work (`web/server.mjs`):

1. `assertSameOrigin` runs before any mutating route parses a body. A request
   carrying an `Origin` must match the origin the page was served from, built
   from the request's own `Host` so `localhost:4242` and `127.0.0.1:4242` both
   pass; a request carrying only a `Referer` must match it too. The comparison
   is `origin === expected || origin.startsWith(expected + "/")` — the same
   boundary reasoning as `guardedPath`, for the same reason.
2. `getJson` now requires `application/json` before it parses, which an HTML
   form cannot send. The form-POST primitive is therefore dead regardless of
   what a future browser does with `Origin`.

Both were mutation-verified against `tests/test_web_harness.py`: disabling the
origin check fails exactly the two cross-origin tests; disabling the
content-type check fails exactly the `text/plain` test; a blanket refusal
fails thirteen, including the two positive tests — so the suite pins the gate
in both directions rather than only "a refusal happened".

**The desktop shell is untouched by this and needs to be.** Its transport is
Tauri's `invoke("selekyt_rpc")` (`apps/desktop/ui/app.mjs`), not HTTP; the
`fetch` fallback below it is only taken in a plain browser, where the page and
the API share one origin. So a same-origin gate on this harness constrains
nothing the packaged app does — and a gate loose enough to admit a Tauri
webview's own origin (`tauri://localhost`, `http://tauri.localhost`) would be
loose enough to admit an attacker's, which is why it was not loosened that way.

**The residual, stated separately rather than folded into "fixed":** a request
with neither `Origin` nor `Referer` is accepted, and there is still no token.
That is the deliberate boundary. The threat model closed here is a *remote*
page reaching a local service through the user's browser; a local client is
already inside the trust boundary a localhost bind draws. Adding an
unforgeable local secret is a real change in product shape (multi-user or
remote access), and is not claimed here.

### `guardedPath` is a prefix check and is bypassable — VERIFIED, then FIXED

`web/server.mjs` `guardedPath`:

```js
function guardedPath(raw) {
  const candidate = resolve(String(raw || ""));
  if (candidate.startsWith(ROOT)) return candidate;
  throw new Error("path must be inside the project");
}
```

I extracted the function and ran it with `ROOT=/tmp/gptest/selektyt`.
`/etc/passwd` is blocked. `/tmp/gptest/selektytEVIL/secrets/credentials.txt`
**passes** — `String.prototype.startsWith` is not a path-boundary test, so any
sibling whose name extends the repo name (`selektyt-backup`, `selektyt.old`,
`selektyt-anything`) escapes the jail entirely. `test_a_sibling_directory_extending_the_project_name_is_rejected`
asserts that `/etc/hostname` is rejected, and it passes — while the bypass
stands. The test proves the guard blocks the obvious case and nothing else.
The correct check is `candidate === ROOT || candidate.startsWith(ROOT + sep)`.

**Fixed** (the correction above is what the code now does), and the sibling
case is now a regression test —
`tests/test_web_harness.py::test_a_sibling_directory_extending_the_project_name_is_rejected`
— alongside the obvious one. The audit's point about the test was the real
finding, and it applied to the test as much as to the guard: the test that
asserts `/etc/hostname` is refused passes either way, so it could not detect
the regression. The new test builds a real sibling of the repo, sends the
*absolute* path (the shape of the bypass — no `..` in it, so a traversal
filter would not catch it either) and asserts it is refused; it fails against
the one-line guard above and passes against the separator version.

### `/api/file` serves the whole repo, not the output directory — VERIFIED, then FIXED

`web/server.mjs` `route` passed `guardedPath(url.searchParams.get("path"))`, and
`guardedPath` confines to `ROOT`, the repository, not `OUT_WEB`. So `.git/`,
`selekyt/`, `.env`, and every other user's `out/web/uploads/` file were
readable by any unauthenticated caller. Only the static-file route at `:334`
bothered to confine itself to `WEB`.

**Fixed.** The download route now goes through a separate `guardedArtifact`,
whose boundary is `OUT_WEB + sep`. `guardedPath` still governs the *source*
path a user points the pipeline at, where the repo root is the right scope —
the two boundaries are now different because the two operations are different.
`tests/test_web_harness.py` asserts `web/server.mjs`,
`docs/TECHNICAL_AUDIT.md` and `.git/HEAD` are all refused; both of those tests
fail against the old guard.

### Cookie credentials are persisted and then served — VERIFIED (composition), INSPECTED (mechanism), then FIXED at the persistence boundary

`selekyt/pipeline.py:206` writes `media_info` into `pipeline_state.json`.
`media_info` is the full yt-dlp `--dump-single-json` blob (INSPECTED at the
call site), which embeds the `Cookie` header used for the download. The
cookies module itself is careful — `selekyt/cookies.py:36-52` sanitizes tab/newline
in values, `:55-74` `chmod(0o600)` and emit only a count plus a path, never a
value; `providers/youtube.py:30-46` resolves and validates the file without
printing it. That care was discarded the moment the blob was persisted, and
`/api/file` would have served it to anyone who asked for `out/web/pipeline/pipeline_state.json`.

**Fixed at the write, not the read.** `media_info` now passes through
`_scrub_credentials` before `state.update`, which drops `http_headers`,
`cookies`, `_cookies`, `cookie_manager`, `cookiejar` and `cookies_str` at every
depth (the per-format entries carry request headers too). Nothing downstream
reads those keys — the pipeline consumes duration, title and format ids — so
the denylist is explicit rather than fuzzy, which is what keeps it from also
dropping format metadata the QA reports read. No credentials have ever been
captured on this host: the only URL source ever run is the unauthenticated
archive.org one, and a grep for `Cookie` across `out/` and every corpus project
returned zero hits. This was a latent defect, not a demonstrated leak, and it
is now closed on both sides — the blob is never written, and the route that
would have served it no longer can.

### SSRF is reachable — INSPECTED, then PARTLY FIXED at the boundary

`web/server.mjs` `resolveMedia` accepts any `https?://` `source` and hands it to the
pipeline. `selekyt/server.py`'s `_pipeline_source` rejected `file://` but
placed no restriction on host or port, so `http://169.254.169.254/latest/meta-data/`
(the cloud metadata endpoint) was a valid source, fetched from the server's
network position, and any response error text comes back to the caller. This
is the highest-impact CSRF target: an attacker does not need to read the
response, only to make the request.

**Partly fixed 2026-09-22.** `_pipeline_source` now refuses the metadata
endpoints before the URL is ever handed to the downloader: the whole IPv4
link-local range `169.254.0.0/16` (which contains 169.254.169.254 for AWS,
Azure and GCP alike), IPv6 `fe80::/10`, the AWS IPv6 metadata address
`fd00:ec2::254`, and the DNS names `metadata.google.internal` and
`metadata.azure.com`. The refusal message names the host it objected to. The
guard is on the only URL-fetch entry point the web shell has — `/api/export`'s
`source` is provenance only, since `op_audio_analysis` requires `media` to be
a regular local file, and `resolveMedia` in `web/server.mjs` never fetches.

**What this does not close, stated plainly:** it is a boundary check on the
URL as written, not egress filtering on the connection. A hostname that
resolves to a link-local address — DNS rebinding, or an internal name — is not
caught here, because yt-dlp resolves at fetch time and this function sees the
string the caller typed. Port is unrestricted, so a request to a service on
the local network is still possible. The half it closes is the documented
vector, which is a literal IP an attacker can type; the other half needs a
fetch-time check and is not addressed. Pinned by
`tests/test_server.py::test_pipeline_source_refuses_a_cloud_metadata_endpoint`
(7 URLs; mutation-verified — disabling the guard fails exactly those 7 and
nothing else) with a sibling test that ordinary URLs still pass, so the guard
cannot be a blanket rejection.

### Subprocess invocation is correct — VERIFIED

Every `subprocess`/`spawn` call in `engine/`, `providers/`, `selekyt/` and
`web/` is list-form with no `shell=True`. `providers/youtube.py:118` runs
`stdout=2, stderr=PIPE` specifically to keep yt-dlp progress off fd 1 so it
cannot corrupt the sidecar's line protocol — a detail that shows the protocol
was attacked rather than assumed. `engine/render.py:33-34` builds the
`subtitles=` filter value with shell quoting of its own input, but the path
comes from `ass_for_candidate`'s own temp output, never from the user.

### What untrusted input reaches — VERIFIED

The user-controlled values that reach subprocesses are: the source URL/ path,
`--cookies` path, `model`, `language`, and `caption_style`. All are passed as
list arguments. `caption_style` is process-global (`selekyt/cli.py:177` sets
`os.environ["SELEKYT_CAPTION_STYLE"]`; `selekyt/server.py:329-358` sets and
restores it per request); a value outside the known preset set is not
validated at the boundary I inspected, but it is only ever interpolated into
ASS text, not a command. The sidecar's `_HANDLERS` (`selekyt/server.py:410-436`)
is a fixed whitelist of 17 ops with unknown ops raising — genuinely safe
against method-name injection.

### Error messages leak internals — INSPECTED

`selekyt/server.py:456-474` catches everything and returns `str(exc)` to the
caller, and `web/server.mjs` `spawnChild` slices up to 2000 chars of stderr into
rejection messages. Full filesystem paths, provider error text, and stack
summaries travel to any caller.

---

## 3. Reliability

### Interrupt recovery is the strongest part of the system — INSPECTED

`selekyt/pipeline.py:33-77` quarantines a corrupt state file and recovers from
`.bak`; `:80-94` rotates a backup only from a parseable version and writes via
`os.replace` on a same-directory temp (`providers/atomic.py:24-34`). The render
loop checkpoints after each clip (`:431-432`) rather than once at the end, and
the comment at `:385-387` records that the earlier single-end-of-run
fingerprint made a killed run unresumable. This design works: an interrupt
during clip 3 of 5 loses nothing.

### Partial-failure semantics: captions and renders can disagree — VERIFIED then FIXED

The order in the render loop was the defect:

- write `captions/NN.ass`, `.srt`, `.json` unconditionally, then
- consult the render cache, and render only on a miss.

The render cache key was `_fingerprint` (source, resolved path, size, mtime,
model, language) plus `_selection_digest` (sha256 of `start,end,text`).
**Neither included the caption style**, and distinct `SELEKYT_CAPTION_STYLE`
values provably produce distinct `ass_for_candidate` bytes — so a re-run with a
different style rewrote `captions/NN.ass` to the new style, took the cache hit,
and **shipped the MP4 that had the old style burned into it**. The video and
its sidecar caption file disagreed, and nothing downstream detected it:
`engine/qa.py` never checks that captions are present, and `nle_export`
re-derives the timeline from `retention_intervals`, not from the shipped file.

**FIXED.** `selekyt/pipeline.py` now reads `caption_style = style_name()` and
requires `cached.get("caption_style") == caption_style` before reusing a
render, and writes that style back into the cache record. A record with no
style key — which is what every pre-fix `pipeline_state.json` looks like —
reads as a miss rather than as a match against `minimal`, so the corpus
re-renders once instead of staying on an unvalidated key. Pinned by
`tests/test_caption_style_cache.py`: a style change forces a re-render, the
same style still reuses the cache, a record with no style misses, and the
record carries the style that was burned in.

**What remains true and is not a defect:** the captions are still written
before the cache is consulted, so a run killed in that window leaves caption
artifacts for a clip whose render never happened. They are regenerated on
resume, but it means the presence of `captions/NN.ass` is not evidence of a
completed render.

### The known "export failed after renders" case — INSPECTED

`:437-452` builds the timeline, exports EDL/FCPXML, validates them, and
`raise`s on any problem — after the MP4s are on disk and the per-clip state is
checkpointed at `:432`. This is the case the task flags. It recovers correctly
on resume (the cache hits, the timeline re-exports), so it is not a
data-loss bug. It is a *reporting* bug: the run dies having produced a
finished, payable deliverable, and the exit code says failure.

### The export-vs-render comment overstates a guarantee — INSPECTED, then measured in two legs

The comment at the export site reads that the timeline is built from the same
retention_intervals the render concatenated, so its duration is the rendered
file's duration. That was an assumption, not a check: nothing at the export
site measured the rendered MP4. **The render is measured now**, so the
finding's sharper form — "the rendered MP4's own duration is never measured
anywhere in the pipeline" — is no longer true, and the comment has been
rewritten to say what is actually verified rather than what was assumed.

The agreement is checked in **two legs that meet at the edit plan**, not one
direct comparison:

1. `validate_edl` / `validate_fcpxml` (and, since 2026-09-22, the independent
   `pycmx` parse) check the *timeline* against the plan on the frame grid it
   was written on.
2. `write_report` probes the *rendered MP4s* and compares their durations
   against the same plan's `edited_duration` at 1% — roughly seven times the
   worst drift measured across the corpus (0.025 s in 17.08 s).

A render that silently dropped an interval is off by a whole clip and lands
far outside 1%, so the failure this check exists for is caught. What is still
not measured directly is sub-1% slop: container muxer clocking plus per-
interval `-t` clamping can move the shipped file a frame or two from the
timeline, which is exactly why `tools/nle_check.py:52-63` documents a measured
worst case of 0.69 frames (pentagon-esper-briefing clip 04, 28.762 s declared
against 28.785 s rendered) and carries a two-frame tolerance. That direct
comparison lives in the tool, not the pipeline, and the tool is still not
scheduled — the open part of top-5 finding #5.

### QA can pass while the output is wrong — VERIFIED, then partly RESOLVED

`engine/qa.py:19-43` (`validate_output`) asserts: file exists, size ≥ 1000
bytes, a video stream, an audio stream, orientation (`h > w` vertical / `w >
h` landscape), and duration ≥ 1.0 s. It does not check: the aspect ratio is
actually 16:9 or 9:16 (only that it is landscape or vertical), that the
duration matches the edit plan, that captions are present, that the audio is
not silent, or that two clips are the same length as each other. Warnings in
`validate_outputs_expanded` (`:212-267`) — silent tail, black frames, low
visual diversity — are computed and then never fail the run. `write_report`
(`:324-345`) raises only on `not report["ok"]`.

**Of that list, the aspect ratio, the plan duration and the silent-audio case
are now checked** — see the resolution note below the code excerpt. The rest of
the list still stands: captions are not verified at runtime, and the advisory
findings still never fail a run, which is by design (a usable render with a
quiet tail is not discarded) and would be a defect only if a *broken* render
slipped through — the check for that is the decode sample and the plan
comparison, not the advisory tier.

`black_frame_pct` (`:138-164`) and `visual_diversity_score` (`:167-209`)
swallow all exceptions to `return 0.0` / `continue`, so a broken OpenCV path
reports a clean bill of health rather than a failure. This is the pattern the
repo's own docs warn about: a metric that fails open is indistinguishable from
a metric that passed.

**Resolved 2026-09-22, except caption presence.** The paragraph above was
written against the code as it stood; what each part does now:

- **Aspect ratio is checked precisely.** `_display_aspect` (`:19-35`) derives
  the display aspect from the stream's sample aspect ratio rather than reading
  the container's often-unpopulated `display_aspect_ratio`, and
  `validate_output` (`:271-278`) errors when it is more than 1% off 16:9 or
  9:16. This was found by measuring, not by reading: a real anamorphic source
  (720×486, SAR 8:9) shipped a landscape deliverable at DAR 320:243 — 4:3 —
  labelled 16:9, and nothing else in the pipeline noticed.
- **Duration is compared against the edit plan.** `write_report` (`:639`)
  reads the `edit_plan.json` the pipeline writes beside the outputs, maps each
  entry's `edited_duration` to the render's stem, and `validate_output`
  (`:327-341`) errors beyond 1% drift. The tolerance is roughly seven times the
  worst drift measured across the corpus's 18 real renders (0.025 s in 17.08 s,
  0.15%); a dropped retention interval is off by a whole clip and lands far
  outside it. This is the only check that can see a wrong *cut* — a cleanly
  encoded video of the wrong program decodes, has audio, and passes every
  structural check. Stem-matched, not position-matched, because the plan holds
  one entry per clip and each clip yields two delivers.
- **The advisory metrics no longer fail open.** Both return `None` when they
  cannot measure anything — an unprobeable file, a capture that will not open,
  a sample that decoded no frames — and `validate_outputs_expanded` reports the
  gap in `warnings` and records `null` in `qa.json` rather than recording a
  number that reads as a pass. `black_frame_pct` also divides by the frames it
  actually decoded rather than the frames it asked for, so a capture that
  silently drops seek positions cannot dilute the ratio toward "clean".
  Note the deliberate asymmetry: a *single* clip still scores a defined
  diversity of 1.0, because with no peer there is nothing to measure and the
  value is a definition, not a claim.
- **Caption presence remains open**, and deliberately so. There is no cheap
  exact signal for "the subtitle filter ran"; a sampled-pixel heuristic would
  need a per-clip uncaptioned control that runtime QA does not have, and a
  false failure would discard a good render. The real verification is
  `tests/test_render.py::test_the_burned_captions_are_in_the_pixels`, which
  decodes the rendered MP4 frame by frame against an uncaptioned control of
  the same source — a test-time comparison, not a runtime one.

### The falsifiable audit tools are built and never wired in — VERIFIED

`tools/nle_check.py` and `tools/editorial_audit.py` are the best code in the
repository for catching the classes of bug that survive the test suite.
`nle_check.py` parses the EDL with `pycmx` — an independent CMX 3600 reader —
for the stated reason at `:20-23`: "validating our output with our own parser
proves nothing." It checks relinkable media, record-span overlap, timeline
duration against the rendered file, and EDL/FCPXML agreement.

None of it runs. VERIFIED by grep: the tools are referenced only by
themselves and by one cross-reference at `nle_check.py:23`. They are not
called by `selekyt/pipeline.py`, `selekyt/server.py`, `web/server.mjs`, or
`.github/workflows/ci.yml`. **Correction:** an earlier version of this section
said `pycmx` was not installed and not a dependency, reporting
`ModuleNotFoundError`. That is no longer true and was wrong when the section
below on CI was written — `pycmx>=1.5` is in the `nle`, `dev` and `all` extras
and 1.5.1 is installed here, so the independent-parse check is *able* to run.
The finding that survives is the narrower one: the tool that would catch this
class of bug is still not invoked by anything automated, so in practice the
independent parse only ever runs when a human asks for it. Meanwhile the
pipeline validates its EDL with `engine.nle_export`'s own
validator (`pipeline.py:442-447`), which is precisely the self-validation
`nle_check.py` was written to rule out.

### Concurrency: there is none, and the design assumes it — VERIFIED

A grep for `flock|fcntl|filelock|Lock(|RLock|threading.|asyncio|ProcessPool|ThreadPool`
across `engine/`, `providers/`, `selekyt/`, `web/` returned **zero results**.
Two specific consequences:

- `providers/atomic.py:24-34` writes to a **fixed** `path.name + ".tmp"`. Two
  concurrent writers of one state file race on the same temp filename and one
  silently loses.
- `pipeline_state.json` is read-modify-write with no lock, and the web shell
  hardcodes one project directory (`web/server.mjs` `route`), so two concurrent
  web runs clobber each other by construction.

The single sidecar child serializes requests today (`selekyt/server.py:476-498`
is a `for line in stream` loop), which is why the process-global caption-style
env var is currently safe. That serial loop is also a hard availability
ceiling: one long pipeline blocks `/api/ping`, and one sidecar crash rejects
every queued request (`web/server.mjs` `spawnChild`).

---

## 4. Scalability

### Transcription is CPU-only and the default install cannot transcribe — VERIFIED

`pyproject.toml` lists `opencv-python-headless`, `numpy`, and `yt-dlp` as base
dependencies. `faster-whisper` exists only in the `transcription` extra. A
plain `pip install selekyt && selekyt source.mp4` therefore fails at
transcription with no engine available. This is a packaging defect that
presents as a broken product.

### Disk is the binding constraint, at 100× and at 1× — VERIFIED

Measured on this machine: 13 GB free of 29 GB. One real 24:29 source produces
a project of ~900 MB — `source` 843 MB, `transcription` 46 MB, deliverables
`facebook` 6.5 MB + `shorts` 3.4 MB, `timeline` 28 KB. **92% of the cost of a
project is retained intermediates; the deliverables are ~10 MB.** `out/`
already holds 2.7 GB from two runs. There is no cleanup, no GC, and no
retention policy anywhere in the codebase (VERIFIED by grep). At 100× volume
this is ~98 GB of intermediates against a 29 GB disk — the system fails on
disk before it fails on CPU, and it fails silently, in `OSError` from
`write_json_atomic`, mid-run, after the checkpoint has already been written.

The fix is not more disk: it is deleting `source/` and `transcription/` once
the renders validate, which nothing did. **As of 2026-09-22 something does.**

`selekyt --clean-project <dir>` reclaims exactly those directories from a
finished run, and refuses to touch an incomplete one. It is opt-in and
post-hoc rather than automatic, because the policy question the finding ends
on — which files are safe to delete mid-run — has a different answer for a run
that is still going. Deleting the extracted audio would break resume (a
resumed run needs the checkpoints it already paid for), and deleting the
downloaded source would break renders that have not happened yet. So the gate
is completeness: every render the edit plan called for must be on disk, judged
on the conventional location (`<project>/facebook/<idx>.mp4`,
`<project>/shorts/<idx>.mp4`) rather than on the path recorded in the state,
because that path's shape is not stable across versions — the current run
writes an absolute path, corpus states predating that fix wrote one relative to
the invocation's cwd, and reading the recorded string would find or miss the
file depending on where the caller stands.

Measured on copies of the committed corpus (local sources, so `source/` is
absent — a URL run additionally holds the 843 MB download, which the same
command removes unless `--keep-source` is given):

| project | before | after | reclaimed |
|---|---|---|---|
| `nasa360-ep5` | 115 MB | 54 MB | 60 MB (52%) |
| `royal-society-cloke` | 120 MB | 86 MB | 34 MB (28%) |
| `ooi-vertical` | 69 MB | 64 MB | 4 MB (6%) |

The spread is the point of measuring rather than asserting: the fraction
depends on how much of the source the transcript's chunks cover, and
`ooi-vertical`'s audio is short enough that the cleanup is nearly a no-op. A
cleanup that claimed a single figure would be wrong for two of the three.

`transcript.json` is deliberately kept — a re-run reuses it and skips
transcription, so removing it would trade disk for a re-transcribe on every
later invocation. So are every deliverable and every report. `--keep-source`
exists because the download is also what `tools/nle_check.py` probes to
cross-check the render against the timeline and what a re-render needs; without
it the command clears `state["media_path"]` rather than leaving it pointing at a
file that no longer exists, which would have made a resumed run trust a dead
path instead of re-downloading.

**Still open, and why it is not closed here:** nothing *schedules* the cleanup,
so `out/` still grows between invocations until somebody runs it. An automatic
trigger after a successful run is the honest remaining work, and it needs a
retention policy (how many finished projects to keep, whether the user re-renders)
that is a product decision, not a function. The `OSError` at 100× is deferred
rather than solved.

### Memory: one VAD backend loads the whole WAV — INSPECTED

`EnergyVAD._read_mono` reads the entire decoded WAV into memory. At 100×
volume, with 100 concurrent analyses, that is tens of GB. `providers/sherpa.py:302-348`
`_WindowedWav` already solves this with windowed reads — the better
implementation exists in the same package and is not shared.

### One VAD backend cannot work — INSPECTED, then FIXED to say so

`SileroVADBackend.analyze` always raised `NotImplementedError`, yet
`is_available` asked only whether onnxruntime was importable and a model path
was supplied — both satisfiable on a real host — so the backend could report
*available* and then raise at the call. It was listed as the preferred leg of
the chain, so the chain documented a leg that could not fire, and a user who
selected it got a runtime error with no indication that the backend was never
implemented.

**Fixed 2026-09-22 by making the stub honest, not by implementing it.**
`is_available` now returns False whatever the environment has, because the
inference path does not exist; the class docstring says the backend is not
implemented and explains why it remains in the registry (so `--vad-backend
silero` reports "not implemented" rather than "unknown backend"); the module
docstring now lists the chain as it is actually selected, including EnergyVAD,
which the old docstring omitted entirely though it is the effective default;
and forcing silero now names the real condition and the backends that do work,
instead of "unavailable", which sends a user to install onnxruntime to fix
something the code does not have. Pinned by
`tests/test_vad.py::test_silero_reports_unavailable_even_when_onnxruntime_is_present`
(mutation-verified against the old check) and `::test_forcing_the_silero_backend_says_it_is_not_implemented`.

Implementing the backend is still open and is deliberately not done here — it
needs a model file and an inference path, neither of which this environment
can exercise, so a claim of parity would be unverifiable.

### `make_thoughts` is quadratic with the cap applied after the work — INSPECTED, then FIXED on the cap half

`_words_in` is called per thought; the 4000-thought cap was enforced *after*
the work, so it bounded the output, not the cost.

**The cap half is fixed 2026-09-22, and measured first.** The scaling was
confirmed on a synthetic transcript before anything was changed:

| source length | before | after |
|---|---|---|
| 5 min | 128 ms | 130 ms |
| 10 min | 295 ms | 298 ms |
| 20 min | 742 ms | 738 ms |
| 40 min | 1956 ms | 1169 ms |
| 80 min | 6043 ms | 1822 ms |
| 160 min | — | 3054 ms |

Below the cap nothing changes — the cap is not reached, so the code paths are
identical. Above it the cost is now bounded, growing roughly linearly instead
of superlinearly, and the 80-minute case is 3.3× faster. The cap is also hit
on real corpus runs, not just synthetic ones: `emwcon-panel-2018` (842
segments) and `nasa360-ep5` (425 segments) both return exactly 4000 thoughts.

Stopping inside the loop is equivalent to slicing afterwards **only** because
the list is built in non-decreasing start order, so a thought a later sentence
index would append sorts after the truncated tail and would have been sliced
off anyway. That equivalence is the whole justification for the change, so it
was checked rather than assumed: a differential test ran the old and new
implementations over all 8 committed corpus transcripts and compared start,
end, text, word count, coherence and completeness — all identical. The
invariant is now pinned by
`tests/test_editorial.py::test_the_thought_cap_is_taken_inside_the_scan_and_truncates_in_start_order`,
which asserts monotonic ordering on the synthetic fixture and on every corpus
transcript.

Said plainly, that test is **not** a performance regression test and does not
try to be: the two implementations are output-identical by construction, so no
behavioural assertion distinguishes them. What the test pins is the ordering
invariant that makes the early return correct. The timing numbers above are
measurements, recorded here rather than asserted in the suite.

**Still open:** `_words_in` itself, which is 11% of the remaining cost at 80
minutes (620 ms of 5.6 s before this fix). It rescans `all_segs` from the
front for every thought even though consecutive thoughts share a window, so a
cursor would make it amortise. Not done — it is a smaller share than the cap,
and the correctness argument for a cursor is weaker than the one here.

### Multi-tenancy: state is a single file — VERIFIED

Everything for one project lives in `pipeline_state.json` under one directory
that the web shell hardcodes (`web/server.mjs` `route`). There is no project ID in
the URL, no per-user isolation, no shared-nothing decomposition: the state
file, the render cache, and the upload directory are all singletons. Two
tenants are not a scaling problem, they are a correctness problem — they
write the same file. Any multi-tenant offering requires the project directory
to become a parameter before anything else.

---

## 5. Test quality

The suite is green and large (1034 passed / 12 skipped, re-run 2026-09-20; every
skip is an environment skip on absent optional backends, not a failure). The interesting
question is which of those tests would still pass if the output were wrong.

### Tests that assert what the code claims rather than what the output is

- **`tests/test_render.py:170-176`** — `test_render_with_burned_captions`
  asserted `out.is_file()` and `out.stat().st_size > 0`. It never looked
  inside the pixels. A render with no captions at all passed it. **RESOLVED
  2026-09-22:** the test is gone, replaced by
  `test_the_burned_captions_are_in_the_pixels`, which decodes the MP4 with
  ffmpeg and compares it frame by frame against an uncaptioned render of the
  same flat source, requiring ink, in the caption band, inside the safe area.
  It runs on all four presets; the 9:16 path has its own variant; and
  `test_an_absent_ass_produces_no_ink` pins the other half of the contract.
  The new test was mutation-checked: deleting the `subtitles` filter from
  `_render_one` fails all five. (Compare `:138-167`, the anamorphic test,
  which computes the display aspect ratio from the probed SAR and asserts it
  to 0.005 — that is a real test, and it is the pattern the new one follows.)
- **`tests/test_audio_analysis.py:133-142`** — named
  `test_reproducible_segmentation_across_runs`, it asserted
  `a["speech_regions"] >= 0 and b["speech_regions"] >= 0`, which is true of
  any non-negative integer in both runs. The reproducibility claim in the
  name was never tested. (The cache-reuse assertions above it were genuine.)
  **RESOLVED 2026-09-22:** the test now runs three times — threshold 0.3,
  then 0.5, then 0.3 again — and asserts that the third run's persisted
  regions, region count and speech ratio are *equal* to the first's, that
  both re-runs report `scores_cached is True` and reused the same score
  file, that the 0.5 run produced *different* speech regions from the 0.3
  run, and that its retained speech duration is no greater. The difference
  assertion is what makes the equality one meaningful: a pair of thresholds
  that land on identical regions would leave "the threshold reaches the
  segmenter" untested. On this fixture the energy score saturates at 1.0
  during the bursts, so 0.5 and 0.9 are such a pair; 0.3 and 0.5 straddle
  the boundary where the segmentation actually moves, which is why the test
  uses those two. That measurement is recorded in the test's own docstring.
- **`tests/test_jumpcut_fcpxml.py:138-156`** — asserts the document version,
  that `<asset ` and `ref="r1"` are present, and `content.count("<asset-clip") == 2`.
  Structure, not cuts: no cut time is parsed and compared to the input
  cutlist, so a timeline with the right shape and wrong timings passes.
- **Four tests assert a literal constant**: `tests/test_content_pack.py:70-72`,
  `tests/test_project_lifecycle.py:275`, `tests/test_unicode_pipeline.py:171`,
  `tests/test_pipeline_e2e.py:161` all assert `pack["source_based"] is True`.
  This asserts the code sets a flag to `True`. It is evidence the field
  exists and is spelled correctly, and nothing else; it does not distinguish
  a source-based product from one that rewrites speech. **Left in place,
  deliberately:** the four tests exist to pin that the field is populated at
  all on the real pack paths, and the claim they could be upgraded into —
  that the output media is byte-identical to the source over the kept spans
  — is a different test belonging to the render pass, not a stronger
  assertion in these four. They stay as weak-but-non-vacuous pins; they are
  the remaining item behind row #11's "Mostly resolved".
- **`tests/test_editorial_rank.py:51`** — contained `or True` in an assertion.
  I VERIFIED that `rank()` *does* enforce the 4-second separation this
  appeared to guard (`editorial.py`: `if any(not (c.end <= x.start-4 or
  c.start >= x.end+4) for x in chosen): continue`), so today this was dead
  weight rather than a masked live bug. It was still a landmine: it disabled
  the assertion it was written to carry, and the next person to touch
  `rank()` had no test. **RESOLVED 2026-09-22:** the tautology is replaced by
  the post-condition callers actually depend on — the retained clips are
  sorted by start and every consecutive pair must satisfy `earlier.end <=
  later.start`, and the overlapping candidate must be absent from the
  result. Verified by mutation: deleting the guard from `rank()` fails this
  test alone (`1 failed, 10 passed` —
  `test_rank_rejects_overlapping_candidates`); inverting its sense fails it
  plus `test_rank_rejects_near_duplicate_text` (`2 failed, 9 passed`), which
  is the signature of an assertion covering the defect rather than the happy
  path. Honestly noted: the count assertion fires before the span assertion
  under the first mutation, so I do not claim the span check alone isolates
  the defect — the test as a whole detects a disabled and an inverted
  filter, which is what it needs to do.
- **Four tests assert a literal constant**: `tests/test_content_pack.py:70-72`,
  `tests/test_project_lifecycle.py:275`, `tests/test_unicode_pipeline.py:171`,
  `tests/test_pipeline_e2e.py:161` all assert `pack["source_based"] is True`.
- **`selekyt/pipeline.py:502`** (now `:888`) set `state["complete"] = True`
  and nothing in the codebase ever read it. The resume logic keyed off
  `renders`, not `complete`. A test asserting `state["complete"]` would have
  been asserting a write, not a fact. **RESOLVED 2026-09-22:** the write is
  gone, replaced by a comment at the call site explaining why no such flag is
  written — a write-only flag invites a future reader of
  `pipeline_state.json` to treat it as a guarantee that a later, failing step
  succeeded. Confirmed by grep across the repo, the web shell and the tests
  before deleting: no reader existed, and no test asserted the field.

### The repo's own warning, and the tests that answer it

`docs/AI_EXECUTION_STATE.md` records that two past defects were *asserted by
the code and false*. The correct response is the one `tests/test_project_lifecycle.py`
takes: run a real pipeline on fixture media and cross-check the artifacts
against each other and against independent recomputation. That file is the
model the rest of the suite should follow — `:103-104` cross-checks artifact
index and timecode agreement, `:131-132` recomputes the weighted score from
the weights the `Candidate` documents (to 5e-4), `:157-159` asserts
`edited_duration == Σintervals`, `:168-173` reconciles the pacing census, and
`:188` requires report paths to resolve inside the project. These would fail
on a wrong output. Most of the rest of the suite would not.

### Skipped surface — VERIFIED

Thirteen tests skip, and none of them is the surface a reader would worry
about. Seven are `test_proxy_validation.py`, an opt-in measurement suite that
names the exact recording it needs (a ~239 MB YouTube source, not
redistributable with the repo) and the env var that un-skips it. Five are
`test_wer_bench.py` rows for corpus runs that predate the `wer_err_total` field
in their own run record, so the number the test would assert does not exist
yet; the sixth is a Dutch row with no run record at all. Both groups are
measurements against committed corpus state, not tests of behaviour that could
regress silently.

Notably *not* skipped: `test_web_harness.py` — the only end-to-end test of the
HTTP layer, the layer that shipped with the bypassable path jail — and
`test_sidecar_parity.py`, the only cross-check of the Rust and Python VAD
paths. The parity test used to skip on any host without a real recording;
`conftest.py` now generates a deterministic synthetic recording once per
session when `SELEKYT_PARITY_MEDIA` does not name one, so the contract runs on
every machine. That is what makes the skip list above a list of unavailable
*measurements* rather than a list of untested *behaviour*; when a skip appears
in the second category it is a finding in itself.

### CI does not run the tools that would catch the real classes of bug — VERIFIED

`.github/workflows/ci.yml` installs `.[dev,llm,nle]`, ffmpeg and node, then runs
`pytest tests/ -q -rs`. `pycmx` is in the `nle`, `dev` and `all` extras and is
installed in CI, so the independent-parse check is *able* to run — an earlier
version of this section said it was in no extra, which was wrong. What CI still
does not do is run `tools/editorial_audit.py` or `tools/nle_check.py`
themselves: they are invoked by hand or by `tools/corpus_run.py`, so a change
that breaks the render/interchange agreement is only caught when a human audits
a project directory.

---

## 6. Defects, ranked by severity

Severity = (likelihood a user hits it) × (damage when hit). "Falsifies QA"
means the shipped output is wrong and nothing in the system says so.

| # | Severity | Area | File:line | Failure scenario |
|---|---|---|---|---|
| 1 | **Critical** | Security | `web/server.mjs` (origin + Content-Type gates) | No auth, no origin check, `Content-Type`-blind JSON POST. Any web page the user visits drives the pipeline via CSRF. Combined with #2/#3 this is remote credential theft from a localhost service. **FIXED 2026-09-22 at the boundary the attack actually used** — every mutating route now calls `assertSameOrigin` before its body is parsed: a request carrying a foreign `Origin` (or a foreign `Referer` when `Origin` is absent) is refused with the offending origin named, and `getJson` refuses any body that is not `application/json`, which kills the no-prefilter `text/plain` form primitive outright. Pinned by four tests in `tests/test_web_harness.py`: the cross-origin POST, the cross-origin-`Referer` POST, and two positive tests (own origin, and no header at all) that make a blanket refusal fail. Mutation-verified both ways. **Residual, deliberately open:** a request with neither `Origin` nor `Referer` is still accepted and there is still no token — that is curl, a script or the test suite, i.e. a process the localhost bind already trusted; an unforgeable local secret is a different product shape (multi-user / remote access). See "There is no authentication at any layer" below. |
| 2 | **Critical** | Security | `web/server.mjs` `guardedPath` | `guardedPath` prefix bypass. `/tmp/selektyt-backup/secrets` passes the jail. Any sibling directory whose name extends the repo name reads arbitrary files via `/api/file`. The test at `test_a_sibling_directory_extending_the_project_name_is_rejected` passes while the bypass stands. **FIXED —** the boundary is now `ROOT + sep`, and a sibling-prefix regression test (`test_a_sibling_directory_extending_the_project_name_is_rejected`) fails against the old one-liner. The `/api/file` half was a separate defect with a separate boundary, fixed separately below. |
| 3 | **Critical** | Security | `selekyt/pipeline.py:206` → `web/server.mjs` `route` | yt-dlp `media_info` (embedding the `Cookie` header) is persisted to `pipeline_state.json` and served unauthenticated by `/api/file`. `cookies.py` goes out of its way never to print a cookie value; this path prints all of them. **FIXED on both sides —** `media_info` now passes through `_scrub_credentials` before it is ever written, dropping `http_headers`/`cookies`/`_cookies`/`cookie_manager`/`cookiejar`/`cookies_str` at every depth, and `/api/file`'s boundary is now `OUT_WEB + sep` so the state file is not inside it either. Pinned by `tests/test_pipeline_hardening.py::test_scrub_credentials_removes_every_credential_field`. This was a latent defect, never a demonstrated leak — no URL source run on this host was authenticated. |
| 4 | **High** | Security | `web/server.mjs` `resolveMedia`, `selekyt/server.py` | SSRF. `http://169.254.169.254/...` is a valid `source`, fetched from the server's network. No host/port allowlist anywhere. **Partly fixed 2026-09-22 —** `_pipeline_source` now refuses the metadata endpoints (IPv4 `169.254.0.0/16`, IPv6 `fe80::/10`, `fd00:ec2::254`, `metadata.google.internal`, `metadata.azure.com`) before the downloader is handed anything, on the only URL-fetch path the web shell has. **Still open:** this is a boundary check on the URL as written, not egress filtering — DNS rebinding to a link-local address, and any host on any port, are both still reachable. See "SSRF is reachable" below for the full limit. |
| 5 | **High** | Reliability | `selekyt/pipeline.py:391-419`, `:117-151` | Caption-style render-cache staleness. Re-run with a different `SELEKYT_CAPTION_STYLE`: the new ASS is written, the old MP4 is shipped. **Falsifies QA.** The cache key omits the style in both `_fingerprint` and `_selection_digest`. **FIXED —** the key now requires a matching `caption_style`, records it back, and treats a record with no style as a miss; pinned by `tests/test_caption_style_cache.py` (5 tests). |
| 6 | **High** | Scalability | `pyproject.toml` | `faster-whisper` is not a base dependency. Default install cannot transcribe. First-run experience is a crash. **Mostly resolved 2026-09-22 —** the failure was never a traceback: `providers/transcribe.py` runs faster-whisper → sherpa-onnx → a `RuntimeError` naming both pip extras and the sherpa model path, and `selekyt cli --doctor` reports the gap before any work, so the first-run experience is an actionable error. The deliberate design decision that neither ASR engine is a base dependency stands and is documented in `pyproject.toml`; it is what lets the package install on a host with no ctranslate2 wheel. The remaining real residue was *ordering*: for a URL source the download ran to completion before the error fired, so a user on a default install fetched a whole video to be told to run pip. **FIXED —** `run_pipeline` now probes `select_engine` alongside its existing fail-fast parameter block, so the same message arrives before the network. Pinned by `tests/test_pipeline_hardening.py::test_pipeline_reports_a_missing_transcription_engine_before_download` (fails if the probe is removed; the sibling test fails if it is made unconditional). `select_engine` is the one place the choice is made, so the pre-flight cannot drift from the transcription path. |
| 7 | **High** | Reliability | `engine/qa.py:19-43`, `:138-209` | QA never checks aspect ratio precisely, duration vs edit plan, or caption presence; `black_frame_pct`/`visual_diversity_score` swallow exceptions and report clean. **Falsifies QA.** **Partly resolved 2026-09-22 —** the aspect ratio *is* checked precisely now (display aspect from SAR, 1% tolerance), the container duration is compared against the edit plan's per-clip `edited_duration` at a 1% tolerance, and the two advisory metrics return `None` on any failure to measure instead of a clean-looking `0.0`/`1.0`. Still open: **caption presence** — no cheap exact signal exists for whether the burn happened, and a sampled-frame heuristic would fail false on real corpus clips, so that verification lives in `tests/test_render.py::test_the_burned_captions_are_in_the_pixels` rather than in runtime QA. |
| 8 | **High** | Scalability | no cleanup anywhere; `providers/atomic.py` | ~900 MB retained per project, 92% intermediates, 13 GB free, no GC. At 100× the run dies in `OSError` mid-write after the checkpoint. **The concurrency half is fixed 2026-09-22 —** the atomic write no longer uses one fixed `.tmp` name per target. Two writers to the same state file used to share it, so the second write truncated the first's buffer in place and the first's `os.replace` then landed the *second* process's payload, while the second's own replace raised `FileNotFoundError` because its source name had been consumed — state losing a payload with an error that reached the wrong caller. Each write now stages through a unique `mkstemp` name in the same directory (same-directory is what keeps `os.replace` atomic), with cleanup on failure. Pinned by `tests/test_transcription_recovery.py::test_concurrent_writes_to_one_state_file_do_not_share_a_temp_name`, which forces the interleave and was mutation-verified against the old name: it fails with exactly the `FileNotFoundError` described above. Pinned by `tests/test_retention.py` (7 tests): the reclaim, the deliverables that must survive it, the `--keep-source` toggle and the `media_path` clearing, the three refusal shapes, and idempotency. The completeness gate was mutation-verified — disabling the file-existence check fails exactly `test_an_incomplete_run_is_refused_and_keeps_its_intermediates`; the "no renders recorded" check turned out to be unreachable given the file check and is kept as a clearer error message rather than as protection.

**The disk-growth half is addressed 2026-09-22 by an opt-in control, not by automatic cleanup** — `selekyt --clean-project <dir>` removes `source/` and `transcription/` from a finished run, refusing unless every render the plan called for is on disk, and reports the bytes it reclaimed. Measured on the committed corpus: 60 MB of 115 MB (52%), 34 MB of 120 MB (28%) and 4 MB of 69 MB (6%) — the spread is why the claim is a table and not a number. **Still open:** nothing schedules it, so `out/` still grows until a user runs it, and the `OSError` at 100× is deferred rather than solved. See "Disk is the binding constraint" for the measurement and the reasoning about what is and is not safe to delete. |
| 9 | **Medium** | Architecture | `engine/fcpxml.py` vs `engine/nle_export.py`; `selekyt/server.py` (`op_render_fcpxml`) vs `selekyt/pipeline.py` | Two live FCPXML emitters with divergent error policy (raise vs silent fallback) behind two entry points. The web preview and the shipped file can disagree. **The policy half is closed 2026-09-22 —** both `_to_file_uri` functions now raise on an unresolvable path with the path named (the "silently falls back" description was stale), and the alignment is pinned by one test asserting the raise on both emitters. What remains is the maintenance cost the finding was really about: two deliberately separate emitters speaking different vocabularies, a duplicated `Track` dataclass, and the possibility of drift in what the alignment test does not cover. Merging them would rewrite a working export for no user-visible gain, so it is recorded as accepted, not fixed. |
| 10 | **Medium** | Reliability | `selekyt/pipeline.py` export block | Export validation runs after renders are checkpointed, so a bad timeline kills a run that has already produced deliverables. Recovers on resume; reports failure. Also, the export comment claimed timeline duration == render duration without ever measuring the render. **The second half is resolved 2026-09-22 —** the rendered MP4s *are* measured now (`write_report` probes each one against the plan's `edited_duration` at 1%), and the comment now says what is verified instead of assuming it. The agreement is checked in two legs that meet at the edit plan, not one direct comparison; sub-frame slop between the timeline and the render is still only measured by `tools/nle_check.py`, which remains unscheduled. **The first half is still open by design** — running the media-backed cross-check before the checkpoint would mean discarding payable deliverables on a failure, so the ordering is deliberate and the run recovers on resume. |
| 11 | **Medium** | Test quality | `tests/test_render.py:170-176` et al. | The burned-captions test cannot detect a missing caption track; four tests assert a literal `True` constant; `test_audio_analysis.py:133-142` does not test what its name claims; `test_editorial_rank.py:51` disables its own assertion via `or True`. **Mostly resolved 2026-09-22 —** the burned-captions half is closed by a pixel-decoding test; `test_editorial_rank.py` now asserts the retained clips' sorted spans do not overlap instead of `... or True`; `test_reproducible_segmentation_across_runs` now re-runs at the same threshold and compares the persisted regions, and proves the threshold reaches the segmenter (0.3 vs 0.5 straddle the boundary where this fixture's segmentation actually moves; 0.5 vs 0.9 do not, because the fixture's bursts saturate the score at 1.0). Both replacements were mutation-verified. Remaining by deliberate decision: the four `is True` constants (see §"Three more near-vacuous assertions, left in place"). |
| 12 | **Medium** | Scalability | `selekyt/server.py` `_envelope`/`process_line`, `web/server.mjs` `spawnChild`/`rpc` | One serial sidecar, FIFO `pending[]` with no request id, no timeout. One slow pipeline blocks `/api/ping`; one crash rejects every queued request; a `stdin.write` failure leaves a promise unresolved forever. **Mostly resolved 2026-09-22.** Three of the four named halves are closed; the one that is not is a property of the design rather than a bug left in place.<br><br>**Correlation replaces position.** Every request now carries an `id` the sidecar echoes back on both the success and the failure envelope (`_envelope`), and the bridge matches a response to the request that asked for it instead of to whichever request happens to be first in line. This was not theoretical: the wire *is* stdout, and a library deep in the pipeline that writes to fd 1 directly — CTranslate2 has done it from under faster-whisper — emits a line that is a response to nothing. Under the old FIFO that line was consumed as one, and the request behind it silently received the answer to the request in front of it, or an "invalid response" error about a line it never sent. Nothing complained. Now a line that parses but names no live request is dropped and logged, and an unparseable line is dropped too rather than charged to a request — `_progress_to_stderr` already keeps Python-level prints off the wire, which covers `print()` but not a C extension or a subprocess that inherited fd 1, so the two guards are at different layers on purpose. A caller that sends no `id` still gets FIFO, which is correct for the single-threaded loop and keeps the Rust worker and every caller predating the field working.<br><br>**A crash names what it killed.** The exit handler still rejects every queued request — correct, the sidecar is gone — but each rejection now carries the op it was serving, because "the sidecar exited" answers a queue of different questions with the same words and the one a user was watching was not identifiable.<br><br>**No unresolved promise.** A `stdin.write` that throws is caught, and the entry is *removed* from the queue before the promise settles — leaving it in place is the subtler half of the bug, since a response arriving later settles an already-settled promise (a no-op) and shifts every response after it by one. A stdin `error` event settles everything still queued and drops the reference; the child is deliberately not killed, because a broken stdin is usually the aftermath of a death whose exit handler has already cleared the queue, and if it has not, the process reads EOF and exits on its own. Killing it here could instead cut short a render that was still progressing.<br><br>**Still open, and why: `/api/ping` cannot be answered while a run is in flight.** The probe gets a 5s deadline and reports *busy* rather than hanging for the length of a pipeline — the page used to read an engine that was working as "unreachable" until the run finished — but the probe genuinely cannot be answered. The sidecar is one thread and `serve()` is a blocking line loop, so while it serves a request it cannot even *read* a probe, let alone answer one. Answering from a second thread would require thread-safe handlers in a sidecar whose handlers are not (models, `SELEKYT_CAPTION_STYLE`, the `sys.stdout` swap in `_progress_to_stderr`), so it is recorded as a design limit rather than a fix. The deadline applies to ping only: a transcribe and a hang are not distinguishable from outside the process, and a long op that gets a deadline would be killed or reported failed for working.<br><br>**What pins it.** `tests/test_server.py` (3 tests) pins the wire side: the id is echoed on both envelopes, a request without one gets no synthetic field, and a string id is carried verbatim rather than coerced. `tests/test_web_harness.py` (5 tests) pins the queue side against a sidecar script — the one route that forwards its body verbatim is the one these use, because the `mode` field the fake reads is a no-op parameter to the real sidecar: a line printed past the protocol does not steal the next answer, an envelope for an id nobody sent is dropped, answers arriving back to front still reach their request, the probe reports busy inside its deadline while the request that held it completes untouched, and a dead sidecar names the op it died serving and is replaced by the next one. Each was mutation-verified and failed exactly the test that names it: restoring the FIFO's line consumption fails the stray test only; ignoring the id fails the foreign-id and out-of-order tests only; removing the probe's deadline fails the busy test on the deadline assertion, which is what proves the queue is correct *and* still slow without it — correlation fixed the mismatch, not the hang; dropping the op from the crash envelope fails the dead-sidecar test only; dropping the id from `_envelope` fails the two wire tests only. |
| 13 | **Medium** | Scalability | `web/server.mjs` `route`, `pipeline_state.json` | No project ID, no per-tenant isolation, hardcoded output directory. Two concurrent web runs clobber one state file. Multi-tenancy is a correctness bug, not a scaling task. **The collision half is FIXED 2026-09-22; the isolation half is not, and is a product-shape question rather than a bug.** `pipeline_state.json` is a run's state machine — it records what has been rendered so a resume picks up where the run left off — and two runs into one directory do not share that machine, they fight over it. The second's state write landed over the first's renders list and the first then resumed against a state that no longer described its own work. The atomic write from row #8 is what made this silent rather than a crash: neither payload was corrupted, last writer won, and nothing said which run the state file described. `run_pipeline` and `export` now hold their project directory for the duration of a run through `_runExclusive`, and a second caller into the same directory is refused with the directory and the holding op named, instead of being allowed to corrupt the first. The lock is keyed by directory, not global: the two routes write to different directories and a run holding one does not block the other. It is in-process, which is the right scope because the bridge is one process and every web request crosses it — and because a lock file on disk would outlive a bridge restart and report a project busy forever after a crash nothing cleaned up. It is released in a `finally`, and release on a dead sidecar falls out of that for free: the queue's exit handler settles the outstanding promise with an error, `rpcData` throws, and the `finally` runs. Pinned by three tests in `tests/test_web_harness.py`: two concurrent runs of one project are refused rather than interleaved, a refused run releases the project for the next one (the stuck-lock case, which reads as "this project is busy" forever if the release path is wrong), and a run holding one project does not block another. Mutation-verified: disabling the guard fails exactly the first two and *not* the third, which is what pins the lock to a directory rather than to the server. **Still open:** there is still one project directory per route and no project ID, so this is a collision guard, not isolation — a user gets one `out/web/pipeline` and one `out/web/project`, and naming, listing or choosing between past projects is a product surface the shell does not have. Two browser tabs can no longer silently corrupt each other; they still cannot have separate work. |
| 14 | **Low** | Reliability | `selekyt/pipeline.py:502` | `state["complete"] = True` is written and never read; resume keys off `renders`. Dead state. **FIXED 2026-09-22 —** the write is removed (the line is now `:888`, which also records why no completion flag exists). No reader existed anywhere to break. |
| 15 | **Low** | Scalability | `SileroVADBackend.analyze` (always `NotImplementedError`), `EnergyVAD._read_mono` (full WAV in memory), `make_thoughts` quadratic pre-cap | The documented fallback chain has a leg that cannot fire; one VAD reads a whole file while another in the same package already windows it; the thought cap bounds output, not cost. **The first third is fixed 2026-09-22 —** `is_available` no longer reports a backend whose `analyze` raises (it returned True on a host with onnxruntime and a model path), the module docstring now lists the chain as selected — including `EnergyVAD`, which it omitted though it is the effective default — and `--vad-backend silero` now says "not implemented" and names the working backends. The backend itself is still unimplemented; see "One VAD backend cannot work". `EnergyVAD._read_mono` is still open, and `make_thoughts` is half-closed: the cap now bounds cost as well as output (3.3× faster at 80 minutes, output-identical on all 8 corpus transcripts), while `_words_in` still rescans from the front per thought. |

---

## Top 5 findings

1. **No authentication, no origin policy, and a Content-Type-blind JSON
   parser on a localhost HTTP service** — the routes at `:283` onward. Any web
   page can drive the pipeline via CSRF. **Fixed:** a POST with a foreign
   `Origin` is refused before its body is parsed, a body that is not
   `application/json` never reaches `JSON.parse`, and four tests in
   `tests/test_web_harness.py` pin both gates in both directions. The residual
   is stated in row #1 above: no token, and no-header requests still accepted.
2. **`guardedPath` is a `startsWith` prefix check and is bypassable** —
   `web/server.mjs` `guardedPath`. `/repo-backup/secrets` escapes the jail; the
   existing test passed anyway. **Fixed:** the boundary now includes the
   separator, and a sibling-prefix regression test covers the case the
   original test could not see.
3. **Cookie credentials persisted into `pipeline_state.json` and served
   unauthenticated** — `selekyt/pipeline.py:206` → `web/server.mjs` `route`.
   **Fixed on both sides:** the blob is scrubbed before it is written, and the
   route that would have served it no longer serves outside `OUT_WEB`.
4. **Caption-style render-cache staleness** — `selekyt/pipeline.py:391-419`
   with a style-blind key at `:117-151`. A re-run with a different style
   ships the old video beside the new captions, and no check in the system
   detects it. **Fixed:** the key now carries `caption_style`, records it
   back, and treats a record with no style as a miss (row #5 above; pinned by
   `tests/test_caption_style_cache.py`).
5. **QA that cannot detect a wrong output, plus the tools that could are
   unwired** — `engine/qa.py:19-43` never checks aspect, duration-vs-plan, or
   captions; `tools/nle_check.py` (independent `pycmx` parse, render-vs-timeline
   cross-check) is never called by the pipeline, the web shell, or CI. **Partly
   fixed 2026-09-22 —** the QA half is row #7 above: aspect ratio and
   duration-vs-edit-plan are now measured precisely at runtime, and the two
   exception-swallowing metrics report `None` instead of a clean-looking zero.
   The EDL half is wired in now too, in the one place it can be cheaply: every
   export is parsed by `pycmx` — an independent CMX 3600 reader — and a
   rejection is fatal (`engine.nle_export.independent_parse_edl`, called from
   `selekyt/pipeline.py` at the export-validation site). That is deliberately
   *not* `tools/nle_check.py` itself, whose value is the media-backed
   render-vs-timeline cross-check; wiring that in after renders are
   checkpointed is row #10's hazard. The remaining open defect is the
   scheduling one: `pycmx` is a declared dependency, so `tools/nle_check.py`
   *can* run — nothing schedules it to. CI installs the library but does not
   invoke the tools (see "CI does not run the tools" below).

## The single most severe risk

**The web layer is an unauthenticated, origin-blind control plane for a
service that fetches arbitrary URLs and reads arbitrary files, and it holds
the user's YouTube cookie material.** Individually each weakness is
tractable; together they chain: a crafted page the user visits POSTs a
`source` of `http://169.254.169.254/...` (SSRF from the server's network) or
a `path` of `/etc`-adjacent data (traversal via the prefix bypass), and
`/api/file` returns `pipeline_state.json` containing the user's `Cookie`
header. All three of the top findings are the same defect viewed from three
angles, and fixing any one of them alone leaves the chain intact. The
`127.0.0.1` bind is not a control, because the attacker's code runs in the
user's browser on that same host.

**All three links are cut as of 2026-09-22.** The prefix bypass is fixed at
`ROOT + sep` / `OUT_WEB + sep`, the cookie material is never written to the
state file in the first place, so there is no `pipeline_state.json` to serve,
and the metadata endpoints are refused at the URL boundary. The control plane
itself is now gated too: a POST that arrives with a foreign `Origin` or
`Referer` is refused before any route runs, and a body that is not
`application/json` never reaches `JSON.parse` (row #1, closed).

What the chain relied on was that a page the user merely *visits* could drive
the pipeline. It no longer can: the browser attaches `Origin` to a POST and a
page cannot forge it, so the request from `evil.example` is rejected with its
own origin named in the error. The residual, stated plainly, is that a request
carrying **neither** `Origin` nor `Referer` is still accepted — that is curl, a
script or the test suite, i.e. a local process the localhost bind already
treated as trusted. Closing that too would mean a bearer token or a
same-process secret, which is a different product shape (multi-user, remote
access) than a local single-user tool; it is recorded below as the honest
boundary of this fix, not as a gap I have closed.

## The thing that would embarrass the company in an enterprise review

**`guardedPath`** (`web/server.mjs` `guardedPath`). It is a one-line jail that a
reviewer will read in five seconds and break in ten:

```js
if (candidate.startsWith(ROOT)) return candidate;
```

It is the security control the codebase actually relies on — it gates
`/api/file`, `/api/run_pipeline`, and `/api/export` — it is bypassable by any
sibling directory whose name extends the repo name, and it is defended by a
test (`test_a_sibling_directory_extending_the_project_name_is_rejected`) that asserts the one case it does
handle. An enterprise reviewer does not need to find the cookie leak or the
SSRF to fail this product; they need only read those four lines and write
`/tmp/selektyt-anything/secrets` — which I did, and it passed the guard.

**Resolved.** The guard now reads `candidate === ROOT ||
candidate.startsWith(ROOT + sep)`, and the test that could not see the bypass
is no longer the only one on it. The code block above is the *old* guard,
retained because the finding is about that line and a reader needs to see what
was wrong with it; it is not what ships. Two things had to change, and only
one of them was code: the boundary, and the test that asserted the obvious
case and therefore passed while the jail was open. The second is the more
general lesson of this audit — a guard's test has to be written against the
bypass, not against the intended input, or it certifies nothing.
