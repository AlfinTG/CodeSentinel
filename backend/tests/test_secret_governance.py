"""Tests for Production Secret Governance and KMS Integration (Phase 5)."""

import json
from unittest.mock import MagicMock

import httpx
import pytest

from app.core.config import Settings
from app.core.secrets import (
    AwsSecretsManagerProvider,
    CompositeSecretProvider,
    EnvSecretProvider,
    GcpSecretManagerProvider,
    InMemorySecretProvider,
    SecretString,
    VaultSecretProvider,
    get_secret_provider,
    is_sensitive_key,
    redact_sensitive_dict,
    resolve_and_inject_secrets,
    sanitize_database_url,
    validate_secret_rotation,
)


class TestSecretStringAndMasking:
    def test_secret_string_masking(self):
        secret = SecretString("super-sensitive-token-12345")
        assert repr(secret) == "'***REDACTED***'"
        assert str(secret) == "***REDACTED***"
        assert secret.get_secret_value() == "super-sensitive-token-12345"
        assert len(secret) == len("super-sensitive-token-12345")
        assert secret == "super-sensitive-token-12345"
        assert secret == SecretString("super-sensitive-token-12345")
        assert secret != "wrong-token"

    def test_is_sensitive_key(self):
        assert is_sensitive_key("DATABASE_URL")
        assert is_sensitive_key("SESSION_SECRET_KEY")
        assert is_sensitive_key("GITHUB_WEBHOOK_SECRET")
        assert is_sensitive_key("NEBIUS_TOKEN_FACTORY_API_KEY")
        assert is_sensitive_key("AWS_SECRET_ACCESS_KEY")
        assert is_sensitive_key("USER_PASSWORD")
        assert is_sensitive_key("GITHUB_APP_PRIVATE_KEY")
        assert not is_sensitive_key("API_PORT")
        assert not is_sensitive_key("API_HOST")
        assert not is_sensitive_key("CODESENTINEL_ENV")
        assert not is_sensitive_key("RUNNER_CONTAINER_RUNTIME")

    def test_sanitize_database_url(self):
        url = "postgresql+psycopg://codesentinel:my_super_password@localhost:5432/codesentinel_dev"
        sanitized = sanitize_database_url(url)
        assert "my_super_password" not in sanitized
        assert ":****@" in sanitized
        assert "codesentinel" in sanitized
        assert "localhost:5432" in sanitized

        # URLs without credentials or malformed URLs
        assert sanitize_database_url("sqlite:///:memory:") == "sqlite:///:memory:"
        assert sanitize_database_url("") == ""

    def test_redact_sensitive_dict(self):
        payload = {
            "api_host": "0.0.0.0",
            "database_url": "postgresql+psycopg://user:p@ssword123@db:5432/app",
            "auth": {
                "session_secret_key": "topsecret32charactersminimumlength!",
                "allow_demo": False,
                "token_obj": SecretString("in-memory-token"),
            },
            "public_metric": 42,
        }
        redacted = redact_sensitive_dict(payload)
        assert redacted["api_host"] == "0.0.0.0"
        assert redacted["public_metric"] == 42
        assert "p@ssword123" not in redacted["database_url"]
        assert ":****@" in redacted["database_url"]
        assert redacted["auth"]["session_secret_key"] == "***REDACTED***"
        assert redacted["auth"]["token_obj"] == "***REDACTED***"
        assert redacted["auth"]["allow_demo"] is False


