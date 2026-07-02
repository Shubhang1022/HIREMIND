"""SQLAlchemy models for candidate profiles.

Models
------
- Candidate        — core profile record
- CareerHistory    — one row per job role
- Education        — one row per degree / certification block
- Skill            — one row per skill entry
- Certification    — professional certifications
- Language         — spoken languages
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Candidate(Base):
    """Core candidate profile, mirroring the top-level candidate JSON structure."""

    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Source identifier from the dataset (e.g. "CAND_0001234")
    candidate_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)

    # ── Profile fields ────────────────────────────────────────────────────────
    first_name: Mapped[Optional[str]] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128))
    headline: Mapped[Optional[str]] = mapped_column(String(512))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    current_title: Mapped[Optional[str]] = mapped_column(String(256))
    current_company: Mapped[Optional[str]] = mapped_column(String(256))
    location: Mapped[Optional[str]] = mapped_column(String(256))
    country: Mapped[Optional[str]] = mapped_column(String(128))
    years_of_experience: Mapped[Optional[float]] = mapped_column(Float)

    # ── Logistics ────────────────────────────────────────────────────────────
    preferred_work_mode: Mapped[Optional[str]] = mapped_column(String(64))
    willing_to_relocate: Mapped[Optional[bool]] = mapped_column(Boolean)

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

    # ── Relationships ─────────────────────────────────────────────────────────
    career_history: Mapped[List["CareerHistory"]] = relationship(
        "CareerHistory", back_populates="candidate", cascade="all, delete-orphan"
    )
    education: Mapped[List["Education"]] = relationship(
        "Education", back_populates="candidate", cascade="all, delete-orphan"
    )
    skills: Mapped[List["Skill"]] = relationship(
        "Skill", back_populates="candidate", cascade="all, delete-orphan"
    )
    certifications: Mapped[List["Certification"]] = relationship(
        "Certification", back_populates="candidate", cascade="all, delete-orphan"
    )
    languages: Mapped[List["Language"]] = relationship(
        "Language", back_populates="candidate", cascade="all, delete-orphan"
    )
    redrob_signal: Mapped[Optional["RedrobSignal"]] = relationship(
        "RedrobSignal", back_populates="candidate", uselist=False, cascade="all, delete-orphan"
    )
    candidate_ranks: Mapped[List["CandidateRank"]] = relationship(
        "CandidateRank", back_populates="candidate"
    )

    def __repr__(self) -> str:
        return f"<Candidate {self.candidate_id}>"


class CareerHistory(Base):
    """One row per job role in the candidate's career history."""

    __tablename__ = "career_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )

    company: Mapped[Optional[str]] = mapped_column(String(256))
    title: Mapped[Optional[str]] = mapped_column(String(256))
    description: Mapped[Optional[str]] = mapped_column(Text)
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    duration_months: Mapped[Optional[int]] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    company_size: Mapped[Optional[str]] = mapped_column(String(64))  # e.g. "1-10", "201-500"
    location: Mapped[Optional[str]] = mapped_column(String(256))

    # ── Relationship ──────────────────────────────────────────────────────────
    candidate: Mapped["Candidate"] = relationship("Candidate", back_populates="career_history")

    def __repr__(self) -> str:
        return f"<CareerHistory {self.title} @ {self.company}>"


class Education(Base):
    """Educational qualification record."""

    __tablename__ = "education"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )

    institution: Mapped[Optional[str]] = mapped_column(String(256))
    degree: Mapped[Optional[str]] = mapped_column(String(256))
    field_of_study: Mapped[Optional[str]] = mapped_column(String(256))
    start_year: Mapped[Optional[int]] = mapped_column(Integer)
    end_year: Mapped[Optional[int]] = mapped_column(Integer)
    grade: Mapped[Optional[str]] = mapped_column(String(64))

    # ── Relationship ──────────────────────────────────────────────────────────
    candidate: Mapped["Candidate"] = relationship("Candidate", back_populates="education")

    def __repr__(self) -> str:
        return f"<Education {self.degree} from {self.institution}>"


class Skill(Base):
    """Individual skill entry with proficiency and duration metadata."""

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    proficiency: Mapped[Optional[str]] = mapped_column(
        String(32)
    )  # beginner / intermediate / advanced / expert
    duration_months: Mapped[Optional[int]] = mapped_column(Integer)
    endorsements: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Relationship ──────────────────────────────────────────────────────────
    candidate: Mapped["Candidate"] = relationship("Candidate", back_populates="skills")

    def __repr__(self) -> str:
        return f"<Skill {self.name} ({self.proficiency})>"


class Certification(Base):
    """Professional certification or accreditation."""

    __tablename__ = "certifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    issuing_organization: Mapped[Optional[str]] = mapped_column(String(256))
    issue_date: Mapped[Optional[date]] = mapped_column(Date)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date)
    credential_id: Mapped[Optional[str]] = mapped_column(String(256))

    # ── Relationship ──────────────────────────────────────────────────────────
    candidate: Mapped["Candidate"] = relationship("Candidate", back_populates="certifications")

    def __repr__(self) -> str:
        return f"<Certification {self.name}>"


class Language(Base):
    """Spoken / written language record."""

    __tablename__ = "languages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    proficiency: Mapped[Optional[str]] = mapped_column(String(64))  # native / fluent / conversational

    # ── Relationship ──────────────────────────────────────────────────────────
    candidate: Mapped["Candidate"] = relationship("Candidate", back_populates="languages")

    def __repr__(self) -> str:
        return f"<Language {self.name} ({self.proficiency})>"


# Import here to avoid circular reference issues at module level
from app.models.redrob_signals import RedrobSignal  # noqa: E402, F401
from app.models.ranking import CandidateRank  # noqa: E402, F401
