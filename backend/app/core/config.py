"""Core configuration settings for CodeSentinel."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Runtime
    CODESENTINEL_ENV: str = "development"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Database
    DATABASE_URL: str = (
        "postgresql+psycopg://codesentinel:codesentinel_password@localhost:5432/codesentinel_dev"
    )
    TEST_DATABASE_URL: str = "sqlite+aiosqlite:///:memory:"

    # Redis & Queue
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_QUEUE_NAME: str = "codesentinel_jobs"
    # Supported: "database" (primary durable PostgreSQL/database queue with workers),
    #            "background" (local fallback using FastAPI in-process BackgroundTasks for analysis)
    QUEUE_BACKEND: Literal["database", "background"] = "database"

    # Worker configuration
    WORKER_POLL_INTERVAL_SECONDS: float = Field(default=1.0, gt=0.0, le=3600.0)
    WORKER_DEFAULT_JOB_TIMEOUT_SECONDS: int = Field(default=300, ge=1, le=86400)
    WORKER_DEFAULT_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=50)
    WORKER_DEFAULT_RETRY_DELAY_SECONDS: int = Field(default=5, ge=0, le=3600)
    WORKER_INTERRUPTED_RECOVERY_INTERVAL_SECONDS: int = Field(default=30, ge=1, le=3600)

    # Security & Session
    SESSION_SECRET_KEY: str = "development-secret-key-replace-in-production-32-chars-min"
    SESSION_COOKIE_NAME: str = "codesentinel_session"
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"

    # GitHub App
    GITHUB_APP_ID: str = ""
    GITHUB_APP_CLIENT_ID: str = ""
    GITHUB_APP_CLIENT_SECRET: str = ""
    GITHUB_WEBHOOK_SECRET: str = "development-webhook-secret"
    GITHUB_APP_PRIVATE_KEY_PATH: str = ""

    # Nebius Token Factory & NVIDIA Open Model
    NEBIUS_TOKEN_FACTORY_BASE_URL: str = "https://api.tokenfactory.nebius.com/v1"
    NEBIUS_TOKEN_FACTORY_API_KEY: str = ""
    NEBIUS_MODEL_ID: str = "nvidia/llama-3.1-nemotron-70b-instruct"
    NEBIUS_TIMEOUT_SECONDS: int = 45
    NEBIUS_MAX_RETRIES: int = 2

    # Runner limits
    RUNNER_MAX_EXECUTION_TIME_SECONDS: int = 300
    RUNNER_MAX_OUTPUT_BYTES: int = 262144

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]


settings = Settings()
