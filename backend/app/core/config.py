"""Core configuration settings for CodeSentinel."""

from pydantic import Field, model_validator
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

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_QUEUE_NAME: str = "codesentinel_jobs"

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

    # Runner limits & Container execution configuration
    # Wall-clock timeout applied to every individual check execution (must be > 0).
    RUNNER_MAX_EXECUTION_TIME_SECONDS: int = Field(default=300, gt=0)
    # Hard cap on bytes captured from stdout+stderr per check before truncation (must be > 0).
    # Applied during streaming reads so OOM from adversarial output is prevented.
    RUNNER_MAX_OUTPUT_BYTES: int = Field(default=262144, gt=0)
    # Maximum number of checks that may run concurrently (advisory; enforced by Phase 2 worker).
    RUNNER_MAX_CONCURRENT_CHECKS: int = Field(default=4, gt=0)
    # Container execution runtime: 'docker', 'podman', or 'subprocess' (dev fallback only).
    RUNNER_CONTAINER_RUNTIME: str = "docker"
    # Container image used for isolated check execution.
    RUNNER_CONTAINER_IMAGE: str = "python:3.12-slim"
    # Container memory limit (e.g. '1g', '512m').
    RUNNER_CONTAINER_MEMORY_LIMIT: str = "1g"
    # Container CPU quota in fractional cores (e.g. 1.0 = 1 core).
    RUNNER_CONTAINER_CPU_LIMIT: float = Field(default=1.0, gt=0.0)
    # Container PID limit (prevents fork-bombs).
    RUNNER_CONTAINER_PID_LIMIT: int = Field(default=64, gt=0)
    # Container tmpfs size for writable /tmp.
    RUNNER_CONTAINER_TMPFS_SIZE: str = "64m"
    # Network access for checks: default False (--network none).
    RUNNER_NETWORK_ENABLED: bool = False
    # Read-only root filesystem (--read-only).
    RUNNER_CONTAINER_READ_ONLY_ROOT: bool = True
    # Container user: UID:GID (non-root).
    RUNNER_CONTAINER_USER: str = "10001:10001"
    # Drop all Linux capabilities (--cap-drop=ALL).
    RUNNER_CONTAINER_CAP_DROP_ALL: bool = True
    # Security option no-new-privileges.
    RUNNER_CONTAINER_NO_NEW_PRIVILEGES: bool = True
    # Fail closed when container execution is required but runtime/daemon unavailable.
    RUNNER_FAIL_CLOSED_ON_MISSING_RUNTIME: bool = True
    # Trusted root directory under which all check workspaces must reside.
    # In production, this must be explicitly configured to prevent arbitrary host mounts.
    RUNNER_WORKSPACE_ROOT: str = ""

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.CODESENTINEL_ENV == "production":
            if self.RUNNER_CONTAINER_RUNTIME.lower() == "subprocess":
                raise ValueError(
                    "Insecure configuration: RUNNER_CONTAINER_RUNTIME cannot be 'subprocess' in production. "
                    "Production execution of untrusted code strictly requires a container runtime ('docker' or 'podman')."
                )
            if not self.RUNNER_WORKSPACE_ROOT:
                raise ValueError(
                    "Insecure configuration: RUNNER_WORKSPACE_ROOT must be explicitly configured in production "
                    "to enforce strict workspace boundary containment."
                )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]


settings = Settings()
