from models.core import Segment, Word
from engine.editorial import make_thoughts, rank, score_candidate
from engine.context_guard import validate


def test_thought_segmentation_preserves_complete_thoughts():
    segs = [
        Segment(0, 2, "People think this is simple.", []),
        Segment(2, 5, "But the real reason is much stranger.", []),
        Segment(5, 10, "It changes the entire way the system behaves.", []),
    ]
    thoughts = make_thoughts(segs, min_sec=5)
    assert thoughts
    assert "real reason" in thoughts[0].text


def test_the_thought_cap_is_taken_inside_the_scan_and_truncates_in_start_order():
    """``make_thoughts`` used to apply its 4000-thought cap as a post-hoc slice
    after the whole scan, so on a long transcript it paid the full
    superlinear cost and then threw most of the result away — 6.0 s against
    1.8 s on an 80-minute source. The cap is now taken inside the loop.

    That is only equivalent because the list is built in non-decreasing start
    order: a later sentence index can only append a thought that sorts after
    the truncated tail, so stopping early returns the same list the slice
    would have. This test pins that invariant, because it is the whole
    justification for the change — a future reordering of the generation would
    silently make the early return return the wrong 4000.

    It is not, and cannot be, a performance regression test: the two
    implementations are output-identical by construction, so no behavioural
    assertion distinguishes them. The equivalence was checked by running both
    over every committed corpus transcript (8, all identical), and the timing
    is recorded in the audit rather than asserted here."""
    from pathlib import Path
    import json

    def build(minutes: int):
        """A transcript long enough to exceed the cap: 4 s sentences with real
        word timestamps, since empty word lists would not exercise the
        per-thought word scan the cost comes from."""
        segs, t = [], 0.0
        while t < minutes * 60:
            words = [
                Word(start=t + j * 0.3, end=t + j * 0.3 + 0.25, text=f"word number {j}")
                for j in range(12)
            ]
            segs.append(Segment(t, t + 4.0, " ".join(w.text for w in words) + ".", words))
            t += 4.0
        return segs

    capped = make_thoughts(build(25))
    assert len(capped) == 4000, \
        "this fixture has to reach the cap, or the truncation below asserts nothing"
    starts = [t.start for t in capped]
    assert starts == sorted(starts), \
        "the list must be in start order, or an in-loop cap is not equivalent to a slice"

    # Extending the input past the cap cannot change the returned thoughts.
    longer = make_thoughts(build(120))
    assert len(longer) == 4000
    assert [(t.start, t.end, t.text) for t in capped] == \
           [(t.start, t.end, t.text) for t in longer]

    # The invariant also has to hold on real transcripts, where sentence
    # lengths and boilerplate breaks vary.
    for path in sorted(Path("corpus").rglob("transcript.json"))[:8]:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["segments"] if isinstance(data, dict) else data
        segs = [
            Segment(float(s["start"]), float(s["end"]), s.get("text") or "", [])
            for s in rows if isinstance(s, dict) and "start" in s
        ]
        if not segs:
            continue
        real = make_thoughts(segs)
        rs = [t.start for t in real]
        assert rs == sorted(rs), f"{path.name} is not in start order"


def test_rank_is_deterministic_and_non_overlapping():
    """Three claims that clear the filter (each 20+ words, 8-55 s, distinct
    content), so the ranking has something to choose from.

    This test used to be vacuous in a way its name hid: the segments were 14
    words each, below the 20-word floor, so ``rank`` returned an empty list and
    the assertion compared ``[] == []``. It would have passed if ranking had
    stopped returning anything at all. The non-empty check below is what makes
    the determinism assertion mean something, and the overlap check is the one
    the name has always promised and never made.
    """
    segs = [
        Segment(0, 20, "Nobody expects this, but the hidden system quietly changed everything about how the industry works, and here is precisely why that matters to you today.", []),
        Segment(25, 48, "This is another genuinely interesting section of the talk, containing a surprising result that most practitioners would never predict without running the numbers themselves.", []),
        Segment(55, 80, "The practical insight is remarkably simple in hindsight, and the figures on the slide explain the final outcome far more clearly than any lengthy narrative could.", []),
    ]
    first = [c.to_dict() for c in rank(segs, 3)]
    second = [c.to_dict() for c in rank(segs, 3)]
    assert first, "ranking returned nothing, so the determinism check below is vacuous"
    assert first == second

    spans = [(c["start"], c["end"]) for c in first]
    for (a, b), (c, d) in zip(spans, spans[1:]):
        assert b <= c, f"selected clips overlap in source time: {b} > {c}"


