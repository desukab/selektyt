from models.core import Segment, Word
from engine.editorial import make_thoughts, rank, score_candidate


def test_thought_segmentation_preserves_complete_thoughts():
    segs = [
        Segment(0, 2, "People think this is simple.", []),
        Segment(2, 5, "But the real reason is much stranger.", []),
        Segment(5, 10, "It changes the entire way the system behaves.", []),
    ]
    thoughts = make_thoughts(segs, min_sec=5)
    assert thoughts
    assert "real reason" in thoughts[0].text


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


# ── a candidate's text, bounds and words must describe one clip ──────────────

def _words(text, start, step=0.3):
    """Word timing of the kind faster-whisper returns: leading space on every
    token after the first, one word per 0.3 s."""
    out, t = [], start
    for i, w in enumerate(text.split()):
        out.append(Word((" " if i else "") + w, t, t + step * 0.8))
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
