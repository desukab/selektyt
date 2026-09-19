# AI execution state — SelekyT

Checkpoint for a following Atria session. Factual as of the commit named below;
every line below was true on this host at that point.

## Current repository state

* Repository: `/home/azureuser/selektyt`, on `main`, working tree **clean**.
* Up to date with `origin/main` (`https://github.com/desukab/selektyt.git`).
  Nothing unpushed, nothing stashed, nothing in `git stash list`.
* **Latest commit:** this file's own commit — *docs: persistent AI execution
  state checkpoint*. It is a doc-only checkpoint with no code change. The last
  *substantive* change is `2dd4ff1` — *docs: release validation record and
  updated product gap map*; the five defects it fixed are listed below. Run
  `git log -1` for the current hash rather than trusting a hash printed here,
  since amending this file changes its own commit hash.
* Test suite: **819 passed, 10 skipped** (~64 s). The 10 skips are optional
  backends absent from this host (sherpa-onnx, silero, ollama) and tests that
  depend on them. Re-run to confirm this number before trusting any claim in
  this file.
* Five commits landed on `origin/main` from outside this session
  (`36bdff4`..`edf3b30`): a switch from MIT to a proprietary license plus
  project-positioning docs. They were integrated by rebase, not overwritten.
  **Check `LICENSE` and the top-level positioning docs** before assuming the
  MIT-era wording in older files still applies.
* Preserved agent worktrees under `.claude/worktrees/` are **not** merged into
  `main` and must not be deleted (standing instruction). The timeline worktree
  (`agent-a2478f4bf3ccaf916`, `feat(timeline): edited-timeline model …`) is the
  only one whose subject matter overlaps live code paths — check whether it has
  already been integrated before reworking anything in `engine/timeline*`.

### How to run things here

* Tests need the project venv: `.venv/bin/python -m pytest -q`. A bare
  `python -m pytest` fails.
* The shell **cwd resets to `/home/azureuser` between Bash calls** — always `cd
  /home/azureuser/selektyt` or use paths from repo root.
* Web shell (when needed): `setsid env PATH="$PWD/.venv/bin:$PATH" nohup node
  web/server.mjs --port 8791 … & disown`. The venv must be on `PATH` or the
  sidecar cannot resolve `yt-dlp`. Plain `nohup … &` dies with the shell.
* No project server is listening right now. Scratch run scripts in `/tmp` were
  removed.
* Disk: 14 G free of 29 G. The real NASA source alone is 883 MB under
  `out/web/pipeline/project/source/`.

## Completed work

A real-world release validation pass, run on real input through the web shell
rather than by reading code. Five defects were found and fixed, each with a
regression test; three were verified to fail on the pre-fix code.

* `5265ed2` fix(edl): advance record spans from the previous event's snapped
  out-point — a one-frame overlap the validator caught only *after* renders and
  captions were written.
* `ec1b84e` fix(download): keep yt-dlp progress off the JSON wire; accept a
  muxed `.mpg`. The wire bug is the *inherited child* case that a Python-level
  `sys.stdout` swap cannot catch.
* `af1e686` fix(pipeline): name the missing speech when a source has no audible
  track, instead of letting it surface as an editorial rejection.
* `2571154` fix(reframe): the 9:16 crop is now SAR-aware, so it is 9:16 in
  *display* space. On the real NTSC source the output went from displaying
  1088:2187 (≈1:2) to 1232:2187 (≈9:16).
* `2dd4ff1` docs: validation record, updated gap map, and
  `tools/editorial_audit.py` (the repeatable audit).

Real end-to-end evidence lives in `docs/release-validation.md`; the
per-capability status table is `docs/product-gap-map.md`. A genuine
rights-permitted URL (`archive.org/details/NASA_360_program5_HD`, 24:29 of
two-host unscripted speech) ran the whole chain green: download → transcript →
selection → both renders → captions → EDL/FCPXML → QA, plus the editorial audit.

