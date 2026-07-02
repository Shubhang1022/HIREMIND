"""Pydantic v2 schemas for RankingRun and CandidateRank."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── CandidateRank ─────────────────────────────────────────────────────────────

class CandidateRankRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    rank: int = Field(..., ge=1, le=100)
    final_score: float = Field(..., ge=0.0, le=1.0)
    reasoning: Optional[str] = Field(None, max_length=300)

    semantic_skill_fit: Optional[float] = None
    experience_quality: Optional[float] = None
    career_progression: Optional[float] = None
    behavioral_signals: Optional[float] = None
    logistics_fit: Optional[float] = None
    profile_integrity: Optional[float] = None
    disqualifier_multiplier: float = 1.0


# ── RankingRun ────────────────────────────────────────────────────────────────

class RankingRunCreate(BaseModel):
    """Request body to trigger a new ranking run."""

    job_description_id: Optional[uuid.UUID] = Field(
        None,
        description="UUID of the JobDescription to rank against. "
                    "If omitted, uses the hardcoded JD from config/jd_text.py.",
    )
    candidates_file: Optional[str] = Field(
        None,
        description="Optional override path to candidates.jsonl. "
                    "Defaults to the value in ranking_config.yaml.",
    )
    config_override: Optional[Dict] = Field(
        None,
        description="Optional YAML config overrides (partial dict). "
                    "Merged on top of ranking_config.yaml.",
    )


class RankingRunRead(BaseModel):
    """Summary view of a RankingRun (no candidate rank rows)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_description_id: Optional[uuid.UUID] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    phase1_runtime_seconds: Optional[float] = None
    phase2_runtime_seconds: Optional[float] = None
    total_candidates_read: Optional[int] = None
    valid_candidates: Optional[int] = None
    honeypots_detected: Optional[int] = None
    consulting_only_disqualified: Optional[int] = None
    non_technical_disqualified: Optional[int] = None
    candidates_scored: Optional[int] = None
    score_stats: Optional[Dict] = None
    model_used: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime


class RankingRunDetail(RankingRunRead):
    """Full RankingRun detail including all 100 CandidateRank rows."""

    candidate_ranks: List[CandidateRankRead] = []
