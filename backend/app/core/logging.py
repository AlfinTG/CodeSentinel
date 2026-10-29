"""Structured JSON logging and context-aware correlation tracking for CodeSentinel."""

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from app.services.redaction import redact_secrets

# Context variables for tracing across asynchronous tasks and thread boundaries
request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id_ctx", default=None
)
correlation_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id_ctx", default=None
)
run_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "run_id_ctx", default=None
)


def sanitize_correlation_id(raw_id: str | None, default: str) -> str:
    """Validate and sanitize external correlation / request IDs to prevent header injection and log spoofing.

    Accepts ASCII alphanumeric characters, underscores, dashes, dots, and colons up to 128 chars.
    Rejects any string with whitespace, CRLF, control characters, or excessive length.
    """
    if not raw_id or not isinstance(raw_id, str):
        return default
    cleaned = raw_id.strip()
    if not cleaned or len(cleaned) > 128 or not re.match(r"^[a-zA-Z0-9_\-\.:]+$", cleaned):
        return default
    return cleaned


def set_log_context(
    request_id: str | None = None,
    correlation_id: str | None = None,
    run_id: str | None = None,
) -> None:
    """Convenience helper to set active context variables for the current execution context."""
    if request_id is not None:
        request_id_ctx.set(request_id)
    if correlation_id is not None:
        correlation_id_ctx.set(correlation_id)
    if run_id is not None:
        run_id_ctx.set(run_id)


def clear_log_context() -> None:
    """Clear all correlation context variables."""
    request_id_ctx.set(None)
    correlation_id_ctx.set(None)
    run_id_ctx.set(None)


class StructuredJsonFormatter(logging.Formatter):
    """Custom formatter emitting single-line JSON with correlation identifiers and secret redaction."""

    def __init__(self, *args: Any, redact: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.redact = redact

    def format(self, record: logging.LogRecord) -> str:
        try:
            # Retrieve base message safely (prevent format interpolation exceptions from breaking logging)
            try:
                raw_message = record.getMessage()
            except Exception:
                raw_message = str(record.msg)

            sanitized_message = redact_secrets(raw_message) if self.redact else raw_message

            # Resolve correlation context (record extra overrides ContextVar)
            req_id = getattr(record, "request_id", None) or request_id_ctx.get()
            corr_id = getattr(record, "correlation_id", None) or correlation_id_ctx.get()
            r_id = getattr(record, "run_id", None) or run_id_ctx.get()

            log_payload: dict[str, Any] = {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": sanitized_message,
            }

            if req_id:
                log_payload["request_id"] = req_id
            if corr_id:
                log_payload["correlation_id"] = corr_id
            if r_id:
                log_payload["run_id"] = r_id

            # Format exception or stack trace if present
            if record.exc_info:
                exc_text = self.formatException(record.exc_info)
                log_payload["exception"] = redact_secrets(exc_text) if self.redact else exc_text
            elif record.stack_info:
                stack_text = self.formatStack(record.stack_info)
                log_payload["stack"] = redact_secrets(stack_text) if self.redact else stack_text

            return json.dumps(log_payload, default=str)
        except Exception as exc:
            # Fallback that NEVER raises, ensuring logging failures never break API or worker execution
            safe_exc = redact_secrets(str(exc)) if self.redact else str(exc)
            return json.dumps({
                "timestamp": datetime.now(UTC).isoformat(),
                "level": "ERROR",
                "logger": "app.core.logging",
                "message": f"[LOGGING_ERROR] Failed to format log record: {safe_exc}",
            })


def configure_logging(log_level: str = "INFO", json_format: bool = True) -> None:
    """Configure root logger with StructuredJsonFormatter on sys.stdout."""
    root_logger = logging.getLogger()
    level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger.setLevel(level)

    # Remove existing handlers to prevent duplicates
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(StructuredJsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s")
        )

    root_logger.addHandler(handler)
