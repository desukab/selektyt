from __future__ import annotations
import re
from models.core import Segment, Candidate

HOOK_PATTERNS = [r"\bbut\b", r"\bhowever\b", r"\bactually\b", r"\bthe truth is\b", r"\bwhy\b", r"\bhow\b", r"\bno one\b", r"\bnobody\b", r"\bsecret\b", r"\bmistake\b", r"\bproblem\b", r"\bchanged\b", r"\bfirst\b", r"\bworst\b"]
EMOTION = ["shocking", "surprising", "crazy", "dangerous", "failed", "fear", "love", "hate", "never", "impossible"]


def _sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?।॥]$", text.strip()))


def make_thoughts(segments: list[Segment], min_sec: float = 6.0, max_sec: float = 55.0) -> list[Segment]:
    """Create overlapping editorial windows instead of one-shot sentence buckets.

    A long-form interview often contains the hook in one sentence and the payoff in the
    next few sentences. Windows therefore overlap so ranking can discover a complete story.
    """
    clean = [s for s in segments if s.text.strip() and s.end > s.start]
    thoughts: list[Segment] = []
    for i, start_seg in enumerate(clean):
        cur: list[Segment] = []
        for s in clean[i:]:
            cur.append(s)
            duration = cur[-1].end - cur[0].start
            if duration > max_sec:
                break
            if duration >= min_sec and (_sentence_end(s.text) or duration >= max_sec - 2):
                thoughts.append(Segment(cur[0].start, cur[-1].end, " ".join(x.text for x in cur).strip(), [w for x in cur for w in x.words]))
    # Keep useful windows; cap pathological candidate explosion deterministically.
    return thoughts[:4000]


def _score(s: Segment) -> tuple[float, float, float, float, str]:
    text = s.text.strip(); low = text.lower()
    hook_hits = sum(bool(re.search(p, low)) for p in HOOK_PATTERNS)
    emotion_hits = sum(word in low for word in EMOTION)
    numbers = len(re.findall(r"\b\d+(?:\.\d+)?%?\b", text))
    question = 1 if "?" in text else 0
    contrast = 1 if re.search(r"\bbut\b|\bhowever\b|\binstead\b|\bactually\b", low) else 0
    filler = len(re.findall(r"\b(um|uh|you know|like)\b", low))
    words = max(1, len(text.split()))
    density = min(1.0, words / max(1.0, s.duration * 2.8))
    editorial = max(0.0, min(1.0, 0.34*density + 0.18*min(hook_hits/3,1) + 0.18*contrast + 0.15*min(numbers/2,1) + 0.15*min(emotion_hits/2,1) - 0.10*min(filler/4,1)))
    virality = min(1.0, 0.27*min(hook_hits/3,1) + 0.18*question + 0.22*contrast + 0.18*min(emotion_hits/2,1) + 0.15*min(numbers/2,1))
    context = min(1.0, 0.60 + min(words/55, 1.0)*0.40)
    visual = min(1.0, 0.40 + 0.15*contrast + 0.15*min(numbers/2,1) + 0.30*(1 if any(k in low for k in ["show","see","look","map","before","after","inside","built","video","picture"]) else 0))
    story_type = "contradiction" if contrast else "question" if question else "revelation" if hook_hits else "insight"
    return editorial, virality, context, visual, story_type


def score_candidate(s: Segment) -> Candidate:
    editorial, virality, context, visual, story_type = _score(s)
    text = s.text.strip()
    # The hook is a label for ranking/reporting; source speech itself is never rewritten here.
    first = re.split(r"(?<=[.!?।॥])\s+", text, maxsplit=1)[0]
    hook = first[:160]
    payoff = text[-220:]
    return Candidate(s.start, s.end, text, story_type, hook, payoff, editorial, virality, context, 0.9, "Candidate contains an identifiable hook/tension signal and enough source context for review.", visual, s.words)


def rank(thoughts: list[Segment], limit: int = 8) -> list[Candidate]:
    candidates = [score_candidate(s) for s in thoughts if 12 <= s.duration <= 55 and len(s.text.split()) >= 20]
    candidates.sort(key=lambda c: (c.total_score, c.context_score, c.end-c.start), reverse=True)
    chosen: list[Candidate] = []
    for c in candidates:
        # Avoid near-duplicates but permit distinct stories from the same region.
        if any(not (c.end <= x.start - 4 or c.start >= x.end + 4) for x in chosen):
            continue
        chosen.append(c)
        if len(chosen) >= limit:
            break
    return chosen
