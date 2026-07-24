"""Background job manager service — handles persistence, retries, state validation, and cancellations."""

import asyncio
import logging
import time
import gc
import os
import psutil
import threading
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from app.core.config import settings
from app.services.storage_provider import create_supabase_client

logger = logging.getLogger(__name__)

# Initialize client
supabase_client = create_supabase_client(settings.supabase_url, settings.supabase_service_key)

_lock = threading.Lock()

# Strict FSM Transitions
VALID_TRANSITIONS = {
    "queued": {"processing", "failed", "cancelled"},
    "processing": {"embedding", "failed", "cancelled"},
    "embedding": {"indexing", "failed", "cancelled"},
    "indexing": {"completed", "failed", "cancelled"},
    "failed": {"retrying", "queued", "processing"},
    "retrying": {"processing", "failed", "cancelled"},
    "completed": set(),
    "cancelled": {"queued", "processing"}
}


class LockLostError(RuntimeError):
    """Raised when a worker attempts to update a job but has lost the lock."""


from enum import Enum

class LockResult(Enum):
    ACQUIRED = "ACQUIRED"
    ALREADY_HELD = "ALREADY_HELD"
    ERROR = "ERROR"


def get_clean_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if "postgresql+asyncpg://" in url:
        url = url.replace("postgresql+asyncpg://", "postgresql://")
    return url


