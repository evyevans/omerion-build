"""Central configuration — all env vars + config/agents.yaml.

Agents should import `settings` and never call `os.getenv` directly.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ─── Supabase ───────────────────────────────────────────────
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""
    # Direct connection string for LangGraph PostgresSaver (not the pooler).
    database_url: str = ""

    # ─── Anthropic (primary reasoning) ──────────────────────────
    anthropic_api_key: str = ""
    claude_model_opus: str = "claude-opus-4-6"
    claude_model_sonnet: str = "claude-sonnet-4-6"
    claude_model_haiku: str = "claude-haiku-4-5-20251001"
    # Beta header for Claude Managed Agents (R1-R4).
    anthropic_managed_agents_beta: str = "managed-agents-2026-04-01"

    # ─── OpenAI (embeddings only) ───────────────────────────────
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = 512  # must match pinecone index dimension

    # ─── Pinecone ───────────────────────────────────────────────
    pinecone_api_key: str = ""
    pinecone_index: str = "omerion-legion-rag"
    pinecone_environment: str = "us-east-1"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"

    # ─── Google Workspace (personal Gmail, OAuth refresh-token flow) ─────
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_refresh_token: str = ""
    google_crm_sheet_id: str = ""
    google_command_center_sheet_id: str = ""
    google_linkedin_crm_sheet_id: str = ""
    google_outreach_templates_sheet_id: str = ""
    google_lead_database_sheet_id: str = ""
    google_ops_hub_sheet_id: str = ""
    google_newsletter_sheet_id: str = ""
    google_delegated_user: str = ""           # kept for legacy callers; unused under personal OAuth
    google_apps_script_webhook_url: str = ""

    # ─── Google Drive / Knowledge Base pipeline ──────────────────
    # Path to a service account JSON file with Drive read-only access.
    google_service_account_json: str = ""
    # Google Drive folder ID of the "Knowledge Base" folder.
    google_drive_folder_id: str = ""
    google_kb_new_folder_id: str = ""
    google_agents_folder_id: str = ""

    # ─── KB Pipeline config ──────────────────────────────────────
    # Which vector store(s) to write to: 'pinecone' | 'supabase' | 'both'
    vector_store: str = "both"
    # 'small' → text-embedding-3-small | 'large' → text-embedding-3-large
    embedding_model_tier: str = "small"
    chunk_size: int = 512
    chunk_overlap: int = 50
    embedding_batch_size: int = 100

    # ─── ElevenLabs ─────────────────────────────────────────────
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""

    # ─── Fireflies ──────────────────────────────────────────────
    fireflies_api_key: str = ""
    fireflies_webhook_secret: str = ""

    # ─── DeepSeek (fallback coder) ──────────────────────────────
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    # ─── Qwen (fallback planner) ────────────────────────────────
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode"

    # ─── Omerion runtime (local FastAPI, replaces prior gateway) ─
    # Externally reachable base URL (used to build founder approve/reject links).
    omerion_public_base_url: str = ""
    # Bearer token expected on inbound /hitl/* and /webhooks/* routes.
    omerion_webhook_token: str = ""

    # ─── GitHub (personal account, no org yet) ──────────────────
    github_token: str = ""
    github_user: str = ""
    github_build_repo: str = ""               # e.g. evy/omerion-build (Build Orchestrator target)

    # ─── Cursor / Antigravity inbox ─────────────────────────────
    cursor_inbox_dir: str = "./.cursor_inbox"

    # ─── Discord webhook notifications ─────────────────────────
    # Two URLs so HITL alerts and run-completion messages can target
    # different channels; either may be left blank to disable that path.
    discord_completion_webhook_url: str = ""
    discord_hitl_webhook_url: str = ""
    # SEEK agent — #seek channel webhook for job-hunting HITL cards.
    discord_seek_webhook_url: str = ""
    # R4 regression alerts — posts to #alerts or #mission-control.
    discord_alerts_webhook_url: str = ""

    # ─── Discord first-party bot ──────────────────────────────
    discord_bot_token: str = ""
    discord_app_id: str = ""
    discord_guild_id: str = ""
    discord_hitl_channel_id: str = ""
    discord_omerion_room_channel_id: str = ""
    discord_mission_control_channel_id: str = ""

    # ─── Firecrawl (LinkedIn Jobs discovery for SEEK) ──────────
    firecrawl_api_key: str = ""
    firecrawl_base_url: str = "https://api.firecrawl.dev"

    # ─── LinkedIn (outreach automation) ────────────────────────
    # li_at session cookie from a logged-in LinkedIn browser session.
    # Extract: DevTools → Application → Cookies → linkedin.com → li_at
    # Used by the browser-use sender in linkedin_outreach/tools.py.
    linkedin_session_cookie: str = ""
    # Proxycurl commercial LinkedIn data API key (~$0.01/profile).
    # Powers the linkedin_mcp server's scraping tools.
    # Sign up at https://proxycurl.com
    proxycurl_api_key: str = ""

    # ─── Hunter.io (email finder for SEEK outreach targets) ────
    hunter_api_key: str = ""

    # ─── SerpAPI (Google Jobs aggregation for SEEK discovery) ──
    serp_api_key: str = ""

    # ─── Railway (DEPLOYER — cloud provisioning) ────────────────
    railway_api_token: str = ""
    railway_project_id: str = ""        # Railway project that hosts Omerion services
    railway_service_id: str = ""        # Target service for container deployments
    railway_api_url: str = "https://backboard.railway.app/graphql/v2"

    # ─── Supabase Management API (DEPLOYER — migration runner) ──
    # Service-role access token for the Management API (not the anon key).
    # Obtain from: https://supabase.com/dashboard/account/tokens
    supabase_management_token: str = ""
    supabase_project_ref: str = ""      # e.g. "abcdefghijklmnopq" (from project URL)

    # ─── Stripe billing ─────────────────────────────────────────
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""
    # Agentic Factory self-serve Product IDs (set in Stripe dashboard)
    stripe_product_standard: str = ""   # $5 Standard Blueprint tier
    stripe_product_executive: str = ""  # $10 Executive Blueprint tier
    # Stripe Payment Link URLs — append ?client_reference_id={session_id} at runtime.
    # Create in Stripe dashboard → Payment Links. The checkout.session.completed
    # webhook in stripe.py reads client_reference_id to correlate payment → session.
    stripe_payment_link_standard: str = ""   # Payment Link for Standard Blueprint ($5)

    # ─── Agentic Factory delivery ───────────────────────────────
    # Gmail App Password for sending emails from omerion.io@gmail.com
    gmail_app_password: str = ""
    # Calendly scheduling URL embedded in the blueprint HTML.
    calendly_url: str = ""
    # Calendly Personal Access Token
    calendly_token: str = ""
    # Founder email — BCCed on every blueprint delivery for QA review.
    founder_email: str = ""

    # ─── Newsletter content library (Google Drive → newsletter_materials) ─────
    # The newsletter_generator syncs the latest uploaded file from these Drive
    # folders into the newsletter_materials table before each send. Per-type
    # folders take precedence; both fall back to newsletter_drive_folder_id.
    # Files should be named: "{industry}__{material_type}__{seq}.ext"
    #   e.g. "real_estate__skill_pack__3.pdf"  /  "general__playbook__1.pdf"
    newsletter_drive_folder_id: str = ""           # shared fallback folder
    newsletter_skillpack_drive_folder_id: str = "" # bi-weekly skill packs
    newsletter_playbook_drive_folder_id: str = ""  # monthly playbooks
    # Base44 REST API — used to update BlueprintRequest after pipeline delivery
    base44_api_key: str = ""
    base44_app_id: str = ""

    # ─── Langfuse LLM Observability ────────────────────────────
    # Set these three vars to enable full LLM tracing + cost dashboards.
    # Leave blank to disable silently (no agent changes needed).
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ─── LangSmith graph tracing (additive to Langfuse) ───────
    langsmith_api_key: str = ""
    langsmith_project: str = "omerion-production"
    langchain_tracing_v2: bool = False
    auditor_breach_threshold: int = 3  # violations per scan window to trigger auto-pause

    # ─── LLM runtime hardening flags (see omerion_core/llm/runtime_config.py) ─
    # All default OFF — when disabled, ClaudeRouter.complete() behaves byte-
    # identically to pre-hardening. Flip per-env via .env or process env.
    #
    # NOTE: Langfuse tracing was already wired before these flags existed; it
    # is gated by langfuse_secret_key/public_key being set (above). The flag
    # below is a master kill-switch in case we ever need to force-disable
    # tracing without unsetting the keys.
    enable_langfuse_tracing: bool = True
    enable_router_style_filter: bool = False   # opt-in post-call style_guard.filter()
    enable_agent_budgets: bool = False         # in-process per-(agent,day) + per-(agent,run) caps
    # Placeholder reserved for v2 — NOT read anywhere in code today.
    # When wired, this will enable NeMo Guardrails as a safety-rail middleware
    # for jailbreak / PII / topical control. Style stays in style_guard.filter().
    enable_nemo_guardrails: bool = False

    # ─── Runtime ────────────────────────────────────────────────
    omerion_env: str = "dev"
    log_level: str = "INFO"
    # Root of the codebase on the current host. Used by security_auditor and
    # qa_tester to locate files for subprocess scans. Override via REPO_ROOT
    # env var in dev. Defaults to /app which is correct for Docker/Railway.
    repo_root: str = "/app"
    # Per-run cost cap (USD). 0 disables the check. When the executor sees a
    # completed run whose llm_cost_usd exceeds this, it logs an error so the
    # founder notices early instead of after a billing surprise.
    per_run_cost_cap_usd: float = 0.0

    # Per-(skill, day) cumulative spend cap. 0 disables. Checked by
    # agent_wrapper before dispatching a new run. Crosses the threshold →
    # CostBudgetExceeded, the run is skipped rather than queued, and a
    # Mission Control alert is raised (Wave 3.5).
    per_skill_daily_cost_cap_usd: float = 0.0

    # ─── Wave 2: write-safety bounds ────────────────────────────
    # Maximum dollar amount the offer-matching agent may surface to
    # opportunities.value_est_usd without HITL approval. Above this →
    # wrapper raises ValueBoundExceeded → run is routed to hitl_waiting.
    # The AI itself can only output a value_bucket Literal["S","M","L","XL"];
    # the bucket → dollar range is mapped deterministically below.
    max_opportunity_value_usd: float = 250_000.0

    # Deterministic bucket → (low, mid, high) USD range. The wrapper picks
    # mid as the persisted value_est_usd unless HITL approves an exact figure.
    # Keep these aligned with the offer-matching prompt (it asks for the
    # bucket label, not a number).
    value_bucket_ranges_usd: dict[str, tuple[float, float, float]] = Field(
        default_factory=lambda: {
            "S":  (5_000.0,   10_000.0,  15_000.0),
            "M":  (15_000.0,  32_500.0,  50_000.0),
            "L":  (50_000.0,  100_000.0, 150_000.0),
            "XL": (150_000.0, 200_000.0, 250_000.0),
        }
    )

    # ─── YAML config (agents.yaml) ──────────────────────────────
    agents_config_path: str = Field(default="config/agents.yaml")

    def _validate_prod(self) -> None:
        """Fail loudly at startup when RUNTIME_ENV=prod and critical keys are absent.

        Wave 3.6: extended to cover the full Wave-1+2 dependency set —
        Discord HITL webhook, Pinecone, Stripe webhook secret. Each one
        is a different category of failure if it's silently missing:
          * Anthropic / Supabase / DB → no LLM / no DB / no HITL checkpointing
          * Discord HITL webhook → HITL cards never reach the founder
          * Pinecone → RAG queries return zero (silent quality collapse)
          * Stripe webhook secret → revenue events never persist
        """
        if self.omerion_env != "prod":
            return
        missing: list[str] = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.supabase_url:
            missing.append("SUPABASE_URL")
        if not self.supabase_service_role_key:
            missing.append("SUPABASE_SERVICE_ROLE_KEY")
        if not self.database_url:
            missing.append("DATABASE_URL (required for HITL checkpointing in prod)")
        # Wave 3.6 additions ─ each maps to a real production failure mode.
        if not getattr(self, "discord_hitl_webhook_url", ""):
            missing.append("DISCORD_HITL_WEBHOOK_URL (HITL cards have no destination)")
        if not getattr(self, "pinecone_api_key", ""):
            missing.append("PINECONE_API_KEY (RAG queries silently return zero results)")
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY (required for all Pinecone embeddings via embed())")
        if not self.stripe_webhook_secret:
            missing.append("STRIPE_WEBHOOK_SECRET (revenue webhook will reject all events)")
        if missing:
            raise RuntimeError(
                f"OMERION cannot start in prod with missing required env vars: {missing}"
            )

    def _load_agents_config(self) -> dict[str, Any]:
        path = Path(self.agents_config_path)
        if not path.exists():
            return {}
        with path.open() as f:
            return yaml.safe_load(f) or {}

    def agent(self, agent_key: str) -> dict[str, Any]:
        cfg = _agents_config_cache(self.agents_config_path)
        return {**cfg.get("global", {}), **cfg.get(agent_key, {})}

    def shared(self, section: str) -> dict[str, Any]:
        """Top-level shared sections (personas, offer_packages, demo_catalog)."""
        cfg = _agents_config_cache(self.agents_config_path)
        return cfg.get(section, {}) or {}


@lru_cache(maxsize=8)
def _agents_config_cache(path_str: str) -> dict[str, Any]:
    """Module-level cached loader — avoids unhashable-self on lru_cache."""
    path = Path(path_str)
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


settings = Settings()
settings._validate_prod()


def validate_growth_agent_settings() -> list[str]:
    """Warn at startup about Growth department env vars that silently degrade.

    These vars have try/except fallbacks that swallow missing-key errors;
    surfacing them here ensures the founder knows before a test run why
    results are empty rather than discovering it from zero-result logs.
    """
    warnings_list: list[str] = []
    if not settings.hunter_api_key:
        warnings_list.append(
            "HUNTER_API_KEY missing — biz_dev_outreach email enrichment returns 0 results"
        )
    if not settings.serp_api_key:
        warnings_list.append(
            "SERP_API_KEY missing — biz_dev_outreach Google Jobs discovery returns 0 results"
        )
    if not settings.firecrawl_api_key:
        warnings_list.append(
            "FIRECRAWL_API_KEY missing — biz_dev_outreach Wellfound/YC/LinkedIn scraping disabled"
        )
    if not settings.linkedin_session_cookie:
        warnings_list.append(
            "LINKEDIN_SESSION_COOKIE missing — linkedin_outreach send will fail at runtime "
            "(set to li_at cookie from LinkedIn DevTools → Application → Cookies)"
        )
    if not settings.newsletter_drive_folder_id and not settings.newsletter_skillpack_drive_folder_id:
        warnings_list.append(
            "Newsletter Drive folder IDs not set — newsletter_generator Drive sync will find 0 materials"
        )
    return warnings_list
