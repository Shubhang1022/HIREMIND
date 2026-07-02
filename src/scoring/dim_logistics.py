"""Dimension 5: Location & Logistics Fit scorer.

Takes the pre-computed structured features dict and returns a float in
[0.0, 1.0].
"""
from __future__ import annotations


def _notice_period_score(notice_period_days: int) -> float:
    """Notice-period tier scoring for logistics (5 tiers including >180)."""
    if notice_period_days <= 30:
        return 1.0
    elif notice_period_days <= 60:
        return 0.7
    elif notice_period_days <= 90:
        return 0.5
    elif notice_period_days <= 180:
        return 0.3
    else:
        return 0.1


def score_logistics_fit(features: dict) -> float:
    """Compute the Location & Logistics Fit dimension score.

    Parameters
    ----------
    features:
        Structured features dict produced by StructuredFeatureExtractor.

    Returns
    -------
    float
        Score in [0.0, 1.0].
    """
    location_score = features.get("location_fit_score", 0.0)
    notice_score = _notice_period_score(features.get("notice_period_days", 0))
    salary_score = features.get("salary_alignment_score", 0.0)

    logistics_fit = (
        0.50 * location_score
        + 0.30 * notice_score
        + 0.20 * salary_score
    )

    return float(max(0.0, min(1.0, logistics_fit)))
