"""Outreach copy style guardrails.

Patterns extracted from blader/humanizer (29 AI-writing tells) and
hardikpandya/stop-slop (5-dimension scoring rubric).

**Two enforcement modes, two purposes:**

1. **Prompt-embedded negative list** (`STYLE_GUARD_RULES`,
   `UNIVERSAL_AGENT_RULES`) — soft guidance to the LLM at generation
   time. Reduces violation rate at the source but cannot guarantee
   compliance. Kept for backwards-compat with agents that haven't been
   migrated through the wrapper.

2. **Programmatic hard filter** (`filter()` below — Wave 1.6) — the
   *deterministic gate*. The wrapper calls this on every human-facing
   draft. A non-empty violation list causes the wrapper to reject the
   output and route to HITL.

The two modes complement: prompts ask the LLM to behave; the filter
enforces it.
"""
from __future__ import annotations

import re

# ── Programmatic phrase ban-lists ────────────────────────────────────────────
# Source: hardikpandya/stop-slop references/phrases.md
SLOP_BANNED_PHRASES: list[str] = [
    # Throat-clearing openers
    "Here's the thing", "The uncomfortable truth is", "It turns out",
    "The real ", "Let me be clear", "The truth is,", "I'll say it again",
    "I'm going to be honest", "Can we talk about", "Here's what I find interesting",
    "Here's the problem though",
    # Emphasis crutches
    "Full stop.", "Period.", "Let that sink in.", "This matters because",
    "Make no mistake", "Here's why that matters",
    # Filler adverbs
    "really", "just", "literally", "genuinely", "honestly", "simply",
    "actually", "deeply", "truly", "fundamentally", "inherently",
    "inevitably", "interestingly", "importantly", "crucially",
    # AI filler constructs
    "At its core", "In today's ", "It's worth noting", "At the end of the day",
    "When it comes to", "In a world where", "The reality is",
    # Meta-commentary / signposting
    "Let's dive in", "Let's explore", "Let's break this down",
    "Here's what you need to know", "without further ado",
    "Let me walk you through", "In this section", "As we'll see",
    "I want to explore",
    # Performative / vague declaratives
    "The implications are significant", "The stakes are high",
    "The consequences are real", "This is genuinely hard",
    "actually matters",
]

# Source: blader/humanizer SKILL.md — 29 AI-writing patterns
HUMANIZER_VOICE_RULES: list[str] = [
    # Significance inflation
    "stands as", "serves as", "is a testament", "marking a pivotal moment",
    "underscores its importance", "reflects broader", "setting the stage for",
    "represents a shift", "key turning point", "evolving landscape",
    "indelible mark", "deeply rooted",
    # High-frequency AI vocabulary (post-2023)
    "Additionally,", "align with", "crucial", "delve", "emphasizing",
    "enduring", "enhance", "fostering", "garner", "highlight the",
    "interplay", "intricate", "pivotal", "showcase", "tapestry",
    "testament", "underscore", "vibrant",
    # Copula avoidance (prefer "is"/"are")
    "boasts a", "features a", "offers a",
    # Vague attributions
    "Industry reports", "Observers have cited", "Experts argue",
    "Some critics argue", "Several sources",
    # Generic positive conclusions
    "The future looks bright", "Exciting times lie ahead",
    "journey toward excellence", "major step in the right direction",
    # Negative parallelism
    "It's not just about", "It's not merely", "not just X; it's Y",
    # Collaborative chatbot artifacts
    "I hope this helps", "Of course!", "Certainly!", "You're absolutely right",
    "Would you like me to", "Let me know if you'd like",
    # Knowledge-cutoff hedging
    "as of my last training", "based on available information",
    "While specific details are limited",
    # Sycophantic tone
    "Great question!", "That's an excellent point",
    # -ing abuse (fake depth tacked onto sentences)
    "highlighting ", "underscoring ", "emphasizing ", "fostering ",
    "cultivating ", "encompassing ", "showcasing ",
]

# ── Prompt-embeddable negative-list ─────────────────────────────────────────
STYLE_GUARD_RULES = """\
STYLE GUARDRAILS — never produce copy that contains:

Banned AI buzzwords & jargon:
- "delve", "dive into", "navigate the world of", "in today's fast-paced world"
- "elevate", "unleash", "unlock", "supercharge", "revolutionize"
- "leverage" as a verb, "robust", "seamless", "cutting-edge", "innovative"
- "synergy", "ecosystem", "holistic", "paradigm", "game-changer"
- "pivotal", "testament", "underscores", "vibrant", "tapestry", "garner"
- "fostering", "cultivating", "showcasing" tacked on to add fake depth

Banned openers & throat-clearers:
- "I hope this finds you well", "I hope you're doing well", "I trust this email finds you"
- "Just checking in", "Just wanted to circle back", "touching base"
- "Here's the thing:", "The uncomfortable truth is", "Let me be clear"
- "Let's dive in", "Let's explore", "Here's what you need to know"
- adverb-heavy openers ("Genuinely,", "Truly,", "Absolutely,", "Honestly,")
- rhetorical questions in the first sentence

Banned emphasis & filler:
- em-dashes (—) or en-dashes (–) used for dramatic pauses
- "Full stop." / "Period." / "Let that sink in." / "Make no mistake"
- "At its core", "At the end of the day", "In today's [X]", "It's worth noting"
- three-item lists where every item starts with the same verb

Banned closers & sycophancy:
- closing with "Looking forward to hearing from you" or "Best regards" boilerplate
- "Great question!", "Of course!", "Certainly!", "You're absolutely right!"
- generic praise of the prospect's company ("impressive growth", "great work")

Banned vague framing:
- "Industry reports", "Experts argue", "Observers have noted" (name the source or drop it)
- "serves as", "stands as a testament" — use "is"/"are" instead
- "It's not just about X; it's Y" negative parallelism
- "The future looks bright", "Exciting times lie ahead" — end with a concrete fact
- "As a [persona]" framings — show you know them, don't announce it

Write the way a peer would write to a peer they respect: direct,
specific, short sentences, one concrete next step. If you would not
say it out loud on a call, don't write it.
"""

