"""Application configuration via pydantic-settings."""

from __future__ import annotations

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

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"

    # Supabase
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""

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
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # App
    app_env: str = "development"
    debug: bool = False

    # AI / Ranking
    feature_cache_dir: str = "./feature_cache"
    submission_output: str = "./submission.csv"
    ranking_config_path: str = "./config/ranking_config.yaml"
    embedding_model: str = "BAAI/bge-large-en-v1.5"

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Upload limits
    max_upload_size_mb: int = 50
    upload_dir: str = "./uploads"


settings = Settings()
