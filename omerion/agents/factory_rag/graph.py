"""Factory RAG LangGraph — ingest deployment outcomes into Pinecone for reuse.

Trigger types:
  success_ingest   — deployment succeeded; embed blueprint + KPI summary
  failure_ingest   — build task failed; embed root cause + lesson
  blueprint_ingest — blueprint approved but not yet deployed; embed for pre-deployment lookup
  maintenance      — prune vectors older than 90 days
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.graph import END, StateGraph

from omerion_core.events.bus import EventType, emit_event
from omerion_core.llm.json_extraction import extract_json_object
from omerion_core.llm.router import ClaudeRouter, Tier

from agents.factory_rag.prompts import FACTORY_RAG_FAILURE_SYSTEM, FACTORY_RAG_SUCCESS_SYSTEM
from agents.factory_rag.state import FactoryRAGState
from agents.factory_rag.tools import (
    fetch_approved_blueprint,
    fetch_deployment_data,
    fetch_failed_task_data,
    generate_content_hash,
    prune_factory_documents,
    upsert_factory_documents,
)

logger = logging.getLogger(__name__)


def classify_trigger(state: FactoryRAGState) -> dict[str, Any]:
    return {}


def route_trigger(state: FactoryRAGState) -> str:
    if state.get("trigger_type") in ("success_ingest", "failure_ingest", "blueprint_ingest", "maintenance"):
        return "prepare_documents"
    return END


def prepare_documents(state: FactoryRAGState) -> dict[str, Any]:
    trigger = state.get("trigger_type")
    source_id = state.get("source_id", "")
    industry = state.get("industry", "general")
    router = ClaudeRouter()
    docs: list[dict] = []

    if trigger == "success_ingest":
        data = fetch_deployment_data(source_id)
        if data:
            bp_json = json.dumps(data.get("blueprint", {}), indent=2)[:6000]
            resp = router.complete(
                system=FACTORY_RAG_SUCCESS_SYSTEM,
                prompt=f"Blueprint Data:\n{bp_json}",
                tier=Tier.DEFAULT,
                temperature=0.1,
                max_tokens=800,
            )
            summary, _ = extract_json_object(resp["text"])
            content = resp["text"]
            docs.append({
                "doc_type": "blueprint_template",
                "source_id": source_id,
                "industry": industry,
                "wartt_summary": summary.get("wartt_summary", ""),
                "kpi_results": summary.get("kpi_results", ""),
                "service_package": data.get("deployment", {}).get("service_package", ""),
                "content_hash": generate_content_hash(content),
            })

    elif trigger == "failure_ingest":
        data = fetch_failed_task_data(source_id)
        if data:
            resp = router.complete(
                system=FACTORY_RAG_FAILURE_SYSTEM,
                prompt=f"Failed Task Data:\n{json.dumps(data, indent=2)[:4000]}",
                tier=Tier.DEFAULT,
                temperature=0.1,
                max_tokens=600,
            )
            lesson, _ = extract_json_object(resp["text"])
            docs.append({
                "doc_type": "failure_lesson",
                "source_id": source_id,
                "industry": industry,
                "failure_type": lesson.get("failure_type", ""),
                "root_cause": lesson.get("root_cause", ""),
                "lesson": lesson.get("lesson", ""),
                "content_hash": generate_content_hash(resp["text"]),
            })

    elif trigger == "blueprint_ingest":
        data = fetch_approved_blueprint(source_id)
        if data:
            bp_json = data.get("blueprint_json", {})
            docs.append({
                "doc_type": "blueprint_template",
                "source_id": source_id,
                "industry": industry,
                "status": "pending_deployment",
                "content_hash": generate_content_hash(json.dumps(bp_json)),
            })

    return {"documents_to_ingest": docs}


def ingest_to_pinecone(state: FactoryRAGState) -> dict[str, Any]:
    trigger = state.get("trigger_type")
    industry = state.get("industry", "general")
    docs = state.get("documents_to_ingest", [])
    ingested = 0
    pruned = 0

    if trigger == "maintenance":
        pruned = prune_factory_documents(industry=industry, older_than_days=90)
    elif docs:
        ingested = upsert_factory_documents(docs, industry=industry)

    return {"ingested_count": ingested, "pruned_count": pruned}


def emit_updated(state: FactoryRAGState) -> dict[str, Any]:
    emit_event(EventType.FACTORY_PLAYBOOK_UPDATED, "factory-rag", {
        "trigger_type": state.get("trigger_type"),
        "source_id": state.get("source_id"),
        "ingested_count": state.get("ingested_count", 0),
        "pruned_count": state.get("pruned_count", 0),
    })
    return {}


def build() -> StateGraph:
    workflow = StateGraph(FactoryRAGState)
    workflow.add_node("classify_trigger", classify_trigger)
    workflow.add_node("prepare_documents", prepare_documents)
    workflow.add_node("ingest_to_pinecone", ingest_to_pinecone)
    workflow.add_node("emit_updated", emit_updated)
    workflow.set_entry_point("classify_trigger")
    workflow.add_conditional_edges("classify_trigger", route_trigger, {
        "prepare_documents": "prepare_documents",
        END: END,
    })
    workflow.add_edge("prepare_documents", "ingest_to_pinecone")
    workflow.add_edge("ingest_to_pinecone", "emit_updated")
    workflow.add_edge("emit_updated", END)
    return workflow.compile()
