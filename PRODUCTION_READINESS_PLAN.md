# Production Readiness Plan: CodeSentinel

**Repository:** `https://github.com/AlfinTG/CodeSentinel.git`  
**Current Working Branch:** `prashant`  
**Baseline Commit:** `63fab0d` (`Merge pull request #2 from AlfinTG/prashant`)  
**Audit Date:** October 29, 2026  
**Document Status:** Comprehensive Operational Assessment & 10-Phase Production Roadmap  

---

## 1. Executive Summary & Purpose

CodeSentinel is an evidence-led regression diagnosis and repair verification platform designed to bridge GitHub source revisions to deterministic test outcomes and structured AI hypotheses. Its foundational design principle is:

$$\text{Observed Test Outcomes} = \text{Authoritative Factual Truth}$$
$$\text{AI Model Outputs} = \text{Non-Authoritative Hypotheses Grounded in Bounded Evidence}$$

The purpose of this document is to provide an evidence-based audit of CodeSentinel's current codebase on branch `prashant`, classify its capabilities against strict audit criteria, report actual verified test and build executions, and present an expanded, logically sequenced **ten-phase engineering roadmap** required to transition CodeSentinel from a local/prototype release into a hardened, enterprise-ready production platform.

---

## 2. Project Architecture & Current State Overview

CodeSentinel follows a decoupled client-server architecture with an asynchronous execution pipeline, containerized service orchestration, and external AI provider integration:

```
[ GitHub Webhook ] â”€â”€(HMAC-SHA256)â”€â”€> [ FastAPI API Layer ] â”€â”€> [ PostgreSQL 16 ]
                                             â”‚                         â”‚
                                    (FastAPI BackgroundTasks)          â”‚
                                             â–¼                         â–¼
                                   [ Runner / Isolated Subproc ]   [ Nebius Token Factory ]
                                   (Stripped Env, No shell=True)  (NVIDIA Nemotron 70B)
                                             â”‚                         â”‚
                                             â–¼                         â–¼
                                    [ Evidence Store ] <â”€â”€â”€ [ Model Validator ]
                                   (256 KiB Capped, Digest)   (Cross-Referenced IDs)
                                             â”‚
                                             â–¼
                                 [ React + Vite Dashboard ]
                                 (Factual / AI Strict Separation)
```

### Core Architecture Components

1. **Frontend (`frontend/src/`):** React 18 + TypeScript + Vite single-page application. Features master-detail run history, strict visual separation between deterministic test outcomes (`passed`, `failed`, `timed_out`, `cancelled`, `error`) and AI diagnosis states (`not_requested`, `queued`, `running`, `succeeded`, `unavailable`, `invalid_response`), an evidence viewer with canary redaction visibility, and a candidate patch inspection tab.
2. **Backend API (`backend/app/`):** FastAPI application enforcing request correlation (`X-Request-ID`), security headers (`nosniff`, `DENY`), canonical JSON error responses, object-level authorization across all project resources, and raw-bytes HMAC-SHA256 signature verification.
3. **Database Layer (`backend/app/models/`, `backend/alembic/`):** SQLAlchemy 2.0 ORM with 10 core tables: `users`, `projects`, `project_check_profiles`, `github_deliveries`, `runs`, `check_runs`, `evidence_items`, `analyses`, `patch_attempts`, and `audit_events`. Schema tracked via Alembic migrations `0001_initial_schema` and `0002_patch_attempt_phase6_columns`.
4. **Execution & Evidence Engine (`backend/app/services/runner.py`, `normalizer.py`, `redaction.py`):** Subprocess-based check runner enforcing an approved binary allowlist (`pytest`, `python`, `npm`, `node`, `cargo`, `go`), environment stripping (removing `DATABASE_URL`, `NEBIUS_TOKEN_FACTORY_API_KEY`, and session secrets), secret pattern redaction, 256 KiB evidence output bounding, and sha256 content digests.
5. **AI Diagnosis Engine (`backend/app/services/analysis.py`):** Server-side adapter calling Nebius Token Factory (NVIDIA `llama-3.1-nemotron-70b-instruct`) with bounded prompts ($\le 24,000$ characters), strict JSON schema validation, and verification ensuring all cited evidence IDs exist on the current run.
6. **Candidate Patch Engine (`backend/app/services/patch.py`):** Ephemeral sandbox patch applicator that validates unified diffs ($\le 16\text{ KiB}$), checks for directory traversal or modifications to protected paths, and executes validation checks. Operates under an absolute invariant: **never performs GitHub branch writes, PR creation, auto-merges, or autonomous deployments.**

