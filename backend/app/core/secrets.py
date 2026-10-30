"""Production Secret Governance and Secret Manager Integration for CodeSentinel.

Provides dynamic, zero-disk secret resolution from cloud Secret Management Services
(AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault, or in-memory injection),
safe credential masking, and candidate secret rotation validation.

Note on Key Management Services (KMS):
Secret payloads stored in AWS Secrets Manager or GCP Secret Manager may be encrypted
at rest by cloud infrastructure using Cloud KMS keys. CodeSentinel resolves credentials
via Secret Manager APIs and does not perform application-level KMS envelope operations
(e.g., direct kms:Decrypt API calls).
"""

from __future__ import annotations

import abc
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Keys identified as sensitive requiring automatic masking in logs and error traces
SENSITIVE_KEY_PATTERNS = [
    r"SECRET",
    r"PASSWORD",
    r"TOKEN",
    r"PRIVATE_KEY",
    r"CREDENTIAL",
    r"DATABASE_URL",
    r"API_KEY",
]

_SENSITIVE_RE = re.compile("|".join(SENSITIVE_KEY_PATTERNS), re.IGNORECASE)


class SecretString:
    """Wrapper for sensitive strings to prevent accidental leakage in logs or representations."""

    def __init__(self, value: str):
        self._value = value

    def get_secret_value(self) -> str:
        """Explicitly retrieve the raw secret string."""
        return self._value

    def __repr__(self) -> str:
        return "'***REDACTED***'"

    def __str__(self) -> str:
        return "***REDACTED***"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, SecretString):
            return self._value == other._value
        if isinstance(other, str):
            return self._value == other
        return False

    def __len__(self) -> int:
        return len(self._value)


def is_sensitive_key(key: str) -> bool:
    """Check if a configuration or environment key name corresponds to a sensitive secret."""
    return bool(_SENSITIVE_RE.search(key))


def sanitize_database_url(url: str) -> str:
    """Sanitize database connection URL by masking password component."""
    if not url or "@" not in url:
        return url
    try:
        parsed = urlparse(url)
        if parsed.password:
            netloc = parsed.netloc.replace(f":{parsed.password}@", ":****@")
            return parsed._replace(netloc=netloc).geturl()
    except Exception:  # noqa: BLE001
        pass
    # Fallback regex masking if URL parsing fails
    return re.sub(r":([^@]+)@", ":****@", url)


def redact_sensitive_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact sensitive values in dictionaries for safe logging and telemetry."""
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            redacted[key] = redact_sensitive_dict(value)
        elif is_sensitive_key(key):
            if key.upper() == "DATABASE_URL" and isinstance(value, str):
                redacted[key] = sanitize_database_url(value)
            else:
                redacted[key] = "***REDACTED***"
        elif isinstance(value, SecretString):
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted


# ──────────────────────────────────────────────────────────────────────────────
# Secret Provider Interface & Implementations
# ──────────────────────────────────────────────────────────────────────────────


class SecretProvider(abc.ABC):
    """Abstract base provider for loading configuration secrets."""

    @abc.abstractmethod
    def get_secret(self, key: str) -> str | None:
        """Retrieve a single secret by name."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_all_secrets(self) -> dict[str, str]:
        """Retrieve all managed secrets as a dictionary."""
        raise NotImplementedError


class EnvSecretProvider(SecretProvider):
    """Fallback provider resolving secrets from environment variables (local/dev)."""

    def get_secret(self, key: str) -> str | None:
        return os.environ.get(key)

    def get_all_secrets(self) -> dict[str, str]:
        return {k: v for k, v in os.environ.items() if is_sensitive_key(k)}


class InMemorySecretProvider(SecretProvider):
    """In-memory provider for testing and isolated runtime secret injection."""

    def __init__(self, secrets: dict[str, str] | None = None):
        self._secrets: dict[str, str] = dict(secrets or {})

    def set_secret(self, key: str, value: str) -> None:
        self._secrets[key] = value

    def get_secret(self, key: str) -> str | None:
        return self._secrets.get(key)

    def get_all_secrets(self) -> dict[str, str]:
        return dict(self._secrets)


