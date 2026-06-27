# Semantic Dedup Policy — TRACK (R1 Market/Tech Watcher)

**Maintained by:** TRACK (r1_market_tech_watcher, Agent #11)  
**Last updated:** 2026-06-03  
**Source of truth:** `tools.py` — `DEDUP_HARD_SKIP = 0.96`, `DEDUP_SOFT_FLAG = 0.90`  
**Purpose:** Documents the dual-threshold semantic deduplication system that prevents
the same story from flooding `rd_insights` and R3's synthesis budget.

---

## Why Semantic Dedup Exists

RSS feeds from a16z, SaaStr, Business Insider, and TechCrunch frequently republish
the same story. A single $25M AI SDR funding round might appear as 4–8 entries across
feeds within 48 hours. URL dedup catches the same link; semantic dedup catches the
same STORY under different URLs — the problem URL dedup cannot solve.

Without this gate, R3 would receive 4–8 identical signals about the same funding
round and potentially generate duplicate or over-weighted proposals.

---

## The Two Thresholds

### Hard Skip (DEDUP_HARD_SKIP = 0.96)

When the cosine similarity of a new signal's summary embedding is **≥ 0.96** against
any prior insight in `rd_insights` (Pinecone `rd_insights` namespace) OR against any
insight already accepted in the current batch, the signal is **dropped entirely**.

```
cosine(new_signal.summary, prior_insight.summary) ≥ 0.96 → HARD SKIP
```

Logged as `r1_semantic_duplicate_skipped` with the matching score.

**Why 0.96:** At this threshold, the texts are essentially identical in meaning.
The only meaningful variation is word-level rephrasing of the same factual content.
A score this high means no new information is added.

### Soft Flag (DEDUP_SOFT_FLAG = 0.90)

When cosine similarity is **≥ 0.90 and < 0.96**, the signal is **kept** but tagged
with metadata marking it as a near-duplicate:
```python
ins.metadata = {
    **ins.metadata,
    "near_duplicate": True,
    "nearest_score": round(nearest, 3)
}
```

This metadata propagates into Pinecone when the signal is embedded. R3 can inspect
`near_duplicate = true` signals and down-weight them in RICE calculations (lower
Confidence, as multiple near-duplicates represent corroboration of the same source
story, not independent signal sources).

**Why 0.90:** At this threshold, the texts cover the same topic with enough variation
to potentially add a nuanced angle. They should surface for R3 to evaluate, but not
be treated as independent signals.

---

## Comparison Scope

The dedup system compares against:

1. **Pinecone `rd_insights` namespace** — all prior insights written by any previous run.
   This is the cross-batch dedup gate (catches stories reported weeks apart under new URLs).

2. **Current batch already-accepted insights** — prevents two identical stories
   that arrived in the same batch from both surviving the Pinecone check (which hasn't
   seen them yet).

---

## Failure Behavior (Non-Fatal)

Both the embedding step and the Pinecone query are wrapped in `try/except`:
- If `embed(ins.summary)` fails → signal is accepted without dedup (conservative: keep > drop)
- If `pinecone_index().query()` fails → `_pinecone_nearest()` returns 0.0 (no match)

Dedup failures are logged but **never block the pipeline**. A false accept (keeping
a near-duplicate) is preferable to a false drop (losing a valid new signal).

---

## URL Dedup (Separate Layer)

Before semantic dedup, `write_insight()` in tools.py performs a URL-level dedup:
```python
existing = supabase.table("rd_insights").select("insight_id").eq("source_url", i.source_url).limit(1).execute()
```
If the URL already exists in `rd_insights`, the signal is skipped before any LLM
tagging or embedding occurs.

**Layer order:**
```
fetch_signals() → is_relevant() (keyword filter)
               → URL dedup (Supabase)
               → tag_signal() (Haiku LLM tagging)
               → semantic_dedup() (Pinecone cosine comparison)
               → write_insight() (Supabase insert + Pinecone embed)
```

---

## Tuning Notes

| Scenario | Threshold adjustment |
|---|---|
| Too many duplicates slipping through | Lower DEDUP_HARD_SKIP (e.g., 0.94) |
| Valid distinct signals being dropped | Raise DEDUP_HARD_SKIP (e.g., 0.97) |
| R3 sees too much near-duplicate noise | Lower DEDUP_SOFT_FLAG (e.g., 0.85) |
| Too many signals flagged as near-duplicates | Raise DEDUP_SOFT_FLAG (e.g., 0.93) |

Both constants are defined in `tools.py` and also configured in `agents.yaml`
under `r1_market_tech_watcher.dedup.hard_skip` and `.soft_flag`. The agents.yaml
values are used for documentation only — the code constants are authoritative.
To change thresholds, update both.
