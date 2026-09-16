from __future__ import annotations
import re
from collections import Counter
from models.core import Segment, Candidate

CONTRAST = r"\b(?:but|however|instead|actually|although|yet|rather|because|so|therefore|అయితే|కానీ|ఎందుకంటే|అందుకే|लेकिन|मगर|क्योंकि|इसलिए|ஆனால்|எனவே|ಆದರೆ|ಏಕೆಂದರೆ|কিন্তু|কারণ|তাই)\b"
HOOK = r"\b(?:why|how|what happened|the reason|the truth|first|worst|problem|mistake|changed|never|secret|surprising|actually|ఎందుకు|ఎలా|నిజం|సమస్య|తప్పు|మారింది|क्यों|कैसे|सच|समस्या|गलती|ஏன்|எப்படி|உண்மை|சிக்கல்|ಮಾರ್ಪು|কেন|কীভাবে|সত্য|সমস্যা)\b"
FILLER = r"\b(?:um|uh|erm|hmm|you know|i mean|అంటే|ఉమ్|హ్మ్|मतलब|यानी|அதாவது|ஹ்ம்)\b"


def _sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?।॥？！]$", text.strip()))


def make_thoughts(segments: list[Segment], min_sec: float = 6.0, max_sec: float = 55.0) -> list[Segment]:
    clean = [s for s in segments if s.text.strip() and s.end > s.start]
    thoughts: list[Segment] = []
    for i in range(len(clean)):
        cur: list[Segment] = []
        for s in clean[i:]:
            cur.append(s)
            duration = cur[-1].end - cur[0].start
            if duration > max_sec:
                break
            if duration >= min_sec and (_sentence_end(s.text) or duration >= max_sec - 2):
                thoughts.append(Segment(cur[0].start, cur[-1].end, " ".join(x.text for x in cur).strip(), [w for x in cur for w in x.words]))
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
    editorial = max(0.0, min(1.0, .24*density + .22*substance + .22*progression + .18*opening + .14*payoff - .30*filler))
    virality = max(0.0, min(1.0, .34*opening + .24*contrast + .18*questions + .14*numbers + .10*substance))
    context = min(1.0, .40 + .30*substance + .20*progression + .10*payoff)
    visual = min(1.0, .35 + .20*numbers + .20*contrast + .15*questions + .10*(n >= 35))
    if contrast >= .5 or progression > .70: story_type = "conflict" if contrast >= 1 else "revelation"
    elif questions: story_type = "question"
    elif numbers: story_type = "fact"
    elif payoff > .70: story_type = "story"
    else: story_type = "insight"
    reason = f"opening={opening:.2f}; progression={progression:.2f}; payoff={payoff:.2f}; density={density:.2f}; fillers={int(filler*4)}"
    return editorial, virality, context, visual, story_type, reason


def score_candidate(s: Segment) -> Candidate:
    editorial, virality, context, visual, story_type, detail = _score(s)
    text = s.text.strip()
    first = re.split(r"(?<=[.!?।॥？！])\s+", text, maxsplit=1)[0]
    return Candidate(s.start, s.end, text, story_type, first[:160], text[-220:], editorial, virality, context, .90, f"Structural editorial score: {detail}.", visual, s.words)


def _similarity(a: str, b: str) -> float:
    aa = Counter(re.findall(r"\w+", a.lower(), re.UNICODE)); bb = Counter(re.findall(r"\w+", b.lower(), re.UNICODE))
    if not aa or not bb: return 0.0
    return sum((aa & bb).values()) / max(1, min(sum(aa.values()), sum(bb.values())))


def rank(thoughts: list[Segment], limit: int = 8) -> list[Candidate]:
    candidates = [score_candidate(s) for s in thoughts if 12 <= s.duration <= 55 and len(re.findall(r"\w+", s.text, re.UNICODE)) >= 20]
    candidates.sort(key=lambda c: (c.total_score, c.editorial_score, c.context_score, c.end-c.start), reverse=True)
    chosen: list[Candidate] = []; kinds: Counter[str] = Counter()
    for c in candidates:
        if any(not (c.end <= x.start-4 or c.start >= x.end+4) for x in chosen): continue
        if any(_similarity(c.text, x.text) >= .78 for x in chosen): continue
        if kinds[c.story_type] >= 2 and len(chosen) < limit-1: continue
        chosen.append(c); kinds[c.story_type] += 1
        if len(chosen) >= limit: break
    return chosen
