from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from models.core import Segment, Word
from providers.atomic import read_json, write_json_atomic


FALLBACK_CHAIN = ["small", "base", "tiny"]

EMPTY_RETRY_PEAK_DB = -45.0
"""Peak level above which a chunk that decoded to zero segments is not silence.

The VAD gate is part of the decode path, so it can suppress an entire chunk
without raising any exception — the decode simply returns no segments. That
outcome is written down as ``status: "ok"`` with an empty ``segments`` list,
which is byte-identical to a genuinely silent chunk, so the failure hardens
into the transcript and the run proceeds as though it had nothing to transcribe.

The two are separable by measuring the audio. Committed corpus, 2026-09-20:
alice-1903 is exactly digital zero (peak −120 dB, the silence floor) and an
empty result is correct; telugu-launch peaks at −1.8 dB, so its empty result
is suppression rather than silence. −45 dB sits between the two with room
either side.

What the retry found on telugu-launch is why no second decode is merged on the
strength of being non-empty. Measured on the 173.7 s source with the small
model: forcing Telugu with the VAD *on* returns 0 segments over the whole clip;
with the VAD *off* it returns 5 segments / 24.88 s of which 54.7% of letters are
Telugu script and the rest is romanized noise plus one Chinese character. A
repeat run of the same file gave 23 segments, so the unguarded decode is not
even stable run to run. Merging it would have written fabricated text into a
transcript whose correct content is empty. See the note in ``transcribe``.
"""


def _vad_retry_needed(peak_db: float) -> bool:
    """Whether an empty decode over this level is silence or suppression.

    The VAD gate is a heuristic inside the decode path, and where it is wrong it
    is wrong in one direction: it discards speech. An empty result over audible
    audio is therefore recorded as suppression rather than as silence, so the
    transcript's emptiness is not misread as "nothing was said"; an empty result
    over silence is correct as it stands.

    The level alone says the audio is there, not that it is speech, and it does
    not license a second decode — see ``transcribe`` for what the unguarded
    decode actually produces on the one source where this fires.
    """
    return peak_db > EMPTY_RETRY_PEAK_DB


def _peak_db(path: Path) -> float:
    """Peak level of a 16 kHz mono WAV in dBFS, floored at silence.

    Reads the audio the chunk was decoded from, which still exists at the call
    site — the file is deleted once the outcome is written.
    """
    import wave

    import numpy as np

    try:
        with wave.open(str(path), "rb") as w:
            n = w.getnframes()
            raw = w.readframes(n)
    except Exception:
        # Unreadable audio cannot be measured, so do not let it block the
        # decode: report the silence side of the bar and let the empty result
        # stand as before.
        return EMPTY_RETRY_PEAK_DB - 1.0
    if not raw:
        return -120.0
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    peak = float(np.abs(x).max())
    # Exactly digital silence has no magnitude; floor it so the value stays
    # finite in the checkpoint.
    if peak <= 0.0:
        return -120.0
    return 20.0 * np.log10(peak)


def _write_chunk_checkpoint(
    path: Path,
    *,
    index: int,
    start: float,
    end: float,
    decode_start: float,
    language: str,
    segments: list[Segment],
    status: str = "ok",
    error: str | None = None,
    note: str | None = None,
) -> None:
    """Record one chunk's outcome atomically.

    A failed decode is written down too — with ``status: "failed"`` — so the
    failure is still visible on disk after the run dies. ``_load_chunk_checkpoint``
    refuses to serve a failed record as a result, which is what makes a failed
    chunk retry instead of hardening into a permanently empty one.

    ``note`` carries an outcome that is neither a clean decode nor a failure:
    a chunk that returned no segments over non-silent audio. It stays ``ok``
    because the decode did complete, but the note keeps it distinguishable from
    genuine silence on disk, where the two would otherwise be identical.
    """
    payload: dict = {
        "index": index,
        "start": start,
        "end": end,
        "decode_start": decode_start,
        "status": status,
    }
    if note:
        payload["note"] = note
    if status == "ok":
        payload["language"] = language
        payload["segments"] = [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "words": [w.__dict__ for w in s.words],
            }
            for s in segments
        ]
    else:
        payload["language"] = language
        payload["error"] = error or "unknown error"
    write_json_atomic(path, payload)


