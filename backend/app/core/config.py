"""Core configuration settings for CodeSentinel."""

from typing import Literal

from pydantic import Field, PrivateAttr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
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
    SESSION_MAX_AGE_SECONDS: int = Field(default=604800, gt=0)
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    ALLOW_DEMO_DEV_FALLBACK: bool = True
    GITHUB_OAUTH_STATE_COOKIE_NAME: str = "codesentinel_oauth_state"
    GITHUB_OAUTH_STATE_MAX_AGE_SECONDS: int = Field(default=600, gt=0)
    GITHUB_OAUTH_AUTHORIZE_URL: str = "https://github.com/login/oauth/authorize"
    GITHUB_OAUTH_TOKEN_URL: str = "https://github.com/login/oauth/access_token"
    GITHUB_OAUTH_USER_URL: str = "https://api.github.com/user"
    GITHUB_OAUTH_REDIRECT_URI: str = ""
    FRONTEND_BASE_URL: str = "http://localhost:5173"

    # GitHub App
    GITHUB_APP_ID: str = ""
    GITHUB_APP_CLIENT_ID: str = ""
    GITHUB_APP_CLIENT_SECRET: str = ""
    GITHUB_WEBHOOK_SECRET: str = "development-webhook-secret"
    GITHUB_APP_PRIVATE_KEY_PATH: str = ""

    # AI Provider Selection
    # Supported: "nvidia" (NVIDIA NIM API), "nebius" (Nebius Token Factory)
    AI_PROVIDER: str = "nvidia"

    # NVIDIA NIM API (https://integrate.api.nvidia.com)
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_API_KEY: str = ""
    NVIDIA_MODEL_ID: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    NVIDIA_TIMEOUT_SECONDS: int = 60
    NVIDIA_MAX_RETRIES: int = 2
    NVIDIA_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(default=3, ge=1, le=50)
    NVIDIA_CIRCUIT_BREAKER_RECOVERY_SECONDS: int = Field(default=30, ge=1, le=3600)

    # Nebius Token Factory & NVIDIA Open Model
    NEBIUS_TOKEN_FACTORY_BASE_URL: str = "https://api.tokenfactory.nebius.com/v1"
    NEBIUS_TOKEN_FACTORY_API_KEY: str = ""
    NEBIUS_MODEL_ID: str = "nvidia/llama-3.1-nemotron-70b-instruct"
    NEBIUS_TIMEOUT_SECONDS: int = 45
    NEBIUS_MAX_RETRIES: int = 2
    NEBIUS_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(default=3, ge=1, le=50)
    NEBIUS_CIRCUIT_BREAKER_RECOVERY_SECONDS: int = Field(default=30, ge=1, le=3600)

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

    # Secret Management & KMS Integration (Phase 5)
    SECRET_PROVIDER: Literal[
        "env",
        "memory",
        "aws_secrets_manager",
        "gcp_secret_manager",
        "vault",
    ] = "env"
    SECRET_MANAGER_SECRET_ID: str = ""
    VAULT_ADDR: str = ""
    VAULT_TOKEN: str = ""
    # Cloud Secret Manager & Infrastructure KMS attribution.
    # Note: CodeSentinel retrieves managed secrets via Cloud Secret Managers
    # (AWS Secrets Manager, GCP Secret Manager, or HashiCorp Vault). In cloud infrastructure,
    # those secrets may be encrypted at rest using Cloud KMS keys.
    # CodeSentinel does not perform direct application-level KMS envelope encryption (e.g. kms:Decrypt).
    KMS_KEY_ID: str = ""

    _secrets_resolved: bool = PrivateAttr(default=False)

    def initialize_secrets(self, provider: object | None = None) -> dict[str, str]:
        """Resolve secrets dynamically from the configured KMS/Secret provider."""
        from app.core.secrets import resolve_and_inject_secrets

        resolved = resolve_and_inject_secrets(self, provider=provider)  # type: ignore[arg-type]
        self._secrets_resolved = True
        return resolved

    def validate_production_secrets(self) -> None:
        """Validate production secrets for cryptographic strength and absence of development defaults."""
        db_url = self.DATABASE_URL.lower()
        if (
            "codesentinel_password" in db_url
            or "codesentinel_dev" in db_url
            or db_url.startswith("sqlite")
            or "sqlite+aiosqlite" in db_url
        ):
            raise ValueError(
                "Insecure configuration: DATABASE_URL retains default development credentials, "
                "database name, or SQLite in production."
            )

        session_key = self.SESSION_SECRET_KEY.strip()
        if not session_key:
            raise ValueError(
                "Insecure configuration: SESSION_SECRET_KEY must not be empty or whitespace in production."
            )
        if (
            session_key == "development-secret-key-replace-in-production-32-chars-min"
            or len(session_key) < 32
        ):
            raise ValueError(
                "Insecure configuration: SESSION_SECRET_KEY must not use default development value and "
                "must be at least 32 characters in production."
            )

        webhook_secret = self.GITHUB_WEBHOOK_SECRET.strip()
        if not webhook_secret:
            raise ValueError(
                "Insecure configuration: GITHUB_WEBHOOK_SECRET must not be empty or whitespace in production."
            )
        if webhook_secret == "development-webhook-secret":
            raise ValueError(
                "Insecure configuration: GITHUB_WEBHOOK_SECRET must not use default development value in production."
            )
        if len(webhook_secret) < 32:
            raise ValueError(
                "Insecure configuration: GITHUB_WEBHOOK_SECRET must be at least 32 characters in production."
            )

    def validate_provider_configuration(self) -> None:
        """Validate that required parameters for cloud KMS providers are present in production."""
        if self.SECRET_PROVIDER == "vault" and not self.VAULT_ADDR:
            raise ValueError(
                "Insecure configuration: VAULT_ADDR must be configured when SECRET_PROVIDER is 'vault'."
            )
        if (
            self.SECRET_PROVIDER in ("aws", "aws_secrets_manager")
            and not self.SECRET_MANAGER_SECRET_ID
        ):
            raise ValueError(
                "Insecure configuration: SECRET_MANAGER_SECRET_ID must be configured "
                "when SECRET_PROVIDER is 'aws_secrets_manager'."
            )
        if (
            self.SECRET_PROVIDER in ("gcp", "gcp_secret_manager")
            and not self.SECRET_MANAGER_SECRET_ID
        ):
            raise ValueError(
                "Insecure configuration: SECRET_MANAGER_SECRET_ID must be configured "
                "when SECRET_PROVIDER is 'gcp_secret_manager'."
            )

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
            if not self.COOKIE_SECURE:
                raise ValueError(
                    "Insecure configuration: COOKIE_SECURE must be True in production to ensure "
                    "session cookies are transmitted only over HTTPS."
                )
            if self.SECRET_PROVIDER == "env" or self._secrets_resolved:
                self.validate_production_secrets()
            else:
                self.validate_provider_configuration()
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def effective_ai_provider(self) -> str:
        prov = self.AI_PROVIDER.strip().lower() if self.AI_PROVIDER else ""
        if prov in ("nvidia", "nvidia_nim"):
            return "nvidia"
        if prov in ("nebius", "nebius_token_factory"):
            return "nebius_token_factory"
        if self.NVIDIA_API_KEY and not self.NEBIUS_TOKEN_FACTORY_API_KEY:
            return "nvidia"
        if self.NEBIUS_TOKEN_FACTORY_API_KEY and not self.NVIDIA_API_KEY:
            return "nebius_token_factory"
        return "nebius_token_factory"


settings = Settings()
