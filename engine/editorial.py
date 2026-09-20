from __future__ import annotations
import re
from collections import Counter
from models.core import Segment, Candidate
from engine.context_guard import validate
from engine.edit_plan import align_span
from engine.story_units import (
    boilerplate_fraction,
    coherence_score,
    split_sentences,
    _classify,
    _containment,
    _content_tokens,
    _continues_prior,
    segment_story_units,
)

CONTRAST = r"\b(?:but|however|instead|actually|although|yet|rather|because|so|therefore|అయితే|కానీ|ఎందుకంటే|అందుకే|లెకిన|मगर|क्योंकि|इसलिए|ஆனால்|எனவே|ಆದರೆ|ಏಕೆಂದರೆ|কিন্তু|কারণ|তাই)\b"
HOOK = r"\b(?:why|how|what happened|the reason|the truth|first|worst|problem|mistake|changed|never|secret|surprising|actually|ఎందుకు|ఎలా|నిజం|సమస్య|తప్పు|మారింది|क्यों|कैसे|सच|समस्या|गलती|ஏன்|எப்படி|உண்மை|சிக்கல்|ಮಾರ್ಪು|কেন|কীভাবে|সত্য|সমস্যা)\b"
FILLER = r"\b(?:um|uh|erm|hmm|you know|i mean|అంటే|ఉమ్|హ్మ్|मतलब|यानी|அதாவது|ஹ்ம்)\b"

_CONNECTIVES_EDITORIAL = {
    "but", "however", "actually", "because", "so", "therefore", "and",
    "అయితే", "కానీ", "లెకిన", "ఎందుకంటే", "అందుకే",
    "लेकिन", "मगर", "क्योंकि", "इसलिए",
    "ஆனால்", "எனவே", "ಆದರೆ", "ಏಕೆಂದರೆ",
    "কিন্তু", "কারণ", "তাই",
}

_ANAPHORA_EDITORIAL = {
    "he", "she", "it", "they", "that", "this", "those", "these", "so",
    "అది", "ఇది", "వారు", "అంతా",
    "वह", "यह", "वे",
    "அது", "இது", "அவர்",
    "ಅದು", "ಇದು", "ಅವರು",
    "সে", "এটি", "তারা",
}

VISUAL_DEICTICS = r"\b(?:as you can see|look at this|right here|this one|see here|ఇలా|ఇక్కడ|చూడండి|ऐसे|देखो|இதைபார்|ಇಲ್ಲಿ ನೋಡಿ|এখানে দেখুন)\b"


def _sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?।॥？！]$", text.strip()))


def _is_semantic_boundary(segments: list[Segment], idx: int) -> bool:
    """Is position `idx` a good semantic boundary to start a candidate clip?

    A boundary exists at:
      - The very first segment (always valid)
      - After the previous segment ended with terminal punctuation (new sentence)
    """
    if idx == 0:
        return True
    return _sentence_end(segments[idx - 1].text)


def _standalone_score(text_lower: str) -> float:
    """1.0 = strong standalone; 0.0 = starts mid-conversation or with connective."""
    words = text_lower.split()
    if not words:
        return 0.0
    first = words[0]
    if first in _CONNECTIVES_EDITORIAL:
        return 0.0
    if first in _ANAPHORA_EDITORIAL and len(words) < 25:
        return 0.2
    return 1.0


def _repetition_score(tokens: list[str]) -> float:
    """0.0 = no internal repetition; 1.0 = heavily repeated text."""
    if len(tokens) < 4:
        return 0.0
    bigrams = [(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)]
    freq = Counter(bigrams)
    repeated = sum(cnt for cnt in freq.values() if cnt > 1)
    return min(1.0, repeated / max(1, len(bigrams)))


def _dead_air_fraction(words: list, duration: float, pause_threshold: float = 0.7) -> float:
    """Fraction of duration spent in long pauses (>= pause_threshold seconds)."""
    if duration <= 0 or len(words) < 2:
        return 0.0
    sorted_words = sorted(words, key=lambda w: w.start)
    silence = 0.0
    for i in range(1, len(sorted_words)):
        gap = sorted_words[i].start - sorted_words[i - 1].end
        if gap >= pause_threshold:
            silence += gap
    return min(1.0, silence / max(0.01, duration))