## Outstanding work

In rough order of how much it would cost a user (fuller list in
`docs/release-validation.md`):

1. **YouTube is unfetchable from this host.** Every `yt-dlp` variant returned
   "Sign in to confirm you're not a bot" while plain `curl` returned 200 — a
   datacenter-IP block, not a code defect. **No YouTube fetch has ever been
   observed here.** Chapters, members-only and age gates are entirely untested.
2. **No NLE has opened an exported timeline.** EDL/FCPXML are format-valid and
   self-consistent; import compatibility with Premiere/Resolve/FCP/Avid is
   unclaimed.
3. **No video upload in the web shell** — a video must already be inside the
   project tree (`guardedPath` confines it), while the CLI has no such
   restriction, so the two entry points disagree about what a user can point at.
4. **Desktop shell is unbuilt** — Tauri compiles and passes clippy, but no
   binary is packaged and the webview has never rendered (no webkit2gtk).
5. **Optional backends untested here** — sherpa-onnx, silero, ollama are wired
   and degrade cleanly by design, but unit-tested only.
6. **Editorial judgement is unmeasured at scale** — three clips from one source
   passing the audit is evidence the machinery is sound, not that the choices
   would survive comparison with a human editor across sources and languages.
7. **Vertical reframing verified on one anamorphic + synthetic sources only** —
   the SAR fix is provably correct *geometry*, but whether the wider crop keeps
   a moving subject well framed is not measured. A 9:16 cut from SD footage is
   sub-SD (308×486), a property of the input.

## Current strategic objective

Take SelekyT from "verified local-file release" toward a genuinely usable v1
for real users. The phase's own rule was to **discover whether it actually
produces good short-form edits from real-world human speech and real URL
sources**, not to add features: do not restart a broad feature-development
cycle, do not rewrite working architecture, fix only measurable problems with
the smallest robust fix, and record concrete failures rather than hiding them.

That objective is largely met for the local-file and URL paths. The remaining
work above is what separates the current state from a production-ready v1.

## Next 10 highest-leverage tasks

Ordered by evidence gained per unit of work, not by lines of code.

1. **Get one real YouTube fetch observed, by any means available.** Use
   `--cookies` / a browser cookie file, a residential proxy, or run from a
   non-datacenter network. Until a single YouTube download succeeds, item G1
   cannot move, and it is the only input path entirely unverified.
2. **Import a timeline into a real NLE.** Even one (Premiere, Resolve, FCP or
   Avid) converts "structurally valid" into "compatible". Resolve has a free
   tier and reads CMX 3600 — the cheapest first try. If it fails, the EDL
   rounding arithmetic is the likeliest culprit.
3. **Run the editorial audit across 3–5 more real sources** of differing
   length, language and production style, and record *which* checks fail. This
   directly attacks G6, the least-measured remaining risk.
4. **Test the `.mpg` path end to end.** The accept-any-container fix (defect 2)
   is pinned at the discovery step only; a full run through a real MPEG-PS
   source would exercise transcription, render and export on that wrapper.
5. **Re-measure subject framing after the wider anamorphic crop** on a source
   with real lateral motion (G7). The SAR fix is provably correct as geometry;
   whether the tracked subject stays well composed is not measured.
6. **Resolve the web-shell / CLI input disagreement** (G3). Decide between
   exposing a video upload route or documenting the project-root confinement —
   the previous session decided URL-first with no code change, which is why
   upload is still missing.
7. **Verify the sherpa-onnx and silero fallbacks actually fire.** Install one
   optional backend on a scratch venv and confirm `fallback_reason` reports it
   and the output is still usable. Only do this if it unblocks a release
   question — it is not on the critical path.
8. **Package the Tauri shell once, on one platform** (G4), or record explicitly
   that v1 ships CLI + web shell only. Do not spend main effort here until G1
   and G2 are settled.
