"""Candidate endpoints.

Endpoints
---------
GET  /api/v1/candidates          — paginated list of candidates
GET  /api/v1/candidates/{id}     — full candidate detail
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_session
from app.models.candidate import Candidate
from app.schemas.candidate import CandidateListItem, CandidateRead

router = APIRouter()


@router.get("", response_model=List[CandidateListItem], summary="List candidates")
async def list_candidates(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=50, ge=1, le=200, description="Items per page"),
    location: Optional[str] = Query(default=None, description="Filter by location (partial match)"),
    session: AsyncSession = Depends(get_session),
) -> List[CandidateListItem]:
    """Return a paginated list of candidates.

    Supports optional filtering by location.
    """
    stmt = select(Candidate)

    if location:
        stmt = stmt.where(Candidate.location.ilike(f"%{location}%"))

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    result = await session.execute(stmt)
    candidates = result.scalars().all()

    return [CandidateListItem.model_validate(c) for c in candidates]


@router.get("/{candidate_id}", response_model=CandidateRead, summary="Get candidate by ID")
async def get_candidate(
    candidate_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> CandidateRead:
    """Return full candidate profile including all nested relationships.

    ``candidate_id`` is the internal UUID, not the dataset ``CAND_XXXXXXX`` string.
    """
    stmt = (
        select(Candidate)
        .where(Candidate.id == candidate_id)
        .options(
            selectinload(Candidate.career_history),
            selectinload(Candidate.education),
            selectinload(Candidate.skills),
            selectinload(Candidate.certifications),
            selectinload(Candidate.languages),
        )
    )
    result = await session.execute(stmt)
    candidate = result.scalar_one_or_none()

    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate {candidate_id} not found.",
        )

    return CandidateRead.model_validate(candidate)
