"""Tests for StructuredFeatureExtractor."""
import copy
import pytest

from src.features.structured import StructuredFeatureExtractor

# ---------------------------------------------------------------------------
# Re-use the minimal candidate from test_reader as a base fixture
# ---------------------------------------------------------------------------

MINIMAL_CANDIDATE: dict = {
    "candidate_id": "CAND_0001234",
    "profile": {
        "anonymized_name": "Candidate A",
        "headline": "Senior ML Engineer with 7 years in production AI systems",
        "summary": (
            "Experienced ML engineer specialising in NLP, embeddings, and "
            "large-scale retrieval pipelines. Shipped multiple RAG systems to "
            "production serving 100k+ daily users."
        ),
        "location": "Pune, Maharashtra",
        "country": "India",
        "years_of_experience": 7.0,
        "current_title": "Senior ML Engineer",
        "current_company": "Redrob AI",
        "current_company_size": "51-200",
        "current_industry": "Technology",
    },
    "career_history": [
        {
            "company": "Redrob AI",
            "title": "Senior ML Engineer",
            "start_date": "2022-01-01",
            "end_date": None,
            "duration_months": 29,
            "is_current": True,
            "industry": "Technology",
            "company_size": "51-200",
            "description": (
                "Designed and deployed FAISS + sentence-transformer retrieval "
                "system serving 100k users. Ran A/B tests to measure latency "
                "improvements. SLA: p99 < 200ms."
            ),
        }
    ],
    "education": [
        {
            "institution": "IIT Bombay",
            "degree": "B.Tech",
            "field_of_study": "Computer Science",
            "start_year": 2013,
            "end_year": 2017,
            "grade": "8.5 CGPA",
            "tier": "tier_1",
        }
    ],
    "skills": [
        {"name": "Python", "proficiency": "expert", "endorsements": 42, "duration_months": 84},
        {"name": "FAISS", "proficiency": "advanced", "endorsements": 15, "duration_months": 29},
        {"name": "sentence-transformers", "proficiency": "advanced", "endorsements": 10, "duration_months": 29},
    ],
    "redrob_signals": {
        "profile_completeness_score": 92.0,
        "signup_date": "2023-06-01",
        "last_active_date": "2025-05-28",
        "open_to_work_flag": True,
        "notice_period_days": 30,
        "expected_salary_range_inr_lpa": {"min": 28.0, "max": 45.0},
        "recruiter_response_rate": 0.85,
        "avg_response_time_hours": 6.0,
        "github_activity_score": 72.0,
        "saved_by_recruiters_30d": 5,
        "verified_email": True,
        "verified_phone": True,
        "linkedin_connected": True,
        "willing_to_relocate": False,
        "preferred_work_mode": "hybrid",
        "profile_views_received_30d": 120,
        "applications_submitted_30d": 3,
        "skill_assessment_scores": {"Python": 88, "Machine Learning": 91},
        "connection_count": 350,
        "endorsements_received": 67,
        "search_appearance_30d": 45,
        "interview_completion_rate": 1.0,
        "offer_acceptance_rate": 0.5,
    },
}


# ---------------------------------------------------------------------------
# Test 1: Full AI engineer candidate
# ---------------------------------------------------------------------------

class TestAIEngineerCandidate:
    """test_ai_engineer_candidate: full AI engineer with expected feature values."""

    def setup_method(self):
        self.extractor = StructuredFeatureExtractor()
        # Build a rich AI engineer candidate extending the minimal base
        candidate = copy.deepcopy(MINIMAL_CANDIDATE)
        candidate["profile"]["years_of_experience"] = 7.0
        candidate["profile"]["location"] = "Pune, Maharashtra"
        candidate["career_history"] = [
            {
                "company": "Redrob AI",
                "title": "Senior ML Engineer",
                "start_date": "2022-01-01",
                "end_date": None,
                "duration_months": 41,
                "is_current": True,
                "industry": "Technology",
                "company_size": "51-200",
                "description": (
                    "Designed and deployed FAISS + sentence-transformer retrieval "
                    "system serving 100k real users. Ran A/B tests to measure latency "
                    "improvements. SLA: p99 < 200ms. Deployed to production."
                ),
            },
            {
                "company": "Flipkart",
                "title": "ML Engineer",
                "start_date": "2018-06-01",
                "end_date": "2021-12-31",
                "duration_months": 42,
                "is_current": False,
                "industry": "E-commerce",
                "company_size": "10001+",
                "description": (
                    "Built ML model pipelines for recommendation engine serving "
                    "millions of users. Implemented end-to-end model pipeline."
                ),
            },
        ]
        candidate["skills"] = [
            {"name": "Python", "proficiency": "expert", "endorsements": 42, "duration_months": 84},
            {"name": "FAISS", "proficiency": "advanced", "endorsements": 15, "duration_months": 41},
            {"name": "sentence-transformers", "proficiency": "advanced", "endorsements": 10, "duration_months": 41},
            {"name": "Weaviate", "proficiency": "advanced", "endorsements": 8, "duration_months": 24},
            {"name": "NDCG evaluation", "proficiency": "intermediate", "endorsements": 5, "duration_months": 20},
        ]
        self.candidate = candidate
        self.features = self.extractor.extract(candidate)

    def test_years_exp(self):
        assert self.features["years_exp"] == 7.0

    def test_has_vector_db(self):
        # Weaviate maps to vector_db cluster
        assert self.features["has_vector_db"] is True

    def test_has_embedding_retrieval(self):
        # sentence-transformers maps to embedding_retrieval cluster
        assert self.features["has_embedding_retrieval"] is True

    def test_production_evidence_score_positive(self):
        assert self.features["production_evidence_score"] > 0

    def test_location_fit_score_pune(self):
        # Pune is a preferred city → score 1.0
        assert self.features["location_fit_score"] == 1.0

    def test_location_city(self):
        assert self.features["location_city"] == "Pune"

    def test_has_python_advanced(self):
        assert self.features["has_python_advanced"] is True

    def test_not_disqualified(self):
        assert self.features["is_disqualified"] is False

    def test_is_not_honeypot(self):
        assert self.features["is_honeypot"] is False

    def test_candidate_id_passthrough(self):
        assert self.features["candidate_id"] == "CAND_0001234"

    def test_batch_idx_default(self):
        assert self.features["batch_idx"] == 0

    def test_position_in_batch_default(self):
        assert self.features["position_in_batch"] == 0

    def test_batch_idx_custom(self):
        f = self.extractor.extract(self.candidate, batch_idx=3, position_in_batch=7)
        assert f["batch_idx"] == 3
        assert f["position_in_batch"] == 7


