"""AI-powered regression diagnosis service via Nebius Token Factory.

Responsibilities:
- Build a bounded, evidence-grounded prompt (≤ MAX_PROMPT_CHARS)
- Call the NVIDIA model served through Nebius Token Factory with retry/timeout
- Parse and strictly validate the canonical ModelDiagnosisSchema
- Validate that every evidence_id in hypotheses belongs to the run
- Record latency, token counts, and model_id on the Analysis record
- Handle provider failure / invalid response without altering test_outcome

Gate 4 contract (IMPLEMENTATION_PLAN.md):
  real opt-in call succeeds with model ID recorded;
  provider failure/invalid output is honest and test outcome unchanged;
  no secret leakage in any error path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.analysis import Analysis
from app.models.evidence import EvidenceItem
from app.models.run import AnalysisOutcome, Run
from app.schemas.analysis import ModelDiagnosisSchema

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Prompt construction limits
# ──────────────────────────────────────────────────────────────────────────────
MAX_PROMPT_CHARS = 24_000          # hard cap on the full prompt sent to the model
MAX_EVIDENCE_ITEMS = 20            # number of evidence items included
MAX_EVIDENCE_CONTENT_CHARS = 800   # per-item content truncation for the prompt
SCHEMA_VERSION = "1.0"

# ──────────────────────────────────────────────────────────────────────────────
# Canonical system prompt (immutable — versioned with SCHEMA_VERSION)
# ──────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are a regression-diagnosis assistant. Your sole task is to analyse \
evidence from automated test runs and produce a structured diagnosis.

Rules:
1. Base every claim on the provided evidence items. Do NOT invent facts.
2. Label all root-cause statements explicitly as HYPOTHESES.
3. Do NOT produce executable commands, patch instructions, HTML, or tool calls.
4. Confidence values must be qualitative: "low", "medium", or "high" — \
   never a numeric probability.
5. evidence_ids must be UUIDs taken verbatim from the evidence list below. \
   Never fabricate IDs.
6. Respond ONLY with a single valid JSON object matching the schema. \
   No markdown fences, no preamble, no trailing text.

Output schema (strict):
{
  "summary": "<string ≤500 chars>",
  "root_cause_hypotheses": [
    {
      "statement": "<string ≤1000 chars — clearly a hypothesis>",
      "evidence_ids": ["<uuid from the list>"],
      "confidence": "low | medium | high",
      "reasoning": "<string ≤2000 chars>"
    }
  ],
  "affected_behaviors": ["<string>"],
  "recommended_next_steps": ["<string>"],
  "missing_evidence": ["<string>"]
}
Array limits: root_cause_hypotheses ≤5; others ≤10.
"""


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def run_analysis(run: Run, analysis: Analysis, db: Session) -> None:
    """Execute AI diagnosis for *run*, update *analysis* in-place, and commit.

    This function is designed to be called from a background worker.
    It NEVER raises — all failure modes produce a safe error state.
    The run's test_outcome is never modified here.
    """
    try:
        _do_analysis(run, analysis, db)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in run_analysis for run=%s", run.id)
        _mark_failed(analysis, db, safe_error_code="internal_error", exc=exc)


# ──────────────────────────────────────────────────────────────────────────────
# Internal implementation
# ──────────────────────────────────────────────────────────────────────────────

def _do_analysis(run: Run, analysis: Analysis, db: Session) -> None:
    """Core analysis pipeline."""
    # 1. Fetch evidence items for this run
    evidence_items: list[EvidenceItem] = (
        db.query(EvidenceItem)
        .filter(EvidenceItem.run_id == run.id)
        .order_by(EvidenceItem.created_at.asc())
        .limit(MAX_EVIDENCE_ITEMS)
        .all()
    )

    if not evidence_items:
        _mark_failed(analysis, db, safe_error_code="no_evidence")
        return

    # 2. Build the bounded user prompt
    user_prompt = _build_prompt(run, evidence_items)

    # 3. Compute input digest (sha256 of the prompt — used for deduplication)
    input_digest = hashlib.sha256(user_prompt.encode()).hexdigest()
    analysis.input_digest = input_digest

    # 4. Transition to running
    analysis.status = "running"
    analysis.started_at = datetime.now(UTC)
    run.analysis_outcome = AnalysisOutcome.RUNNING.value
    db.commit()

    # 5. Call the model
    raw_response, latency_ms, token_counts = _call_nebius(user_prompt)

    analysis.latency_ms = latency_ms
    analysis.input_tokens = token_counts.get("prompt_tokens")
    analysis.output_tokens = token_counts.get("completion_tokens")

    if raw_response is None:
        _mark_failed(analysis, db, safe_error_code="provider_unavailable")
        return

    # 6. Parse and validate the structured schema
    valid_evidence_ids = {str(e.id) for e in evidence_items}
    diagnosis, validation_error = _parse_and_validate(raw_response, valid_evidence_ids)

    if validation_error:
        logger.warning(
            "Analysis schema validation failed for run=%s: %s", run.id, validation_error
        )
        _mark_failed(analysis, db, safe_error_code="invalid_model_response")
        run.analysis_outcome = AnalysisOutcome.INVALID_RESPONSE.value
        db.commit()
        return

    # 7. Persist success
    analysis.result_json = diagnosis.model_dump()
    analysis.status = "succeeded"
    analysis.completed_at = datetime.now(UTC)
    run.analysis_outcome = AnalysisOutcome.SUCCEEDED.value
    db.commit()

    logger.info(
        "Analysis succeeded for run=%s model=%s latency_ms=%s",
        run.id,
        analysis.model_id,
        latency_ms,
    )


