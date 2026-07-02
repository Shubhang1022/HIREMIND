"""SQLAlchemy model for stored Job Descriptions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class JobDescription(Base):
    """Persisted Job Description used for ranking runs."""

    __tablename__ = "job_descriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Core fields ───────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    company: Mapped[Optional[str]] = mapped_column(String(256))
    location: Mapped[Optional[str]] = mapped_column(String(256))

    # ── Experience requirements ───────────────────────────────────────────────
    experience_years_min: Mapped[Optional[int]] = mapped_column(Integer)
    experience_years_max: Mapped[Optional[int]] = mapped_column(Integer)

    # ── Salary range ──────────────────────────────────────────────────────────
    salary_min_lpa: Mapped[Optional[float]] = mapped_column(Float)
    salary_max_lpa: Mapped[Optional[float]] = mapped_column(Float)

    # ── Skill / location lists (stored as JSONB arrays) ───────────────────────
    must_have_skills: Mapped[Optional[list]] = mapped_column(JSONB, default=list)
    nice_to_have_skills: Mapped[Optional[list]] = mapped_column(JSONB, default=list)
    hard_disqualifiers: Mapped[Optional[list]] = mapped_column(JSONB, default=list)
    preferred_locations: Mapped[Optional[list]] = mapped_column(JSONB, default=list)
    acceptable_locations: Mapped[Optional[list]] = mapped_column(JSONB, default=list)

    # ── Full text for embedding ───────────────────────────────────────────────
    full_text: Mapped[Optional[str]] = mapped_column(Text)

    # ── Status ────────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Timestamps ───────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ── Relationship ──────────────────────────────────────────────────────────
    ranking_runs: Mapped[list["RankingRun"]] = relationship(  # type: ignore[name-defined]
        "RankingRun", back_populates="job_description"
    )

    def __repr__(self) -> str:
        return f"<JobDescription {self.title!r} @ {self.company}>"


# Avoid circular import
from app.models.ranking import RankingRun  # noqa: E402, F401
