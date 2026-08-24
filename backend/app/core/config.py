"""Application configuration, read from the environment.

Read and validated once at startup. If a required variable is missing, the app
fails immediately with a clear message instead of blowing up mid-request with
something incomprehensible.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# This file lives in backend/app/core/, so the project root is three levels up.
# The path is resolved from the file's own location and NOT from the working
# directory, so the .env is found whether uvicorn starts from backend/, from the
# repository root, or from anywhere else.
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Environment ---
    app_env: str = "development"
    log_level: str = "INFO"

    # --- Required ---
    # Deliberately without defaults: if they are missing, the app must not start.
    database_url: str
    redis_url: str
    secret_key: str

    # --- Ingest ---
    public_ingest_base: str = "http://localhost:8010"
    max_body_bytes: int = 1_048_576  # 1 MB
    anon_retention_hours: int = 72

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the settings, reading the environment once per process."""
    # mypy treats database_url, redis_url and secret_key as required constructor
    # arguments because they have no defaults. It is right about the static
    # signature and wrong about the runtime behaviour: pydantic-settings fills
    # them from the environment on instantiation.
    #
    # Only this error, and only on this line, is silenced. Giving the fields
    # defaults would be the wrong fix: being required is exactly what makes the
    # app fail at startup rather than mid-request.
    return Settings()  # type: ignore[call-arg]
