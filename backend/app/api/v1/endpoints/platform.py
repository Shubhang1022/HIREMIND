"""Platform endpoints — projects, jobs, upload, analyze, analytics, export.

Uses JSON file persistence so data survives server restarts.
Will be migrated to Supabase once the project is active.
"""

from __future__ import annotations

import asyncio
import random
import csv
import threading
import io
import json
import logging
import os
import shutil
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from app.core.auth import get_optional_user, AuthUser
from app.core.config import settings
from app.core.startup_state import is_upload_allowed, readiness_snapshot
from app.core.diag import log_call, log_stage, diag_snapshot

router = APIRouter()
logger = logging.getLogger(__name__)

_UPLOAD_MEMORY_SPIKE_MB = 50.0
memory_safety_mode = os.getenv("MEMORY_SAFETY_MODE", "False").lower() in ("true", "1")
# Rate limiting for auto-resume operations (Render free tier protection)
_auto_resume_semaphore = asyncio.Semaphore(settings.auto_resume_max_concurrent)

def _compute_accuracy_metrics(results: list) -> dict:
    """Aggregate accuracy / confidence metrics from ranked candidate results."""
    if not results:
        return {
            "average_match_percent": 0.0,
            "average_ai_score": 0.0,
            "high_confidence_count": 0,
            "high_confidence_rate": 0.0,
            "average_semantic_similarity_percent": 0.0,
            "average_role_match_percent": 0.0,
            "average_skill_match_percent": 0.0,
            "average_experience_match_percent": 0.0,
            "eligible_rate": 0.0,
            "ranked_count": 0,
        }

    n = len(results)

    def _avg(key: str, default: float = 0.0) -> float:
        vals = [float(r.get(key) or default) for r in results]
        return round(sum(vals) / n, 2) if vals else 0.0

    high_conf = sum(1 for r in results if str(r.get("confidence", "")).lower() == "high")
    eligible = sum(1 for r in results if r.get("eligibility", True))

    return {
        "average_match_percent": _avg("match_percent"),
        "average_ai_score": round(
            sum(float(r.get("ai_score") or r.get("score") or 0.0) for r in results) / n, 4
        ),
        "high_confidence_count": high_conf,
        "high_confidence_rate": round(high_conf / n * 100, 1),
        "average_semantic_similarity_percent": _avg("semantic_similarity_percent", 50.0),
        "average_role_match_percent": _avg("role_match_percent", 50.0),
        "average_skill_match_percent": _avg("critical_skill_match_percent", 50.0),
        "average_experience_match_percent": _avg("experience_match_percent", 50.0),
        "eligible_rate": round(eligible / n * 100, 1),
        "ranked_count": n,
    }


def _release_embedding_model() -> None:
    """Unload embedding model and clear encoder singleton to free RAM."""
    global _encoder
    try:
        from app.services.model_service import unload_model
        unload_model()
    except Exception as exc:
        logger.warning("[MEMORY] unload_model failed: %s", exc)
    _encoder = None


def parse_jd_backup(text: str) -> dict:
    if not text:
        return {}
    import re
    result = {}
    
    # 1. Experience extraction
    min_exp = 0.0
    exp_matches = re.findall(r'(?:experience|exp|work\s+exp)[:\-\s–—]*(\d+)\s*(?:-|to|–|—)?\s*(\d+)?\s*(?:years|yrs|year|yr)', text, re.IGNORECASE)
    if exp_matches:
        try:
            min_exp = float(exp_matches[0][0])
        except:
            pass
    else:
        plus_matches = re.findall(r'(\d+)\+\s*(?:years|yrs|year|yr)', text, re.IGNORECASE)
        if plus_matches:
            try:
                min_exp = float(plus_matches[0])
            except:
                pass
    result["experience_years"] = {"min": min_exp}
    
    # 2. Role Category Classification
    role_category = "BACKEND"
    text_lower = text.lower()
    if "mlops" in text_lower:
        role_category = "MLOPS"
    elif "devops" in text_lower or "sre" in text_lower or "infrastructure" in text_lower:
        role_category = "DEVOPS"
    elif "data engineer" in text_lower or "etl" in text_lower:
        role_category = "DATA_ENGINEERING"
    elif "data scientist" in text_lower or "data science" in text_lower:
        role_category = "DATA_SCIENCE"
    elif "ai" in text_lower or "machine learning" in text_lower or "ml" in text_lower or "nlp" in text_lower:
        role_category = "AI_ML"
    elif "frontend" in text_lower or "react" in text_lower or "ui" in text_lower:
        role_category = "FRONTEND"
    elif "project manager" in text_lower or "scrum master" in text_lower:
        role_category = "PROJECT_MANAGEMENT"
    elif "product manager" in text_lower:
        role_category = "PRODUCT_MANAGEMENT"
    elif "design" in text_lower or "ux" in text_lower:
        role_category = "DESIGN"
    result["role_category"] = role_category
    
    # 3. Required skills extraction
    skills = []
    predefined_skills = [
        "python", "sql", "bash", "docker", "kubernetes", "mlflow", "kubeflow", "airflow",
        "aws", "azure", "gcp", "ci/cd", "github actions", "jenkins", "linux", "git",
        "java", "c++", "rust", "go", "scala", "spark", "hadoop", "kafka", "pandas",
        "numpy", "scikit-learn", "tensorflow", "pytorch", "keras", "spacy", "nltk",
        "huggingface", "llm", "embeddings", "vector search", "faiss", "pinecone",
        "weaviate", "qdrant", "milvus", "scrum", "agile", "jira", "terraform", "ansible"
    ]
    for skill in predefined_skills:
        if re.search(r'\b' + re.escape(skill) + r'\b', text_lower):
            skills.append(skill.title() if len(skill) > 3 else skill.upper())
            
    match = re.search(r'(?:required skills|key skills|requirements|what you will need|skills required)[:\-\s–]*([\s\S]+?)(?:\n\n|\n\w+[:\-\s]|$)', text, re.IGNORECASE)
    if match:
        list_text = match.group(1)
        items = re.split(r'[\n•\*\-,\u2022]', list_text)
        for item in items:
            item_clean = item.strip().strip("•*-* \t")
            if item_clean and len(item_clean) < 50 and not any(kw in item_clean.lower() for kw in ["responsibilities", "experience", "years", "plus", "location", "hybrid", "preferred"]):
                if item_clean not in skills:
                    skills.append(item_clean)
                    
    result["must_have_skills"] = skills[:15]
    result["nice_to_have_skills"] = skills[15:25]
    
    # Seniority
    seniority = "Mid"
    if "senior" in text_lower or "lead" in text_lower or "sr." in text_lower:
        seniority = "Senior"
    elif "junior" in text_lower or "jr." in text_lower or "entry" in text_lower:
        seniority = "Junior"
    result["seniority"] = seniority
    
    # Title
    title = ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if lines:
        title = lines[0]
        if len(title) > 80 or "job description" in title.lower() or "jd" in title.lower():
            words = title.split()
            title = " ".join(words[:5])
    result["title"] = title
    
    return result

# ── Supabase integration ──────────────────────────────────────────────────────────
from app.services.storage_provider import create_supabase_client
supabase_client = create_supabase_client(settings.supabase_url, settings.supabase_service_key)

def get_user_id(current_user: Optional[AuthUser]) -> str:
    # Use default user ID if unauthenticated to maintain local compatibility
    return current_user.id if current_user else "d6c20e10-8518-46b3-ba72-e88e77d2a912"

def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

import numpy as np

# Mock memory dictionaries for schema validation / health stats (some code might still reference _health_stats)
_health_stats = {
    "duplicate_projects_prevented": 0,
    "exports_generated": 0,
}

def stream_jsonl(file_like):
    for line in file_like:
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue

def stream_json_list(file_like):
    decoder = json.JSONDecoder()
    buffer = ""
    chunk_size = 65536
    found_list_start = False
    
    while True:
        chunk = file_like.read(chunk_size)
        if not chunk:
            break
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")
        buffer += chunk
        
        if not found_list_start:
            buffer = buffer.lstrip()
            if buffer.startswith("{"):
                idx = buffer.find("[")
                if idx != -1:
                    buffer = buffer[idx + 1:]
                    found_list_start = True
            elif buffer.startswith("["):
                buffer = buffer[1:]
                found_list_start = True
            else:
                found_list_start = True
                
        if found_list_start:
            while buffer:
                buffer = buffer.lstrip().lstrip(",").lstrip()
                if not buffer:
                    break
                if buffer.startswith("]"):
                    break
                try:
                    obj, index = decoder.raw_decode(buffer)
                    yield obj
                    buffer = buffer[index:]
                except json.JSONDecodeError:
                    break

def stream_csv(file_like):
    import csv
    def line_generator():
        for line in file_like:
            if isinstance(line, bytes):
                yield line.decode("utf-8-sig", errors="replace")
            else:
                yield line
                
    reader = csv.reader(line_generator())
    try:
        headers = next(reader)
    except StopIteration:
        return
        
    headers = [h.strip() if h else f"col_{i}" for i, h in enumerate(headers)]
    for i, row in enumerate(reader):
        raw = {headers[j]: (str(v) if v is not None else "") for j, v in enumerate(row) if j < len(headers)}
        if any(v for v in raw.values()):
            yield raw

def stream_candidates(file_like, filename: str):
    ext = Path(filename).suffix.lower()
    if ext == ".jsonl":
        return stream_jsonl(file_like)
    elif ext == ".json":
        return stream_json_list(file_like)
    elif ext == ".csv":
        return stream_csv(file_like)
    else:
        from src.ingestion.engine import IngestionEngine
        engine = IngestionEngine()
        content = file_like.read()
        records = engine.parse_file(content, filename)
        is_redrob_nested = False
        if records:
            first_raw = records[0].raw
            if isinstance(first_raw, dict) and "profile" in first_raw and "career_history" in first_raw and "skills" in first_raw:
                is_redrob_nested = True
        
        def legacy_generator():
            if is_redrob_nested:
                for r in records:
                    yield r.raw
            else:
                candidates_obj = engine.normalize_records(records)
                for c_obj in candidates_obj:
                    yield c_obj.to_dict()
        return legacy_generator()



# ── Hashing and Fingerprinting Helpers ─────────────────────────────────────────
import hashlib

def get_sha256_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def compute_dataset_hash(candidates: list[dict], project_id: Optional[str] = None) -> str:
    if project_id:
        try:
            proj_res = supabase_client.table("projects").select("current_candidate_path").eq("id", project_id).execute()
            if proj_res.data and proj_res.data[0].get("current_candidate_path"):
                current_path = proj_res.data[0]["current_candidate_path"]
                bucket, path = current_path.split("/", 1)
                
                h = hashlib.sha256()
                from app.services.storage_provider import StorageService
                for chunk in StorageService.download_stream(bucket, path):
                    h.update(chunk)
                return h.hexdigest()
        except Exception:
            pass
    if not candidates:
        return ""
    cand_strings = []
    for c in sorted(candidates, key=lambda x: x.get("candidate_id", "")):
        c_str = f"{c.get('candidate_id','')}|{c.get('candidate_name','')}|{c.get('years_of_experience',0)}|{c.get('skills',[])}"
        cand_strings.append(c_str)
    return get_sha256_hash("||".join(cand_strings))


def compute_project_hash(name: str, dataset_hash: str, jd_hash: str) -> str:
    combined = f"{name or ''}||{dataset_hash or ''}||{jd_hash or ''}"
    return get_sha256_hash(combined)

def _update_project_hash(project_id: str) -> None:
    try:
        proj_res = supabase_client.table("projects").select("*").eq("id", project_id).execute()
        if not proj_res.data:
            return
        p = proj_res.data[0]
        
        jobs_res = supabase_client.table("jobs").select("description").eq("project_id", project_id).execute()
        jd_text = ""
        if jobs_res.data:
            jd_text = jobs_res.data[0].get("description") or ""
        jd_hash = get_sha256_hash(jd_text) if jd_text else ""
        
        dataset_hash = compute_dataset_hash(None, project_id=project_id)
        project_hash = compute_project_hash(p.get("name", ""), dataset_hash, jd_hash)
        
        supabase_client.table("projects").update({
            "project_hash": project_hash,
            "dataset_hash": dataset_hash,
            "jd_hash": jd_hash,
            "updated_at": _now()
        }).eq("id", project_id).execute()
    except Exception:
        pass


async def _verify_background_jobs_table_exists() -> None:
    import logging
    logger = logging.getLogger(__name__)
    try:
        supabase_client.table("background_jobs").select("id").limit(1).execute()
        logger.info("[STARTUP] Verified background_jobs table exists in Supabase database.")
        print("[STARTUP] Verified background_jobs table exists in Supabase database.", flush=True)
    except Exception as exc:
        logger.error("[STARTUP_ERROR] Failed to verify background_jobs table: %s. Please run migrations.", exc)
        print(f"[STARTUP_ERROR] Failed to verify background_jobs table: {exc}", flush=True)


async def _recover_interrupted_jobs() -> None:
    from app.services.job_manager import JobManager
    manager = JobManager.get_instance()
    await manager.recover_interrupted_jobs()


async def run_startup_initialization() -> None:
    """
    Run startup integrity checks and timeouts enforcement using Supabase.
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        integrity_result = _run_integrity_check()
        logger.info("Startup Integrity Check: %s", integrity_result)

        # Verify table existence (Phase 1)
        await _verify_background_jobs_table_exists()
        
        # Distributed lock clearance and stale jobs cleanup on boot
        from app.services.job_manager import JobManager
        manager = JobManager.get_instance()
        await manager.clear_locks_on_boot()
        await manager.cleanup_stale_jobs(timeout_minutes=15)
        
        # Recover unfinished jobs (Phase 1)
        await _recover_interrupted_jobs()

        # Phase 5: Resume any projects that have candidate files but failed/missing indexes
        await _resume_indexing_for_eligible_projects()

        _enforce_analysis_timeouts()
        _enforce_embedding_timeouts()
        logger.info("Enforced analysis and embedding timeouts on startup.")
    except Exception as exc:
        logger.warning("Startup initialization encountered an error: %s", exc)


async def _resume_indexing_for_eligible_projects() -> None:
    """
    Phase 5: Resume indexing for projects where:
    - embedding_status is 'failed' or 'pending'
    - current_candidate_path exists (file was already uploaded)
    - No active background job for this project
    This ensures failed indexing is retried automatically without re-upload.
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        # Find projects with uploaded files but failed/pending indexing
        res = supabase_client.table("projects").select("id, current_candidate_path, embedding_status, user_id").in_(
            "embedding_status", ["failed", "pending"]
        ).not_.is_("current_candidate_path", "null").execute()

        if not res.data:
            logger.info("[RESUME_INDEXING] No eligible projects found for auto-resume.")
            return

        logger.info("[RESUME_INDEXING] Found %d projects eligible for auto-resume.", len(res.data))

        # Check which ones have active background jobs already
        from app.services.job_manager import JobManager
        manager = JobManager.get_instance()

        for p in res.data:
            project_id = p["id"]
            user_id = p.get("user_id", "")
            
            # Skip if job is already running in memory
            in_memory_job = manager.get_job_status(project_id)
            if in_memory_job and in_memory_job.get("status") in ("queued", "processing", "embedding", "indexing", "retrying"):
                logger.info("[RESUME_INDEXING] project=%s already has an active in-memory job, skipping.", project_id)
                continue

            # Check database for active jobs
            active_res = supabase_client.table("background_jobs").select("id, status").eq(
                "project_id", project_id
            ).in_("status", ["queued", "processing", "embedding", "indexing", "retrying"]).execute()
            if active_res.data:
                logger.info("[RESUME_INDEXING] project=%s has active DB job, skipping auto-resume.", project_id)
                continue

            logger.info(
                "[RESUME_INDEXING] project=%s embedding_status=%s has candidate file — scheduling auto-resume.",
                project_id, p.get("embedding_status")
            )
            # Schedule with a short delay so the server finishes booting first
            import asyncio as _asyncio
            # Calculate staggered delay to prevent concurrent auto-resume storms
            stagger = random.uniform(settings.auto_resume_stagger_min, settings.auto_resume_stagger_max)
            delay_seconds = settings.auto_resume_base_delay + stagger
            logger.info('[RESUME_INDEXING] Scheduling project=%s with delay=%.1fs (stagger=%.1fs)', project_id, delay_seconds, stagger)
            _asyncio.create_task(_delayed_resume_indexing(project_id, user_id, delay_seconds))

    except Exception as exc:
        logger.warning("[RESUME_INDEXING] Error during auto-resume scan: %s", exc)


async def _delayed_resume_indexing(project_id: str, user_id: str, delay_seconds: float = 15.0) -> None:
    """Wait delay_seconds then kick off a fresh indexing run for the project."""
    import asyncio as _asyncio
    import logging
    logger = logging.getLogger(__name__)
    await _asyncio.sleep(delay_seconds)
    
    # Use semaphore to limit concurrent auto-resume operations
    async with _auto_resume_semaphore:
    try:
        logger.info("[RESUME_INDEXING] Starting delayed auto-resume for project=%s", project_id)
        supabase_client.table("projects").update({
            "embedding_status": "queued",
            "status": "INDEXING",
            "updated_at": _now(),
        }).eq("id", project_id).execute()

        from app.services.job_manager import JobManager
        manager = JobManager.get_instance()
        coro = manager.register_job(project_id, user_id or "", "indexing")
        try:
            loop = _asyncio.get_event_loop()
        except RuntimeError:
            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)
        if loop.is_running():
            try:
                await coro
            except Exception:
                pass
        else:
            loop.run_until_complete(coro)

        async def _run_in_thread():
            try:
                await _asyncio.to_thread(
                    _safe_background_task,
                    "auto_resume_indexing",
                    process_project_data_task,
                    project_id,
                )
            except Exception as e:
                logger.error("Background task wrapper failed: %s", e)

        _asyncio.create_task(_run_in_thread())
        logger.info("[RESUME_INDEXING] Auto-resume kicked off for project=%s", project_id)
    except Exception as exc:
        logger.error("[RESUME_INDEXING] Auto-resume failed for project=%s: %s", project_id, exc)


# ── Embedding model singleton ─────────────────────────────────────────────────
# All code in this file must go through _get_encoder() to access the model.
# The model is loaded ONCE via ModelService (preloaded at startup).
# Never call EmbeddingEncoder() or SentenceTransformer() directly here.
_encoder = None  # kept for health-check compatibility (main.py references it)
_encoder_lock = threading.Lock()


