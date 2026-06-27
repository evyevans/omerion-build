"""Shared engagement scoring primitive.

Why this lives in core: crm_nurture used opens(1.0)+clicks(3.0) uncapped raw sum,
while icp_scoring used count/10.0 capped at 1.0. Same signal, two scales,
inconsistent thresholds. Centralizing means a single weight definition that
both agents share, with each caller passing their config.
"""
from __future__ import annotations


def engagement_score(
    activity_rows: list[dict],
    *,
    weights: dict[str, float] | None = None,
    cap: float | None = None,
) -> float:
    """Sum weighted activity counts. Optionally clamp to `cap`.

    `activity_rows` is a list of `{"activity_type": str}` dicts (typically from
    `contact_activity_log`). `weights` maps activity_type -> weight; missing
    types contribute zero. `cap` is applied after the sum if provided.
    """
    w = weights or {"email_open": 1.0, "link_click": 3.0}
    score = 0.0
    for row in activity_rows:
        t = row.get("activity_type")
        if t in w:
            score += w[t]
    if cap is not None:
        score = min(score, cap)
    return score
