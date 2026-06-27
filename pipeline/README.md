# Knowledge Base Ingestion Pipeline

Automated RAG pipeline that watches the Google Drive "Knowledge Base" folder,
chunks and embeds new/updated documents, and upserts them into Pinecone
(`knowledge-base` namespace) and/or Supabase pgvector (`document_chunks` table).

---

## Architecture

```
Google Drive "Knowledge Base" folder
        │  (Drive Push Notification — expires every 7 days, auto-renewed)
        ▼
POST /webhooks/drive        ← Bearer token auth (OMERION_WEBHOOK_TOKEN)
  FastAPI BackgroundTask
        │
        ▼
KnowledgeBaseIngestionPipeline.run(file_id, event_type)
        │
        ├─► FileExtractor          gdoc / pdf / docx / txt → plain text
        │
        ├─► DocumentIndex.check()  sha256 hash comparison → skip if unchanged
        │
        ├─► DocumentChunker        512-token chunks, 50-token overlap (tiktoken cl100k_base)
        │       └─ code blocks preserved intact
        │
        ├─► EmbeddingGenerator     embed_batch() in groups of 100
        │       └─ reuses omerion_core.llm.embeddings (no separate OpenAI client)
        │
        └─► VectorStoreUpserter
                ├─ PineconeUpserter  → omerion-rag index, namespace=knowledge-base
                └─ SupabaseUpserter  → document_chunks table (pgvector 1536-dim)
                       ↓
               document_index updated (status, hash, chunk_count)
```

**Deletion path:**
```
Drive trash/remove event → VectorStoreUpserter.delete_file() → document_index.delete()
```

---

## File Layout

> All Python source lives inside `omerion/pipeline/` (not project root `pipeline/`)
> because the server runs from `omerion/` as the working directory.

```
omerion/
└── pipeline/
    ├── __init__.py
    ├── main.py        orchestration + FastAPI router (knowledge_base_router)
    ├── extractor.py   FileExtractor — Drive API text extraction
    ├── chunker.py     DocumentChunker — recursive tiktoken splitter
    ├── embedder.py    EmbeddingGenerator — wraps omerion_core.llm.embeddings
    ├── upserter.py    PineconeUpserter + SupabaseUpserter + VectorStoreUpserter
    ├── index.py       DocumentIndex — dedup + audit log
    └── watcher.py     Drive channel registration + renewal

omerion/omerion_core/inbound/knowledge_base.py   ← router shim mounted in app.py
omerion/infra/supabase/migrations/0021_knowledge_base.sql
setup.sql              ← standalone SQL for manual Supabase setup
.env.example           ← full environment variable template
```

---

## Setup

### 1. Google Service Account

1. In Google Cloud Console, create a service account with **Drive API** read access.
2. Download the JSON key and save it to `omerion/config/kb-service-account.json`
   (or any path — set `GOOGLE_SERVICE_ACCOUNT_JSON` accordingly).
3. Share the "Knowledge Base" Google Drive folder with the service account's email
   (e.g. `kb-reader@your-project.iam.gserviceaccount.com`) with **Viewer** permission.

### 2. Environment Variables

Copy `.env.example` to `.env` and fill in:

```
GOOGLE_SERVICE_ACCOUNT_JSON=config/kb-service-account.json
GOOGLE_DRIVE_FOLDER_ID=<folder ID from Drive URL>
VECTOR_STORE=both          # pinecone | supabase | both
OMERION_PUBLIC_BASE_URL=https://your-vps.com
OMERION_WEBHOOK_TOKEN=<long random secret>
```

### 3. Run the Supabase Migration

```bash
# Option A — Supabase CLI
supabase db push

# Option B — paste setup.sql into the Supabase SQL editor
```

### 4. Bootstrap the Pinecone knowledge-base Namespace

```bash
cd omerion
python -m infra.pinecone.setup
```

### 5. Register the Drive Push-Notification Channel

```bash
cd omerion
python -m pipeline.watcher
```

This POSTs to the Drive API and stores the channel metadata in
`drive_watch_channels` for auto-renewal.

The channel will be renewed automatically every day at 03:00 ET by the
APScheduler job registered in `main.py` lifespan
(`renew_drive_channels` from `pipeline.watcher`).

> **Option B — Google Apps Script fallback:** If your backend is behind a
> firewall or you cannot register a public webhook, install an `onChange` trigger
> in Google Apps Script bound to the Knowledge Base folder. Have it POST to
> `POST /webhooks/drive` with the Bearer token and JSON body
> `{"file_id": "<id>", "event_type": "updated"}`.

### 6. Start the Server

```bash
cd omerion
uv run uvicorn main:app --reload
```

The webhook endpoint is live at `POST /webhooks/drive`.

---

## Querying the Knowledge Base (Agents)

```python
from omerion_core.llm.embeddings import embed
from omerion_core.clients.supabase_client import supabase

query_vec = embed("What are Evykynn's AI consulting offer packages?")
result = supabase.rpc("match_documents", {
    "query_embedding": query_vec,
    "match_threshold": 0.75,
    "match_count": 5,
}).execute()

for row in result.data:
    print(row["content"], row["similarity"])
```

For Pinecone queries, use `pinecone_index().query()` with `namespace="knowledge-base"`.

---

## Deduplication Logic

| Scenario | Action |
|----------|--------|
| `file_id` not in `document_index` | Process → insert |
| `file_id` exists, hash matches | Skip (no re-embedding) |
| `file_id` exists, hash differs | Delete old vectors → re-embed → update |
| Drive `trash`/`remove` event | Delete all vectors + index record |

---

## Cost Estimates (text-embedding-3-small)

| Volume | Est. chunks | Est. cost |
|--------|-------------|-----------|
| 10 docs × 5 pages | ~250 chunks | < $0.001 |
| 100 docs × 10 pages | ~5,000 chunks | ~$0.01 |
| 1,000 docs × 10 pages | ~50,000 chunks | ~$0.10 |

Re-ingestion of unchanged files costs $0 (hash-skipped).

---

## Channel Renewal

Google Drive push-notification channels expire after 7 days. The watcher
registers channels in `drive_watch_channels` with their expiry timestamp.
The APScheduler job `renew_drive_channels()` runs daily at 03:00 ET, stops
any channel expiring within the next 24 hours, and registers a fresh one.

To add the scheduler job in `omerion/main.py`:

```python
from pipeline.watcher import renew_drive_channels
_scheduler.add_job(renew_drive_channels, "cron", hour=3, minute=0, timezone="America/New_York")
```
