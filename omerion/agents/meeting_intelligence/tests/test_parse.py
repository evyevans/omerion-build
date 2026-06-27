"""Unit tests for Meeting Intelligence helpers."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.meeting_intelligence.state import ConsultingProposal
from agents.meeting_intelligence.tools import _chunk_text, _parse_json


def test_parse_json_extracts_object_from_prose():
    raw = "here is json: {\"who\": [\"A\"], \"what\": \"x\"} trailing words"
    out = _parse_json(raw, {})
    assert out["who"] == ["A"]


def test_parse_json_extracts_array_from_prose():
    raw = "result: [{\"phase\": \"phase_1\"}]"
    out = _parse_json(raw, [])
    assert out == [{"phase": "phase_1"}]


def test_parse_json_returns_fallback_on_garbage():
    assert _parse_json("no braces here", "fallback") == "fallback"


def test_chunk_text_respects_target():
    text = "word " * 400
    chunks = _chunk_text(text, target=100)
    assert len(chunks) >= 3
    assert all(c.strip() for c in chunks)


def test_demo_reference_accepts_canonical_codenames():
    for demo in ("DAAM", "CAPA", "ASAP", "REMI"):
        assert ConsultingProposal(demo_reference=demo).demo_reference == demo


def test_demo_reference_rejects_retired_codenames():
    for retired in ("ORIA", "RORA"):
        with pytest.raises(ValidationError):
            ConsultingProposal(demo_reference=retired)


def test_consulting_proposal_carries_operator_archetype():
    p = ConsultingProposal(operator_archetype="high_velocity")
    assert p.operator_archetype == "high_velocity"
