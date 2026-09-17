# AutoCut behavioral parity spec

This document pins down every AutoCut behavior SelekyT is expected to match.
Values are verified from AutoCut's source (checked out at
`~/autocut-inspect`), not guessed. Parity means: **given the same inputs, the
same edit boundaries come out**. Where SelekyT intentionally deviates, the
deviation is listed here so it is a recorded decision, not a silent drift.

Parity is enforced in two places that must agree:

- Python (`engine/vad.py`, `engine/cut_algebra.py`, `engine/jumpcut.py`,
  `engine/timecode.py`, `engine/fcpxml.py`)
- Rust (`rust/core/src/vad.rs`, `rust/core/src/editalgebra.rs`,
  `rust/core/src/export_mp4.rs`, `rust/core/src/export_fcpxml.rs`,
  `rust/core/src/timecode.rs`)

Cross-language agreement is checked against the live `selekyt-mech` sidecar
for segmentation, CutList inversion, SMPTE timecode, and FCPXML rendering
(byte-identical); reference values are also pinned as unit tests in
`tests/test_vad.py`, `tests/test_cut_algebra.py`, `tests/test_timecode.py`,
and `tests/test_fcpxml_multitrack.py`.

## Verified reference values

| Constant | Value | Source |
|---|---|---|
| Silero chunk | 512 samples @16kHz = 32ms | `vad.rs: CHUNK_SIZE` |
| Default threshold | 0.5 | `VadParams::default` |
| Default min silence | 100ms | `VadParams::default` |
| Default min speech | 150ms | `VadParams::default` |
| Hysteresis release | `max(threshold - 0.15, 0.05)` | `vad.rs` |
| Silence merge | strict `< min_silence_ms` (short silences merge) | `vad.rs: merge_close` |
| Speech keep | `>= min_speech_ms` | `vad.rs: drop_short` |
| CutList pad | 0.3s both sides | `store.svelte.ts: pad: 0.3` |
| Inversion clamp | `[0, source_duration]` | `cutlist.rs: from_speech_segments` |
| Inversion merge | `s <= last.1` (touching merges) | `cutlist.rs: from_speech_segments` |
| Inversion tiling | gaps → Remove, covers `[0, duration]` | `cutlist.rs` / `cuts.ts: tileCutlist` |
| DEGENERATE_SECONDS | 0.01 | `cuts.ts` |
| MIN_KEEP_SECONDS | 0.05 | `cuts.ts` |
| resolveForExport coalesce | `< 1e-6` between adjacent removes | `cuts.ts: resolveForExport` |
| MP4 export | ffconcat demuxer, CRF 18/22/26, audio 192/128/96k, `scale=-2:H`, `out_time_us=` progress | `export_mp4.rs` |
| FCPXML | 1.11, canonical rationals (60000 @59.94, 24000 @23.976), DF/NDF, source-TC asset start | `export_fcpxml.rs` |
| Linked tracks | video lanes climb 1,2…; audio-only descend -1,-2…; per-keep coverage trim; per-track TC shift | `export_fcpxml.rs` / `commands.rs` |
| Track offset from TC | `trackTC - referenceTC` when both carry timecode; else 0 | `commands.rs: open_track` |
| Degenerate fps | non-positive/NaN/inf → 30fps fallback (`1/30`), never an infinite frame | `timecode.rs: detect_rate` |

## Algorithms

### Speech segmentation (hysteresis)

1. Score every chunk (probabilities in `[0,1]`).
2. Binarize with hysteresis: speech starts when a chunk reaches `threshold`;
   it continues while chunks stay `>= release` where
   `release = max(threshold - 0.15, 0.05)`.
3. Group consecutive speech chunks into raw runs.
4. Merge two runs when the gap is **strictly less** than `min_silence_ms`
   (a gap of exactly 100ms stays a gap).
5. Drop runs **strictly shorter** than `min_speech_ms`.

This is cadence-independent: it operates on the score's own frame grid, so
Python (energy backend, 32ms) and the Rust core (Silero/energy, 32ms) agree
on segment boundaries for identical score arrays. Both scorers drop trailing
samples short of a full 512-sample chunk (Rust `score_energy`, Python
`EnergyVAD.analyze`), so they always emit the same frame count for the same
input; only a sub-32ms input is padded to a single frame so the score array is
never empty.