---

## 3. Observed Test & Build Verification

The following verification commands were executed directly against the local repository during this review:

### 3.1. Backend Test Suite
- **Command:** `pytest backend/tests/ -v`
- **Observed Result:** **241 passed, 21 skipped, 1 warning in 40.04s** (Exit code: 0)
- **Suite Breakdown (262 total tests collected):**
  - `test_container_isolation.py`: 38 passed, 7 skipped (Windows host / non-Linux cgroup v2 environment)
  - `test_github_auth.py`: 16 passed (OAuth flow, CSRF, cookie security, token exchange)
  - `test_nebius_live.py`: 1 skipped (requires live `NEBIUS_TOKEN_FACTORY_API_KEY`)
  - `test_phase0_baseline.py`: 5 passed (health probes, security headers, settings secret-safety)
  - `test_phase1_api_authorization.py`: 2 passed (cross-user isolation, authorization)
  - `test_phase1_migration.py`: 2 passed (all 10 entities registered, constraints verified)
  - `test_phase1_transitions.py`: 5 passed (legal, illegal, and aggregate lifecycle transitions)
  - `test_phase2_audit_regressions.py`: 7 passed (audit logging, event tracking)
  - `test_phase2_postgres_integration.py`: 13 skipped (requires live PostgreSQL server)
  - `test_phase2_queue_and_worker.py`: 15 passed (in-memory & durable queue mechanics, worker retry)
  - `test_phase2_webhooks.py`: 5 passed (HMAC verification, idempotency, event filtering)
  - `test_phase3_runner_and_evidence.py`: 6 passed (redaction, bounding, binary allowlist, environment stripping, timeouts)
  - `test_phase4_analysis.py`: 21 passed (prompt bounding, schema validation, foreign evidence rejection, provider decoupling)
  - `test_phase4_circuit_breaker.py`: 20 passed (circuit breaker state transitions, failure threshold, recovery half-open)
  - `test_phase5_api_extensions.py`: 7 passed (run cancellation, demo seeding, run-level evidence retrieval, unauthorized SSE blocked, SSE streaming, nonexistent run 404, terminal completion)
  - `test_phase6_observability.py`: 19 passed (Prometheus metrics, OpenTelemetry traces, structured logs)
  - `test_phase6_patches.py`: 4 passed (diff validation, unified diff patching, ephemeral sandbox verification, authorization)
  - `test_phase9_fuzzing.py`: 9 passed (adversarial HMAC tampering, diff traversal, prompt injection containment)
  - `test_secret_governance.py`: 31 passed (KMS secret providers, production secret validation, redaction)
  - `test_workspace_preparation.py`: 9 passed (tarball extraction traversal safety, GitHub archive download)

### 3.2. Frontend Production Build & Lint
- **Command:** `npm run lint` and `npm run build` (inside `frontend/`)
- **Observed Result:** **ESLint 0 errors/warnings; clean build in 14.58s** (Exit code: 0)
- **Artifacts:**
  - `dist/index.html` (0.82 kB | gzip: 0.46 kB)
  - `dist/assets/index-ZT992Wjd.css` (2.61 kB | gzip: 1.08 kB)
  - `dist/assets/index-BobU5WpO.js` (222.40 kB | gzip: 62.52 kB)
  - 1578 modules transformed with zero TypeScript errors.
