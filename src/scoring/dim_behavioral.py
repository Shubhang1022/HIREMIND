"""Dimension 4: Behavioral Signals & Engagement scorer.

Takes the pre-computed structured features dict and returns a float in
[0.0, 1.0].  Formulas follow docs/RankingLogic.md §6.
"""
from __future__ import annotations

import math


def _recency_score(days_since_active: int) -> float:
    """Activity recency sub-score."""
    if days_since_active <= 7:
        return 1.0
    elif days_since_active <= 30:
        return 0.8
    elif days_since_active <= 60:
        return 0.6
    elif days_since_active <= 90:
        return 0.4
    else:
        return 0.2


def _notice_score(notice_period_days: int) -> float:
    """Notice-period sub-score (for hiring readiness)."""
    if notice_period_days <= 30:
        return 1.0
    elif notice_period_days <= 60:
        return 0.7
    elif notice_period_days <= 90:
        return 0.5
    else:
        return 0.3


def _hiring_readiness_score(features: dict) -> float:
    """0.4×open_to_work + 0.3×recency + 0.3×notice (RankingLogic.md §6.1)."""
    otw = 1.0 if features.get("open_to_work", False) else 0.0
    recency = _recency_score(features.get("days_since_active", 0))
    notice = _notice_score(features.get("notice_period_days", 0))
    return 0.4 * otw + 0.3 * recency + 0.3 * notice


def _recruiter_engagement_score(features: dict) -> float:
    """0.6×response_rate + 0.4×(1 - avg_time/168) (RankingLogic.md §6.2)."""
    response_rate = features.get("recruiter_response_rate", 0.0)
    avg_hours = features.get("avg_response_time_hours", 0.0)
    time_score = max(0.0, 1.0 - avg_hours / 168.0)
    return 0.6 * response_rate + 0.4 * time_score


def _platform_trust_score(features: dict) -> float:
    """(verified_email + verified_phone + linkedin_connected) / 3 (§6.3)."""
    verified = [
        int(bool(features.get("verified_email", False))),
        int(bool(features.get("verified_phone", False))),
        int(bool(features.get("linkedin_connected", False))),
    ]
    return sum(verified) / 3.0


def _github_normalized(features: dict) -> tuple[float, float]:
    """Return (normalized_score, effective_weight) (§6.4).

    If github_activity_score == -1 → neutral 0.5, weight 0.05.
    Otherwise → score/100, weight 0.15.
    """
    raw = features.get("github_activity_score", -1)
    if raw == -1:
        return 0.5, 0.05
    return min(float(raw) / 100.0, 1.0), 0.15


def _market_validation_score(features: dict) -> float:
    """log1p(saved) / log(51), capped at 1.0 (§6.5)."""
    saved = features.get("saved_by_recruiters_30d", 0)
    return min(math.log1p(saved) / math.log(51), 1.0)


def score_behavioral_signals(features: dict) -> float:
    """Compute the Behavioral Signals & Engagement dimension score.

    Parameters
    ----------
    features:
        Structured features dict produced by StructuredFeatureExtractor.

    Returns
    -------
    float
        Score in [0.0, 1.0].
    """
    hiring = _hiring_readiness_score(features)
    engagement = _recruiter_engagement_score(features)
    trust = _platform_trust_score(features)
    github_norm, github_weight = _github_normalized(features)
    market = _market_validation_score(features)

    # When github_weight is reduced (0.05 instead of 0.15), redistribute the
    # 0.10 difference to hiring_readiness so weights still sum to 1.0.
    hiring_weight = 0.30 + (0.15 - github_weight)

    behavioral_signals = (
        hiring_weight * hiring
        + 0.25 * engagement
        + 0.20 * trust
        + github_weight * github_norm
        + 0.10 * market
    )

    return float(max(0.0, min(1.0, behavioral_signals)))
