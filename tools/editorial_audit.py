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
  - mid-sentence cut  a cut boundary that drops a word from inside a sentence,
                       so the clip starts or ends mid-thought or carries a
                       hole in the speech (measured against the transcript's
                       own utterance spans, not against the candidate's text:
                       the text joins under-punctuated segments and reads
                       clean cuts as splits)
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
  - orphaned edge     a clip whose first or last kept word sits inside a
                      sentence — measured against the transcript words the
                      boundary *dropped*, which the selection's own word list
                      cannot show because it holds only the words the clip kept

Every check returns (ok, detail). The exit code is nonzero if any hard check
fails, so this composes with CI rather than being a report a human has to
remember to read. A check that cannot decide — a transcript too unpunctuated to
mark where its sentences end — is reported as REVIEW, and REVIEW still fails the
exit code: a clip the tool could not measure is not a clip it has passed.

Sweeping the corpus, and comparing two sweeps, answers the question the audit
by itself cannot: whether a change to SeleKyT made its output better or worse.
``--corpus`` audits every committed run and ``--compare`` diffs the result
against a sweep recorded with ``--baseline``, classifying each changed clip as
an improvement or a regression rather than leaving a reader to eyeball two
reports.
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


def _word_sentence_map(cand: dict,
                       utterances: list[tuple[float, float]] | None = None) -> list[int]:
    """Assign each timestamped word to the index of its sentence.

    The sentence structure comes from ``utterances`` — the transcript's own
    segment spans, sorted — when the caller has them. Each word is placed in
    the utterance containing its start, which is a lookup by time rather than a
    match by text, so it cannot desync. On the committed corpus all 1759 words
    of all seven selected projects land inside exactly one segment, so this
    join is exact rather than approximate.

    The utterances are what the selection's words were cut from, so they are
    the structure the check is asking about. A candidate's own ``text`` is not:
    it is ``" ".join(segment.text)`` (engine/editorial.py) and a transcript
    segment regularly carries no terminal punctuation — pentagon-esper-briefing
    clip 02 joins 'We stay in continuous contact with them' to 'What was used
    to destroy the compound' with nothing between them but a space, so a
    punctuation split reads two utterances six seconds apart as one sentence
    and a clean cut between them as a mid-sentence split. Deriving sentences
    from that text is what made this audit report failures the clips do not
    have.

    Without utterances the caller gets the punctuation split of ``text`` as a
    fallback. That is imprecise in exactly the way above and is kept only for
    direct callers of the check with no transcript in hand; ``audit`` always
    passes the real one.
    """
    spans = _word_spans(cand)
    if not spans:
        return []

    if utterances:
        import bisect
        starts = [u[0] for u in utterances]
        out = []
        for st, _en, _t in spans:
            i = bisect.bisect_right(starts, st) - 1
            out.append(i if i >= 0 else -1)
        return out

    sents = _sentences(cand.get("text", ""))
    if not sents:
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


