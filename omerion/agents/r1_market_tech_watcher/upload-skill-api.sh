#!/usr/bin/env bash
# Upload new r1-market-tech-watcher skill version via Anthropic API (bypasses Console drag-drop).
set -euo pipefail
cd "$(dirname "$0")/../../.."  # omerion/
uv run python - <<'PY'
from pathlib import Path
from anthropic import Anthropic
from anthropic.lib import files_from_dir
from omerion_core.settings import settings

skill_dir = Path("agents/r1_market_tech_watcher/skill-package/r1-market-tech-watcher").resolve()
client = Anthropic(api_key=settings.anthropic_api_key)
v = client.beta.skills.versions.create(
    skill_id="skill_01DLjFz6eQ5ViD6HvS11EWWs",
    files=files_from_dir(str(skill_dir)),
)
print(f"OK — new version: {v.version}")
print(f"skill_id: skill_01DLjFz6eQ5ViD6HvS11EWWs")
PY