# ---------------------------------------------------------------------------
# Test 2: Non-technical candidate
# ---------------------------------------------------------------------------

class TestNonTechnicalCandidate:
    """test_non_technical_candidate: Accountant with no AI history → disqualified."""

    def setup_method(self):
        self.extractor = StructuredFeatureExtractor()
        candidate = copy.deepcopy(MINIMAL_CANDIDATE)
        candidate["candidate_id"] = "CAND_0009901"
        candidate["profile"]["current_title"] = "Accountant"
        candidate["profile"]["years_of_experience"] = 5.0
        candidate["career_history"] = [
            {
                "company": "KPMG India",
                "title": "Accountant",
                "start_date": "2020-01-01",
                "end_date": None,
                "duration_months": 65,
                "is_current": True,
                "industry": "Finance",
                "company_size": "10001+",
                "description": (
                    "Managed financial records and prepared annual audit reports. "
                    "Handled tax filings and reconciliations."
                ),
            },
            {
                "company": "Deloitte India",
                "title": "Junior Accountant",
                "start_date": "2017-01-01",
                "end_date": "2019-12-31",
                "duration_months": 36,
                "is_current": False,
                "industry": "Finance",
                "company_size": "10001+",
                "description": "Assisted senior accountants with audit preparation and bookkeeping.",
            },
        ]
        candidate["skills"] = [
            {"name": "Excel", "proficiency": "advanced", "endorsements": 30, "duration_months": 60},
            {"name": "Tally", "proficiency": "intermediate", "endorsements": 10, "duration_months": 36},
        ]
        self.features = self.extractor.extract(candidate)

    def test_non_technical_title_only(self):
        assert self.features["non_technical_title_only"] is True

    def test_is_disqualified(self):
        assert self.features["is_disqualified"] is True

    def test_disqualifier_reason(self):
        assert self.features["disqualifier_reason"] == "non_technical"

    def test_has_no_ai_skills(self):
        assert self.features["has_embedding_retrieval"] is False
        assert self.features["has_vector_db"] is False


# ---------------------------------------------------------------------------
# Test 3: Consulting-only candidate
# ---------------------------------------------------------------------------

