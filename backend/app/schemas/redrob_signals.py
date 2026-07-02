"""Pydantic v2 schema for RedrobSignal."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict


class RedrobSignalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID

    open_to_work_flag: bool = False
    last_active_date: Optional[date] = None
    notice_period_days: Optional[int] = None
    willing_to_relocate: bool = False

    recruiter_response_rate: Optional[float] = None
    avg_response_time_hours: Optional[float] = None
    interview_completion_rate: Optional[float] = None
    offer_acceptance_rate: Optional[float] = None

    verified_email: bool = False
    verified_phone: bool = False
    linkedin_connected: bool = False

    github_activity_score: Optional[float] = None
    saved_by_recruiters_30d: int = 0
    profile_completeness_score: Optional[float] = None

    expected_salary_min_lpa: Optional[float] = None
    expected_salary_max_lpa: Optional[float] = None

    skill_assessment_scores: Optional[Dict[str, float]] = None

    created_at: datetime
    updated_at: datetime
