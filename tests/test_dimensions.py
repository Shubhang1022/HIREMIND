"""Tests for all 8 dimension scorers and the DimensionScorer orchestrator.

Run with:
    python -m pytest tests/test_dimensions.py -v
"""
from __future__ import annotations

import pytest

from src.scoring.dimensions import DimScores, DimensionScorer


# ---------------------------------------------------------------------------
# Minimal feature dict helpers
# ---------------------------------------------------------------------------

def _base_features() -> dict:
    """Return a minimal features dict with neutral/safe default values."""
    return {
        # Identity / disqualification
        "is_disqualified": False,
        "consulting_only": False,
        "candidate_specialization": "retrieval_search",
        # Skills
        "skill_depth_score": 0.5,
        "core_jd_skill_count": 2,
        "skill_assessment_scores": {},
        "keyword_stuffing_penalty": 1.0,
        "llm_only_recency_penalty": 1.0,
        # Experience
        "relevant_years_exp": 5.0,
        "years_exp": 5.0,
        "derived_years_exp": 5.0,
        "product_company_ratio": 0.8,
        "production_evidence_score": 0.5,
        "longest_tenure_months": 24,
        "job_hop_penalty": 1.0,
        # Progression
        "title_seniority_scores": [2],
        "max_company_size_band": 4,
        "leadership_evidence_score": 0.5,
        "seniority_trajectory_bonus": 0.0,
        "stagnation_penalty": 0.0,
        # Behavioral
        "open_to_work": False,
        "days_since_active": 30,
        "notice_period_days": 30,
        "recruiter_response_rate": 0.8,
        "avg_response_time_hours": 12.0,
        "github_activity_score": 50,
        "verified_email": True,
        "verified_phone": True,
        "linkedin_connected": True,
        "saved_by_recruiters_30d": 5,
        # Logistics / Location
        "location_fit_score": 1.0,
        "salary_alignment_score": 0.8,
        # Integrity
        "profile_completeness_score": 80.0,
        # Education
        "education_tier": "tier_1",
        "education_is_tech": True,
    }


# ---------------------------------------------------------------------------
# test_specialization_match
# ---------------------------------------------------------------------------

def test_specialization_match_management_disqualified():
    """A management candidate with no retrieval experience gets disqualified."""
    features = _base_features()
    features["candidate_specialization"] = "management"
    features["core_jd_skill_count"] = 0
    features["has_embedding_retrieval"] = False
    features["has_vector_db"] = False

    scorer = DimensionScorer(jd_specialization="retrieval_search")
    dim_scores = scorer.score_all(features, cosine_sim=0.8)

    assert dim_scores.specialization_match == 0.0
    assert dim_scores.disqualifier_multiplier == 0.0
    assert dim_scores.final_score() == 0.0


def test_specialization_match_retrieval_perfect():
    """A retrieval specialization candidate gets perfect score for retrieval JD."""
    features = _base_features()
    features["candidate_specialization"] = "retrieval_search"

    scorer = DimensionScorer(jd_specialization="retrieval_search")
    dim_scores = scorer.score_all(features, cosine_sim=0.8)

    assert dim_scores.specialization_match == 1.0
    assert dim_scores.disqualifier_multiplier == 1.0


# ---------------------------------------------------------------------------
# test_required_skills_match
# ---------------------------------------------------------------------------

def test_required_skills_match_good():
    """Candidate with core skills coverage and depth should score > 0.5."""
    features = _base_features()
    features["core_jd_skill_count"] = 4
    features["skill_depth_score"] = 0.8
    features["skill_assessment_scores"] = {"python": 90}

    scorer = DimensionScorer()
    dim_scores = scorer.score_all(features, cosine_sim=0.8)

    assert dim_scores.required_skills_match > 0.5


# ---------------------------------------------------------------------------
# test_relevant_experience_good
# ---------------------------------------------------------------------------

def test_relevant_experience_good():
    """Candidate with 5 years relevant experience should score > 0.5."""
    features = _base_features()
    features["relevant_years_exp"] = 5.0
    features["product_company_ratio"] = 0.9
    features["production_evidence_score"] = 0.8
    features["longest_tenure_months"] = 24

    scorer = DimensionScorer()
    dim_scores = scorer.score_all(features, cosine_sim=0.5)

    assert dim_scores.relevant_experience > 0.5


# ---------------------------------------------------------------------------
# test_education_scoring
# ---------------------------------------------------------------------------

def test_education_scoring():
    """Candidate with tier_1 technical degree should score 1.0."""
    features = _base_features()
    features["education_tier"] = "tier_1"
    features["education_is_tech"] = True

    scorer = DimensionScorer()
    dim_scores = scorer.score_all(features, cosine_sim=0.5)

    assert dim_scores.education == 1.0


# ---------------------------------------------------------------------------
# test_final_score_sum
# ---------------------------------------------------------------------------

def test_final_score_sum():
    """When all dimension scores are 1.0 and multiplier is 1.0, final_score
    should equal exactly 1.0."""
    dim_scores = DimScores(
        specialization_match=1.0,
        required_skills_match=1.0,
        relevant_experience=1.0,
        semantic_similarity=1.0,
        career_growth=1.0,
        behavioral_fit=1.0,
        integrity=1.0,
        education=1.0,
        disqualifier_multiplier=1.0,
    )
    assert dim_scores.final_score() == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# test_final_score_disqualified
# ---------------------------------------------------------------------------

def test_final_score_disqualified():
    """When disqualifier_multiplier is 0.0 final_score must be 0.0 regardless
    of individual dimension scores."""
    dim_scores = DimScores(
        specialization_match=0.9,
        required_skills_match=0.8,
        relevant_experience=0.7,
        semantic_similarity=0.85,
        career_growth=0.75,
        behavioral_fit=0.8,
        integrity=0.9,
        education=0.85,
        disqualifier_multiplier=0.0,
    )
    assert dim_scores.final_score() == pytest.approx(0.0, abs=1e-9)
