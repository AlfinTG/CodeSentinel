# Team Workstreams & Engineering Delivery Plan

**Repository:** `https://github.com/AlfinTG/CodeSentinel.git`  
**Current Working Branch:** `prashant`  
**Baseline Commit:** `63fab0d` (`Merge pull request #2 from AlfinTG/prashant`)  
**Document Status:** Team Coordination & Implementation Architecture  
**Primary Reference:** `PRODUCTION_READINESS_PLAN.md`  

---

## 1. Purpose & Operational Guidelines

This document translates the ten-phase production roadmap defined in `PRODUCTION_READINESS_PLAN.md` into actionable engineering workstreams distributed across three primary technical ownership areas:

- **Teammate A:** Backend Security, Execution & Sandboxing
- **Teammate B:** Backend Architecture, Persistence & AI Integration
- **Teammate C:** Frontend, Integration & Release Coordination

### Core Operating Principles
1. **Safety & Git Discipline:** All team development branches off and merges into `prashant` via reviewed pull requests. No direct pushes to `main`, no destructive resets, and no changes to untracked teammate files.
2. **Deterministic Truth Invariant:** Observed test results are authoritative facts. Model output remains strictly a hypothesis. Under no circumstances may AI output overturn or modify test results.
3. **No Unilateral Merges:** No teammate may be the sole reviewer or merger of their own implementation. Security-critical changes (runner isolation, HMAC verification, authorization) require mandatory review by Teammate A and Teammate B.
4. **Flexible Role Allocation:** Role labels (Teammate A, B, C) delineate distinct engineering ownership domains. Specific team members may interchange assignments based on availability and skill sets without altering domain boundaries or acceptance criteria.

---

## 2. Team Ownership Areas & Primary Responsibilities

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                            CodeSentinel Team                                â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚       Teammate A         â”‚       Teammate B         â”‚       Teammate C      â”‚
â”‚  Backend Security &      â”‚ Backend Architecture,    â”‚ Frontend, Integration â”‚
â”‚  Execution Sandboxing    â”‚ Persistence & AI Engine  â”‚ & Release Operations  â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚ â€¢ Container Isolation    â”‚ â€¢ Distributed Queue / RQ â”‚ â€¢ Dashboard UI/UX     â”‚
â”‚ â€¢ Subprocess Sandboxing  â”‚ â€¢ Database Migrations    â”‚ â€¢ Diff Viewers        â”‚
â”‚ â€¢ Resource Quotas / Caps â”‚ â€¢ State Transitions      â”‚ â€¢ API Client Contractsâ”‚
â”‚ â€¢ Webhook HMAC Integrity â”‚ â€¢ Nebius Token Factory   â”‚ â€¢ E2E Playwright Testsâ”‚
â”‚ â€¢ User Auth & RBAC       â”‚ â€¢ Evidence Normalization â”‚ â€¢ CI/CD Pipelines     â”‚
â”‚ â€¢ Threat Modeling / Canaries â€¢ Prompt & Schema Guardsâ”‚ â€¢ Release Checklist   â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### 2.1. Teammate A â€” Backend Security and Execution
- **Core Ownership:** Host security, runner execution boundaries, cryptographic verification, and authentication/authorization infrastructure.
- **Specific Responsibilities:**
  - Hardening check execution environments against process escapes, network calls, and resource exhaustion.
  - Transitioning host `subprocess.run` into true containerized sandboxes (Docker / Podman with read-only root filesystems and dropped capabilities).
  - Enforcing raw-body HMAC-SHA256 GitHub webhook verification and deduplication barriers.
  - Designing and implementing production GitHub App OAuth 2.0 Web Application Flow and secure session management.
  - Maintaining secret redaction regex patterns and canary verification across logs, traces, and model prompts.
  - Conducting adversarial security testing, threat modeling, and path traversal auditing.