def _get_encoder():
    """Return the process-wide EmbeddingEncoder backed by the ModelService singleton.

    Raises ModelLoadTimeout / ModelLoadFailed on failure — never hangs forever.
    """
    logger.info("[ENTER get_encoder()]")
    print("[ENTER get_encoder()]", flush=True)
    global _encoder
    
    # Fast path: check without lock
    if _encoder is not None:
        # Verify the underlying model is still the same
        from app.core.config import settings as _settings
        if _encoder.model_name == _settings.embedding_model and _encoder._model is not None:
            logger.info("[EXIT get_encoder()] (fast path)")
            print("[EXIT get_encoder()] (fast path)", flush=True)
            return _encoder

    with _encoder_lock:
        # Double checked lock
        if _encoder is not None:
            from app.core.config import settings as _settings
            if _encoder.model_name == _settings.embedding_model and _encoder._model is not None:
                logger.info("[EXIT get_encoder()] (fast path - locked)")
                print("[EXIT get_encoder()] (fast path - locked)", flush=True)
                return _encoder

        try:
            from app.services.model_service import get_model, is_loaded, ModelLoadTimeout, ModelLoadFailed

            # Get (or wait for) the singleton model
            raw_model = get_model()  # raises on timeout/failure; never returns None

            # Wrap in EmbeddingEncoder so encode_batch / encode_single / embedding_dim work
            import sys, os as _os
            _project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))))
            if _project_root not in sys.path:
                sys.path.insert(0, _project_root)

            from app.core.config import settings as _settings
            from src.features.embedding import EmbeddingEncoder
            enc = EmbeddingEncoder(model_name=_settings.embedding_model)
            enc._model = raw_model  # inject the already-loaded model — no download
            _encoder = enc
            logger.info("[EXIT get_encoder()] (full path)")
            print("[EXIT get_encoder()] (full path)", flush=True)
            return _encoder
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error("[EXIT get_encoder()] (exception: %s)\n%s", e, tb)
            print(f"[EXIT get_encoder()] (exception: {e})\n{tb}", flush=True)
            raise


def preload_model_singleton() -> None:
    """Call from FastAPI lifespan to kick off non-blocking model preload.

    The model loads in a daemon thread so startup is not delayed.
    The first call to _get_encoder() will block only if the preload is still
    in progress, but subsequent calls are instant.
    """
    from app.services.model_service import preload
    from app.core.config import settings as _settings
    preload(model_name=_settings.embedding_model)


# ── Memory Telemetry & Active Project Lock ────────────────────────────────────
_active_analyses: set[str] = set()

def get_memory_mb() -> float:
    try:
        import os
        import psutil
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except Exception:
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return float(line.split()[1]) / 1024
        except Exception:
            pass
        return 0.0

def log_memory(label: str) -> float:
    import logging
    import gc
    import psutil
    import os
    
    mem = 0.0
    vms = 0.0
    try:
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mem = mem_info.rss / (1024 * 1024)
        vms = mem_info.vms / (1024 * 1024)
    except Exception:
        mem = get_memory_mb()
        
    gc_counts = gc.get_count()
    
    # Try to read VmHWM (Peak RSS) on Linux
    peak_hwm = 0.0
    try:
        if os.path.exists("/proc/self/status"):
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmHWM:"):
                        peak_hwm = float(line.split()[1]) / 1024
                        break
    except Exception:
        pass

    msg = (
        f"[MEMORY_TELEMETRY] {label} | RSS: {mem:.2f} MB | "
        f"VMS: {vms:.2f} MB | Peak HWM: {peak_hwm:.2f} MB | "
        f"GC Counts: {gc_counts}"
    )
    logging.getLogger(__name__).info(msg)
    print(msg, flush=True)
    return mem


def _log_upload_memory_spike(baseline_mb: float, stage: str, current_mb: float) -> None:
    delta = current_mb - baseline_mb
    if delta > _UPLOAD_MEMORY_SPIKE_MB:
        logger.warning(
            "[UPLOAD_MEMORY_SPIKE] stage=%s baseline=%.1fMB current=%.1fMB delta=%.1fMB",
            stage,
            baseline_mb,
            current_mb,
            delta,
        )


def _ensure_upload_service_ready() -> None:
    if not is_upload_allowed():
        snap = readiness_snapshot()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Service is still initializing. Retry upload shortly.",
                "readiness": snap,
            },
        )


def _safe_background_task(task_name: str, fn, *args, **kwargs) -> None:
    """Run a background callable; never let exceptions escape to the worker.

    On any unhandled exception:
    1. Logs full traceback + RSS + CPU + thread count
    2. Attempts to mark the associated job as FAILED (best-effort)
    3. Logs top tracemalloc allocations if available
    """
    import traceback as _tb
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        tb_str = _tb.format_exc()
        _rss = _cpu = _nth = 0.0
        try:
            import psutil as _ps
            _proc = _ps.Process(os.getpid())
            _rss = _proc.memory_info().rss / (1024 * 1024)
            _cpu = _proc.cpu_percent(interval=None)
            _nth = _proc.num_threads()
        except Exception:
            pass

        logger.exception(
            "[BACKGROUND_TASK_FATAL] task=%s exception_type=%s exception=%s "
            "rss=%.1fMB cpu=%.1f%% threads=%d\n%s",
            task_name, type(exc).__name__, exc, _rss, _cpu, _nth, tb_str,
        )

        # Log top tracemalloc allocations if available
        try:
            import tracemalloc as _tm
            if _tm.is_tracing():
                snap = _tm.take_snapshot()
                top = snap.statistics("lineno")[:5]
                alloc_str = "\n".join(str(s) for s in top)
                logger.error("[TRACEMALLOC_TOP5] task=%s\n%s", task_name, alloc_str)
        except Exception:
            pass

        # Best-effort: mark the job as FAILED so SSE stops and UI updates
        # Extract project_id from args if present (first positional after task_name)
        _project_id = args[0] if args and isinstance(args[0], str) else None
        if _project_id:
            try:
                _sync_fail_job(_project_id, f"BACKGROUND_TASK_FATAL:{task_name}:{type(exc).__name__}:{exc}")
                supabase_client.table("projects").update({
                    "embedding_status": "failed",
                    "status": "FAILED",
                    "upload_statistics": {"failure_reason": f"{task_name} crashed: {exc}"},
                    "updated_at": _now(),
                }).eq("id", _project_id).execute()
                logger.info("[BACKGROUND_TASK_FATAL] project=%s marked as FAILED in DB", _project_id)
            except Exception as cleanup_exc:
                logger.error("[BACKGROUND_TASK_FATAL] cleanup failed for project=%s: %s",
                             _project_id, cleanup_exc)


def _extract_jd_raw_text(content: bytes, filename: str) -> str:
    filename_lower = (filename or "").lower()
    raw_text = ""

    if filename_lower.endswith(".docx"):
        try:
            from docx import Document
            import io as _io
            doc = Document(_io.BytesIO(content))
            raw_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            raw_text = ""
    elif filename_lower.endswith(".pdf"):
        try:
            import pypdf
            import io as _io
            reader = pypdf.PdfReader(_io.BytesIO(content))
            raw_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            raw_text = ""

    if not raw_text:
        raw_text = content.decode("utf-8", errors="replace")
    return raw_text.strip()


def process_jd_llm_background_task(project_id: str, job_id: str, raw_text: str) -> None:
    """Background-only JD enrichment — LLM must never run on the upload request path."""
    from app.core.openrouter import parse_jd_with_llm

    logger.info("[JD_PARSE_BACKGROUND_START] project=%s job=%s", project_id, job_id)
    try:
        llm_parsed = {}
        try:
            # Background thread: use run_coroutine_threadsafe if loop is running,
            # otherwise create a fresh event loop.  Never use asyncio.run() because
            # it creates a new loop that can conflict with the uvicorn event loop.
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            if loop.is_running():
                from asyncio import run_coroutine_threadsafe
                fut = run_coroutine_threadsafe(parse_jd_with_llm(raw_text), loop)
                llm_parsed = fut.result(timeout=60.0)
            else:
                llm_parsed = loop.run_until_complete(parse_jd_with_llm(raw_text))
        except Exception:
            llm_parsed = parse_jd_backup(raw_text)

        updates = {
            "required_skills": llm_parsed.get("must_have_skills", []),
            "nice_to_have_skills": llm_parsed.get("nice_to_have_skills", []),
            "min_experience": float(llm_parsed.get("experience_years", {}).get("min") or 0.0),
            "preferred_locations": llm_parsed.get("preferred_locations", []),
        }
        title = llm_parsed.get("title")
        if title:
            updates["title"] = title

        supabase_client.table("jobs").update(updates).eq("id", job_id).execute()
        logger.info("[JD_PARSE_BACKGROUND_COMPLETE] project=%s job=%s", project_id, job_id)
    except Exception:
        logger.exception("[JD_PARSE_BACKGROUND_FATAL] project=%s job=%s", project_id, job_id)


def process_candidate_upload_task(
    project_id: str,
    user_id: str,
    temp_raw_path: str,
    filename: str,
) -> None:
    """Parse, store, and index candidates — runs only in background (never on upload POST)."""
    temp_local_file = Path(temp_raw_path)
    baseline_mb = get_memory_mb()

    try:
        logger.info(
            "[CANDIDATE_UPLOAD_BACKGROUND_START] project=%s file=%s rss=%.1fMB",
            project_id,
            filename,
            baseline_mb,
        )

        proj_res = supabase_client.table("projects").select("*").eq("id", project_id).execute()
        if not proj_res.data:
            logger.error("[CANDIDATE_UPLOAD_BACKGROUND] project %s not found", project_id)
            return
        p = proj_res.data[0]

        parsed_path = Path(f"data/temp_upload_{project_id}.jsonl")
        parsed_path.parent.mkdir(parents=True, exist_ok=True)

        records_parsed = 0
        with open(temp_local_file, "rb") as raw_f, open(parsed_path, "w", encoding="utf-8") as out_f:
            chunk: list[dict] = []
            for cand_raw in stream_candidates(raw_f, filename):
                standardized = standardize_candidate(cand_raw)
                chunk.append(standardized)
                if len(chunk) >= 1000:
                    for c in chunk:
                        out_f.write(json.dumps(c, ensure_ascii=False) + "\n")
                    records_parsed += len(chunk)
                    chunk = []
            if chunk:
                for c in chunk:
                    out_f.write(json.dumps(c, ensure_ascii=False) + "\n")
                records_parsed += len(chunk)

        if records_parsed == 0:
            raise ValueError("No valid candidate records found in file")

        _log_upload_memory_spike(baseline_mb, "after_parse", get_memory_mb())

        version_res = supabase_client.table("candidate_uploads").select("version").eq(
            "project_id", project_id
        ).order("version", desc=True).limit(1).execute()
        new_version = version_res.data[0]["version"] + 1 if version_res.data else 1

        from app.services.storage_provider import StorageService
        storage_path = f"{project_id}/candidate_v{new_version}.jsonl"
        file_bytes = parsed_path.read_bytes()
        StorageService.upload_file("candidate-files", storage_path, file_bytes)

        _log_upload_memory_spike(baseline_mb, "after_supabase_write", get_memory_mb())

        supabase_client.table("candidate_uploads").insert({
            "project_id": project_id,
            "storage_path": storage_path,
            "version": new_version,
            "candidate_count": records_parsed,
            "status": "COMPLETED",
        }).execute()

        supabase_client.table("projects").update({
            "candidate_count": records_parsed,
            "status": "uploaded" if p.get("status") in ("CREATED", "draft") else p.get("status"),
            "embedding_status": "queued",
            "version": new_version,
            "current_candidate_path": f"candidate-files/{storage_path}",
            "updated_at": _now(),
        }).eq("id", project_id).execute()

        from app.services.job_manager import JobManager
        job_manager = JobManager.get_instance()
        # Use the sync helper (same pattern as _sync_update_progress) to avoid
        # asyncio.run() creating a new event loop that conflicts with the running one
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        coro = job_manager.register_job(project_id, user_id, "indexing")
        if loop.is_running():
            from asyncio import run_coroutine_threadsafe
            fut = run_coroutine_threadsafe(coro, loop)
            try:
                job_id = fut.result(timeout=10.0)
            except Exception:
                job_id = None
        else:
            job_id = loop.run_until_complete(coro)
        if not job_id:
            logger.warning(
                "[CANDIDATE_UPLOAD_BACKGROUND] no job_id for project %s — in-memory tracking only",
                project_id,
            )
        _sync_update_progress(project_id, "Upload received", 5, status="queued")

        _log_upload_memory_spike(baseline_mb, "after_background_job_creation", get_memory_mb())

        process_project_data_task(project_id)
        logger.info(
            "[CANDIDATE_UPLOAD_BACKGROUND_COMPLETE] project=%s records=%d",
            project_id,
            records_parsed,
        )
    except Exception as upload_exc:
        import traceback as _tb
        tb_str = _tb.format_exc()
        logger.exception("[CANDIDATE_UPLOAD_BACKGROUND_FATAL] project=%s error=%s\n%s",
                         project_id, upload_exc, tb_str)
        # Mark the background job as FAILED so worker-status/SSE reports correctly
        try:
            _sync_fail_job(project_id, f"CANDIDATE_UPLOAD_FAILED:{type(upload_exc).__name__}:{upload_exc}")
        except Exception:
            pass
        try:
            supabase_client.table("projects").update({
                "embedding_status": "failed",
                "status": "FAILED",
                "upload_statistics": {
                    "failure_reason": f"Candidate upload processing failed: {upload_exc}",
                    "traceback": tb_str[:500],
                },
                "updated_at": _now(),
            }).eq("id", project_id).execute()
        except Exception:
            pass
    finally:
        for path in (temp_local_file, Path(f"data/temp_upload_{project_id}.jsonl")):
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass


def standardize_candidate(c: dict) -> dict:
    if not isinstance(c, dict):
        return {}

    # If it is already in the Redrob nested format, return it
    if "profile" in c and "career_history" in c and "skills" in c:
        if "candidate_id" not in c and "external_id" in c:
            c["candidate_id"] = c["external_id"]
        elif "candidate_id" not in c:
            c["candidate_id"] = f"CAND_{abs(hash(c.get('profile', {}).get('anonymized_name', ''))) % 10000000:07d}"
        return c

    # Build the Redrob-compatible nested format
    profile = {
        "anonymized_name": c.get("full_name") or c.get("candidate_name") or c.get("name") or "—",
        "headline": c.get("headline") or "—",
        "summary": c.get("summary") or c.get("text_for_embedding") or "—",
        "location": c.get("location") or "—",
        "country": c.get("country") or "India",
        "years_of_experience": float(c.get("years_of_experience") or c.get("years_exp") or 0.0),
        "current_title": c.get("current_title") or "—",
        "current_company": c.get("current_company") or "—",
        "current_company_size": c.get("current_company_size") or "11-50",
        "current_industry": c.get("current_industry") or "Technology",
    }

    # Format career history
    raw_history = c.get("experience") or c.get("career_history") or []
    career_history = []
    if isinstance(raw_history, list):
        for role in raw_history:
            if isinstance(role, dict):
                career_history.append({
                    "company": role.get("company") or "—",
                    "title": role.get("title") or "—",
                    "start_date": role.get("start_date") or "2020-01-01",
                    "end_date": role.get("end_date"),
                    "duration_months": int(role.get("duration_months") or 12),
                    "is_current": bool(role.get("is_current", False)),
                    "industry": role.get("industry") or "Technology",
                    "company_size": role.get("company_size") or "11-50",
                    "description": role.get("description") or "",
                })
    
    # If career history is empty, populate a dummy role based on current title/company
    if not career_history and (profile["current_title"] != "—" or profile["current_company"] != "—"):
        career_history.append({
            "company": profile["current_company"],
            "title": profile["current_title"],
            "start_date": "2020-01-01",
            "end_date": None,
            "duration_months": int(profile["years_of_experience"] * 12) or 12,
            "is_current": True,
            "industry": "Technology",
            "company_size": "11-50",
            "description": profile["summary"] or "",
        })

    # Format skills
    raw_skills = c.get("skills") or []
    skills = []
    if isinstance(raw_skills, list):
        for s in raw_skills:
            if isinstance(s, dict):
                skills.append({
                    "name": s.get("name") or "",
                    "proficiency": s.get("proficiency") or "intermediate",
                    "endorsements": int(s.get("endorsements") or 0),
                    "duration_months": int(s.get("duration_months") or 0),
                })
            elif isinstance(s, str):
                skills.append({
                    "name": s,
                    "proficiency": "intermediate",
                    "endorsements": 0,
                    "duration_months": 0,
                })

    # Format education
    raw_edu = c.get("education") or []
    education = []
    if isinstance(raw_edu, list):
        for edu in raw_edu:
            if isinstance(edu, dict):
                education.append({
                    "institution": edu.get("institution") or edu.get("school") or "—",
                    "degree": edu.get("degree") or "—",
                    "field_of_study": edu.get("field_of_study") or edu.get("major") or "—",
                    "start_year": int(edu.get("start_year") or 2016),
                    "end_year": int(edu.get("end_year") or 2020),
                    "grade": edu.get("grade"),
                    "tier": edu.get("tier") or "unknown",
                })
            elif isinstance(edu, str):
                education.append({
                    "institution": "—",
                    "degree": edu,
                    "field_of_study": "—",
                    "start_year": 2016,
                    "end_year": 2020,
                    "grade": None,
                    "tier": "unknown",
                })

    # Redrob signals
    raw_signals = c.get("redrob_signals") or c.get("signals") or {}
    redrob_signals = {
        "profile_completeness_score": float(raw_signals.get("profile_completeness_score") or 100.0),
        "signup_date": raw_signals.get("signup_date") or "2025-01-01",
        "last_active_date": raw_signals.get("last_active_date") or "2025-06-01",
        "open_to_work_flag": bool(raw_signals.get("open_to_work_flag", True)),
        "profile_views_received_30d": int(raw_signals.get("profile_views_received_30d") or 10),
        "applications_submitted_30d": int(raw_signals.get("applications_submitted_30d") or 5),
        "recruiter_response_rate": float(raw_signals.get("recruiter_response_rate") or 1.0),
        "avg_response_time_hours": float(raw_signals.get("avg_response_time_hours") or 24.0),
        "skill_assessment_scores": raw_signals.get("skill_assessment_scores") or {},
        "connection_count": int(raw_signals.get("connection_count") or 50),
        "endorsements_received": int(raw_signals.get("endorsements_received") or 5),
        "notice_period_days": int(raw_signals.get("notice_period_days") or 30),
        "expected_salary_range_inr_lpa": raw_signals.get("expected_salary_range_inr_lpa") or {"min": 10.0, "max": 20.0},
        "preferred_work_mode": raw_signals.get("preferred_work_mode") or "remote",
        "willing_to_relocate": bool(raw_signals.get("willing_to_relocate", True)),
        "github_activity_score": float(raw_signals.get("github_activity_score") or 50.0),
        "search_appearance_30d": int(raw_signals.get("search_appearance_30d") or 5),
        "saved_by_recruiters_30d": int(raw_signals.get("saved_by_recruiters_30d") or 1),
        "interview_completion_rate": float(raw_signals.get("interview_completion_rate") or 1.0),
        "offer_acceptance_rate": float(raw_signals.get("offer_acceptance_rate") or 1.0),
        "verified_email": bool(raw_signals.get("verified_email", True)),
        "verified_phone": bool(raw_signals.get("verified_phone", True)),
        "linkedin_connected": bool(raw_signals.get("linkedin_connected", True)),
    }

    candidate_id = c.get("candidate_id") or c.get("external_id") or f"CAND_{abs(hash(profile['anonymized_name'])) % 10000000:07d}"

    return {
        "candidate_id": candidate_id,
        "profile": profile,
        "career_history": career_history,
        "skills": skills,
        "redrob_signals": redrob_signals,
        "education": education,
        "certifications": c.get("certifications") or [],
        "languages": c.get("languages") or [],
    }


