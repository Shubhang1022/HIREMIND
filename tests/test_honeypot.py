"""Tests for HoneypotDetector and HardDisqualifierChecker.

Run with:  python -m pytest tests/test_honeypot.py -v
"""
from __future__ import annotations

import pytest

from src.scoring.honeypot import HoneypotDetector, HardDisqualifierChecker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(
    current_title: str = "ML Engineer",
    years_exp: float = 5.0,
    career_history: list | None = None,
    skills: list | None = None,
) -> dict:
    """Build a minimal candidate dict for testing."""
    return {
        "profile": {
            "current_title": current_title,
            "years_of_experience": years_exp,
        },
        "career_history": career_history or [],
        "skills": skills or [],
    }


# ---------------------------------------------------------------------------
# HoneypotDetector tests
# ---------------------------------------------------------------------------

class TestHoneypotDetector:

    def setup_method(self):
        self.detector = HoneypotDetector()

    # ------------------------------------------------------------------ #
    # check_tenure_impossible                                             #
    # ------------------------------------------------------------------ #

    def test_tenure_impossible_flag(self):
        """Role starting in 2020 with duration_months=120 is impossible
        (only ~60 months could have passed by June 2025, +12 tolerance = 72).
        """
        career_history = [
            {
                "title": "Software Engineer",
                "company": "Acme",
                "start_date": "2020-01-01",
                "duration_months": 120,   # impossible: max allowed is (2025-2020)*12+12 = 72
                "is_current": False,
                "description": "Built products.",
            }
        ]
        assert self.detector.check_tenure_impossible(career_history) is True

    def test_tenure_possible(self):
        """Role starting in 2020 with 48 months is fine (well within 60+12=72)."""
        career_history = [
            {
                "title": "Software Engineer",
                "company": "Acme",
                "start_date": "2020-01-01",
                "duration_months": 48,
                "is_current": False,
                "description": "",
            }
        ]
        assert self.detector.check_tenure_impossible(career_history) is False

    def test_tenure_impossible_triggers_honeypot(self):
        """check() should return (True, [...]) when tenure is impossible."""
        candidate = _make_candidate(
            career_history=[
                {
                    "title": "Senior Engineer",
                    "company": "Acme",
                    "start_date": "2021-01-01",
                    "duration_months": 100,  # impossible: max = (2025-2021)*12+12 = 60
                    "is_current": True,
                    "description": "",
                }
            ]
        )
        is_honeypot, flags = self.detector.check(candidate)
        assert is_honeypot is True
        assert "tenure_impossible" in flags

    # ------------------------------------------------------------------ #
    # check_expert_zero_duration                                          #
    # ------------------------------------------------------------------ #

    def test_expert_zero_duration_flag(self):
        """Expert skill with duration_months=0 should be flagged."""
        skills = [
            {"name": "PyTorch", "proficiency": "expert", "duration_months": 0},
        ]
        assert self.detector.check_expert_zero_duration(skills) is True

    def test_expert_nonzero_duration_clean(self):
        """Expert skill with duration_months>0 is fine."""
        skills = [
            {"name": "PyTorch", "proficiency": "expert", "duration_months": 24},
        ]
        assert self.detector.check_expert_zero_duration(skills) is False

    def test_expert_zero_duration_triggers_honeypot(self):
        """check() should return (True, [...'expert_zero_duration'...])."""
        candidate = _make_candidate(
            skills=[
                {"name": "Transformer", "proficiency": "expert", "duration_months": 0},
                {"name": "Python", "proficiency": "advanced", "duration_months": 36},
            ]
        )
        is_honeypot, flags = self.detector.check(candidate)
        assert is_honeypot is True
        assert "expert_zero_duration" in flags

    # ------------------------------------------------------------------ #
    # check_skills_ratio                                                  #
    # ------------------------------------------------------------------ #

    def test_skills_ratio_extreme_flag(self):
        """3 years exp, 8 expert/advanced skills → ratio = 8/3 ≈ 2.67 > 2.0 → 1.0."""
        skills = [
            {"name": f"Skill{i}", "proficiency": "expert", "duration_months": 12}
            for i in range(8)
        ]
        result = self.detector.check_skills_ratio(skills, years_exp=3.0)
        assert result == 1.0

    def test_skills_ratio_suspicion(self):
        """Ratio between 1.5 and 2.0 should return 0.5 (suspicion)."""
        # 8 expert/advanced skills, 5 years exp → ratio = 8/5 = 1.6
        skills = [
            {"name": f"Skill{i}", "proficiency": "expert", "duration_months": 12}
            for i in range(8)
        ]
        result = self.detector.check_skills_ratio(skills, years_exp=5.0)
        assert result == 0.5

    def test_skills_ratio_clean(self):
        """Ratio <= 1.5 → 0.0 (clean)."""
        skills = [
            {"name": f"Skill{i}", "proficiency": "expert", "duration_months": 12}
            for i in range(4)
        ]
        result = self.detector.check_skills_ratio(skills, years_exp=5.0)
        assert result == 0.0

    def test_skills_ratio_extreme_triggers_honeypot(self):
        """check() should return (True, [...'skills_ratio_extreme'...]) for extreme ratio."""
        candidate = _make_candidate(
            years_exp=3.0,
            skills=[
                {"name": f"Skill{i}", "proficiency": "advanced", "duration_months": 12}
                for i in range(8)
            ],
        )
        is_honeypot, flags = self.detector.check(candidate)
        assert is_honeypot is True
        assert "skills_ratio_extreme" in flags

    def test_skills_ratio_suspicion_alone_not_honeypot(self):
        """Suspicion ratio (0.5) alone is not enough to be a definitive honeypot.
        Requires combined suspicion >= 1.0.
        """
        candidate = _make_candidate(
            years_exp=5.0,
            skills=[
                {"name": f"Skill{i}", "proficiency": "expert", "duration_months": 12}
                for i in range(8)
            ],
            # No career history → mismatch_count = 0, total suspicion = 0.5 < 1.0
            career_history=[],
        )
        is_honeypot, flags = self.detector.check(candidate)
        assert is_honeypot is False
        # skills_ratio_extreme should NOT be in flags since ratio=1.6 only suspicion
        assert "skills_ratio_extreme" not in flags

    # ------------------------------------------------------------------ #
    # check_title_desc_mismatch                                           #
    # ------------------------------------------------------------------ #

    def _make_mismatch_role(self, idx: int) -> dict:
        """Create a role with non-technical title + technical description."""
        return {
            "title": "Marketing Manager",
            "company": f"Company{idx}",
            "start_date": f"202{idx}-01-01",
            "duration_months": 12,
            "is_current": False,
            "description": (
                "Built vector database pipelines using embedding models. "
                "Applied transformer architectures and machine learning "
                "to improve retrieval."
            ),
        }

    def test_title_desc_mismatch_flag(self):
        """3 mismatched roles → check_title_desc_mismatch returns >= 3."""
        career_history = [self._make_mismatch_role(i) for i in range(3)]
        count = self.detector.check_title_desc_mismatch(career_history)
        assert count >= 3

    def test_title_desc_mismatch_triggers_honeypot(self):
        """3 mismatched roles → check() returns (True, [...'extreme_title_desc_mismatch'...])."""
        career_history = [self._make_mismatch_role(i) for i in range(3)]
        candidate = _make_candidate(career_history=career_history)
        is_honeypot, flags = self.detector.check(candidate)
        assert is_honeypot is True
        assert "extreme_title_desc_mismatch" in flags

    def test_title_desc_mismatch_tech_title_is_clean(self):
        """A technical title with technical description is NOT a mismatch."""
        career_history = [
            {
                "title": "ML Engineer",
                "company": "TechCorp",
                "start_date": "2022-01-01",
                "duration_months": 24,
                "is_current": True,
                "description": (
                    "Built embedding pipelines, deployed transformer models, "
                    "used faiss and machine learning for retrieval."
                ),
            }
        ]
        count = self.detector.check_title_desc_mismatch(career_history)
        assert count == 0

    # ------------------------------------------------------------------ #
    # Clean candidate                                                      #
    # ------------------------------------------------------------------ #

    def test_clean_candidate(self):
        """Normal AI engineer with realistic profile → not a honeypot."""
        candidate = _make_candidate(
            current_title="Senior ML Engineer",
            years_exp=6.0,
            career_history=[
                {
                    "title": "ML Engineer",
                    "company": "ProductCo",
                    "start_date": "2019-01-01",
                    "duration_months": 36,
                    "is_current": False,
                    "description": "Built and deployed ML models in production.",
                },
                {
                    "title": "Senior ML Engineer",
                    "company": "AICorp",
                    "start_date": "2022-01-01",
                    "duration_months": 30,
                    "is_current": True,
                    "description": "Led embedding retrieval system serving real users.",
                },
            ],
            skills=[
                {"name": "Python", "proficiency": "expert", "duration_months": 60},
                {"name": "PyTorch", "proficiency": "advanced", "duration_months": 36},
                {"name": "FAISS", "proficiency": "advanced", "duration_months": 24},
            ],
        )
        is_honeypot, flags = self.detector.check(candidate)
        assert is_honeypot is False
        assert flags == []


