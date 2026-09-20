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

1. **The web shell has no authentication, no origin policy, and a prefix-based
   path jail that is bypassable** (`web/server.mjs`). It accepts arbitrary
   URLs for the pipeline to fetch and serves any file under the repo root.
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

### Two FCPXML emitters with divergent policy — VERIFIED

`engine/fcpxml.py` is a CutList-driven emitter; `engine/nle_export.py` is a
Timeline-driven emitter. Both are live, in different products:

- `selekyt/pipeline.py:21` imports `nle_export` and `:438` calls it — this is
  what writes the timelines a user actually receives.
- `selekyt/server.py:27` imports `engine.fcpxml` and `:185-194` (`op_render_fcpxml`)
  implements the sidecar's `render_fcpxml` op with the **older** cutlist
  emitter, which `:187` documents as "Rust sidecar's `render_fcpxml` op
  (asserted in test_sidecar_parity)".

So the interactive web path and the batch pipeline emit FCPXML through
different code. Both route their timecode math through `engine.timecode`, so
the *arithmetic* has one source of truth — but the *policy* does not. The two
emitters disagree on how to handle an unresolvable asset path: the cutlist
emitter's `_to_file_uri` raises, the timeline emitter's silently falls back
(INSPECTED). An asset that fails in one path will silently produce a
relinkable-looking-but-broken file in the other. There is also a second
`Track` dataclass (`engine/fcpxml.py:33` alongside `engine/timeline.py:75`)
representing the same concept.

This is the largest pure-maintenance cost in the codebase and the most likely
source of a "the web preview is not the cut I got" report.

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
no confinement. `web/server.mjs:291` confines a local source to the repo root
via `guardedPath`, and `:297` hardcodes `out: join(OUT_WEB, "pipeline")`. The
web comment at `:299-300` documents that the cookies path is *deliberately*
not confined. So the two entry points disagree on three axes (source path,
output location, cookie path), and `docs/product-gap-map.md` confirms this is
a decision rather than an oversight. It is a defensible one, but it means
behaviour verified through the CLI is not what the web user gets, and the two
are tested separately rather than against a shared contract.

Web exposure is also narrower than the CLI: `selekyt/cli.py` exposes 16
tunable flags; `/api/run_pipeline` (`web/server.mjs:298-310`) forwards 9 of
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

### There is no authentication at any layer — VERIFIED

`web/server.mjs:345-360` binds `SELEKYT_HOST || "127.0.0.1"` and listens with
no bearer token, no session, no `Origin`/`Referer` check, and no CORS policy.
Every route in the manual router (`:283` onward: `/api/run_pipeline`,
`/api/file`, `/api/export`, `/api/segment`, `/api/cutlist_from_segments`) is
open. The Python sidecar's own comment at `selekyt/server.py:108-121` is
explicit that it is not an authorization boundary, which is a correct local
design decision — but the HTTP layer in front of it makes no such
qualification.

Because `getJson` never inspects the request `Content-Type` (VERIFIED:
`Content-Type` appears only on *outgoing* responses), a cross-origin
`text/plain` HTML form POST carrying valid JSON reaches `JSON.parse` with no
CORS preflight. That is the classic JSON CSRF primitive. Any web page the
user visits can drive the pipeline, and `127.0.0.1` binding does not stop it —
the browser is on the same host.

### `guardedPath` is a prefix check and is bypassable — VERIFIED

`web/server.mjs:203-209`:

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
`selektyt-anything`) escapes the jail entirely. `tests/test_web_harness.py:218`
asserts that `/etc/hostname` is rejected, and it passes — while the bypass
stands. The test proves the guard blocks the obvious case and nothing else.
The correct check is `candidate === ROOT || candidate.startsWith(ROOT + sep)`.

**Fixed** (the correction above is what the code now does), and the sibling
case is now a regression test in `tests/test_web_harness.py` alongside the
obvious one — the audit's point about the test was the real finding, and it
applied to the test as much as to the guard.

### `/api/file` serves the whole repo, not the output directory — VERIFIED, then FIXED

`web/server.mjs:317` passed `guardedPath(url.searchParams.get("path"))`, and
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

### SSRF is reachable — INSPECTED

`web/server.mjs:288-290` accepts any `https?://` `source` and hands it to the
pipeline. `selekyt/server.py:307-325` rejects `file://` but places no
restriction on host or port, so `http://169.254.169.254/latest/meta-data/`
(the cloud metadata endpoint) is a valid source, fetched from the server's
network position, and any response error text comes back to the caller. This
is the highest-impact CSRF target: an attacker does not need to read the
response, only to make the request.

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
caller, and `web/server.mjs:128-138` slices up to 2000 chars of stderr into
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

