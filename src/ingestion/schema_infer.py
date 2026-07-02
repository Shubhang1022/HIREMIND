"""Schema inference — maps arbitrary column names to canonical fields."""

from __future__ import annotations

import re
from typing import Any

# Column name patterns → canonical field
FIELD_PATTERNS: dict[str, list[str]] = {
    "full_name": [
        r"^name$", r"full.?name", r"candidate.?name", r"applicant.?name",
        r"^candidate$", r"^person$",
    ],
    "first_name": [r"first.?name", r"given.?name", r"fname"],
    "last_name": [r"last.?name", r"surname", r"family.?name", r"lname"],
    "email": [r"^email", r"e.?mail", r"mail.?id", r"contact.?email"],
    "phone": [r"^phone", r"mobile", r"contact.?number", r"tel"],
    "headline": [r"headline", r"tagline", r"professional.?headline"],
    "summary": [r"^summary", r"^about", r"bio", r"profile.?summary", r"description"],
    "current_title": [
        r"current.?title", r"job.?title", r"^title$", r"position", r"role",
        r"designation", r"current.?role",
    ],
    "current_company": [
        r"current.?company", r"^company$", r"employer", r"organization",
        r"org", r"current.?employer",
    ],
    "location": [r"^location", r"^city", r"^address", r"^region", r"^state"],
    "country": [r"^country", r"nationality"],
    "years_of_experience": [
        r"years?.?of?.?exp", r"^yoe$", r"experience.?years", r"total.?exp",
        r"^experience$", r"exp.?years",
    ],
    "skills": [r"^skills", r"skill.?set", r"technical.?skills", r"competencies"],
    "education": [r"^education", r"qualification", r"degree", r"academic"],
    "experience": [r"^experience", r"work.?history", r"employment", r"career"],
    "certifications": [r"certification", r"certificate", r"credential"],
}


def _match_field(column: str) -> str | None:
    col = column.strip().lower().replace(" ", "_")
    for field, patterns in FIELD_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, col, re.IGNORECASE):
                return field
    return None


def infer_schema(columns: list[str]) -> dict[str, str]:
    """Map source columns to canonical field names."""
    mapping: dict[str, str] = {}
    used_fields: set[str] = set()

    for col in columns:
        field = _match_field(col)
        if field and field not in used_fields:
            mapping[col] = field
            used_fields.add(field)

    return mapping


def extract_skills(value: Any) -> list[dict[str, str]]:
    """Parse skills from various formats."""
    if not value:
        return []
    if isinstance(value, list):
        return [{"name": str(s).strip()} for s in value if s]
    text = str(value)
    # Split on common delimiters
    parts = re.split(r"[,;|/•·\n]", text)
    return [{"name": p.strip()} for p in parts if p.strip()]


def extract_experience_from_text(text: str) -> list[dict[str, Any]]:
    """Heuristic extraction of experience blocks from free text."""
    if not text:
        return []
    entries: list[dict[str, Any]] = []
    blocks = re.split(r"\n{2,}", text.strip())
    for block in blocks[:10]:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        entry: dict[str, Any] = {"title": lines[0]}
        if len(lines) > 1:
            entry["company"] = lines[1]
        if len(lines) > 2:
            entry["description"] = " ".join(lines[2:])
        entries.append(entry)
    return entries


def parse_years(value: Any) -> float | None:
    """Parse years of experience from various formats."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None
