# Agent worktree disposition

Every agent worktree created during the multi-agent build, classified and
recorded so the reasoning survives the session. None were deleted; none were
force-pushed; no history was rewritten.

| Worktree (`.claude/worktrees/`) | Branch | Ahead of `main` | Classification |
| --- | --- | --- | --- |
| `agent-a8b3e2de76a11eaa1` | `worktree-agent-a8b3e2de76a11eaa1` | 2 commits | **useful but unverifiable** — preserved, not merged |
| `agent-a2478f4bf3ccaf916` | `worktree-agent-a2478f4bf3ccaf916` | 1 commit | **already merged** — substance is byte-identical to `main` |
| `agent-a0d0fba273d1b37e1` | `worktree-agent-a0d0fba273d1b37e1` | 0 commits | **unmerged** — ~4,200 lines of untracked engine modules and tests, never committed |
| `agent-aca62967bb6c0054b` | `worktree-agent-aca62967bb6c0054b` | 0 commits | **unmerged** — untracked `bench/` package and `tests/test_bench.py` |
| `agent-afe57bda5629eefa7` | `worktree-agent-afe57bda5629eefa7` | 0 commits | superseded — all four untracked test files are on `main` |

## agent-a8b3e2de76a11eaa1 — Tauri 2 desktop shell

**Verdict: preserved on its branch, not merged.** The instruction was to
evaluate it against the current project and available toolchains before
merging, and to preserve it with documented reasoning if it cannot be
verified. It cannot.

What was actually verified on this host (rustc 1.98.1, Linux x86_64):

```bash
cargo build --release --locked     # ok (51s)
cargo clippy  --release --locked -- -D warnings   # clean
cargo test    --release --locked -- --test-threads=1   # 5/5 pass
```

What blocks the merge:

1. **No packaged binary has been produced on any platform.** The build's own
   codegen warns: *"neither `binaries/` nor `resources/` is staged, so this
   codegen pass drops `bundle.externalBin` and `bundle.resources`. A real
   packaging run stages them first; this build is a compile/lint check only."*
   So the crate compiling does not mean the app ships.
2. **`webkit2gtk` is unavailable here**, so the webview half of the shell has
   never rendered. `docs/tauri-termux-blocker.md` records the same blocker for
   the original target platform.
3. **Its test suite is not reliably green under the default harness.**
   `cargo test` (parallel, the default) fails `rpc_reports_a_sidecar_that_dies_
   before_answering` in roughly 2 of 5 runs with
   `Text file busy (os error 26)` at child spawn. It passes 5/5 with
   `--test-threads=1` and in isolation. The scratch-directory naming is
   `(process_id, atomic_counter)`, which is unique within a run, so the exact
   race was not isolated; but a test that fails 40% of the time cannot go into
   a `main` whose CI gate is `-D warnings` and a green test run.
4. **The web shell already covers the user-facing surface.** `web/` serves the
   same UI and drives the same pipeline over local HTTP; the README documents
   it as the usable local interface. Merging an unverifiable second shell would
   add a surface that cannot be demonstrated to work.

Useful parts worth keeping on the branch for a future packaging effort: the
target-triple sidecar resolution in `apps/desktop/src-tauri/src/main.rs`, the
`web/tauri-bridge.mjs` bridge, and `docs/packaging.md` / `docs/desktop-bundling.md`.

## agent-a2478f4bf3ccaf916 — timeline + EDL/FCPXML interchange

Classified **already merged**. Its single commit claims to add
`engine/timeline.py`, `engine/timeline_captions.py` and `engine/nle_export.py`
with their tests — and all seven substantive files are **byte-identical** to
the versions on `main`:

```
IDENTICAL  engine/timeline.py
IDENTICAL  engine/timeline_captions.py
IDENTICAL  engine/nle_export.py
IDENTICAL  tests/timeline_fixtures.py
IDENTICAL  tests/test_timeline.py
IDENTICAL  tests/test_timeline_captions.py
IDENTICAL  tests/test_nle_export.py
```

The only content unique to the branch is a `conftest.py` that rewrites the
editable-install finder's path map so tests import from the worktree directory.
That is a worktree-local convenience, not product work: `main`'s suite runs
green from the repo root without it (803 passed). Not merged.

Since that classification was written, `main` has gone further than the branch
proposed: `engine/timeline_build.py` now writes a per-cut EDL and FCPXML for
every shipped cut from the same interval list the render concatenated
(`c219163` fixed the EDL's rate handling, `431001e` wired it into the pipeline
and the web shell). There is now even less on the branch worth taking.

This is the case the instruction warned about — a branch that looks like 3239
lines of new work in `git diff --stat` and is in fact already on `main`. The
diff stat alone would have produced a redundant merge.

## The other three — corrected: two of them hold unmerged work

This section previously said all three "sit at `72aae5c` with no commits ahead.
Nothing to integrate." That was true about *commits* and false about work: two
of the three carry files that were never committed and exist nowhere on `main`.
They were missed because the check counted commits ahead of `main` and never
listed untracked files. The classification is corrected here, and the standing
instruction not to delete these worktrees is why the work survived long enough
to be found.

**`agent-a0d0fba273d1b37e1` — unmerged, ~4,200 lines.** Nine untracked files:
six engine modules (`lexical.py` 151, `rhythm.py` 599, `threads.py` 911,
`topics.py` 287, `redundancy.py` 359, `corpus.py` 673), two test files
(`test_corpus.py` 518, `test_corpus_modules.py` 360) and `docs/corpus-model.md`
(333). None of the six module names is referenced anywhere in `selekyt/`,
`engine/`, `tests/` or `tools/` on `main`, and all six import standalone — so
this is a self-contained editorial-analysis layer that was written, never
committed, and never wired in. Whether it is worth integrating is an open
question; it is *not* "nothing to integrate".

**`agent-aca62967bb6c0054b` — unmerged.** An untracked `bench/` package
(`runner.py`, `synthetic.py`, `report.py`, `vad_probe.py`, `RESULTS.md`,
`RESULTS.json`) plus `tests/test_bench.py` (517 lines). None of it is on
`main`. Its own `RESULTS.md` states the limitation plainly — synthetic media
and a synthetic transcript, "NOT REAL FOOTAGE. NOT REAL ASR" — so if it is ever
integrated, its numbers must not be quoted as measurements. `tools/microbench.py`
on `main` measures the real pipeline instead.

**`agent-afe57bda5629eefa7` — superseded, correctly classified.** Its four
untracked test files are all on `main`: `test_determinism.py`,
`test_unicode_pipeline.py` and `test_project_lifecycle.py` are byte-identical,
and `test_sidecar_contract.py` is the earlier 337-line draft of `main`'s
1,357-line version. Genuinely nothing left to take.

All three worktrees remain in place under the standing instruction.

## Re-audit note

Re-audited 2026-09-20 against `main` at `90a824d`. The table's "ahead of `main`"
column counts commits only and was the reason the two untracked-file cases above
were filed as empty; read the per-worktree sections rather than the column.
The timeline classification still holds — `0328858` on `main` introduces exactly
the same seven files the worktree's commit does, with matching line counts
(`engine/timeline_captions.py` 680, `tests/test_nle_export.py` 384,
`tests/test_timeline.py` 268, `tests/test_timeline_captions.py` 401,
`tests/timeline_fixtures.py` 181), so that branch remains fully superseded.
