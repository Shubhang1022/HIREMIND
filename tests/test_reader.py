"""Tests for CandidateStreamReader and validate()."""
import json
import pytest

from src.data.reader import CandidateStreamReader
from src.data.validator import validate


# ---------------------------------------------------------------------------
# Minimal realistic candidate fixture
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
        {"name": "Python", "proficiency": "expert", "endorsements": 42},
        {"name": "FAISS", "proficiency": "advanced", "endorsements": 15},
        {"name": "sentence-transformers", "proficiency": "advanced", "endorsements": 10},
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
        # extra fields from full schema — included but not required by validator
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


def _write_jsonl(path, records: list[dict]) -> None:
    """Write *records* to *path* as JSONL (one JSON object per line)."""
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


# ===========================================================================
# CandidateStreamReader tests
# ===========================================================================


class TestCandidateStreamReader:
    def test_empty_file(self, tmp_path):
        """Reader on an empty file yields nothing; stats show 0 total."""
        empty_file = tmp_path / "empty.jsonl"
        empty_file.write_text("", encoding="utf-8")

        reader = CandidateStreamReader(str(empty_file))
        results = list(reader)

        assert results == []
        stats = reader.get_stats()
        assert stats["total_lines"] == 0
        assert stats["valid"] == 0
        assert stats["skipped"] == 0
        assert stats["errors"] == []

    def test_valid_line(self, tmp_path):
        """Reader on a file with one valid candidate JSON yields that candidate."""
        jsonl_file = tmp_path / "candidates.jsonl"
        _write_jsonl(jsonl_file, [MINIMAL_CANDIDATE])

        reader = CandidateStreamReader(str(jsonl_file))
        results = list(reader)

        assert len(results) == 1
        assert results[0]["candidate_id"] == "CAND_0001234"

        stats = reader.get_stats()
        assert stats["total_lines"] == 1
        assert stats["valid"] == 1
        assert stats["skipped"] == 0
        assert stats["errors"] == []

    def test_invalid_json_line(self, tmp_path):
        """Reader skips the invalid line and records it in stats errors."""
        jsonl_file = tmp_path / "candidates.jsonl"
        with open(jsonl_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(MINIMAL_CANDIDATE) + "\n")
            f.write("{ this is not valid json }\n")
            second = dict(MINIMAL_CANDIDATE, candidate_id="CAND_0009999")
            f.write(json.dumps(second) + "\n")

        reader = CandidateStreamReader(str(jsonl_file), skip_invalid=True)
        results = list(reader)

        # Only two valid records should be yielded
        assert len(results) == 2
        candidate_ids = {r["candidate_id"] for r in results}
        assert "CAND_0001234" in candidate_ids
        assert "CAND_0009999" in candidate_ids

        stats = reader.get_stats()
        assert stats["total_lines"] == 3
        assert stats["valid"] == 2
        assert stats["skipped"] == 1
        assert len(stats["errors"]) == 1
        assert "invalid JSON" in stats["errors"][0] or "Line 2" in stats["errors"][0]

    def test_limit(self, tmp_path):
        """Reader with limit=1 yields only 1 candidate even if file has more."""
        candidates = [
            dict(MINIMAL_CANDIDATE, candidate_id=f"CAND_{i:07d}") for i in range(1, 6)
        ]
        jsonl_file = tmp_path / "candidates.jsonl"
        _write_jsonl(jsonl_file, candidates)

        reader = CandidateStreamReader(str(jsonl_file), limit=1)
        results = list(reader)

        assert len(results) == 1
        stats = reader.get_stats()
        assert stats["valid"] == 1

    def test_blank_lines_ignored(self, tmp_path):
        """Blank lines between records do not affect valid count."""
        jsonl_file = tmp_path / "candidates.jsonl"
        with open(jsonl_file, "w", encoding="utf-8") as f:
            f.write("\n")
            f.write(json.dumps(MINIMAL_CANDIDATE) + "\n")
            f.write("   \n")

        reader = CandidateStreamReader(str(jsonl_file))
        results = list(reader)

        assert len(results) == 1
        stats = reader.get_stats()
        assert stats["total_lines"] == 1  # blank lines are not counted
        assert stats["valid"] == 1
        assert stats["skipped"] == 0

    def test_multiple_candidates(self, tmp_path):
        """Reader yields all valid candidates in order."""
        ids = ["CAND_0000001", "CAND_0000002", "CAND_0000003"]
        candidates = [dict(MINIMAL_CANDIDATE, candidate_id=cid) for cid in ids]
        jsonl_file = tmp_path / "candidates.jsonl"
        _write_jsonl(jsonl_file, candidates)

        reader = CandidateStreamReader(str(jsonl_file))
        results = list(reader)

        assert [r["candidate_id"] for r in results] == ids
        assert reader.get_stats()["valid"] == 3


