from __future__ import annotations

from src.intelligence.understanding import CandidateUnderstandingEngine, JobUnderstandingEngine
from src.intelligence.interview_copilot import InterviewCopilot


def test_job_understanding_extracts_required_skills() -> None:
    jd = "Senior AI Engineer. Must have Python, embeddings, vector database (Pinecone/Qdrant), and NDCG evaluation."
    job = JobUnderstandingEngine().extract(jd)
    assert job.seniority == "Senior"
    assert "python" in [s.lower() for s in job.required_skills]


def test_candidate_understanding_smoke() -> None:
    cand = {
        "candidate_id": "CAND_0000001",
        "profile": {"headline": "ML Engineer", "summary": "Built retrieval systems", "years_of_experience": 6},
        "career_history": [
            {"title": "ML Engineer", "description": "Deployed semantic search with embeddings and FAISS. Led a team of 3."}
        ],
        "skills": [{"name": "Python", "proficiency": "expert"}, {"name": "FAISS", "proficiency": "advanced"}],
        "redrob_signals": {},
    }
    u = CandidateUnderstandingEngine().extract(cand)
    assert u.candidate_id == "CAND_0000001"
    assert u.years_experience == 6
    assert any("python" == s.name for s in u.skills)


def test_interview_copilot_generates_questions() -> None:
    job = JobUnderstandingEngine().extract("Senior AI Engineer founding team, retrieval, vector DB, evaluation.")
    plan = InterviewCopilot(max_questions=6).generate(
        candidate_id="CAND_1",
        features={"has_vector_db": False, "leadership_evidence_score": 0.2, "notice_period_days": 90},
        job=job,
        missing_skills=["Vector database / ANN search"],
        risks=["Potential keyword stuffing; verify claims via deep-dive interview"],
    )
    assert plan.candidate_id == "CAND_1"
    assert 3 <= len(plan.questions) <= 6