def _audio_quality_features(words: list, duration: float) -> dict | None:
    """Audio-derived pacing features from word timestamps.

    Returns None when word timestamps are unavailable (pipeline runs
    without audio analysis), so callers can fall back to neutral scoring.
    Features (each 0.0 → 1.0, higher is worse):
      - silence_ratio: total inter-word gap / clip duration
      - long_gap_ratio: fraction of gaps >= 0.7s
    """
    if not words or duration <= 0 or len(words) < 2:
        return None
    sorted_words = sorted(words, key=lambda w: w.start)
    gaps = [
        max(0.0, sorted_words[i].start - sorted_words[i - 1].end)
        for i in range(1, len(sorted_words))
    ]
    if not gaps:
        return None
    silence_ratio = min(1.0, sum(gaps) / max(0.01, duration))
    long_gap_ratio = min(1.0, sum(1 for g in gaps if g >= 0.7) / len(gaps))
    return {
        "silence_ratio": round(silence_ratio, 3),
        "long_gap_ratio": round(long_gap_ratio, 3),
        "gap_count": len(gaps),
    }


def _words_in(segs: list[Segment], start: float, end: float) -> list:
    """Words overlapping [start, end], clipped to it, for a span's timestamps."""
    out = []
    for seg in segs:
        if seg.end < start or seg.start > end:
            continue
        for w in seg.words or []:
            if w.end <= start or w.start >= end:
                continue
            out.append(w)
    return out


def _spans_from_unit(unit, segs: list[Segment], min_sec: float, max_sec: float) -> list[Segment]:
    """Every candidate span starting inside one story unit.

    A span may run past the unit's end into the next one — crossing a weak
    boundary is allowed and penalised later by the coherence score, since a
    lexically diverse paragraph and a genuine story splice are not the same
    thing. A span may never include a boilerplate sentence: bylines and
    section markers are cut points, not content.
    """
    sents = unit.sentences
    out: list[Segment] = []
    for i in range(len(sents)):
        cur: list = []
        for s in sents[i:]:
            if s.boilerplate:
                break
            cur.append(s)
            duration = cur[-1].end - cur[0].start
            if duration > max_sec:
                break
            if duration >= min_sec and (_sentence_end(s.text) or duration >= max_sec - 2):
                out.append(Segment(
                    cur[0].start, cur[-1].end,
                    " ".join(x.text for x in cur).strip(),
                    _words_in(segs, cur[0].start, cur[-1].end),
                ))
    return out


def _completeness(unit, thought: Segment) -> float:
    """1.0 = the clip opens and closes its story; low = it cuts a flowing thought.

    Both ends matter. A clip that ends mid-sentence cuts the payoff; one that
    *starts* mid-sentence inherits a subject it never introduced, which is
    the more visible defect — the viewer is dropped into a clause. Ending
    early is only penalised when the next sentence continues the same topic;
    starting late is penalised whenever the first sentence is a continuation
    fragment of the one before it.
    """
    score = 1.0
    if thought.end < unit.end - 0.05:
        rest = [s for s in unit.sentences if s.start >= thought.end - 0.05]
        if rest:
            pieces = split_sentences(thought.text)
            last = pieces[-1] if pieces else thought.text
            if _continues_prior(rest[0].text, last):
                score = min(score, 0.35)
            else:
                continuity = _containment(_content_tokens(last), _content_tokens(rest[0].text))
                if continuity >= 0.30:
                    score = min(score, 0.35)
                else:
                    score = min(score, 0.75)
    # A clip that does not open its unit must still open a complete thought:
    # its first sentence must not be a continuation of the preceding one.
    if thought.start > unit.start + 0.05:
        before = [s for s in unit.sentences if s.end <= thought.start + 0.05]
        pieces = split_sentences(thought.text)
        first = pieces[0] if pieces else thought.text
        if before and _continues_prior(first, before[-1].text):
            score = min(score, 0.35)
    return score


