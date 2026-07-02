"""Types for the generic ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedRecord:
    """Raw parsed record before normalization."""

    source_file: str
    record_index: int
    raw: dict[str, Any]
    text_content: str = ""


@dataclass
class NormalizedCandidate:
    """Canonical candidate representation inferred from any dataset."""

    external_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    headline: str | None = None
    summary: str | None = None
    current_title: str | None = None
    current_company: str | None = None
    location: str | None = None
    country: str | None = None
    years_of_experience: float | None = None
    skills: list[dict[str, Any]] = field(default_factory=list)
    experience: list[dict[str, Any]] = field(default_factory=list)
    education: list[dict[str, Any]] = field(default_factory=list)
    certifications: list[dict[str, Any]] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)
    text_for_embedding: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "headline": self.headline,
            "summary": self.summary,
            "current_title": self.current_title,
            "current_company": self.current_company,
            "location": self.location,
            "country": self.country,
            "years_of_experience": self.years_of_experience,
            "skills": self.skills,
            "experience": self.experience,
            "education": self.education,
            "certifications": self.certifications,
            "raw_data": self.raw_data,
            "text_for_embedding": self.text_for_embedding,
        }
