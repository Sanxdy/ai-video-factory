"""A VERIFIED claim must name its source — otherwise it is not verified."""
from __future__ import annotations

from apps.research.research_agent import _enforce_named_sources


def _f(status, source):
    return {"claim": "c", "status": status, "source": source}


def test_verified_with_named_source_survives():
    out = _enforce_named_sources([_f("VERIFIED", "NASA Solar System Exploration")])
    assert out[0]["status"] == "VERIFIED"
    assert out[0]["source"] == "NASA Solar System Exploration"


def test_verified_without_source_is_demoted():
    out = _enforce_named_sources([_f("VERIFIED", "")])
    assert out[0]["status"] == "UNVERIFIED"
    assert out[0]["source"] == ""


def test_verified_with_model_knowledge_is_demoted():
    for filler in ("model knowledge", "General Knowledge", "various sources", "n/a", "-"):
        out = _enforce_named_sources([_f("VERIFIED", filler)])
        assert out[0]["status"] == "UNVERIFIED", filler


def test_unverified_is_left_alone():
    out = _enforce_named_sources([_f("UNVERIFIED", "")])
    assert out[0]["status"] == "UNVERIFIED"


def test_input_is_not_mutated():
    facts = [_f("VERIFIED", "")]
    _enforce_named_sources(facts)
    assert facts[0]["status"] == "VERIFIED"