def make_thoughts(segments: list[Segment], min_sec: float = 6.0, max_sec: float = 55.0) -> list[Segment]:
    """Generate candidate clips over the whole transcript, sentence by sentence.

    Story units (see engine.story_units) are computed first and supply each
    candidate's coherence and completeness, but they do not hard-wall the
    search: a span may cross a weak boundary and let its coherence score
    argue against it, because a lexically diverse paragraph is not a splice.
    Boilerplate sentences are the one hard stop — a byline is never clip
    content.
    """
    clean = [s for s in segments if s.text.strip() and s.end > s.start]
    if not clean:
        return []

    units = segment_story_units(clean)
    # A segment can sit in two units when a boundary is drawn inside it (the
    # marker peel splits one segment's sentences across units). Collecting
    # words per unit would then emit those words twice and the caption track
    # would show a ghost cue, so the pool is deduplicated by index.
    seen: set[int] = set()
    all_segs: list[Segment] = []
    for u in units:
        for i in u.segment_indices:
            if 0 <= i < len(clean) and i not in seen:
                seen.add(i)
                all_segs.append(clean[i])
    sentences: list = []
    for u in units:
        for s in u.sentences:
            sentences.append((s, u))
    if not sentences:
        return []

    thoughts: list[Segment] = []
    for i in range(len(sentences)):
        cur: list = []
        for s, u in sentences[i:]:
            if s.boilerplate:
                break
            cur.append(s)
            duration = cur[-1].end - cur[0].start
            if duration > max_sec:
                break
            if duration >= min_sec and (_sentence_end(s.text) or duration >= max_sec - 2):
                text = " ".join(x.text for x in cur).strip()
                # A clip crossing a unit boundary is judged on both ends by
                # the units it actually spans: the opening unit decides
                # whether it starts inside a story, the closing one whether
                # it finishes one.
                thoughts.append(Segment(
                    cur[0].start, cur[-1].end, text,
                    _words_in(all_segs, cur[0].start, cur[-1].end),
                    coherence=coherence_score(text),
                    completeness=min(
                        _completeness(sentences[i][1], Segment(cur[0].start, cur[-1].end, text, [])),
                        _completeness(u, Segment(cur[0].start, cur[-1].end, text, [])),
                    ),
                ))
    return thoughts[:4000]