- **Primary Source Files:**
  - `backend/app/services/runner.py` (Execution engine & binary allowlist)
  - `backend/app/services/redaction.py` (Secret mask patterns & canaries)
  - `backend/app/services/webhook.py` (HMAC-SHA256 & event parsing)
  - `backend/app/api/deps.py` (Object-level authorization & user context)
  - `backend/app/api/v1/endpoints/webhooks.py` (Webhook entrypoint)
  - `backend/app/api/v1/endpoints/auth.py` (Authentication endpoints)
  - `backend/app/services/patch.py` (Unified diff validator & sandbox execution)
  - `backend/tests/test_phase2_webhooks.py` & `test_phase3_runner_and_evidence.py`

### 2.2. Teammate B â€” Backend Architecture, Persistence, and AI Integration
- **Core Ownership:** Async worker architecture, database modeling and lifecycle transitions, and external AI provider integration.
- **Specific Responsibilities:**
  - Designing and implementing distributed task queue processing (Redis + Celery or ARQ) replacing in-process `fastapi.BackgroundTasks`.
  - Managing PostgreSQL schema evolution, Alembic migrations, foreign key constraints, and transactional consistency.
  - Enforcing strict lifecycle and test outcome state transition integrity (`transitions.py`).
  - Maintaining Nebius Token Factory API client, token/cost controls, request timeouts, retries, and circuit breakers.
  - Enforcing strict JSON schema validation and evidence-ID cross-referencing on model hypotheses.
  - Managing evidence storage bounding (256 KiB hard cap), truncation markers, and SHA-256 digest computation.
- **Primary Source Files:**
  - `backend/app/services/analysis.py` (Nebius Token Factory adapter & prompt builder)
  - `backend/app/schemas/analysis.py` (Canonical Pydantic schema validation)
  - `backend/app/services/transitions.py` (State machine transition guards)
  - `backend/app/services/normalizer.py` (Evidence output normalizer & capping)
  - `backend/app/models/` (SQLAlchemy entities: `run.py`, `analysis.py`, `evidence.py`, `patch.py`, `project.py`)
  - `backend/alembic/versions/` (Database migration scripts)
  - `backend/app/api/v1/endpoints/analysis.py` (Analysis triggers & retries)
  - `backend/app/api/v1/endpoints/runs.py` (Run queries, checks, cancellation)
  - `backend/tests/test_phase1_transitions.py` & `test_phase4_analysis.py`

### 2.3. Teammate C â€” Frontend, Integration, and Release Coordination
- **Core Ownership:** Developer experience, UI dashboard, API contracts, CI/CD pipelines, documentation, and staging rehearsal.
- **Specific Responsibilities:**
  - Modernizing the React 18 + TypeScript + Vite frontend dashboard using Tailwind CSS and Radix/Shadcn UI primitives.
  - Implementing high-fidelity code diff presentation (Monaco Editor / CodeMirror) for candidate patch inspection.
  - Coordinating typed API clients (`frontend/src/services/api.ts`) and contract alignment with backend endpoints.
  - Implementing real-time run telemetry and log streaming via Server-Sent Events (SSE).
  - Managing GitHub Actions CI workflows (`.github/workflows/ci.yml`), automated typechecking, linting, and build validation.
  - Maintaining the release gate scorecard (`Documentation/RELEASE_CHECKLIST.md`) and tracking cross-team blockers.
  - Organizing staging deployment rehearsals, Playwright end-to-end testing, and disaster recovery drills.
- **Primary Source Files:**
  - `frontend/src/App.tsx` (Root dashboard state coordinator)
  - `frontend/src/components/` (`RunList.tsx`, `RunDetailView.tsx`, `EvidenceViewer.tsx`, `AnalysisPanel.tsx`, `PatchView.tsx`)
  - `frontend/src/services/api.ts` (Typed API client)
  - `frontend/src/types.ts` (Domain interface contracts)
  - `frontend/nginx.conf` & `frontend/Dockerfile` (Production web distribution)
  - `.github/workflows/ci.yml` (CI orchestration)
  - `docker-compose.yml` & `scripts/start.ps1` (Deployment automation)
  - `Documentation/RELEASE_CHECKLIST.md` & `READINESS_SCORECARD.md`

---