class TestConsultingOnlyCandidate:
    """test_consulting_only_candidate: entire career at TCS/Wipro/Infosys."""

    def setup_method(self):
        self.extractor = StructuredFeatureExtractor()
        candidate = copy.deepcopy(MINIMAL_CANDIDATE)
        candidate["candidate_id"] = "CAND_0009902"
        candidate["profile"]["current_title"] = "Senior Software Engineer"
        candidate["profile"]["years_of_experience"] = 8.0
        candidate["career_history"] = [
            {
                "company": "TCS",
                "title": "Senior Software Engineer",
                "start_date": "2021-01-01",
                "end_date": None,
                "duration_months": 53,
                "is_current": True,
                "industry": "IT Services",
                "company_size": "10001+",
                "description": "Developed enterprise Java applications for banking clients.",
            },
            {
                "company": "Wipro",
                "title": "Software Engineer",
                "start_date": "2018-01-01",
                "end_date": "2020-12-31",
                "duration_months": 36,
                "is_current": False,
                "industry": "IT Services",
                "company_size": "10001+",
                "description": "Worked on .NET development for insurance domain.",
            },
            {
                "company": "Infosys",
                "title": "Software Trainee",
                "start_date": "2017-01-01",
                "end_date": "2017-12-31",
                "duration_months": 12,
                "is_current": False,
                "industry": "IT Services",
                "company_size": "10001+",
                "description": "Trained in Java and worked on maintenance projects.",
            },
        ]
        candidate["skills"] = [
            {"name": "Java", "proficiency": "advanced", "endorsements": 20, "duration_months": 96},
            {"name": ".NET", "proficiency": "intermediate", "endorsements": 8, "duration_months": 36},
        ]
        self.features = self.extractor.extract(candidate)

    def test_consulting_only(self):
        assert self.features["consulting_only"] is True

    def test_is_disqualified(self):
        assert self.features["is_disqualified"] is True

    def test_disqualifier_reason(self):
        assert self.features["disqualifier_reason"] == "consulting_only"

    def test_product_company_ratio_zero(self):
        assert self.features["product_company_ratio"] == 0.0

    def test_product_company_months_zero(self):
        assert self.features["product_company_months"] == 0

    def test_consulting_months_positive(self):
        assert self.features["consulting_company_months"] > 0


# ---------------------------------------------------------------------------
# Test 4: Seniority progression
# ---------------------------------------------------------------------------

class TestSeniorityProgression:
    """test_seniority_progression: Junior→Senior→Lead → bonus=0.2, trend>0."""

    def setup_method(self):
        self.extractor = StructuredFeatureExtractor()
        candidate = copy.deepcopy(MINIMAL_CANDIDATE)
        candidate["candidate_id"] = "CAND_0009903"
        candidate["profile"]["current_title"] = "Lead ML Engineer"
        candidate["profile"]["years_of_experience"] = 9.0
        candidate["career_history"] = [
            {
                "company": "StartupA",
                "title": "Junior Data Scientist",
                "start_date": "2016-01-01",
                "end_date": "2018-06-30",
                "duration_months": 30,
                "is_current": False,
                "industry": "Technology",
                "company_size": "11-50",
                "description": "Trained basic ML models for classification tasks.",
            },
            {
                "company": "MidCo",
                "title": "Senior ML Engineer",
                "start_date": "2018-07-01",
                "end_date": "2021-12-31",
                "duration_months": 42,
                "is_current": False,
                "industry": "Technology",
                "company_size": "501-1000",
                "description": (
                    "Built end-to-end machine learning pipelines. Deployed models to production."
                ),
            },
            {
                "company": "BigCorp",
                "title": "Lead ML Engineer",
                "start_date": "2022-01-01",
                "end_date": None,
                "duration_months": 41,
                "is_current": True,
                "industry": "Technology",
                "company_size": "1001-5000",
                "description": "Led a team of 5 engineers. Architected the recommendation system.",
            },
        ]
        self.features = self.extractor.extract(candidate)

    def test_seniority_trajectory_bonus(self):
        assert self.features["seniority_trajectory_bonus"] == 0.2

    def test_seniority_trend_positive(self):
        assert self.features["seniority_trend"] > 0

    def test_seniority_levels_ascending(self):
        scores = self.features["title_seniority_scores"]
        # Junior(1) → Senior(3) → Lead(4) — ascending
        assert scores[0] < scores[1] < scores[2]

    def test_not_disqualified(self):
        assert self.features["is_disqualified"] is False


# ---------------------------------------------------------------------------
# Test 5: Honeypot — expert skill with zero duration
# ---------------------------------------------------------------------------

class TestHoneypotExpertZeroDuration:
    """test_honeypot_expert_zero_duration: expert skill duration_months=0 → honeypot."""

    def setup_method(self):
        self.extractor = StructuredFeatureExtractor()
        candidate = copy.deepcopy(MINIMAL_CANDIDATE)
        candidate["candidate_id"] = "CAND_0009904"
        candidate["profile"]["years_of_experience"] = 3.0
        candidate["skills"] = [
            # Expert skill with zero duration — definitive honeypot flag
            {"name": "PyTorch", "proficiency": "expert", "endorsements": 50, "duration_months": 0},
            {"name": "TensorFlow", "proficiency": "expert", "endorsements": 30, "duration_months": 0},
            {"name": "Python", "proficiency": "advanced", "endorsements": 20, "duration_months": 36},
        ]
        self.features = self.extractor.extract(candidate)

    def test_is_honeypot(self):
        assert self.features["is_honeypot"] is True

    def test_is_disqualified(self):
        assert self.features["is_disqualified"] is True

    def test_disqualifier_reason_honeypot(self):
        assert self.features["disqualifier_reason"] == "honeypot"

    def test_honeypot_flags_contain_expert_zero_duration(self):
        assert "expert_zero_duration" in self.features["honeypot_flags"]

    def test_honeypot_suspicion_score_max(self):
        assert self.features["honeypot_suspicion_score"] == 1.0