# ── Universal rules for all agents (memory, security, output discipline) ─────
# Source: everything-claude-code best practices distilled for agent system prompts
UNIVERSAL_AGENT_RULES = """\
UNIVERSAL OUTPUT RULES:
- Memory discipline: build on context already established; do not re-explain
  or re-confirm what the caller already knows.
- Security discipline: never echo API keys, credentials, session tokens, or
  raw PII (emails, phone numbers) into your reasoning or output unless they
  are the explicit requested output.
- Completion discipline: report results, not process. One structured output
  per task. No preamble, no trailing "let me know if you need anything else."
"""

# ── 5-dimension scoring rubric for post-generation HITL gates ───────────────
# Threshold: any draft scoring <3 on any dimension routes to founder review.
SLOP_SCORING_RUBRIC = """\
Score this draft 1-5 on each dimension:
1. DIRECTNESS — does it get to the ask in the first 2 sentences?
2. RHYTHM — sentences vary in length; no run-on AI cadence?
3. TRUST — every claim is verifiable; no inflated stats or fake specifics?
4. AUTHENTICITY — sounds like a person, not a marketing template?
5. DENSITY — every sentence carries weight; no filler?

Output JSON: {"directness": int, "rhythm": int, "trust": int,
"authenticity": int, "density": int, "notes": "<one-line reason for any score below 4>"}
"""


# ─── HARD FILTER (Wave 1.6) ────────────────────────────────────────────────
#
# The wrapper calls `filter(text)` on every human-facing draft. A violation
# list with one or more entries → the wrapper rejects the output and routes
# to HITL with the violation list attached.
#
# Why these checks are case-insensitive substring + boundary regex (not LLM):
#   * Auditable — the ban list IS the policy.
#   * Cheap — runs in microseconds; called on every draft.
#   * Deterministic — same input always yields same violation set.
#   * Never hallucinates a new violation.
#
# The check is intentionally conservative:
#   * Filler adverbs ("really", "just", etc.) are banned only when used as
#     the FIRST word of a sentence or following a comma — a single "really"
#     inside a normal sentence is not a violation. This avoids false
#     positives on legitimate uses.
#   * Phrase bans are case-insensitive substring matches.
#   * Em/en dashes used for *pauses* are flagged; normal compound-word
#     hyphens are not (single ASCII `-` is allowed).

# Build the patterns once at import time. The wrapper calls filter()
# hot-path so we don't want to recompile on every call.
_PHRASE_BANS_LOWER: tuple[str, ...] = tuple(
    p.lower().strip() for p in (SLOP_BANNED_PHRASES + HUMANIZER_VOICE_RULES) if p.strip()
)

# Filler adverbs are checked positionally — sentence-start or after a comma —
# to avoid flagging legitimate mid-sentence uses.
_FILLER_ADVERBS = (
    "really", "just", "literally", "genuinely", "honestly", "simply",
    "actually", "deeply", "truly", "fundamentally", "inherently",
    "inevitably", "interestingly", "importantly", "crucially",
)
_FILLER_RE = re.compile(
    r"(?:^|(?<=[.!?]\s)|(?<=,\s))(?:" + "|".join(_FILLER_ADVERBS) + r")\b",
    re.IGNORECASE,
)

# Em-dash / en-dash used for pauses (flanked by spaces or sentence-ish punct).
_DASH_RE = re.compile(r"\s+[—–]\s+|[—–]\s|\s[—–]")


def filter(  # noqa: A001 — `filter` is the intended public name; shadows builtin only at module level
    text: str,
    *,
    max_violations: int = 25,
) -> tuple[bool, list[str]]:
    """Deterministic style-guard hard filter.

    Args:
      text: The human-facing draft to check.
      max_violations: Truncate the violation list at this length. Default
                       25 is plenty for HITL display.

    Returns:
      (ok, violations). `ok` is True iff `violations` is empty.

    Violations are short, human-readable strings; the wrapper attaches
    them to the HITL review for the founder to see.
    """
    if not text or not isinstance(text, str):
        return True, []

    violations: list[str] = []
    lower = text.lower()

    # 1. Phrase bans (case-insensitive substring).
    for phrase in _PHRASE_BANS_LOWER:
        if not phrase:
            continue
        if phrase in lower:
            violations.append(f'banned phrase: "{phrase}"')
            if len(violations) >= max_violations:
                return False, violations

    # 2. Positional filler adverbs.
    for m in _FILLER_RE.finditer(text):
        violations.append(f'filler at position {m.start()}: "{m.group(0).strip()}"')
        if len(violations) >= max_violations:
            return False, violations

    # 3. Em/en dashes used for pauses.
    if _DASH_RE.search(text):
        violations.append("em/en dash used for dramatic pause (use a period or hyphen)")
        if len(violations) >= max_violations:
            return False, violations

    return len(violations) == 0, violations


__all__ = [
    "SLOP_BANNED_PHRASES",
    "HUMANIZER_VOICE_RULES",
    "STYLE_GUARD_RULES",
    "UNIVERSAL_AGENT_RULES",
    "SLOP_SCORING_RUBRIC",
    "filter",
]
