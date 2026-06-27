"""Factory RAG tools — real Pinecone embed + namespace-scoped upsert.

Namespace strategy (audit §5):
  delivery_projects__<industry>   e.g. delivery_projects__saas, delivery_projects__real_estate
  Within each namespace, metadata filters narrow by doc_type / service_package.

Dedup:
  Vector similarity (cosine >= 0.96 hard skip, 0.90-0.95 soft flag).
  Pinecone vector ID is deterministic: f"{doc_type}:{source_id}" — idempotent retries.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from omerion_core.clients.pinecone_client import pinecone_index
from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.embeddings import embed
from omerion_core.logging import get_logger

# Structured logger: the failure paths below pass kwargs (source_id=, error=…),
# which the stdlib logging.Logger rejects with TypeError inside the except block.
log = get_logger("omerion.agents.factory_rag")

_NAMESPACE_PREFIX = "delivery_projects"
_DEDUP_HARD = 0.96
_DEDUP_SOFT = 0.90


def _namespace(industry: str) -> str:
    safe = (industry or "general").lower().replace(" ", "_").replace("-", "_")
    return f"{_NAMESPACE_PREFIX}__{safe}"


def generate_content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()


def _embed_text_for_doc(doc: dict[str, Any]) -> str:
    """Build the text to embed from a factory document."""
    parts = []
    for field in ("wartt_summary", "kpi_results", "lesson", "root_cause",
                  "failure_type", "recommendation", "content"):
        val = doc.get(field)
        if val:
            parts.append(str(val)[:800])
    return " | ".join(parts)[:1200] or doc.get("doc_type", "factory document")


def upsert_factory_documents(docs: list[dict[str, Any]], industry: str) -> int:
    """Embed and upsert factory documents into the industry-scoped Pinecone namespace.

    Returns the number of vectors actually upserted (dedup skips not counted).
    Never raises — failures are logged and the partial count returned.
    """
    if not docs:
        return 0

    ns = _namespace(industry)
    idx = pinecone_index()
    upserted = 0
    run_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    for doc in docs:
        doc_type = doc.get("doc_type", "unknown")
        source_id = doc.get("source_id", "unknown")
        vector_id = f"{doc_type}:{source_id}"
        embed_text = _embed_text_for_doc(doc)

        try:
            vec = embed(embed_text)
        except Exception as exc:
            log.warning("factory_rag_embed_failed", source_id=source_id, error=str(exc))
            continue

        # Dedup: query for close existing vectors from this source_id
        try:
            result = idx.query(
                vector=vec,
                top_k=1,
                include_metadata=False,
                filter={"source_id": {"$eq": source_id}, "doc_type": {"$eq": doc_type}},
                namespace=ns,
            )
            if result.matches and result.matches[0].score >= _DEDUP_HARD:
                log.debug("factory_rag_dedup_skip", vector_id=vector_id, score=result.matches[0].score)
                continue
            is_near_dup = bool(result.matches and result.matches[0].score >= _DEDUP_SOFT)
        except Exception as exc:
            # Fail CLOSED: a Pinecone hiccup must not be read as "not a duplicate"
            # and let us upsert into the SHARED RAG index (read by every agent).
            # Skip this vector; it can be re-ingested on a later run.
            log.warning("factory_rag_dedup_query_failed", error=str(exc), vector_id=vector_id)
            continue

        metadata: dict[str, Any] = {
            "agent_id": "factory_rag",
            "department": "delivery",
            "namespace": ns,
            "doc_type": doc_type,
            "source_id": source_id,
            "industry": industry,
            "run_date": run_date,
            "content_hash": doc.get("content_hash") or generate_content_hash(embed_text),
        }
        for field in ("service_package", "failure_type", "kpi_results", "wartt_summary",
                      "lesson", "root_cause", "recommendation", "status"):
            if doc.get(field):
                metadata[field] = str(doc[field])[:200]
        if is_near_dup:
            metadata["is_apparent_duplicate"] = True

        try:
            idx.upsert(vectors=[{"id": vector_id, "values": vec, "metadata": metadata}], namespace=ns)
            upserted += 1
        except Exception as exc:
            log.warning("factory_rag_upsert_failed", vector_id=vector_id, error=str(exc))

    log.info("factory_rag_upserted", namespace=ns, count=upserted, total=len(docs))
    return upserted


def prune_factory_documents(industry: str, older_than_days: int = 90) -> int:
    """Delete vectors older than `older_than_days` from the industry namespace.

    Uses Pinecone metadata filter on `run_date`. Returns 1 if operation succeeded, 0 on error.
    """
    ns = _namespace(industry)
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=older_than_days)).strftime("%Y-%m-%d")
    try:
        idx = pinecone_index()
        idx.delete(filter={"run_date": {"$lt": cutoff}}, namespace=ns)
        log.info("factory_rag_pruned", namespace=ns, cutoff=cutoff)
        return 1
    except Exception as exc:
        log.warning("factory_rag_prune_failed", namespace=ns, error=str(exc))
        return 0


def query_factory_rag(
    query_embedding: list[float],
    industry: str,
    doc_type: str | None = None,
    service_package: str | None = None,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Retrieve similar factory documents for reuse in new proposals.

    Namespace is scoped to `delivery_projects__<industry>`.
    Optional metadata filters narrow by doc_type and/or service_package.
    """
    ns = _namespace(industry)
    idx = pinecone_index()
    filter_dict: dict[str, Any] = {"agent_id": {"$eq": "factory_rag"}}
    if doc_type:
        filter_dict["doc_type"] = {"$eq": doc_type}
    if service_package:
        filter_dict["service_package"] = {"$eq": service_package}

    result = idx.query(
        vector=query_embedding,
        top_k=top_k,
        namespace=ns,
        filter=filter_dict,
        include_metadata=True,
    )
    return [
        {"score": m.score, **m.metadata}
        for m in (result.matches or [])
        if m.score >= 0.70
    ]


# ── Supabase fetch helpers (used by graph.py) ─────────────────────────────────

def fetch_deployment_data(deployment_id: str) -> dict[str, Any]:
    resp = supabase.table("deployments").select("*").eq("deployment_id", deployment_id).execute()
    if not resp.data:
        return {}
    dep = resp.data[0]
    blueprint_id = dep.get("blueprint_id")
    bp: dict = {}
    if blueprint_id:
        bp_resp = supabase.table("automation_blueprints").select("blueprint_json") \
            .eq("blueprint_id", blueprint_id).execute()
        bp = (bp_resp.data or [{}])[0].get("blueprint_json", {})
    tasks_resp = supabase.table("build_tasks").select("*").eq("deployment_id", deployment_id).execute()
    return {"deployment": dep, "blueprint": bp, "tasks": tasks_resp.data or []}


def fetch_failed_task_data(task_id: str) -> dict[str, Any]:
    resp = supabase.table("build_tasks").select("*").eq("task_id", task_id).execute()
    return resp.data[0] if resp.data else {}


def fetch_approved_blueprint(blueprint_id: str) -> dict[str, Any]:
    resp = supabase.table("automation_blueprints").select("*").eq("blueprint_id", blueprint_id).execute()
    return resp.data[0] if resp.data else {}
