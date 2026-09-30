"""Measure transcript coverage against speech energy across the corpus.

The corpus's most misleading shipped failure — `telugu-tutorial`, recorded as
OK with one clip — is a transcript covering 4.5% of a file that is 73%
speech-energy audio. No audit in the pipeline saw it, because every audit
compares the transcript against itself or against the selection, never against
the audio it claims to describe.

``docs/TRANSCRIPTION_EVALUATION.md`` §8 recommendation 3 proposes a coverage
check that fails loudly on that class of input. Before a threshold is written
into production it has to be *measured*, not chosen: the ratio of ASR coverage
to speech-energy fraction has to separate the known-bad source from the
known-good ones by a margin wide enough that a cut point is not arbitrary.
That is what this tool establishes.

The measurement itself lives in ``engine.coverage``, not here, so the corpus
survey and the production check cannot drift apart — the numbers this prints
are the numbers the pipeline would have computed on the same source.

The two columns were previously measured on the three non-English sources
alone (§5.2) and disagreed on every row, in both directions — the Hungarian
source has an energy-VAD fraction of 0% against an ASR coverage of 87%, which
is a broken VAD, not a broken transcript. That is why the check is
one-directional and degrades to "not computable" when the VAD itself saw
nothing, instead of passing.

Usage:
    .venv/bin/python tools/coverage_measure.py [--root corpus] [--json out.json]
                                                [--vad-backend energy]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.coverage import _COVERAGE_BACKENDS, COLLAPSE_RATIO, measure


def discover(root: Path) -> list[tuple[str, Path, Path]]:
    """Every corpus project that has both its extraction and its transcript.

    The extraction is what the ASR actually heard, so it is the right thing to
    measure; the original source would give the same fractions at much higher
    decode cost and is not needed here.
    """
    found = []
    for run in sorted(root.glob("*_run")):
        audio = run / "project" / "transcription" / "audio.wav"
        tr = run / "project" / "transcript.json"
        if audio.is_file() and tr.is_file():
            found.append((run.name, audio, tr))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="corpus", type=Path)
    ap.add_argument("--json", type=Path, help="write the full record here")
    ap.add_argument(
        "--vad-backend", default=None,
        choices=[n for n, _cls in _COVERAGE_BACKENDS],
        help=("Which backend supplies the audio truth. Defaults to the chain the "
              "pipeline prefers (silero, then ffmpeg, then energy); forcing one "
              "re-measures the corpus against a different instrument. Whisper is "
              "absent from the choices on purpose — it infers speech from the "
              "transcript's own word timings, so the ratio would be ~1.0 by "
              "construction and a collapse invisible."),
    )
    args = ap.parse_args(argv)

    rows = []
    for name, audio, tr in discover(args.root):
        try:
            record = json.loads(tr.read_text(encoding="utf-8"))
            measured = measure(audio, record.get("segments") or [],
                               vad_backend=args.vad_backend)
            # The regions are dropped here, not in ``measure``: this record is a
            # distribution across a corpus — one row per source, read for the
            # ratios — and carrying every region would dwarf it by orders of
            # magnitude and serve no question the tool answers. Memory is where
            # they are kept, and it takes them from the same dict this just read.
            measured.pop("speech_regions", None)
            rows.append({"source": name,
                         "asr_segments": len(record.get("segments") or []),
                         **measured})
        except Exception as exc:  # a source that cannot be measured is a result
            rows.append({"source": name, "error": f"{type(exc).__name__}: {exc}"})

    rows.sort(key=lambda r: (r.get("coverage_ratio") is None,
                             -(r.get("coverage_ratio") or math.inf)))
    print(f"{'source':28} {'dur':>7} {'vad%':>6} {'asr%':>6} {'ratio':>7}  mean dBFS")
    for r in rows:
        if "error" in r:
            print(f"{r['source']:28} ERROR {r['error']}")
            continue
        ratio = r["coverage_ratio"]
        shown = f"{ratio:>7.3f}" if ratio is not None else "     n/a"
        print(f"{r['source']:28} {r['duration_s']:>7.1f} {r['vad_speech_frac'] * 100:>5.1f}% "
              f"{r['asr_speech_frac'] * 100:>5.1f}% {shown}  {r['mean_rms_dbfs']:>7.1f}")

    computable = [r for r in rows if r.get("coverage_ratio") is not None]
    if computable:
        vals = sorted(r["coverage_ratio"] for r in computable)
        print(f"\ncoverage_ratio over {len(computable)} computable source(s): "
              f"min {vals[0]:.3f}, median {vals[len(vals) // 2]:.3f}, max {vals[-1]:.3f}")
        below = [r["source"] for r in computable if r["coverage_ratio"] < COLLAPSE_RATIO]
        print(f"below the {COLLAPSE_RATIO} collapse threshold: {below or 'none'}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"\nrecord: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
