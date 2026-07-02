"""ScoreAssembler — Phase 2 score assembly.

Loads all cached features, computes cosine similarities against the JD
embedding, scores all 8 dimensions, and returns final scores ready for
top-N selection.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

# Ensure project root on path so relative imports work when run directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.features.cache import FeatureCache
from src.scoring.dimensions import DimScores, DimensionScorer

# Batch size for loading embeddings during Phase 2 similarity computation.
_EMB_LOAD_BATCH = 10_000


class ScoreAssembler:
    """Load cached features, compute cosine similarities, score all 8 dimensions.

    Parameters
    ----------
    cache_dir:
        Path to the feature cache directory written by precompute.py.
    config:
        Optional configuration dict (passed through to DimensionScorer).
    jd_path:
        Optional path to the JD JSON file.
    """

    def __init__(
        self,
        cache_dir: str,
        config: dict | None = None,
        jd_path: str | None = None,
    ) -> None:
        self.cache = FeatureCache(cache_dir)
        self.config = config or {}
        self.jd_path = jd_path

        # Load JD dict
        self.jd_dict = {}
        if jd_path:
            try:
                with open(jd_path, "r", encoding="utf-8") as fh:
                    self.jd_dict = json.load(fh)
            except Exception:
                pass

        # Classify JD specialization
        from src.scoring.dim_specialization import classify_jd_specialization
        self.jd_specialization = classify_jd_specialization(self.jd_dict)

        self.scorer = DimensionScorer(
            self.config,
            jd_specialization=self.jd_specialization,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assemble(self) -> tuple[list[str], np.ndarray, list[DimScores]]:
        """Load all cached features and compute final scores.

        Returns
        -------
        candidate_ids : list[str]
            Ordered list of candidate IDs corresponding to the arrays.
        final_scores : np.ndarray, shape [N]
            Final weighted score for every candidate.
        dim_scores : list[DimScores]
            Per-candidate DimScores objects (preserves full detail for top-N).
        """
        # -------------------------------------------------------------- #
        # 1. Load meta → id/index maps                                     #
        # -------------------------------------------------------------- #
        meta = self.cache.load_meta()
        if not meta:
            # Empty cache — return empty results gracefully
            return [], np.array([], dtype=np.float32), []

        index_to_id: list[str] = meta.get("index_to_id", [])
        total = len(index_to_id)
        if total == 0:
            return [], np.array([], dtype=np.float32), []

        # -------------------------------------------------------------- #
        # 2. Load JD embedding                                             #
        # -------------------------------------------------------------- #
        jd_embedding = self.cache.load_jd_embedding()  # shape [1, 384]

        # -------------------------------------------------------------- #
        # 3. Load all structured features into memory                      #
        # -------------------------------------------------------------- #
        all_features: list[dict] = []
        for batch_id in self.cache.batch_ids():
            batch = self.cache.load_structured_batch(batch_id)
            all_features.extend(batch)

        n = len(all_features)
        if n == 0:
            return [], np.array([], dtype=np.float32), []

        # -------------------------------------------------------------- #
        # 4. Load embeddings in batches, compute cosine similarity         #
        # -------------------------------------------------------------- #
        cosine_sims = np.zeros(n, dtype=np.float32)
        batch_ids = self.cache.batch_ids()
        global_start = 0

        for batch_id in batch_ids:
            emb_batch = self.cache.load_embedding_batch(batch_id)  # [B, D]
            batch_size = emb_batch.shape[0]

            # Cosine similarity: both JD and candidate embeddings are L2-normalised
            # during precompute, so dot product == cosine similarity.
            sims = (emb_batch @ jd_embedding.T).squeeze(-1)  # [B]
            cosine_sims[global_start: global_start + batch_size] = sims
            global_start += batch_size

        # -------------------------------------------------------------- #
        # 5. Pool-normalise cosine similarities to [0, 1]                  #
        #    Normalise only eligible candidates for sharper discrimination. #
        # -------------------------------------------------------------- #
        eligible_mask = np.array(
            [not f.get("is_disqualified", False) for f in all_features],
            dtype=bool,
        )
        sims_normalised = np.zeros(n, dtype=np.float32)
        if eligible_mask.any():
            eligible_sims = cosine_sims[eligible_mask]
            sim_min = float(eligible_sims.min())
            sim_max = float(eligible_sims.max())
            sims_normalised[eligible_mask] = (
                (eligible_sims - sim_min) / (sim_max - sim_min + 1e-8)
            ).astype(np.float32)

        # -------------------------------------------------------------- #
        # 6. Score each candidate                                          #
        # -------------------------------------------------------------- #
        weights = self.config.get("weights", None)
        dim_scores_list: list[DimScores] = []
        final_scores = np.zeros(n, dtype=np.float32)

        for i, features in enumerate(all_features):
            ds = self.scorer.score_all(features, cosine_sim=float(sims_normalised[i]))
            dim_scores_list.append(ds)
            final_scores[i] = ds.final_score(weights)

        # -------------------------------------------------------------- #
        # 7. Return                                                        #
        # -------------------------------------------------------------- #
        candidate_ids = list(index_to_id) + [""] * max(0, n - len(index_to_id))

        return candidate_ids[:n], final_scores, dim_scores_list
