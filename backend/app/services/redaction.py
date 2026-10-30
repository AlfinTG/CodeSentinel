"""Secret redaction engine for test outputs, logs, and evidence items."""

import re
from typing import Any

# Common patterns for API tokens, secret keys, and credentials
REDACTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # GitHub Tokens
    (re.compile(r"(ghp_[a-zA-Z0-9]{36,255})"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(ghs_[a-zA-Z0-9]{36,255})"), "[REDACTED_GITHUB_APP_TOKEN]"),
    (re.compile(r"(github_pat_[a-zA-Z0-9_]{40,255})"), "[REDACTED_GITHUB_PAT]"),
    # Generic API Keys & Bearer Tokens
    (re.compile(r"(sk-[a-zA-Z0-9]{20,255})"), "[REDACTED_API_KEY]"),
    (re.compile(r"(Bearer\s+)[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE), r"\1[REDACTED_BEARER_TOKEN]"),
    # Private Key Blocks
    (
        re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+ PRIVATE KEY-----"),
        "[REDACTED_PRIVATE_KEY_BLOCK]",
    ),
    # Connection strings with credentials
    (
        re.compile(r"(://[^:]+:)([^@]+)(@)", re.IGNORECASE),
        r"\1[REDACTED_PASSWORD]\3",
    ),
    # Password and Secret assignments (value must not start with '[' to avoid
    # overwriting already-inserted [REDACTED_*] placeholders from canary pass)
    (
        re.compile(
            r"""((?:password|secret|token|api_key|private_key)\s*[:=]\s*["']?)([^"'\s,;\[]+)(["']?)""",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]\3",
    ),
]


def redact_secrets(text: Any, custom_canaries: list[str] | None = None) -> str:
    """Scans and masks credentials, tokens, private keys, and canary strings from text.

    Ensures that secrets never reach database storage, UI rendering, or external LLM prompts.
    Handles non-string inputs safely without crashing.
    """
    if text is None:
        return ""

    redacted = str(text) if not isinstance(text, str) else text
    if not redacted:
        return ""

    # Redact explicit canary values first (e.g. from runtime configuration)
    if custom_canaries:
        for canary in custom_canaries:
            if canary and len(canary.strip()) >= 6:
                redacted = redacted.replace(canary, "[REDACTED_CANARY]")

    # Redact known patterns
    for pattern, replacement in REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)

    return redacted
