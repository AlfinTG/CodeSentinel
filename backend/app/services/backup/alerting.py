"""Backup monitoring and alerting service for CodeSentinel.

Dispatches structured notifications to webhook endpoints (Slack, Discord, Microsoft Teams,
or generic HTTP JSON receivers) and pings dead-man's-snitch monitors (Healthchecks.io,
Cronitor, Better Uptime) to guarantee visibility into backup job success, failure, or silent omission.
"""

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger("app.services.backup.alerting")


EXACT_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pass",
    "token",
    "secret",
    "authorization",
    "auth",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "secret_key",
    "private_key",
    "client_secret",
    "credential",
    "credentials",
    "backup_encryption_key",
    "encryption_key",
}

SENSITIVE_KEY_SUFFIXES = (
    "_password",
    "_passwd",
    "_token",
    "_secret",
    "_apikey",
    "_api_key",
    "_private_key",
    "_client_secret",
    "_credential",
    "_credentials",
)

SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b(password|passwd|token|secret|api_key|apikey|secret_key|client_secret|authorization)=([^\s&,;]+)",
    re.IGNORECASE,
)
DB_URL_PATTERN = re.compile(r"://([^:]+):([^@]+)@", re.IGNORECASE)
BEARER_TOKEN_PATTERN = re.compile(r"\b(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE)


def is_sensitive_key(key: str) -> bool:
    """Determine whether a dict key name represents a sensitive credential.

    Uses exact matching and compound suffix checks while explicitly allowing
    harmless fields such as 'object_key', 'key_count', and 's3_key'.
    """
    k = key.lower().strip()
    if k in EXACT_SENSITIVE_KEYS:
        return True
    return any(k.endswith(suffix) for suffix in SENSITIVE_KEY_SUFFIXES)


def _sanitize_string(val: str) -> str:
    """Redact passwords and tokens embedded within string values."""
    # Redact user:password in URLs
    clean_val = DB_URL_PATTERN.sub(r"://\1:[REDACTED]@", val)
    # Redact query-style or config assignments
    clean_val = SECRET_ASSIGNMENT_PATTERN.sub(r"\1=[REDACTED]", clean_val)
    # Redact Bearer authentication headers
    clean_val = BEARER_TOKEN_PATTERN.sub(r"\1[REDACTED]", clean_val)
    return clean_val


def _sanitize_value(val: Any) -> Any:
    """Recursively sanitize arbitrary data structures (dicts, lists, tuples, strings)."""
    if isinstance(val, dict):
        return sanitize_alert_payload(val)
    if isinstance(val, list):
        return [_sanitize_value(item) for item in val]
    if isinstance(val, tuple):
        return tuple(_sanitize_value(item) for item in val)
    if isinstance(val, str):
        return _sanitize_string(val)
    return val


def sanitize_alert_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Redact passwords, tokens, and secret strings from alert payloads before dispatch.

    Recursively sanitizes nested dictionaries, lists, and tuples without over-redacting
    harmless fields containing substrings such as 'object_key' or 'key_count'.
    """
    sanitized: dict[str, Any] = {}
    for k, v in data.items():
        if is_sensitive_key(k):
            sanitized[k] = "[REDACTED]"
        else:
            sanitized[k] = _sanitize_value(v)
    return sanitized


def format_slack_payload(alert_data: dict[str, Any]) -> dict[str, Any]:
    """Format alert data into Slack-compatible webhook blocks and attachment."""
    status = alert_data.get("status", "UNKNOWN").upper()
    is_success = status == "SUCCESS"
    color = "#2eb886" if is_success else "#a30200"
    emoji = ":white_check_mark:" if is_success else ":rotating_light:"

    database = alert_data.get("database", "unknown")
    host = alert_data.get("host", "unknown")

    fields = [
        {"title": "Database", "value": str(database), "short": True},
        {"title": "Host", "value": str(host), "short": True},
    ]

    if "backup_file" in alert_data:
        fields.append({"title": "Backup File", "value": str(alert_data["backup_file"]), "short": True})
    if "file_size_bytes" in alert_data:
        fields.append({"title": "File Size", "value": f"{alert_data['file_size_bytes']} bytes", "short": True})
    if "sha256" in alert_data:
        fields.append({"title": "SHA-256", "value": str(alert_data["sha256"])[:16] + "...", "short": True})
    if "error" in alert_data:
        fields.append({"title": "Error Details", "value": str(alert_data["error"]), "short": False})

    return {
        "text": f"{emoji} *CodeSentinel Database Backup - {status}*",
        "attachments": [
            {
                "color": color,
                "fields": fields,
                "footer": "CodeSentinel SRE Backup Pipeline",
                "ts": int(time.time()),
            }
        ],
    }


def format_discord_payload(alert_data: dict[str, Any]) -> dict[str, Any]:
    """Format alert data into Discord-compatible webhook embed."""
    status = alert_data.get("status", "UNKNOWN").upper()
    is_success = status == "SUCCESS"
    color = 0x2ECC71 if is_success else 0xE74C3C

    fields = [
        {"name": "Database", "value": str(alert_data.get("database", "unknown")), "inline": True},
        {"name": "Host", "value": str(alert_data.get("host", "unknown")), "inline": True},
    ]
    if "error" in alert_data:
        fields.append({"name": "Error Details", "value": str(alert_data["error"]), "inline": False})

    return {
        "content": f"**CodeSentinel Backup Alert: {status}**",
        "embeds": [
            {
                "title": f"Backup {status}",
                "color": color,
                "fields": fields,
                "timestamp": alert_data.get("timestamp", datetime.now(UTC).isoformat()),
            }
        ],
    }


def send_backup_alert(
    webhook_url: str | None,
    status: str | dict[str, Any],
    database: str = "unknown",
    host: str = "localhost",
    backup_file: str | None = None,
    file_size_bytes: int | None = None,
    sha256: str | None = None,
    error: str | None = None,
    max_retries: int = 2,
    timeout_seconds: float = 10.0,
) -> bool:
    """Send structured backup notification to the configured webhook URL.

    Accepts either discrete arguments or a payload dictionary as the second parameter.
    Never raises exceptions or breaks the caller's execution flow.
    """
    if not webhook_url:
        return False

    if isinstance(status, dict):
        payload_dict = status
        status_val = str(payload_dict.get("status", "SUCCESS"))
        database_val = str(payload_dict.get("database", database))
        host_val = str(payload_dict.get("host", host))
        backup_file_val = payload_dict.get("backup_file", backup_file)
        file_size_val = payload_dict.get("file_size_bytes", file_size_bytes)
        sha256_val = payload_dict.get("sha256", sha256)
        error_val = payload_dict.get("error", error)
    else:
        status_val = str(status) if status else "SUCCESS"
        database_val = database
        host_val = host
        backup_file_val = backup_file
        file_size_val = file_size_bytes
        sha256_val = sha256
        error_val = error

    raw_payload: dict[str, Any] = {
        "status": status_val.upper(),
        "database": database_val,
        "host": host_val,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if backup_file_val:
        raw_payload["backup_file"] = backup_file_val
    if file_size_val is not None:
        raw_payload["file_size_bytes"] = file_size_val
    if sha256_val:
        raw_payload["sha256"] = sha256_val
    if error_val:
        raw_payload["error"] = error_val

    safe_payload = sanitize_alert_payload(raw_payload)

    # Detect platform format
    if "hooks.slack.com" in webhook_url:
        post_json = format_slack_payload(safe_payload)
    elif "discord.com/api/webhooks" in webhook_url:
        post_json = format_discord_payload(safe_payload)
    else:
        post_json = safe_payload

    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                resp = client.post(webhook_url, json=post_json)
                if resp.status_code in (200, 204):
                    return True
                logger.warning(
                    "Webhook notification returned non-success code %d: %s",
                    resp.status_code,
                    resp.text[:100],
                )
        except Exception as exc:
            logger.warning("Attempt %d to send backup alert failed: %s", attempt + 1, exc)
            if attempt < max_retries:
                time.sleep(1.0)

    return False


def ping_snitch(snitch_url: str | None, event: str = "success", timeout_seconds: float = 10.0) -> bool:
    """Ping a dead-man's-snitch service (Healthchecks.io / Cronitor / Better Uptime).

    event can be 'start', 'success' (default ping), or 'fail'.
    """
    if not snitch_url:
        return False

    target_url = snitch_url.rstrip("/")
    if event == "start":
        target_url = f"{target_url}/start"
    elif event == "fail":
        target_url = f"{target_url}/fail"

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.get(target_url)
            return resp.status_code in (200, 202)
    except Exception as exc:
        logger.warning("Failed to ping snitch %s (%s): %s", snitch_url, event, exc)
        return False
