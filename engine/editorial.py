from __future__ import annotations
import re
from models.core import Segment, Candidate

HOOK_PATTERNS = [
    r"\bbut\b", r"\bhowever\b", r"\bactually\b", r"\bthe truth is\b",
    r"\bwhy\b", r"\bhow\b", r"\bno one\b", r"\bnobody\b", r"\bsecret\b",
    r"\bmistake\b", r"\bproblem\b", r"\bchanged\b", r"\bfirst\b", r"\bworst\b",
]
EMOTION = ["shocking", "surprising", "crazy", "dangerous", "failed", "fear", "love", "hate", "never", "impossible"]


def _sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?।]$", text.strip()))


def make_thoughts(segments: list[Segment], min_sec: float = 8.0, max_sec: float = 75.0) -> list[Segment]:
    thoughts: list[Segment] = []
    cur: list[Segment] = []
    for s in segments:
        cur.append(s)
        duration = cur[-1].end - cur[0].start
        boundary = _sentence_end(s.text) and duration >= min_sec
        if boundary or duration >= max_sec:
            thoughts.append(Segment(cur[0].start, cur[-1].end, " ".join(x.text for x in cur).strip(), [w for x in cur for w in x.words]))
            cur = []
    if cur:
        thoughts.append(Segment(cur[0].start, cur[-1].end, " ".join(x.text for x in cur).strip(), [w for x in cur for w in x.words]))
    return thoughts


def score_candidate(s: Segment) -> Candidate:
    text = s.text.strip()
    low = text.lower()
    hook_hits = sum(bool(re.search(p, low)) for p in HOOK_PATTERNS)
    emotion_hits = sum(word in low for word in EMOTION)
    numbers = len(re.findall(r"\b\d+(?:\.\d+)?%?\b", text))
    question = 1 if "?" in text else 0
    contrast = 1 if re.search(r"\bbut\b|\bhowever\b|\binstead\b|\bactually\b", low) else 0
    filler = len(re.findall(r"\b(um|uh|you know|like)\b", low))
    words = max(1, len(text.split()))
    density = min(1.0, words / max(1.0, s.duration * 2.8))
    editorial = min(1.0, 0.32 * density + 0.20 * min(hook_hits / 3, 1) + 0.18 * contrast + 0.15 * min(numbers / 2, 1) + 0.15 * min(emotion_hits / 2, 1) - 0.10 * min(filler / 4, 1))
    virality = min(1.0, 0.30 * min(hook_hits / 3, 1) + 0.20 * question + 0.20 * contrast + 0.15 * min(emotion_hits / 2, 1) + 0.15 * min(numbers / 2, 1))
    context = 0.95 if len(text.split()) >= 28 else 0.72
    visual = min(1.0, 0.45 + 0.15 * contrast + 0.15 * min(numbers / 2, 1) + 0.25 * (1 if any(k in low for k in ["show", "see", "look", "map", "before", "after", "inside", "built"]) else 0))
    story_type = "contradiction" if contrast else "revelation" if hook_hits else "insight"
    hook = text.split(". ")[0][:160]
    payoff = text[-180:]
    reason = "Strong opening/tension/payoff structure with sufficient context."
    return Candidate(s.start, s.end, text, story_type, hook, payoff, editorial, virality, context, 0.9, reason, visual)


def rank(thoughts: list[Segment], limit: int = 8) -> list[Candidate]:
    candidates = [score_candidate(s) for s in thoughts if 12 <= s.duration <= 75 and len(s.text.split()) >= 20]
    candidates.sort(key=lambda c: c.total_score, reverse=True)
    chosen: list[Candidate] = []
    for c in candidates:
        if any(not (c.end <= x.start - 3 or c.start >= x.end + 3) for x in chosen):
            continue
        chosen.append(c)
        if len(chosen) >= limit:
            break
    return chosen
