"""FastAPI application entry point — HireMind AI."""

import os
# Enforce thread limits before any heavy ML library gets imported
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import asyncio
import logging
import signal
import sys
import time
import gc
import threading
import traceback
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# Resolve Git version SHA once at startup
try:
    _git_sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode("utf-8").strip()
except Exception:
    _git_sha = os.environ.get("RENDER_GIT_COMMIT", "unknown")


# Setup path to import from project root 'src'
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import psutil

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.v1.router import api_router
from app.middleware.rate_limit import rate_limit_middleware

# JSON formatter (Phase 8)
class JSONLogFormatter(logging.Formatter):
    def format(self, record):
        import json
        import os
        from datetime import datetime, timezone
        
        # Get memory usage safely
        memory_usage = 0.0
        try:
            import psutil
            process = psutil.Process(os.getpid())
            memory_usage = process.memory_info().rss / (1024 * 1024)
        except Exception:
            pass

        log_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "log_level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "worker_pid": os.getpid(),
            "memory_usage": f"{memory_usage:.2f} MB"
        }
        
        # Inject standard extra custom keys if present
        for key in ["project_id", "job_id", "request_id", "stage", "elapsed_time", "candidate_count"]:
            if hasattr(record, key):
                log_record[key] = getattr(record, key)
                
        return json.dumps(log_record)

def setup_json_logging():
    root_logger = logging.getLogger()
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        
    handler = logging.StreamHandler()
    handler.setFormatter(JSONLogFormatter())
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    
    # Also override uvicorn loggers
    for uvicorn_logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
        u_logger = logging.getLogger(uvicorn_logger_name)
        u_logger.handlers = []
        u_logger.propagate = True

setup_json_logging()
logger = logging.getLogger(__name__)

_startup_time = time.time()

# ── Global exception handlers — catch every unhandled exception before the worker dies ──

def _sys_excepthook(exc_type, exc_value, exc_tb):
    """Catch unhandled exceptions in the main thread."""
    tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.critical(
        "[WORKER_CRASH] Unhandled exception in main thread — worker will exit\n"
        "Type: %s\nValue: %s\nTraceback:\n%s",
        exc_type.__name__, exc_value, tb_str,
    )
    print(f"[WORKER_CRASH] {exc_type.__name__}: {exc_value}", flush=True)
    # Call the original excepthook to preserve default behaviour
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _sys_excepthook


def _threading_excepthook(args):
    """Catch unhandled exceptions in daemon/worker threads."""
    tb_str = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    thread_name = args.thread.name if args.thread else "unknown"
    logger.critical(
        "[THREAD_EXCEPTION] Unhandled exception in thread=%s\n"
        "Type: %s\nValue: %s\nTraceback:\n%s",
        thread_name, args.exc_type.__name__, args.exc_value, tb_str,
    )
    print(f"[THREAD_EXCEPTION] thread={thread_name} {args.exc_type.__name__}: {args.exc_value}", flush=True)

threading.excepthook = _threading_excepthook


def _asyncio_exception_handler(loop, context):
    """Catch unhandled exceptions in asyncio tasks."""
    exc = context.get("exception")
    msg = context.get("message", "unknown")
    task = context.get("task")
    task_name = getattr(task, "get_name", lambda: "?")() if task else "?"

    # CancelledError during shutdown is expected — do not log as crash
    if isinstance(exc, asyncio.CancelledError):
        logger.debug(
            "[ASYNC_CANCELLED] Task=%s cancelled (expected during shutdown)",
            task_name,
        )
        return

    tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else msg
    logger.critical(
        "[ASYNC_EXCEPTION] Unhandled exception in asyncio task=%s message=%s\n%s",
        task_name, msg, tb_str,
    )
    print(f"[ASYNC_EXCEPTION] task={task_name} {type(exc).__name__ if exc else msg}", flush=True)

# Applied to the event loop after it is created (see lifespan)


# ── Signal handlers ──────────────────────────────────────────────────────────
# IMPORTANT: Do NOT raise KeyboardInterrupt from signal handlers.
# Raising KeyboardInterrupt from a C-level signal handler interrupts any
# running coroutine mid-execution, corrupts asyncio state, and causes
# "Task exception was never retrieved" for the deferred startup task.
# Instead: log the signal and let uvicorn's built-in SIGTERM handling do its job.
# Uvicorn already listens for SIGTERM/SIGINT and performs graceful shutdown.

def _make_signal_handler(sig_name: str):
    def _handler(signum, frame):
        rss = 0.0
        try:
            rss = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
        except Exception:
            pass
        logger.info(
            "[SIGNAL_RECEIVED] signal=%s pid=%d rss=%.1fMB uptime=%.1fs — "
            "delegating to uvicorn shutdown",
            sig_name, os.getpid(), rss, time.time() - _startup_time,
        )
        print(f"[SIGNAL_RECEIVED] signal={sig_name} pid={os.getpid()}", flush=True)
        # Do NOT raise KeyboardInterrupt here.
        # Uvicorn already handles SIGTERM/SIGINT and will trigger the lifespan
        # shutdown path cleanly. Raising KeyboardInterrupt from inside an asyncio
        # event loop corrupts coroutine state and produces unhandled task exceptions.
        # For SIGQUIT (Linux debug), we re-raise as a last resort only.
        if sig_name == "SIGQUIT":
            raise KeyboardInterrupt(f"SIGQUIT received — forcing shutdown")
    return _handler