class TestSecretProviders:
    def test_in_memory_provider(self):
        provider = InMemorySecretProvider({"KEY_A": "val_a"})
        assert provider.get_secret("KEY_A") == "val_a"
        assert provider.get_secret("KEY_B") is None

        provider.set_secret("KEY_B", "val_b")
        assert provider.get_secret("KEY_B") == "val_b"
        assert provider.get_all_secrets() == {"KEY_A": "val_a", "KEY_B": "val_b"}

    def test_env_provider(self, monkeypatch):
        monkeypatch.setenv("CODESENTINEL_TEST_SECRET", "super-secret-val")
        monkeypatch.setenv("PUBLIC_TEST_VAR", "visible")

        provider = EnvSecretProvider()
        assert provider.get_secret("CODESENTINEL_TEST_SECRET") == "super-secret-val"
        all_secrets = provider.get_all_secrets()
        assert "CODESENTINEL_TEST_SECRET" in all_secrets
        assert "PUBLIC_TEST_VAR" not in all_secrets

    def test_vault_provider_success(self):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "data": {
                    "SESSION_SECRET_KEY": "vault-resolved-session-secret-key-32",
                    "DATABASE_URL": "postgresql://vault:vault@localhost/db",
                }
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = mock_response

        provider = VaultSecretProvider(
            vault_addr="https://vault.example.com",
            vault_token="s.mocktoken",
            secret_path="secret/data/codesentinel",
            client=mock_client,
        )

        assert (
            provider.get_secret("SESSION_SECRET_KEY")
            == "vault-resolved-session-secret-key-32"
        )
        assert provider.get_secret("NONEXISTENT") is None

        # Verify caching (mock client should only be called once)
        assert (
            provider.get_secret("SESSION_SECRET_KEY")
            == "vault-resolved-session-secret-key-32"
        )
        assert mock_client.get.call_count == 1

    def test_vault_provider_failure(self):
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")

        provider = VaultSecretProvider(
            vault_addr="https://vault.example.com",
            vault_token="s.mocktoken",
            client=mock_client,
        )

        with pytest.raises(RuntimeError, match="Vault secret resolution failed"):
            provider.get_secret("ANY_KEY")

    def test_aws_secrets_manager_provider(self):
        mock_boto = MagicMock()
        mock_boto.get_secret_value.return_value = {
            "SecretString": json.dumps({
                "SESSION_SECRET_KEY": "aws-resolved-session-secret-key-32chars",
                "GITHUB_WEBHOOK_SECRET": "aws-webhook-secret",
            })
        }

        provider = AwsSecretsManagerProvider(
            secret_id="codesentinel/prod",
            region_name="us-east-1",
            boto_client=mock_boto,
        )

        assert (
            provider.get_secret("SESSION_SECRET_KEY")
            == "aws-resolved-session-secret-key-32chars"
        )
        assert provider.get_secret("GITHUB_WEBHOOK_SECRET") == "aws-webhook-secret"
        assert provider.get_secret("MISSING_KEY") is None
        assert mock_boto.get_secret_value.call_count == 1

    def test_gcp_secret_manager_provider(self):
        mock_gcp = MagicMock()
        mock_response = MagicMock()
        payload_json = json.dumps({"SESSION_SECRET_KEY": "gcp-resolved-session-key-32chars"})
        mock_response.payload.data = payload_json.encode("utf-8")
        mock_gcp.access_secret_version.return_value = mock_response

        provider = GcpSecretManagerProvider(
            project_id="my-gcp-project",
            secret_id="codesentinel-secrets",
            gcp_client=mock_gcp,
        )

        assert (
            provider.get_secret("SESSION_SECRET_KEY")
            == "gcp-resolved-session-key-32chars"
        )

    def test_composite_provider(self):
        provider_a = InMemorySecretProvider({"KEY_1": "val_1"})
        provider_b = InMemorySecretProvider({"KEY_1": "val_1_b", "KEY_2": "val_2"})

        composite = CompositeSecretProvider([provider_a, provider_b])
        # Priority should go to provider_a
        assert composite.get_secret("KEY_1") == "val_1"
        assert composite.get_secret("KEY_2") == "val_2"
        assert composite.get_secret("KEY_3") is None

    def test_get_secret_provider_factory(self):
        assert isinstance(get_secret_provider("env"), EnvSecretProvider)
        assert isinstance(
            get_secret_provider("memory", secrets={"K": "V"}), InMemorySecretProvider
        )
        assert isinstance(
            get_secret_provider("vault", vault_addr="http://localhost:8200", vault_token="t"),
            VaultSecretProvider,
        )
        assert isinstance(
            get_secret_provider("aws", secret_id="test-id"),
            AwsSecretsManagerProvider,
        )
        assert isinstance(
            get_secret_provider("gcp", project_id="proj-id"),
            GcpSecretManagerProvider,
        )


