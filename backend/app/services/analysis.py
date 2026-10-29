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
import random
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.telemetry import record_nebius_usage
from app.models.analysis import Analysis
from app.models.evidence import EvidenceItem
from app.models.run import AnalysisOutcome, Run
from app.schemas.analysis import ModelDiagnosisSchema
from app.services.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

nebius_circuit_breaker = CircuitBreaker(
    failure_threshold=settings.NEBIUS_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    recovery_timeout_seconds=settings.NEBIUS_CIRCUIT_BREAKER_RECOVERY_SECONDS,
    name="nebius_token_factory",
)


class NebiusCallResult(tuple):
    """3-tuple representation of Nebius call result for backward compatibility.

    Unpacks as (raw_response, latency_ms, token_counts) for existing callers and test mocks.
    Carries additional fields for error classification:
      - error_code: str | None ('provider_auth_error', 'provider_invalid_request', 'provider_unavailable')
      - is_transient: bool (True for timeouts/429/5xx/network errors; False for 400/401/403/404)
    """

    def __new__(
        cls,
        raw_response: dict[str, Any] | None,
        latency_ms: int,
        token_counts: dict[str, int],
        error_code: str | None = None,
        is_transient: bool = False,
    ):
        return super().__new__(cls, (raw_response, latency_ms, token_counts))

    def __init__(
        self,
        raw_response: dict[str, Any] | None,
        latency_ms: int,
        token_counts: dict[str, int],
        error_code: str | None = None,
        is_transient: bool = False,
    ):
        self.error_code = error_code
        self.is_transient = is_transient


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
        nebius_circuit_breaker.release_probe()
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

    # 5. Check circuit breaker before contacting model provider
    if not nebius_circuit_breaker.can_execute():
        logger.warning(
            "Nebius circuit breaker is OPEN or probe in-flight for run=%s — fast-failing analysis without calling provider",
            run.id,
        )
        record_nebius_usage(
            model_id=analysis.model_id or settings.NEBIUS_MODEL_ID,
            duration_seconds=0.0,
            outcome="circuit_breaker_open",
        )
        _mark_failed(analysis, db, safe_error_code="circuit_breaker_open")
        return

    # Call the model
    try:
        call_result = _call_nebius(user_prompt)
        raw_response = call_result[0]
        latency_ms = call_result[1]
        token_counts = call_result[2]
        error_code = getattr(call_result, "error_code", None) or "provider_unavailable"
        is_transient = getattr(
            call_result,
            "is_transient",
            raw_response is None,
        )

        analysis.latency_ms = latency_ms
        analysis.input_tokens = token_counts.get("prompt_tokens")
        analysis.output_tokens = token_counts.get("completion_tokens")

        if raw_response is None:
            record_nebius_usage(
                model_id=analysis.model_id or settings.NEBIUS_MODEL_ID,
                duration_seconds=latency_ms / 1000.0,
                outcome=f"failed_{error_code}",
            )
            if is_transient:
                nebius_circuit_breaker.record_failure()
            else:
                # Permanent failure (e.g. 401 auth or 400 invalid request)
                # Release probe slot without incrementing transient failure count
                nebius_circuit_breaker.release_probe()
            _mark_failed(analysis, db, safe_error_code=error_code)
            return

        prompt_tok = token_counts.get("prompt_tokens", 0) if isinstance(token_counts, dict) else 0
        comp_tok = token_counts.get("completion_tokens", 0) if isinstance(token_counts, dict) else 0
        record_nebius_usage(
            model_id=analysis.model_id or settings.NEBIUS_MODEL_ID,
            prompt_tokens=prompt_tok,
            completion_tokens=comp_tok,
            duration_seconds=latency_ms / 1000.0,
            outcome="succeeded",
        )
        nebius_circuit_breaker.record_success()

    except Exception:
        # In case of any unexpected exception during call or processing, release probe
        nebius_circuit_breaker.release_probe()
        raise

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


def _backoff_sleep(seconds: float) -> None:
    """Sleep helper for exponential backoff, isolated for deterministic testing."""
    time.sleep(seconds)


def _compute_backoff_delay(attempt: int, resp: httpx.Response | None = None) -> float:
    """Calculate bounded exponential backoff with jitter, respecting Retry-After on 429."""
    if resp is not None and resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                parsed = float(retry_after.strip())
                if 0.0 <= parsed <= 10.0:  # strictly bounded
                    return parsed
            except ValueError:
                pass
    base_delay = min(0.5 * (2 ** attempt), 4.0)
    jitter = random.uniform(0.05, 0.25)
    return min(base_delay + jitter, 5.0)