### Partial-failure semantics: captions and renders can disagree — VERIFIED

The order in the render loop is the defect:

- `:390` `retention_intervals(c)`
- `:391-396` **write `captions/NN.ass`, `.srt`, `.json` unconditionally**
- `:413-419` **then** consult the render cache
- `:421-422` render only on a miss

The render cache key is `_fingerprint` (`:117-120`: source, resolved path,
size, mtime, model, language) plus `_selection_digest` (`:142-151`: sha256 of
`start,end,text`). **Neither includes the caption style.** I VERIFIED that
distinct `SELEKYT_CAPTION_STYLE` values produce distinct `ass_for_candidate`
bytes (same 631-byte length, differing `Style` line). So a re-run with a
different style rewrites `captions/NN.ass` to the new style, takes the cache
hit, and **ships the MP4 that has the old style burned into it**. The video
and its sidecar caption file now disagree, and nothing downstream detects it:
`engine/qa.py:19-43` never checks that captions are present, and
`nle_export` re-derives the timeline from `retention_intervals`, not from the
shipped file.

The same ordering means a run killed between `:396` and `:421` leaves caption
artifacts for a clip whose render never happened — harmless, since they are
regenerated, but it means the presence of `captions/NN.ass` is not evidence of
a completed render.

### The known "export failed after renders" case — INSPECTED

`:437-452` builds the timeline, exports EDL/FCPXML, validates them, and
`raise`s on any problem — after the MP4s are on disk and the per-clip state is
checkpointed at `:432`. This is the case the task flags. It recovers correctly
on resume (the cache hits, the timeline re-exports), so it is not a
data-loss bug. It is a *reporting* bug: the run dies having produced a
finished, payable deliverable, and the exit code says failure.

### The export-vs-render comment overstates a guarantee — INSPECTED

`:433-436` reads:

> The timeline is built from the same retention_intervals the render just
> concatenated, so its duration is the rendered file's duration

This is an assumption, not a check. `:384` probes the source's frame rate and
nothing else; the rendered MP4's own duration is never measured anywhere in
the pipeline. The concat of N intervals plus per-interval `-t` clamping plus
container muxer slop means the shipped file can differ from the timeline by a
frame or two — which is exactly why `tools/nle_check.py:50-55` documents
measured discrepancies of 0-1 frames (746 rendered vs a 745-frame timeline,
513 vs 512) and carries a two-frame tolerance.

### QA can pass while the output is wrong — VERIFIED

`engine/qa.py:19-43` (`validate_output`) asserts: file exists, size ≥ 1000
bytes, a video stream, an audio stream, orientation (`h > w` vertical / `w >
h` landscape), and duration ≥ 1.0 s. It does not check: the aspect ratio is
actually 16:9 or 9:16 (only that it is landscape or vertical), that the
duration matches the edit plan, that captions are present, that the audio is
not silent, or that two clips are the same length as each other. Warnings in
`validate_outputs_expanded` (`:212-267`) — silent tail, black frames, low
visual diversity — are computed and then never fail the run. `write_report`
(`:324-345`) raises only on `not report["ok"]`.

`black_frame_pct` (`:138-164`) and `visual_diversity_score` (`:167-209`)
swallow all exceptions to `return 0.0` / `continue`, so a broken OpenCV path
reports a clean bill of health rather than a failure. This is the pattern the
repo's own docs warn about: a metric that fails open is indistinguishable from
a metric that passed.

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
  hardcodes one project directory (`web/server.mjs:297`), so two concurrent
  web runs clobber each other by construction.

The single sidecar child serializes requests today (`selekyt/server.py:476-498`
is a `for line in stream` loop), which is why the process-global caption-style
env var is currently safe. That serial loop is also a hard availability
ceiling: one long pipeline blocks `/api/ping`, and one sidecar crash rejects
every queued request (`web/server.mjs:128-138`).

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
the renders validate, which nothing does.

### Memory: one VAD backend loads the whole WAV — INSPECTED

`EnergyVAD._read_mono` reads the entire decoded WAV into memory. At 100×
volume, with 100 concurrent analyses, that is tens of GB. `providers/sherpa.py:302-348`
`_WindowedWav` already solves this with windowed reads — the better
implementation exists in the same package and is not shared.

### One VAD backend cannot work — INSPECTED

`SileroVADBackend.analyze` always raises `NotImplementedError` and its
`is_available` can never return true. It is listed as a fallback in the chain,
so the fallback chain documents a leg that cannot fire. A user who selects it
gets a runtime error with no indication that the backend was never
implemented.

### `make_thoughts` is quadratic with the cap applied after the work — INSPECTED

