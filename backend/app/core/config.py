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
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Upload limits
    max_upload_size_mb: int = 50
    upload_dir: str = "./uploads"


settings = Settings()
