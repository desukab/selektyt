from __future__ import annotations
import re
from collections import Counter
from models.core import Segment, Candidate

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


def make_thoughts(segments: list[Segment], min_sec: float = 6.0, max_sec: float = 55.0) -> list[Segment]:
    """Generate candidate clips respecting semantic boundaries.

    Only starts new candidates at sentence boundaries (after the previous
    segment ends with terminal punctuation) or at the very first segment.
    Falls back to starting at every segment if no boundary is found within
    max_sec of the current position, preserving original behaviour for
    edge cases.
    """
    clean = [s for s in segments if s.text.strip() and s.end > s.start]
    if not clean:
        return []

    thoughts: list[Segment] = []
    boundary_found = False
    for i in range(len(clean)):
        if _is_semantic_boundary(clean, i):
            boundary_found = True
        if not boundary_found and i > 0:
            continue

        cur: list[Segment] = []
        for s in clean[i:]:
            cur.append(s)
            duration = cur[-1].end - cur[0].start
            if duration > max_sec:
                break
            if duration >= min_sec and (_sentence_end(s.text) or duration >= max_sec - 2):
                thoughts.append(Segment(
                    cur[0].start, cur[-1].end,
                    " ".join(x.text for x in cur).strip(),
                    [w for x in cur for w in x.words],
                ))

    if not thoughts:
        for i in range(len(clean)):
            cur: list[Segment] = []
            for s in clean[i:]:
                cur.append(s)
                duration = cur[-1].end - cur[0].start
                if duration > max_sec:
                    break
                if duration >= min_sec and (_sentence_end(s.text) or duration >= max_sec - 2):
                    thoughts.append(Segment(
                        cur[0].start, cur[-1].end,
                        " ".join(x.text for x in cur).strip(),
                        [w for x in cur for w in x.words],
                    ))
    return thoughts[:4000]


def _score(s: Segment) -> tuple[float, float, float, float, str, str]:
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
    # Base formula identical to original; audio only ADDS a small adjustment
    # when timestamps are available, and is exactly zero otherwise.
    editorial = max(0.0, min(1.0, .24*density + .22*substance + .22*progression + .18*opening + .14*payoff - .30*filler - .12*(1 - standalone)))
    if audio_q:
        silence_ratio = audio_q["silence_ratio"]
        long_gap_ratio = audio_q["long_gap_ratio"]
        audio_pace = max(0.0, 1.0 - 0.8 * silence_ratio - 0.4 * long_gap_ratio)
        editing_adj = 0.05 * audio_pace - 0.08 * long_gap_ratio
        editorial = max(0.0, min(1.0, editorial + editing_adj))
    virality = max(0.0, min(1.0, .34*opening + .24*contrast + .18*questions + .14*numbers + .10*substance))
    context = min(1.0, .40 + .30*substance + .20*progression + .10*payoff - .12*(1 - standalone))
    visual = min(1.0, .35 + .20*numbers + .20*contrast + .15*questions + .10*(n >= 35) - .15*visual_dep)
    if contrast >= .5 or progression > .70: story_type = "conflict" if contrast >= 1 else "revelation"
    elif questions: story_type = "question"
    elif numbers: story_type = "fact"
    elif payoff > .70: story_type = "story"
    else: story_type = "insight"
    reason = (f"opening={opening:.2f}; progression={progression:.2f}; payoff={payoff:.2f}; "
              f"density={density:.2f}; fillers={int(filler*4)}; standalone={standalone:.2f}; "
              f"repetition={repetition:.2f}")
    if audio_q:
        silence_ratio = audio_q["silence_ratio"]
        long_gap_ratio = audio_q["long_gap_ratio"]
        audio_pace = max(0.0, 1.0 - 0.8 * silence_ratio - 0.4 * long_gap_ratio)
        reason += (f"; audio_pace={audio_pace:.2f}; "
                   f"silence={audio_q['silence_ratio']:.3f}")
    return editorial, virality, context, visual, story_type, reason


def score_candidate(s: Segment) -> Candidate:
    editorial, virality, context, visual, story_type, detail = _score(s)
    text = s.text.strip()
    first = re.split(r"(?<=[.!?।॥？！])\s+", text, maxsplit=1)[0]
    words = s.words or []
    dead_air = _dead_air_fraction(words, s.duration) if words else None
    if dead_air is not None:
        detail += f"; dead_air={dead_air:.2f}"
    return Candidate(
        s.start, s.end, text, story_type, first[:160], text[-220:],
        editorial, virality, context, .90,
        f"Structural editorial score: {detail}.",
        visual, s.words,
    )


def _similarity(a: str, b: str) -> float:
    aa = Counter(re.findall(r"\w+", a.lower(), re.UNICODE)); bb = Counter(re.findall(r"\w+", b.lower(), re.UNICODE))
    if not aa or not bb: return 0.0
    return sum((aa & bb).values()) / max(1, min(sum(aa.values()), sum(bb.values())))


def _hook_similarity(a: str, b: str) -> float:
    """Compare only the opening ~50 chars (hook region) of two candidates."""
    return _similarity(a[:50], b[:50])


def rank(thoughts: list[Segment], limit: int = 8) -> list[Candidate]:
    candidates = [score_candidate(s) for s in thoughts if 12 <= s.duration <= 55 and len(re.findall(r"\w+", s.text, re.UNICODE)) >= 20]
    candidates.sort(key=lambda c: (c.total_score, c.editorial_score, c.context_score, c.end-c.start), reverse=True)
    chosen: list[Candidate] = []; kinds: Counter[str] = Counter(); topic_tokens: Counter[str] = Counter()
    for c in candidates:
        if any(not (c.end <= x.start-4 or c.start >= x.end+4) for x in chosen): continue
        if any(_similarity(c.text, x.text) >= .78 for x in chosen): continue
        if any(_hook_similarity(c.text, x.text) >= .65 for x in chosen): continue
        c_tokens = Counter(re.findall(r"\w+", c.text.lower(), re.UNICODE))
        topic_overlap = sum((topic_tokens & c_tokens).values())
        if topic_overlap > 12 and len(chosen) < limit - 1: continue
        if kinds[c.story_type] >= 2 and len(chosen) < limit-1: continue
        chosen.append(c)
        kinds[c.story_type] += 1
        topic_tokens.update(c_tokens)
        if len(chosen) >= limit: break
    return chosen
