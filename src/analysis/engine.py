"""Generic AI analysis engine for any candidate dataset."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.ingestion.types import NormalizedCandidate


@dataclass
class AnalysisConfig:
    top_k: int = 100
    model_name: str = "BAAI/bge-small-en-v1.5"  # lighter model for SaaS responsiveness


@dataclass
class CandidateScore:
    candidate_index: int
    external_id: str | None
    rank: int = 0
    ai_score: float = 0.0
    match_percent: float = 0.0
    confidence: float = 0.0
    hiring_readiness: str = "medium"
    integrity_score: float = 1.0
    semantic_score: float = 0.0
    experience_score: float = 0.0
    behavioral_score: float = 0.0
    skill_gap_score: float = 0.0
    reasoning: str = ""
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    interview_questions: list[str] = field(default_factory=list)
    behavioral_signals: dict[str, Any] = field(default_factory=dict)


class GenericAnalysisEngine:
    """Rank and analyze candidates against a job description using semantic AI."""

    def __init__(self, config: AnalysisConfig | None = None):
        self.config = config or AnalysisConfig()
        self._embedder = None

    @property
    def embedder(self):
        if self._embedder is None:
            from src.intelligence.embeddings import BGELargeEmbeddingLayer
            self._embedder = BGELargeEmbeddingLayer(model_name=self.config.model_name)
        return self._embedder

    def _extract_job_skills(self, jd_text: str) -> list[str]:
        """Extract required skills from job description."""
        skills: set[str] = set()
        # Common tech skills pattern
        tech_patterns = [
            r"\b(python|java|javascript|typescript|react|node\.?js|aws|azure|gcp|docker|kubernetes|"
            r"sql|postgresql|mongodb|redis|machine learning|deep learning|nlp|llm|pytorch|tensorflow|"
            r"fastapi|django|flask|spring|go|rust|c\+\+|scala|spark|kafka|airflow|dbt|snowflake|"
            r"terraform|ci/cd|agile|scrum|leadership|communication|problem.?solving)\b"
        ]
        for pat in tech_patterns:
            for m in re.finditer(pat, jd_text, re.IGNORECASE):
                skills.add(m.group(0).lower())

        # Explicit skills section
        skills_section = re.search(
            r"(?:required|must.?have|skills?)[:\s]+([^\n]+(?:\n[^\n]+)*)",
            jd_text, re.IGNORECASE
        )
        if skills_section:
            for part in re.split(r"[,;|•·]", skills_section.group(1)):
                s = part.strip().lower()
                if 2 < len(s) < 50:
                    skills.add(s)
        return list(skills)[:30]

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom < 1e-9:
            return 0.0
        return float(np.dot(a, b) / denom)

    def _score_experience(self, candidate: NormalizedCandidate, jd_text: str) -> float:
        yoe = candidate.years_of_experience or 0
        # Extract min years from JD
        min_match = re.search(r"(\d+)\+?\s*(?:years?|yrs?)", jd_text, re.IGNORECASE)
        min_years = float(min_match.group(1)) if min_match else 3.0

        if yoe >= min_years:
            return min(1.0, 0.7 + (yoe - min_years) * 0.05)
        return max(0.2, yoe / min_years * 0.7)

    def _score_skills(self, candidate: NormalizedCandidate, required_skills: list[str]) -> tuple[float, list[str]]:
        if not required_skills:
            return 0.7, []
        cand_skills = {s.get("name", "").lower() for s in candidate.skills}
        cand_text = (candidate.text_for_embedding or "").lower()
        matched = []
        missing = []
        for skill in required_skills:
            if skill in cand_skills or skill in cand_text:
                matched.append(skill)
            else:
                missing.append(skill)
        score = len(matched) / len(required_skills) if required_skills else 0.7
        return score, missing

    def _integrity_check(self, candidate: NormalizedCandidate) -> tuple[float, list[str]]:
        risks = []
        score = 1.0
        yoe = candidate.years_of_experience or 0
        exp_count = len(candidate.experience)

        if yoe > 30:
            risks.append("Unusually high years of experience")
            score -= 0.2
        if yoe > 0 and exp_count == 0:
            risks.append("Experience claimed but no work history found")
            score -= 0.15
        if not candidate.full_name and not candidate.email:
            risks.append("Missing identity information")
            score -= 0.1

        return max(0.0, score), risks

    def _generate_questions(
        self, candidate: NormalizedCandidate, missing_skills: list[str], jd_text: str
    ) -> list[str]:
        questions = []
        if missing_skills:
            questions.append(
                f"Can you describe your experience with {missing_skills[0]} and a project where you applied it?"
            )
        if candidate.current_title:
            questions.append(
                f"In your role as {candidate.current_title}, what was your most impactful contribution?"
            )
        questions.append("Walk me through a challenging technical problem you solved recently.")
        if "leadership" in jd_text.lower() or "lead" in jd_text.lower():
            questions.append("Tell me about a time you led a team or mentored junior engineers.")
        questions.append("Why are you interested in this role, and what would you bring to the team?")
        return questions[:5]

    def _hiring_readiness(self, score: float) -> str:
        if score >= 0.75:
            return "high"
        if score >= 0.5:
            return "medium"
        if score >= 0.3:
            return "low"
        return "not_ready"

    def analyze(
        self,
        candidates: list[NormalizedCandidate],
        job_description: str,
        job_title: str = "Role",
    ) -> list[CandidateScore]:
        """Full analysis pipeline: embed, rank, explain."""
        if not candidates:
            return []

        required_skills = self._extract_job_skills(job_description)
        jd_text = f"{job_title}. {job_description}"

        # Embed job and candidates
        job_emb = self.embedder.embed_job(jd_text)
        cand_texts = [c.text_for_embedding or c.full_name or "unknown" for c in candidates]
        cand_embs = self.embedder.embed_candidates(cand_texts)

        scores: list[CandidateScore] = []
        for i, candidate in enumerate(candidates):
            semantic = self._cosine_similarity(job_emb, cand_embs[i])
            exp_score = self._score_experience(candidate, job_description)
            skill_score, missing = self._score_skills(candidate, required_skills)
            integrity, integrity_risks = self._integrity_check(candidate)

            ai_score = (
                0.40 * semantic +
                0.25 * exp_score +
                0.25 * skill_score +
                0.10 * integrity
            )

            strengths = []
            if semantic > 0.6:
                strengths.append("Strong semantic match to job requirements")
            if exp_score > 0.7:
                strengths.append(f"Solid experience ({candidate.years_of_experience or 'N/A'} years)")
            if skill_score > 0.7:
                strengths.append("Good skill alignment")

            weaknesses = []
            if missing:
                weaknesses.append(f"Missing key skills: {', '.join(missing[:3])}")
            if exp_score < 0.5:
                weaknesses.append("Experience level below requirements")

            reasoning_parts = []
            if candidate.years_of_experience:
                reasoning_parts.append(f"{candidate.years_of_experience}y exp")
            reasoning_parts.append(f"Match {int(ai_score * 100)}%")
            if candidate.location:
                reasoning_parts.append(candidate.location)
            reasoning = "; ".join(reasoning_parts)[:300]

            scores.append(CandidateScore(
                candidate_index=i,
                external_id=candidate.external_id,
                ai_score=round(ai_score, 4),
                match_percent=round(ai_score * 100, 1),
                confidence=round(min(0.95, 0.5 + semantic * 0.5), 2),
                hiring_readiness=self._hiring_readiness(ai_score),
                integrity_score=round(integrity, 2),
                semantic_score=round(semantic, 4),
                experience_score=round(exp_score, 4),
                behavioral_score=round(0.5 + semantic * 0.3, 4),
                skill_gap_score=round(skill_score, 4),
                reasoning=reasoning,
                strengths=strengths,
                weaknesses=weaknesses,
                risks=integrity_risks,
                missing_skills=missing[:10],
                interview_questions=self._generate_questions(candidate, missing, job_description),
                behavioral_signals={"engagement": "moderate", "profile_completeness": len(candidate.skills) / 10},
            ))

        # Rank by ai_score descending
        scores.sort(key=lambda s: s.ai_score, reverse=True)
        top_k = min(self.config.top_k, len(scores))
        for rank, score in enumerate(scores[:top_k], start=1):
            score.rank = rank

        return scores[:top_k]
