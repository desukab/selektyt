// Deterministic 16kHz mono PCM WAV sample: speech-like bursts on silence.
// Used by the shell harness (GET /api/sample.wav) and its tests so an
// analysis can be exercised with zero external media.

export function makeSampleWav({
  duration = 8,
  sampleRate = 16000,
  seed = 42,
  speechAmplitude = 0.9,
} = {}) {
  const n = Math.round(duration * sampleRate);
  const samples = new Float32Array(n);
  const bursts = [
    [0.4, 2.1],
    [3.0, 4.6],
    [5.6, 7.1],
  ];
  const fade = Math.round(0.02 * sampleRate);
  const f0 = 300 + (seed % 4) * 20;
  const harmonics = [1, 0.6, 0.3];
  const peak = harmonics.reduce((a, b) => a + b, 0);
  for (const [start, end] of bursts) {
    const s = Math.round(start * sampleRate);
    const e = Math.max(s + 1, Math.round(end * sampleRate));
    for (let i = s; i < e; i++) {
      const t = (i - s) / sampleRate;
      let v = 0;
      for (let k = 0; k < harmonics.length; k++) {
        v += harmonics[k] * Math.sin(2 * Math.PI * (k + 1) * f0 * t);
      }
      const am = 0.85 + 0.15 * Math.sin(2 * Math.PI * 5 * t);
      v = (v / peak) * speechAmplitude * am;
      const edge = Math.min(i - s, e - 1 - i);
      if (edge < fade) v *= 0.5 - 0.5 * Math.cos((Math.PI * edge) / fade);
      samples[i] = v;
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