class VaultSecretProvider(SecretProvider):
    """HashiCorp Vault KV Secret Engine v2 provider."""

    def __init__(
        self,
        vault_addr: str,
        vault_token: str,
        secret_path: str = "secret/data/codesentinel",
        mount_point: str = "secret",
        client: httpx.Client | None = None,
    ):
        self.vault_addr = vault_addr.rstrip("/")
        self.vault_token = vault_token
        self.secret_path = secret_path.lstrip("/")
        self.mount_point = mount_point
        self._client = client
        self._cached_secrets: dict[str, str] | None = None

    def _fetch_vault_secrets(self) -> dict[str, str]:
        if self._cached_secrets is not None:
            return self._cached_secrets

        url = f"{self.vault_addr}/v1/{self.secret_path}"
        headers = {
            "X-Vault-Token": self.vault_token,
            "Accept": "application/json",
        }

        try:
            client = self._client or httpx.Client(timeout=10.0)
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            # KV v2 stores data under data.data
            if "data" in data and isinstance(data["data"], dict):
                inner = data["data"].get("data", data["data"])
                self._cached_secrets = {str(k): str(v) for k, v in inner.items()}
            else:
                self._cached_secrets = {}
            return self._cached_secrets
        except Exception as exc:
            logger.error("Failed to load secrets from HashiCorp Vault: %s", type(exc).__name__)
            raise RuntimeError(f"Vault secret resolution failed: {exc}") from exc

    def get_secret(self, key: str) -> str | None:
        secrets = self._fetch_vault_secrets()
        return secrets.get(key)

    def get_all_secrets(self) -> dict[str, str]:
        return dict(self._fetch_vault_secrets())


class AwsSecretsManagerProvider(SecretProvider):
    """AWS Secrets Manager provider using boto3 when available or HTTP client."""

    def __init__(
        self,
        secret_id: str = "codesentinel/production",
        region_name: str = "us-east-1",
        boto_client: Any | None = None,
    ):
        self.secret_id = secret_id
        self.region_name = region_name
        self._client = boto_client
        self._cached_secrets: dict[str, str] | None = None

    def _fetch_aws_secrets(self) -> dict[str, str]:
        if self._cached_secrets is not None:
            return self._cached_secrets

        try:
            if self._client is not None:
                client = self._client
            else:
                import boto3

                client = boto3.client("secretsmanager", region_name=self.region_name)
            response = client.get_secret_value(SecretId=self.secret_id)
            if "SecretString" in response:
                parsed = json.loads(response["SecretString"])
                self._cached_secrets = {str(k): str(v) for k, v in parsed.items()}
            else:
                self._cached_secrets = {}
            return self._cached_secrets
        except Exception as exc:
            logger.error("Failed to load secrets from AWS Secrets Manager: %s", type(exc).__name__)
            raise RuntimeError(f"AWS Secrets Manager resolution failed: {exc}") from exc

    def get_secret(self, key: str) -> str | None:
        secrets = self._fetch_aws_secrets()
        return secrets.get(key)

    def get_all_secrets(self) -> dict[str, str]:
        return dict(self._fetch_aws_secrets())


class GcpSecretManagerProvider(SecretProvider):
    """Google Cloud Secret Manager provider using google-cloud-secret-manager when available."""

    def __init__(
        self,
        project_id: str,
        secret_id: str = "codesentinel-secrets",
        version: str = "latest",
        gcp_client: Any | None = None,
    ):
        self.project_id = project_id
        self.secret_id = secret_id
        self.version = version
        self._client = gcp_client
        self._cached_secrets: dict[str, str] | None = None

    def _fetch_gcp_secrets(self) -> dict[str, str]:
        if self._cached_secrets is not None:
            return self._cached_secrets

        try:
            if self._client is not None:
                client = self._client
            else:
                from google.cloud import secretmanager

                client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{self.project_id}/secrets/{self.secret_id}/versions/{self.version}"
            response = client.access_secret_version(request={"name": name})
            payload = response.payload.data.decode("UTF-8")
            parsed = json.loads(payload)
            self._cached_secrets = {str(k): str(v) for k, v in parsed.items()}
            return self._cached_secrets
        except Exception as exc:
            logger.error("Failed to load secrets from GCP Secret Manager: %s", type(exc).__name__)
            raise RuntimeError(f"GCP Secret Manager resolution failed: {exc}") from exc

    def get_secret(self, key: str) -> str | None:
        secrets = self._fetch_gcp_secrets()
        return secrets.get(key)

    def get_all_secrets(self) -> dict[str, str]:
        return dict(self._fetch_gcp_secrets())


