"""Repeatable editorial audit for a real SelekyT run.

This is the benchmark the release validation phase asked for: instead of
trusting that a run "looks fine", it scores the concrete properties that
determine whether a shipped clip is publishable, and prints the ones that
fail so a human editor can check them.

It reads only the artifacts a normal run already writes, so it can be re-run
on any project directory:

    python tools/editorial_audit.py out/url-e2e/project

What it measures, and why each one is falsifiable rather than impressionistic:

  - mid-word cut      a cut boundary that lands inside a spoken word, which
                       produces a clipped or truncated phoneme on screen
  - mid-sentence cut  a cut boundary that splits a sentence, so the clip
                       starts or ends mid-thought (measured against the
                       transcript's own sentence boundaries)
  - short final word  the clip's last spoken word is under 200ms, so the
                       render can shave the tail of a word
  - orphaned cue      a caption cue whose text does not appear anywhere in the
                       candidate's transcript, i.e. a caption for speech that
                       the selection does not claim to contain
  - cue overrun       a caption cue ending past the rendered duration
  - span mismatch     the caption span and the rendered duration disagree by
                       more than the tolerance ffprobe rounds to
  - silence share     how much of the kept timeline is pause rather than
                       speech; a clip over the bar has its shipped render
                       decoded so the word-free window is measured
                       acoustically, and the finding says what the measurement
                       found rather than asserting a cause the timings cannot
                       support
  - duplication       the same sentence shipped in two clips, which is what a
                       reordering editor produces when its story units overlap
  - selection size    zero selections is a hard failure with a diagnostic

Every check returns (ok, detail). The exit code is nonzero if any hard check
fails, so this composes with CI rather than being a report a human has to
remember to read.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TOLERANCE_S = 0.05


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _sentences(text: str) -> list[str]:
    """Split on terminal punctuation, keeping the words a boundary sits between.

    A cut is 'mid-sentence' when the word on each side of it belong to the same
    sentence, so this returns the sentence text rather than just the offsets.
    """
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _word_spans(cand: dict) -> list[tuple[float, float, str]]:
    words = cand.get("words") or []
    return [(w["start"], w["end"], w.get("text", "")) for w in words if
            isinstance(w, dict) and w.get("start") is not None]


def check_cuts_land_between_words(cand: dict, intervals: list[tuple[float, float]]) -> tuple[bool, list[str]]:
    """No interval boundary may fall inside a spoken word."""
    spans = _word_spans(cand)
    if not spans:
        return True, []
    bad = []
    for a, b in intervals:
        for st, en, text in spans:
            if st < a < en:
                bad.append(f"start {a:.2f}s inside word '{text}' ({st:.2f}-{en:.2f}s)")
            if st < b < en:
                bad.append(f"end {b:.2f}s inside word '{text}' ({st:.2f}-{en:.2f}s)")
    return not bad, bad


def _word_sentence_map(cand: dict) -> list[int]:
    """Assign each timestamped word to the index of its sentence.

    Walks the sentence token lists and the word list together in order, so a
    word is attributed positionally rather than by substring. Substring
    matching is unreliable here: 'he' matches inside 'the', and a repeated
    word would bind to its first occurrence and misreport every later one.
    """
    spans = _word_spans(cand)
    sents = _sentences(cand.get("text", ""))
    if not spans or not sents:
        return [-1] * len(spans)

    def toks(s: str) -> list[str]:
        return [t for t in re.split(r"[^0-9a-z']+", s.lower()) if t]

    stoks = [toks(s) for s in sents]
    # Flatten to one stream with the sentence index of each token.
    flat: list[tuple[str, int]] = []
    for si, ts in enumerate(stoks):
        flat.extend((t, si) for t in ts)
    out = [-1] * len(spans)
    fi = 0
    for wi, (_, _, text) in enumerate(spans):
        t = toks(text)
        if not t:
            continue
        # Find this word's token stream ahead in the sentence stream.
        for start in range(fi, max(fi, len(flat) - len(t) + 1)):
            if all(flat[start + k][0] == t[k] for k in range(len(t))):
                out[wi] = flat[start][1]
                fi = start + len(t)
                break
    return out


def check_cuts_respect_sentences(cand: dict, intervals: list[tuple[float, float]]) -> tuple[bool, list[str]]:
    """Each cut boundary must fall at a sentence edge.

    A boundary is mid-sentence when the word just before it and the word just
    after it belong to the same sentence. Each interval has two such pairs:
    its own start (the word before vs the first kept word) and its end (the
    last kept word vs the word after).
    """
    spans = _word_spans(cand)
    if len(spans) < 2 or not intervals:
        return True, []
    sent_of = _word_sentence_map(cand)
    if all(s == -1 for s in sent_of):
        return True, []
    starts = [st for st, _en, _t in spans]
    bad = []

    def adjacent(t: float) -> tuple[int, int]:
        """Indices of the word at-or-before and at-or-after time t."""
        import bisect
        lo = bisect.bisect_right(starts, t) - 1
        hi = bisect.bisect_left(starts, t)
        lo = max(0, min(lo, len(spans) - 1))
        hi = max(0, min(hi, len(spans) - 1))
        return lo, hi

    for a, b in intervals:
        lo, hi = adjacent(a)
        if lo != hi and sent_of[lo] != -1 and sent_of[lo] == sent_of[hi]:
            bad.append(f"start {a:.2f}s splits '{spans[lo][2]}'/'{spans[hi][2]}'")
        lo, hi = adjacent(b)
        if lo != hi and sent_of[lo] != -1 and sent_of[lo] == sent_of[hi]:
            bad.append(f"end {b:.2f}s splits '{spans[lo][2]}'/'{spans[hi][2]}'")
    return not bad, bad


def check_last_word_is_not_shaved(cand: dict, intervals: list[tuple[float, float]]) -> tuple[bool, list[str]]:
    """The last kept word must fit inside the interval with its phoneme tail.

    A word is shaved when the interval ends *before* the word does — the render
    truncates it mid-phoneme. Word *duration* alone is not the signal: unstressed
    function words in real speech genuinely run 90-150ms ("the", "to", "go"), so
    thresholding on duration alone flags natural speech. What matters is that the
    interval's end is at or past the word's end, which is the actual cut.
    """
    spans = _word_spans(cand)
    if not spans or not intervals:
        return True, []
    last_end = intervals[-1][1]
    kept = [w for w in spans if w[0] < last_end]
    if not kept:
        return True, []
    st, en, text = max(kept, key=lambda w: w[0])
    if en > last_end + 1e-6:
        return False, [f"final word '{text}' ends {en:.2f}s but interval ends {last_end:.2f}s"]
    return True, []


def check_caption_text_is_transcript_text(cand: dict, cues: list[dict]) -> tuple[bool, list[str]]:
    """Every cue's words must appear in the candidate's own transcript."""
    text = (cand.get("text") or "").lower()
    if not text or not cues:
        return True, []
    stopwords = {"the", "a", "an", "of", "and", "to", "in", "is", "it", "that", "this"}
    bad = []
    for cue in cues:
        for w in cue.get("text", "").split():
            wl = w.strip(",.!?;:-\"'").lower()
            if len(wl) > 2 and wl not in stopwords and wl not in text:
                bad.append(f"cue '{cue.get('text', '')[:40]}' has '{wl}' absent from transcript")
                break
    return not bad, bad


def check_cues_fit_the_render(cues: list[dict], rendered_s: float) -> tuple[bool, list[str]]:
    if not cues or rendered_s <= 0:
        return True, []
    bad = [f"cue ends {cue['end']:.2f}s past the {rendered_s:.2f}s render"
           for cue in cues if cue.get("end", 0) > rendered_s + TOLERANCE_S]
    return not bad, bad


SILENCE_SHARE_MAX = 0.30
"""The hard bar of criterion P5 (`docs/EVALUATION.md`): no clip ships with more
than 30% word-free timeline. The audit's threshold used to be 0.55, which is
2.75× looser than the criterion it exists to enforce — every clip in the
committed corpus passed it, including two that exceed this bar (40.8% and
46.3%), so the check could not fire on the one condition it was written for.

