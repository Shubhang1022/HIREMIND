"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("", summary="Health check")
async def health_check() -> dict:
    """Return a simple liveness probe response."""
    return {"status": "ok", "service": "india-run-ai-copilot", "version": "1.0.0"}
