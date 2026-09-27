// Deterministic 16kHz mono PCM WAV sample: voiced-speech-like bursts on
// silence. Used by the shell harness (GET /api/sample.wav) and its tests so an
// analysis can be exercised with zero external media.
//
// This is a synthetic *voiced excitation*, not a recording, and the
// distinction matters. The VAD chain prefers a neural speech model (silero),
// and a speech model is entitled to score a pure tone as non-speech — the
// previous sample was a 300 Hz fundamental with two harmonics and a shallow
// 15% amplitude modulation, and silero scored it at a maximum of 0.31 over the
// whole 8 s, i.e. the demo's own sample showed "no speech found" on a signal
// that is plainly audible. The energy and ffmpeg backends scored the same
// signal 60% / 57% voiced, so the defect only appeared once the chain
// preferred the model, which is when it shipped.
//
// The replacement keeps the shape of voiced speech: a low fundamental (an
// adult-male pitch) with several harmonics, and a deep syllable-rate envelope.
// Measured across the design space, those two properties are what silero keys
// on — fundamental frequency and harmonic count alone barely move it, while
// syllable modulation depth is decisive (0.2 depth scores 0.04 voiced, 0.8
// scores 0.60). f0 100-140 Hz with 8 harmonics and a depth of 0.8 sits in the
// middle of a plateau where every backend segments all three bursts; 160 Hz
// and above falls off, and a noise floor above roughly -50 dB makes ffmpeg
// score the whole file speech, so the silence is left digital. The plateau is
// recorded in `docs/AI_EXECUTION_STATE.md`; the shipped signal is pinned by
// `tests/test_web_harness.py::test_sample_wav_is_voiced_to_every_available_backend`.

const BURSTS = [
  [0.4, 2.1],
  [3.0, 4.6],
  [5.6, 7.1],
];
const HARMONICS = 8;
const SYLLABLE_HZ = 4;
const SYLLABLE_DEPTH = 0.8;

export function makeSampleWav({
  duration = 8,
  sampleRate = 16000,
  seed = 42,
  speechAmplitude = 0.9,
} = {}) {
  const n = Math.round(duration * sampleRate);
  const samples = new Float32Array(n);
  const fade = Math.round(0.02 * sampleRate);
  const f0 = 100 + (seed % 3) * 20;
  for (const [start, end] of BURSTS) {
    const s = Math.round(start * sampleRate);
    const e = Math.max(s + 1, Math.round(end * sampleRate));
    for (let i = s; i < e; i++) {
      const t = (i - s) / sampleRate;
      let v = 0;
      for (let k = 1; k <= HARMONICS; k++) {
        v += Math.sin(2 * Math.PI * k * f0 * t);
      }
      v /= HARMONICS;
      v *= 1 - SYLLABLE_DEPTH + SYLLABLE_DEPTH * Math.sin(2 * Math.PI * SYLLABLE_HZ * t);
      samples[i] = v;
    }
  }
  // Full-scale the way the measurement did. The harmonic sum's peak is not
  // predictable from the harmonic count, and the energy backend compares RMS
  // against fixed absolute thresholds, so leaving the level to chance leaves
  // the demo's behaviour to chance on the one host whose default backend is
  // level-blind.
  let peak = 0;
  for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(samples[i]));
  if (peak > 0) {
    const scale = speechAmplitude / peak;
    for (const [start, end] of BURSTS) {
      const s = Math.round(start * sampleRate);
      const e = Math.max(s + 1, Math.round(end * sampleRate));
      for (let i = s; i < e; i++) {
        const t = (i - s) / sampleRate;
        const edge = Math.min(i - s, e - 1 - i);
        let v = samples[i] * scale;
        if (edge < fade) v *= 0.5 - 0.5 * Math.cos((Math.PI * edge) / fade);
        samples[i] = v;
      }
    }
  }
  return encodeWav(samples, sampleRate);
}

export function encodeWav(samples, sampleRate = 8000) {
  const n = samples.length;
  const buf = Buffer.alloc(44 + n * 2);
  buf.write("RIFF", 0);
  buf.writeUInt32LE(36 + n * 2, 4);
  buf.write("WAVE", 8);
  buf.write("fmt ", 12);
  buf.writeUInt32LE(16, 16); // fmt chunk size
  buf.writeUInt16LE(1, 20); // PCM
  buf.writeUInt16LE(1, 22); // mono
  buf.writeUInt32LE(sampleRate, 24);
  buf.writeUInt32LE(sampleRate * 2, 28); // byte rate
  buf.writeUInt16LE(2, 32); // block align
  buf.writeUInt16LE(16, 34); // bits per sample
  buf.write("data", 36);
  buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) {
    const v = Math.max(-1, Math.min(1, samples[i]));
    buf.writeInt16LE(Math.round(v * 32767), 44 + i * 2);
  }
  return buf;
}