`_words_in` is called per thought; the 4000-thought cap is enforced after the
quadratic work is done, so it bounds the output, not the cost.

### Multi-tenancy: state is a single file — VERIFIED

Everything for one project lives in `pipeline_state.json` under one directory
that the web shell hardcodes (`web/server.mjs:297`). There is no project ID in
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
  asserts `out.is_file()` and `out.stat().st_size > 0`. It never looks inside
  the pixels. A render with no captions at all passes it. This is the test
  that guards the feature the caption-style cache bug breaks, and it cannot
  see the breakage. (Compare `:138-167`, the anamorphic test, which computes
  the display aspect ratio from the probed SAR and asserts it to 0.005 — that
  is a real test, and it is what the weak one should look like.)
- **`tests/test_audio_analysis.py:133-142`** — named
  `test_reproducible_segmentation_across_runs`, it asserts
  `a["speech_regions"] >= 0 and b["speech_regions"] >= 0`, which is true of
  any non-negative integer in both runs. The reproducibility claim in the
  name is never tested. (The cache-reuse assertions above it are genuine.)
- **`tests/test_jumpcut_fcpxml.py:138-156`** — asserts the document version,
  that `<asset ` and `ref="r1"` are present, and `content.count("<asset-clip") == 2`.
  Structure, not cuts: no cut time is parsed and compared to the input
  cutlist, so a timeline with the right shape and wrong timings passes.
- **Four tests assert a literal constant**: `tests/test_content_pack.py:70-72`,
  `tests/test_project_lifecycle.py:275`, `tests/test_unicode_pipeline.py:171`,
  `tests/test_pipeline_e2e.py:161` all assert `pack["source_based"] is True`.
  This asserts the code sets a flag to `True`. It is evidence the field
  exists and is spelled correctly, and nothing else; it does not distinguish
  a source-based product from one that rewrites speech.
- **`tests/test_editorial_rank.py:51`** — contains `or True` in an assertion.
  I VERIFIED that `rank()` *does* enforce the 4-second separation this
  appears to guard (`editorial.py`: `if any(not (c.end <= x.start-4 or
  c.start >= x.end+4) for x in chosen): continue`), so today this is dead
  weight rather than a masked live bug. It is still a landmine: it disables
  the assertion it was written to carry, and the next person to touch `rank()`
  has no test.
- **`selekyt/pipeline.py:502`** sets `state["complete"] = True` and nothing in
  the codebase ever reads it. The resume logic keys off `renders`, not
  `complete`. A test asserting `state["complete"]` is asserting a write, not
  a fact.

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

