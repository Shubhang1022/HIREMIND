"""Dimension 6: Profile Integrity scorer.

Takes the pre-computed structured features dict and returns a float in
[0.0, 1.0].
"""
from __future__ import annotations


def _platform_trust_score(features: dict) -> float:
    """(verified_email + verified_phone + linkedin_connected) / 3."""
    verified = [
        int(bool(features.get("verified_email", False))),
        int(bool(features.get("verified_phone", False))),
        int(bool(features.get("linkedin_connected", False))),
    ]
    return sum(verified) / 3.0


def _consistency_score(features: dict) -> float:
    """Score based on derived_years_exp vs years_exp discrepancy."""
    derived = features.get("derived_years_exp", 0.0)
    stated = features.get("years_exp", 0.0)
    discrepancy = abs(derived - stated)
    if discrepancy <= 2:
        return 1.0
    elif discrepancy <= 5:
        return 0.7
    else:
        return 0.4


def score_profile_integrity(features: dict) -> float:
    """Compute the Profile Integrity dimension score.

    Parameters
    ----------
    features:
        Structured features dict produced by StructuredFeatureExtractor.

    Returns
    -------
    float
        Score in [0.0, 1.0].
    """
    completeness_raw = features.get("profile_completeness_score", 0.0)
    completeness = completeness_raw / 100.0

    trust = _platform_trust_score(features)
    consistency = _consistency_score(features)

    profile_integrity = (
        0.50 * completeness
        + 0.33 * trust
        + 0.17 * consistency
    )

    # Apply 0.8 multiplier for very incomplete profiles
    if completeness_raw < 40:
        profile_integrity *= 0.8

    return float(max(0.0, min(1.0, profile_integrity)))
