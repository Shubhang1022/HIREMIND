"""Normalize parsed records into canonical candidate format."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from src.ingestion.schema_infer import (
    extract_experience_from_text,
    extract_skills,
    infer_schema,
    parse_years,
)
from src.ingestion.types import NormalizedCandidate, ParsedRecord


def _build_name(record: dict[str, Any], mapping: dict[str, str]) -> tuple[str | None, str | None, str | None]:
    first = last = full = None
    for src_col, field in mapping.items():
        val = record.get(src_col)
        if not val:
            continue
        if field == "first_name":
            first = str(val).strip()
        elif field == "last_name":
            last = str(val).strip()
        elif field == "full_name":
            full = str(val).strip()

    if not full and (first or last):
        full = " ".join(filter(None, [first, last]))
    elif full and not first:
        parts = full.split(None, 1)
        first = parts[0] if parts else None
        last = parts[1] if len(parts) > 1 else None

    return first, last, full


def _build_embedding_text(candidate: NormalizedCandidate) -> str:
    parts = [
        candidate.full_name or "",
        candidate.headline or "",
        candidate.current_title or "",
        candidate.current_company or "",
        candidate.summary or "",
        candidate.location or "",
    ]
    for skill in candidate.skills:
        parts.append(skill.get("name", ""))
    for exp in candidate.experience:
        parts.extend([exp.get("title", ""), exp.get("company", ""), exp.get("description", "")])
    for edu in candidate.education:
        parts.extend([edu.get("degree", ""), edu.get("institution", ""), edu.get("field_of_study", "")])
    return " ".join(p for p in parts if p).strip()


class RecordNormalizer:
    """Convert arbitrary parsed records to NormalizedCandidate."""

    def __init__(self, schema_mapping: dict[str, str] | None = None):
        self.schema_mapping = schema_mapping or {}

    def infer_and_set_schema(self, columns: list[str]) -> dict[str, str]:
        self.schema_mapping = infer_schema(columns)
        return self.schema_mapping

    def normalize(self, record: ParsedRecord) -> NormalizedCandidate:
        raw = record.raw
        mapping = self.schema_mapping

        if not mapping and raw:
            mapping = infer_schema(list(raw.keys()))

        candidate = NormalizedCandidate(raw_data=raw)

        for src_col, field in mapping.items():
            val = raw.get(src_col)
            if val is None or val == "":
                continue
            if field == "skills":
                candidate.skills = extract_skills(val)
            elif field == "years_of_experience":
                candidate.years_of_experience = parse_years(val)
            elif field == "experience" and isinstance(val, str):
                candidate.experience = extract_experience_from_text(val)
            elif field == "education" and isinstance(val, str):
                candidate.education = [{"degree": val}]
            elif hasattr(candidate, field):
                setattr(candidate, field, str(val).strip() if not isinstance(val, (list, dict)) else val)

        first, last, full = _build_name(raw, mapping)
        candidate.first_name = candidate.first_name or first
        candidate.last_name = candidate.last_name or last
        candidate.full_name = candidate.full_name or full

        # Fallback: scan unmapped columns for skills-like content
        if not candidate.skills:
            for col, val in raw.items():
                if col not in mapping and val and re.search(r"skill", col, re.I):
                    candidate.skills = extract_skills(val)
                    break

        # Use text content from documents
        if record.text_content and not candidate.summary:
            candidate.summary = record.text_content[:2000]

        if not candidate.external_id:
            key = candidate.email or candidate.full_name or str(record.record_index)
            candidate.external_id = hashlib.md5(f"{record.source_file}:{key}".encode()).hexdigest()[:12]

        candidate.text_for_embedding = _build_embedding_text(candidate)
        return candidate
