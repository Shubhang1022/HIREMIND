"""Export endpoints for submission and audit artifacts."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.core.config import settings

router = APIRouter()


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        # Resolve relative to project root (parent of backend/)
        root = Path(__file__).resolve().parents[5]
        path = root / path_str.lstrip("./")
    return path


@router.get("/submission", summary="Download submission.csv")
async def download_submission() -> FileResponse:
    """Return the latest ranking submission CSV if present."""
    path = _resolve(settings.submission_output)
    if not path.is_file() or path.stat().st_size <= 50:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="submission.csv not found. Run: python run_pipeline.py ...",
        )
    return FileResponse(path, media_type="text/csv", filename="submission.csv")


@router.get("/audit", summary="Download ranking audit JSON")
async def download_audit() -> FileResponse:
    """Return ranking_audit.json if the pipeline was run with --audit-log."""
    path = _resolve("./ranking_audit.json")
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ranking_audit.json not found. Re-run rank.py with --audit-log.",
        )
    return FileResponse(path, media_type="application/json", filename="ranking_audit.json")
