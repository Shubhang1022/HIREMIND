"""Audit logging service."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def log_audit(
    db: AsyncSession,
    *,
    user_id: Optional[str],
    action: str,
    project_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Write audit log entry. Uses raw SQL for Supabase compatibility."""
    from sqlalchemy import text

    await db.execute(
        text("""
            INSERT INTO audit_logs (user_id, project_id, action, resource_type, resource_id, details, ip_address)
            VALUES (:user_id, :project_id, :action, :resource_type, :resource_id, :details::jsonb, :ip_address)
        """),
        {
            "user_id": user_id,
            "project_id": project_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": str(details or {}),
            "ip_address": ip_address,
        },
    )
    await db.commit()
