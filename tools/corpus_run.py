"""Run the evaluation corpus: probe a source, run the pipeline, record the outcome.

This is the tool that turns docs/EVALUATION.md from a protocol into results. It
writes one JSON record per source to corpus/results.jsonl — including failures.

A source that crashes, transcribes to nothing, or is rejected by the context
guard is a *result*, not an interruption: the record is written with the error
and the exit is non-zero only if the harness itself failed, never because a
source was difficult. The point of a corpus is to find where the pipeline
breaks, so hiding the breakages would defeat it.

Usage:
    .venv/bin/python tools/corpus_run.py corpus/manifest.json --out corpus/results.jsonl
    .venv/bin/python tools/corpus_run.py corpus/manifest.json --only telugu-launch
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The probe fields that decide which axis a source exercises. r_frame_rate is
# per-stream and must be taken from the video stream (see providers/ffmpeg.py).
_PROBE = (
    "format=duration:stream=codec_name,width,height,r_frame_rate,"
    "sample_aspect_ratio,bit_rate,channels"
)


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def probe(path: Path) -> dict:
    """ffprobe the source. Returns {} if ffprobe cannot read it at all.

    A file ffprobe rejects is recorded, not silently skipped, so the manifest
    can carry a URL that turned out to be dead.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             _PROBE, "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=300,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return {"ffprobe_error": (exc.stderr.strip()[:400] if isinstance(exc, subprocess.CalledProcessError) else repr(exc)[:400])}
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    rec = {"duration": float(fmt.get("duration") or 0.0)}
    if streams:
        s = streams[0]
        rec.update({
            "codec": s.get("codec_name"),
            "width": s.get("width"), "height": s.get("height"),
            "fps": s.get("r_frame_rate"), "sar": s.get("sample_aspect_ratio"),
            "video_bit_rate": s.get("bit_rate"), "channels": s.get("channels"),
        })
    return rec


