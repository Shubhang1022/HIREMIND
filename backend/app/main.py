"""FastAPI application entry point — HireMind AI."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1.router import api_router
from app.middleware.rate_limit import rate_limit_middleware

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-warm the embedding model on startup in a background thread."""
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
        from app.api.v1.endpoints.platform import run_startup_initialization
        run_startup_initialization()
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
    import time
    from app.core.config import settings
    
    # 1. Check Supabase database connectivity
    db_status = "healthy"
    db_error = None
    try:
        from app.api.v1.endpoints.platform import supabase_client
        supabase_client.table("projects").select("id").limit(1).execute()
    except Exception as e:
        db_status = "unhealthy"
        db_error = str(e)
        
    # 2. Check Storage Service
    storage_status = "healthy"
    storage_error = None
    try:
        from app.services.storage_provider import StorageService
        StorageService.file_exists("candidate-files", "test-connectivity-probe")
    except Exception as e:
        storage_status = "unhealthy"
        storage_error = str(e)
        
    # 3. Model Status (lazy loaded or cached)
    model_cached = False
    model_name = settings.embedding_model
    try:
        from app.api.v1.endpoints.platform import _encoder
        if _encoder is not None and _encoder._model is not None:
            model_cached = True
    except Exception:
        pass
        
    # 4. Memory Telemetry
    try:
        from app.api.v1.endpoints.platform import get_memory_mb
        ram_mb = get_memory_mb()
    except Exception:
        ram_mb = 0.0
        
    overall_status = "healthy"
    if db_status == "unhealthy" or storage_status == "unhealthy":
        overall_status = "degraded"
        
    return {
        "status": overall_status,
        "timestamp": time.time(),
        "database": {
            "status": db_status,
            "error": db_error
        },
        "storage": {
            "status": storage_status,
            "error": storage_error
        },
        "model": {
            "configured_model": model_name,
            "is_cached_in_ram": model_cached
        },
        "memory": {
            "rss_ram_mb": round(ram_mb, 2),
            "safety_limit_mb": 450.0,
            "is_under_safety_threshold": ram_mb < 450.0
        }
    }