### 3.3. Nebius Live Provider Call Status
- **Status:** **NOT RUN (Simulated in Automated Tests via Mock/Adapter)**
- **Verification Details:** The live Nebius Token Factory call requires an active `NEBIUS_TOKEN_FACTORY_API_KEY`. In `test_phase4_analysis.py`, provider responses are verified using deterministic mock fixtures and simulated payload failures. While the adapter logic in `_call_nebius` is fully implemented and tested against real response schemas, a live call against the external Nebius endpoint remains unverified until staging credentials are provided.

---

## 4. Evidence-Based Audit & Capability Classification

Every capability below is classified under one of four strict audit classifications based on real file paths, function implementations, and observed test evidence:
- **VERIFIED:** Implementation and test evidence confirm it exists and works within the verified scope.
- **PARTIAL:** Implementation exists, but important functionality, safeguards, operational reliability, or full edge-case coverage remain incomplete.
- **MISSING:** Architectural requirement or critical production control not yet implemented or absent from the codebase.
- **UNKNOWN:** Capability exists in design documentation or dependencies, but cannot be verified without external live infrastructure.

| Capability / Component | Status | Code Reference | Evidence / Test Verification | Production Gap & Risk |
|---|---|---|---|---|
| **Raw-Body HMAC-SHA256 Webhook Verification** | **VERIFIED** | `backend/app/services/webhook.py` (`verify_github_signature`) | `backend/tests/test_phase2_webhooks.py::test_invalid_signature_rejected` | Constant-time `hmac.compare_digest` verified. Webhook secrets must be rotated securely via secret manager in prod. |
| **Delivery Deduplication & Idempotency** | **VERIFIED** | `backend/app/services/webhook.py` (`process_github_delivery`) | `backend/tests/test_phase2_webhooks.py::test_duplicate_delivery_is_idempotent` | Re-delivered `X-GitHub-Delivery` payloads return `202 Accepted` with `duplicate=True` without re-executing runs. |
| **Object-Level Authorization Enforcement** | **VERIFIED** | `backend/app/api/deps.py` (`get_authorized_project`), `runs.py`, `patches.py` | `backend/tests/test_phase1_api_authorization.py::test_run_and_evidence_cross_user_isolation`, `test_phase6_patches.py` | Verified against cross-user snooping. Current implementation uses synthetic fallback user (`demo-dev`) in dev/demo mode. |
| **Lifecycle & Outcome State Transition Integrity** | **VERIFIED** | `backend/app/services/transitions.py` | `backend/tests/test_phase1_transitions.py::*` (5 tests) | Invalid lifecycle state jumps (e.g., `completed` $\rightarrow$ `running`) strictly raise `InvalidStateTransitionError`. |
| **Subprocess Execution Sandboxing & Redaction** | **PARTIAL** | `backend/app/services/runner.py`, `redaction.py`, `normalizer.py` | `backend/tests/test_phase3_runner_and_evidence.py::*` (6 tests) | Strips environment variables, enforces binary allowlist, and redacts canaries. **Gap:** Executes via host OS subprocess (`subprocess.run`), not true containerized worker isolation (cgroups, namespaces, seccomp). |
| **Evidence Size Capping & Content Digests** | **VERIFIED** | `backend/app/services/normalizer.py` | `backend/tests/test_phase3_runner_and_evidence.py::test_evidence_bounding_at_256_kib` | Hard cap of 262,144 bytes with truncation markers and SHA-256 digest calculation. |
| **AI Diagnosis Schema & Evidence Cross-Referencing** | **VERIFIED** | `backend/app/services/analysis.py`, `backend/app/schemas/analysis.py` | `backend/tests/test_phase4_analysis.py::*` (15 tests) | Schema validated with Pydantic; foreign or hallucinated evidence IDs rejected; prompt bounded at 24,000 characters. |
| **Provider Failure Decoupling (Truth Invariant)** | **VERIFIED** | `backend/app/services/analysis.py` | `backend/tests/test_phase4_analysis.py::test_provider_failure_does_not_alter_test_outcome` | If Nebius API fails, times out, or returns invalid JSON, run's `test_outcome` remains untouched while `analysis_outcome` transitions to `unavailable` or `invalid_response`. |
| **Candidate Patch Sandbox & Path Traversal Guards** | **VERIFIED** | `backend/app/services/patch.py` | `backend/tests/test_phase6_patches.py::test_validate_patch_diff_security_enforcement` | Rejects directory traversal (`../`), protected files (`.git`, `.env`, `docker-compose.yml`), diffs $>16\text{ KiB}$, and applies patches only in ephemeral temporary directories. |
| **Asynchronous Job Transport & Scaling** | **PARTIAL** | `backend/app/api/v1/endpoints/analysis.py`, `docker-compose.yml` | Code inspection (`BackgroundTasks` usage) | Currently dispatches AI diagnosis via in-process `fastapi.BackgroundTasks`. Redis container exists in Docker Compose, but RQ/Celery distributed worker queue is not wired into the task execution loop. |
| **User Authentication & Session Management** | **PARTIAL** | `backend/app/api/deps.py`, `backend/app/api/v1/endpoints/auth.py` | Code inspection | Tests authenticate via `X-Test-User-ID`; unauthenticated requests fall back to auto-creating `demo-dev`. Real GitHub OAuth 2.0 Web Application Flow and signed server session cookies are not implemented. |
| **Production Database Migrations** | **VERIFIED** | `backend/alembic/versions/` (`0001`, `0002`) | `backend/tests/test_phase1_migration.py::*` (2 tests) | 10 entities, foreign keys, unique indexes, and Phase 6 patch columns tracked in revision tree. Verified against SQLite in tests and PostgreSQL in Docker. |
| **Frontend Application Build & Presentation** | **VERIFIED** | `frontend/src/` | `npm run build` (Clean build, 1576 modules transformed, 0 TS errors) | Fully functional UI with strict test vs. AI separation. **Gap:** Styled using custom inline CSS; lacks production design system, accessibility audits, and real-time WebSocket updates. |
| **Automated Test Coverage & CI Suite** | **VERIFIED** | `backend/tests/`, `.github/workflows/ci.yml` | 53 passing backend tests, GitHub Actions CI workflow | Automated CI runs Ruff linting, pytest, and frontend Vite build on pull requests and pushes to `main`. |
| **Secrets Management & KMS** | **MISSING** | `.env.example`, `.env.production.example` | File inspection | Environment variables read from plaintext `.env` files. No integration with AWS Secrets Manager, GCP Secret Manager, or HashiCorp Vault. |
| **Production Telemetry, Monitoring & Alerting** | **MISSING** | `backend/app/main.py` | Code inspection | Application outputs basic Python logging and attaches `X-Request-ID`. Missing Prometheus metrics, OpenTelemetry distributed tracing, Sentry error tracking, and alerting rules. |
| **Automated Database Backup, Restore & Rollback** | **MISSING** | `scripts/start.ps1`, `docker-compose.yml` | Code inspection | Docker Compose provisions a volume (`postgres_data`), but automated point-in-time recovery (PITR), logical backup scripts (`pg_dump`), and tested rollback automation are missing. |
| **Live External Nebius Network Handshake** | **UNKNOWN** | `backend/app/services/analysis.py` (`_call_nebius`) | Code inspection | Implementation complete, but unverified against live external Nebius Token Factory servers due to absence of live production credentials in test environment. |

