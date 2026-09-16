"""Optional LLM editorial metadata — Ollama response handling, fully mocked.

The contract: the LLM is an enhancement layer only. It must never rewrite
source speech, and any failure (Ollama down, malformed JSON, timeout) degrades
to empty metadata with the pipeline continuing normally.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import engine.llm_editorial as llm
from engine.llm_editorial import _ask_ollama, attach_metadata
from models.core import Candidate


class _FakeResponse:
    def __init__(self, payload: dict, text: str = ""):
        self._payload = payload
        self.text = text

    def raise_for_status(self):
        if "error" in self._payload:
            raise RuntimeError("ollama 500")

    def json(self):
        return self._payload


def _candidate() -> Candidate:
    return Candidate(
        0.0, 10.0, "We shipped the feature and the metric moved 40%.",
        "story", "the hook", "the payoff",
        editorial_score=0.8, virality_score=0.7, context_score=0.9,
        confidence=0.9, reason="t", visual_potential=0.8,
    )


def test_no_ollama_url_is_a_noop():
    c = _candidate()
    out = attach_metadata(c, None)
    assert out is c
    assert out.llm_metadata is None


def test_attacts_parsed_metadata(monkeypatch):
    captured = {}

    def fake_post(url, *, json, timeout):
        captured["url"] = url
        captured["body"] = json
        return _FakeResponse(
            {"response": 'Sure! {"arc": "setup-payoff", "pacing": "fast", '
                         '"hook_quality": "strong", "payoff_quality": "clear", '
                         '"explanation": "clear setup then payoff"}'},
        )

    monkeypatch.setattr(llm.requests, "post", fake_post)
    c = attach_metadata(_candidate(), "http://localhost:11434")
    assert captured["url"] == "http://localhost:11434/api/generate"
    assert c.llm_metadata["arc"] == "setup-payoff"
    assert c.llm_metadata["pacing"] == "fast"
    # source speech is untouched
    assert "We shipped the feature" in c.text


def test_extracts_json_from_chatty_response(monkeypatch):
    """The model often wraps JSON in prose; the extractor must find the object."""
    monkeypatch.setattr(
        llm.requests, "post",
        lambda *a, **k: _FakeResponse(
            {"response": "Here is my analysis:\n```json\n{\"arc\": \"revelation\"}\n```\nDone."},
        ),
    )
    c = attach_metadata(_candidate(), "http://localhost:11434")
    assert c.llm_metadata == {"arc": "revelation"}


def test_no_json_in_response_yields_empty_metadata(monkeypatch):
    monkeypatch.setattr(
        llm.requests, "post",
        lambda *a, **k: _FakeResponse({"response": "I could not classify this."}),
    )
    c = attach_metadata(_candidate(), "http://localhost:11434")
    assert c.llm_metadata == {}


def test_ollama_error_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(
        llm.requests, "post",
        lambda *a, **k: _FakeResponse({"error": "model not found"}),
    )
    c = attach_metadata(_candidate(), "http://localhost:11434")
    assert c.llm_metadata == {}


def test_connection_failure_degrades_gracefully(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("ollama not running")

    monkeypatch.setattr(llm.requests, "post", boom)
    c = attach_metadata(_candidate(), "http://localhost:11434")
    assert c.llm_metadata == {}
    assert "We shipped the feature" in c.text


def test_non_dict_payload_becomes_empty(monkeypatch):
    monkeypatch.setattr(
        llm.requests, "post",
        lambda *a, **k: _FakeResponse({"response": "[1, 2, 3]"}),
    )
    c = attach_metadata(_candidate(), "http://localhost:11434")
    assert c.llm_metadata == {}


def test_prompt_never_leaks_beyond_500_chars(monkeypatch):
    captured = {}

    def fake_post(url, *, json, timeout):
        captured["body"] = json
        return _FakeResponse({"response": "{}"})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    long = Candidate(0, 10, "x " * 500, "story", "h", "p", 0.5, 0.5, 0.5, 0.5, "t")
    attach_metadata(long, "http://localhost:11434")
    transcript = captured["body"]["prompt"]
    tail = transcript.split("Transcript (first 500 chars):\n", 1)[1]
    assert len(tail) <= 500
