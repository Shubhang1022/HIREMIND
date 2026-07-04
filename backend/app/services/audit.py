"""Audit logging service using Supabase."""

from __future__ import annotations

from typing import Any, Optional

from app.core.config import settings
from app.services.storage_provider import create_supabase_client

# Initialize supabase client
supabase_client = create_supabase_client(settings.supabase_url, settings.supabase_service_key)


async def log_audit(
    *,
    user_id: Optional[str],
    action: str,
    project_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Write audit log entry to Supabase."""
    try:
        supabase_client.table("audit_logs").insert({
            "user_id": user_id,
            "project_id": project_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details or {},
            "ip_address": ip_address,
        }).execute()
    except Exception:
        # Fallback or silent ignore to prevent audit failures blocking core workflows
        pass