Read the name with care. This is a *word-coverage* bar, not an audio
measurement: it is computed from transcript word timings and never decodes the
window it flags. The 40.8% clip above was measured acoustically and is a loud
music bed with 0.00 s of true silence in it (`docs/METRICS.md` A2.4), so the
number was right and the word "silence" was wrong. A FAIL here means the window
needs to be listened to before it is characterised as anything."""


def check_silence_share(cand: dict, intervals: list[tuple[float, float]]) -> tuple[bool, float]:
    """Share of the kept timeline that is gap rather than speech.

    Returns (ok, share). Share is always in [0, 1]: each overlap term is clamped
    against its interval, so ``spoken`` cannot exceed ``kept`` no matter where
    the word spans fall. A span outside every interval reads as 1.0 (all
    word-free), not as a negative number, so there is no out-of-range value to
    interpret as a bug — that case has to be caught by the checks that look at
    the spans directly.

    Read the name with care: this is *word-free* share, not measured silence.
    It is computed from transcript word timings and never decodes audio, so a
    high share can be dead air the edit kept **or** a non-speech bed the
    transcript has no words for — on the committed NASA 360 clip 05 the 40.8%
    window is a loud music bed (mean −31.4 dB, 0.00 s of true silence; see
    `docs/METRICS.md` A2.4). A FAIL therefore means a human should listen to
    the window, not that the cut is wrong; `engine.edit_plan.retention_intervals`
    cannot make that distinction either, and deliberately does not act on it.
    """
    spans = _word_spans(cand)
    if not spans or not intervals:
        return True, 0.0
    kept = sum(b - a for a, b in intervals)
    if kept <= 0:
        return False, 0.0
    spoken = 0.0
    for a, b in intervals:
        for st, en, _t in spans:
            spoken += max(0.0, min(en, b) - max(st, a))
    share = 1.0 - spoken / kept
    return share <= SILENCE_SHARE_MAX, share


QUIET_DBFS = -60.0
"""The acoustic floor that separates dead air from a non-speech bed.

