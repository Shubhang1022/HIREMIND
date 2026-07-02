"""FastAPI application entry point — HireMind AI."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1.router import api_router
from app.middleware.rate_limit import rate_limit_middleware

logger = logging.getLogger(__name__)


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


    try:
        from app.api.v1.endpoints.platform import run_startup_initialization
        run_startup_initialization()
    except Exception as exc:
        logger.warning("Startup lifespan platform initialization error: %s", exc)

    yield


app = FastAPI(
    title="HireMind AI API",
    description="AI Recruiter Copilot — generic candidate analysis and ranking platform.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(rate_limit_middleware)
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
@app.head("/")
async def root_status():
    return {"status": "healthy", "service": "hiremind-ai", "version": "2.5.0"}


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