class CompositeSecretProvider(SecretProvider):
    """Chains multiple secret providers with fallback order."""

    def __init__(self, providers: list[SecretProvider]):
        self.providers = providers

    def get_secret(self, key: str) -> str | None:
        for provider in self.providers:
            val = provider.get_secret(key)
            if val is not None:
                return val
        return None

    def get_all_secrets(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for provider in reversed(self.providers):
            merged.update(provider.get_all_secrets())
        return merged


def get_secret_provider(provider_type: str | None = None, **kwargs: Any) -> SecretProvider:
    """Factory creating secret provider from configuration.

    Supported providers:
      - 'env': Environment variables (default)
      - 'memory' / 'inmemory': In-memory provider for testing
      - 'vault': HashiCorp Vault KV v2 engine
      - 'aws' / 'aws_secrets_manager': AWS Secrets Manager
      - 'gcp' / 'gcp_secret_manager': GCP Secret Manager

    Raises:
      ValueError: If provider_type is unrecognized or misspelled.
    """
    if provider_type is None:
        return EnvSecretProvider()
    provider_clean = provider_type.strip().lower()
    if not provider_clean or provider_clean == "env":
        return EnvSecretProvider()
    if provider_clean in ("memory", "inmemory"):
        return InMemorySecretProvider(kwargs.get("secrets"))
    if provider_clean == "vault":
        return VaultSecretProvider(
            vault_addr=kwargs.get("vault_addr", ""),
            vault_token=kwargs.get("vault_token", ""),
            secret_path=kwargs.get("secret_path", "secret/data/codesentinel"),
            client=kwargs.get("client"),
        )
    if provider_clean in ("aws", "aws_secrets_manager"):
        return AwsSecretsManagerProvider(
            secret_id=kwargs.get("secret_id", "codesentinel/production"),
            region_name=kwargs.get("region_name", "us-east-1"),
            boto_client=kwargs.get("boto_client"),
        )
    if provider_clean in ("gcp", "gcp_secret_manager"):
        return GcpSecretManagerProvider(
            project_id=kwargs.get("project_id", ""),
            secret_id=kwargs.get("secret_id", "codesentinel-secrets"),
            version=kwargs.get("version", "latest"),
            gcp_client=kwargs.get("gcp_client"),
        )
    raise ValueError(
        f"Unsupported or unknown secret provider: {provider_clean!r}. "
        "Supported providers are: 'env', 'memory', 'vault', 'aws_secrets_manager', 'gcp_secret_manager'."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Settings Resolution & Secret Injection
# ──────────────────────────────────────────────────────────────────────────────


MANAGED_SECRET_KEYS = [
    "DATABASE_URL",
    "SESSION_SECRET_KEY",
    "GITHUB_WEBHOOK_SECRET",
    "GITHUB_APP_CLIENT_SECRET",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "NEBIUS_TOKEN_FACTORY_API_KEY",
    "REDIS_URL",
]


def resolve_and_inject_secrets(
    settings_obj: Any,
    provider: SecretProvider | None = None,
) -> dict[str, str]:
    """Query secret provider and inject resolved secrets directly into Settings object in memory.

    Returns a dictionary of keys resolved with masked previews for diagnostic reporting.
    """
    effective_provider = provider or get_secret_provider(
        getattr(settings_obj, "SECRET_PROVIDER", "env"),
        vault_addr=getattr(settings_obj, "VAULT_ADDR", ""),
        vault_token=getattr(settings_obj, "VAULT_TOKEN", ""),
        secret_path=getattr(settings_obj, "SECRET_MANAGER_SECRET_ID", "secret/data/codesentinel"),
        secret_id=getattr(settings_obj, "SECRET_MANAGER_SECRET_ID", "codesentinel/production"),
    )

    resolved: dict[str, str] = {}
    for key in MANAGED_SECRET_KEYS:
        val = effective_provider.get_secret(key)
        if val is not None:
            setattr(settings_obj, key, val)
            resolved[key] = "***RESOLVED_FROM_KMS***"

    logger.info(
        "Secret governance: dynamically resolved %d managed secrets from %s",
        len(resolved),
        type(effective_provider).__name__,
    )
    return resolved


# ──────────────────────────────────────────────────────────────────────────────
# Secret Rotation Validation
# ──────────────────────────────────────────────────────────────────────────────


def validate_secret_rotation(
    current_secret: str,
    new_secret: str,
    min_length: int = 32,
) -> tuple[bool, str | None]:
    """Validate that a candidate rotated secret satisfies cryptographic security requirements.

    Returns (is_valid, rejection_reason).
    """
    if not new_secret or not isinstance(new_secret, str):
        return False, "New secret cannot be empty"

    if len(new_secret) < min_length:
        return False, f"New secret must be at least {min_length} characters long"

    if new_secret == current_secret:
        return False, "New secret must differ from current secret"

    # Reject common development placeholders
    disallowed_patterns = [
        "development",
        "placeholder",
        "example",
        "change-this",
        "secret-key",
        "password123",
        "admin",
    ]
    for pattern in disallowed_patterns:
        if pattern in new_secret.lower():
            return False, f"New secret contains insecure placeholder pattern: {pattern!r}"

    # Entropy check: ensure candidate has sufficient character variety
    has_upper = any(c.isupper() for c in new_secret)
    has_lower = any(c.islower() for c in new_secret)
    has_digit = any(c.isdigit() for c in new_secret)
    has_special = any(not c.isalnum() for c in new_secret)

    variety = sum([has_upper, has_lower, has_digit, has_special])
    if variety < 2:
        return False, "New secret must include characters from at least 2 categories (upper, lower, digit, special)"

    return True, None
