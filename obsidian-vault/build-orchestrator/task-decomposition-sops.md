# Task Decomposition SOPs

## Task Granularity Rules

Apply all rules when decomposing a blueprint into TaskSpec items. Every rule is enforced — a decomposition that violates any rule must be revised before tasks are persisted.

### Granularity Ceiling
Maximum 5 files changed per task. If a logical unit touches more files, split it — the `depends_on` dependency graph handles ordering. Tasks that change >5 files are always scope violations, not estimates.

### Phase Assignment
- **phase_1 — Foundation:** Schema migrations, data models, config, infrastructure setup, environment variables. No user-facing code. No external API calls. No LangGraph nodes.
- **phase_2 — Core Logic:** Business rules, integrations, APIs, LangGraph graph/state/tools/prompts, MCP tools. Depends on phase_1 completion.
- **phase_3 — Delivery:** UI, client-facing endpoints, reporting dashboards, documentation, QA harness. Depends on phase_2 completion.

Rule: A task may not mix phases. A migration + a LangGraph node is two tasks.

### Slug Format
- `kebab-case`, all lowercase, no version numbers, no underscores
- Max 40 characters
- Pattern: `<verb>-<noun>` — e.g., `add-revenue-events-schema`, `build-icp-scoring-node`, `create-client-dashboard`
- Slugs must be unique within the deployment

### Title Format
- Imperative verb + noun phrase, ≤60 characters
- Examples: "Add revenue_events Supabase schema", "Build ICP fit scoring LangGraph node"
- No "WIP", "draft", or version suffixes

### Acceptance Criteria Requirements
Each task must have **minimum 3 acceptance criteria**. Each criterion must be:
- **Verifiable** (pass/fail — not "should be good" or "looks clean")
- **Specific** (names the function, table, or endpoint being tested)

Required criterion types (at least one of each per task):
1. **Functional:** "Function `score_contact()` returns a float between 0.0 and 1.0 for all valid inputs"
2. **Test coverage:** "pytest coverage for `omerion/agents/<name>/tools.py` ≥ 80%"
3. **Integration** (for tasks with external API calls): "Tool returns a valid response from [API] in test mode using mocked credentials"

### Module Taxonomy
Assign exactly one `module` per task:

| module | covers |
|---|---|
| `data_layer` | Supabase migrations, RPC functions, ENUM changes, index additions |
| `agent_core` | LangGraph graph, state, tools, prompts for a single agent |
| `mcp_server` | FastMCP tools, MCP server stdio setup, tool schemas |
| `discord_integration` | Discord channel handlers, bot routing, webhook receivers |
| `api_endpoint` | FastAPI routes, webhooks, health endpoints |
| `config` | agents.yaml, settings, environment variables, Railway config |
| `test_suite` | pytest files only — no production code changes |
| `client_deliverable` | Google Docs, Drive folders, client-facing exports |

### Effort Estimation
`effort_days` must be an integer from 1 to 10. No task may exceed 10 days — split it.

| days | scope |
|---|---|
| 1–2 | Simple config change, single migration, test additions to existing module |
| 3–5 | Single LangGraph node, one external API integration, one MCP tool set |
| 6–10 | Multi-node agent, complex integration with multiple API endpoints, full MCP server |

### depends_on
List of slug strings this task cannot begin until merged. Empty list `[]` for all phase_1 tasks. Phase_2 tasks must list their phase_1 prerequisites. Phase_3 tasks must list phase_2 prerequisites.

### Mandatory Fields (all must be non-null)
`slug`, `title`, `phase`, `rationale`, `acceptance_criteria` (min 3 items), `effort_days`, `depends_on`, `module`