## 3. Ten-Phase Production Roadmap Execution Matrix

Every phase maps directly to `PRODUCTION_READINESS_PLAN.md` with explicit ownership, collaborative touchpoints, source artifacts, testing requirements, and definitions of done.

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                           Phase Dependency Chain                            â”‚
â”‚                                                                             â”‚
â”‚  [Phase 1: Sandboxing] (Teammate A)                                         â”‚
â”‚         â”‚                                                                   â”‚
â”‚         â–¼                                                                   â”‚
â”‚  [Phase 2: Distributed Queue] (Teammate B) â”€â”€> [Phase 3: Auth] (Teammate A) â”‚
â”‚         â”‚                                             â”‚                     â”‚
â”‚         â–¼                                             â–¼                     â”‚
â”‚  [Phase 4: Nebius Live] (Teammate B) <â”€â”€â”€â”€â”€â”€â”€ [Phase 5: Secrets] (Teammate A)
â”‚         â”‚                                                                   â”‚
â”‚         â–¼                                                                   â”‚
â”‚  [Phase 6: Observability] (Teammate B & C)                                  â”‚
â”‚         â”‚                                                                   â”‚
â”‚         â–¼                                                                   â”‚
â”‚  [Phase 7: Frontend UI/UX] (Teammate C)                                     â”‚
â”‚         â”‚                                                                   â”‚
â”‚         â–¼                                                                   â”‚
â”‚  [Phase 8: Backup & Recovery] (Teammate B)                                  â”‚
â”‚         â”‚                                                                   â”‚
â”‚         â–¼                                                                   â”‚
â”‚  [Phase 9: Staging Rehearsal & Fuzzing] (All Teammates)                     â”‚
â”‚         â”‚                                                                   â”‚
â”‚         â–¼                                                                   â”‚
â”‚  [Phase 10: Production Cutover & Sign-Off] (All Teammates / Teammate C Lead) â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

---

### Phase 1: Runner Hardening and True Container Isolation
- **Primary Lead:** Teammate A  
- **Collaborator:** Teammate B (Job configuration contracts)  
- **Target Components & Files:**
  - `backend/app/services/runner.py`
  - `backend/app/services/patch.py`
  - `[PROPOSED] backend/app/services/container_runner.py`
  - `backend/tests/test_phase3_runner_and_evidence.py`
  - `[PROPOSED] backend/tests/test_container_isolation.py`
- **Scope & Implementation Details:**
  Replace host OS `subprocess.run` check execution with ephemeral, restricted container execution using Docker SDK (`docker-py`) or Podman.
  Enforce strict flags: `--network none`, `--memory 1g`, `--cpus 1.0`, `--pids-limit 64`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, and read-only root filesystems with ephemeral `tmpfs` mounts.
- **Dependencies:** None.
- **Testing Requirements:**
  - Automated security tests verifying check scripts cannot establish network connections to localhost, LAN, or WAN.
  - Assertions proving fork bombs or memory exhaustion terminate cleanly within container limits without crashing the host.
- **Definition of Done:** Host OS execution completely replaced; container sandbox passes all security isolation tests; exit codes, outputs, and timeouts match existing `CheckExecutionResult` contracts.

---

### Phase 2: Distributed Task Queue and Worker Decoupling
- **Primary Lead:** Teammate B  
- **Collaborator:** Teammate A (Runner container lifecycle management)  
- **Target Components & Files:**
  - `backend/app/api/v1/endpoints/analysis.py`
  - `backend/app/api/v1/endpoints/runs.py`
  - `docker-compose.yml` (add dedicated worker container service)
  - `[PROPOSED] backend/app/worker.py` (Worker entrypoint)
  - `[PROPOSED] backend/app/core/queue.py` (Redis queue connection & dispatch)
  - `[PROPOSED] backend/tests/test_worker_queue.py`
- **Scope & Implementation Details:**
  Decouple asynchronous work from in-process `fastapi.BackgroundTasks` into an out-of-process distributed worker pool (using Redis with ARQ or Celery).
  Move `execute_run_checks` and `run_analysis` into worker tasks with heartbeat monitoring, task lease renewal, and automatic state recovery on worker crashes.
