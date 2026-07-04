"""FastAPI application entry point — HireMind AI."""

import asyncio
import logging
import os
import sys
import time
import gc
import threading
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



_startup_time = time.time()

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
    # ── Step 1: Validate required environment variables ──────────────────────
    missing_vars = validate_required_env()
    log_startup_summary(missing_vars)

    # ── Step 2: Full STARTUP CHECK (subsystem verification table) ────────────
    startup_ok = run_startup_check()
    if not startup_ok:
        logger.error(
            "[STARTUP_FAILED] One or more critical subsystem checks failed. "
            "The service will start but may not function correctly. "
            "Fix the issues above and redeploy."
        )

    # ── Step 3: Deployment diagnostics ───────────────────────────────────────
    log_deployment_diagnostics("STARTUP")

    import sys
    import os
    from pathlib import Path

    # Verify OpenRouter Key Loaded (Requirement 2)
    key_val = settings.openrouter_api_key
    key_preview = key_val[:12] if key_val else "MISSING"

    # Check key source
    env_file_path = Path(__file__).resolve().parent.parent.parent / ".env"
    has_system_env = "OPENROUTER_API_KEY" in os.environ
    env_file_key = None
    if env_file_path.is_file():
        try:
            with open(env_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("OPENROUTER_API_KEY="):
                        env_file_key = line.split("=", 1)[1].strip()
                        if env_file_key.startswith(('"', "'")) and env_file_key.endswith(('"', "'")):
                            env_file_key = env_file_key[1:-1]
        except Exception:
            pass

    source = "system environment"
    if env_file_key and key_val == env_file_key:
        source = ".env file"
    elif has_system_env:
        source = "system environment (overridden by custom source priority)"

    print(f"OpenRouter Key Loaded: {key_preview}... (Source: {source})", file=sys.stderr, flush=True)
    logger.warning("OpenRouter Key Loaded: %s... (Source: %s)", key_preview, source)

    # Log normalized origin list (Task 3)
    logger.info("[CORS_STARTUP] Normalized CORS Allowed Origins: %s", allowed_origins)
    print(f"[CORS_STARTUP] Normalized CORS Allowed Origins: {allowed_origins}", file=sys.stderr, flush=True)

    # Compare Origin vs Configured Frontend URL (Task 7)
    frontend_prod = "https://hiremind-gilt.vercel.app"
    if frontend_prod not in allowed_origins:
        logger.warning("[CORS_STARTUP] Production frontend URL %s is missing from CORS allowed origins!", frontend_prod)
        print(f"[CORS_STARTUP_WARNING] Production frontend URL {frontend_prod} is missing from CORS allowed origins!", file=sys.stderr, flush=True)

    try:
        from app.api.v1.endpoints.platform import run_startup_initialization, preload_model_singleton
        # model_service was already validated in run_startup_check(); no need to re-import.
        # Kick off non-blocking model preload in background thread immediately.
        preload_model_singleton()
        await run_startup_initialization()

        # ── Task 8: MODEL SERVICE DIAGNOSTICS block ───────────────────────────
        # Wait up to 2 seconds for the preload thread to get past its first check
        # (either cache-hit fast path or has-started-downloading state).
        await asyncio.sleep(0.1)

        try:
            from app.services import model_service as _ms_mod
            _ms_loaded   = _ms_mod.is_loaded()
            _ms_name     = _ms_mod.get_model_name() or settings.embedding_model
            _ms_state    = _ms_mod.get_load_state()
            _ms_err      = _ms_mod.get_load_error()
            _ms_rss      = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
            _ms_threads  = threading.active_count()
            _ms_import   = "OK"
        except Exception as _diag_exc:
            _ms_loaded = False; _ms_name = "unknown"; _ms_state = "error"
            _ms_err = _diag_exc; _ms_rss = 0.0; _ms_threads = 0
            _ms_import = f"FAILED: {_diag_exc}"

        _diag_block = f"""
============================== MODEL SERVICE DIAGNOSTICS ==============================
  Import OK         : {_ms_import}
  Singleton Created : {"YES" if _ms_loaded else "NOT YET (loading in background)"}
  Model Name        : {_ms_name}
  Load State        : {_ms_state}
  Load Error        : {_ms_err or "None"}
  Model Ready       : {_ms_loaded}
  Current RAM       : {_ms_rss:.1f} MB
  Thread Count      : {_ms_threads}
==============================
"""
        print(_diag_block, flush=True)
        logger.info("[MODEL_SERVICE_DIAGNOSTICS] import=%s state=%s loaded=%s name=%s ram=%.1fMB threads=%d",
                    _ms_import, _ms_state, _ms_loaded, _ms_name, _ms_rss, _ms_threads)

    except Exception as exc:
        logger.warning("Startup lifespan platform initialization error: %s", exc)

    # Print middleware stack execution order (Task 1)
    print("--- Audited Middleware Execution Order (First to Last) ---", file=sys.stderr, flush=True)
    for idx, m in enumerate(reversed(app.user_middleware)):
        print(f"[{idx}] Middleware Class: {m.cls.__name__}", file=sys.stderr, flush=True)

    # Programmatically generate RegisteredRoutes.md and MiddlewareOrder.md (Task 1 & 4)
    try:
        artifacts_dir = "C:\\Users\\HP\\.gemini\\antigravity-ide\\brain\\b099a49a-5f3b-44e9-8f48-c198d6c4ebba"
        os.makedirs(artifacts_dir, exist_ok=True)
        
        # RegisteredRoutes.md
        routes_lines = [
            "# Registered API Routes\n",
            "This document lists all active routes and HTTP methods registered on the FastAPI backend.\n",
            "| Route Path | HTTP Methods | Description |",
            "| :--- | :--- | :--- |"
        ]
        for route in app.routes:
            methods = ", ".join(route.methods) if getattr(route, "methods", None) else "ASGI App / Lifespan"
            desc = getattr(route, "description", None) or getattr(route, "summary", None) or "No description"
            routes_lines.append(f"| `{route.path}` | `{methods}` | {desc} |")
        with open(os.path.join(artifacts_dir, "RegisteredRoutes.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(routes_lines))
        logger.info("Successfully programmatically generated RegisteredRoutes.md")
        
        # MiddlewareOrder.md
        mw_lines = [
            "# Audited Middleware Order\n",
            "This document details the exact execution order of the middleware stack in the FastAPI application.\n",
            "```",
            "Incoming Request",
            "   ↓"
        ]
        for m in reversed(app.user_middleware):
            mw_lines.append(f"[{m.cls.__name__}]")
            mw_lines.append("   ↓")
        mw_lines.append("[FastAPI Router]")
        mw_lines.append("```\n")
        mw_lines.append("### Detailed Middleware Configuration")
        for m in app.user_middleware:
            mw_lines.append(f"* **Class**: `{m.cls.__module__}.{m.cls.__name__}`")
            opts = getattr(m, "options", None) or getattr(m, "kwargs", None)
            if opts:
                mw_lines.append(f"  * **Options**: `{opts}`")
        with open(os.path.join(artifacts_dir, "MiddlewareOrder.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(mw_lines))
        logger.info("Successfully programmatically generated MiddlewareOrder.md")
    except Exception as e:
        logger.warning("Could not generate RegisteredRoutes.md or MiddlewareOrder.md audit files: %s", e)

    yield
    # Log shutdown deployment diagnostics (Phase 10)
    async def perform_shutdown_cleanups():
        print("\n[SHUTDOWN_START]", flush=True)
        print("\nSignal Received:\nSIGTERM", flush=True)
        logger.info("[SHUTDOWN_START] Signal Received: SIGTERM")
        
        print("\nCancelling Active Background Jobs...", end="", flush=True)
        try:
            from app.services.job_manager import JobManager
            JobManager.get_instance().cancel_all_active_jobs()
            print("\n✓ Completed", flush=True)
        except Exception as e:
            print("\n✗ Failed", flush=True)
            logger.error("Failed to cancel background jobs: %s", e)

        print("\nPersisting Worker State...", end="", flush=True)
        # Background jobs persistence completed in JobManager cancel_all_active_jobs
        print("\n✓ Completed", flush=True)

        print("\nClosing Database Connections...", end="", flush=True)
        print("\n✓ Completed", flush=True)

        print("\nClosing Storage Clients...", end="", flush=True)
        print("\n✓ Completed", flush=True)

        print("\nCleaning Temporary Resources...", end="", flush=True)
        try:
            from app.services.cache_service import CacheService
            CacheService.clear()
            print("\n✓ Completed", flush=True)
        except Exception:
            print("\n✗ Failed", flush=True)

        print("\nRunning Garbage Collection (Best Effort)...", end="", flush=True)
        import gc
        gc.collect()
        print("\n✓ Completed", flush=True)

        try:
            log_deployment_diagnostics("SHUTDOWN")
        except Exception:
            pass

        print("\nFlushing Logs...", end="", flush=True)
        try:
            import logging
            logging.shutdown()
            print("\n✓ Completed", flush=True)
        except Exception:
            print("\n✗ Failed", flush=True)

        print("\n[SHUTDOWN_COMPLETE]", flush=True)
        print("\nWaiting for Uvicorn/FastAPI graceful termination...", flush=True)

    try:
        import asyncio
        await asyncio.wait_for(perform_shutdown_cleanups(), timeout=30.0)
    except asyncio.TimeoutError:
        print("\n✗ Shutdown operations timed out (>30s). Letting hosting platform terminate the container.", flush=True)
        logger.error("Graceful shutdown operations timed out. Incomplete cleanups logged.")


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

# 3. Custom CORS Preflight Failure Logger Middleware (Task 2)
# Register it last so it is the absolute outer layer, executing FIRST on requests
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
