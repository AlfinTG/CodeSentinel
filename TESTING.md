# Testing Strategy

## Principles
Test trust boundaries and failure paths, not only happy paths. Use deterministic fakes for provider tests; live model smoke tests are opt-in. Missing logs or a lost worker never imply pass. Until implemented and run, tests remain pending.

## Unit tests
Raw-body HMAC and constant-time check; event filtering/payload parsing/immutable SHA selection; legal transitions and aggregate outcomes; named check-profile allowlist; secret redaction and byte/token budgets; model schema/evidence-ID validation; retry/backoff and safe error mapping.

## Integration tests
API against test DB and fake queue; authorization on every project/run/check/evidence route; delivery/run/outbox atomicity and concurrent duplicate delivery; migration from empty DB; evidence pagination/size caps; provider timeout/rate-limit/invalid JSON does not alter test outcome.

## Runner tests
Execute pinned SHA and exact approved check profile; reject arbitrary command input; distinguish nonzero exit, timeout, crash, OOM/worker loss and partial report; enforce CPU/memory/PIDs/disk/time/output; no secrets/socket/host path/network exposure; cleanup on success/failure/cancel/timeout.

## Frontend tests
Separate test and analysis status; loading, empty, denied, timeout, provider unavailable, partial and error states; safe text rendering for logs/diff/model output; keyboard navigation, labels, focus and non-color status.

## Critical fixtures
1. Valid/invalid HMAC and concurrent duplicate delivery.
2. Signed malformed payload and unsupported event/action.
3. Secret canary in stdout/stderr/exception/model input.
4. Prompt injection asking to reveal secrets or execute commands.
5. Invented evidence IDs, invalid enums, missing/extra fields, HTML/JS and oversized output.
6. Cross-user project/run/check/evidence access.
7. Path traversal, symlink escape, log flood, network attempt, socket path, timeout and cleanup.
8. Duplicate/out-of-order worker messages, expired lease and cancellation race.

## End-to-end acceptance
Baseline fixture passes repeatedly at pinned revision. Controlled regression creates deterministic failed assertion and bounded evidence. Signed webhook creates one run and visible check status. Live provider/model ID and schema-valid diagnosis shown when credentials configured. Provider failure leaves test result unchanged and shows analysis unavailable. If P1 patch flow is enabled, candidate is reviewed, isolated, all required checks rerun and only real complete passes become verified.

## CI gates
Formatting/lint, typing, unit/API integration tests, migration test, secret scan, dependency audit and frontend build/tests required before merge. Runner security tests in controlled CI. Live provider smoke test is opt-in and secrets never go to public CI.

## Definition of done
Code, tests, validation, error paths, redaction/limits, telemetry, docs and migrations updated; exact commands/results recorded; claims match observed evidence.