- **Dependencies:** Phase 1 (Runner container spawning by workers).
- **Testing Requirements:**
  - Chaos test terminating the worker process mid-execution; verify run transitions cleanly to `error` or recovers automatically without hanging indefinitely.
  - Idempotency test asserting that duplicated Redis task delivery does not create redundant database rows.
- **Definition of Done:** Web API no longer runs heavy jobs in-process; worker processes consume from Redis; run cancellations cleanly terminate active worker tasks within 5 seconds.

---

### Phase 3: GitHub App Authentication and Identity Infrastructure
- **Primary Lead:** Teammate A  
- **Collaborator:** Teammate C (Frontend login/session UX)  
- **Target Components & Files:**
  - `backend/app/api/deps.py`
  - `backend/app/api/v1/endpoints/auth.py`
  - `backend/app/core/config.py`
  - `frontend/src/services/api.ts`
  - `[PROPOSED] backend/tests/test_github_auth.py`
- **Scope & Implementation Details:**
  Replace development synthetic user fallback (`demo-dev` in `deps.py`) with complete GitHub OAuth 2.0 Web Application Flow.
  Implement `GET /api/v1/auth/github/login` (CSRF state generation) and `GET /api/v1/auth/github/callback` (token exchange, user persistence).
  Issue signed, encrypted, `HttpOnly`, `SameSite=Lax`, `Secure` session cookies. Implement cryptographic installation token generation for GitHub App API interactions.
- **Dependencies:** None.
- **Testing Requirements:**
  - Tests validating state parameter rejection on CSRF attempts.
  - Verification that unauthenticated requests to protected project/run endpoints return 401 Unauthorized.
  - Session revocation test verifying `POST /api/v1/auth/logout` terminates the server session.
- **Definition of Done:** Mock developer user removed from production code; all user actions bound to real GitHub identities; object-level authorization fully enforced.

---

### Phase 4: Nebius Token Factory Live Verification and Resiliency
- **Primary Lead:** Teammate B  
- **Collaborator:** Teammate A (Canary and secret leakage auditing)  
- **Target Components & Files:**
  - `backend/app/services/analysis.py`
  - `backend/app/core/config.py`
  - `[PROPOSED] backend/app/services/circuit_breaker.py`
  - `[PROPOSED] backend/tests/test_nebius_live.py` (Opt-in live integration test)
- **Scope & Implementation Details:**
  Execute live network handshake against `https://api.tokenfactory.nebius.com/v1` using an active account key and the designated model `nvidia/llama-3.1-nemotron-70b-instruct`.
  Implement an in-memory circuit breaker that trips after 3 consecutive upstream 5xx errors or timeouts, cleanly transitioning analysis to `unavailable` without exhausting provider quotas.
  Record token usage telemetry (`input_tokens`, `output_tokens`) and request latency in `analyses` table.
- **Dependencies:** Phase 2 (Workers execute model calls).
- **Testing Requirements:**
  - Opt-in live test (`pytest -m live_provider`) validating real Nebius API response parsing and schema conformance.
  - Unit tests verifying circuit breaker trip and reset dynamics under simulated upstream faults.
- **Definition of Done:** Confirmed live handshake with Nebius API returning valid structured diagnosis; token spend and latency recorded; zero secret leakage in provider error traces.

---

### Phase 5: Production Secret Governance and KMS Integration
- **Primary Lead:** Teammate A  
- **Collaborator:** Teammate B (Database and Redis credential resolution)  
- **Target Components & Files:**
  - `backend/app/core/config.py`
  - `.env.production.example`
  - `docker-compose.yml`
  - `[PROPOSED] backend/app/core/secrets.py` (KMS / Vault client adapter)
  - `[PROPOSED] backend/tests/test_secret_governance.py`
