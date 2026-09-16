from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from models.core import Segment, Word


FALLBACK_CHAIN = ["small", "base", "tiny"]


def _require(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"{name} is required and must be on PATH")


def _duration(path: Path) -> float:
    _require("ffprobe")
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    return float(subprocess.check_output(cmd, text=True).strip())


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


def _transcribe_chunk(model, path: Path, offset: float, language: str | None, beam_size: int = 1):
    segments, info = model.transcribe(
        str(path),
        language=language,
        beam_size=beam_size,
        word_timestamps=True,
        vad_filter=True,
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
) -> tuple[list[Segment], str]:
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
    initial_model_name = model_name
    model = WhisperModel(model_name, device="auto", compute_type="int8", cpu_threads=_threads)
    all_segments: list[Segment] = []
    detected_languages: list[str] = []
    running_wall = 0.0
    running_duration = 0.0
    slow_count = 0

    for index in range(count):
        start = index * step
        end = min(total, start + chunk_seconds)
        decode_start = max(0.0, start - overlap_seconds) if index else 0.0
        decode_duration = end - decode_start
        chunk_path = chunks_dir / f"chunk_{index:04d}.json"
        was_cached = False
        wall = 0.0

        if chunk_path.exists():
            try:
                data = json.loads(chunk_path.read_text(encoding="utf-8"))
                chunk_segments = [
                    Segment(
                        float(s["start"]),
                        float(s["end"]),
                        str(s["text"]),
                        [Word(**w) for w in s.get("words", [])],
                    )
                    for s in data.get("segments", [])
                ]
                detected = str(data.get("language") or "unknown")
                was_cached = True
                print(f"[transcribe] chunk {index + 1}/{count}: cached", flush=True)
            except (OSError, ValueError, TypeError, KeyError):
                chunk_segments = []
                detected = "unknown"
        else:
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
            try:
                chunk_segments, detected = _transcribe_chunk(model, wav, decode_start, language, beam_size)
            except Exception as exc:
                print(f"    FAILED after {time.time() - t0:.1f}s: {exc}", flush=True)
                chunk_segments = []
                detected = "unknown"
            wall = time.time() - t0
            try:
                wav.unlink()
            except OSError:
                pass

            chunk_segments = [
                s for s in chunk_segments
                if index == 0 or s.start >= start - 0.05
            ]
            chunk_path.write_text(
                json.dumps(
                    {
                        "index": index,
                        "start": start,
                        "end": end,
                        "decode_start": decode_start,
                        "language": detected,
                        "segments": [
                            {
                                "start": s.start,
                                "end": s.end,
                                "text": s.text,
                                "words": [w.__dict__ for w in s.words],
                            }
                            for s in chunk_segments
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        all_segments.extend(chunk_segments)
        if detected and detected != "unknown":
            detected_languages.append(detected)

        if not was_cached:
            fallback_tag = " (fallback)" if model_name != initial_model_name else ""
            rt = wall / max(0.1, decode_duration)
            running_wall += wall
            running_duration += decode_duration
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
    print(f"[transcribe] complete: {len(merged)} segments; language={detected}", flush=True)
    return merged, detected