---

## 5. Ten-Phase Production Readiness Roadmap

To transition CodeSentinel into an enterprise-grade production platform, the engineering work is organized into **exactly ten distinct, logically sequenced phases**:

```
[Phase 1: Sandboxing] â”€â”€> [Phase 2: Queue Decoupling] â”€â”€> [Phase 3: Auth & Identity] â”€â”€> [Phase 4: Nebius Live Handshake]
                                                                                                  â”‚
                                                                                                  â–¼
[Phase 8: Backup/Restore] <â”€â”€ [Phase 7: Frontend Polish] <â”€â”€ [Phase 6: Observability] <â”€â”€ [Phase 5: Secrets & KMS]
         â”‚
         â–¼
[Phase 9: Staging Rehearsal] â”€â”€> [Phase 10: Production Release]
```

---

### Phase 1: Runner Hardening & True Container Isolation
- **Work Area:** Execution isolation and sandbox security.
- **Description:** Replace host OS subprocess execution in `backend/app/services/runner.py` and `patch.py` with true isolated, ephemeral container sandboxes (via Docker engine or Podman).
- **Dependencies:** None (builds on Phase 3 runner baseline).
- **Testing Requirements:** Integration tests verifying container cannot communicate on the network, access host filesystem, or exceed CPU/memory caps.
- **Measurable Acceptance Criteria:**
  - Check commands execute inside a non-root container with `--network none`.
  - Memory capped strictly at 1 GiB; CPU capped at 1.0 core; PID limit capped at 64.
  - Linux security profiles enforced: `--cap-drop=ALL` and `--security-opt=no-new-privileges`.
