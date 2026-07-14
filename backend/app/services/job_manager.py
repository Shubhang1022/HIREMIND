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
# Permissive to support recovery checkpoints and stage skips
VALID_TRANSITIONS = {
    "queued": {"processing", "embedding", "indexing", "completed", "failed", "cancelled"},
    "processing": {"embedding", "indexing", "completed", "failed", "cancelled"},
    "embedding": {"indexing", "completed", "failed", "cancelled"},
    "indexing": {"completed", "failed", "cancelled"},
    "failed": {"retrying", "queued", "processing", "embedding", "indexing", "completed"},
    "retrying": {"processing", "embedding", "indexing", "completed", "failed", "cancelled"},
    "completed": set(),
    "cancelled": set()
}


def safe_execute(query_builder, max_retries: int = 3, initial_delay: float = 0.5):
    """Executes a Supabase query builder with retry backoff.
    Logs HTTP status and response body on failure.
    """
    import time
    delay = initial_delay
    for attempt in range(1, max_retries + 1):
        try:
            res = query_builder.execute()
            return res
        except Exception as exc:
            status_code = getattr(exc, "status", None) or getattr(exc, "code", "unknown")
            message = getattr(exc, "message", str(exc))
            logger.warning(
                "[DB_OPERATION_RETRY] Attempt %d/%d failed: status=%s, error=%s. Retrying in %.2fs...",
                attempt, max_retries, status_code, message, delay
            )
            if attempt == max_retries:
                logger.error(
                    "[DB_OPERATION_FATAL] All %d attempts failed. Last error: status=%s, message=%s",
                    max_retries, status_code, message
                )
                raise exc
            time.sleep(delay)
            delay *= 2

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
        """Validate status transition constraints.

        Returns True if the transition is allowed.
        Same-to-same is always allowed (idempotent, no log noise).
        Backward transitions are always rejected.
        """
        if current_status == target_status:
            return True  # idempotent — no error log
        allowed = VALID_TRANSITIONS.get(current_status, set())
        valid = target_status in allowed
        if not valid:
            logger.warning(
                "[FSM] Rejected illegal transition %s → %s for project (call ignored)",
                current_status, target_status,
            )
        return valid

    async def register_job(self, project_id: str, user_id: str, job_type: str) -> Optional[str]:
        """Register a new job in Supabase background_jobs table.

        Returns the job_id string on success, or None if the DB insert failed.
        In either case the in-memory progress cache is always populated so the
        background worker can still update progress without a DB record.
        """
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
            "failure_reason": None,
        }

        # Clear any stale cancellation token for this project
        if project_id in self._cancellation_tokens:
            self._cancellation_tokens.remove(project_id)

        job_id: Optional[str] = None
        try:
            res = supabase_client.table("background_jobs").insert(job_data).execute()
            job_id = res.data[0]["id"] if res.data else None
            if job_id is None:
                logger.warning(
                    "[JOB_MANAGER] background_jobs insert returned empty data for project %s. "
                    "Check that the 'user_id' column exists on the live table "
                    "(run supabase/migrations/002_background_jobs_user_id.sql if missing). "
                    "In-memory tracking will still work.",
                    project_id,
                )
        except Exception as exc:
            logger.error(
                "[JOB_MANAGER] Failed to insert background_jobs row for project %s: %s. "
                "Common cause: 'user_id' column missing from live table. "
                "Run supabase/migrations/002_background_jobs_user_id.sql to fix. "
                "In-memory tracking will still work for this session.",
                project_id,
                exc,
            )
            # Do NOT re-raise — the background task must still be allowed to start.
            # Progress updates will use the in-memory cache only for this session.

        # Always populate the in-memory cache regardless of DB outcome.
        # This ensures progress-stream and worker-status endpoints keep working
        # even if the DB insert failed.
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
            "eta": "00:00:00",
        }

        if job_id:
            logger.info("Registered background job %s for project %s", job_id, project_id)
        else:
            logger.warning(
                "Background job for project %s has no DB record — running in memory-only mode.",
                project_id,
            )
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
        
        transition_accepted = self.validate_transition(current_status, target_status)
        logger.info(
            "[FSM_TRANSITION_CHECK] project=%s current_db_status=%s requested_transition=%s -> %s accepted=%s",
            project_id, current_status, current_status, target_status, transition_accepted
        )
        print(
            f"[FSM_TRANSITION_CHECK] project={project_id} current_db_status={current_status} "
            f"requested_transition={current_status} -> {target_status} accepted={transition_accepted}",
            flush=True
        )

        if not transition_accepted:
            logger.error(
                "Illegal job status transition from %s to %s for project %s rejected",
                current_status, target_status, project_id
            )
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
            builder = supabase_client.table("background_jobs").update(db_updates).eq("project_id", project_id).eq("status", current_status)
            res = safe_execute(builder)
            
            # Log PATCH response and Final DB status
            db_data = res.data if hasattr(res, "data") else None
            final_status = db_data[0].get("status") if db_data else target_status
            logger.info(
                "[DB_PATCH_APPLIED] project=%s status_before=%s status_after=%s patch_response=%s",
                project_id, current_status, final_status, db_data
            )
            print(
                f"[DB_PATCH_APPLIED] project={project_id} status_before={current_status} "
                f"status_after={final_status} patch_response={db_data}",
                flush=True
            )
        except Exception as exc:
            logger.error("Supabase job update failed: %s", exc)
            raise exc

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
            builder = supabase_client.table("background_jobs").update(db_updates).eq("project_id", project_id)
            safe_execute(builder)
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
            builder = supabase_client.table("background_jobs").update(db_updates).eq("project_id", project_id)
            safe_execute(builder)
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
        """Startup job checker: restarts interrupted jobs with exponential backoff.

        Backoff schedule (seconds): attempt 1 → 60, 2 → 120, 3 → 300.
        Jobs that have exceeded 3 retries are permanently failed.
        Jobs whose failure_reason contains MODEL_LOAD_FAILED or MODEL_LOAD_TIMEOUT
        are also permanently failed — retrying would produce the same hang.

        Prints a recovery summary table to logs on completion.
        """
        BACKOFF_SECONDS = {1: 60, 2: 120, 3: 300}
        NON_RETRYABLE_REASONS = ("MODEL_LOAD_FAILED", "MODEL_LOAD_TIMEOUT", "model_load_failed",
                                  "INDEX_DIMENSION_MISMATCH")

        recovered = 0
        skipped = 0
        permanent_failures = 0
        retry_counts: dict[str, int] = {}

        try:
            res = supabase_client.table("background_jobs").select("*").in_(
                "status", ["queued", "processing", "embedding", "indexing", "retrying"]
            ).execute()
            if not res.data:
                logger.info("[RECOVERY] No interrupted background jobs found.")
                logger.info(
                    "[RECOVERY_SUMMARY] recovered=0 skipped=0 permanent_failures=0 retry_counts={}"
                )
                return

            logger.info("[RECOVERY] Found %d unfinished background jobs.", len(res.data))

            for job in res.data:
                project_id = job["project_id"]
                retry_count = job.get("retry_count", 0)
                failure_reason = job.get("failure_reason") or ""

                is_non_retryable = any(r in failure_reason for r in NON_RETRYABLE_REASONS)

                if is_non_retryable or retry_count >= 3:
                    reason = (
                        "Non-retryable failure: model load error." if is_non_retryable
                        else "Exceeded maximum recovery retries (3)."
                    )
                    logger.warning(
                        "[RECOVERY] Permanently failing job %s for project %s: %s",
                        job["id"], project_id, reason,
                    )
                    b_job_builder = supabase_client.table("background_jobs").update({
                        "status": "failed",
                        "failure_reason": reason,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", job["id"])
                    safe_execute(b_job_builder)

                    project_builder = supabase_client.table("projects").update({
                        "embedding_status": "failed",
                        "status": "failed",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", project_id)
                    safe_execute(project_builder)
                    
                    permanent_failures += 1
                    continue

                new_retry = retry_count + 1
                delay = BACKOFF_SECONDS.get(new_retry, 300)
                logger.info(
                    "[RECOVERY] Scheduling retry %d/3 for job %s project %s in %ds",
                    new_retry, job["id"], project_id, delay,
                )
                
                b_job_builder = supabase_client.table("background_jobs").update({
                    "status": "retrying",
                    "retry_count": new_retry,
                    "current_stage": f"Recovering (attempt {new_retry}/3, backoff {delay}s)",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", job["id"])
                safe_execute(b_job_builder)

                asyncio.create_task(self._safely_run_indexing_with_backoff(project_id, delay))
                recovered += 1
                retry_counts[project_id] = new_retry

        except Exception as e:
            logger.error("[RECOVERY] Error during startup background recovery: %s", e)

        # ── Recovery summary ──────────────────────────────────────────────────
        logger.info(
            "[RECOVERY_SUMMARY] recovered=%d skipped=%d permanent_failures=%d retry_counts=%s",
            recovered, skipped, permanent_failures, retry_counts or "none",
        )
        print(
            f"\n[RECOVERY_SUMMARY] "
            f"Recovered={recovered} | Skipped={skipped} | "
            f"Permanent Failures={permanent_failures} | "
            f"Retry Counts={retry_counts or 'none'}",
            flush=True,
        )

    async def _safely_run_indexing_with_backoff(self, project_id: str, delay_seconds: float):
        """Wait ``delay_seconds`` then spawn the indexing task."""
        logger.info(
            "[RECOVERY] Waiting %ds before retrying indexing for project %s",
            int(delay_seconds), project_id,
        )
        await asyncio.sleep(delay_seconds)
        await self._safely_run_indexing(project_id)

    async def _safely_run_indexing(self, project_id: str):
        """Spawns process_project_data_task under a safe wrapper."""
        from app.api.v1.endpoints.platform import process_project_data_task
        try:
            # We run it synchronously as an async wrapper task
            await asyncio.to_thread(process_project_data_task, project_id)
        except Exception as e:
            logger.error("Recovered indexing failed for project %s: %s", project_id, e)
