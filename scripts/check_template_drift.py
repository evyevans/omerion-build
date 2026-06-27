#!/usr/bin/env python3
"""Detect drift between clients/_template/client_config.yaml and the
ClientConfigSpec Pydantic model (Fix #10).

Exit codes:
  0 = template matches schema (modulo REPLACE_ME placeholders)
  1 = drift detected — template missing fields or has unknown keys

Run as:
  uv run python scripts/check_template_drift.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.schemas.base import ClientConfigSpec  # noqa: E402


TEMPLATE = REPO / "clients" / "_template" / "client_config.yaml"


def main() -> int:
    if not TEMPLATE.exists():
        print(f"FAIL: template not found at {TEMPLATE}")
        return 1
    raw: dict[str, Any] = yaml.safe_load(TEMPLATE.read_text())

    schema_fields = set(ClientConfigSpec.model_fields.keys())
    template_keys = set(raw.keys())

    missing = sorted(schema_fields - template_keys)
    unknown = sorted(template_keys - schema_fields)

    if not missing and not unknown:
        print("OK template in sync with ClientConfigSpec")
        return 0

    if missing:
        print("FAIL template is missing fields declared by ClientConfigSpec:")
        for k in missing:
            print(f"  - {k}")
    if unknown:
        print("FAIL template has keys not in ClientConfigSpec (typo? renamed field?):")
        for k in unknown:
            print(f"  - {k}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