def check_cuts_respect_sentences(cand: dict,
                                 intervals: list[tuple[float, float]],
                                 utterances: list[tuple[float, float]] | None = None,
                                 ) -> tuple[bool, list[str]]:
    """No cut may take a word out of the middle of a sentence.

    A boundary damages a sentence when the sentence *loses a word* to it: the
    word before it is dropped, the word after it is dropped, or a word between
    the two is dropped. That is the condition that leaves a hole in the speech
    the viewer hears, and it is what the finding reports.

    A boundary that only shortens the gap between two kept words is not that.
    It tightens a pause and every word of the sentence is still on screen, so
    the sentence is intact. This is a real distinction and not a loosening:
    every mid-sentence finding this check produced on the committed corpus was
    that case — the four it flagged (pentagon-esper-briefing clip 04, emwcon
    clip 01, ooi-vertical clip 01, royal-society-cloke clip 01) each removed
    0.5-1.4 s of gap between two transcript-adjacent words and lost no word at
    all, and each was reported twice, once per boundary of the removed span.
    The check now stays quiet on them and still fires on the loss, which the
    corpus has none of — so the committed trees no longer cover this check, and
    the tests pin the loss directly instead.

    ``utterances`` are the transcript's segment spans and are the sentence
    structure; see ``_word_sentence_map``. Without them the check falls back to
    splitting the candidate's own text, which merges under-punctuated
    utterances and is the source of the false findings above.

    One limit to state plainly: the utterances only ever change a verdict the
    word list can show both sides of. A selection's ``words`` are the words it
    kept — 1754 of 1755 on the committed corpus lie inside a retention interval
    — so an excised utterance leaves no trace in the list and the two words the
    boundary separates are kept and adjacent, which is the pause case. On that
    data the source makes no observable difference to any verdict ``audit``
    can reach, and the unit tests are what pin it: they build candidates whose
    word list does carry the loss. The corpus and end-to-end legs cannot, and
    a clip that ends mid-utterance is invisible to both, because its word list
    ends at the last kept word. That is a real gap in what this check covers
    and it is not closed here.
    """
    spans = _word_spans(cand)
    if len(spans) < 2 or not intervals:
        return True, []
    sent_of = _word_sentence_map(cand, utterances)
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

    def kept(i: int) -> bool:
        """The word is wholly inside a kept interval."""
        st, en, _t = spans[i]
        return any(a <= st and en <= b for a, b in intervals)

    for a, b in intervals:
        for t, edge in ((a, "start"), (b, "end")):
            lo, hi = adjacent(t)
            # Different sentences is a clean cut between utterances; -1 is a
            # word the utterances do not cover, which is not a sentence split.
            if lo == hi or sent_of[lo] == -1 or sent_of[lo] != sent_of[hi]:
                continue
            # Nothing between lo and hi is lost: lo and hi are kept and every
            # word between them is. An empty between-range is a pause.
            if kept(lo) and kept(hi) and all(kept(k) for k in range(lo + 1, hi)):
                continue
            bad.append(f"{edge} {t:.2f}s splits '{spans[lo][2]}'/'{spans[hi][2]}'")
    return not bad, bad


def _terminal_punct(text: str) -> bool:
    """The word/string closes a sentence in a script the transcript punctuates.

    Latin, Devanagari and CJK sentence terminators are all covered so the check
    is not English-only on a corpus that already carries Hungarian and Telugu.
    """
    return bool(re.search(r"[.!?։।。？][\"')\]]*\s*$", text.strip()))