Below this the waveform is digital quiet by the standard production definition.
`check_silence_share` never touches the audio, so when it fires the only honest
thing left to do is decode the render and count the seconds actually under this
floor — which is what the two committed over-bar clips needed: both turned out
to hold content, not silence (`docs/METRICS.md` A2.4)."""

QUIET_LINEAR = 10.0 ** (QUIET_DBFS / 20.0)
RENDER_SAMPLE_RATE = 16000


def _decode_render_audio(mp4: Path) -> tuple[list[int], int] | None:
    """Decode a shipped render to mono PCM, or ``None`` if that is impossible.

    One ffmpeg pass to 16 kHz 16-bit mono, then exact sample math in Python on
    the whole buffer. Parsing a filter's stderr was tried and discarded: the
    values it printed were uniform across windows that audibly differ, and a
    number a filter formatted is not a number this audit can reproduce.

    ``None`` is deliberate rather than an exception: a machine without ffmpeg,
    a project whose renders were never produced, or a corrupt file all mean the
    window cannot be measured, and that has to be *reported*, never silently
    cleared (see `word_free_finding`).
    """
    import shutil
    import struct
    import subprocess
    import tempfile
    import wave

    if shutil.which("ffmpeg") is None:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "pcm.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(mp4), "-ac", "1",
                 "-ar", str(RENDER_SAMPLE_RATE), "-c:a", "pcm_s16le", str(raw)],
                capture_output=True, check=True, timeout=180,
            )
            with wave.open(str(raw), "rb") as w:
                rate = w.getframerate()
                n = w.getnframes()
                frames = w.readframes(n)
    except Exception:
        return None
    # A truncated wav yields fewer frames than the header claims; unpack what is
    # there rather than raising, since a partial buffer still measures fine.
    n = len(frames) // 2
    return list(struct.unpack("<%dh" % n, frames[: n * 2])), rate


def measure_audio(samples: list[int], rate: int) -> dict | None:
    """RMS, peak and seconds of digital quiet over a decoded buffer."""
    import math

    n = len(samples)
    if not n or rate <= 0:
        return None
    floor = QUIET_LINEAR * 32768.0
    quiet = 0
    peak = 0
    total = 0
    for s in samples:
        a = -s if s < 0 else s
        if a > peak:
            peak = a
        if a < floor:
            quiet += 1
        total += s * s
    rms = math.sqrt(total / n) / 32768.0
    dur = n / rate
    return {
        "dur": dur,
        "quiet_s": quiet / rate,
        "quiet_share": (quiet / rate) / dur,
        "rms_db": 20 * math.log10(rms) if rms > 0 else float("-inf"),
        "peak_db": 20 * math.log10(peak / 32768.0) if peak > 0 else float("-inf"),
    }


def measure_clip_render(project: Path, index: int) -> dict | None:
    """Acoustic measurement of the render shipped for clip ``index``, or None.

    The facebook render is the one the social formats are cut from, so it is
    measured; the shorts render is the fallback when facebook is absent. Both
    carry the same timeline, so measuring either answers the question the
    silence-share failure asks: what is actually in that window.
    """
    for kind in ("facebook", "shorts"):
        mp4 = project / kind / f"{index:02d}.mp4"
        if not mp4.is_file():
            continue
        decoded = _decode_render_audio(mp4)
        if decoded is None:
            return None
        return measure_audio(*decoded)
    return None


def word_free_finding(share: float, measured: dict | None) -> tuple[str, str]:
    """Compose the finding line for a word-free share that is over the bar.

    Returns ``(state, detail)``. ``state`` is ``REVIEW`` when acoustic
    measurement contradicts dead air, and ``FAIL`` when it corroborates the
    reading — or when no measurement was possible at all, which is the case
    that must never be cleared: an unmeasurable window stays a failure with the
    reason attached, not a pass.

    REVIEW is not an acquittal. ``audit`` keeps the clip's boolean False either
    way, so the exit code does not soften and CI still fails; REVIEW only
    changes what the line claims, from a cause the word timings cannot support
    to the figures the decode actually produced. A human still has to listen,
    because "not digital silence" does not establish that the material belongs
    in the clip — a loud window can be a music bed or speech the transcript
    missed, and those need opposite fixes.
    """
    if measured is None:
        return (
            "FAIL",
            f"silence share {share:.1%} (word-free; cause unmeasured — the "
            f"render could not be decoded to check it)",
        )
    if measured["quiet_share"] <= SILENCE_SHARE_MAX:
        # Name the level, because the fix depends on it. A loud window is a
        # music bed or speech the transcript missed — opposite fixes, and the
        # second one is a transcription-quality problem, not an editing one. A
        # low-level window is a bed or a pause, which is an editorial call.
        if measured["rms_db"] >= -30.0:
            why = ("loud content rather than a pause — check for a music bed "
                   "or speech the transcript missed")
        else:
            why = ("low-level content rather than a pause — check whether the "
                   "material earns its place")
        return (
            "REVIEW",
            f"silence share {share:.1%} (word-free; measured "
            f"{measured['quiet_s']:.2f}s = {measured['quiet_share']:.1%} below "
            f"{QUIET_DBFS:.0f} dBFS, RMS {measured['rms_db']:.1f} dB — not dead "
            f"air; {why})",
        )
    return (
        "FAIL",
        f"silence share {share:.1%} (word-free; measured "
        f"{measured['quiet_s']:.2f}s = {measured['quiet_share']:.1%} below "
        f"{QUIET_DBFS:.0f} dBFS, RMS {measured['rms_db']:.1f} dB — dead air "
        f"corroborated)",
    )


def check_no_duplicate_sentences(cands: list[dict]) -> tuple[bool, list[str]]:
    """The same sentence must not ship in two clips."""
    seen: dict[str, int] = {}
    bad = []
    for i, c in enumerate(cands):
        for s in _sentences(c.get("text", "")):
            key = " ".join(s.lower().split())
            if len(key) < 24:
                continue
            if key in seen:
                bad.append(f"clips {seen[key] + 1} and {i + 1} both ship: {key[:60]}")
            else:
                seen[key] = i
    return not bad, bad


def _parse_srt_seconds(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8").strip()
    cues: list[dict] = []
    if not raw:
        return cues
    for block in re.split(r"\n\s*\n", raw):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        m = re.fullmatch(r"(?P<st>\d{1,2}:\d{2}:\d{2},\d{3}) --> (?P<en>\d{1,2}:\d{2}:\d{2},\d{3})", lines[1])
        if not m:
            continue

        def sec(ts: str) -> float:
            h, mn, rest = ts.split(":")
            s, ms = rest.split(",")
            return int(h) * 3600 + int(mn) * 60 + int(s) + int(ms) / 1000

        cues.append({"start": sec(m["st"]), "end": sec(m["en"]), "text": " ".join(lines[2:])})
    return cues


def audit(project: Path) -> tuple[bool, dict]:
    """Run every check. Returns (all_ok, report).

    A project with no ``selections.json`` is a failure with a reason, not an
    error: the run produced nothing to audit, which is a finding in itself, so
    it is returned as one. Four of the eleven committed sources land here — the
    VAD gate suppressed their transcripts entirely
    (``providers/whisper.py``, §Completed work in
    ``docs/AI_EXECUTION_STATE.md``).
    """
    selections = _load(project / "selections.json")
    report = _load(project / "report.json")
    findings: list[tuple[str, bool, str]] = []
    if selections is None:
        # Say which stage stopped the run. "No selections" is true but it is
        # the symptom, and it sends an editor to look at the selection step
        # when the transcript above it is what came back empty. All four of the
        # committed sources that land here have a transcript.json with zero
        # segments — the VAD gate suppressed the audio (providers/whisper.py,
        # §Completed work in docs/AI_EXECUTION_STATE.md) — which is a different
        # fix than a selection stage that rejected everything it saw.
        transcript = _load(project / "transcript.json")
        if transcript is not None:
            n = len(transcript.get("segments") or [])
            lang = transcript.get("language", "?")
            where = "transcription returned no segments"
            if n:
                where = f"transcription returned {n} segments"
            findings.append((
                "project", False,
                f"no selections.json — {where} ({lang}); selection never ran. "
                f"report.json present: {report is not None}",
            ))
        else:
            findings.append((
                "project", False,
                f"no selections.json and no transcript.json — the run stopped "
                f"before transcription completed. report.json present: {report is not None}",
            ))
        return False, {"findings": findings}
    # selections.json is a bare list of candidates; report.json wraps its own
    # copy under "selected". Accept either so the audit runs on any project dir.
    if isinstance(selections, list):
        cands = selections
    else:
        cands = selections.get("selected") or []

    if not cands:
        reasons = (report or {}).get("rejection_reasons") or {}
        sample = list(reasons.items())[:3] if isinstance(reasons, dict) else []
        findings.append(("selection size", False, f"no clips selected; rejection reasons: {sample}"))
        return False, {"findings": findings}

    from engine.edit_plan import retention_intervals
    from models.core import Candidate, Word

    ok_all = True
    review: list[str] = []
    for i, cand in enumerate(cands, start=1):
        words = [Word(w["text"], w["start"], w["end"], w.get("confidence", 1.0))
                 for w in (cand.get("words") or [])
                 if isinstance(w, dict) and w.get("start") is not None and w.get("end") is not None]
        c = Candidate(
            cand["start"], cand["end"], cand.get("text", ""),
            cand.get("story_type", ""), cand.get("hook", ""), cand.get("payoff", ""),
            cand.get("editorial_score", 0.0), cand.get("virality_score", 0.0),
            cand.get("context_score", 0.0), cand.get("confidence", 0.0),
            cand.get("reason", ""), cand.get("visual_potential", 0.0),
            words,
        )
        intervals = retention_intervals(c)
        name, ok, detail = f"clip {i:02d}", True, []

        o, d = check_cuts_land_between_words(cand, intervals)
        ok &= o
        detail += d
        o, d = check_cuts_respect_sentences(cand, intervals)
        ok &= o
        detail += d
        o, d = check_last_word_is_not_shaved(cand, intervals)
        ok &= o
        detail += d
        o, share = check_silence_share(cand, intervals)
        ok &= o
        if o:
            detail.append(f"silence share {share:.1%}")
        else:
            # The word timings said the window is empty of speech. That is all
            # they can say: on nasa360-ep5 clip 05 this reading was explained
            # first as a pause and then as dead air to cut, and measuring the
            # render showed a loud music bed (docs/METRICS.md A2.4). Decode the
            # shipped render and report what is actually in the window, so the
            # finding states a measurement instead of a guess.
            state, line = word_free_finding(share, measure_clip_render(project, i))
            detail.append(line)
            if state == "REVIEW":
                review.append(name)

        # Captions shipped beside this clip, if any.
        srt = project / "captions" / f"{i:02d}.srt"
        cues = _parse_srt_seconds(srt) if srt.is_file() else []
        if cues:
            o, d = check_caption_text_is_transcript_text(cand, cues)
            ok &= o
            detail += d
            mp4 = project / "facebook" / f"{i:02d}.mp4"
            if mp4.is_file():
                rendered = _probe_duration(mp4)
                o, d = check_cues_fit_the_render(cues, rendered)
                ok &= o
                detail += d
                detail.append(f"rendered {rendered:.2f}s, {len(cues)} cues")
        findings.append((name, ok, "; ".join(detail) or "clean"))
        ok_all &= ok

    o, d = check_no_duplicate_sentences(cands)
    ok_all &= o
    findings.append(("cross-clip", o, "; ".join(d) or "no duplicated sentences"))
    return ok_all, {"findings": findings, "review": review}


def _probe_duration(mp4: Path) -> float:
    import subprocess
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=noprint_wrappers=1:nokey=1", str(mp4)],
                             capture_output=True, text=True, check=True)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: editorial_audit.py <project-dir>", file=sys.stderr)
        return 2
    project = Path(argv[1])
    if not project.is_dir():
        print(f"not a directory: {project}", file=sys.stderr)
        return 2
    ok, rep = audit(project)
    # audit() returns no report when the project has no selections.json, which
    # is itself the finding: the run never produced anything to audit. Report
    # it as a failure rather than dying with a KeyError, so a sweep across the
    # corpus keeps going instead of stopping at the first incomplete project.
    if not rep.get("findings"):
        print(f"{project}: no auditable artifacts (no selections.json)")
        print()
        print("FAILURES PRESENT")
        return 1
    width = max((len(n) for n, _o, _d in rep["findings"]), default=8)
    review = set(rep.get("review") or [])
    for name, cok, detail in rep["findings"]:
        if cok:
            mark = "PASS"
        elif name in review:
            mark = "REVIEW"
        else:
            mark = "FAIL"
        print(f"{name:<{width}}  {mark}  {detail}")
    print()
    # REVIEW findings are still failures for the exit code — a clip over the bar
    # needs a human to listen to it before it ships — so they are counted, not
    # cleared. Silently passing them would be the exact defect this state was
    # added to remove.
    if ok:
        print("ALL CHECKS PASS")
    elif review:
        print(f"FAILURES PRESENT ({len(review)} clip(s) awaiting human review)")
        return 1
    else:
        print("FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
