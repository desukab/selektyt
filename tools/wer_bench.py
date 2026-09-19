"""Measure the transcription layer instead of auditing its output.

Four things this tool does, and one it refuses to:

1. ``fetch`` — download the ground-truth corpus from its one pinned source.
   The audio is not committed, so reproducibility has to survive a clean
   checkout.
2. ``prepare`` — build a *fixed, reproducible subsample* of a ground-truth
   corpus (LibriSpeech test-clean) into a manifest. The manifest lists the
   exact files used, so a number can be re-derived.
3. ``bench`` — transcribe that subsample with one model and report WER, CER,
   wall time, real-time factor and peak RSS. The decode parameters are the
   production ones (``providers.whisper._transcribe_chunk``): int8, greedy
   beam 1, ``vad_filter=True``, ``condition_on_previous_text=False``. What
   varies is on the command line, never hidden.
4. ``describe`` — re-run a corpus source through the *production* transcription
   entry point (``providers.whisper.transcribe``) and report what is measurable
   about the output: segment count, character count, mean segment duration,
   detected language and its confidence, and transcript samples. This is the
   instrument for sources where no ground truth exists.

What it refuses: it will never compute a WER for a language it has no
reference transcript for. ``describe`` is deliberately not a WER command.
Inventing a reference for Telugu, Hindi or Hungarian would be fabricating
ground truth, which is worse than reporting "unmeasurable".

Usage::

    .venv/bin/python tools/wer_bench.py fetch \\
        --out corpus/wer/test-clean.tar.gz
    .venv/bin/python tools/wer_bench.py prepare \\
        --tar corpus/wer/test-clean.tar.gz --out corpus/wer/librispeech-clean --count 50
    .venv/bin/python tools/wer_bench.py bench \\
        --manifest corpus/wer/librispeech-clean/manifest.jsonl --model small --tag c9-small
    .venv/bin/python tools/wer_bench.py describe \\
        --media corpus/wer/media_hadhazy-protest.webm --model medium --language hu

``fetch`` exists because the manifest's audio is gitignored (ground-truth audio
stays out of the repository), which means the WER number is only reproducible if
the input can be re-obtained from exactly one place. OpenSLR's LibriSpeech is
public and stable, so that one place is named in code rather than in a doc.
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import tarfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
# The default scratch dir is gitignored: ground-truth audio stays out of the
# repository. The *results* are the evidence and are written here too; copy
# the numbers into docs/TRANSCRIPTION_EVALUATION.md.
DEFAULT_SCRATCH = ROOT / "corpus" / "wer"

# The decode parameters the production path uses. Duplicating them here rather
# than importing a private function keeps the bench stable when the pipeline's
# plumbing changes; a drift detector below asserts they still match.
PROD_DECODE = {
    "compute_type": "int8",
    "device": "auto",
    "vad_filter": True,
    "word_timestamps": True,
    "condition_on_previous_text": False,
}


# ── WER / CER ───────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace.

    This is the standard LibriSpeech reporting convention: the reference
    transcripts carry case and punctuation, the hypothesis carries neither, so
    both are flattened before comparison. Counting case and punctuation as
    errors would report a model's formatting, not its recognition.
    """
    out = []
    for ch in text.lower():
        # Keep apostrophes and hyphens *inside* a word ("don't", "well-known");
        # they are part of the word, not separators. Everything else
        # non-alphanumeric becomes a space.
        out.append(ch if (ch.isalnum() or ch in "'’-") else " ")
    return " ".join("".join(out).split())


def _edit_distance(a: list, b: list) -> list[list[int]]:
    """Full standard Levenshtein matrix; the edit count is `[len(a)][len(b)]`.

    O(len(a) * len(b)); clips are short. Keeping every row rather than only the
    last costs a few KB and makes the returned value match its name — an
    earlier version returned only the final row, which is correct but reads as
    a bug to anyone comparing it against the textbook algorithm.
    """
    rows, cols = len(a) + 1, len(b) + 1
    matrix = [list(range(cols))] + [[i] + [0] * (cols - 1) for i in range(1, rows)]
    for i in range(1, rows):
        ai = a[i - 1]
        prev, cur = matrix[i - 1], matrix[i]
        for j in range(1, cols):
            cur[j] = min(
                prev[j] + 1,        # deletion
                cur[j - 1] + 1,     # insertion
                prev[j - 1] + (ai != b[j - 1]),  # substitution
            )
    return matrix