def _call_nebius(
    user_prompt: str,
) -> NebiusCallResult:
    """Call the Nebius Token Factory OpenAI-compatible endpoint.

    Returns NebiusCallResult (unpacks as (parsed_json_or_None, latency_ms, token_counts)).
    Never raises — all network/parse/HTTP errors are caught and classified safely.
    Permanent 4xx errors are not retried. Transient 429/5xx/network errors are retried
    with bounded backoff and jitter.
    """
    if not settings.NEBIUS_TOKEN_FACTORY_API_KEY:
        logger.warning("NEBIUS_TOKEN_FACTORY_API_KEY not set — skipping live call")
        return NebiusCallResult(None, 0, {}, error_code="provider_unavailable", is_transient=False)

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
    last_status: int | None = None
    total_latency_ms = 0

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
            call_latency = int((time.monotonic() - t0) * 1000)
            total_latency_ms += call_latency

            # Check for rate limiting (transient)
            if resp.status_code == 429:
                last_status = 429
                last_exc = Exception("rate_limit_429")
                logger.warning(
                    "Nebius rate limit 429 on attempt %d of %d",
                    attempt + 1,
                    settings.NEBIUS_MAX_RETRIES + 1,
                )
                if attempt < settings.NEBIUS_MAX_RETRIES:
                    delay = _compute_backoff_delay(attempt, resp)
                    _backoff_sleep(delay)
                    continue
                break

            # Check for server errors (transient)
            if resp.status_code >= 500:
                last_status = resp.status_code
                last_exc = Exception(f"server_error_{resp.status_code}")
                logger.warning(
                    "Nebius server error %d on attempt %d of %d",
                    resp.status_code,
                    attempt + 1,
                    settings.NEBIUS_MAX_RETRIES + 1,
                )
                if attempt < settings.NEBIUS_MAX_RETRIES:
                    delay = _compute_backoff_delay(attempt, resp)
                    _backoff_sleep(delay)
                    continue
                break

            # Check for permanent client errors (4xx other than 429)
            if 400 <= resp.status_code < 500:
                last_status = resp.status_code
                if resp.status_code in (401, 403):
                    error_code = "provider_auth_error"
                    logger.warning(
                        "Nebius authentication failure (HTTP %d). Permanent error; not retrying.",
                        resp.status_code,
                    )
                elif resp.status_code == 404:
                    error_code = "provider_model_not_found"
                    logger.warning(
                        "Nebius model not found (HTTP 404). Permanent error; not retrying."
                    )
                else:
                    error_code = "provider_invalid_request"
                    logger.warning(
                        "Nebius request error (HTTP %d). Permanent error; not retrying.",
                        resp.status_code,
                    )
                return NebiusCallResult(
                    None,
                    total_latency_ms,
                    {},
                    error_code=error_code,
                    is_transient=False,
                )

            resp.raise_for_status()

            data = resp.json()
            content_str = data["choices"][0]["message"]["content"]
            parsed = json.loads(content_str)
            token_counts = data.get("usage", {})
            return NebiusCallResult(parsed, call_latency, token_counts, error_code=None, is_transient=False)

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            call_latency = int((time.monotonic() - t0) * 1000)
            total_latency_ms += call_latency
            logger.warning(
                "Nebius network error on attempt %d: %s",
                attempt + 1,
                type(exc).__name__,
            )
            last_exc = exc
            if attempt < settings.NEBIUS_MAX_RETRIES:
                delay = _compute_backoff_delay(attempt)
                _backoff_sleep(delay)
                continue

        except httpx.HTTPStatusError as exc:
            call_latency = int((time.monotonic() - t0) * 1000)
            total_latency_ms += call_latency
            status_code = exc.response.status_code if exc.response is not None else 0
            logger.warning(
                "Nebius HTTP status error on attempt %d: %d",
                attempt + 1,
                status_code,
            )
            last_status = status_code
            last_exc = exc
            if 400 <= status_code < 500 and status_code != 429:
                err_code = "provider_auth_error" if status_code in (401, 403) else "provider_invalid_request"
                return NebiusCallResult(
                    None,
                    total_latency_ms,
                    {},
                    error_code=err_code,
                    is_transient=False,
                )
            if attempt < settings.NEBIUS_MAX_RETRIES:
                delay = _compute_backoff_delay(attempt, exc.response)
                _backoff_sleep(delay)
                continue

        except (KeyError, json.JSONDecodeError, ValueError) as exc:
            call_latency = int((time.monotonic() - t0) * 1000)
            total_latency_ms += call_latency
            logger.warning("Nebius response parse error on attempt %d: %s", attempt + 1, exc)
            last_exc = exc
            return NebiusCallResult(
                None,
                total_latency_ms,
                {},
                error_code="invalid_model_response",
                is_transient=False,
            )

    logger.error(
        "All Nebius attempts exhausted. Last status: %s, last error: %s",
        last_status,
        type(last_exc).__name__ if last_exc else "unknown",
    )
    return NebiusCallResult(
        None,
        total_latency_ms,
        {},
        error_code="provider_unavailable",
        is_transient=True,
    )


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
