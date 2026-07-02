"""Candidate Quality Engine.

Calculates the Candidate Quality Score (0-100) based on profile completeness,
career consistency, project quality, certification quality, experience depth,
and skill diversity.
"""
from __future__ import annotations


def calculate_candidate_quality_score(features: dict, candidate: dict) -> float:
    """Calculate the candidate quality score on a [0.0, 1.0] scale.

    Parameters
    ----------
    features : dict
        Flat feature dictionary from StructuredFeatureExtractor.
    candidate : dict
        Raw candidate dictionary.

    Returns
    -------
    float
        Quality score in range [0.0, 1.0].
    """
    # 1. Profile completeness (20% weight)
    profile_completeness = float(features.get("profile_completeness_score", 0.0) or 0.0) / 100.0

    # 2. Career consistency (25% weight)
    hop_penalty = float(features.get("job_hop_penalty", 1.0) or 1.0)
    longest_tenure = float(features.get("longest_tenure_months", 0.0) or 0.0)
    tenure_score = min(longest_tenure / 36.0, 1.0)  # 36 months is perfect
    consistency = 0.5 * hop_penalty + 0.5 * tenure_score

    # 3. Project quality (15% weight)
    all_desc = " ".join(r.get("description", "").lower() for r in candidate.get("career_history", []) or [])
    project_keywords = ["project", "build", "developed", "designed", "architected", "delivered", "deployed"]
    project_hits = sum(1 for kw in project_keywords if kw in all_desc)
    project_score = min(project_hits / 10.0, 1.0)

    # 4. Certification quality (15% weight)
    certs = candidate.get("certifications", []) or []
    cert_score = min(len(certs) * 0.3, 1.0)

    # 5. Experience depth (15% weight)
    total_yoe = float(features.get("years_exp", 0.0) or 0.0)
    rel_yoe = float(features.get("relevant_years_exp", 0.0) or 0.0)
    depth_score = (rel_yoe / total_yoe) if total_yoe > 0 else 0.0
    depth_score = min(depth_score, 1.0)

    # 6. Skill diversity (10% weight)
    skills = candidate.get("skills", []) or []
    diversity_score = min(len(skills) / 15.0, 1.0)

    quality_score = (
        0.20 * profile_completeness
        + 0.25 * consistency
        + 0.15 * project_score
        + 0.15 * cert_score
        + 0.15 * depth_score
        + 0.10 * diversity_score
    )
    return float(max(0.0, min(1.0, quality_score)))
