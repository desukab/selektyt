# faster-whisper `tiny` — provenance

## What this is

The `tiny` size of OpenAI Whisper, converted to the CTranslate2 format that
`faster-whisper` loads, republished by Systran. SeleKyT bundles it so a clean
`pip install selekyt` can transcribe without a network connection.

## Origin

- Upstream model: `openai/whisper-tiny` (OpenAI)
- Republisher: `Systran/faster-whisper-tiny` on Hugging Face
- License: **MIT** (per the republisher's model card; the conversion scripts
  carry the same licence)
- Fetched: 2026-10-09
- Upstream commit at fetch: `d90ca5fe260221311c53c58e660288d3deb8d356`

## Files

| file | purpose |
|---|---|
| `model.bin` | the CTranslate2 weights (75 MB, int8-capable) |
| `config.json` | architecture + alignment heads + language ids |
| `tokenizer.json` | the tokeniser |
| `vocabulary.txt` | the raw vocab |

Only these four are needed to construct a `WhisperModel` from the directory
path; the republisher's repo also carries ONNX and PyTorch copies that
SeleKyT does not use, so they are not shipped.

## Why it is bundled

`providers/whisper.py` passes a *size name* to `WhisperModel` for `small` and
`base`, which makes faster-whisper resolve and download them from Hugging Face
at first use. That is the right behaviour for the larger sizes, which are
selected when accuracy matters and download cost is acceptable. `tiny` is
different in one way that matters for a release: it is the last entry in
`FALLBACK_CHAIN`, so a machine that has already given up on `small` and `base`
for speed lands on it. Making that third stop a network download means a run
that has already extracted its audio and built its chunks can still die on a
connection error. Bundling it makes the end of the chain always reachable.

It is also the model the acceptance run uses, so the shipped wheel is verified
against the same weights a user gets.

## Accuracy

`tiny` is the least accurate size Whisper ships. It is used here as the
guaranteed-available floor and as the acceptance default, not as the
recommended model. A user who wants the corpus-measured accuracy installs the
`transcription` extra and passes `--model small`, which downloads on first
use. The fallback chain never *upgrades*, only downgrades, so a run that
asked for `small` keeps `small` unless it is repeatedly too slow.
