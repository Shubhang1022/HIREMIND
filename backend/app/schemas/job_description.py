"""Pydantic v2 schemas for JobDescription."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class JobDescriptionBase(BaseModel):
    title: str = Field(..., description="Job title, e.g. 'Senior AI Engineer — Founding Team'")
    company: Optional[str] = None
    location: Optional[str] = None
    experience_years_min: Optional[int] = None
    experience_years_max: Optional[int] = None
    salary_min_lpa: Optional[float] = None
    salary_max_lpa: Optional[float] = None
    must_have_skills: Optional[List[str]] = None
    nice_to_have_skills: Optional[List[str]] = None
    hard_disqualifiers: Optional[List[str]] = None
    preferred_locations: Optional[List[str]] = None
    acceptable_locations: Optional[List[str]] = None
    full_text: Optional[str] = None
    is_active: bool = True


class JobDescriptionCreate(JobDescriptionBase):
    """Schema for creating a new JobDescription."""
    pass


class JobDescriptionRead(JobDescriptionBase):
    """Full JobDescription read response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
