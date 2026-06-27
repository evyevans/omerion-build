"""Omerion shared core library.

Imported by every one of the 14 agents. Provides:
  - settings     (Pydantic env config)
  - clients      (Supabase, Pinecone, Google, Twilio, ElevenLabs, Fireflies, GitHub)
  - llm          (Claude router + DeepSeek/Qwen fallbacks + OpenAI embeddings)
  - state        (base LangGraph state model)
  - hitl         (founder_review_queue + pause/resume)
  - telemetry    (run-level and node-level observability)
  - events       (emit_event + Realtime subscribe)
  - rate_limit   (token-bucket per service)
  - retry        (tenacity wrappers)
  - validation   (enum validation for persona, company_type, sentiment, channel)
  - optout       (opt-out guard clauses + full cascade)
  - fit_score    (deterministic 0-100 scoring, zero AI)
  - ingestion    (lead dedup + upsert by linkedin_url/email)
  - task_engine  (event-driven task generation with guards)
  - sequence     (4-touch counter + template selection)
"""
from omerion_core.settings import settings

__all__ = ["settings"]
__version__ = "0.2.0"
