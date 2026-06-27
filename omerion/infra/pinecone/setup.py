"""Pinecone index bootstrap — idempotent.

Run once per environment:
    python -m infra.pinecone.setup

Creates the `omerion-legion-rag` serverless index (cosine, 512-dim to match
OpenAI `text-embedding-3-small` with `dimensions=512` parameter).

Namespaces (generalized for cross-industry AI automation):
    emails          — outbound/inbound email threads (all industries)
    transcripts     — meeting/call transcripts (chunked)
    dossiers        — prospect/company research (account-specific)
    playbooks       — sales/ops playbooks + best practices
    blueprints      — approved process templates
    rd_insights     — market research + competitive intelligence
    job_postings    — job descriptions + opportunities
    outreach_signals — interaction outcomes + RAG traction data
    knowledge-base  — reference docs (Drive Knowledge Base)

Standard metadata schema (required on every vector):
    - persona: "founder" | "investor" | "operator" | "team_member" | "customer" | "prospect"
    - industry: "general_b2b" | "saas" | "fintech" | "healthcare" | ... (any industry)
    - agent_type: agent that created this vector (market_watcher, lead_scraper, etc)
    - content_date: ISO 8601 timestamp
    - source_url: where the content came from (internal:// or external URL)

Namespace-specific metadata (optional, as needed):
    - dossiers: account_id, kind (summary|pain|hook)
    - rd_insights: impact_tag, source_type, priority
    - playbooks: playbook_type, use_case
    - job_postings: job_level, role_category, compensation_range
    - outreach_signals: signal_type, outcome, conversion_stage
"""
from __future__ import annotations

import time

from pinecone import Pinecone, ServerlessSpec

from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.infra.pinecone")

NAMESPACES = [
    "emails",
    "transcripts",
    "dossiers",
    "playbooks",
    "blueprints",
    "rd_insights",
    "job_postings",      # SEEK — job descriptions + Evykynn profile vector
    "outreach_signals",  # RAG Traction — per-interaction outcome signals
    "knowledge-base",    # KB pipeline — Google Drive Knowledge Base documents
]

EMBED_DIM = 512  # text-embedding-3-small with dimensions=512 (Matryoshka dim reduction)
METRIC = "cosine"


def ensure_index() -> None:
    pc = Pinecone(api_key=settings.pinecone_api_key)
    name = settings.pinecone_index

    existing = {i["name"] for i in pc.list_indexes()}
    if name in existing:
        log.info("pinecone_index_exists", index=name)
    else:
        log.info("pinecone_index_creating", index=name, dim=EMBED_DIM, metric=METRIC)
        pc.create_index(
            name=name,
            dimension=EMBED_DIM,
            metric=METRIC,
            spec=ServerlessSpec(
                cloud=settings.pinecone_cloud or "aws",
                region=settings.pinecone_region or "us-east-1",
            ),
        )
        while not pc.describe_index(name).status.get("ready"):
            time.sleep(1)
        log.info("pinecone_index_ready", index=name)

    index = pc.Index(name)

    # Warm namespaces with a zero-vector sentinel (upsert is idempotent by id).
    for ns in NAMESPACES:
        index.upsert(
            vectors=[
                {
                    "id": f"__init__:{ns}",
                    "values": [1.0] + [0.0] * (EMBED_DIM - 1),
                    "metadata": {
                        "persona": "system",
                        "industry": "system",
                        "agent_type": "bootstrap",
                        "content_date": "1970-01-01",
                        "source_url": "internal://bootstrap",
                    },
                }
            ],
            namespace=ns,
        )
        log.info("pinecone_namespace_warmed", index=name, namespace=ns)


if __name__ == "__main__":
    ensure_index()