def check_edges_respect_sentences(
    cand: dict,
    utterances: list[tuple[float, float]],
    utterance_words: list[list[tuple[float, float, str]]],
) -> tuple[bool, bool, list[str]]:
    """A clip's first and last kept word must not sit inside a sentence.

    Returns ``(hard_ok, needs_review, detail)``. A mid-sentence edge is a hard
    failure — it is a fact about the shipped artifact. An edge the transcript
    cannot decide is not: it is unmeasured, and it goes to human review like the
    silence check's ambiguous readings do, so the exit code still fails on it
    instead of recording a pass nothing supports.

    ``check_cuts_respect_sentences`` asks whether a boundary *loses a word the
    clip kept*: it compares the words either side of a cut that both survive,
    so a clip that simply begins partway into an utterance leaves it nothing to
    compare — the boundary has no word on its other side. That is the gap its
    docstring states plainly, and this check closes it by looking outward
    instead of inward: at the transcript words the clip *dropped*.

    For each edge it finds the utterance containing the clip's first or last
    kept word, then asks whether the speech on the far side of the cut finishes
    the sentence. An opening is clean when the speech before it ends a
    sentence; a closing is clean when the clip's own last word does. The
    failure is an orphan — pentagon-esper-briefing clip 04 opens on 'Syria.'
    after dropping 'You mentioned the oil fields and securing the oil fields in
    the east of', so the shipped clip begins with the last word of a sentence
    it cut in half. royal-society-cloke clip 01 fails the other edge, ending on
    'Somebody' and dropping 'says, you know, actually tomorrow'. Both are
    defects no other check in this file reports.

    This check looks past the clip's own bounds, so it sees the close edge that
    a clip spanning into the following utterance moves out of reach: the words
    that continue the sentence live in the next segment, not the one the clip
    opened in.

    ``utterances`` and ``utterance_words`` are parallel and share an index;
    ``audit`` builds them together. Words are joined by time, which is exact on
    this corpus.

    Honest limits. The decision needs sentence punctuation, and 34-47% of the
    segments on the English sources of this corpus carry none at all — the
    transcript does not mark where its own sentences end. When the utterance an
    edge sits in has no terminal punctuation anywhere, the question has no
    answer on that transcript, because *every* edge in it is consistent with
    mid-sentence and none is distinguishable from a clean one. That edge is
    reported as undecidable rather than guessed at, and it is counted so a run
    whose transcript is unpunctuated reads as unmeasured rather than as
    passing. The proportion is a property of the ASR output, not of the clips:
    on this corpus it follows the language model that transcribed the source,
    and a re-transcription is what would change it.
    """
    spans = _word_spans(cand)
    if not spans or not utterances or len(utterance_words) != len(utterances):
        return True, False, []

    import bisect
    starts = [u[0] for u in utterances]

    def utterance_of(t: float) -> int:
        i = bisect.bisect_right(starts, t) - 1
        return i if 0 <= i < len(utterances) else -1

    def sentence_ends_before(idx: int, t: float) -> bool | None:
        """Does speech before ``t`` in utterance ``idx`` close a sentence?

        ``None`` means the utterance carries no terminal punctuation at all, so
        the question has no answer on this transcript.
        """
        ws = utterance_words[idx]
        if not any(_terminal_punct(w[2]) for w in ws):
            return None
        dropped = " ".join(w[2] for w in ws if w[1] <= t + 0.05)
        if not dropped.strip():
            return True  # the clip opens the utterance itself
        return _terminal_punct(dropped)

    bad: list[str] = []
    notes: list[str] = []
    undecidable = 0

    first = spans[0]
    i = utterance_of(first[0])
    if i >= 0:
        verdict = sentence_ends_before(i, first[0])
        if verdict is None:
            undecidable += 1
        elif not verdict:
            pre = " ".join(w[2] for w in utterance_words[i] if w[1] <= first[0] + 0.05).strip()
            bad.append(
                f"opens mid-sentence: the clip's first word '{first[2]}' follows "
                f"'{pre[-72:]}' with no sentence break between them"
            )

    last = spans[-1]
    j = utterance_of(last[0])
    if j >= 0:
        ws = utterance_words[j]
        after = [w for w in ws if w[0] >= last[1] - 0.05]
        if after:
            if not any(_terminal_punct(w[2]) for w in ws):
                undecidable += 1
            elif not _terminal_punct(last[2]):
                post = " ".join(w[2] for w in after).strip()
                bad.append(
                    f"closes mid-sentence: the clip's last word '{last[2]}' is "
                    f"followed by '{post[:72:]}' with no sentence break"
                )

    # An undecidable edge is unmeasured, not clean: it goes to review so the
    # exit code still fails on it, and the count is reported so the proportion
    # is visible as a property of the transcript rather than of the clips. The
    # note is kept out of ``bad`` — a hard failure says the shipped clip is
    # wrong, and this one says the transcript cannot say.
    review = bool(undecidable) and not bad
    if undecidable:
        edges = "edge" if undecidable == 1 else "edges"
        notes.append(
            f"{undecidable} {edges} undecidable — the transcript marks no "
            f"sentence end in the utterance(s) involved"
        )
    return not bad, review, bad + notes


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

    # The transcript's segments are the sentence structure the mid-sentence
    # check needs. Each selection's words were cut from them, and joining a
    # word to its segment by time is exact on the committed corpus. Without
    # these the check would fall back to splitting the candidate's own text,
    # which merges under-punctuated utterances and reports cuts that are clean.
    transcript = _load(project / "transcript.json")
    utterances: list[tuple[float, float]] = []
    # Words of each utterance, parallel to ``utterances`` by index. The edge
    # check looks at the speech a boundary cut away, which the selection's own
    # word list cannot show it: those words are the ones the clip kept.
    utterance_words: list[list[tuple[float, float, str]]] = []
    if transcript is not None:
        for seg in transcript.get("segments") or []:
            try:
                utterances.append((float(seg["start"]), float(seg["end"])))
            except (KeyError, TypeError, ValueError):
                continue
            ws = []
            for w in seg.get("words") or []:
                if not isinstance(w, dict):
                    continue
                try:
                    ws.append((float(w["start"]), float(w["end"]), w.get("text", "")))
                except (KeyError, TypeError, ValueError):
                    continue
            utterance_words.append(ws)
        utterances.sort()
        # Keep the parallel lists aligned through the same sort, by the
        # utterance start each pair shares.
        order = sorted(range(len(utterances)), key=lambda i: utterances[i])
        utterances = [utterances[i] for i in order]
        utterance_words = [utterance_words[i] for i in order]

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
        o, d = check_cuts_respect_sentences(cand, intervals, utterances)
        ok &= o
        detail += d
        o, needs_review, d = check_edges_respect_sentences(cand, utterances, utterance_words)
        ok &= o
        if needs_review:
            review.append(name)
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