def enrich_candidate_with_intelligence(c: dict) -> dict:
    c = standardize_candidate(c)
    if "candidate_intelligence" not in c:
        try:
            import sys, os
            _project_root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
            if _project_root not in sys.path:
                sys.path.insert(0, _project_root)
            from src.features.structured import StructuredFeatureExtractor
            extractor = StructuredFeatureExtractor()
            feats = extractor.extract(c)
            c["candidate_intelligence"] = feats.get("candidate_intelligence")
            c["candidate_specialization"] = feats.get("candidate_specialization")
            c["specialization_confidence"] = feats.get("specialization_confidence")
            c["candidate_type"] = feats.get("candidate_type")
            c["relevant_years_exp"] = feats.get("relevant_years_exp")
            c["education_tier"] = feats.get("education_tier")
            c["education_is_tech"] = feats.get("education_is_tech")
            c["candidate_quality_score"] = feats.get("candidate_quality_score")
            c["candidate_role_category"] = feats.get("candidate_role_category")
            c["is_disqualified"] = feats.get("is_disqualified")
            c["disqualifier_reason"] = feats.get("disqualifier_reason")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Could not extract intelligence for candidate: %s", e)
    return c


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    project_hash: Optional[str] = None
    dataset_hash: Optional[str] = None
    jd_hash: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


class JobCreate(BaseModel):
    title: str
    description: str
    company: Optional[str] = None
    location: Optional[str] = None
    work_mode: Optional[str] = None
    required_skills: list[str] = []
    min_experience: Optional[float] = None
    
    # Recruiter-controlled metadata
    openings: Optional[int] = 5
    shortlist_size: Optional[int] = 15
    priority: Optional[str] = "balanced"
    min_match_percent: Optional[float] = None
    salary_range: Optional[str] = None
    job_location: Optional[str] = None
    employment_type: Optional[str] = None


class AnalysisRequest(BaseModel):
    job_id: str
    top_k: int = 100
    performance_mode: Optional[str] = "balanced"


class ExportRequest(BaseModel):
    ranking_id: str
    format: str = "csv"


# ── Analysis Timeout Recovery ─────────────────────────────────────────────────
def _enforce_analysis_timeouts() -> None:
    try:
        res = supabase_client.table("projects").select("*").in_("status", ["queued", "processing", "ranking"]).execute()
        now_dt = datetime.now(timezone.utc)
        for p in res.data:
            updated_at_str = p.get("updated_at") or p.get("created_at")
            if updated_at_str:
                updated_at_dt = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                age_minutes = (now_dt - updated_at_dt).total_seconds() / 60.0
                if age_minutes > 30.0:
                    supabase_client.table("projects").update({
                        "status": "failed",
                        "updated_at": _now()
                    }).eq("id", p["id"]).execute()
    except Exception:
        pass


# ── Background Worker Health Check & Indexing ─────────────────────────────────
def _enforce_embedding_timeouts() -> None:
    try:
        res = supabase_client.table("projects").select("*").in_("embedding_status", ["queued", "processing"]).execute()
        now_dt = datetime.now(timezone.utc)
        for p in res.data:
            started_at_str = p.get("updated_at") or p.get("created_at")
            if started_at_str:
                started_dt = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
                age_hours = (now_dt - started_dt).total_seconds() / 3600.0
                if age_hours > 1.0:
                    supabase_client.table("projects").update({
                        "embedding_status": "failed",
                        "updated_at": _now()
                    }).eq("id", p["id"]).execute()
    except Exception:
        pass

