"""Dimension 3: Career Progression & Leadership scorer.

Takes the pre-computed structured features dict and returns a float in
[0.0, 1.0].
"""
from __future__ import annotations


def _seniority_trajectory_score(title_seniority_scores: list) -> float:
    """Score based on level range across all roles (max_level - min_level)."""
    if not title_seniority_scores:
        return 0.5
    max_level = max(title_seniority_scores)
    min_level = min(title_seniority_scores)
    level_range = max_level - min_level

    if level_range >= 3:
        return 1.0
    elif level_range == 2:
        return 0.85
    elif level_range == 1:
        return 0.7
    else:  # level_range == 0
        return 0.5


def _company_growth_score(max_company_size_band: int) -> float:
    """Normalise max company size band (1-8) to [0, 1] by dividing by 8."""
    return min(max_company_size_band / 8.0, 1.0)


def score_career_progression(features: dict) -> float:
    """Compute the Career Progression & Leadership dimension score.

    Parameters
    ----------
    features:
        Structured features dict produced by StructuredFeatureExtractor.

    Returns
    -------
    float
        Score in [0.0, 1.0].
    """
    title_seniority_scores: list = features.get("title_seniority_scores", [])
    traj_score = _seniority_trajectory_score(title_seniority_scores)

    company_score = _company_growth_score(features.get("max_company_size_band", 0))

    leadership_score = features.get("leadership_evidence_score", 0.0)

    # scope_ownership_score: use leadership_evidence_score as proxy
    scope_score = leadership_score

    career_progression = (
        0.35 * traj_score
        + 0.25 * company_score
        + 0.25 * leadership_score
        + 0.15 * scope_score
        + features.get("seniority_trajectory_bonus", 0.0)
        - features.get("stagnation_penalty", 0.0)
    )

    return float(max(0.0, min(1.0, career_progression)))
