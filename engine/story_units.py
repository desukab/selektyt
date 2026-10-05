"""Story-unit segmentation, boilerplate detection and coherence scoring.

A transcript usually contains several distinct stories. Generating clip
candidates across a story boundary produces a clip that starts one story
and ends another — technically dense, editorially worthless. This module
splits a transcript into coherent story units *before* candidates are
generated, so a candidate can never span unrelated stories.

Boilerplate (section markers, issue/byline/date read-outs) is treated as
a scored signal rather than a flat blacklist: it marks hard story
boundaries and costs the candidate editorial points, instead of merely
being crossed off a list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from models.core import Segment

# --- boilerplate -----------------------------------------------------------

# Section markers: "Part 2", "Chapter 3". These are real story boundaries,
# so they split units, but they are not metadata — a "Part 2" header is a
# legitimate structural beat of the source, not an apology by the editor.
_SECTION = re.compile(
    r"\b(?:part|chapter|section|episode|installment)\s+"
    r"(?:\d+|[ivx]+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    re.IGNORECASE,
)
_SECTION_INDIC = re.compile(
    r"భాగం|అధ్యాయం|अध्याय|खंड|பகுதி|ಅಧ್ಯಾಯ|ഭാഗം|অধ্যায়",
)

# Publication metadata: bylines, issue/date read-outs, author affiliation.
# This is the article's colophon, not its content.
_META = re.compile(
    r"\b(?:"
    r"this\s+article\s+appears\s+in|"
    r"print\s+issue|digital\s+issue|this\s+issue|"
    r"(?:associate|contributing|senior|staff|freelance|editorial)\s+"
    r"(?:editor|writer|journalist|director)|"
    r"is\s+a\s+(?:freelance|staff|contributing|senior)\s+"
    r"(?:writer|editor|journalist)|"
    r"(?:writes|reports|covers)\s+for\b|"
    r"at\s+(?:ieee|id)\s+spectrum|ieee\s+spectrum|spectrum\s+magazine|"
    r"copyright|all\s+rights\s+reserved|"
    r"subscribe\s+(?:to|for)|read\s+more\s+at|"
    r"this\s+(?:story|article)\s+(?:was|is)\s+(?:originally|first)\s+published"
    r")\b",
    re.IGNORECASE,
)

# A month next to a day or a year is a date read-out when it is not inside
# a longer factual clause ("in March 1990 the system went live" is content;
# a trailing "February 16." is a dateline). Match only a short standalone
# date phrase so historical facts are not penalised.
_DATELINE = re.compile(
    r"(?:^|[.\s])(?:(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+\d{1,2}\b)"
    r"(?=\s*[.,]|\s*$)",
    re.IGNORECASE,
)


# A byline usually survives ASR garbling in the role word ("associated data"
# for "associate editor"), so match the shape instead: a name, then "is a/an".
# It is only treated as boilerplate when an affiliation follows, which the
# back-pass in segment_story_units checks.
_BYLINE_SHAPE = re.compile(
    r"^\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:is|was)\s+(?:a|an)\b"
)

# Affiliation / publication tails that complete a byline.
_AFFILIATION = re.compile(
    r"\bat\s+(?:ieee|id)\s+spectrum|ieee\s+spectrum|spectrum\s+magazine|"
    r"\bmagazine\b|\bpublication\b",
    re.IGNORECASE,
)


@dataclass
class Sentence:
    text: str
    start: float
    end: float
    boilerplate: str = ""  # "" | "section" | "meta" | "dateline"
    # Whether ``start``/``end`` were read off the word stream, as opposed to
    # estimated from character span. A clip may only *open* on a sentence this
    # is true of: an estimated bound lands inside whichever word happens to be
    # speaking, and ``edit_plan.align_span`` then rebuilds the clip's text from
    # the words that survive the cut, silently deleting the subject. See
    # ``_segment_sentences`` and ``editorial.make_thoughts``.
    word_anchored: bool = True


@dataclass
class StoryUnit:
    start: float
    end: float
    segment_indices: list[int] = field(default_factory=list)
    sentences: list[Sentence] = field(default_factory=list)
    boilerplate: bool = False
    coherence: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences).strip()


_TERMINAL = re.compile(r"[.!?।॥？！]+")

# Titles of address and military rank that end in a period and are always
# followed by the name they qualify. Measured across every multi-piece segment
# of the corpus: of 354 pieces, 11 carried a period that was not a boundary, and
# 9 of those were one of these followed by a capitalised name. The remaining two
# ("no.", "etc.") are genuinely ambiguous in either direction and are left
# alone rather than guessed at -- see ``_is_boundary`` for why the safe
# direction is to not split.
_TITLES = frozenset(
    "mr mrs ms dr prof sr jr st gen sen rep gov lt sgt col capt maj rev hon".split())


def _is_boundary(text: str, m: re.Match[str]) -> bool:
    """Is this terminal-mark run a real sentence boundary?

    Four cases where it is not, each structural rather than a guess:

    - a decimal point between digits (``3.14``);
    - a period with letters tight against it on both sides (``mediawiki.org``);
    - a period ending a token with internal periods (``U.S.``, ``e.g.``);
    - a period after a title or a single lowercase letter, when the next word
      is capitalised (``Dr. Esper``, ``a. The first step``).

    That last rule is where the residual ambiguity lives: ``I saw Dr. He
    left.`` is a boundary this refuses to see. It is accepted deliberately,
    because the failure this exists to fix is the opposite one --
    ``Thank you, Mr. Secretary.`` split into ``Mr.`` and ``Secretary.``,
    the second piece timed into the middle of the word ``Mr.``, and a clip
    that opened on ``Mr.`` and dropped ``Thank you,`` with no sentence break
    between them. A sentence that runs one name long is a small error; a clip
    whose first word is cut in half is a defect a viewer sees. Under-splitting
    is the safe direction, so the rule errs that way.
    """
    if m.group() != ".":
        return True  # "!!", "??" and the multilingual marks are never ambiguous
    before, after = text[: m.start()], text[m.end():]
    if before and after and before[-1].isdigit() and after[0].isdigit():
        return False
    if before and after and before[-1].isalpha() and after[0].isalpha():
        return False
    if not after.lstrip():
        return True  # the text ends here, so the period ends the sentence
    word = before.split()[-1].lower() if before.split() else ""
    # An acronym or abbreviation carries its periods internally, so a period
    # at its edge belongs to the token rather than to the sentence.
    if "." in word and all(ch.isalpha() or ch == "." for ch in word):
        return False
    # A title qualifies the capitalised name that follows it.
    if word in _TITLES and after.lstrip()[:1].isupper():
        return False
    # A single lowercase letter is a lettered list item, not a sentence.
    # A digit is not (``Section 2. The next part`` is two sentences).
    if len(word) == 1 and word.isalpha() and after.lstrip()[:1].isupper():
        return False
    return True


def split_sentences(text: str) -> list[str]:
    """Split on terminal punctuation, keeping the punctuation. Multilingual.

    A period that terminates a title (``Mr.``, ``Dr.``) is not a boundary; see
    :func:`_is_boundary` for the measured cost of treating it as one.
    """
    stripped = text.strip()
    out: list[str] = []
    cursor = 0
    for m in _TERMINAL.finditer(stripped):
        if not _is_boundary(stripped, m):
            continue
        piece = stripped[cursor: m.end()].strip()
        if piece:
            out.append(piece)
        cursor = m.end()
    tail = stripped[cursor:].strip()
    if tail:
        out.append(tail)
    return out or [stripped]


def _split_markers(sentence: str) -> list[str]:
    """Peel a leading or trailing boilerplate phrase off a content sentence.

    "…Federal Aviation Administration, Part 1." is a real sentence with a
    section marker stapled to its tail. The sentence is content; only
    "Part 1." is the marker, so the two are separated and the marker
    becomes a boundary of its own rather than condemning the sentence.
    """
    if not sentence.strip():
        return []
    for pattern in (_META, _SECTION, _DATELINE):
        m = pattern.search(sentence)
        if not m:
            continue
        head, tail = sentence[: m.start()].strip(), sentence[m.start():].strip()
        # Only peel when the remainder still carries a sentence's worth of
        # content; otherwise the whole line is the marker and is left intact.
        if head and len(head.split()) >= 6:
            return [head, tail] if tail else [head]
        if tail and len(tail.split()) >= 6:
            return [tail] if head else [tail]
    return [sentence]


def _proportional_sentences(pieces: list[str], seg: Segment) -> list[Sentence]:
    """Sentences timed proportionally to character span — the fallback.

    This is the only option when a segment carries no word timestamps, and the
    bound returned for a sentence that the word stream cannot cover. It is
    marked ``word_anchored=False`` so no clip is allowed to open on it: the
    estimate lands wherever the character count happens to fall, which can be
    inside a word belonging to the previous sentence, and
    ``edit_plan.align_span`` would then rebuild the clip's opening from the
    words that survive the cut.
    """
    total_chars = max(1, sum(len(p) for p in pieces))
    dur = max(0.0, seg.end - seg.start)
    out: list[Sentence] = []
    cursor = 0.0
    for piece in pieces:
        frac = len(piece) / total_chars
        s = seg.start + cursor * dur
        e = seg.start + min(cursor + frac, 1.0) * dur
        out.append(Sentence(piece, round(s, 3), round(e, 3), _classify(piece),
                            word_anchored=False))
        cursor += frac
    if out:
        out[-1].end = seg.end
    return out


# Punctuation stripped before a sentence's text is matched against the word
# stream: ASR emits punctuation as part of the word token ("start."), and a
# sentence boundary in one writing system is a word-joiner in another. Kept in
# sync with ``corpus._norm_text``; defined here because ``corpus`` imports this
# module and not the reverse.
_STRIP_PUNCT = str.maketrans("", "", "।॥？！.,;:—-–\"'()[]{}")


def _norm_text(t: str) -> str:
    return t.translate(_STRIP_PUNCT).strip()


def _piece_tokens(t: str) -> set[str]:
    """Letters-and-digits of each token of ``t``, case-folded, as a set.

    The sentence text and the word stream are two decodes of the same audio and
    disagree on punctuation and capitalisation (``"hand?"`` vs ``"hand"``) and
    on contraction spelling (``"I'm"`` vs ``"Im"``). Reducing both to a set of
    alphanumeric token bodies makes the coverage test below compare content
    rather than spelling, while still refusing a sentence whose words are
    simply different words -- ``"Speed"`` does not cover
    ``"Speed it up, guys."`` no matter how it is spelt.

    A token that is entirely digits is split into its digits rather than kept
    as one body. faster-whisper emits a decimal number as two words --
    ``"40"`` and ``".3"`` for the spoken ``"40.3"`` -- where the sentence text
    has the one token ``"40.3"``. Kept whole, the sentence's body is ``"403"``,
    which no word ever produces, and the sentence is judged uncovered. On the
    committed Royal Society transcript that refusal cost the reel's opening:
    segment 0 (``"Now last year the UK recorded its highest ever temperature of
    40.3 degrees Celsius..."``) fell back to proportional timing, the scan
    refused it as an opening sentence, and the clip lost 12 s of its hook.
    Letters still decide identity, so the loosening is only in how digits pair
    up: a sentence's own words are what cover it, and two numbers sharing digits
    cannot cover a sentence that shares none of its words. ``"34"`` is a subset
    of ``"40.3"``'s digits, and that is the accepted cost — against the
    alternative of a covered sentence judged uncovered.
    """
    out: set[str] = set()
    for p in t.split():
        body = re.sub(r"[^\w]", "", p, flags=re.UNICODE).lower()
        if not body:
            continue
        if body.isdigit():
            out.update(body)
        else:
            out.add(body)
    return out


def _segment_sentences(seg: Segment) -> list[Sentence]:
    """Sentences of one ASR segment, timed from the word stream where it exists.

    Word timing is measured and sentence timing is not: splitting a segment by
    character proportion puts a boundary wherever the character count falls,
    which is routinely inside a word, and even inside a *different* sentence's
    word. On the committed Tears of Steel transcript, segment 0 spans 0.0–7.0 s
    with the text ``"We have main engine start. Four, three, two, one."`` but
    only five words — ``We / have / main / engine / start.`` — which cover the
    first sentence alone. The proportional split therefore manufactured a
    boundary at 3.792 s for a sentence whose words do not exist, a clip opening
    there snapped back to the word ``have``, and the shipped reel opened on
    ``"have main engine start."`` with its subject deleted.

    Where the words exist, each sentence is anchored to the words that compose
    it by consuming the stream until the sentence's normalised text is covered,
    which is the same calculation ``corpus._align_sentence_times`` already
    performed for the corpus — moved here so the candidate scan sees real
    bounds instead of inheriting the corpus's post-hoc repair.

    Deliberately conservative: when the count does not line up — the words ran
    out before a sentence was covered, or consumed nothing for it — the whole
    segment keeps proportional timing and every sentence is marked
    ``word_anchored=False``. One plausible-looking timecode is worse than an
    admitted estimate.
    """
    pieces: list[str] = []
    for piece in split_sentences(seg.text):
        pieces.extend(_split_markers(piece))
    if not pieces:
        return []
    words = seg.words or []
    if not words:
        return _proportional_sentences(pieces, seg)

    times: list[tuple[float, float]] = []
    ptr = 0
    for piece in pieces:
        target = _norm_text(piece)
        # ``_norm_text`` keeps case and punctuation, which the word stream also
        # carries, but the two decode them differently -- ``"hand?"`` vs
        # ``"hand"``, ``"I'm"`` vs ``"Im"`` -- so the comparison is on the
        # letters and digits of each token, case-folded.
        need = _piece_tokens(target)
        if not need:
            times.append((seg.start, seg.start))
            continue
        while ptr < len(words) and not _norm_text(words[ptr].text):
            ptr += 1
        start_w = ptr
        have: set[str] = set()
        while ptr < len(words):
            w = _norm_text(words[ptr].text)
            if not w:
                ptr += 1
                continue
            have |= _piece_tokens(w)
            ptr += 1
            if need <= have:
                break
        if start_w >= len(words) or ptr <= start_w or not need <= have:
            # The word stream ran out before this sentence was covered, consumed
            # nothing for it, or covered only its *length*: the transcript's
            # words and its sentence text disagree. That last case is the one
            # the character-count test used to pass, and it is the worst of the
            # three, because the bounds it emits look exact. On the committed
            # transcript, ``"Speed it up, guys."`` has one word -- ``Speed`` --
            # and the old test consumed ``"Speed Why don't you just admit"``,
            # reached the character count, and anchored the sentence at 342.78 s
            # to speech that is not that sentence. Requiring the sentence's own
            # tokens to be present rejects it, and the segment falls back to the
            # proportional bounds it admits to.
            return _proportional_sentences(pieces, seg)
        s_t = words[start_w].start
        e_t = words[max(start_w, ptr - 1)].end if ptr > start_w else words[start_w].end
        times.append((s_t, max(e_t, s_t)))
    if len(times) != len(pieces):
        return _proportional_sentences(pieces, seg)

    out: list[Sentence] = []
    for piece, (s, e) in zip(pieces, times):
        # Unrounded: ``s`` and ``e`` *are* word timestamps, and rounding them
        # breaks the identity ``sentence.start == word.start`` that
        # ``edit_plan`` relies on. On the committed transcript, ``"Я люблю
        # это!"`` opens at the word ``"Я"`` (213.01999 s) and rounding the
        # sentence to 213.02 put that word 1e-5 s outside the bound;
        # ``_BOUND_EPS`` is 1e-6, so ``_clip_words`` decided ``"Я"`` belonged to
        # the previous sentence and dropped it, and the clip shipped as
        # ``"люблю это!"`` minus its subject. Estimates are rounded (below);
        # measurements are not.
        out.append(Sentence(piece, s, e, _classify(piece)))
    return out


def _classify(text: str) -> str:
    """Boilerplate kind for a sentence, or '' when it is real content."""
    if _META.search(text):
        return "meta"
    if _SECTION.search(text) or _SECTION_INDIC.search(text):
        return "section"
    if _DATELINE.search(text):
        return "dateline"
    return ""


def boilerplate_fraction(text: str) -> tuple[float, list[str]]:
    """Fraction of this text's sentences that are boilerplate, with kinds.

    Metadata (byline/issue) costs the clip editorial value; a bare section
    marker ("Part 2") is structural and costs nothing here — it is handled
    as a unit boundary instead.
    """
    sentences = split_sentences(text)
    if not sentences:
        return 0.0, []
    kinds: list[str] = []
    cost = 0.0
    for s in sentences:
        kind = _classify(s)
        if not kind:
            continue
        kinds.append(kind)
        cost += 0.25 if kind == "section" else 1.0
    return min(1.0, cost / len(sentences)), sorted(set(kinds))


# --- lexical coherence ------------------------------------------------------

# A compact English stopword list. For other scripts every word is content,
# which still compares correctly — the signal is *relative* drop in overlap.
_STOP = {
    "the", "a", "an", "of", "to", "in", "and", "or", "is", "are", "was",
    "were", "be", "been", "being", "that", "this", "these", "those", "it",
    "its", "as", "for", "on", "with", "at", "by", "from", "but", "not",
    "have", "has", "had", "he", "she", "they", "we", "you", "i", "his",
    "her", "their", "our", "your", "which", "there", "when", "what", "who",
    "will", "would", "can", "could", "may", "might", "should", "if", "then",
    "than", "so", "such", "into", "about", "after", "before", "between",
    "during", "under", "over", "also", "more", "most", "some", "any", "all",
    "each", "other", "one", "two", "up", "out", "no", "do", "does", "did",
    "been", "very", "just", "only", "even", "because", "while", "where",
    "how", "now", "new", "said", "says",
}


def _content_tokens(text: str) -> set[str]:
    toks = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
    return {t for t in toks if len(t) > 2 and t not in _STOP and not t.isdigit()}


def _containment(a: set[str], b: set[str]) -> float:
    """Overlap relative to the smaller bag, so length differences are fair."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def coherence_score(text: str) -> float:
    """0..1 — how topically continuous a span of text is.

    Two failure modes look identical to a plain mean of adjacent overlap:
    a splice of two unrelated stories has one sharp seam and high overlap
    inside each half, while a coherent but lexically diverse paragraph has
    uniformly low overlap everywhere. The first is a real defect, the
    second is just writing style, so the score is built from the *seam*
    rather than the average: a span is incoherent when one boundary is
    much weaker than its neighbours, not when all of them are weak.

    Discourse continuation ("But the real reason…") counts as full
    continuity — connectives tie sentences into one argument whether or
    not they share words.
    """
    sentences = [s for s in split_sentences(text) if s.strip()]
    if len(sentences) < 2:
        return 1.0
    sims: list[float] = []      # every boundary; a connective counts as 1.0
    plain: list[float] = []     # lexical boundaries only
    for i in range(1, len(sentences)):
        if _continues_prior(sentences[i], sentences[i - 1]):
            sims.append(1.0)
            continue
        overlap = _containment(_content_tokens(sentences[i - 1]),
                               _content_tokens(sentences[i]))
        sims.append(overlap)
        plain.append(overlap)
    if not sims:
        return 0.5
    if len(sims) == 1:
        # One boundary carries no pattern, so it cannot distinguish the two
        # cases this score exists to separate: a splice of two unrelated
        # stories and a coherent pair that merely shares no wording. High
        # overlap is real evidence of continuity; zero overlap is the
        # absence of evidence, not evidence of a seam, so it scores neutral
        # rather than zero. Two boundaries are the minimum from which a
        # sharp seam can actually be seen.
        return round(max(sims[0], 0.5), 3)
    # A sharp seam is what stitching two stories together looks like. It is
    # detected among the *lexical* boundaries only: a connective asserts
    # continuity explicitly, and letting it raise the mean would make an
    # ordinary quiet boundary look like a seam by comparison.
    if len(plain) >= 2:
        pmean = sum(plain) / len(plain)
        if pmean > 0.15 and min(plain) < 0.4 * pmean:
            return round(0.5 * pmean, 3)
    return round(max(sum(sims) / len(sims), min(sims)), 3)


# --- unit segmentation ------------------------------------------------------


def _all_sentences(segments: list[Segment]) -> list[tuple[Sentence, int]]:
    """Every sentence with the index of the segment it came from."""
    out: list[tuple[Sentence, int]] = []
    for i, seg in enumerate(segments):
        for sent in _segment_sentences(seg):
            out.append((sent, i))
    return out


def _continues_prior(text: str, prev: str = "") -> bool:
    """Does this sentence overtly attach to the one before it?

    Discourse coherence is not lexical overlap: "People think this is
    simple." → "But the real reason is much stranger." shares no content
    words yet is one argument. Three attachment signals cover the cases
    bag-of-words cannot see:

      - a connective or contrast opener ("But", "However", "So"),
      - an anaphoric opener ("It", "This", "They"),
      - a definite-noun subject that was introduced in the previous
        sentence ("The team" after "the launch") — the article marks a
        phrase as already-known, which only makes sense if the topic
        carries over.
    """
    low = text.lower()
    first = low.split()[:1]
    if first and first[0] in _DISCOURSE_OPENERS:
        return True
    if _ANAPHORA_OPENERS.search(low):
        return True
    if prev and _DEFINITE_SUBJECT.search(low):
        prev_tokens = _content_tokens(prev)
        # "The team celebrated" continues the prior sentence only when the
        # prior sentence established that subject; a bare "The" opening a
        # brand-new topic is a headline, not a continuation.
        subj = _DEFINITE_SUBJECT.search(low)
        if subj:
            head = subj.group(1).lower()
            if head in prev_tokens or any(t in prev.lower() for t in head.split()):
                return True
    return False


# Words that open a sentence by attaching to the previous one. Contrast
# markers ("but", "however") and causal chains ("because", "so") tie two
# sentences into one argument even when they share no vocabulary.
_DISCOURSE_OPENERS = {
    "but", "however", "yet", "although", "though", "because", "so",
    "therefore", "then", "thus", "while", "whereas", "still", "and",
    "which", "who", "when", "if", "as", "also", "moreover", "furthermore",
    "nevertheless", "nonetheless", "hence", "since", "unless", "except",
}
_ANAPHORA_OPENERS = re.compile(
    r"^(?:it|this|that|these|those|they|he|she|we|you|its|their|our|"
    r"the\s+(?:real\s+)?(?:reason|result|effect|problem|system|way|truth|"
    r"outcome|conclusion|answer|cause|story|figure))\b"
)
# "The <noun> ..." opening: the definite article marks the noun as
# already introduced, which is a continuation when that noun was in the
# previous sentence.
_DEFINITE_SUBJECT = re.compile(r"\bthe\s+([a-z]{3,})\b", re.IGNORECASE)


def _window_bags(texts: list[str], i: int, radius: int = 2) -> tuple[set[str], set[str]]:
    """Content-word bags for the sentences before and after seam `i`.

    A seam is compared with a couple of sentences of context on each side:
    single ASR sentences are often fragments, and two fragments of one
    thought can share no words at all while still being the same story.
    """
    lo = max(0, i - radius)
    hi = min(len(texts), i + radius)
    before = _content_tokens(" ".join(texts[lo:i]))
    after = _content_tokens(" ".join(texts[i:hi]))
    return before, after


def _merge_soft_markers(units: list[StoryUnit]) -> list[StoryUnit]:
    """Reunite a story that a mid-stream section marker split in two.

    "Part 1." stapled to the tail of a running story is not the same as
    "Part 2." opening a new article. The difference is visible: a marker
    that opens a self-contained section sees that section close with a
    colophon (byline/issue) before the next marker appears. A marker whose
    section carries no colophon is a chapter divider inside one story, so
    the story is stitched back together — provided the two halves are
    still talking about the same thing.
    """
    merged: list[StoryUnit] = []
    i = 0
    while i < len(units):
        u = units[i]
        is_marker = (
            u.boilerplate
            and bool(u.sentences)
            and all(s.boilerplate == "section" for s in u.sentences)
        )
        if is_marker and merged and i + 1 < len(units):
            prev, nxt = merged[-1], units[i + 1]
            if not prev.boilerplate and not nxt.boilerplate and prev.sentences and nxt.sentences:
                # A colophon before the next marker means the marker opened a
                # whole article; no colophon means it opened a chapter.
                hard = False
                for w in units[i + 1:]:
                    if w.boilerplate:
                        hard = any(
                            s.boilerplate in ("meta", "dateline") for s in w.sentences
                        )
                        break
                if not hard:
                    cont = _containment(
                        _content_tokens(prev.text), _content_tokens(nxt.text)
                    )
                    if cont >= 0.10:
                        sents = [
                            s for s in (prev.sentences + u.sentences + nxt.sentences)
                            if not s.boilerplate
                        ]
                        merged[-1] = StoryUnit(
                            start=prev.start,
                            end=nxt.end,
                            segment_indices=sorted(
                                set(prev.segment_indices + u.segment_indices + nxt.segment_indices)
                            ),
                            sentences=sents,
                            boilerplate=False,
                            coherence=coherence_score(" ".join(s.text for s in sents)),
                        )
                        i += 2
                        continue
        merged.append(u)
        i += 1
    return merged


def segment_story_units(segments: list[Segment]) -> list[StoryUnit]:
    """Split a transcript into coherent story units.

    Boundaries are placed where:
      - a sentence is boilerplate of any kind (section markers, bylines,
        datelines all mark transitions), and
      - the topical overlap across a seam collapses relative to its
        neighbourhood, which catches topic shifts that carry no marker.

    A coherence break is only allowed where the preceding sentence ends with
    terminal punctuation — a mid-sentence seam is ASR fragmenting, not the
    story changing. A unit that turns out to be entirely boilerplate is
    flagged and its sentences are excluded from clip text.
    """
    sents = _all_sentences(segments)
    if not sents:
        return []

    # A byline often survives the role word being misheard ("Willie Jones is
    # an associated data" + "at ID spectrum."), leaving the first half
    # reading as content. Reunite them: a byline-shaped sentence immediately
    # preceding an affiliation is metadata, not story.
    for j in range(1, len(sents)):
        prev_s, this_s = sents[j - 1][0], sents[j][0]
        if (
            not prev_s.boilerplate
            and this_s.boilerplate == "meta"
            and _BYLINE_SHAPE.match(prev_s.text)
            and _AFFILIATION.search(this_s.text)
        ):
            prev_s.boilerplate = "meta"

    texts = [s.text for s, _ in sents]

    groups: list[list[tuple[Sentence, int]]] = [[sents[0]]]
    for i in range(1, len(sents)):
        prev_bp = bool(sents[i - 1][0].boilerplate)
        this_bp = bool(sents[i][0].boilerplate)
        # Boilerplate always separates: a byline does not belong to the story
        # before it, and "Part 2" does not belong to "Part 1".
        if prev_bp or this_bp:
            groups.append([sents[i]])
            continue
        # Never break mid-sentence: the seam is ASR fragmenting a thought.
        if not _TERMINAL.search(sents[i - 1][0].text.strip()[-2:]):
            groups[-1].append(sents[i])
            continue
        # An explicit discourse attachment ("But the real reason…") continues
        # the current argument regardless of lexical overlap. This is checked
        # before the word-overlap test because it is the stronger, direct
        # signal — overlap can be coincidental, connectives are not.
        if _continues_prior(sents[i][0].text, sents[i - 1][0].text):
            groups[-1].append(sents[i])
            continue
        before, after = _window_bags(texts, i)
        seam = _containment(before, after)
        lo = max(0, i - 3)
        hi = min(len(texts), i + 4)
        local = [
            _containment(*_window_bags(texts, j))
            for j in range(lo, hi)
            if j > 0
        ]
        baseline = sum(local) / max(1, len(local))
        # A topic change needs an established topic to change from. One short
        # sentence cannot establish one, so two neighbouring short sentences
        # are assumed to belong to the same paragraph until something
        # concrete (a boilerplate marker, a hard lexical drop after real
        # accumulation) says otherwise.
        established = len(groups[-1]) >= 2
        if established and seam < 0.10 and (seam < 0.5 * baseline or baseline < 0.12):
            groups.append([sents[i]])
            continue
        groups[-1].append(sents[i])

    units: list[StoryUnit] = []
    for group in groups:
        kinds = {s.boilerplate for s, _ in group if s.boilerplate}
        non_bp = [(s, i) for s, i in group if not s.boilerplate]
        units.append(
            StoryUnit(
                start=group[0][0].start,
                end=group[-1][0].end,
                segment_indices=sorted({i for _, i in group}),
                sentences=[s for s, _ in (non_bp or group)],
                boilerplate=bool(kinds) and not non_bp,
                coherence=coherence_score(" ".join(s.text for s, _ in group)),
            )
        )
    return _merge_soft_markers(units)