9. **Add a second source to the determinism check.** The web shell reproduced
   the CLI run's selection exactly, but on one source; a second source would
   make that claim real.
10. **Audit the preserved worktrees for now-merged or superseded work** and
    record the verdict in `docs/agent-worktrees.md`. The timeline worktree in
    particular may duplicate or conflict with live `engine/timeline*`.

## Important decisions

* **URL-first web UX, no code change.** The web shell already accepts a URL in
  the same field and dispatches it to the same sidecar path as the CLI, so a
  separate video-upload route was judged redundant work. This is *why* the
  upload gap in G3 was left open deliberately, not overlooked.
* **The 9:16 fix keeps the frame anamorphic rather than emitting square
  pixels.** Scaling the crop width by SAR gives a correct display ratio with
  zero resampling. The alternative (`setsar=1`) gets the ratio right only by
  lying about the pixel shape and would stretch content ~12.5 % horizontally.
  If a platform is later found to ignore container SAR, revisit this.
* **The speech-free guard is placed outside the transcription branch** so a
  *cached* empty transcript is also caught, not just a fresh one.
* **`tools/editorial_audit.py` reads only artifacts a normal run already
  writes** and prints failing checks, so it is the repeatable benchmark the
  phase asked for rather than a one-off script. Prefer extending it over
  writing ad-hoc checks.
* **No `Co-authored-by` or generated-by metadata in commit messages**, per the
  standing user instruction. Commit subjects use conventional prefixes and the
  body carries root cause plus pinning test.
* **Never `git push --force`, never reset or rewrite history, never delete the
  preserved agent worktrees.**

## Known risks

* **The classifier that gates Bash can time out**, and a timeout mid-`git stash
  pop` once left the working tree on pre-fix code. It was caught and restored.
  Do not stash a fix mid-verification unless the restore runs in the same tool
  round; prefer reproducing the defect with an explicit pre-fix command or a
  scratch checkout.
* **`git stash` + classifier is not the only fragile pattern**: the shell cwd
  resets between Bash calls, so a command that assumes it is still in the repo
  root will silently run against `/home/azureuser`.
* **The anamorphic fix changed the vertical crop width for every source**, not
  just anamorphic ones — square-pixel sources now round to an even width
  (e.g. 274 px at 486 tall instead of 273). This is why the existing
  square-pixel 9:16 test still passes but on slightly different geometry.
* **Disk is the binding constraint on real runs.** 14 G free, and one real
  source is 883 MB. A second long source plus its renders will not fit
  comfortably; check `df` before any end-to-end run and never delete project
  data, git history, worktrees or caches blindly.
* **The docs record is only as good as the last real run.** `docs/product-gap-
  map.md` says "as of `2dd4ff1`"; any future change to a verified path must
  update that row with what was actually exercised, not what the code now
  claims to do.

## What another Atria session must know

* **Do not trust claims in the code about its own output.** Two of the five
  defects (the 9:16 ratio, the "is 9:16" comment in `engine/render.py`) were
  *asserted by the code and false*. The `crop=ih*9/16:ih` filter even carried a
  comment saying it had produced "an exact 9:16 frame". Verify geometry,
  durations and cue counts by probing the rendered files.
* **Read the two docs first**: `docs/release-validation.md` (what was actually
  run, what failed, what was fixed) and `docs/product-gap-map.md`
  (per-capability status with the basis for each claim). Both distinguish
  "code exists and unit tests pass" from "a user can rely on this".
* **Start any work by re-running the suite** (`.venv/bin/python -m pytest -q`)
  and confirming 819/10. A number other than that means something has drifted.
* **The previous phase's own framing applies**: do not simply report that
  SelekyT is complete. The purpose is to discover whether it produces good
  short-form edits from real speech and real URLs — and G1 (YouTube) and G2
  (NLE import) are the two claims that still have no real-world evidence behind
  them at all.
