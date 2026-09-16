"""Optional LLM editorial metadata via Ollama. Never rewrites source speech.

The LLM is used only as a semantic/editorial enhancement layer. When Ollama
is unavailable, the pipeline continues normally with deterministic scoring.
"""
from __future__ import annotations
import json
import logging
import requests
from models.core import Candidate

log = logging.getLogger(__name__)


def _ask_ollama(text: str, ollama_url: str, model: str = "llama3.2") -> dict:
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
