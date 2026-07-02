"""Structured feature extraction from a raw candidate dict.

Produces the full ~50-feature dict described in docs/DatabaseSchema.md §3.1.
"""
from __future__ import annotations

import re
from datetime import datetime
from math import log, log1p
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Reference date for all temporal calculations
# ---------------------------------------------------------------------------
REFERENCE_DATE = datetime(2026, 6, 30)

# ---------------------------------------------------------------------------
# Consulting firm list (RankingLogic.md §2.2)
# ---------------------------------------------------------------------------
CONSULTING_FIRMS: frozenset[str] = frozenset({
    "tcs",
    "tata consultancy services",
    "wipro",
    "infosys",
    "accenture",
    "cognizant",
    "capgemini",
    "hcl technologies",
    "hcl",
    "tech mahindra",
    "mphasis",
    "hexaware",
    "niit technologies",
    "ltimindtree",
    "mindtree",
    "l&t infotech",
    "igate",
    "mastech",
})

# ---------------------------------------------------------------------------
# Hard-disqualifier title lists (RankingLogic.md §2.3)
# ---------------------------------------------------------------------------
HARD_DISQUALIFIER_TITLES: frozenset[str] = frozenset({
    "accountant",
    "chartered accountant",
    "ca",
    "graphic designer",
    "ui designer",
    "ux designer",
    "content writer",
    "copywriter",
    "civil engineer",
    "mechanical engineer",
    "structural engineer",
    "hr manager",
    "human resources",
    "recruiter",
    "talent acquisition",
    "customer support",
    "customer service",
    "call center",
    "sales executive",
    "sales manager",
    "retail manager",
})

AI_TITLE_KEYWORDS: frozenset[str] = frozenset({
    "ml",
    "machine learning",
    "ai",
    "artificial intelligence",
    "data scientist",
    "data engineer",
    "nlp",
    "computer vision",
    "deep learning",
    "research engineer",
    "software engineer",
    "backend engineer",
    "platform engineer",
    "mlops",
    "analytics engineer",
    "quantitative",
    "algorithm",
})

# ---------------------------------------------------------------------------
# Seniority level mapping (RankingLogic.md §5.1)
# ---------------------------------------------------------------------------
SENIORITY_LEVELS: dict[int, list[str]] = {
    1: ["junior", "associate", "entry", "trainee", "intern"],
    2: ["engineer", "analyst", "developer", "scientist"],  # no modifier = mid
    3: ["senior", "sr.", "sr "],
    4: ["lead", "tech lead", "technical lead", "staff"],
    5: ["principal", "architect", "distinguished"],
    6: ["director", "vp", "head of", "manager of engineering", "cto", "chief"],
}

# ---------------------------------------------------------------------------
# Company-size band mapping (ordinal encoding)
# ---------------------------------------------------------------------------
_COMPANY_SIZE_MAP: dict[str, int] = {
    "1-10": 1,
    "11-50": 2,
    "51-200": 3,
    "201-500": 4,
    "501-1000": 5,
    "1001-5000": 6,
    "5001-10000": 7,
    "10001+": 8,
}

# ---------------------------------------------------------------------------
# JD Must-have skill clusters (RankingLogic.md §3.3)
# ---------------------------------------------------------------------------
JD_MUST_HAVE_SKILLS: dict[str, list[str]] = {
    "embedding_retrieval": [
        "sentence-transformers",
        "sentence transformers",
        "bge",
        "e5",
        "openai embeddings",
        "dense retrieval",
        "bi-encoder",
        "cross-encoder",
        "semantic search",
        "embedding search",
    ],
    "vector_db": [
        "pinecone",
        "weaviate",
        "qdrant",
        "milvus",
        "faiss",
        "opensearch",
        "elasticsearch",
        "vector search",
        "hybrid search",
        "ann",
    ],
    "python_advanced": ["python"],
    "evaluation_framework": [
        "ndcg",
        "mrr",
        "map",
        "mean average precision",
        "precision@k",
        "learning to rank",
        "ltr",
        "ranker",
        "ranking evaluation",
        "offline evaluation",
        "a/b test",
        "a/b testing",
    ],
}

# AI/ML skill keywords for counting ai_ml_skill_count
AI_ML_SKILL_KEYWORDS: list[str] = [
    "machine learning",
    "ml",
    "deep learning",
    "neural",
    "nlp",
    "natural language processing",
    "computer vision",
    "pytorch",
    "tensorflow",
    "transformers",
    "embedding",
    "vector",
    "faiss",
    "sentence-transformers",
    "sentence transformers",
    "bge",
    "e5",
    "pinecone",
    "weaviate",
    "qdrant",
    "milvus",
    "elasticsearch",
    "opensearch",
    "ndcg",
    "mrr",
    "ltr",
    "ranker",
    "xgboost",
    "lightgbm",
    "sklearn",
    "scikit-learn",
    "hugging face",
    "huggingface",
    "bert",
    "gpt",
    "llm",
    "lora",
    "qlora",
    "peft",
    "langchain",
    "rag",
    "retrieval",
    "recommendation",
    "mlops",
    "data science",
    "data scientist",
    "ai",
    "artificial intelligence",
]

# ---------------------------------------------------------------------------
# Keyword stuffing detection (RankingLogic.md §3.5)
# ---------------------------------------------------------------------------
AI_BUZZWORDS: frozenset[str] = frozenset({
    "langchain",
    "rag",
    "retrieval augmented",
    "pinecone",
    "openai",
    "chatgpt",
    "gpt-4",
    "llama",
    "mistral",
    "vector database",
    "embedding",
    "langsmith",
    "crewai",
    "autogen",
    "llamaindex",
    "chroma",
    "weaviate",
})

PRODUCTION_EVIDENCE_KEYWORDS: list[str] = [
    "deployed",
    "production",
    "real users",
    "a/b",
    "latency",
    "index",
    "serving",
]

# ---------------------------------------------------------------------------
# Production signals (RankingLogic.md §4.3)
# ---------------------------------------------------------------------------
PRODUCTION_SIGNALS_STRONG: list[str] = [
    r"deployed to production",
    r"in production",
    r"serving production",
    r"real users",
    r"serving \d+ users",
    r"a/b test",
    r"a/b testing",
    r"latency sla",
    r"p99 latency",
    r"throughput",
    r"requests per second",
    r"index refresh",
    r"embedding drift",
    r"retrieval regression",
    r"ndcg",
    r"mrr",
    r"offline evaluation",
    r"online evaluation",
    r"ranking system",
    r"recommendation system",
    r"search engine",
]

PRODUCTION_SIGNALS_MEDIUM: list[str] = [
    "deployed",
    "production system",
    "model serving",
    "inference pipeline",
    "model monitoring",
    "feature store",
    "end-to-end",
    "at scale",
    "high availability",
    "model pipeline",
]

PRODUCTION_SIGNALS_WEAK: list[str] = [
    "trained a model",
    "built a model",
    "ml model",
    "experimented",
    "prototyped",
    "poc",
    "proof of concept",
    "kaggle",
]

