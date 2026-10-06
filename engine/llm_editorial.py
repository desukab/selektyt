"""Optional LLM editorial metadata via Ollama. Never rewrites source speech.

The LLM is used only as a semantic/editorial enhancement layer. When Ollama
is unavailable, the pipeline continues normally with deterministic scoring.
"""
from __future__ import annotations
import json
import logging
from models.core import Candidate

# `requests` is the optional `llm` extra, not a base dependency — a base
# `pip install selekyt` has to be able to import this module, because
# `selekyt/pipeline.py` imports `attach_metadata` unconditionally at the top.
# Importing it eagerly meant a base install crashed on `selekyt --help` with
# ModuleNotFoundError before a single flag was parsed. The module-level name is
# kept (rather than importing inside `_ask_ollama`) so the tests that patch
# `llm.requests.post` keep resolving it; `None` is the "extra not installed"
# state and `_ask_ollama` turns it into the same failure every other Ollama
# problem becomes — empty metadata, pipeline continues.
try:
    import requests
except ImportError:  # pragma: no cover - exercised by the clean-install test
    requests = None

log = logging.getLogger(__name__)


def _ask_ollama(text: str, ollama_url: str, model: str = "llama3.2") -> dict:
    if requests is None:
        raise ImportError(
            "the `requests` package is required for LLM metadata; install it "
            "with `pip install 'selekyt[llm]'`. The pipeline continues without "
            "LLM metadata."
        )
    prompt = (
        "Analyze this video transcript and classify its narrative arc.\n"
        "Return ONLY a JSON object with these fields:\n"
        '- "arc": one of "setup-payoff"|"question-answer"|"tension-release"|"contrast"|"revelation"|"none"\n'
        '- "pacing": one of "fast"|"moderate"|"slow"\n'
        '- "hook_quality": one of "strong"|"moderate"|"weak"\n'
        '- "payoff_quality": one of "clear"|"partial"|"missing"\n'
        '- "explanation": a 1-2 sentence explanation of your classification\n\n'
        f"Transcript (first 500 chars):\n{text[:500]}"
    )
    resp = requests.post(
        f"{ollama_url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json().get("response", "")
    s = raw.find("{"); e = raw.rfind("}") + 1
    if s < 0 or e <= s:
        return {}
    return json.loads(raw[s:e])


def attach_metadata(
    c: Candidate, ollama_url: str | None, model: str = "llama3.2"
) -> Candidate:
    """Attach optional LLM-derived editorial metadata to a candidate.

    The LLM never rewrites, fabricates, or alters source speech text.
    If Ollama is unavailable or returns invalid output, metadata is set to {}
    and the pipeline continues normally.
    """
    if not ollama_url:
        return c
    try:
        meta = _ask_ollama(c.text, ollama_url, model)
    except Exception as exc:
        log.debug("LLM metadata failed: %s", exc)
        meta = {}
    c.llm_metadata = meta if isinstance(meta, dict) else {}
    return c
