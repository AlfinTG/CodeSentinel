# Implementation Plan and Gates

Build vertical slices in order. Do not begin patch agents, analytics or UI polish before the core path works.

## Phase 0 — Repository and dependencies
Confirm baseline decisions and actual tree/status. Confirm GitHub App setup, eligible NVIDIA open-model access through Nebius, provider budget and deployment cost. Add source layout only as needed, pinned dependencies/lockfiles, .gitignore, placeholder-only .env.example, formatting/lint/test config, secret scan and CI. Owner chooses open-source license.
**Gate 0:** clean clone installs; documented lint/test/build commands run; no secrets; external blockers identified; license choice explicit.

## Phase 1 — Data/API foundation
Implement PostgreSQL models and Alembic migration; auth/session and project authorization; run/check/evidence/analysis types and legal state transitions; stable errors, request IDs, pagination and health endpoints.
**Gate 1:** migration/integration tests prove constraints, authorization and transitions.

## Phase 2 — Signed event to durable run
GitHub App webhook, raw-body HMAC, supported-event filtering, transactionally persisted delivery/run/outbox, durable async enqueue, immutable SHA/check-profile pinning and duplicate/race handling.
**Gate 2:** valid signature accepted, invalid rejected, concurrent duplicate creates one logical job, webhook does not run checks synchronously.

## Phase 3 — Isolated checks and evidence
Trusted supervisor creates ephemeral constrained job container; only named/allowlisted checks; bounded output; redaction; timeout, cancellation, worker-loss and cleanup behavior.
**Gate 3 — security blocker:** isolation/resource tests pass; no secret/socket/network exposure; deliberate regression produces deterministic failure. No public repository execution before this.

## Phase 4 — Real model diagnosis
Verify account model catalog and eligible NVIDIA model; server-side adapter with timeout/retry/limits; bounded evidence prompt; strict schema/evidence-ID validation.
**Gate 4:** real opt-in call succeeds with model ID recorded; provider failure/invalid output is honest and test outcome remains unchanged; no secret leakage.

## Phase 5 — Dashboard
Project overview, run list/detail, check/evidence and diagnosis views; separate test outcome from analysis; accessible loading/empty/denied/error/partial states.
**Gate 5:** E2E reviewer follows event → pinned run → failing evidence → diagnosis/provider error.

## Phase 6 — Candidate patch (P1, only after 0–5)
Candidate diff in separate isolated workspace, explicit user action, bounded diff, approved checks, attempt history and cleanup. No GitHub branch write, PR creation, merge or deployment.
**Gate 6:** candidate cannot escape workspace; deliberate fixture passes only if all required checks pass; timeout/partial stays unverified.

## Phase 7 — Release and demo
Clean-environment rehearsal; accessible app URL; public repository, license and exact setup. Verify TLS/secrets/limits/retention/kill switch/rollback; re-check official rules and model eligibility.
**Gate 7:** release checklist passes; demo repeatable; no invented metrics/features.

## Fastest credible demo
If time is constrained, complete phases 0–5 and defer patch verification explicitly. Do not weaken webhook validation or runner isolation.