# ---------------------------------------------------------------------------
# HardDisqualifierChecker tests
# ---------------------------------------------------------------------------

class TestHardDisqualifierChecker:

    def setup_method(self):
        self.checker = HardDisqualifierChecker()

    # ------------------------------------------------------------------ #
    # consulting_only                                                      #
    # ------------------------------------------------------------------ #

    def test_consulting_only(self):
        """All roles at TCS / Wipro / Infosys → consulting_only disqualified."""
        candidate = _make_candidate(
            current_title="Software Engineer",
            career_history=[
                {
                    "title": "Software Engineer",
                    "company": "TCS",
                    "start_date": "2018-01-01",
                    "duration_months": 36,
                    "is_current": False,
                    "description": "Worked on client projects.",
                },
                {
                    "title": "Senior Engineer",
                    "company": "Wipro",
                    "start_date": "2021-01-01",
                    "duration_months": 24,
                    "is_current": False,
                    "description": "Consulting engagement.",
                },
                {
                    "title": "Lead Engineer",
                    "company": "Infosys",
                    "start_date": "2023-01-01",
                    "duration_months": 18,
                    "is_current": True,
                    "description": "On-site delivery.",
                },
            ],
        )
        is_consulting = self.checker.is_consulting_only(
            candidate["career_history"]
        )
        assert is_consulting is True

        is_disqualified, reason = self.checker.check(candidate)
        assert is_disqualified is True
        assert reason == "consulting_only"

    def test_mixed_career_not_consulting_only(self):
        """One product company role among consulting → NOT consulting_only."""
        candidate = _make_candidate(
            current_title="ML Engineer",
            career_history=[
                {
                    "title": "Software Engineer",
                    "company": "TCS",
                    "start_date": "2018-01-01",
                    "duration_months": 36,
                    "is_current": False,
                    "description": "Consulting work.",
                },
                {
                    "title": "ML Engineer",
                    "company": "ProductStartup",
                    "start_date": "2021-01-01",
                    "duration_months": 36,
                    "is_current": True,
                    "description": "Built ML pipelines.",
                },
            ],
        )
        assert self.checker.is_consulting_only(candidate["career_history"]) is False
        is_disqualified, reason = self.checker.check(candidate)
        assert is_disqualified is False

    # ------------------------------------------------------------------ #
    # non_technical_no_ai                                                  #
    # ------------------------------------------------------------------ #

    def test_non_technical_no_ai(self):
        """Accountant with no AI history → non_technical_no_ai disqualified."""
        candidate = _make_candidate(
            current_title="Accountant",
            years_exp=8.0,
            career_history=[
                {
                    "title": "Junior Accountant",
                    "company": "FinanceCo",
                    "start_date": "2016-01-01",
                    "duration_months": 36,
                    "is_current": False,
                    "description": "Managed ledgers and financial reports.",
                },
                {
                    "title": "Accountant",
                    "company": "AuditFirm",
                    "start_date": "2019-01-01",
                    "duration_months": 60,
                    "is_current": True,
                    "description": "Tax filings and compliance.",
                },
            ],
        )
        assert self.checker.is_non_technical_no_ai(candidate) is True
        is_disqualified, reason = self.checker.check(candidate)
        assert is_disqualified is True
        assert reason == "non_technical_no_ai"

    def test_false_positive_pivoted_to_ml(self):
        """Marketing Manager who pivoted to ML: has 'ML Engineer' in career_history.

        HardDisqualifierChecker.check() should return (False, '') — NOT disqualified.
        """
        candidate = _make_candidate(
            current_title="Marketing Manager",
            years_exp=7.0,
            career_history=[
                {
                    "title": "Marketing Manager",
                    "company": "BrandCo",
                    "start_date": "2017-01-01",
                    "duration_months": 36,
                    "is_current": False,
                    "description": "Ran digital marketing campaigns.",
                },
                {
                    "title": "ML Engineer",
                    "company": "TechCorp",
                    "start_date": "2020-01-01",
                    "duration_months": 36,
                    "is_current": False,
                    "description": "Built recommendation models.",
                },
                {
                    "title": "Marketing Manager",
                    "company": "StartupX",
                    "start_date": "2023-01-01",
                    "duration_months": 18,
                    "is_current": True,
                    "description": "Growth marketing with data analytics.",
                },
            ],
        )
        # Current title is a hard-disqualifier title, but has ML history
        assert self.checker.is_non_technical_no_ai(candidate) is False
        is_disqualified, reason = self.checker.check(candidate)
        assert is_disqualified is False
        assert reason == ""

    # ------------------------------------------------------------------ #
    # Clean candidate                                                      #
    # ------------------------------------------------------------------ #

    def test_clean_candidate(self):
        """Normal AI engineer → not disqualified by either hard check."""
        candidate = _make_candidate(
            current_title="Senior ML Engineer",
            years_exp=6.0,
            career_history=[
                {
                    "title": "Data Scientist",
                    "company": "ProductCo",
                    "start_date": "2019-01-01",
                    "duration_months": 36,
                    "is_current": False,
                    "description": "NLP models for search.",
                },
                {
                    "title": "ML Engineer",
                    "company": "ScaleUp",
                    "start_date": "2022-01-01",
                    "duration_months": 30,
                    "is_current": True,
                    "description": "Deployed vector search in production.",
                },
            ],
            skills=[
                {"name": "Python", "proficiency": "expert", "duration_months": 60},
            ],
        )
        is_disqualified, reason = self.checker.check(candidate)
        assert is_disqualified is False
        assert reason == ""

        # Also verify the HoneypotDetector agrees
        detector = HoneypotDetector()
        is_honeypot, flags = detector.check(candidate)
        assert is_honeypot is False
        assert flags == []
