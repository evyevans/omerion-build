"""Failure clustering — Wave 5 v2.4.

Transforms TRAINER's failure_signal from "12% failure rate" into
"8 of 10 failures cluster around contacts with no public funding
signal" by embedding the failed inputs and running DBSCAN.

Why this matters: an LLM asked to "fix the failures" without concrete
patterns hallucinates a fix. An LLM shown "these 8 failed inputs are
similar; here are 2 cluster centroids" can propose a *targeted* fix.

Dependencies:
  * `omerion_core.llm.embeddings.embed()` — already uses
    OpenAI text-embedding-3-small at 512 dims (matches the
    omerion-legion-rag Pinecone index).
  * sklearn DBSCAN — already a transitive dep via langgraph stack.
    Graceful fallback if not installed.

Design:
  * Embed each failed input's `rendered_input_text` (capped at first
    2000 chars for cost — embedding cost is per-token).
  * Cluster with DBSCAN(eps=0.4, min_samples=2) on cosine distance.
    eps=0.4 corresponds to ~roughly "same semantic neighborhood"
    for text-embedding-3-small at 512-dim.
  * Return up to 3 biggest clusters with their representative samples
    (the medoid — closest sample to the cluster centroid).
  * Noise points (DBSCAN label = -1) are surfaced separately as
    "miscellaneous failures."

Cost: 10 failures × ~500 tokens each × $0.02/1M tokens for embedding-3-small
     ≈ $0.0001 per TRAINER run. Negligible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from omerion_core.logging import get_logger

log = get_logger("omerion.agents.trainer.clustering")


@dataclass
class FailureCluster:
    """One cluster of semantically-similar failures."""

    cluster_id: int          # 0, 1, 2, …; -1 = noise (DBSCAN convention)
    size: int                # how many failures in this cluster
    representative_input: str    # the medoid sample's input (truncated)
    representative_response: str # the medoid sample's response (truncated)
    sample_invocation_ids: list[str] = field(default_factory=list)


@dataclass
class ClusterReport:
    """The output the LLM gets in its meta-prompt."""

    total_failures: int
    clusters: list[FailureCluster] = field(default_factory=list)
    noise_count: int = 0
    clustering_unavailable: bool = False
    fallback_reason: str | None = None

    def format_for_llm(self, max_clusters: int = 3) -> str:
        """Human-readable, LLM-consumable block summarizing the
        clustering. Inserted into the meta-prompt as `failure_clusters`.
        """
        if self.clustering_unavailable:
            return (
                f"Clustering unavailable ({self.fallback_reason or 'unknown'}). "
                f"Total failures in window: {self.total_failures}."
            )
        if not self.clusters:
            return f"All {self.total_failures} failures appear unrelated (no clusters with ≥2 samples)."

        lines = [
            f"{self.total_failures} failures grouped into "
            f"{len(self.clusters)} cluster(s) + {self.noise_count} unrelated:\n"
        ]
        for c in self.clusters[:max_clusters]:
            pct = 100 * c.size / max(self.total_failures, 1)
            lines.append(
                f"  CLUSTER {c.cluster_id} — {c.size}/{self.total_failures} "
                f"failures ({pct:.0f}%):\n"
                f"    Representative input:    {c.representative_input[:400]!r}\n"
                f"    Representative response: {c.representative_response[:400]!r}\n"
            )
        return "\n".join(lines)


# ─────────────────────────── public entry ─────────────────────────────


def cluster_failures(
    samples: list[Any],
    *,
    eps: float = 0.4,
    min_samples: int = 2,
    input_text_cap: int = 2000,
) -> ClusterReport:
    """Embed + cluster a list of InvocationSample (from shadow_eval).

    Args:
      samples: list of InvocationSample (the failure cohort).
      eps: DBSCAN neighborhood radius in cosine distance. 0.4 ≈ "same
        semantic neighborhood" for text-embedding-3-small @ 512 dim.
        Tighten to 0.3 for stricter clusters; loosen to 0.5 for broader.
      min_samples: minimum cluster size. 2 surfaces pairs; 3 only
        triples-or-more.
      input_text_cap: max chars per input embedded. Cost guard.

    Returns ClusterReport. On any failure path (sklearn missing,
    embedding outage, etc.), returns ClusterReport(clustering_unavailable=True)
    so the caller can degrade gracefully — TRAINER falls back to using
    raw failure samples without clustering.
    """
    total = len(samples)
    if total < min_samples:
        return ClusterReport(
            total_failures=total,
            clustering_unavailable=True,
            fallback_reason=f"too few samples ({total} < min_samples={min_samples})",
        )

    # Lazy imports so a sklearn-less environment still loads the module.
    try:
        from omerion_core.llm.embeddings import embed
    except Exception as exc:  # noqa: BLE001
        return ClusterReport(
            total_failures=total,
            clustering_unavailable=True,
            fallback_reason=f"embed_import_failed: {exc}",
        )
    try:
        import numpy as np
        from sklearn.cluster import DBSCAN
    except Exception as exc:  # noqa: BLE001
        return ClusterReport(
            total_failures=total,
            clustering_unavailable=True,
            fallback_reason=f"sklearn_import_failed: {exc}",
        )

    # 1. Embed each input text (capped).
    vectors: list[list[float]] = []
    for s in samples:
        text = (getattr(s, "rendered_input_text", "") or "")[:input_text_cap]
        if not text:
            # Use a deterministic zero-vector placeholder so the sample
            # stays in the array but lands in DBSCAN noise.
            vectors.append([0.0] * 512)
            continue
        try:
            vec = embed(text)
        except Exception as exc:  # noqa: BLE001
            log.warning("clustering_embed_failed", invocation_id=s.invocation_id, error=str(exc))
            vectors.append([0.0] * 512)
            continue
        vectors.append(vec)

    arr = np.array(vectors, dtype=np.float32)
    if arr.shape[0] < min_samples:
        return ClusterReport(
            total_failures=total,
            clustering_unavailable=True,
            fallback_reason="all embeddings failed",
        )

    # 2. DBSCAN on cosine distance.
    try:
        labels = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(arr)
    except Exception as exc:  # noqa: BLE001
        return ClusterReport(
            total_failures=total,
            clustering_unavailable=True,
            fallback_reason=f"dbscan_failed: {exc}",
        )

    # 3. Group samples by label.
    by_label: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        by_label.setdefault(int(label), []).append(idx)

    noise_count = len(by_label.get(-1, []))
    cluster_labels = sorted(
        (lbl for lbl in by_label.keys() if lbl != -1),
        key=lambda l: -len(by_label[l]),  # biggest cluster first
    )

    clusters: list[FailureCluster] = []
    for lbl in cluster_labels:
        member_indices = by_label[lbl]
        # Medoid: the sample whose embedding is closest to the cluster
        # centroid. Better than picking the first sample, which is
        # arbitrary.
        cluster_arr = arr[member_indices]
        centroid = cluster_arr.mean(axis=0)
        # Cosine distance to centroid (smaller = closer).
        dots = cluster_arr @ centroid
        norms = (np.linalg.norm(cluster_arr, axis=1) * np.linalg.norm(centroid)) + 1e-9
        sims = dots / norms
        medoid_local_idx = int(np.argmax(sims))
        medoid_global_idx = member_indices[medoid_local_idx]
        medoid = samples[medoid_global_idx]

        clusters.append(FailureCluster(
            cluster_id=int(lbl),
            size=len(member_indices),
            representative_input=(getattr(medoid, "rendered_input_text", "") or "")[:600],
            representative_response=(getattr(medoid, "original_response", "") or "")[:600],
            sample_invocation_ids=[
                str(samples[i].invocation_id) for i in member_indices
            ],
        ))

    log.info(
        "clustering_complete",
        total=total,
        clusters=len(clusters),
        biggest_cluster_size=clusters[0].size if clusters else 0,
        noise=noise_count,
    )
    return ClusterReport(
        total_failures=total,
        clusters=clusters,
        noise_count=noise_count,
    )


__all__ = [
    "FailureCluster",
    "ClusterReport",
    "cluster_failures",
]
