from __future__ import annotations

"""Application configuration via pydantic-settings."""

from typing import Optional

from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


from pathlib import Path

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # Prioritize dotenv_settings over env_settings to ensure .env takes precedence (Requirement 2)
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)

    # Supabase
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""
    
    # Hugging Face
    hf_token: str | None = None

    # Feature Flags
    use_supabase_storage: bool = False
    use_local_storage: bool = True
    use_faiss_cache: bool = True
    use_background_indexing: bool = True
    use_openrouter: bool = True

    # Security
    secret_key: str = "changeme-in-production-use-a-long-random-string"

    # CORS
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> List[str]:
        origins = []
        raw_list = [o.strip().lower().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]
        for o in raw_list:
            if not o.startswith(("http://", "https://")):
                import logging
                logging.getLogger(__name__).warning("Rejecting invalid CORS origin format: %s", o)
                continue
            if o not in origins:
                origins.append(o)
        return origins

    # App
    app_env: str = "development"
    debug: bool = False

    # AI / Ranking
    feature_cache_dir: str = "./feature_cache"
    submission_output: str = "./submission.csv"
    ranking_config_path: str = "./config/ranking_config.yaml"
    # IMPORTANT: this default MUST match the model pre-downloaded in backend/Dockerfile.
    # Dockerfile bakes BAAI/bge-small-en-v1.5 (90 MB) into the image.
    # bge-base (438 MB) and bge-large (1.34 GB) cause OOM kills on Render free tier.
    # Override with EMBEDDING_MODEL_NAME env var if running on a larger instance.

    # ?? Render Free Tier Detection & Adaptive Configuration ??
    # Detect Render free tier environment to enable memory-constrained optimizations
    @property
    def is_render_free_tier(self) -> bool:
        """Detect if running on Render free tier (512MB RAM limit)."""
        import os
        # Check if RENDER env var is set AND memory limit indicators
        if os.getenv("RENDER") != "true":
            return False
        # Check for explicit free tier flag or memory constraints
        if os.getenv("RENDER_FREE_TIER") == "true":
            return True
        # Detect based on memory limit env vars (Render sets these)
        memory_limit = os.getenv("MEMORY_AVAILABLE", "").lower()
        if "512" in memory_limit or "0.5" in memory_limit:
            return True
        return False

    @property
    def adaptive_batch_size(self) -> int:
        """Return adaptive batch size: 4 for free tier, 16 for larger instances."""
        if self.is_render_free_tier:
            return 4
        return self.embedding_batch_size

    @property
    def adaptive_chunk_size(self) -> int:
        """Return adaptive chunk size: 32 for free tier, 64 for larger instances."""
        if self.is_render_free_tier:
            return 32
        return self.embedding_chunk_size

    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Memory tuning for Render free tier (512 MB)
    # 64 candidates/chunk balances ~2 min indexing vs peak RAM on 512 MB tier.
    embedding_chunk_size: int = 64
    embedding_batch_size: int = 16
    # Unload the embedding model from RAM after indexing completes.
    unload_model_after_indexing: bool = True
    # RSS threshold (MB) above which FAISS/LLM stages are skipped.
    # Circuit breaker threshold: reject indexing if RSS > this value
    memory_circuit_breaker_threshold_mb: float = 400.0
    # Safety threshold for skipping FAISS/LLM stages
    memory_safety_threshold_mb: float = 450.0

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Upload limits
    max_upload_size_mb: int = 50
    upload_dir: str = "./uploads"

    # Auto-resume rate limiting for Render free tier
    auto_resume_max_concurrent: int = 2  # Max concurrent auto-resume jobs
    auto_resume_base_delay: float = 15.0  # Base delay before auto-resume
    auto_resume_stagger_min: float = 5.0  # Min stagger delay per project
    auto_resume_stagger_max: float = 10.0  # Max stagger delay per project

    # Startup Recovery Configuration (Free-tier Optimization)
    # These settings prevent server crashes on startup by limiting aggressive auto-recovery
    # behavior when multiple background jobs or failed projects need recovery.
    # 
    # Context: On free-tier Render instances (512MB RAM), concurrent recovery of 10+ indexing
    # jobs can cause memory exhaustion and crash loops. These settings coordinate between
    # job_manager.py and platform.py auto_resume mechanisms to prevent resource exhaustion.
    #
    # MAX_RECOVERY_CONCURRENCY: Global limit on concurrent recovery operations across both
    #   job_manager retry mechanism and platform auto_resume mechanism. Set to 2 for free-tier
    #   instances to prevent memory exhaustion. Can be increased for higher-tier instances.
    #   Requirement 3.1
    MAX_RECOVERY_CONCURRENCY: int = 2  # Global limit during startup recovery

    # RECOVERY_JOB_DELAY_SECONDS: Minimum delay between scheduling recovery jobs during startup.
    #   Set to 5 minutes (300s) to allow proper cold start completion on free-tier instances
    #   where startup can take 30-60 seconds. Prevents scheduling multiple jobs before the
    #   system is fully initialized. Requirement 3.1
    RECOVERY_JOB_DELAY_SECONDS: int = 300  # 5-minute minimum delay between recovery jobs

    # MEMORY_CHECK_INTERVAL_SECONDS: How frequently to check available system memory during
    #   recovery operations. Used to detect low-memory conditions and defer recovery jobs
    #   when resources are constrained. Requirement 3.1
    MEMORY_CHECK_INTERVAL_SECONDS: int = 60  # How often to check memory during recovery

    # FREE_MEMORY_THRESHOLD_MB: Minimum free memory (in MB) required before scheduling new
    #   recovery jobs. If available memory drops below this threshold, recovery operations
    #   are deferred to prevent memory exhaustion. Set to 100MB to provide safety buffer
    #   for free-tier instances with 512MB total RAM. Requirement 3.1
    FREE_MEMORY_THRESHOLD_MB: int = 100  # Minimum free memory before pausing recovery

    # LLM-based candidate filtering (replaces embedding model)
    use_llm_filtering: bool = True  # Use LLM instead of embedding model for filtering
    llm_filter_batch_size: int = 10  # Candidates per LLM API call


settings = Settings()