- **Definition of Done:** Malicious test script attempting socket connections, fork bombs, or filesystem traversal terminates cleanly with `runner_error` and does not affect host stability.

---

### Phase 2: Distributed Task Queue & Worker Decoupling
- **Work Area:** Asynchronous scaling and reliability.
- **Description:** Migrate background execution from in-process `fastapi.BackgroundTasks` (`backend/app/api/v1/endpoints/analysis.py`) to an out-of-process distributed queue using Redis and Celery or ARQ.
- **Dependencies:** Phase 1 (Runner containers spawned by workers).
- **Testing Requirements:** Chaos tests simulating API server restart while jobs are running; verification of idempotency on retried task execution.
- **Measurable Acceptance Criteria:**
  - Dedicated worker container service added to `docker-compose.yml`.
  - Runs and analyses persist state transitions in PostgreSQL with heartbeat updates.
  - Cancellation endpoint (`POST /runs/{id}/cancel`) sends explicit termination signal to active worker.
- **Definition of Done:** Zero task loss when API container is terminated during an active analysis run; orphaned jobs cleanly recovered by worker supervisor within 60 seconds.

---

### Phase 3: GitHub App Authentication & Identity Infrastructure
- **Work Area:** Authentication and authorization.
- **Description:** Replace the development mock user (`demo-dev` in `backend/app/api/deps.py`) with real GitHub OAuth 2.0 Web Application Flow and signed server session cookies.
- **Dependencies:** None.
- **Testing Requirements:** Automated OAuth flow testing with state parameter validation; cookie security header assertions.
- **Measurable Acceptance Criteria:**
  - `GET /api/v1/auth/github/login` redirects with cryptographic `state` CSRF token.
  - `GET /api/v1/auth/github/callback` exchanges code for GitHub user profile and persists user in `users` table.
  - Sessions issued via encrypted, `HttpOnly`, `SameSite=Lax`, `Secure` cookies with configurable TTL.
- **Definition of Done:** Unauthenticated users cannot access project or run endpoints; session expiration and revocation (`POST /api/v1/auth/logout`) completely invalidate authentication.

---

### Phase 4: Nebius Token Factory Live Verification & Resiliency
- **Work Area:** AI provider integration.
- **Description:** Conduct live verification against Nebius Token Factory API using real credentials and implement production resiliency patterns (circuit breaker, rate limit smoothing).
- **Dependencies:** Phase 2 (worker queue executes provider calls).
- **Testing Requirements:** Live smoke test suite executing against `https://api.tokenfactory.nebius.com/v1` with verified NVIDIA open model.
- **Measurable Acceptance Criteria:**
  - Confirmed live handshake with `nvidia/llama-3.1-nemotron-70b-instruct` returning valid JSON.
  - Circuit breaker trips after 3 consecutive 5xx provider errors, returning `unavailable` without spamming external API.
  - Token counts (`prompt_tokens`, `completion_tokens`) and request latency accurately logged to `analyses` record.
- **Definition of Done:** Live end-to-end regression analysis on a real test failure produces a schema-valid diagnosis referencing valid evidence IDs within 15 seconds.