class TestSecretResolutionAndSettingsIntegration:
    def test_resolve_and_inject_secrets(self):
        provider = InMemorySecretProvider({
            "SESSION_SECRET_KEY": "injected-custom-session-secret-32-chars",
            "GITHUB_WEBHOOK_SECRET": "injected-custom-webhook-secret",
        })

        test_settings = Settings(
            CODESENTINEL_ENV="development",
            RUNNER_WORKSPACE_ROOT="/tmp/workspaces",
        )
        assert test_settings.SESSION_SECRET_KEY != "injected-custom-session-secret-32-chars"

        resolved = resolve_and_inject_secrets(test_settings, provider=provider)
        assert "SESSION_SECRET_KEY" in resolved
        assert "GITHUB_WEBHOOK_SECRET" in resolved
        assert (
            test_settings.SESSION_SECRET_KEY
            == "injected-custom-session-secret-32-chars"
        )
        assert (
            test_settings.GITHUB_WEBHOOK_SECRET
            == "injected-custom-webhook-secret"
        )

    def test_settings_initialize_secrets_method(self):
        provider = InMemorySecretProvider({
            "NEBIUS_TOKEN_FACTORY_API_KEY": "resolved-nebius-key-12345",
        })
        test_settings = Settings(
            CODESENTINEL_ENV="development",
            RUNNER_WORKSPACE_ROOT="/tmp/workspaces",
        )
        resolved = test_settings.initialize_secrets(provider=provider)
        assert "NEBIUS_TOKEN_FACTORY_API_KEY" in resolved
        assert test_settings.NEBIUS_TOKEN_FACTORY_API_KEY == "resolved-nebius-key-12345"


class TestProductionSecurityValidation:
    def test_production_rejects_default_session_secret(self):
        with pytest.raises(
            ValueError, match="SESSION_SECRET_KEY must not use default development value"
        ):
            Settings(
                CODESENTINEL_ENV="production",
                RUNNER_CONTAINER_RUNTIME="docker",
                RUNNER_WORKSPACE_ROOT="/var/lib/codesentinel/workspaces",
                SESSION_SECRET_KEY="development-secret-key-replace-in-production-32-chars-min",
                GITHUB_WEBHOOK_SECRET="valid-webhook-secret-production-random",
            )

    def test_production_rejects_short_session_secret(self):
        with pytest.raises(
            ValueError, match="SESSION_SECRET_KEY.*must be at least 32 characters"
        ):
            Settings(
                CODESENTINEL_ENV="production",
                RUNNER_CONTAINER_RUNTIME="docker",
                RUNNER_WORKSPACE_ROOT="/var/lib/codesentinel/workspaces",
                SESSION_SECRET_KEY="too-short-secret",
                GITHUB_WEBHOOK_SECRET="valid-webhook-secret-production-random",
            )

    def test_production_rejects_default_webhook_secret(self):
        with pytest.raises(
            ValueError, match="GITHUB_WEBHOOK_SECRET must not use default development value"
        ):
            Settings(
                CODESENTINEL_ENV="production",
                RUNNER_CONTAINER_RUNTIME="docker",
                RUNNER_WORKSPACE_ROOT="/var/lib/codesentinel/workspaces",
                SESSION_SECRET_KEY="valid-secure-session-key-32-chars-long!",
                GITHUB_WEBHOOK_SECRET="development-webhook-secret",
            )

    def test_production_valid_configuration_passes(self):
        prod_settings = Settings(
            CODESENTINEL_ENV="production",
            RUNNER_CONTAINER_RUNTIME="docker",
            RUNNER_WORKSPACE_ROOT="/var/lib/codesentinel/workspaces",
            SESSION_SECRET_KEY="a" * 32,
            GITHUB_WEBHOOK_SECRET="random-strong-webhook-secret-value",
        )
        assert prod_settings.CODESENTINEL_ENV == "production"
        assert prod_settings.SESSION_SECRET_KEY == "a" * 32


class TestSecretRotationValidation:
    def test_rotation_empty_secret(self):
        valid, reason = validate_secret_rotation("current-secret", "")
        assert not valid
        assert reason == "New secret cannot be empty"

    def test_rotation_short_secret(self):
        valid, reason = validate_secret_rotation("current-secret", "short", min_length=32)
        assert not valid
        assert "at least 32 characters" in reason

    def test_rotation_same_secret(self):
        sec = "valid-length-secret-that-is-long-enough-32-chars"
        valid, reason = validate_secret_rotation(sec, sec)
        assert not valid
        assert "must differ" in reason

    def test_rotation_rejects_disallowed_patterns(self):
        candidate = "my-new-development-secret-that-has-over-32-characters"
        valid, reason = validate_secret_rotation("current-secret", candidate)
        assert not valid
        assert "placeholder pattern" in reason

    def test_rotation_entropy_check(self):
        # All lowercase - only 1 variety category
        candidate = "alllowercasestringthatisoverthirtytwocharacterslong"
        valid, reason = validate_secret_rotation("current-secret", candidate)
        assert not valid
        assert "at least 2 categories" in reason

    def test_rotation_valid_secret(self):
        candidate = "K8s_Pr0duct10n_S3cr3t_K3y_W1th_H1gh_Entr0py!"
        valid, reason = validate_secret_rotation("old-secret-12345", candidate)
        assert valid
        assert reason is None

