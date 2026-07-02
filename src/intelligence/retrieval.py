from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np

from src.features.cache import FeatureCache
from src.intelligence.types import RetrievalResult


def _topk_from_scores(ids: list[str], scores: np.ndarray, k: int) -> list[RetrievalResult]:
    if k <= 0 or len(ids) == 0:
        return []
    k = min(k, len(ids))
    # argpartition is faster but needs full array; for small k we can use heap.
    idx = np.argpartition(scores, -k)[-k:]
    idx = idx[np.argsort(scores[idx])[::-1]]
    return [RetrievalResult(candidate_id=ids[i], similarity=float(scores[i])) for i in idx]


@dataclass
class SimilarityRetriever:
    """Cosine-similarity retrieval over cached candidate embeddings."""

    cache_dir: str

    def __post_init__(self) -> None:
        self.cache = FeatureCache(self.cache_dir)

    def top_k(self, job_embedding: np.ndarray, k: int = 50) -> list[RetrievalResult]:
        """Retrieve top-k candidates for a job embedding.

        Assumes embeddings are already L2-normalized (true for our pipeline).
        """
        meta = self.cache.load_meta()
        index_to_id: list[str] = meta.get("index_to_id", [])

        # job_embedding can be [D] or [1, D]
        job = job_embedding.reshape(1, -1).astype(np.float32)

        heap: list[tuple[float, str]] = []  # min-heap of (sim, candidate_id)
        global_idx = 0
        for batch_id in self.cache.batch_ids():
            emb = self.cache.load_embedding_batch(batch_id).astype(np.float32)  # [B, D]
            sims = (emb @ job.T).squeeze(-1)  # [B]
            for j, sim in enumerate(sims.tolist()):
                cid = index_to_id[global_idx + j] if (global_idx + j) < len(index_to_id) else ""
                if not cid:
                    continue
                if len(heap) < k:
                    heapq.heappush(heap, (sim, cid))
                else:
                    if sim > heap[0][0]:
                        heapq.heapreplace(heap, (sim, cid))
            global_idx += emb.shape[0]

        # Convert heap to sorted results descending
        heap.sort(key=lambda x: x[0], reverse=True)
        return [RetrievalResult(candidate_id=cid, similarity=float(sim)) for sim, cid in heap]


@dataclass
class BatchSimilarityComputer:
    """Vectorized similarity computation for a specific batch array."""

    def compute(self, candidate_embeddings: np.ndarray, job_embedding: np.ndarray) -> np.ndarray:
        job = job_embedding.reshape(1, -1).astype(np.float32)
        emb = candidate_embeddings.astype(np.float32)
        return (emb @ job.T).squeeze(-1)