- **Scope & Implementation Details:**
  Eliminate plaintext `.env.production` files in production deployments.
  Implement a dynamic secret resolution adapter capable of fetching `DATABASE_URL`, `SESSION_SECRET_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY`, and `NEBIUS_TOKEN_FACTORY_API_KEY` from cloud KMS (AWS Secrets Manager, GCP Secret Manager, or HashiCorp Vault) at application startup.
  Establish automated secret rotation procedures for webhook HMAC secrets and database passwords.
- **Dependencies:** None.
- **Testing Requirements:**
  - Unit tests proving settings successfully initialize from injected memory dictionaries without reading from filesystem.
  - Redaction test verifying KMS credentials never appear in application logs or exception traces.
- **Definition of Done:** Zero secrets committed to Git or stored as unencrypted disk files; environment securely configured via managed secret provider.

---

### Phase 6: Telemetry, Observability, and Distributed Tracing
- **Primary Lead:** Teammate B  
- **Collaborator:** Teammate C (Dashboard health & metrics visualization)  
- **Target Components & Files:**
  - `backend/app/main.py`
  - `backend/app/core/logging.py`
  - `docker-compose.yml` (add Prometheus exporter / metrics scraper)
  - `[PROPOSED] backend/app/core/telemetry.py` (OpenTelemetry instrumentation)
  - `[PROPOSED] backend/tests/test_telemetry.py`
- **Scope & Implementation Details:**
  Implement OpenTelemetry tracing across FastAPI routes, SQLAlchemy database queries, and outbound HTTP requests to Nebius.
  Expose a Prometheus `/metrics` endpoint collecting HTTP request counts, response latency histograms ($p50, p95, p99$), active check durations, queue lag, and Nebius token consumption.
  Integrate structured JSON logging with correlation IDs (`X-Request-ID` and `run_id`) across all services.
- **Dependencies:** Phase 2 (Worker queue instrumented).
- **Testing Requirements:**
  - Assertions verifying `/metrics` returns valid Prometheus exposition format.
  - Trace context propagation tests verifying correlation ID persists from API request to background worker.
- **Definition of Done:** Prometheus metrics and structured JSON logs active in container stack; observability dashboard displays real-time service health.

---

### Phase 7: Modern UI/UX Redesign and Real-Time Telemetry Streaming
- **Primary Lead:** Teammate C  
- **Collaborator:** Teammate B (API SSE endpoint implementation)  
- **Target Components & Files:**
  - `frontend/src/App.tsx`
  - `frontend/src/components/` (all components)
  - `frontend/src/services/api.ts`
  - `backend/app/api/v1/endpoints/runs.py` (add SSE stream route)
  - `[PROPOSED] frontend/src/components/MonacoDiffViewer.tsx`
  - `[PROPOSED] frontend/src/hooks/useRunEvents.ts` (SSE hook)
  - `[PROPOSED] frontend/tests/e2e/dashboard.spec.ts`
- **Scope & Implementation Details:**
  Refactor frontend layout using Tailwind CSS and Radix UI / Shadcn primitives for improved visual hierarchy and usability.
  Replace `<pre>` diff blocks in `PatchView.tsx` with Monaco Editor or CodeMirror for syntax-highlighted inline and side-by-side diffing.
  Implement Server-Sent Events (SSE) at `GET /api/v1/runs/{id}/events` to stream real-time check execution logs and AI diagnosis updates without browser polling.
  Conduct WCAG 2.1 AA accessibility audit (keyboard navigation, focus states, high contrast).
- **Dependencies:** Phase 2 (Workers publish real-time events via Redis Pub/Sub).
- **Testing Requirements:**
  - Automated Playwright UI tests covering run selection, evidence inspection, and patch proposal flows.
  - Accessibility audit test suite asserting zero critical accessibility violations.
- **Definition of Done:** Frontend built cleanly with Tailwind CSS; Monaco diff rendering operational; real-time event streaming eliminates polling; 100% WCAG 2.1 AA compliant.

---

