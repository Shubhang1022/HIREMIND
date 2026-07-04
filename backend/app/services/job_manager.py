"""Background job manager service — handles persistence, retries, state validation, and cancellations."""

import asyncio
import logging
import time
import gc
import os
import psutil
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.config import settings
from app.services.storage_provider import create_supabase_client

logger = logging.getLogger(__name__)

# Initialize client
supabase_client = create_supabase_client(settings.supabase_url, settings.supabase_service_key)

# Finite State Machine Allowed Transitions
VALID_TRANSITIONS = {
    "queued": {"processing", "failed", "cancelled"},
    "processing": {"embedding", "failed", "cancelled"},
    "embedding": {"indexing", "failed", "cancelled"},
    "indexing": {"completed", "failed", "cancelled"},
    "failed": {"retrying", "queued"},
    "retrying": {"processing", "failed", "cancelled"},
    "completed": set(),
    "cancelled": set()
}

class JobManager:
    _instance: Optional["JobManager"] = None
    
    # Memory progress cache: project_id -> progress details dict
    _progress_cache: Dict[str, Dict[str, Any]] = {}
    
    # Active cancellation request tokens: project_id
    _cancellation_tokens: set[str] = set()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(JobManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    @classmethod
    def get_instance(cls) -> "JobManager":
        if cls._instance is None:
            cls._instance = JobManager()
        return cls._instance

    def validate_transition(self, current_status: str, target_status: str) -> bool:
        """Validate status transition constraints."""
        if current_status == target_status:
            return True
        allowed = VALID_TRANSITIONS.get(current_status, set())
        return target_status in allowed

    async def register_job(self, project_id: str, user_id: str, job_type: str) -> str:
        """Register a new job in Supabase background_jobs table."""
        now_str = datetime.now(timezone.utc).isoformat()
        job_data = {
            "project_id": project_id,
            "user_id": user_id,
            "job_type": job_type,
            "current_stage": "Enqueued",
            "progress_percentage": 0,
            "started_at": now_str,
            "updated_at": now_str,
            "last_heartbeat": now_str,
            "retry_count": 0,
            "status": "queued",
            "failure_reason": None
        }
        
        # Clear cancellation token just in case
        if project_id in self._cancellation_tokens:
            self._cancellation_tokens.remove(project_id)
            
        res = supabase_client.table("background_jobs").insert(job_data).execute()
        job_id = res.data[0]["id"] if res.data else None
        
        # Cache locally for real-time APIs
        self._progress_cache[project_id] = {
            "job_id": job_id,
            "project_id": project_id,
            "user_id": user_id,
            "job_type": job_type,
            "current_stage": "Enqueued",
            "progress_percentage": 0,
            "started_at": time.time(),
            "updated_at": time.time(),
            "last_heartbeat": time.time(),
            "retry_count": 0,
            "status": "queued",
            "processed_candidates": 0,
            "total_candidates": 0,
            "ram_usage": 0.0,
            "peak_ram": 0.0,
            "eta": "00:00:00"
        }
        
        logger.info("Registered background job %s for project %s", job_id, project_id)
        return job_id

    async def update_job_progress(
        self, 
        project_id: str, 
        stage: str, 
        progress: int, 
        status: Optional[str] = None,
        processed_candidates: int = 0,
        total_candidates: int = 0,
        eta: str = "",
        retry_count: Optional[int] = None
    ):
        """Update job metrics in-memory and persist to Supabase background_jobs table."""
        cache = self._progress_cache.get(project_id)
        if not cache:
            # Try to fetch job from database to reconstruct cache
            res = supabase_client.table("background_jobs").select("*").eq("project_id", project_id).order("started_at", desc=True).limit(1).execute()
            if res.data:
                db_job = res.data[0]
                self._progress_cache[project_id] = {
                    "job_id": db_job["id"],
                    "project_id": project_id,
                    "user_id": db_job.get("user_id"),
                    "job_type": db_job.get("job_type", "indexing"),
                    "current_stage": db_job.get("current_stage"),
                    "progress_percentage": db_job.get("progress_percentage", 0),
                    "started_at": time.time(),
                    "updated_at": time.time(),
                    "last_heartbeat": time.time(),
                    "retry_count": db_job.get("retry_count", 0),
                    "status": db_job.get("status"),
                    "processed_candidates": processed_candidates,
                    "total_candidates": total_candidates,
                    "ram_usage": 0.0,
                    "peak_ram": 0.0,
                    "eta": eta
                }
                cache = self._progress_cache[project_id]
            else:
                return

        # Handle State transitions
        current_status = cache["status"]
        target_status = status or current_status
        
        if not self.validate_transition(current_status, target_status):
            logger.error("Illegal job status transition from %s to %s for project %s rejected", current_status, target_status, project_id)
            return

        # RAM calculations
        ram = 0.0
        try:
            process = psutil.Process(os.getpid())
            ram = process.memory_info().rss / (1024 * 1024)
        except Exception:
            pass
            
        cache["peak_ram"] = max(cache.get("peak_ram", 0.0), ram)
        cache["ram_usage"] = ram
        cache["current_stage"] = stage
        cache["progress_percentage"] = progress
        cache["status"] = target_status
        cache["processed_candidates"] = processed_candidates
        cache["total_candidates"] = total_candidates
        cache["eta"] = eta
        cache["updated_at"] = time.time()
        cache["last_heartbeat"] = time.time()
        if retry_count is not None:
            cache["retry_count"] = retry_count

        # Persist to database
        db_updates = {
            "current_stage": stage,
            "progress_percentage": progress,
            "status": target_status,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if retry_count is not None:
            db_updates["retry_count"] = retry_count

        try:
            supabase_client.table("background_jobs").update(db_updates).eq("project_id", project_id).eq("status", current_status).execute()
        except Exception as exc:
            logger.error("Supabase job update failed: %s", exc)

    async def fail_job(self, project_id: str, reason: str):
        """Mark job as failed in memory and DB."""
        cache = self._progress_cache.get(project_id)
        current_status = cache["status"] if cache else "queued"
        
        if not self.validate_transition(current_status, "failed"):
            logger.error("Failed transition rejected from status %s for project %s", current_status, project_id)
            return

        if cache:
            cache["status"] = "failed"
            cache["failure_reason"] = reason
            cache["updated_at"] = time.time()

        db_updates = {
            "status": "failed",
            "failure_reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        try:
            supabase_client.table("background_jobs").update(db_updates).eq("project_id", project_id).execute()
        except Exception as exc:
            logger.error("Supabase job fail persistence failed: %s", exc)

    async def cancel_job(self, project_id: str):
        """Mark job as cancelled in memory and DB."""
        cache = self._progress_cache.get(project_id)
        current_status = cache["status"] if cache else "queued"
        
        if not self.validate_transition(current_status, "cancelled"):
            logger.error("Cancelled transition rejected from status %s for project %s", current_status, project_id)
            return

        if cache:
            cache["status"] = "cancelled"
            cache["current_stage"] = "Cancelled"
            cache["updated_at"] = time.time()

        # Mark cancellation requested in tokens
        self.request_cancellation(project_id)

        db_updates = {
            "status": "cancelled",
            "current_stage": "Cancelled",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        try:
            supabase_client.table("background_jobs").update(db_updates).eq("project_id", project_id).execute()
        except Exception as exc:
            logger.error("Supabase job cancel persistence failed: %s", exc)

    def request_cancellation(self, project_id: str):
        """Set token requesting candidate indexing worker task cancel."""
        self._cancellation_tokens.add(project_id)

    def is_cancelled(self, project_id: str) -> bool:
        """Check if indexing worker task was cancelled midway."""
        return project_id in self._cancellation_tokens

    def clear_cancellation(self, project_id: str):
        """Remove cancellation token."""
        if project_id in self._cancellation_tokens:
            self._cancellation_tokens.remove(project_id)

    def cancel_all_active_jobs(self):
        """Mark all currently cached running or queued jobs as cancelled."""
        active_ids = [pid for pid, info in self._progress_cache.items() if info.get("status") in ["queued", "processing", "embedding", "indexing"]]
        for pid in active_ids:
            self._cancellation_tokens.add(pid)
            info = self._progress_cache.get(pid)
            if info:
                info["status"] = "cancelled"
                info["current_stage"] = "Cancelled"
            try:
                db_updates = {
                    "status": "cancelled",
                    "current_stage": "Cancelled",
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                supabase_client.table("background_jobs").update(db_updates).eq("project_id", pid).execute()
            except Exception as exc:
                logger.error("Supabase job cancel persistence failed: %s", exc)

    def get_job_status(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve current in-memory job details."""
        return self._progress_cache.get(project_id)

    async def recover_interrupted_jobs(self):
        """Startup job checker: restarts interrupted queued/processing tasks or transitions to failed."""
        try:
            res = supabase_client.table("background_jobs").select("*").in_("status", ["queued", "processing", "embedding", "indexing", "retrying"]).execute()
            if not res.data:
                logger.info("[RECOVERY] No interrupted background jobs found.")
                return

            logger.info("[RECOVERY] Found %d unfinished background jobs.", len(res.data))

            from app.api.v1.endpoints.platform import process_project_data_task
            
            for job in res.data:
                project_id = job["project_id"]
                retry_count = job.get("retry_count", 0)
                
                if retry_count < 3:
                    new_retry = retry_count + 1
                    logger.info("[RECOVERY] Retrying job %s for project %s (Attempt %d/3)", job["id"], project_id, new_retry)
                    
                    # Update status in db
                    supabase_client.table("background_jobs").update({
                        "status": "retrying",
                        "retry_count": new_retry,
                        "current_stage": "Recovering",
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", job["id"]).execute()

                    # Re-trigger task inside asyncio event loop
                    asyncio.create_task(self._safely_run_indexing(project_id))
                else:
                    logger.warning("[RECOVERY] Job %s has exceeded max recovery retries. Failing it.", job["id"])
                    supabase_client.table("background_jobs").update({
                        "status": "failed",
                        "failure_reason": "Interrupted by container restart and exceeded max retries.",
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", job["id"]).execute()
                    
                    # Update project status
                    supabase_client.table("projects").update({
                        "embedding_status": "failed",
                        "status": "failed",
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", project_id).execute()

        except Exception as e:
            logger.error("[RECOVERY] Error during startup background recovery: %s", e)

    async def _safely_run_indexing(self, project_id: str):
        """Spawns process_project_data_task under a safe wrapper."""
        from app.api.v1.endpoints.platform import process_project_data_task
        try:
            # We run it synchronously as an async wrapper task
            await asyncio.to_thread(process_project_data_task, project_id)
        except Exception as e:
            logger.error("Recovered indexing failed for project %s: %s", project_id, e)