def _audio_seconds(path: Path) -> float | None:
    """Decodable audio length, or None when the container is not readable.

    A structurally damaged container can still play; this is the same decode
    the pipeline performs, so a source whose audio cannot be decoded is a
    source the pipeline cannot run on, and that needs to be distinguishable
    from 'decodes but contains no speech'.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True, timeout=300,
        )
        return float(out.stdout.strip() or 0.0)
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


def fetch(entry: dict, media_dir: Path) -> tuple[Path | None, str | None]:
    """Fetch a source once into media_dir. Returns (path, error)."""
    url = entry.get("url")
    if not url:
        return None, "manifest entry has no url"
    dest = media_dir / f"{entry['id']}{Path(url).suffix or '.mp4'}"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest, None
    r = subprocess.run(
        ["curl", "-sL", "--retry", "2", "-A", "selektyt-corpus/1.0",
         "-o", str(dest), url],
        capture_output=True, text=True, timeout=1800,
    )
    if r.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        return None, f"fetch failed: {r.stderr.strip()[:300] or 'no bytes'}"
    return dest, None


def _transcript_summary(project: Path) -> dict:
    """What the run actually produced, from the pipeline's own artifacts.

    The transcript is written to transcript.json and the selection record to
    selections.json; pipeline_state.json carries the caches, not the content.
    A run that failed before selection has a transcript and no selections, and
    a run that failed before transcription has neither — that ordering is what
    makes a NO_SPEECH result distinguishable from a HARNESS_ERROR.
    """
    out: dict = {}
    tp = project / "transcript.json"
    if tp.is_file():
        try:
            t = json.loads(tp.read_text(encoding="utf-8"))
            segs = t.get("segments") or []
            text = " ".join((s.get("text") or "").strip() for s in segs)
            out.update({
                "language": t.get("language"),
                "asr_model": (t.get("asr") or {}).get("used_model"),
                "segments": len(segs),
                "transcript_chars": len(text),
                "transcript_head": text[:120],
            })
        except (OSError, json.JSONDecodeError):
            out["transcript_read_error"] = True
    sp = project / "selections.json"
    if sp.is_file():
        try:
            s = json.loads(sp.read_text(encoding="utf-8"))
            # The pipeline writes a bare list of selected candidates; a dict
            # shape would be the other reasonable thing for a caller to hand
            # over, so both are accepted rather than assumed.
            if isinstance(s, list):
                sel, rej = s, []
            else:
                sel, rej = s.get("selected") or [], s.get("rejected") or []
            out.update({
                "clips": len(sel),
                "rejections": len(rej),
                "rejection_reasons": sorted({(r.get("reason") or "?")[:60] for r in rej}),
            })
        except (OSError, json.JSONDecodeError):
            out["selections_read_error"] = True
    return out


def run_one(entry: dict, workdir: Path, keep_media: bool) -> dict:
    """Probe, fetch and run a single corpus entry. Never raises."""
    rec: dict = {
        "id": entry["id"], "axis": entry.get("axis"),
        "genre": entry.get("genre"), "language": entry.get("language"),
        "rights": entry.get("rights"), "url": entry.get("url"),
        "started_utc": _utcnow(),
    }
    t0 = time.monotonic()
    try:
        media_dir = workdir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        media, ferr = fetch(entry, media_dir)
        if media is None:
            rec.update(status="FETCH_FAILED", error=ferr, elapsed_s=round(time.monotonic() - t0, 1))
            return rec
        rec["media"] = str(media)
        rec.update(probe(media))

        project = workdir / f"{entry['id']}_run"
        if project.is_dir():
            shutil.rmtree(project)

        cli = sys.executable
        cmd = [cli, "-m", "selekyt.cli", str(media), "--out", str(project),
               "--limit", str(entry.get("limit", 4)), "--model", entry.get("model", "small")]
        if entry.get("language"):
            cmd += ["--language", entry["language"]]
        if entry.get("audio_analysis"):
            cmd += ["--audio-analysis"]

        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=str(ROOT), timeout=entry.get("timeout_s", 3600))
        rec["exit_code"] = r.returncode
        rec["elapsed_s"] = round(time.monotonic() - t0, 1)
        rec.update(_transcript_summary(project))

        tail = (r.stderr or r.stdout or "").strip().splitlines()[-8:]
        rec["log_tail"] = "\n".join(tail)

        if r.returncode == 0:
            rec["status"] = "OK"
        else:
            rec["status"] = "PIPELINE_FAILED"
            # The pipeline raises named RuntimeErrors for outcomes that are
            # editorial rather than crashes — a speechless source and a
            # selection the gate rejected entirely. Distinguishing them from
            # a stack trace is what makes the corpus results readable: all
            # three look like "exit 1" otherwise.
            for line in (r.stderr or "").splitlines():
                if "no audible spoken content" in line:
                    rec["status"] = "NO_SPEECH_DETECTED"
                    break
                if "passed the editorial/context gate" in line:
                    rec["status"] = "NO_STORIES_PASSED"
                    break
        if not keep_media:
            shutil.rmtree(media_dir / media.name, ignore_errors=True)
    except subprocess.TimeoutExpired:
        rec["status"] = "TIMEOUT"
        rec["elapsed_s"] = round(time.monotonic() - t0, 1)
    except Exception:  # the harness itself broke; that must be visible
        rec["status"] = "HARNESS_ERROR"
        rec["error"] = traceback.format_exc(limit=2)
        rec["elapsed_s"] = round(time.monotonic() - t0, 1)
    rec["finished_utc"] = _utcnow()
    return rec


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("manifest", help="JSON manifest of corpus sources")
    p.add_argument("--workdir", default="corpus", help="where media and projects land")
    p.add_argument("--out", default=None, help="results jsonl (default: <workdir>/results.jsonl)")
    p.add_argument("--only", default=None, help="comma-separated entry ids to run")
    p.add_argument("--keep-media", action="store_true", help="keep downloaded sources")
    args = p.parse_args(argv)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    entries = manifest["sources"]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        entries = [e for e in entries if e["id"] in wanted]
        missing = wanted - {e["id"] for e in entries}
        if missing:
            print(f"unknown ids in --only: {sorted(missing)}", file=sys.stderr)
            return 2

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else workdir / "results.jsonl"

    # Append, so a corpus grows across sessions instead of being re-run whole.
    done: dict[str, dict] = {}
    if out_path.is_file():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and r.get("id"):
                done[r["id"]] = r

    n_new = 0
    for entry in entries:
        prior = done.get(entry["id"])
        if prior and prior.get("status") == "OK":
            print(f"[skip] {entry['id']} already OK")
            continue
        print(f"[run ] {entry['id']} ({entry.get('axis')}) ...", flush=True)
        rec = run_one(entry, workdir, args.keep_media)
        done[entry["id"]] = rec
        n_new += 1
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{rec['status']}] {entry['id']} in {rec.get('elapsed_s')}s")

    # Summary to stdout; the jsonl is the record of truth.
    ok = sum(1 for r in done.values() if r.get("status") == "OK")
    fail = sum(1 for r in done.values() if r.get("status") not in ("OK",))
    print(f"\n{ok} OK / {fail} not-OK / {len(done)} total ({n_new} run this pass)")
    for r in sorted(done.values(), key=lambda r: r.get("id", "")):
        seg = r.get("segments", "—")
        print(f"  {r.get('id', '?'):<28} {r.get('status', '?'):<18} "
              f"segs={seg} clips={r.get('clips', '—')} {r.get('language', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
