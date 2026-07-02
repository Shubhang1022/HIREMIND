"""Ranking endpoints.

Endpoints
---------
POST /api/v1/ranking/run         — trigger a new ranking run
GET  /api/v1/ranking/{run_id}    — get ranking run status + results
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_session
from app.models.ranking import RankingRun
from app.schemas.ranking import RankingRunCreate, RankingRunDetail, RankingRunRead

router = APIRouter()


@router.post(
    "/run",
    response_model=RankingRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a ranking run",
)
async def trigger_ranking_run(
    payload: RankingRunCreate,
    session: AsyncSession = Depends(get_session),
) -> RankingRunRead:
    """Create a new RankingRun record with status=pending.

    In a full implementation this would enqueue a background task (e.g. via
    Celery or FastAPI ``BackgroundTasks``) to invoke ``precompute.py`` and
    ``rank.py``.  For now it creates the record and returns 202 Accepted.
    """
    run = RankingRun(
        job_description_id=payload.job_description_id,
        status="pending",
        config_snapshot=payload.config_override,
    )
    session.add(run)
    await session.flush()  # Get the generated UUID
    await session.refresh(run)

    return RankingRunRead.model_validate(run)


@router.get(
    "/{run_id}",
    response_model=RankingRunDetail,
    summary="Get ranking run status and results",
)
async def get_ranking_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> RankingRunDetail:
    """Return full details of a ranking run, including all CandidateRank rows."""
    stmt = (
        select(RankingRun)
        .where(RankingRun.id == run_id)
        .options(selectinload(RankingRun.candidate_ranks))
    )
    result = await session.execute(stmt)
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ranking run {run_id} not found.",
        )

    return RankingRunDetail.model_validate(run)
