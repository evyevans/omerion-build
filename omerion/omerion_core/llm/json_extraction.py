"""JSON extraction from LLM outputs — shared regex + parse fallback.

Why this lives in core: R1 and R2 both carried identical copies of the
regex-and-json.loads dance, and a third (outcome_attribution) carried a near-twin
for arrays. Centralizing means one place to harden the regex and one place to
log parse failures the same way across agents.

Both functions return (value, ok) so callers can distinguish "no JSON in the
text" from "JSON but wrong shape" from "valid JSON" — the prior copy-pasted
versions collapsed all three into an empty dict, which masked real failures.
"""
from __future__ import annotations

import json
import re
from typing import Any

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[\s*(?:\{.*?\}\s*,?\s*)*\]", re.DOTALL)


def extract_json_object(raw: str) -> tuple[dict[str, Any], bool]:
    """Find the first {...} block and parse it. Returns (data, ok)."""
    if not raw:
        return {}, False
    m = _JSON_OBJ_RE.search(raw)
    if not m:
        return {}, False
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}, False
    if not isinstance(data, dict):
        return {}, False
    return data, True


def extract_json_array(raw: str) -> tuple[list[Any], bool]:
    """Find the first [...] block and parse it. Returns (data, ok)."""
    if not raw:
        return [], False
    m = _JSON_ARRAY_RE.search(raw)
    if not m:
        return [], False
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return [], False
    if not isinstance(data, list):
        return [], False
    return data, True
