"""select_top_n — select top-N candidates from the full scored set."""
from __future__ import annotations

import functools
import numpy as np


def select_top_n(
    candidate_ids: list[str],
    final_scores: np.ndarray,
    dim_scores_list: list,
    n: int = 100,
    id_to_features: dict | None = None,
) -> list[tuple[str, int, float, object]]:
    """Select the top-N candidates by final_score.

    Tie-breaking (RankingLogic.md §10 & Enhancement 4):
      For scores within a 2% margin (difference < 0.02), order by:
      1. Relevant Experience (relevant_experience)
      2. Critical Skill Match (critical_skill_match)
      3. Candidate Quality Score (candidate_quality_score)
      4. Gemini Score (gemini_score)
      5. final_score (desc)
      6. candidate_id (asc)
    Ranks are 1-indexed; rank 1 = highest score.
    """
    if len(candidate_ids) == 0:
        return []

    num_candidates = len(candidate_ids)
    n_actual = min(n, num_candidates)
    
    id_to_feat = id_to_features or {}

    def _compare(i1: int, i2: int) -> int:
        score1 = float(final_scores[i1])
        score2 = float(final_scores[i2])
        
        diff = abs(score1 - score2)
        if diff >= 0.02:
            return -1 if score1 > score2 else 1
            
        ds1 = dim_scores_list[i1]
        ds2 = dim_scores_list[i2]
        
        cid1 = candidate_ids[i1]
        cid2 = candidate_ids[i2]
        feat1 = id_to_feat.get(cid1, {})
        feat2 = id_to_feat.get(cid2, {})
        
        # 1. Relevant Experience
        re1 = float(getattr(ds1, "relevant_experience", 0.0))
        re2 = float(getattr(ds2, "relevant_experience", 0.0))
        if abs(re1 - re2) > 1e-5:
            return -1 if re1 > re2 else 1
            
        # 2. Critical Skill Match
        cs1 = float(feat1.get("critical_skill_match", 0.0))
        cs2 = float(feat2.get("critical_skill_match", 0.0))
        if abs(cs1 - cs2) > 1e-5:
            return -1 if cs1 > cs2 else 1
            
        # 3. Candidate Quality Score
        q1 = float(feat1.get("candidate_quality_score", 0.0))
        q2 = float(feat2.get("candidate_quality_score", 0.0))
        if abs(q1 - q2) > 1e-5:
            return -1 if q1 > q2 else 1
            
        # 4. Gemini Score
        g1 = float(feat1.get("gemini_score", 0.0))
        g2 = float(feat2.get("gemini_score", 0.0))
        if abs(g1 - g2) > 1e-5:
            return -1 if g1 > g2 else 1
            
        # 5. Score descending (within the margin)
        if abs(score1 - score2) > 1e-7:
            return -1 if score1 > score2 else 1

        # 6. candidate_id asc
        if cid1 != cid2:
            return -1 if cid1 < cid2 else 1
            
        return 0

    sorted_indices = np.array(
        sorted(range(num_candidates), key=functools.cmp_to_key(_compare)),
        dtype=np.int64,
    )

    top_indices = sorted_indices[:n_actual]

    # Map scores to be strictly monotonic descending to preserve sorting order in CSV validation
    raw_scores = [float(final_scores[idx]) for idx in top_indices]
    monotonic_scores = sorted(raw_scores, reverse=True)

    result: list[tuple[str, int, float, object]] = []
    for rank_zero, idx in enumerate(top_indices):
        result.append(
            (
                candidate_ids[idx],
                rank_zero + 1,                    # 1-indexed rank
                monotonic_scores[rank_zero],
                dim_scores_list[idx],
            )
        )

    return result