def _state_of(name: str, cok: bool, review: set[str]) -> str:
    if not cok and name not in review:
        return "fail"
    if name in review:
        return "review"
    return "clean"


def sweep(root: Path) -> dict:
    """Audit every ``*_run/project`` under ``root``, as one comparable record.

    A single project's audit says whether that run's clips are publishable. The
    question a change to SeleKyT actually asks is whether the corpus got better
    or worse, which needs every run scored the same way and the states laid out
    side by side. REVIEW is kept distinct from FAIL because a clip that became
    undecidable is not a clip that became wrong — the two call for different
    responses, and folding them together would hide exactly the regression a
    sweep is for.
    """
    runs = sorted(p for p in root.glob("*_run/project") if p.is_dir())
    record = {"runs": []}
    clean_clips = 0
    for project in runs:
        _ok, rep = audit(project)
        review = set(rep.get("review") or [])
        clips = []
        for name, cok, detail in rep["findings"]:
            state = _state_of(name, cok, review)
            if state == "clean":
                # A clean clip carries no information a diff can use, and an
                # absent entry reads as clean to the comparison — so it is
                # counted and then dropped.
                if name != "cross-clip":
                    clean_clips += 1
                continue
            clips.append({"clip": name, "state": state,
                          # The detail is truncated so a sweep stays small, but
                          # the reason prefix is what a change is classified on.
                          "reason": detail.split(";")[0].strip()[:160]})
        record["runs"].append({"source": project.parent.name, "clips": clips})
    record["totals"] = _sweep_totals(record["runs"], clean_clips)
    return record


def _sweep_totals(runs: list[dict], clean_clips: int = 0) -> dict:
    totals = {"runs": len(runs), "clean": clean_clips, "review": 0, "fail": 0}
    for run in runs:
        for clip in run["clips"]:
            if clip["clip"] == "cross-clip":
                continue
            totals[clip["state"]] += 1
    return totals


