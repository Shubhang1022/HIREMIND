"""FastAPI application entry point — HireMind AI."""

import asyncio
import logging
import os
import signal
import sys
import time
import gc
import threading
import traceback
from contextlib import asynccontextmanager
from datetime import datetime

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
    future = context.get("future")
    task = context.get("task")
    task_name = getattr(task, "get_name", lambda: "?")() if task else "?"
    tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else msg
    logger.critical(
        "[ASYNC_EXCEPTION] Unhandled exception in asyncio task=%s message=%s\n%s",
        task_name, msg, tb_str,
    )
    print(f"[ASYNC_EXCEPTION] task={task_name} {type(exc).__name__ if exc else msg}", flush=True)

# Applied to the event loop after it is created (see lifespan)


# ── Signal handlers ──────────────────────────────────────────────────────────

def _make_signal_handler(sig_name: str):
    def _handler(signum, frame):
        rss = 0.0
        try:
            rss = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
        except Exception:
            pass
        logger.info(
            "[SIGNAL_RECEIVED] signal=%s pid=%d rss=%.1fMB uptime=%.1fs",
            sig_name, os.getpid(), rss, time.time() - _startup_time,
        )
        print(f"[SIGNAL_RECEIVED] signal={sig_name} pid={os.getpid()}", flush=True)
        # Re-raise as KeyboardInterrupt so uvicorn's shutdown sequence runs
        raise KeyboardInterrupt(f"Signal {sig_name} received")
    return _handler


