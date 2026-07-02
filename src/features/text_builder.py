"""Candidate and JD text builders for embedding-based semantic similarity.

Implements the text construction logic described in docs/RankingLogic.md §3.2.
"""
from __future__ import annotations

MAX_TEXT_CHARS = 4096
MAX_RECENT_ROLES = 5


def build_candidate_text(candidate: dict) -> str:
    """Build a single text string from a candidate dict for embedding.

    Concatenates (in order):
      1. profile.headline
      2. profile.summary
      3. Career descriptions — most recent first, top 5 roles;
         the current role is prefixed with "Currently: "
      4. Skill names joined by spaces

    The result is truncated to MAX_TEXT_CHARS (4096) characters.

    Args:
        candidate: Raw candidate dict matching the Redrob candidate schema.

    Returns:
        A combined text string, or an empty string if the candidate is empty.
    """
    if not candidate:
        return ""

    parts: list[str] = []

    profile: dict = candidate.get("profile") or {}
    headline: str = (profile.get("headline") or "").strip()
    summary: str = (profile.get("summary") or "").strip()

    if headline:
        parts.append(headline)
    if summary:
        parts.append(summary)

    # Career history — sorted most-recent first, top 5
    career_history: list[dict] = candidate.get("career_history") or []
    sorted_history = sorted(
        career_history,
        key=lambda r: str(r.get("start_date") or ""),
        reverse=True,
    )

    for i, role in enumerate(sorted_history[:MAX_RECENT_ROLES]):
        title = (role.get("title") or "").strip()
        company = (role.get("company") or "").strip()
        description = (role.get("description") or "").strip()

        role_header = f"{title} at {company}" if title or company else ""
        role_text = f"{role_header}: {description}" if role_header and description else (
            role_header or description
        )

        # Determine if this is the current role: either is_current flag or i==0
        is_current = role.get("is_current", False) or (i == 0 and not role.get("end_date"))
        if is_current and role_text:
            role_text = f"Currently: {role_text}"

        if role_text:
            parts.append(role_text)

    # Skill names
    skills: list[dict] = candidate.get("skills") or []
    skill_names = " ".join(
        s.get("name", "").strip() for s in skills if s.get("name", "").strip()
    )
    if skill_names:
        parts.append(skill_names)

    combined = " ".join(filter(None, parts))
    return combined[:MAX_TEXT_CHARS]


def build_jd_text() -> str:
    """Return the enriched JD text used for embedding computation.

    Imports and returns ``JD_TEXT`` from ``config.jd_text``.

    Returns:
        The JD text string.
    """
    from config.jd_text import JD_TEXT  # noqa: PLC0415

    return JD_TEXT
