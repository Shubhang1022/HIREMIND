"""Dimension 2: Relevant Experience scorer.

Takes the pre-computed structured features dict and evaluates candidate's
relevant technical experience in AI/ML and retrieval engineering.
"""
from __future__ import annotations


def _product_company_score(ratio: float) -> float:
    """Convert product-company ratio to a [0, 1] score."""
    if ratio >= 0.9:
        return 1.0
    elif ratio >= 0.7:
        return 0.85
    elif ratio >= 0.5:
        return 0.65
    elif ratio >= 0.3:
        return 0.45
    elif ratio > 0:
        return 0.25
    else:
        return 0.0


def _tenure_stability_score(longest_tenure_months: int) -> float:
    """Stability score based on longest single-role tenure."""
    if longest_tenure_months >= 24:
        return 1.0
    elif longest_tenure_months >= 18:
        return 0.8
    elif longest_tenure_months >= 12:
        return 0.6
    else:
        return 0.4


def score_relevant_experience(features: dict) -> float:
    """Compute the Relevant Experience score.

    Parameters
    ----------
    features:
        Structured features dict produced by StructuredFeatureExtractor.

    Returns
    -------
    float
        Score in [0.0, 1.0].
    """
    years = float(features.get("relevant_years_exp", 0.0) or 0.0)

    # Relevant experience score mapping (more relevant experience is always better)
    if years < 2.0:
        years_score = (years / 2.0) * 0.4
    elif years < 4.0:
        years_score = 0.4 + ((years - 2.0) / 2.0) * 0.3
    elif years < 6.0:
        years_score = 0.7 + ((years - 4.0) / 2.0) * 0.2
    elif years <= 8.0:
        years_score = 0.9 + ((years - 6.0) / 2.0) * 0.1
    else:
        years_score = 1.0

    product_score = _product_company_score(features.get("product_company_ratio", 0.0))
    production_score = features.get("production_evidence_score", 0.0)
    tenure_score = _tenure_stability_score(features.get("longest_tenure_months", 0))

    experience_score = (
        0.30 * years_score
        + 0.35 * product_score
        + 0.25 * production_score
        + 0.10 * tenure_score
    ) * features.get("job_hop_penalty", 1.0)

    return float(max(0.0, min(1.0, experience_score)))
