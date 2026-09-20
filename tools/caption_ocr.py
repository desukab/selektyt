"""Read burned-in (open) captions out of a video, as reference text.

The WER harness in ``tools/wer_bench.py`` scores against a reference transcript.
For real corpus video there usually is not one — the captions are rendered into
the picture, not shipped as a text or subtitle track. This tool recovers them by
OCR, which turns a video the corpus already has into a WER measurement that
would otherwise need a transcript nobody published.

What it produces, and what each is for:

* ``ocr_cache.json`` — the raw per-frame OCR, so the dedup can be re-run and
  tuned without paying for the OCR pass again. The OCR is the expensive part.
* ``reference.jsonl`` — one row per caption: start, end, the text, and how many
  frames support it. The support count is what separates a real caption from a
  transition frame caught mid-render.
* ``reference.txt`` — the rows concatenated, ready to hand to ``wer_bench``.
* ``provenance.json`` — the video, the crop band, the frame rate and the
  model, so the reference is reproducible from the source file.

What it does not do: it does not score anything. Keeping OCR and WER in
separate tools keeps the reference honest — a WER number that could be tuned by
editing the reference is not a measurement.

The reference *is* contaminated by OCR error, and that is the limit on any WER
scored against it. ``build`` reports the defects it can identify, but the
measured WER is an upper bound on the recognition error, not the error itself.
Correcting the OCR by hand to match a decode output would make the number
worthless, so the raw machine reading is what ships.

Usage::

    .venv/bin/python tools/caption_ocr.py \\
        --video corpus/media/ooi-vertical.webm \\
        --out corpus/wer/captions-ooi-vertical

``rapidocr-onnxruntime`` is a measurement-only dependency; it is not used by the
engine and is not in the production requirements. If it is missing the tool
says so rather than degrading to a silent empty reference.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ── frame extraction ────────────────────────────────────────────────────────

def _extract_band_frames(video: Path, out_dir: Path, fps: float,
                         band_top: float, band_bottom: float) -> list[Path]:
    """Crop the caption band out of every Nth frame as PNGs.

    Cropping matters for two reasons: it is many times faster than OCR on the
    full frame, and it is what excludes on-screen furniture that is not
    caption text — title cards, name straps and logos read just as confidently
    as a caption does, and would otherwise become part of the reference.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0:s=x", str(video)],
        capture_output=True, text=True, check=True)
    width, height = (int(x) for x in probe.stdout.strip().split("x"))
    top = int(height * band_top)
    band_h = int(height * (band_bottom - band_top))
    pattern = out_dir / "band_%05d.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video),
         "-vf", f"fps={fps},crop={width}:{band_h}:0:{top}",
         str(pattern)],
        check=True)
    frames = sorted(out_dir.glob("band_*.png"))
    if not frames:
        raise SystemExit(f"no frames extracted from {video}; is it a video?")
    return frames, top, band_h


