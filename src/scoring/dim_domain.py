"""Dimension: Domain Match scorer.

Evaluates the overlap of domains between candidate domains and job domains.
"""
from __future__ import annotations


def score_domain_expertise(candidate_domains: list[str], job_domains: list[str]) -> float:
    """Calculate the domain expertise match score on a [0.0, 1.0] scale.

    Parameters
    ----------
    candidate_domains : list[str]
        Domains extracted from candidate profile.
    job_domains : list[str]
        Domains required by job description.

    Returns
    -------
    float
        Domain score in range [0.0, 1.0].
    """
    if not job_domains:
        return 1.0

    cand_set = {d.strip().lower() for d in candidate_domains if d}
    job_set = {d.strip().lower() for d in job_domains if d}

    if not job_set:
        return 1.0

    matches = sum(1 for d in job_set if d in cand_set or any(d in cd or cd in d for cd in cand_set))
    return float(max(0.0, min(1.0, matches / len(job_set))))
