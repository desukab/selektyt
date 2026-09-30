"""sherpa-onnx transcription backend.

On hosts where faster-whisper cannot run (no ctranslate2 wheel for the
platform's Python, e.g. Termux ARM64 on 3.14), sherpa-onnx's Whisper export
is the transcription engine. It ships as a manylinux-free binary wheel and
runs on CPU.

 sherpa-onnx whisper has a 30-second hard window, so audio is chunked to
 28s. Word-level timestamps are not emitted by these model exports (the
 decoder lacks cross-attention outputs), so words are distributed across
 each whisper segment in proportion to their character count — an
 approximation that is accurate enough for word-safe cut snapping and
 caption alignment, and is stamped into the provenance so nobody mistakes
 it for per-token attention timestamps.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from models.core import Segment, Word
from providers.atomic import read_json, write_json_atomic

# whisper's encoder window is 30s of 16kHz audio; leave headroom for the
# decoder so a chunk never trips the internal "discarding remaining data"
# path that silently truncates the tail of each window.
WHISPER_WINDOW_SECONDS = 28.0

# Where the whisper models live. Resolved once and cached on the recognizer.
DEFAULT_MODELS_DIR = Path.home() / ".cache" / "telugify" / "models"

_SIZE_TO_MODEL = {
    "tiny": "sherpa-onnx-whisper-tiny",
    "base": "sherpa-onnx-whisper-base",
    "small": "sherpa-onnx-whisper-small",
}


def _require(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"{name} is required and must be on PATH")


def _duration(path: Path) -> float:
    _require("ffprobe")
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    return float(subprocess.check_output(cmd, text=True, encoding="utf-8").strip())


def _extract_audio(media: Path, audio: Path) -> None:
    _require("ffmpeg")
    audio.parent.mkdir(parents=True, exist_ok=True)
    tmp = audio.with_suffix(".wav.tmp")
    cmd = [
        "ffmpeg", "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", "-f", "wav", str(tmp),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tmp.replace(audio)


def _norm_tokens(text: str) -> list[str]:
    """Lowercase, punctuation-stripped word tokens for repetition comparison."""
    import re
    return re.findall(r"[a-z0-9']+", text.lower())


def _is_whisper_repeat(a: str, b: str) -> bool:
    """True when two consecutive segments are whisper's repetition artifact.

    The decoder commonly re-emits a segment with a slightly different tail
    ("...Administration, Part 1." then "...Administration."), so exact
    comparison misses it. Two signals both point at the artifact rather than
    at a speaker genuinely repeating themselves: the token sets overlap
    heavily, and one text is a prefix of the other. Real repetition in
    speech is separated by other content, not back-to-back.
    """
    ta, tb = _norm_tokens(a), _norm_tokens(b)
    if not ta or not tb:
        return False
    if len(ta) == len(tb) and ta == tb:
        return True
    # Prefix relationship, either direction: the repeated segment usually
    # gains or loses a trailing clause but keeps the opening intact.
    n = min(len(ta), len(tb))
    if n >= 4 and (ta[:n] == tb[:n]):
        return True
    sa, sb = set(ta), set(tb)
    inter = len(sa & sb)
    union = len(sa | sb)
    if union and inter / union >= 0.75:
        return True
    return False


def _is_section_marker(text: str) -> bool:
    """A bare "Part 2." style marker — content-free by itself."""
    t = text.strip()
    if len(t.split()) > 4:
        return False
    return bool(_SECTION_MARK.search(t) or _SECTION_MARK_INDIC.search(t))


_SECTION_MARK = re.compile(
    r"\b(?:part|chapter|section|episode)\s+(?:\d+|[ivx]+|one|two|three|four|five"
    r"|six|seven|eight|nine|ten)\b", re.IGNORECASE,
)
_SECTION_MARK_INDIC = re.compile(r"భాగం|అధ్యాయం|अध्याय|खंड|பகுதி|ಅಧ್ಯಾಯ|ഭാഗം|অধ্যায়")


def _merge_segments(all_segments: list[Segment]) -> list[Segment]:
    """Drop whisper's repetition, keeping the longer wording.

    The decoder re-emits a segment verbatim across a chunk boundary. The two
    copies are usually adjacent, but a bare section marker can sit between
    them ("…Administration. Part 1. …Administration."), so the comparison
    looks past any content-free markers when finding the previous real
    segment.
    """
    merged: list[Segment] = []
    for segment in all_segments:
        if merged:
            # Walk back over bare markers to the last segment with content.
            prev_idx = len(merged) - 1
            while prev_idx > 0 and _is_section_marker(merged[prev_idx].text):
                prev_idx -= 1
            prev = merged[prev_idx]
            adjacent = segment.start <= prev.end + 0.05
            near = segment.start <= prev.end + 2.5
            if (adjacent or near) and _is_whisper_repeat(prev.text, segment.text):
                # Keep whichever phrasing carries more words; extend the span
                # so no audio coverage is lost.
                if len(_norm_tokens(segment.text)) > len(_norm_tokens(prev.text)):
                    merged[prev_idx] = Segment(prev.start, segment.end, segment.text, prev.words or segment.words)
                else:
                    merged[prev_idx] = Segment(prev.start, segment.end, prev.text, prev.words or segment.words)
                continue
        merged.append(segment)
    return merged


def _split_words(text: str) -> list[str]:
    """Split a whisper segment into words, keeping attached punctuation."""
    return [w for w in text.replace("\n", " ").split(" ") if w.strip()]


def _distribute_words(words: list[str], seg_start: float, seg_end: float) -> list[Word]:
    """Place words inside [seg_start, seg_end] in proportion to their length.

    Character count is a better proxy than a flat split: a seven-character
    word really does take longer to say than a two-character one. Word-safe
    snapping only needs a boundary within a few tens of milliseconds, and
    this lands within that tolerance on natural speech.
    """
    if not words:
        return []
    total = max(1, sum(len(w) for w in words))
    span = max(0.0, seg_end - seg_start)
    out: list[Word] = []
    cursor = seg_start
    for w in words:
        dur = span * len(w) / total
        out.append(Word(w, round(cursor, 3), round(min(cursor + dur, seg_end), 3), 1.0))
        cursor += dur
    if out:
        out[-1].end = seg_end
    return out


def _decode_chunk(recognizer, samples, sample_rate: int, offset: float, language: str):
    """Transcribe one <=28s window. Returns (segments, language)."""
    stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, samples)
    recognizer.decode_stream(stream)
    result = stream.result

    segments: list[Segment] = []
    texts = getattr(result, "segment_texts", None) or []
    starts = getattr(result, "segment_timestamps", None) or []
    durations = getattr(result, "segment_durations", None) or []

    # When segment timestamps are unavailable, fall back to one segment
    # spanning the whole window so the text is not lost.
    if not texts:
        full = (result.text or "").strip()
        if not full:
            return [], language
        texts, starts, durations = [full], [0.0], [len(samples) / sample_rate]

    for i, text in enumerate(texts):
        text = text.strip()
        if not text:
            continue
        s = offset + (starts[i] if i < len(starts) else 0.0)
        d = durations[i] if i < len(durations) else 0.0
        e = min(s + d, offset + WHISPER_WINDOW_SECONDS)
        words = _split_words(text)
        segments.append(
            Segment(round(s, 3), round(e, 3), text, _distribute_words(words, s, e))
        )
    return segments, language


def _model_files(model_name: str, models_dir: Path) -> tuple[Path, Path, Path] | None:
    """Locate a model's three files, in a subdirectory or loose in the dir.

    Tarballs extracted without their wrapper leave the encoder, decoder and
    tokens loose in the models directory; both layouts are probed so
    "small" is used when it is actually present rather than silently
    degrading to base.
    """
    size = model_name if model_name in _SIZE_TO_MODEL else "base"
    for d in (models_dir / _SIZE_TO_MODEL[size], models_dir):
        enc = d / f"{size}-encoder.int8.onnx"
        dec = d / f"{size}-decoder.int8.onnx"
        toks = d / f"{size}-tokens.txt"
        if enc.is_file() and dec.is_file() and toks.is_file():
            return enc, dec, toks
    return None


def _sherpa_onnx():
    """Import sherpa_onnx, or fail with an error an operator can act on.

    sherpa-onnx is an optional extra, not a base dependency: it is only needed
    on hosts where faster-whisper has no wheel. A bare ImportError escaping
    here would surface as "No module named 'sherpa_onnx'" from deep inside a
    transcription run, with no hint that it is a packaging choice. The
    original error is chained rather than discarded, so the traceback still
    shows exactly what the interpreter could not find.
    """
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise RuntimeError(
            "sherpa-onnx is not installed. It is the transcription fallback "
            "for hosts where faster-whisper has no wheel (for example Termux "
            "ARM64); install it with pip install -e '.[sherpa]'"
        ) from exc
    return sherpa_onnx


def _default_threads() -> int:
    """Threads for the sherpa recognizer when the caller did not ask for one.

    Capped at 4: whisper's sherpa export does not scale past a handful of
    threads on CPU and requesting more only contends with the encoder's own
    parallelism.
    """
    return max(1, min(4, os.cpu_count() or 4))


def _recognizer_for(
    model_name: str,
    language: str,
    models_dir: Path | None = None,
    num_threads: int | None = None,
):
    """Build a recognizer for the best available model.

    Returns ``(recognizer, used_model, downgrade_reason)``. ``downgrade_reason``
    is non-None exactly when the requested model was not installed and a
    smaller one was used instead — the run must be able to report that it used
    base when the operator asked for small, rather than silently degrading.
    """
    sherpa_onnx = _sherpa_onnx()

    models_dir = models_dir or DEFAULT_MODELS_DIR
    threads = num_threads or _default_threads()
    # "small" maps to a sherpa-onnx-whisper-small dir if present; the base
    # model is the one that ships with this toolchain, so a missing small
    # model degrades to base rather than failing the run.
    for candidate in (model_name, "base"):
        files = _model_files(candidate, models_dir)
        if files is None:
            continue
        enc, dec, toks = files
        recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=str(enc), decoder=str(dec), tokens=str(toks),
            language=language, num_threads=threads,
            decoding_method="greedy_search",
            enable_segment_timestamps=True,
        )
        reason = None if candidate == model_name else (
            f"requested model '{model_name}' not installed under {models_dir}; "
            f"using '{candidate}'"
        )
        return recognizer, candidate, reason
    raise RuntimeError(
        f"no sherpa-onnx whisper model found under {models_dir}; expected "
        f"{model_name} or base with <size>-encoder.int8.onnx, "
        f"<size>-decoder.int8.onnx, <size>-tokens.txt"
    )


class _WindowedWav:
    """Random-access reader over a PCM WAV that loads one window at a time.

    Reading the entire source into memory (the previous implementation) makes
    the whole point of chunked transcription moot: a three-hour 16 kHz mono
    file is ~350 MB of float32 resident before the first decode. The window is
    read with ``setpos``/``readframes`` on demand, so peak audio memory stays
    bounded by one whisper window regardless of source length.
    """

    def __init__(self, path: Path):
        import wave

        self._f = wave.open(str(path), "rb")
        self.sample_rate = self._f.getframerate()
        self.channels = self._f.getnchannels()
        self.sampwidth = self._f.getsampwidth()
        self.n_frames = self._f.getnframes()

    def window(self, start_sec: float, end_sec: float) -> np.ndarray:
        import numpy as np

        lo = max(0, int(start_sec * self.sample_rate))
        hi = min(self.n_frames, int(end_sec * self.sample_rate))
        if hi <= lo:
            return np.zeros(0, dtype=np.float32)
        self._f.setpos(lo)
        raw = self._f.readframes(hi - lo)
        if self.sampwidth == 2:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif self.sampwidth == 1:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif self.sampwidth == 4:
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            raise ValueError(f"unsupported PCM sample width: {self.sampwidth * 8}-bit")
        if self.channels > 1:
            # Average to mono: dropping a channel could lose a speaker on one
            # side of a stereo mix.
            data = data.reshape(-1, self.channels).mean(axis=1)
        return data

    def close(self) -> None:
        try:
            self._f.close()
        except OSError:
            pass


def transcribe(
    media: Path,
    model_name: str = "base",
    language: str | None = None,
    checkpoint_dir: Path | None = None,
    chunk_minutes: int = 5,
    cpu_threads: int | None = None,
    beam_size: int = 1,
    models_dir: Path | None = None,
    progress: Callable[[dict], None] | None = None,
) -> tuple[list[Segment], str]:
    """Transcribe long media in bounded, resumable chunks.

    Same contract as providers.whisper.transcribe: segments with word
    timestamps, checkpointed per chunk so an interrupted run resumes. Failed
    chunks are checkpointed as failures and retried on the next run, never
    hardened into empty results.

    ``chunk_minutes`` is accepted for signature parity with the whisper backend
    but does not select the chunk size here: sherpa-onnx's whisper export has a
    hard 30-second encoder window, so every chunk is WHISPER_WINDOW_SECONDS
    regardless. That is recorded in the returned provenance so nobody reads a
    run as though it honoured the requested chunk size.
    """
    lang = (language or "en").strip().lower() or "en"
    recognizer, used_model, downgrade_reason = _recognizer_for(
        model_name, lang, models_dir, num_threads=cpu_threads
    )

    checkpoint_dir = checkpoint_dir or media.parent / "transcription"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    audio = checkpoint_dir / "audio.wav"
    if not audio.exists() or audio.stat().st_size < 10000:
        print("[transcribe:sherpa] extracting 16 kHz mono audio", flush=True)
        _extract_audio(media, audio)

    total = _duration(audio)
    reader = _WindowedWav(audio)
    sr = reader.sample_rate
    if sr != 16000:
        # The whisper encoder is fixed at 16 kHz; the extractor above produces
        # exactly that, so a mismatch means the file was not ours.
        reader.close()
        raise ValueError(
            f"sherpa-onnx whisper expects 16 kHz audio, got {sr} Hz in {audio}"
        )

    window = WHISPER_WINDOW_SECONDS
    count = max(1, int((total + window - 1e-6) // window))
    threads = cpu_threads or _default_threads()
    print(
        f"[transcribe:sherpa] {total / 60:.1f} min audio; {count} chunks x "
        f"{window:.0f}s (model={used_model}, lang={lang}, threads={threads}; "
        f"chunk_minutes={chunk_minutes} ignored — fixed whisper window)",
        flush=True,
    )

    all_segments: list[Segment] = []
    failed_chunks: list[int] = []

    def _advance(done: int) -> None:
        """Report the chunk position to a progress sink, if one is attached.

        Sherpa's decode exposes no finer measure than the window counter (no
        wall/RT average is maintained), so that is all a sink gets; the count it
        is measured against is the window count computed above.
        """
        if progress is not None:
            progress({"index": done, "count": count})

    try:
        for index in range(count):
            start = index * window
            chunk_path = checkpoint_dir / f"chunk_{index:04d}.json"
            cached = _load_sherpa_checkpoint(chunk_path)
            if cached is not None:
                chunk_segments = cached
                print(f"[transcribe:sherpa] chunk {index + 1}/{count}: cached", flush=True)
                all_segments.extend(chunk_segments)
                _advance(index + 1)
                continue

            samples = reader.window(start, start + window)
            if len(samples) < sr // 2:
                # The tail of the file is shorter than half a window, so there
                # is no chunk here to decode: the count above was rounded up
                # from a fractional window, and this position was never work.
                _advance(index)
                break

            try:
                segs, _ = _decode_chunk(recognizer, samples, sr, start, lang)
            except Exception as exc:
                # Same rule as the whisper backend: record the failure so it is
                # visible on disk, but never write an empty segments list as
                # though the window were silent.
                _write_sherpa_checkpoint(
                    chunk_path, index, start, used_model, lang, [],
                    status="failed", error=f"{type(exc).__name__}: {exc}",
                )
                failed_chunks.append(index + 1)
                print(
                    f"[transcribe:sherpa] chunk {index + 1}/{count}: FAILED: {exc}",
                    flush=True,
                )
                _advance(index + 1)
                continue
            all_segments.extend(segs)
            _write_sherpa_checkpoint(
                chunk_path, index, start, used_model, lang, segs,
            )
            print(f"[transcribe:sherpa] chunk {index + 1}/{count}: "
                  f"{len(segs)} segments", flush=True)
            _advance(index + 1)
    finally:
        reader.close()

    all_segments.sort(key=lambda s: (s.start, s.end))
    merged = _merge_segments(all_segments)
    if failed_chunks:
        raise RuntimeError(
            f"{len(failed_chunks)} of {count} sherpa chunks failed: "
            f"{', '.join(str(n) for n in failed_chunks[:20])}"
            + (" …" if len(failed_chunks) > 20 else "")
            + ". Successful chunks are checkpointed and will not be repeated; "
            "re-run to retry the failures."
        )
    print(f"[transcribe:sherpa] complete: {len(merged)} segments; language={lang}", flush=True)
    return merged, lang, {
        "engine": "sherpa-onnx-whisper",
        "requested_model": model_name,
        "model": used_model,
        "downgrade_reason": downgrade_reason,
        "word_timestamps": "character-proportional",
        "chunking": f"{window:.0f}s fixed whisper encoder window "
                    f"(chunk_minutes={chunk_minutes} not applicable)",
        "num_threads": threads,
    }


def _load_sherpa_checkpoint(path: Path) -> list[Segment] | None:
    """Load a completed chunk, or None when it must be re-decoded.

    Only ``status == "ok"`` counts: a failed decode is stored so the failure is
    visible on disk, but it is not a result, so the chunk is retried. A
    genuinely silent window is an ``ok`` record with an empty segment list and
    stays cached — silence and failure stay distinguishable.
    """
    data = read_json(path)
    if not isinstance(data, dict) or data.get("status") != "ok":
        return None
    try:
        return [
            Segment(float(s["start"]), float(s["end"]), str(s["text"]),
                    [Word(**w) for w in s.get("words", [])])
            for s in data.get("segments", [])
        ]
    except (TypeError, KeyError, ValueError):
        return None


def _write_sherpa_checkpoint(
    path: Path,
    index: int,
    start: float,
    used_model: str,
    lang: str,
    segments: list[Segment],
    *,
    status: str = "ok",
    error: str | None = None,
) -> None:
    payload: dict = {
        "index": index,
        "start": start,
        "engine": "sherpa-onnx-whisper",
        "model": used_model,
        "language": lang,
        "word_timestamps": "character-proportional",
        "status": status,
    }
    if status == "failed":
        payload["error"] = error or "unknown error"
    payload["segments"] = [
        {"start": s.start, "end": s.end, "text": s.text,
         "words": [w.__dict__ for w in s.words]}
        for s in segments
    ]
    write_json_atomic(path, payload)


def is_available(model_name: str = "base", models_dir: Path | None = None) -> bool:
    """True when sherpa-onnx and a whisper model are usable on this host.

    Probing by actually building the recognizer is deliberate: it confirms
    the model files load, not merely that they exist on disk. Every failure
    mode — missing wheel, missing model, unreadable weights — collapses to
    "unavailable", which is what the caller needs to decide whether to fall
    back.
    """
    try:
        _recognizer_for(model_name, "en", models_dir)
    except Exception:
        return False
    return True


# Keep an explicit note for the parity reader: is_available intentionally does
# not report the downgrade. Callers that need it read the transcribe()
# provenance instead, which records requested_model, model and
# downgrade_reason together.