def _ocr_frames(frames: list[Path], fps: float) -> list[dict]:
    """OCR each frame, keeping the time, the joined text and the confidence."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise SystemExit(
            "rapidocr-onnxruntime is not installed. It is a measurement-only "
            "dependency of this tool, not a production one:\n"
            "    .venv/bin/pip install rapidocr-onnxruntime")
    engine = RapidOCR()
    out = []
    for i, frame in enumerate(frames):
        import cv2
        image = cv2.imread(str(frame))
        result, _ = engine(image)
        if result:
            # Sort top-to-bottom so a two-line caption reads in the right order.
            result.sort(key=lambda row: min(p[1] for p in row[0]))
            text = " ".join(row[1].strip() for row in result).strip()
            conf = float(sum(float(row[2]) for row in result) / len(result))
        else:
            text, conf = None, None
        out.append({"t": round(i / fps, 3), "text": text, "conf": conf})
        if (i + 1) % 50 == 0:
            print(f"[ocr] {i + 1}/{len(frames)} frames")
    return out


# ── turning per-frame OCR into a caption sequence ───────────────────────────

def _normalize(text: str) -> str:
    """The WER harness normalizes the same way, so the reference has to match:
    lowercase, punctuation to whitespace, whitespace collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", text.lower())).strip()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def _cluster_captions(rows: list[dict], min_support: int,
                      min_length: int, distance: float) -> list[dict]:
    """Group per-frame readings into captions.

    A burned-in caption holds steady for a second or more, so it appears as
    several near-identical OCR readings followed by a different set for the
    next caption. The failure modes that have to survive:

    * **Transition frames.** A caption fading or sliding in renders partway,
      and the OCR of a partial render is garbage. Those readings occur once or
      twice and cluster with nothing, so a support floor drops them.
    * **Jitter.** The same caption read across frames varies slightly — a
      dropped word, a box detected a pixel low. Clustering absorbs them into
      the caption they resemble, and the representative is the mode, not the
      first or last reading.
    * **Duplicates.** A caption that degrades and returns would be split by a
      strictly sequential pass. Two clusters over the same span with the same
      bag of words are one caption read twice, so they are merged on multiset
      equality rather than on string equality, which is what catches
      word-order jitter between the two.

    Greedy clustering in time order is deliberate: a caption is assigned to the
    first cluster it resembles, which keeps a reading from shopping for the
    cluster that would make the reference shortest.
    """
    clusters: list[dict] = []
    for row in rows:
        text = _normalize(row["text"])
        if len(text) < min_length:
            continue
        for cluster in clusters:
            if _levenshtein(text, cluster["rep"]) <= max(3, distance * len(cluster["rep"])):
                cluster["members"].append((row["t"], text))
                cluster["rep"] = Counter(t for _, t in cluster["members"]).most_common(1)[0][0]
                break
        else:
            clusters.append({"rep": text, "members": [(row["t"], text)]})

    for cluster in clusters:
        times = sorted(t for t, _ in cluster["members"])
        cluster["start"] = times[0]
        cluster["end"] = times[-1]
        cluster["median"] = times[len(times) // 2]

    # Same words in the same window is one caption read twice; merge the
    # support so the merged caption clears the floor on its real frequency.
    merged: list[dict] = []
    for cluster in sorted(clusters, key=lambda c: c["median"]):
        words = Counter(cluster["rep"].split())
        for earlier in merged:
            if Counter(earlier["rep"].split()) == words and \
                    cluster["start"] <= earlier["end"] + 1.0:
                earlier["members"].extend(cluster["members"])
                earlier["rep"] = Counter(t for _, t in earlier["members"]).most_common(1)[0][0]
                earlier["end"] = max(earlier["end"], cluster["end"])
                earlier["median"] = sorted(t for t, _ in earlier["members"])[
                    len(earlier["members"]) // 2]
                break
        else:
            merged.append(cluster)

    return [c for c in merged if len(c["members"]) >= min_support]


def _reading_instability(captions: list[dict]) -> dict:
    """How often the OCR disagreed with itself on the same rendered caption.

    This is the reference-error estimate, and it is a *floor*, not a ceiling.
    A reading that is stably wrong — a font confusion that repeats on every
    frame, like reading ``OOI`` as ``OOl`` — produces zero disagreement and is
    invisible here, while a frame caught mid-render shows up as one disagreement
    against a dozen stable ones. Both kinds contaminate the reference, so the
    number is reported as "at least", and a WER scored against this reference
    stays an upper bound on recognition error no matter what it says.

    A dictionary check would catch the stable font confusions, but no lexicon
    is installable in this environment, and reaching for one would risk a
    spell-check that quietly no-ops and reports zero defects — which is worse
    than reporting a number with a stated limit, because zero looks like a
    measurement.
    """
    readings = disagreed = 0
    for cluster in captions:
        for _, text in cluster["members"]:
            readings += 1
            if text != cluster["rep"]:
                disagreed += 1
    return {"readings": readings, "disagreed": disagreed,
            "rate": round(disagreed / max(readings, 1), 4)}


# ── entry point ─────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--fps", type=float, default=2.0,
                        help="frames per second to OCR; captions hold for "
                             "longer than this, so 2 fps is the safe default")
    parser.add_argument("--band-top", type=float, default=0.8125,
                        help="caption band starts at this fraction of frame "
                             "height (0.8125 = bottom 19%%)")
    parser.add_argument("--band-bottom", type=float, default=0.9875)
    parser.add_argument("--min-support", type=int, default=3,
                        help="a caption needs this many OCR frames, which is "
                             "what drops transition frames caught mid-render")
    parser.add_argument("--min-length", type=int, default=4,
                        help="ignore readings shorter than this many "
                             "characters: one-word noise, not a caption")
    parser.add_argument("--rebuild", action="store_true",
                        help="re-run only the clustering against an existing "
                             "ocr_cache.json, without re-OCRing")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    cache = args.out / "ocr_cache.json"

    if args.rebuild:
        if not cache.is_file():
            raise SystemExit(f"--rebuild needs {cache}, which does not exist")
        frames = []
        top = band_h = None
    else:
        if not args.video.is_file():
            raise SystemExit(f"video not found: {args.video}")
        frames, top, band_h = _extract_band_frames(
            args.video, args.out / "frames", args.fps,
            args.band_top, args.band_bottom)
        rows = _ocr_frames(frames, args.fps)
        cache.write_text(json.dumps(rows, ensure_ascii=False) + "\n",
                         encoding="utf-8")
        print(f"[ocr] {len(rows)} frames cached to {cache}")
        for frame in frames:
            frame.unlink()

    rows = [r for r in json.loads(cache.read_text(encoding="utf-8")) if r["text"]]
    captions = _cluster_captions(rows, args.min_support, args.min_length, 0.22)
    captions.sort(key=lambda c: c["median"])

    with (args.out / "reference.jsonl").open("w", encoding="utf-8") as handle:
        for cap in captions:
            handle.write(json.dumps({
                "start": cap["start"], "end": cap["end"],
                "text": cap["rep"], "support": len(cap["members"]),
            }, ensure_ascii=False) + "\n")

    reference = " ".join(cap["rep"] for cap in captions)
    (args.out / "reference.txt").write_text(reference + "\n", encoding="utf-8")

    words = len(reference.split())
    bearing = sum(1 for r in rows if r["text"])
    instability = _reading_instability(captions)
    confs = [r["conf"] for r in rows if r["conf"] is not None]
    (args.out / "provenance.json").write_text(json.dumps({
        "video": str(args.video),
        "fps": args.fps,
        "band": {"top": args.band_top, "bottom": args.band_bottom},
        "band_pixels": {"top": top, "height": band_h},
        "ocr_engine": "rapidocr-onnxruntime",
        "frames_ocrd": len(rows),
        "frames_with_text": bearing,
        "mean_ocr_confidence": round(sum(confs) / max(len(confs), 1), 4),
        "captions": len(captions),
        "reference_words": words,
        # Reference-side error is the reason a WER against this reference is an
        # upper bound. Reported here so the number carries its own caveat.
        "reading_instability": instability,
        "reference_error_is_at_least": instability["rate"],
        "reference_error_is_not": "a ceiling; a stably misread caption "
                                  "disagrees with nothing and is invisible "
                                  "to this estimate",
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"[caption_ocr] {len(captions)} captions, {words} reference words "
          f"({bearing}/{len(rows)} frames carried text)")
    print(f"[caption_ocr] mean OCR confidence "
          f"{sum(confs) / max(len(confs), 1):.3f}; the OCR disagreed with "
          f"itself on {instability['disagreed']}/{instability['readings']} "
          f"readings of the same caption ({100 * instability['rate']:.1f}%)")
    print(f"[caption_ocr] reference error is AT LEAST "
          f"{100 * instability['rate']:.1f}% — a stable misread is invisible "
          f"to this estimate, so a WER against this reference is an upper "
          f"bound on recognition error")
    return 0


if __name__ == "__main__":
    sys.exit(main())