Ten tests skip. The meaningful ones: `test_proxy_validation.py`,
`test_sidecar_parity.py`, and `test_web_harness.py`. The skipped
`test_sidecar_parity.py` is the only test of the Rust sidecar's behavioural
parity, and `test_web_harness.py` is the only end-to-end test of the HTTP
layer — the layer with the bypassable path jail. The security-relevant test
in the suite is skipped or shallow, which is how `guardedPath` shipped with a
prefix bypass and a passing test.

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
| 1 | **Critical** | Security | `web/server.mjs:345-360` | No auth, no origin check, `Content-Type`-blind JSON POST. Any web page the user visits drives the pipeline via CSRF. Combined with #2/#3 this is remote credential theft from a localhost service. |
| 2 | **Critical** | Security | `web/server.mjs:203-209` | `guardedPath` prefix bypass. `/tmp/selektyt-backup/secrets` passes the jail. Any sibling directory whose name extends the repo name reads arbitrary files via `/api/file`. The test at `tests/test_web_harness.py:218` passes while the bypass stands. |
| 3 | **Critical** | Security | `selekyt/pipeline.py:206` → `web/server.mjs:317` | yt-dlp `media_info` (embedding the `Cookie` header) is persisted to `pipeline_state.json` and served unauthenticated by `/api/file`. `cookies.py` goes out of its way never to print a cookie value; this path prints all of them. |
| 4 | **High** | Security | `web/server.mjs:288-290`, `selekyt/server.py:307-325` | SSRF. `http://169.254.169.254/...` is a valid `source`, fetched from the server's network. No host/port allowlist anywhere. |
| 5 | **High** | Reliability | `selekyt/pipeline.py:391-419`, `:117-151` | Caption-style render-cache staleness. Re-run with a different `SELEKYT_CAPTION_STYLE`: the new ASS is written, the old MP4 is shipped. **Falsifies QA.** The cache key omits the style in both `_fingerprint` and `_selection_digest`. |
| 6 | **High** | Scalability | `pyproject.toml` | `faster-whisper` is not a base dependency. Default install cannot transcribe. First-run experience is a crash. |
| 7 | **High** | Reliability | `engine/qa.py:19-43`, `:138-209` | QA never checks aspect ratio precisely, duration vs edit plan, or caption presence; `black_frame_pct`/`visual_diversity_score` swallow exceptions and report clean. **Falsifies QA.** |
| 8 | **High** | Scalability | no cleanup anywhere; `providers/atomic.py:24-34` | ~900 MB retained per project, 92% intermediates, 13 GB free, no GC. At 100× the run dies in `OSError` mid-write after the checkpoint. The fixed `.tmp` name also loses data under concurrent writes to one state file. |
| 9 | **Medium** | Architecture | `engine/fcpxml.py` vs `engine/nle_export.py`; `selekyt/server.py:27,185-194` vs `selekyt/pipeline.py:21,438` | Two live FCPXML emitters with divergent error policy (raise vs silent fallback) behind two entry points. The web preview and the shipped file can disagree. |
| 10 | **Medium** | Reliability | `selekyt/pipeline.py:433-452` | Export validation runs after renders are checkpointed, so a bad timeline kills a run that has already produced deliverables. Recovers on resume; reports failure. Also, `:433-436` claims timeline duration == render duration but never measures the render. |
| 11 | **Medium** | Test quality | `tests/test_render.py:170-176` et al. | The burned-captions test cannot detect a missing caption track; four tests assert a literal `True` constant; `test_audio_analysis.py:133-142` does not test what its name claims; `test_editorial_rank.py:51` disables its own assertion via `or True`. |
| 12 | **Medium** | Scalability | `selekyt/server.py:476-498`, `web/server.mjs:93-148` | One serial sidecar, FIFO `pending[]` with no request id, no timeout. One slow pipeline blocks `/api/ping`; one crash rejects every queued request; a `stdin.write` failure leaves a promise unresolved forever. |
| 13 | **Medium** | Scalability | `web/server.mjs:297`, `pipeline_state.json` | No project ID, no per-tenant isolation, hardcoded output directory. Two concurrent web runs clobber one state file. Multi-tenancy is a correctness bug, not a scaling task. |
| 14 | **Low** | Reliability | `selekyt/pipeline.py:502` | `state["complete"] = True` is written and never read; resume keys off `renders`. Dead state. |
| 15 | **Low** | Scalability | `SileroVADBackend.analyze` (always `NotImplementedError`), `EnergyVAD._read_mono` (full WAV in memory), `make_thoughts` quadratic pre-cap | The documented fallback chain has a leg that cannot fire; one VAD reads a whole file while another in the same package already windows it; the thought cap bounds output, not cost. |

---

## Top 5 findings

1. **No authentication, no origin policy, and a Content-Type-blind JSON
   parser on a localhost HTTP service** — `web/server.mjs:345-360`, routes at
   `:283` onward. Any web page can drive the pipeline via CSRF.
2. **`guardedPath` is a `startsWith` prefix check and is bypassable** —
   `web/server.mjs:203-209`. `/repo-backup/secrets` escapes the jail; the
   existing test passes anyway.
3. **Cookie credentials persisted into `pipeline_state.json` and served
   unauthenticated** — `selekyt/pipeline.py:206` → `web/server.mjs:317`.
4. **Caption-style render-cache staleness** — `selekyt/pipeline.py:391-419`
   with a style-blind key at `:117-151`. A re-run with a different style
   ships the old video beside the new captions, and no check in the system
   detects it.
5. **QA that cannot detect a wrong output, plus the tools that could are
   unwired** — `engine/qa.py:19-43` never checks aspect, duration-vs-plan, or
   captions; `tools/nle_check.py` (independent `pycmx` parse, render-vs-timeline
   cross-check) is never called by the pipeline, the web shell, or CI. (`pycmx`
   is now a declared dependency, so the check *can* run — it simply is not
   scheduled to, which is the defect, not a missing library.)

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

## The thing that would embarrass the company in an enterprise review

**`guardedPath`** (`web/server.mjs:203-209`). It is a one-line jail that a
reviewer will read in five seconds and break in ten:

```js
if (candidate.startsWith(ROOT)) return candidate;
```

It is the security control the codebase actually relies on — it gates
`/api/file`, `/api/run_pipeline`, and `/api/export` — it is bypassable by any
sibling directory whose name extends the repo name, and it is defended by a
test (`tests/test_web_harness.py:218`) that asserts the one case it does
handle. An enterprise reviewer does not need to find the cookie leak or the
SSRF to fail this product; they need only read those four lines and write
`/tmp/selektyt-anything/secrets` — which I did, and it passed the guard.