def compare_sweeps(before: dict, after: dict) -> list[dict]:
    """Classify every clip whose state or reason moved between two sweeps.

    Direction matters. A clip that went from failing to clean is an improvement;
    one that went the other way is a regression; one that failed both ways with
    a different reason changed shape and a reader has to look at it, because a
    check that swapped one defect for another is not a wash. Sources present in
    only one sweep are reported too — a corpus leg that quietly dropped a run
    would otherwise read as a score improvement it did not earn.
    """
    index = {run["source"]: run for run in before["runs"]}
    changes = []
    rank = {"clean": 0, "review": 1, "fail": 2}
    for run in after["runs"]:
        source = run["source"]
        prior = index.pop(source, None)
        if prior is None:
            changes.append({"source": source, "clip": "*",
                            "kind": "added", "before": None, "after": "present",
                            "reason": "the run is not in the baseline sweep"})
            continue
        seen = set()
        for clip in run["clips"]:
            name, state, reason = clip["clip"], clip["state"], clip["reason"]
            seen.add(name)
            old = next((c for c in prior["clips"] if c["clip"] == name), None)
            before_state = old["state"] if old else "clean"
            before_reason = old["reason"] if old else ""
            if before_state == state and before_reason == reason:
                continue
            if before_state == state:
                kind = "changed"
            elif rank[state] < rank[before_state]:
                kind = "improvement"
            else:
                kind = "regression"
            changes.append({"source": source, "clip": name, "kind": kind,
                            "before": before_state, "after": state,
                            "reason": reason})
        for old in prior["clips"]:
            if old["clip"] not in seen:
                changes.append({"source": source, "clip": old["clip"],
                                "kind": "improvement", "before": old["state"],
                                "after": "clean",
                                "reason": "the clip is clean in the new sweep"})
    for source in index:
        changes.append({"source": source, "clip": "*", "kind": "removed",
                        "before": "present", "after": None,
                        "reason": "the run is gone from the new sweep"})
    changes.sort(key=lambda c: ({"regression": 0, "removed": 1, "changed": 2,
                                 "added": 3, "improvement": 4}[c["kind"]],
                                c["source"], c["clip"]))
    return changes


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if args is None:
        return 2

    if args.corpus:
        record = sweep(Path(args.path))
        if args.compare:
            try:
                before = json.loads(Path(args.compare).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"cannot read the baseline sweep {args.compare}: {exc}",
                      file=sys.stderr)
                return 2
            changes = compare_sweeps(before, record)
            for c in changes:
                print(f"{c['kind']:<11} {c['source']} {c['clip']}  "
                      f"{c['before']} -> {c['after']}  {c['reason']}")
            print()
            kinds = {}
            for c in changes:
                kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
            if kinds:
                print("CHANGES: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
            else:
                print("no change against the baseline sweep")
            # A regression or a removed run is a real signal; an improvement is
            # not. ``changed`` is reported as a failure because a defect that
            # moved is still a defect, and a sweep that printed it and exited 0
            # would train its readers to ignore it.
            signal = sum(kinds.get(k, 0) for k in ("regression", "removed", "changed"))
            return 1 if signal else 0

        t = record["totals"]
        print(f"{t['runs']} runs, {t['clean']} clean clips, "
              f"{t['review']} awaiting review, {t['fail']} failed")
        for run in record["runs"]:
            for clip in run["clips"]:
                print(f"{run['source']:<28} {clip['state']:<7} {clip['clip']}  "
                      f"{clip['reason']}")
        if args.baseline:
            Path(args.baseline).write_text(json.dumps(record, indent=1) + "\n",
                                           encoding="utf-8")
            print(f"\nrecorded as the baseline sweep: {args.baseline}")
        return 1 if (t["fail"] or t["review"]) else 0

    project = Path(args.path)
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
    failed = [n for n, cok, _d in rep["findings"] if not cok and n not in review]
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
    # added to remove. The edge check can leave a clip clean on every hard
    # criterion and still undecidable, so ``ok`` alone is not the verdict, and a
    # project with both kinds has to say which is which.
    if ok and not review:
        print("ALL CHECKS PASS")
    elif failed and review:
        print(f"FAILURES PRESENT ({len(failed)} clip(s) failed; "
              f"{len(review)} awaiting human review)")
    elif review:
        print(f"FAILURES PRESENT ({len(review)} clip(s) awaiting human review)")
    else:
        print("FAILURES PRESENT")
    return 0 if (ok and not review) else 1


class _Args:
    """``argparse.Namespace`` without argparse's repr in the usage line."""

    def __init__(self, path: str, corpus: bool, baseline: str | None,
                 compare: str | None):
        self.path = path
        self.corpus = corpus
        self.baseline = baseline
        self.compare = compare


USAGE = """usage: editorial_audit.py <project-dir|corpus-root>
                     [--corpus] [--baseline PATH] [--compare PATH]

  <project-dir>      audit one run and print its findings
  --corpus           audit every *_run under the root, as one sweep
  --baseline PATH    record the sweep as the baseline for later comparison
  --compare PATH     diff the sweep against a recorded baseline, and classify
                     each changed clip as a regression, an improvement, or a
                     change of shape


The sweep modes need a corpus root (a directory of *_run trees); the single
project mode needs a project directory containing selections.json."""


def _parse_args(argv: list[str]) -> _Args | None:
    """Hand-rolled so the tool stays a single import-free file.

    ``--baseline`` and ``--compare`` are only meaningful with ``--corpus``:
    a single project has nothing to compare and no baseline to record, so
    pairing them is a usage error rather than a silently ignored flag.
    """
    positional: list[str] = []
    flags: dict[str, str | None] = {"--corpus": None, "--baseline": None,
                                    "--compare": None}
    takes_value = {"--baseline", "--compare"}
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in flags:
            if arg in takes_value:
                if i + 1 >= len(argv):
                    print(f"{arg} needs a path", file=sys.stderr)
                    print(USAGE, file=sys.stderr)
                    return None
                flags[arg] = argv[i + 1]
                i += 2
                continue
            flags[arg] = "yes"
        elif arg in ("-h", "--help"):
            print(USAGE)
            return None
        else:
            positional.append(arg)
        i += 1

    if len(positional) != 1:
        print(USAGE, file=sys.stderr)
        return None
    corpus = flags["--corpus"] is not None
    if not corpus and (flags["--baseline"] or flags["--compare"]):
        print("--baseline and --compare need --corpus", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return None
    return _Args(positional[0], corpus, flags["--baseline"], flags["--compare"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