def _load_chunk_checkpoint(path: Path) -> tuple[list[Segment], str, bool, str | None]:
    """Load a chunk checkpoint.

    Returns ``(segments, language, usable, note)``. Only a record with
    ``status == "ok"`` is usable, so a chunk that failed (or was truncated
    mid-write, or holds a schema we no longer understand) is re-decoded rather
    than silently standing in for an empty one. A genuinely silent chunk is an
    ``ok`` record with an empty ``segments`` list and stays cached — real
    silence and failed transcription stay distinguishable, and ``note`` says
    which one a given empty record is.
    """
    data = read_json(path)
    if not isinstance(data, dict) or data.get("status") != "ok":
        return [], "unknown", False, None
    try:
        segments = [
            Segment(
                float(s["start"]),
                float(s["end"]),
                str(s["text"]),
                [Word(**w) for w in s.get("words", [])],
            )
            for s in data.get("segments", [])
        ]
    except (TypeError, KeyError, ValueError):
        return [], "unknown", False, None
    note = data.get("note")
    return segments, str(data.get("language") or "unknown"), True, (
        note if isinstance(note, str) and note else None
    )


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
    subprocess.run(cmd, check=True)
    tmp.replace(audio)


def _transcribe_chunk(model, path: Path, offset: float, language: str | None,
                      beam_size: int = 1, vad_filter: bool = True):
    segments, info = model.transcribe(
        str(path),
        language=language,
        beam_size=beam_size,
        word_timestamps=True,
        vad_filter=vad_filter,
        condition_on_previous_text=False,
    )
    out: list[Segment] = []
    for s in segments:
        words = [
            Word(
                w.word,
                float(w.start) + offset,
                float(w.end) + offset,
                float(getattr(w, "probability", 1.0)),
            )
            for w in (s.words or [])
        ]
        out.append(
            Segment(
                float(s.start) + offset,
                float(s.end) + offset,
                s.text.strip(),
                words,
            )
        )
    return out, info.language