`time_offset` (AutoCut `DetectParams::source_offset`) is added to every
boundary so detection on a linked track that rolled after the reference still
yields project-time segments. Python: `segment_from_scores(..., time_offset)`;
Rust: `segments_from_scores(scores, params, time_offset)`; both sidecars
accept it as a `segment` param. Cross-language agreement is asserted in
`tests/test_sidecar_parity.py::test_segment_time_offset_matches_across_languages`.

### CutList inversion (`from_speech_segments`)

1. Pad each speech region by `pad` on both sides.
2. Clamp into `[0, source_duration]`.
3. Drop degenerate intervals (`end <= start`).
4. Sort by start.
5. Merge when the next `start <=` the previous `end` (touching merges).
6. Tile: gaps become Remove; output covers `[0, source_duration]`.

SelekyT Python: `cut_algebra.cutlist_from_intervals` (exact).
Rust: `cutlist::CutList::from_speech_segments` (exact).

### Export resolution

`resolveForExport` turns disabled keeps into removes and coalesces adjacent
removes closer than `1e-6`. SelekyT Python: `cut_algebra.resolve_for_export`.
Rust: `editalgebra::resolve_for_export`.

### SMPTE timecode

`parse_smpte` parses `HH:MM:SS:FF` / `HH:MM:SS;FF` into source-relative
seconds with NTSC drop-frame (drop 2 or 4 frames per minute except every
tenth); `detect_rate` resolves a reported fps to a canonical rational
(`1001/30000` @29.97 etc., kept un-reduced because NLEs key on these values);
`seconds_to_rational` renders a duration snapped to the nearest frame.
SelekyT Python: `engine/timecode.py`. Rust: `timecode.rs`. Both sidecars
expose `detect_rate` / `parse_smpte` / `seconds_to_rational` ops, and
cross-language agreement is asserted in `tests/test_sidecar_parity.py`.

`track_offset_from_timecode` reproduces AutoCut's `open_track` behavior: when
both the reference and a linked track carry embedded timecode, the track's
media begins at project time `trackTC - referenceTC`; without timecode on
both sides there is nothing to derive from, so it starts at zero.

### Multi-track FCPXML

One reference track defines project time (project second `t` == reference
source second `t`); linked tracks state where their media begins via `offset`
and are cut in lockstep with the reference's keeps. Per keep, a linked track
contributes a connected clip only for the project time it actually has media
for (`max(keep_start, offset)` … `min(keep_end, offset + duration)`), so a
track that rolled late, stopped early, or is simply shorter never points the
NLE past the end of its file. Video lanes climb `1, 2, …` above the primary
storyline; audio-only lanes descend `-1, -2, …`. SelekyT Python:
`engine/fcpxml.py::render` (fed by `engine/multitrack.py::to_fcpxml_params`).
Rust: `export_fcpxml::render`. Byte-identical output is asserted in
`tests/test_sidecar_parity.py::test_render_fcpxml_is_byte_identical_across_languages`.

## Intentional deviations

- **Energy VAD scoring curve.** The energy backend's RMS→probability curve
  (`energy-rms`) is SelekyT-native and is **not** an AutoCut algorithm. It must
  agree on the *cadence* (one 512-sample @16kHz score per chunk) and on the
  speech/silence *decision* that feeds segmentation, not on raw score values.
  The two implementations deliberately use different RMS→probability anchors:

  | | silence floor | speech ceiling | ZCR gate |
  | --- | --- | --- | --- |
  | Rust `fallback::score_energy` | `QUIET_RMS` 0.0025 | `LOUD_RMS` 0.08 | none |
  | Python `EnergyVAD` | `base_threshold` 0.02 | `speech_ceiling` 0.08 | frames with ZCR outside [0.02, 0.45] are damped ×0.3 |

  Both saturate to 1.0 on voiced bursts and 0.0 on silence, so the 0.5
  threshold decision agrees on clear content; divergence is confined to
  mid-range transitions (measured ≤ 0.09 on the wired fixture, 0 of 128 frames
  disagreeing at the threshold). The ZCR refinement is Python-only by design —
  the Rust core keeps a fixed, well-defined curve so parity tests can assert
  exact numbers.