def process_project_data_task(project_id: str):
    import time
    import traceback
    import logging
    import json
    import tempfile
    import shutil
    import os
    import gc
    import hashlib
    import zipfile
    from pathlib import Path
    import numpy as np
    import pandas as pd
    from datetime import datetime, timezone
    import psutil

    logger = logging.getLogger(__name__)
    t_start = time.time()
    mem_start = get_memory_mb()
    logger.info("[BACKGROUND_TASK_START] Project ID: %s | Memory: %.2fMB", project_id, mem_start)
    print(f"[BACKGROUND_TASK_START] Project ID: {project_id} | Memory: {mem_start:.2f}MB", flush=True)

    peak_ram = mem_start

    def log_worker_heartbeat(stage: str, processed: int, total: int, batch_num: int):
        elapsed_sec = int(time.time() - t_start)
        elapsed_str = f"{elapsed_sec // 3600:02d}:{(elapsed_sec % 3600) // 60:02d}:{elapsed_sec % 60:02d}"
        curr_ram = get_memory_mb()
        nonlocal peak_ram
        peak_ram = max(peak_ram, curr_ram)
        
        heartbeat_msg = f"""
[WORKER_HEARTBEAT]
Project: {project_id[:8]}
Stage: {stage}
Progress: {processed} / {total}
Batch: {batch_num}
RAM: {curr_ram:.1f} MB
Peak RAM: {peak_ram:.1f} MB
Elapsed: {elapsed_str}
"""
        logger.info(heartbeat_msg)
        print(heartbeat_msg, flush=True)

    max_retries = 3
    temp_dir = None

    try:
        from app.services.job_manager import JobManager, LockResult
        job_manager = JobManager.get_instance()
        
        # Load user_id dynamically from projects table to register locking job
        try:
            p_res = supabase_client.table("projects").select("user_id").eq("id", project_id).execute()
            user_id = p_res.data[0].get("user_id") if p_res.data else None
        except Exception:
            user_id = None

        # Lock check / start job using the event loop wrapper
        main_loop = getattr(job_manager, "loop", None)
        coro = job_manager.start_job(project_id, user_id, "Validating candidates")
        if main_loop and main_loop.is_running():
            from asyncio import run_coroutine_threadsafe
            fut = run_coroutine_threadsafe(coro, main_loop)
            started = fut.result(timeout=15.0)
        else:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            if loop.is_running():
                from asyncio import run_coroutine_threadsafe
                fut = run_coroutine_threadsafe(coro, loop)
                started = fut.result(timeout=15.0)
            else:
                started = loop.run_until_complete(coro)

        lock_res, job_id, owner_id = started

        if lock_res == LockResult.ALREADY_HELD:
            logger.warning("[BACKGROUND_TASK_IGNORE] Indexing already active or locked by another worker for project %s. Current owner: %s. Ignoring duplicate call.", project_id, owner_id)
            return
        elif lock_res == LockResult.ERROR:
            err_msg = owner_id or "Unknown lock acquisition error"
            logger.error("[LOCK_ACQUIRE_ERROR] project=%s exception=%s", project_id, err_msg)
            try:
                _sync_fail_job(project_id, f"LOCK_ACQUIRE_ERROR: {err_msg}")
            except Exception:
                pass
            try:
                supabase_client.table("projects").update({
                    "embedding_status": "failed",
                    "status": "FAILED",
                    "upload_statistics": {
                        "failure_reason": f"Lock acquisition failed: {err_msg}",
                    },
                    "updated_at": _now(),
                }).eq("id", project_id).execute()
            except Exception:
                pass
            return

        for attempt in range(1, max_retries + 1):
            try:
                # Check for cancellation requested
                if job_manager.is_cancelled(project_id):
                    _sync_update_progress(project_id, "Cancelled", 0, job_status="cancelled")
                    job_manager.clear_cancellation(project_id)
                    logger.info("Background indexing cancelled for project %s", project_id)
                    return

                if attempt > 1:
                    _sync_update_progress(
                        project_id,
                        f"Retrying (attempt {attempt}/{max_retries})",
                        5,
                        job_status="retrying",
                        retry_count=attempt - 1,
                    )
                    _sync_update_progress(
                        project_id,
                        "Validating candidates",
                        10,
                        job_status="processing",
                        retry_count=attempt - 1,
                    )
                    from app.services.model_service import reset_failed_load
                    reset_failed_load()
                    gc.collect()

                # Calculate dataset hash
                dataset_hash = compute_dataset_hash(None, project_id=project_id)
                proj_res = supabase_client.table("projects").select("*").eq("id", project_id).execute()
                if not proj_res.data:
                    logger.error("Project %s not found in background task", project_id)
                    return
                p = proj_res.data[0]
                version = p.get("version") or 1
                current_path = p.get("current_candidate_path")
                if not current_path:
                    raise FileNotFoundError("No candidate upload path found in project")
                
                bucket, path = current_path.split("/", 1)
                from app.services.storage_provider import StorageService

                # ── PART 14: Hash Cache Reuse ──
                # If project is already completed and the dataset hash matches the computed hash, and zip exists, reuse it!
                zip_key = f"{project_id}/index_v{version}.zip"
                if p.get("embedding_status") == "completed" and p.get("dataset_hash") == dataset_hash:
                    if StorageService.file_exists("embeddings", zip_key):
                        logger.info("[REUSE_INDEX] project=%s dataset_hash=%s already indexed successfully. Skipping rebuild.", project_id, dataset_hash)
                        _sync_update_progress(project_id, "Ready", 100, job_status="completed")
                        break

                # Setup local checkpoints directory
                ckpt_dir = Path("data/indexing_checkpoints") / project_id
                ckpt_dir.mkdir(parents=True, exist_ok=True)
                temp_dir = str(ckpt_dir) # Map to temp_dir so cleanup in finally catches it

                cand_jsonl_path = ckpt_dir / "enriched_candidates.jsonl"
                embeddings_raw_path = ckpt_dir / "embeddings.raw"
                faiss_index_path = ckpt_dir / "candidate_index.faiss"
                parquet_metadata_path = ckpt_dir / "candidate_metadata.parquet"
                lookup_json_path = ckpt_dir / "candidate_lookup.json"

                from src.features.structured import _classify_specialization_with_confidence, classify_candidate_role_category, HARD_DISQUALIFIER_TITLES
                from src.scoring.quality import calculate_candidate_quality_score

                # Update progress (stage only — status stays processing until embedding)
                _sync_update_progress(project_id, "Validating candidates", 10, retry_count=attempt - 1)

                from app.services.job_manager import safe_execute
                safe_execute(
                    supabase_client.table("projects").update({
                        "embedding_status": "processing",
                        "status": "INDEXING",
                        "updated_at": _now()
                    }).eq("id", project_id)
                )

                # ── STAGE 1: Stream candidates & enrich ──
                if not cand_jsonl_path.exists():
                    _sync_update_progress(project_id, "Streaming candidates", 20, retry_count=attempt - 1)
                    logger.info("[STAGE_START] Streaming candidates and standardizing for project %s", project_id)
                    
                    with open(cand_jsonl_path, "w", encoding="utf-8") as f_enriched:
                        for idx, c_raw in enumerate(StorageService.stream_jsonl(bucket, path)):
                            if idx % 10 == 0 and job_manager.is_cancelled(project_id):
                                _sync_update_progress(project_id, "Cancelled", 0, job_status="cancelled")
                                job_manager.clear_cancellation(project_id)
                                return

                            c = standardize_candidate(c_raw)
                            profile = c.get("profile", {})
                            career_history = c.get("career_history", [])
                            skills_list = c.get("skills", [])

                            is_disqualified = False
                            disqualifier_reason = None
                            current_title = str(profile.get("current_title", "")).lower()
                            if any(dt in current_title for dt in HARD_DISQUALIFIER_TITLES):
                                is_disqualified = True
                                disqualifier_reason = "non_technical"

                            cand_specialization, _ = _classify_specialization_with_confidence(profile, career_history, skills_list)
                            cand_category = classify_candidate_role_category(profile, career_history, skills_list)
                            cand_yoe = float(profile.get("years_of_experience") or 0.0)

                            features_so_far = {
                                "years_exp": cand_yoe,
                                "is_disqualified": is_disqualified,
                                "disqualifier_reason": disqualifier_reason,
                            }
                            cand_quality_score = calculate_candidate_quality_score(features_so_far, c)

                            c["candidate_specialization"] = cand_specialization
                            c["candidate_role_category"] = normalize_role_category(cand_category)
                            c["years_exp"] = cand_yoe
                            c["candidate_quality_score"] = cand_quality_score
                            c["is_disqualified"] = is_disqualified
                            c["disqualifier_reason"] = disqualifier_reason

                            f_enriched.write(json.dumps(c, ensure_ascii=False) + "\n")

                # Count candidates
                total_candidates = 0
                with open(cand_jsonl_path, "r", encoding="utf-8") as f_enriched:
                    for _ in f_enriched:
                        total_candidates += 1

                _sync_update_progress(project_id, "Resume standardization", 35, retry_count=attempt - 1)
                logger.info("Enriched %d candidates. Proceeding to embedding generation.", total_candidates)

                # ── STAGE 2: Load embedding model (Singleton) ──
                gc.collect()
                _sync_update_progress(project_id, "Loading embedding model", 50, job_status="embedding", retry_count=attempt - 1)
                
                from src.features.text_builder import build_candidate_text
                from app.services.model_service import is_loaded, ModelLoadTimeout, ModelLoadFailed
                
                t_load_start = time.time()
                encoder = _get_encoder()
                dim = getattr(encoder, 'embedding_dim', 384)
                logger.info("[STAGE_END] project=%s stage=load_model elapsed=%.2fs dim=%d", project_id, time.time() - t_load_start, dim)

                # ── STAGE 3: Generate embeddings & build FAISS (Chunked & Resumable) ──
                chunk_size = settings.embedding_chunk_size
                total_chunks = (total_candidates + chunk_size - 1) // chunk_size
                
                # Check how many are already embedded
                num_embedded = 0
                if embeddings_raw_path.exists():
                    try:
                        num_embedded = os.path.getsize(embeddings_raw_path) // (dim * 4)
                        logger.info("Found %d already embedded candidates in checkpoint raw file", num_embedded)
                    except Exception:
                        pass
                
                # Setup FAISS Index
                import faiss
                index = faiss.IndexFlatIP(dim)
                if num_embedded > 0:
                    try:
                        raw_data = np.fromfile(embeddings_raw_path, dtype=np.float32).reshape(-1, dim)
                        if len(raw_data) > 0:
                            index.add(raw_data)
                            logger.info("Loaded %d vectors into FAISS index from checkpoint", index.ntotal)
                    except Exception as e:
                        logger.error("Failed to load existing embeddings into FAISS: %s. Resetting raw embeddings file.", e)
                        num_embedded = 0
                        if embeddings_raw_path.exists():
                            os.remove(embeddings_raw_path)
                
                # Start loop
                t_embeddings_start = time.time()
                chunk_durations = []
                
                with open(cand_jsonl_path, "r", encoding="utf-8") as f_enriched:
                    candidates_pool = []
                    current_idx = 0
                    
                    for line in f_enriched:
                        # Check cancellation
                        if current_idx % 10 == 0 and job_manager.is_cancelled(project_id):
                            _sync_update_progress(project_id, "Cancelled", 0, job_status="cancelled")
                            job_manager.clear_cancellation(project_id)
                            return
                            
                        if current_idx < num_embedded:
                            current_idx += 1
                            continue
                            
                        c = json.loads(line)
                        candidates_pool.append(c)
                        current_idx += 1
                        
                        if len(candidates_pool) >= chunk_size:
                            chunk_num = (current_idx // chunk_size)
                            t_chunk_start = time.time()
                            
                            progress_pct = 65 + int((current_idx / max(total_candidates, 1)) * 16) # 65% to 81%
                            stage_label = f"Embedding {current_idx} / {total_candidates}"
                            
                            batch_texts = [build_candidate_text(item) if not item.get("is_disqualified", False) else "" for item in candidates_pool]
                            valid_indices = [i for i, text in enumerate(batch_texts) if text != ""]
                            valid_texts = [batch_texts[i] for i in valid_indices]
                            
                            if valid_texts:
                                encoded = encoder.encode_batch(valid_texts)
                                arr = np.array(encoded, dtype=np.float32)
                                chunk_embs = np.zeros((len(candidates_pool), dim), dtype=np.float32)
                                for idx_in_chunk, original_idx in enumerate(valid_indices):
                                    chunk_embs[original_idx] = arr[idx_in_chunk]
                            else:
                                chunk_embs = np.zeros((len(candidates_pool), dim), dtype=np.float32)
                                
                            with open(embeddings_raw_path, "ab") as f_raw:
                                f_raw.write(chunk_embs.tobytes())
                                
                            index.add(chunk_embs)
                            
                            chunk_elapsed = time.time() - t_chunk_start
                            chunk_durations.append(chunk_elapsed)
                            if len(chunk_durations) > 5:
                                chunk_durations.pop(0)
                            avg_chunk_time = sum(chunk_durations) / len(chunk_durations)
                            remaining_chunks = max(0, total_chunks - chunk_num)
                            eta_seconds = remaining_chunks * avg_chunk_time
                            eta_str = f"{int(eta_seconds // 60):02d}:{int(eta_seconds % 60):02d}"
                            overall_speed = current_idx / max(time.time() - t_embeddings_start, 0.001)
                            
                            _sync_update_progress(
                                project_id, stage_label, progress_pct,
                                job_status="embedding",
                                processed_candidates=current_idx,
                                total_candidates=total_candidates,
                                eta=eta_str,
                                speed=overall_speed,
                                retry_count=attempt - 1
                            )
                            log_worker_heartbeat(f"Generating Embeddings (chunk {chunk_num}/{total_chunks})", current_idx, total_candidates, chunk_num)
                            
                            del chunk_embs, batch_texts, valid_texts, valid_indices
                            if 'arr' in locals():
                                del arr
                            if 'encoded' in locals():
                                del encoded
                            gc.collect()
                            candidates_pool = []
                            
                    # Final partial chunk
                    if candidates_pool:
                        chunk_num = total_chunks
                        t_chunk_start = time.time()
                        progress_pct = 81
                        stage_label = f"Embedding {current_idx} / {total_candidates}"
                        
                        batch_texts = [build_candidate_text(item) if not item.get("is_disqualified", False) else "" for item in candidates_pool]
                        valid_indices = [i for i, text in enumerate(batch_texts) if text != ""]
                        valid_texts = [batch_texts[i] for i in valid_indices]
                        
                        if valid_texts:
                            encoded = encoder.encode_batch(valid_texts)
                            arr = np.array(encoded, dtype=np.float32)
                            chunk_embs = np.zeros((len(candidates_pool), dim), dtype=np.float32)
                            for idx_in_chunk, original_idx in enumerate(valid_indices):
                                chunk_embs[original_idx] = arr[idx_in_chunk]
                        else:
                            chunk_embs = np.zeros((len(candidates_pool), dim), dtype=np.float32)
                            
                        with open(embeddings_raw_path, "ab") as f_raw:
                            f_raw.write(chunk_embs.tobytes())
                            
                        index.add(chunk_embs)
                        overall_speed = current_idx / max(time.time() - t_embeddings_start, 0.001)
                        
                        _sync_update_progress(
                            project_id, stage_label, progress_pct,
                            job_status="embedding",
                            processed_candidates=current_idx,
                            total_candidates=total_candidates,
                            eta="00:00",
                            speed=overall_speed,
                            retry_count=attempt - 1
                        )
                        log_worker_heartbeat("Generating Embeddings (Final chunk)", current_idx, total_candidates, chunk_num)
                        
                        del chunk_embs, batch_texts, valid_texts, valid_indices
                        if 'arr' in locals():
                            del arr
                        if 'encoded' in locals():
                            del encoded
                        gc.collect()
                        candidates_pool = []

                # ── STAGE 4: Build FAISS Index File ──
                _sync_update_progress(project_id, "Building FAISS", 82, job_status="indexing", retry_count=attempt - 1)
                faiss.write_index(index, str(faiss_index_path))
                logger.info("FAISS Index written successfully to %s. Total vectors: %d", faiss_index_path, index.ntotal)

                # ── STAGE 5: Build Parquet Metadata & JSON Lookup ──
                metadata_rows = []
                lookup_dict = {}
                offset = 0
                with open(cand_jsonl_path, "r", encoding="utf-8") as f_enriched:
                    for line in f_enriched:
                        c = json.loads(line)
                        c_id = c.get("candidate_id") or f"c_{offset}"
                        
                        skills_list = c.get("skills", [])
                        skills_extracted = [s if isinstance(s, str) else s.get("name", "") for s in skills_list]
                        skills_extracted = [s.lower().strip() for s in skills_extracted if s]
                        
                        metadata_rows.append({
                            "candidate_id": c_id,
                            "role": c.get("candidate_role_category", "BACKEND"),
                            "skills": skills_extracted,
                            "experience": float(c.get("years_exp", 0.0)),
                            "candidate_quality_score": float(c.get("candidate_quality_score", 0.0)),
                            "embedding_offset": offset
                        })
                        lookup_dict[c_id] = c
                        offset += 1

                df = pd.DataFrame(metadata_rows)
                df.to_parquet(parquet_metadata_path, index=False)
                
                with open(lookup_json_path, "w", encoding="utf-8") as f_lookup:
                    json.dump(lookup_dict, f_lookup, ensure_ascii=False)

                logger.info("Metadata and lookup files written successfully.")

                # ── STAGE 6: Zip & Upload ──
                _sync_update_progress(project_id, "Uploading indexes", 92, job_status="indexing", retry_count=attempt - 1)
                
                zip_path = ckpt_dir / f"index_v{version}.zip"
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                    z.write(faiss_index_path, "candidate_index.faiss")
                    z.write(parquet_metadata_path, "candidate_metadata.parquet")
                    z.write(lookup_json_path, "candidate_lookup.json")

                # Compute checksum
                zip_hash = hashlib.sha256()
                with open(zip_path, "rb") as f_zip:
                    while True:
                        data_chunk = f_zip.read(65536)
                        if not data_chunk:
                            break
                        zip_hash.update(data_chunk)
                checksum = zip_hash.hexdigest()

                zip_content = zip_path.read_bytes()
                StorageService.upload_file("embeddings", zip_key, zip_content)
                del zip_content

                # ── STAGE 7: Verify ──
                _sync_update_progress(project_id, "Verifying artifacts", 97, job_status="indexing", retry_count=attempt - 1)
                if not StorageService.file_exists("embeddings", zip_key):
                    raise FileNotFoundError(f"Uploaded ZIP index {zip_key} could not be verified in storage bucket.")

                upload_stats = {
                    "dataset_hash": dataset_hash,
                    "archive_checksum": checksum,
                    "total_candidates": total_candidates,
                    "version": version,
                    "processed_at": _now()
                }

                # Update projects table in DB
                proj_update = supabase_client.table("projects").update({
                    "embedding_status": "completed",
                    "status": "completed",
                    "dataset_hash": dataset_hash,
                    "embeddings_path": f"embeddings/{zip_key}",
                    "faiss_index_path": f"embeddings/{zip_key}",
                    "role_index_path": None,
                    "skill_index_path": None,
                    "upload_statistics": upload_stats,
                    "updated_at": _now()
                }).eq("id", project_id)
                safe_execute(proj_update)

                # Clean up local checkpoint directory
                try:
                    shutil.rmtree(ckpt_dir)
                except Exception:
                    pass

                _sync_update_progress(
                    project_id, "Ready", 100, job_status="completed",
                    processed_candidates=total_candidates,
                    total_candidates=total_candidates,
                    retry_count=attempt - 1
                )
                
                print(
                    f"INDEXING PIPELINE COMPLETE\n"
                    f"DB status = completed\n"
                    f"Project status = completed\n"
                    f"Background job status = completed\n"
                    f"Ready for analysis = true",
                    flush=True
                )
                break

            except Exception as e:
                from app.services.job_manager import LockLostError
                from app.services.model_service import ModelLoadFailed, ModelLoadTimeout
                if isinstance(e, LockLostError) or "LockLostError" in type(e).__name__:
                    logger.warning("[LOCK_LOST_ABORT] project=%s indexing aborted: lock was lost/hijacked.", project_id)
                    return

                elapsed = time.time() - t_start
                err_label = str(e)
                if isinstance(e, (ModelLoadFailed, ModelLoadTimeout)):
                    err_label = f"MODEL_LOAD_FAILED: {e}"
                logger.error("[BACKGROUND_TASK_FAIL] project=%s attempt=%d/%d elapsed=%.3fs\nException: %s",
                             project_id, attempt, max_retries, elapsed, err_label)
                if attempt < max_retries:
                    sleep_secs = 2.0 ** attempt
                    time.sleep(sleep_secs)
                else:
                    # Final failure
                    from app.services.job_manager import JobManager as _JM
                    _jm = _JM.get_instance()
                    cache = _jm._progress_cache.get(project_id)
                    if cache:
                        cache["status"] = "failed"
                        cache["current_stage"] = f"Failed: {str(e)[:120]}"
                        cache["updated_at"] = time.time()
                    _sync_fail_job(project_id, err_label)
                    safe_execute(
                        supabase_client.table("projects").update({
                            "embedding_status": "failed",
                            "status": "FAILED",
                            "upload_statistics": {"failure_reason": f"Background indexing failed: {err_label}"},
                            "updated_at": _now()
                        }).eq("id", project_id)
                    )

    finally:
        try:
            from app.services.job_manager import JobManager
            JobManager.get_instance().finish_job(project_id)
        except Exception:
            pass
        from app.services.cache_service import CacheService
        CacheService.invalidate_project(project_id)
        
        # Cleanup routine
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
        gc.collect()

        # Release embedding model RAM after indexing (512 MB tier optimization)
        if settings.unload_model_after_indexing:
            _release_embedding_model()


# ── Startup Integrity Check (Mocked for Supabase) ─────────────────────────────
def _run_integrity_check() -> str:
    return "Integrity checks passed (Supabase active)"


# ── Projects ──────────────────────────────────────────────────────────────────

# ── Sync Helpers for JobManager (Thread compatibility) ───────────────────────

def _sync_update_progress(
    project_id: str,
    stage: str,
    progress: int,
    job_status: str = None,
    # Legacy positional-style params kept for backwards compatibility
    processed: int = 0,
    total: int = 0,
    eta: str = "",
    retry_count: int = None,
    # Extended params used by newer callers — keyword-only aliases
    processed_candidates: int = None,
    total_candidates: int = None,
    batch: int = None,
    total_batches: int = None,
    elapsed_seconds: float = None,
    eta_seconds: float = None,
    memory_usage: float = None,
    speed: float = None,
    **kwargs,
):
    """Thread-safe wrapper around JobManager.update_job_progress()."""
    # Safe check for kwargs to avoid shadowing and preserve backward compatibility
    if "status" in kwargs:
        job_status = kwargs.pop("status")

    resolved_processed = processed_candidates if processed_candidates is not None else processed
    resolved_total     = total_candidates     if total_candidates     is not None else total

    import asyncio
    from app.services.job_manager import JobManager
    manager = JobManager.get_instance()
    main_loop = getattr(manager, "loop", None)

    coro = manager.update_job_progress(
        project_id,
        stage,
        progress,
        job_status,
        resolved_processed,
        resolved_total,
        eta,
        retry_count,
        speed,
    )

    if main_loop and main_loop.is_running():
        from asyncio import run_coroutine_threadsafe
        future = run_coroutine_threadsafe(coro, main_loop)
        try:
            future.result(timeout=5.0)
        except Exception as exc:
            from app.services.job_manager import LockLostError
            if isinstance(exc, LockLostError) or "LockLostError" in type(exc).__name__:
                raise exc
            pass
    else:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        if loop.is_running():
            from asyncio import run_coroutine_threadsafe
            future = run_coroutine_threadsafe(coro, loop)
            try:
                future.result(timeout=5.0)
            except Exception as exc:
                from app.services.job_manager import LockLostError
                if isinstance(exc, LockLostError) or "LockLostError" in type(exc).__name__:
                    raise exc
                pass
        else:
            try:
                loop.run_until_complete(coro)
            except Exception as exc:
                from app.services.job_manager import LockLostError
                if isinstance(exc, LockLostError) or "LockLostError" in type(exc).__name__:
                    raise exc
                pass

def _sync_fail_job(project_id: str, reason: str):
    import asyncio
    from app.services.job_manager import JobManager
    manager = JobManager.get_instance()
    main_loop = getattr(manager, "loop", None)
    coro = manager.fail_job(project_id, reason)

    if main_loop and main_loop.is_running():
        from asyncio import run_coroutine_threadsafe
        future = run_coroutine_threadsafe(coro, main_loop)
        try:
            future.result(timeout=5.0)
        except Exception:
            pass
    else:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        if loop.is_running():
            from asyncio import run_coroutine_threadsafe
            future = run_coroutine_threadsafe(coro, loop)
            try:
                future.result(timeout=5.0)
            except Exception:
                pass
        else:
            try:
                loop.run_until_complete(coro)
            except Exception:
                pass

@router.get("/diagnostics")
async def get_diagnostics(current_user: Optional[AuthUser] = Depends(get_optional_user)):
    """Exposes deep health diagnostics of the ML environment, active jobs registry, and connectivity."""
    import psutil
    import os
    import gc
    from app.services.model_service import is_loaded, get_model_name
    from app.services.job_manager import JobManager
    
    # 1. Model status
    model_loaded = is_loaded()
    model_name = get_model_name() or settings.embedding_model
    
    # 2. System Usage
    proc = psutil.Process(os.getpid())
    rss_mb = proc.memory_info().rss / (1024 * 1024)
    cpu_pct = proc.cpu_percent(interval=0.1)
    threads_count = proc.num_threads()
    
    # 3. Active jobs registry
    job_manager = JobManager.get_instance()
    # Probe to trigger active jobs pruning
    await job_manager.is_job_active("_non_existent_probe_")
    active_jobs = dict(job_manager._progress_cache)  # clone active jobs
    active_jobs_count = len(active_jobs)
    
    # 4. Queue length
    queue_len = 0
    try:
        res = supabase_client.table("background_jobs").select("id", count="exact").eq("status", "queued").execute()
        queue_len = res.count if hasattr(res, "count") else len(res.data)
    except Exception:
        pass
        
    # 5. Connectivity checks
    supabase_ok = False
    try:
        supabase_client.table("projects").select("id").limit(1).execute()
        supabase_ok = True
    except Exception:
        pass
        
    storage_ok = False
    try:
        from app.services.storage_provider import StorageService
        StorageService.file_exists("candidate-files", "_startup_probe")
        storage_ok = True
    except Exception:
        pass
        
    faiss_ok = False
    try:
        import faiss
        faiss_ok = True
    except Exception:
        pass
        
    return {
        "model_loaded": model_loaded,
        "embedding_model": model_name,
        "system": {
            "memory_usage_mb": round(rss_mb, 1),
            "cpu_usage_percent": cpu_pct,
            "thread_count": threads_count,
            "pid": os.getpid()
        },
        "worker": {
            "active_jobs_count": active_jobs_count,
            "active_jobs": active_jobs,
            "queue_length": queue_len
        },
        "connectivity": {
            "supabase_db": supabase_ok,
            "supabase_storage": storage_ok
        },
        "ml_libraries": {
            "faiss_available": faiss_ok
        }
    }


# Watchdog run — marks jobs whose heartbeat is older than 2 minutes as failed
def _run_worker_watchdog():
    WATCHDOG_TIMEOUT_MINUTES = float(os.environ.get("WATCHDOG_TIMEOUT_MINUTES", "2"))
    try:
        res = supabase_client.table("background_jobs").select("*").in_("status", ["queued", "processing", "embedding", "indexing", "retrying"]).execute()
        now_dt = datetime.now(timezone.utc)
        for job in res.data:
            updated_at_str = job.get("last_heartbeat") or job.get("updated_at") or job.get("started_at")
            if updated_at_str:
                updated_at_dt = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                elapsed_min = (now_dt - updated_at_dt).total_seconds() / 60.0
                if elapsed_min > WATCHDOG_TIMEOUT_MINUTES:
                    project_id = job["project_id"]
                    logger.warning(
                        "[WATCHDOG] job=%s project=%s status=%s heartbeat_age=%.1f min > threshold=%.1f min — marking failed",
                        job["id"], project_id, job["status"], elapsed_min, WATCHDOG_TIMEOUT_MINUTES,
                    )
                    supabase_client.table("background_jobs").update({
                        "status": "failed",
                        "failure_reason": f"Watchdog timeout: no heartbeat for {elapsed_min:.1f} minutes (threshold {WATCHDOG_TIMEOUT_MINUTES:.0f} min).",
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", job["id"]).execute()

                    supabase_client.table("projects").update({
                        "embedding_status": "failed",
                        "status": "failed",
                        "updated_at": _now()
                    }).eq("id", project_id).execute()
    except Exception as exc:
        logger.error("[WATCHDOG] Error in watchdog: %s", exc)


@router.get("/projects/{project_id}/worker-status")
async def get_worker_status(project_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    _run_worker_watchdog()
    proj_res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.services.job_manager import DBConnection
    import uuid
    import time as _time
    from datetime import datetime, timezone

    async with DBConnection() as conn:
        job = await conn.fetchrow(
            """
            SELECT id, job_type, current_stage, progress_percentage, status, failure_reason, retry_count, 
                   started_at, last_heartbeat, processed_candidates, total_candidates, ram_usage, peak_ram, eta, speed
            FROM public.background_jobs
            WHERE project_id = $1
            ORDER BY started_at DESC
            LIMIT 1
            """,
            uuid.UUID(project_id)
        )

    if job:
        started_at = job["started_at"]
        elapsed_time = 0.0
        if started_at:
            try:
                if isinstance(started_at, str):
                    started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                else:
                    started_dt = started_at
                elapsed_time = (datetime.now(timezone.utc) - started_dt).total_seconds()
            except Exception:
                pass

        status_info = {
            "job_id": str(job["id"]),
            "project_id": project_id,
            "job_type": job["job_type"],
            "current_stage": job["current_stage"],
            "progress_percentage": job["progress_percentage"],
            "status": job["status"],
            "failure_reason": job["failure_reason"],
            "retry_count": job["retry_count"],
            "last_heartbeat": job["last_heartbeat"].isoformat() if job["last_heartbeat"] else None,
            "processed_candidates": job["processed_candidates"] or 0,
            "total_candidates": job["total_candidates"] or 0,
            "ram_usage": job["ram_usage"] or 0.0,
            "peak_ram": job["peak_ram"] or 0.0,
            "eta": job["eta"] or "00:00:00",
            "speed": job["speed"] or 0.0,
            "elapsed_time": elapsed_time,
        }
    else:
        status_info = {
            "status": "idle",
            "current_stage": "Not Started",
            "progress_percentage": 0,
            "processed_candidates": 0,
            "total_candidates": 0,
            "ram_usage": 0.0,
            "peak_ram": 0.0,
            "eta": "00:00:00",
            "speed": 0.0,
            "failure_reason": None,
            "retry_count": 0,
            "elapsed_time": 0.0,
        }
    return status_info


@router.get("/projects/{project_id}/diagnostics")
async def get_project_diagnostics(project_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    
    # Check project state
    proj_res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")
    project = proj_res.data[0]
    
    # Query database jobs for this project
    from app.services.job_manager import DBConnection
    import uuid
    from datetime import datetime, timezone
    
    async with DBConnection() as conn:
        job = await conn.fetchrow(
            """
            SELECT id, status, current_stage, started_at, updated_at, last_heartbeat, 
                   retry_count, failure_reason, owner_id, lease_expires_at,
                   processed_candidates, total_candidates, ram_usage, peak_ram, eta, speed
            FROM public.background_jobs
            WHERE project_id = $1
            ORDER BY started_at DESC
            LIMIT 1
            """,
            uuid.UUID(project_id)
        )
        
    diagnostics = {
        "project_id": project_id,
        "project_name": project.get("name"),
        "project_status": project.get("status"),
        "project_embedding_status": project.get("embedding_status"),
        "active_job": None,
        "blocking_reason": None
    }
    
    if job:
        now = datetime.now(timezone.utc)
        lease_expires = job["lease_expires_at"]
        lease_expired = lease_expires < now if lease_expires else True
        last_hb = job["last_heartbeat"]
        seconds_since_hb = (now - last_hb).total_seconds() if last_hb else None
        
        diagnostics["active_job"] = {
            "job_id": str(job["id"]),
            "job_status": job["status"],
            "current_stage": job["current_stage"],
            "owner_id": job["owner_id"],
            "lease_expires_at": lease_expires.isoformat() if lease_expires else None,
            "lease_expired": lease_expired,
            "seconds_since_heartbeat": seconds_since_hb,
            "started_at": job["started_at"].isoformat() if job["started_at"] else None,
            "updated_at": job["updated_at"].isoformat() if job["updated_at"] else None,
            "last_heartbeat": last_hb.isoformat() if last_hb else None,
            "retry_count": job["retry_count"],
            "failure_reason": job["failure_reason"],
            "processed_candidates": job["processed_candidates"],
            "total_candidates": job["total_candidates"],
            "ram_usage": job["ram_usage"],
            "peak_ram": job["peak_ram"],
            "eta": job["eta"],
            "speed": job["speed"],
        }
        
        # Blocked analysis checks
        is_active = job["status"] not in ("completed", "failed", "cancelled")
        if is_active:
            if lease_expired:
                diagnostics["blocking_reason"] = "Job is inactive (lease expired). Can be claimed by any worker."
            else:
                diagnostics["blocking_reason"] = f"Job is active. Owned by worker {job['owner_id']} (lease expires in {round((lease_expires - now).total_seconds(), 1)}s)."
        else:
            diagnostics["blocking_reason"] = "No active background job for this project."
    else:
        diagnostics["blocking_reason"] = "No background job record found in database for this project."
        
    return diagnostics


@router.get("/projects/{project_id}/progress-stream")
async def get_progress_stream(project_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    from fastapi.responses import StreamingResponse
    user_id = get_user_id(current_user)
    proj_res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    async def event_generator():
        import logging as _log
        import uuid
        from datetime import datetime, timezone
        from app.services.job_manager import DBConnection
        _logger = _log.getLogger(__name__)
        last_heartbeat = asyncio.get_event_loop().time()
        HEARTBEAT_INTERVAL = 5.0
        TERMINAL_STATES = {"completed", "failed", "cancelled"}

        while True:
            try:
                async with DBConnection() as conn:
                    job = await conn.fetchrow(
                        """
                        SELECT id, job_type, current_stage, progress_percentage, status, failure_reason, retry_count, 
                               last_heartbeat, processed_candidates, total_candidates, ram_usage, peak_ram, eta, speed, started_at
                        FROM public.background_jobs
                        WHERE project_id = $1
                        ORDER BY started_at DESC
                        LIMIT 1
                        """,
                        uuid.UUID(project_id)
                    )

                if job:
                    elapsed_seconds = int((datetime.now(timezone.utc) - job["started_at"]).total_seconds()) if job["started_at"] else 0
                    status_info = {
                        "job_id": str(job["id"]),
                        "project_id": project_id,
                        "job_type": job["job_type"],
                        "current_stage": job["current_stage"],
                        "progress_percentage": job["progress_percentage"],
                        "status": job["status"],
                        "failure_reason": job["failure_reason"],
                        "retry_count": job["retry_count"],
                        "last_heartbeat": job["last_heartbeat"].isoformat() if job["last_heartbeat"] else None,
                        "processed_candidates": job["processed_candidates"] or 0,
                        "total_candidates": job["total_candidates"] or 0,
                        "ram_usage": job["ram_usage"] or 0.0,
                        "peak_ram": job["peak_ram"] or 0.0,
                        "eta": job["eta"] or "00:00:00",
                        "speed": job["speed"] or 0.0,
                        # Requirement 13 mapped fields
                        "progress": job["progress_percentage"],
                        "stage": job["current_stage"],
                        "candidate_count": job["total_candidates"] or 0,
                        "elapsed_seconds": elapsed_seconds,
                        "elapsed_time": elapsed_seconds,
                    }
                else:
                    status_info = {
                        "status": "idle",
                        "current_stage": "Not Started",
                        "progress_percentage": 0,
                        "processed_candidates": 0,
                        "total_candidates": 0,
                        "ram_usage": 0.0,
                        "peak_ram": 0.0,
                        "eta": "00:00:00",
                        "speed": 0.0,
                        "failure_reason": None,
                        "retry_count": 0,
                        "progress": 0,
                        "stage": "Not Started",
                        "candidate_count": 0,
                        "elapsed_seconds": 0,
                        "elapsed_time": 0,
                    }

                data_json = json.dumps(status_info)
                yield f"data: {data_json}\n\n"

                current_status = status_info.get("status", "idle")
                if current_status in TERMINAL_STATES:
                    _logger.info("[SSE] project=%s sending terminal event status=%s progress=%s",
                                 project_id, current_status, status_info.get("progress_percentage"))
                    yield f"data: {data_json}\n\n"
                    break

                now = asyncio.get_event_loop().time()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now

            except Exception as sse_exc:
                _logger.error("[SSE_INFRASTRUCTURE_ERROR] project=%s event_generator error: %s", project_id, sse_exc)
                # Send a failed event before closing so the frontend knows
                error_payload = json.dumps({
                    "status": "failed",
                    "current_stage": "SSE Connection Interrupted",
                    "stage": "SSE Connection Interrupted",
                    "progress_percentage": 0,
                    "progress": 0,
                    "processed_candidates": 0,
                    "total_candidates": 0,
                    "candidate_count": 0,
                    "eta": "00:00:00",
                    "ram_usage": 0.0,
                    "peak_ram": 0.0,
                    "failure_reason": "SSE Infrastructure Error",
                    "elapsed_seconds": 0,
                    "elapsed_time": 0,
                })
                yield f"data: {error_payload}\n\n"
                break

            await asyncio.sleep(2.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx buffering on Render
        },
    )


@router.post("/projects/{project_id}/cancel-indexing")
async def cancel_indexing(project_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    proj_res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.services.job_manager import JobManager
    manager = JobManager.get_instance()
    await manager.cancel_job(project_id)
    
    supabase_client.table("projects").update({
        "embedding_status": "failed",
        "status": "failed",
        "updated_at": _now()
    }).eq("id", project_id).execute()
    
    return {"status": "cancelled", "message": "Indexing cancellation requested."}


@router.post("/projects/{project_id}/retry-indexing")
async def retry_indexing(
    project_id: str,
    background_tasks: BackgroundTasks,
    current_user: Optional[AuthUser] = Depends(get_optional_user),
):
    """
    Retry indexing for a project whose previous indexing run failed.

    - Requires the project to be in embedding_status='failed'.
    - Reuses the already-uploaded candidate file — no re-upload required.
    - Resets the project back to embedding_status='queued' and kicks off a
      fresh indexing background task.
    - Returns 409 if the project is already indexing or completed.
    - Returns 400 if no candidate file exists to index.
    """
    user_id = get_user_id(current_user)
    proj_res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")
    p = proj_res.data[0]

    embedding_status = p.get("embedding_status", "pending")

    # Guard: only retry if not already active
    from app.services.job_manager import JobManager
    job_manager_inst = JobManager.get_instance()
    is_active = False
    try:
        is_active = await job_manager_inst.is_job_active(project_id)
    except Exception:
        pass

    if is_active or embedding_status in ("queued", "processing"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Indexing already running",
        )
    if embedding_status == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Indexing already completed successfully. Run analysis to use the results.",
        )

    # Guard: must have a candidate file to reindex
    current_path = p.get("current_candidate_path")
    if not current_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No candidate file found for this project. Please upload candidates first.",
        )

    logger.info("[RETRY_INDEXING] project=%s user=%s current_path=%s", project_id, user_id[:8], current_path)

    # Reset project state to queued so the background task can run cleanly
    supabase_client.table("projects").update({
        "embedding_status": "queued",
        "status": "INDEXING",
        "updated_at": _now(),
    }).eq("id", project_id).execute()

    # Register a fresh background_jobs row
    from app.services.job_manager import JobManager
    manager = JobManager.get_instance()
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    coro = manager.register_job(project_id, user_id, "indexing")
    if loop.is_running():
        from asyncio import run_coroutine_threadsafe
        fut = run_coroutine_threadsafe(coro, loop)
        try:
            job_id = fut.result(timeout=10.0)
        except Exception:
            job_id = None
    else:
        job_id = loop.run_until_complete(coro)

    _sync_update_progress(project_id, "Upload received", 5, status="queued")

    # Kick off fresh indexing in background — reuses current_candidate_path from DB
    background_tasks.add_task(
        _safe_background_task,
        "retry_indexing",
        process_project_data_task,
        project_id,
    )

    logger.info("[RETRY_INDEXING] project=%s job_id=%s indexing restarted", project_id, job_id)
    return {
        "status": "queued",
        "message": "Indexing restarted using existing candidate file. No re-upload needed.",
        "job_id": job_id,
        "project_id": project_id,
    }


@router.get("/projects")
async def list_projects(current_user: Optional[AuthUser] = Depends(get_optional_user)):
    import traceback as _tb
    import threading as _thr
    _t0 = time.time()
    _tid = _thr.get_ident()
    _pid = os.getpid()
    logger.info("[REQUEST_RECEIVED] GET /projects pid=%d tid=%d", _pid, _tid)
    try:
        user_id = get_user_id(current_user)
        logger.info("[AUTH_VERIFIED] GET /projects user=%s elapsed=%.3fs", user_id[:8], time.time() - _t0)
        logger.info("[USER_RESOLVED] GET /projects user=%s elapsed=%.3fs", user_id[:8], time.time() - _t0)

        logger.info("[QUERY_STARTED] GET /projects elapsed=%.3fs", time.time() - _t0)
        res = supabase_client.table("projects").select("*").eq("user_id", user_id).execute()
        logger.info("[SUPABASE_RESPONSE] GET /projects rows=%d elapsed=%.3fs",
                    len(res.data) if res.data else 0, time.time() - _t0)

        data = res.data or []
        logger.info("[SERIALIZATION] GET /projects rows=%d elapsed=%.3fs", len(data), time.time() - _t0)
        logger.info("[RESPONSE_SENT] GET /projects elapsed=%.3fs", time.time() - _t0)
        return data

    except HTTPException:
        raise
    except Exception as exc:
        tb_str = _tb.format_exc()
        rss = get_memory_mb()
        logger.error(
            "[REQUEST_EXCEPTION] GET /projects elapsed=%.3fs rss=%.1fMB tid=%d\n"
            "Exception: %s\nTraceback:\n%s",
            time.time() - _t0, rss, _tid, exc, tb_str,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": f"Internal error listing projects: {exc}",
                     "traceback": tb_str},
        )


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    import logging
    logger = logging.getLogger(__name__)
    user_id = get_user_id(current_user)

    dup = supabase_client.table("projects").select("id").eq("user_id", user_id).eq("name", body.name).execute()
    if dup.data:
        _health_stats["duplicate_projects_prevented"] += 1
        res = supabase_client.table("projects").select("*").eq("id", dup.data[0]["id"]).execute()
        return res.data[0]

    pid = str(uuid.uuid4())
    now = _now()
    project = {
        "id": pid,
        "user_id": user_id,
        "name": body.name,
        "description": body.description or "",
        "status": "draft",
        "candidate_count": 0,
        "job_count": 0,
        "project_hash": body.project_hash,
        "embedding_status": "ready",
        "upload_statistics": {},
        "created_at": now,
        "updated_at": now,
    }
    
    supabase_client.table("projects").insert(project).execute()
    return project


@router.get("/projects/{project_id}")
async def get_project(project_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Project not found")
    return res.data[0]


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    existing = supabase_client.table("projects").select("id").eq("id", project_id).eq("user_id", user_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = {}
    if body.name is not None:
        update_data["name"] = body.name
    if body.description is not None:
        update_data["description"] = body.description
    if body.status is not None:
        update_data["status"] = body.status

    if not update_data:
        return existing.data[0]

    update_data["updated_at"] = _now()
    res = supabase_client.table("projects").update(update_data).eq("id", project_id).execute()
    return res.data[0]


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    existing = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Project not found")
    p = existing.data[0]
    version = p.get("version") or 1

    uploads_res = supabase_client.table("candidate_uploads").select("storage_path").eq("project_id", project_id).execute()
    supabase_client.table("projects").delete().eq("id", project_id).execute()

    from app.services.storage_provider import StorageService
    for upload in uploads_res.data:
        try:
            StorageService.delete_file("candidate-files", upload["storage_path"])
        except Exception:
            pass

    for v in range(1, version + 1):
        try:
            StorageService.delete_file("embeddings", f"{project_id}/embeddings_v{v}.npy")
            StorageService.delete_file("faiss-indexes", f"{project_id}/faiss_v{v}.index")
            StorageService.delete_file("embeddings", f"{project_id}/ids_v{v}.json")
            StorageService.delete_file("skill-indexes", f"{project_id}/skill_index_v{v}.json")
            for cat in ["MLOPS", "DEVOPS", "DATA_ENGINEERING", "DATA_SCIENCE", "AI_ML", "FRONTEND", "PROJECT_MANAGEMENT", "PRODUCT_MANAGEMENT", "DESIGN", "MARKETING", "HR", "BACKEND"]:
                StorageService.delete_file("role-indexes", f"{project_id}/role_{cat.upper()}_v{v}.jsonl")
        except Exception:
            pass

    from app.services.cache_service import CacheService
    CacheService.invalidate_project(project_id)


# ── File Upload ───────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/upload")
async def upload_file(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    upload_type: str = "candidates",
    title: Optional[str] = None,
    openings: Optional[int] = None,
    shortlist_size: Optional[int] = None,
    priority: Optional[str] = None,
    min_match_percent: Optional[float] = None,
    salary_range: Optional[str] = None,
    job_location: Optional[str] = None,
    employment_type: Optional[str] = None,
    current_user: Optional[AuthUser] = Depends(get_optional_user),
):
    """
    Upload a candidate dataset or job description.

    SAFETY CONTRACT:
    - Never loads SentenceTransformer / FAISS / OpenRouter on this path.
    - LLM parsing is deferred to a background task.
    - Every exception returns JSON; nothing propagates to kill the worker.
    - Returns HTTP 200 within 2 seconds (all heavy work is backgrounded).
    """
    import traceback as _tb
    import threading as _thr

    _t0 = time.time()
    _rss0 = get_memory_mb()
    _tid = _thr.get_ident()
    _pid = os.getpid()

    def _elapsed() -> float:
        return time.time() - _t0

    def _rss() -> float:
        return get_memory_mb()

    def _rss_delta() -> float:
        return _rss() - _rss0

    logger.info(
        "[UPLOAD_REQUEST_RECEIVED] project=%s type=%s file=%s pid=%d tid=%d rss=%.1fMB",
        project_id, upload_type, file.filename, _pid, _tid, _rss0,
    )

    try:
        # ── AUTH / PROJECT VERIFY ─────────────────────────────────────────────
        user_id = get_user_id(current_user)
        logger.info("[AUTH_VERIFIED] project=%s user=%s elapsed=%.3fs", project_id, user_id[:8], _elapsed())

        proj_res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
        if not proj_res.data:
            logger.warning("[PROJECT_NOT_FOUND] project=%s elapsed=%.3fs", project_id, _elapsed())
            raise HTTPException(status_code=404, detail="Project not found")
        p = proj_res.data[0]
        logger.info("[PROJECT_VERIFIED] project=%s elapsed=%.3fs rss=%.1fMB", project_id, _elapsed(), _rss())

        filename = file.filename or "upload"
        logger.info("[FILE_RECEIVED] project=%s filename=%s type=%s elapsed=%.3fs", project_id, filename, upload_type, _elapsed())

        # ── GUARD: never call model/FAISS/OpenRouter inline ───────────────────
        # Any code path that reaches _get_encoder() or parse_jd_with_llm()
        # synchronously on this coroutine is a bug and will be caught here.

        # ── BRANCH: candidates ────────────────────────────────────────────────
        if upload_type == "candidates":
            # Guard: check if indexing is already active
            from app.services.job_manager import JobManager
            job_manager_inst = JobManager.get_instance()
            is_active = False
            try:
                is_active = await job_manager_inst.is_job_active(project_id)
            except Exception:
                pass
            if is_active or p.get("embedding_status") in ("queued", "processing"):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Indexing already running",
                )

            rss_after_file = _rss()
            logger.info("[FILE_PARSED_START] project=%s rss=%.1fMB elapsed=%.3fs", project_id, rss_after_file, _elapsed())

            try:
                # Save raw file to disk — no parsing on the request path
                temp_raw_dir = Path("data/temp_raw")
                temp_raw_dir.mkdir(parents=True, exist_ok=True)
                temp_raw_path = temp_raw_dir / f"raw_{project_id}_{uuid.uuid4().hex[:8]}{Path(filename).suffix}"
                raw_bytes = await file.read()
                temp_raw_path.write_bytes(raw_bytes)
                file_size_kb = len(raw_bytes) / 1024
                del raw_bytes  # release immediately

                logger.info(
                    "[FILE_PARSED] project=%s size=%.1fKB rss=%.1fMB delta=%.1fMB elapsed=%.3fs",
                    project_id, file_size_kb, _rss(), _rss_delta(), _elapsed(),
                )

                if _rss_delta() > 50:
                    logger.warning(
                        "[UPLOAD_MEMORY_SPIKE] project=%s rss_delta=%.1fMB after file read",
                        project_id, _rss_delta(),
                    )

            except Exception as exc:
                logger.exception("[UPLOAD_FATAL_EXCEPTION] project=%s stage=file_read error=%s", project_id, exc)
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"Could not read uploaded file: {exc}", "stage": "file_read",
                             "traceback": _tb.format_exc()},
                )

            # ── Schedule heavy candidate processing in background ─────────────
            logger.info("[BACKGROUND_TASK_SCHEDULED] project=%s task=process_candidate_upload elapsed=%.3fs", project_id, _elapsed())
            background_tasks.add_task(
                _safe_background_task,
                "process_candidate_upload",
                process_candidate_upload_task,
                project_id,
                user_id,
                str(temp_raw_path),
                filename,
            )

            elapsed_total = _elapsed()
            logger.info(
                "[UPLOAD_RESPONSE_SENT] project=%s type=candidates elapsed=%.3fs rss=%.1fMB",
                project_id, elapsed_total, _rss(),
            )
            if elapsed_total > 2.0:
                logger.warning(
                    "[UPLOAD_SLOW] project=%s elapsed=%.3fs exceeded 2s target",
                    project_id, elapsed_total,
                )
            return {
                "status": "queued",
                "message": f"Candidate file received. Processing in background.",
                "filename": filename,
            }

        # ── BRANCH: job_description ───────────────────────────────────────────
        elif upload_type == "job_description":
            try:
                content = await file.read()
                logger.info(
                    "[FILE_PARSED] project=%s size=%.1fKB rss=%.1fMB elapsed=%.3fs",
                    project_id, len(content) / 1024, _rss(), _elapsed(),
                )
            except Exception as exc:
                logger.exception("[UPLOAD_FATAL_EXCEPTION] project=%s stage=file_read error=%s", project_id, exc)
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"Could not read JD file: {exc}", "traceback": _tb.format_exc()},
                )

            # Extract raw text synchronously (local CPU only — no network, no model)
            try:
                raw_text = _extract_jd_raw_text(content, filename)
                del content  # release bytes immediately
                if not raw_text:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Unsupported or unreadable job description format.",
                    )
                logger.info(
                    "[FILE_PARSED] project=%s raw_text_len=%d rss=%.1fMB elapsed=%.3fs",
                    project_id, len(raw_text), _rss(), _elapsed(),
                )
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception("[UPLOAD_FATAL_EXCEPTION] project=%s stage=text_extract error=%s", project_id, exc)
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"Could not extract JD text: {exc}", "traceback": _tb.format_exc()},
                )

            # ── Immediate parse with fast regex fallback (no LLM on hot path) ──
            # Use parse_jd_backup for instant skills/experience extraction.
            # LLM enrichment is dispatched to a background task after response.
            try:
                quick_parsed = parse_jd_backup(raw_text)
            except Exception:
                quick_parsed = {}

            required_skills = quick_parsed.get("must_have_skills", [])
            min_experience = float((quick_parsed.get("experience_years") or {}).get("min") or 0.0)

            jid = str(uuid.uuid4())
            job = {
                "id": jid,
                "project_id": project_id,
                "title": title or quick_parsed.get("title") or "Job Description",
                "description": raw_text,
                "company": "Company",
                "location": "Remote",
                "work_mode": "Remote",
                "required_skills": required_skills,
                "nice_to_have_skills": quick_parsed.get("nice_to_have_skills", []),
                "min_experience": min_experience,
                "preferred_locations": quick_parsed.get("preferred_locations", []),
                "openings": openings or 5,
                "shortlist_size": shortlist_size or 15,
                "priority": priority or "balanced",
                "min_match_percent": min_match_percent,
                "salary_range": salary_range,
                "job_location": job_location,
                "employment_type": employment_type or "Full-time",
                "created_at": _now(),
            }

            # ── DB writes ─────────────────────────────────────────────────────
            try:
                logger.info("[SUPABASE_UPLOAD_STARTED] project=%s stage=jobs_insert elapsed=%.3fs", project_id, _elapsed())
                with log_call("supabase", "jobs.insert", project_id=project_id):
                    supabase_client.table("jobs").insert(job).execute()
                logger.info("[SUPABASE_UPLOAD_FINISHED] project=%s stage=jobs_insert elapsed=%.3fs rss=%.1fMB", project_id, _elapsed(), _rss())

                with log_call("supabase", "jobs.count", project_id=project_id):
                    count_res = supabase_client.table("jobs").select("id", count="exact").eq("project_id", project_id).execute()
                with log_call("supabase", "projects.update_job_count", project_id=project_id):
                    supabase_client.table("projects").update({
                        "job_count": count_res.count or 1,
                        "status": "uploaded" if p.get("status") in ("CREATED", "draft") and p.get("candidate_count", 0) > 0 else p.get("status"),
                        "updated_at": _now(),
                    }).eq("id", project_id).execute()
            except Exception as exc:
                logger.exception("[UPLOAD_FATAL_EXCEPTION] project=%s stage=db_insert error=%s", project_id, exc)
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"Database error during JD upload: {exc}", "traceback": _tb.format_exc()},
                )

            # ── Dispatch LLM enrichment to background ─────────────────────────
            logger.info("[BACKGROUND_JOB_CREATED] project=%s job=%s elapsed=%.3fs", project_id, jid, _elapsed())
            background_tasks.add_task(
                _safe_background_task,
                "process_jd_llm",
                process_jd_llm_background_task,
                project_id,
                jid,
                raw_text,
            )
            logger.info("[BACKGROUND_TASK_SCHEDULED] project=%s task=process_jd_llm elapsed=%.3fs", project_id, _elapsed())

            elapsed_total = _elapsed()
            logger.info(
                "[UPLOAD_RESPONSE_SENT] project=%s type=job_description elapsed=%.3fs rss=%.1fMB",
                project_id, elapsed_total, _rss(),
            )
            if elapsed_total > 2.0:
                logger.warning("[UPLOAD_SLOW] project=%s elapsed=%.3fs exceeded 2s target", project_id, elapsed_total)
            return job

        else:
            raise HTTPException(status_code=400, detail=f"Unknown upload_type: {upload_type}")

    except HTTPException:
        raise  # let FastAPI handle 4xx normally
    except Exception as exc:
        tb_str = _tb.format_exc()
        logger.error(
            "[UPLOAD_FATAL_EXCEPTION] project=%s elapsed=%.3fs rss=%.1fMB\n"
            "Exception: %s\nTraceback:\n%s",
            project_id, _elapsed(), _rss(), exc, tb_str,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"Upload failed with internal error: {exc}",
                "stage": "upload_handler",
                "traceback": tb_str,
            },
        )