def transcribe(
    media: Path,
    model_name: str = "small",
    language: str | None = None,
    checkpoint_dir: Path | None = None,
    chunk_minutes: int = 5,
    overlap_seconds: float = 15.0,
    cpu_threads: int | None = None,
    beam_size: int = 1,
    vad_filter: bool = True,
) -> tuple[list[Segment], str, dict]:
    """Transcribe long media in bounded, resumable chunks.

    Audio is normalized once to 16 kHz mono PCM. Each Whisper invocation is
    limited to ``chunk_minutes`` plus a small overlap. Completed chunks are
    written to JSON immediately so an interrupted run resumes without repeating
    earlier work. The overlap is resolved in favor of the earlier chunk.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install transcription support: pip install -e '.[transcription]'") from exc

    if chunk_minutes < 1:
        raise ValueError("chunk_minutes must be >= 1")
    if overlap_seconds < 0 or overlap_seconds >= chunk_minutes * 60:
        raise ValueError("overlap_seconds must be >= 0 and smaller than chunk length")

    checkpoint_dir = checkpoint_dir or media.parent / "transcription"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    audio = checkpoint_dir / "audio.wav"
    chunks_dir = checkpoint_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    if not audio.exists() or audio.stat().st_size < 10000:
        print("[transcribe] extracting 16 kHz mono audio", flush=True)
        _extract_audio(media, audio)

    total = _duration(audio)
    chunk_seconds = float(chunk_minutes * 60)
    step = chunk_seconds
    count = max(1, int((total + step - 1e-6) // step))

    print(
        f"[transcribe] {total / 60:.1f} min audio; {count} chunks x {chunk_minutes} min "
        f"(+{overlap_seconds:.0f}s overlap)",
        flush=True,
    )

    _threads = cpu_threads or int(os.environ.get("SELEKYT_CPU_THREADS", str(os.cpu_count() or 4)))
    fallback_rt = float(os.environ.get("SELEKYT_FALLBACK_RT", "10.0"))
    fallback_consecutive = int(os.environ.get("SELEKYT_FALLBACK_CONSECUTIVE", "3"))
    chunk_timeout = int(os.environ.get("SELEKYT_CHUNK_TIMEOUT_MIN", "20")) * 60
    initial_model_name = model_name
    model = WhisperModel(model_name, device="auto", compute_type="int8", cpu_threads=_threads)
    all_segments: list[Segment] = []
    detected_languages: list[str] = []
    running_wall = 0.0
    running_duration = 0.0
    slow_count = 0
    # Chunk numbers that failed to decode. A failed chunk is checkpointed as a
    # failure, not as an empty result, so it is retried on the next run; if it
    # fails again the transcript must not be shipped as though it were whole.
    failed_chunks: list[int] = []
    # Chunks that came back empty, with the measurement that says whether that
    # was silence or suppression. Carried into the transcript's provenance so
    # the outcome is visible beside the model and language it was produced
    # with — an empty transcript over loud audio is a weaker result than one
    # over silence, and the two must not read the same.
    vad_recoveries: list[tuple[int, str]] = []

    for index in range(count):
        start = index * step
        end = min(total, start + chunk_seconds)
        decode_start = max(0.0, start - overlap_seconds) if index else 0.0
        decode_duration = end - decode_start
        chunk_path = chunks_dir / f"chunk_{index:04d}.json"
        was_cached = False
        wall = 0.0

        if chunk_path.exists():
            chunk_segments, detected, was_cached, cached_note = _load_chunk_checkpoint(chunk_path)
            if was_cached:
                print(f"[transcribe] chunk {index + 1}/{count}: cached", flush=True)
                # A recovered chunk loaded from disk on a later run: the note is
                # the only record of what happened, so carry it into this run's
                # provenance too.
                if cached_note:
                    vad_recoveries.append((index + 1, cached_note))
            else:
                # A record that is not a completed decode: a previous failure,
                # a truncated write, or an unreadable schema. It is re-decoded
                # rather than standing in for an empty chunk.
                print(f"[transcribe] chunk {index + 1}/{count}: re-decoding (unusable checkpoint)", flush=True)
        if not was_cached:
            wav = chunks_dir / f"chunk_{index:04d}.wav"
            tmp = wav.with_suffix(".wav.tmp")
            _require("ffmpeg")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{decode_start:.3f}", "-i", str(audio),
                    "-t", f"{decode_duration:.3f}", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", "-f", "wav", str(tmp),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            tmp.replace(wav)
            t0 = time.time()
            print(f"[transcribe] chunk {index + 1}/{count}: {start / 60:.1f}-{end / 60:.1f} min", flush=True)
            failed: str | None = None
            try:
                chunk_segments, detected = _transcribe_chunk(
                    model, wav, decode_start, language, beam_size, vad_filter,
                )
            except Exception as exc:
                # Record the failure but never checkpoint it as a result: an
                # empty segments list written as "ok" would look identical to a
                # genuinely silent chunk and the failure would harden into the
                # transcript permanently.
                failed = f"{type(exc).__name__}: {exc}"
                print(f"    FAILED after {time.time() - t0:.1f}s: {exc}", flush=True)
                chunk_segments = []
                detected = "unknown"
            wall = time.time() - t0
            # Both the measurement and, if it is needed, the retry read this
            # file, so it is deleted only once the outcome is settled. Deleting
            # sooner makes an unreadable path of it, and an unreadable path
            # falls on the silence side of the bar — which quietly disables the
            # retry for every chunk.
            peak = _peak_db(wav) if failed is None else EMPTY_RETRY_PEAK_DB - 1.0

            if failed is None:
                chunk_segments = [
                    s for s in chunk_segments
                    if index == 0 or s.start >= start - 0.05
                ]
            note: str | None = None
            if failed is None and not chunk_segments:
                # A clean decode that produced nothing. That is correct for a
                # silent chunk and wrong for a loud one, and the checkpoint
                # cannot tell them apart, so measure the audio to decide which
                # happened and record it.
                #
                # The earlier version of this branch decoded a second time
                # without the VAD gate and merged whatever came back. That
                # merge is gone, and it is gone on measurement rather than on
                # caution. The unguarded decode of telugu-launch — the one
                # committed source where this branch provably fires — returns
                # 5 segments / 24.88 s of nonsense in the correct script (54.7%
                # Telugu letters, the rest romanized noise plus one Chinese
                # character), and 23 segments on a repeat run of the same file.
                # It is unstable, and it is nonsense in the right script, so no
                # cheap local check separates it from real speech: a
                # script-consistency guard was tried and passes the garbage, and
                # a confidence threshold would reject precisely on the hard
                # audio this branch exists for. Merging it would have written
                # fabricated text into a transcript whose correct content is
                # empty, byte-identical to a real transcript.
                #
                # Nothing is lost by dropping it: no committed chunk checkpoint
                # in the corpus carries a recovery note, so the retry has never
                # fired on a real run and never merged anything. What is kept is
                # the part that is verifiable — the measurement that separates
                # suppression from silence, written to the checkpoint so an
                # empty transcript over loud audio no longer reads as silence.
                if _vad_retry_needed(peak):
                    note = (
                        f"no segments over non-silent audio (peak {peak:.1f} dB); "
                        f"the VAD gate may have suppressed the chunk, and this "
                        f"model could not transcribe it with or without the gate"
                    )
                else:
                    note = f"no segments; chunk is silent (peak {peak:.1f} dB)"
            try:
                wav.unlink()
            except OSError:
                pass
            _write_chunk_checkpoint(
                chunk_path,
                index=index,
                start=start,
                end=end,
                decode_start=decode_start,
                language=detected,
                segments=chunk_segments,
                status="ok" if failed is None else "failed",
                error=failed,
                note=note,
            )
            if note:
                vad_recoveries.append((index + 1, note))
                print(f"    chunk {index + 1}/{count}: {note}", flush=True)
            if failed is not None:
                failed_chunks.append(index + 1)

        all_segments.extend(chunk_segments)
        if detected and detected != "unknown":
            detected_languages.append(detected)

        if not was_cached:
            fallback_tag = " (fallback)" if model_name != initial_model_name else ""
            rt = wall / max(0.1, decode_duration)
            running_wall += wall
            running_duration += decode_duration
            if wall > chunk_timeout:
                print(
                    f"[transcribe] WARNING: chunk {index + 1}/{count} took {wall / 60:.1f} min "
                    f"(> {chunk_timeout / 60:.0f} min timeout); check for a stall",
                    flush=True,
                )
            print(
                f"    chunk {index + 1}/{count}: {wall:.1f}s wall, {rt:.2f}x RT, "
                f"model={model_name}, threads={_threads}{fallback_tag}, "
                f"segments={len(chunk_segments)}",
                flush=True,
            )

            if index >= 2 and running_duration > 0:
                avg_rt = running_wall / running_duration
                remaining_audio = max(0.0, total - end)
                eta = avg_rt * remaining_audio
                print(
                    f"    ETA: ~{eta / 60:.1f} min remaining "
                    f"({count - (index + 1)} chunks, {avg_rt:.2f}x avg RT)",
                    flush=True,
                )

            if index > 0 and rt > fallback_rt:
                slow_count += 1
                if slow_count >= fallback_consecutive:
                    fallback_idx = FALLBACK_CHAIN.index(model_name) if model_name in FALLBACK_CHAIN else -1
                    if fallback_idx != -1 and fallback_idx < len(FALLBACK_CHAIN) - 1:
                        model_name = FALLBACK_CHAIN[fallback_idx + 1]
                        print(
                            f"[transcribe] falling back to {model_name} after "
                            f"{slow_count} consecutive slow chunks",
                            flush=True,
                        )
                        model = WhisperModel(
                            model_name, device="auto", compute_type="int8", cpu_threads=_threads
                        )
                        slow_count = 0
            else:
                slow_count = 0

    all_segments.sort(key=lambda s: (s.start, s.end))
    merged: list[Segment] = []
    for segment in all_segments:
        if merged and segment.start < merged[-1].end - 0.05 and segment.text.strip() == merged[-1].text.strip():
            continue
        merged.append(segment)

    detected = language or (max(set(detected_languages), key=detected_languages.count) if detected_languages else "unknown")
    if failed_chunks:
        # The checkpoints for the chunks that did decode are all on disk, so a
        # re-run resumes from them and only re-attempts these.
        raise RuntimeError(
            f"{len(failed_chunks)} of {count} transcription chunks failed: "
            f"{', '.join(str(n) for n in failed_chunks[:20])}"
            + (" …" if len(failed_chunks) > 20 else "")
            + ". Successful chunks are checkpointed and will not be repeated; "
            "re-run to retry the failures."
        )
    downgrade_reason = None
    if model_name != initial_model_name:
        downgrade_reason = (
            f"downgraded from {initial_model_name} to {model_name} after "
            f"{fallback_consecutive} consecutive chunks slower than "
            f"{fallback_rt:.1f}x realtime"
        )
    print(f"[transcribe] complete: {len(merged)} segments; language={detected}", flush=True)
    provenance = {
        "requested_model": initial_model_name,
        "model": model_name,
        "downgrade_reason": downgrade_reason,
        "chunking": f"{chunk_minutes} min chunks (+{overlap_seconds:.0f}s overlap)",
        "chunks": count,
        "num_threads": _threads,
    }
    if vad_recoveries:
        # Recorded rather than repaired silently: a chunk that decoded to
        # nothing over audible audio is a weaker result than one over silence,
        # and a reader of the transcript needs to see which chunks and why.
        provenance["vad_empty_chunks"] = [
            {"chunk": n, "note": text} for n, text in vad_recoveries
        ]
    return merged, detected, provenance
