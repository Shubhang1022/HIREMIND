"""SQLAlchemy models for ranking runs and per-candidate rank results."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class RankingRun(Base):
    """A single execution of the ranking pipeline against a given JD.

    One RankingRun → many CandidateRank rows (the top-100 output).
    """

    __tablename__ = "ranking_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_description_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_descriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )  # pending / running / completed / failed

    # ── Timing ────────────────────────────────────────────────────────────────
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    phase1_runtime_seconds: Mapped[Optional[float]] = mapped_column(Float)
    phase2_runtime_seconds: Mapped[Optional[float]] = mapped_column(Float)

    # ── Run statistics (mirroring audit log schema) ───────────────────────────
    total_candidates_read: Mapped[Optional[int]] = mapped_column(Integer)
    valid_candidates: Mapped[Optional[int]] = mapped_column(Integer)
    honeypots_detected: Mapped[Optional[int]] = mapped_column(Integer)
    consulting_only_disqualified: Mapped[Optional[int]] = mapped_column(Integer)
    non_technical_disqualified: Mapped[Optional[int]] = mapped_column(Integer)
    candidates_scored: Mapped[Optional[int]] = mapped_column(Integer)

    # ── Score statistics (JSONB) ──────────────────────────────────────────────
    score_stats: Mapped[Optional[dict]] = mapped_column(JSONB)
    weights_used: Mapped[Optional[dict]] = mapped_column(JSONB)

    # ── Config snapshot ───────────────────────────────────────────────────────
    model_used: Mapped[Optional[str]] = mapped_column(String(256))
    config_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)

    # ── Error information (if failed) ─────────────────────────────────────────
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # ── Timestamps ───────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    job_description: Mapped[Optional["JobDescription"]] = relationship(  # type: ignore[name-defined]
        "JobDescription", back_populates="ranking_runs"
    )
    candidate_ranks: Mapped[list["CandidateRank"]] = relationship(
        "CandidateRank", back_populates="ranking_run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<RankingRun {self.id} status={self.status}>"


class CandidateRank(Base):
    """A single candidate's rank result within a RankingRun (one of top-100)."""

    __tablename__ = "candidate_ranks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ranking_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ranking_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Rank output ───────────────────────────────────────────────────────────
    rank: Mapped[int] = mapped_column(Integer, nullable=False)          # 1–100
    final_score: Mapped[float] = mapped_column(Float, nullable=False)   # 4 d.p.
    reasoning: Mapped[Optional[str]] = mapped_column(String(300))

    # ── Dimension scores ──────────────────────────────────────────────────────
    semantic_skill_fit: Mapped[Optional[float]] = mapped_column(Float)
    experience_quality: Mapped[Optional[float]] = mapped_column(Float)
    career_progression: Mapped[Optional[float]] = mapped_column(Float)
    behavioral_signals: Mapped[Optional[float]] = mapped_column(Float)
    logistics_fit: Mapped[Optional[float]] = mapped_column(Float)
    profile_integrity: Mapped[Optional[float]] = mapped_column(Float)
    disqualifier_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # ── Relationships ─────────────────────────────────────────────────────────
    ranking_run: Mapped["RankingRun"] = relationship(
        "RankingRun", back_populates="candidate_ranks"
    )
    candidate: Mapped["Candidate"] = relationship(  # type: ignore[name-defined]
        "Candidate", back_populates="candidate_ranks"
    )

    def __repr__(self) -> str:
        return f"<CandidateRank rank={self.rank} score={self.final_score:.4f}>"


# Avoid circular import
from app.models.job_description import JobDescription  # noqa: E402, F401
from app.models.candidate import Candidate  # noqa: E402, F401
