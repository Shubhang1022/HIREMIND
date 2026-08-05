"""Extended Health Check & Production Metrics API Endpoints."""

import logging
import os
import psutil
import shutil
import time
from typing import Dict, Any
from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.services.storage_provider import create_supabase_client

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize supabase client
supabase_client = create_supabase_client(settings.supabase_url, settings.supabase_service_key)


@router.get("", summary="Extended health probe")
async def health_check() -> Dict[str, Any]:
    """Perform health checks on all critical subsystems without loading ML models."""
    # 1. Database status & Queue info
    db_status_str = "disconnected"
    queue_len = 0
    indexing_jobs_list = []
    worker_status_str = "idle"
    try:
        from app.services.job_manager import DBConnection
        import uuid
        async with DBConnection() as conn:
            val = await conn.fetchval("SELECT 1")
            if val == 1:
                db_status_str = "connected"
                # Queue length
                queue_len = await conn.fetchval(
                    "SELECT COUNT(*) FROM public.background_jobs WHERE status = 'queued'"
                )
                # Active jobs
                rows = await conn.fetch(
                    """
                    SELECT id, project_id, current_stage, progress_percentage, status 
                    FROM public.background_jobs 
                    WHERE status NOT IN ('completed', 'failed', 'cancelled')
                    """
                )
                indexing_jobs_list = [
                    {
                        "job_id": str(r["id"]),
                        "project_id": str(r["project_id"]),
                        "current_stage": r["current_stage"],
                        "progress_percentage": r["progress_percentage"],
                        "status": r["status"]
                    }
                    for r in rows
                ]
                if indexing_jobs_list:
                    worker_status_str = "running"
    except Exception as e:
        logger.error("Health check DB query failed: %s", e)

    # 2. Storage status
    storage_status_str = "disconnected"
    try:
        from app.services.storage_provider import StorageService
        StorageService.file_exists("candidate-files", "_startup_probe")
        storage_status_str = "connected"
    except Exception:
        pass

    # 3. Model status
    from app.services import model_service as _ms
    model_status_str = _ms.get_load_state()

    # 4. Process stats
    rss_mb = 0.0
    cpu_pct = 0.0
    try:
        proc = psutil.Process(os.getpid())
        rss_mb = proc.memory_info().rss / (1024 * 1024)
        cpu_pct = proc.cpu_percent(interval=None)
    except Exception:
        pass

    # Get cached git SHA if main is imported, otherwise run fallback
    git_sha = "unknown"
    try:
        from app.main import _git_sha
        git_sha = _git_sha
    except Exception:
        git_sha = os.environ.get("RENDER_GIT_COMMIT", "unknown")

    overall_health = "healthy" if db_status_str == "connected" and storage_status_str == "connected" else "unhealthy"
    
    return {
        "status": overall_health,
        "database_status": db_status_str,
        "storage_status": storage_status_str,
        "background_worker_status": worker_status_str,
        "queue_length": queue_len,
        "rss_memory_mb": round(rss_mb, 2),
        "cpu_percent": round(cpu_pct, 1),
        "version": git_sha,
        "model_status": model_status_str
    }


@router.get("/metrics", summary="Production performance metrics")
async def metrics() -> Dict[str, Any]:
    """Retrieve statistical aggregates of analysis jobs and queue latency."""
    try:
        # Query total, completed, failed counts from rankings
        total_rankings = supabase_client.table("rankings").select("id", count="exact").execute()
        total_count = total_rankings.count or 0

        completed_rankings = supabase_client.table("rankings").select("id", count="exact").eq("status", "completed").execute()
        completed_count = completed_rankings.count or 0

        failed_rankings = supabase_client.table("rankings").select("id", count="exact").eq("status", "failed").execute()
        failed_count = failed_rankings.count or 0

        # Query metrics averages from analysis_metrics table
        metrics_res = supabase_client.table("analysis_metrics").select("total_analysis_time, embedding_time, faiss_time, llm_time").execute()
        
        avg_total = 0.0
        avg_embedding = 0.0
        avg_faiss = 0.0
        avg_llm = 0.0
        
        if metrics_res.data:
            data = metrics_res.data
            avg_total = sum(x.get("total_analysis_time") or 0.0 for x in data) / len(data)
            avg_embedding = sum(x.get("embedding_time") or 0.0 for x in data) / len(data)
            avg_faiss = sum(x.get("faiss_time") or 0.0 for x in data) / len(data)
            avg_llm = sum(x.get("llm_time") or 0.0 for x in data) / len(data)

        # Active analyses from platform module cache
        from app.api.v1.endpoints.platform import _active_analyses
        active_count = len(_active_analyses)

        # Worker Restarts (mocked or retrieved from process uptime checks)
        uptime = 0.0
        try:
            uptime = time.time() - psutil.boot_time()
        except Exception:
            pass

        return {
            "total_analyses": total_count,
            "active_analyses": active_count,
            "failed_analyses": failed_count,
            "completed_analyses": completed_count,
            "average_analysis_time_sec": round(avg_total, 3),
            "average_embedding_time_sec": round(avg_embedding, 3),
            "average_faiss_time_sec": round(avg_faiss, 3),
            "average_llm_time_sec": round(avg_llm, 3),
            "openrouter_requests_logged": completed_count, # Recruiter evaluation batch per ranking
            "system_uptime_sec": round(uptime, 2)
        }
    except Exception as e:
        logger.error("Failed to compile metrics report: %s", e)
        raise HTTPException(status_code=500, detail=f"Metrics error: {e}")