def test_rank_rejects_a_time_overlapping_candidate():
    """The non-overlap check above is only as strong as its input: the three
    segments there are already disjoint in source time, so a ``rank`` with the
    de-overlap guard deleted returns the same answer and the assertion still
    passes. This test supplies what that one cannot — two candidates that
    *overlap*, and would both be kept if the guard were gone.

    The wording is the other half of what makes this work. With three clips
    about the same topic, the similarity and topic-token filters in ``rank``
    drop the second one on their own, so the overlap guard never gets to fire
    and the test would again pass against a guardless ``rank``. The three
    passages here are deliberately about separate subjects — finance, whales,
    bridge maintenance — which I confirmed by instrumenting ``rank``: the
    overlapping candidate is skipped by the overlap check and by nothing else.
    """
    segs = [
        Segment(0, 20, "The fiscal quarter closed with an unexpected surplus in the ledger, and the auditors confirmed every line item reconciled against the prior statement without a single discrepancy remaining open.", []),
        Segment(10, 32, "Marine biologists tagged a juvenile whale near the reef shelf, tracking its dive profile across three separate feeding cycles throughout the migration season this year entirely.", []),
        Segment(60, 85, "The bridge maintenance crew repainted the north span overnight, finishing two hours ahead of the Monday commute schedule and under the allocated municipal budget by six percent.", []),
    ]
    chosen = rank(segs, 3)
    assert len(chosen) == 2, "the overlapping candidate must be the one dropped"

    spans = sorted((c.start, c.end) for c in chosen)
    for (_, b), (c, _) in zip(spans, spans[1:]):
        assert b <= c, f"selected clips overlap in source time: {b} > {c}"
    assert not any(10 <= c.start < 32 for c in chosen), \
        "the whale passage (10-32 s) overlaps the fiscal clip and must not ship"


# ── a candidate's text, bounds and words must describe one clip ──────────────

def _words(text, start, step=0.3, conf=1.0):
    """Word timing of the kind faster-whisper returns: leading space on every
    token after the first, one word per 0.3 s. ``conf`` stands in for the
    per-word probability the decoder actually emits."""
    out, t = [], start
    for i, w in enumerate(text.split()):
        out.append(Word((" " if i else "") + w, t, t + step * 0.8, conf))
        t += step
    return out


def test_scored_candidate_text_agrees_with_its_words():
    """Selection bounds are estimated proportionally to character span, so a
    bound lands inside a word — and the word list, which is real timing,
    already contains that word while the text does not. Found on the committed
    pentagon corpus: clip 04 opened on 'Syria.' (364.50-364.84 s) but its text
    began at 'has', and the shipped SRT said 'Syria.'. The content pack's hook
    described a clip that was not the rendered one.

    The candidate is the fix's seam: it is built once, here, and everything
    downstream (EDL, render, captions, content pack) reads its fields. If text
    and words disagree here they disagree in every artefact."""
    text = ("Syria. has the deployment of reinforcements that you announced "
            "last week begun and could you describe more fully what you "
            "intend to do there?")
    rest = _words(" ".join(text.split()[1:]), 364.84)
    # The opening word straddles the estimated bound: on the committed corpus
    # it spans 364.50-364.84000000000003 against a bound of 364.84, so the
    # strict inequality in _word_aligned sees it as inside.
    words = [Word(" Syria.", 364.50, 364.84000000000003)] + rest
    seg = Segment(364.84, rest[-1].end, " ".join(text.split()[1:]), words)
    c = score_candidate(seg)

    assert c.text.startswith("Syria."), \
        f"candidate text opens {c.text[:30]!r}, omitting the word the " \
        f"shipped clip opens on"
    assert c.start == 364.50, "the opening bound must snap back to the word"
    # The closing edge is the mirror image: a bound inside the final word must
    # drop it rather than carry a half word in both the text and the render.
    assert c.text == " ".join(text.split())


def test_scored_candidate_drops_a_partial_closing_word_from_text():
    """The same desync on the closing edge, where the word is partially past
    the bound. Measured on the royal-society corpus, a candidate ending
    235.237 s cut into 'Somebody' (235.22-235.38 s); the text omitted it and
    the words list included it. Both must settle on not having the word."""
    text = "They tend to hear about things a day in advance. Somebody"
    words = _words(text, 181.78)
    end = 181.78 + 0.3 * (len(words) - 1) + 0.017  # inside the final word
    seg = Segment(181.78, end, " ".join(text.split()[:-1]), words)
    c = score_candidate(seg)

    assert not c.text.rstrip().endswith("Somebody"), \
        "a half-cut closing word must not survive into the candidate text"
    for w in words[:-1]:
        assert w.end <= c.end, f"word {w.text!r} extends past the closed bound"


