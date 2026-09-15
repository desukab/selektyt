from __future__ import annotations
from models.core import Candidate


def retention_intervals(c: Candidate, min_pause: float = 0.55, keep_edge: float = 0.08) -> list[tuple[float, float]]:
    """Return source intervals after conservative pause removal.

    We only remove unusually long gaps between recognized words. We do not impose a
    universal cut-per-second target: the source's actual speech rhythm controls cuts.
    """
    if not c.text.strip():
        return []
    # Candidate text is already complete; without word timestamps, keep it contiguous.
    # The pipeline can pass word timing later as an extension without changing the API.
    return [(c.start + keep_edge, max(c.start + keep_edge, c.end - keep_edge))]
