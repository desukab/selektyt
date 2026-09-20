"""Reproduce the per-stage machine-time numbers, instead of quoting them.

`docs/TIME_SAVINGS.md` §2 decomposed the machine side of a run stage by stage
into wall times and RTFs. Those figures came from a terminal session whose
output was never saved and whose source media was later deleted to free disk,
so a reader could check the totals against `corpus/results.jsonl` but nothing
about how they were composed. This tool is the missing artifact: it runs each
stage against committed artifacts and prints a table of the same shape, so the
numbers in the document can be re-derived rather than trusted.

What is measured, and on what:

* **transcribe** — a fixed-length window of 16 kHz mono, decoded from the
  committed source with the production transcription entry point, model warm.
  The window length is on the command line because it is the whole comparison:
  the RTF is per second of *audio*, so a 240 s window and a 60 s window must
  not be quietly mixed.
* **editorial** — `make_thoughts` → `rank` → context gate over the committed
  `transcript.json`. Reports the segment and word counts it operated on,
  because "425 segments" and "156 segments" are different work and the doc
  quoted the former without saying which source it came from.
* **captions**, **timeline** — the shipped clip set from `selections.json`,
  the same files the product emitted.
* **render** — `engine.render.render_clip` over a fixed window of the source,
  both geometries, so the 16:9 / 9:16 cost gap is measured on this host rather
  than asserted.

Best-of-N repetition is a flag, defaulting to 3 as the document claimed. Wall
time is `perf_counter` around the call; CPU time is `resource.getrusage` so
the wall/CPU ratio is measurable too — that ratio is what tells you a slow run
was host contention rather than the pipeline.

What it refuses: it will not infer a number it did not measure. Nothing here
extrapolates from one stage to another, and no total is printed as a "machine
time for the job" — the stages are measured on different windows on purpose,
and summing them would be the exact double-count §10.7 was corrected for.

Usage::

    .venv/bin/python tools/microbench.py \\
        --project corpus/royal-society-cloke_run/project \\
        --source corpus/media/royal-society-cloke.webm --repeat 3

    .venv/bin/python tools/microbench.py --only editorial --repeat 1

Reproducing ``docs/TIME_SAVINGS.md`` §2 exactly means pointing at that job's own
artifacts, which survived::

    .venv/bin/python tools/microbench.py \\
        --project corpus/nasa360-ep5_run/project \\
        --source out/web/pipeline/project/source/NASA_360_program5_HD.mov

That run is recorded in §2b. Beware the transcription stage's cache: the whisper
backend stores chunk checkpoints in ``checkpoint_dir``, which defaults to the audio
file's parent, so a measured call can silently read a JSON off disk instead of
transcribing. Each run here gets a fresh directory for exactly that reason — an
earlier version reported 796 ms for a stage that takes 39 s.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import resource
except ImportError:
    # `resource` is Unix-only; the module is unimportable on Windows. The
    # CPU-time column degrades to None there rather than breaking import.
    resource = None

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models.core import Candidate, Segment, Word  # noqa: E402
from engine.captions import ass_for_candidate, srt_for_candidate  # noqa: E402
from engine.caption_director import caption_metadata  # noqa: E402
from engine.context_guard import validate  # noqa: E402
from engine.edit_plan import retention_intervals  # noqa: E402
from engine.editorial import make_thoughts, rank  # noqa: E402
from engine.render import render_clip  # noqa: E402
from engine.timeline_build import timeline_for_candidate  # noqa: E402
from engine.nle_export import export as nle_export  # noqa: E402
from providers.ffmpeg import require_ffmpeg  # noqa: E402

WINDOW = 240.0  # seconds of 16 kHz mono, the §2 transcription window
RENDER_WINDOW = 60.0  # seconds of source, the §2 render window


def _load_transcript(project: Path) -> list[Segment]:
    data = json.loads((project / "transcript.json").read_text(encoding="utf-8"))
    return [
        Segment(
            float(s["start"]),
            float(s["end"]),
            s["text"],
            [Word(**w) for w in s.get("words", [])],
        )
        for s in data.get("segments", [])
    ]


def _selections(project: Path) -> list[dict]:
    data = json.loads((project / "selections.json").read_text(encoding="utf-8"))
    sel = data["selections"] if isinstance(data, dict) else data
    return sorted(sel, key=lambda c: c["start"])


def _decode_window(source: Path, out: Path, seconds: float) -> tuple[Path, float]:
    """Decode the first `seconds` of audio to 16 kHz mono via ffmpeg.

    Returns the wav and its real duration, which is what an RTF is computed
    against — a window that runs past the end of the source is shorter than
    asked for and would otherwise inflate every RTF in the table.
    """
    import subprocess

    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source),
         "-t", str(seconds), "-ac", "1", "-ar", "16000", "-f", "wav", str(out)],
        check=True, capture_output=True,
    )
    import wave

    with wave.open(str(out), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
    return out, frames / rate


def _bench(fn, repeat: int) -> tuple[float, float]:
    """Best wall time of `repeat` runs, and the CPU time of that best run.

    `fn` takes the run index, 1-based, so a stage that must not reuse state
    between runs (the transcription checkpoint) can vary its inputs.
    """
    best = float("inf")
    best_cpu = 0.0
    for i in range(1, max(1, repeat) + 1):
        ru0 = resource.getrusage(resource.RUSAGE_SELF).ru_utime if resource else 0.0
        t0 = time.perf_counter()
        fn(i)
        wall = time.perf_counter() - t0
        cpu = (
            resource.getrusage(resource.RUSAGE_SELF).ru_utime - ru0
            if resource
            else 0.0
        )
        if wall < best:
            best, best_cpu = wall, cpu
    return best, best_cpu


def bench_transcribe(project: Path, source: Path, model: str, repeat: int,
                     window: float) -> dict:
    from providers.transcribe import transcribe as transcribe_auto

    wav, duration = _decode_window(source, project / "transcription" / "microbench.wav", window)
    print(f"[transcribe] {duration:.1f} s of 16 kHz mono, model={model}, repeat={repeat}",
          flush=True)
    # A cold load must not be charged to the stage: §10.7 counts it separately,
    # and the first call is where the weights come off disk.
    transcribe_auto(wav, model_name=model, beam_size=1, cpu_threads=4)

    # The chunk checkpoint lives in ``checkpoint_dir``, which defaults to the
    # wav's own parent. Without a fresh dir per call every measured run after
    # the first reads a JSON off disk and reports that as transcription time —
    # an earlier version of this bench did exactly that and printed a
    # sub-second figure for a stage that takes ~40 s. Each run here gets a dir
    # nothing has written to, so the timing is decode, not file I/O.
    bench_root = project / "microbench_transcribe"
    bench_root.mkdir(parents=True, exist_ok=True)

    def run(i: int) -> None:
        transcribe_auto(wav, model_name=model, beam_size=1, cpu_threads=4,
                        checkpoint_dir=bench_root / f"run{i}")

    wall, cpu = _bench(run, repeat)
    return {"stage": "transcribe", "wall": wall, "cpu": cpu,
            "basis": f"{duration:.1f} s of 16 kHz mono, model warm, "
                     f"small/int8/beam 1/4 threads, fresh checkpoint per run",
            "work": f"{duration:.1f} s audio"}


def bench_editorial(project: Path, repeat: int) -> dict:
    segments = _load_transcript(project)
    words = sum(len(s.words) for s in segments)
    print(f"[editorial] {len(segments)} segments / {words} words, repeat={repeat}", flush=True)

    def run(_i: int) -> tuple[list[Segment], list[Candidate]]:
        thoughts = make_thoughts(segments)
        ranked = rank(thoughts, limit=8)
        for c in ranked:
            validate(c)
        return thoughts, ranked

    wall, cpu = _bench(run, repeat)
    thoughts, ranked = run(0)
    return {"stage": "editorial", "wall": wall, "cpu": cpu,
            "basis": "make_thoughts → rank → context gate on the committed "
                     "transcript.json",
            "work": f"{len(segments)} segments / {words} words → "
                    f"{len(thoughts)} thoughts → {len(ranked)} gate-passed"}


def bench_captions(project: Path, repeat: int) -> dict:
    sel = _selections(project)
    out = project / "microbench_captions"
    out.mkdir(parents=True, exist_ok=True)
    print(f"[captions] {len(sel)} clips, repeat={repeat}", flush=True)

    cands = [
        Candidate(s["start"], s["end"], s["text"], s.get("story_type", "story"),
                  s.get("hook", ""), s.get("payoff", ""), 0.9, 0.9, 0.9, 0.9,
                  "bench", 0.9,
                  [Word(w["text"], w["start"], w["end"])
                   for w in s.get("words", [])])
        for s in sel
    ]

    def run(_i: int) -> None:
        for i, c in enumerate(cands, start=1):
            srt_for_candidate(c, out / f"{i:02d}.srt")
            ass_for_candidate(c, out / f"{i:02d}.ass")

    wall, cpu = _bench(run, repeat)
    style = caption_metadata(cands[0])["style"] if cands else "n/a"
    return {"stage": "captions", "wall": wall, "cpu": cpu,
            "basis": f"SRT + ASS for every shipped clip, style={style}",
            "work": f"{len(cands)} clips"}


def bench_timeline(project: Path, source: Path, repeat: int) -> dict:
    sel = _selections(project)
    require_ffmpeg()
    from providers.ffmpeg import probe_fps

    fps = probe_fps(source)
    out = project / "microbench_timeline"
    out.mkdir(parents=True, exist_ok=True)
    print(f"[timeline] {len(sel)} clips at {fps:.3f} fps, repeat={repeat}", flush=True)

    cands = [
        Candidate(s["start"], s["end"], s["text"], s.get("story_type", "story"),
                  s.get("hook", ""), s.get("payoff", ""), 0.9, 0.9, 0.9, 0.9,
                  "bench", 0.9,
                  [Word(w["text"], w["start"], w["end"])
                   for w in s.get("words", [])])
        for s in sel
    ]

    def run(_i: int) -> None:
        for i, c in enumerate(cands, start=1):
            timeline = timeline_for_candidate(source, c, fps=fps)
            nle_export(timeline, out / f"{i:02d}")

    wall, cpu = _bench(run, repeat)
    return {"stage": "timeline", "wall": wall, "cpu": cpu,
            "basis": "timeline build + EDL + FCPXML for every shipped clip",
            "work": f"{len(cands)} clips at {fps:.3f} fps"}


def bench_render(project: Path, source: Path, repeat: int, window: float) -> dict:
    require_ffmpeg()
    out = project / "microbench_render"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for vertical, label in ((False, "16:9"), (True, "9:16 anamorphic")):
        cand = Candidate(0.0, window, "microbench window", "story", "", "",
                         0.9, 0.9, 0.9, 0.9, "bench", 0.9, [])
        print(f"[render] {label}, {window:.0f} s window, repeat={repeat}", flush=True)

        def run(_i: int, cand=cand, vertical=vertical) -> None:
            render_clip(source, cand, out / f"{label}.mp4", vertical=vertical)

        wall, cpu = _bench(run, repeat)
        rows.append({"stage": f"render {label}", "wall": wall, "cpu": cpu,
                     "basis": f"engine.render.render_clip, {window:.0f} s of "
                              f"the committed source",
                     "work": f"{window:.0f} s of output"})
    return rows


def _print(rows: list[dict]) -> None:
    print("\n| Stage | Wall (best) | CPU (best run) | Work | Basis |")
    print("| --- | --- | --- | --- | --- |")
    for r in rows:
        print(f"| {r['stage']} | {r['wall'] * 1000:.1f} ms | "
              f"{r['cpu'] * 1000:.1f} ms | {r['work']} | {r['basis']} |")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default=str(ROOT / "corpus" / "royal-society-cloke_run" / "project"),
                    help="a corpus project directory holding transcript.json and selections.json")
    ap.add_argument("--source", default=str(ROOT / "corpus" / "media" / "royal-society-cloke.webm"),
                    help="the source media the project was built from")
    ap.add_argument("--model", default="small")
    ap.add_argument("--window", type=float, default=WINDOW,
                    help="seconds of 16 kHz mono for the transcription stage")
    ap.add_argument("--render-window", type=float, default=RENDER_WINDOW,
                    help="seconds of source for the render stage")
    ap.add_argument("--repeat", type=int, default=3,
                    help="best-of-N; the document claimed best of 3")
    ap.add_argument("--only", choices=["transcribe", "editorial", "captions",
                                       "timeline", "render"],
                    help="run one stage")
    args = ap.parse_args(argv)

    project = Path(args.project)
    source = Path(args.source)
    if not project.is_dir():
        ap.error(f"no such project: {project}")
    if not source.is_file():
        ap.error(f"no such source: {source}")

    stages = {
        "transcribe": lambda: [bench_transcribe(project, source, args.model,
                                                args.repeat, args.window)],
        "editorial": lambda: [bench_editorial(project, args.repeat)],
        "captions": lambda: [bench_captions(project, args.repeat)],
        "timeline": lambda: [bench_timeline(project, source, args.repeat)],
        "render": lambda: bench_render(project, source, args.repeat, args.render_window),
    }
    names = [args.only] if args.only else ["transcribe", "editorial", "captions",
                                           "timeline", "render"]
    rows: list[dict] = []
    for name in names:
        rows.extend(stages[name]())
    _print(rows)

    print("\nNo total is printed. Each stage is measured on a different window on")
    print("purpose — summing them would double-count, which is what §10.7 was")
    print("corrected for. Copy the rows into docs/TIME_SAVINGS.md §2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
