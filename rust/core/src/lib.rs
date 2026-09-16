//! SelekyT mechanical core: the deterministic, tool-verified half of the
//! hybrid product's edit pipeline.
//!
//! Everything in this crate is a pure function over intervals, scores, or
//! media metadata — no I/O beyond what an explicit ffmpeg/ffprobe subprocess
//! call performs. The editorial/AI layer lives in the Python sidecar; this
//! crate is where mechanical editing (silence detection, cut math, timecode,
//! FCPXML/MP4 export) is pinned down.
//!
//! Modules:
//!   audio, probe, timecode  — media truth (PCM extraction, ffprobe, SMPTE)
//!   vad, fallback          — speech scoring + deterministic segmentation
//!   cutlist, editalgebra   — the canonical KEEP/REMOVE cutlist model
//!   export_mp4, export_fcpxml — renderers / exporters
//!   waveform               — timeline amplitude envelope
//!   sync                   — poison-tolerant locking helper
//!
//! `audio`, `cutlist`, `export_fcpxml`, `export_mp4`, `probe`, `sync`,
//! `timecode`, `vad`, `waveform` are adapted from AutoCut with permission.
//! `editalgebra` is a Rust port of AutoCut's `src/lib/cuts.ts`. `fallback`
//! is SelekyT-native. See docs/autocut-provenance.md.
pub mod audio;
pub mod cutlist;
pub mod editalgebra;
pub mod export_fcpxml;
pub mod export_mp4;
pub mod fallback;
pub mod probe;
pub mod sync;
pub mod timecode;
pub mod vad;
pub mod waveform;

/// Score PCM with whichever VAD this build was compiled for (Silero when the
/// `silero` feature is on, deterministic energy-RMS otherwise). Both paths use
/// the same 32ms/512-sample chunk cadence.
pub fn score_default(samples: &[i16]) -> anyhow::Result<Vec<f32>> {
    #[cfg(feature = "silero")]
    {
        vad::score_chunks(samples, None)
    }
    #[cfg(not(feature = "silero"))]
    {
        Ok(fallback::score_energy(samples))
    }
}

/// Human/API-facing name for the active VAD backend (reported in every ping).
pub fn vad_backend() -> &'static str {
    if cfg!(feature = "silero") {
        "silero"
    } else {
        "energy"
    }
}

/// Model identifier reported alongside `vad_backend`.
pub fn vad_model() -> &'static str {
    if cfg!(feature = "silero") {
        "silero-v5"
    } else {
        "energy-rms"
    }
}