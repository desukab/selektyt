# Agent worktree disposition

Every agent worktree created during the multi-agent build, classified and
recorded so the reasoning survives the session. None were deleted; none were
force-pushed; no history was rewritten.

| Worktree (`.claude/worktrees/`) | Branch | Ahead of `main` | Classification |
| --- | --- | --- | --- |
| `agent-a8b3e2de76a11eaa1` | `worktree-agent-a8b3e2de76a11eaa1` | 2 commits | **useful but unverifiable** — preserved, not merged |
| `agent-a2478f4bf3ccaf916` | `worktree-agent-a2478f4bf3ccaf916` | 1 commit | **already merged** — substance is byte-identical to `main` |
| `agent-a0d0fba273d1b37e1` | `worktree-agent-a0d0fba273d1b37e1` | 0 | no unmerged work |
| `agent-aca62967bb6c0054b` | `worktree-agent-aca62967bb6c0054b` | 0 | no unmerged work |
| `agent-afe57bda5629eefa7` | `worktree-agent-afe57bda5629eefa7` | 0 | no unmerged work |

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
green from the repo root without it (785 passed). Not merged.

This is the case the instruction warned about — a branch that looks like 3239
lines of new work in `git diff --stat` and is in fact already on `main`. The
diff stat alone would have produced a redundant merge.

## The other three

`agent-a0d0fba273d1b37e1`, `agent-aca62967bb6c0054b` and
`agent-afe57bda5629eefa7` all sit at `72aae5c`, an ancestor of `main`, with no
commits ahead. Nothing to integrate. They are left in place.