# ---------------------------------------------------------------------------
# Leadership evidence patterns (RankingLogic.md §5.3)
# ---------------------------------------------------------------------------
LEADERSHIP_PATTERNS: list[tuple[str, str]] = [
    (r"led\s+(?:a\s+)?team\s+of\s+(\d+)", "team_lead"),
    (r"managed\s+(?:a\s+)?team\s+of\s+(\d+)", "team_manage"),
    (r"led\s+(\d+)\s+engineers?", "team_lead"),
    (r"technical\s+lead", "tech_lead"),
    (r"architected\s+(?:the|a)\s+\w+", "architected"),
    (r"owned\s+(?:the|a)\s+\w+\s+(?:system|platform|service)", "owned_system"),
    (r"mentored?\s+\w+", "mentored"),
    (r"cross[\s-]functional", "cross_functional"),
]

# ---------------------------------------------------------------------------
# Honeypot detection (RankingLogic.md §2.1)
# ---------------------------------------------------------------------------
NON_TECHNICAL_TITLES_HP: frozenset[str] = frozenset({
    "marketing manager",
    "accountant",
    "graphic designer",
    "content writer",
    "sales executive",
    "hr manager",
    "operations manager",
    "customer support",
})

TECHNICAL_DESC_KEYWORDS_HP: frozenset[str] = frozenset({
    "vector database",
    "embedding",
    "transformer",
    "neural network",
    "machine learning",
    "deep learning",
    "faiss",
    "pinecone",
    "qdrant",
    "sentence-transformers",
    "fine-tun",
    "llm",
    "bert",
    "gpt",
})

# Location scoring sets
PREFERRED_CITIES: frozenset[str] = frozenset({"pune", "noida"})
ACCEPTABLE_CITIES: frozenset[str] = frozenset({
    "hyderabad",
    "mumbai",
    "delhi",
    "gurgaon",
    "gurugram",
    "bangalore",
    "bengaluru",
    "chennai",
    "kolkata",
    "ahmedabad",
})

# Salary targets
TARGET_SALARY_MIN = 25.0
TARGET_SALARY_MAX = 55.0