for _sig, _name in [(signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")]:
    try:
        signal.signal(_sig, _make_signal_handler(_name))
    except (OSError, ValueError):
        pass  # Some signals can't be caught in all contexts

# SIGQUIT (Linux only) — only this one forces an immediate stop
try:
    signal.signal(signal.SIGQUIT, _make_signal_handler("SIGQUIT"))
except (AttributeError, OSError, ValueError):
    pass


# ── Process heartbeat ─────────────────────────────────────────────────────────

def _start_heartbeat(interval_seconds: float = 30.0) -> threading.Thread:
    """Daemon thread that logs RSS/CPU/threads every N seconds."""
    def _heartbeat():
        while True:
            try:
                time.sleep(interval_seconds)
                proc = psutil.Process(os.getpid())
                rss   = proc.memory_info().rss / (1024 * 1024)
                cpu   = proc.cpu_percent(interval=None)
                nthrd = proc.num_threads()
                loop  = None
                pending_tasks = 0
                loop_state = "unknown"
                try:
                    loop = asyncio.get_event_loop()
                    loop_state = "running" if loop.is_running() else "stopped"
                    pending_tasks = len([t for t in asyncio.all_tasks(loop) if not t.done()])
                except Exception:
                    pass
                logger.info(
                    "[WORKER_HEARTBEAT] pid=%d rss=%.1fMB cpu=%.1f%% threads=%d "
                    "pending_tasks=%d loop=%s uptime=%.0fs",
                    os.getpid(), rss, cpu, nthrd, pending_tasks, loop_state,
                    time.time() - _startup_time,
                )
            except Exception as hb_exc:
                logger.warning("[WORKER_HEARTBEAT_ERROR] %s", hb_exc)

    t = threading.Thread(target=_heartbeat, name="worker-heartbeat", daemon=True)
    t.start()
    return t

# Normalize and validate origins (Task 3 & 6)
allowed_origins = list(set([
    "http://localhost:3000",
    "https://hiremind-gilt.vercel.app"
] + settings.cors_origins_list))

# If credentials enabled, verify no wildcard exists
if "*" in allowed_origins:
    raise ValueError("CORS configuration error: allow_credentials cannot be set to True when allow_origins contains '*'")

allowed_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
allowed_headers = [
    "Authorization",
    "Content-Type",
    "Accept",
    "Origin",
    "X-Requested-With",
    "X-Title",
    "Access-Control-Request-Method",
    "Access-Control-Request-Headers"
]


# ── Startup environment validation ────────────────────────────────────────────

_REQUIRED_ENV_VARS = {
    "SUPABASE_URL": "Supabase project URL (e.g. https://xyz.supabase.co)",
    "SUPABASE_SERVICE_KEY": "Supabase service role key",
    "OPENROUTER_API_KEY": "OpenRouter API key for LLM scoring",
}

def validate_required_env() -> list[str]:
    """
    Check all required environment variables. Returns list of missing var names.
    Logs a clear STARTUP_ERROR for each missing var.
    Does NOT raise — caller decides whether to abort.
    """
    missing = []
    for var, description in _REQUIRED_ENV_VARS.items():
        # Check both raw os.environ and settings object
        raw = os.environ.get(var, "").strip()
        settings_val = ""
        if var == "SUPABASE_URL":
            settings_val = (settings.supabase_url or "").strip()
        elif var == "SUPABASE_SERVICE_KEY":
            settings_val = (settings.supabase_service_key or "").strip()
        elif var == "OPENROUTER_API_KEY":
            settings_val = (settings.openrouter_api_key or "").strip()

        value = raw or settings_val
        if not value:
            logger.error(
                "[STARTUP_ERROR] Required environment variable %s is not set. "
                "Description: %s. "
                "Set this in Render environment variables or .env file before deploying.",
                var, description,
            )
            missing.append(var)
        else:
            logger.info("[STARTUP_ENV] %s = configured", var)
    return missing


def log_startup_summary(missing_vars: list[str]) -> None:
    """Print a concise, human-readable startup summary to stderr."""
    proc = psutil.Process(os.getpid())
    rss = proc.memory_info().rss / (1024 * 1024)
    avail = psutil.virtual_memory().available / (1024 * 1024)
    cpu = proc.cpu_percent(interval=None)

    status_line = "✓ ALL REQUIRED VARS PRESENT" if not missing_vars else f"✗ MISSING VARS: {', '.join(missing_vars)}"

    summary = f"""
╔══════════════════════════════════════════════════════╗
║         HireMind AI — STARTUP SUMMARY                ║
╠══════════════════════════════════════════════════════╣
║  PID          : {os.getpid():<38}║
║  Python       : {sys.version.split()[0]:<38}║
║  RSS Memory   : {rss:>6.1f} MB                                 ║
║  Avail RAM    : {avail:>6.1f} MB                                 ║
║  CPU          : {cpu:>5.1f}%                                   ║
║  Embedding    : {settings.embedding_model:<38}║
║  App Env      : {settings.app_env:<38}║
║  Supabase URL : {(settings.supabase_url or 'NOT SET')[:38]:<38}║
║  CORS Origins : {str(settings.cors_origins)[:38]:<38}║
║  Env Vars     : {status_line:<38}║
╚══════════════════════════════════════════════════════╝
"""
    print(summary, file=sys.stderr, flush=True)
    logger.info("[STARTUP_SUMMARY] pid=%d rss=%.1fMB avail_ram=%.1fMB model=%s env=%s missing_vars=%s",
                os.getpid(), rss, avail, settings.embedding_model, settings.app_env,
                missing_vars or "none")



def log_deployment_diagnostics(label: str):
    try:
        process = psutil.Process(os.getpid())
        pid = process.pid
        uptime = time.time() - _startup_time
        
        # Memory Info
        mem_info = process.memory_info()
        rss = mem_info.rss / (1024 * 1024)
        vms = mem_info.vms / (1024 * 1024)
        
        # Peak memory (HWM on Linux)
        peak_hwm = 0.0
        if os.path.exists("/proc/self/status"):
            try:
                with open("/proc/self/status", "r") as f:
                    for line in f:
                        if line.startswith("VmHWM:"):
                            peak_hwm = float(line.split()[1]) / 1024
                            break
            except Exception:
                pass
                        
        cpu_usage = process.cpu_percent(interval=0.1)
        num_threads = process.num_threads()
        gc_stats = gc.get_stats()
        
        msg = f"""
==================================================
[DEPLOYMENT_DIAGNOSTICS] - {label}
Container PID: {pid}
Uptime: {uptime:.2f} seconds
Current RSS: {rss:.2f} MB
Peak RSS (HWM): {peak_hwm:.2f} MB
Virtual Memory (VMS): {vms:.2f} MB
CPU Usage: {cpu_usage:.1f}%
Thread Count: {num_threads}
GC Stats: {gc_stats}
==================================================
"""
        logger.info(msg)
        print(msg, flush=True)
        
        # Write to RenderDiagnosticsReport.md (Phase 10)
        try:
            diag_path = "C:\\Users\\HP\\.gemini\\antigravity-ide\\brain\\b099a49a-5f3b-44e9-8f48-c198d6c4ebba\\RenderDiagnosticsReport.md"
            with open(diag_path, "a", encoding="utf-8") as f:
                f.write(f"\n## Diagnostics: {label} ({datetime.now().isoformat()})\n")
                f.write(f"```\n{msg}\n```\n")
        except Exception:
            pass
    except Exception as e:
        logger.error("Failed to log diagnostics: %s", e)


def verify_ai_dependencies():
    """Legacy dependency check — kept for backward compatibility.
    The authoritative startup check is run_startup_check() in lifespan.
    """
    import traceback
    import importlib.util

    failed = []

    # FAISS
    try:
        import faiss  # noqa: F401
    except Exception as e:
        failed.append(("faiss", traceback.format_exc()))
        logger.error("FAISS Failed to load:\n%s", traceback.format_exc())

    # numpy
    try:
        import numpy  # noqa: F401
    except Exception as e:
        failed.append(("numpy", traceback.format_exc()))

    if settings.openrouter_api_key:
        logger.info("✓ OpenRouter key present")
    else:
        logger.warning("⚠ OpenRouter key is missing — LLM scoring will use fallback")

    if failed:
        logger.warning("[STARTUP_DIAGNOSTICS] AI dependency issues: %s",
                       [f[0] for f in failed])
    else:
        logger.info("[STARTUP_DIAGNOSTICS] Dependency check passed.")


async def run_startup_check() -> bool:
    """
    Verify all critical subsystems before accepting traffic.

    Prints a JOB SYSTEM READINESS block and a STARTUP CHECK table to stdout.
    Returns True if all checks passed, False if any critical check failed.
    Aborts startup (sys.exit(1)) if critical job system check fails.
    """
    import traceback
    import importlib.util
    import sys
    from app.services.job_manager import DBConnection, JobManager, LockResult
    from app.api.v1.endpoints.platform import supabase_client as _sc

    checks: list[tuple[str, bool, str]] = []   # (label, passed, detail)

    # 1. asyncpg import check
    asyncpg_ok = False
    asyncpg_version = "missing"
    try:
        import asyncpg
        asyncpg_ok = True
        asyncpg_version = asyncpg.__version__
        checks.append(("Import:asyncpg", True, f"version {asyncpg_version}"))
        logger.info("[DEPENDENCY_CHECK] asyncpg=%s status=available", asyncpg_version)
    except Exception as exc:
        checks.append(("Import:asyncpg", False, str(exc)))
        logger.error("[DEPENDENCY_CHECK] asyncpg status=missing error=%s", exc)

    # 2. Database Connectivity
    db_ok = False
    if asyncpg_ok:
        try:
            async with DBConnection() as conn:
                val = await conn.fetchval("SELECT 1")
                if val == 1:
                    db_ok = True
                    checks.append(("Database Connection", True, "SELECT 1 succeeded"))
                    logger.info("[DATABASE_CONNECTION_CHECK] success=true")
                else:
                    checks.append(("Database Connection", False, f"Unexpected SELECT 1 result: {val}"))
                    logger.error("[DATABASE_CONNECTION_CHECK] success=false detail=unexpected_result")
        except Exception as exc:
            checks.append(("Database Connection", False, str(exc)))
            logger.error("[DATABASE_CONNECTION_CHECK] success=false detail=%s", exc)
    else:
        checks.append(("Database Connection", False, "Skipped — asyncpg missing"))

    # 3. Schema Check
    schema_ok = False
    if db_ok:
        try:
            manager = JobManager.get_instance()
            schema_ok = await manager.ensure_db_schema()
            checks.append(("Database Schema Check", schema_ok, "Schema verified" if schema_ok else "Schema invalid"))
        except Exception as exc:
            checks.append(("Database Schema Check", False, str(exc)))
    else:
        checks.append(("Database Schema Check", False, "Skipped — DB connection missing"))

    # 4. Supabase API Connectivity
    supabase_ok = False
    try:
        _sc.table("projects").select("id").limit(1).execute()
        supabase_ok = True
        checks.append(("Supabase API Connectivity", True, ""))
    except Exception as exc:
        checks.append(("Supabase API Connectivity", False, str(exc)[:80]))

    # 5. Lock mechanism readiness
    locking_ok = False
    lock_detail = ""
    if db_ok and schema_ok:
        try:
            import uuid
            # Query an existing project to test locking
            async with DBConnection() as conn:
                project_row = await conn.fetchrow("SELECT id FROM public.projects LIMIT 1")
                if project_row:
                    test_project_id = str(project_row["id"])
                    created_test_project = False
                else:
                    # Create a temporary project
                    test_project_id = str(uuid.uuid4())
                    try:
                        await conn.execute(
                            "INSERT INTO public.projects (id, name, created_at, updated_at) VALUES ($1, $2, NOW(), NOW())",
                            uuid.UUID(test_project_id), "Startup Lock Test Project"
                        )
                        created_test_project = True
                    except Exception as proj_exc:
                        logger.warning("[LOCK_MECHANISM_CHECK] Could not insert test project, using dummy UUID: %s", proj_exc)
                        created_test_project = False

                try:
                    # Test lock acquisition
                    lock_res, job_id, owner_id = await manager.acquire_lock(test_project_id, None, "startup_check")
                    if lock_res == LockResult.ACQUIRED:
                        # Clean up the job we inserted
                        await conn.execute(
                            "DELETE FROM public.background_jobs WHERE id = $1",
                            uuid.UUID(job_id)
                        )
                        # Remove from progress cache
                        manager._progress_cache.pop(test_project_id, None)
                        locking_ok = True
                        lock_detail = "Lock acquire and release test passed"
                    elif lock_res == LockResult.ALREADY_HELD:
                        locking_ok = True
                        lock_detail = f"Lock mechanism verified (already held by {owner_id})"
                    else:
                        lock_detail = f"Lock result: {lock_res}"
                finally:
                    if created_test_project:
                        try:
                            await conn.execute("DELETE FROM public.projects WHERE id = $1", uuid.UUID(test_project_id))
                        except Exception as del_exc:
                            logger.warning("[LOCK_MECHANISM_CHECK] Failed to delete test project: %s", del_exc)
        except Exception as exc:
            lock_detail = f"Lock test failed: {exc}"
            logger.error("[LOCK_MECHANISM_CHECK] success=false error=%s", exc)

    checks.append(("Lock Mechanism Readiness", locking_ok, lock_detail))

    # Critical Job system check
    job_system_ready = asyncpg_ok and db_ok and schema_ok and locking_ok and supabase_ok

    # Other legacy checks
    for pkg in ("fastapi", "pydantic", "numpy"):
        try:
            importlib.import_module(pkg)
            checks.append((f"Import:{pkg}", True, ""))
        except Exception:
            checks.append((f"Import:{pkg}", False, traceback.format_exc()[-120:]))

    # ── FAISS ─────────────────────────────────────────────────────────────────
    try:
        import faiss  # noqa: F401
        checks.append(("FAISS", True, ""))
    except Exception:
        checks.append(("FAISS", False, traceback.format_exc()[-120:]))

    # ── background_jobs table ─────────────────────────────────────────────────
    try:
        _sc.table("background_jobs").select("id").limit(1).execute()
        checks.append(("background_jobs table", True, ""))
    except Exception:
        checks.append(("background_jobs table", False, str(sys.exc_info()[1])[:80]))

    # ── projects table ────────────────────────────────────────────────────────
    try:
        _sc.table("projects").select("id").limit(1).execute()
        checks.append(("projects table", True, ""))
    except Exception:
        checks.append(("projects table", False, str(sys.exc_info()[1])[:80]))

    # ── Storage ───────────────────────────────────────────────────────────────
    try:
        from app.services.storage_provider import StorageService
        StorageService.file_exists("candidate-files", "_startup_probe")
        checks.append(("Storage", True, ""))
    except Exception:
        checks.append(("Storage", False, str(sys.exc_info()[1])[:80]))

    # ── OpenRouter key present ────────────────────────────────────────────────
    openrouter_ok = bool(settings.openrouter_api_key)
    checks.append(("OpenRouter key", openrouter_ok, "" if openrouter_ok else "OPENROUTER_API_KEY not set — LLM scoring disabled"))

    # Print beautiful readiness block to stdout
    print("\n================ JOB SYSTEM READINESS ================\n", flush=True)
    print(f"asyncpg             {'PASS' if asyncpg_ok else 'FAIL'} ({asyncpg_version})", flush=True)
    print(f"database            {'PASS' if db_ok else 'FAIL'}", flush=True)
    print(f"job schema          {'PASS' if schema_ok else 'FAIL'}", flush=True)
    print(f"distributed locking {'PASS' if locking_ok else 'FAIL'}", flush=True)
    print(f"Supabase            {'PASS' if supabase_ok else 'FAIL'}", flush=True)
    print(f"\nJOB SYSTEM READY = {str(job_system_ready).upper()}\n", flush=True)
    print("=======================================================\n", flush=True)

    # ── Print table ───────────────────────────────────────────────────────────
    all_critical_pass = all(ok for label, ok, _ in checks
                            if label not in ("OpenRouter key",))   # OpenRouter is non-fatal

    width = 60
    sep = "─" * width
    lines = [
        "",
        "┌" + sep + "┐",
        "│  STARTUP CHECK" + " " * (width - 15) + "│",
        "├" + sep + "┤",
    ]
    for label, ok, detail in checks:
        mark = "✓" if ok else "✗"
        status_str = "PASS" if ok else "FAIL"
        row = f"│  {mark} {label:<28} {status_str}"
        row = row + " " * (width - len(row) + 1) + "│"
        lines.append(row)
        if not ok and detail:
            truncated = detail[:width - 6]
            lines.append(f"│    ↳ {truncated:<{width-6}}│")

    lines += [
        "├" + sep + "┤",
        f"│  Ready = {'TRUE' if all_critical_pass else 'FALSE'}" + " " * (width - 14) + "│",
        "└" + sep + "┘",
        "",
    ]
    report = "\n".join(lines)
    try:
        print(report, flush=True)
    except UnicodeEncodeError:
        try:
            print(report.encode('ascii', errors='replace').decode('ascii'), flush=True)
        except Exception:
            pass
    logger.info("[STARTUP_CHECK] all_critical_pass=%s checks=%d",
                all_critical_pass, len(checks))
    for label, ok, detail in checks:
        if not ok:
            logger.error("[STARTUP_CHECK_FAIL] %s — %s", label, detail)

    if not job_system_ready:
        logger.critical("[JOB_SYSTEM_FATAL] Critical startup check failed. Job system is not ready.")

    return all_critical_pass and job_system_ready


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Install asyncio exception handler on the running event loop ──────────
    try:
        _loop = asyncio.get_event_loop()
        _loop.set_exception_handler(_asyncio_exception_handler)
        logger.info("[WORKER_STARTED] pid=%d loop=%s", os.getpid(), _loop)

        from app.services import model_service
        model_service.set_main_loop(_loop)
    except Exception as exc:
        logger.warning("[WORKER_STARTED] Could not install asyncio exception handler: %s", exc)

    print(f"[WORKER_STARTED] pid={os.getpid()} uptime=0s", flush=True)

    # ── faulthandler: dumps C-level tracebacks on SIGSEGV / SIGABRT ──────────
    try:
        import faulthandler
        faulthandler.enable()
        logger.info("[DIAGNOSTIC] faulthandler enabled — C-level stack traces will print on SIGSEGV")
    except Exception as exc:
        logger.warning("[DIAGNOSTIC] faulthandler.enable() failed: %s", exc)

    # ── tracemalloc: available for on-demand memory profiling ─────────────────
    try:
        import tracemalloc
        tracemalloc.start(25)  # keep 25-frame traceback
        logger.info("[DIAGNOSTIC] tracemalloc started (25-frame depth)")
    except Exception as exc:
        logger.warning("[DIAGNOSTIC] tracemalloc.start() failed: %s", exc)

    # ── Start heartbeat thread ────────────────────────────────────────────────
    _start_heartbeat(interval_seconds=30.0)

    # ── Step 1: Validate env vars — fast, no I/O ─────────────────────────────
    missing_vars = validate_required_env()
    log_startup_summary(missing_vars)

    # ── Step 2: Record RSS at API-ready point ────────────────────────────────
    # Model is NOT loaded here. It loads lazily on the first request that needs
    # embeddings. This keeps startup RSS ~130-150 MB instead of ~950 MB.
    _rss_at_ready = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    _api_ready_time = time.time()
    logger.info(
        "[STARTUP_PERF] API ready in %.2fs | RSS=%.1fMB "
        "(model NOT preloaded — lazy-load on first embedding request)",
        _api_ready_time - _startup_time, _rss_at_ready,
    )
    print(
        f"[STARTUP_PERF] API accepting requests — "
        f"elapsed={_api_ready_time - _startup_time:.2f}s RSS={_rss_at_ready:.1f}MB "
        f"(model state: unloaded — lazy load enabled)",
        flush=True,
    )

    # ── Step 3: Model preload INTENTIONALLY REMOVED ───────────────────────────
    # preload_model_singleton() was removed from startup to eliminate the
    # ~950 MB RSS spike that caused Railway OOM kills.
    #
    # Previous behaviour:  startup → preload in background thread → RSS 946 MB → OOM
    # New behaviour:       startup → RSS ~150 MB → first indexing/analysis request
    #                      → lazy load → RSS ~550-600 MB → stays resident
    #
    # The model is loaded exactly once (singleton) on the first call to
    # model_service.get_model(), which is triggered by _get_encoder() in platform.py.
    # Concurrent requests wait on the same threading.Event — no duplicate loads.
    logger.info(
        "[STARTUP] Model preload skipped — lazy load mode active. "
        "model=%s will load on first embedding request.",
        settings.embedding_model,
    )

    # ── Step 4: Run startup checks synchronously inside lifespan ──
    from app.core.startup_state import (
        mark_api_ready,
        mark_startup_check_complete,
        mark_initialization_complete,
    )

    try:
        startup_ok = await run_startup_check()
        if not startup_ok:
            logger.critical("[JOB_SYSTEM_FATAL] Critical startup check failed. Job system is not ready. Aborting startup.")
            raise RuntimeError("Critical startup check failed. Job system is not ready.")

        mark_api_ready()
        mark_startup_check_complete(ok=True)

        # Run startup initialization (locks clearance, stale job cleanup, etc.)
        from app.api.v1.endpoints.platform import run_startup_initialization
        await run_startup_initialization()
        mark_initialization_complete()
        logger.info("[STARTUP] Lifespan startup check and initialization completed successfully.")

        # Model service diagnostics (runs synchronously here, non-blocking / won't trigger load)
        try:
            from app.services import model_service as _ms_mod
            _ms_state  = _ms_mod.get_load_state()
            _ms_name   = _ms_mod.get_model_name() or settings.embedding_model
            _ms_rss    = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
            _ms_threads = threading.active_count()
            print(
                f"\n============================== MODEL SERVICE DIAGNOSTICS ==============================\n"
                f"  Import OK    : OK\n"
                f"  Load State   : {_ms_state}\n"
                f"  Model Name   : {_ms_name}\n"
                f"  Current RAM  : {_ms_rss:.1f} MB\n"
                f"  Thread Count : {_ms_threads}\n"
                f"==============================",
                flush=True,
            )
            logger.info(
                "[MODEL_SERVICE_DIAGNOSTICS] state=%s name=%s ram=%.1fMB threads=%d",
                _ms_state, _ms_name, _ms_rss, _ms_threads,
            )
        except Exception as exc:
            logger.warning("[STARTUP] model diagnostics error: %s", exc)

        try:
            log_deployment_diagnostics("STARTUP_COMPLETE")
        except Exception as exc:
            logger.warning("[STARTUP] log_deployment_diagnostics error: %s", exc)

    except Exception as exc:
        logger.critical("[STARTUP_FATAL] Startup initialization encountered a fatal error: %s", exc)
        raise exc

    # Log CORS config
    logger.info("[CORS_STARTUP] Allowed Origins: %s", allowed_origins)
    frontend_prod = "https://hiremind-gilt.vercel.app"
    if frontend_prod not in allowed_origins:
        logger.warning("[CORS_STARTUP] Production frontend URL %s missing from allowed origins!", frontend_prod)

    # ── Yield: server is now live and accepting requests ─────────────────────
    logger.info("[WORKER_READY] pid=%d rss=%.1fMB uptime=%.2fs",
                os.getpid(), psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024),
                time.time() - _startup_time)
    print(f"[WORKER_READY] pid={os.getpid()}", flush=True)
    yield
    logger.info("[WORKER_EXIT] pid=%d uptime=%.1fs", os.getpid(), time.time() - _startup_time)
    print(f"[WORKER_EXIT] pid={os.getpid()}", flush=True)

    # ── Shutdown ──────────────────────────────────────────────────────────────
    async def perform_shutdown_cleanups():
        print("\n[SHUTDOWN_START]", flush=True)
        logger.info("[SHUTDOWN_START] Signal Received: SIGTERM")

        try:
            from app.services.job_manager import JobManager, close_asyncpg_pool
            JobManager.get_instance().cancel_all_active_jobs()
            await close_asyncpg_pool()
        except Exception as e:
            logger.error("Failed to clean up background jobs or database pool: %s", e)

        try:
            from app.services.cache_service import CacheService
            CacheService.clear()
        except Exception:
            pass

        gc.collect()

        try:
            log_deployment_diagnostics("SHUTDOWN")
        except Exception:
            pass

        logging.shutdown()
        print("\n[SHUTDOWN_COMPLETE]", flush=True)

    try:
        await asyncio.wait_for(perform_shutdown_cleanups(), timeout=30.0)
    except asyncio.TimeoutError:
        print("\n✗ Shutdown timed out (>30s).", flush=True)
        logger.error("Graceful shutdown operations timed out.")


app = FastAPI(
    title="HireMind AI API",
    description="AI Recruiter Copilot — generic candidate analysis and ranking platform.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# 1. Register rate limit middleware first (so it runs last in request execution stack)
app.middleware("http")(rate_limit_middleware)

# 2. Register CORSMiddleware next (so it runs outer-level/first in execution stack)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=allowed_methods,
    allow_headers=allowed_headers,
)

# 3. Request logging middleware — logs every request with timing, status, and exceptions
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    import uuid as _uuid
    _t0 = time.time()
    _pid = os.getpid()
    _method = request.method
    _path = request.url.path
    _content_length = request.headers.get("content-length", "?")
    _rid = request.headers.get("x-request-id") or _uuid.uuid4().hex[:12]

    def _diag():
        try:
            proc = psutil.Process(_pid)
            rss = proc.memory_info().rss / (1024 * 1024)
            cpu = proc.cpu_percent(interval=None)
            nth = proc.num_threads()
            return rss, cpu, nth
        except Exception:
            return 0.0, 0.0, 0

    rss0, cpu0, nth0 = _diag()
    logger.info(
        "[REQUEST_START] rid=%s method=%s path=%s content_length=%s pid=%d rss=%.1fMB cpu=%.1f%% threads=%d",
        _rid, _method, _path, _content_length, _pid, rss0, cpu0, nth0,
    )

    try:
        response = await call_next(request)
        elapsed = time.time() - _t0
        rss1, cpu1, nth1 = _diag()
        logger.info(
            "[REQUEST_END] rid=%s method=%s path=%s status=%d elapsed=%.3fs "
            "rss=%.1fMB cpu=%.1f%% threads=%d",
            _rid, _method, _path, response.status_code, elapsed, rss1, cpu1, nth1,
        )
        if response.status_code >= 500:
            logger.error(
                "[REQUEST_FAILED] rid=%s method=%s path=%s status=%d elapsed=%.3fs "
                "rss=%.1fMB — server error",
                _rid, _method, _path, response.status_code, elapsed, rss1,
            )
        return response
    except Exception as exc:
        elapsed = time.time() - _t0
        rss1, cpu1, nth1 = _diag()
        tb_str = traceback.format_exc()
        logger.error(
            "[REQUEST_FAILED] rid=%s method=%s path=%s elapsed=%.3fs rss=%.1fMB "
            "exception=%s\n%s",
            _rid, _method, _path, elapsed, rss1, exc, tb_str,
        )
        # Log top memory allocations if tracemalloc is running
        try:
            import tracemalloc as _tm
            if _tm.is_tracing():
                snapshot = _tm.take_snapshot()
                top = snapshot.statistics("lineno")[:5]
                alloc_str = "\n".join(str(s) for s in top)
                logger.error("[TRACEMALLOC_TOP5] rid=%s\n%s", _rid, alloc_str)
        except Exception:
            pass
        from fastapi.responses import JSONResponse as _JR
        return _JR(
            status_code=500,
            content={"detail": f"Unhandled exception: {exc}", "request_id": _rid},
        )


# 4. Custom CORS Preflight Failure Logger Middleware
@app.middleware("http")
async def log_cors_preflight_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        origin = request.headers.get("origin")
        requested_method = request.headers.get("access-control-request-method")
        requested_headers = request.headers.get("access-control-request-headers")
        path = request.url.path
        
        logger.info(
            "[CORS_PREFLIGHT_AUDIT] Incoming preflight request: path=%s, origin=%s, method=%s, headers=%s",
            path, origin, requested_method, requested_headers
        )
        
        response = await call_next(request)
        
        has_cors = "access-control-allow-origin" in response.headers
        if not has_cors or response.status_code >= 400:
            reason = []
            if not origin:
                reason.append("Origin header missing")
            else:
                norm_origin = origin.strip().lower().rstrip("/")
                if norm_origin not in [o.lower() for o in allowed_origins]:
                    reason.append(f"Origin '{origin}' not in allowed list")
                    
            if requested_method and requested_method not in allowed_methods:
                reason.append(f"Method '{requested_method}' not in allowed methods")
                
            if requested_headers:
                allowed_hdrs_lower = [h.lower() for h in allowed_headers]
                req_hdrs_list = [h.strip().lower() for h in requested_headers.split(",") if h.strip()]
                for h in req_hdrs_list:
                    if h not in allowed_hdrs_lower:
                        reason.append(f"Header '{h}' not in allowed headers")
                        
            reason_str = "; ".join(reason) if reason else "Unknown rejection or missing Access-Control-Allow-Origin header"
            logger.warning(
                "[CORS_PREFLIGHT_FAILURE] Preflight rejected or failed: status=%s, origin=%s, path=%s, reason=%s",
                response.status_code, origin, path, reason_str
            )
        else:
            logger.info("[CORS_PREFLIGHT_SUCCESS] Preflight accepted: origin=%s, status=%s", origin, response.status_code)
        return response
        
    return await call_next(request)


# Global Exception Handlers ensuring CORS preservation (Task 4)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning("[VALIDATION_ERROR] Request validation failed: %s", exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.warning("[HTTP_EXCEPTION] HTTPException caught: status=%s, detail=%s", exc.status_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb_str = traceback.format_exc()
    try:
        proc = psutil.Process(os.getpid())
        rss = proc.memory_info().rss / (1024 * 1024)
        cpu = proc.cpu_percent(interval=None)
        nth = proc.num_threads()
    except Exception:
        rss = cpu = nth = 0
    logger.error(
        "[UNHANDLED_EXCEPTION] path=%s method=%s exception=%s "
        "rss=%.1fMB cpu=%.1f%% threads=%d\n%s",
        request.url.path, request.method, exc, rss, cpu, nth, tb_str,
        exc_info=False,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred.", "error": str(exc)},
    )


app.include_router(api_router, prefix="/api/v1")


@app.get("/")
@app.head("/")
async def root_status():
    return {"status": "healthy", "service": "hiremind-ai", "version": "2.5.0"}


@app.get("/health/cors", tags=["health"])
async def health_cors(request: Request):
    origin = request.headers.get("origin")
    is_allowed = False
    if origin:
        is_allowed = origin.strip().lower().rstrip("/") in [o.lower() for o in allowed_origins]
    return {
        "status": "healthy",
        "allowed_origins": allowed_origins,
        "allowed_methods": allowed_methods,
        "allowed_headers": allowed_headers,
        "credentials_enabled": True,
        "request_origin": origin,
        "is_origin_allowed": is_allowed
    }


@app.get("/ping", tags=["health"])
async def ping():
    """Lightweight keep-alive probe for UptimeRobot / BetterStack."""
    return {
        "status": "ok",
        "service": "HireMind",
        "uptime": round(time.time() - _startup_time, 2),
        "version": _git_sha,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/health", tags=["health"])
async def health_check_details():
    """Extended liveness and metrics diagnostics probe."""
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
        # Run a simple check (probe file presence)
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

    overall_health = "healthy" if db_status_str == "connected" and storage_status_str == "connected" else "unhealthy"
    
    return {
        "status": overall_health,
        "database_status": db_status_str,
        "storage_status": storage_status_str,
        "background_worker_status": worker_status_str,
        "queue_length": queue_len,
        "rss_memory_mb": round(rss_mb, 2),
        "cpu_percent": round(cpu_pct, 1),
        "version": _git_sha,
        "uptime": round(time.time() - _startup_time, 2),
        "current_indexing_jobs": indexing_jobs_list,
        "model_status": model_status_str
    }


@app.get("/ready", tags=["health"])
async def readiness_check():
    """Readiness probe to verify that critical dependencies are ready."""
    api_status = "healthy"
    
    # 2. Supabase
    supabase_status = "healthy"
    try:
        from app.api.v1.endpoints.platform import supabase_client as _sc
        _sc.table("projects").select("id").limit(1).execute()
    except Exception:
        supabase_status = "unhealthy"
        
    # 3. Database Connection
    database_status = "healthy"
    try:
        from app.services.job_manager import DBConnection
        async with DBConnection() as conn:
            val = await conn.fetchval("SELECT 1")
            if val != 1:
                database_status = "unhealthy"
    except Exception:
        database_status = "unhealthy"
        
    # 4. Job system & Distributed locking
    job_system_status = "healthy" if database_status == "healthy" else "unhealthy"
    distributed_locking_status = "healthy" if database_status == "healthy" else "unhealthy"
    
    # 5. Embedding model load state (do NOT load it)
    from app.services import model_service as _ms
    embedding_model_status = _ms.get_load_state()  # returns "unloaded", "loaded", "loading", or "failed"
    
    overall_ready = (
        api_status == "healthy" and 
        supabase_status == "healthy" and 
        database_status == "healthy" and 
        job_system_status == "healthy"
    )
    
    status_code = 200 if overall_ready else 503
    
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content={
            "api": api_status,
            "supabase": supabase_status,
            "database": database_status,
            "job_system": job_system_status,
            "distributed_locking": distributed_locking_status,
            "embedding_model": embedding_model_status
        }
    )
