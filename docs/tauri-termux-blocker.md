# Tauri shell on Termux ARM64 — blocker record

**Status:** BLOCKED on this device. Recorded per the mandate: "If Tauri cannot
build on Termux, record the blocker and proceed with the sidecar protocol tests
instead."

## What was checked (2026-09-17, Termux ARM64, rustc 1.98.1)

| Check | Result |
|---|---|
| `pkg search webkit` | no webkit2gtk package in the Termux mirrors |
| `pkg-config --exists webkit2gtk-4.1` | **ABSENT** |
| `pkg-config --exists gtk+-3.0` | **ABSENT** |
| `cargo-tauri` binary | not installed |
| `cargo search tauri-cli` | reachable (`tauri-cli 3.0.0-alpha.1`), i.e. the crate is fetchable but **not buildable here** |
| `node` / `npm` | **present** — the Svelte frontend can be developed now |
| CI target (ubuntu-latest x64) | webkit2gtk installable via apt — the shell **will** build in CI |

## Why this blocks the build

Tauri's Linux backend links `webkit2gtk-4.1` through `webkit2gtk-sys`, which
needs the system library and its pkg-config metadata. Neither the library nor
GTK3 is packaged for Termux/Android, so `cargo install tauri-cli` (and any
`tauri build`) fails at the FFI build step, not at Rust compilation. This is an
environment limitation, not a code defect.

## Mitigation in force

1. **The integration surface is the JSON-RPC protocol, not the shell.** Both
   sidecars already speak the same envelope and are covered by wired tests:
   - `rust/sidecar` (`selekyt-mech`) — `tests/test_sidecar_parity.py` spawns the
     compiled binary and asserts cross-language agreement.
   - `selekyt/server.py` (Python) — `tests/test_server.py`, including the
     wire-integrity test that stdout stays one-JSON-object-per-line.
2. **CI (`.github/workflows/ci.yml`) runs on ubuntu-latest x64**, where
   webkit2gtk is installable. The shell will build and be validated there.
3. **The Svelte frontend can be developed now** with the available `node`/`npm`
   (Vite dev server) against a local harness that spawns both sidecars; Tauri
   wraps the same frontend later. Vision order is unchanged: engine first,
   minimal shell later.

## What unblocks this

- Run in CI / a desktop Linux or macOS host, or
- A webkit2gtk package appears in Termux, or
- Tauri's mobile (Android) target is adopted instead of the Linux backend.