### Phase 8: Automated Backup, Disaster Recovery, and Rollback Automation
- **Primary Lead:** Teammate B  
- **Collaborator:** Teammate A (Storage access permissions and encryption)  
- **Target Components & Files:**
  - `backend/alembic/versions/`
  - `scripts/`
  - `[PROPOSED] scripts/backup_db.sh` (or `.ps1`)
  - `[PROPOSED] scripts/restore_db.sh` (or `.ps1`)
  - `[PROPOSED] backend/tests/test_migration_rollback.py`
- **Scope & Implementation Details:**
  Configure automated PostgreSQL logical backups (`pg_dump`) and write-ahead log (WAL) archiving to encrypted external storage.
  Implement and test point-in-time recovery (PITR) procedures targeting Recovery Time Objective (RTO) $\le 30\text{ minutes}$ and Recovery Point Objective (RPO) $\le 5\text{ minutes}$.
  Verify bidirectional Alembic migration integrity (`alembic upgrade head` followed by `alembic downgrade -1`).
- **Dependencies:** None.
- **Testing Requirements:**
  - Automated migration test asserting clean schema application and rollback across all migration revisions.
  - Drill test restoring a PostgreSQL database from a backup snapshot into a fresh container.
- **Definition of Done:** Documented, script-automated backup and restore pipeline tested; migration rollbacks confirmed functional without data loss.

---

### Phase 9: End-to-End Staging Rehearsal and Security Fuzzing
- **Primary Lead:** All Teammates (Teammate C Coordinating)  
- **Specific Roles:**
  - Teammate A: Adversarial prompt injection, webhook replay, and sandbox breakout tests.
  - Teammate B: Database connection pool stress testing and Redis queue saturation tests.
  - Teammate C: Automated load generation and Playwright user journey testing.
- **Target Components & Files:**
  - `.github/workflows/ci.yml`
  - `[PROPOSED] tests/load/locustfile.py`
  - `[PROPOSED] tests/security/fuzz_diffs.py`
  - `[PROPOSED] tests/security/prompt_injection_fixtures.json`
- **Scope & Implementation Details:**
  Deploy complete multi-container CodeSentinel stack into a dedicated staging environment mirroring production.
  Execute load tests: benchmark webhook ingestion at $\ge 100\text{ req/sec}$ with $p99 < 50\text{ms}$.
  Conduct adversarial fuzzing: malformed unified diffs, path traversal attempts, and prompt injection attacks trying to leak secrets or fake test passes.
- **Dependencies:** Phases 1 through 8.
- **Testing Requirements:**
  - Load testing reports verifying zero connection pool exhaustion under 200 concurrent users.
  - Security audit reports proving zero arbitrary code execution or prompt injection overrides.
- **Definition of Done:** 48-hour continuous staging soak test passes with zero service restarts, zero data corruption, and 100% audit trail generation.

---

### Phase 10: Production Cutover, Live Monitoring, and Release Sign-Off
- **Primary Lead:** All Teammates (Teammate C Lead)  
- **Specific Roles:**
  - Teammate A: Final security audit sign-off and webhook URL verification.
  - Teammate B: Production database migration execution and worker pool scaling.
  - Teammate C: Release gate checklist validation, DNS routing, and canary verification.
- **Target Components & Files:**
  - `Documentation/RELEASE_CHECKLIST.md`
  - `Documentation/READINESS_SCORECARD.md`
  - `docker-compose.yml` (Production overrides)
- **Scope & Implementation Details:**
  Execute production deployment under SSL/TLS termination with hardened reverse proxy.
  Configure production GitHub App webhook endpoints and activate live monitoring alert rules (error rates $>1\%$, worker queue lag $>30\text{s}$).
  Conduct canary validation on a designated production test repository.
  Sign off on all release gates in `RELEASE_CHECKLIST.md`.
- **Dependencies:** Phase 9.
- **Testing Requirements:**
  - Live production canary run: webhook receipt $\rightarrow$ isolated container check $\rightarrow$ evidence extraction $\rightarrow$ Nebius AI diagnosis $\rightarrow$ dashboard rendering.
- **Definition of Done:** All release checklist security gates formally signed off; live system operating in production with verified evidence-led regression diagnosis.

---