@router.get("/projects/{project_id}/candidates")
async def list_candidates(
    project_id: str,
    page: int = 1,
    page_size: int = 50,
    search: str = "",
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    res = supabase_client.table("projects").select("current_candidate_path").eq("id", project_id).eq("user_id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    current_path = res.data[0].get("current_candidate_path")
    if not current_path:
        return {
            "total": 0,
            "page": page,
            "page_size": page_size,
            "pages": 1,
            "candidates": [],
        }

    bucket, path = current_path.split("/", 1)
    total = 0
    matched_rows = []
    q = search.lower() if search else None

    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size

    from app.services.storage_provider import StorageService
    for c_raw in StorageService.stream_jsonl(bucket, path):
        c = standardize_candidate(c_raw)
        profile = c.get("profile", {})
        signals = c.get("redrob_signals", {})
        skills = c.get("skills", [])
        
        candidate_id = c.get("candidate_id", "")
        name = profile.get("anonymized_name", "—")
        current_title = profile.get("current_title", "—")
        current_company = profile.get("current_company", "—")
        location = profile.get("location", "—")
        top_skills = [s.get("name", "") for s in skills[:5] if s.get("name")]

        match = True
        if q:
            match = (
                q in name.lower()
                or q in current_title.lower()
                or q in current_company.lower()
                or q in location.lower()
                or q in candidate_id.lower()
                or any(q in r.lower() for r in top_skills)
            )

        if match:
            if total >= start_idx and total < end_idx:
                matched_rows.append({
                    "candidate_id": candidate_id,
                    "name": name,
                    "current_title": current_title,
                    "current_company": current_company,
                    "location": location,
                    "years_of_experience": profile.get("years_of_experience", 0),
                    "top_skills": top_skills,
                    "open_to_work": signals.get("open_to_work_flag", False),
                    "notice_period_days": signals.get("notice_period_days", None),
                    "profile_completeness": signals.get("profile_completeness_score", 0),
                })
            total += 1

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "candidates": matched_rows,
    }


@router.get("/projects/{project_id}/candidates/{candidate_id}")
async def get_candidate(
    project_id: str,
    candidate_id: str,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    res = supabase_client.table("projects").select("current_candidate_path").eq("id", project_id).eq("user_id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    current_path = res.data[0].get("current_candidate_path")
    if not current_path:
        raise HTTPException(status_code=404, detail="Candidate not found")

    bucket, path = current_path.split("/", 1)
    from app.services.storage_provider import StorageService
    for c in StorageService.stream_jsonl(bucket, path):
        if c.get("candidate_id") == candidate_id:
            return standardize_candidate(c)

    raise HTTPException(status_code=404, detail="Candidate not found")


@router.get("/projects/{project_id}/jobs")
async def list_jobs(project_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    proj = supabase_client.table("projects").select("id").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj.data:
        raise HTTPException(status_code=404, detail="Project not found")

    res = supabase_client.table("jobs").select("*").eq("project_id", project_id).execute()
    return res.data


@router.post("/projects/{project_id}/jobs", status_code=status.HTTP_201_CREATED)
async def create_job(
    project_id: str,
    body: JobCreate,
    current_user: Optional[AuthUser] = Depends(get_optional_user),
):
    """Create a job by pasting/typing raw JD text. Parses requirements with LLM fallback."""
    import logging
    logger = logging.getLogger(__name__)
    user_id = get_user_id(current_user)
    proj = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj.data:
        raise HTTPException(status_code=404, detail="Project not found")
    p = proj.data[0]

    # Parse the raw description text for structured fields
    raw_text = body.description or ""
    required_skills = body.required_skills or []
    min_experience = body.min_experience or 0.0

    # If no skills provided, try to extract them from the description text
    if not required_skills and raw_text:
        llm_parsed: dict = {}
        try:
            from app.core.openrouter import parse_jd_with_llm
            llm_parsed = await parse_jd_with_llm(raw_text)
        except Exception:
            try:
                llm_parsed = parse_jd_backup(raw_text)
            except Exception:
                pass
        if llm_parsed:
            required_skills = llm_parsed.get("must_have_skills", [])
            if not min_experience:
                min_experience = float(llm_parsed.get("experience_years", {}).get("min") or 0.0)

    jid = str(uuid.uuid4())
    job = {
        "id": jid,
        "project_id": project_id,
        "title": body.title,
        "description": raw_text,
        "company": body.company or "Company",
        "location": body.location or "Remote",
        "work_mode": body.work_mode or "Remote",
        "required_skills": required_skills,
        "nice_to_have_skills": [],
        "min_experience": min_experience,
        "preferred_locations": [],
        "openings": body.openings or 5,
        "shortlist_size": body.shortlist_size or 15,
        "priority": body.priority or "balanced",
        "min_match_percent": body.min_match_percent,
        "salary_range": body.salary_range,
        "job_location": body.job_location,
        "employment_type": body.employment_type or "Full-time",
        "created_at": _now(),
    }

    try:
        with log_call("supabase", "jobs.insert", project_id=project_id):
            supabase_client.table("jobs").insert(job).execute()

        # Update project job count
        with log_call("supabase", "jobs.count", project_id=project_id):
            job_count_res = supabase_client.table("jobs").select("id", count="exact").eq("project_id", project_id).execute()
        with log_call("supabase", "projects.update_job_count", project_id=project_id):
            supabase_client.table("projects").update({
                "job_count": job_count_res.count or 1,
                "updated_at": _now(),
            }).eq("id", project_id).execute()
    except Exception as exc:
        import traceback as _tb
        logger.exception(
            "[UPLOAD_FATAL_EXCEPTION] project=%s stage=create_job_db exception_type=%s exception=%s\n%s",
            project_id, type(exc).__name__, exc, _tb.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": f"Database error creating job: {exc}", "traceback": _tb.format_exc()},
        )

    logger.info("Created job %s for project %s via text input", jid, project_id)
    return job


# ── AI Analysis ───────────────────────────────────────────────────────────────

import time as _time
_backend_ranking_cache: dict[str, dict] = {}
_CACHE_TTL = 2 * 24 * 60 * 60

def _get_cached_ranking(project_id: str, job_id: str) -> dict | None:
    key = f"{project_id}:{job_id}"
    entry = _backend_ranking_cache.get(key)
    if not entry:
        return None
    if _time.time() - entry["cached_at"] > _CACHE_TTL:
        del _backend_ranking_cache[key]
        return None
    return entry["ranking"]

def _set_cached_ranking(project_id: str, job_id: str, ranking: dict) -> None:
    key = f"{project_id}:{job_id}"
    _backend_ranking_cache[key] = {"ranking": ranking, "cached_at": _time.time()}

def normalize_role_category(cat: str) -> str:
    if not cat:
        return "BACKEND"
    cat_clean = str(cat).strip().upper()
    if cat_clean in {"MLOPS", "DEVOPS", "DATA_ENGINEERING", "DATA_SCIENCE", "AI_ML", "FRONTEND", "PROJECT_MANAGEMENT", "PRODUCT_MANAGEMENT", "DESIGN", "MARKETING", "HR", "BACKEND"}:
        return cat_clean
    mapping = {
        "BACKEND ENGINEER": "BACKEND",
        "DEVOPS ENGINEER": "DEVOPS",
        "PLATFORM ENGINEER": "DEVOPS",
        "MLOPS ENGINEER": "MLOPS",
        "ML ENGINEER": "AI_ML",
        "RETRIEVAL ENGINEER": "AI_ML",
        "SEARCH ENGINEER": "AI_ML",
        "RECOMMENDATION SYSTEMS ENGINEER": "AI_ML",
        "DATA SCIENTIST": "DATA_SCIENCE",
        "FRONTEND ENGINEER": "FRONTEND",
        "PROJECT MANAGER": "PROJECT_MANAGEMENT",
        "OPERATIONS MANAGER": "PROJECT_MANAGEMENT",
    }
    return mapping.get(cat_clean, "BACKEND")


async def is_project_analysis_ready(project_id: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Canonical readiness check for project analysis.
    Verifies:
      1. Candidate upload exists.
      2. Indexing status is completed.
      3. Required artifacts exist.
      4. Artifact version matches current candidate upload.
      5. No active indexing replacement job exists.
    """
    import uuid
    from app.services.storage_provider import StorageService

    try:
        # 1. Fetch project
        proj_res = supabase_client.table("projects").select("*").eq("id", project_id).execute()
        if not proj_res.data:
            return False, "PROJECT_NOT_FOUND", "Project not found."
        p = proj_res.data[0]
        version = p.get("version") or 1
        embedding_status = p.get("embedding_status", "pending")

        # 2. Check candidate upload exists
        uploads_res = supabase_client.table("candidate_uploads").select("*").eq("project_id", project_id).eq("status", "COMPLETED").order("version", desc=True).limit(1).execute()
        if not uploads_res.data:
            return False, "CANDIDATES_NOT_UPLOADED", "Candidate upload record not found. Please upload candidates first."
        latest_upload = uploads_res.data[0]
        upload_version = latest_upload["version"]

        # 3. Check version mismatch
        if version != upload_version:
            return False, "INDEX_VERSION_MISMATCH", f"Project version ({version}) does not match latest candidate upload version ({upload_version})."

        # 4. Check active indexing jobs
        active_job_res = supabase_client.table("background_jobs").select("status").eq("project_id", project_id).not_.in_("status", ["completed", "failed", "cancelled"]).execute()
        if active_job_res.data:
            return False, "INDEXING_IN_PROGRESS", "Candidate indexing is still in progress."

        if embedding_status in ["queued", "processing", "pending"]:
            return False, "INDEXING_IN_PROGRESS", "Candidate indexing is still in progress."
        elif embedding_status == "failed":
            return False, "INDEXING_FAILED", "Candidate indexing failed. Use the retry endpoint to restart indexing — no re-upload required."

        # 5. Check physical artifacts
        zip_key = f"{project_id}/index_v{version}.zip"

        required_preflights = [
            ("embeddings", zip_key, "Compressed Index Archive")
        ]

        missing_preflights = []
        for bucket_name, file_key, label in required_preflights:
            if not StorageService.file_exists(bucket_name, file_key):
                missing_preflights.append(f"{label} ({bucket_name}/{file_key})")

        if missing_preflights:
            missing_str = ", ".join(missing_preflights)
            return False, "ARTIFACTS_MISSING", f"Required indexing artifacts are missing: {missing_str}. Please retry indexing."

        return True, None, None

    except Exception as e:
        return False, "ERROR", f"Failed to verify project readiness: {str(e)}"


@router.get("/projects/{project_id}/readiness")
async def check_project_readiness(
    project_id: str,
    current_user: Optional[AuthUser] = Depends(get_optional_user),
):
    """
    Exposes project analysis readiness status and reasons.
    """
    user_id = get_user_id(current_user)
    proj_res = supabase_client.table("projects").select("id").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    ready, code, message = await is_project_analysis_ready(project_id)
    return {
        "ready": ready,
        "code": code,
        "message": message
    }


@router.post("/projects/{project_id}/analyze")
async def run_analysis(
    project_id: str,
    body: AnalysisRequest,
    current_user: Optional[AuthUser] = Depends(get_optional_user),
):
    import logging
    import asyncio
    import time
    import heapq
    import numpy as np
    import json
    import uuid
    from collections import Counter
    from app.services.storage_provider import StorageService
    from app.services.cache_service import CacheService
    from src.ranking.engine import COMPATIBLE_CATEGORIES

    logger = logging.getLogger(__name__)
    user_id = get_user_id(current_user)

    # 1. Concurrent Analysis Protection (Project Lock)
    if project_id in _active_analyses:
        logger.warning("Concurrent analysis attempt rejected for project %s", project_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analysis already in progress."
        )

    proj_res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")
    p = proj_res.data[0]
    embedding_status = p.get("embedding_status", "pending")

    # Canonical Readiness Check (Phase 8 / Requirement 16)
    ready, error_code, error_msg = await is_project_analysis_ready(project_id)
    if not ready:
        logger.warning("Analysis attempt rejected for project %s: code=%s, msg=%s", project_id, error_code, error_msg)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": error_code,
                "message": error_msg,
                "retry_endpoint": f"/api/v1/platform/projects/{project_id}/retry-indexing" if error_code in ("INDEXING_FAILED", "ARTIFACTS_MISSING") else None,
                "action": "retry_indexing" if error_code in ("INDEXING_FAILED", "ARTIFACTS_MISSING") else None,
            }
        )

    # All artifacts verified, heal status to completed if needed
    if embedding_status != "completed":
        logger.info("[ANALYZE_HEAL] Healing embedding_status from %s to completed for project %s", embedding_status, project_id)
        supabase_client.table("projects").update({
            "embedding_status": "completed",
            "status": "COMPLETED",
            "updated_at": _now()
        }).eq("id", project_id).execute()
        embedding_status = "completed"

    # Verify Job description exists
    job_res = supabase_client.table("jobs").select("*").eq("id", body.job_id).execute()
    if not job_res.data:
        raise HTTPException(status_code=404, detail="Job description not found. Please add a job description first.")
    job = job_res.data[0]

    # Verify specialized role specialty file exists for the category
    jd_category = job.get("role_category") or "BACKEND"
    allowed_categories = COMPATIBLE_CATEGORIES.get(jd_category.upper(), {jd_category.upper()})
    missing_roles = []
    for cat in allowed_categories:
        role_key = f"{project_id}/role_{cat.upper()}_v{version}.jsonl"
        if not StorageService.file_exists("role-indexes", role_key):
            missing_roles.append(f"Role specialty file for {cat} ({role_key})")
    if missing_roles:
        missing_str = ", ".join(missing_roles)
        logger.error("Analysis preflight role specialty check failed for project %s: missing %s", project_id, missing_str)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ARTIFACTS_MISSING",
                "message": f"Required specialized category indexing artifacts are missing: {missing_str}. Please retry indexing.",
                "retry_endpoint": f"/api/v1/platform/projects/{project_id}/retry-indexing",
                "action": "retry_indexing",
            }
        )

    # Database-level check for active analyses
    if p.get("status") in ["queued", "processing", "ranking"]:
        logger.info("Duplicate database-level analysis prevented for project %s", project_id)
        active_res = supabase_client.table("rankings").select("*").eq("project_id", project_id).eq("job_id", body.job_id).order("created_at", desc=True).limit(1).execute()
        if active_res.data:
            ranking_id = active_res.data[0]["id"]
            results_res = supabase_client.table("ranking_results").select("full_result").eq("ranking_id", ranking_id).order("rank").execute()
            active_res.data[0]["results"] = [r["full_result"] for r in results_res.data]
            return active_res.data[0]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analysis already in progress."
        )

    # Acquire lock
    _active_analyses.add(project_id)

    # Initialize telemetry variables
    t_start_all = time.time()
    current_stage = "Analysis Started"
    peak_memory = get_memory_mb()
    
    # Define variables for cleanup
    candidates_pool = []
    passed_role = []
    passed_exp = []
    passed_skills = []
    faiss_input_candidates = []
    retrieved_candidates = []
    scored_pool = []
    top_scored = []
    top_100_candidates = []
    top_100_embs = []
    results_rows = []
    index = None
    candidate_ids_list = None
    full_embs = None
    jd_emb = None
    engine_res = None
    results = []

    # Initialize counters for telemetry
    total_candidates_in_dataset = 0
    AFTER_SKILL_FILTER = 0
    AFTER_ROLE_FILTER = 0
    AFTER_EXPERIENCE_FILTER = 0
    FAISS_INPUT_COUNT = 0
    AFTER_FAISS = 0

    def log_checkpoint(num: int, name: str, details: str = ""):
        check_overall_timeout()
        elapsed = time.time() - t_start_all
        mem = get_memory_mb()
        log_line = f"[CHECKPOINT {num}] {name} ✓ | Elapsed: {elapsed:.3f}s | RAM: {mem:.2f}MB | {details}"
        logger.info(log_line)
        print(log_line, flush=True)

    def log_error_diagnostics(exc: Exception, is_oom: bool = False):
        import traceback
        tb_str = traceback.format_exc()
        elapsed = time.time() - t_start_all
        mem = get_memory_mb()
        cand_count = total_candidates_in_dataset
        emb_status = "unknown"
        if "p" in locals() and isinstance(locals()["p"], dict):
            emb_status = locals()["p"].get("embedding_status", "unknown")
            
        error_report = f"""
==================================================
[ANALYSIS_FAILURE_DIAGNOSTICS]
Project ID: {project_id}
Exception Type: {type(exc).__name__}
Error Message: {str(exc)}
Pipeline Stage: {current_stage}
Elapsed Time: {elapsed:.3f}s
Memory Usage: {mem:.2f}MB
Candidate Count: {cand_count}
Embedding Status: {emb_status}
Is OOM: {is_oom}
--------------------------------------------------
Python Traceback:
{tb_str}
==================================================
"""
        logger.error(error_report)
        print(error_report, flush=True)

    def check_overall_timeout():
        elapsed = time.time() - t_start_all
        if elapsed > 60.0:
            logger.error("[TIMEOUT] Analysis exceeded 60s limit. Stage: %s | RAM: %.2fMB | Elapsed: %.3fs", current_stage, get_memory_mb(), elapsed)
            print(f"[TIMEOUT] Analysis exceeded 60s limit. Stage: {current_stage} | RAM: {get_memory_mb():.2f}MB | Elapsed: {elapsed:.3f}s", flush=True)
            
            raise HTTPException(
                status_code=504,
                detail="Analysis timed out. Please try again."
            )

    def update_peak():
        nonlocal peak_memory
        peak_memory = max(peak_memory, get_memory_mb())

    # STARTUP_MEMORY log
    print("[ANALYSIS_START]", flush=True)
    log_memory("STARTUP_MEMORY")
    
    current_stage = "Project Loaded"
    log_checkpoint(1, "Project Loaded", f"Project: {p.get('name')}")

    try:
        # Get Job Description details
        job_res = supabase_client.table("jobs").select("*").eq("id", body.job_id).execute()
        if not job_res.data:
            raise HTTPException(status_code=400, detail="Job description not found.")
        job = job_res.data[0]
        current_stage = "Job Description Loaded"
        log_checkpoint(2, "Job Description Loaded", f"Job: {job.get('title')}")

        current_path = p.get("current_candidate_path")
        if not current_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No candidates in project. Upload a candidates file first."
            )

        bucket, path = current_path.split("/", 1)
        current_stage = "Candidate File Located"
        log_checkpoint(3, "Candidate File Located", f"Path: {current_path}")
        
        total_candidates_in_dataset = p.get("candidate_count") or 0
        if total_candidates_in_dataset == 0:
            total_candidates_in_dataset = sum(1 for _ in StorageService.stream_jsonl(bucket, path))
            supabase_client.table("projects").update({"candidate_count": total_candidates_in_dataset}).eq("id", project_id).execute()

        # Invalidation check via JD hash (including openings/positions)
        openings = job.get("openings") or job.get("open_positions") or ""
        jd_content = f"{job.get('title', '')}|{job.get('description', '')}|{json.dumps(job.get('required_skills', []))}|{openings}"
        jd_hash = get_sha256_hash(jd_content)
        dataset_hash = compute_dataset_hash(None, project_id=project_id)

        # Check existing rankings
        existing_rank = supabase_client.table("rankings").select("*").eq("project_id", project_id).eq("dataset_hash", dataset_hash).eq("jd_hash", jd_hash).execute()
        if existing_rank.data:
            r = existing_rank.data[0]
            if r.get("metadata_only_fallback") and p.get("embedding_status") == "ready":
                logger.info("Cached ranking was metadata-only fallback, but embeddings are now ready. Bypassing cache to compute full vector ranking.")
                CacheService.invalidate_project(project_id)
            elif r.get("ai_enhancement_unavailable"):
                logger.info("Cached ranking had AI Enhancement Unavailable. Bypassing cache to retry OpenRouter.")
                CacheService.invalidate_project(project_id)
            else:
                logger.info("Dataset fingerprint check: Reusing existing ranking results for project %s", project_id)
                supabase_client.table("projects").update({"status": "COMPLETED", "updated_at": _now()}).eq("id", project_id).execute()
                results_res = supabase_client.table("ranking_results").select("full_result").eq("ranking_id", r["id"]).order("rank").execute()
                r["results"] = [x["full_result"] for x in results_res.data]
                return r

        # Mark database status as processing
        supabase_client.table("projects").update({"status": "processing", "updated_at": _now()}).eq("id", project_id).execute()

        # Setup limits
        MAX_ROLE_FILTER = 20000
        MAX_EXP_FILTER = 10000
        MAX_SKILL_FILTER = 3000
        MAX_FAISS_INPUT = 2000
        MAX_FAISS_RESULTS = 500
        MAX_DEEP_SCORING = 100

        filter_time = 0.0
        index_lookup_time = 0.0
        embedding_time = 0.0
        faiss_time = 0.0
        scoring_time = 0.0
        llm_time = 0.0

        # 1. Download and extract the index zip
        version = p.get("version") or 1
        zip_key = f"{project_id}/index_v{version}.zip"

        cached_index = CacheService.get("faiss-indexes", f"{project_id}/index_v{version}")
        cached_metadata = CacheService.get("metadata", f"{project_id}/metadata_v{version}")
        cached_lookup = CacheService.get("lookup", f"{project_id}/lookup_v{version}")

        if cached_index is not None and cached_metadata is not None and cached_lookup is not None:
            index = cached_index
            df_metadata = cached_metadata
            lookup_dict = cached_lookup
            logger.info("[CACHE_HIT] Loaded FAISS, metadata parquet and lookup json from CacheService for project %s", project_id)
        else:
            t_download = time.time()
            zip_bytes = StorageService.download_file("embeddings", zip_key)
            logger.info("[DOWNLOAD] Downloaded index zip for project %s in %.3fs", project_id, time.time() - t_download)
            
            import zipfile
            import io
            import pandas as pd
            import faiss
            
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
                faiss_bytes = z.read("candidate_index.faiss")
                parquet_bytes = z.read("candidate_metadata.parquet")
                lookup_bytes = z.read("candidate_lookup.json")
            
            index = faiss.deserialize_index(np.frombuffer(faiss_bytes, dtype=np.uint8))
            df_metadata = pd.read_parquet(io.BytesIO(parquet_bytes))
            lookup_dict = json.loads(lookup_bytes.decode("utf-8"))
            
            CacheService.set("faiss-indexes", f"{project_id}/index_v{version}", index)
            CacheService.set("metadata", f"{project_id}/metadata_v{version}", df_metadata)
            CacheService.set("lookup", f"{project_id}/lookup_v{version}", lookup_dict)

        total_candidates_in_dataset = len(df_metadata)

        # Extract requirements from Job Description for prefiltering
        jd_min_exp = float(job.get("min_experience") or 0.0)
        jd_skills = [s.lower().strip() for s in (job.get("required_skills") or []) if isinstance(s, str) and s.strip()]

        # 2. Smart Prefiltering using Pandas
        t_filter_start = time.time()
        
        df_filtered = df_metadata
        if allowed_categories:
            df_filtered = df_filtered[df_filtered['role'].str.upper().isin(allowed_categories)]
            
        if jd_min_exp > 0:
            df_filtered = df_filtered[df_filtered['experience'] >= (jd_min_exp - 2.0)]
            
        if jd_skills:
            df_filtered = df_filtered[df_filtered['skills'].apply(lambda s_list: bool(set(s_list) & set(jd_skills)))]

        # Limit to top MAX_FAISS_INPUT (2000) sorted by candidate_quality_score
        df_filtered = df_filtered.sort_values(by='candidate_quality_score', ascending=False)
        df_top = df_filtered.head(MAX_FAISS_INPUT)

        FAISS_INPUT_COUNT = len(df_top)
        AFTER_ROLE_FILTER = len(df_top)
        AFTER_EXPERIENCE_FILTER = len(df_top)
        AFTER_SKILL_FILTER = len(df_top)

        faiss_input_candidates = []
        for _, r in df_top.iterrows():
            cid = r["candidate_id"]
            if cid in lookup_dict:
                faiss_input_candidates.append(lookup_dict[cid])

        filter_time = time.time() - t_filter_start
        
        current_stage = "POST_FILTER_MEMORY"
        log_checkpoint(5, "Role Filtering Completed", f"Passed: {AFTER_ROLE_FILTER}")
        log_checkpoint(6, "Experience Filtering Completed", f"Passed: {AFTER_EXPERIENCE_FILTER}")
        log_checkpoint(7, "Skill Filtering Completed", f"Passed: {AFTER_SKILL_FILTER}")
        log_memory("POST_FILTER_MEMORY")

        # Check safety memory threshold
        embedding_status = p.get("embedding_status", "pending")
        metadata_only_fallback = False
        fallback_reason = None

        if get_memory_mb() > settings.memory_safety_threshold_mb or memory_safety_mode:
            logger.warning("[MEMORY_WARNING] Process RAM exceeds 450MB. Disabling FAISS search.")
            metadata_only_fallback = True
            fallback_reason = "memory_safety_limit_exceeded"
        elif embedding_status not in ["ready", "completed"]:
            metadata_only_fallback = True
            fallback_reason = f"embedding_status_{embedding_status}"

        # 3. FAISS Retrieval Search
        similarities_map = {}
        retrieved_candidates = []

        if metadata_only_fallback or len(df_top) == 0:
            current_stage = "POST_FAISS_MEMORY"
            log_checkpoint(9, "FAISS Retrieval Completed", f"Skipped (Metadata-only fallback or empty pool)")
            log_memory("POST_FAISS_MEMORY")
            
            top_metadata = df_top.head(MAX_FAISS_RESULTS)
            for _, r in top_metadata.iterrows():
                cid = r['candidate_id']
                if cid in lookup_dict:
                    retrieved_candidates.append(lookup_dict[cid])
                    similarities_map[cid] = 0.5
            AFTER_FAISS = len(retrieved_candidates)
        else:
            # Generate JD embedding
            t_emb_start = time.time()
            jd_text = (job.get("title", "") + " " + job.get("description", "")).strip()
            encoder = _get_encoder()
            
            try:
                async def generate_jd_embedding():
                    return encoder.encode_single(jd_text, normalize=True, bge_mode="query")
                jd_emb = await asyncio.wait_for(generate_jd_embedding(), timeout=300.0)
                embedding_time = time.time() - t_emb_start
            except Exception as e:
                logger.error("Failed to generate JD embedding: %s", e)
                metadata_only_fallback = True
                fallback_reason = f"embedding_error: {e}"

            current_stage = "POST_EMBEDDING_MEMORY"
            log_checkpoint(8, "Embeddings Ready", f"Model: {settings.embedding_model}")
            log_memory("POST_EMBEDDING_MEMORY")

            if metadata_only_fallback:
                current_stage = "POST_FAISS_MEMORY"
                log_checkpoint(9, "FAISS Retrieval Completed", f"Aborted (Embedding failure)")
                log_memory("POST_FAISS_MEMORY")
                top_metadata = df_top.head(MAX_FAISS_RESULTS)
                for _, r in top_metadata.iterrows():
                    cid = r['candidate_id']
                    if cid in lookup_dict:
                        retrieved_candidates.append(lookup_dict[cid])
                        similarities_map[cid] = 0.5
                AFTER_FAISS = len(retrieved_candidates)
            else:
                t_faiss_start = time.time()
                try:
                    import faiss
                    pool_offsets = df_top['embedding_offset'].tolist()
                    sel = faiss.IDSelectorArray(np.array(pool_offsets, dtype=np.int64))
                    params = faiss.SearchParameters()
                    params.sel = sel
                    
                    top_k_search = min(MAX_FAISS_RESULTS, len(pool_offsets))
                    similarities, ann_indices = index.search(
                        jd_emb.reshape(1, -1).astype(np.float32),
                        top_k_search,
                        params=params
                    )
                    similarities = similarities[0]
                    ann_indices = ann_indices[0]
                    
                    offset_to_id = df_top.set_index('embedding_offset')['candidate_id'].to_dict()
                    for sim, global_idx in zip(similarities, ann_indices):
                        if global_idx < 0:
                            continue
                        cid = offset_to_id.get(global_idx)
                        if cid and cid in lookup_dict:
                            retrieved_candidates.append(lookup_dict[cid])
                            similarities_map[cid] = float(sim)
                    
                    faiss_time = time.time() - t_faiss_start
                except Exception as e:
                    logger.error("FAISS search encountered error: %s", e)
                    metadata_only_fallback = True
                    top_metadata = df_top.head(MAX_FAISS_RESULTS)
                    for _, r in top_metadata.iterrows():
                        cid = r['candidate_id']
                        if cid in lookup_dict:
                            retrieved_candidates.append(lookup_dict[cid])
                            similarities_map[cid] = 0.5
                
                AFTER_FAISS = len(retrieved_candidates)
                current_stage = "POST_FAISS_MEMORY"
                log_checkpoint(9, "FAISS Retrieval Completed", f"Retrieved: {AFTER_FAISS}")
                log_memory("POST_FAISS_MEMORY")
                update_peak()

        t_stage3_start = time.time()
        scored_pool = []
        for c in retrieved_candidates:
            cid = c.get("candidate_id")
            norm_sim = similarities_map.get(cid, 0.5)
            
            cand_cat = normalize_role_category(c.get("candidate_role_category") or c.get("candidate_specialization") or "BACKEND")
            if cand_cat == normalize_role_category(jd_category):
                boost = 0.15
            elif cand_cat not in allowed_categories:
                boost = -0.10
            else:
                boost = 0.0
                
            blend_score = 0.70 * norm_sim + 0.30 * boost
            scored_pool.append({
                "candidate": c,
                "similarity": norm_sim,
                "blend_score": blend_score
            })
            
        scored_pool.sort(key=lambda x: -x["blend_score"])
        top_scored = scored_pool[:MAX_DEEP_SCORING]
        top_100_candidates = [item["candidate"] for item in top_scored]
        
        scoring_time = time.time() - t_stage3_start
        current_stage = "POST_SCORING_MEMORY"
        log_checkpoint(10, "Hybrid Scoring Completed", f"Top scored pool size: {len(top_100_candidates)}")
        log_memory("POST_SCORING_MEMORY")

        top_100_embs = []
        if not metadata_only_fallback and top_100_candidates:
            try:
                id_to_offset = df_top.set_index('candidate_id')['embedding_offset'].to_dict()
                for c in top_100_candidates:
                    cid = c.get("candidate_id")
                    if cid in id_to_offset:
                        offset = id_to_offset[cid]
                        vec = index.reconstruct(int(offset))
                        top_100_embs.append(vec)
                if top_100_embs:
                    top_100_embs = np.array(top_100_embs, dtype=np.float32)
                else:
                    top_100_embs = None
            except Exception as reconstruct_exc:
                logger.warning("FAISS vector reconstruction failed: %s", reconstruct_exc)
                top_100_embs = None

        current_stage = "POST_LLM_MEMORY"
        # Enforce memory safety limit check before calling LLM
        call_llm_flag = True
        if get_memory_mb() > settings.memory_safety_threshold_mb or memory_safety_mode:
            logger.warning("[MEMORY_WARNING] Process RAM exceeds 450MB before LLM stage. Disabling LLM enhancement.")
            call_llm_flag = False

        log_checkpoint(11, "LLM Evaluation Started", f"Enhancement active: {call_llm_flag}")
        log_memory("POST_LLM_MEMORY")

        from src.ranking.engine import UnifiedRankingEngine
        engine = UnifiedRankingEngine(
            encoder=_get_encoder(),
            config={
                "performance_mode": body.performance_mode or "balanced",
                "apply_eligibility": True
            }
        )
        
        from src.ranking.engine import validate_tuple
        
        # Wrap LLM evaluation in 60-second timeout (Requirement 5)
        try:
            async def run_llm_ranking():
                return await engine.rank_candidates(
                    candidates=top_100_candidates,
                    jd_dict=job,
                    top_n=body.top_k,
                    call_llm=call_llm_flag,
                    candidate_embeddings=top_100_embs
                )

            engine_res = await asyncio.wait_for(run_llm_ranking(), timeout=60.0) # 60 seconds
        except asyncio.TimeoutError:
            logger.error("OpenRouter LLM evaluation timed out after 60 seconds. Falling back to deterministic ranking.")
            engine_res = await engine.rank_candidates(
                candidates=top_100_candidates,
                jd_dict=job,
                top_n=body.top_k,
                call_llm=False,
                candidate_embeddings=top_100_embs
            )
            
        assert isinstance(engine_res, tuple), f"Expected tuple from rank_candidates, got {type(engine_res).__name__}"
        validate_tuple(engine_res, 3, "platform.py run_analysis", "(results, ranked_tuples, blended_scores)")
        results, _, _ = engine_res
        print("[LLM_END]")
        log_memory("LLM_MEMORY")
        update_peak()

        scoring_time += engine.metrics.get("ranking_time", 0.0)
        llm_time += engine.metrics.get("llm_time", 0.0)

        categories = [
            normalize_role_category(c.get("candidate_role_category") or c.get("candidate_specialization") or "BACKEND")
            for c in faiss_input_candidates
        ]
        cat_counts = Counter(categories)
        top_categories = [cat for cat, count in cat_counts.most_common(3)]
        
        prefilter_statistics = {
            "total_uploaded": total_candidates_in_dataset,
            "eligible": AFTER_SKILL_FILTER,
            "filtered_out": max(0, total_candidates_in_dataset - AFTER_SKILL_FILTER),
            "top_categories": top_categories
        }

        analysis_status = "no_qualified_candidates" if getattr(engine, "status", "") == "no_qualified_candidates" else "completed"
        ranking_id = str(uuid.uuid4())
        now = _now()

        version_metadata = {
            "ranking_version": "v2.5",
            "embedding_model": settings.embedding_model,
            "llm_provider": "OpenRouter",
            "llm_model": settings.openrouter_model,
            "generated_at": now
        }

        total_time_all = time.time() - t_start_all

        ai_enhancement_error = getattr(engine, "ai_enhancement_unavailable", False)
        ai_enhancement_unavailable = False
        if ai_enhancement_error:
            ai_enhancement_unavailable = True

        metrics = {
            "total_candidates": total_candidates_in_dataset,
            "candidates_filtered": total_candidates_in_dataset - AFTER_SKILL_FILTER,
            "candidates_retrieved": AFTER_FAISS,
            "candidates_scored": len(top_100_candidates),
            "llm_candidates_evaluated": engine.metrics.get("llm_candidates_evaluated", 0),
            "retrieval_time": filter_time + index_lookup_time + faiss_time,
            "ranking_time": scoring_time,
            "llm_time": llm_time,
            "total_analysis_time": total_time_all,
            "filter_time": filter_time,
            "index_lookup_time": index_lookup_time,
            "embedding_time": embedding_time,
            "faiss_time": faiss_time,
            "scoring_time": scoring_time,
            "total_time": total_time_all,
            "total_candidates_funnel": total_candidates_in_dataset,
            "after_role_filter": AFTER_ROLE_FILTER,
            "after_experience_filter": AFTER_EXPERIENCE_FILTER,
            "after_skill_filter": AFTER_SKILL_FILTER,
            "faiss_input_count": FAISS_INPUT_COUNT,
            "after_faiss": AFTER_FAISS,
            "after_scoring": len(top_100_candidates),
            "llm_input_count": engine.metrics.get("llm_candidates_evaluated", 0),
            "after_llm_selection": len(results),
            "peak_memory_mb": round(peak_memory, 2),
        }

        accuracy_metrics = _compute_accuracy_metrics(results)
        metrics["accuracy"] = accuracy_metrics

        ranking = {
            "id": ranking_id,
            "project_id": project_id,
            "job_id": body.job_id,
            "status": analysis_status,
            "total_candidates": total_candidates_in_dataset,
            "ranked_count": len(results),
            "results": results,
            "dataset_hash": dataset_hash,
            "jd_hash": jd_hash,
            "version_metadata": version_metadata,
            "metrics": metrics,
            "accuracy_metrics": accuracy_metrics,
            "prefilter_statistics": prefilter_statistics,
            "metadata_only_fallback": metadata_only_fallback,
            "ai_enhancement_unavailable": ai_enhancement_unavailable,
            "created_at": now,
        }
        if analysis_status == "no_qualified_candidates":
            ranking["message"] = "No strong candidates found for this role."
            ranking["alternative_candidates"] = getattr(engine, "alternative_candidates", [])

        supabase_client.table("rankings").insert({
            "id": ranking_id,
            "project_id": project_id,
            "job_id": body.job_id,
            "version": 1,
            "status": analysis_status,
            "total_candidates": total_candidates_in_dataset,
            "ranked_count": len(results),
            "dataset_hash": dataset_hash,
            "jd_hash": jd_hash,
            "version_metadata": version_metadata,
            "metrics": metrics,
            "prefilter_statistics": prefilter_statistics,
            "metadata_only_fallback": metadata_only_fallback,
            "ai_enhancement_unavailable": ai_enhancement_unavailable,
            "created_at": now
        }).execute()

        results_rows = []
        for res in results:
            results_rows.append({
                "ranking_id": ranking_id,
                "candidate_id": res.get("candidate_id"),
                "rank": res.get("rank"),
                "score": res.get("ai_score") or res.get("score") or 0.0,
                "reasoning": res.get("reasoning"),
                "eligibility": res.get("eligibility", True),
                "critical_skill_coverage": res.get("critical_skill_coverage"),
                "full_result": res
            })
            
        for i in range(0, len(results_rows), 50):
            supabase_client.table("ranking_results").insert(results_rows[i:i+50]).execute()

        current_stage = "FINAL_MEMORY"
        log_checkpoint(12, "Results Stored Successfully", f"Ranking ID: {ranking_id}")
        log_memory("FINAL_MEMORY")

        avg_score = accuracy_metrics["average_ai_score"]
        try:
            supabase_client.table("analysis_metrics").insert({
                "ranking_id": ranking_id,
                "project_id": project_id,
                "upload_time": 0.0,
                "embedding_time": embedding_time,
                "faiss_time": faiss_time,
                "llm_time": llm_time,
                "total_analysis_time": total_time_all,
                "average_match_score": avg_score,
                "accuracy_metrics": accuracy_metrics,
            }).execute()
        except Exception as metrics_exc:
            logger.warning("[ANALYSIS_METRICS] insert failed (column may be missing): %s", metrics_exc)
            try:
                supabase_client.table("analysis_metrics").insert({
                    "ranking_id": ranking_id,
                    "project_id": project_id,
                    "upload_time": 0.0,
                    "embedding_time": embedding_time,
                    "faiss_time": faiss_time,
                    "llm_time": llm_time,
                    "total_analysis_time": total_time_all,
                    "average_match_score": avg_score,
                }).execute()
            except Exception:
                pass

        supabase_client.table("projects").update({
            "status": "COMPLETED",
            "updated_at": now
        }).eq("id", project_id).execute()
        
        _set_cached_ranking(project_id, body.job_id, ranking)
        
        # Print final completed state (Phase 7)
        elapsed_total = time.time() - t_start_all
        print(f"[ANALYSIS_COMPLETED] | Total time: {elapsed_total:.3f}s | Peak RAM: {peak_memory:.2f}MB", flush=True)
        return ranking

    except MemoryError as exc:
        log_error_diagnostics(exc, is_oom=True)
        try:
            from app.services.cache_service import CacheService
            CacheService.clear()
        except Exception:
            pass
        try:
            from src.features.embedding import _MODEL_CACHE
            _MODEL_CACHE.clear()
        except Exception:
            pass
        global _encoder
        _encoder = None
        import gc
        gc.collect()
        
        # Try to mark the project as failed in Supabase so the UI doesn't hang
        try:
            supabase_client.table("projects").update({
                "status": "failed",
                "upload_statistics": {"failure_reason": "Out of memory error"},
                "updated_at": _now()
            }).eq("id", project_id).execute()
        except Exception:
            pass
            
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server temporarily overloaded: Out of memory."
        )

    except Exception as exc:
        log_error_diagnostics(exc, is_oom=False)
        try:
            supabase_client.table("projects").update({
                "status": "failed",
                "upload_statistics": {"failure_reason": str(exc)},
                "updated_at": _now()
            }).eq("id", project_id).execute()
        except Exception:
            pass
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {str(exc)}"
        )
    finally:
        # 1. Release active project cache references from CacheService
        try:
            from app.services.cache_service import CacheService
            CacheService.invalidate_project(project_id)
        except Exception:
            pass
            
        # 2. Release local large variables
        large_vars = [
            "candidates_pool", "passed_role", "passed_exp", "passed_skills",
            "faiss_input_candidates", "retrieved_candidates", "scored_pool",
            "top_scored", "top_100_candidates", "top_100_embs", "results_rows",
            "index", "candidate_ids_list", "full_embs", "jd_emb", "engine_res",
            "results"
        ]
        for v in large_vars:
            if v in locals():
                try:
                    del locals()[v]
                except Exception:
                    pass
                    
        # 3. Release embedding model RAM and trigger GC
        import gc
        gc.collect()
        if settings.unload_model_after_indexing:
            _release_embedding_model()
        else:
            try:
                from src.features.embedding import _MODEL_CACHE
                _MODEL_CACHE.clear()
            except Exception:
                pass
        
        # 4. Release concurrent analysis project lock
        _active_analyses.discard(project_id)
        
        # 5. Log telemetry FINAL_MEMORY and Peak Memory
        update_peak()
        log_memory("FINAL_MEMORY")
        print(f"[MEMORY_TELEMETRY] PEAK_MEMORY: {peak_memory:.2f} MB", flush=True)
        from app.core.config import settings
        print(f"[MEMORY_TELEMETRY] CURRENT_MODEL: {settings.embedding_model}", flush=True)
        print(f"[MEMORY_TELEMETRY] CANDIDATE_COUNT: {total_candidates_in_dataset}", flush=True)
        print(f"[MEMORY_TELEMETRY] FILTERED_COUNT: {AFTER_SKILL_FILTER}", flush=True)


def _top_strengths(ds) -> list[str]:
    if not ds:
        return []
    dims = {
        "Strong semantic skill fit": ds.required_skills_match,
        "Strong experience quality": ds.relevant_experience,
        "Good career progression": ds.career_growth,
        "High hiring readiness": ds.behavioral_fit,
        "Ideal logistics fit": ds.specialization_match,
    }
    return [k for k, v in sorted(dims.items(), key=lambda x: -x[1]) if v > 0.6][:3]


def _top_weaknesses(ds) -> list[str]:
    if not ds:
        return []
    dims = {
        "Limited semantic skill match": ds.required_skills_match,
        "Weaker experience profile": ds.relevant_experience,
        "Flat career progression": ds.career_growth,
        "Lower engagement signals": ds.behavioral_fit,
        "Non-preferred logistics": ds.specialization_match,
    }
    return [k for k, v in sorted(dims.items(), key=lambda x: x[1]) if v < 0.5][:2]


# ── Ranking retrieval ─────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/rankings/{ranking_id}")
async def get_ranking(project_id: str, ranking_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    res = supabase_client.table("rankings").select("*").eq("id", ranking_id).eq("project_id", project_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Ranking not found")
        
    ranking = res.data[0]
    
    results_res = supabase_client.table("ranking_results").select("full_result").eq("ranking_id", ranking_id).order("rank").execute()
    ranking["results"] = [r["full_result"] for r in results_res.data]

    # Expose accuracy_metrics at top level for frontend (stored inside metrics.accuracy)
    stored_metrics = ranking.get("metrics") or {}
    if not ranking.get("accuracy_metrics") and isinstance(stored_metrics, dict):
        ranking["accuracy_metrics"] = stored_metrics.get("accuracy")

    return ranking


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/analytics")
async def get_analytics(
    project_id: str,
    ranking_id: Optional[str] = None,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    proj_res = supabase_client.table("projects").select("current_candidate_path").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    current_path = proj_res.data[0].get("current_candidate_path")
    candidates_count = 0
    skill_counts: dict[str, int] = {}
    exp_buckets = {"0-2 years": 0, "2-5 years": 0, "5-10 years": 0, "10+ years": 0}

    if current_path:
        bucket, path = current_path.split("/", 1)
        from app.services.storage_provider import StorageService
        for c in StorageService.stream_jsonl(bucket, path):
            candidates_count += 1
            for s in c.get("skills", []):
                name = s.get("name") if isinstance(s, dict) else s
                if name:
                    skill_counts[name] = skill_counts.get(name, 0) + 1
            yoe = float(c.get("profile", {}).get("years_of_experience") or c.get("years_exp") or 0)
            if yoe < 2:
                exp_buckets["0-2 years"] += 1
            elif yoe < 5:
                exp_buckets["2-5 years"] += 1
            elif yoe < 10:
                exp_buckets["5-10 years"] += 1
            else:
                exp_buckets["10+ years"] += 1

    skill_dist = [{"skill": k, "count": v} for k, v in
                  sorted(skill_counts.items(), key=lambda x: -x[1])[:15]]
    exp_dist = [{"range": k, "count": v} for k, v in exp_buckets.items()]

    quality = {"high": 0, "medium": 0, "low": 0}
    match_bd = {"excellent": 0, "good": 0, "fair": 0, "poor": 0}
    hidden_gems = []
    high_risk = []

    if ranking_id:
        results_res = supabase_client.table("ranking_results").select("full_result").eq("ranking_id", ranking_id).execute()
        for row_data in results_res.data:
            row = row_data.get("full_result", {})
            readiness = row.get("hiring_readiness", "medium")
            quality[readiness] = quality.get(readiness, 0) + 1
            mp = row.get("match_percent", 0)
            if mp >= 80:
                match_bd["excellent"] += 1
            elif mp >= 60:
                match_bd["good"] += 1
            elif mp >= 40:
                match_bd["fair"] += 1
            else:
                match_bd["poor"] += 1

    return {
        "skill_distribution": skill_dist,
        "experience_distribution": exp_dist,
        "quality_breakdown": quality,
        "match_breakdown": match_bd,
        "hidden_gems": hidden_gems,
        "high_risk_profiles": high_risk,
        "hiring_funnel": {
            "uploaded": candidates_count,
            "analyzed": candidates_count,
            "ranked": sum(quality.values()),
            "shortlisted": quality.get("high", 0),
        },
    }


# ── Export ────────────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/export")
async def export_results(
    project_id: str,
    body: ExportRequest,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    p_res = supabase_client.table("projects").select("name").eq("id", project_id).eq("user_id", user_id).execute()
    if not p_res.data:
        raise HTTPException(status_code=404, detail="Project not found")
    p = p_res.data[0]

    r_res = supabase_client.table("rankings").select("*").eq("id", body.ranking_id).eq("project_id", project_id).execute()
    if not r_res.data:
        raise HTTPException(status_code=404, detail="Ranking not found")
    r = r_res.data[0]

    job_res = supabase_client.table("jobs").select("title").eq("id", r["job_id"]).execute()
    job_title = job_res.data[0]["title"] if job_res.data else "Unknown Job"

    results_res = supabase_client.table("ranking_results").select("full_result").eq("ranking_id", body.ranking_id).order("rank").execute()
    r_results = [row["full_result"] for row in results_res.data]

    if len(r_results) == 0 and r.get("status") == "completed":
        import logging
        logging.getLogger(__name__).warning("Ranking export mismatch detected: UI rankings cannot be empty for completed runs.")
        raise HTTPException(status_code=400, detail="Ranking export mismatch detected")

    _health_stats["exports_generated"] += 1

    from app.services.export_service import ExportService

    if body.format == "json":
        r["results"] = r_results
        content = json.dumps(r, indent=2)
        return StreamingResponse(
            io.BytesIO(content.encode()),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=ranking-{body.ranking_id[:8]}.json"},
        )
    elif body.format == "xlsx":
        xlsx_data = ExportService.generate_xlsx(r_results)
        return StreamingResponse(
            io.BytesIO(xlsx_data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=ranking-{body.ranking_id[:8]}.xlsx"},
        )
    elif body.format == "pdf":
        pdf_data = ExportService.generate_pdf(p.get("name", "Project"), job_title, r_results)
        return StreamingResponse(
            io.BytesIO(pdf_data),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=ranking-{body.ranking_id[:8]}.pdf"},
        )
    else:
        # Default to csv
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "Rank", "Candidate Name", "Current Role", "Current Company",
            "Experience", "Location", "Match %", "AI Score", "Eligibility",
            "Critical Skill Coverage", "Top Skills", "Recommendation", "Reasoning"
        ])
        
        for row in r_results:
            cand_name = row.get("candidate_name") or row.get("candidate_id", "")
            role = row.get("current_title", "")
            company = row.get("current_company", "")
            exp = f"{row.get('years_of_experience', 0.0)} Years"
            loc = row.get("location", "")
            match_pct = f"{row.get('match_percent')}%"
            ai_score = row.get("ai_score", 0.0)
            elig = "Eligible" if row.get("eligibility") else f"Ineligible: {row.get('eligibility_reason', '')}"
            skill_cov = row.get("critical_skill_coverage", "")
            
            skills_raw = row.get("top_skills", [])
            skills_list = [s.get("name") if isinstance(s, dict) else str(s) for s in skills_raw]
            skills_str = ", ".join(skills_list)
            
            recommendation = row.get("hiring_readiness", "")
            reasoning = row.get("reasoning", "")
            
            writer.writerow([
                row.get("rank"), cand_name, role, company,
                exp, loc, match_pct, ai_score, elig,
                skill_cov, skills_str, recommendation, reasoning
            ])
            
        return StreamingResponse(
            io.BytesIO(out.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=ranking-{body.ranking_id[:8]}.csv"},
        )


# ── Health Dashboard ──────────────────────────────────────────────────────────

@router.get("/health-stats")
async def get_health_stats(current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    
    # Fetch projects for the user from Supabase
    proj_res = supabase_client.table("projects").select("id, status, candidate_count").eq("user_id", user_id).execute()
    projects_list = proj_res.data or []
    
    num_projects = len(projects_list)
    failed_jobs = sum(1 for p in projects_list if p.get("status") == "failed")
    total_candidates = sum(p.get("candidate_count") or 0 for p in projects_list)
    
    # Fetch rankings count for the user's projects
    if projects_list:
        project_ids = [p["id"] for p in projects_list]
        rankings_res = supabase_client.table("rankings").select("id").in_("project_id", project_ids).execute()
        num_rankings = len(rankings_res.data or [])
    else:
        num_rankings = 0

    return {
        "projects": num_projects,
        "candidates": total_candidates,
        "rankings": num_rankings,
        "failed_jobs": failed_jobs,
        "duplicate_projects_prevented": _health_stats.get("duplicate_projects_prevented", 0),
        "exports_generated": _health_stats.get("exports_generated", 0),
    }


@router.get("/projects/{project_id}/performance-metrics")
async def get_performance_metrics(
    project_id: str,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    p_res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not p_res.data:
        raise HTTPException(status_code=404, detail="Project not found")
        
    r_res = supabase_client.table("rankings").select("*").eq("project_id", project_id).order("created_at", desc=True).limit(1).execute()
    if not r_res.data:
        return {
            "project_id": project_id,
            "has_metrics": False,
            "message": "No analysis runs found for this project yet."
        }
        
    ranking = r_res.data[0]
    metrics = ranking.get("metrics") or {}
    
    return {
        "project_id": project_id,
        "ranking_id": ranking.get("id"),
        "has_metrics": True,
        "performance_mode": ranking.get("version_metadata", {}).get("performance_mode", "balanced"),
        "metadata_only_fallback": ranking.get("metadata_only_fallback", False),
        "ai_enhancement_unavailable": ranking.get("ai_enhancement_unavailable", False),
        "timing_metrics": {
            "total_analysis_time_sec": metrics.get("total_analysis_time", 0.0),
            "retrieval_time_sec": metrics.get("retrieval_time", 0.0),
            "filter_time_sec": metrics.get("filter_time", 0.0),
            "index_lookup_time_sec": metrics.get("index_lookup_time", 0.0),
            "embedding_time_sec": metrics.get("embedding_time", 0.0),
            "faiss_time_sec": metrics.get("faiss_time", 0.0),
            "scoring_time_sec": metrics.get("scoring_time", 0.0),
            "llm_time_sec": metrics.get("llm_time", 0.0),
        },
        "funnel_metrics": {
            "total_candidates": metrics.get("total_candidates", 0),
            "after_role_filter": metrics.get("after_role_filter", 0),
            "after_experience_filter": metrics.get("after_experience_filter", 0),
            "after_skill_filter": metrics.get("after_skill_filter", 0),
            "faiss_input_count": metrics.get("faiss_input_count", 0),
            "after_faiss": metrics.get("after_faiss", 0),
            "after_scoring": metrics.get("after_scoring", 0),
            "llm_input_count": metrics.get("llm_input_count", 0),
            "after_llm_selection": metrics.get("after_llm_selection", 0),
        },
        "memory_metrics": {
            "peak_memory_mb": metrics.get("peak_memory_mb", 0.0),
            "limit_mb": 450.0,
            "safety_margin_mb": max(0.0, 450.0 - metrics.get("peak_memory_mb", 0.0))
        }
    }