# Proficiency weight map
PROFICIENCY_WEIGHTS: dict[str, float] = {
    "beginner": 0.25,
    "intermediate": 0.5,
    "advanced": 0.75,
    "expert": 1.0,
}


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _parse_date(date_str: str | None) -> datetime | None:
    """Parse an ISO date string to datetime; return None on failure."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(str(date_str))
    except (ValueError, TypeError):
        return None


def _company_size_band(size_str: str | None) -> int:
    """Convert a company_size string to ordinal band 1-8; 0 if unknown."""
    if not size_str:
        return 0
    return _COMPANY_SIZE_MAP.get(str(size_str).strip(), 0)


def _is_consulting(company_name: str) -> bool:
    """Return True if the company name matches a known consulting firm."""
    name_lower = company_name.lower().strip()
    return any(cf in name_lower for cf in CONSULTING_FIRMS)


def infer_seniority(title: str) -> int:
    """Map a job title string to a seniority level 1-6.

    Levels:
      1 = Junior/Intern
      2 = Mid-level (default)
      3 = Senior
      4 = Lead/Staff
      5 = Principal/Architect
      6 = Director/VP/Head
    """
    title_lower = title.lower()
    for level in range(6, 0, -1):
        if any(kw in title_lower for kw in SENIORITY_LEVELS[level]):
            return level
    return 2  # default to mid-level


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------

class StructuredFeatureExtractor:
    """Extract all structured features from a single candidate dict."""

    def extract(
        self,
        candidate: dict[str, Any],
        batch_idx: int = 0,
        position_in_batch: int = 0,
    ) -> dict[str, Any]:
        """Return a flat feature dict for *candidate*.

        Parameters
        ----------
        candidate:
            Raw candidate dict matching the Redrob candidate JSON schema.
        batch_idx:
            Batch index (pass-through metadata, default 0).
        position_in_batch:
            Position within the batch (pass-through metadata, default 0).
        """
        profile = candidate.get("profile", {})
        career_history = candidate.get("career_history", [])
        skills = candidate.get("skills", [])
        signals = candidate.get("redrob_signals", {})

        # ------------------------------------------------------------------ #
        # Identity                                                             #
        # ------------------------------------------------------------------ #
        candidate_id: str = candidate.get("candidate_id", "")

        # ------------------------------------------------------------------ #
        # Experience features                                                  #
        # ------------------------------------------------------------------ #
        years_exp: float = float(profile.get("years_of_experience", 0.0))

        total_duration_months: int = sum(
            int(r.get("duration_months", 0)) for r in career_history
        )
        derived_years_exp: float = total_duration_months / 12.0
        career_history_count: int = len(career_history)

        current_role_months: int = 0
        longest_tenure_months: int = 0
        for role in career_history:
            dur = int(role.get("duration_months", 0))
            if role.get("is_current", False):
                current_role_months = dur
            if dur > longest_tenure_months:
                longest_tenure_months = dur

        # ------------------------------------------------------------------ #
        # Company type features                                                #
        # ------------------------------------------------------------------ #
        product_company_months: int = 0
        consulting_company_months: int = 0
        for role in career_history:
            dur = int(role.get("duration_months", 0))
            if _is_consulting(role.get("company", "")):
                consulting_company_months += dur
            else:
                product_company_months += dur

        product_company_ratio: float = (
            product_company_months / total_duration_months
            if total_duration_months > 0
            else 0.0
        )

        consulting_only: bool = _compute_consulting_only(career_history)

        # Company size bands
        max_company_size_band: int = 0
        for role in career_history:
            band = _company_size_band(role.get("company_size"))
            if band > max_company_size_band:
                max_company_size_band = band

        current_company_size_band: int = _company_size_band(
            profile.get("current_company_size")
        )

        # ------------------------------------------------------------------ #
        # Title/seniority features                                             #
        # ------------------------------------------------------------------ #
        # Sort career history by start_date ascending for seniority sequence
        sorted_history = sorted(
            career_history,
            key=lambda r: str(r.get("start_date", "")),
        )
        title_seniority_scores: list[int] = [
            infer_seniority(r.get("title", "")) for r in sorted_history
        ]

        current_seniority_level: int = infer_seniority(
            profile.get("current_title", "")
        )

        seniority_trend: float = _compute_seniority_trend(title_seniority_scores)

        non_technical_title_only: bool = _is_non_technical_no_ai(
            profile, career_history
        )

        # ------------------------------------------------------------------ #
        # Skills features                                                      #
        # ------------------------------------------------------------------ #
        total_skills_count: int = len(skills)

        ai_ml_skill_count: int = sum(
            1
            for s in skills
            if any(kw in s.get("name", "").lower() for kw in AI_ML_SKILL_KEYWORDS)
        )

        core_jd_skill_count: int = _count_core_jd_skills(skills)

        skill_depth_score: float = _compute_skill_depth_score(skills, signals)

        # Boolean capability flags
        has_embedding_retrieval: bool = _skill_matches_cluster(
            skills, JD_MUST_HAVE_SKILLS["embedding_retrieval"]
        )
        has_vector_db: bool = _skill_matches_cluster(
            skills, JD_MUST_HAVE_SKILLS["vector_db"]
        )
        has_python_advanced: bool = _has_python_advanced(skills)
        has_evaluation_framework: bool = _skill_matches_cluster(
            skills, JD_MUST_HAVE_SKILLS["evaluation_framework"]
        )

        keyword_stuffing_penalty: float = _compute_keyword_stuffing_penalty(
            skills, career_history
        )
        llm_only_recency_penalty: float = _compute_llm_recency_penalty(
            career_history
        )

        # ------------------------------------------------------------------ #
        # Production evidence                                                  #
        # ------------------------------------------------------------------ #
        production_evidence_score: float = _compute_production_evidence_score(
            career_history
        )

        all_desc_lower = " ".join(
            r.get("description", "").lower() for r in career_history
        )
        has_ab_testing: bool = bool(
            re.search(r"a/b\s*test", all_desc_lower)
        )
        has_latency_sla: bool = any(
            kw in all_desc_lower
            for kw in ("latency", "throughput", "sla", "p99", "requests per second")
        )
        has_real_users: bool = bool(
            re.search(r"real users|serving \d+ users", all_desc_lower)
        )

        # ------------------------------------------------------------------ #
        # Career progression                                                   #
        # ------------------------------------------------------------------ #
        seniority_trajectory_bonus: float = _compute_seniority_trajectory_bonus(
            title_seniority_scores
        )

        job_hop_penalty: float = _compute_job_hop_penalty(career_history)

        stagnation_penalty: float = _compute_stagnation_penalty(
            career_history, title_seniority_scores, sorted_history, current_role_months
        )

        leadership_evidence_score: float = _compute_leadership_evidence_score(
            career_history
        )

        # ------------------------------------------------------------------ #
        # Behavioral signals                                                   #
        # ------------------------------------------------------------------ #
        open_to_work: bool = bool(signals.get("open_to_work_flag", False))

        last_active_str: str = signals.get("last_active_date", "")
        last_active_dt = _parse_date(last_active_str)
        days_since_active: int = (
            (REFERENCE_DATE - last_active_dt).days if last_active_dt else 0
        )

        notice_period_days: int = int(signals.get("notice_period_days", 0))
        recruiter_response_rate: float = float(
            signals.get("recruiter_response_rate", 0.0)
        )
        avg_response_time_hours: float = float(
            signals.get("avg_response_time_hours", 0.0)
        )
        github_activity_score: float = float(
            signals.get("github_activity_score", -1)
        )
        interview_completion_rate: float = float(
            signals.get("interview_completion_rate", 0.0)
        )
        offer_acceptance_rate: float = float(
            signals.get("offer_acceptance_rate", -1)
        )
        verified_email: bool = bool(signals.get("verified_email", False))
        verified_phone: bool = bool(signals.get("verified_phone", False))
        linkedin_connected: bool = bool(signals.get("linkedin_connected", False))
        saved_by_recruiters_30d: int = int(
            signals.get("saved_by_recruiters_30d", 0)
        )
        profile_completeness_score: float = float(
            signals.get("profile_completeness_score", 0.0)
        )
        skill_assessment_scores: dict = dict(
            signals.get("skill_assessment_scores", {})
        )

        # ------------------------------------------------------------------ #
        # Location / logistics                                                 #
        # ------------------------------------------------------------------ #
        country: str = profile.get("country", "")
        raw_location: str = profile.get("location", "")
        location_city: str = raw_location.split(",")[0].strip() if raw_location else ""

        location_fit_score: float = _compute_location_fit_score(
            raw_location, country, signals.get("willing_to_relocate", False)
        )

        salary_range = signals.get("expected_salary_range_inr_lpa", {})
        salary_min_lpa: float = float(salary_range.get("min", 0.0))
        salary_max_lpa: float = float(salary_range.get("max", 0.0))
        salary_alignment_score: float = _compute_salary_alignment(
            salary_min_lpa, salary_max_lpa
        )

        preferred_work_mode: str = signals.get("preferred_work_mode", "")
        willing_to_relocate: bool = bool(signals.get("willing_to_relocate", False))

        # ------------------------------------------------------------------ #
        # Honeypot detection                                                   #
        # ------------------------------------------------------------------ #
        is_honeypot, honeypot_flags, honeypot_suspicion_score = _detect_honeypot(
            candidate
        )

        # ------------------------------------------------------------------ #
        # Disqualifier determination                                           #
        # ------------------------------------------------------------------ #
        is_disqualified = False
        disqualifier_reason = ""

        if is_honeypot:
            is_disqualified = True
            disqualifier_reason = "honeypot"
        elif consulting_only:
            is_disqualified = True
            disqualifier_reason = "consulting_only"
        elif non_technical_title_only:
            is_disqualified = True
            disqualifier_reason = "non_technical"

        # ------------------------------------------------------------------ #
        # Custom additions: Specialization, Relevant Exp, Education          #
        # ------------------------------------------------------------------ #
        education_list = candidate.get("education", [])
        education_tier, education_is_tech = _extract_education_features(education_list)
        relevant_years_exp = _compute_relevant_experience(career_history)
        candidate_specialization, specialization_confidence = _classify_specialization_with_confidence(profile, career_history, skills)
        candidate_role_category = classify_candidate_role_category(profile, career_history, skills)
        candidate_type = "management" if candidate_role_category in ("PROJECT_MANAGEMENT", "PRODUCT_MANAGEMENT", "HR", "MARKETING") else "technical"
        domains = _extract_domains(profile, career_history)

        # ------------------------------------------------------------------ #
        # Assemble and return                                                  #
        # ------------------------------------------------------------------ #
        features_so_far = {
            "candidate_specialization": candidate_specialization,
            "specialization_confidence": specialization_confidence,
            "candidate_role_category": candidate_role_category,
            "candidate_type": candidate_type,
            "relevant_years_exp": relevant_years_exp,
            "education_tier": education_tier,
            "education_is_tech": education_is_tech,
            # Identity
            "candidate_id": candidate_id,
            "batch_idx": batch_idx,
            "position_in_batch": position_in_batch,
            # Experience
            "years_exp": years_exp,
            "derived_years_exp": derived_years_exp,
            "career_history_count": career_history_count,
            "total_duration_months": total_duration_months,
            "current_role_months": current_role_months,
            "longest_tenure_months": longest_tenure_months,
            # Company type
            "product_company_months": product_company_months,
            "consulting_company_months": consulting_company_months,
            "product_company_ratio": product_company_ratio,
            "consulting_only": consulting_only,
            "max_company_size_band": max_company_size_band,
            "current_company_size_band": current_company_size_band,
            # Title/seniority
            "title_seniority_scores": title_seniority_scores,
            "current_seniority_level": current_seniority_level,
            "seniority_trend": seniority_trend,
            "non_technical_title_only": non_technical_title_only,
            # Skills
            "total_skills_count": total_skills_count,
            "ai_ml_skill_count": ai_ml_skill_count,
            "core_jd_skill_count": core_jd_skill_count,
            "skill_depth_score": skill_depth_score,
            "has_embedding_retrieval": has_embedding_retrieval,
            "has_vector_db": has_vector_db,
            "has_python_advanced": has_python_advanced,
            "has_evaluation_framework": has_evaluation_framework,
            "keyword_stuffing_penalty": keyword_stuffing_penalty,
            "llm_only_recency_penalty": llm_only_recency_penalty,
            # Production evidence
            "production_evidence_score": production_evidence_score,
            "has_ab_testing": has_ab_testing,
            "has_latency_sla": has_latency_sla,
            "has_real_users": has_real_users,
            # Career progression
            "seniority_trajectory_bonus": seniority_trajectory_bonus,
            "job_hop_penalty": job_hop_penalty,
            "stagnation_penalty": stagnation_penalty,
            "leadership_evidence_score": leadership_evidence_score,
            # Behavioral signals
            "open_to_work": open_to_work,
            "days_since_active": days_since_active,
            "notice_period_days": notice_period_days,
            "recruiter_response_rate": recruiter_response_rate,
            "avg_response_time_hours": avg_response_time_hours,
            "github_activity_score": github_activity_score,
            "interview_completion_rate": interview_completion_rate,
            "offer_acceptance_rate": offer_acceptance_rate,
            "verified_email": verified_email,
            "verified_phone": verified_phone,
            "linkedin_connected": linkedin_connected,
            "saved_by_recruiters_30d": saved_by_recruiters_30d,
            "profile_completeness_score": profile_completeness_score,
            "skill_assessment_scores": skill_assessment_scores,
            # Location / logistics
            "country": country,
            "location_city": location_city,
            "location_fit_score": location_fit_score,
            "salary_min_lpa": salary_min_lpa,
            "salary_max_lpa": salary_max_lpa,
            "salary_alignment_score": salary_alignment_score,
            "preferred_work_mode": preferred_work_mode,
            "willing_to_relocate": willing_to_relocate,
            # Honeypot flags
            "is_honeypot": is_honeypot,
            "honeypot_flags": honeypot_flags,
            "honeypot_suspicion_score": honeypot_suspicion_score,
            "is_disqualified": is_disqualified,
            "disqualifier_reason": disqualifier_reason,
        }

        from src.scoring.quality import calculate_candidate_quality_score
        quality_score = calculate_candidate_quality_score(features_so_far, candidate)
        features_so_far["candidate_quality_score"] = quality_score

        candidate_intelligence = {
            "candidate_type": candidate_type,
            "specialization": candidate_specialization,
            "experience_years": years_exp,
            "relevant_experience": relevant_years_exp,
            "skills": [s.get("name", "") for s in skills if s.get("name")],
            "candidate_quality_score": quality_score,
            "education_tier": education_tier,
            "education_is_tech": education_is_tech,
            "domain_expertise": domains,
            "specialization_confidence": specialization_confidence
        }
        features_so_far["candidate_intelligence"] = candidate_intelligence

        return features_so_far


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------

def _compute_consulting_only(career_history: list) -> bool:
    """Return True if every role is at a consulting firm."""
    if not career_history:
        return False
    for role in career_history:
        if not _is_consulting(role.get("company", "")):
            return False
    return True


def _compute_seniority_trend(levels: list[int]) -> float:
    """Linear regression slope of seniority_levels vs position index.

    Returns 0.0 if fewer than 2 roles.
    """
    if len(levels) < 2:
        return 0.0
    x = np.arange(len(levels), dtype=float)
    y = np.array(levels, dtype=float)
    # numpy polyfit gives [slope, intercept]
    slope = float(np.polyfit(x, y, 1)[0])
    return slope


def _is_non_technical_no_ai(profile: dict, career_history: list) -> bool:
    """Return True if current title is a hard disqualifier AND no AI history."""
    current_title = profile.get("current_title", "").lower()
    is_current_nontechnical = any(t in current_title for t in HARD_DISQUALIFIER_TITLES)
    if not is_current_nontechnical:
        return False
    # Check if any career_history entry has an AI/ML title
    for role in career_history:
        title_lower = role.get("title", "").lower()
        if any(k in title_lower for k in AI_TITLE_KEYWORDS):
            return False
    return True


def _skill_matches_cluster(skills: list, keywords: list[str]) -> bool:
    """Return True if any skill name matches any keyword in the cluster."""
    for s in skills:
        name_lower = s.get("name", "").lower()
        if any(kw in name_lower for kw in keywords):
            return True
    return False


def _has_python_advanced(skills: list) -> bool:
    """Return True if Python skill is at 'advanced' or 'expert' level."""
    for s in skills:
        if "python" in s.get("name", "").lower():
            if s.get("proficiency", "") in ("advanced", "expert"):
                return True
    return False


def _count_core_jd_skills(skills: list) -> int:
    """Count how many of the 4 JD must-have clusters are covered."""
    count = 0
    skills_lower = [s.get("name", "").lower() for s in skills]
    for _cluster, keywords in JD_MUST_HAVE_SKILLS.items():
        if any(any(kw in s for kw in keywords) for s in skills_lower):
            count += 1
    return count


def _compute_skill_depth_score(skills: list, signals: dict) -> float:
    """Compute weighted skill depth across 4 must-have JD clusters.

    Formula per skill match in a cluster:
        score = prof_weight × (0.6 + 0.4 × min(duration_months/24, 1.0))
                             × (1 + 0.3 × log1p(endorsements)/log(51))
    Best-matching skill per cluster is used; averaged over 4 clusters.
    """
    assessments = signals.get("skill_assessment_scores", {})

    total_score = 0.0
    for _cluster_name, keywords in JD_MUST_HAVE_SKILLS.items():
        cluster_score = 0.0
        for skill in skills:
            skill_lower = skill.get("name", "").lower()
            if any(kw in skill_lower for kw in keywords):
                prof_w = PROFICIENCY_WEIGHTS.get(skill.get("proficiency", "beginner"), 0.25)
                dur_months = float(skill.get("duration_months", 0))
                duration_score = min(dur_months / 24.0, 1.0)
                endorsements = float(skill.get("endorsements", 0))
                endorsement_score = log1p(endorsements) / log(51)

                raw = prof_w * (0.6 + 0.4 * duration_score) * (1 + 0.3 * endorsement_score)

                # Assessment boost
                assessment_boost = 0.0
                for assess_key, assess_val in assessments.items():
                    if any(kw in assess_key.lower() for kw in keywords):
                        assessment_boost = (float(assess_val) / 100.0) * 0.2
                        break

                cluster_score = max(cluster_score, raw + assessment_boost)

        total_score += cluster_score

    return total_score / len(JD_MUST_HAVE_SKILLS)


def _compute_keyword_stuffing_penalty(skills: list, career_history: list) -> float:
    """Return 0.4 if severe keyword stuffing detected, 0.7 if mild, else 1.0."""
    skills_lower = [s.get("name", "").lower() for s in skills]
    buzzword_count = sum(
        1 for s in skills_lower if any(b in s for b in AI_BUZZWORDS)
    )

    if buzzword_count < 6:
        return 1.0

    all_descriptions = " ".join(
        r.get("description", "").lower() for r in career_history
    )
    production_hits = sum(
        1 for e in PRODUCTION_EVIDENCE_KEYWORDS if e in all_descriptions
    )

    all_titles = [r.get("title", "").lower() for r in career_history]
    technical_titles = sum(
        1 for t in all_titles if any(k in t for k in AI_TITLE_KEYWORDS)
    )

    if production_hits < 2 and technical_titles == 0:
        return 0.4  # Strong stuffing penalty
    elif production_hits < 2:
        return 0.7  # Mild penalty

    return 1.0


def _compute_llm_recency_penalty(career_history: list) -> float:
    """Return 0.7 if all AI experience < 12 months old, 0.85 if < 24 months, else 1.0."""
    ai_exp_dates: list[datetime] = []
    for role in career_history:
        desc_lower = role.get("description", "").lower()
        has_ai = any(
            k in desc_lower
            for k in ("machine learning", "neural", "embedding", "model", "ml ", "ai ")
        )
        if has_ai:
            start = _parse_date(role.get("start_date"))
            if start:
                ai_exp_dates.append(start)

    if not ai_exp_dates:
        return 1.0

    earliest_ai = min(ai_exp_dates)
    months_of_ai_exp = (REFERENCE_DATE - earliest_ai).days / 30.44

    if months_of_ai_exp < 12:
        return 0.7
    elif months_of_ai_exp < 24:
        return 0.85

    return 1.0


def _compute_production_evidence_score(career_history: list) -> float:
    """Compute production evidence score 0.0–1.0 from description keywords."""
    all_desc = " ".join(r.get("description", "").lower() for r in career_history)

    strong_hits = sum(
        1 for p in PRODUCTION_SIGNALS_STRONG if re.search(p, all_desc)
    )
    medium_hits = sum(
        1 for p in PRODUCTION_SIGNALS_MEDIUM if p in all_desc
    )
    weak_hits = sum(
        1 for p in PRODUCTION_SIGNALS_WEAK if p in all_desc
    )

    score = min(
        1.0,
        (strong_hits * 0.20) + (medium_hits * 0.08) + (weak_hits * 0.02),
    )
    return score


def _compute_seniority_trajectory_bonus(levels: list[int]) -> float:
    """Return 0.2 if seniority sequence is non-decreasing, else 0.0."""
    if len(levels) <= 1:
        return 0.0
    is_non_decreasing = all(
        levels[i] <= levels[i + 1] for i in range(len(levels) - 1)
    )
    return 0.2 if is_non_decreasing else 0.0


def _compute_job_hop_penalty(career_history: list) -> float:
    """Return 0.85 or 0.70 if job-hopper pattern detected, else 1.0.

    Uses the last 6 years window and counts short tenures (<18 months).
    """
    if len(career_history) <= 1:
        return 1.0

    past_roles = sorted(
        [r for r in career_history if not r.get("is_current", False)],
        key=lambda r: str(r.get("start_date", "")),
    )

    if not past_roles:
        return 1.0

    recent_years = 6
    cutoff = datetime(
        REFERENCE_DATE.year - recent_years,
        REFERENCE_DATE.month,
        REFERENCE_DATE.day,
    )

    recent_short = sum(
        1
        for r in past_roles
        if (
            _parse_date(r.get("start_date")) is not None
            and _parse_date(r.get("start_date")) > cutoff
            and int(r.get("duration_months", 0)) < 18
        )
    )

    if recent_short <= 2:
        return 1.0
    elif recent_short <= 3:
        return 0.85
    else:
        return 0.70


def _compute_stagnation_penalty(
    career_history: list,
    title_seniority_scores: list[int],
    sorted_history: list,
    current_role_months: int,
) -> float:
    """Return 0.1 if current_role_months > 48 AND same seniority as previous role."""
    if current_role_months <= 48:
        return 0.0
    if len(title_seniority_scores) < 2:
        return 0.0
    # Current seniority is the last in sorted_history
    current_seniority = title_seniority_scores[-1]
    previous_seniority = title_seniority_scores[-2]
    if current_seniority == previous_seniority:
        return 0.1
    return 0.0


def _compute_leadership_evidence_score(career_history: list) -> float:
    """Compute leadership score 0.0–1.0 from description patterns."""
    all_desc = " ".join(r.get("description", "").lower() for r in career_history)

    hits = 0
    max_team_size = 0
    for pattern, label in LEADERSHIP_PATTERNS:
        match = re.search(pattern, all_desc)
        if match:
            hits += 1
            if label in ("team_lead", "team_manage"):
                try:
                    size = int(match.group(1))
                    max_team_size = max(max_team_size, size)
                except (IndexError, ValueError):
                    pass

    base_score = min(hits / 4.0, 0.8)
    team_size_bonus = min(max_team_size / 20.0, 0.2)
    return min(base_score + team_size_bonus, 1.0)


def _compute_location_fit_score(
    location: str, country: str, willing_to_relocate: bool
) -> float:
    """Compute location fit score per RankingLogic.md §7.1."""
    location_lower = location.lower()
    country_lower = country.lower()

    if any(city in location_lower for city in PREFERRED_CITIES):
        return 1.0
    elif any(city in location_lower for city in ACCEPTABLE_CITIES):
        return 0.85
    elif country_lower == "india":
        return 0.7 if willing_to_relocate else 0.55
    else:
        return 0.4 if willing_to_relocate else 0.2


def _compute_salary_alignment(salary_min: float, salary_max: float) -> float:
    """Compute salary alignment score per RankingLogic.md §7.2."""
    overlap_start = max(salary_min, TARGET_SALARY_MIN)
    overlap_end = min(salary_max, TARGET_SALARY_MAX)

    if overlap_end <= overlap_start:
        if salary_max < TARGET_SALARY_MIN:
            return 0.5  # Expects less — could negotiate
        else:
            return 0.3  # Expects more than ceiling

    overlap = overlap_end - overlap_start
    candidate_range = salary_max - salary_min
    target_range = TARGET_SALARY_MAX - TARGET_SALARY_MIN

    overlap_fraction = overlap / min(candidate_range + 1, target_range)

    if overlap_fraction >= 0.7:
        return 1.0
    elif overlap_fraction >= 0.4:
        return 0.8
    else:
        return 0.6


def _detect_honeypot(
    candidate: dict,
) -> tuple[bool, list[str], float]:
    """Run all honeypot checks; return (is_honeypot, flags, suspicion_score)."""
    career_history = candidate.get("career_history", [])
    skills = candidate.get("skills", [])
    profile = candidate.get("profile", {})
    years_exp = float(profile.get("years_of_experience", 1.0))

    flags: list[str] = []
    suspicion_score: float = 0.0

    # Flag 1: Impossible tenure
    if _check_tenure_impossible(career_history):
        flags.append("impossible_tenure")
        return True, flags, 1.0

    # Flag 2: Expert skill with zero duration_months
    if _check_expert_zero_duration(skills):
        flags.append("expert_zero_duration")
        return True, flags, 1.0

    # Flag 3: Skills-to-experience ratio
    ratio_score = _check_skills_ratio(skills, years_exp)
    if ratio_score == 1.0:
        flags.append("skills_ratio_definitive")
        return True, flags, 1.0
    elif ratio_score == 0.5:
        flags.append("skills_ratio_suspicion")
        suspicion_score += 0.5

    # Flag 4: Title–description mismatch
    mismatch_count = _check_title_desc_mismatch(career_history)
    if mismatch_count >= 3:
        flags.append("title_desc_mismatch_definitive")
        return True, flags, 1.0
    elif mismatch_count >= 2:
        flags.append("title_desc_mismatch_suspicion")
        suspicion_score += 0.3

    is_honeypot_flag = suspicion_score >= 1.0
    if is_honeypot_flag and "suspicion_threshold_exceeded" not in flags:
        flags.append("suspicion_threshold_exceeded")

    return is_honeypot_flag, flags, suspicion_score


def _check_tenure_impossible(career_history: list) -> bool:
    """Return True if any role has duration_months > company_max_age + tolerance."""
    reference_year = REFERENCE_DATE.year
    for role in career_history:
        start_dt = _parse_date(role.get("start_date"))
        if start_dt is None:
            continue
        start_year = start_dt.year
        company_max_age_months = (reference_year - start_year) * 12
        dur = int(role.get("duration_months", 0))
        if dur > company_max_age_months + 12:
            return True
    return False


def _check_expert_zero_duration(skills: list) -> bool:
    """Return True if any expert skill has duration_months == 0."""
    for skill in skills:
        if (
            skill.get("proficiency") == "expert"
            and skill.get("duration_months", 1) == 0
        ):
            return True
    return False


def _check_skills_ratio(skills: list, years_exp: float) -> float:
    """Return 1.0 (definitive), 0.5 (suspicion), or 0.0 (clean)."""
    expert_advanced = sum(
        1 for s in skills if s.get("proficiency") in ("expert", "advanced")
    )
    ratio = expert_advanced / max(years_exp, 1.0)
    if ratio > 2.0:
        return 1.0
    elif ratio > 1.5:
        return 0.5
    return 0.0


def _check_title_desc_mismatch(career_history: list) -> int:
    """Count roles where a non-technical title has a technical description."""
    mismatch_count = 0
    for role in career_history:
        title_lower = role.get("title", "").lower()
        desc_lower = role.get("description", "").lower()
        is_non_tech_title = any(t in title_lower for t in NON_TECHNICAL_TITLES_HP)
        is_tech_desc = (
            sum(1 for k in TECHNICAL_DESC_KEYWORDS_HP if k in desc_lower) >= 3
        )
        if is_non_tech_title and is_tech_desc:
            mismatch_count += 1
    return mismatch_count


ROLES_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "Retrieval Engineer": {
        "titles": ["retrieval engineer", "embedding engineer", "retrieval specialist", "retrieval systems engineer"],
        "skills": ["sentence-transformers", "sentence transformers", "bge", "e5", "openai embeddings", "dense retrieval", "bi-encoder", "cross-encoder", "semantic search", "embedding search", "vector database", "pinecone", "weaviate", "qdrant", "milvus", "faiss"],
        "desc": ["retrieval", "dense retrieval", "bi-encoder", "cross-encoder", "vector search", "embedding search", "semantic search", "hybrid search", "ann", "vector database", "pinecone", "weaviate", "qdrant", "milvus", "faiss"]
    },
    "Search Engineer": {
        "titles": ["search engineer", "information retrieval engineer", "ir engineer", "search specialist"],
        "skills": ["elasticsearch", "opensearch", "solr", "lucene", "hybrid search", "bm25", "inverted index", "keyword search"],
        "desc": ["elasticsearch", "opensearch", "solr", "lucene", "hybrid search", "bm25", "inverted index", "keyword search", "search engine", "information retrieval"]
    },
    "Recommendation Systems Engineer": {
        "titles": ["recommendation engineer", "recommender systems engineer", "recsys engineer", "recommendation systems engineer"],
        "skills": ["collaborative filtering", "matrix factorization", "recency-bias", "recsys", "candidate generation", "two-tower model"],
        "desc": ["collaborative filtering", "matrix factorization", "recency-bias", "recsys", "candidate generation", "two-tower", "recommendation system", "recommender system"]
    },
    "ML Engineer": {
        "titles": ["ml engineer", "machine learning engineer", "deep learning engineer", "ai engineer", "computer vision engineer", "nlp engineer"],
        "skills": ["pytorch", "tensorflow", "transformers", "xgboost", "scikit-learn", "neural network", "deep learning", "keras", "hugging face", "huggingface", "llm", "bert", "gpt"],
        "desc": ["machine learning", "deep learning", "neural network", "pytorch", "tensorflow", "transformers", "xgboost", "scikit-learn", "hugging face", "huggingface", "llm", "bert", "gpt", "fine-tuning", "lora", "qlora"]
    },
    "Data Scientist": {
        "titles": ["data scientist", "applied scientist", "quantitative researcher", "ds"],
        "skills": ["pandas", "numpy", "statistics", "regression", "a/b testing", "data analysis", "jupyter", "r programming"],
        "desc": ["data scientist", "applied scientist", "data analysis", "statistics", "regression", "a/b testing", "pandas", "numpy", "data insight"]
    },
    "MLOps Engineer": {
        "titles": ["mlops engineer", "ml platform engineer", "ml infrastructure engineer"],
        "skills": ["mlflow", "kubeflow", "triton", "sagemaker", "dvc", "wandb", "model serving", "model monitoring", "bentoml"],
        "desc": ["mlops", "model serving", "model monitoring", "mlflow", "kubeflow", "triton", "sagemaker", "dvc", "wandb", "ml platform", "ml infrastructure"]
    },
    "DevOps Engineer": {
        "titles": ["devops engineer", "site reliability engineer", "sre", "cloud engineer"],
        "skills": ["docker", "kubernetes", "terraform", "ansible", "jenkins", "ci/cd", "prometheus", "grafana"],
        "desc": ["devops", "site reliability", "sre", "docker", "kubernetes", "terraform", "ansible", "jenkins", "ci/cd", "prometheus", "grafana", "deployment pipeline"]
    },
    "Platform Engineer": {
        "titles": ["platform engineer", "infrastructure engineer", "systems engineer"],
        "skills": ["aws", "gcp", "azure", "distributed systems", "kubernetes", "iam", "apache kafka", "rabbitmq"],
        "desc": ["platform engineer", "infrastructure engineer", "aws", "gcp", "azure", "distributed systems", "iam", "kubernetes", "kafka", "rabbitmq"]
    },
    "Backend Engineer": {
        "titles": ["backend engineer", "software engineer - backend", "backend developer", "software engineer"],
        "skills": ["python", "go", "java", "fastapi", "django", "postgresql", "redis", "apis", "rest api", "sql", "mysql"],
        "desc": ["backend", "software engineer", "fastapi", "django", "postgresql", "redis", "rest api", "api development", "database schema"]
    },
    "Frontend Engineer": {
        "titles": ["frontend engineer", "ui engineer", "web developer", "frontend developer", "javascript developer"],
        "skills": ["react", "next.js", "typescript", "javascript", "css", "html", "vue", "angular", "tailwind"],
        "desc": ["frontend", "ui engineer", "ux", "react", "next.js", "typescript", "javascript", "css", "html", "vue", "angular", "web design", "frontend development"]
    },
    "Project Manager": {
        "titles": ["project manager", "program manager", "scrum master", "pmo", "pm"],
        "skills": ["agile", "scrum", "jira", "pmp", "resource planning", "budgeting", "project planning"],
        "desc": ["project manager", "program manager", "scrum master", "pmo", "agile", "scrum", "jira", "resource planning", "budgeting", "project planning", "stakeholder management"]
    },
    "Operations Manager": {
        "titles": ["operations manager", "operations lead", "coo", "operations specialist"],
        "skills": ["supply chain", "logistics", "vendor management", "process optimization", "standard operating procedures", "sop"],
        "desc": ["operations manager", "operations lead", "supply chain", "logistics", "vendor management", "process optimization", "standard operating procedures", "sop"]
    }
}

def _classify_specialization_with_confidence(profile: dict, career_history: list, skills: list) -> tuple[str, float]:
    current_title = str(profile.get("current_title", "")).lower()
    headline = str(profile.get("headline", "")).lower()
    summary = str(profile.get("summary", "")).lower()
    all_skills = [str(s.get("name", "")).lower() for s in skills if s.get("name")]
    
    past_titles = [str(r.get("title", "")).lower() for r in career_history if r.get("title")]
    past_descs = [str(r.get("description", "")).lower() for r in career_history if r.get("description")]
    
    scores = {}
    for role_name, config in ROLES_KEYWORDS.items():
        score = 0.0
        
        # 1. Check current title
        for t_kw in config["titles"]:
            if t_kw in current_title:
                score += 8.0
                
        # 2. Check headline
        for t_kw in config["titles"]:
            if t_kw in headline:
                score += 4.0
                
        # 3. Check past titles
        for t_kw in config["titles"]:
            for pt in past_titles:
                if t_kw in pt:
                    score += 3.0
                    
        # 4. Check skills
        for s_kw in config["skills"]:
            for sk in all_skills:
                if s_kw in sk:
                    score += 2.0
                    
        # 5. Check descriptions and summary
        for d_kw in config["desc"]:
            if d_kw in summary:
                score += 1.0
            for desc in past_descs:
                if d_kw in desc:
                    score += 0.5
                    
        scores[role_name] = score
        
    best_role = "Backend Engineer"
    max_score = 0.0
    for r, s in scores.items():
        if s > max_score:
            max_score = s
            best_role = r
            
    if max_score == 0.0:
        mgmt_words = ["manager", "scrum", "pm", "pmo", "director", "agile", "operations", "coo", "lead"]
        if any(w in current_title or w in headline for w in mgmt_words):
            if "operations" in current_title or "operations" in headline:
                best_role = "Operations Manager"
            else:
                best_role = "Project Manager"
        else:
            best_role = "Backend Engineer"
            
    # Calculate confidence rating
    has_direct_title = any(t_kw in current_title or any(t_kw in pt for pt in past_titles) for t_kw in ROLES_KEYWORDS[best_role]["titles"])
    
    skill_hits = sum(1 for s in ROLES_KEYWORDS[best_role]["skills"] if any(s in sk for sk in all_skills))
    desc_hits = sum(1 for d in ROLES_KEYWORDS[best_role]["desc"] if d in summary or any(d in desc for desc in past_descs))
    supporting_hits = skill_hits + desc_hits
    
    if max_score == 0.0:
        confidence = 0.1
    elif has_direct_title and supporting_hits >= 2:
        confidence = 1.0
    elif has_direct_title or supporting_hits >= 3:
        confidence = 0.7
    elif supporting_hits >= 1:
        confidence = 0.4
    else:
        confidence = 0.1
        
    return best_role, confidence

def _extract_domains(profile: dict, career_history: list) -> list[str]:
    headline = str(profile.get("headline", "")).lower()
    summary = str(profile.get("summary", "")).lower()
    all_desc = " ".join(str(r.get("description", "") or "") for r in career_history).lower()
    text = headline + " " + summary + " " + all_desc
    
    domain_hints = {
        "search/retrieval": ["retrieval", "semantic search", "vector", "embedding", "ann", "hybrid search"],
        "ranking/recsys": ["ranking", "ranker", "recommendation", "recommender", "ltr", "lambdamart", "ndcg", "mrr"],
        "nlp/llm": ["nlp", "transformer", "bert", "gpt", "llm", "fine-tun", "lora", "qlora", "peft"],
        "cv": ["computer vision", "opencv", "image", "detection", "segmentation"],
        "mlops/serving": ["mlops", "model serving", "inference", "monitoring", "feature store", "drift"],
        "data/analytics": ["sql", "warehouse", "spark", "airflow", "etl", "dbt", "analytics"],
    }
    
    out = []
    for domain, hints in domain_hints.items():
        if any(h in text for h in hints):
            out.append(domain)
    return out


def _compute_relevant_experience(career_history: list) -> float:
    relevant_months = 0
    for role in career_history:
        title = role.get("title", "").lower()
        desc = role.get("description", "").lower()
        
        # Keywords
        title_keywords = ["ml", "machine learning", "ai", "artificial intelligence", "data scientist", "deep learning", "nlp", "computer vision", "retrieval", "search", "vector", "embedding", "recommendation", "recommender", "recsys"]
        desc_keywords = ["machine learning", "deep learning", "pytorch", "tensorflow", "neural network", "llm", "transformer", "nlp", "retrieval", "vector database", "embedding-based", "semantic search", "hybrid search", "ndcg", "mrr", "map", "pinecone", "weaviate", "qdrant", "milvus", "faiss"]
        
        is_relevant = any(kw in title for kw in title_keywords) or any(kw in desc for kw in desc_keywords)
        if is_relevant:
            relevant_months += int(role.get("duration_months", 0))
            
    return relevant_months / 12.0


def _extract_education_features(education_list: list) -> tuple[str, bool]:
    highest_tier = "tier_3"
    is_tech = False
    
    tier_map = {"tier_1": 3, "tier_2": 2, "tier_3": 1}
    highest_val = 0
    
    tech_keywords = ["computer science", "computer engineering", "information technology", "data science", "machine learning", "artificial intelligence", "mathematics", "statistics", "software engineering"]
    
    for edu in education_list:
        tier = edu.get("tier", "tier_3")
        if tier in tier_map:
            if tier_map[tier] > highest_val:
                highest_val = tier_map[tier]
                highest_tier = tier
                
        field_of_study = str(edu.get("field_of_study", "")).lower()
        if any(kw in field_of_study for kw in tech_keywords):
            is_tech = True
            
    return highest_tier, is_tech


ROLE_CATEGORY_KEYWORDS = {
    "MLOPS": {
        "titles": ["mlops", "ml platform", "ml infrastructure", "model serving", "ml ops", "machine learning ops"],
        "skills": ["mlflow", "kubeflow", "triton", "sagemaker", "dvc", "wandb", "bentoml", "tfx", "seldon"],
        "desc": ["mlops", "model serving", "model monitoring", "ml platform", "ml infrastructure", "triton server", "inference pipeline"]
    },
    "DEVOPS": {
        "titles": ["devops", "site reliability", "sre", "cloud engineer", "infrastructure engineer", "platform engineer", "systems engineer"],
        "skills": ["docker", "kubernetes", "terraform", "ansible", "jenkins", "ci/cd", "prometheus", "grafana", "helm", "aws", "gcp", "azure", "git", "gitlab", "github actions"],
        "desc": ["devops", "site reliability", "sre", "infrastructure", "deployment pipeline", "cloud infrastructure", "ci/cd", "terraform", "kubernetes"]
    },
    "DATA_ENGINEERING": {
        "titles": ["data engineer", "data engineering", "etl developer", "big data engineer", "analytics engineer"],
        "skills": ["spark", "hadoop", "airflow", "etl", "dbt", "kafka", "scala", "hive", "snowflake", "redshift", "bigquery", "pyspark", "luigi"],
        "desc": ["data engineer", "data engineering", "etl", "spark", "airflow", "data pipeline", "data warehouse", "dbt", "kafka"]
    },
    "DATA_SCIENCE": {
        "titles": ["data scientist", "data science", "ds", "applied scientist", "quantitative researcher", "statistical analyst", "analyst"],
        "skills": ["pandas", "numpy", "statistics", "regression", "a/b testing", "data analysis", "jupyter", "r programming", "scikit-learn", "sklearn", "tableau", "powerbi"],
        "desc": ["data scientist", "data science", "statistics", "regression", "a/b testing", "data analysis", "pandas", "numpy", "insights"]
    },
    "AI_ML": {
        "titles": ["machine learning engineer", "ml engineer", "deep learning engineer", "ai engineer", "computer vision engineer", "nlp engineer", "retrieval engineer", "search engineer", "recommendation engineer", "recsys engineer", "research scientist", "ai research"],
        "skills": ["pytorch", "tensorflow", "transformers", "xgboost", "neural network", "deep learning", "hugging face", "huggingface", "llm", "bert", "gpt", "rag", "langchain", "embeddings", "vector search", "vector database", "pinecone", "weaviate", "qdrant", "milvus", "faiss", "elasticsearch", "opensearch", "ndcg", "mrr", "semantic search"],
        "desc": ["machine learning", "deep learning", "neural network", "pytorch", "tensorflow", "transformers", "hugging face", "huggingface", "llm", "bert", "gpt", "fine-tuning", "lora", "qlora", "rag", "vector search", "recommendation system", "recsys"]
    },
    "FRONTEND": {
        "titles": ["frontend", "ui engineer", "web developer", "frontend developer", "javascript developer", "react developer", "angular developer", "vue developer"],
        "skills": ["react", "next.js", "typescript", "javascript", "css", "html", "vue", "angular", "tailwind", "sass", "webpack", "npm", "yarn"],
        "desc": ["frontend", "ui engineer", "ux", "react", "next.js", "typescript", "javascript", "css", "html", "vue", "angular", "web design", "frontend development"]
    },
    "PROJECT_MANAGEMENT": {
        "titles": ["project manager", "program manager", "scrum master", "pmo", "pm", "delivery manager", "agile coach"],
        "skills": ["agile", "scrum", "jira", "pmp", "resource planning", "budgeting", "project planning", "confluence", "scrum master"],
        "desc": ["project manager", "program manager", "scrum master", "pmo", "agile", "scrum", "jira", "project planning", "stakeholder management"]
    },
    "PRODUCT_MANAGEMENT": {
        "titles": ["product manager", "product owner", "prod mgr", "product management", "director of product", "head of product"],
        "skills": ["product strategy", "roadmap", "user stories", "jira", "market research", "analytics", "product lifecycle", "prd"],
        "desc": ["product manager", "product owner", "product strategy", "roadmap", "user stories", "market research", "prd", "product launch"]
    },
    "DESIGN": {
        "titles": ["graphic designer", "ui designer", "ux designer", "product designer", "illustrator", "creative director", "visual designer"],
        "skills": ["figma", "sketch", "photoshop", "illustrator", "wireframing", "prototyping", "design system", "adobe", "canva"],
        "desc": ["graphic designer", "ui designer", "ux designer", "product designer", "figma", "sketch", "photoshop", "design system", "wireframing"]
    },
    "MARKETING": {
        "titles": ["marketing", "digital marketing", "seo specialist", "content marketer", "growth marketer", "sales", "account manager"],
        "skills": ["seo", "sem", "google analytics", "copywriting", "social media", "content marketing", "sales", "crm", "lead generation"],
        "desc": ["marketing", "seo", "sales", "digital marketing", "campaign", "lead generation", "growth", "advertising"]
    },
    "HR": {
        "titles": ["hr manager", "human resources", "recruiter", "talent acquisition", "sourcing specialist", "people operations"],
        "skills": ["recruiting", "sourcing", "interviewing", "onboarding", "payroll", "employee relations", "talent acquisition", "ats"],
        "desc": ["hr manager", "human resources", "recruiter", "talent acquisition", "sourcing", "employee relations", "people operations"]
    },
    "BACKEND": {
        "titles": ["backend engineer", "software engineer - backend", "backend developer", "software engineer", "developer", "engineer", "coder", "programmer"],
        "skills": ["python", "go", "java", "fastapi", "django", "postgresql", "redis", "apis", "rest api", "sql", "mysql", "c++", "c#", "node.js", "express", "spring boot"],
        "desc": ["backend", "software engineer", "fastapi", "django", "postgresql", "redis", "rest api", "api development", "database schema"]
    }
}


def classify_candidate_role_category(profile: dict, career_history: list, skills: list) -> str:
    current_title = str(profile.get("current_title", "")).lower()
    headline = str(profile.get("headline", "")).lower()
    summary = str(profile.get("summary", "")).lower()
    all_skills = [str(s.get("name", "")).lower() for s in skills if s.get("name")]
    
    past_titles = [str(r.get("title", "")).lower() for r in career_history if r.get("title")]
    past_descs = [str(r.get("description", "")).lower() for r in career_history if r.get("description")]
    
    scores = {}
    for role_name, config in ROLE_CATEGORY_KEYWORDS.items():
        score = 0.0
        
        # 1. Current title match (heavy weight)
        for kw in config["titles"]:
            if kw in current_title:
                score += 8.0
                
        # 2. Headline match
        for kw in config["titles"]:
            if kw in headline:
                score += 4.0
                
        # 3. Past titles match
        for kw in config["titles"]:
            for pt in past_titles:
                if kw in pt:
                    score += 3.0
                    
        # 4. Skills match
        for kw in config["skills"]:
            for sk in all_skills:
                if kw in sk:
                    score += 2.0
                    
        # 5. Descriptions & summary match
        for kw in config["desc"]:
            if kw in summary:
                score += 1.0
            for desc in past_descs:
                if kw in desc:
                    score += 0.5
                    
        scores[role_name] = score
        
    best_role = "BACKEND"
    max_score = 0.0
    for r, s in scores.items():
        if s > max_score:
            max_score = s
            best_role = r
            
    if max_score == 0.0:
        # Check fallback keywords for Project Management or HR/Marketing
        mgmt_words = ["manager", "scrum", "pm", "pmo", "director", "agile", "operations", "lead", "product"]
        if any(w in current_title or w in headline for w in mgmt_words):
            if "product" in current_title or "product" in headline:
                best_role = "PRODUCT_MANAGEMENT"
            else:
                best_role = "PROJECT_MANAGEMENT"
        else:
            best_role = "BACKEND"
            
    return best_role

