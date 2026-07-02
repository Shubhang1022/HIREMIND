from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractedSkill:
    name: str
    evidence: list[str] = field(default_factory=list)
    proficiency: str | None = None


@dataclass(frozen=True)
class CandidateUnderstanding:
    candidate_id: str
    experience_summary: str
    years_experience: float
    skills: list[ExtractedSkill]
    leadership_signals: list[str]
    domain_expertise: list[str]
    career_growth_signals: list[str]


@dataclass(frozen=True)
class JobUnderstanding:
    role: str
    seniority: str
    required_skills: list[str]
    preferred_skills: list[str]
    behavioral_expectations: list[str]
    job_summary: str
    domains: list[str] = field(default_factory=list)
    min_experience: float = 0.0


@dataclass(frozen=True)
class RetrievalResult:
    candidate_id: str
    similarity: float


@dataclass(frozen=True)
class RankingSignals:
    semantic_fit: float
    experience_fit: float
    leadership: float
    behavioral_signals: float
    hiring_readiness: float
    career_stability: float
    candidate_integrity: float

    def final(self, weights: dict[str, float] | None = None) -> float:
        w = weights or {
            "semantic_fit": 0.30,
            "experience_fit": 0.20,
            "leadership": 0.10,
            "behavioral_signals": 0.10,
            "hiring_readiness": 0.10,
            "career_stability": 0.10,
            "candidate_integrity": 0.10,
        }
        return (
            w["semantic_fit"] * self.semantic_fit
            + w["experience_fit"] * self.experience_fit
            + w["leadership"] * self.leadership
            + w["behavioral_signals"] * self.behavioral_signals
            + w["hiring_readiness"] * self.hiring_readiness
            + w["career_stability"] * self.career_stability
            + w["candidate_integrity"] * self.candidate_integrity
        )


@dataclass(frozen=True)
class Explainability:
    candidate_id: str
    why_selected: list[str]
    why_not_selected: list[str]
    missing_skills: list[str]
    risks: list[str]
    recruiter_summary: str


@dataclass(frozen=True)
class InterviewPlan:
    candidate_id: str
    questions: list[str]