def test_an_already_aligned_candidate_is_left_alone():
    """The fix snaps bounds that fall inside a word and rebuilds the text, so a
    span that is already word-aligned must come back unchanged — otherwise
    every candidate in the corpus is rewritten and the scoring drifts."""
    text = "Nobody expects the system to change this quickly at all."
    words = _words(text, 10.0)
    end = 10.0 + 0.3 * len(words)
    seg = Segment(10.0, end, text, words)
    c = score_candidate(seg)

    assert (c.start, c.end) == (10.0, end)
    assert c.text == text, "an aligned span must not have its text rebuilt"


def test_reasons_differ_between_clips_and_name_their_evidence():
    """Priority 0 requires the product to answer "why was this selected?", and
    the reason field is the answer. It used to be a fixed-format string of
    numbers whose prose prefix was identical for every clip, so on the real
    corpus all six candidates' reasons were the same length and opened the same
    way — the field carried measurements but no clip-specific judgement.

    Two clips with opposite profiles must now read differently in prose, not
    only in digits, and the prose must stay truthful about the numbers."""
    strong = ("We found that the failure rate doubled overnight, and the "
              "cause was a single misconfigured valve we could name and fix.")
    weak = ("So, um, well, you know, it is, like, basically the sort of thing "
            "that, you know, one might perhaps consider at some point maybe.")
    c_strong = score_candidate(Segment(5.0, 5.0 + 0.3 * len(strong.split()),
                                       strong, _words(strong, 5.0)))
    c_weak = score_candidate(Segment(95.0, 95.0 + 0.3 * len(weak.split()),
                                     weak, _words(weak, 95.0)))

    for c, label in ((c_strong, "strong"), (c_weak, "weak")):
        assert any(ch.isalpha() for ch in c.reason), \
            f"{label} reason quotes only numbers"
        # The prose sentence comes first and the numeric detail follows it, so
        # the reason stays measurable as well as readable.
        assert c.reason.lstrip()[:1].isalpha(), \
            f"{label} reason does not open with a sentence"

    assert c_strong.reason != c_weak.reason
    # Not merely different digits: the clip-specific prose differs too. Two
    # reasons that share their opening words are still boilerplate.
    assert c_strong.reason.split(".")[0] != c_weak.reason.split(".")[0], \
        f"both reasons open with the same prose: {c_strong.reason.split('.')[0]!r}"
    # The strongest/weakest signal named in the prose is a real claim about the
    # clip, so the score it asserts must actually appear in the reason itself.
    for c in (c_strong, c_weak):
        assert "opening=" in c.reason and "density=" in c.reason

    # The claim must match the measurement, not flatter the clip: the weak
    # passage is filler-heavy, and a reason that called it clean of filler
    # would be a fabrication.
    assert "clean of filler" not in c_weak.reason, c_weak.reason
    assert "fillers" in c_weak.reason


def test_confidence_is_measured_not_constant_and_the_guard_can_fire():
    """``score_candidate`` set ``confidence`` to the literal 0.90 for every
    candidate from every source, which made ``context_guard.validate``'s
    ``c.confidence < 0.75`` branch unreachable from any input — a guard written
    to reject clips the decoder was unsure of rejected nothing, and the shipped
    report's confidence column read "0.9 (all six)" because it was a constant,
    not a measurement.

    faster-whisper emits a probability per word, so the confidence is now that.
    """
    text = ("We found that the failure rate doubled overnight, and the cause "
            "was a single misconfigured valve we could name and fix.")
    n = len(text.split())
    span = lambda conf: Segment(5.0, 5.0 + 0.3 * n, text, _words(text, 5.0, conf=conf))

    clean = score_candidate(span(0.97))
    garbled = score_candidate(span(0.30))
    assert clean.confidence != garbled.confidence, \
        "confidence must vary with the transcript it was scored from"
    assert clean.confidence > 0.75 and garbled.confidence < 0.75

    # The guard's own branch, which was dead code before this:
    assert validate(clean)[0] is True
    ok, why = validate(garbled)
    assert ok is False and why == "low factual confidence", \
        f"a low-confidence clip must be gated, got ok={ok} why={why!r}"

    # One hard word in a long clip is a word an editor can fix; averaging
    # rather than taking the minimum keeps the clip.
    mixed = _words(text, 5.0, conf=0.97)
    mixed[3] = Word(mixed[3].text, mixed[3].start, mixed[3].end, 0.05)
    c = score_candidate(Segment(5.0, 5.0 + 0.3 * n, text, mixed))
    assert validate(c)[0] is True, "a single low word must not sink a clean clip"

    # A transcript path that supplies no probabilities must read as unknown,
    # never as low: Word.confidence defaults to 1.0 and there is no evidence
    # to translate into a rejection.
    c = score_candidate(Segment(5.0, 5.0 + 0.3 * n, text, []))
    assert c.confidence == 1.0 and validate(c)[0] is True