- **Word-safe editorial snapping.** `JumpCutEngine` snaps keep edges outward
  to word boundaries and keeps intentional/breathing pauses instead of raw
  `pad=0.3`. This is a deliberate editorial layer on top of the parity core;
  the parity Inversion is available as `cutlist_from_intervals` for exact
  AutoCut output.
- **Merge strictness in the editorial loop.** `cuts.ts normalizeKeeps` merges
  with `k.start < last.end` (touching stays separate) while the VAD→CutList
  inversion uses `s <= last.1`. SelekyT mirrors both: the editorial model uses
  `<`-merge, the inversion uses `<=`-merge.
- **FCPXML clip vocabulary.** The previous Python emitter invented its own
  spine (`<clip name="keep_0.00_...">` plus `<marker value="remove_silence">`
  for gaps). It has been replaced with the AutoCut emitter's vocabulary —
  `<asset>` + `<asset-clip>` on the primary storyline, DF/NDF stamping,
  source-TC asset starts — so the Python and Rust sides now produce
  byte-identical documents. Skip markers are gone: a REMOVE is the gap
  between two keeps, not an object on the timeline.

## NOT verified

- **Silero V5 numeric weight parity.** The model's raw probabilities are not
  reproduced on this device (onnxruntime is not installable on Python 3.14
  Termux ARM64). Behavior yielded by the weights — as opposed to the
  segmentation algorithm — is out of scope for parity claims.

## Test coverage

- `tests/test_sidecar_parity.py` — **wired cross-language check**: spawns the
  compiled `selekyt-mech` binary over its JSON-RPC pipe and asserts the Python
  core agrees on the 32ms scoring cadence + VAD provenance
  (`test_score_cadence_and_provenance_match_python`), segmentation for identical
  score arrays (hysteresis / strict merge / min-speech), CutList inversion,
  SMPTE timecode ops, `time_offset`, **byte-identical multi-track FCPXML**, and
  that both sidecars answer with the same envelope
  (`test_envelope_shape_matches_the_python_sidecar`). Runs in CI via
  `SELEKYT_MECH`; set `SELEKYT_PARITY_MEDIA` to cross-check a real file.
- `tests/test_timecode.py` — SMPTE reference values mirroring
  `rust/core/src/timecode.rs` tests, including the degenerate-fps fallback and
  the 120000-denominator path.
- `tests/test_fcpxml_multitrack.py` — multi-track FCPXML behaviors mirroring
  the `export_fcpxml.rs` test suite: lane fan-out, coverage trimming, record
  cursor, per-track TC shift, DF/NDF stamping, escaping, empty-timeline
  refusal.
- `tests/test_vad.py::test_parity_reference_boundaries_32ms` — exact 32ms run
  boundaries.
- `tests/test_vad.py::test_parity_reference_gap_stays_split` — strict
  `< min_silence_ms` (128ms gap stays split).
- `tests/test_vad.py::test_parity_release_floor_clamps_low_threshold` —
  `max(threshold - 0.15, 0.05)` floor.
- `tests/test_vad.py::test_parity_time_offset_shifts_boundaries_not_durations`
  — `source_offset` shifts placement without rescaling durations.
- `tests/test_multitrack.py::test_to_fcpxml_params_*` — the timeline model's
  conversion to export params (reference must sit at project time 0).
- `tests/test_cut_algebra.py::test_parity_inversion_*` — pad/clamp/merge/tile
  reference values.
- `tests/test_cut_algebra.py::test_parity_resolve_for_export_*` — 1e-6
  coalescing.
- `rust/core/src/vad.rs` + `rust/core/src/editalgebra.rs` +
  `rust/core/src/export_fcpxml.rs` + `rust/core/src/timecode.rs` unit tests —
  Rust reference implementation.
- `rust/sidecar/src/main.rs` — JSON-RPC ops used by the live cross-check.