## 4. Cross-Workstream Dependencies & Synchronization

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ Pre-Requisite Feature / Output       â”‚ Required By Workstream  â”‚ Target Milestone Phase â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚ Container Sandbox Runner             â”‚ Worker Queue Execution  â”‚ Phase 1 â”€â”€> Phase 2    â”‚
â”‚ Redis Worker Queue Service           â”‚ Real-Time SSE Stream    â”‚ Phase 2 â”€â”€> Phase 7    â”‚
â”‚ Redis Worker Queue Service           â”‚ Live Nebius Calls       â”‚ Phase 2 â”€â”€> Phase 4    â”‚
â”‚ GitHub OAuth 2.0 Web Flow            â”‚ Frontend Login UX       â”‚ Phase 3 â”€â”€> Phase 7    â”‚
â”‚ OpenTelemetry / Prometheus Exporter  â”‚ Operations Dashboard    â”‚ Phase 6 â”€â”€> Phase 9    â”‚
â”‚ Automated Backup / Restore Scripts   â”‚ Staging Deployment      â”‚ Phase 8 â”€â”€> Phase 9    â”‚
â”‚ Staging Load & Security Soak Test    â”‚ Production Cutover      â”‚ Phase 9 â”€â”€> Phase 10   â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### Pull Request & Review Rules
1. **Security Changes (Phases 1, 3, 5):** Must be authored or co-reviewed by Teammate A. Requires 2 approvals.
2. **Architecture & Persistence (Phases 2, 4, 8):** Must be authored or co-reviewed by Teammate B. Requires 2 approvals.
3. **Frontend & CI/CD (Phases 6, 7, 9, 10):** Must be reviewed by Teammate C and at least one backend teammate.
4. **Automated CI Requirement:** No PR may be merged unless all 53 backend tests pass, Ruff linting passes with zero errors, and frontend TypeScript build completes cleanly.

---

## 5. Security & Invariant Release Checklist (Blocking Gates)

Any unfulfilled item below strictly **BLOCKS** production release:

- [ ] **Gate 1 (Deterministic Factual Authority):** Test check outcomes are the sole factual truth. AI model output is strictly labeled as a hypothesis and cannot modify or override test outcomes.
- [ ] **Gate 2 (Read-Only Git Operations):** System has zero permission to push commits, create pull requests, or merge branches in user repositories.
- [ ] **Gate 3 (No Shell Execution):** Runner processes never invoke `shell=True` or execute through shell wrappers (`sh -c`, `bash -c`).
- [ ] **Gate 4 (Container Isolation):** Check runner executes within non-root containers with `--network none`, dropped capabilities (`CAP_DROP=ALL`), and memory/CPU quotas.
- [ ] **Gate 5 (Zero-Secret Runner Environment):** Application database credentials, API tokens, and session secrets are completely excluded from runner environments.
- [ ] **Gate 6 (Evidence-ID Cross Referencing):** AI diagnosis root-cause hypotheses strictly reference valid evidence IDs belonging to the current run; hallucinated IDs trigger immediate response invalidation.
- [ ] **Gate 7 (Live Nebius Verification):** Production release verified with live Nebius Token Factory API handshake using designated model `nvidia/llama-3.1-nemotron-70b-instruct`.
- [ ] **Gate 8 (Disaster Recovery):** Database point-in-time recovery and Alembic migration rollbacks verified via automated restore drill.

---

## 6. Open Architecture Questions

The following questions require team alignment during Phase 1 sprint planning:

1. **Container Runtime Selection:** Should the worker spawn runner containers via local Docker daemon socket (`/var/run/docker.sock` restricted to trusted supervisor) or via rootless Podman to completely eliminate daemon privilege risks?
2. **Worker Queue Framework:** Should the team adopt **ARQ** (lightweight async Redis queue matching FastAPI's asyncio event loop) or **Celery** (mature multi-language ecosystem with extensive plug-in tooling)?
3. **Candidate Patch Storage:** Should candidate patch unified diffs continue residing directly in the PostgreSQL `patch_attempts.diff_content` column, or move to short-lived S3/GCS object storage if patch volume scales?