---

### Phase 5: Production Secret Governance & KMS Integration
- **Work Area:** Cryptography and credentials management.
- **Description:** Remove all dependency on plaintext `.env` files in production and integrate with cloud Key Management Services (AWS Secrets Manager, GCP Secret Manager, or HashiCorp Vault).
- **Dependencies:** None.
- **Testing Requirements:** Verification that application boots and connects using credentials loaded directly from memory via secret manager SDK.
- **Measurable Acceptance Criteria:**
  - Production settings dynamically pull `DATABASE_URL`, `SESSION_SECRET_KEY`, `GITHUB_APP_CLIENT_SECRET`, and `NEBIUS_TOKEN_FACTORY_API_KEY` from KMS.
  - Automated secret rotation procedures documented and validated for webhook HMAC secret.
- **Definition of Done:** No secrets or credentials exist in container environment variables or files on disk in production runtime.

---

### Phase 6: Telemetry, Observability & Distributed Tracing
- **Work Area:** Site reliability and monitoring.
- **Description:** Implement full-stack observability with OpenTelemetry tracing, Prometheus `/metrics` export, structured JSON logging, and Sentry error tracking.
- **Dependencies:** Phase 2 (Workers and API instrumented).
- **Testing Requirements:** Assertions verifying trace context propagation across HTTP requests, Redis queue tasks, and database sessions.
- **Measurable Acceptance Criteria:**
  - `/metrics` endpoint exposes Prometheus counters for HTTP latency, active runs, check durations, and Nebius token spend.
  - Correlation ID (`X-Request-ID`) attached to all log lines and database queries.
  - Sentry SDK integrated with automatic redaction of canaries, tokens, and PII.
- **Definition of Done:** Operations dashboard visualizes $p50$, $p95$, and $p99$ response times, queue depth, error rates, and model latency in real-time.

---

### Phase 7: Modern UI/UX Redesign & Real-Time Telemetry Streaming
- **Work Area:** Presentation and developer experience.
- **Description:** Refine frontend dashboard by migrating from inline CSS to a modern Tailwind CSS + Radix/Shadcn design system, integrating Monaco Editor diffing, and adding Server-Sent Events (SSE).
- **Dependencies:** Phase 2 (API can stream worker events).
- **Testing Requirements:** Playwright/Cypress end-to-end UI tests; WCAG 2.1 AA accessibility auditing.
- **Measurable Acceptance Criteria:**
  - Real-time updates via SSE (`/api/v1/runs/{id}/events`) eliminate browser polling.
  - Candidate patch diffs rendered with Monaco Editor syntax highlighting.
  - WCAG 2.1 AA compliant keyboard navigation and contrast ratios.
- **Definition of Done:** Clean UI build with zero accessibility violations, responsive across desktop and tablet viewports, rendering live streaming check logs.

---

### Phase 8: Automated Backup, Disaster Recovery & Rollback Automation
- **Work Area:** Persistence reliability and disaster recovery.
- **Description:** Establish automated PostgreSQL backup schedules, point-in-time recovery (PITR) procedures, and zero-downtime database migration rollbacks.
- **Dependencies:** None.
- **Testing Requirements:** Automated disaster recovery drill restoring database from cold backup into a clean instance.
- **Measurable Acceptance Criteria:**
  - Automated daily snapshots and continuous WAL archiving to durable object storage (S3/GCS).
  - Recovery Time Objective (RTO) $\le 30\text{ minutes}$; Recovery Point Objective (RPO) $\le 5\text{ minutes}$.
  - Alembic `downgrade()` scripts verified for all schema versions.
- **Definition of Done:** Rehearsed end-to-end database restore script successfully reconstitutes 100% of runs, evidence, and analyses on a clean PostgreSQL instance.

---