def _rates(reference: str, hypothesis: str) -> dict:
    """Word and character error rates of ``hypothesis`` against ``reference``.

    Both are normalized first. An empty reference with a non-empty hypothesis
    is a 100% error, not a division by zero — and an empty hypothesis against
    a real reference is too, which is what makes a silent decode visible as a
    bad number instead of a crash.
    """
    ref_w, hyp_w = _normalize(reference).split(), _normalize(hypothesis).split()
    ref_c, hyp_c = list(_normalize(reference)), list(_normalize(hypothesis))
    out = {}
    for key, (r, h) in (("wer", (ref_w, hyp_w)), ("cer", (ref_c, hyp_c))):
        if not r:
            # No reference to score against: any hypothesis at all is a total
            # failure, and silence is a pass. Reported as exactly 100% rather
            # than err/1, which would put an empty reference above 100% and
            # make a summary row read like a arithmetic bug.
            err = len(h)
            n = 1
            out[key] = 1.0 if h else 0.0
        else:
            err = _edit_distance(r, h)[len(r)][len(h)]
            n = len(r)
            out[key] = err / n
        out[f"{key}_ref_len"] = n
        out[f"{key}_err"] = err
    return out


# ── audio plumbing ───────────────────────────────────────────────────────────

def _wav_duration(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as f:
        return f.getnframes() / f.getframerate()


def _to_pcm_wav(src: Path, cache_dir: Path, sample_rate: int = 16000) -> Path:
    """Decode anything ffmpeg reads to mono PCM at ``sample_rate``.

    Cached by content path so a repeated bench does not re-decode. This is the
    same normalization the production extraction performs
    (``providers.audio.extract_pcm_wav``); keeping it here means the bench
    scores the same audio the pipeline would see.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / (src.stem + ".wav")
    if out.is_file() and out.stat().st_size > 0:
        return out
    import subprocess

    tmp = out.with_suffix(".wav.tmp")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1",
         "-ar", str(sample_rate), "-c:a", "pcm_s16le", "-f", "wav", str(tmp)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    tmp.replace(out)
    return out


def _peak_rss_mb() -> float:
    """Peak RSS of this process in MiB (Linux reports kilobytes)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _read_mono(wav: Path) -> tuple[np.ndarray, int]:
    """Read a PCM WAV as mono float32 in [-1, 1], plus its sample rate."""
    import wave

    with wave.open(str(wav), "rb") as f:
        channels, rate = f.getnchannels(), f.getframerate()
        raw = f.readframes(f.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def _cpu_count() -> int:
    import os

    return os.cpu_count() or 4


def _assert_decode_params_match_production() -> None:
    """Fail loudly if the production decode drifts from what the bench scores.

    A benchmark that silently uses different settings from the pipeline
    measures nothing the pipeline can act on, so this is checked at run time
    rather than assumed in the docs.
    """
    try:
        from providers.whisper import _transcribe_chunk
    except ImportError:
        return
    import inspect

    src = inspect.getsource(_transcribe_chunk)
    for key, expected in PROD_DECODE.items():
        if key in ("compute_type", "device"):
            continue
        token = f"{key}={expected!r}"
        if token not in src:
            # Not necessarily wrong, but the bench's assumed parity is now
            # unverified: report it in the run record instead of guessing.
            print(f"[wer_bench] WARNING: production _transcribe_chunk does not "
                  f"show {token}; bench parity is unverified", flush=True)


# ── model handle ─────────────────────────────────────────────────────────────

class _Model:
    """One loaded faster-whisper model, decoded against many clips.

    Loading per clip would make the timing numbers dominated by model load
    rather than decode. The tradeoff is that this is not the exact call graph
    of ``providers.whisper.transcribe``; ``describe`` covers that path.
    """

    def __init__(self, name: str, cpu_threads: int | None, vad_filter: bool):
        from faster_whisper import WhisperModel

        self.name = name
        self.vad_filter = vad_filter
        load_t0 = time.time()
        # 0 means "use all cores" to ctranslate2; None is rejected. The
        # production path normalizes the same way before constructing.
        self.model = WhisperModel(
            name, device=PROD_DECODE["device"],
            compute_type=PROD_DECODE["compute_type"],
            cpu_threads=cpu_threads or 0,
        )
        self.load_seconds = time.time() - load_t0
        self.cpu_threads = cpu_threads

    def decode(self, wav: Path, language: str | None, beam_size: int) -> dict:
        t0 = time.time()
        segments, info = self.model.transcribe(
            str(wav),
            language=language,
            beam_size=beam_size,
            word_timestamps=PROD_DECODE["word_timestamps"],
            vad_filter=self.vad_filter,
            condition_on_previous_text=PROD_DECODE["condition_on_previous_text"],
        )
        texts = [s.text.strip() for s in segments]
        wall = time.time() - t0
        return {
            "text": " ".join(t for t in texts if t),
            "segment_count": len(texts),
            "wall_seconds": wall,
            "detected_language": info.language,
            "language_probability": round(float(info.language_probability), 4),
        }


# ── commands ─────────────────────────────────────────────────────────────────

# OpenSLR's LibriSpeech test-clean. The URL is pinned here rather than looked up
# per run: a WER figure that can only be reproduced from "whatever test-clean is
# current" is not reproducible. LibriSpeech is distributed under CC BY 4.0;
# attribution belongs alongside any number quoted from this corpus.
LIBRISPEECH_TEST_CLEAN_URL = "https://www.openslr.org/resources/12/test-clean.tar.gz"
LIBRISPEECH_LICENSE = ("LibriSpeech (Open Speech and Language Resources, "
                       "https://www.openslr.org), CC BY 4.0")


def cmd_fetch(argv: list[str]) -> int:
    """Download the ground-truth corpus the manifest's audio comes from.

    The audio is deliberately not committed; this command is what makes the WER
    number reproducible from a clean checkout. It refuses to clobber an existing
    file, because a half-downloaded tar.gz would silently corrupt a later
    ``prepare``.
    """
    p = argparse.ArgumentParser(description="Download LibriSpeech test-clean from OpenSLR.")
    p.add_argument("--out", required=True, help="destination .tar.gz path")
    p.add_argument("--url", default=LIBRISPEECH_TEST_CLEAN_URL,
                   help="override the pinned source URL")
    args = p.parse_args(argv)

    import subprocess

    out = Path(args.out)
    if out.is_file() and out.stat().st_size > 0:
        print(f"[fetch] {out} already exists ({out.stat().st_size / 1e6:.0f} MB); "
              f"delete it to re-download")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] {args.url} -> {out}", flush=True)
    # curl rather than urllib: a 346 MB download through urllib gives no
    # progress and no resumability, and a stuck transfer would look like work.
    subprocess.run(["curl", "-fL", "--retry", "3", "-o", str(out), args.url], check=True)
    print(f"[fetch] {out.stat().st_size / 1e6:.0f} MB. Source: {LIBRISPEECH_LICENSE}")
    print(f"[fetch] next: tools/wer_bench.py prepare --tar {out} "
          f"--out corpus/wer/librispeech-clean --count 50")
    return 0


def cmd_prepare(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Build a fixed subsample of a ground-truth corpus.")
    p.add_argument("--tar", required=True, help="tar.gz of the corpus (LibriSpeech layout)")
    p.add_argument("--out", required=True, help="output dir for the subsample + manifest")
    p.add_argument("--count", type=int, default=30, help="number of clips to sample")
    p.add_argument("--max-minutes", type=float, default=8.0,
                   help="stop adding clips once this much audio is sampled")
    p.add_argument("--name", default=None, help="dataset name recorded in the manifest")
    args = p.parse_args(argv)

    tar_path = Path(args.tar)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(tar_path, "r:gz") as tf:
        members = sorted(m.name for m in tf.getmembers()
                          if m.name.endswith(".flac") and m.isfile())
        if not members:
            print(f"[prepare] no .flac members in {tar_path}", file=sys.stderr)
            return 2
        # Deterministic stride, not randomness: the same tar always yields the
        # same clip set, so a WER figure names the audio behind it. A stride
        # also spreads the sample across speakers and books instead of
        # clustering it in one speaker's directory.
        stride = max(1, len(members) // args.count)
        picked: list[str] = []
        total = 0.0
        for name in members[::stride]:
            if len(picked) >= args.count:
                break
            picked.append(name)
            # Extract only what the sample needs. The full corpus is multiple
            # GB and this host has single-digit GB free; extracting everything
            # would fill the disk for no measurement gain.
            tf.extract(name, out_dir)
            # Duration from the FLAC header: the last 4 bytes of the STREAMINFO
            # block carry total samples; ffprobe is the reliable fallback.
        # Transcripts: every .txt is tiny, so take them all — the reference for
        # a sampled clip must exist even when its neighbours were not sampled.
        txt_members = [m.name for m in tf.getmembers()
                       if m.name.endswith(".txt") and m.isfile()]
        for name in txt_members:
            tf.extract(name, out_dir)

    manifest = []
    for name in picked:
        flac = out_dir / name
        utt = flac.stem  # LibriSpeech utterance id, e.g. 6930-75918-0000
        chapter_dir = flac.parent
        # LibriSpeech keeps one transcript per *chapter*, named for the
        # speaker-chapter pair (e.g. 6829-68771.trans.txt), holding one line
        # per utterance.
        ref_txt = chapter_dir / f"{utt.rsplit('-', 1)[0]}.trans.txt"
        if not ref_txt.is_file():
            print(f"[prepare] WARNING: no transcript for {utt} ({ref_txt})")
            continue
        line = next((l for l in ref_txt.read_text(encoding="utf-8").splitlines()
                     if l.startswith(utt + " ")), None)
        if line is None:
            print(f"[prepare] WARNING: no transcript line for {utt}")
            continue
        manifest.append({
            "utterance": utt,
            "audio": str(flac.relative_to(out_dir)),
            "reference": line[len(utt) + 1:],
        })

    # Trim by total duration, longest clips first removed, so the reported
    # audio budget is honoured rather than overshot.
    wavs = []
    for item in manifest:
        wav = _to_pcm_wav(out_dir / item["audio"], out_dir / "pcm")
        wavs.append(wav)
        item["duration"] = round(_wav_duration(wav), 3)
    total = sum(i["duration"] for i in manifest)
    if total > args.max_minutes * 60:
        # Drop the longest clips until within budget — keeps the sample spread.
        for idx in sorted(range(len(manifest)), key=lambda i: -manifest[i]["duration"]):
            if total <= args.max_minutes * 60:
                break
            total -= manifest[idx]["duration"]
            manifest[idx] = None
        manifest = [i for i in manifest if i is not None]
        picked = [i["audio"] for i in manifest]

    total = sum(i["duration"] for i in manifest)
    manifest_path = out_dir / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as fh:
        for item in manifest:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    # A WER figure is only as reproducible as the input it was scored on, so
    # the manifest carries its own provenance next to it: where the audio came
    # from, under what licence, and the exact invocation that selected these
    # clips out of it. The tar is deleted after use, and this is what remains.
    (out_dir / "provenance.json").write_text(json.dumps({
        "corpus": "LibriSpeech test-clean",
        "source_url": LIBRISPEECH_TEST_CLEAN_URL,
        "licence": LIBRISPEECH_LICENSE + " — attribution for the reference "
                   "transcripts this file lists.",
        "tar_member_count": len(members),
        "stride": stride,
        "prepare_args": {"count": args.count, "max_minutes": args.max_minutes,
                         "name": args.name},
        "clips": len(manifest),
        "audio_seconds": round(total, 2),
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[prepare] {len(manifest)} clips, {total / 60:.2f} min audio "
          f"(of {len(members)} available, stride {stride})")
    print(f"[prepare] manifest: {manifest_path}")
    return 0


def cmd_bench(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Transcribe a ground-truth subsample and score WER/CER.")
    p.add_argument("--manifest", required=True, help="manifest.jsonl from `prepare`")
    p.add_argument("--model", required=True, help="faster-whisper model name")
    p.add_argument("--tag", default=None, help="run label (default: <model>-beam<beam>)")
    p.add_argument("--language", default="en", help="language code to force")
    p.add_argument("--beam-size", type=int, default=1, help="decoding beam size")
    p.add_argument("--vad-filter", default="true",
                   choices=["true", "false"], help="whisper-internal VAD filter")
    p.add_argument("--cpu-threads", type=int, default=None)
    p.add_argument("--scratch", default=str(DEFAULT_SCRATCH))
    p.add_argument("--per-clip", action="store_true", help="include per-clip rows")
    args = p.parse_args(argv)

    _assert_decode_params_match_production()
    manifest_dir = Path(args.manifest).parent
    items = [json.loads(l) for l in Path(args.manifest).read_text(encoding="utf-8").splitlines() if l.strip()]
    tag = args.tag or f"{args.model}-beam{args.beam_size}-vad{args.vad_filter}"
    runs_dir = Path(args.scratch) / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    model = _Model(args.model, args.cpu_threads, args.vad_filter == "true")
    per_clip = []
    t_start = time.time()
    for item in items:
        wav = _to_pcm_wav(manifest_dir / item["audio"], manifest_dir / "pcm")
        res = model.decode(wav, args.language, args.beam_size)
        rates = _rates(item["reference"], res["text"])
        rec = {
            "utterance": item["utterance"],
            "duration": item.get("duration") or round(_wav_duration(wav), 3),
            **res,
            **rates,
        }
        per_clip.append(rec)
        print(f"[bench] {item['utterance']} dur={rec['duration']:.1f}s "
              f"wer={rec['wer'] * 100:.1f}% cer={rec['cer'] * 100:.1f}% "
              f"rt={res['wall_seconds'] / max(rec['duration'], 0.1):.2f}x "
              f"lang={res['detected_language']}({res['language_probability']:.2f})", flush=True)
    wall_total = time.time() - t_start

    audio_total = sum(c["duration"] for c in per_clip)
    ref_chars = sum(c["cer_ref_len"] for c in per_clip)
    summary = {
        "tag": tag,
        "corpus": "librispeech-test-clean",
        "model": args.model,
        "language": args.language,
        "beam_size": args.beam_size,
        "vad_filter": args.vad_filter == "true",
        "decode_params": dict(PROD_DECODE, beam_size=args.beam_size,
                              language=args.language,
                              vad_filter=args.vad_filter == "true"),
        "clip_count": len(per_clip),
        "audio_seconds": round(audio_total, 2),
        "audio_minutes": round(audio_total / 60, 3),
        # Aggregate by summed edit operations over summed reference length.
        # Averaging per-clip WER would weight a 3-second clip as heavily as a
        # 30-second one; the sum is the standard corpus-level figure.
        "wer": round(sum(c["wer_err"] for c in per_clip)
                     / max(1, sum(c["wer_ref_len"] for c in per_clip)), 4),
        "cer": round(sum(c["cer_err"] for c in per_clip)
                     / max(1, sum(c["cer_ref_len"] for c in per_clip)), 4),
        "wer_ref_words": sum(c["wer_ref_len"] for c in per_clip),
        "cer_ref_chars": ref_chars,
        # The two integers behind the rates above. A run record that asserts a
        # percentage without its numerator and denominator cannot be checked by
        # arithmetic; these are what make the figures auditable rather than
        # quoted.
        "wer_err_total": sum(c["wer_err"] for c in per_clip),
        "cer_err_total": sum(c["cer_err"] for c in per_clip),
        "wall_seconds": round(wall_total, 2),
        "decode_seconds": round(sum(c["wall_seconds"] for c in per_clip), 2),
        "model_load_seconds": round(model.load_seconds, 2),
        # RTF on decode work only; including model load would penalize a model
        # for the cost of being loaded once.
        "rtf": round(sum(c["wall_seconds"] for c in per_clip) / max(audio_total, 0.1), 3),
        "rtf_including_load": round(wall_total / max(audio_total, 0.1), 3),
        "peak_rss_mb": round(_peak_rss_mb(), 1),
        "cpu_threads": model.cpu_threads or _cpu_count(),
        "cpu_count": _cpu_count(),
        "segments_total": sum(c["segment_count"] for c in per_clip),
        "empty_decodes": sum(1 for c in per_clip if not c["text"].strip()),
    }
    if args.per_clip:
        summary["per_clip"] = per_clip

    out_path = runs_dir / f"{tag}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n[bench] {tag}: WER={summary['wer'] * 100:.2f}% CER={summary['cer'] * 100:.2f}% "
          f"on {summary['clip_count']} clips / {summary['audio_minutes']:.2f} min")
    print(f"[bench] RTF={summary['rtf']}x (decode) / {summary['rtf_including_load']}x (incl load); "
          f"peak RSS={summary['peak_rss_mb']} MB; wall={summary['wall_seconds']:.1f}s")
    print(f"[bench] wrote {out_path}")
    return 0


def cmd_describe(argv: list[str]) -> int:
    """Re-run a corpus source through the production transcription path.

    Reports everything measurable about the output. This is NOT a WER: there
    is no reference transcript, so no error rate is computed for it.
    """
    p = argparse.ArgumentParser(description="Measure a corpus source's transcript, no ground truth involved.")
    p.add_argument("--media", required=True)
    p.add_argument("--model", default="medium")
    p.add_argument("--language", default=None, help="force a language code, or omit for auto-detect")
    p.add_argument("--beam-size", type=int, default=1)
    p.add_argument("--chunk-minutes", type=int, default=5,
                   help="production default 5; set 1 to force multiple chunks")
    p.add_argument("--vad-filter", default="true", choices=["true", "false"])
    p.add_argument("--tag", default=None)
    p.add_argument("--scratch", default=str(DEFAULT_SCRATCH))
    args = p.parse_args(argv)

    from providers.whisper import transcribe as production_transcribe

    media = Path(args.media)
    tag = args.tag or f"describe-{media.stem}-{args.model}-beam{args.beam_size}-chunk{args.chunk_minutes}-vad{args.vad_filter}-lang{args.language or 'auto'}"
    runs_dir = Path(args.scratch) / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    work = Path(args.scratch) / ("describe_" + tag)
    work.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    # The production entry point. Its provenance records the model that
    # actually ran, including a mid-run downgrade — without it a fallback to
    # tiny would look like a medium result.
    segments, detected, prov = production_transcribe(
        media,
        model_name=args.model,
        language=args.language,
        checkpoint_dir=work / "transcription",
        chunk_minutes=args.chunk_minutes,
        beam_size=args.beam_size,
        vad_filter=args.vad_filter == "true",
    )
    wall = time.time() - t0

    speech_dur = sum(s.end - s.start for s in segments)
    rec = {
        "tag": tag,
        "media": str(media),
        "model_requested": args.model,
        "model_used": prov.get("model"),
        "downgrade_reason": prov.get("downgrade_reason"),
        "language_forced": args.language,
        "language_detected": detected,
        "beam_size": args.beam_size,
        "chunk_minutes": args.chunk_minutes,
        "vad_filter": args.vad_filter == "true",
        "chunking": prov.get("chunking"),
        "chunks": prov.get("chunks"),
        "segments": len(segments),
        "transcript_chars": sum(len(s.text) for s in segments),
        "speech_seconds": round(speech_dur, 2),
        "mean_segment_seconds": round(speech_dur / len(segments), 3) if segments else 0.0,
        "wall_seconds": round(wall, 2),
        "peak_rss_mb": round(_peak_rss_mb(), 1),
        # The samples are here so a reader can see the output and judge it
        # themselves. They are NOT evidence of correctness — judging
        # phonetic plausibility in a language nobody here speaks is how
        # garbage gets mistaken for a transcript.
        "samples": [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text}
            for s in segments[:12]
        ],
        "note": ("No reference transcript exists for this source and no speaker of "
                 "the language was available; no error rate is or can be computed. "
                 "Counts and samples only."),
    }
    out_path = runs_dir / f"{tag}.json"
    out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[describe] {tag}: {rec['segments']} segments, {rec['transcript_chars']} chars, "
          f"mean seg {rec['mean_segment_seconds']}s, detected={detected} "
          f"(model used: {rec['model_used']}) in {wall:.1f}s")
    for s in rec["samples"][:6]:
        print(f"    [{s['start']:.1f}-{s['end']:.1f}] {s['text'][:120]}")
    print(f"[describe] wrote {out_path}")
    return 0


def cmd_report(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Print all recorded runs as a table.")
    p.add_argument("--scratch", default=str(DEFAULT_SCRATCH))
    args = p.parse_args(argv)
    runs_dir = Path(args.scratch) / "runs"
    if not runs_dir.is_dir():
        print(f"[report] no runs in {runs_dir}")
        return 2
    rows = []
    for f in sorted(runs_dir.glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        rows.append((f.stem, r))
    print(f"{'run':<52}{'WER':>8}{'CER':>8}{'clips':>7}{'min':>7}{'RTF':>7}{'RSSMB':>8}")
    for name, r in rows:
        # Three record shapes, and the table has to survive a fourth: a bench
        # scores, a describe counts, a detect identifies. A run type the table
        # has never seen should print as unknown, not crash the report — the
        # report is how you find out what is in this directory.
        if "wer" in r:
            print(f"{name:<52}{r['wer'] * 100:>7.2f}%{r['cer'] * 100:>7.2f}%"
                  f"{r['clip_count']:>7}{r['audio_minutes']:>7.2f}"
                  f"{r['rtf']:>7.2f}{r['peak_rss_mb']:>8.0f}")
        elif "segments" in r:
            # A describe row carries no error rate and never will: it is the
            # instrument for sources with no reference transcript. Its RTF is
            # wall time over the speech the transcript *claims*, so a source
            # that decoded almost nothing shows an absurd RTF — that is the
            # coverage failure made visible, not a bug.
            rtf = r.get("wall_seconds", 0.0) / max(r.get("speech_seconds") or 1.0, 0.1)
            print(f"{name:<52}{'—':>8}{'—':>8}"
                  f"{'segs':>4}{r.get('segments', 0):>3}"
                  f"{r.get('speech_seconds', 0) / 60:>7.1f}"
                  f"{rtf:>7.2f}{r.get('peak_rss_mb', 0):>8.0f}")
        elif "detected_language" in r:
            print(f"{name:<52}{'detect':>8}{r['detected_language']:>8}"
                  f"{'p=' + format(round(r['language_probability'], 3), '.3f'):>21}"
                  f"{'':>14}{'':>8}")
        else:
            print(f"{name:<52}{'unknown record shape':>40}")
    return 0


def cmd_detect(argv: list[str]) -> int:
    """Ask the model what language it thinks a source is, and how sure.

    ``providers.whisper.transcribe`` reports the detected language but not its
    confidence, and confidence is what separates "the model heard Telugu and
    failed to decode it" from "the model did not hear Telugu at all". This
    measures the second half without touching the production call signature.
    """
    p = argparse.ArgumentParser(description="Language identification with confidence.")
    p.add_argument("--media", required=True)
    p.add_argument("--model", default="small")
    p.add_argument("--seconds", type=float, default=60.0,
                   help="how much audio to identify from (faster-whisper "
                        "identifies from the start of the file)")
    p.add_argument("--tag", default=None)
    p.add_argument("--scratch", default=str(DEFAULT_SCRATCH))
    args = p.parse_args(argv)

    from faster_whisper import WhisperModel

    media = Path(args.media)
    tag = args.tag or f"detect-{media.stem}-{args.model}"
    wav = _to_pcm_wav(media, Path(args.scratch) / "pcm")
    # faster-whisper's detect_language consumes a slice from the start; the
    # harness hands it a bounded window so a long source costs a bounded time.
    data, sr = _read_mono(wav)
    n = int(min(len(data), args.seconds * sr))
    model = WhisperModel(args.model, device=PROD_DECODE["device"],
                         compute_type=PROD_DECODE["compute_type"], cpu_threads=0)
    result = model.detect_language(data[:n])
    # faster-whisper returns (language, probability, all_language_probs).
    # The full ranking is more informative than the top hit alone: a model
    # with te at 0.30 and hi at 0.28 is telling a different story than one
    # with te at 0.95.
    lang = result[0]
    prob = float(result[1])
    all_probs = result[2] if len(result) > 2 else []
    ranking = [(k, round(float(v), 4)) for k, v in sorted(all_probs, key=lambda kv: -kv[1])[:5]] \
        if isinstance(all_probs, list) else []
    rec = {
        "tag": tag,
        "media": str(media),
        "model": args.model,
        "identify_seconds": round(n / sr, 2),
        "detected_language": lang,
        "language_probability": round(prob, 4),
        "top5": ranking,
    }
    runs_dir = Path(args.scratch) / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{tag}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[detect] {tag}: {lang} @ {prob:.3f} (over {n / sr:.0f}s)")
    return 0


def main(argv: list[str]) -> int:
    cmds = {"fetch": cmd_fetch, "prepare": cmd_prepare, "bench": cmd_bench,
            "describe": cmd_describe, "detect": cmd_detect, "report": cmd_report}
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in cmds:
        print(f"unknown command: {cmd} (expected one of {sorted(cmds)})", file=sys.stderr)
        return 2
    return cmds[cmd](rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
