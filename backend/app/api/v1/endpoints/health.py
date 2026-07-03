"""Extended Health Check & Production Metrics API Endpoints."""

import logging
import os
import psutil
import shutil
from typing import Dict
from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.services.storage_provider import create_supabase_client
from app.core.database import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize supabase client
supabase_client = create_supabase_client(settings.supabase_url, settings.supabase_service_key)


@router.get("", summary="Extended health probe")
async def health_check() -> Dict[str, any]:
    """Perform health checks on all critical subsystems."""
    status = "healthy"
    
    # 1. Database check
    database_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1;"))
            database_ok = True
    except Exception as e:
        logger.error("Health check: Database failure: %s", e)
        status = "unhealthy"

    # 2. Supabase client check
    supabase_ok = False
    try:
        res = supabase_client.table("projects").select("id").limit(1).execute()
        supabase_ok = True
    except Exception as e:
        logger.error("Health check: Supabase failure: %s", e)
        status = "unhealthy"

    # 3. Storage bucket check
    storage_ok = False
    try:
        # Check if we can query bucket names
        supabase_client.storage.list_buckets()
        storage_ok = True
    except Exception as e:
        logger.error("Health check: Storage failure: %s", e)
        status = "unhealthy"

    # 4. Libraries checks
    faiss_ok = False
    try:
        import faiss
        faiss_ok = True
    except ImportError:
        status = "unhealthy"

    torch_ok = False
    try:
        import torch
        torch_ok = True
    except ImportError:
        status = "unhealthy"

    transformers_ok = False
    try:
        import transformers
        transformers_ok = True
    except ImportError:
        status = "unhealthy"

    sentence_transformers_ok = False
    try:
        import sentence_transformers
        sentence_transformers_ok = True
    except ImportError:
        status = "unhealthy"

    # 5. OpenRouter key verification
    openrouter_ok = bool(settings.openrouter_api_key)

    # 6. Memory and Disk checks
    memory_ok = False
    try:
        process = psutil.Process(os.getpid())
        ram_mb = process.memory_info().rss / (1024 * 1024)
        memory_ok = ram_mb < 450.0
    except Exception:
        pass

    disk_ok = False
    try:
        total, used, free = shutil.disk_usage("/")
        disk_ok = free > (100 * 1024 * 1024)  # > 100MB
    except Exception:
        pass

    return {
        "status": status,
        "database": database_ok,
        "supabase": supabase_ok,
        "storage": storage_ok,
        "faiss": faiss_ok,
        "torch": torch_ok,
        "transformers": transformers_ok,
        "sentence_transformers": sentence_transformers_ok,
        "openrouter": openrouter_ok,
        "embedding_model": bool(settings.embedding_model),
        "memory_ok": memory_ok,
        "disk_ok": disk_ok
    }


@router.get("/metrics", summary="Production performance metrics")
async def metrics() -> Dict[str, any]:
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
