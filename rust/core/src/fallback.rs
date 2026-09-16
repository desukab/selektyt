//! SelekyT-native deterministic speech-scoring fallback.
//!
//! NOT AutoCut code: AutoCut always scores with Silero V5 via the
//! `voice_activity_detector` crate. This module exists so the hybrid product
//! keeps a buildable, deterministic mechanical core on machines where
//! onnxruntime cannot be installed (Termux ARM64, constrained CI) while
//! preserving the SAME 512-sample @16kHz (32ms) chunk cadence Silero uses, so
//! `segments_from_scores` reads either scorer's output identically.
//!
//! Backend provenance travels with the callers: code that scores with this
//! module MUST record `vad_backend="energy"` (and a fallback_reason) in any
//! report, exactly as the pluggable VAD contract in the Python core does.
//!
//! The score is a soft-clipped RMS loudness in [0,1]: energy below `quiet`
//! scores 0, energy at or above `loud` scores 1, and values in between map
//! linearly. Zero-crossing rate is ignored — this deliberately trades the
//! Energy-VAD's ZCR refinement for a fixed, well-defined, testable curve so
//! parity tests can assert exact numbers instead of ranges.

use crate::audio::{to_unit, VAD_SAMPLE_RATE};

/// Silero's fixed 512-sample analysis window at 16kHz. Kept in lockstep with
/// `vad::CHUNK_SIZE` so both scorers feed the same cadence downstream.
pub const CHUNK_SIZE: usize = 512;
pub const CHUNK_SECONDS: f64 = CHUNK_SIZE as f64 / VAD_SAMPLE_RATE as f64; // 0.032

/// Below this RMS the window is silent. Above `loud` it is full-strength
/// speech. Tuned so 16-bit silence-plus-dither (sum of two uint16 noise
/// floors ≈ 1.9e-5 RMS) sits firmly in the silent zone.
pub const QUIET_RMS: f32 = 0.0025;
pub const LOUD_RMS: f32 = 0.08;

/// Score 16kHz mono samples into one probability per 512-sample chunk.
///
/// Trailing samples short of a full chunk are dropped, mirroring how the
/// reference implementation consumes the stream chunk by chunk.
pub fn score_energy(samples: &[i16]) -> Vec<f32> {
    samples
        .chunks(CHUNK_SIZE)
        .filter(|c| c.len() == CHUNK_SIZE)
        .map(|chunk| {
            let mut sum = 0f64;
            for &s in chunk {
                let u = to_unit(s) as f64;
                sum += u * u;
            }
            let rms = (sum / chunk.len() as f64).sqrt() as f32;
            ((rms - QUIET_RMS) / (LOUD_RMS - QUIET_RMS)).clamp(0.0, 1.0)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sine_samples(n: usize, freq_hz: f64, amp: f32) -> Vec<i16> {
        let sr = VAD_SAMPLE_RATE as f64;
        let amp = amp as f64;
        (0..n)
            .map(|i| (amp * (2.0 * std::f64::consts::PI * freq_hz * i as f64 / sr).sin()) * 32767.0)
            .map(|v| v as i16)
            .collect()
    }

    #[test]
    fn scores_full_scale_sine_as_speech() {
        let samples = sine_samples(CHUNK_SIZE, 440.0, 0.9);
        let scores = score_energy(&samples);
        assert_eq!(scores.len(), 1);
        assert!(scores[0] >= 0.99, "loud sine should saturate, got {}", scores[0]);
    }

    #[test]
    fn scores_silence_as_not_speech() {
        let samples = vec![0i16; CHUNK_SIZE * 3];
        let scores = score_energy(&samples);
        assert_eq!(scores.len(), 3);
        assert!(scores.iter().all(|s| *s < 1e-6));
    }

    #[test]
    fn quiet_dither_stays_below_the_threshold() {
        // uint16 noise (±1 LSB around 0) must read as silence.
        let samples: Vec<i16> = (0..CHUNK_SIZE)
            .map(|i| if i % 2 == 0 { 1 } else { 0 })
            .collect();
        let scores = score_energy(&samples);
        assert!(scores[0] < 0.1, "dither leaked into speech: {}", scores[0]);
    }

    #[test]
    fn trailing_partial_chunk_is_dropped() {
        let samples = sine_samples(CHUNK_SIZE * 2 + 100, 440.0, 0.9);
        assert_eq!(score_energy(&samples).len(), 2);
    }

    #[test]
    fn light_tone_is_above_silence_but_not_full_speech() {
        // -23 dBFS tone: RMS ~0.07, inside the linear zone, not saturated.
        let samples = sine_samples(CHUNK_SIZE, 440.0, 0.07);
        let score = score_energy(&samples)[0];
        assert!(score > 0.3 && score < 0.99, "got {score}");
    }
}