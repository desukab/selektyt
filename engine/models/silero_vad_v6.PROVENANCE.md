# Silero VAD v6 — provenance

`silero_vad_v6.onnx` is a byte-identical copy of the asset `faster_whisper/assets/
silero_vad_v6.onnx` shipped by **faster-whisper 1.2.1**, which is a hard dependency
of this project. It was copied rather than loaded through the package, because a
VAD backend must not fail because a transcription dependency was removed, and
because the model is used outside any faster-whisper code path.

Verified on 2026-09-27:

* `sha256` prefix `4cbf549b8326f60f…`, 1,245,151 bytes.
* ONNX contract: input `input` float `['seq_len', 576]`, state `h` / `c` float
  `[1, 1, 128]`; outputs `speech_probs`, `hn`, `cn`. The 576-wide input is
  **512 new samples plus 64 samples of context** — see the driver in
  `engine/vad.py` and `docs/autocut-parity.md`.

## Licence

MIT — `silero_vad_v6.LICENSE` here is the upstream `LICENSE` fetched from
`github.com/snakers4/silero-vad`. Copyright (c) 2020-present Silero Team.

One upstream page carries a badge reading "License: CC BY-NC 4.0" while the
sidebar and the `LICENSE` file it links to both say MIT. The `LICENSE` file is
authoritative and is what is stored here; the badge is noted so that a future
reader who finds it does not think this record missed it.
