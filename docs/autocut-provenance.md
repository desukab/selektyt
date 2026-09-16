# AutoCut reuse — provenance & attribution

## Authorization

SelekyT reuses proven components from
[AutoCut](https://github.com/cobanov/autocut) by Ali Cobanov.

The author explicitly granted permission (via Instagram, email, and Discord
message) for AutoCut to be freely used, built upon, or copied, including for
this project. The private messages are not committed to this repository; this
document is the record of provenance and attribution.

## License status

AutoCut does **not** carry an OSI-approved open-source license. Reuse is by
the author's explicit word, **not** by license. Prefer—and preserve—the
in-tree attribution headers over any license claim. Do not describe AutoCut
as "MIT", "Apache", or any other OSI-licensed project.

## What is reused and where

| AutoCut component (source files) | SelekyT location | Form |
|---|---|---|
| `src-tauri/src/audio.rs` | `rust/core/src/audio.rs` | Copied, attribution header |
| `src-tauri/src/cutlist.rs` | `rust/core/src/cutlist.rs` | Copied, attribution header |
| `src-tauri/src/export_fcpxml.rs` | `rust/core/src/export_fcpxml.rs` | Copied, attribution header |
| `src-tauri/src/export_mp4.rs` | `rust/core/src/export_mp4.rs` | Copied, attribution header; `build_concat_list` made `pub` for the sidecar |
| `src-tauri/src/probe.rs` | `rust/core/src/probe.rs` | Copied, attribution header |
| `src-tauri/src/sync.rs` | `rust/core/src/sync.rs` | Copied, attribution header |
| `src-tauri/src/timecode.rs` | `rust/core/src/timecode.rs` | Copied, attribution header |
| `src-tauri/src/vad.rs` | `rust/core/src/vad.rs` | Copied + adapted: Silero scorer gated behind optional `silero` cargo feature |
| `src-tauri/src/waveform.rs` | `rust/core/src/waveform.rs` | Copied, attribution header |
| `src/lib/cuts.ts` | `rust/core/src/editalgebra.rs` | Rust port (same semantics) |
| `src/lib/types.ts` (cut types) | `rust/core/src/cutlist.rs` | Rust port |

Every reused/ported file carries a header naming the upstream path and the
permission basis. `rust/core/src/fallback.rs` (energy-RMS scoring), the
`rust/sidecar` binary, and `rust/core/src/lib.rs` selection helpers are
SelekyT-native and are not derived from AutoCut.

## Adjustments made to copied files

- `export_mp4.rs`: `build_concat_list` is now `pub fn` (it was private; the
  `selekyt-mech` sidecar invokes it over its JSON-RPC surface).
- `vad.rs`: Silero model invocation (`detect`, `score_chunks`,
  `is_cancelled`) is gated behind the `silero` cargo feature so the crate
  builds without onnxruntime. `segments_from_scores` and the hysteresis /
  merge / drop helpers are unconditional.
- `.gitignore`: `rust/target/` is ignored.

## Behavioral parity

The parity contract between the copied Rust code and the Python core is
described in `docs/autocut-parity.md`. Behavioral reference values were
verified from the AutoCut source checked out at `~/autocut-inspect`.

## Branding / UI

AutoCut's branding, name, and UI are not carried over. SelekyT keeps its own
name and shell; attribution lives in source headers and this document, not in
product UI.