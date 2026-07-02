"""Pydantic schemas for HireMind AI API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Projects ──────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    project_hash: Optional[str] = None
    dataset_hash: Optional[str] = None
    jd_hash: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    status: Optional[str] = None


class ProjectResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    description: Optional[str] = None
    status: str
    candidate_count: int = 0
    job_count: int = 0
    created_at: datetime
    updated_at: datetime


# ── Jobs ──────────────────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=10)
    company: Optional[str] = None
    location: Optional[str] = None
    work_mode: Optional[str] = None
    required_skills: list[str] = Field(default_factory=list)
    min_experience: Optional[float] = None
    
    # Recruiter-controlled metadata
    openings: Optional[int] = Field(5, ge=1)
    shortlist_size: Optional[int] = Field(15, ge=1)
    priority: Optional[str] = Field("balanced", pattern="^(quality|balanced|screening)$")
    min_match_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    salary_range: Optional[str] = None
    job_location: Optional[str] = None
    employment_type: Optional[str] = None


class JobResponse(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    description: str
    required_skills: list[str] = Field(default_factory=list)
    created_at: datetime
    
    # Recruiter-controlled metadata
    openings: Optional[int] = None
    shortlist_size: Optional[int] = None
    priority: Optional[str] = None
    min_match_percent: Optional[float] = None
    salary_range: Optional[str] = None
    job_location: Optional[str] = None
    employment_type: Optional[str] = None


# ── Candidates ────────────────────────────────────────────────────────────────

class CandidateResponse(BaseModel):
    id: UUID
    project_id: UUID
    external_id: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    headline: Optional[str] = None
    current_title: Optional[str] = None
    current_company: Optional[str] = None
    location: Optional[str] = None
    years_of_experience: Optional[float] = None
    skills: list[dict[str, Any]] = Field(default_factory=list)
    experience: list[dict[str, Any]] = Field(default_factory=list)
    education: list[dict[str, Any]] = Field(default_factory=list)


# ── Rankings ──────────────────────────────────────────────────────────────────

class AnalysisRequest(BaseModel):
    job_id: UUID
    top_k: int = Field(default=100, ge=1, le=500)
    performance_mode: Optional[str] = "balanced"


class RankingResultResponse(BaseModel):
    id: UUID
    candidate_id: UUID
    rank: int
    ai_score: float
    match_percent: float
    confidence: float
    hiring_readiness: str
    integrity_score: float
    reasoning: Optional[str] = None
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    interview_questions: list[str] = Field(default_factory=list)
    candidate: Optional[CandidateResponse] = None


class RankingResponse(BaseModel):
    id: UUID
    project_id: UUID
    job_id: UUID
    status: str
    total_candidates: int
    ranked_count: int
    results: list[RankingResultResponse] = Field(default_factory=list)
    created_at: datetime


# ── Uploads ───────────────────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    id: UUID
    project_id: UUID
    file_name: str
    file_type: str
    status: str
    records_parsed: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# ── Analytics ─────────────────────────────────────────────────────────────────

class AnalyticsResponse(BaseModel):
    skill_distribution: list[dict[str, Any]] = Field(default_factory=list)
    experience_distribution: list[dict[str, Any]] = Field(default_factory=list)
    quality_breakdown: dict[str, int] = Field(default_factory=dict)
    match_breakdown: dict[str, int] = Field(default_factory=dict)
    hidden_gems: list[dict[str, Any]] = Field(default_factory=list)
    high_risk_profiles: list[dict[str, Any]] = Field(default_factory=list)
    hiring_funnel: dict[str, int] = Field(default_factory=dict)


# ── Exports ───────────────────────────────────────────────────────────────────

class ExportRequest(BaseModel):
    ranking_id: UUID
    format: str = Field(default="csv", pattern="^(csv|json|pdf)$")
    report_type: str = Field(default="hiring", pattern="^(hiring|candidate|analytics)$")
