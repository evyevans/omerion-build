"""
Scout keyword pre-filter: deterministic job title → persona lookup.
Returns a persona + HIGH confidence for ~60% of common titles,
passing the rest to the AI classifier.
"""
import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "personas.json"
_VALID_PERSONAS: set[str] = set()
_KEYWORD_MAP: dict[str, str] = {}


def _load():
    global _VALID_PERSONAS, _KEYWORD_MAP
    if _VALID_PERSONAS:
        return
    data = json.loads(_CONFIG_PATH.read_text())
    _VALID_PERSONAS = set(data["personas"])
    _KEYWORD_MAP = {k.lower(): v for k, v in data["keyword_map"].items()}


def classify_by_title(job_title: str) -> dict:
    """
    Returns {"persona": str, "confidence": "HIGH"} if a keyword matches,
    or {"persona": None, "confidence": None} to signal AI fallback.
    """
    _load()
    normalized = job_title.lower().strip()
    for keyword, persona in _KEYWORD_MAP.items():
        if keyword in normalized:
            return {"persona": persona, "confidence": "HIGH"}
    return {"persona": None, "confidence": None}


def validate_persona(persona: str) -> bool:
    """True if persona is in the valid enum set."""
    _load()
    return persona in _VALID_PERSONAS


if __name__ == "__main__":
    import sys
    title = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Managing Broker"
    print(classify_by_title(title))
