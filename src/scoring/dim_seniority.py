"""Dimension: Seniority Match scorer.

Compares candidate seniority level with job description seniority level.
"""
from __future__ import annotations


def map_seniority_string_to_int(seniority: str) -> int:
    """Map a seniority string to an integer level 1-4."""
    s = str(seniority).lower().strip()
    if "junior" in s or "entry" in s or "intern" in s:
        return 1
    elif "mid" in s or "engineer" in s or "developer" in s:
        # Default mid
        if "senior" in s or "lead" in s or "staff" in s or "principal" in s or "architect" in s:
            pass  # proceed to check senior/staff
        else:
            return 2
    
    if "senior" in s or "sr" in s:
        return 3
    elif "staff" in s or "lead" in s or "principal" in s or "architect" in s or "director" in s or "vp" in s:
        return 4
    
    return 2  # default fallback to Mid


def score_seniority_match(candidate_seniority_level: int | str, job_seniority: str) -> float:
    """Score the seniority match between candidate and job description.

    Exact match = 1.0; 1 level difference = 0.7; >1 level difference = 0.2.
    """
    # Candidate seniority level can be an integer 1-6 (from StructuredFeatureExtractor)
    # or a string like "Senior"
    if isinstance(candidate_seniority_level, int):
        # Map 1-6 to 1-4 scale
        if candidate_seniority_level == 1:
            cand_val = 1
        elif candidate_seniority_level == 2:
            cand_val = 2
        elif candidate_seniority_level == 3:
            cand_val = 3
        else:
            cand_val = 4
    else:
        cand_val = map_seniority_string_to_int(str(candidate_seniority_level))

    job_val = map_seniority_string_to_int(job_seniority)

    diff = abs(cand_val - job_val)
    if diff == 0:
        return 1.0
    elif diff == 1:
        return 0.7
    else:
        return 0.2
