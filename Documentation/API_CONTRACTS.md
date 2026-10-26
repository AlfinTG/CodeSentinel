# API Contracts (MVP)

These are canonical contract sketches for implementation, not proof that routes already exist. All JSON objects should be validated by Pydantic schemas. Prefix application routes with `/api/v1`. Use UTC RFC 3339 timestamps and UUIDs for internal IDs.

## 1. Common rules

- Browser endpoints require an authenticated session and resource-level authorization.
- Webhook endpoint authenticates via GitHub signature; it is not a user-session route.
- JSON request and response bodies use UTF-8.
- Never return provider keys, installation private keys, webhook secrets, raw unredacted logs, stack traces from internal errors, or raw provider error payloads.
- Stable error shape: `{"error":{"code":"stable_snake_case_code","message":"Safe human-readable message","request_id":"..."}}`.
- For duplicate webhook deliveries, return a successful acknowledgment (e.g. `202`) with the existing run ID if available; do not create new work.

## 2. Endpoints

### `GET /api/v1/auth/github/start`
Starts the GitHub App user-authorization flow. Validate `state`; do not accept arbitrary callback URLs.

### `GET /api/v1/auth/github/callback`
Validates authorization state and exchange result, resolves the GitHub user, creates a server-side session, and redirects only to an allowlisted local route. Store session in a Secure/HttpOnly/SameSite cookie in deployed environments.

### `POST /api/v1/auth/logout`
Invalidates the server-side session and clears the cookie; protect the state-changing action with CSRF controls.

### `GET /api/v1/auth/me`
Returns current user identity and authorized project count; no tokens or installation secrets.

### `GET /api/v1/health/live`
Process liveness only. `200 {"status":"ok"}` if process is running.

### `GET /api/v1/health/ready`
Readiness of required dependencies (DB and queue). Do not expose credentials/connection strings. Return `503` if not ready.

### `POST /api/v1/webhooks/github`
Headers: `X-Hub-Signature-256`, `X-GitHub-Event`, `X-GitHub-Delivery`. Read raw bytes for signature validation before parsing.

Accepted event types in MVP: `push`, `pull_request` actions `opened`, `reopened`, `synchronize`, `ready_for_review` (filter draft PR behavior explicitly). Payload must resolve to an authorized installation/repository and a usable immutable commit SHA. Others are safely acknowledged or rejected according to documented policy.

Response accepted: `202 {"accepted":true,"duplicate":false,"run_id":"uuid"}`. Duplicate: `202 {"accepted":true,"duplicate":true,"run_id":"uuid-or-null"}`. Invalid signature: `401`; malformed signed payload: `400`; unsupported event may return `202` ignored when appropriate to prevent endless provider retries. All decisions are logged by safe error code.

### `GET /api/v1/projects`
Returns only projects the user is authorized to view.

```json
{"items":[{"id":"uuid","repository":"owner/repo","default_branch":"main","created_at":"2026-01-01T00:00:00Z"}]}
```

### `GET /api/v1/projects/{project_id}`
Returns repository identity, configured check profiles, integration health summary, and retention/configuration metadata that is safe to expose.

### `GET /api/v1/projects/{project_id}/runs?cursor=&limit=`
`limit` default 20, maximum 100. Cursor pagination, stable ordering by creation time + ID. Response contains run ID, commit SHA, event kind, created/started/completed timestamps, lifecycle, test outcome, analysis outcome, and summary fields derived from records—not model prose alone.

### `GET /api/v1/runs/{run_id}`
Returns immutable source identifiers, lifecycle, test/analysis outcomes, configured checks, timing, and attempt summaries. Does not inline unbounded logs.

### `GET /api/v1/runs/{run_id}/checks`
Returns check ID, configured check name, status, exit code if known, duration, timestamps, truncation flag, and bounded factual summary.

### `GET /api/v1/checks/{check_id}/evidence?cursor=&limit=`
Returns redacted evidence metadata and page-sized content. Authorize via its parent project/run. Large content is paged or downloaded through a short-lived authorized route; do not expose public object URLs.

### `GET /api/v1/runs/{run_id}/analysis`
Returns analysis status and validated structured diagnosis when present; otherwise a safe error code and retryability hint. Model text is rendered as text, not HTML.

### `POST /api/v1/runs/{run_id}/analysis/retry`
Authenticated, authorized and rate-limited. Only retry if evidence exists and policy allows. Use idempotency key or explicit analysis attempt record. Do not rerun tests as a side effect.

### `POST /api/v1/runs/{run_id}/cancel`
Allowed only for queued/running runs and where the worker supports cancellation. Response reports accepted cancellation request; final state changes only when coordinator records it.

### P1: `POST /api/v1/runs/{run_id}/patch-attempts`
Creates a candidate patch request after feature flag/security gate. It does not write to GitHub. Response includes patch attempt ID and state.

### P1: `GET /api/v1/patch-attempts/{attempt_id}`
Returns candidate diff metadata, isolated validation state, required check outcomes, and explicit non-verified/verified status. Never reports success from model statement alone.

## 3. Canonical model diagnosis schema

```json
{
  "summary": "One or two sentence summary, grounded in evidence",
  "root_cause_hypotheses": [
    {
      "statement": "A plausible cause, clearly marked as a hypothesis",
      "evidence_ids": ["uuid"],
      "confidence": "low | medium | high",
      "reasoning": "Brief evidence-linked rationale"
    }
  ],
  "affected_behaviors": ["Named test/route/journey, if evidenced"],
  "recommended_next_steps": ["Safe, testable suggestion"],
  "missing_evidence": ["Information needed to distinguish alternatives"]
}
```

Enforce maximum field/item counts and string lengths. `evidence_ids` must be a subset of IDs for this run. Confidence is qualitative and not a calibrated probability. No HTML, tool calls, executable commands, or patch application instructions are accepted as trusted instructions. An optional suggested diff is not part of P0.

## 4. Suggested stable error codes

`invalid_signature`, `unsupported_event`, `payload_invalid`, `project_not_found`, `forbidden`, `run_not_cancellable`, `evidence_not_found`, `analysis_unavailable`, `analysis_schema_invalid`, `provider_rate_limited`, `provider_timeout`, `runner_unavailable`, `check_timed_out`, `request_too_large`, `rate_limited`, `internal_error`.

## 5. Contract tests

Each endpoint needs tests for success, unauthorized access, malformed input, expected status codes, stable error shape, pagination/size bounds, and secret absence. Webhook tests must use raw byte fixtures and the documented GitHub HMAC procedure.
