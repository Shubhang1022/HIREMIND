"""
app.core.diag — Centralised runtime diagnostics helpers.

Provides:
  - log_call()   context manager: wraps every external call with CALL_START/SUCCESS/FAILED
  - diag_snapshot()   returns a dict with RSS/CPU/threads/uptime
  - log_stage()  logs STAGE_START / STAGE_END / STAGE_FAIL for pipeline stages

Usage:
    from app.core.diag import log_call, diag_snapshot, log_stage

    with log_call("supabase", "select projects", project_id=project_id):
        result = supabase_client.table("projects").select("*").execute()

    with log_stage(logger, project_id, job_id, "embedding"):
        embed_all(candidates)
"""

from __future__ import annotations

import logging
import os
import time
import traceback
from contextlib import contextmanager
from typing import Optional

import psutil

logger = logging.getLogger(__name__)

_STARTUP_TIME = time.time()


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def diag_snapshot() -> dict:
    """Return a dict with current RSS, CPU, thread count, and uptime."""
    snap = {"rss_mb": 0.0, "cpu_pct": 0.0, "threads": 0, "uptime_s": time.time() - _STARTUP_TIME}
    try:
        proc = psutil.Process(os.getpid())
        snap["rss_mb"]  = proc.memory_info().rss / (1024 * 1024)
        snap["cpu_pct"] = proc.cpu_percent(interval=None)
        snap["threads"] = proc.num_threads()
    except Exception:
        pass
    return snap


# ---------------------------------------------------------------------------
# External call wrapper
# ---------------------------------------------------------------------------

@contextmanager
def log_call(
    service: str,
    operation: str,
    *,
    project_id: Optional[str] = None,
    job_id: Optional[str] = None,
    stage: Optional[str] = None,
    log: Optional[logging.Logger] = None,
):
    """Context manager that emits CALL_START / CALL_SUCCESS / CALL_FAILED.

    Usage::

        with log_call("supabase", "select_projects", project_id=pid):
            res = supabase_client.table("projects").select("*").execute()
    """
    _log = log or logger
    _t0 = time.time()
    snap0 = diag_snapshot()
    _log.info(
        "[CALL_START] service=%s op=%s project=%s job=%s stage=%s rss=%.1fMB",
        service, operation,
        project_id or "-", job_id or "-", stage or "-",
        snap0["rss_mb"],
    )
    try:
        yield
        elapsed_ms = (time.time() - _t0) * 1000
        snap1 = diag_snapshot()
        _log.info(
            "[CALL_SUCCESS] service=%s op=%s project=%s elapsed_ms=%.1f rss=%.1fMB",
            service, operation,
            project_id or "-", elapsed_ms, snap1["rss_mb"],
        )
    except Exception as exc:
        elapsed_ms = (time.time() - _t0) * 1000
        snap1 = diag_snapshot()
        tb = traceback.format_exc()
        _log.error(
            "[CALL_FAILED] service=%s op=%s project=%s job=%s stage=%s "
            "elapsed_ms=%.1f rss=%.1fMB exception=%s\n%s",
            service, operation,
            project_id or "-", job_id or "-", stage or "-",
            elapsed_ms, snap1["rss_mb"], exc, tb,
        )
        raise  # always re-raise — callers decide how to handle


# ---------------------------------------------------------------------------
# Pipeline stage wrapper
# ---------------------------------------------------------------------------

@contextmanager
def log_stage(
    log: logging.Logger,
    project_id: str,
    stage: str,
    *,
    job_id: Optional[str] = None,
    extra: Optional[dict] = None,
):
    """Context manager that emits STAGE_START / STAGE_END / STAGE_FAIL.

    Usage::

        with log_stage(logger, project_id, "embedding", job_id=job_id):
            generate_embeddings(candidates)
    """
    snap0 = diag_snapshot()
    _t0 = time.time()
    log.info(
        "[STAGE_START] project=%s job=%s stage=%s rss=%.1fMB cpu=%.1f%% threads=%d %s",
        project_id, job_id or "-", stage,
        snap0["rss_mb"], snap0["cpu_pct"], snap0["threads"],
        str(extra or ""),
    )
    try:
        yield
        elapsed = time.time() - _t0
        snap1 = diag_snapshot()
        log.info(
            "[STAGE_END] project=%s job=%s stage=%s elapsed=%.2fs "
            "rss=%.1fMB cpu=%.1f%% threads=%d",
            project_id, job_id or "-", stage, elapsed,
            snap1["rss_mb"], snap1["cpu_pct"], snap1["threads"],
        )
    except Exception as exc:
        elapsed = time.time() - _t0
        snap1 = diag_snapshot()
        tb = traceback.format_exc()
        log.error(
            "[STAGE_FAIL] project=%s job=%s stage=%s elapsed=%.2fs "
            "rss=%.1fMB cpu=%.1f%% threads=%d exception_type=%s exception=%s\n%s",
            project_id, job_id or "-", stage, elapsed,
            snap1["rss_mb"], snap1["cpu_pct"], snap1["threads"],
            type(exc).__name__, exc, tb,
        )
        raise
