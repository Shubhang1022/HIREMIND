"""Dimension: Specialization Match scorer.

Takes the pre-computed structured features dict and evaluates the match
between candidate specialization and job role.
"""
from __future__ import annotations


def classify_jd_specialization(jd_dict: dict) -> str:
    """Classify the job description specialization into one of 12 roles."""
    from src.intelligence.understanding import JobUnderstandingEngine
    engine = JobUnderstandingEngine()
    jd_text = (
        jd_dict.get("title", "")
        + " "
        + jd_dict.get("description", "")
        + " "
        + jd_dict.get("full_text", "")
    )
    return engine._infer_role(jd_text.lower())


def score_specialization_match(features: dict, jd_specialization: str) -> float:
    """Score the candidate's specialization match to the job description."""
    candidate_spec = features.get("candidate_specialization", "Backend Engineer")

    # Map legacy strings for backwards compatibility with tests and old data
    if candidate_spec == "management":
        candidate_spec = "Project Manager"
    elif candidate_spec == "retrieval_search":
        candidate_spec = "Retrieval Engineer"
    elif candidate_spec == "ai_ml_general":
        candidate_spec = "ML Engineer"
    elif candidate_spec == "mlops_infra" or candidate_spec == "data_eng":
        candidate_spec = "MLOps Engineer"
    elif candidate_spec == "other_tech":
        candidate_spec = "Backend Engineer"

    if jd_specialization == "retrieval_search":
        jd_specialization = "Retrieval Engineer"
    elif jd_specialization == "ai_ml_general":
        jd_specialization = "ML Engineer"
    elif jd_specialization in ("mlops_infra", "data_eng"):
        jd_specialization = "MLOps Engineer"

    # 1. Exact match
    if candidate_spec == jd_specialization:
        return 1.0

    # Adjacency rules
    # Let's group roles by general family to assign similarity scores
    retrieval_family = {"Retrieval Engineer", "Search Engineer", "Recommendation Systems Engineer"}
    ai_ml_family = {"ML Engineer", "Data Scientist", "MLOps Engineer"}
    infra_family = {"DevOps Engineer", "Platform Engineer", "MLOps Engineer"}
    backend_family = {"Backend Engineer", "Platform Engineer", "Search Engineer"}
    frontend_family = {"Frontend Engineer", "Backend Engineer"}
    mgmt_family = {"Project Manager", "Operations Manager"}

    # Check family overlaps
    if candidate_spec in retrieval_family and jd_specialization in retrieval_family:
        return 0.8

    if candidate_spec in retrieval_family and jd_specialization == "ML Engineer":
        return 0.7
    if jd_specialization in retrieval_family and candidate_spec == "ML Engineer":
        return 0.7

    if candidate_spec in ai_ml_family and jd_specialization in ai_ml_family:
        return 0.7

    if candidate_spec in infra_family and jd_specialization in infra_family:
        return 0.8

    if candidate_spec in backend_family and jd_specialization in backend_family:
        return 0.7

    if candidate_spec in frontend_family and jd_specialization in frontend_family:
        return 0.6

    if candidate_spec in mgmt_family and jd_specialization in mgmt_family:
        return 0.7

    # Technical adjacencies (cross-family)
    is_cand_tech = candidate_spec not in mgmt_family
    is_jd_tech = jd_specialization not in mgmt_family

    if is_cand_tech and is_jd_tech:
        return 0.4  # Default match between different tech roles

    # Management to Tech or vice versa
    if candidate_spec in mgmt_family and jd_specialization in retrieval_family:
        has_emb = bool(features.get("has_embedding_retrieval", False))
        has_vdb = bool(features.get("has_vector_db", False))
        core_count = int(features.get("core_jd_skill_count", 0))
        if (has_emb and has_vdb) or core_count >= 2:
            return 0.4
        return 0.0

    if candidate_spec in mgmt_family and jd_specialization in ai_ml_family:
        if int(features.get("ai_ml_skill_count", 0)) >= 5:
            return 0.4
        return 0.0

    return 0.1  # Minimal match for unrelated roles
