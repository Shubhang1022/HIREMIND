"""Honeypot detection and hard disqualifier checking.

Standalone implementations — do NOT import from src.features.structured.
"""
from __future__ import annotations

from datetime import datetime

# ---------------------------------------------------------------------------
# Reference date for all temporal calculations
# ---------------------------------------------------------------------------
REFERENCE_DATE = datetime(2026, 6, 30)

# ---------------------------------------------------------------------------
# Honeypot detection constants  (RankingLogic.md §2.1)
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

# ---------------------------------------------------------------------------
# Hard-disqualifier constants  (RankingLogic.md §2.2 / §2.3)
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
# HoneypotDetector
# ---------------------------------------------------------------------------

class HoneypotDetector:
    """Detect fabricated / honeypot candidate profiles."""

    def check(self, candidate: dict) -> tuple[bool, list[str]]:
        """Return (is_honeypot, triggered_flags).

        Flags emitted: 'tenure_impossible', 'expert_zero_duration',
                       'skills_ratio_extreme', 'extreme_title_desc_mismatch'.

        A candidate is a definitive honeypot if ANY of:
          - tenure_impossible triggered
          - expert_zero_duration triggered
          - check_skills_ratio returns 1.0  (ratio > 2.0)
          - check_title_desc_mismatch returns >= 3
          - combined suspicion score >= 1.0
        """
        career_history: list = candidate.get("career_history", [])
        skills: list = candidate.get("skills", [])
        profile: dict = candidate.get("profile", {})
        years_exp: float = float(profile.get("years_of_experience", 0.0))

        flags: list[str] = []
        suspicion_score: float = 0.0

        # --- Flag 1: impossible tenure ---
        if self.check_tenure_impossible(career_history):
            flags.append("tenure_impossible")

        # --- Flag 2: expert skill with zero duration ---
        if self.check_expert_zero_duration(skills):
            flags.append("expert_zero_duration")

        # --- Flag 3: skills-to-experience ratio ---
        ratio_result = self.check_skills_ratio(skills, years_exp)
        if ratio_result == 1.0:
            flags.append("skills_ratio_extreme")
        else:
            suspicion_score += ratio_result  # 0.5 if suspicion

        # --- Flag 4: extreme title–description mismatch ---
        mismatch_count = self.check_title_desc_mismatch(career_history)
        if mismatch_count >= 3:
            flags.append("extreme_title_desc_mismatch")
        elif mismatch_count >= 2:
            suspicion_score += 0.5

        # Definitive if any hard flag triggered
        if flags:
            return True, flags

        # Definitive if suspicion accumulates to >= 1.0
        if suspicion_score >= 1.0:
            return True, flags

        return False, flags

    # ------------------------------------------------------------------
    # Individual checkers
    # ------------------------------------------------------------------

    def check_tenure_impossible(self, career_history: list) -> bool:
        """Return True if any role duration_months > (2025 - start_year)*12 + 12."""
        for role in career_history:
            start_date_str = role.get("start_date")
            if not start_date_str:
                continue
            try:
                start_dt = datetime.fromisoformat(str(start_date_str))
            except (ValueError, TypeError):
                continue
            start_year = start_dt.year
            company_max_age_months = (REFERENCE_DATE.year - start_year) * 12
            duration = int(role.get("duration_months", 0))
            if duration > company_max_age_months + 12:
                return True
        return False

    def check_expert_zero_duration(self, skills: list) -> bool:
        """Return True if ANY skill has proficiency='expert' AND duration_months=0."""
        for skill in skills:
            if (
                skill.get("proficiency") == "expert"
                and int(skill.get("duration_months", 1)) == 0
            ):
                return True
        return False

    def check_skills_ratio(self, skills: list, years_exp: float) -> float:
        """Return 1.0 (definitive), 0.5 (suspicion), or 0.0 (clean).

        Definitive: (expert + advanced count) / max(years_exp, 1.0) > 2.0
        Suspicion:  ratio > 1.5
        """
        expert_advanced = sum(
            1 for s in skills if s.get("proficiency") in ("expert", "advanced")
        )
        ratio = expert_advanced / max(years_exp, 1.0)
        if ratio > 2.0:
            return 1.0
        if ratio > 1.5:
            return 0.5
        return 0.0

    def check_title_desc_mismatch(self, career_history: list) -> int:
        """Count roles where a non-technical title has a technical description.

        Non-technical titles: marketing manager, accountant, graphic designer,
                              content writer, sales executive, hr manager,
                              operations manager, customer support
        Technical description: >= 3 of the TECHNICAL_DESC_KEYWORDS_HP present.
        """
        mismatch_count = 0
        for role in career_history:
            title_lower = role.get("title", "").lower()
            desc_lower = role.get("description", "").lower()

            is_non_tech_title = any(t in title_lower for t in NON_TECHNICAL_TITLES_HP)
            tech_keyword_hits = sum(
                1 for k in TECHNICAL_DESC_KEYWORDS_HP if k in desc_lower
            )
            is_tech_desc = tech_keyword_hits >= 3

            if is_non_tech_title and is_tech_desc:
                mismatch_count += 1
        return mismatch_count


# ---------------------------------------------------------------------------
# HardDisqualifierChecker
# ---------------------------------------------------------------------------

class HardDisqualifierChecker:
    """Check hard-disqualifier conditions that zero out a candidate's score."""

    def check(self, candidate: dict) -> tuple[bool, str]:
        """Return (is_disqualified, reason).

        Reasons: '' (clean), 'consulting_only', 'non_technical_no_ai'.
        """
        career_history: list = candidate.get("career_history", [])

        if self.is_consulting_only(career_history):
            return True, "consulting_only"

        if self.is_non_technical_no_ai(candidate):
            return True, "non_technical_no_ai"

        return False, ""

    # ------------------------------------------------------------------
    # Individual checkers
    # ------------------------------------------------------------------

    def is_consulting_only(self, career_history: list) -> bool:
        """Return True if ALL roles are at known consulting firms."""
        if not career_history:
            return False
        for role in career_history:
            company_name = role.get("company", "").lower().strip()
            if not any(cf in company_name for cf in CONSULTING_FIRMS):
                return False
        return True

    def is_non_technical_no_ai(self, candidate: dict) -> bool:
        """Return True if current_title is a hard-disqualifier AND no AI/ML role history.

        Disqualifies only when:
          - current_title matches any HARD_DISQUALIFIER_TITLES entry
          AND
          - no career_history entry has a title containing any AI_TITLE_KEYWORDS term
        """
        profile: dict = candidate.get("profile", {})
        current_title: str = profile.get("current_title", "").lower()

        is_current_nontechnical = any(
            t in current_title for t in HARD_DISQUALIFIER_TITLES
        )
        if not is_current_nontechnical:
            return False

        # Check if any past/current role has an AI/ML title
        career_history: list = candidate.get("career_history", [])
        for role in career_history:
            title_lower = role.get("title", "").lower()
            if any(k in title_lower for k in AI_TITLE_KEYWORDS):
                return False  # They have AI/ML history — do NOT disqualify

        return True
