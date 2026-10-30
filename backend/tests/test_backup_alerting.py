"""Tests for CodeSentinel backup monitoring and alerting service."""

import httpx

from app.services.backup.alerting import (
    format_discord_payload,
    format_slack_payload,
    ping_snitch,
    sanitize_alert_payload,
    send_backup_alert,
)


def test_sanitize_alert_payload_masks_credentials():
    """Verify passwords, tokens, and database URLs in alert payloads are redacted."""
    raw_payload = {
        "database": "codesentinel_prod",
        "error": "Failed connection to postgresql://admin:super_secret_pw@db.internal:5432/codesentinel_prod with token=xyz123",
        "details": {
            "password": "plain_password_here",
            "safe_field": "public_info",
        },
    }

    sanitized = sanitize_alert_payload(raw_payload)
    assert "super_secret_pw" not in str(sanitized)
    assert "[REDACTED]" in sanitized["error"]
    assert sanitized["details"]["password"] == "[REDACTED]"
    assert sanitized["details"]["safe_field"] == "public_info"


def test_sanitize_alert_payload_preserves_harmless_key_fields():
    """Verify harmless fields containing 'key' like object_key and key_count are NOT redacted."""
    payload = {
        "object_key": "backups/20261030/db.dump.enc",
        "key_count": 42,
        "s3_key": "s3://bucket/path/backup.dump",
        "key_id": "kms-key-id-12345",
        "meta": {
            "object_key": "nested/backup.dump",
            "key_count": 10,
        },
        "query": "https://s3.example.com/download?object_key=db.dump&api_key=sk-secret-12345",
    }

    sanitized = sanitize_alert_payload(payload)
    assert sanitized["object_key"] == "backups/20261030/db.dump.enc"
    assert sanitized["key_count"] == 42
    assert sanitized["s3_key"] == "s3://bucket/path/backup.dump"
    assert sanitized["key_id"] == "kms-key-id-12345"
    assert sanitized["meta"]["object_key"] == "nested/backup.dump"
    assert sanitized["meta"]["key_count"] == 10
    # Query string: object_key is preserved, api_key is redacted
    assert "object_key=db.dump" in sanitized["query"]
    assert "sk-secret-12345" not in sanitized["query"]
    assert "api_key=[REDACTED]" in sanitized["query"]


def test_sanitize_alert_payload_recursively_sanitizes_nested_lists_and_tuples():
    """Verify nested lists and tuples containing sensitive data or dicts are recursively sanitized."""
    payload = {
        "audit_trail": [
            "Normal log entry",
            "Connection failed with Bearer ghp_SuperSecretGithubToken12345",
            {
                "user": "deploy-agent",
                "api_key": "sk-live-token-abc",
                "backup_encryption_key": "base64keyhere",
                "object_key": "valid/s3/path",
            },
        ],
        "tuple_diagnostics": (
            "postgresql://service_user:super_pass_123@db.prod:5432/codesentinel",
            {"secret": "nested_secret_val"},
        ),
    }

    sanitized = sanitize_alert_payload(payload)

    # List items
    assert sanitized["audit_trail"][0] == "Normal log entry"
    assert "ghp_SuperSecretGithubToken12345" not in str(sanitized["audit_trail"][1])
    assert "Bearer [REDACTED]" in sanitized["audit_trail"][1]
    assert sanitized["audit_trail"][2]["api_key"] == "[REDACTED]"
    assert sanitized["audit_trail"][2]["backup_encryption_key"] == "[REDACTED]"
    assert sanitized["audit_trail"][2]["object_key"] == "valid/s3/path"

    # Tuple items
    assert "super_pass_123" not in sanitized["tuple_diagnostics"][0]
    assert ":[REDACTED]@" in sanitized["tuple_diagnostics"][0]
    assert sanitized["tuple_diagnostics"][1]["secret"] == "[REDACTED]"


