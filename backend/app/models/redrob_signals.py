"""SQLAlchemy model for Redrob platform engagement signals.

This is a 1-to-1 extension of the Candidate model, holding the 23 behavioral
and platform engagement signals described in ``redrob_signals_doc.docx``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class RedrobSignal(Base):
    """Redrob platform signals — one row per Candidate (1-to-1)."""

    __tablename__ = "redrob_signals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        unique=True,  # Enforces 1-to-1 at the DB level
        nullable=False,
        index=True,
    )

    # ── Hiring readiness ──────────────────────────────────────────────────────
    open_to_work_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_active_date: Mapped[Optional[date]] = mapped_column(Date)
    notice_period_days: Mapped[Optional[int]] = mapped_column(Integer)
    willing_to_relocate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Recruiter engagement ──────────────────────────────────────────────────
    recruiter_response_rate: Mapped[Optional[float]] = mapped_column(Float)  # 0.0–1.0
    avg_response_time_hours: Mapped[Optional[float]] = mapped_column(Float)
    interview_completion_rate: Mapped[Optional[float]] = mapped_column(Float)  # 0.0–1.0
    offer_acceptance_rate: Mapped[Optional[float]] = mapped_column(Float)  # 0.0–1.0 or -1 (no history)

    # ── Verification / trust ──────────────────────────────────────────────────
    verified_email: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_phone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    linkedin_connected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Platform activity ─────────────────────────────────────────────────────
    github_activity_score: Mapped[Optional[float]] = mapped_column(Float)  # 0–100 or -1 if not linked
    saved_by_recruiters_30d: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    profile_completeness_score: Mapped[Optional[float]] = mapped_column(Float)  # 0–100

    # ── Salary expectation ────────────────────────────────────────────────────
    expected_salary_min_lpa: Mapped[Optional[float]] = mapped_column(Float)
    expected_salary_max_lpa: Mapped[Optional[float]] = mapped_column(Float)

    # ── Skill assessment scores (JSON dict: {skill_name: 0-100}) ─────────────
    skill_assessment_scores: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)

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
    candidate: Mapped["Candidate"] = relationship(  # type: ignore[name-defined]
        "Candidate", back_populates="redrob_signal"
    )

    def __repr__(self) -> str:
        return f"<RedrobSignal candidate_id={self.candidate_id}>"
