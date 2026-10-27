"""Evidence normalizer, size capping, and structured evidence extraction."""

import hashlib
import re
from dataclasses import dataclass

from app.models.evidence import EvidenceKind
from app.services.redaction import redact_secrets

MAX_EVIDENCE_BYTES = 262144  # 256 KiB storage cap per check bundle
TRUNCATION_MARKER = (
    "\n\n[TRUNCATED: Output exceeded 262144 bytes storage limit; remaining content dropped]"
)


@dataclass
class NormalizedEvidence:
    kind: str
    source_label: str
    content_redacted: str
    content_digest: str
    truncated: bool
    byte_length: int


def normalize_and_bound_output(
    raw_output: str,
    source_label: str = "check_output",
    custom_canaries: list[str] | None = None,
) -> NormalizedEvidence:
    """Sanitize, redact, size-cap, and compute content digest for evidence output."""
    # 1. Redact secrets first
    redacted_text = redact_secrets(raw_output, custom_canaries=custom_canaries)

    # 2. Check byte length and apply bounding
    encoded = redacted_text.encode("utf-8")
    truncated = False

    if len(encoded) > MAX_EVIDENCE_BYTES:
        truncated = True
        marker_bytes = TRUNCATION_MARKER.encode("utf-8")
        allowed_len = MAX_EVIDENCE_BYTES - len(marker_bytes)
        truncated_bytes = encoded[:allowed_len]
        # Decode ignoring cut characters at the boundary
        bounded_text = truncated_bytes.decode("utf-8", errors="ignore") + TRUNCATION_MARKER
        encoded = bounded_text.encode("utf-8")
    else:
        bounded_text = redacted_text

    byte_length = len(encoded)
    content_digest = hashlib.sha256(encoded).hexdigest()

    # Determine kind
    kind = EvidenceKind.LOG_EXCERPT.value
    if "assert " in bounded_text.lower() or "assertionerror" in bounded_text.lower():
        kind = EvidenceKind.FAILED_ASSERTION.value
    elif "traceback (most recent call last)" in bounded_text.lower():
        kind = EvidenceKind.STACK_TRACE.value
    elif "test session starts" in bounded_text.lower() or "tests passed" in bounded_text.lower():
        kind = EvidenceKind.TEST_REPORT.value

    return NormalizedEvidence(
        kind=kind,
        source_label=source_label,
        content_redacted=bounded_text,
        content_digest=content_digest,
        truncated=truncated,
        byte_length=byte_length,
    )


def extract_key_failure_excerpt(output: str, max_lines: int = 50) -> str:
    """Extract the most relevant failure lines (traceback, failed assertions) for model prompt grounding."""
    lines = output.splitlines()

    # Look for failure keywords
    failure_indices = []
    for i, line in enumerate(lines):
        if re.search(r"(FAILED|ERROR|AssertionError|Traceback)", line):
            failure_indices.append(i)

    if not failure_indices:
        # Return tail of output if no specific failure marker found
        return "\n".join(lines[-max_lines:])

    # Return window around the first and last failures
    first_idx = max(0, failure_indices[0] - 5)
    last_idx = min(len(lines), failure_indices[-1] + 15)

    excerpt_lines = lines[first_idx:last_idx]
    if len(excerpt_lines) > max_lines:
        excerpt_lines = excerpt_lines[:max_lines] + ["[Additional excerpt lines omitted]"]

    return "\n".join(excerpt_lines)