def test_format_slack_payload():
    """Verify Slack webhook payload formatting structure and colors."""
    success_data = {
        "status": "SUCCESS",
        "database": "codesentinel_dev",
        "host": "localhost",
        "backup_file": "backup_20261030.dump",
        "file_size_bytes": 1024,
    }
    slack_payload = format_slack_payload(success_data)
    assert "*CodeSentinel Database Backup - SUCCESS*" in slack_payload["text"]
    assert slack_payload["attachments"][0]["color"] == "#2eb886"

    fail_data = {
        "status": "FAILURE",
        "database": "codesentinel_dev",
        "error": "Disk quota exceeded",
    }
    slack_fail = format_slack_payload(fail_data)
    assert "*CodeSentinel Database Backup - FAILURE*" in slack_fail["text"]
    assert slack_fail["attachments"][0]["color"] == "#a30200"


def test_format_discord_payload():
    """Verify Discord webhook payload formatting structure."""
    alert_data = {
        "status": "SUCCESS",
        "database": "codesentinel_dev",
        "host": "db.local",
    }
    discord_payload = format_discord_payload(alert_data)
    assert "CodeSentinel Backup Alert: SUCCESS" in discord_payload["content"]
    assert len(discord_payload["embeds"]) == 1
    assert discord_payload["embeds"][0]["color"] == 0x2ECC71


def test_send_backup_alert_success_and_failure(monkeypatch):
    """Verify send_backup_alert handles successful HTTP 200 and handles timeouts/errors cleanly."""
    # 1. No webhook URL returns False immediately
    assert send_backup_alert(None, "SUCCESS", "db") is False

    # 2. Successful dispatch
    def mock_post_200(self, url, json=None):
        return httpx.Response(status_code=200)

    monkeypatch.setattr(httpx.Client, "post", mock_post_200)
    assert send_backup_alert("https://hooks.slack.com/services/xxx", "SUCCESS", "db") is True

    # 3. Upstream error with retries returns False without raising
    def mock_post_500(self, url, json=None):
        raise httpx.ConnectError("Network unreachable")

    monkeypatch.setattr(httpx.Client, "post", mock_post_500)
    assert send_backup_alert("https://example.com/webhook", "FAILURE", "db", max_retries=1) is False


def test_send_backup_alert_accepts_dictionary_payload(monkeypatch):
    """Verify send_backup_alert accepts a dictionary payload as sent by backup_db scripts."""
    captured_json = {}

    def mock_post(self, url, json=None):
        captured_json.update(json)
        return httpx.Response(status_code=200)

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    payload = {
        "status": "SUCCESS",
        "database": "codesentinel_dev",
        "host": "staging-db",
        "backup_file": "backup.dump.enc",
        "file_size_bytes": 2048,
        "sha256": "abc123sha256",
        "encrypted": True,
    }

    assert send_backup_alert("https://example.com/webhook", payload) is True
    assert captured_json["database"] == "codesentinel_dev"
    assert captured_json["host"] == "staging-db"
    assert captured_json["status"] == "SUCCESS"
    assert captured_json["backup_file"] == "backup.dump.enc"
    assert captured_json["file_size_bytes"] == 2048
    assert captured_json["sha256"] == "abc123sha256"


def test_ping_snitch(monkeypatch):
    """Verify dead-man's-snitch ping routes to start, success, and fail endpoints."""
    called_urls = []

    def mock_get(self, url):
        called_urls.append(url)
        return httpx.Response(status_code=200)

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    assert ping_snitch(None) is False
    assert ping_snitch("https://hc-ping.com/my-uuid", "start") is True
    assert "https://hc-ping.com/my-uuid/start" in called_urls

    assert ping_snitch("https://hc-ping.com/my-uuid", "success") is True
    assert "https://hc-ping.com/my-uuid" in called_urls

    assert ping_snitch("https://hc-ping.com/my-uuid", "fail") is True
    assert "https://hc-ping.com/my-uuid/fail" in called_urls

