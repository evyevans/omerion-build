from typing import TypedDict, List, Dict, Any


class FactoryRAGState(TypedDict):
    """LangGraph state for the FACTORY RAG agent."""
    trigger_type: str            # "success_ingest" | "failure_ingest" | "blueprint_ingest" | "maintenance"
    source_id: str               # deployment_id, task_id, or blueprint_id
    industry: str                # namespace routing key — e.g. "saas", "real_estate", "general"
    documents_to_ingest: List[Dict[str, Any]]
    ingested_count: int
    pruned_count: int
