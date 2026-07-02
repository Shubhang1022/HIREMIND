"""Pydantic v2 schemas for Candidate and related models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── CareerHistory ─────────────────────────────────────────────────────────────

class CareerHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    duration_months: Optional[int] = None
    is_current: bool = False
    company_size: Optional[str] = None
    location: Optional[str] = None


# ── Education ─────────────────────────────────────────────────────────────────

class EducationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    institution: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    grade: Optional[str] = None


# ── Skill ─────────────────────────────────────────────────────────────────────

class SkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    proficiency: Optional[str] = None
    duration_months: Optional[int] = None
    endorsements: int = 0


# ── Certification ─────────────────────────────────────────────────────────────

class CertificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    issuing_organization: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    credential_id: Optional[str] = None


# ── Language ──────────────────────────────────────────────────────────────────

class LanguageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    proficiency: Optional[str] = None


# ── Candidate ─────────────────────────────────────────────────────────────────

class CandidateBase(BaseModel):
    """Shared fields used by both Create and Read schemas."""

    candidate_id: str = Field(..., description="Source dataset identifier, e.g. CAND_0001234")
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    headline: Optional[str] = None
    summary: Optional[str] = None
    current_title: Optional[str] = None
    current_company: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    years_of_experience: Optional[float] = None
    preferred_work_mode: Optional[str] = None
    willing_to_relocate: Optional[bool] = None


class CandidateCreate(CandidateBase):
    """Schema for creating a new Candidate record."""
    pass


class CandidateRead(CandidateBase):
    """Full candidate read response including all nested relationships."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    career_history: List[CareerHistoryRead] = []
    education: List[EducationRead] = []
    skills: List[SkillRead] = []
    certifications: List[CertificationRead] = []
    languages: List[LanguageRead] = []


class CandidateListItem(BaseModel):
    """Lightweight candidate item for list views (no nested data)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    current_title: Optional[str] = None
    current_company: Optional[str] = None
    location: Optional[str] = None
    years_of_experience: Optional[float] = None