# ===========================================================================
# validate() tests
# ===========================================================================


class TestValidate:
    def test_valid_candidate(self):
        """validator returns (True, []) for a well-formed candidate."""
        is_valid, errors = validate(MINIMAL_CANDIDATE)
        assert is_valid is True
        assert errors == []

    def test_missing_required_field_candidate_id(self):
        """validator returns (False, [error msg]) when candidate_id is missing."""
        bad = {k: v for k, v in MINIMAL_CANDIDATE.items() if k != "candidate_id"}
        is_valid, errors = validate(bad)
        assert is_valid is False
        assert any("candidate_id" in e for e in errors)

    def test_invalid_candidate_id_pattern(self):
        """candidate_id that does not match CAND_XXXXXXX is rejected."""
        bad = dict(MINIMAL_CANDIDATE, candidate_id="CAND_123")
        is_valid, errors = validate(bad)
        assert is_valid is False
        assert any("CAND_" in e or "pattern" in e for e in errors)

    def test_missing_profile_field(self):
        """Missing a required profile sub-field produces an error."""
        bad_profile = {k: v for k, v in MINIMAL_CANDIDATE["profile"].items()
                       if k != "headline"}
        bad = dict(MINIMAL_CANDIDATE, profile=bad_profile)
        is_valid, errors = validate(bad)
        assert is_valid is False
        assert any("headline" in e for e in errors)

    def test_years_of_experience_negative(self):
        """years_of_experience < 0 is rejected."""
        bad_profile = dict(MINIMAL_CANDIDATE["profile"], years_of_experience=-1)
        bad = dict(MINIMAL_CANDIDATE, profile=bad_profile)
        is_valid, errors = validate(bad)
        assert is_valid is False
        assert any("years_of_experience" in e for e in errors)

    def test_empty_career_history(self):
        """career_history with 0 entries is rejected."""
        bad = dict(MINIMAL_CANDIDATE, career_history=[])
        is_valid, errors = validate(bad)
        assert is_valid is False
        assert any("career_history" in e for e in errors)

    def test_missing_career_history_field(self):
        """Missing a required career_history item field produces an error."""
        bad_ch = [{k: v for k, v in MINIMAL_CANDIDATE["career_history"][0].items()
                   if k != "company"}]
        bad = dict(MINIMAL_CANDIDATE, career_history=bad_ch)
        is_valid, errors = validate(bad)
        assert is_valid is False
        assert any("company" in e for e in errors)

    def test_invalid_skill_proficiency(self):
        """A skill with proficiency not in the allowed set is rejected."""
        bad_skills = [dict(MINIMAL_CANDIDATE["skills"][0], proficiency="ninja")]
        bad = dict(MINIMAL_CANDIDATE, skills=bad_skills)
        is_valid, errors = validate(bad)
        assert is_valid is False
        assert any("proficiency" in e for e in errors)

    def test_missing_redrob_signal_field(self):
        """Missing a required redrob_signals field produces an error."""
        bad_signals = {k: v for k, v in MINIMAL_CANDIDATE["redrob_signals"].items()
                       if k != "open_to_work_flag"}
        bad = dict(MINIMAL_CANDIDATE, redrob_signals=bad_signals)
        is_valid, errors = validate(bad)
        assert is_valid is False
        assert any("open_to_work_flag" in e for e in errors)

    def test_multiple_errors_reported(self):
        """Multiple validation failures are all reported in the errors list."""
        bad = {"candidate_id": "BAD_ID"}  # missing almost everything
        is_valid, errors = validate(bad)
        assert is_valid is False
        # Should have errors for missing top-level fields and bad ID format
        assert len(errors) >= 2
