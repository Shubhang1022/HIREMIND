"""Dimension 1: Required Skills Match scorer.

Takes the pre-computed structured features dict and evaluates the match
quality and depth of required technical skills.
"""
from __future__ import annotations
import re


def check_skill_match(candidate_skills: list[str], target_skill: str, features: dict | None = None) -> bool:
    target_lower = target_skill.lower()
    
    if features is not None:
        if "vector" in target_lower or "database" in target_lower or "vdb" in target_lower:
            if features.get("has_vector_db", False):
                return True
        if "embedding" in target_lower or "retrieval" in target_lower:
            if features.get("has_embedding_retrieval", False):
                return True
        if "python" in target_lower:
            if features.get("has_python_advanced", False):
                return True
        if "eval" in target_lower or "ndcg" in target_lower or "mrr" in target_lower or "map" in target_lower:
            if features.get("has_evaluation_framework", False):
                return True

    for cs in candidate_skills:
        cs_lower = cs.lower()
        if target_lower in cs_lower or cs_lower in target_lower:
            return True
        words = [w for w in re.split(r"[/\s,()-]+", target_lower) if len(w) > 2]
        if words and all(w in cs_lower for w in words):
            return True
    return False


def score_required_skills(features: dict, jd_dict: dict | None = None) -> float:
    """Compute the Required Skills Match dimension score.

    Parameters
    ----------
    features:
        Structured features dict produced by StructuredFeatureExtractor.
    jd_dict:
        Optional Job Description dictionary containing must_have_skills and nice_to_have_skills.

    Returns
    -------
    float
        Score in [0.0, 1.0].
    """
    if not jd_dict:
        # Fallback to default logic
        core_coverage = features.get("core_jd_skill_count", 0) / 4.0
        depth = features.get("skill_depth_score", 0.0)

        assessment_scores: dict = features.get("skill_assessment_scores", {})
        if assessment_scores:
            avg_score = sum(assessment_scores.values()) / len(assessment_scores)
            assessment_boost = min(avg_score / 100.0, 1.0)
        else:
            assessment_boost = 0.5

        score = (
            0.60 * core_coverage
            + 0.30 * depth
            + 0.10 * assessment_boost
        )

        score *= features.get("keyword_stuffing_penalty", 1.0)
        score *= features.get("llm_only_recency_penalty", 1.0)

        return float(max(0.0, min(1.0, score)))

    # Dynamic Skill Weighting logic
    must_have = jd_dict.get("must_have_skills", []) or jd_dict.get("required_skills", []) or []
    nice_to_have = jd_dict.get("nice_to_have_skills", []) or jd_dict.get("preferred_skills", []) or []

    # Get candidate skills
    cand_intel = features.get("candidate_intelligence", {})
    if cand_intel:
        cand_skills = cand_intel.get("skills", [])
    else:
        cand_skills = [s.get("name", "") for s in features.get("skills", []) if s.get("name")]

    cand_skills_lower = [s.lower() for s in cand_skills]

    critical = []
    important = []
    optional = []

    crit_keywords = ["embedding", "retrieval", "vector", "database", "search", "recsys", "python"]

    for s in must_have:
        s_lower = s.lower()
        if any(kw in s_lower for kw in crit_keywords):
            critical.append(s)
        else:
            important.append(s)

    for s in nice_to_have:
        optional.append(s)

    critical_matches = [s for s in critical if check_skill_match(cand_skills_lower, s, features)]
    important_matches = [s for s in important if check_skill_match(cand_skills_lower, s, features)]
    optional_matches = [s for s in optional if check_skill_match(cand_skills_lower, s, features)]

    # Store match counts in features dict for export/reasoning/tie-breaking
    features["critical_skill_match"] = len(critical_matches)
    features["important_skill_match"] = len(important_matches)
    features["optional_skill_match"] = len(optional_matches)

    weighted_num = 3.0 * len(critical_matches) + 2.0 * len(important_matches) + 1.0 * len(optional_matches)
    weighted_den = 3.0 * len(critical) + 2.0 * len(important) + 1.0 * len(optional)

    skills_match_score = weighted_num / weighted_den if weighted_den > 0.0 else 1.0

    # Skill depth & assessment boost
    depth = features.get("skill_depth_score", 0.0)
    assessment_scores = features.get("skill_assessment_scores", {})
    if assessment_scores:
        avg_score = sum(assessment_scores.values()) / len(assessment_scores)
        assessment_boost = min(avg_score / 100.0, 1.0)
    else:
        assessment_boost = 0.5

    # Blended score
    score = (
        0.70 * skills_match_score
        + 0.20 * depth
        + 0.10 * assessment_boost
    )

    score *= features.get("keyword_stuffing_penalty", 1.0)
    score *= features.get("llm_only_recency_penalty", 1.0)

    return float(max(0.0, min(1.0, score)))


def calculate_critical_skill_coverage(features: dict, jd_dict: dict | None) -> tuple[int, int, float, list[str]]:
    """Compute candidate's critical skill coverage against must_have_skills from JD."""
    if not jd_dict:
        return 0, 0, 0.0, []
    must_have = jd_dict.get("must_have_skills", []) or jd_dict.get("required_skills", []) or []
    if not must_have:
        return 0, 0, 0.0, []
        
    cand_intel = features.get("candidate_intelligence", {})
    if cand_intel:
        cand_skills = cand_intel.get("skills", [])
    else:
        cand_skills = [s.get("name", "") if isinstance(s, dict) else str(s) for s in features.get("skills", [])]
        
    cand_skills_lower = [s.lower().strip() for s in cand_skills if s]
    
    matches = []
    matched_count = 0
    for s in must_have:
        if check_skill_match(cand_skills_lower, s, features):
            matched_count += 1
            matches.append(s)
            
    coverage_ratio = matched_count / len(must_have) if must_have else 0.0
    return matched_count, len(must_have), coverage_ratio, matches
