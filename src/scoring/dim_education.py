"""Dimension: Education scorer.

Scores the candidate's education tier and field of study.
"""
from __future__ import annotations


def score_education(features: dict) -> float:
    """Score candidate's education.

    - tier_1: 1.0
    - tier_2: 0.7
    - tier_3: 0.4
    - default/none: 0.2
    - 1.2x boost if technical field of study (CS/IT/AI/Math/Stats), capped at 1.0.
    """
    tier = features.get("education_tier", "tier_3")
    is_tech = bool(features.get("education_is_tech", False))

    if tier == "tier_1":
        base_score = 1.0
    elif tier == "tier_2":
        base_score = 0.7
    elif tier == "tier_3":
        base_score = 0.4
    else:
        base_score = 0.2

    if is_tech:
        base_score = min(base_score * 1.2, 1.0)

    return float(base_score)
