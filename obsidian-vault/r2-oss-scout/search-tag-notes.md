# R2 Search Tag Notes

Last updated: 2026-06-03
Tags validated in: omerion/agents/r2_oss_scout/tools.py (_VALID_TAGS)
Config key: agents.yaml → r2_oss_scout.search_tags

Tags drive GitHub search queries. One query is issued per tag, returning the top
10 repos by stars. Tags map to Omerion's four service packages + one internal bucket.

---

## Tag: `daam`
**Maps to:** DAAM service package (Data Automation & AI Management)
**Search intent:** Repos that automate data pipelines, AI model management, evaluation frameworks, or workflow orchestration for B2B operators.
**High-fit signals:** ETL, DAG, model registry, evaluation harness, feature store, data quality, schema validation
**Low-fit traps:** Generic ML training frameworks (PyTorch, HuggingFace) — these are infrastructure, not automation
**Star floor:** 150
**Typical integration types:** component, pattern
**Editorial note:** Prioritise repos that operate over structured business data (CRM rows, invoices, contracts) rather than scientific/ML datasets.

---

## Tag: `capa`
**Maps to:** CAPA service package (Client Acquisition & Pipeline Automation)
**Search intent:** Repos that automate lead generation, CRM enrichment, outreach sequencing, or sales pipeline management.
**High-fit signals:** LinkedIn scraping, email finder, contact enrichment, CRM sync, outreach queue, reply detection
**Low-fit traps:** Full CRM platforms (HubSpot SDK, Salesforce SDK) — we don't build on top of existing CRMs
**Star floor:** 150
**Typical integration types:** component, pattern
**Editorial note:** Browser automation libs (Playwright, Selenium) score high composability only if they expose a programmatic Python API without a mandatory browser install step in the Railway container.

---

## Tag: `remi`
**Maps to:** REMI service package (Revenue & Marketing Intelligence)
**Search intent:** Repos for market intelligence, competitive analysis, signal extraction from news/RSS/social, or semantic tagging of business content.
**High-fit signals:** RSS parsing, semantic similarity, keyword extraction, named entity recognition (NER), dedup
**Low-fit traps:** Full analytics platforms (Mixpanel, Amplitude wrappers) — Omerion generates signals, does not consume product analytics
**Star floor:** 150
**Typical integration types:** component, pattern
**Editorial note:** NLP repos score composability lower if they require a GPU or model download at import time — Railway containers are CPU-only.

---

## Tag: `asap`
**Maps to:** ASAP service package (Automated Systems & Agent Platforms)
**Search intent:** Agent frameworks, tool-use libraries, MCP utilities, HITL primitives, or orchestration helpers that could extend or replace hand-rolled Omerion infrastructure.
**High-fit signals:** LangGraph-compatible, MCP server utilities, stdio process management, checkpointer patterns, interrupt/resume primitives
**Low-fit traps:** Other full agent frameworks (AutoGen, CrewAI) — integration_type must be `reference_only` as these compete with, not extend, Omerion's stack
**Star floor:** 150
**Typical integration types:** component, pattern, reference_only
**Editorial note:** This is R2's highest-value tag. Score fit aggressively when the repo provides a clean Python API that slots into LangGraph nodes. Score composability 0.30 or lower if the repo assumes its own event loop.

---

## Tag: `internal_os`
**Maps to:** Omerion internal tooling bucket — no external service package
**Search intent:** Repos that Omerion's own engineers maintain, fork, or seed. May include private mirrors, internal utilities, or repos Evy has starred/bookmarked for evaluation.
**High-fit signals:** Any repo explicitly referenced in a HITL card, founder Slack message, or `rd_proposals` row as "consider integrating X"
**Star floor:** 50 (tag-conditional — see integration-rubric.md)
**Typical integration types:** full_module, component
**Editorial note:** R1 signals with `impact_tag = "internal_os"` seed this tag's search terms. Unlike other tags, `internal_os` repos often have low public star counts but very high fit because they solve Omerion-specific problems. Always escalate to Sonnet for final scoring — Haiku under-rates niche internal tools.

---

## Adding New Tags

1. Add the tag string to `_VALID_TAGS` set in `tools.py`
2. Add it to `r2_oss_scout.search_tags` list in `agents.yaml`
3. Add a `## Tag: <tag>` section to this file with all fields populated
4. If the tag maps to a new service package, update `omerion-tech-stack.md`

Tags not present in `_VALID_TAGS` are silently rejected by `analyze_repo()` validation.
