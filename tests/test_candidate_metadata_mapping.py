"""
Regression tests for candidate metadata mapping, strict payload validation,
and integrity regression protection.
"""
from __future__ import annotations

import pytest
import numpy as np
import asyncio

import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_ROOT = _PROJECT_ROOT / "backend"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from src.ranking.engine import UnifiedRankingEngine, validate_ranking_payload, resolve_candidate_metadata


class MockEncoder:
    """Mock encoder to avoid loading sentence_transformers during fast metadata mapping tests.

    Default dimension 384 matches the production model BAAI/bge-small-en-v1.5.
    """
    def __init__(self, dim: int = 384) -> None:
        self.embedding_dim = dim

    def encode_single(self, text: str, normalize: bool = True, bge_mode: str = "query") -> np.ndarray:
        return np.ones((self.embedding_dim,), dtype=np.float32)

    def encode_batch(self, texts: list[str], normalize: bool = True, bge_mode: str = "passage") -> np.ndarray:
        return np.ones((len(texts), self.embedding_dim), dtype=np.float32)


def test_candidate_metadata_mapping() -> None:
    """Verify that candidate details are correctly mapped from the original profile."""
    candidate = {
        "candidate_id": "CAND_TEST_001",
        "profile": {
            "candidate_name": "Atharv Joshi",
            "current_title": "Frontend Engineer",
            "years_of_experience": 8.3,
            "current_company": "Acme Corp",
            "location": "Pune",
            "country": "India"
        },
        "skills": [
            {"name": "React", "proficiency": "intermediate"},
            {"name": "TypeScript", "proficiency": "intermediate"},
            {"name": "JavaScript", "proficiency": "intermediate"}
        ],
        "career_history": [
            {
                "company": "Acme Corp",
                "title": "Frontend Engineer",
                "duration_months": 36,
                "is_current": True
            }
        ]
    }
    
    jd = {
        "title": "Frontend Engineer",
        "description": "We need a Frontend Engineer experienced with React and TypeScript.",
        "must_have_skills": ["React", "TypeScript"]
    }

    from app.api.v1.endpoints.platform import enrich_candidate_with_intelligence
    candidate = enrich_candidate_with_intelligence(candidate)

    encoder = MockEncoder()
    engine = UnifiedRankingEngine(encoder=encoder, config={"apply_eligibility": False})

    results, _, _ = asyncio.run(engine.rank_candidates(
        candidates=[candidate],
        jd_dict=jd,
        top_n=5,
        call_llm=False
    ))

    assert len(results) == 1
    res = results[0]
    assert res["candidate_id"] == "CAND_TEST_001"
    assert res["candidate_name"] == "Atharv Joshi"
    assert res["current_title"] == "Frontend Engineer"
    assert res["current_company"] == "Acme Corp"
    assert res["location"] == "Pune"
    assert res["years_of_experience"] == 8.3


def test_integrity_regression_protection() -> None:
    """Verify that healthy profiles score high on integrity, while incomplete profiles score low."""
    # 1. Healthy, verified, complete candidate profile
    healthy_candidate = {
        "candidate_id": "CAND_HEALTHY_01",
        "profile": {
            "candidate_name": "Healthy Candidate",
            "current_title": "Engineer",
            "years_of_experience": 5.0,
            "current_company": "Great Co"
        },
        "skills": [
            {"name": "Python", "proficiency": "intermediate"},
            {"name": "SQL", "proficiency": "intermediate"}
        ],
        "career_history": [
            {
                "company": "Great Co",
                "title": "Engineer",
                "duration_months": 60,  # 5 years, matches stated YOE exactly
                "is_current": True
            }
        ],
        "redrob_signals": {
            "profile_completeness_score": 90.0,
            "verified_email": True,
            "verified_phone": True,
            "linkedin_connected": True
        }
    }

    # 2. Incomplete, unverified candidate profile
    incomplete_candidate = {
        "candidate_id": "CAND_INCOMPLETE_02",
        "profile": {
            "candidate_name": "Incomplete Candidate",
            "years_of_experience": 10.0  # Big discrepancy against career history duration (empty)
        },
        "skills": [],
        "career_history": [],
        "redrob_signals": {
            "profile_completeness_score": 10.0,  # very low completeness
            "verified_email": False,
            "verified_phone": False,
            "linkedin_connected": False
        }
    }

    jd = {
        "title": "Software Engineer",
        "description": "General software engineering role"
    }

    from app.api.v1.endpoints.platform import enrich_candidate_with_intelligence
    healthy_candidate = enrich_candidate_with_intelligence(healthy_candidate)
    incomplete_candidate = enrich_candidate_with_intelligence(incomplete_candidate)

    encoder = MockEncoder()
    engine = UnifiedRankingEngine(encoder=encoder, config={"apply_eligibility": False})

    results, _, _ = asyncio.run(engine.rank_candidates(
        candidates=[healthy_candidate, incomplete_candidate],
        jd_dict=jd,
        top_n=5,
        call_llm=False
    ))

    results_map = {r["candidate_id"]: r for r in results}
    
    assert "CAND_HEALTHY_01" in results_map
    assert "CAND_INCOMPLETE_02" in results_map
    
    healthy_integrity = results_map["CAND_HEALTHY_01"]["integrity_score"]
    incomplete_integrity = results_map["CAND_INCOMPLETE_02"]["integrity_score"]

    # Assert healthy integrity > 0.5 and incomplete integrity < 0.5
    assert healthy_integrity > 0.5, f"Expected healthy integrity > 0.5, got {healthy_integrity}"
    assert incomplete_integrity < 0.5, f"Expected incomplete integrity < 0.5, got {incomplete_integrity}"