class DBConnection:
    def __init__(self):
        self.conn = None

    async def __aenter__(self):
        url = get_clean_db_url()
        import asyncpg
        self.conn = await asyncpg.connect(url, ssl="require")
        return self.conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            await self.conn.close()


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

    # Process-wide worker ID
    worker_id: str = f"worker_{uuid.uuid4().hex[:8]}"

    # Schema initialized flag
    _schema_initialized: bool = False

    async def ensure_db_schema(self) -> bool:
        """Verify all required columns and indexes exist in public.background_jobs."""
        if JobManager._schema_initialized:
            return True
            
        try:
            async with DBConnection() as conn:
                # 1. Check table existence
                table_exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = 'public' AND table_name = 'background_jobs'
                    )
                    """
                )
                if not table_exists:
                    logger.error("[DB_SCHEMA_ERROR] Table public.background_jobs does not exist.")
                    return False
                
                # 2. Check columns
                columns = await conn.fetch(
                    """
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_schema = 'public' AND table_name = 'background_jobs'
                    """
                )
                existing_cols = {c["column_name"] for c in columns}
                required_cols = {
                    "id", "project_id", "user_id", "job_type", "current_stage", 
                    "progress_percentage", "status", "failure_reason", "started_at", 
                    "updated_at", "last_heartbeat", "retry_count", "owner_id", 
                    "lease_expires_at", "processed_candidates", "total_candidates", 
                    "ram_usage", "peak_ram", "eta", "speed"
                }
                
                missing_cols = required_cols - existing_cols
                if missing_cols:
                    logger.error("[DB_SCHEMA_ERROR] Missing columns in public.background_jobs: %s", missing_cols)
                    return False
                    
                # 3. Check partial unique index
                index_exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM pg_indexes 
                        WHERE schemaname = 'public' 
                          AND tablename = 'background_jobs' 
                          AND indexname = 'idx_background_jobs_active_project'
                    )
                    """
                )
                if not index_exists:
                    logger.error("[DB_SCHEMA_ERROR] Index idx_background_jobs_active_project does not exist.")
                    return False
                    
                logger.info("[JOB_SCHEMA_CHECK] jobs_table=true lock_fields=true lease_fields=true required_indexes=true success=true")
                JobManager._schema_initialized = True
                return True
        except Exception as e:
            logger.error("[DB_SCHEMA_ERROR] Failed to verify database schema: %s", e)
            return False

    async def clear_locks_on_boot(self):
        """Clear owner_id and lease_expires_at for all active background jobs at startup.
        This allows new workers to instantly reclaim the locks on restart without waiting 60s.
        """
        await self.ensure_db_schema()
        try:
            async with DBConnection() as conn:
                logger.info("[STARTUP] Clearing background job lock owners and leases...")
                await conn.execute(
                    """
                    UPDATE public.background_jobs
                    SET owner_id = NULL,
                        lease_expires_at = NULL
                    WHERE status NOT IN ('completed', 'failed', 'cancelled');
                    """
                )
                logger.info("[STARTUP] Lock owners and leases cleared successfully.")
        except Exception as e:
            logger.error("[STARTUP_ERROR] Failed to clear lock owners: %s", e)

    async def acquire_lock(self, project_id: str, user_id: Optional[str], current_stage: str) -> tuple[LockResult, Optional[str], Optional[str]]:
        """
        Tries to acquire or renew a distributed DB-level lock for the project.
        Returns (LockResult, job_id, owner_id).
        """
        worker_id = self.worker_id
        now = datetime.now(timezone.utc)
        lease_duration = timedelta(seconds=60)
        expires_at = now + lease_duration

        logger.info("[LOCK_ACQUIRE_START] project=%s worker=%s", project_id, worker_id)

        try:
            async with DBConnection() as conn:
                # Check for active job
                active_job = await conn.fetchrow(
                    """
                    SELECT id, owner_id, lease_expires_at, status 
                    FROM public.background_jobs
                    WHERE project_id = $1 AND status NOT IN ('completed', 'failed', 'cancelled')
                    """,
                    uuid.UUID(project_id)
                )

                if active_job:
                    job_id = str(active_job["id"])
                    owner_id = active_job["owner_id"]
                    lease_expires_at = active_job["lease_expires_at"]

                    if owner_id == worker_id or owner_id is None or lease_expires_at < now:
                        # We can claim/reclaim ownership!
                        await conn.execute(
                            """
                            UPDATE public.background_jobs
                            SET owner_id = $1,
                                lease_expires_at = $2,
                                last_heartbeat = $3,
                                updated_at = $3,
                                status = 'processing',
                                current_stage = $4,
                                failure_reason = NULL
                            WHERE id = $5
                            """,
                            worker_id, expires_at, now, current_stage, uuid.UUID(job_id)
                        )
                        self._progress_cache[project_id] = {
                            "job_id": job_id,
                            "project_id": project_id,
                            "user_id": user_id,
                            "current_stage": current_stage,
                            "progress_percentage": 0,
                            "started_at": now.isoformat(),
                            "updated_at": time.time(),
                            "last_heartbeat": time.time(),
                            "status": "processing",
                            "processed_candidates": 0,
                            "total_candidates": 0,
                            "eta": "00:00:00",
                            "speed": 0.0,
                        }
                        logger.info("[LOCK_ACQUIRED] Lock claimed/recovered for project %s. Job ID: %s", project_id, job_id)
                        return LockResult.ACQUIRED, job_id, worker_id
                    else:
                        logger.warning("[LOCK_ALREADY_HELD] project=%s owner=%s", project_id, owner_id)
                        return LockResult.ALREADY_HELD, job_id, owner_id
                else:
                    # Create a new active job
                    job_id = str(uuid.uuid4())
                    try:
                        await conn.execute(
                            """
                            INSERT INTO public.background_jobs (
                                id, project_id, user_id, job_type, current_stage, progress_percentage, status, owner_id, lease_expires_at, started_at, updated_at, last_heartbeat
                            ) VALUES ($1, $2, $3, 'indexing', $4, 0, 'processing', $5, $6, $7, $7, $7)
                            """,
                            uuid.UUID(job_id), uuid.UUID(project_id), uuid.UUID(user_id) if user_id else None,
                            current_stage, worker_id, expires_at, now
                        )
                        self._progress_cache[project_id] = {
                            "job_id": job_id,
                            "project_id": project_id,
                            "user_id": user_id,
                            "current_stage": current_stage,
                            "progress_percentage": 0,
                            "started_at": now.isoformat(),
                            "updated_at": time.time(),
                            "last_heartbeat": time.time(),
                            "status": "processing",
                            "processed_candidates": 0,
                            "total_candidates": 0,
                            "eta": "00:00:00",
                            "speed": 0.0,
                        }
                        logger.info("[LOCK_ACQUIRED] Created new background job %s and locked for project %s", job_id, project_id)
                        return LockResult.ACQUIRED, job_id, worker_id
                    except Exception as ins_err:
                        logger.warning("[LOCK_REJECTED] Concurrent insert failed for project %s: %s", project_id, ins_err)
                        res = await conn.fetchrow(
                            "SELECT id, owner_id FROM public.background_jobs WHERE project_id = $1 AND status NOT IN ('completed', 'failed', 'cancelled')",
                            uuid.UUID(project_id)
                        )
                        if res:
                            return LockResult.ALREADY_HELD, str(res["id"]), res["owner_id"]
                        return LockResult.ERROR, None, str(ins_err)
        except Exception as e:
            logger.error("[LOCK_ERROR] Error acquiring lock for project %s: %s", project_id, e)
            return LockResult.ERROR, None, str(e)

    async def cleanup_stale_jobs(self, timeout_minutes: int = 15):
        """
        Scan database for active jobs that haven't updated their heartbeat for timeout_minutes.
        Transition them to failed status.
        """
        await self.ensure_db_schema()
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(minutes=timeout_minutes)
        try:
            async with DBConnection() as conn:
                stale_jobs = await conn.fetch(
                    """
                    SELECT id, project_id, current_stage 
                    FROM public.background_jobs
                    WHERE status NOT IN ('completed', 'failed', 'cancelled')
                      AND last_heartbeat < $1
                    """,
                    threshold
                )
                for job in stale_jobs:
                    job_id = job["id"]
                    project_id = str(job["project_id"])
                    logger.warning(
                        "[CLEANUP_STALE_JOB] Failing stale job %s for project %s (no heartbeat since %s)",
                        job_id, project_id, threshold
                    )
                    reason = f"Stale job cleanup: no heartbeat received for over {timeout_minutes} minutes (stage: {job['current_stage']})."
                    await conn.execute(
                        """
                        UPDATE public.background_jobs
                        SET status = 'failed',
                            failure_reason = $1,
                            updated_at = $2
                        WHERE id = $3
                        """,
                        reason, now, job_id
                    )
                    await conn.execute(
                        """
                        UPDATE public.projects
                        SET embedding_status = 'failed',
                            status = 'failed',
                            updated_at = $1
                        WHERE id = $2
                        """,
                        now, uuid.UUID(project_id)
                    )
        except Exception as e:
            logger.error("[CLEANUP_ERROR] Failed during stale job cleanup: %s", e)

    async def is_job_active(self, project_id: str) -> bool:
        """Check if there is an active job in the DB that has a valid lease/heartbeat."""
        now = datetime.now(timezone.utc)
        try:
            async with DBConnection() as conn:
                res = await conn.fetchrow(
                    """
                    SELECT id, lease_expires_at, owner_id 
                    FROM public.background_jobs
                    WHERE project_id = $1 
                      AND status NOT IN ('completed', 'failed', 'cancelled')
                      AND lease_expires_at >= $2
                    """,
                    uuid.UUID(project_id), now
                )
                return res is not None
        except Exception:
            return False

    async def start_job(self, project_id: str, user_id: Optional[str] = None, current_stage: str = "Enqueued") -> tuple[LockResult, Optional[str], Optional[str]]:
        """Acquire lock on job and transition state to 'processing'."""
        db_ok = await self.ensure_db_schema()
        if not db_ok:
            return LockResult.ERROR, None, "Database schema check failed"
        return await self.acquire_lock(project_id, user_id, current_stage)

    def finish_job(self, project_id: str):
        """Clean up in-memory progress details."""
        if project_id in self._progress_cache:
            del self._progress_cache[project_id]

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
        valid = target_status in allowed
        if not valid:
            import traceback
            tb_str = "".join(traceback.format_stack())
            frames = traceback.extract_stack()
            caller_info = "Unknown"
            for frame in reversed(frames):
                if "job_manager.py" not in frame.filename:
                    caller_info = f"{frame.filename}:{frame.lineno} in {frame.name}"
                    break
            if caller_info == "Unknown" and len(frames) >= 2:
                caller_frame = frames[-2]
                caller_info = f"{caller_frame.filename}:{caller_frame.lineno} in {caller_frame.name}"

            logger.error(
                "[FSM_TRANSITION_ERROR] Rejected illegal transition %s → %s.\n"
                "Caller: %s\nStack trace:\n%s",
                current_status, target_status, caller_info, tb_str
            )
        return valid

    async def register_job(self, project_id: str, user_id: str, job_type: str) -> str:
        """Register a new job in Supabase background_jobs table with lock protection."""
        logger.info("[INDEX_JOB_REGISTER_START] project=%s", project_id)
        db_ok = await self.ensure_db_schema()
        if not db_ok:
            raise RuntimeError("Database schema check failed")
        
        now = datetime.now(timezone.utc)
        lease_duration = timedelta(seconds=60)
        expires_at = now + lease_duration
        worker_id = self.worker_id
        
        async with DBConnection() as conn:
            active_job = await conn.fetchrow(
                """
                SELECT id, owner_id, lease_expires_at, status 
                FROM public.background_jobs
                WHERE project_id = $1 AND status NOT IN ('completed', 'failed', 'cancelled')
                """,
                uuid.UUID(project_id)
            )
            
            if active_job:
                owner_id = active_job["owner_id"]
                lease_expires_at = active_job["lease_expires_at"]
                
                if owner_id == worker_id or owner_id is None or lease_expires_at < now:
                    job_id = str(active_job["id"])
                    await conn.execute(
                        """
                        UPDATE public.background_jobs
                        SET owner_id = $1,
                            lease_expires_at = $2,
                            last_heartbeat = $3,
                            updated_at = $3,
                            status = 'queued',
                            current_stage = 'Enqueued',
                            failure_reason = NULL,
                            retry_count = 0
                        WHERE id = $4
                        """,
                        worker_id, expires_at, now, uuid.UUID(job_id)
                    )
                    logger.info("[INDEX_JOB_REGISTERED] Reclaimed existing job %s for project %s as queued", job_id, project_id)
                else:
                    job_id = str(active_job["id"])
                    logger.warning("[INDEX_JOB_REGISTERED] Reused active job %s for project %s owned by %s", job_id, project_id, owner_id)
            else:
                job_id = str(uuid.uuid4())
                try:
                    await conn.execute(
                        """
                        INSERT INTO public.background_jobs (
                            id, project_id, user_id, job_type, current_stage, progress_percentage, status, owner_id, lease_expires_at, started_at, updated_at, last_heartbeat, retry_count
                        ) VALUES ($1, $2, $3, $4, 'Enqueued', 0, 'queued', $5, $6, $7, $7, $7, 0)
                        """,
                        uuid.UUID(job_id), uuid.UUID(project_id), uuid.UUID(user_id) if user_id else None,
                        job_type, worker_id, expires_at, now
                    )
                    logger.info("[INDEX_JOB_REGISTERED] Registered new job %s for project %s", job_id, project_id)
                except Exception as e:
                    logger.warning("[INDEX_JOB_REGISTERED] Concurrent insert failed for project %s, fetching existing: %s", project_id, e)
                    res = await conn.fetchrow(
                        "SELECT id FROM public.background_jobs WHERE project_id = $1 AND status NOT IN ('completed', 'failed', 'cancelled')",
                        uuid.UUID(project_id)
                    )
                    if res:
                        job_id = str(res["id"])
                    else:
                        raise e

        # Synchronize progress cache
        self._progress_cache[project_id] = {
            "job_id": job_id,
            "project_id": project_id,
            "user_id": user_id,
            "job_type": job_type,
            "current_stage": "Enqueued",
            "progress_percentage": 0,
            "started_at": now.isoformat(),
            "updated_at": time.time(),
            "last_heartbeat": time.time(),
            "retry_count": 0,
            "status": "queued",
            "processed_candidates": 0,
            "total_candidates": 0,
            "eta": "00:00:00",
            "speed": 0.0,
        }
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
        retry_count: Optional[int] = None,
        speed: Optional[float] = None
    ):
        """Update job metrics in-memory and persist to Supabase background_jobs table."""
        worker_id = self.worker_id
        cache = self._progress_cache.get(project_id)
        if not cache:
            # Reconstruct cache from DB
            try:
                async with DBConnection() as conn:
                    db_job = await conn.fetchrow(
                        "SELECT id, user_id, job_type, current_stage, progress_percentage, started_at, retry_count, status "
                        "FROM public.background_jobs WHERE project_id = $1 ORDER BY started_at DESC LIMIT 1",
                        uuid.UUID(project_id)
                    )
                    if db_job:
                        self._progress_cache[project_id] = {
                            "job_id": str(db_job["id"]),
                            "project_id": project_id,
                            "user_id": str(db_job["user_id"]) if db_job["user_id"] else None,
                            "job_type": db_job["job_type"],
                            "current_stage": db_job["current_stage"],
                            "progress_percentage": db_job["progress_percentage"],
                            "started_at": db_job["started_at"].isoformat() if db_job["started_at"] else datetime.now(timezone.utc).isoformat(),
                            "updated_at": time.time(),
                            "last_heartbeat": time.time(),
                            "retry_count": db_job["retry_count"],
                            "status": db_job["status"],
                            "processed_candidates": processed_candidates,
                            "total_candidates": total_candidates,
                            "ram_usage": 0.0,
                            "peak_ram": 0.0,
                            "eta": eta,
                            "speed": speed or 0.0
                        }
                        cache = self._progress_cache[project_id]
                    else:
                        return
            except Exception as e:
                logger.error("[PROGRESS_CACHE_RECONSTRUCT_FAILED] project=%s error=%s", project_id, e)
                return

        # Handle State transitions
        current_status = cache["status"]
        target_status = status or current_status
        
        transition_accepted = self.validate_transition(current_status, target_status)
        if not transition_accepted:
            logger.error(
                "Illegal job status transition from %s to %s for project %s rejected.",
                current_status, target_status, project_id
            )
            await self.fail_job(project_id, f"FSM_TRANSITION_ERROR: Illegal transition {current_status} -> {target_status}")
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
        if speed is not None:
            cache["speed"] = speed
        cache["updated_at"] = time.time()
        cache["last_heartbeat"] = time.time()
        if retry_count is not None:
            cache["retry_count"] = retry_count

        now = datetime.now(timezone.utc)
        is_terminal = target_status in ('completed', 'failed', 'cancelled')
        expires_at = None if is_terminal else now + timedelta(seconds=60)
        db_owner = None if is_terminal else worker_id

        # Persist to database verifying lock ownership
        try:
            async with DBConnection() as conn:
                res = await conn.execute(
                    """
                    UPDATE public.background_jobs
                    SET current_stage = $1,
                        progress_percentage = $2,
                        status = $3,
                        last_heartbeat = $4,
                        lease_expires_at = $5,
                        updated_at = $4,
                        retry_count = COALESCE($6, retry_count),
                        processed_candidates = $7,
                        total_candidates = $8,
                        ram_usage = $9,
                        peak_ram = $10,
                        eta = $11,
                        speed = $12,
                        owner_id = $13
                    WHERE id = $14 AND owner_id = $15
                    """,
                    stage, progress, target_status, now, expires_at, retry_count,
                    processed_candidates, total_candidates, ram, cache["peak_ram"],
                    eta, speed or 0.0, db_owner, uuid.UUID(cache["job_id"]), worker_id
                )
                
                if res == 'UPDATE 0':
                    current_lock = await conn.fetchrow(
                        "SELECT owner_id, lease_expires_at, status FROM public.background_jobs WHERE id = $1",
                        uuid.UUID(cache["job_id"])
                    )
                    owner = current_lock["owner_id"] if current_lock else "unknown"
                    expires = current_lock["lease_expires_at"] if current_lock else "unknown"
                    logger.error(
                        "[LOCK_LOST] Worker %s lost lock for project %s (current owner: %s, expires: %s)",
                        worker_id, project_id, owner, expires
                    )
                    raise LockLostError(f"Worker {worker_id} lost lock for project {project_id} to worker {owner}")

        except LockLostError:
            raise
        except Exception as exc:
            logger.error("Supabase job update failed: %s", exc)
            raise exc

    async def fail_job(self, project_id: str, reason: str):
        """Mark job as failed in memory and DB."""
        cache = self._progress_cache.get(project_id)
        if cache:
            now = datetime.now(timezone.utc)
            try:
                async with DBConnection() as conn:
                    # Update background jobs
                    await conn.execute(
                        """
                        UPDATE public.background_jobs
                        SET status = 'failed',
                            failure_reason = $1,
                            updated_at = $2,
                            lease_expires_at = NULL
                        WHERE id = $3 AND owner_id = $4
                        """,
                        reason, now, uuid.UUID(cache["job_id"]), self.worker_id
                    )
                    # Update project status
                    await conn.execute(
                        """
                        UPDATE public.projects
                        SET embedding_status = 'failed',
                            status = 'failed',
                            updated_at = $1
                        WHERE id = $2
                        """,
                        now, uuid.UUID(project_id)
                    )
            except Exception as exc:
                logger.error("Supabase job fail persistence failed: %s", exc)
            
            cache["status"] = "failed"
            cache["failure_reason"] = reason
            cache["updated_at"] = time.time()
            
        self.finish_job(project_id)

    async def cancel_job(self, project_id: str):
        """Mark job as cancelled in memory and DB."""
        cache = self._progress_cache.get(project_id)
        if cache:
            now = datetime.now(timezone.utc)
            try:
                async with DBConnection() as conn:
                    # Update background jobs
                    await conn.execute(
                        """
                        UPDATE public.background_jobs
                        SET status = 'cancelled',
                            current_stage = 'Cancelled',
                            updated_at = $1,
                            lease_expires_at = NULL
                        WHERE id = $2 AND owner_id = $3
                        """,
                        now, uuid.UUID(cache["job_id"]), self.worker_id
                    )
                    # Update project status
                    await conn.execute(
                        """
                        UPDATE public.projects
                        SET embedding_status = 'failed',
                            status = 'failed',
                            updated_at = $1
                        WHERE id = $2
                        """,
                        now, uuid.UUID(project_id)
                    )
            except Exception as exc:
                logger.error("Supabase job cancel persistence failed: %s", exc)
            
            cache["status"] = "cancelled"
            cache["current_stage"] = "Cancelled"
            cache["updated_at"] = time.time()

        self.request_cancellation(project_id)
        self.finish_job(project_id)

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

    def get_job_status(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve current in-memory job details."""
        return self._progress_cache.get(project_id)

    async def cancel_all_active_jobs(self):
        """Mark all active jobs in the database as cancelled."""
        now = datetime.now(timezone.utc)
        try:
            async with DBConnection() as conn:
                active_jobs = await conn.fetch(
                    "SELECT project_id FROM public.background_jobs WHERE status IN ('queued', 'processing', 'embedding', 'indexing', 'retrying')"
                )
                for job in active_jobs:
                    pid = str(job["project_id"])
                    self._cancellation_tokens.add(pid)
                    cache = self._progress_cache.get(pid)
                    if cache:
                        cache["status"] = "cancelled"
                        cache["current_stage"] = "Cancelled"
                
                await conn.execute(
                    """
                    UPDATE public.background_jobs
                    SET status = 'cancelled',
                        current_stage = 'Cancelled',
                        updated_at = $1,
                        lease_expires_at = NULL
                    WHERE status IN ('queued', 'processing', 'embedding', 'indexing', 'retrying')
                    """,
                    now
                )
        except Exception as exc:
            logger.error("Database job cancel all failed: %s", exc)

    _recovery_semaphore: Optional[asyncio.Semaphore] = None

    def get_recovery_semaphore(self) -> asyncio.Semaphore:
        if self._recovery_semaphore is None:
            self._recovery_semaphore = asyncio.Semaphore(1)
        return self._recovery_semaphore

    async def recover_interrupted_jobs(self):
        """Startup job checker: restarts interrupted jobs with exponential backoff.

        Backoff schedule (seconds): attempt 1 → 60, 2 → 120, 3 → 300.
        Jobs that have exceeded 3 retries are permanently failed.
        Jobs whose failure_reason contains MODEL_LOAD_FAILED or MODEL_LOAD_TIMEOUT
        are also permanently failed — retrying would produce the same hang.

        Prints a recovery summary table to logs on completion.
        """
        await self.ensure_db_schema()
        BACKOFF_SECONDS = {1: 60, 2: 120, 3: 300}
        NON_RETRYABLE_REASONS = ("MODEL_LOAD_FAILED", "MODEL_LOAD_TIMEOUT", "model_load_failed",
                                  "INDEX_DIMENSION_MISMATCH")

        recovered = 0
        skipped = 0
        permanent_failures = 0
        retry_counts: dict[str, int] = {}

        try:
            now = datetime.now(timezone.utc)
            async with DBConnection() as conn:
                # Find all stale background jobs that are in active status
                jobs = await conn.fetch(
                    """
                    SELECT id, project_id, user_id, retry_count, failure_reason, status 
                    FROM public.background_jobs
                    WHERE status IN ('queued', 'processing', 'embedding', 'indexing', 'retrying')
                      AND (owner_id IS NULL OR lease_expires_at < $1)
                    """,
                    now
                )
                
                if not jobs:
                    logger.info("[RECOVERY] No interrupted background jobs found.")
                    logger.info(
                        "[RECOVERY_SUMMARY] recovered=0 skipped=0 permanent_failures=0 retry_counts={}"
                    )
                    return

                logger.info("[RECOVERY] Found %d unfinished background jobs.", len(jobs))

                for job in jobs:
                    job_id = str(job["id"])
                    project_id = str(job["project_id"])
                    user_id = str(job["user_id"]) if job["user_id"] else None
                    retry_count = job.get("retry_count", 0)
                    failure_reason = job.get("failure_reason") or ""

                    # Verify that candidate upload exists
                    upload = await conn.fetchrow(
                        "SELECT id FROM public.candidate_uploads WHERE project_id = $1 AND status = 'COMPLETED' LIMIT 1",
                        uuid.UUID(project_id)
                    )
                    if not upload:
                        reason = "Recovery aborted: candidate upload not found."
                        logger.warning(
                            "[RECOVERY] Permanently failing job %s for project %s: %s",
                            job_id, project_id, reason,
                        )
                        await conn.execute(
                            """
                            UPDATE public.background_jobs 
                            SET status = 'failed', 
                                failure_reason = $1, 
                                updated_at = NOW(),
                                lease_expires_at = NULL,
                                owner_id = NULL
                            WHERE id = $2
                            """,
                            reason, job["id"]
                        )
                        await conn.execute(
                            """
                            UPDATE public.projects 
                            SET embedding_status = 'failed', 
                                status = 'failed', 
                                updated_at = NOW() 
                            WHERE id = $1
                            """,
                            job["project_id"]
                        )
                        permanent_failures += 1
                        continue

                    is_non_retryable = any(r in failure_reason for r in NON_RETRYABLE_REASONS)

                    if is_non_retryable or retry_count >= 3:
                        reason = (
                            "Non-retryable failure: model load error." if is_non_retryable
                            else "Exceeded maximum recovery retries (3)."
                        )
                        logger.warning(
                            "[RECOVERY] Permanently failing job %s for project %s: %s",
                            job_id, project_id, reason,
                        )
                        await conn.execute(
                            """
                            UPDATE public.background_jobs 
                            SET status = 'failed', 
                                failure_reason = $1, 
                                updated_at = NOW(),
                                lease_expires_at = NULL,
                                owner_id = NULL
                            WHERE id = $2
                            """,
                            reason, job["id"]
                        )
                        await conn.execute(
                            """
                            UPDATE public.projects 
                            SET embedding_status = 'failed', 
                                status = 'failed', 
                                updated_at = NOW() 
                            WHERE id = $1
                            """,
                            job["project_id"]
                        )
                        permanent_failures += 1
                        continue

                    new_retry = retry_count + 1
                    delay = BACKOFF_SECONDS.get(new_retry, 300)
                    logger.info(
                        "[RECOVERY] Scheduling retry %d/3 for job %s project %s in %ds",
                        new_retry, job_id, project_id, delay,
                    )
                    
                    await conn.execute(
                        """
                        UPDATE public.background_jobs 
                        SET status = 'retrying', 
                            retry_count = $1, 
                            current_stage = $2, 
                            updated_at = NOW() 
                        WHERE id = $3
                        """,
                        new_retry, f"Recovering (attempt {new_retry}/3, backoff {delay}s)", job["id"]
                    )

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
        """Spawns process_project_data_task under a safe wrapper with concurrency control."""
        from app.api.v1.endpoints.platform import process_project_data_task
        sem = self.get_recovery_semaphore()
        async with sem:
            try:
                await asyncio.to_thread(process_project_data_task, project_id)
            except Exception as e:
                logger.error("Recovered indexing failed for project %s: %s", project_id, e)
