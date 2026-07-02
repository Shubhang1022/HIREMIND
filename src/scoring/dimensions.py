"""DimScores dataclass and DimensionScorer orchestrator.

Combines all 9 dimension scorers into a single convenience API.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from src.scoring.dim_semantic import score_required_skills
from src.scoring.dim_experience import score_relevant_experience
from src.scoring.dim_progression import score_career_progression
from src.scoring.dim_behavioral import score_behavioral_signals
from src.scoring.dim_integrity import score_profile_integrity


@dataclass
class DimScores:
    """Container for all 9 dimension scores plus the disqualifier multiplier.
    
    Fields are ordered to maintain strict backward compatibility with direct positional instantiations.
    """

    specialization_match: float
    required_skills_match: float
    relevant_experience: float
    semantic_similarity: float
    career_growth: float
    behavioral_fit: float
    integrity: float
    education: float
    disqualifier_multiplier: float  # 0.0 (disqualified) or 1.0 (valid)
    seniority_match: float = 0.5
    domain_expertise: float = 0.5

    def final_score(self, weights: dict | None = None) -> float:
        """Compute the weighted final score using the new hackathon-grade weighted breakdown (PART 3):
        - 35% Critical Skill Match (required_skills_match)
        - 25% Semantic Similarity (semantic_similarity)
        - 20% Relevant Experience (relevant_experience)
        - 15% Role Compatibility (specialization_match)
        - 5% Candidate Quality (average of career_growth, behavioral_fit, and integrity)
        """
        candidate_quality = (self.career_growth + self.behavioral_fit + self.integrity) / 3.0
        
        return (
            0.35 * self.required_skills_match
            + 0.25 * self.semantic_similarity
            + 0.20 * self.relevant_experience
            + 0.15 * self.specialization_match
            + 0.05 * candidate_quality
        ) * self.disqualifier_multiplier


class DimensionScorer:
    """Orchestrates all dimension scorers for a single candidate.

    Parameters
    ----------
    config:
        Optional configuration dict.
    jd_embedding:
        Optional pre-computed JD embedding array.
    jd_specialization:
        The Job Description specialization type (e.g. 'Retrieval Engineer').
    jd_dict:
        Optional Job Description dictionary.
    """

    def __init__(
        self,
        config: dict | None = None,
        jd_embedding: np.ndarray | None = None,
        jd_specialization: str = "Retrieval Engineer",
        jd_dict: dict | None = None,
    ) -> None:
        self.config = config or {}
        self.jd_embedding = jd_embedding
        self.jd_dict = jd_dict or {}
        self.jd_specialization = jd_specialization

        # Determine jd_seniority and jd_domains from jd_dict
        from src.intelligence.understanding import JobUnderstandingEngine
        engine = JobUnderstandingEngine()
        
        jd_text = (
            self.jd_dict.get("title", "")
            + " "
            + self.jd_dict.get("description", "")
            + " "
            + self.jd_dict.get("full_text", "")
        ).strip()
        
        if self.jd_dict and jd_text:
            job_und = engine.extract(jd_text)
            self.jd_seniority = job_und.seniority
            self.jd_domains = job_und.domains
            self.jd_specialization = job_und.role
        else:
            self.jd_seniority = "Senior"
            self.jd_domains = ["search/retrieval"]

    def score_all(self, features: dict, cosine_sim: float = 0.0) -> DimScores:
        """Compute all 9 dimension scores for *features*."""
        multiplier = 0.0 if features.get("is_disqualified", False) else 1.0

        # Import scorers here to avoid circular imports
        from src.scoring.dim_specialization import score_specialization_match
        from src.scoring.dim_education import score_education
        from src.scoring.dim_seniority import score_seniority_match
        from src.scoring.dim_domain import score_domain_expertise

        spec_score = score_specialization_match(features, self.jd_specialization)

        # Enforce hard filter:
        # 1. Project Managers / Operations Managers cannot outrank technical specialists
        #    unless they have strong technical/retrieval evidence.
        if features.get("candidate_specialization") in ("Project Manager", "Operations Manager") and spec_score <= 0.2:
            multiplier = 0.0

        # 2. Hard filtering for critical skills
        skills_score = score_required_skills(features, jd_dict=self.jd_dict)
        
        must_have = self.jd_dict.get("must_have_skills", []) or self.jd_dict.get("required_skills", []) or []
        if must_have:
            critical_hits = features.get("critical_skill_match", 0)
            important_hits = features.get("important_skill_match", 0)
            must_have_matches = critical_hits + important_hits
            overlap_ratio = must_have_matches / len(must_have)
            if overlap_ratio < 0.40 and must_have_matches < 2:
                multiplier = 0.0
        else:
            if int(features.get("core_jd_skill_count", 0)) == 0:
                multiplier = 0.0

        # Calculate other dimension scores
        education_score = score_education(features)
        domain_score = score_domain_expertise(
            features.get("candidate_intelligence", {}).get("domain_expertise", []),
            self.jd_domains
        )
        seniority_score = score_seniority_match(
            features.get("current_seniority_level", 2),
            self.jd_seniority
        )

        return DimScores(
            specialization_match=spec_score,
            required_skills_match=skills_score,
            relevant_experience=score_relevant_experience(features),
            semantic_similarity=cosine_sim,
            career_growth=score_career_progression(features),
            behavioral_fit=score_behavioral_signals(features),
            integrity=score_profile_integrity(features),
            education=education_score,
            disqualifier_multiplier=multiplier,
            seniority_match=seniority_score,
            domain_expertise=domain_score,
        )
