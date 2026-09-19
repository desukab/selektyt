"""Read what the decode loop actually did on a chunk the pipeline transcribed.

The production `_transcribe_chunk` calls `model.transcribe(...)` and keeps only
`info.language` from the result. The rest of that object carries the answer to
the one question the root-cause analysis could not settle from transcript shape
alone: is the decoder failing its own confidence checks and being retried, or is
it hearing the audio as non-speech and never trying?

Those two readings imply different fixes. A temperature that climbed above 0.0
means a decode failed `log_prob_threshold` / `compression_ratio_threshold` /
`no_speech_threshold` and was retried at the next rung of faster-whisper's
default ladder `[0.0, 0.2, 0.4, 0.6, 0.8, 1.0]` — up to six attempts per window,
which is where the >4x decode time and the run-to-run instability both come
from. A `no_speech_prob` above the threshold with temperature pinned at 0.0
means the model gated the audio as non-speech, which points at a *tunable*
production parameter instead of at the model.

This is a diagnostic, not a production change: it reproduces the production
decode parameters on an existing chunk WAV and prints the `info` object the
pipeline discards. Run it against the chunk checkpoint a describe run already
produced, so the probe and the production decode see byte-identical audio.

Usage:
    .venv/bin/python tools/decode_probe.py \\
        corpus/wer/describe_d-small-forced-telugu-tutorial/transcription/chunks/chunk_0000.wav \\
        --model small --language te
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from faster_whisper import WhisperModel

# Duplicated from the production _transcribe_chunk rather than imported: the
# point is to reproduce the decode the pipeline performs, and importing the
# function would also import its checkpointing and its fallback chain.
PROD_DECODE = {
    "device": "auto",
    "compute_type": "int8",
    "beam_size": 1,
    "word_timestamps": True,
    "condition_on_previous_text": False,
    # temperature deliberately NOT set: neither production nor this probe
    # overrides it, so the default ladder is active in both. Setting it here
    # would measure something the pipeline never does.
}


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("wav", help="chunk WAV to decode (any 16 kHz mono PCM)")
    p.add_argument("--model", default="small")
    p.add_argument("--language", default=None,
                   help="force a code, or omit for auto-detect")
    p.add_argument("--out", default=None, help="write the record as JSON here")
    args = p.parse_args(argv)

    wav = Path(args.wav)
    if not wav.is_file():
        print(f"[probe] no such file: {wav}", flush=True)
        return 2

    model = WhisperModel(args.model, device=PROD_DECODE["device"],
                         compute_type=PROD_DECODE["compute_type"], cpu_threads=0)
    t0 = time.monotonic()
    segments, info = model.transcribe(
        str(wav), language=args.language,
        beam_size=PROD_DECODE["beam_size"],
        word_timestamps=PROD_DECODE["word_timestamps"],
        vad_filter=True,
        condition_on_previous_text=PROD_DECODE["condition_on_previous_text"],
    )
    out = list(segments)
    wall = time.monotonic() - t0

    # 16 kHz mono 16-bit PCM: the byte count is the only duration source that
    # does not require another ffprobe call.
    audio_seconds = wav.stat().st_size / 2 / 16000
    rec = {
        "model": args.model,
        "language_forced": args.language,
        "wav": str(wav),
        "audio_seconds": round(audio_seconds, 1),
        "wall_seconds": round(wall, 1),
        "wall_over_audio": round(wall / audio_seconds, 2) if audio_seconds else None,
        "language_detected": info.language,
        "language_probability": round(float(info.language_probability), 4),
        "temperature_used": info.temperature,
        "no_speech_prob": round(float(info.no_speech_prob), 4),
        "avg_logprob": round(float(info.avg_logprob), 4),
        "compression_ratio": round(float(info.compression_ratio), 4),
        "duration_after_vad": round(float(info.duration_after_vad), 1),
        "segments": len(out),
        "transcript_chars": sum(len(s.text.strip()) for s in out),
        "word_confidences": [round(float(w.probability), 3)
                             for s in out for w in (s.words or [])][:20],
    }
    text = json.dumps(rec, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"[probe] wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