def test_payload_validation_rules() -> None:
    """Verify that validate_ranking_payload strictly catches invalid ranking items."""
    # Healthy case should pass without raising any exception
    healthy_payload = [{
        "candidate_id": "CAND_OK",
        "candidate_name": "Healthy Name",
        "years_of_experience": 5.0,
        "integrity_score": 0.9,
        "rank": 1,
        "ai_score": 0.8
    }]
    validate_ranking_payload(healthy_payload)

    # 1. Missing candidate_id
    with pytest.raises(ValueError, match="candidate_id is missing"):
        validate_ranking_payload([{
            "candidate_name": "Name",
            "years_of_experience": 5.0,
            "integrity_score": 0.9,
            "rank": 1,
            "ai_score": 0.8
        }])

    # 2. Duplicate candidate_id
    with pytest.raises(ValueError, match="Duplicate candidate_id"):
        validate_ranking_payload([
            {"candidate_id": "CAND_DUP", "candidate_name": "A", "years_of_experience": 1.0, "integrity_score": 0.5, "rank": 1, "ai_score": 0.9},
            {"candidate_id": "CAND_DUP", "candidate_name": "B", "years_of_experience": 2.0, "integrity_score": 0.6, "rank": 2, "ai_score": 0.8}
        ])

    # 3. Blank name
    with pytest.raises(ValueError, match="blank name"):
        validate_ranking_payload([{
            "candidate_id": "CAND_01",
            "candidate_name": "",
            "years_of_experience": 5.0,
            "integrity_score": 0.9,
            "rank": 1,
            "ai_score": 0.8
        }])

    # 4. Placeholder name "Candidate"
    with pytest.raises(ValueError, match="name is placeholder 'Candidate'"):
        validate_ranking_payload([{
            "candidate_id": "CAND_01",
            "candidate_name": "Candidate",
            "years_of_experience": 5.0,
            "integrity_score": 0.9,
            "rank": 1,
            "ai_score": 0.8
        }])

    # 5. Negative experience
    with pytest.raises(ValueError, match="negative or missing experience"):
        validate_ranking_payload([{
            "candidate_id": "CAND_01",
            "candidate_name": "Valid Name",
            "years_of_experience": -1.5,
            "integrity_score": 0.9,
            "rank": 1,
            "ai_score": 0.8
        }])

    # 6. Integrity score out of bounds
    with pytest.raises(ValueError, match="integrity score .* is out of bounds"):
        validate_ranking_payload([{
            "candidate_id": "CAND_01",
            "candidate_name": "Valid Name",
            "years_of_experience": 5.0,
            "integrity_score": 1.2,
            "rank": 1,
            "ai_score": 0.8
        }])

    # 7. Duplicate rank
    with pytest.raises(ValueError, match="Duplicate rank"):
        validate_ranking_payload([
            {"candidate_id": "CAND_1", "candidate_name": "A", "years_of_experience": 1.0, "integrity_score": 0.5, "rank": 1, "ai_score": 0.9},
            {"candidate_id": "CAND_2", "candidate_name": "B", "years_of_experience": 2.0, "integrity_score": 0.6, "rank": 1, "ai_score": 0.8}
        ])
