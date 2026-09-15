from __future__ import annotations
import re
from models.core import Candidate


def validate(c: Candidate) -> tuple[bool, str]:
    if c.confidence < 0.75:
        return False, "low factual confidence"
    if c.context_score < 0.70:
        return False, "insufficient standalone context"
    # Prevent sensational hooks that are materially unsupported by the candidate text.
    if re.search(r"\b(nobody|everyone|always|never|the world|secret)\b", c.hook.lower()) and len(c.text.split()) < 35:
        return False, "absolute claim needs more context"
    return True, "ok"