### Phase 9: End-to-End Staging Rehearsal & Security Fuzzing
- **Work Area:** Pre-production validation and penetration testing.
- **Description:** Deploy complete CodeSentinel stack into a staging environment mirroring production and conduct rigorous fuzzing, load testing, and security evaluations.
- **Dependencies:** Phases 1 through 8.
- **Testing Requirements:** Locust load testing suite; adversarial webhook and prompt injection test suites.
- **Measurable Acceptance Criteria:**
  - Webhook ingestion benchmark: $\ge 100\text{ req/sec}$ with $p99 < 50\text{ms}$.
  - Adversarial prompt injection attacks in test output fail to manipulate model schema or bypass evidence-ID cross-referencing.
  - Malformed unified diff fuzzing yields zero unhandled exceptions or filesystem leaks.
- **Definition of Done:** 48-hour continuous soak test with synthetic regression events completes with zero service restarts, zero data corruption, and 100% audit logging.

---

### Phase 10: Production Cutover, Live Monitoring & Release Sign-Off
- **Work Area:** Production launch and release gating.
- **Description:** Execute production deployment, configure GitHub App production webhook URLs, activate production monitoring alerts, and conduct final release gate sign-off.
- **Dependencies:** Phase 9.
- **Testing Requirements:** Production smoke test validating end-to-end flow on a designated canary repository.
- **Measurable Acceptance Criteria:**
  - Multi-container stack running on production orchestrator with SSL/TLS termination.
  - Alerting rules active for high error rates, queue backpressure, and provider failures.
  - All release checklist security gates formally signed off.
- **Definition of Done:** Production deployment verified with real GitHub App webhooks and live Nebius Token Factory model hypotheses, operating under the verified evidence-led invariant.

---

## 6. Release Gates & Security Invariant Checklist

Production deployment is strictly **BLOCKED** if any of the following mandatory gates are unmet:

1. **Deterministic Test Authority (Invariant Gate):** Observed check exit codes are the sole factual truth. AI model output is strictly labeled as hypothesis and cannot overturn a test failure under any circumstance.
2. **Read-Only / No Autonomous Git Writes (Safety Gate):** CodeSentinel shall not push commits, create pull requests, or merge code autonomously into upstream repositories.
3. **No Shell Invocations (Sandbox Gate):** Runner processes must never execute with `shell=True` or through shell wrappers (`sh -c`, `bash -c`).
4. **Zero-Secret Runner Environment (Credential Gate):** Host secrets, database connection strings, and provider API keys must never be mounted or exposed within runner environments.
5. **Strict Grounding (Truth Gate):** AI diagnosis root-cause hypotheses must cite valid evidence IDs present in the current run's factual evidence store. Hallucinated or foreign IDs must trigger immediate response invalidation.
6. **Live Provider Verification (Handshake Gate):** Production release requires verified live authentication with Nebius Token Factory and confirmed schema validation.

---

## 7. Immediate Next Steps & Decision Register

| Priority | Action Item | Target Phase | Owner / Area |
|---|---|---|---|
| **P0** | Establish true container-based runner isolation (Docker/Podman with dropped capabilities) | Phase 1 | Security & Runner |
| **P0** | Decouple task execution into dedicated Redis-backed workers | Phase 2 | Backend Infrastructure |
| **P0** | Implement production GitHub App OAuth flow and signed session cookies | Phase 3 | Authentication |
| **P1** | Conduct live Nebius Token Factory API verification with production credentials | Phase 4 | AI Provider Integration |
| **P1** | Implement KMS / Secret Manager integration to eliminate `.env` files | Phase 5 | Security Governance |
| **P1** | Configure OpenTelemetry and Prometheus `/metrics` instrumentation | Phase 6 | Site Reliability Engineering |
| **P1** | Redesign frontend using Tailwind CSS and integrate Monaco Diff viewer | Phase 7 | Frontend UI/UX |
| **P2** | Validate PostgreSQL backup and point-in-time recovery automation | Phase 8 | Database Operations |
| **P2** | Execute load testing and adversarial fuzzing in staging environment | Phase 9 | Quality Assurance |
| **P2** | Perform final production cutover and canary verification | Phase 10 | Release Management |