for _sig, _name in [(signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")]:
    try:
        signal.signal(_sig, _make_signal_handler(_name))
    except (OSError, ValueError):
        pass  # Some signals can't be caught in all contexts

# SIGQUIT (Linux only)
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
            logger.info("[STARTUP_ENV] %s = %s...", var, value[:8] if len(value) > 8 else "***")
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


def run_startup_check() -> bool:
    """
    Verify all critical subsystems before accepting traffic.

    Prints a STARTUP CHECK table to stdout.
    Returns True if all checks passed, False if any critical check failed.
    Never raises — always returns.
    """
    import traceback
    import importlib.util

    checks: list[tuple[str, bool, str]] = []   # (label, passed, detail)

    # ── Imports ───────────────────────────────────────────────────────────────
    for pkg in ("fastapi", "pydantic", "supabase", "sentence_transformers", "numpy"):
        try:
            importlib.import_module(pkg)
            checks.append((f"Import:{pkg}", True, ""))
        except Exception:
            checks.append((f"Import:{pkg}", False, traceback.format_exc()[-120:]))

    # ── model_service import ──────────────────────────────────────────────────
    try:
        import importlib as _il
        _il.import_module("app.services.model_service")
        checks.append(("Model Service", True, ""))
    except Exception:
        checks.append(("Model Service", False, traceback.format_exc()[-200:]))

    # ── FAISS ─────────────────────────────────────────────────────────────────
    try:
        import faiss  # noqa: F401
        checks.append(("FAISS", True, ""))
    except Exception:
        checks.append(("FAISS", False, traceback.format_exc()[-120:]))

    # ── Supabase DB ───────────────────────────────────────────────────────────
    try:
        from app.api.v1.endpoints.platform import supabase_client as _sc
        _sc.table("projects").select("id").limit(1).execute()
        checks.append(("Supabase DB", True, ""))
    except Exception:
        checks.append(("Supabase DB", False, str(sys.exc_info()[1])[:80]))

    # ── background_jobs table ─────────────────────────────────────────────────
    try:
        from app.api.v1.endpoints.platform import supabase_client as _sc2
        _sc2.table("background_jobs").select("id").limit(1).execute()
        checks.append(("background_jobs table", True, ""))
    except Exception:
        checks.append(("background_jobs table", False, str(sys.exc_info()[1])[:80]))

    # ── projects table ────────────────────────────────────────────────────────
    try:
        from app.api.v1.endpoints.platform import supabase_client as _sc3
        _sc3.table("projects").select("id").limit(1).execute()
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
    print(report, flush=True)
    logger.info("[STARTUP_CHECK] all_critical_pass=%s checks=%d",
                all_critical_pass, len(checks))
    for label, ok, detail in checks:
        if not ok:
            logger.error("[STARTUP_CHECK_FAIL] %s — %s", label, detail)

    return all_critical_pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Install asyncio exception handler on the running event loop ──────────
    try:
        _loop = asyncio.get_event_loop()
        _loop.set_exception_handler(_asyncio_exception_handler)
        logger.info("[WORKER_STARTED] pid=%d loop=%s", os.getpid(), _loop)
    except Exception as exc:
        logger.warning("[WORKER_STARTED] Could not install asyncio exception handler: %s", exc)

    print(f"[WORKER_STARTED] pid={os.getpid()} uptime=0s", flush=True)

    # ── Start heartbeat thread ────────────────────────────────────────────────
    _start_heartbeat(interval_seconds=30.0)

    # ── Step 1: Validate env vars — fast, no I/O ─────────────────────────────
    missing_vars = validate_required_env()
    log_startup_summary(missing_vars)

    # ── Step 2: Record pre-preload RSS for the performance report ─────────────
    _rss_before_preload = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    _api_ready_time = time.time()
    logger.info(
        "[STARTUP_PERF] API ready in %.2fs | RSS=%.1fMB (before model preload)",
        _api_ready_time - _startup_time, _rss_before_preload,
    )
    print(
        f"[STARTUP_PERF] API accepting requests — "
        f"elapsed={_api_ready_time - _startup_time:.2f}s RSS={_rss_before_preload:.1f}MB",
        flush=True,
    )

    # ── Step 3: Kick off non-blocking model preload in background thread ──────
    # preload_model_singleton() returns immediately — model loads in a daemon thread.
    # The /health endpoint reports model_state=loading until it completes.
    # NO blocking calls before yield — server starts accepting requests NOW.
    try:
        from app.api.v1.endpoints.platform import preload_model_singleton
        preload_model_singleton()
    except Exception as exc:
        logger.warning("[STARTUP] preload_model_singleton failed: %s", exc)

    # ── Step 4: Schedule deferred startup tasks as a background asyncio task ──
    # run_startup_check() makes network calls (Supabase, Storage).
    # Scheduling it as a background task means the server yields and becomes
    # ready immediately; the check runs after the first event-loop iteration.
    async def _deferred_startup():
        # Small yield so uvicorn finishes binding and starts accepting connections
        await asyncio.sleep(0.5)

        # Mark API as ready immediately — server is live and accepting requests
        try:
            from app.core.startup_state import (
                mark_api_ready,
                mark_startup_check_complete,
                mark_initialization_complete,
            )
            mark_api_ready()
            logger.info("[STARTUP_STATE] mark_api_ready() called")
        except Exception as exc:
            logger.warning("[STARTUP_STATE] mark_api_ready failed: %s", exc)

        startup_ok = False
        try:
            startup_ok = run_startup_check()
            if not startup_ok:
                logger.error(
                    "[STARTUP_FAILED] One or more critical subsystem checks failed. "
                    "Service is running but may be degraded."
                )
        except Exception as exc:
            logger.warning("[STARTUP] run_startup_check deferred error: %s", exc)
        finally:
            try:
                from app.core.startup_state import mark_startup_check_complete
                mark_startup_check_complete(ok=startup_ok)
                logger.info("[STARTUP_STATE] mark_startup_check_complete(ok=%s) called", startup_ok)
            except Exception as exc:
                logger.warning("[STARTUP_STATE] mark_startup_check_complete failed: %s", exc)

        try:
            from app.api.v1.endpoints.platform import run_startup_initialization
            await run_startup_initialization()
        except Exception as exc:
            logger.warning("[STARTUP] run_startup_initialization error: %s", exc)
        finally:
            try:
                from app.core.startup_state import mark_initialization_complete
                mark_initialization_complete()
                logger.info("[STARTUP_STATE] mark_initialization_complete() called")
            except Exception as exc:
                logger.warning("[STARTUP_STATE] mark_initialization_complete failed: %s", exc)

        # Model service diagnostics (runs ~0.5s after boot, non-blocking)
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

    async def _run_deferred_startup_safe():
        """Outer safety wrapper — no exception from _deferred_startup can escape to the event loop."""
        try:
            await _deferred_startup()
        except Exception as exc:
            logger.critical(
                "[WORKER_CRASH] _deferred_startup raised unhandled exception: %s\n%s",
                exc, traceback.format_exc(),
            )

    asyncio.create_task(_run_deferred_startup_safe())

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
            from app.services.job_manager import JobManager
            JobManager.get_instance().cancel_all_active_jobs()
        except Exception as e:
            logger.error("Failed to cancel background jobs: %s", e)

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
    _t0 = time.time()
    _pid = os.getpid()
    _method = request.method
    _path = request.url.path
    _content_length = request.headers.get("content-length", "?")

    logger.info(
        "[REQUEST_START] method=%s path=%s content_length=%s pid=%d",
        _method, _path, _content_length, _pid,
    )

    exc_info = None
    try:
        response = await call_next(request)
        elapsed = time.time() - _t0
        logger.info(
            "[REQUEST_END] method=%s path=%s status=%d elapsed=%.3fs",
            _method, _path, response.status_code, elapsed,
        )
        if response.status_code >= 500:
            logger.error(
                "[REQUEST_ERROR] method=%s path=%s status=%d elapsed=%.3fs — server error returned",
                _method, _path, response.status_code, elapsed,
            )
        return response
    except Exception as exc:
        elapsed = time.time() - _t0
        import traceback as _tb
        logger.error(
            "[REQUEST_EXCEPTION] method=%s path=%s elapsed=%.3fs error=%s\n%s",
            _method, _path, elapsed, exc, _tb.format_exc(),
        )
        from fastapi.responses import JSONResponse as _JR
        return _JR(
            status_code=500,
            content={"detail": f"Unhandled exception: {exc}"},
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
    logger.error("[UNHANDLED_EXCEPTION] Unhandled exception caught: %s", exc, exc_info=True)
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


@app.get("/health", tags=["health"])
async def detailed_health():
    import time as _time
    import threading as _threading

    # ── 1. Supabase connectivity ──────────────────────────────────────────────
    db_status = "healthy"
    db_error = None
    try:
        from app.api.v1.endpoints.platform import supabase_client
        supabase_client.table("projects").select("id").limit(1).execute()
    except Exception as e:
        db_status = "unhealthy"
        db_error = str(e)

    # ── 2. Storage connectivity ───────────────────────────────────────────────
    storage_status = "healthy"
    storage_error = None
    try:
        from app.services.storage_provider import StorageService
        StorageService.file_exists("candidate-files", "test-connectivity-probe")
    except Exception as e:
        storage_status = "unhealthy"
        storage_error = str(e)

    # ── 3. OpenRouter connectivity (lightweight key-presence check) ───────────
    openrouter_status = "configured" if settings.openrouter_api_key else "not_configured"

    # ── 4. Model singleton status ─────────────────────────────────────────────
    from app.services import model_service as _ms
    model_loaded = _ms.is_loaded()
    model_name = _ms.get_model_name() or settings.embedding_model
    model_state = _ms.get_load_state()
    model_error = str(_ms.get_load_error()) if _ms.get_load_error() else None

    # ── 5. FAISS availability ─────────────────────────────────────────────────
    faiss_available = False
    try:
        import faiss as _faiss  # noqa: F401
        faiss_available = True
    except ImportError:
        pass

    # ── 6. Memory telemetry ───────────────────────────────────────────────────
    rss_mb = 0.0
    cpu_pct = 0.0
    avail_mb = 0.0
    try:
        _proc = psutil.Process(os.getpid())
        rss_mb = _proc.memory_info().rss / (1024 * 1024)
        cpu_pct = _proc.cpu_percent(interval=None)
        avail_mb = psutil.virtual_memory().available / (1024 * 1024)
    except Exception:
        pass

    # ── 7. Background jobs ────────────────────────────────────────────────────
    active_jobs: list[dict] = []
    failed_recent = 0
    recovering_jobs = 0
    try:
        from app.services.job_manager import JobManager
        from app.api.v1.endpoints.platform import supabase_client as _sc
        _jm = JobManager.get_instance()
        for pid, info in _jm._progress_cache.items():
            active_jobs.append({
                "project_id": pid,
                "status": info.get("status"),
                "stage": info.get("current_stage"),
                "progress": info.get("progress_percentage"),
                "processed": info.get("processed_candidates"),
                "total": info.get("total_candidates"),
            })
        _fjobs = _sc.table("background_jobs").select("id").eq("status", "failed").execute()
        failed_recent = len(_fjobs.data) if _fjobs.data else 0
        _rjobs = _sc.table("background_jobs").select("id").eq("status", "retrying").execute()
        recovering_jobs = len(_rjobs.data) if _rjobs.data else 0
    except Exception:
        pass

    # ── 8. In-process ranking cache size ─────────────────────────────────────
    cache_size = 0
    try:
        from app.api.v1.endpoints.platform import _backend_ranking_cache
        cache_size = len(_backend_ranking_cache)
    except Exception:
        pass

    # ── 9. Thread count ───────────────────────────────────────────────────────
    thread_count = _threading.active_count()

    # ── Overall status ────────────────────────────────────────────────────────
    overall = "healthy"
    if db_status == "unhealthy" or storage_status == "unhealthy":
        overall = "degraded"
    if not model_loaded:
        overall = "degraded" if overall == "healthy" else overall

    return {
        "status": overall,
        "timestamp": _time.time(),
        "uptime_seconds": round(_time.time() - _startup_time, 1),
        "database": {"status": db_status, "error": db_error},
        "storage": {"status": storage_status, "error": storage_error},
        "openrouter": {"status": openrouter_status, "ready": openrouter_status == "configured"},
        "model": {
            "loaded": model_loaded,
            "model_state": model_state,
            "cached": model_loaded,
            "name": model_name,
            "error": model_error,
            "configured_model": settings.embedding_model,
        },
        "faiss": {"available": faiss_available, "faiss_loaded": faiss_available},
        "memory": {
            "rss_mb": round(rss_mb, 1),
            "available_mb": round(avail_mb, 1),
            "safety_limit_mb": 450.0,
            "under_threshold": rss_mb < 450.0,
        },
        "cpu_percent": round(cpu_pct, 1),
        "threads": thread_count,
        "background_jobs": {
            "active": active_jobs,
            "active_count": len(active_jobs),
            "failed_jobs": failed_recent,
            "recovering_jobs": recovering_jobs,
        },
        "ranking_cache_size": cache_size,
        "supabase_ready": db_status == "healthy",
        "storage_ready": storage_status == "healthy",
        "openrouter_ready": openrouter_status == "configured",
        "model_loaded": model_loaded,
    }
