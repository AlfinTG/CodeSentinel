# Security Requirements and Threat Model

## Assets and trust boundaries
Assets: GitHub installation authority, source code, provider credentials, sessions, run/evidence data and worker host. Trusted: API/coordinator/supervisor. Untrusted: webhook payload before verification, repository code, test output, model input/output and candidate patches.

## Required controls
1. Verify X-Hub-Signature-256 against HMAC-SHA256 of exact raw request bytes using constant-time comparison before parsing.
2. Validate event/action allowlist, installation ID, repository ID and immutable SHA.
3. Unique delivery ID plus transactionally safe run/outbox creation; rate limits and bounded request body.
4. Server-side session security: state/CSRF protections, Secure/HttpOnly/SameSite cookies in deployment, allowlisted redirects, session rotation and expiry.
5. Object-level authorization for every project/run/check/evidence/analysis/patch resource.
6. No secrets in source control, frontend bundles, logs, model payloads or untrusted runner environment.
7. Runner isolation: non-root, ephemeral workspace, no Docker socket/privileged mode/broad host mounts, default-deny network, CPU/memory/PID/disk/time/output limits and cleanup.
8. Approved named check profiles only; no arbitrary shell string from UI, repository or model.
9. Redact secrets before persistence and model submission; bound evidence and preserve truncation markers.
10. Strict model schema; evidence references belong to run; render output as text; model output never authorizes commands or changes test status.
11. Dependency/secret scanning, pinned dependencies, TLS, safe errors, audit events and kill switch.

## Threat cases
Forged/replayed/concurrent duplicate webhook; malformed/oversized signed payload; wrong installation/repository; cross-user access; session fixation/CSRF/open redirect; prompt injection in files/logs/diffs; secret canary in output/provider error; log flood, path traversal, symlink escape, network attempt, resource exhaustion, process spawning, timeout, worker loss; invented evidence IDs; malformed model response; cleanup/cancellation/retry race.

## Release blockers
Do not enable public repository-code execution until actual host/runtime isolation is demonstrated by tests. If not proven, demo controlled fixtures in a trusted isolated environment and disclose limitation. Do not claim multi-tenant hardening based on this document. No automatic branch writes, merge or deployment in MVP.

## Incident readiness
Document secret rotation, runner kill switch, provider disablement, retention/deletion, backups and restore checks before public launch. Never store raw webhook bodies or provider responses without an approved, bounded, redacted diagnostic need.