def _build_prompt(run: Run, items: list[EvidenceItem]) -> str:
    """Construct the bounded evidence prompt for the model."""
    lines: list[str] = [
        "## Regression Diagnosis Request",
        f"Run ID: {run.id}",
        f"Commit SHA: {run.head_sha}",
        f"Event: {run.event_kind}",
        "",
        f"## Evidence Items ({len(items)} items)",
    ]

    for item in items:
        content = item.content_redacted[:MAX_EVIDENCE_CONTENT_CHARS]
        truncated_note = "  [truncated for prompt]" if len(item.content_redacted) > MAX_EVIDENCE_CONTENT_CHARS else ""
        lines.extend([
            "",
            f"### Evidence ID: {item.id}",
            f"Kind: {item.kind}",
            f"Source: {item.source_label}",
            "Content:",
            content + truncated_note,
        ])

    prompt = "\n".join(lines)

    # Hard cap: truncate if somehow still over limit
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS] + "\n[PROMPT TRUNCATED]"

    return prompt


def _call_nebius(
    user_prompt: str,
) -> tuple[dict[str, Any] | None, int, dict[str, int]]:
    """Call the Nebius Token Factory OpenAI-compatible endpoint.

    Returns (parsed_json_or_None, latency_ms, token_counts).
    Never raises — all network/parse errors are caught and logged safely.
    """
    if not settings.NEBIUS_TOKEN_FACTORY_API_KEY:
        logger.warning("NEBIUS_TOKEN_FACTORY_API_KEY not set — skipping live call")
        return None, 0, {}

    url = f"{settings.NEBIUS_TOKEN_FACTORY_BASE_URL}/chat/completions"
    payload = {
        "model": settings.NEBIUS_MODEL_ID,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }

    last_exc: Exception | None = None
    for attempt in range(settings.NEBIUS_MAX_RETRIES + 1):
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=settings.NEBIUS_TIMEOUT_SECONDS) as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {settings.NEBIUS_TOKEN_FACTORY_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            latency_ms = int((time.monotonic() - t0) * 1000)

            if resp.status_code == 429:
                logger.warning("Nebius rate limit hit (attempt %d)", attempt + 1)
                last_exc = Exception("rate_limit")
                continue

            if resp.status_code >= 500:
                logger.warning(
                    "Nebius server error %d (attempt %d)", resp.status_code, attempt + 1
                )
                last_exc = Exception(f"server_error_{resp.status_code}")
                continue

            resp.raise_for_status()

            data = resp.json()
            content_str = data["choices"][0]["message"]["content"]
            parsed = json.loads(content_str)
            token_counts = data.get("usage", {})
            return parsed, latency_ms, token_counts

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            # IMPORTANT: never log the api key or any part of the response that might contain it
            logger.warning(
                "Nebius network error on attempt %d: %s", attempt + 1, type(exc).__name__
            )
            last_exc = exc
        except (KeyError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Nebius response parse error on attempt %d: %s", attempt + 1, exc)
            last_exc = exc
            break  # malformed response — no point retrying

    logger.error("All Nebius attempts exhausted. Last error: %s", type(last_exc).__name__ if last_exc else "unknown")
    return None, 0, {}


def _parse_and_validate(
    raw: dict[str, Any],
    valid_evidence_ids: set[str],
) -> tuple[ModelDiagnosisSchema | None, str | None]:
    """Validate raw model output against the canonical schema.

    Returns (diagnosis, None) on success, (None, error_message) on failure.

    Critically: validates that every evidence_id in hypotheses is a UUID
    that actually belongs to the current run. Foreign or invented IDs are
    rejected to prevent the model from hallucinating evidence links.
    """
    try:
        diagnosis = ModelDiagnosisSchema.model_validate(raw)
    except ValidationError as exc:
        return None, f"pydantic_validation: {exc.error_count()} errors"

    # Evidence ID cross-reference check
    for hyp in diagnosis.root_cause_hypotheses:
        for eid in hyp.evidence_ids:
            if eid not in valid_evidence_ids:
                return None, f"foreign_evidence_id: {eid!r} does not belong to this run"

    # Reject content that looks like HTML or executable commands (basic guard)
    suspicious_patterns = ["<script", "```", "$ ", "sudo ", "curl ", "wget "]
    full_text = json.dumps(diagnosis.model_dump())
    for pat in suspicious_patterns:
        if pat in full_text:
            return None, f"rejected_content: suspicious pattern {pat!r} found in model output"

    return diagnosis, None


def _mark_failed(
    analysis: Analysis,
    db: Session,
    safe_error_code: str,
    exc: Exception | None = None,
) -> None:
    """Transition analysis to a safe error state without leaking details."""
    analysis.status = "failed"
    analysis.safe_error_code = safe_error_code
    analysis.completed_at = datetime.now(UTC)
    # Ensure run's analysis_outcome reflects unavailability
    # (caller may set INVALID_RESPONSE specifically — don't overwrite it)
    try:
        run = db.query(Run).filter(Run.id == analysis.run_id).first()
        if run and run.analysis_outcome not in (
            AnalysisOutcome.INVALID_RESPONSE.value,
        ):
            run.analysis_outcome = AnalysisOutcome.UNAVAILABLE.value
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not persist analysis failure for run=%s", analysis.run_id)


# ──────────────────────────────────────────────────────────────────────────────
# Prompt construction helper (exported for tests)
# ──────────────────────────────────────────────────────────────────────────────

def build_analysis_prompt(run: Run, evidence_items: list[EvidenceItem]) -> str:
    """Public wrapper around _build_prompt for unit testing."""
    return _build_prompt(run, evidence_items)


def validate_diagnosis(
    raw: dict[str, Any], valid_evidence_ids: set[str]
) -> tuple[ModelDiagnosisSchema | None, str | None]:
    """Public wrapper around _parse_and_validate for unit testing."""
    return _parse_and_validate(raw, valid_evidence_ids)