def _score(s: Segment) -> tuple[float, float, float, float, str, str, float]:
    text = s.text.strip(); low = text.lower()
    tokens = re.findall(r"\w+", low, flags=re.UNICODE)
    n = max(1, len(tokens)); duration = max(0.1, s.duration)
    sentences = len(re.findall(r"[.!?।॥？！]", text))
    hook = min(1.0, len(re.findall(HOOK, low, re.I)) / 2)
    contrast = min(1.0, len(re.findall(CONTRAST, low, re.I)) / 2)
    filler = min(1.0, len(re.findall(FILLER, low, re.I)) / 4)
    numbers = min(1.0, len(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", text)) / 2)
    questions = 1.0 if re.search(r"[?？！]", text) else 0.0
    density = min(1.0, (n / duration) / 2.8)
    substance = min(1.0, n / 70)
    uniqueness = len(set(tokens)) / n
    progression = min(1.0, .35*min(sentences/3,1) + .35*contrast + .30*min(uniqueness/.72,1))
    opening = min(1.0, .45*hook + .25*questions + .30*min(n/24,1))
    payoff = min(1.0, .45*(1 if sentences >= 2 else .45) + .35*substance + .20*(1 if _sentence_end(text) else .25))
    standalone = _standalone_score(low)
    repetition = _repetition_score(tokens)
    visual_dep = min(1.0, len(re.findall(VISUAL_DEICTICS, low, re.I)) / 1)
    audio_q = _audio_quality_features(s.words or [], duration)

    # Editorial coherence: one story stays on topic, a splice of two
    # collapses at the seam. This scales the whole score so a clip that
    # wanders cannot rank on density alone.
    # `or 1.0` must not absorb a measured zero: a splice scores 0.0
    # deliberately, and rewriting it as perfect coherence defeats the
    # whole signal. Missing means unknown, which is 1.0 by default.
    coherence = getattr(s, "coherence", None)
    coherence = 1.0 if coherence is None else float(coherence)
    coherence_factor = 0.35 + 0.65 * coherence
    # A clip that cuts a flowing thought short is unfinished, however well
    # its text scores; closing the story is worth keeping.
    completeness = getattr(s, "completeness", None)
    completeness = 1.0 if completeness is None else float(completeness)
    completeness_factor = 0.45 + 0.55 * completeness
    # Boilerplate (byline, issue, dateline) is the article's colophon, not
    # its content; it costs editorial value but cannot simply be deleted,
    # because the audio still has to be cut somewhere.
    bp_frac, bp_kinds = boilerplate_fraction(text)

    # Base formula identical to original; audio only ADDS a small adjustment
    # when timestamps are available, and is exactly zero otherwise.
    editorial = max(0.0, min(1.0, .24*density + .22*substance + .22*progression + .18*opening + .14*payoff - .30*filler - .12*(1 - standalone)))
    if audio_q:
        silence_ratio = audio_q["silence_ratio"]
        long_gap_ratio = audio_q["long_gap_ratio"]
        audio_pace = max(0.0, 1.0 - 0.8 * silence_ratio - 0.4 * long_gap_ratio)
        editing_adj = 0.05 * audio_pace - 0.08 * long_gap_ratio
        editorial = max(0.0, min(1.0, editorial + editing_adj))
    editorial = max(0.0, min(1.0, editorial * coherence_factor * completeness_factor * (1.0 - 1.5 * bp_frac)))
    virality = max(0.0, min(1.0, .34*opening + .24*contrast + .18*questions + .14*numbers + .10*substance))
    # Context asks "can this stand alone?" — a clip that fills its screen time
    # has enough material to be self-supporting even when delivery is slow,
    # so duration floors the substance estimate that context uses. Editorial
    # density still uses the word-count term above, where sparseness matters.
    substance_ctx = min(1.0, max(substance, duration / 30.0))
    completeness_ctx = getattr(s, "completeness", None)
    completeness_ctx = 1.0 if completeness_ctx is None else float(completeness_ctx)
    context = min(1.0, .40 + .30*substance_ctx + .20*progression + .10*payoff - .12*(1 - standalone) + .10*completeness_ctx)
    visual = min(1.0, .35 + .20*numbers + .20*contrast + .15*questions + .10*(n >= 35) - .15*visual_dep)
    if contrast >= .5 or progression > .70: story_type = "conflict" if contrast >= 1 else "revelation"
    elif questions: story_type = "question"
    elif numbers: story_type = "fact"
    elif payoff > .70: story_type = "story"
    else: story_type = "insight"
    reason = (f"opening={opening:.2f}; progression={progression:.2f}; payoff={payoff:.2f}; "
              f"density={density:.2f}; fillers={int(filler*4)}; standalone={standalone:.2f}; "
              f"repetition={repetition:.2f}; coherence={coherence:.2f}; "
              f"completeness={completeness:.2f}"
              + (f"; boilerplate={bp_frac:.2f}({','.join(bp_kinds)})" if bp_kinds else ""))
    if audio_q:
        silence_ratio = audio_q["silence_ratio"]
        long_gap_ratio = audio_q["long_gap_ratio"]
        audio_pace = max(0.0, 1.0 - 0.8 * silence_ratio - 0.4 * long_gap_ratio)
        reason += (f"; audio_pace={audio_pace:.2f}; "
                   f"silence={audio_q['silence_ratio']:.3f}")
    return editorial, virality, context, visual, story_type, reason, coherence, completeness


def _clean_hook_payoff(text: str) -> tuple[str, str]:
    """Hook and payoff drawn from real content, skipping boilerplate.

    A byline or dateline must never be offered as the payoff of a clip even
    when it happens to be the last sentence the transcript produced. ASR
    often omits sentence-final punctuation, leaving one long unpunctuated
    span; in that case the hook opens the clip and the payoff closes it,
    rather than both being the same string.
    """
    pieces = split_sentences(text)
    clean = [p for p in pieces if not _classify(p)]
    if not clean:
        clean = [text.strip()]
    body = " ".join(clean).strip()
    if len(clean) == 1:
        cut = min(120, max(20, len(body) // 3))
        return body[:cut], body[-220:]
    return clean[0][:160], clean[-1][-220:]


def score_candidate(s: Segment) -> Candidate:
    # The span is aligned before it is scored, so the score, the hook, the
    # text and the shipped interval all describe the same clip. Without this
    # a candidate whose opening word is snapped into the cut is scored and
    # hooked on a text that omits it. See edit_plan.align_span.
    start, end, text = align_span(s.start, s.end, s.words or [], s.text.strip())
    aligned = Segment(start, end, text, s.words, s.coherence, s.completeness)
    editorial, virality, context, visual, story_type, detail, coherence, completeness = _score(aligned)
    hook, payoff = _clean_hook_payoff(text)
    words = aligned.words or []
    dead_air = _dead_air_fraction(words, aligned.duration) if words else None
    if dead_air is not None:
        detail += f"; dead_air={dead_air:.2f}"
    return Candidate(
        start, end, text, story_type, hook, payoff,
        editorial, virality, context, .90,
        f"Structural editorial score: {detail}.",
        visual, words, coherence, getattr(s, "completeness", 1.0),
    )


def _similarity(a: str, b: str) -> float:
    aa = Counter(re.findall(r"\w+", a.lower(), re.UNICODE)); bb = Counter(re.findall(r"\w+", b.lower(), re.UNICODE))
    if not aa or not bb: return 0.0
    return sum((aa & bb).values()) / max(1, min(sum(aa.values()), sum(bb.values())))


def _hook_similarity(a: str, b: str) -> float:
    """Compare only the opening ~50 chars (hook region) of two candidates."""
    return _similarity(a[:50], b[:50])


def rank(thoughts: list[Segment], limit: int = 8) -> list[Candidate]:
    # 8s is a complete short-form beat; below that a clip cannot establish a
    # setup and payoff, so the floor sits there rather than at 12s.
    candidates = [score_candidate(s) for s in thoughts if 8 <= s.duration <= 55 and len(re.findall(r"\w+", s.text, re.UNICODE)) >= 20]
    candidates.sort(key=lambda c: (c.total_score, c.editorial_score, c.context_score, c.end-c.start), reverse=True)
    chosen: list[Candidate] = []; kinds: Counter[str] = Counter(); topic_tokens: Counter[str] = Counter()
    for c in candidates:
        # A candidate that cannot stand alone must not claim a window: it
        # would block the valid sub-span next to it and then be discarded
        # by the caller's filter, leaving the slot empty by construction
        # rather than by editorial judgement.
        ok, _why = validate(c)
        if not ok:
            continue
        if any(not (c.end <= x.start-4 or c.start >= x.end+4) for x in chosen): continue
        if any(_similarity(c.text, x.text) >= .78 for x in chosen): continue
        if any(_hook_similarity(c.text, x.text) >= .65 for x in chosen): continue
        # Content tokens only: raw word counts are dominated by "the" and
        # "of", which starves a single-topic source — every candidate of a
        # documentary looks like a duplicate of every other one.
        c_tokens = Counter(_content_tokens(c.text))
        topic_overlap = sum((topic_tokens & c_tokens).values())
        if topic_overlap > 12 and len(chosen) < limit - 1: continue
        if kinds[c.story_type] >= 2 and len(chosen) < limit-1: continue
        chosen.append(c)
        kinds[c.story_type] += 1
        topic_tokens.update(c_tokens)
        if len(chosen) >= limit: break
    return chosen
