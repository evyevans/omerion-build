"""LangGraph for Client Intake (Agent #12).

Flow:
    load_transcript
      → retrieve_similar_profiles
      → extract_profile           (retries up to 2x on parse failure)
      → validate_completeness
      → emit_profile
"""
from __future__ import annotations

import uuid
from typing import Any

from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from omerion_core.domain.wartt_schemas import ClientProfile
from omerion_core.events.bus import EventType, emit_event
from omerion_core.llm.json_extraction import extract_json_object
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from agents.client_intake.prompts import INTAKE_EXTRACTION_SYSTEM, INTAKE_USER_PROMPT
from agents.client_intake.state import IntakeState
from agents.client_intake.tools import (
    fetch_blueprint_data,
    persist_client_profile,
    query_similar_profiles,
)

log = get_logger("omerion.agents.client_intake")


@traced_node("load_transcript")
def load_transcript(state: IntakeState) -> dict[str, Any]:
    blueprint_id = state.blueprint_id
    if not blueprint_id:
        log.error("intake_blueprint_id_missing")
        return {"data_gaps": ["CRITICAL: blueprint_id missing"], "confidence_score": 0.0}

    transcript_text, founder_notes, _client_name = fetch_blueprint_data(blueprint_id)
    if not transcript_text:
        log.error("intake_transcript_empty", blueprint_id=blueprint_id)
        return {
            "transcript_text": "",
            "data_gaps": ["CRITICAL: transcript — insufficient data"],
            "confidence_score": 0.0,
        }

    return {
        "transcript_text": transcript_text,
        "founder_notes": founder_notes or "",
        "client_id": state.client_id or str(uuid.uuid4()),
    }


@traced_node("retrieve_similar_profiles")
def retrieve_similar_profiles(state: IntakeState) -> dict[str, Any]:
    transcript = state.transcript_text
    if not transcript:
        return {}
    profiles = query_similar_profiles(transcript[:500])
    return {"rag_similar_profiles": profiles}


@traced_node("extract_profile")
def extract_profile(state: IntakeState) -> dict[str, Any]:
    transcript = state.transcript_text
    if not transcript:
        return {}

    router = ClaudeRouter()
    resp = router.complete(
        tier=Tier.DEFAULT,
        system=INTAKE_EXTRACTION_SYSTEM,
        prompt=INTAKE_USER_PROMPT.format(
            transcript=transcript,
            founder_notes=state.founder_notes or "",
            rag_profiles=str(state.rag_similar_profiles) if state.rag_similar_profiles else "None",
        ),
        max_tokens=2000,
        temperature=0.0,
    )

    data, ok = extract_json_object(resp["text"])
    if not ok or not data:
        log.warning("intake_extraction_parse_failed", attempts=state.extraction_attempts)
        return {"extraction_attempts": state.extraction_attempts + 1}

    try:
        profile = ClientProfile(**{k: str(v) if isinstance(v, str) else v for k, v in data.items()})
    except (ValidationError, TypeError) as exc:
        log.warning("intake_profile_validation_failed", error=str(exc))
        return {"extraction_attempts": state.extraction_attempts + 1}

    return {"client_profile": profile}


@traced_node("validate_completeness")
def validate_completeness(state: IntakeState) -> dict[str, Any]:
    profile = state.client_profile
    if not profile:
        return {"data_gaps": ["CRITICAL: extraction_failed"], "confidence_score": 0.2}

    gaps: list[str] = []
    confidence = 1.0

    if not profile.pain_points:
        gaps.append("CRITICAL: pain_points — no pain points extracted")
        confidence -= 0.15

    if not profile.business_model or len(profile.business_model) < 20:
        gaps.append("CRITICAL: business_model — too vague")
        confidence -= 0.15

    if not getattr(profile, "tech_stack", None):
        gaps.append("OPTIONAL: tech_stack — client may not have mentioned tools")
        confidence -= 0.05

    if not getattr(profile, "budget_band", None):
        gaps.append("OPTIONAL: budget_band — no budget signal detected")
        confidence -= 0.05

    if not getattr(profile, "crm_system", None):
        gaps.append("OPTIONAL: crm_system — could not identify CRM")
        confidence -= 0.05

    existing = list(getattr(profile, "data_gaps", []) or [])
    all_gaps = list(dict.fromkeys(existing + gaps))

    return {
        "data_gaps": all_gaps,
        "confidence_score": max(0.0, confidence),
    }


@traced_node("emit_profile")
def emit_profile(state: IntakeState) -> dict[str, Any]:
    blueprint_id = state.blueprint_id
    client_id = state.client_id
    profile = state.client_profile
    confidence = state.confidence_score
    gaps = list(state.data_gaps)

    if profile and client_id:
        persist_client_profile(blueprint_id, client_id, profile.model_dump(), confidence)

        emit_event(
            EventType.CLIENT_PROFILE_READY,
            source_agent="client-intake",
            payload={
                "blueprint_id": blueprint_id,
                "client_id": client_id,
                "confidence_score": confidence,
            },
        )

        if gaps and confidence < 0.7:
            emit_event(
                EventType.CLIENT_INTAKE_GAPS_DETECTED,
                source_agent="client-intake",
                payload={
                    "blueprint_id": blueprint_id,
                    "client_id": client_id,
                    "data_gaps": gaps,
                    "confidence_score": confidence,
                },
            )

    return {}


def should_reprompt(state: IntakeState) -> str:
    if state.client_profile is None and state.extraction_attempts < 2:
        return "extract_profile"
    return "validate_completeness"


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer

    g = StateGraph(IntakeState)
    g.add_node("load_transcript", load_transcript)
    g.add_node("retrieve_similar_profiles", retrieve_similar_profiles)
    g.add_node("extract_profile", extract_profile)
    g.add_node("validate_completeness", validate_completeness)
    g.add_node("emit_profile", emit_profile)

    g.set_entry_point("load_transcript")
    g.add_edge("load_transcript", "retrieve_similar_profiles")
    g.add_edge("retrieve_similar_profiles", "extract_profile")
    g.add_conditional_edges(
        "extract_profile",
        should_reprompt,
        {"extract_profile": "extract_profile", "validate_completeness": "validate_completeness"},
    )
    g.add_edge("validate_completeness", "emit_profile")
    g.add_edge("emit_profile", END)

    return g.compile(checkpointer=get_checkpointer())
